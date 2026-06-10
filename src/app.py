"""
Flask web interface for the Stock Research AI Agent.
"""

import sys
from pathlib import Path

# Add project root to Python path to allow imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    Response,
    abort,
    send_from_directory,
)
from flask_wtf.csrf import CSRFProtect
from flask_cors import CORS
from clerk_backend_api import Clerk as ClerkClient, AuthenticateRequestOptions
import os
import re
import threading
import queue
import json
import time
import requests
from functools import wraps
from dotenv import load_dotenv
import uuid
import bleach
import markdown as md_lib
from markupsafe import Markup
from decimal import Decimal
from datetime import datetime, timedelta
from psycopg2.extras import RealDictCursor

from orchestrator_graph import OrchestratorSession, create_session
from langsmith_service import create_emitter
from portfolio.portfolio_service import get_portfolio_service
from portfolio.history_service import get_history_service
from watchlist.watchlist_service import get_watchlist_service
from data_providers import DataProviderFactory
from report_storage import ReportStorage
from pdf_generator import get_pdf_generator
from currency_utils import convert_to_usd, detect_currency

# Load environment variables
load_dotenv()

# Clerk auth client
_clerk_secret_key_raw = os.getenv("CLERK_SECRET_KEY")
if not _clerk_secret_key_raw:
    raise RuntimeError("CLERK_SECRET_KEY environment variable is not set")
_clerk_jwt_key_raw = os.getenv("CLERK_JWT_KEY")
if not _clerk_jwt_key_raw:
    raise RuntimeError("CLERK_JWT_KEY environment variable is not set")
clerk_client = ClerkClient(bearer_auth=_clerk_secret_key_raw)
CLERK_JWT_KEY = _clerk_jwt_key_raw.replace("\\n", "\n")

# Create Flask app
# Set template and static folders explicitly to point to project root
app = Flask(
    __name__,
    template_folder=str(project_root / "templates"),
    static_folder=str(project_root / "static"),
)
_secret_key = os.getenv("FLASK_SECRET_KEY")
if not _secret_key:
    raise RuntimeError("FLASK_SECRET_KEY environment variable is not set")
app.secret_key = _secret_key

# Session cookie hardening — SECURE flag requires HTTPS (disabled in local dev)
_is_production = os.getenv("FLASK_ENV", "development") != "development"
app.config["SESSION_COOKIE_HTTPONLY"] = True        # JS cannot read the cookie
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"      # blocks cross-site request forgery
app.config["SESSION_COOKIE_SECURE"] = _is_production  # HTTPS-only in prod

csrf = CSRFProtect(app)

# Module-level DB manager — used by the new generation_status / generation_events
# multi-worker shared-state helpers. Existing code paths still create local
# `db = get_database_manager()` bindings; both reference the same singleton.
from database import get_database_manager as _bootstrap_get_database_manager
db = _bootstrap_get_database_manager()

# Exempt all /api/* routes from CSRF — authenticated via Clerk Bearer token.
# Flask-WTF's csrf_protect runs as the LAST before_request hook added by init_app.
# We replace it with a wrapper that skips /api/ paths.
def _wrap_api_csrf_exempt():
    _hooks = app.before_request_funcs.setdefault(None, [])
    if _hooks:
        _orig = _hooks[-1]  # The csrf_protect hook just registered

        def _patched():
            if request.path.startswith('/api/'):
                return  # Bearer token auth is sufficient; no CSRF needed
            return _orig()

        _hooks[-1] = _patched


_wrap_api_csrf_exempt()

# Allow React dev server (port 3000) to call Flask during development
CORS(app, origins=["http://localhost:3000"], supports_credentials=True)

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    get_remote_address, app=app, default_limits=[], storage_uri="memory://"
)


@limiter.request_filter
def _skip_rate_limits_in_tests():
    """Pytest sets TESTING=True; avoid 429s on routes under tight limits."""
    from flask import current_app

    return bool(current_app.config.get("TESTING"))


# Tunable via env for staging/production (defaults match Phase 1 roadmap abuse protection)
def _research_rate_limit():
    return os.getenv("STOCKPRO_RATE_LIMIT_RESEARCH", "30 per hour")


def _report_gen_rate_limit():
    return os.getenv("STOCKPRO_RATE_LIMIT_REPORT_GEN", "15 per hour")


def _report_post_rate_limit():
    return os.getenv("STOCKPRO_RATE_LIMIT_GENERATE_REPORT", "20 per hour")


def _popup_start_rate_limit():
    return os.getenv("STOCKPRO_RATE_LIMIT_POPUP_START", "60 per hour")


def _continue_conversation_rate_limit():
    """Limit POST /continue (chat agent turns + SSE) — same abuse class as research."""
    return os.getenv("STOCKPRO_RATE_LIMIT_CONTINUE", "60 per hour")


def _chat_report_rate_limit():
    """Limit POST /chat_report (Q&A against stored report)."""
    return os.getenv("STOCKPRO_RATE_LIMIT_CHAT_REPORT", "40 per hour")


def sse_user_facing_error(exc: BaseException) -> str:
    """Build a short SSE error string for the chat UI (no stack traces; cap API noise)."""
    msg = (str(exc) or "").strip()
    if not msg:
        return "Something went wrong. Please try again."
    max_len = 500
    if len(msg) > max_len:
        return msg[: max_len - 3] + "..."
    return msg


def flash_status(message: str, status_type: str = "info"):
    """Set a status message and type in the session for the next page render.
    status_type: 'success', 'error', or 'info'
    """
    session["status_message"] = message
    session["status_type"] = status_type


def pop_status():
    """Pop status_message and status_type from the session, returning a dict."""
    return {
        "status_message": session.pop("status_message", None),
        "status_type": session.pop("status_type", "info"),
    }


def _wants_json() -> bool:
    """Return True if the caller wants a JSON response (React SPA or API client)."""
    return (
        request.args.get("format") == "json"
        or "application/json" in request.headers.get("Accept", "")
    )


def _free_tier_quota_message() -> str:
    from report_usage import get_free_tier_report_limit

    limit = get_free_tier_report_limit()
    if limit == 3:
        return "You've used your 3 free reports this month."
    return f"You've used your {limit} free reports this month."


def _session_hits_report_quota() -> bool:
    uid = session.get("user_id")
    if not uid:
        return False
    from database import get_database_manager
    from report_usage import quota_exceeded_for_user

    db = get_database_manager()
    exceeded, _, _ = quota_exceeded_for_user(db, uid)
    return exceeded


def _report_quota_json_error():
    return (
        jsonify({"error": "limit_reached", "message": _free_tier_quota_message()}),
        403,
    )


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" in session:
            return f(*args, **kwargs)

        # Accept long-lived StockPro API tokens (CLI / headless agents).
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer sp_"):
            from auth_tokens import verify_token

            raw_token = auth_header.split(" ", 1)[1].strip()
            user_id = verify_token(raw_token)
            if user_id:
                session["user_id"] = user_id
                return f(*args, **kwargs)
            return jsonify({"error": "Unauthorized"}), 401

        # Verify Clerk session token via authenticate_request
        request_state = clerk_client.authenticate_request(
            request, AuthenticateRequestOptions(jwt_key=CLERK_JWT_KEY)
        )
        if request_state.is_authenticated:
            clerk_user_id = request_state.payload["sub"]
            if "user_id" not in session or session["user_id"] != clerk_user_id:
                # Upsert user in PostgreSQL (Supabase-compatible)
                from database import get_database_manager

                db = get_database_manager()
                user = db.get_user_by_id(clerk_user_id)
                if not user:
                    clerk_user = clerk_client.users.get(user_id=clerk_user_id)
                    email = ""
                    username = clerk_user_id
                    if clerk_user.email_addresses:
                        email = clerk_user.email_addresses[0].email_address or ""
                    if clerk_user.username:
                        username = clerk_user.username
                    elif clerk_user.first_name or clerk_user.last_name:
                        username = (
                            f"{clerk_user.first_name or ''}{clerk_user.last_name or ''}".strip()
                            or clerk_user_id
                        )
                    db.create_user(
                        user_id=clerk_user_id, username=username, email=email
                    )
                else:
                    username = user.get("username", clerk_user_id)
                session["user_id"] = clerk_user_id
                session["username"] = username
                # Warm price cache in background — fire and forget
                threading.Thread(
                    target=_warm_portfolio_cache, args=(clerk_user_id,), daemon=True
                ).start()
                get_or_create_session_id()
            # Update last_active_at (fire-and-forget, non-blocking)
            _uid = session.get("user_id")
            if _uid:
                try:
                    from database import get_database_manager as _get_db
                    _db = _get_db()
                    _db.update_last_active(_uid)
                except Exception:
                    pass  # non-critical
            return f(*args, **kwargs)
        app.logger.warning("Clerk auth failed: %s", request_state.reason)
        if _wants_json() or request.headers.get("Authorization"):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("sign_in"))

    return decorated


def admin_required(f):
    """Decorator for admin-only API endpoints. Verifies Clerk JWT and checks
    publicMetadata.role == 'admin'. Returns 403 if not admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        request_state = clerk_client.authenticate_request(
            request, AuthenticateRequestOptions(jwt_key=CLERK_JWT_KEY)
        )
        if not request_state.is_authenticated:
            return jsonify({"error": "Unauthorized"}), 401
        clerk_user_id = request_state.payload["sub"]
        # Fetch full Clerk user to check publicMetadata
        try:
            clerk_user = clerk_client.users.get(user_id=clerk_user_id)
            metadata = clerk_user.public_metadata or {}
            if metadata.get("role") != "admin":
                return jsonify({"error": "Forbidden"}), 403
        except Exception:
            return jsonify({"error": "Forbidden"}), 403
        # Attach admin user id for use in endpoint
        request.admin_user_id = clerk_user_id
        return f(*args, **kwargs)
    return decorated


@app.context_processor
def inject_user():
    return {
        "current_user": {
            "is_authenticated": "user_id" in session,
            "user_id": session.get("user_id"),
            "username": session.get("username"),
        },
        "clerk_publishable_key": os.getenv("CLERK_PUBLISHABLE_KEY", ""),
    }


@app.context_processor
def inject_active_research_session():
    sid = session.get("session_id")
    if sid:
        try:
            row = db.get_generation_status(sid)
            if row and row.get("status") == "in_progress":
                return {"active_research_session_id": sid}
        except Exception:
            pass
    return {"active_research_session_id": None}


_MD_ALLOWED_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "code",
    "blockquote",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "hr",
    "br",
    "ul",
    "ol",
    "li",
]
_MD_ALLOWED_ATTRS = {**bleach.sanitizer.ALLOWED_ATTRIBUTES, "*": ["class"]}


@app.template_filter("currency")
def currency_filter(value):
    try:
        return "{:,.2f}".format(float(value))
    except (ValueError, TypeError):
        return "0.00"


@app.template_filter("markdown")
def markdown_filter(text):
    raw_html = md_lib.markdown(
        text or "", extensions=["tables", "fenced_code", "nl2br", "sane_lists"]
    )
    return Markup(
        bleach.clean(
            raw_html, tags=_MD_ALLOWED_TAGS, attributes=_MD_ALLOWED_ATTRS, strip=True
        )
    )


@app.template_filter("markdown_preview")
def markdown_preview_filter(text, length=250):
    if not text:
        return ""
    text = re.sub(r"#{1,6}\s+", "", text)
    text = re.sub(r"\*{1,2}([^*\n]+)\*{1,2}", r"\1", text)
    text = re.sub(r"_([^_\n]+)_", r"\1", text)
    text = re.sub(r"`[^`\n]+`", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"^[-*+]\s+", "", text, flags=re.MULTILINE)
    text = " ".join(text.split())
    return text[:length] + ("..." if len(text) > length else "")


def _page_range(current, total, delta=2):
    pages = sorted(
        {1, total}
        | set(range(max(1, current - delta), min(total, current + delta) + 1))
    )
    result, prev = [], None
    for p in pages:
        if prev and p - prev > 1:
            result.append(None)  # None = ellipsis
        result.append(p)
        prev = p
    return result


# Global agent instances (keyed by session ID)
agent_sessions = {}

# SSE events now live in the Postgres `generation_events` table so a producer
# thread on one gunicorn worker can stream to a consumer connected to another.
# Report-generation status lives in `generation_status` (see DatabaseManager
# helpers: get_generation_status / set_generation_status / update_generation_status).


class _PgEventQueue:
    """Drop-in replacement for queue.Queue used by StepEmitter / SSE producer.

    `put_nowait` and `put` both append a row to `generation_events` keyed by
    session_id. Consumers (the /stream/<id> SSE endpoint) poll via
    db.read_generation_events_since().
    """

    __slots__ = ("session_id",)

    def __init__(self, session_id: str):
        self.session_id = session_id

    def _append(self, item: dict) -> None:
        try:
            event_type = (item or {}).get("type") or "step"
            db.append_generation_event(self.session_id, event_type, item or {})
        except Exception as e:
            app.logger.warning("PgEventQueue append failed for %s: %s", self.session_id, e)

    def put_nowait(self, item: dict) -> None:
        self._append(item)

    def put(self, item: dict, block: bool = True, timeout: float | None = None) -> None:
        self._append(item)


def _step_code_for(progress, english_step: str) -> str:
    """Map an English step string from the agent into a stable, locale-free code.

    Frontend uses this to render a translated label.
    """
    if progress is None:
        return "researching"
    s = (english_step or "").lower()
    if "starting" in s:
        return "starting"
    if "planning" in s:
        return "planning"
    if "researching" in s and "subjects" in s:
        return "researching"
    if "synthesiz" in s:
        return "synthesizing"
    if "saving" in s:
        return "saving"
    if "ready" in s:
        return "ready"
    return "working"

# Per-worker creation timestamps used to evict in-memory `agent_sessions`
# (LangGraph orchestrator instances). Postgres `generation_status.expires_at`
# handles TTL for the cross-worker shared status data.
_session_created_at: dict = {}


def _evict_stale_sessions(max_age_seconds: int = 86400):
    cutoff = time.time() - max_age_seconds
    stale = [sid for sid, t in _session_created_at.items() if t < cutoff]
    for sid in stale:
        agent_sessions.pop(sid, None)
        _session_created_at.pop(sid, None)
    # Clean up Postgres-side expired rows opportunistically.
    try:
        db.evict_stale_generation_data()
    except Exception as e:
        app.logger.warning("evict_stale_generation_data failed: %s", e)


def initialize_session(session_id: str) -> OrchestratorSession:
    """Initialize or get orchestrator session."""
    if session_id not in agent_sessions:
        _evict_stale_sessions()
        _session_created_at[session_id] = time.time()
        try:
            agent_sessions[session_id] = create_session()
        except Exception as e:
            raise ValueError(f"Failed to initialize session: {str(e)}")
    return agent_sessions[session_id]


def get_or_create_session_id():
    """Get or create a session ID for the current user."""
    if "session_id" not in session:
        session["session_id"] = str(uuid.uuid4())
    return session["session_id"]


def _warm_portfolio_cache(user_id):
    """Background warm: pre-fetch prices for portfolio + watchlist into price_cache."""
    try:
        svc = get_portfolio_service()

        symbol_pairs = []
        for p in svc.list_portfolios(user_id):
            for h in svc.db.get_holdings(p["portfolio_id"]):
                if Decimal(str(h.get("total_quantity", 0))) > 0:
                    symbol_pairs.append((h["symbol"], h["asset_type"]))

        for row in svc.db.get_watched_symbols_for_user(user_id):
            symbol_pairs.append((row["symbol"], row["asset_type"]))

        # Deduplicate while preserving order
        seen = set()
        unique_pairs = []
        for pair in symbol_pairs:
            if pair not in seen:
                seen.add(pair)
                unique_pairs.append(pair)

        from price_cache_service import get_price_cache_service

        get_price_cache_service().refresh(unique_pairs)

    except Exception:
        pass  # Never surface errors into login flow


def _fetch_clarifying_questions(ticker: str, trade_type: str) -> list:
    """Make a single LLM call to get 1-3 multiple-choice clarifying questions."""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage

    prompt = (
        f"You are a stock research assistant. A user wants a {trade_type} research report on {ticker}. "
        "Generate 1–3 multiple-choice clarifying questions to better tailor the report. "
        "Each question must have 3–4 short answer options. "
        "Return ONLY a JSON array of objects with keys 'question' (string) and 'options' (array of strings). "
        'Example: [{"question": "What is your time horizon?", "options": ["Under 1 month", "1–6 months", "6–12 months", "1+ years"]}]'
    )
    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash", temperature=0.3, max_output_tokens=400
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        raw = response.content or ""
        # Strip markdown fences if present
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        questions = json.loads(raw.strip())
        if isinstance(questions, list) and questions and isinstance(questions[0], dict):
            return questions
    except Exception:
        pass
    return [
        {
            "question": f"What is your primary goal for researching {ticker}?",
            "options": [
                "Long-term investment",
                "Swing trade",
                "Day trade",
                "General analysis",
            ],
        }
    ]


# ==================== Auth Routes ====================


# Jinja UI is deprecated. Any GET to a non-allowed path redirects into the React SPA.
_SPA_PASSTHROUGH_PREFIXES = (
    "/app/",
    "/api/",
    "/stream/",
    "/ws/",
    "/static/",
    "/auth/",
    "/sign-out",
    "/cli/auth",
)


# Exact root-level paths that must be served by their own routes, not redirected to /app/.
# These are AEO/SEO files that AI crawlers and search engines fetch from the domain root.
_SPA_PASSTHROUGH_EXACT = ("/", "/llms.txt", "/robots.txt", "/sitemap.xml")

# Top-level paths that correspond to real React Router routes inside the SPA.
# Hits here get redirected to /app<path> (preserving the path) so the SPA can render
# the right page. Anything not in this set returns a real 404 (no soft-404).
_SPA_ROUTE_EXACT = frozenset({
    "/pricing", "/sign-in", "/sign-up", "/home", "/portfolio",
    "/reports", "/watchlist", "/alerts", "/research", "/settings",
    "/device", "/legal/terms", "/legal/privacy", "/legal/refund",
    "/billing/return", "/about", "/press",
})
_SPA_ROUTE_PREFIXES = (
    "/sign-in/", "/sign-up/", "/portfolio/", "/report/", "/chat/", "/ticker/",
)


@app.before_request
def _force_spa_for_gets():
    if request.method != "GET":
        return None
    path = request.path
    if path in _SPA_PASSTHROUGH_EXACT or path == "/app" or path.startswith(_SPA_PASSTHROUGH_PREFIXES):
        return None
    if _wants_json():
        return None
    if path in _SPA_ROUTE_EXACT or path.startswith(_SPA_ROUTE_PREFIXES):
        return redirect(f"/app{path}", code=301)
    abort(404)


def _safe_redirect_url(next_url, fallback="/"):
    """Allow only relative paths to prevent open redirects."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return fallback


