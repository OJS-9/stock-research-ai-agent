"""
PostgreSQL database connection and schema management for reports, chunks, and portfolios.
"""

import logging
import os
import threading
import time
import json
import uuid
from decimal import Decimal
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
import psycopg2
import psycopg2.pool
import psycopg2.extras
from dotenv import load_dotenv
from encryption import encrypt, decrypt, hmac_email

load_dotenv()

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages PostgreSQL database connections and operations for reports and chunks."""

    def __init__(self):
        """Initialize database manager with connection pool."""
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable not set")

        try:
            self._pool = psycopg2.pool.ThreadedConnectionPool(
                2,
                15,
                dsn=database_url,
                connect_timeout=10,
                options="-c statement_timeout=30000",
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=3,
            )
            logger.info("PostgreSQL connection pool initialized")
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to create PostgreSQL connection pool: {e}")

        # Tracks when each pooled connection was last returned (monotonic seconds),
        # keyed by id(conn). Used to skip the liveness ping on connections that were
        # in active use moments ago -- only idle ones risk a dropped socket.
        self._released_at = {}
        self._released_lock = threading.Lock()

    _POOL_ACQUIRE_ATTEMPTS = 3
    # Only ping a connection that has been idle in the pool longer than this.
    # Supabase drops idle sockets; a connection reused within this window is still
    # warm, so we skip the round-trip. Keeps DB-heavy requests (many quick
    # checkouts) from paying a SELECT 1 on every single one.
    _PING_IF_IDLE_SECONDS = float(os.getenv("DB_PING_IF_IDLE_SECONDS", "20"))

    def get_connection(self):
        """Return a pooled connection that is verified alive. Supabase can drop an
        idle connection's SSL layer while conn.closed stays 0, so we ping it -- but
        only when it has sat idle long enough to plausibly be dead."""
        last_err = None
        for _ in range(self._POOL_ACQUIRE_ATTEMPTS):
            try:
                conn = self._pool.getconn()
            except psycopg2.Error as e:
                raise RuntimeError(f"Failed to get connection from pool: {e}")

            with self._released_lock:
                released_at = self._released_at.pop(id(conn), None)

            # Recently active connection: trust it, skip the ping round-trip.
            if (
                not conn.closed
                and released_at is not None
                and (time.monotonic() - released_at) < self._PING_IF_IDLE_SECONDS
            ):
                return conn

            try:
                if conn.closed:
                    raise psycopg2.OperationalError("pooled connection is closed")
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                conn.rollback()
                return conn
            except psycopg2.Error as e:
                last_err = e
                try:
                    self._pool.putconn(conn, close=True)
                except Exception:
                    pass
                with self._released_lock:
                    self._released_at.pop(id(conn), None)
        raise RuntimeError(f"Failed to get a live connection after retries: {last_err}")

    def _release(self, conn):
        """Return a connection to the pool."""
        if conn is not None:
            try:
                self._pool.putconn(conn)
                if not conn.closed:
                    with self._released_lock:
                        self._released_at[id(conn)] = time.monotonic()
            except Exception:
                pass

    # Total checkout attempts for a single read/write operation. One retry is
    # enough: the issue is a connection silently dropped by Supabase, and it
    # self-resolves on the next fresh connection. Deliberately not higher, so a
    # genuine outage fails fast instead of stacking 30s statement_timeouts.
    _MAX_DB_TRIES = 2

    # Substring backstop for psycopg2.OperationalError raised when Supabase has
    # dropped the socket. The robust signal is conn.closed != 0 (set after the
    # error); these markers catch the case where the drop is reported before the
    # connection object flips closed. Lowercased at compare time.
    _DROPPED_CONN_MARKERS = (
        "ssl connection has been closed unexpectedly",
        "server closed the connection unexpectedly",
        "connection already closed",
        "connection not open",
        "no connection to the server",
        "terminating connection due to",
        "could not receive data from server",
        "could not send data to server",
    )

    @classmethod
    def _is_dropped_connection(cls, conn, exc) -> bool:
        """True only when the connection was dropped underneath us -- not for a
        genuine query error like a statement timeout (QueryCanceled subclasses
        OperationalError but leaves the connection open, so it must NOT retry)."""
        if not isinstance(exc, psycopg2.OperationalError):
            return False
        if conn is not None and getattr(conn, "closed", 0):
            return True
        msg = str(exc).lower()
        return any(marker in msg for marker in cls._DROPPED_CONN_MARKERS)

    def _discard(self, conn):
        """Drop a poisoned connection so the pool replaces it on next checkout."""
        try:
            self._pool.putconn(conn, close=True)
        except Exception:
            pass
        with self._released_lock:
            self._released_at.pop(id(conn), None)

    def _run_read(self, operation):
        """Run a read-only operation(conn) with one transparent retry if the
        pooled connection was dropped mid-query. Safe for SELECTs only: a read
        that fails on a dead socket never reached the server, so re-running it on
        a fresh connection cannot double-apply anything."""
        last_err = None
        for attempt in range(self._MAX_DB_TRIES):
            conn = self.get_connection()
            try:
                return operation(conn)
            except psycopg2.OperationalError as e:
                if not self._is_dropped_connection(conn, e):
                    raise  # real query error -- let it surface, conn released below
                last_err = e
                self._discard(conn)
                conn = None
                if attempt + 1 < self._MAX_DB_TRIES:
                    logger.warning(
                        "DB read hit a dropped connection; retrying on a fresh one"
                    )
                    continue
                raise
            finally:
                if conn is not None:
                    self._release(conn)
        raise last_err

    def _run_write(self, operation):
        """Run a write operation(conn) and commit, with one transparent retry if
        the connection was dropped BEFORE the commit. A pre-commit failure never
        reached durable state, so the retry is safe. A failure during commit()
        itself is ambiguous (the server may have committed before the socket
        died), so commit() lives outside the retryable block and is never retried
        -- that is what stops a committed-but-unacked write from double-applying."""
        last_err = None
        for attempt in range(self._MAX_DB_TRIES):
            conn = self.get_connection()
            try:
                try:
                    result = operation(conn)
                except psycopg2.OperationalError as e:
                    # Failure before commit: nothing durable happened.
                    if not self._is_dropped_connection(conn, e):
                        raise  # real query error -- surface it
                    last_err = e
                    self._discard(conn)
                    conn = None
                    if attempt + 1 < self._MAX_DB_TRIES:
                        logger.warning(
                            "DB write hit a dropped connection pre-commit; retrying"
                        )
                        continue
                    raise
                # operation succeeded; the commit is the ambiguous part. It is
                # intentionally outside the retry path above.
                conn.commit()
                return result
            finally:
                if conn is not None:
                    self._release(conn)
        raise last_err

    def init_schema(self):
        """Initialize database schema (create tables if they don't exist)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:

                # Give this session extra headroom for lock waits during rolling deploys
                # (old container may still hold pg_proc share locks via active triggers).
                cur.execute("SET LOCAL statement_timeout = '60s'")
                cur.execute("SET LOCAL lock_timeout = '30s'")

                # Trigger function for updated_at auto-update.
                # Skip CREATE OR REPLACE if the function already exists -- that path
                # takes an exclusive lock on pg_proc and will time out during a
                # rolling deploy while the old container is still running triggers.
                cur.execute(
                    "SELECT 1 FROM pg_proc WHERE proname = 'update_updated_at' LIMIT 1"
                )
                if cur.fetchone() is None:
                    cur.execute("""
                        CREATE OR REPLACE FUNCTION update_updated_at()
                        RETURNS TRIGGER AS $$
                        BEGIN
                          NEW.updated_at = NOW();
                          RETURN NEW;
                        END;
                        $$ LANGUAGE plpgsql
                    """)

                # Users
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id       VARCHAR(36)  PRIMARY KEY,
                        username      VARCHAR(80)  NOT NULL UNIQUE,
                        email         VARCHAR(120) NOT NULL UNIQUE,
                        password_hash VARCHAR(255),
                        google_id     VARCHAR(255) UNIQUE,
                        tier          VARCHAR(20)  NOT NULL DEFAULT 'free',
                        is_pro        BOOLEAN      NOT NULL DEFAULT FALSE,
                        telegram_chat_id VARCHAR(64),
                        created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'is_pro'
                        ) THEN
                            ALTER TABLE users ADD COLUMN is_pro BOOLEAN NOT NULL DEFAULT FALSE;
                        END IF;
                    END $$
                """)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'telegram_chat_id'
                        ) THEN
                            ALTER TABLE users ADD COLUMN telegram_chat_id VARCHAR(64);
                        END IF;
                    END $$
                """)
                # email_hash column: HMAC of email for indexed lookup (email column stores ciphertext)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'email_hash'
                        ) THEN
                            ALTER TABLE users ADD COLUMN email_hash VARCHAR(64);
                        END IF;
                    END $$
                """)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'preferences'
                        ) THEN
                            ALTER TABLE users ADD COLUMN preferences JSONB DEFAULT '{}';
                        END IF;
                    END $$
                """)
                # disabled flag for admin to suspend accounts
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'disabled'
                        ) THEN
                            ALTER TABLE users ADD COLUMN disabled BOOLEAN DEFAULT FALSE;
                        END IF;
                    END $$
                """)
                # last_active_at timestamp updated on each authenticated request
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'last_active_at'
                        ) THEN
                            ALTER TABLE users ADD COLUMN last_active_at TIMESTAMP;
                        END IF;
                    END $$
                """)
                # activation_email_sent: one-time 24h post-signup nudge flag (issue #120)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'activation_email_sent'
                        ) THEN
                            ALTER TABLE users ADD COLUMN activation_email_sent BOOLEAN DEFAULT FALSE;
                        END IF;
                    END $$
                """)
                # weekly_digest_last_sent: date of last weekly portfolio digest (issue #129)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'weekly_digest_last_sent'
                        ) THEN
                            ALTER TABLE users ADD COLUMN weekly_digest_last_sent DATE;
                        END IF;
                    END $$
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_username  ON users (username)"
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_email     ON users (email)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_email_hash ON users (email_hash)")
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_google_id ON users (google_id)"
                )

                # Telegram connect tokens (one-time link codes)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS telegram_connect_tokens (
                        token       VARCHAR(64) PRIMARY KEY,
                        user_id     VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        expires_at  TIMESTAMP   NOT NULL,
                        created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_telegram_connect_user_id ON telegram_connect_tokens (user_id)"
                )

                # Reports
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS reports (
                        report_id   VARCHAR(36) PRIMARY KEY,
                        user_id     VARCHAR(36) REFERENCES users(user_id) ON DELETE SET NULL,
                        ticker      VARCHAR(10) NOT NULL,
                        trade_type  VARCHAR(50) NOT NULL,
                        report_text TEXT        NOT NULL,
                        metadata    JSONB,
                        created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_report_ticker     ON reports (ticker)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_report_created_at ON reports (created_at)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_report_user_id    ON reports (user_id)"
                )
                # expiry_nudge_sent: per-report 7-day staleness nudge flag (issue #130)
                cur.execute(
                    "ALTER TABLE reports ADD COLUMN IF NOT EXISTS expiry_nudge_sent BOOLEAN DEFAULT FALSE"
                )

                # Report chunks
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS report_chunks (
                        chunk_id    VARCHAR(36) PRIMARY KEY,
                        report_id   VARCHAR(36) NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
                        chunk_text  TEXT        NOT NULL,
                        section     VARCHAR(500),
                        chunk_index INT         NOT NULL,
                        embedding   JSONB,
                        chunk_type  VARCHAR(20) DEFAULT 'report',
                        created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunk_report_id ON report_chunks (report_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunk_section   ON report_chunks (section)"
                )
                # Idempotent migration for existing databases
                cur.execute(
                    "ALTER TABLE report_chunks ADD COLUMN IF NOT EXISTS chunk_type VARCHAR(20) DEFAULT 'report'"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_chunk_type ON report_chunks (chunk_type)"
                )

                # Portfolios
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS portfolios (
                        portfolio_id  VARCHAR(36)   PRIMARY KEY,
                        name          VARCHAR(100)  NOT NULL DEFAULT 'My Portfolio',
                        description   TEXT,
                        user_id       VARCHAR(36)   REFERENCES users(user_id) ON DELETE SET NULL,
                        track_cash    BOOLEAN       NOT NULL DEFAULT TRUE,
                        cash_balance  NUMERIC(18,2) NOT NULL DEFAULT 0,
                        created_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                        updated_at    TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_trigger WHERE tgname = 'set_portfolios_updated_at'
                        ) THEN
                            CREATE TRIGGER set_portfolios_updated_at
                            BEFORE UPDATE ON portfolios
                            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
                        END IF;
                    END $$
                """)

                # Holdings
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS holdings (
                        holding_id       VARCHAR(36)   PRIMARY KEY,
                        portfolio_id     VARCHAR(36)   NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
                        symbol           VARCHAR(20)   NOT NULL,
                        asset_type       VARCHAR(10)   NOT NULL CHECK (asset_type IN ('stock', 'crypto')),
                        total_quantity   NUMERIC(18,8) NOT NULL DEFAULT 0,
                        average_cost     NUMERIC(18,8) NOT NULL DEFAULT 0,
                        total_cost_basis NUMERIC(18,2) NOT NULL DEFAULT 0,
                        created_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                        updated_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (portfolio_id, symbol)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_holdings_portfolio_id ON holdings (portfolio_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_holdings_symbol       ON holdings (symbol)"
                )
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_trigger WHERE tgname = 'set_holdings_updated_at'
                        ) THEN
                            CREATE TRIGGER set_holdings_updated_at
                            BEFORE UPDATE ON holdings
                            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
                        END IF;
                    END $$
                """)

                # Transactions
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        transaction_id   VARCHAR(36)   PRIMARY KEY,
                        holding_id       VARCHAR(36)   NOT NULL REFERENCES holdings(holding_id) ON DELETE CASCADE,
                        transaction_type VARCHAR(10)   NOT NULL CHECK (transaction_type IN ('buy', 'sell')),
                        quantity         NUMERIC(18,8) NOT NULL,
                        price_per_unit   NUMERIC(18,8) NOT NULL,
                        fees             NUMERIC(18,2) DEFAULT 0,
                        transaction_date TIMESTAMP     NOT NULL,
                        notes            TEXT,
                        import_source    VARCHAR(50),
                        created_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_txn_holding_id ON transactions (holding_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_txn_date       ON transactions (transaction_date)"
                )

                # CSV imports
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS csv_imports (
                        import_id     VARCHAR(36)  PRIMARY KEY,
                        portfolio_id  VARCHAR(36)  NOT NULL REFERENCES portfolios(portfolio_id) ON DELETE CASCADE,
                        filename      VARCHAR(255) NOT NULL,
                        row_count     INT          NOT NULL,
                        success_count INT          NOT NULL,
                        error_count   INT          NOT NULL,
                        errors_json   JSONB,
                        imported_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_csv_portfolio_id ON csv_imports (portfolio_id)"
                )

                # Watchlists
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS watchlists (
                        watchlist_id VARCHAR(36)  PRIMARY KEY,
                        user_id      VARCHAR(36)  NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        name         VARCHAR(100) NOT NULL DEFAULT 'My Watchlist',
                        position     INT          NOT NULL DEFAULT 0,
                        created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                        updated_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_watchlists_user_id ON watchlists (user_id)"
                )
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_trigger WHERE tgname = 'set_watchlists_updated_at'
                        ) THEN
                            CREATE TRIGGER set_watchlists_updated_at
                            BEFORE UPDATE ON watchlists
                            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
                        END IF;
                    END $$
                """)

                # Watchlist sections
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS watchlist_sections (
                        section_id   VARCHAR(36)  PRIMARY KEY,
                        watchlist_id VARCHAR(36)  NOT NULL REFERENCES watchlists(watchlist_id) ON DELETE CASCADE,
                        name         VARCHAR(100) NOT NULL,
                        position     INT          NOT NULL DEFAULT 0,
                        created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sections_watchlist_id ON watchlist_sections (watchlist_id)"
                )

                # Watchlist items
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS watchlist_items (
                        item_id      VARCHAR(36)  PRIMARY KEY,
                        watchlist_id VARCHAR(36)  NOT NULL REFERENCES watchlists(watchlist_id) ON DELETE CASCADE,
                        section_id   VARCHAR(36)  REFERENCES watchlist_sections(section_id) ON DELETE SET NULL,
                        symbol       VARCHAR(20)  NOT NULL,
                        asset_type   VARCHAR(10)  NOT NULL CHECK (asset_type IN ('stock', 'crypto')),
                        display_name VARCHAR(100),
                        position     INT          NOT NULL DEFAULT 0,
                        is_pinned    BOOLEAN      NOT NULL DEFAULT FALSE,
                        created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (watchlist_id, symbol)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_watchlist_id ON watchlist_items (watchlist_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_section_id   ON watchlist_items (section_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_items_symbol       ON watchlist_items (symbol)"
                )

                # Per-user ticker notes (rich text from /ticker/<symbol>)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ticker_notes (
                        user_id    VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        symbol     VARCHAR(20) NOT NULL,
                        content    TEXT        NOT NULL DEFAULT '',
                        created_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, symbol)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ticker_notes_symbol ON ticker_notes (symbol)"
                )
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_trigger WHERE tgname = 'set_ticker_notes_updated_at'
                        ) THEN
                            CREATE TRIGGER set_ticker_notes_updated_at
                            BEFORE UPDATE ON ticker_notes
                            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
                        END IF;
                    END $$
                """)

                # Price cache
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS price_cache (
                        symbol         VARCHAR(20)   PRIMARY KEY,
                        asset_type     VARCHAR(10)   NOT NULL CHECK (asset_type IN ('stock', 'crypto')),
                        price          NUMERIC(18,8),
                        change_percent NUMERIC(10,4),
                        display_name   VARCHAR(100),
                        last_updated   TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_price_cache_last_updated ON price_cache (last_updated)"
                )
                cur.execute(
                    "ALTER TABLE price_cache ADD COLUMN IF NOT EXISTS currency VARCHAR(3) DEFAULT 'USD'"
                )

                # Public view (global per-symbol Reddit + X synthesis)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ticker_public_view (
                        symbol           VARCHAR(20)  PRIMARY KEY,
                        summary_md       TEXT,
                        bullish_pct      INTEGER,
                        top_themes_json  JSONB,
                        reddit_posts     JSONB,
                        x_posts          JSONB,
                        last_updated     TIMESTAMPTZ,
                        status           VARCHAR(16),
                        error_message    TEXT
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_ticker_public_view_last_updated ON ticker_public_view (last_updated)"
                )

                # Price alerts (user-defined targets vs cached quotes; evaluation in jobs later)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS price_alerts (
                        alert_id          VARCHAR(36) PRIMARY KEY,
                        user_id           VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        symbol            VARCHAR(20) NOT NULL,
                        asset_type        VARCHAR(10) NOT NULL DEFAULT 'stock'
                            CHECK (asset_type IN ('stock', 'crypto')),
                        direction         VARCHAR(10) NOT NULL CHECK (direction IN ('above', 'below')),
                        target_price      NUMERIC(18, 8) NOT NULL,
                        active            BOOLEAN NOT NULL DEFAULT TRUE,
                        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        last_triggered_at TIMESTAMP NULL
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_price_alerts_user_id ON price_alerts (user_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_price_alerts_symbol ON price_alerts (symbol)"
                )
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_trigger WHERE tgname = 'set_price_alerts_updated_at'
                        ) THEN
                            CREATE TRIGGER set_price_alerts_updated_at
                            BEFORE UPDATE ON price_alerts
                            FOR EACH ROW EXECUTE FUNCTION update_updated_at();
                        END IF;
                    END $$
                """)

                # Fired when an alert condition matches cached price (in-app notification feed)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS price_alert_notifications (
                        notification_id   VARCHAR(36) PRIMARY KEY,
                        user_id           VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        alert_id          VARCHAR(36) NOT NULL REFERENCES price_alerts(alert_id) ON DELETE CASCADE,
                        symbol            VARCHAR(20) NOT NULL,
                        body              TEXT NOT NULL,
                        read_at           TIMESTAMP NULL,
                        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pan_user_created ON price_alert_notifications (user_id, created_at DESC)"
                )
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_pan_user_unread
                    ON price_alert_notifications (user_id)
                    WHERE read_at IS NULL
                    """)

                # Public waitlist signups (pre-launch; no mail provider yet)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS waitlist_emails (
                        id         SERIAL PRIMARY KEY,
                        email      TEXT UNIQUE NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                """)

                # Monthly research report usage (free tier quota)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS report_usage (
                        user_id      VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        period       CHAR(7)     NOT NULL,
                        report_count INT         NOT NULL DEFAULT 0,
                        PRIMARY KEY (user_id, period)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_report_usage_period ON report_usage (period)"
                )

                # Admin event log (structured events for the admin Logs tab)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS admin_events (
                        event_id    VARCHAR(36) PRIMARY KEY,
                        event_type  VARCHAR(50) NOT NULL,
                        user_id     VARCHAR(36),
                        payload     JSONB,
                        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_admin_events_type ON admin_events (event_type)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_admin_events_created ON admin_events (created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_admin_events_user ON admin_events (user_id)"
                )

                # App config (key-value runtime configuration for the admin Config tab)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS app_config (
                        key         VARCHAR(100) PRIMARY KEY,
                        value       JSONB NOT NULL,
                        updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Long-lived API tokens for CLI / headless agents
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS api_keys (
                        id           VARCHAR(36) PRIMARY KEY,
                        user_id      VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        name         VARCHAR(100) NOT NULL,
                        token_hash   VARCHAR(64)  NOT NULL UNIQUE,
                        prefix       VARCHAR(16)  NOT NULL,
                        created_at   TIMESTAMPTZ DEFAULT NOW(),
                        last_used_at TIMESTAMPTZ,
                        revoked_at   TIMESTAMPTZ
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys (user_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_api_keys_token_hash ON api_keys (token_hash)"
                )

                # Device-code flow: pending agent authorizations awaiting user approval.
                # access_token holds the raw token between approval and the next agent poll
                # (max 10 min, deleted on first successful poll).
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS device_codes (
                        device_code  VARCHAR(128) PRIMARY KEY,
                        user_code    VARCHAR(16)  NOT NULL UNIQUE,
                        user_id      VARCHAR(36)  REFERENCES users(user_id) ON DELETE CASCADE,
                        api_key_id   VARCHAR(36)  REFERENCES api_keys(id) ON DELETE SET NULL,
                        expires_at   TIMESTAMPTZ  NOT NULL,
                        approved_at  TIMESTAMPTZ,
                        access_token TEXT,
                        created_at   TIMESTAMPTZ  DEFAULT NOW()
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_device_codes_user_code ON device_codes (user_code)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_device_codes_expires_at ON device_codes (expires_at)"
                )

                # RLS: backend uses the postgres role (bypasses RLS); these policies
                # only matter for any client that ever connects as `authenticated` or `anon`.
                cur.execute("ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY")
                cur.execute("DROP POLICY IF EXISTS api_keys_owner ON api_keys")
                cur.execute(
                    "CREATE POLICY api_keys_owner ON api_keys "
                    "FOR ALL USING (user_id = auth.jwt()->>'sub') "
                    "WITH CHECK (user_id = auth.jwt()->>'sub')"
                )

                cur.execute("ALTER TABLE device_codes ENABLE ROW LEVEL SECURITY")
                cur.execute("DROP POLICY IF EXISTS device_codes_owner ON device_codes")
                # Once approved, the owner can read their pending/approved rows.
                # Pre-approval rows have user_id=NULL and are only accessible by service_role.
                cur.execute(
                    "CREATE POLICY device_codes_owner ON device_codes "
                    "FOR ALL USING (user_id = auth.jwt()->>'sub') "
                    "WITH CHECK (user_id = auth.jwt()->>'sub')"
                )

                # Subscriptions: Whop is source of truth; we cache the active
                # membership state here for fast tier/quota lookups.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS subscriptions (
                        id                  VARCHAR(36) PRIMARY KEY,
                        user_id             VARCHAR(36) NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                        whop_membership_id  VARCHAR(64) NOT NULL UNIQUE,
                        whop_plan_id        VARCHAR(64) NOT NULL,
                        tier                VARCHAR(20) NOT NULL,
                        cadence             VARCHAR(20) NOT NULL,
                        status              VARCHAR(20) NOT NULL DEFAULT 'active',
                        current_period_end  TIMESTAMPTZ,
                        created_at          TIMESTAMPTZ DEFAULT NOW(),
                        updated_at          TIMESTAMPTZ DEFAULT NOW()
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions (user_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_subscriptions_status  ON subscriptions (status)"
                )
                cur.execute("ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY")
                cur.execute("DROP POLICY IF EXISTS subscriptions_owner ON subscriptions")
                cur.execute(
                    "CREATE POLICY subscriptions_owner ON subscriptions "
                    "FOR ALL USING (user_id = auth.jwt()->>'sub') "
                    "WITH CHECK (user_id = auth.jwt()->>'sub')"
                )

                # Multi-worker shared state: report-generation status replaces
                # the per-process `_generation_status` dict in app.py so any
                # gunicorn worker can serve any /api/report_status poll.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS generation_status (
                        session_id      TEXT PRIMARY KEY,
                        user_id         TEXT NOT NULL,
                        status          TEXT NOT NULL,
                        report_id       TEXT,
                        progress        INT  DEFAULT 0,
                        step            TEXT,
                        step_code       TEXT,
                        done            INT,
                        total           INT,
                        partial         BOOLEAN DEFAULT FALSE,
                        message         TEXT,
                        questions       JSONB,
                        subjects        JSONB,
                        failed_subjects JSONB,
                        ticker          TEXT,
                        trade_type      TEXT,
                        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        expires_at      TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours')
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_generation_status_user ON generation_status (user_id)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_generation_status_expires ON generation_status (expires_at)"
                )
                # ticker/trade_type: let /api/reports/answer re-prime a fresh
                # agent when it lands on a different gunicorn worker than
                # /api/reports/generate (issue #165, same class as #116)
                cur.execute("""
                    DO $$ BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM information_schema.columns
                            WHERE table_schema = 'public' AND table_name = 'generation_status' AND column_name = 'ticker'
                        ) THEN
                            ALTER TABLE generation_status ADD COLUMN ticker TEXT;
                            ALTER TABLE generation_status ADD COLUMN trade_type TEXT;
                        END IF;
                    END $$
                """)

                # SSE event queue: replaces the per-process `_sse_queues` dict
                # so the producer thread on one worker and the SSE consumer on
                # another worker can communicate via Postgres.
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS generation_events (
                        id          BIGSERIAL PRIMARY KEY,
                        session_id  TEXT   NOT NULL,
                        seq         BIGINT NOT NULL,
                        event_type  TEXT   NOT NULL,
                        payload     JSONB  NOT NULL,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (session_id, seq)
                    )
                """)
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_generation_events_session ON generation_events (session_id, seq)"
                )

                # admin_events and app_config are managed outside init_schema
                # (created directly in Supabase). Mirror the RLS lockdown here
                # so any schema re-create or dev clone keeps PostgREST blocked.
                # Backend uses the postgres role, which bypasses RLS.
                cur.execute("""
                    DO $$
                    BEGIN
                        IF to_regclass('public.admin_events') IS NOT NULL THEN
                            EXECUTE 'ALTER TABLE public.admin_events ENABLE ROW LEVEL SECURITY';
                        END IF;
                        IF to_regclass('public.app_config') IS NOT NULL THEN
                            EXECUTE 'ALTER TABLE public.app_config ENABLE ROW LEVEL SECURITY';
                        END IF;
                    END $$;
                """)

            conn.commit()
            logger.info("Database schema initialized")

        except (psycopg2.errors.InternalError_, psycopg2.errors.DeadlockDetected) as e:
            # Two or more processes ran init_schema concurrently — common when
            # gunicorn forks N workers that all import app.py at the same time.
            # The schema is idempotent (CREATE IF NOT EXISTS) so whichever process
            # commits first wins; the rest can safely roll back and continue.
            if conn:
                conn.rollback()
            logger.warning("init_schema concurrent update ignored: %s", e)
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to initialize schema: {e}")
        finally:
            self._release(conn)

        # Run data migrations in a separate transaction so they succeed
        # even when the schema transaction hits a concurrent-update conflict.
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE portfolios SET track_cash = TRUE WHERE track_cash = FALSE"
                )
            conn.commit()
        except Exception:
            if conn:
                conn.rollback()
        finally:
            self._release(conn)

    def add_waitlist_email(self, email: str) -> None:
        """Persist a waitlist signup. Duplicate emails are ignored (idempotent)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO waitlist_emails (email)
                    VALUES (%s)
                    ON CONFLICT (email) DO NOTHING
                    """,
                    (email,),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to save waitlist email: {e}") from e
        finally:
            self._release(conn)

    def save_report(
        self,
        ticker: str,
        trade_type: str,
        report_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """Save a report to the database. Returns report_id."""
        report_id = str(uuid.uuid4())
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO reports (report_id, user_id, ticker, trade_type, report_text, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """,
                    (
                        report_id,
                        user_id,
                        ticker.upper(),
                        trade_type,
                        report_text,
                        json.dumps(metadata) if metadata else None,
                    ),
                )
            conn.commit()
            return report_id
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to save report: {e}")
        finally:
            self._release(conn)

    def get_report(
        self, report_id: str, user_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Retrieve a report by ID, optionally verifying ownership."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if user_id:
                    cur.execute(
                        """
                        SELECT report_id, user_id, ticker, trade_type, report_text, metadata, created_at
                        FROM reports WHERE report_id = %s AND user_id = %s
                    """,
                        (report_id, user_id),
                    )
                else:
                    cur.execute(
                        """
                        SELECT report_id, user_id, ticker, trade_type, report_text, metadata, created_at
                        FROM reports WHERE report_id = %s
                    """,
                        (report_id,),
                    )
                return cur.fetchone()
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get report: {e}")
        finally:
            self._release(conn)

    def get_reports_by_ticker(
        self, ticker: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Get recent reports for a ticker."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT report_id, ticker, trade_type, report_text, metadata, created_at
                    FROM reports WHERE ticker = %s ORDER BY created_at DESC LIMIT %s
                """,
                    (ticker.upper(), limit),
                )
                return cur.fetchall()
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get reports by ticker: {e}")
        finally:
            self._release(conn)

    def get_all_reports(
        self,
        ticker: Optional[str] = None,
        trade_type: Optional[str] = None,
        sort_order: str = "DESC",
        limit: int = 20,
        offset: int = 0,
        user_id: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Get paginated reports with optional filtering."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                where_clauses = []
                params = []

                if user_id:
                    where_clauses.append("user_id = %s")
                    params.append(user_id)
                if ticker:
                    where_clauses.append("ticker = %s")
                    params.append(ticker.upper())
                if trade_type:
                    where_clauses.append("trade_type = %s")
                    params.append(trade_type)

                where_sql = (
                    ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                )
                sort_order = (
                    "DESC"
                    if sort_order.upper() not in ("ASC", "DESC")
                    else sort_order.upper()
                )

                cur.execute(
                    f"SELECT COUNT(*) as total FROM reports {where_sql}", params
                )
                total_count = cur.fetchone()["total"]

                cur.execute(
                    f"""
                    SELECT report_id, user_id, ticker, trade_type, report_text, metadata, created_at
                    FROM reports {where_sql}
                    ORDER BY created_at {sort_order}
                    LIMIT %s OFFSET %s
                """,
                    params + [limit, offset],
                )

                return cur.fetchall(), total_count
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get all reports: {e}")
        finally:
            self._release(conn)

    def get_report_ticker_summaries(
        self,
        *,
        user_id: str,
        ticker: Optional[str] = None,
        sort_order: str = "DESC",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """
        Get one row per ticker with latest report metadata and per-ticker report count.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                where_parts = ["r.user_id = %s"]
                params: List[Any] = [user_id]
                if ticker:
                    where_parts.append("r.ticker = %s")
                    params.append(ticker.upper())
                where_sql = " AND ".join(where_parts)
                sort_order = (
                    "DESC"
                    if sort_order.upper() not in ("ASC", "DESC")
                    else sort_order.upper()
                )

                cur.execute(
                    f"""
                    SELECT COUNT(*) AS total
                    FROM (
                        SELECT DISTINCT r.ticker
                        FROM reports r
                        WHERE {where_sql}
                    ) t
                    """,
                    params,
                )
                total_count = int(cur.fetchone()["total"])

                cur.execute(
                    f"""
                    WITH filtered AS (
                        SELECT r.*
                        FROM reports r
                        WHERE {where_sql}
                    ),
                    latest AS (
                        SELECT DISTINCT ON (ticker)
                            ticker,
                            report_id,
                            trade_type,
                            report_text,
                            created_at
                        FROM filtered
                        ORDER BY ticker, created_at DESC
                    ),
                    counts AS (
                        SELECT ticker, COUNT(*) AS report_count
                        FROM filtered
                        GROUP BY ticker
                    )
                    SELECT
                        l.ticker,
                        l.report_id,
                        l.trade_type,
                        l.report_text,
                        l.created_at,
                        c.report_count
                    FROM latest l
                    JOIN counts c ON c.ticker = l.ticker
                    ORDER BY l.created_at {sort_order}
                    LIMIT %s OFFSET %s
                    """,
                    params + [limit, offset],
                )
                return list(cur.fetchall()), total_count
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get report ticker summaries: {e}")
        finally:
            self._release(conn)

    def save_chunks(self, report_id: str, chunks: List[Dict[str, Any]]):
        """Save report chunks with embeddings."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                for chunk in chunks:
                    chunk_id = str(uuid.uuid4())
                    cur.execute(
                        """
                        INSERT INTO report_chunks
                        (chunk_id, report_id, chunk_text, section, chunk_index, embedding, chunk_type)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                        (
                            chunk_id,
                            report_id,
                            chunk["chunk_text"],
                            chunk.get("section"),
                            chunk["chunk_index"],
                            (
                                json.dumps(chunk.get("embedding"))
                                if chunk.get("embedding")
                                else None
                            ),
                            chunk.get("chunk_type", "report"),
                        ),
                    )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to save chunks: {e}")
        finally:
            self._release(conn)

    def get_chunks_by_report(
        self, report_id: str, include_embeddings: bool = True
    ) -> List[Dict[str, Any]]:
        """Retrieve all chunks for a report."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if include_embeddings:
                    cur.execute(
                        """
                        SELECT chunk_id, report_id, chunk_text, section, chunk_index, embedding, chunk_type, created_at
                        FROM report_chunks WHERE report_id = %s ORDER BY chunk_index ASC
                    """,
                        (report_id,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT chunk_id, report_id, chunk_text, section, chunk_index, chunk_type, created_at
                        FROM report_chunks WHERE report_id = %s ORDER BY chunk_index ASC
                    """,
                        (report_id,),
                    )
                return cur.fetchall()
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get chunks: {e}")
        finally:
            self._release(conn)

    def delete_report(self, report_id: str):
        """Delete a report and all its chunks (cascade)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM reports WHERE report_id = %s", (report_id,))
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to delete report: {e}")
        finally:
            self._release(conn)

    # ==================== Portfolio Methods ====================

    def create_portfolio(
        self,
        portfolio_id: str,
        name: str = "My Portfolio",
        description: str = "",
        user_id: Optional[str] = None,
        track_cash: bool = True,
        cash_balance: float = 0.0,
    ):
        """Create a new portfolio."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO portfolios (portfolio_id, name, description, user_id, track_cash, cash_balance)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """,
                    (
                        portfolio_id,
                        name,
                        description,
                        user_id,
                        track_cash,
                        cash_balance,
                    ),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to create portfolio: {e}")
        finally:
            self._release(conn)

    def get_portfolio(self, portfolio_id: str) -> Optional[Dict[str, Any]]:
        """Get portfolio by ID."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT portfolio_id, name, description, user_id, created_at, updated_at,
                           COALESCE(track_cash, FALSE) AS track_cash,
                           COALESCE(cash_balance, 0) AS cash_balance
                    FROM portfolios WHERE portfolio_id = %s
                """,
                    (portfolio_id,),
                )
                return cur.fetchone()
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get portfolio: {e}")
        finally:
            self._release(conn)

    def update_cash_balance(self, portfolio_id: str, cash_balance: float) -> None:
        """Update the cash balance for a portfolio."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE portfolios SET cash_balance = %s WHERE portfolio_id = %s
                """,
                    (cash_balance, portfolio_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update cash balance: {e}")
        finally:
            self._release(conn)

    def enable_cash_tracking(self, portfolio_id: str) -> None:
        """Enable cash tracking for a portfolio."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE portfolios SET track_cash = TRUE WHERE portfolio_id = %s
                """,
                    (portfolio_id,),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to enable cash tracking: {e}")
        finally:
            self._release(conn)

    def list_portfolios(self, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List portfolios, optionally filtered by user."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if user_id is not None:
                    cur.execute(
                        """
                        SELECT portfolio_id, name, description, user_id, created_at, updated_at,
                               COALESCE(track_cash, FALSE) AS track_cash,
                               COALESCE(cash_balance, 0) AS cash_balance
                        FROM portfolios WHERE user_id = %s ORDER BY created_at ASC
                    """,
                        (user_id,),
                    )
                else:
                    cur.execute("""
                        SELECT portfolio_id, name, description, user_id, created_at, updated_at,
                               COALESCE(track_cash, FALSE) AS track_cash,
                               COALESCE(cash_balance, 0) AS cash_balance
                        FROM portfolios ORDER BY created_at ASC
                    """)
                return cur.fetchall()
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to list portfolios: {e}")
        finally:
            self._release(conn)

    # ==================== User Methods ====================

    def create_user(
        self,
        user_id: str,
        username: str,
        email: str,
        password_hash: Optional[str] = None,
        google_id: Optional[str] = None,
    ):
        """Create a new user. Email is stored AES-256 encrypted; email_hash used for lookups."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO users (user_id, username, email, email_hash, password_hash, google_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO NOTHING
                """,
                    (user_id, username, encrypt(email), hmac_email(email), password_hash, google_id),
                )
                created = cur.rowcount == 1
            conn.commit()
            if created:
                try:
                    self.admin_log_event("signup", user_id, {"username": username})
                except Exception:
                    pass
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to create user: {e}")
        finally:
            self._release(conn)

    def _decrypt_user_row(self, row) -> Optional[Dict[str, Any]]:
        """Decrypt sensitive fields on a user row dict returned from the DB."""
        if row is None:
            return None
        row = dict(row)
        if row.get("email"):
            row["email"] = decrypt(row["email"])
        if row.get("telegram_chat_id"):
            row["telegram_chat_id"] = decrypt(row["telegram_chat_id"])
        return row

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Get a user by username."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT user_id, username, email, password_hash, google_id, tier,
                           COALESCE(is_pro, FALSE) AS is_pro, telegram_chat_id, created_at
                    FROM users WHERE username = %s
                """,
                    (username,),
                )
                return self._decrypt_user_row(cur.fetchone())
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get user: {e}")
        finally:
            self._release(conn)

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by ID."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT user_id, username, email, password_hash, google_id, tier,
                           COALESCE(is_pro, FALSE) AS is_pro, telegram_chat_id, created_at,
                           COALESCE(preferences, '{}') AS preferences
                    FROM users WHERE user_id = %s
                """,
                    (user_id,),
                )
                return self._decrypt_user_row(cur.fetchone())
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get user: {e}")
        finally:
            self._release(conn)

    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Get a user by email. Uses email_hash for indexed lookup (email column is encrypted)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT user_id, username, email, password_hash, google_id, tier,
                           COALESCE(is_pro, FALSE) AS is_pro, telegram_chat_id, created_at
                    FROM users WHERE email_hash = %s
                """,
                    (hmac_email(email),),
                )
                return self._decrypt_user_row(cur.fetchone())
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get user: {e}")
        finally:
            self._release(conn)

    def update_last_active(self, user_id: str):
        """Touch last_active_at for the given user."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET last_active_at = NOW() WHERE user_id = %s",
                    (user_id,),
                )
            conn.commit()
        except psycopg2.Error:
            if conn:
                conn.rollback()
        finally:
            self._release(conn)

    # ── Admin queries ──────────────────────────────────────────────

    def admin_get_dashboard(self) -> Dict[str, Any]:
        """Return KPI data for the admin dashboard."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT COUNT(*) AS total FROM users")
                total_users = cur.fetchone()["total"]

                cur.execute(
                    "SELECT COUNT(*) AS total FROM reports WHERE created_at >= CURRENT_DATE"
                )
                reports_today = cur.fetchone()["total"]

                # Recent signups (last 10)
                cur.execute(
                    """SELECT user_id, username, email, tier, created_at
                       FROM users ORDER BY created_at DESC LIMIT 10"""
                )
                recent_signups = []
                for row in cur.fetchall():
                    r = dict(row)
                    if r.get("email"):
                        r["email"] = decrypt(r["email"])
                    recent_signups.append(r)

                # Recent reports (last 10)
                cur.execute(
                    """SELECT r.report_id, r.ticker, r.trade_type, r.created_at,
                              u.username
                       FROM reports r
                       LEFT JOIN users u ON r.user_id = u.user_id
                       ORDER BY r.created_at DESC LIMIT 10"""
                )
                recent_reports = [dict(row) for row in cur.fetchall()]

            return {
                "total_users": total_users,
                "reports_today": reports_today,
                "revenue_mtd": 0,
                "recent_signups": recent_signups,
                "recent_reports": recent_reports,
            }
        except psycopg2.Error as e:
            raise RuntimeError(f"Admin dashboard query failed: {e}")
        finally:
            self._release(conn)

    def admin_get_users(
        self, search: str = "", page: int = 1, per_page: int = 20,
        sort: str = "created_at", order: str = "desc"
    ) -> Dict[str, Any]:
        """Paginated user list for admin panel."""
        allowed_sort = {"created_at", "username", "tier", "last_active_at"}
        if sort not in allowed_sort:
            sort = "created_at"
        if order not in ("asc", "desc"):
            order = "desc"
        offset = (page - 1) * per_page
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                where = ""
                params: list = []
                if search:
                    where = "WHERE username ILIKE %s"
                    params.append(f"%{search}%")

                cur.execute(f"SELECT COUNT(*) AS total FROM users {where}", params)
                total = cur.fetchone()["total"]

                cur.execute(
                    f"""SELECT user_id, username, email, tier,
                               COALESCE(is_pro, FALSE) AS is_pro,
                               COALESCE(disabled, FALSE) AS disabled,
                               last_active_at, created_at
                        FROM users {where}
                        ORDER BY {sort} {order} NULLS LAST
                        LIMIT %s OFFSET %s""",
                    params + [per_page, offset],
                )
                users = []
                for row in cur.fetchall():
                    r = dict(row)
                    if r.get("email"):
                        r["email"] = decrypt(r["email"])
                    users.append(r)

            return {
                "users": users,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": max(1, -(-total // per_page)),
            }
        except psycopg2.Error as e:
            raise RuntimeError(f"Admin users query failed: {e}")
        finally:
            self._release(conn)

    def admin_update_user(self, user_id: str, updates: Dict[str, Any]) -> bool:
        """Update user fields (disabled, tier). Returns True if a row was updated."""
        allowed = {"disabled", "tier"}
        sets = []
        params = []
        for key, val in updates.items():
            if key in allowed:
                sets.append(f"{key} = %s")
                params.append(val)
        if not sets:
            return False
        params.append(user_id)
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE users SET {', '.join(sets)} WHERE user_id = %s",
                    params,
                )
                updated = cur.rowcount > 0
            conn.commit()
            return updated
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Admin update user failed: {e}")
        finally:
            self._release(conn)

    # ── Admin Events (Logs tab) ─────────────────────────────────────────

    def admin_log_event(
        self, event_type: str, user_id: Optional[str] = None, payload: Optional[Dict] = None
    ) -> None:
        """Write a structured event to admin_events. Fire-and-forget safe."""
        if not user_id:
            # Analytics events with no user attribution are useless and corrupt
            # aggregates grouped by user_id. Drop rather than insert a null row
            # (issue #145). Callers that legitimately lack a user should not log.
            return
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO admin_events (event_id, event_type, user_id, payload)
                       VALUES (%s, %s, %s, %s)""",
                    (str(uuid.uuid4()), event_type, user_id, json.dumps(payload or {})),
                )
            conn.commit()
        except Exception as e:
            if conn:
                conn.rollback()
            logger.warning("admin_log_event failed: %s", e)
        finally:
            self._release(conn)

    def admin_get_events(
        self, event_type: str = "", user_id: str = "",
        page: int = 1, per_page: int = 50
    ) -> Dict[str, Any]:
        """Paginated event log with optional type/user filters."""
        offset = (page - 1) * per_page
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                clauses: list = []
                params: list = []
                if event_type:
                    clauses.append("event_type = %s")
                    params.append(event_type)
                if user_id:
                    clauses.append("user_id = %s")
                    params.append(user_id)
                where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

                cur.execute(f"SELECT COUNT(*) AS total FROM admin_events {where}", params)
                total = cur.fetchone()["total"]

                cur.execute(
                    f"""SELECT e.event_id, e.event_type, e.user_id, e.payload, e.created_at,
                               u.username
                        FROM admin_events e
                        LEFT JOIN users u ON e.user_id = u.user_id
                        {where}
                        ORDER BY e.created_at DESC
                        LIMIT %s OFFSET %s""",
                    params + [per_page, offset],
                )
                events = [dict(row) for row in cur.fetchall()]

            return {
                "events": events,
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": max(1, -(-total // per_page)),
            }
        except psycopg2.Error as e:
            raise RuntimeError(f"Admin events query failed: {e}")
        finally:
            self._release(conn)

    def admin_get_event_types(self) -> List[str]:
        """Return distinct event types for the filter dropdown."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT event_type FROM admin_events ORDER BY event_type")
                return [row[0] for row in cur.fetchall()]
        except psycopg2.Error as e:
            raise RuntimeError(f"Admin event types query failed: {e}")
        finally:
            self._release(conn)

    # ── Admin Stats ─────────────────────────────────────────────────────

    def admin_get_stats(self) -> Dict[str, Any]:
        """Aggregate stats for the admin Stats tab."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Reports per day (last 30 days)
                cur.execute("""
                    SELECT DATE(created_at) AS day, COUNT(*) AS count
                    FROM reports
                    WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
                    GROUP BY DATE(created_at)
                    ORDER BY day
                """)
                reports_per_day = [dict(r) for r in cur.fetchall()]

                # Signups per day (last 30 days)
                cur.execute("""
                    SELECT DATE(created_at) AS day, COUNT(*) AS count
                    FROM users
                    WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'
                    GROUP BY DATE(created_at)
                    ORDER BY day
                """)
                signups_per_day = [dict(r) for r in cur.fetchall()]

                # Feature usage counts
                cur.execute("SELECT COUNT(*) AS total FROM portfolios")
                total_portfolios = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) AS total FROM watchlists")
                total_watchlists = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) AS total FROM alerts")
                total_alerts = cur.fetchone()["total"]
                cur.execute("SELECT COUNT(*) AS total FROM reports")
                total_reports = cur.fetchone()["total"]

                # Agent performance from admin_events (if we have research events)
                cur.execute("""
                    SELECT
                        COUNT(*) AS total_runs,
                        COUNT(*) FILTER (WHERE payload->>'status' = 'success') AS successes,
                        COUNT(*) FILTER (WHERE payload->>'status' = 'error') AS errors,
                        AVG((payload->>'duration_s')::FLOAT)
                            FILTER (WHERE payload->>'duration_s' IS NOT NULL) AS avg_duration_s
                    FROM admin_events
                    WHERE event_type = 'research_complete'
                """)
                agent_perf = dict(cur.fetchone())

            return {
                "reports_per_day": reports_per_day,
                "signups_per_day": signups_per_day,
                "feature_usage": {
                    "portfolios": total_portfolios,
                    "watchlists": total_watchlists,
                    "alerts": total_alerts,
                    "reports": total_reports,
                },
                "agent_performance": agent_perf,
            }
        except psycopg2.Error as e:
            raise RuntimeError(f"Admin stats query failed: {e}")
        finally:
            self._release(conn)

    # ── App Config ──────────────────────────────────────────────────────

    def config_get_all(self) -> Dict[str, Any]:
        """Return all config key-value pairs."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT key, value, updated_at FROM app_config ORDER BY key")
                return {row["key"]: {"value": row["value"], "updated_at": row["updated_at"]} for row in cur.fetchall()}
        except psycopg2.Error as e:
            raise RuntimeError(f"Config get all failed: {e}")
        finally:
            self._release(conn)

    def config_set(self, key: str, value: Any) -> None:
        """Upsert a config key."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO app_config (key, value, updated_at)
                       VALUES (%s, %s, CURRENT_TIMESTAMP)
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP""",
                    (key, json.dumps(value)),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Config set failed: {e}")
        finally:
            self._release(conn)

    def config_delete(self, key: str) -> bool:
        """Delete a config key. Returns True if deleted."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM app_config WHERE key = %s", (key,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Config delete failed: {e}")
        finally:
            self._release(conn)

    def get_user_by_google_id(self, google_id: str) -> Optional[Dict[str, Any]]:
        """Get a user by Google OAuth sub (google_id)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT user_id, username, email, password_hash, google_id, tier,
                           COALESCE(is_pro, FALSE) AS is_pro, telegram_chat_id, created_at
                    FROM users WHERE google_id = %s
                """,
                    (google_id,),
                )
                return self._decrypt_user_row(cur.fetchone())
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get user: {e}")
        finally:
            self._release(conn)

    def update_user_google_id(self, user_id: str, google_id: str):
        """Link a Google account to an existing user."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users SET google_id = %s WHERE user_id = %s
                """,
                    (google_id, user_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update user: {e}")
        finally:
            self._release(conn)

    def get_user_preferences(self, user_id: str) -> Dict[str, Any]:
        """Return user preferences dict (empty dict if not set)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(preferences, '{}') FROM users WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {}
                val = row[0]
                if isinstance(val, str):
                    return json.loads(val)
                return val or {}
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get preferences: {e}")
        finally:
            self._release(conn)

    def update_user_preferences(self, user_id: str, patch: Dict[str, Any], display_name: Optional[str] = None) -> None:
        """Merge patch into preferences JSONB. Optionally update username (display name)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET preferences = COALESCE(preferences, '{}') || %s::jsonb
                    WHERE user_id = %s
                    """,
                    (json.dumps(patch), user_id),
                )
                if display_name is not None:
                    cur.execute(
                        "UPDATE users SET username = %s WHERE user_id = %s",
                        (display_name[:80], user_id),
                    )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update preferences: {e}")
        finally:
            self._release(conn)

    def user_is_pro(self, user_id: str) -> bool:
        """Paid users bypass free-tier quotas. True for tier in {starter, ultra}
        OR legacy is_pro flag = true."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tier, COALESCE(is_pro, FALSE) FROM users WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                if not row:
                    return False
                tier = (row[0] or "free").lower()
                return tier in ("starter", "ultra") or bool(row[1])
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to read user tier: {e}")
        finally:
            self._release(conn)

    def set_user_tier(self, user_id: str, tier: str) -> None:
        """Set users.tier and sync legacy is_pro flag."""
        tier = (tier or "free").lower()
        if tier not in ("free", "starter", "ultra"):
            raise ValueError(f"unknown tier: {tier}")
        is_pro = tier in ("starter", "ultra")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET tier = %s, is_pro = %s WHERE user_id = %s",
                    (tier, is_pro, user_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to set tier: {e}")
        finally:
            self._release(conn)

    def upsert_subscription(
        self,
        *,
        user_id: str,
        whop_membership_id: str,
        whop_plan_id: str,
        tier: str,
        cadence: str,
        status: str = "active",
        current_period_end=None,
    ) -> str:
        """Insert or update a subscription row keyed by whop_membership_id."""
        import uuid as _uuid

        sub_id = str(_uuid.uuid4())
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO subscriptions
                        (id, user_id, whop_membership_id, whop_plan_id, tier,
                         cadence, status, current_period_end, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (whop_membership_id) DO UPDATE SET
                        whop_plan_id       = EXCLUDED.whop_plan_id,
                        tier               = EXCLUDED.tier,
                        cadence            = EXCLUDED.cadence,
                        status             = EXCLUDED.status,
                        current_period_end = EXCLUDED.current_period_end,
                        updated_at         = NOW()
                    RETURNING id
                    """,
                    (sub_id, user_id, whop_membership_id, whop_plan_id, tier,
                     cadence, status, current_period_end),
                )
                row = cur.fetchone()
            conn.commit()
            return row[0] if row else sub_id
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to upsert subscription: {e}")
        finally:
            self._release(conn)

    def get_active_subscription(self, user_id: str):
        """Return the most recent active subscription row, or None."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, user_id, whop_membership_id, whop_plan_id,
                           tier, cadence, status, current_period_end,
                           created_at, updated_at
                    FROM subscriptions
                    WHERE user_id = %s AND status = 'active'
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (user_id,),
                )
                return cur.fetchone()
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to read subscription: {e}")
        finally:
            self._release(conn)

    def set_subscription_status(self, whop_membership_id: str, status: str) -> None:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE subscriptions SET status = %s, updated_at = NOW() "
                    "WHERE whop_membership_id = %s",
                    (status, whop_membership_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to set subscription status: {e}")
        finally:
            self._release(conn)

    def get_subscription_user(self, whop_membership_id: str):
        """Look up the user_id behind an existing subscription row."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id FROM subscriptions WHERE whop_membership_id = %s",
                    (whop_membership_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to read subscription user: {e}")
        finally:
            self._release(conn)

    def update_subscription_period_end(self, whop_membership_id: str, period_end) -> None:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE subscriptions SET current_period_end = %s, updated_at = NOW() "
                    "WHERE whop_membership_id = %s",
                    (period_end, whop_membership_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update period end: {e}")
        finally:
            self._release(conn)

    def count_user_portfolios(self, user_id: str) -> int:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM portfolios WHERE user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to count portfolios: {e}")
        finally:
            self._release(conn)

    def claim_activation_email_candidates(self) -> List[Dict[str, Any]]:
        """Atomically select-and-mark users due the 24h activation email (#120).

        Eligible = signed up 23-25h ago, zero portfolios, not yet sent. A single
        UPDATE ... RETURNING claims the rows, so concurrent runners (multiple
        gunicorn workers, overlapping cron invocations) never double-send. The
        caller sends each email and, on failure, calls reset_activation_email_flag
        so the user retries on the next run (still inside the 23-25h window).

        Returns dicts: user_id, username, email (decrypted), ticker (most recent
        report ticker or None), language ('en' or 'he').
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET activation_email_sent = TRUE
                    WHERE user_id IN (
                        SELECT u.user_id
                        FROM users u
                        LEFT JOIN portfolios p ON p.user_id = u.user_id
                        WHERE u.created_at BETWEEN NOW() - INTERVAL '25 hours'
                                              AND NOW() - INTERVAL '23 hours'
                          AND p.portfolio_id IS NULL
                          AND u.activation_email_sent IS NOT TRUE
                    )
                    RETURNING user_id, username, email,
                              COALESCE(preferences, '{}') AS preferences
                    """
                )
                rows = [dict(r) for r in cur.fetchall()]
                ticker_by_user: Dict[str, str] = {}
                if rows:
                    cur.execute(
                        """
                        SELECT DISTINCT ON (user_id) user_id, ticker
                        FROM reports
                        WHERE user_id = ANY(%s)
                        ORDER BY user_id, created_at DESC
                        """,
                        ([r["user_id"] for r in rows],),
                    )
                    ticker_by_user = {
                        t["user_id"]: t["ticker"] for t in cur.fetchall()
                    }
                conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to claim activation email candidates: {e}")
        finally:
            self._release(conn)

        results: List[Dict[str, Any]] = []
        for row in rows:
            prefs = row.get("preferences") or {}
            language = (prefs.get("language") or "en").strip().lower()
            if language not in ("en", "he"):
                language = "en"
            results.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "email": decrypt(row["email"]) if row.get("email") else None,
                    "ticker": ticker_by_user.get(row["user_id"]),
                    "language": language,
                }
            )
        return results

    def reset_activation_email_flag(self, user_id: str) -> None:
        """Clear activation_email_sent so a failed send is retried next run (#120)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET activation_email_sent = FALSE WHERE user_id = %s",
                    (user_id,),
                )
                conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to reset activation email flag: {e}")
        finally:
            self._release(conn)

    def claim_weekly_digest_candidates(self) -> List[Dict[str, Any]]:
        """Atomically select-and-mark users due the weekly portfolio digest (#129).

        Eligible = has at least one holding with a positive quantity, has not
        turned off the 'weekly_summary' notification preference, and has not been
        sent a digest in the last 6 days. A single UPDATE ... RETURNING claims the
        rows by stamping weekly_digest_last_sent = CURRENT_DATE, so overlapping
        cron invocations never double-send. The caller computes and sends each
        digest and, on failure, calls reset_weekly_digest_flag so the user is
        retried on the next run.

        Returns dicts: user_id, username, email (decrypted), language ('en'/'he').
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET weekly_digest_last_sent = CURRENT_DATE
                    WHERE user_id IN (
                        SELECT u.user_id
                        FROM users u
                        WHERE EXISTS (
                            SELECT 1 FROM holdings h
                            JOIN portfolios p ON p.portfolio_id = h.portfolio_id
                            WHERE p.user_id = u.user_id
                              AND h.total_quantity > 0
                        )
                        AND COALESCE(
                            u.preferences -> 'notifications' ->> 'weekly_summary',
                            'true'
                        ) <> 'false'
                        AND (
                            u.weekly_digest_last_sent IS NULL
                            OR u.weekly_digest_last_sent < CURRENT_DATE - INTERVAL '6 days'
                        )
                    )
                    RETURNING user_id, username, email,
                              COALESCE(preferences, '{}') AS preferences
                    """
                )
                rows = [dict(r) for r in cur.fetchall()]
                conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to claim weekly digest candidates: {e}")
        finally:
            self._release(conn)

        results: List[Dict[str, Any]] = []
        for row in rows:
            prefs = row.get("preferences") or {}
            language = (prefs.get("language") or "en").strip().lower()
            if language not in ("en", "he"):
                language = "en"
            results.append(
                {
                    "user_id": row["user_id"],
                    "username": row["username"],
                    "email": decrypt(row["email"]) if row.get("email") else None,
                    "language": language,
                }
            )
        return results

    def reset_weekly_digest_flag(self, user_id: str) -> None:
        """Clear weekly_digest_last_sent so a failed send is retried next run (#129)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET weekly_digest_last_sent = NULL WHERE user_id = %s",
                    (user_id,),
                )
                conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to reset weekly digest flag: {e}")
        finally:
            self._release(conn)

    def claim_report_expiry_candidates(
        self, only_user_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Atomically select-and-mark reports due the 7-day expiry nudge (#130).

        Eligible report = created 7-14 days ago, not yet nudged, belongs to a
        user, is the newest report for that (user, ticker) pair (so superseded
        reports are skipped), and the user has not turned off the 'report_expiry'
        notification preference. A single UPDATE ... RETURNING claims the rows by
        stamping expiry_nudge_sent = TRUE, so overlapping cron invocations never
        double-send. The 7-14 day window also bounds the backlog on first deploy
        and gives a failed send several days of retry headroom (the caller calls
        reset_report_expiry_flag on failure).

        Pass only_user_id to restrict the claim to a single user -- used for safe
        testing so a live run can never touch real users' reports.

        Returns dicts: report_id, user_id, ticker, username, email (decrypted),
        language ('en' or 'he').
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                user_filter = "AND r.user_id = %s" if only_user_id else ""
                params = (only_user_id,) if only_user_id else ()
                cur.execute(
                    f"""
                    UPDATE reports
                    SET expiry_nudge_sent = TRUE
                    WHERE report_id IN (
                        SELECT r.report_id
                        FROM reports r
                        JOIN users u ON u.user_id = r.user_id
                        WHERE r.user_id IS NOT NULL
                          AND r.expiry_nudge_sent IS NOT TRUE
                          AND r.created_at <= NOW() - INTERVAL '7 days'
                          AND r.created_at >= NOW() - INTERVAL '14 days'
                          AND NOT EXISTS (
                              SELECT 1 FROM reports r2
                              WHERE r2.user_id = r.user_id
                                AND r2.ticker = r.ticker
                                AND r2.created_at > r.created_at
                          )
                          AND COALESCE(
                              u.preferences -> 'notifications' ->> 'report_expiry',
                              'true'
                          ) <> 'false'
                          {user_filter}
                    )
                    RETURNING report_id, user_id, ticker
                    """,
                    params,
                )
                rows = [dict(r) for r in cur.fetchall()]
                users_by_id: Dict[str, Dict[str, Any]] = {}
                if rows:
                    cur.execute(
                        """
                        SELECT user_id, username, email,
                               COALESCE(preferences, '{}') AS preferences
                        FROM users
                        WHERE user_id = ANY(%s)
                        """,
                        ([r["user_id"] for r in rows],),
                    )
                    users_by_id = {u["user_id"]: dict(u) for u in cur.fetchall()}
                conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to claim report expiry candidates: {e}")
        finally:
            self._release(conn)

        results: List[Dict[str, Any]] = []
        for row in rows:
            user = users_by_id.get(row["user_id"])
            if not user:
                continue
            prefs = user.get("preferences") or {}
            language = (prefs.get("language") or "en").strip().lower()
            if language not in ("en", "he"):
                language = "en"
            results.append(
                {
                    "report_id": row["report_id"],
                    "user_id": row["user_id"],
                    "ticker": row["ticker"],
                    "username": user.get("username"),
                    "email": decrypt(user["email"]) if user.get("email") else None,
                    "language": language,
                }
            )
        return results

    def reset_report_expiry_flag(self, report_id: str) -> None:
        """Clear expiry_nudge_sent so a failed send is retried next run (#130)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE reports SET expiry_nudge_sent = FALSE WHERE report_id = %s",
                    (report_id,),
                )
                conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to reset report expiry flag: {e}")
        finally:
            self._release(conn)

    def count_user_watchlist_items(self, user_id: str) -> int:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(wi.*)
                    FROM watchlist_items wi
                    JOIN watchlists w ON w.watchlist_id = wi.watchlist_id
                    WHERE w.user_id = %s
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to count watchlist items: {e}")
        finally:
            self._release(conn)

    def count_user_active_alerts(self, user_id: str) -> int:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM price_alerts "
                    "WHERE user_id = %s AND COALESCE(active, TRUE) = TRUE",
                    (user_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to count alerts: {e}")
        finally:
            self._release(conn)

    def set_user_telegram_chat_id(self, user_id: str, telegram_chat_id: str) -> None:
        """Persist Telegram chat_id on the users table (stored AES-256 encrypted)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET telegram_chat_id = %s WHERE user_id = %s",
                    (encrypt(str(telegram_chat_id)), user_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to set telegram_chat_id: {e}")
        finally:
            self._release(conn)

    def create_telegram_connect_token(self, user_id: str, ttl_minutes: int = 10) -> str:
        """Create a short-lived, one-time token for linking a Telegram chat to a user."""
        token = uuid.uuid4().hex
        expires_at = datetime.utcnow() + timedelta(minutes=int(ttl_minutes))
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO telegram_connect_tokens (token, user_id, expires_at)
                    VALUES (%s, %s, %s)
                    """,
                    (token, user_id, expires_at),
                )
            conn.commit()
            return token
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to create telegram connect token: {e}")
        finally:
            self._release(conn)

    def consume_telegram_connect_token(
        self, token: str, telegram_chat_id: str
    ) -> Optional[str]:
        """
        Atomically validate+consume a connect token and link the chat id.

        Returns:
            user_id if token was valid; otherwise None.
        """
        token = (token or "").strip()
        if not token:
            return None
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT token, user_id, expires_at
                    FROM telegram_connect_tokens
                    WHERE token = %s
                    """,
                    (token,),
                )
                row = cur.fetchone()
                if not row:
                    conn.rollback()
                    return None
                if row["expires_at"] < datetime.utcnow():
                    cur.execute(
                        "DELETE FROM telegram_connect_tokens WHERE token = %s", (token,)
                    )
                    conn.commit()
                    return None

                user_id = row["user_id"]
                cur.execute(
                    "UPDATE users SET telegram_chat_id = %s WHERE user_id = %s",
                    (str(telegram_chat_id), user_id),
                )
                cur.execute(
                    "DELETE FROM telegram_connect_tokens WHERE token = %s", (token,)
                )
            conn.commit()
            return user_id
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to consume telegram connect token: {e}")
        finally:
            self._release(conn)

    def get_report_usage_count(self, user_id: str, period: str) -> int:
        """Monthly report count for quota (period = YYYY-MM)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT report_count FROM report_usage
                    WHERE user_id = %s AND period = %s
                    """,
                    (user_id, period),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to read report usage: {e}")
        finally:
            self._release(conn)

    def increment_report_usage(
        self, user_id: str, period: Optional[str] = None
    ) -> None:
        """
        Count one successful report toward the user's monthly quota.
        No-op for pro users. Caller passes period (default: current UTC month).
        """
        if self.user_is_pro(user_id):
            return

        if period is None:
            period = datetime.now(timezone.utc).strftime("%Y-%m")
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO report_usage (user_id, period, report_count)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (user_id, period) DO UPDATE SET
                        report_count = report_usage.report_count + 1
                    """,
                    (user_id, period),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to increment report usage: {e}")
        finally:
            self._release(conn)

    # ==================== Holdings Methods ====================

    def create_holding(
        self, holding_id: str, portfolio_id: str, symbol: str, asset_type: str
    ):
        """Create a new holding."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO holdings (holding_id, portfolio_id, symbol, asset_type)
                    VALUES (%s, %s, %s, %s)
                """,
                    (holding_id, portfolio_id, symbol.upper(), asset_type),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to create holding: {e}")
        finally:
            self._release(conn)

    def get_holding(self, portfolio_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get a specific holding by portfolio and symbol."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT holding_id, portfolio_id, symbol, asset_type,
                           total_quantity, average_cost, total_cost_basis,
                           created_at, updated_at
                    FROM holdings WHERE portfolio_id = %s AND symbol = %s
                """,
                    (portfolio_id, symbol.upper()),
                )
                result = cur.fetchone()
            if result:
                result["total_quantity"] = Decimal(str(result["total_quantity"]))
                result["average_cost"] = Decimal(str(result["average_cost"]))
                result["total_cost_basis"] = Decimal(str(result["total_cost_basis"]))
            return result
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get holding: {e}")
        finally:
            self._release(conn)

    def get_holding_by_id(self, holding_id: str) -> Optional[Dict[str, Any]]:
        """Get a holding by its ID."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT holding_id, portfolio_id, symbol, asset_type,
                           total_quantity, average_cost, total_cost_basis,
                           created_at, updated_at
                    FROM holdings WHERE holding_id = %s
                """,
                    (holding_id,),
                )
                result = cur.fetchone()
            if result:
                result["total_quantity"] = Decimal(str(result["total_quantity"]))
                result["average_cost"] = Decimal(str(result["average_cost"]))
                result["total_cost_basis"] = Decimal(str(result["total_cost_basis"]))
            return result
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get holding: {e}")
        finally:
            self._release(conn)

    def get_holdings_for_ticker(
        self, user_id: str, symbol: str
    ) -> List[Dict[str, Any]]:
        """Get all holdings of a ticker across a user's portfolios in one query."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT h.holding_id, h.portfolio_id, h.symbol, h.asset_type,
                           h.total_quantity, h.average_cost, h.total_cost_basis,
                           p.name AS portfolio_name
                    FROM holdings h
                    JOIN portfolios p ON p.portfolio_id = h.portfolio_id
                    WHERE p.user_id = %s AND h.symbol = %s AND h.total_quantity > 0
                """,
                    (user_id, symbol.upper()),
                )
                results = cur.fetchall()
            for result in results:
                result["total_quantity"] = Decimal(str(result["total_quantity"]))
                result["average_cost"] = Decimal(str(result["average_cost"]))
                result["total_cost_basis"] = Decimal(str(result["total_cost_basis"]))
            return results
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get holdings for ticker: {e}")
        finally:
            self._release(conn)

    def get_holdings(self, portfolio_id: str) -> List[Dict[str, Any]]:
        """Get all holdings for a portfolio."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT holding_id, portfolio_id, symbol, asset_type,
                           total_quantity, average_cost, total_cost_basis,
                           created_at, updated_at
                    FROM holdings WHERE portfolio_id = %s ORDER BY symbol ASC
                """,
                    (portfolio_id,),
                )
                results = cur.fetchall()
            for result in results:
                result["total_quantity"] = Decimal(str(result["total_quantity"]))
                result["average_cost"] = Decimal(str(result["average_cost"]))
                result["total_cost_basis"] = Decimal(str(result["total_cost_basis"]))
            return results
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get holdings: {e}")
        finally:
            self._release(conn)

    def update_holding(
        self,
        holding_id: str,
        total_quantity: Decimal,
        average_cost: Decimal,
        total_cost_basis: Decimal,
    ):
        """Update holding totals."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE holdings
                    SET total_quantity = %s, average_cost = %s, total_cost_basis = %s
                    WHERE holding_id = %s
                """,
                    (
                        str(total_quantity),
                        str(average_cost),
                        str(total_cost_basis),
                        holding_id,
                    ),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update holding: {e}")
        finally:
            self._release(conn)

    def delete_holding(self, holding_id: str):
        """Delete a holding and all its transactions (cascade)."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM holdings WHERE holding_id = %s", (holding_id,))
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to delete holding: {e}")
        finally:
            self._release(conn)

    # ==================== Transaction Methods ====================

    def add_transaction(
        self,
        transaction_id: str,
        holding_id: str,
        transaction_type: str,
        quantity: Decimal,
        price_per_unit: Decimal,
        fees: Decimal,
        transaction_date: datetime,
        notes: str = "",
        import_source: str = "manual",
    ):
        """Add a transaction to a holding."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO transactions
                    (transaction_id, holding_id, transaction_type, quantity,
                     price_per_unit, fees, transaction_date, notes, import_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        transaction_id,
                        holding_id,
                        transaction_type,
                        str(quantity),
                        str(price_per_unit),
                        str(fees),
                        transaction_date,
                        notes,
                        import_source,
                    ),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to add transaction: {e}")
        finally:
            self._release(conn)

    def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        """Get a transaction by ID."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT transaction_id, holding_id, transaction_type, quantity,
                           price_per_unit, fees, transaction_date, notes, import_source, created_at
                    FROM transactions WHERE transaction_id = %s
                """,
                    (transaction_id,),
                )
                result = cur.fetchone()
            if result:
                result["quantity"] = Decimal(str(result["quantity"]))
                result["price_per_unit"] = Decimal(str(result["price_per_unit"]))
                result["fees"] = Decimal(str(result["fees"]))
            return result
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get transaction: {e}")
        finally:
            self._release(conn)

    def get_transactions(self, holding_id: str) -> List[Dict[str, Any]]:
        """Get all transactions for a holding."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT transaction_id, holding_id, transaction_type, quantity,
                           price_per_unit, fees, transaction_date, notes, import_source, created_at
                    FROM transactions WHERE holding_id = %s ORDER BY transaction_date ASC
                """,
                    (holding_id,),
                )
                results = cur.fetchall()
            for result in results:
                result["quantity"] = Decimal(str(result["quantity"]))
                result["price_per_unit"] = Decimal(str(result["price_per_unit"]))
                result["fees"] = Decimal(str(result["fees"]))
            return results
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get transactions: {e}")
        finally:
            self._release(conn)

    def delete_transaction(self, transaction_id: str):
        """Delete a transaction."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM transactions WHERE transaction_id = %s",
                    (transaction_id,),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to delete transaction: {e}")
        finally:
            self._release(conn)

    def get_all_portfolio_transactions(self, portfolio_id: str) -> List[Dict[str, Any]]:
        """Get all transactions for a portfolio joined with holding symbol and asset_type."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT t.transaction_id, t.holding_id, t.transaction_type, t.quantity,
                           t.price_per_unit, t.fees, t.transaction_date, t.notes,
                           h.symbol, h.asset_type
                    FROM transactions t
                    JOIN holdings h ON t.holding_id = h.holding_id
                    WHERE h.portfolio_id = %s
                    ORDER BY t.transaction_date ASC
                """,
                    (portfolio_id,),
                )
                results = cur.fetchall()
            for result in results:
                result["quantity"] = Decimal(str(result["quantity"]))
                result["price_per_unit"] = Decimal(str(result["price_per_unit"]))
                result["fees"] = Decimal(str(result["fees"]))
            return results
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to get portfolio transactions: {e}")
        finally:
            self._release(conn)

    # ==================== Watchlist Methods ====================

    def create_watchlist(self, watchlist_id, user_id, name="My Watchlist"):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watchlists (watchlist_id, user_id, name) VALUES (%s, %s, %s)",
                    (watchlist_id, user_id, name),
                )
            conn.commit()
        finally:
            self._release(conn)

    def get_watchlist(self, watchlist_id):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM watchlists WHERE watchlist_id = %s", (watchlist_id,)
                )
                return cur.fetchone()
        finally:
            self._release(conn)

    def list_watchlists(self, user_id):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM watchlists WHERE user_id = %s ORDER BY position, created_at",
                    (user_id,),
                )
                return cur.fetchall()
        finally:
            self._release(conn)

    def update_watchlist(self, watchlist_id, name):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE watchlists SET name = %s WHERE watchlist_id = %s",
                    (name, watchlist_id),
                )
            conn.commit()
        finally:
            self._release(conn)

    def delete_watchlist(self, watchlist_id):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM watchlists WHERE watchlist_id = %s", (watchlist_id,)
                )
            conn.commit()
        finally:
            self._release(conn)

    # ── Section CRUD ─────────────────────────────────────────────

    def create_section(self, section_id, watchlist_id, name, position=0):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watchlist_sections (section_id, watchlist_id, name, position) VALUES (%s, %s, %s, %s)",
                    (section_id, watchlist_id, name, position),
                )
            conn.commit()
        finally:
            self._release(conn)

    def list_sections(self, watchlist_id):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM watchlist_sections WHERE watchlist_id = %s ORDER BY position, created_at",
                    (watchlist_id,),
                )
                return cur.fetchall()
        finally:
            self._release(conn)

    def update_section(self, section_id, name):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE watchlist_sections SET name = %s WHERE section_id = %s",
                    (name, section_id),
                )
            conn.commit()
        finally:
            self._release(conn)

    def delete_section(self, section_id):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM watchlist_sections WHERE section_id = %s",
                    (section_id,),
                )
            conn.commit()
        finally:
            self._release(conn)

    # ── Item CRUD ────────────────────────────────────────────────

    def add_watchlist_item(
        self,
        item_id,
        watchlist_id,
        symbol,
        asset_type,
        display_name=None,
        section_id=None,
    ):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watchlist_items (item_id, watchlist_id, section_id, symbol, asset_type, display_name) VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        item_id,
                        watchlist_id,
                        section_id,
                        symbol,
                        asset_type,
                        display_name,
                    ),
                )
            conn.commit()
        finally:
            self._release(conn)

    def remove_watchlist_item(self, item_id):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM watchlist_items WHERE item_id = %s", (item_id,)
                )
            conn.commit()
        finally:
            self._release(conn)

    def get_watchlist_items(self, watchlist_id):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT wi.*, ws.name AS section_name, ws.position AS section_position
                    FROM watchlist_items wi
                    LEFT JOIN watchlist_sections ws ON wi.section_id = ws.section_id
                    WHERE wi.watchlist_id = %s
                    ORDER BY ws.position, ws.created_at, wi.position, wi.created_at
                """,
                    (watchlist_id,),
                )
                return cur.fetchall()
        finally:
            self._release(conn)

    def move_item_to_section(self, item_id, section_id):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE watchlist_items SET section_id = %s WHERE item_id = %s",
                    (section_id, item_id),
                )
            conn.commit()
        finally:
            self._release(conn)

    def set_item_pinned(self, item_id, is_pinned):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE watchlist_items SET is_pinned = %s WHERE item_id = %s",
                    (bool(is_pinned), item_id),
                )
            conn.commit()
        finally:
            self._release(conn)

    def get_pinned_items(self, user_id):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT wi.*
                    FROM watchlist_items wi
                    JOIN watchlists wl ON wi.watchlist_id = wl.watchlist_id
                    WHERE wl.user_id = %s AND wi.is_pinned = TRUE
                    ORDER BY wi.created_at
                    LIMIT 3
                """,
                    (user_id,),
                )
                return cur.fetchall()
        finally:
            self._release(conn)

    def count_pinned_items(self, user_id):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM watchlist_items wi
                    JOIN watchlists wl ON wi.watchlist_id = wl.watchlist_id
                    WHERE wl.user_id = %s AND wi.is_pinned = TRUE
                """,
                    (user_id,),
                )
                row = cur.fetchone()
                return row["cnt"] if row else 0
        finally:
            self._release(conn)

    def get_all_watched_symbols(self):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT DISTINCT symbol, asset_type FROM watchlist_items")
                return cur.fetchall()
        finally:
            self._release(conn)

    def get_ticker_notes(self, user_id: str, symbol: str) -> list:
        """Return all notes for a user+ticker, newest first."""
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, title, content, created_at
                    FROM ticker_notes
                    WHERE user_id = %s AND symbol = %s
                    ORDER BY created_at DESC
                    """,
                    (user_id, symbol.upper()),
                )
                return cur.fetchall()
        finally:
            self._release(conn)

    def create_ticker_note(
        self, user_id: str, symbol: str, title: str, content: str
    ) -> None:
        """Insert a new note for a user+ticker."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ticker_notes (user_id, symbol, title, content)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (user_id, symbol.upper(), title or "", content or ""),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to create ticker note: {e}")
        finally:
            if conn:
                self._release(conn)

    def update_ticker_note(
        self, note_id: int, user_id: str, title: str, content: str
    ) -> None:
        """Update an existing note — ownership enforced via user_id."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ticker_notes
                    SET title = %s, content = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND user_id = %s
                    """,
                    (title or "", content or "", note_id, user_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update ticker note: {e}")
        finally:
            if conn:
                self._release(conn)

    def delete_ticker_note(self, note_id: int, user_id: str) -> None:
        """Delete a note — ownership enforced via user_id."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ticker_notes WHERE id = %s AND user_id = %s",
                    (note_id, user_id),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to delete ticker note: {e}")
        finally:
            if conn:
                self._release(conn)

    def get_watched_symbols_for_user(self, user_id):
        """Return all watched symbols for a specific user."""
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT DISTINCT wi.symbol, wi.asset_type
                    FROM watchlist_items wi
                    JOIN watchlists w ON wi.watchlist_id = w.watchlist_id
                    WHERE w.user_id = %s
                """,
                    (user_id,),
                )
                return cur.fetchall()
        finally:
            self._release(conn)

    # ── Price Cache ──────────────────────────────────────────────

    def upsert_price_cache(
        self, symbol, asset_type, price, change_percent, display_name=None, currency="USD"
    ):
        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO price_cache (symbol, asset_type, price, change_percent, display_name, currency, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (symbol) DO UPDATE SET
                        price          = EXCLUDED.price,
                        change_percent = EXCLUDED.change_percent,
                        display_name   = COALESCE(EXCLUDED.display_name, price_cache.display_name),
                        currency       = EXCLUDED.currency,
                        last_updated   = NOW()
                """,
                    (symbol, asset_type, price, change_percent, display_name, currency),
                )

        self._run_write(op)
        try:
            if os.getenv("STOCKPRO_ALERT_EVAL_ENABLED", "true").lower() in (
                "1",
                "true",
                "yes",
            ):
                from alerts.evaluation import evaluate_alerts_for_symbols

                evaluate_alerts_for_symbols(self, [symbol])
        except Exception:
            logger.exception("price alert evaluation failed after cache upsert")

    def get_cached_prices(self, symbols):
        if not symbols:
            return {}
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                placeholders = ",".join(["%s"] * len(symbols))
                cur.execute(
                    f"SELECT * FROM price_cache WHERE symbol IN ({placeholders})",
                    list(symbols),
                )
                rows = cur.fetchall()
                return {row["symbol"]: row for row in rows}
        finally:
            self._release(conn)

    def get_all_cached_prices(self):
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM price_cache")
                rows = cur.fetchall()
                return {row["symbol"]: row for row in rows}
        finally:
            self._release(conn)

    # ── Ticker Public View ───────────────────────────────────────

    def upsert_ticker_public_view(
        self,
        symbol: str,
        summary_md: Optional[str] = None,
        bullish_pct: Optional[int] = None,
        top_themes: Optional[list] = None,
        reddit_posts: Optional[list] = None,
        x_posts: Optional[list] = None,
        status: str = "ready",
        error_message: Optional[str] = None,
    ):
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ticker_public_view (
                        symbol, summary_md, bullish_pct, top_themes_json,
                        reddit_posts, x_posts, status, error_message, last_updated
                    )
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, NOW())
                    ON CONFLICT (symbol) DO UPDATE SET
                        summary_md      = EXCLUDED.summary_md,
                        bullish_pct     = EXCLUDED.bullish_pct,
                        top_themes_json = EXCLUDED.top_themes_json,
                        reddit_posts    = EXCLUDED.reddit_posts,
                        x_posts         = EXCLUDED.x_posts,
                        status          = EXCLUDED.status,
                        error_message   = EXCLUDED.error_message,
                        last_updated    = NOW()
                    """,
                    (
                        symbol.upper(),
                        summary_md,
                        bullish_pct,
                        json.dumps(top_themes) if top_themes is not None else None,
                        json.dumps(reddit_posts) if reddit_posts is not None else None,
                        json.dumps(x_posts) if x_posts is not None else None,
                        status,
                        error_message,
                    ),
                )
            conn.commit()
        finally:
            self._release(conn)

    def get_ticker_public_view(self, symbol: str) -> Optional[dict]:
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM ticker_public_view WHERE symbol = %s",
                    (symbol.upper(),),
                )
                return cur.fetchone()
        finally:
            self._release(conn)

    def get_stale_public_view_symbols(self, ttl_hours: int = 24) -> list:
        """Return symbols whose public view is older than ttl_hours or in 'error' state."""
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT symbol FROM ticker_public_view
                    WHERE last_updated IS NULL
                       OR last_updated < NOW() - (%s || ' hours')::interval
                    """,
                    (str(ttl_hours),),
                )
                return [row["symbol"] for row in cur.fetchall()]
        finally:
            self._release(conn)

    # ==================== Price alerts ====================

    def create_price_alert(
        self,
        alert_id: str,
        user_id: str,
        symbol: str,
        direction: str,
        target_price: float,
        asset_type: str = "stock",
    ) -> None:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO price_alerts
                    (alert_id, user_id, symbol, asset_type, direction, target_price)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (alert_id, user_id, symbol, asset_type, direction, target_price),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to create price alert: {e}")
        finally:
            if conn:
                self._release(conn)

    def list_price_alerts_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        conn = self.get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM price_alerts
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                )
                return list(cur.fetchall())
        finally:
            self._release(conn)

    def delete_price_alert(self, alert_id: str, user_id: str) -> bool:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM price_alerts
                    WHERE alert_id = %s AND user_id = %s
                    """,
                    (alert_id, user_id),
                )
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to delete price alert: {e}")
        finally:
            if conn:
                self._release(conn)

    def set_price_alert_active(self, alert_id: str, user_id: str, active: bool) -> bool:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE price_alerts SET active = %s
                    WHERE alert_id = %s AND user_id = %s
                    """,
                    (active, alert_id, user_id),
                )
                ok = cur.rowcount > 0
            conn.commit()
            return ok
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update price alert: {e}")
        finally:
            if conn:
                self._release(conn)

    def list_active_alerts_for_symbols(
        self, symbols: List[str]
    ) -> List[Dict[str, Any]]:
        if not symbols:
            return []
        norm = [s.upper() for s in symbols]

        def op(conn):
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM price_alerts
                    WHERE active = TRUE AND symbol = ANY(%s)
                    """,
                    (norm,),
                )
                return list(cur.fetchall())

        return self._run_read(op)

    def record_price_alert_trigger(
        self,
        notification_id: str,
        user_id: str,
        alert_id: str,
        symbol: str,
        body: str,
    ) -> None:
        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO price_alert_notifications
                    (notification_id, user_id, alert_id, symbol, body)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (notification_id, user_id, alert_id, symbol, body),
                )
                cur.execute(
                    """
                    UPDATE price_alerts
                    SET last_triggered_at = NOW(), active = FALSE
                    WHERE alert_id = %s
                    """,
                    (alert_id,),
                )

        try:
            self._run_write(op)
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to record price alert trigger: {e}")

    def list_price_alert_notifications_for_user(
        self, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        def op(conn):
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM price_alert_notifications
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                return list(cur.fetchall())

        return self._run_read(op)

    def count_unread_price_alert_notifications(self, user_id: str) -> int:
        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM price_alert_notifications
                    WHERE user_id = %s AND read_at IS NULL
                    """,
                    (user_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0

        return self._run_read(op)

    def mark_price_alert_notification_read(
        self, notification_id: str, user_id: str
    ) -> bool:
        def op(conn):
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE price_alert_notifications
                    SET read_at = NOW()
                    WHERE notification_id = %s AND user_id = %s AND read_at IS NULL
                    """,
                    (notification_id, user_id),
                )
                return cur.rowcount > 0

        try:
            return self._run_write(op)
        except psycopg2.Error as e:
            raise RuntimeError(f"Failed to mark notification read: {e}")

    def mark_all_price_alert_notifications_read(self, user_id: str) -> int:
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE price_alert_notifications SET read_at = NOW() WHERE user_id = %s AND read_at IS NULL",
                    (user_id,),
                )
                count = cur.rowcount
            conn.commit()
            return count
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to mark all notifications read: {e}")
        finally:
            if conn:
                self._release(conn)

    # ==================== CSV Import Logging ====================

    def log_csv_import(
        self,
        import_id: str,
        portfolio_id: str,
        filename: str,
        row_count: int,
        success_count: int,
        error_count: int,
        errors_json: List[Dict[str, Any]],
    ):
        """Log a CSV import operation."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO csv_imports
                    (import_id, portfolio_id, filename, row_count, success_count, error_count, errors_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                    (
                        import_id,
                        portfolio_id,
                        filename,
                        row_count,
                        success_count,
                        error_count,
                        json.dumps(errors_json) if errors_json else None,
                    ),
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to log CSV import: {e}")
        finally:
            self._release(conn)

    # --- Multi-worker shared state: generation_status + generation_events ---

    _GEN_STATUS_FIELDS = {
        "status",
        "report_id",
        "progress",
        "step",
        "step_code",
        "done",
        "total",
        "partial",
        "message",
        "questions",
        "subjects",
        "failed_subjects",
        "ticker",
        "trade_type",
    }
    _GEN_STATUS_JSON_FIELDS = {"questions", "subjects", "failed_subjects"}

    def set_generation_status(
        self, session_id: str, user_id: str, **fields: Any
    ) -> None:
        """UPSERT a row in generation_status. Fields must be in _GEN_STATUS_FIELDS."""
        cols = ["session_id", "user_id"]
        vals: List[Any] = [session_id, user_id]
        for k, v in fields.items():
            if k not in self._GEN_STATUS_FIELDS:
                raise ValueError(f"Unknown generation_status field: {k}")
            cols.append(k)
            vals.append(json.dumps(v) if k in self._GEN_STATUS_JSON_FIELDS else v)

        placeholders = ", ".join(["%s"] * len(cols))
        col_list = ", ".join(cols)
        update_cols = [c for c in cols if c not in ("session_id", "user_id")]
        update_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        if update_clause:
            update_clause += ", updated_at=NOW()"
        else:
            update_clause = "updated_at=NOW()"

        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO generation_status ({col_list})
                    VALUES ({placeholders})
                    ON CONFLICT (session_id) DO UPDATE SET {update_clause}
                    """,
                    vals,
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to set generation_status: {e}") from e
        finally:
            self._release(conn)

    def get_generation_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a generation_status row as a dict, or None if missing."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, user_id, status, report_id, progress, step,
                           step_code, done, total, partial, message, questions,
                           subjects, failed_subjects, ticker, trade_type,
                           created_at, updated_at, expires_at
                    FROM generation_status
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
        finally:
            self._release(conn)

    def update_generation_status(self, session_id: str, **fields: Any) -> None:
        """UPDATE specific fields on an existing generation_status row."""
        if not fields:
            return
        sets: List[str] = []
        vals: List[Any] = []
        for k, v in fields.items():
            if k not in self._GEN_STATUS_FIELDS:
                raise ValueError(f"Unknown generation_status field: {k}")
            sets.append(f"{k}=%s")
            vals.append(json.dumps(v) if k in self._GEN_STATUS_JSON_FIELDS else v)
        sets.append("updated_at=NOW()")
        vals.append(session_id)

        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE generation_status SET {', '.join(sets)} WHERE session_id=%s",
                    vals,
                )
            conn.commit()
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to update generation_status: {e}") from e
        finally:
            self._release(conn)

    def append_generation_event(
        self, session_id: str, event_type: str, payload: Dict[str, Any]
    ) -> int:
        """Append an SSE event for a session and return its monotonic seq."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO generation_events (session_id, seq, event_type, payload)
                    VALUES (
                        %s,
                        COALESCE(
                            (SELECT MAX(seq) FROM generation_events WHERE session_id = %s),
                            0
                        ) + 1,
                        %s,
                        %s
                    )
                    RETURNING seq
                    """,
                    (session_id, session_id, event_type, json.dumps(payload)),
                )
                seq = cur.fetchone()[0]
            conn.commit()
            return int(seq)
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to append generation_event: {e}") from e
        finally:
            self._release(conn)

    def read_generation_events_since(
        self, session_id: str, last_seq: int
    ) -> List[Dict[str, Any]]:
        """Return events with seq > last_seq, ordered by seq."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT seq, event_type, payload
                    FROM generation_events
                    WHERE session_id = %s AND seq > %s
                    ORDER BY seq
                    """,
                    (session_id, last_seq),
                )
                rows = cur.fetchall()
                return [
                    {"seq": int(r[0]), "event_type": r[1], "payload": r[2]}
                    for r in rows
                ]
        finally:
            self._release(conn)

    def evict_stale_generation_data(self) -> int:
        """Delete expired generation_status rows + their events. Returns rows deleted."""
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM generation_events
                    WHERE session_id IN (
                        SELECT session_id FROM generation_status WHERE expires_at < NOW()
                    )
                    """
                )
                cur.execute(
                    "DELETE FROM generation_status WHERE expires_at < NOW()"
                )
                deleted = cur.rowcount or 0
            conn.commit()
            return deleted
        except psycopg2.Error as e:
            if conn:
                conn.rollback()
            raise RuntimeError(f"Failed to evict stale generation data: {e}") from e
        finally:
            self._release(conn)


# Global instance (thread-safe singleton — concurrent init_schema causes PostgreSQL
# "tuple concurrently updated" when DDL runs from multiple connections at once)
_db_manager: Optional[DatabaseManager] = None
_db_manager_lock = threading.Lock()


def get_database_manager() -> DatabaseManager:
    """Get or create global database manager instance."""
    global _db_manager
    if _db_manager is None:
        with _db_manager_lock:
            if _db_manager is None:
                _db_manager = DatabaseManager()
                _db_manager.init_schema()
    return _db_manager
