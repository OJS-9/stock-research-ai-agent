"""CLI/headless report flow: /api/reports/generate and /api/reports/answer.

Regression tests for issue #165: generate and answer can land on different
gunicorn workers, so answer must re-prime a fresh agent from the
generation_status DB row instead of running a blank agent (which surfaced
as a fake "upstream data sources are down" error).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent / "src"))


@pytest.fixture
def api_app():
    from app import app as flask_app

    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    flask_app.config["SECRET_KEY"] = "test-reports-cli-flow"
    return flask_app


@pytest.fixture
def api_client(api_app):
    return api_app.test_client()


def _needs_input_row(**overrides):
    row = {
        "session_id": "sid-cli",
        "user_id": "u1",
        "status": "needs_input",
        "questions": [{"question": "Goal?", "options": ["Long", "Short"]}],
        "subjects": [],
        "ticker": "AAPL",
        "trade_type": "Investment",
    }
    row.update(overrides)
    return row


def test_generate_persists_ticker_and_trade_type(api_client):
    """generate must store ticker/trade_type in the DB row so a different
    worker can re-prime the agent at answer time (#165)."""
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.pending_questions = [{"question": "Goal?", "options": ["Long"]}]

    with patch.object(app_module, "_session_hits_report_quota", return_value=False), \
         patch.object(app_module, "initialize_session", return_value=mock_agent), \
         patch.object(app_module.db, "set_generation_status") as set_status, \
         patch.object(app_module.db, "get_user_by_id", return_value=None), \
         patch("spend_budget.get_spend_budget_usd", return_value=2.5):
        with api_client.session_transaction() as sess:
            sess["user_id"] = "u1"
            sess["username"] = "u"
        resp = api_client.post(
            "/api/reports/generate",
            json={"ticker": "AAPL", "trade_type": "Investment"},
        )

    assert resp.status_code == 200
    kwargs = set_status.call_args.kwargs
    assert kwargs["status"] == "needs_input"
    assert kwargs["ticker"] == "AAPL"
    assert kwargs["trade_type"] == "Investment"


def test_answer_reprimes_fresh_agent_from_db_row(api_client):
    """Cross-worker case (#165): answer lands on a worker whose in-memory agent
    is blank. It must restore ticker/trade_type from the DB row, set the user
    identity, and start generation instead of failing."""
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.current_ticker = None
    mock_agent.current_trade_type = None
    mock_agent.user_id = None
    mock_agent.username = None
    mock_thread = MagicMock()

    with patch.object(app_module, "initialize_session", return_value=mock_agent), \
         patch.object(app_module.db, "get_generation_status", return_value=_needs_input_row()), \
         patch.object(app_module.db, "set_generation_status"), \
         patch.object(app_module.db, "get_user_by_id", return_value={"username": "nu", "preferences": {}}), \
         patch("spend_budget.get_spend_budget_usd", return_value=2.5), \
         patch.object(app_module.threading, "Thread", return_value=mock_thread):
        with api_client.session_transaction() as sess:
            sess["user_id"] = "u1"
        resp = api_client.post(
            "/api/reports/answer",
            json={"session_id": "sid-cli", "answers": ["Long-term"]},
        )

    assert resp.status_code == 200
    assert resp.get_json()["success"] is True
    assert mock_agent.current_ticker == "AAPL"
    assert mock_agent.current_trade_type == "Investment"
    assert mock_agent.user_id == "u1"
    assert mock_agent.username == "nu"
    mock_thread.start.assert_called_once()


def test_answer_keeps_primed_agent_when_same_worker(api_client):
    """Same-worker case: the agent already has its ticker -- do not overwrite."""
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.current_ticker = "AAPL"
    mock_agent.current_trade_type = "Investment"
    mock_agent.user_id = "u1"
    mock_agent.username = "nu"
    mock_thread = MagicMock()

    row = _needs_input_row(ticker="MSFT", trade_type="Swing")

    with patch.object(app_module, "initialize_session", return_value=mock_agent), \
         patch.object(app_module.db, "get_generation_status", return_value=row), \
         patch.object(app_module.db, "set_generation_status"), \
         patch.object(app_module.db, "get_user_by_id", return_value={"username": "nu", "preferences": {}}), \
         patch("spend_budget.get_spend_budget_usd", return_value=2.5), \
         patch.object(app_module.threading, "Thread", return_value=mock_thread):
        with api_client.session_transaction() as sess:
            sess["user_id"] = "u1"
        resp = api_client.post(
            "/api/reports/answer",
            json={"session_id": "sid-cli", "answers": ["Long-term"]},
        )

    assert resp.status_code == 200
    assert mock_agent.current_ticker == "AAPL"
    mock_thread.start.assert_called_once()


def test_answer_400_when_ticker_unrecoverable(api_client):
    """Legacy row without ticker + fresh agent: fail loudly with 400 instead of
    running a blank agent that reports a fake upstream-sources error (#165)."""
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.current_ticker = None
    mock_agent.current_trade_type = None
    mock_thread = MagicMock()

    row = _needs_input_row(ticker=None, trade_type=None)

    with patch.object(app_module, "initialize_session", return_value=mock_agent), \
         patch.object(app_module.db, "get_generation_status", return_value=row), \
         patch.object(app_module.db, "set_generation_status"), \
         patch.object(app_module.threading, "Thread", return_value=mock_thread):
        with api_client.session_transaction() as sess:
            sess["user_id"] = "u1"
        resp = api_client.post(
            "/api/reports/answer",
            json={"session_id": "sid-cli", "answers": ["Long-term"]},
        )

    assert resp.status_code == 400
    assert "error" in resp.get_json()
    mock_thread.start.assert_not_called()


def test_report_status_hides_ticker_fields(api_client):
    """The status API response must not change shape: ticker/trade_type are
    internal re-priming state, not part of the public payload."""
    import app as app_module

    row = _needs_input_row(status="in_progress", progress=5, step="Starting...")

    with patch.object(app_module.db, "get_generation_status", return_value=row):
        with api_client.session_transaction() as sess:
            sess["user_id"] = "u1"
        resp = api_client.get("/api/report_status/sid-cli")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "ticker" not in data
    assert "trade_type" not in data
    assert data["status"] == "in_progress"