@app.route("/sign-in")
def sign_in():
    """Sign-in page (Clerk hosted component)."""
    if "user_id" in session:
        return redirect(url_for("index"))
    next_url = request.args.get("next", "")
    return render_template("sign_in.html", next_url=_safe_redirect_url(next_url, "/"))


@app.route("/auth/sso-callback")
def auth_sso_callback():
    """OAuth/SSO callback: ClerkJS runs handleRedirectCallback here to set __session cookie, then redirects."""
    next_url = request.args.get("next", "")
    redirect_url = _safe_redirect_url(next_url, "/")
    return render_template("auth_sso_callback.html", redirect_url=redirect_url)


@app.route("/sign-up")
def sign_up():
    """Sign-up page (Clerk hosted component)."""
    if "user_id" in session:
        return redirect(url_for("index"))
    return render_template("sign_up.html")


@app.route("/cli/auth")
def cli_auth():
    """CLI authentication page -- renders Clerk sign-in, redirects JWT to localhost callback."""
    port = request.args.get("port", "")
    if not port.isdigit() or not (1024 <= int(port) <= 65535):
        return "Invalid port", 400
    return render_template(
        "cli_auth.html",
        callback_port=port,
        clerk_publishable_key=os.getenv("CLERK_PUBLISHABLE_KEY", ""),
    )


@app.route("/sign-out")
def sign_out():
    """Sign out: clear Flask session and redirect to sign-in."""
    session.clear()
    return redirect(url_for("sign_in"))


# --- Waitlist (ConvertKit) ---

_WAITLIST_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _subscribe_waitlist_convertkit(email: str) -> None:
    """Send email to ConvertKit when configured; otherwise log only. Logs API failures."""
    api_key = (os.getenv("CONVERTKIT_API_KEY") or "").strip()
    form_id = (os.getenv("CONVERTKIT_FORM_ID") or "").strip()
    if not api_key or not form_id:
        app.logger.info("Waitlist signup (ConvertKit disabled): %s", email)
        return
    url = f"https://api.convertkit.com/v3/forms/{form_id}/subscribe"
    try:
        resp = requests.post(
            url,
            json={"api_key": api_key, "email": email},
            timeout=15,
        )
        if resp.status_code >= 400:
            app.logger.warning(
                "ConvertKit subscribe failed: status=%s body=%s",
                resp.status_code,
                (resp.text or "")[:500],
            )
    except Exception as exc:
        app.logger.warning("ConvertKit subscribe error: %s", exc, exc_info=True)


@app.route("/waitlist")
def waitlist():
    """Public waitlist landing page."""
    return render_template("waitlist.html", **pop_status())


@app.route("/waitlist/join", methods=["POST"])
@limiter.limit("30 per minute", key_func=get_remote_address)
def waitlist_join():
    """Accept waitlist signup; sync to ConvertKit when configured."""
    email = (request.form.get("email") or "").strip()
    if not email or not _WAITLIST_EMAIL_RE.match(email):
        flash_status("Please enter a valid email address.", "error")
        return redirect(url_for("waitlist"))
    _subscribe_waitlist_convertkit(email)
    return redirect(url_for("waitlist_thanks"))


@app.route("/waitlist/thanks")
def waitlist_thanks():
    """Thank-you page after waitlist signup."""
    return render_template("waitlist_thanks.html")


@app.route("/login")
def login():
    """Compatibility redirect for legacy /login links."""
    return redirect(url_for("sign_in"))


def _render_authenticated_home():
    """Render the current authenticated app homepage (Markets)."""
    # Initialize session ID if needed
    get_or_create_session_id()

    # Get current values from session for form pre-filling
    current_ticker = session.get("current_ticker", "")
    current_trade_type = session.get("current_trade_type", "Investment")

    # Pinned tickers for Market Overview
    pinned_tickers = None
    user_id = session.get("user_id")
    if user_id:
        try:
            watchlist_svc = get_watchlist_service()
            pinned_tickers = watchlist_svc.get_pinned_tickers(user_id)
        except Exception:
            pinned_tickers = None

    return render_template(
        "index.html",
        current_ticker=current_ticker,
        current_trade_type=current_trade_type,
        pinned_tickers=pinned_tickers,
    )


@app.route("/llms.txt")
def llms_txt():
    """AI-engine index file (llmstxt.org spec). Served at the domain root for AEO."""
    return send_from_directory(app.static_folder, "llms.txt", mimetype="text/markdown; charset=utf-8")


@app.route("/robots.txt")
def robots_txt():
    """Robots policy — allows AI crawlers (GPTBot, PerplexityBot, ClaudeBot, etc.)."""
    return send_from_directory(app.static_folder, "robots.txt", mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    """XML sitemap of public pages."""
    return send_from_directory(app.static_folder, "sitemap.xml", mimetype="application/xml")


@app.route("/")
def index():
    """Redirect root to the React SPA. Jinja UI is deprecated."""
    if _wants_json():
        return jsonify({"authenticated": "user_id" in session})
    # 301 (permanent) so search engines / AI crawlers consolidate ranking signal on /app/.
    return redirect("/app/", code=301)


@app.route("/chat")
@login_required
def chat():
    """Render the chat interface."""
    # Initialize session ID if needed
    get_or_create_session_id()

    # Get conversation history from session
    conversation_history = session.get("conversation_history", [])
    current_ticker = session.get("current_ticker", "")
    current_trade_type = session.get("current_trade_type", "Investment")

    if _wants_json():
        return jsonify({
            "conversation_history": conversation_history,
            "current_ticker": current_ticker,
            "current_trade_type": current_trade_type,
        })
    return render_template(
        "chat.html",
        conversation_history=conversation_history,
        current_ticker=current_ticker,
        current_trade_type=current_trade_type,
    )


@app.route("/start_research", methods=["POST"])
@login_required
@limiter.limit(_research_rate_limit, key_func=get_remote_address)
def start_research():
    """Handle form submission to start research."""
    ticker = request.form.get("ticker", "").strip()
    trade_type = request.form.get("trade_type", "")

    # Validate input
    if not ticker:
        flash_status("Please enter a stock ticker.", "error")
        return redirect(url_for("index"))

    if not trade_type:
        flash_status("Please select a trade type.", "error")
        return redirect(url_for("index"))

    ticker = ticker.upper()

    if _session_hits_report_quota():
        flash_status(_free_tier_quota_message(), "error")
        return redirect(url_for("index"))

    try:
        session_id = get_or_create_session_id()
        agent = initialize_session(session_id)
        agent.reset_conversation()

        # Start research
        response = agent.start_research(ticker, trade_type)

        # Store conversation in session as list of message dicts
        conversation_history = [{"role": "assistant", "content": response}]

        session["conversation_history"] = conversation_history
        session["current_ticker"] = ticker
        session["current_trade_type"] = trade_type
        session.pop("report_chat_mode", None)
        flash_status(f"Research started for {ticker} ({trade_type})", "success")

    except Exception as e:
        flash_status(f"Error: {str(e)}", "error")
        session["conversation_history"] = []

    return redirect(url_for("chat"))


@app.route("/continue", methods=["POST"])
@csrf.exempt
@login_required
@limiter.limit(_continue_conversation_rate_limit, key_func=get_remote_address)
def continue_conversation():
    """Start a conversation turn in a background thread; return SSE session info."""
    # Accept JSON (React SPA) or form data (legacy Jinja2 templates)
    if request.is_json:
        body = request.get_json(force=True) or {}
        user_input = (body.get("message") or body.get("user_response") or "").strip()
        # Allow React to pass report_id to enter report chat mode
        incoming_report_id = body.get("report_id")
        if incoming_report_id:
            session["current_report_id"] = incoming_report_id
            session["report_chat_mode"] = True
            # Look up ticker from the report so IR/SEC tools can use it
            if not session.get("current_ticker"):
                try:
                    storage = ReportStorage()
                    rpt = storage.get_report(incoming_report_id, user_id=session.get("user_id"))
                    if rpt:
                        session["current_ticker"] = rpt.get("ticker", "")
                except Exception:
                    pass
    else:
        user_input = request.form.get("user_response", "").strip()
        incoming_report_id = None

    if not user_input:
        return jsonify({"success": False, "error": "⚠️ Please enter a response."}), 400

    session_id = get_or_create_session_id()
    agent = initialize_session(session_id)
    agent.user_id = session.get("user_id")
    agent.username = session.get("username")

    # Set user language preference for Hebrew chat/report generation
    try:
        from database import get_database_manager
        _db = get_database_manager()
        _user = _db.get_user_by_id(session.get("user_id"))
        agent.language = (_user.get("preferences") or {}).get("language", "en") if _user else "en"
    except Exception:
        agent.language = "en"

    # Snapshot mutable session state so the background thread can read it safely
    previous_report_id = session.get("current_report_id")
    report_chat_mode = session.get("report_chat_mode", False)
    conversation_history_snapshot = list(session.get("conversation_history", []))
    session_ticker = session.get("current_ticker")

    # SSE event sink — Postgres-backed so any worker can consume the stream.
    step_q = _PgEventQueue(session_id)
    emitter = create_emitter(step_q)
    agent.set_emitter(emitter)

    def run_in_background():
        try:
            print(
                f"[Continue] report_chat_mode={report_chat_mode}, previous_report_id={previous_report_id}"
            )
            if report_chat_mode and previous_report_id:
                agent.current_report_id = previous_report_id
                agent.current_ticker = session_ticker
                print(
                    f"[Continue] Calling chat_with_report for report {previous_report_id}..."
                )
                result = agent.chat_with_report(user_input)
                response = result["answer"]
                sources = result.get("sources", [])
                print(f"[Continue] chat_with_report returned ({len(response)} chars, {len(sources)} sources)")
            else:
                response = agent.continue_conversation(user_input)
                sources = []

            new_history = list(conversation_history_snapshot)
            new_history.append({"role": "user", "content": user_input})
            new_history.append({"role": "assistant", "content": response})

            current_report_id = agent.current_report_id
            report_generated = False
            report_preview = None

            if (
                not report_chat_mode
                and current_report_id
                and current_report_id != previous_report_id
            ):
                report_text = getattr(agent, "last_report_text", None) or ""
                if report_text:
                    report_preview = f"# Research Report\n\n{report_text}"
                    new_history.append({"role": "assistant", "content": report_preview})
                    report_generated = True

            step_q.put(
                {
                    "type": "done",
                    "user_message": user_input,
                    "assistant_message": response,
                    "sources": sources,
                    "conversation_history": new_history,
                    "report_generated": report_generated,
                    "report_preview": report_preview,
                    "current_report_id": current_report_id,
                    "report_text": getattr(agent, "last_report_text", None) or "",
                }
            )
        except Exception as e:
            app.logger.exception("continue_conversation background task failed")
            step_q.put({"type": "error", "message": sse_user_facing_error(e)})
        finally:
            agent.set_emitter(None)

    t = threading.Thread(target=run_in_background, daemon=True)
    t.start()

    return jsonify({"success": True, "streaming": True, "session_id": session_id})


@app.route("/stream/<session_id>")
@login_required
def stream_steps(session_id: str):
    """SSE endpoint — streams step messages until 'done' or 'error'."""
    if session.get("session_id") != session_id:
        abort(403)

    def event_stream():
        last_seq = 0
        deadline = time.time() + 120  # overall stream timeout
        poll_interval = 0.5
        while time.time() < deadline:
            try:
                events = db.read_generation_events_since(session_id, last_seq)
            except Exception as e:
                app.logger.warning("read_generation_events_since failed: %s", e)
                events = []
            for ev in events:
                payload = ev.get("payload") or {}
                last_seq = ev["seq"]
                yield f"data: {json.dumps(payload)}\n\n"
                if payload.get("type") in ("done", "error"):
                    return
            time.sleep(poll_interval)
        yield f"data: {json.dumps({'type': 'error', 'message': 'Request timed out'})}\n\n"

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/commit_session", methods=["POST"])
@csrf.exempt
@login_required
def commit_session():
    """Persist state from SSE 'done' payload back into the Flask session."""
    data = request.get_json(force=True) or {}
    if "conversation_history" in data:
        session["conversation_history"] = data["conversation_history"]
    if "current_report_id" in data and data["current_report_id"]:
        session["current_report_id"] = data["current_report_id"]
    if "report_text" in data and data["report_text"]:
        session["report_text"] = data["report_text"]
    return jsonify({"success": True})


@app.route("/generate_report", methods=["POST"])
@login_required
@limiter.limit(_report_post_rate_limit, key_func=get_remote_address)
def generate_report():
    """Handle form submission to generate report after followup questions."""
    if _session_hits_report_quota():
        flash_status(_free_tier_quota_message(), "error")
        return redirect(url_for("chat"))

    try:
        session_id = get_or_create_session_id()
        agent = initialize_session(session_id)

        # Extract context from conversation history
        conversation_history = session.get("conversation_history", [])
        context = ""
        for msg in conversation_history:
            if msg.get("role") == "user":
                context += f"User: {msg.get('content', '')}\n"

        # Generate report
        flash_status("Generating report... This may take a few minutes.", "info")
        session["conversation_history"] = conversation_history  # Preserve history
        session.modified = True

        report_text = agent.generate_report(context=context)
        report_id = agent.current_report_id

        # Store report in session
        session["current_report_id"] = report_id
        session["report_text"] = report_text
        flash_status(
            f"Report generated successfully! Report ID: {report_id[:8]}...", "success"
        )

        # Add full report to conversation
        report_preview = f"# Research Report\n\n{report_text}"
        conversation_history.append({"role": "assistant", "content": report_preview})
        session["conversation_history"] = conversation_history

    except Exception as e:
        flash_status(f"Error generating report: {str(e)}", "error")

    return redirect(url_for("chat"))


@app.route("/clear", methods=["POST"])
@login_required
def clear_conversation():
    """Handle form submission to clear conversation."""
    session["conversation_history"] = []
    session["current_ticker"] = ""
    session["current_trade_type"] = "Investment"
    session.pop("report_chat_mode", None)
    flash_status("Conversation cleared. Ready for new research.", "info")

    # Optionally reset agent
    session_id = get_or_create_session_id()
    if session_id in agent_sessions:
        try:
            agent_sessions[session_id].reset_conversation()
        except Exception as e:
            app.logger.warning(f"Session reset failed: {e}")

    return redirect(url_for("chat"))


# ==================== Popup Q&A + Background Generation Routes ====================


@app.route("/popup_start", methods=["POST"])
@csrf.exempt
@login_required
@limiter.limit(_popup_start_rate_limit, key_func=get_remote_address)
def popup_start():
    """Fetch clarifying questions for ticker + trade_type and initialize agent session."""
    ticker = (request.form.get("ticker") or "").strip().upper()
    trade_type = (request.form.get("trade_type") or "").strip()

    if not ticker or not trade_type:
        return jsonify({"error": "ticker and trade_type are required"}), 400

    session_id = get_or_create_session_id()
    agent = initialize_session(session_id)
    agent.reset_conversation()

    session["current_ticker"] = ticker
    session["current_trade_type"] = trade_type

    language = (request.form.get("language") or "").strip().lower()
    if language in ("en", "he"):
        agent.language = language
        session["language"] = language

    position_summary = (request.form.get("position_summary") or "").strip()
    position_goal = (request.form.get("position_goal") or "").strip()
    if position_summary:
        session["position_summary"] = position_summary
    else:
        session.pop("position_summary", None)
    if position_goal:
        session["position_goal"] = position_goal
    else:
        session.pop("position_goal", None)

    # Let the orchestrator agent run one turn; it will call ask_user_questions tool
    try:
        agent.start_research(ticker, trade_type)
    except Exception:
        pass
    questions = agent.pending_questions

    # Fallback if agent did not call the tool or returned malformed data
    if not isinstance(questions, list) or not questions:
        questions = [
            {
                "question": f"What is your primary goal for researching {ticker}?",
                "options": [
                    "Long-term investment",
                    "Swing trade",
                    "Day trade",
                    "General analysis",
                ],
            }
        ]

    from research_subjects import get_research_subjects_for_trade_type

    subjects = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "priority": s.priority.get(trade_type, 99),
        }
        for s in get_research_subjects_for_trade_type(trade_type)
    ]
    return jsonify(
        {"questions": questions, "session_id": session_id, "subjects": subjects}
    )


