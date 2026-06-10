"""Research AJAX: /popup_start and /start_generation (agent / thread mocked)."""

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
    flask_app.config["SECRET_KEY"] = "test-research-ajax"
    return flask_app


@pytest.fixture
def api_client(api_app):
    return api_app.test_client()


def test_popup_start_400_without_ticker_or_trade_type(api_client):
    with api_client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["username"] = "u"
    resp = api_client.post("/popup_start", data={"ticker": "", "trade_type": ""})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_popup_start_returns_questions_json(api_client):
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.pending_questions = [
        {"question": "Goal?", "options": ["Long", "Short"]},
    ]

    with patch.object(app_module, "initialize_session", return_value=mock_agent):
        with api_client.session_transaction() as sess:
            sess["user_id"] = "u1"
            sess["username"] = "u"
        resp = api_client.post(
            "/popup_start",
            data={"ticker": "AAPL", "trade_type": "Investment"},
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["questions"][0]["question"] == "Goal?"
    assert "session_id" in data
    assert "subjects" in data
    mock_agent.reset_conversation.assert_called_once()
    mock_agent.start_research.assert_called_once_with("AAPL", "Investment")


def test_popup_start_stores_position_summary_and_goal_in_session(api_client):
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.pending_questions = []

    with patch.object(app_module, "initialize_session", return_value=mock_agent):
        with api_client.session_transaction() as sess:
            sess["user_id"] = "u1"
            sess["username"] = "u"
        resp = api_client.post(
            "/popup_start",
            data={
                "ticker": "NVDA",
                "trade_type": "Investment",
                "position_summary": 'Portfolio "Main": 50 shares at $400.00',
                "position_goal": "DCA into my position",
            },
        )

    assert resp.status_code == 200
    with api_client.session_transaction() as sess:
        assert sess.get("position_summary") == 'Portfolio "Main": 50 shares at $400.00'
        assert sess.get("position_goal") == "DCA into my position"


def test_popup_start_clears_position_keys_when_not_provided(api_client):
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.pending_questions = []

    # Pre-set stale position keys in the session
    with api_client.session_transaction() as sess:
        sess["user_id"] = "u1"
        sess["username"] = "u"
        sess["position_summary"] = "old summary"
        sess["position_goal"] = "old goal"

    with patch.object(app_module, "initialize_session", return_value=mock_agent):
        api_client.post(
            "/popup_start",
            data={"ticker": "AAPL", "trade_type": "Investment"},
        )

    with api_client.session_transaction() as sess:
        assert "position_summary" not in sess
        assert "position_goal" not in sess


def test_start_generation_includes_position_block_in_context(api_client):
    import app as app_module

    mock_agent = MagicMock()
    mock_thread = MagicMock()
    captured_context = {}

    def fake_generate_report(**kwargs):
        captured_context["context"] = kwargs.get("context", "")

    mock_agent.generate_report.side_effect = fake_generate_report
    mock_agent.current_report_id = "r1"

    def fake_thread(target, daemon):
        target()  # run synchronously to capture context
        return mock_thread

    with patch.object(app_module, "_session_hits_report_quota", return_value=False):
        with patch.object(app_module, "initialize_session", return_value=mock_agent):
            with patch.object(app_module, "get_or_create_session_id", return_value="sid-pos"):
                with patch("spend_budget.get_spend_budget_usd", return_value=2.5):
                    with patch.object(app_module, "create_emitter", return_value=MagicMock()):
                        with patch.object(app_module.threading, "Thread", side_effect=fake_thread):
                            with api_client.session_transaction() as sess:
                                sess["user_id"] = "u1"
                                sess["username"] = "nu"
                                sess["position_summary"] = 'Portfolio "P1": 10 shares at $500.00'
                                sess["position_goal"] = "DCA into my position"
                            api_client.post(
                                "/start_generation",
                                json={"questions": ["Goal?"], "answers": ["Hold"]},
                                content_type="application/json",
                            )

    assert "User's existing position" in captured_context.get("context", "")
    assert "DCA into my position" in captured_context.get("context", "")


def test_start_generation_returns_success_and_schedules_thread(api_client):
    import app as app_module

    mock_agent = MagicMock()
    mock_thread = MagicMock()

    with patch.object(app_module, "_session_hits_report_quota", return_value=False):
        with patch.object(app_module, "initialize_session", return_value=mock_agent):
            with patch.object(app_module, "get_or_create_session_id", return_value="sid-gen"):
                with patch("spend_budget.get_spend_budget_usd", return_value=2.5):
                    with patch.object(app_module.threading, "Thread", return_value=mock_thread):
                        with api_client.session_transaction() as sess:
                            sess["user_id"] = "u1"
                            sess["username"] = "nu"
                        resp = api_client.post(
                            "/start_generation",
                            json={"questions": ["Q?"], "answers": ["A"]},
                            content_type="application/json",
                        )

    assert resp.status_code == 200
    assert resp.get_json() == {"success": True}
    assert mock_agent.user_id == "u1"
    assert mock_agent.username == "nu"
    mock_thread.start.assert_called_once()


def test_start_generation_reprimes_ticker_from_session_when_agent_fresh(api_client):
    """Cross-worker case (#116): the agent has no in-memory ticker, but the
    Flask cookie carries it from popup_start. start_generation must restore it
    and proceed instead of silently no-opping."""
    import app as app_module

    # Fresh agent on a different worker — no primed ticker/trade_type.
    mock_agent = MagicMock()
    mock_agent.current_ticker = None
    mock_agent.current_trade_type = None
    mock_thread = MagicMock()

    with patch.object(app_module, "_session_hits_report_quota", return_value=False):
        with patch.object(app_module, "initialize_session", return_value=mock_agent):
            with patch.object(app_module, "get_or_create_session_id", return_value="sid-reprime"):
                with patch("spend_budget.get_spend_budget_usd", return_value=2.5):
                    with patch.object(app_module.threading, "Thread", return_value=mock_thread):
                        with api_client.session_transaction() as sess:
                            sess["user_id"] = "u1"
                            sess["username"] = "nu"
                            sess["current_ticker"] = "TEVA.TA"
                            sess["current_trade_type"] = "Investment"
                        resp = api_client.post(
                            "/start_generation",
                            json={"questions": ["Q?"], "answers": ["A"]},
                            content_type="application/json",
                        )

    assert resp.status_code == 200
    assert resp.get_json() == {"success": True}
    # Agent was re-primed from the cookie, so generation can run.
    assert mock_agent.current_ticker == "TEVA.TA"
    assert mock_agent.current_trade_type == "Investment"
    mock_thread.start.assert_called_once()


def test_start_generation_400_when_no_active_session_anywhere(api_client):
    """If neither the in-memory agent nor the cookie has a ticker, fail loudly
    (400) rather than marking the job 'ready' with no report (#116)."""
    import app as app_module

    mock_agent = MagicMock()
    mock_agent.current_ticker = None
    mock_agent.current_trade_type = None
    mock_thread = MagicMock()

    with patch.object(app_module, "_session_hits_report_quota", return_value=False):
        with patch.object(app_module, "initialize_session", return_value=mock_agent):
            with patch.object(app_module, "get_or_create_session_id", return_value="sid-none"):
                with patch.object(app_module.threading, "Thread", return_value=mock_thread):
                    with api_client.session_transaction() as sess:
                        sess["user_id"] = "u1"
                        sess["username"] = "nu"
                    resp = api_client.post(
                        "/start_generation",
                        json={"questions": ["Q?"], "answers": ["A"]},
                        content_type="application/json",
                    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()
    mock_thread.start.assert_not_called()


class TestPositionCheckApi:
    def test_returns_positions_for_authenticated_user(self, api_client):
        import app as app_module
        from decimal import Decimal

        mock_svc = MagicMock()
        mock_svc.get_holdings_for_ticker.return_value = [
            {
                "portfolio_name": "Main",
                "portfolio_id": "p1",
                "total_quantity": Decimal("50"),
                "average_cost": Decimal("400.00"),
                "total_cost_basis": Decimal("20000.00"),
            }
        ]

        with patch.object(app_module, "get_portfolio_service", return_value=mock_svc):
            with api_client.session_transaction() as sess:
                sess["user_id"] = "u1"
                sess["username"] = "u"
            resp = api_client.get("/api/position_check/NVDA")

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["positions"]) == 1
        pos = data["positions"][0]
        assert pos["portfolio_name"] == "Main"
        assert pos["quantity"] == 50.0
        assert pos["average_cost"] == 400.0
        mock_svc.get_holdings_for_ticker.assert_called_once_with(user_id="u1", symbol="NVDA")

    def test_returns_empty_when_no_holdings(self, api_client):
        import app as app_module

        mock_svc = MagicMock()
        mock_svc.get_holdings_for_ticker.return_value = []

        with patch.object(app_module, "get_portfolio_service", return_value=mock_svc):
            with api_client.session_transaction() as sess:
                sess["user_id"] = "u1"
                sess["username"] = "u"
            resp = api_client.get("/api/position_check/AAPL")

        assert resp.status_code == 200
        assert resp.get_json() == {"positions": []}

    def test_redirects_unauthenticated_user(self, api_client):
        resp = api_client.get("/api/position_check/AAPL")
        # login_required redirects or returns 302/401
        assert resp.status_code in (302, 401)
