"""Waitlist / ConvertKit integration."""

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
    flask_app.config["SECRET_KEY"] = "test-waitlist"
    return flask_app


@pytest.fixture
def api_client(api_app):
    return api_app.test_client()


class TestWaitlistPages:
    def test_waitlist_renders(self, api_client):
        resp = api_client.get("/waitlist")
        assert resp.status_code == 200
        assert b"Join Waitlist" in resp.data

    def test_waitlist_thanks_renders(self, api_client):
        resp = api_client.get("/waitlist/thanks")
        assert resp.status_code == 200
        assert b"on the list" in resp.data


class TestWaitlistJoin:
    def test_invalid_email_redirects_with_flash(self, api_client):
        resp = api_client.post(
            "/waitlist/join",
            data={"email": "not-an-email"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"valid email" in resp.data.lower()

    def test_convertkit_called_when_configured(self, api_client, monkeypatch):
        monkeypatch.setenv("CONVERTKIT_API_KEY", "ck-test-key")
        monkeypatch.setenv("CONVERTKIT_FORM_ID", "999888")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"subscription":{}}'

        with patch("app.requests.post", return_value=mock_resp) as mock_post:
            resp = api_client.post(
                "/waitlist/join",
                data={"email": "user@example.com"},
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert "/waitlist/thanks" in (resp.headers.get("Location") or "")

        mock_post.assert_called_once()
        call_kw = mock_post.call_args[1]
        assert call_kw.get("json") == {
            "api_key": "ck-test-key",
            "email": "user@example.com",
        }
        assert mock_post.call_args[0][0].endswith("/forms/999888/subscribe")

    def test_no_convertkit_env_skips_http(self, api_client, monkeypatch):
        monkeypatch.delenv("CONVERTKIT_API_KEY", raising=False)
        monkeypatch.delenv("CONVERTKIT_FORM_ID", raising=False)

        with patch("app.requests.post") as mock_post:
            resp = api_client.post(
                "/waitlist/join",
                data={"email": "solo@example.com"},
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert "/waitlist/thanks" in (resp.headers.get("Location") or "")
        mock_post.assert_not_called()

    def test_api_waitlist_valid_email(self, api_client, monkeypatch):
        monkeypatch.delenv("CONVERTKIT_API_KEY", raising=False)
        monkeypatch.delenv("CONVERTKIT_FORM_ID", raising=False)
        resp = api_client.post(
            "/api/waitlist",
            json={"email": "spa@example.com"},
        )
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}

    def test_api_waitlist_invalid_email(self, api_client):
        resp = api_client.post(
            "/api/waitlist",
            json={"email": "not-an-email"},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["ok"] is False
        assert "valid email" in body["error"].lower()

    def test_api_waitlist_missing_email(self, api_client):
        resp = api_client.post("/api/waitlist", json={})
        assert resp.status_code == 400
        assert resp.get_json()["ok"] is False

    def test_api_waitlist_calls_convertkit(self, api_client, monkeypatch):
        monkeypatch.setenv("CONVERTKIT_API_KEY", "ck-test-key")
        monkeypatch.setenv("CONVERTKIT_FORM_ID", "9264674")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"subscription":{}}'
        with patch("app.requests.post", return_value=mock_resp) as mock_post:
            resp = api_client.post(
                "/api/waitlist",
                json={"email": "ck@example.com"},
            )
        assert resp.status_code == 200
        assert resp.get_json() == {"ok": True}
        mock_post.assert_called_once()

    def test_convertkit_error_still_shows_success(self, api_client, monkeypatch):
        monkeypatch.setenv("CONVERTKIT_API_KEY", "ck-test-key")
        monkeypatch.setenv("CONVERTKIT_FORM_ID", "111")

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "upstream error"

        with patch("app.requests.post", return_value=mock_resp):
            resp = api_client.post(
                "/waitlist/join",
                data={"email": "fail@example.com"},
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert "/waitlist/thanks" in (resp.headers.get("Location") or "")