@app.route("/start_generation", methods=["POST"])
@csrf.exempt
@login_required
@limiter.limit(_report_gen_rate_limit, key_func=get_remote_address)
def start_generation():
    """Kick off background report generation with collected Q&A context."""
    data = request.get_json(force=True) or {}
    questions = data.get("questions", [])
    answers = data.get("answers", [])
    selected_subject_ids = (
        data.get("selected_subject_ids") or None
    )  # None = no user selection

    if _session_hits_report_quota():
        return _report_quota_json_error()

    session_id = get_or_create_session_id()
    agent = initialize_session(session_id)
    agent.user_id = session.get("user_id")
    agent.username = session.get("username")

    # Re-prime the agent's ticker/trade_type from the Flask session cookie when
    # this worker's in-memory agent is fresh. popup_start runs start_research on
    # whichever gunicorn worker served it; start_generation can land on a
    # different worker whose agent_sessions has no primed agent (issue #116).
    # The signed cookie is shared across workers, so it restores the state.
    if not agent.current_ticker:
        _ct = (session.get("current_ticker") or "").strip().upper()
        _tt = (session.get("current_trade_type") or "").strip()
        if _ct and _tt:
            agent.current_ticker = _ct
            agent.current_trade_type = _tt

    # If the ticker still cannot be recovered, fail loudly instead of marking the
    # job "ready" with no report (the silent no-op users hit under multi-worker).
    if not agent.current_ticker or not agent.current_trade_type:
        return (
            jsonify({"error": "No active research session. Please restart the report."}),
            400,
        )

    # Set user language preference for Hebrew report generation
    try:
        from database import get_database_manager
        _db = get_database_manager()
        _user = _db.get_user_by_id(session.get("user_id"))
        agent.language = (_user.get("preferences") or {}).get("language", "en") if _user else "en"
    except Exception:
        agent.language = "en"

    # Snapshot budget input for the background thread.
    from spend_budget import get_spend_budget_usd

    spend_budget_usd = get_spend_budget_usd(agent.user_id)

    # Build context string from Q&A pairs
    lines = []
    for i, q in enumerate(questions):
        a = answers[i] if i < len(answers) else ""
        if q:
            lines.append(f"Q: {q}")
            lines.append(f"A: {a}")
    context_str = "User context:\n" + "\n".join(lines) if lines else ""

    position_summary = session.pop("position_summary", "")
    position_goal = session.pop("position_goal", "")
    if position_summary:
        position_block = f"User's existing position:\n{position_summary}"
        if position_goal:
            position_block += f"\nUser's goal for this research: {position_goal}"
        context_str = position_block + ("\n\n" + context_str if context_str else "")

    user_id = session.get("user_id") or ""
    db.set_generation_status(
        session_id,
        user_id,
        status="in_progress",
        report_id=None,
        progress=5,
        step="Starting...",
        step_code="starting",
    )

    def run_generation():
        import threading as _threading

        subject_counter = {"done": 0, "total": 0}
        counter_lock = _threading.Lock()

        def progress_fn(progress, step):
            try:
                if progress is None:
                    # Subject completed — increment counter, interpolate within 20–75% band
                    with counter_lock:
                        subject_counter["done"] += 1
                        done = subject_counter["done"]
                        total = subject_counter["total"] or 1
                        pct = 20 + int((done / total) * 55)
                        db.update_generation_status(
                            session_id,
                            progress=min(pct, 75),
                            step=f"Researching: {done}/{total} subjects done",
                            step_code="researching",
                            done=done,
                            total=total,
                        )
                else:
                    fields = {
                        "progress": progress,
                        "step": step,
                        "step_code": _step_code_for(progress, step),
                    }
                    if progress == 20 and "subjects" in (step or ""):
                        try:
                            subject_counter["total"] = int(step.split()[1])
                            fields["total"] = subject_counter["total"]
                        except (IndexError, ValueError):
                            pass
                    db.update_generation_status(session_id, **fields)
            except Exception as e:
                app.logger.warning("progress_fn DB update failed: %s", e)

        emitter = create_emitter()
        agent.set_emitter(emitter)
        agent.set_progress_fn(progress_fn)
        try:
            agent.generate_report(
                context=context_str,
                selected_subjects=selected_subject_ids,
                spend_budget_usd=spend_budget_usd,
            )
            db.update_generation_status(
                session_id,
                status="ready",
                report_id=agent.current_report_id,
                progress=100,
                step="Report ready",
                step_code="ready",
            )
        except Exception as e:
            db.update_generation_status(
                session_id,
                status="error",
                message=str(e),
                step_code="error",
            )
        finally:
            agent.set_emitter(None)
            agent.set_progress_fn(None)

    threading.Thread(target=run_generation, daemon=True).start()
    return jsonify({"success": True})


@app.route("/api/health")
@csrf.exempt
def api_health():
    return jsonify({"status": "ok"})


@app.route("/api/report_status/<session_id>")
@login_required
def report_status(session_id: str):
    """Poll endpoint for background generation status."""
    user_id = session.get("user_id")
    row = db.get_generation_status(session_id)

    # No row yet (race between CLI's first poll and the generate handler
    # registering state, or the cold-worker warm-up case). Return "unknown" so
    # the caller keeps polling instead of crashing with a 403. No ownership
    # information is leaked because the response is identical for any caller.
    if row is None:
        return jsonify({"status": "unknown"})

    web_session_ok = session.get("session_id") == session_id
    status_owner = row.get("user_id")
    api_owner_ok = bool(status_owner) and bool(user_id) and status_owner == user_id

    if not web_session_ok and not api_owner_ok:
        app.logger.warning(
            "report_status forbidden: session=%s user=%s owner=%s web_ok=%s",
            session_id, user_id, status_owner, web_session_ok,
        )
        return jsonify({"error": "forbidden"}), 403

    # Strip internal/timestamp columns and any None values for a clean response.
    _hidden = {"user_id", "session_id", "created_at", "updated_at", "expires_at"}
    return jsonify({k: v for k, v in row.items() if k not in _hidden and v is not None})


@app.route("/api/reports/generate", methods=["POST"])
@csrf.exempt
@login_required
@limiter.limit(_report_gen_rate_limit, key_func=get_remote_address)
def api_reports_generate():
    """Stateless report generation endpoint for headless/CLI clients.

    Accepts JSON body: {ticker, trade_type, context (optional)}
    Returns: {success: true, session_id: "<uuid>"}
    Poll GET /api/report_status/<session_id> until status == "ready" or "error".
    """
    data = request.get_json(force=True) or {}
    ticker = (data.get("ticker") or "").strip().upper()
    trade_type = (data.get("trade_type") or "").strip()
    context_str = (data.get("context") or "").strip()
    no_questions = bool(data.get("no_questions"))

    if not ticker or not trade_type:
        return jsonify({"error": "ticker and trade_type are required"}), 400

    if _session_hits_report_quota():
        return _report_quota_json_error()

    # Fresh session_id independent of any browser session
    session_id = str(uuid.uuid4())
    agent = initialize_session(session_id)
    agent.reset_conversation()

    user_id = session.get("user_id")
    agent.user_id = user_id

    # Username: prefer session (Clerk web flow), fall back to DB lookup (Bearer token)
    agent.username = session.get("username")
    if not agent.username and user_id:
        try:
            from database import get_database_manager as _get_db
            _user_rec = _get_db().get_user_by_id(user_id)
            if _user_rec:
                agent.username = _user_rec.get("username", user_id)
        except Exception:
            agent.username = user_id

    # Language preference from user profile
    try:
        from database import get_database_manager as _get_db2
        _user_rec2 = _get_db2().get_user_by_id(user_id)
        agent.language = (
            (_user_rec2.get("preferences") or {}).get("language", "en") if _user_rec2 else "en"
        )
    except Exception:
        agent.language = "en"

    # Prime the orchestrator with ticker/trade_type (mirrors popup_start)
    try:
        agent.start_research(ticker, trade_type)
    except Exception:
        pass

    from spend_budget import get_spend_budget_usd
    spend_budget_usd = get_spend_budget_usd(agent.user_id)

    # Always surface clarifying questions + subject areas so the CLI flow
    # matches the web flow. Callers that want to skip (headless automation)
    # can pass no_questions=True.
    pending = agent.pending_questions if isinstance(agent.pending_questions, list) else []
    if not pending:
        # Fallback matches /ask_questions — ensure the user always gets a prompt.
        pending = [
            {
                "question": f"What is your primary goal for researching {ticker}?",
                "options": [
                    "Long-term investment",
                    "Swing trade",
                    "Day trade",
                    "General analysis",
                ],
            }
        ]

    from research_subjects import get_research_subjects_for_trade_type
    subjects = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "priority": s.priority.get(trade_type, 99),
        }
        for s in get_research_subjects_for_trade_type(trade_type)
    ]

    if not no_questions:
        db.set_generation_status(
            session_id,
            user_id,
            status="needs_input",
            questions=pending,
            subjects=subjects,
        )
        return jsonify({
            "success": True,
            "session_id": session_id,
            "questions": pending,
            "subjects": subjects,
        })

    db.set_generation_status(
        session_id,
        user_id,
        status="in_progress",
        report_id=None,
        progress=5,
        step="Starting...",
        step_code="starting",
    )

    _start_report_generation_thread(session_id, agent, context_str, user_id, spend_budget_usd)
    return jsonify({"success": True, "session_id": session_id, "questions": []})


def _start_report_generation_thread(session_id, agent, context_str, user_id, spend_budget_usd, selected_subjects=None):
    """Spawn the background report-generation thread for CLI/headless flows."""
    def run_generation():
        import threading as _threading

        subject_counter = {"done": 0, "total": 0}
        counter_lock = _threading.Lock()

        def progress_fn(progress, step):
            try:
                if progress is None:
                    with counter_lock:
                        subject_counter["done"] += 1
                        done = subject_counter["done"]
                        total = subject_counter["total"] or 1
                        pct = 20 + int((done / total) * 55)
                        db.update_generation_status(
                            session_id,
                            progress=min(pct, 75),
                            step=f"Researching: {done}/{total} subjects done",
                        )
                else:
                    fields = {"progress": progress, "step": step}
                    if progress == 20 and "subjects" in (step or ""):
                        try:
                            subject_counter["total"] = int(step.split()[1])
                        except (IndexError, ValueError):
                            pass
                    db.update_generation_status(session_id, **fields)
            except Exception as e:
                app.logger.warning("progress_fn DB update failed: %s", e)

        emitter = create_emitter()
        agent.set_emitter(emitter)
        agent.set_progress_fn(progress_fn)
        try:
            agent.generate_report(
                context=context_str,
                selected_subjects=selected_subjects,
                spend_budget_usd=spend_budget_usd,
            )
            failed = list(getattr(agent, "last_failed_subjects", []) or [])
            is_partial = bool(getattr(agent, "last_is_partial", False))
            gate_aborted = bool(getattr(agent, "last_quality_gate_aborted", False))
            report_id = agent.current_report_id

            # When the quality gate aborted (>50% subjects failed) or no report
            # was produced at all, surface this as an error to CLI/headless callers
            # instead of a misleading "ready". Otherwise the user pays for an
            # empty report and trusts a fake success.
            if not report_id or gate_aborted:
                db.update_generation_status(
                    session_id,
                    status="error",
                    message=(
                        f"Report generation failed: research could not produce "
                        f"usable output. Most likely the ticker was invalid or "
                        f"upstream data sources are down. Failed subjects: "
                        f"{', '.join(failed) if failed else 'all'}."
                    ),
                    failed_subjects=failed,
                    step_code="error",
                )
            elif is_partial and failed:
                # Some subjects failed but the report has content — succeed but warn.
                db.update_generation_status(
                    session_id,
                    status="ready",
                    report_id=report_id,
                    progress=100,
                    step="Report ready (partial)",
                    step_code="ready",
                    partial=True,
                    failed_subjects=failed,
                )
            else:
                db.update_generation_status(
                    session_id,
                    status="ready",
                    report_id=report_id,
                    progress=100,
                    step="Report ready",
                    step_code="ready",
                )
        except Exception as e:
            db.update_generation_status(
                session_id,
                status="error",
                message=str(e),
                step_code="error",
            )
        finally:
            agent.set_emitter(None)
            agent.set_progress_fn(None)

    threading.Thread(target=run_generation, daemon=True).start()


@app.route("/api/reports/answer", methods=["POST"])
@csrf.exempt
@login_required
def api_reports_answer():
    """Submit answers to clarifying questions and kick off generation.

    Accepts JSON body: {session_id, answers: [str, ...] or [{question, answer}, ...]}
    Reads the paused session (status == needs_input), builds context_str from
    Q&A pairs, and starts the background generation thread.
    """
    data = request.get_json(force=True) or {}
    session_id = (data.get("session_id") or "").strip()
    answers_in = data.get("answers") or []
    selected_subject_ids = data.get("selected_subject_ids") or None

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    row = db.get_generation_status(session_id)
    user_id = session.get("user_id")

    if row is None:
        return jsonify({"error": "unknown session"}), 404
    if row.get("user_id") != user_id:
        return jsonify({"error": "forbidden"}), 403
    if row.get("status") != "needs_input":
        return jsonify({"error": f"session not awaiting input (status={row.get('status')})"}), 409

    questions = row.get("questions") or []

    # Accept either a flat list of answer strings (aligned with questions order)
    # or a list of {question, answer} dicts.
    lines = []
    for i, q in enumerate(questions):
        q_text = q.get("question") if isinstance(q, dict) else str(q)
        ans = ""
        if i < len(answers_in):
            a = answers_in[i]
            ans = a.get("answer", "") if isinstance(a, dict) else str(a)
        lines.append(f"Q: {q_text}")
        lines.append(f"A: {ans}")
    context_str = "User context:\n" + "\n".join(lines) if lines else ""

    agent = initialize_session(session_id)

    from spend_budget import get_spend_budget_usd
    spend_budget_usd = get_spend_budget_usd(agent.user_id)

    db.set_generation_status(
        session_id,
        user_id,
        status="in_progress",
        report_id=None,
        progress=5,
        step="Starting...",
        step_code="starting",
    )

    _start_report_generation_thread(
        session_id, agent, context_str, user_id, spend_budget_usd,
        selected_subjects=selected_subject_ids,
    )
    return jsonify({"success": True, "session_id": session_id})


@app.route("/api/position_check/<ticker>")
@login_required
def position_check(ticker: str):
    """Return user's existing holdings of a ticker across all portfolios."""
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"holding": False, "positions": []})
    svc = get_portfolio_service()
    symbol = ticker.upper()
    holdings = svc.get_holdings_for_ticker(user_id=user_id, symbol=symbol)
    if not holdings:
        return jsonify({"holding": False, "positions": []})

    # Enrich with live price for market_value and return calculations
    from data_providers import DataProviderFactory
    factory = DataProviderFactory()
    try:
        price = float(factory.get_current_price(symbol) or 0)
    except Exception:
        price = None

    positions = []
    for h in holdings:
        qty = float(h["total_quantity"])
        avg_cost = float(h["average_cost"])
        cost_basis = float(h["total_cost_basis"])
        market_value = qty * price if price else None
        total_return = (market_value - cost_basis) if market_value is not None else None
        return_pct = (total_return / cost_basis * 100) if (total_return is not None and cost_basis > 0) else None
        positions.append({
            "portfolio_name": h["portfolio_name"],
            "portfolio_id": h["portfolio_id"],
            "quantity": qty,
            "average_cost": avg_cost,
            "total_cost_basis": cost_basis,
            "current_price": price,
            "market_value": market_value,
            "total_return": total_return,
            "return_pct": return_pct,
        })

    return jsonify({"holding": True, "positions": positions})


@app.route("/api/usage", methods=["GET"])
@login_required
def api_usage():
    """Monthly free-tier research report usage for the signed-in user."""
    from database import get_database_manager
    from report_usage import current_period_month, get_free_tier_report_limit

    uid = session["user_id"]
    db = get_database_manager()
    period = current_period_month()
    limit = get_free_tier_report_limit()
    if db.user_is_pro(uid):
        return jsonify(
            {
                "reports_used": 0,
                "reports_limit": None,
                "period": period,
                "is_pro": True,
            }
        )
    used = db.get_report_usage_count(uid, period)
    return jsonify(
        {
            "reports_used": used,
            "reports_limit": limit if limit > 0 else None,
            "period": period,
            "is_pro": False,
        }
    )


# ==================== Portfolio Routes ====================


@app.route("/portfolio")
@login_required
def portfolio():
    """Portfolio list page."""
    portfolio_service = get_portfolio_service()
    data = portfolio_service.get_portfolios_with_summaries(user_id=session["user_id"])
    status = pop_status()
    if _wants_json():
        return jsonify({"portfolios": data["portfolios"], "overall": data["overall"]})
    return render_template(
        "portfolio_list.html",
        portfolios=data["portfolios"],
        overall=data["overall"],
        **status,
        user_id=session.get("user_id", ""),
    )


@app.route("/portfolio/create", methods=["POST"])
@login_required
def create_portfolio_route():
    """Create a new portfolio."""
    name = request.form.get("name", "").strip()
    if not name:
        flash_status("Portfolio name is required", "error")
        return redirect(url_for("portfolio"))
    track_cash = request.form.get("track_cash") == "on"
    cash_balance = 0.0
    if track_cash:
        try:
            raw = request.form.get("cash_balance", "").strip().replace(",", "")
            cash_balance = float(raw) if raw else 0.0
            cash_balance = max(0.0, cash_balance)
        except (ValueError, TypeError):
            cash_balance = 0.0
    portfolio_service = get_portfolio_service()
    portfolio_id = portfolio_service.create_portfolio(
        name=name,
        user_id=session["user_id"],
        track_cash=track_cash,
        cash_balance=cash_balance,
    )
    return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))


