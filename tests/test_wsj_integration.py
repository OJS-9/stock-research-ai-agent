"""
Tests for WSJ Nimble agent integration in news_service.py.
"""

import sys
import os
import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent / 'src'))

import news_service


class TestWsjConstants:
    """WSJ agent constants must be present in news_service."""

    def test_wsj_agent_constant_exists(self):
        assert hasattr(news_service, 'WSJ_AGENT')
        assert news_service.WSJ_AGENT == "wsj_article_template_2026_03_02_z7hhhvxe"

    def test_wsj_pipeline_constant_exists(self):
        assert hasattr(news_service, 'WSJ_PIPELINE')
        assert news_service.WSJ_PIPELINE == "WSJcomUSBusiness"


class TestMapArticleWsj:
    """_map_article should produce a WSJ fallback URL when article_url is missing."""

    def test_wsj_fallback_url_uses_wsj_search(self):
        item = {"headline": "Fed raises rates", "summary": "", "image_url": ""}
        result = news_service._map_article(item, "WSJ")
        assert result["url"].startswith("https://www.wsj.com/search")
        assert "Fed+raises+rates" in result["url"] or "Fed%20raises%20rates" in result["url"] or "Fed" in result["url"]

    def test_wsj_article_url_used_when_present(self):
        item = {"headline": "Fed raises rates", "article_url": "https://wsj.com/articles/123"}
        result = news_service._map_article(item, "WSJ")
        assert result["url"] == "https://wsj.com/articles/123"

    def test_wsj_publisher_label(self):
        item = {"headline": "Test", "article_url": "https://wsj.com/articles/1"}
        result = news_service._map_article(item, "WSJ")
        assert result["publisher"] == "WSJ"


class TestRefreshPrimaryWsj:
    """_refresh_primary must call WSJ agent and include WSJ articles in cache."""

    def _make_article(self, title, publisher_hint=""):
        return {
            "headline": title,
            "summary": "Summary",
            "image_url": "",
            "article_url": f"https://example.com/{title.replace(' ', '-').lower()}",
        }

    def _patch_nimble(self, mock_client):
        """Patch NimbleClient at the nimble_client module level (where it's imported from)."""
        import nimble_client as nimble_module
        return patch.object(nimble_module, 'NimbleClient', return_value=mock_client)

    def test_wsj_agent_called_in_refresh_primary(self):
        bloomberg_items = [self._make_article("Bloomberg 1"), self._make_article("Bloomberg 2")]
        morningstar_items = [self._make_article("Morningstar 1")]
        wsj_items = [self._make_article("WSJ Article 1"), self._make_article("WSJ Article 2")]

        mock_client = MagicMock()
        mock_client.run_agent.side_effect = lambda agent, params: {
            news_service.BLOOMBERG_AGENT: bloomberg_items,
            news_service.MORNINGSTAR_AGENT: morningstar_items,
            news_service.WSJ_AGENT: wsj_items,
        }.get(agent, [])

        # _refresh_primary does `from nimble_client import NimbleClient` inside the function
        # so we patch the class on the nimble_client module directly
        import nimble_client as nimble_module
        with patch.object(nimble_module, 'NimbleClient', return_value=mock_client):
            news_service._cache["primary"]["fetched_at"] = None
            news_service._refresh_primary()

        # WSJ agent was called with correct params
        mock_client.run_agent.assert_any_call(
            news_service.WSJ_AGENT, {"feed_name": news_service.WSJ_PIPELINE}
        )

    def test_bloomberg_agent_called_with_search_url(self):
        """Regenerated Bloomberg agent (issue #146) takes a full search URL, not a query keyword."""
        mock_client = MagicMock()
        mock_client.run_agent.side_effect = lambda agent, params: []

        import nimble_client as nimble_module
        with patch.object(nimble_module, 'NimbleClient', return_value=mock_client):
            news_service._cache["primary"]["fetched_at"] = None
            news_service._refresh_primary()

        mock_client.run_agent.assert_any_call(
            news_service.BLOOMBERG_AGENT, {"url": news_service.BLOOMBERG_SEARCH_URL}
        )
        assert news_service.BLOOMBERG_SEARCH_URL.startswith("https://www.bloomberg.com/search?query=")

    def test_wsj_articles_appear_in_cache_after_refresh(self):
        bloomberg_items = [self._make_article("Bloomberg 1")]
        morningstar_items = [self._make_article("Morningstar 1")]
        wsj_items = [self._make_article("WSJ Story 1")]

        mock_client = MagicMock()
        mock_client.run_agent.side_effect = lambda agent, params: {
            news_service.BLOOMBERG_AGENT: bloomberg_items,
            news_service.MORNINGSTAR_AGENT: morningstar_items,
            news_service.WSJ_AGENT: wsj_items,
        }.get(agent, [])

        import nimble_client as nimble_module
        with patch.object(nimble_module, 'NimbleClient', return_value=mock_client):
            news_service._cache["primary"]["fetched_at"] = None
            news_service._refresh_primary()

        publishers = [a["publisher"] for a in news_service._cache["primary"]["articles"]]
        assert "WSJ" in publishers

    def test_round_robin_order_bloomberg_morningstar_wsj(self):
        """First three articles should come from Bloomberg, Morningstar, WSJ respectively."""
        bloomberg_items = [self._make_article("B1"), self._make_article("B2")]
        morningstar_items = [self._make_article("M1"), self._make_article("M2")]
        wsj_items = [self._make_article("W1"), self._make_article("W2")]

        mock_client = MagicMock()
        mock_client.run_agent.side_effect = lambda agent, params: {
            news_service.BLOOMBERG_AGENT: bloomberg_items,
            news_service.MORNINGSTAR_AGENT: morningstar_items,
            news_service.WSJ_AGENT: wsj_items,
        }.get(agent, [])

        import nimble_client as nimble_module
        with patch.object(nimble_module, 'NimbleClient', return_value=mock_client):
            news_service._cache["primary"]["fetched_at"] = None
            news_service._refresh_primary()

        articles = news_service._cache["primary"]["articles"]
        # All articles with images are sorted first; since none have images here,
        # round-robin order is preserved.
        publishers = [a["publisher"] for a in articles]
        # The cycle should produce B, M, W, B, M, W
        assert publishers[0] == "Bloomberg"
        assert publishers[1] == "Morningstar"
        assert publishers[2] == "WSJ"