@app.route("/api/portfolio/<portfolio_id>/toggle-cash", methods=["POST"])
@login_required
def toggle_cash_tracking(portfolio_id: str):
    """Enable cash tracking for a portfolio."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        return {"ok": False, "error": "Not found"}, 404
    try:
        portfolio_service.enable_cash_tracking(portfolio_id)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/portfolio/<portfolio_id>/cash", methods=["POST"])
@login_required
def update_portfolio_cash(portfolio_id: str):
    """Update cash balance for a portfolio. Supports deposit, withdraw, or set."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        return {"ok": False, "error": "Not found"}, 404
    if not portfolio_data.get("track_cash"):
        return {"ok": False, "error": "Portfolio does not track cash"}, 400
    data = request.get_json(silent=True) or {}
    action = data.get("action", "set")
    try:
        amount = float(data.get("amount", data.get("cash_balance", 0)))
        if action == "deposit":
            new_balance = portfolio_service.deposit_cash(portfolio_id, amount)
        elif action == "withdraw":
            new_balance = portfolio_service.withdraw_cash(portfolio_id, amount)
        else:
            if amount < 0:
                return {"ok": False, "error": "Cash balance cannot be negative"}, 400
            portfolio_service.update_cash_balance(portfolio_id, amount)
            new_balance = amount
        return {"ok": True, "cash_balance": new_balance}
    except ValueError as e:
        return {"ok": False, "error": str(e)}, 400
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route("/portfolio/<portfolio_id>")
@login_required
def portfolio_detail(portfolio_id: str):
    """Portfolio dashboard for a specific portfolio."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        abort(404)
    try:
        summary = portfolio_service.get_portfolio_summary(
            portfolio_id, with_prices=False
        )
        status = pop_status()
        if _wants_json():
            return jsonify({"portfolio": portfolio_data, "summary": summary, "holdings": summary["holdings"]})
        return render_template(
            "portfolio.html",
            portfolio=portfolio_data,
            summary=summary,
            holdings=summary["holdings"],
            **status,
        )
    except Exception as e:
        flash_status(f"Error loading portfolio: {str(e)}", "error")
        if _wants_json():
            return jsonify({"error": str(e)}), 500
        return render_template(
            "portfolio.html",
            portfolio=portfolio_data,
            summary=None,
            holdings=[],
            **pop_status(),
        )


@app.route("/portfolio/<portfolio_id>/add", methods=["GET", "POST"])
@login_required
def add_transaction(portfolio_id: str):
    """Add transaction form."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        abort(404)

    if request.method == "POST":
        try:
            # Parse form data
            symbol = request.form.get("symbol", "").strip().upper()
            transaction_type = request.form.get("transaction_type", "")
            quantity = Decimal(request.form.get("quantity", "0"))
            price = Decimal(request.form.get("price", "0"))
            date_str = request.form.get("date", "")
            fees = Decimal(request.form.get("fees", "0") or "0")
            notes = request.form.get("notes", "")
            asset_type = request.form.get("asset_type", None)

            # Validate
            if not symbol:
                raise ValueError("Symbol is required")
            if transaction_type not in ("buy", "sell"):
                raise ValueError("Invalid transaction type")
            if quantity <= 0:
                raise ValueError("Quantity must be positive")
            if price <= 0:
                raise ValueError("Price must be positive")
            if not date_str:
                raise ValueError("Date is required")

            transaction_date = datetime.strptime(date_str, "%Y-%m-%d")

            portfolio_service.add_transaction(
                portfolio_id=portfolio_id,
                symbol=symbol,
                transaction_type=transaction_type,
                quantity=quantity,
                price_per_unit=price,
                transaction_date=transaction_date,
                fees=fees,
                notes=notes,
                asset_type=asset_type if asset_type else None,
            )

            flash_status(
                f"Transaction added: {transaction_type.upper()} {quantity} {symbol}",
                "success",
            )

        except Exception as e:
            flash_status(f"Error: {str(e)}", "error")

        return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

    # GET request - show form
    status = pop_status()
    if _wants_json():
        return jsonify({"portfolio": portfolio_data})
    return render_template("add_transaction.html", portfolio=portfolio_data, **status)


@app.route("/portfolio/<portfolio_id>/import", methods=["GET", "POST"])
@login_required
def import_csv(portfolio_id: str):
    """CSV import page."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        abort(404)

    if request.method == "POST":
        try:
            if "csv_file" not in request.files:
                raise ValueError("No file uploaded")

            file = request.files["csv_file"]
            if file.filename == "":
                raise ValueError("No file selected")

            file.seek(0, 2)
            if file.tell() > 10 * 1024 * 1024:
                raise ValueError("File exceeds 10MB limit")
            file.seek(0)
            csv_content = file.read().decode("utf-8")
            broker = (request.form.get("broker") or "").strip().lower() or None
            result = portfolio_service.import_csv(
                portfolio_id=portfolio_id,
                csv_content=csv_content,
                filename=file.filename,
                format_type=broker,
            )

            session["import_summary"] = {
                "success_count": result.success_count,
                "error_count": result.error_count,
            }
            if result.error_count > 0:
                flash_status(
                    f"{result.success_count} rows imported successfully, {result.error_count} rows failed",
                    "info",
                )
                session["import_errors"] = result.errors
            else:
                flash_status(
                    f"{result.success_count} rows imported successfully, 0 rows failed",
                    "success",
                )
                session["import_errors"] = []

        except Exception as e:
            flash_status(f"Import failed: {str(e)}", "error")
            session["import_summary"] = None
            session["import_errors"] = []

        return redirect(url_for("import_csv", portfolio_id=portfolio_id))

    # GET request - show import form
    status = pop_status()
    import_summary = session.pop("import_summary", None)
    import_errors = session.pop("import_errors", None)
    if _wants_json():
        return jsonify({"portfolio": portfolio_data, "import_summary": import_summary, "import_errors": import_errors})
    return render_template(
        "import_csv.html",
        portfolio=portfolio_data,
        **status,
        import_summary=import_summary,
        import_errors=import_errors,
    )


@app.route("/portfolio/<portfolio_id>/holding/<symbol>")
@login_required
def holding_detail(portfolio_id: str, symbol: str):
    """View holding details and transactions."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        abort(404)

    try:
        holding = portfolio_service.get_holding(portfolio_id, symbol)

        if not holding:
            flash_status(f"Holding not found: {symbol}", "info")
            return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

        if holding.get("total_quantity", Decimal("0")) <= Decimal("0"):
            flash_status(
                f"{symbol} is a closed position with no remaining quantity.", "info"
            )
            return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

        transactions = portfolio_service.get_transactions(holding["holding_id"])

        # Get current price
        provider, _ = DataProviderFactory.get_provider_for_symbol(symbol)
        current_price = provider.get_current_price(symbol) or Decimal("0")

        holding["current_price"] = current_price
        holding["market_value"] = holding["total_quantity"] * current_price
        holding["unrealized_gain"] = (
            holding["market_value"] - holding["total_cost_basis"]
        )

        if holding["total_cost_basis"] > 0:
            holding["unrealized_gain_pct"] = (
                holding["unrealized_gain"] / holding["total_cost_basis"]
            ) * 100
        else:
            holding["unrealized_gain_pct"] = Decimal("0")

        status = pop_status()

        if _wants_json():
            return jsonify({"portfolio": portfolio_data, "holding": holding, "transactions": transactions})
        return render_template(
            "holding_detail.html",
            portfolio=portfolio_data,
            holding=holding,
            transactions=transactions,
            **status,
        )

    except Exception as e:
        flash_status(f"Error: {str(e)}", "error")
        return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))


@app.route(
    "/portfolio/<portfolio_id>/transaction/<transaction_id>/delete", methods=["POST"]
)
@login_required
def delete_transaction(portfolio_id: str, transaction_id: str):
    """Delete a transaction."""
    try:
        portfolio_service = get_portfolio_service()

        txn = portfolio_service.get_transaction(transaction_id)
        if not txn:
            flash_status("Transaction not found", "error")
            return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

        holding = portfolio_service.get_holding_by_id(txn["holding_id"])
        if not holding or holding.get("portfolio_id") != portfolio_id:
            flash_status("Transaction not found", "error")
            return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))

        portfolio = portfolio_service.get_portfolio(holding["portfolio_id"])
        if not portfolio or portfolio.get("user_id") != session["user_id"]:
            flash_status("Not authorized", "error")
            return redirect(url_for("portfolio"))

        symbol = holding["symbol"]
        if portfolio_service.delete_transaction(transaction_id):
            flash_status("Transaction deleted", "success")
        else:
            flash_status("Failed to delete transaction", "error")

        if symbol:
            return redirect(
                url_for("holding_detail", portfolio_id=portfolio_id, symbol=symbol)
            )

    except Exception as e:
        from werkzeug.exceptions import HTTPException

        if isinstance(e, HTTPException):
            raise
        flash_status(f"Error: {str(e)}", "error")

    return redirect(url_for("portfolio_detail", portfolio_id=portfolio_id))


@app.route(
    "/api/portfolio/<portfolio_id>/transaction/<transaction_id>",
    methods=["PUT"],
)
@login_required
def api_edit_transaction(portfolio_id: str, transaction_id: str):
    """Edit a transaction's quantity, price, date, and notes."""
    from database import get_database_manager

    svc = get_portfolio_service()

    # Verify ownership chain: transaction → holding → portfolio → user
    txn = svc.get_transaction(transaction_id)
    if not txn:
        return jsonify({"success": False, "error": "Transaction not found"}), 404
    holding = svc.get_holding_by_id(txn["holding_id"])
    if not holding or holding.get("portfolio_id") != portfolio_id:
        return jsonify({"success": False, "error": "Not found"}), 404
    portfolio = svc.get_portfolio(portfolio_id)
    if not portfolio or portfolio.get("user_id") != session["user_id"]:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    data = request.get_json(silent=True) or {}
    try:
        quantity = Decimal(str(data["quantity"])) if "quantity" in data else None
        price_per_unit = Decimal(str(data["price_per_unit"])) if "price_per_unit" in data else None
    except Exception:
        return jsonify({"success": False, "error": "Invalid quantity or price"}), 400

    if quantity is not None and quantity <= 0:
        return jsonify({"success": False, "error": "quantity must be positive"}), 400
    if price_per_unit is not None and price_per_unit <= 0:
        return jsonify({"success": False, "error": "price must be positive"}), 400

    db = get_database_manager()
    conn = None
    try:
        conn = db.get_connection()
        fields, values = [], []
        if quantity is not None:
            fields.append("quantity = %s")
            values.append(float(quantity))
        if price_per_unit is not None:
            fields.append("price_per_unit = %s")
            values.append(float(price_per_unit))
        if "transaction_date" in data:
            fields.append("transaction_date = %s")
            values.append(data["transaction_date"])
        if "notes" in data:
            fields.append("notes = %s")
            values.append(str(data["notes"])[:500])
        if not fields:
            return jsonify({"success": False, "error": "No fields to update"}), 400
        values.append(transaction_id)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE transactions SET {', '.join(fields)} WHERE transaction_id = %s",
                values,
            )
        conn.commit()
        # Recalculate holding after edit
        svc._recalculate_holding(holding["holding_id"])
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db._release(conn)


@app.route("/api/portfolio/<portfolio_id>/transaction", methods=["POST"])
@login_required
def api_add_transaction(portfolio_id: str):
    """Add a transaction via JSON (React SPA)."""
    portfolio_service = get_portfolio_service()
    portfolio = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio or portfolio.get("user_id") != session["user_id"]:
        return jsonify({"success": False, "error": "Not found"}), 404

    data = request.get_json(silent=True) or {}
    try:
        symbol = (data.get("symbol") or "").strip().upper()
        transaction_type = (data.get("type") or data.get("transaction_type") or "").strip().lower()
        quantity = Decimal(str(data["shares"])) if "shares" in data else Decimal(str(data.get("quantity", 0)))
        price = Decimal(str(data.get("price", 0)))
        date_str = data.get("date", "")
        notes = data.get("notes", "")

        if not symbol:
            return jsonify({"success": False, "error": "Symbol is required"}), 400
        if transaction_type not in ("buy", "sell"):
            return jsonify({"success": False, "error": "type must be buy or sell"}), 400
        if quantity <= 0:
            return jsonify({"success": False, "error": "shares must be positive"}), 400
        if price <= 0:
            return jsonify({"success": False, "error": "price must be positive"}), 400

        transaction_date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.utcnow()
        portfolio_service.add_transaction(
            portfolio_id=portfolio_id,
            symbol=symbol,
            transaction_type=transaction_type,
            quantity=quantity,
            price_per_unit=price,
            transaction_date=transaction_date,
            notes=notes,
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route(
    "/api/portfolio/<portfolio_id>/transaction/<transaction_id>",
    methods=["DELETE"],
)
@login_required
def api_delete_transaction(portfolio_id: str, transaction_id: str):
    """Delete a transaction via JSON (React SPA)."""
    svc = get_portfolio_service()
    txn = svc.get_transaction(transaction_id)
    if not txn:
        return jsonify({"success": False, "error": "Transaction not found"}), 404
    holding = svc.get_holding_by_id(txn["holding_id"])
    if not holding or holding.get("portfolio_id") != portfolio_id:
        return jsonify({"success": False, "error": "Not found"}), 404
    portfolio = svc.get_portfolio(portfolio_id)
    if not portfolio or portfolio.get("user_id") != session["user_id"]:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if svc.delete_transaction(transaction_id):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Delete failed"}), 500


@app.route("/api/portfolios", methods=["POST"])
@login_required
def api_create_portfolio():
    """Create a new portfolio (JSON API for React SPA)."""
    import math as _math
    from database import get_database_manager
    from tiers import get_limit

    uid = session["user_id"]
    db = get_database_manager()
    limit = get_limit(uid, "portfolios")
    if limit != _math.inf and db.count_user_portfolios(uid) >= int(limit):
        return jsonify({
            "ok": False,
            "error": "quota_exceeded",
            "resource": "portfolios",
            "limit": int(limit),
            "message": f"Free plan allows {int(limit)} portfolio. Upgrade to add more.",
        }), 402

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Portfolio name is required"}), 400
    track_cash = data.get("track_cash", True)
    cash_balance = 0.0
    if track_cash:
        try:
            cash_balance = max(0.0, float(data.get("cash_balance", 0)))
        except (ValueError, TypeError):
            cash_balance = 0.0
    portfolio_service = get_portfolio_service()
    portfolio_id = portfolio_service.create_portfolio(
        name=name,
        user_id=session["user_id"],
        track_cash=track_cash,
        cash_balance=cash_balance,
    )
    return jsonify({"ok": True, "portfolio_id": portfolio_id})


@app.route("/api/portfolios/prices")
@login_required
def portfolios_prices():
    """Return live price summaries for all user portfolios (for async list page)."""
    portfolio_service = get_portfolio_service()
    portfolios = portfolio_service.list_portfolios(user_id=session["user_id"])

    from concurrent.futures import ThreadPoolExecutor

    def to_float(v):
        return float(v) if v is not None else None

    def _fetch_summary(pid):
        try:
            from currency_utils import convert_to_usd
            from decimal import Decimal as _D

            summary = portfolio_service.get_portfolio_summary(pid, with_prices=True)
            display_currency = summary.get("display_currency", "USD")
            # Aggregate totals must stay in USD even if this portfolio displays in ILS.
            mv_native = summary.get("total_market_value") or _D("0")
            ug_native = summary.get("total_unrealized_gain") or _D("0")
            mv = float(convert_to_usd(_D(str(mv_native)), display_currency))
            ug = float(convert_to_usd(_D(str(ug_native)), display_currency))
            row = {
                "portfolio_id": pid,
                "display_currency": display_currency,
                "total_market_value": to_float(summary.get("total_market_value")),
                "total_unrealized_gain": to_float(summary.get("total_unrealized_gain")),
                "total_unrealized_gain_pct": to_float(summary.get("total_unrealized_gain_pct")),
                "stock_allocation_pct": to_float(summary.get("stock_allocation_pct")),
                "crypto_allocation_pct": to_float(summary.get("crypto_allocation_pct")),
            }
            holdings_out = []
            day_change = 0.0
            for h in summary.get("holdings", []):
                h_mv = to_float(h.get("market_value")) or 0.0
                h_day_pct = to_float(h.get("day_change_pct")) or 0.0
                day_change += h_mv * h_day_pct / 100.0
                holdings_out.append({
                    "symbol": h["symbol"],
                    "name": h.get("name", h["symbol"]),
                    "total_quantity": to_float(h.get("total_quantity")),
                    "average_cost": to_float(h.get("average_cost")),
                    "price_available": h.get("price_available", False),
                    "current_price": to_float(h.get("current_price")),
                    "market_value": to_float(h.get("market_value")),
                    "unrealized_gain": to_float(h.get("unrealized_gain")),
                    "unrealized_gain_pct": to_float(h.get("unrealized_gain_pct")),
                    "currency": h.get("currency", "USD"),
                })
            row["holdings"] = holdings_out
            row["day_change"] = day_change
            # day_change is summed in native (ILS) when display_currency=ILS
            # because each h_mv is native; convert to USD for the cross-portfolio aggregate.
            day_change_usd = float(
                convert_to_usd(_D(str(day_change)), display_currency)
            )
            return row, mv, ug, day_change_usd
        except Exception:
            return {
                "portfolio_id": pid,
                "total_market_value": None,
                "total_unrealized_gain": None,
                "total_unrealized_gain_pct": None,
                "day_change": None,
            }, 0.0, 0.0, 0.0

    pids = [p["portfolio_id"] for p in portfolios]
    with ThreadPoolExecutor(max_workers=min(len(pids), 5)) as pool:
        futures = {pid: pool.submit(_fetch_summary, pid) for pid in pids}

    result = []
    total_value = 0.0
    total_pnl = 0.0
    total_day_change = 0.0
    for pid in pids:
        row, mv, ug, dc = futures[pid].result()
        result.append(row)
        total_value += mv
        total_pnl += ug
        total_day_change += dc

    return jsonify({
        "portfolios": result,
        "totals": {
            "total_value": total_value,
            "total_pnl": total_pnl,
            "day_change": total_day_change,
        },
    })


@app.route("/api/portfolio/<portfolio_id>/prices")
@login_required
def portfolio_prices(portfolio_id):
    """Return live prices and computed P&L for all holdings as JSON."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        return jsonify({"error": "Not found"}), 404

    summary = portfolio_service.get_portfolio_summary(portfolio_id, with_prices=True)
    breakdowns = {}
    try:
        breakdowns = portfolio_service.get_allocation_breakdowns_from_summary(summary)
        if not isinstance(breakdowns, dict):
            breakdowns = {}
    except Exception:
        breakdowns = {
            "prices_loaded": bool(summary.get("prices_loaded")),
            "sector": [],
            "market": [],
        }

    def to_float(v):
        return float(v) if v is not None else None

    holdings_out = []
    has_ils = False
    for h in summary["holdings"]:
        cur = h.get("currency", "USD")
        if cur == "ILS":
            has_ils = True
        holdings_out.append(
            {
                "symbol": h["symbol"],
                "name": h.get("name", h["symbol"]),
                "total_quantity": to_float(h.get("total_quantity")),
                "average_cost": to_float(h.get("average_cost")),
                "price_available": h.get("price_available", False),
                "current_price": to_float(h.get("current_price")),
                "market_value": to_float(h.get("market_value")),
                "unrealized_gain": to_float(h.get("unrealized_gain")),
                "unrealized_gain_pct": to_float(h.get("unrealized_gain_pct")),
                "currency": cur,
            }
        )

    resp = {
        "holdings": holdings_out,
        "display_currency": summary.get("display_currency", "USD"),
        "total_cost_basis": to_float(summary.get("total_cost_basis")),
        "total_market_value": to_float(summary.get("total_market_value")),
        "total_unrealized_gain": to_float(summary.get("total_unrealized_gain")),
        "total_unrealized_gain_pct": to_float(
            summary.get("total_unrealized_gain_pct")
        ),
        "stock_allocation_pct": to_float(summary.get("stock_allocation_pct")),
        "crypto_allocation_pct": to_float(summary.get("crypto_allocation_pct")),
        "track_cash": bool(summary.get("track_cash")),
        "cash_balance": to_float(summary.get("cash_balance")),
        "cash_allocation_pct": to_float(summary.get("cash_allocation_pct")),
        "breakdowns": {
            "sector": breakdowns.get("sector", []),
            "market": breakdowns.get("market", []),
            "prices_loaded": bool(breakdowns.get("prices_loaded")),
        },
    }
    if has_ils:
        from currency_utils import get_usd_ils_rate
        resp["fx_rates"] = {"ILS_USD": to_float(Decimal("1") / get_usd_ils_rate())}
    return jsonify(resp)


@app.route("/api/portfolio/<portfolio_id>/history")
@login_required
def portfolio_history(portfolio_id):
    """Return portfolio value history as JSON.

    Query params:
      range: 1W | 1M | 3M | YTD | 1Y | all  (default: all → monthly)
    """
    portfolio = get_portfolio_service().get_portfolio(portfolio_id)
    if not portfolio or portfolio.get("user_id") != session["user_id"]:
        return jsonify({"error": "Not found"}), 404
    history_service = get_history_service()
    range_param = request.args.get("range", "all").upper()
    if range_param == "ALL":
        range_param = "all"
    if range_param == "all":
        data = history_service.get_monthly_values(portfolio_id)
        return jsonify({"history": data, "granularity": "monthly"})
    data = history_service.get_values_for_range(portfolio_id, range_param)
    return jsonify({"history": data, "granularity": "daily"})


# ============================================================================
# Price alerts (Phase 2 — STOA-16; persistence + evaluation + in-app notifications)
# ============================================================================


def _alert_row_to_json(row, cache=None):
    if not row:
        return None
    return {
        "alert_id": row["alert_id"],
        "symbol": row["symbol"],
        "asset_type": row["asset_type"],
        "direction": row["direction"],
        "target_price": (
            float(row["target_price"]) if row.get("target_price") is not None else None
        ),
        "current_price": (
            float(cache.get(row["symbol"], {}).get("price", 0))
            if cache and cache.get(row["symbol"], {}).get("price") is not None
            else None
        ),
        "active": bool(row["active"]),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "last_triggered_at": (
            row["last_triggered_at"].isoformat()
            if row.get("last_triggered_at")
            else None
        ),
    }


@app.route("/api/alerts", methods=["GET"])
@login_required
def api_list_alerts():
    from database import get_database_manager
    from datetime import timezone as _tz

    db = get_database_manager()
    uid = session["user_id"]
    rows = db.list_price_alerts_for_user(uid)
    symbols = list({r["symbol"] for r in rows})
    cache = db.get_cached_prices(symbols) if symbols else {}
    alerts = [_alert_row_to_json(r, cache) for r in rows]

    # Compute stats — triggered = has last_triggered_at, active = active but not triggered
    triggered_count = sum(1 for r in rows if r.get("last_triggered_at"))
    active_count = sum(1 for r in rows if r.get("active") and not r.get("last_triggered_at"))
    paused_count = sum(1 for r in rows if not r.get("active"))
    # Triggered in last 30 days: count DISTINCT alerts, not notification rows
    try:
        cutoff = datetime.now(_tz.utc) - timedelta(days=30)
        notifs = db.list_price_alert_notifications_for_user(uid, limit=500)
        triggered_30d = len({
            n["alert_id"] for n in notifs
            if n.get("created_at") and n["created_at"].replace(tzinfo=_tz.utc) >= cutoff
        })
    except Exception:
        triggered_30d = 0

    return jsonify({
        "success": True,
        "alerts": alerts,
        "stats": {
            "active_count": active_count,
            "paused_count": paused_count,
            "triggered_count": triggered_count,
            "triggered_30d_count": triggered_30d,
        },
    })


@app.route("/api/alerts", methods=["POST"])
@login_required
def api_create_alert():
    from database import get_database_manager

    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    direction = (data.get("direction") or "").strip().lower()
    if not symbol:
        return jsonify({"success": False, "error": "symbol is required"}), 400
    if direction not in ("above", "below"):
        return (
            jsonify({"success": False, "error": "direction must be above or below"}),
            400,
        )
    try:
        target_price = float(data.get("target_price"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "invalid target_price"}), 400
    if target_price <= 0:
        return (
            jsonify({"success": False, "error": "target_price must be positive"}),
            400,
        )
    asset_type = (data.get("asset_type") or "stock").strip().lower()
    if asset_type not in ("stock", "crypto"):
        return jsonify({"success": False, "error": "invalid asset_type"}), 400

    import math as _math
    from tiers import get_limit

    uid = session["user_id"]
    db = get_database_manager()
    limit = get_limit(uid, "price_alerts")
    if limit != _math.inf and db.count_user_active_alerts(uid) >= int(limit):
        return jsonify({
            "success": False,
            "error": "quota_exceeded",
            "resource": "price_alerts",
            "limit": int(limit),
            "message": f"Your plan allows {int(limit)} active alerts. Upgrade to add more.",
        }), 402

    alert_id = str(uuid.uuid4())
    db.create_price_alert(
        alert_id=alert_id,
        user_id=session["user_id"],
        symbol=symbol,
        direction=direction,
        target_price=target_price,
        asset_type=asset_type,
    )
    return jsonify({"success": True, "alert_id": alert_id})


@app.route("/api/alerts/<alert_id>", methods=["DELETE"])
@login_required
def api_delete_alert(alert_id):
    from database import get_database_manager

    db = get_database_manager()
    if not db.delete_price_alert(alert_id, session["user_id"]):
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({"success": True})


@app.route("/api/alerts/<alert_id>", methods=["PATCH"])
@login_required
def api_patch_alert(alert_id):
    from database import get_database_manager

    data = request.get_json(silent=True) or {}
    if "active" not in data:
        return jsonify({"success": False, "error": "active field required"}), 400
    active = bool(data.get("active"))
    db = get_database_manager()
    if not db.set_price_alert_active(alert_id, session["user_id"], active):
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({"success": True})


def _alert_notification_row_to_json(row):
    if not row:
        return None
    return {
        "notification_id": row["notification_id"],
        "alert_id": row["alert_id"],
        "symbol": row["symbol"],
        "body": row["body"],
        "read_at": row["read_at"].isoformat() if row.get("read_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }


@app.route("/api/alerts/notifications", methods=["GET"])
@login_required
def api_list_alert_notifications():
    from database import get_database_manager

    db = get_database_manager()
    try:
        limit = min(100, max(1, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    uid = session["user_id"]
    rows = db.list_price_alert_notifications_for_user(uid, limit=limit)
    unread = db.count_unread_price_alert_notifications(uid)
    return jsonify(
        {
            "success": True,
            "notifications": [_alert_notification_row_to_json(r) for r in rows],
            "unread_count": unread,
        }
    )


@app.route("/api/alerts/notifications/mark-all-read", methods=["POST"])
@login_required
def api_mark_all_alert_notifications_read():
    from database import get_database_manager

    db = get_database_manager()
    count = db.mark_all_price_alert_notifications_read(session["user_id"])
    return jsonify({"success": True, "marked": count})


@app.route("/api/alerts/notifications/<notification_id>", methods=["PATCH"])
@login_required
def api_patch_alert_notification(notification_id):
    from database import get_database_manager

    data = request.get_json(silent=True) or {}
    if not data.get("read"):
        return jsonify({"success": False, "error": "read: true required"}), 400
    db = get_database_manager()
    if not db.mark_price_alert_notification_read(notification_id, session["user_id"]):
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({"success": True})


# ============================================================================
# Telegram linking (STOA-35)
# ============================================================================


@app.route("/api/telegram/connect-token", methods=["GET"])
@login_required
def api_telegram_connect_token():
    """Generate a short-lived token a user can paste into Telegram `/connect <token>`."""
    from database import get_database_manager

    db = get_database_manager()
    token = db.create_telegram_connect_token(session["user_id"], ttl_minutes=10)
    return jsonify({"success": True, "token": token, "ttl_minutes": 10})


# ==================== Billing (Whop) ====================


@app.route("/api/billing/plans", methods=["GET"])
def api_billing_plans():
    """Public: tier plan IDs and prices for the SPA pricing page."""
    from tiers import plans_public

    return jsonify({"success": True, "plans": plans_public()})


@app.route("/api/billing/checkout-session", methods=["POST"])
@login_required
def api_billing_checkout_session():
    """Return a Whop hosted checkout URL with user_id/tier/cadence baked in.

    SPA opens this URL in a new tab; on payment Whop fires `membership.activated`
    with the metadata, which our webhook uses to link the user to a tier.
    """
    data = request.get_json(silent=True) or {}
    tier = (data.get("tier") or "").strip().lower()
    cadence = (data.get("cadence") or "monthly").strip().lower()

    from tiers import build_checkout_url

    url = build_checkout_url(tier, cadence, session["user_id"])
    if not url:
        return jsonify({"success": False, "error": "plan not configured"}), 400

    return jsonify({"success": True, "checkout_url": url})


@app.route("/api/billing/webhook", methods=["POST"])
def api_billing_webhook():
    """Whop webhook receiver. Verifies HMAC sig, updates DB."""
    from billing.whop_service import verify_webhook, WhopSignatureError
    from database import get_database_manager
    from datetime import datetime, timezone

    raw_body = request.get_data() or b""
    try:
        event = verify_webhook(raw_body, dict(request.headers))
    except WhopSignatureError as e:
        app.logger.warning("whop webhook rejected: %s", e)
        return jsonify({"ok": False, "error": "signature"}), 401

    event_type = (event.get("action") or event.get("type") or "").lower()
    payload = event.get("data") or {}
    membership_id = (
        payload.get("id")
        or payload.get("membership_id")
        or payload.get("membership", {}).get("id")
    )
    plan_id = payload.get("plan_id") or payload.get("plan", {}).get("id")
    metadata = payload.get("metadata") or {}
    user_id = metadata.get("user_id")

    db = get_database_manager()

    # Look up the user_id from a previously-stored sub if metadata is missing
    if not user_id and membership_id:
        user_id = db.get_subscription_user(membership_id)

    def _parse_period_end():
        v = (
            payload.get("expires_at")
            or payload.get("renewal_period_end")
            or payload.get("current_period_end")
        )
        if not v:
            return None
        try:
            if isinstance(v, (int, float)):
                return datetime.fromtimestamp(int(v), tz=timezone.utc)
            return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            return None

    if event_type in ("membership.activated", "membership.went_valid", "membership_went_valid"):
        # Tier + cadence both come from metadata we set on the checkout URL.
        tier = (metadata.get("tier") or "").lower()
        cadence = (metadata.get("cadence") or "monthly").lower()
        if not (user_id and membership_id):
            return jsonify({"ok": False, "error": "missing user_id/membership_id"}), 400
        if tier not in ("starter", "ultra"):
            app.logger.warning("whop webhook: bad/missing tier metadata: %s", tier)
            return jsonify({"ok": False, "error": "missing tier metadata"}), 400
        if cadence not in ("monthly", "yearly"):
            cadence = "monthly"
        db.upsert_subscription(
            user_id=user_id,
            whop_membership_id=membership_id,
            whop_plan_id=plan_id or "",
            tier=tier,
            cadence=cadence,
            status="active",
            current_period_end=_parse_period_end(),
        )
        db.set_user_tier(user_id, tier)
        return jsonify({"ok": True})

    if event_type in ("membership.deactivated", "membership.cancelled", "membership_went_invalid"):
        if membership_id:
            db.set_subscription_status(membership_id, "canceled")
        if user_id:
            db.set_user_tier(user_id, "free")
        return jsonify({"ok": True})

    if event_type in ("payment.succeeded", "payment_succeeded"):
        period_end = _parse_period_end()
        if membership_id and period_end:
            db.update_subscription_period_end(membership_id, period_end)
        return jsonify({"ok": True})

    if event_type in ("refund.created", "refund_created"):
        if membership_id:
            db.set_subscription_status(membership_id, "canceled")
        if user_id:
            db.set_user_tier(user_id, "free")
        app.logger.info("whop refund processed for membership %s", membership_id)
        return jsonify({"ok": True})

    # Unknown event types: 200 so Whop doesn't retry forever
    return jsonify({"ok": True, "ignored": event_type})


# Some webhook handlers must NOT receive CSRF protection (Flask-WTF wraps
# the app); make sure this one is exempt if csrf is initialized.
try:
    from flask_wtf.csrf import CSRFProtect  # noqa: F401
    _csrf = app.extensions.get("csrf") if hasattr(app, "extensions") else None
    if _csrf is not None and hasattr(_csrf, "exempt"):
        _csrf.exempt(api_billing_webhook)
except Exception:
    pass


@app.route("/api/settings", methods=["GET"])
@login_required
def api_settings_get():
    """Return user profile and preferences."""
    from database import get_database_manager

    db = get_database_manager()
    user = db.get_user_by_id(session["user_id"])
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404

    from tiers import get_all_limits

    # Never return decrypted email in logs — just expose username
    return jsonify({
        "success": True,
        "profile": {
            "user_id": user["user_id"],
            "display_name": user.get("username", ""),
            "is_pro": bool(user.get("is_pro")),
            "tier": user.get("tier", "free"),
            "tier_limits": get_all_limits(user["user_id"]),
        },
        "preferences": user.get("preferences") or {},
    })


@app.route("/api/settings", methods=["PUT"])
@login_required
def api_settings_put():
    """Update user preferences (partial update merged into JSONB)."""
    from database import get_database_manager

    data = request.get_json(silent=True) or {}
    display_name = data.pop("display_name", None)
    if display_name is not None:
        display_name = str(display_name).strip()[:80]

    db = get_database_manager()
    try:
        db.update_user_preferences(
            user_id=session["user_id"],
            patch=data,
            display_name=display_name if display_name else None,
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/telegram/disconnect", methods=["POST"])
@login_required
def api_telegram_disconnect():
    """Disconnect Telegram integration by clearing the stored chat ID."""
    from database import get_database_manager

    db = get_database_manager()
    conn = None
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET telegram_chat_id = NULL WHERE user_id = %s",
                (session["user_id"],),
            )
        conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db._release(conn)


@app.route("/api/watchlists/all", methods=["DELETE"])
@login_required
def api_delete_all_watchlists():
    """Delete all watchlists for the current user (danger zone)."""
    wl_svc = get_watchlist_service()
    uid = session["user_id"]
    try:
        watchlists = wl_svc.db.list_watchlists(uid)
        for wl in watchlists:
            wl_svc.delete_watchlist(wl["watchlist_id"], uid)
        return jsonify({"success": True, "deleted": len(watchlists)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/reports/all", methods=["DELETE"])
@login_required
def api_delete_all_reports():
    """Delete all reports for the current user (danger zone)."""
    storage = ReportStorage()
    uid = session["user_id"]
    try:
        reports, _ = storage.get_all_reports(user_id=uid, limit=10000, offset=0)
        for r in reports:
            storage.delete_report(r["report_id"])
        return jsonify({"success": True, "deleted": len(reports)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/account", methods=["DELETE"])
@login_required
def api_delete_account():
    """Delete the current user's account and all associated data."""
    from database import get_database_manager

    uid = session["user_id"]
    db = get_database_manager()
    conn = None
    try:
        # Delete from Clerk first
        try:
            clerk_client.users.delete(user_id=uid)
        except Exception as e:
            app.logger.warning("Clerk user delete failed: %s", e)

        # Delete all user data (reports, holdings cascade from portfolio, watchlists, etc.)
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM price_alert_notifications WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM price_alerts WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM ticker_notes WHERE user_id = %s", (uid,))
            # Reports and chunks cascade
            cur.execute("DELETE FROM reports WHERE user_id = %s", (uid,))
            # Watchlist items/sections cascade from watchlists
            cur.execute("DELETE FROM watchlists WHERE user_id = %s", (uid,))
            # Holdings/transactions cascade from portfolios
            cur.execute("DELETE FROM portfolios WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM telegram_connect_tokens WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))
        conn.commit()
        session.clear()
        return jsonify({"success": True})
    except Exception as e:
        if conn:
            conn.rollback()
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db._release(conn)


@app.route("/api/telegram/test-message", methods=["POST"])
@login_required
def api_telegram_test_message():
    """Send a test message to the user's connected Telegram account."""
    from database import get_database_manager

    db = get_database_manager()
    user = db.get_user_by_id(session["user_id"])
    if not user or not user.get("telegram_chat_id"):
        return jsonify({"success": False, "error": "Telegram not connected"}), 400

    chat_id = user["telegram_chat_id"]
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        return jsonify({"success": False, "error": "Telegram bot not configured"}), 500

    try:
        import telegram as tg

        bot = tg.Bot(token=bot_token)
        import asyncio

        asyncio.get_event_loop().run_until_complete(
            bot.send_message(chat_id=chat_id, text="Test message from StockPro — your alerts are connected!")
        )
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# Report History & Export Routes
# ============================================================================
@app.route("/reports")
@login_required
def report_history():
    """Render ticker-centric research history page."""
    get_or_create_session_id()

    # Get filter parameters from query string
    ticker = request.args.get("ticker", "").strip().upper() or None
    trade_type = request.args.get("trade_type", "").strip() or None
    sort_order = request.args.get("sort", "DESC").upper()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    per_page = 12

    # Calculate offset
    offset = (page - 1) * per_page

    try:
        storage = ReportStorage()
        user_id = session.get("user_id")
        reports, total_count = storage.get_report_ticker_summaries(
            user_id=user_id,
            ticker=ticker,
            sort_order=sort_order,
            limit=per_page,
            offset=offset,
        )

        # Calculate pagination info
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1

        if _wants_json():
            return jsonify({"reports": reports, "total_count": total_count, "current_page": page, "total_pages": total_pages})
        return render_template(
            "reports.html",
            reports=reports,
            total_count=total_count,
            current_page=page,
            total_pages=total_pages,
            per_page=per_page,
            filter_ticker=ticker or "",
            filter_trade_type=trade_type or "",
            sort_order=sort_order,
            page_range=_page_range(page, total_pages),
        )
    except Exception as e:
        if _wants_json():
            return jsonify({"error": str(e)}), 500
        return render_template(
            "reports.html",
            reports=[],
            total_count=0,
            current_page=1,
            total_pages=1,
            per_page=per_page,
            filter_ticker="",
            filter_trade_type="",
            sort_order="DESC",
            page_range=[1],
            error=str(e),
        )


_BULLISH_WORDS = frozenset([
    "surge", "rally", "beat", "record", "profit", "upgrade", "buy",
    "growth", "strong", "outperform", "bullish", "rises", "gains", "up",
    "positive", "boost", "increased", "higher", "exceeded",
])
_BEARISH_WORDS = frozenset([
    "drop", "plunge", "miss", "loss", "downgrade", "sell", "decline",
    "weak", "underperform", "bearish", "falls", "down", "negative",
    "cut", "lower", "disappointing", "concern", "risk", "layoff", "warning",
])


def _news_sentiment(text: str) -> str:
    """Simple keyword-based sentiment: bullish / bearish / neutral."""
    words = (text or "").lower().split()
    bull = sum(1 for w in words if w.strip(".,!?") in _BULLISH_WORDS)
    bear = sum(1 for w in words if w.strip(".,!?") in _BEARISH_WORDS)
    if bull > bear:
        return "bullish"
    if bear > bull:
        return "bearish"
    return "neutral"


def _enrich_news(articles, symbol_filter: str = None):
    """Add sentiment field to news articles; optionally filter to one symbol."""
    enriched = []
    for a in (articles or []):
        title = (a.get("title") or a.get("headline") or "")
        if symbol_filter and symbol_filter not in title.upper():
            continue
        a = dict(a)
        a["sentiment"] = _news_sentiment(title + " " + (a.get("summary") or ""))
        enriched.append(a)
    return enriched


@app.route("/api/news")
def api_news():
    from news_service import get_briefing

    symbol = (request.args.get("symbol") or "").strip().upper() or None
    articles = get_briefing()
    return jsonify(_enrich_news(articles, symbol_filter=symbol))


@app.route("/api/news/more")
def api_news_more():
    from news_service import get_more

    return jsonify(get_more())


@app.route("/api/reports")
@login_required
def api_reports():
    """AJAX endpoint for filtered reports (returns JSON)."""
    ticker = request.args.get("ticker", "").strip().upper() or None
    trade_type = request.args.get("trade_type", "").strip() or None
    sort_order = request.args.get("sort", "DESC").upper()
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    per_page = 12

    offset = (page - 1) * per_page

    try:
        storage = ReportStorage()
        user_id = session.get("user_id")
        reports, total_count = storage.get_all_reports(
            ticker=ticker,
            trade_type=trade_type,
            sort_order=sort_order,
            limit=per_page,
            offset=offset,
            user_id=user_id,
        )

        # Convert datetime objects to ISO strings for JSON
        for report in reports:
            if report.get("created_at"):
                report["created_at"] = report["created_at"].isoformat()

        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1

        return jsonify(
            {
                "success": True,
                "reports": reports,
                "total_count": total_count,
                "current_page": page,
                "total_pages": total_pages,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/report/<report_id>")
@login_required
def view_report(report_id):
    """View a single report."""
    try:
        storage = ReportStorage()
        report = storage.get_report(report_id, user_id=session.get("user_id"))

        if not report:
            abort(404)

        if _wants_json():
            return jsonify({"report": report})
        return render_template("report_view.html", report=report)
    except Exception as e:
        app.logger.error(f"Error loading report {report_id}: {e}")
        abort(500)


@app.route("/report/<report_id>/pdf")
@login_required
def download_report_pdf(report_id):
    """Download report as PDF."""
    try:
        storage = ReportStorage()
        report = storage.get_report(report_id, user_id=session.get("user_id"))

        if not report:
            abort(404)

        # Generate PDF
        pdf_generator = get_pdf_generator()
        pdf_bytes = pdf_generator.generate_pdf(
            ticker=report["ticker"],
            trade_type=report["trade_type"],
            report_text=report["report_text"],
            created_at=report.get("created_at"),
        )

        # Create filename
        filename = f"{report['ticker']}_report_{report_id[:8]}.pdf"

        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": len(pdf_bytes),
            },
        )
    except Exception as e:
        app.logger.error(f"Error generating PDF for report {report_id}: {e}")
        abort(500)


@app.route("/report/<report_id>/chat")
@login_required
def chat_with_report(report_id):
    """Open chat interface with report context pre-loaded."""
    try:
        storage = ReportStorage()
        report = storage.get_report(report_id, user_id=session.get("user_id"))

        if not report:
            abort(404)

        # Store report context in session for chat
        session["current_report_id"] = report_id
        session["current_ticker"] = report["ticker"]
        session["current_trade_type"] = report["trade_type"]
        session["report_chat_mode"] = True

        # Initialize conversation with report context
        session["conversation_history"] = [
            {
                "role": "assistant",
                "content": f"I've loaded the research report for **{report['ticker']}** ({report['trade_type']}). "
                f"Feel free to ask me any questions about this analysis!",
            }
        ]
        flash_status(f'Report loaded: {report["ticker"]}', "success")

        return redirect(url_for("chat"))
    except Exception as e:
        flash_status(f"Error loading report: {str(e)}", "error")
        return redirect(url_for("report_history"))


# ==================== Ticker-Centric Report View & Notes ====================


@app.route("/ticker/<symbol>")
@login_required
def ticker_detail(symbol: str):
    """Ticker-centric page showing all reports + per-user notes."""
    from database import get_database_manager

    symbol = (symbol or "").strip().upper()
    if not symbol:
        abort(404)

    get_or_create_session_id()

    storage = ReportStorage()
    user_id = session.get("user_id")

    reports, _ = storage.get_all_reports(
        ticker=symbol,
        trade_type=None,
        sort_order="DESC",
        limit=50,
        offset=0,
        user_id=user_id,
    )

    db = get_database_manager()
    raw_notes = db.get_ticker_notes(user_id, symbol) if user_id else []
    notes = []
    for n in raw_notes:
        preview = bleach.clean(n["content"], tags=[], strip=True)[:120]
        notes.append(
            {
                "id": n["id"],
                "title": n["title"] or "Untitled",
                "content": n["content"],
                "preview": preview,
                "created_at": n["created_at"],
            }
        )

    if _wants_json():
        return jsonify({"symbol": symbol, "reports": reports, "notes": notes})
    return render_template(
        "ticker.html",
        symbol=symbol,
        reports=reports,
        notes=notes,
    )


@app.route("/ticker/<symbol>/notes", methods=["POST"])
@login_required
def save_ticker_notes(symbol: str):
    """Create a new note for the ticker."""
    from database import get_database_manager

    symbol = (symbol or "").strip().upper()
    if not symbol:
        abort(404)

    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("sign_in"))

    title = (request.form.get("title", "") or "").strip()
    raw_content = request.form.get("content", "") or ""
    cleaned = bleach.clean(
        raw_content,
        tags=_MD_ALLOWED_TAGS,
        attributes=_MD_ALLOWED_ATTRS,
        strip=True,
    )

    db = get_database_manager()
    db.create_ticker_note(user_id, symbol, title, cleaned)
    flash_status("Note saved.", "success")
    return redirect(url_for("ticker_detail", symbol=symbol))


@app.route("/ticker/<symbol>/notes/<int:note_id>/edit", methods=["POST"])
@login_required
def edit_ticker_note(symbol: str, note_id: int):
    """Update an existing ticker note."""
    from database import get_database_manager

    symbol = (symbol or "").strip().upper()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("sign_in"))

    title = (request.form.get("title", "") or "").strip()
    raw_content = request.form.get("content", "") or ""
    cleaned = bleach.clean(
        raw_content,
        tags=_MD_ALLOWED_TAGS,
        attributes=_MD_ALLOWED_ATTRS,
        strip=True,
    )

    db = get_database_manager()
    db.update_ticker_note(note_id, user_id, title, cleaned)
    flash_status("Note updated.", "success")
    return redirect(url_for("ticker_detail", symbol=symbol))