class TestEmptyResultHandling:
    """Empty/placeholder agent results must be dropped and logged, not silently served."""

    def _make_article(self, title):
        return {
            "headline": title,
            "summary": "Summary",
            "image_url": "",
            "article_url": f"https://example.com/{title.replace(' ', '-').lower()}",
        }

    def test_empty_dict_record_does_not_become_ghost_article(self):
        """A source returning [{}] (Nimble's empty-result shape) yields no blank card."""
        mock_client = MagicMock()
        mock_client.run_agent.side_effect = lambda agent, params: {
            news_service.BLOOMBERG_AGENT: [{}],  # empty placeholder record
            news_service.MORNINGSTAR_AGENT: [self._make_article("Morningstar 1")],
            news_service.WSJ_AGENT: [self._make_article("WSJ 1")],
        }.get(agent, [])

        import nimble_client as nimble_module
        with patch.object(nimble_module, 'NimbleClient', return_value=mock_client):
            news_service._cache["primary"]["fetched_at"] = None
            news_service._refresh_primary()

        articles = news_service._cache["primary"]["articles"]
        assert all(a["title"] for a in articles)
        assert "Bloomberg" not in [a["publisher"] for a in articles]

    def test_empty_source_logs_warning(self, caplog):
        mock_client = MagicMock()
        mock_client.run_agent.side_effect = lambda agent, params: {
            news_service.BLOOMBERG_AGENT: [{}],
            news_service.MORNINGSTAR_AGENT: [self._make_article("Morningstar 1")],
            news_service.WSJ_AGENT: [self._make_article("WSJ 1")],
        }.get(agent, [])

        import nimble_client as nimble_module
        with patch.object(nimble_module, 'NimbleClient', return_value=mock_client):
            news_service._cache["primary"]["fetched_at"] = None
            with caplog.at_level(logging.WARNING):
                news_service._refresh_primary()

        assert "Bloomberg" in caplog.text
        assert "no usable articles" in caplog.text

    def test_all_sources_empty_logs_aggregate_error(self, caplog):
        mock_client = MagicMock()
        mock_client.run_agent.side_effect = lambda agent, params: []

        import nimble_client as nimble_module
        with patch.object(nimble_module, 'NimbleClient', return_value=mock_client):
            news_service._cache["primary"]["fetched_at"] = None
            with caplog.at_level(logging.ERROR):
                news_service._refresh_primary()

        assert news_service._cache["primary"]["articles"] == []
        assert any(
            r.levelno == logging.ERROR and "all news sources returned no usable articles" in r.message
            for r in caplog.records
        )