@app.route("/ticker/<symbol>/notes/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_ticker_note(symbol: str, note_id: int):
    """Delete a ticker note."""
    from database import get_database_manager

    symbol = (symbol or "").strip().upper()
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("sign_in"))

    db = get_database_manager()
    db.delete_ticker_note(note_id, user_id)
    flash_status("Note deleted.", "success")
    return redirect(url_for("ticker_detail", symbol=symbol))


# ==================== Watchlist Routes ====================


@app.route("/watchlist")
@login_required
def watchlist():
    """Main watchlist page — auto-creates default watchlist if none exist."""
    try:
        watchlist_svc = get_watchlist_service()
        user_id = session["user_id"]
        watchlists = watchlist_svc.list_watchlists(user_id)

        # Auto-create default
        if not watchlists:
            watchlist_svc.get_or_create_default_watchlist(user_id)
            watchlists = watchlist_svc.list_watchlists(user_id)

        active_watchlist_id = request.args.get(
            "wl", watchlists[0]["watchlist_id"] if watchlists else None
        )
        active_watchlist = None
        if active_watchlist_id:
            # Ownership check
            wl = watchlist_svc.db.get_watchlist(active_watchlist_id)
            if wl and wl["user_id"] == user_id:
                active_watchlist = watchlist_svc.get_watchlist_with_items(
                    active_watchlist_id
                )

        status = pop_status()
        if _wants_json():
            return jsonify({"watchlists": watchlists, "active_watchlist": active_watchlist})
        return render_template(
            "watchlist.html",
            watchlists=watchlists,
            active_watchlist=active_watchlist,
            **status,
        )
    except Exception as e:
        if _wants_json():
            return jsonify({"error": str(e)}), 500
        return render_template(
            "watchlist.html",
            watchlists=[],
            active_watchlist=None,
            status_message=f"Error loading watchlist: {str(e)}",
            status_type="error",
        )


@app.route("/watchlist/create", methods=["POST"])
@login_required
def watchlist_create():
    name = request.form.get("name", "").strip() or "My Watchlist"
    try:
        watchlist_svc = get_watchlist_service()
        wl_id = watchlist_svc.create_watchlist(session["user_id"], name)
        flash_status(f'Watchlist "{name}" created', "success")
        return redirect(url_for("watchlist", wl=wl_id))
    except Exception as e:
        flash_status(f"Error: {str(e)}", "error")
        return redirect(url_for("watchlist"))


@app.route("/watchlist/<watchlist_id>/rename", methods=["POST"])
@login_required
def watchlist_rename(watchlist_id):
    watchlist_svc = get_watchlist_service()
    wl = watchlist_svc.db.get_watchlist(watchlist_id)
    if not wl or wl["user_id"] != session["user_id"]:
        abort(403)
    name = request.form.get("name", "").strip()
    if name:
        watchlist_svc.rename_watchlist(watchlist_id, name)
        flash_status(f'Renamed to "{name}"', "success")
    return redirect(url_for("watchlist", wl=watchlist_id))


@app.route("/watchlist/<watchlist_id>/delete", methods=["POST"])
@login_required
def watchlist_delete(watchlist_id):
    watchlist_svc = get_watchlist_service()
    wl = watchlist_svc.db.get_watchlist(watchlist_id)
    if not wl or wl["user_id"] != session["user_id"]:
        abort(403)
    watchlist_svc.delete_watchlist(watchlist_id)
    flash_status("Watchlist deleted", "success")
    return redirect(url_for("watchlist"))


@app.route("/watchlist/<watchlist_id>/add-symbol", methods=["POST"])
@login_required
def watchlist_add_symbol(watchlist_id):
    watchlist_svc = get_watchlist_service()
    wl = watchlist_svc.db.get_watchlist(watchlist_id)
    if not wl or wl["user_id"] != session["user_id"]:
        abort(403)
    symbol = request.form.get("symbol", "").strip().upper()
    section_id = request.form.get("section_id") or None
    if not symbol:
        flash_status("Symbol is required", "error")
        return redirect(url_for("watchlist", wl=watchlist_id))
    try:
        watchlist_svc.add_symbol(watchlist_id, symbol, section_id)
        flash_status(f"{symbol} added to watchlist", "success")
    except ValueError as e:
        flash_status(str(e), "error")
    except Exception as e:
        flash_status(f"Error adding {symbol}: {str(e)}", "error")
    return redirect(url_for("watchlist", wl=watchlist_id))


@app.route("/watchlist/item/<item_id>/remove", methods=["POST"])
@login_required
def watchlist_remove_item(item_id):
    watchlist_svc = get_watchlist_service()
    # Find which watchlist this item belongs to for ownership check + redirect
    from database import get_database_manager

    db = get_database_manager()
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT wi.watchlist_id, wl.user_id
                FROM watchlist_items wi
                JOIN watchlists wl ON wi.watchlist_id = wl.watchlist_id
                WHERE wi.item_id = %s
            """,
                (item_id,),
            )
            row = cur.fetchone()
    finally:
        db._release(conn)

    if not row or row["user_id"] != session["user_id"]:
        abort(403)

    watchlist_id = row["watchlist_id"]
    watchlist_svc.remove_symbol(item_id)
    flash_status("Symbol removed", "success")
    return redirect(url_for("watchlist", wl=watchlist_id))


@app.route("/watchlist/item/<item_id>/pin", methods=["POST"])
@login_required
def watchlist_toggle_pin(item_id):
    watchlist_svc = get_watchlist_service()
    from database import get_database_manager

    db = get_database_manager()
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT wi.watchlist_id, wi.is_pinned, wl.user_id
                FROM watchlist_items wi
                JOIN watchlists wl ON wi.watchlist_id = wl.watchlist_id
                WHERE wi.item_id = %s
            """,
                (item_id,),
            )
            row = cur.fetchone()
    finally:
        db._release(conn)

    if not row or row["user_id"] != session["user_id"]:
        abort(403)

    watchlist_id = row["watchlist_id"]
    user_id = session["user_id"]

    try:
        if row["is_pinned"]:
            watchlist_svc.unpin_item(item_id)
            flash_status("Unpinned from homepage", "success")
        else:
            watchlist_svc.pin_item(user_id, item_id)
            flash_status("Pinned to homepage", "success")
    except ValueError as e:
        flash_status(str(e), "error")

    return redirect(url_for("watchlist", wl=watchlist_id))


@app.route("/watchlist/<watchlist_id>/section/create", methods=["POST"])
@login_required
def watchlist_create_section(watchlist_id):
    watchlist_svc = get_watchlist_service()
    wl = watchlist_svc.db.get_watchlist(watchlist_id)
    if not wl or wl["user_id"] != session["user_id"]:
        abort(403)
    name = request.form.get("name", "").strip()
    if name:
        try:
            watchlist_svc.create_section(watchlist_id, name)
            flash_status(f'Section "{name}" created', "success")
        except Exception as e:
            flash_status(f"Error creating section: {str(e)}", "error")
    return redirect(url_for("watchlist", wl=watchlist_id))


@app.route("/watchlist/section/<section_id>/rename", methods=["POST"])
@login_required
def watchlist_rename_section(section_id):
    watchlist_svc = get_watchlist_service()
    from database import get_database_manager

    db = get_database_manager()
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ws.watchlist_id, wl.user_id
                FROM watchlist_sections ws
                JOIN watchlists wl ON ws.watchlist_id = wl.watchlist_id
                WHERE ws.section_id = %s
            """,
                (section_id,),
            )
            row = cur.fetchone()
    finally:
        db._release(conn)

    if not row or row["user_id"] != session["user_id"]:
        abort(403)

    name = request.form.get("name", "").strip()
    if name:
        watchlist_svc.rename_section(section_id, name)
        flash_status(f'Section renamed to "{name}"', "success")
    return redirect(url_for("watchlist", wl=row["watchlist_id"]))


@app.route("/watchlist/section/<section_id>/delete", methods=["POST"])
@login_required
def watchlist_delete_section(section_id):
    watchlist_svc = get_watchlist_service()
    from database import get_database_manager

    db = get_database_manager()
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT ws.watchlist_id, wl.user_id
                FROM watchlist_sections ws
                JOIN watchlists wl ON ws.watchlist_id = wl.watchlist_id
                WHERE ws.section_id = %s
            """,
                (section_id,),
            )
            row = cur.fetchone()
    finally:
        db._release(conn)

    if not row or row["user_id"] != session["user_id"]:
        abort(403)

    watchlist_svc.delete_section(section_id)
    flash_status("Section deleted", "success")
    return redirect(url_for("watchlist", wl=row["watchlist_id"]))


@app.route("/api/watchlist/<watchlist_id>/news-recap", methods=["GET"])
@login_required
@limiter.limit(
    lambda: os.getenv("STOCKPRO_RATE_LIMIT_WATCHLIST_NEWS_RECAP", "30 per hour"),
    key_func=get_remote_address,
)
def api_watchlist_news_recap(watchlist_id: str):
    """Return a compact digest of recent news for all symbols in a watchlist."""
    watchlist_svc = get_watchlist_service()
    wl = watchlist_svc.db.get_watchlist(watchlist_id)
    if not wl or wl["user_id"] != session["user_id"]:
        abort(403)

    items = watchlist_svc.db.get_watchlist_items(watchlist_id) or []
    symbols = [row.get("symbol") for row in items if isinstance(row, dict)]

    from watchlist.news_recap_service import get_watchlist_news_recap

    try:
        digest = get_watchlist_news_recap(
            user_id=session["user_id"], watchlist_id=watchlist_id, symbols=symbols
        )
        return jsonify({"success": True, "items": digest})
    except Exception as e:
        app.logger.warning("watchlist news recap failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": "Failed to fetch news recap"}), 500


@app.route("/api/watchlist/<watchlist_id>/earnings", methods=["GET"])
@login_required
@limiter.limit(
    lambda: os.getenv("STOCKPRO_RATE_LIMIT_WATCHLIST_EARNINGS", "30 per hour"),
    key_func=get_remote_address,
)
def api_watchlist_earnings_calendar(watchlist_id: str):
    """Return upcoming earnings calendar for all symbols in a watchlist."""
    watchlist_svc = get_watchlist_service()
    wl = watchlist_svc.db.get_watchlist(watchlist_id)
    if not wl or wl["user_id"] != session["user_id"]:
        abort(403)

    items = watchlist_svc.db.get_watchlist_items(watchlist_id) or []
    symbols = [row.get("symbol") for row in items if isinstance(row, dict)]

    from watchlist.earnings_calendar_service import get_watchlist_earnings_calendar

    try:
        calendar = get_watchlist_earnings_calendar(
            user_id=session["user_id"],
            watchlist_id=watchlist_id,
            symbols=symbols,
        )
        return jsonify({"success": True, "items": calendar})
    except Exception as e:
        app.logger.warning("watchlist earnings calendar failed: %s", e, exc_info=True)
        return (
            jsonify({"success": False, "error": "Failed to fetch earnings calendar"}),
            500,
        )


# ============================================================================
# Analytics endpoint (Phase 2 React SPA)
# ============================================================================


@app.route("/api/portfolio/<portfolio_id>/analytics")
@login_required
def api_portfolio_analytics(portfolio_id: str):
    """Return analytics data for the portfolio analytics page.

    Query params:
      range: 1M | 3M | YTD | 1Y | all  (default: 1Y)

    Returns KPIs, allocation, sector breakdown, performance ranking,
    value history, and risk metrics.
    """
    portfolio = get_portfolio_service().get_portfolio(portfolio_id)
    if not portfolio or portfolio.get("user_id") != session["user_id"]:
        return jsonify({"success": False, "error": "Not found"}), 404

    range_param = request.args.get("range", "1Y").upper()
    if range_param == "ALL":
        range_param = "all"

    svc = get_portfolio_service()

    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    try:
        summary = svc.get_portfolio_summary(portfolio_id, with_prices=True)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    holdings = summary.get("holdings", [])

    # Best / worst performer
    ranked = sorted(
        [h for h in holdings if h.get("unrealized_gain_pct") is not None],
        key=lambda h: float(h.get("unrealized_gain_pct") or 0),
        reverse=True,
    )
    best = ranked[0] if ranked else None
    worst = ranked[-1] if ranked else None

    kpis = {
        "total_value": _f(summary.get("total_market_value")),
        "total_cost_basis": _f(summary.get("total_cost_basis")),
        "total_return": _f(summary.get("total_unrealized_gain")),
        "total_return_pct": _f(summary.get("total_unrealized_gain_pct")),
        "holdings_count": summary.get("holdings_count"),
        "best_performer": {
            "symbol": best["symbol"],
            "return_pct": _f(best.get("unrealized_gain_pct")),
        } if best else None,
        "worst_performer": {
            "symbol": worst["symbol"],
            "return_pct": _f(worst.get("unrealized_gain_pct")),
        } if worst else None,
    }

    # Allocation + sector breakdowns
    try:
        breakdowns = svc.get_allocation_breakdowns_from_summary(summary)
    except Exception:
        breakdowns = {"sector": [], "market": []}

    # Performance leaderboard (all holdings sorted by return %)
    leaderboard = [
        {
            "symbol": h["symbol"],
            "asset_type": h.get("asset_type"),
            "return_pct": _f(h.get("unrealized_gain_pct")),
            "return_abs": _f(h.get("unrealized_gain")),
            "market_value": _f(h.get("market_value")),
            "weight_pct": _f(
                (float(h.get("market_value") or 0) / float(summary.get("total_market_value") or 1)) * 100
                if summary.get("total_market_value") else None
            ),
        }
        for h in ranked
    ]

    # Portfolio value history
    try:
        history_data = get_history_service().get_values_for_range(portfolio_id, range_param)
    except Exception:
        history_data = []

    # Risk metrics (imported from risk_service if available)
    risk_metrics = {}
    try:
        from portfolio.risk_service import compute_risk_metrics
        risk_metrics = compute_risk_metrics(portfolio_id, holdings)
    except ImportError:
        pass
    except Exception as e:
        app.logger.warning("risk metrics failed: %s", e)

    return jsonify({
        "success": True,
        "kpis": kpis,
        "allocation": breakdowns.get("market", []),
        "sector": breakdowns.get("sector", []),
        "leaderboard": leaderboard,
        "history": history_data,
        "risk_metrics": risk_metrics,
    })


# ============================================================================
# Ticker data endpoints (Phase 2 React SPA)
# ============================================================================

_YFINANCE_PERIOD_MAP = {
    "1D": "1d",
    "1W": "5d",
    "1M": "1mo",
    "3M": "3mo",
    "YTD": "ytd",
    "1Y": "1y",
    "all": "max",
}


@app.route("/api/ticker/<symbol>/history")
@login_required
def api_ticker_history(symbol: str):
    """Return OHLCV price history for a ticker.

    Query params:
      range: 1D | 1W | 1M | 3M | YTD | 1Y | all  (default: 1M)
    """
    import yfinance as yf

    range_param = request.args.get("range", "1M").upper()
    if range_param == "ALL":
        range_param = "all"
    period = _YFINANCE_PERIOD_MAP.get(range_param, "1mo")
    try:
        ticker = yf.Ticker(symbol.upper())
        hist = ticker.history(period=period)
        if hist.empty:
            return jsonify({"success": True, "history": []})
        data = [
            {"date": str(idx.date()), "close": round(float(row["Close"]), 4)}
            for idx, row in hist.iterrows()
        ]
        return jsonify({"success": True, "history": data})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/ticker/<symbol>/fundamentals")
@login_required
def api_ticker_fundamentals(symbol: str):
    """Return key fundamental metrics for a ticker via yfinance."""
    import yfinance as yf

    def _safe(val):
        if val is None or val != val:  # NaN check
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return val

    try:
        info = yf.Ticker(symbol.upper()).info
        raw_currency = info.get("currency", "USD")
        if raw_currency == "ILA":
            currency = "ILS"
            ila_convert = lambda v: v / 100 if v is not None else None
        else:
            currency = raw_currency if raw_currency else "USD"
            ila_convert = lambda v: v

        current_price = _safe(info.get("currentPrice") or info.get("regularMarketPrice"))
        w52_high = _safe(info.get("fiftyTwoWeekHigh"))
        w52_low = _safe(info.get("fiftyTwoWeekLow"))

        return jsonify({
            "success": True,
            "symbol": symbol.upper(),
            "name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "exchange": info.get("exchange"),
            "currency": currency,
            "market_cap": _safe(info.get("marketCap")),
            "pe_ratio": _safe(info.get("trailingPE")),
            "eps": _safe(info.get("trailingEps")),
            "revenue": _safe(info.get("totalRevenue")),
            "gross_margin": _safe(info.get("grossMargins")),
            "week_52_high": _safe(ila_convert(w52_high)),
            "week_52_low": _safe(ila_convert(w52_low)),
            "avg_volume": _safe(info.get("averageVolume")),
            "beta": _safe(info.get("beta")),
            "dividend_yield": _safe(info.get("dividendYield")),
            "current_price": _safe(ila_convert(current_price)),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/ticker/<symbol>/public-view")
@login_required
def api_ticker_public_view(symbol: str):
    """Return cached public-view summary for a symbol (or computing placeholder).

    If no row exists yet, or the row is stuck on `status='computing'` with a
    stale `last_updated` (likely from a prior crashed refresh), kick off a
    background refresh so the next poll picks up real data instead of looping
    forever on the placeholder.
    """
    import threading as _t
    from datetime import datetime, timezone, timedelta
    from database import get_database_manager

    sym = (symbol or "").strip().upper()
    db = get_database_manager()
    row = db.get_ticker_public_view(sym)

    def _kick_refresh():
        from watchlist.public_view_service import refresh_public_view
        _t.Thread(target=refresh_public_view, args=(sym,), daemon=True).start()

    if not row:
        # Not yet computed — start a refresh and return a placeholder.
        try:
            db.upsert_ticker_public_view(sym, status="computing")
        except Exception:
            pass
        _kick_refresh()
        return jsonify({
            "symbol": sym,
            "status": "computing",
            "summary_md": None,
            "bullish_pct": None,
            "top_themes": [],
            "reddit_posts": [],
            "x_posts": [],
            "last_updated": None,
            "error_message": None,
        })

    # Row exists but stuck on "computing" with stale last_updated — re-kick.
    if (row.get("status") or "ready") == "computing":
        last = row.get("last_updated")
        if last is None:
            _kick_refresh()
        else:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last > timedelta(minutes=2):
                _kick_refresh()
    return jsonify({
        "symbol": sym,
        "status": row.get("status") or "ready",
        "summary_md": row.get("summary_md"),
        "bullish_pct": row.get("bullish_pct"),
        "top_themes": row.get("top_themes_json") or [],
        "reddit_posts": row.get("reddit_posts") or [],
        "x_posts": row.get("x_posts") or [],
        "last_updated": row["last_updated"].isoformat() if row.get("last_updated") else None,
        "error_message": row.get("error_message"),
    })


@app.route("/api/ticker/<symbol>/public-view/refresh", methods=["POST"])
@login_required
@limiter.limit("1 per minute", key_func=get_remote_address)
def api_ticker_public_view_refresh(symbol: str):
    """Force a refresh of the public-view for a symbol (rate-limited)."""
    import threading as _t
    from watchlist.public_view_service import refresh_public_view
    from database import get_database_manager

    sym = (symbol or "").strip().upper()
    if not sym:
        return jsonify({"success": False, "error": "missing symbol"}), 400

    db = get_database_manager()
    try:
        db.upsert_ticker_public_view(sym, status="computing")
    except Exception:
        pass

    _t.Thread(target=refresh_public_view, args=(sym,), daemon=True).start()
    return jsonify({"success": True, "status": "computing"}), 202


@app.route("/api/ticker/search")
@login_required
def api_ticker_search():
    """Validate a ticker symbol and return name + price preview.

    Query params:
      q: symbol to search (required)
    """
    import yfinance as yf
    from database import get_database_manager

    q = (request.args.get("q") or "").strip().upper()
    if not q:
        return jsonify({"success": False, "error": "q is required"}), 400

    # Check price_cache first for a fast response
    try:
        db = get_database_manager()
        cached = db.get_cached_prices([q])
        if cached and q in cached:
            c = cached[q]
            return jsonify({
                "success": True,
                "valid": True,
                "symbol": q,
                "name": c.get("display_name", q),
                "price": float(c["price"]) if c.get("price") else None,
                "change_pct": float(c["change_percent"]) if c.get("change_percent") else None,
                "currency": c.get("currency", "USD"),
            })
    except Exception:
        pass

    # Fallback to yfinance
    try:
        info = yf.Ticker(q).info
        name = info.get("longName") or info.get("shortName") or q
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        change_pct = info.get("regularMarketChangePercent")
        raw_currency = info.get("currency", "USD")
        currency = "ILS" if raw_currency == "ILA" else (raw_currency or "USD")
        if raw_currency == "ILA" and price:
            price = price / 100
        if not name or name == q:
            return jsonify({"success": True, "valid": False, "symbol": q})
        return jsonify({
            "success": True,
            "valid": True,
            "symbol": q,
            "name": name,
            "price": float(price) if price else None,
            "change_pct": float(change_pct) if change_pct else None,
            "currency": currency,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/tickers/recent")
@login_required
def api_tickers_recent():
    """Return the last 5 distinct tickers the user has researched."""
    from database import get_database_manager

    db = get_database_manager()
    uid = session["user_id"]
    conn = None
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (ticker) ticker, created_at
                FROM reports
                WHERE user_id = %s
                ORDER BY ticker, created_at DESC
                LIMIT 5
                """,
                (uid,),
            )
            rows = cur.fetchall()
        tickers = [r[0] for r in rows]
        return jsonify({"success": True, "tickers": tickers})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db._release(conn)


@app.route("/api/report/<report_id>/sections")
@login_required
def api_report_sections(report_id: str):
    """Return ordered list of section names for report TOC and chat topics."""
    from database import get_database_manager

    storage = ReportStorage()
    report = storage.get_report(report_id, user_id=session["user_id"])
    if not report:
        return jsonify({"success": False, "error": "Not found"}), 404

    db = get_database_manager()
    conn = None
    try:
        conn = db.get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT section, MIN(chunk_index) AS first_idx
                FROM report_chunks
                WHERE report_id = %s AND section IS NOT NULL AND section != ''
                GROUP BY section
                ORDER BY first_idx ASC
                """,
                (report_id,),
            )
            rows = cur.fetchall()
        sections = [r[0] for r in rows]
        return jsonify({"success": True, "sections": sections})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        db._release(conn)


@app.route("/api/reports/<report_id>", methods=["DELETE"])
@login_required
def api_delete_report(report_id: str):
    """Delete a single report (ownership verified)."""
    storage = ReportStorage()
    report = storage.get_report(report_id, user_id=session["user_id"])
    if not report:
        return jsonify({"success": False, "error": "Not found"}), 404
    try:
        storage.delete_report(report_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/portfolio/<portfolio_id>/transactions")
@login_required
def api_portfolio_transactions(portfolio_id: str):
    """Return recent transactions for a portfolio.

    Query params:
      limit: max rows to return (default 20)
    """
    from database import get_database_manager

    portfolio = get_portfolio_service().get_portfolio(portfolio_id)
    if not portfolio or portfolio.get("user_id") != session["user_id"]:
        return jsonify({"success": False, "error": "Not found"}), 404

    try:
        limit = min(200, max(1, int(request.args.get("limit", 20))))
    except (TypeError, ValueError):
        limit = 20

    db = get_database_manager()
    rows = db.get_all_portfolio_transactions(portfolio_id)
    # Sort descending by date and slice
    rows_sorted = sorted(rows, key=lambda r: r.get("transaction_date") or "", reverse=True)[:limit]

    def _row(r):
        return {
            "transaction_id": r["transaction_id"],
            "symbol": r["symbol"],
            "asset_type": r["asset_type"],
            "transaction_type": r["transaction_type"],
            "quantity": float(r["quantity"]),
            "price_per_unit": float(r["price_per_unit"]),
            "fees": float(r["fees"]),
            "transaction_date": r["transaction_date"].isoformat() if r.get("transaction_date") else None,
            "notes": r.get("notes", ""),
        }

    return jsonify({"success": True, "transactions": [_row(r) for r in rows_sorted]})


@app.route("/api/telegram/status")
@login_required
def api_telegram_status():
    """Return Telegram connection status for the current user."""
    from database import get_database_manager

    db = get_database_manager()
    user = db.get_user_by_id(session["user_id"])
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404

    raw = user.get("telegram_chat_id")
    if raw:
        try:
            chat_id_str = str(raw)
            snippet = chat_id_str[:4] + "…" if len(chat_id_str) > 4 else chat_id_str
        except Exception:
            snippet = "****"
        connected = True
    else:
        snippet = None
        connected = False

    return jsonify({
        "success": True,
        "connected": connected,
        "chat_id_snippet": snippet,
        "bot_username": os.getenv("TELEGRAM_BOT_USERNAME", "StockProBot"),
    })


@app.route("/api/home")
@login_required
def api_home():
    """Aggregate home dashboard data in a single call."""
    from database import get_database_manager

    uid = session["user_id"]
    db = get_database_manager()
    svc = get_portfolio_service()
    wl_svc = get_watchlist_service()

    def _safe_float(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    # Portfolio aggregate totals
    try:
        portfolios = svc.list_portfolios(user_id=uid)
        total_value = 0.0
        total_pnl = 0.0
        day_change = 0.0
        holdings_preview = []
        for p in portfolios:
            try:
                summary = svc.get_portfolio_summary(p["portfolio_id"], with_prices=False)
                # Overlay cached prices for instant response
                symbols = [h["symbol"] for h in summary.get("holdings", []) if h.get("symbol")]
                cached = db.get_cached_prices(symbols) if symbols else {}
                all_holdings = summary.get("holdings", [])
                p_total_value = 0.0
                p_total_pnl = 0.0
                # Calculate totals from ALL holdings
                for h in all_holdings:
                    sym = h["symbol"]
                    cp = cached.get(sym)
                    current_price = _safe_float(cp.get("price")) if cp else None
                    qty = _safe_float(h.get("total_quantity")) or 0
                    avg_cost = _safe_float(h.get("average_cost")) or 0
                    # Bug 2: fall back to cost basis when no cached price to avoid
                    # silently dropping the holding from the total
                    if current_price is None:
                        current_price = avg_cost if avg_cost else None
                    if current_price is not None:
                        cur = detect_currency(sym)
                        mv = current_price * qty
                        ug = mv - (avg_cost * qty)
                        # Bug 1: convert ILS amounts to USD before accumulating
                        if cur != "USD":
                            mv = float(convert_to_usd(Decimal(str(mv)), cur))
                            ug = float(convert_to_usd(Decimal(str(ug)), cur))
                        p_total_value += mv
                        p_total_pnl += ug
                        # Day change from cached change_percent
                        chg_pct = _safe_float(cp.get("change_percent")) if cp else None
                        if chg_pct:
                            day_chg_native = qty * current_price * (chg_pct / 100)
                            if cur != "USD":
                                day_change += float(convert_to_usd(Decimal(str(day_chg_native)), cur))
                            else:
                                day_change += day_chg_native
                # Bug 3: include cash balance when cash tracking is enabled
                if summary.get("track_cash"):
                    cash = _safe_float(summary.get("cash_balance")) or 0.0
                    p_total_value += cash
                # Preview: only first 5 for display
                for h in all_holdings[:5]:
                    sym = h["symbol"]
                    cp = cached.get(sym)
                    current_price = _safe_float(cp.get("price")) if cp else None
                    qty = _safe_float(h.get("total_quantity")) or 0
                    avg_cost = _safe_float(h.get("average_cost")) or 0
                    if current_price is not None:
                        mv = current_price * qty
                        ug = mv - (avg_cost * qty)
                        ug_pct = (ug / (avg_cost * qty) * 100) if avg_cost * qty else 0
                    else:
                        mv = None
                        ug = None
                        ug_pct = None
                    holdings_preview.append({
                        "symbol": sym,
                        "portfolio_name": p["name"],
                        "quantity": qty,
                        "average_cost": avg_cost,
                        "market_value": mv,
                        "unrealized_gain": ug,
                        "unrealized_gain_pct": ug_pct,
                    })
                total_value += p_total_value
                total_pnl += p_total_pnl
            except Exception as _e:
                app.logger.debug("api_home: portfolio summary error: %s", _e)
        portfolio_totals = {
            "total_value": total_value,
            "total_pnl": total_pnl,
            "day_change": day_change,
            "day_change_pct": (day_change / total_value * 100) if total_value else 0,
        }
    except Exception:
        portfolio_totals = {}
        holdings_preview = []

    # Recent reports (last 5)
    try:
        storage = ReportStorage()
        reports, _ = storage.get_all_reports(user_id=uid, limit=5, offset=0)
        for r in reports:
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat()
    except Exception:
        reports = []

    # Active alerts count
    try:
        alert_rows = db.list_price_alerts_for_user(uid)
        active_alerts_count = sum(1 for r in alert_rows if r.get("active") and not r.get("last_triggered_at"))
    except Exception:
        active_alerts_count = 0

    # Pinned watchlist items with prices
    try:
        pinned = wl_svc.get_pinned_tickers(uid)
        watchlist_preview = []
        if pinned:
            from database import get_database_manager as _db
            cached = db.get_cached_prices([t["symbol"] for t in pinned if t.get("symbol")])
            for t in pinned:
                sym = t.get("symbol", "")
                c = (cached or {}).get(sym, {})
                watchlist_preview.append({
                    "symbol": sym,
                    "name": c.get("display_name", sym),
                    "price": float(c["price"]) if c.get("price") else None,
                    "change_pct": float(c["change_percent"]) if c.get("change_percent") else None,
                })
    except Exception:
        watchlist_preview = []

    # News (top 5) — force refresh on first load after login
    try:
        from news_service import get_briefing, force_refresh
        if request.args.get("refresh_news"):
            force_refresh()
        briefing = get_briefing()
        news_items = briefing[:5] if briefing else []
    except Exception:
        news_items = []

    return jsonify({
        "success": True,
        "portfolio_totals": portfolio_totals,
        "holdings_preview": holdings_preview[:10],
        "recent_reports": reports,
        "active_alerts_count": active_alerts_count,
        "watchlist_preview": watchlist_preview,
        "news": news_items,
    })


@app.route("/api/portfolio/<portfolio_id>")
@login_required
def api_portfolio_detail(portfolio_id: str):
    """Return portfolio + holdings JSON for React SPA."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        return jsonify({"error": "Not found"}), 404
    try:
        summary = portfolio_service.get_portfolio_summary(portfolio_id, with_prices=False)
        return jsonify({"portfolio": portfolio_data, "summary": summary, "holdings": summary["holdings"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/report/<report_id>")
@login_required
def api_report_detail(report_id: str):
    """Return single report JSON for React SPA."""
    try:
        storage = ReportStorage()
        report = storage.get_report(report_id, user_id=session.get("user_id"))
        if not report:
            return jsonify({"error": "Not found"}), 404
        if report.get("created_at") and hasattr(report["created_at"], "isoformat"):
            report["created_at"] = report["created_at"].isoformat()
        return jsonify({"report": report})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlists")
@login_required
def api_watchlists():
    """Return all watchlists + active watchlist JSON for React SPA."""
    try:
        watchlist_svc = get_watchlist_service()
        user_id = session["user_id"]
        watchlists = watchlist_svc.list_watchlists(user_id)
        if not watchlists:
            watchlist_svc.get_or_create_default_watchlist(user_id)
            watchlists = watchlist_svc.list_watchlists(user_id)
        active_watchlist_id = request.args.get("wl", watchlists[0]["watchlist_id"] if watchlists else None)
        active_watchlist = None
        if active_watchlist_id:
            wl = watchlist_svc.db.get_watchlist(active_watchlist_id)
            if wl and wl["user_id"] == user_id:
                active_watchlist = watchlist_svc.get_watchlist_with_items(active_watchlist_id)
                if active_watchlist:
                    # Flatten all items (sectioned + unsectioned) into a single list
                    all_items = list(active_watchlist.get("unsectioned_items", []))
                    for section in active_watchlist.get("sections", []):
                        all_items.extend(section.get("section_items", []))
                    # Normalise field names for React
                    for item in all_items:
                        item["change_pct"] = item.pop("change_percent", None)
                    active_watchlist["items"] = all_items
        return jsonify({"watchlists": watchlists, "active_watchlist": active_watchlist})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/<watchlist_id>/symbol", methods=["POST"])
@login_required
def api_watchlist_add_symbol(watchlist_id):
    """Add a symbol to a watchlist (JSON API for React SPA)."""
    watchlist_svc = get_watchlist_service()
    wl = watchlist_svc.db.get_watchlist(watchlist_id)
    if not wl or wl["user_id"] != session["user_id"]:
        return jsonify({"error": "Not found"}), 404
    data = request.get_json(silent=True) or {}
    symbol = (data.get("symbol") or "").strip().upper()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    import math as _math
    from database import get_database_manager
    from tiers import get_limit

    uid = session["user_id"]
    db = get_database_manager()
    limit = get_limit(uid, "watchlist_items")
    if limit != _math.inf and db.count_user_watchlist_items(uid) >= int(limit):
        return jsonify({
            "error": "quota_exceeded",
            "resource": "watchlist_items",
            "limit": int(limit),
            "message": f"Your plan allows {int(limit)} watchlist items. Upgrade to add more.",
        }), 402

    try:
        watchlist_svc.add_symbol(watchlist_id, symbol, None)
        return jsonify({"success": True, "symbol": symbol})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/watchlist/item/<item_id>", methods=["DELETE"])
@login_required
def api_watchlist_remove_item(item_id):
    """Remove an item from a watchlist (JSON API for React SPA)."""
    from database import get_database_manager
    db = get_database_manager()
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT wi.watchlist_id, wl.user_id FROM watchlist_items wi "
                "JOIN watchlists wl ON wi.watchlist_id = wl.watchlist_id WHERE wi.item_id = %s",
                (item_id,),
            )
            row = cur.fetchone()
    finally:
        db._release(conn)
    if not row or row["user_id"] != session["user_id"]:
        return jsonify({"error": "Not found"}), 404
    get_watchlist_service().remove_symbol(item_id)
    return jsonify({"success": True})


@app.route("/api/watchlist/item/<item_id>/pin", methods=["PATCH"])
@login_required
def api_watchlist_toggle_pin(item_id):
    """Toggle pin status of a watchlist item (JSON API for React SPA)."""
    from database import get_database_manager
    db = get_database_manager()
    conn = db.get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT wi.watchlist_id, wi.is_pinned, wl.user_id FROM watchlist_items wi "
                "JOIN watchlists wl ON wi.watchlist_id = wl.watchlist_id WHERE wi.item_id = %s",
                (item_id,),
            )
            row = cur.fetchone()
    finally:
        db._release(conn)
    if not row or row["user_id"] != session["user_id"]:
        return jsonify({"error": "Not found"}), 404
    watchlist_svc = get_watchlist_service()
    try:
        if row["is_pinned"]:
            watchlist_svc.unpin_item(item_id)
            is_pinned = False
        else:
            watchlist_svc.pin_item(session["user_id"], item_id)
            is_pinned = True
        return jsonify({"success": True, "is_pinned": is_pinned})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/portfolio/<portfolio_id>/holding/<symbol>")
@login_required
def api_holding_detail(portfolio_id: str, symbol: str):
    """Return holding + transactions JSON for React SPA."""
    portfolio_service = get_portfolio_service()
    portfolio_data = portfolio_service.get_portfolio(portfolio_id)
    if not portfolio_data or portfolio_data.get("user_id") != session["user_id"]:
        return jsonify({"error": "Not found"}), 404
    try:
        holding = portfolio_service.get_holding(portfolio_id, symbol)
        if not holding:
            return jsonify({"error": "Holding not found"}), 404
        transactions = portfolio_service.get_transactions(holding["holding_id"])
        provider, _ = DataProviderFactory.get_provider_for_symbol(symbol)
        current_price = provider.get_current_price(symbol) or Decimal("0")
        holding["current_price"] = float(current_price)
        holding["market_value"] = float(holding["total_quantity"] * current_price)
        holding["unrealized_gain"] = float(holding["market_value"] - float(holding["total_cost_basis"]))
        cost = float(holding["total_cost_basis"])
        holding["unrealized_gain_pct"] = float(holding["unrealized_gain"] / cost * 100) if cost > 0 else 0.0
        # Serialise Decimal fields
        for key in ("total_quantity", "average_cost", "total_cost_basis"):
            if key in holding and hasattr(holding[key], "__float__"):
                holding[key] = float(holding[key])
        for tx in transactions:
            for k in ("quantity", "price_per_unit", "total_value"):
                if k in tx and hasattr(tx[k], "__float__"):
                    tx[k] = float(tx[k])
            if tx.get("transaction_date") and hasattr(tx["transaction_date"], "isoformat"):
                tx["transaction_date"] = tx["transaction_date"].isoformat()
        return jsonify({"portfolio": portfolio_data, "holding": holding, "transactions": transactions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Device-code auth + CLI token management ---


@app.route("/api/device/authorize", methods=["POST"])
@limiter.limit("10/minute")
def device_authorize():
    """Start a device-code flow. No auth. Returns {device_code, user_code, expires_in, interval}."""
    from auth_tokens import create_device_code

    data = create_device_code()
    # Respect X-Forwarded-Proto so the printed URL uses https behind Railway's proxy.
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    data["verification_uri"] = f"{scheme}://{host}/app/device"
    return jsonify(data)


@app.route("/api/device/token", methods=["POST"])
@limiter.limit("30/minute")
def device_token():
    """Agent polls here with its device_code. Returns status; includes access_token once approved."""
    from auth_tokens import poll_device_code

    body = request.get_json(silent=True) or {}
    device_code = (body.get("device_code") or "").strip()
    if not device_code:
        return jsonify({"error": "device_code required"}), 400
    result = poll_device_code(device_code)
    if result.get("status") == "unknown":
        return jsonify({"status": "pending"}), 200  # don't leak existence
    return jsonify(result)


@app.route("/api/device/approve", methods=["POST"])
@login_required
def device_approve():
    """Master approves a user_code for their account. Called from /app/device."""
    from auth_tokens import approve_device_code

    body = request.get_json(silent=True) or {}
    user_code = body.get("user_code") or ""
    result = approve_device_code(session["user_id"], user_code)
    if "error" in result:
        code = 404 if result["error"] in ("unknown_code", "invalid_code") else 410
        return jsonify(result), code
    return jsonify(result)


@app.route("/api/tokens", methods=["GET"])
@login_required
def tokens_list():
    from auth_tokens import list_api_keys

    return jsonify({"tokens": list_api_keys(session["user_id"])})


@app.route("/api/tokens", methods=["POST"])
@login_required
def tokens_create():
    """Create a token. Returns raw token ONCE."""
    from auth_tokens import create_api_key

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "CLI token").strip()[:100] or "CLI token"
    key_id, raw_token = create_api_key(session["user_id"], name)
    return jsonify(
        {
            "id": key_id,
            "name": name,
            "access_token": raw_token,
        }
    )


@app.route("/api/tokens/<key_id>", methods=["DELETE"])
@login_required
def tokens_revoke(key_id):
    from auth_tokens import revoke_api_key

    ok = revoke_api_key(session["user_id"], key_id)
    if not ok:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"status": "revoked"})


# --- SPA serving (production) ---
# In production, serve the React SPA from the built dist/ folder.
# All non-API, non-static paths fall through to index.html for client-side routing.
_spa_dist = project_root / "stockpro-web" / "dist"


@app.route("/app/", defaults={"path": ""})
@app.route("/app/<path:path>")
def serve_spa(path):
    """Serve the React SPA static files.

    Prerendered routes live at dist/<route>/index.html (vite-prerender-plugin
    output) so non-JS crawlers see real content per page. We check for that
    first, then fall back to the root index.html for client-side routing.
    """
    if path and (_spa_dist / path).is_file():
        return send_from_directory(str(_spa_dist), path)
    if path:
        prerendered = _spa_dist / path / "index.html"
        if prerendered.is_file():
            return send_from_directory(str(_spa_dist / path), "index.html")
    index = _spa_dist / "index.html"
    if index.is_file():
        return send_from_directory(str(_spa_dist), "index.html")
    return "SPA not built. Run: cd stockpro-web && npm run build", 404


# ── Admin API ──────────────────────────────────────────────────────

@app.route("/api/admin/dashboard")
@admin_required
def api_admin_dashboard():
    """KPI cards + recent lists for the admin dashboard."""
    from database import get_database_manager
    db = get_database_manager()
    data = db.admin_get_dashboard()
    # Serialize datetimes
    for item in data.get("recent_signups", []):
        if item.get("created_at") and hasattr(item["created_at"], "isoformat"):
            item["created_at"] = item["created_at"].isoformat()
    for item in data.get("recent_reports", []):
        if item.get("created_at") and hasattr(item["created_at"], "isoformat"):
            item["created_at"] = item["created_at"].isoformat()
    return jsonify(data)


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    """Paginated user list with search and sort."""
    from database import get_database_manager
    db = get_database_manager()
    search = request.args.get("search", "")
    page = int(request.args.get("page", 1))
    sort = request.args.get("sort", "created_at")
    order = request.args.get("order", "desc")
    data = db.admin_get_users(search=search, page=page, sort=sort, order=order)
    # Serialize datetimes
    for u in data.get("users", []):
        for dt_field in ("created_at", "last_active_at"):
            if u.get(dt_field) and hasattr(u[dt_field], "isoformat"):
                u[dt_field] = u[dt_field].isoformat()
    return jsonify(data)


@app.route("/api/admin/users/<user_id>", methods=["PATCH"])
@admin_required
def api_admin_update_user(user_id):
    """Update user fields (disable/enable, change tier)."""
    from database import get_database_manager
    db = get_database_manager()
    body = request.get_json(silent=True) or {}
    updates = {}
    if "disabled" in body:
        updates["disabled"] = bool(body["disabled"])
    if "tier" in body:
        updates["tier"] = str(body["tier"])
    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400
    updated = db.admin_update_user(user_id, updates)
    if not updated:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/admin/users/<user_id>/impersonate", methods=["POST"])
@admin_required
def api_admin_impersonate(user_id):
    """Create a Clerk impersonation session for the target user."""
    try:
        # Clerk Backend API: create an actor token for impersonation
        result = clerk_client.actor_tokens.create(
            request_body={"user_id": user_id, "actor": {"sub": request.admin_user_id}}
        )
        return jsonify({"token": result.token, "url": result.url})
    except Exception as e:
        app.logger.error("Impersonation failed for %s: %s", user_id, e)
        return jsonify({"error": "Impersonation failed"}), 500


# ── Admin Phase 2: Stats, Logs, Config ──────────────────────────────

@app.route("/api/admin/stats")
@admin_required
def api_admin_stats():
    """Aggregated stats for the admin Stats tab."""
    from database import get_database_manager
    db = get_database_manager()
    data = db.admin_get_stats()
    # Serialize date objects
    for item in data.get("reports_per_day", []):
        if item.get("day") and hasattr(item["day"], "isoformat"):
            item["day"] = item["day"].isoformat()
    for item in data.get("signups_per_day", []):
        if item.get("day") and hasattr(item["day"], "isoformat"):
            item["day"] = item["day"].isoformat()
    return jsonify(data)


@app.route("/api/admin/events")
@admin_required
def api_admin_events():
    """Paginated event log with optional filters."""
    from database import get_database_manager
    db = get_database_manager()
    event_type = request.args.get("event_type", "")
    user_id = request.args.get("user_id", "")
    page = int(request.args.get("page", 1))
    data = db.admin_get_events(event_type=event_type, user_id=user_id, page=page)
    for ev in data.get("events", []):
        if ev.get("created_at") and hasattr(ev["created_at"], "isoformat"):
            ev["created_at"] = ev["created_at"].isoformat()
    return jsonify(data)


@app.route("/api/admin/events/types")
@admin_required
def api_admin_event_types():
    """Distinct event types for filter dropdown."""
    from database import get_database_manager
    db = get_database_manager()
    return jsonify({"types": db.admin_get_event_types()})


@app.route("/api/admin/config")
@admin_required
def api_admin_config_get():
    """Return all config key-value pairs."""
    from database import get_database_manager
    db = get_database_manager()
    data = db.config_get_all()
    for entry in data.values():
        if entry.get("updated_at") and hasattr(entry["updated_at"], "isoformat"):
            entry["updated_at"] = entry["updated_at"].isoformat()
    return jsonify(data)


@app.route("/api/admin/config", methods=["PUT"])
@admin_required
def api_admin_config_set():
    """Upsert a config key-value pair."""
    from database import get_database_manager
    db = get_database_manager()
    body = request.get_json(silent=True) or {}
    key = body.get("key")
    value = body.get("value")
    if not key:
        return jsonify({"error": "Missing key"}), 400
    db.config_set(key, value)
    db.admin_log_event("config_changed", getattr(request, "admin_user_id", None), {
        "key": key, "value": value,
    })
    return jsonify({"ok": True})


@app.route("/api/admin/config/<key>", methods=["DELETE"])
@admin_required
def api_admin_config_delete(key):
    """Delete a config key."""
    from database import get_database_manager
    db = get_database_manager()
    deleted = db.config_delete(key)
    if not deleted:
        return jsonify({"error": "Key not found"}), 404
    db.admin_log_event("config_deleted", getattr(request, "admin_user_id", None), {"key": key})
    return jsonify({"ok": True})


def main():
    """Main entry point for the Flask app."""
    # Check for required environment variables
    if not os.getenv("GEMINI_API_KEY"):
        app.logger.warning(
            "GEMINI_API_KEY not set — research and embeddings will fail until it is configured."
        )

    from watchlist.price_refresh import start_price_refresh
    from watchlist.public_view_refresh import start_public_view_refresh

    start_price_refresh()
    start_public_view_refresh()

    app.run(
        host=os.getenv("FLASK_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 5000)),
        debug=not _is_production,
    )


if __name__ == "__main__":
    main()
