"""
Smoke tests for Phase 1 (P0 #1), Phase 2 (P0 #2), and Phase 3 (P0 #7).

Phase 1 — MemorySaver checkpointer:
1. research_graph has an InMemorySaver checkpointer attached
2. All expected nodes are registered (including quality_gate_node)
3. Invoking without thread_id raises ValueError
4. run_research passes a valid UUID as thread_id

Phase 2 — quality_gate_node:
5. All subjects succeed  → failed_subjects=[], is_partial_report=False, all outputs passed through
6. Some subjects fail    → failed subjects filtered, is_partial_report=True, clean outputs passed through
7. >50% subjects fail    → gate aborts: report_text set to error message, routes to storage_node
8. synthesis prompt includes missing-sections note when failed_subjects present
9. synthesis prompt has no missing-sections note when all subjects succeed
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# quality_gate_node drops outputs that are too short, contain no numeric data
# points, or never reference the ticker. A "good" section must clear all three:
# >= QUALITY_GATE_MIN_OUTPUT_CHARS (default 200), at least one number/percentage,
# and a mention of the ticker. Tests use AAPL (default) and NVDA, so name both.
_GOOD_TEXT = (
    "AAPL and NVDA both posted strong results this quarter, with revenue up 12.5% "
    "year over year and operating margins near 30%. Analysts model roughly 8% growth "
    "for the next 4 quarters, citing resilient demand and a price target around 250. "
    "Free cash flow exceeded 20 billion, supporting continued buybacks and dividends."
)


def test_graph_compiled_without_checkpointer():
    """Research graph is compiled without a checkpointer (no thread-scoped persistence)."""
    from research_graph import research_graph

    assert getattr(research_graph, "checkpointer", None) is None


def test_expected_nodes_present():
    from research_graph import research_graph

    nodes = list(research_graph.nodes.keys())
    for expected in ["planner_node", "specialized_node", "quality_gate_node", "synthesis_node", "storage_node"]:
        assert expected in nodes, f"Missing node: {expected}"


def test_invoke_does_not_require_thread_id_without_checkpointer():
    """Without a checkpointer, invoke is not required to receive configurable.thread_id."""
    from research_graph import research_graph
    from langgraph.graph.state import CompiledStateGraph

    assert isinstance(research_graph, CompiledStateGraph)
    assert research_graph.checkpointer is None


def test_run_research_passes_run_name(monkeypatch):
    """run_research passes a human-readable run_name in the invoke config."""
    import research_graph as rg

    captured = {}

    def mock_invoke(state, config=None):
        captured["config"] = config
        return {**state, "report_id": "test-id", "report_text": "ok"}

    monkeypatch.setattr(rg.research_graph, "invoke", mock_invoke)

    rg.run_research("AAPL", "investment")

    assert captured.get("config", {}).get("run_name") == "AAPL Research"


def test_each_run_gets_distinct_run_name_for_different_tickers(monkeypatch):
    """Different tickers produce different run_name values (LangSmith / tracing)."""
    import research_graph as rg

    names = []

    def mock_invoke(state, config=None):
        names.append((config or {}).get("run_name"))
        return {**state, "report_id": "x", "report_text": "ok"}

    monkeypatch.setattr(rg.research_graph, "invoke", mock_invoke)

    rg.run_research("AAPL", "investment")
    rg.run_research("NVDA", "swing")

    assert names == ["AAPL Research", "NVDA Research"]


# ---------------------------------------------------------------------------
# Phase 2: quality_gate_node
# ---------------------------------------------------------------------------

def _make_state(**overrides):
    base = {
        "ticker": "AAPL",
        "trade_type": "investment",
        "conversation_context": "",
        "plan": None,
        "subject_id": "",
        "research_outputs": {},
        "failed_subjects": [],
        "is_partial_report": False,
        "report_text": "",
        "report_id": "",
        "user_id": None,
        "emitter": None,
    }
    base.update(overrides)
    return base


def test_gate_all_success():
    """All subjects succeed → nothing filtered, failed_subjects empty, is_partial_report False."""
    from research_graph import quality_gate_node

    outputs = {
        "news_catalysts": {"subject_id": "news_catalysts", "research_output": _GOOD_TEXT, "sources": []},
        "company_overview": {"subject_id": "company_overview", "research_output": _GOOD_TEXT + "b", "sources": []},
    }
    result = quality_gate_node(_make_state(research_outputs=outputs))

    assert result["failed_subjects"] == []
    assert result["is_partial_report"] is False
    assert set(result["research_outputs"].keys()) == {"news_catalysts", "company_overview"}


def test_gate_partial_failure():
    """One subject fails → filtered out, is_partial_report True, clean subject passes through."""
    from research_graph import quality_gate_node

    outputs = {
        "news_catalysts": {"subject_id": "news_catalysts", "research_output": _GOOD_TEXT, "sources": []},
        "company_overview": {"subject_id": "company_overview", "error": "API timeout", "research_output": "Error"},
    }
    result = quality_gate_node(_make_state(research_outputs=outputs))

    assert result["failed_subjects"] == ["company_overview"]
    assert result["is_partial_report"] is True
    assert "news_catalysts" in result["research_outputs"]
    assert "company_overview" not in result["research_outputs"]


def test_gate_majority_failure_aborts():
    """More than 50% fail → gate writes error report_text and sets is_partial_report=True."""
    from research_graph import quality_gate_node

    outputs = {
        "subj_a": {"error": "failed", "research_output": "err"},
        "subj_b": {"error": "failed", "research_output": "err"},
        "subj_c": {"research_output": _GOOD_TEXT, "sources": []},
    }
    result = quality_gate_node(_make_state(ticker="NVDA", research_outputs=outputs))

    assert result["is_partial_report"] is True
    assert "NVDA" in result["report_text"]
    assert "failed" in result["report_text"].lower() or "error" in result["report_text"].lower()
    assert "Details:" in result["report_text"]
    assert "subj_a" in result["report_text"]
    # clean output still captured even on abort
    assert "subj_c" in result["research_outputs"]


def test_quality_gate_route_abort_goes_to_storage():
    """When gate has aborted (report_text set, no clean outputs), route to storage_node."""
    from research_graph import _quality_gate_route

    state = _make_state(
        is_partial_report=True,
        report_text="Research generation failed for AAPL: ...",
        research_outputs={},  # all failed
    )
    assert _quality_gate_route(state) == "storage_node"


def test_quality_gate_route_abort_with_partial_clean_goes_to_storage():
    """>50% failure with some clean subjects: gate sets report_text — must skip synthesis."""
    from research_graph import _quality_gate_route

    state = _make_state(
        is_partial_report=True,
        report_text="Research generation failed for NVDA: 2 of 3 subjects...",
        research_outputs={
            "subj_c": {"research_output": _GOOD_TEXT, "sources": []},
        },
    )
    assert _quality_gate_route(state) == "storage_node"


def test_quality_gate_route_partial_goes_to_synthesis():
    """When some subjects succeeded, route to synthesis_node even if is_partial_report=True."""
    from research_graph import _quality_gate_route

    state = _make_state(
        is_partial_report=True,
        report_text="",
        research_outputs={"news_catalysts": {"research_output": "ok"}},
    )
    assert _quality_gate_route(state) == "synthesis_node"


def test_quality_gate_route_all_success_goes_to_synthesis():
    """Normal path: all subjects succeeded → synthesis_node."""
    from research_graph import _quality_gate_route

    state = _make_state(
        is_partial_report=False,
        report_text="",
        research_outputs={"news_catalysts": {"research_output": "ok"}},
    )
    assert _quality_gate_route(state) == "synthesis_node"


def test_synthesis_prompt_includes_missing_sections_note():
    """_build_synthesis_prompt injects missing-sections note when failed_subjects present."""
    from agents.synthesis_node import _build_synthesis_prompt
    from unittest.mock import MagicMock

    plan = MagicMock()
    plan.selected_subject_ids = ["news_catalysts"]
    plan.trade_context = ""

    prompt = _build_synthesis_prompt(
        ticker="AAPL",
        trade_type="Investment",
        research_outputs={"news_catalysts": {"subject_name": "News", "research_output": "ok", "sources": []}},
        plan=plan,
        failed_subjects=["company_overview"],
    )

    assert "Missing Research Sections" in prompt
    assert "Research unavailable for this section" in prompt


def test_synthesis_prompt_no_missing_note_when_all_succeed():
    """_build_synthesis_prompt omits the missing-sections block when failed_subjects is empty."""
    from agents.synthesis_node import _build_synthesis_prompt
    from unittest.mock import MagicMock

    plan = MagicMock()
    plan.selected_subject_ids = ["news_catalysts"]
    plan.trade_context = ""

    prompt = _build_synthesis_prompt(
        ticker="AAPL",
        trade_type="Investment",
        research_outputs={"news_catalysts": {"subject_name": "News", "research_output": "ok", "sources": []}},
        plan=plan,
        failed_subjects=[],
    )

    assert "Missing Research Sections" not in prompt


# ---------------------------------------------------------------------------
# Phase 3: synthesis truncation detection (P0 #7)
# ---------------------------------------------------------------------------

def test_is_truncated_with_end_marker():
    """Report containing END_OF_REPORT is never truncated regardless of length."""
    from agents.synthesis_node import _is_truncated
    assert _is_truncated("short text END_OF_REPORT", 8000) is False
    assert _is_truncated("x" * 40000 + " END_OF_REPORT", 8000) is False


def test_is_truncated_short_without_marker():
    """Short report missing END_OF_REPORT is NOT flagged as truncated (genuine short output)."""
    from agents.synthesis_node import _is_truncated
    assert _is_truncated("This is a short report.", 8000) is False


def test_is_truncated_long_without_marker():
    """Long report (~90%+ of token budget) missing END_OF_REPORT IS flagged as truncated."""
    from agents.synthesis_node import _is_truncated
    # 8000 tokens * 4 chars/token * 0.9 threshold = 28800 chars minimum to trigger
    long_text = "x" * 29000
    assert _is_truncated(long_text, 8000) is True


def test_synthesis_node_clean_report(monkeypatch):
    """Normal path: LLM returns report with END_OF_REPORT → passed through unchanged."""
    from agents import synthesis_node as sn
    from unittest.mock import MagicMock

    clean_report = "Full report content. END_OF_REPORT"
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content=clean_report)
    monkeypatch.setattr(sn, "ChatGoogleGenerativeAI", lambda **kw: mock_llm)

    plan = MagicMock()
    plan.selected_subject_ids = ["news_catalysts"]
    plan.trade_context = ""
    plan.planner_reasoning = ""

    result = sn.synthesis_node({
        "ticker": "AAPL",
        "trade_type": "Investment",
        "research_outputs": {"news_catalysts": {"subject_name": "News", "research_output": "ok", "sources": []}},
        "plan": plan,
        "emitter": None,
        "failed_subjects": [],
    })

    assert result["report_text"] == clean_report
    assert result.get("is_partial_report") is None or result.get("is_partial_report") is False
    assert mock_llm.invoke.call_count == 1


def test_synthesis_node_truncation_retry_succeeds(monkeypatch):
    """Truncated report: retry called once, combined output contains END_OF_REPORT."""
    from agents import synthesis_node as sn
    from unittest.mock import MagicMock

    truncated = "x" * 29000          # long enough to trigger _is_truncated
    continuation = " ...rest. END_OF_REPORT"

    call_count = {"n": 0}

    def fake_invoke(messages, config=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return MagicMock(content=truncated)
        return MagicMock(content=continuation)

    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = fake_invoke
    monkeypatch.setattr(sn, "ChatGoogleGenerativeAI", lambda **kw: mock_llm)

    plan = MagicMock()
    plan.selected_subject_ids = ["news_catalysts"]
    plan.trade_context = ""
    plan.planner_reasoning = ""

    result = sn.synthesis_node({
        "ticker": "AAPL",
        "trade_type": "Investment",
        "research_outputs": {"news_catalysts": {"subject_name": "News", "research_output": "ok", "sources": []}},
        "plan": plan,
        "emitter": None,
        "failed_subjects": [],
    })

    assert "END_OF_REPORT" in result["report_text"]
    assert result["report_text"].startswith(truncated)
    assert mock_llm.invoke.call_count == 2


def test_synthesis_node_truncation_retry_fails(monkeypatch):
    """Truncated report: retry also lacks END_OF_REPORT → flagged as incomplete."""
    from agents import synthesis_node as sn
    from unittest.mock import MagicMock

    truncated = "x" * 29000

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content=truncated)
    monkeypatch.setattr(sn, "ChatGoogleGenerativeAI", lambda **kw: mock_llm)

    plan = MagicMock()
    plan.selected_subject_ids = ["news_catalysts"]
    plan.trade_context = ""
    plan.planner_reasoning = ""

    result = sn.synthesis_node({
        "ticker": "AAPL",
        "trade_type": "Investment",
        "research_outputs": {"news_catalysts": {"subject_name": "News", "research_output": "ok", "sources": []}},
        "plan": plan,
        "emitter": None,
        "failed_subjects": [],
    })

    assert result["is_partial_report"] is True
    assert "[INCOMPLETE REPORT" in result["report_text"]
    assert mock_llm.invoke.call_count == 2


def test_storage_node_completeness_flag_complete(monkeypatch):
    """storage_node writes completeness='complete' when is_partial_report is False."""
    import research_graph as rg
    from unittest.mock import MagicMock

    captured = {}
    mock_storage = MagicMock()
    mock_storage.store_report.side_effect = lambda **kw: (captured.update(kw) or "rpt-1")
    monkeypatch.setattr(rg, "ReportStorage", lambda: mock_storage, raising=False)

    # Patch the import inside storage_node
    import sys
    fake_module = MagicMock()
    fake_module.ReportStorage = lambda: mock_storage
    monkeypatch.setitem(sys.modules, "report_storage", fake_module)

    plan = MagicMock()
    plan.selected_subject_ids = ["news_catalysts"]
    plan.trade_context = ""
    plan.planner_reasoning = ""

    rg.storage_node({
        "ticker": "AAPL",
        "trade_type": "Investment",
        "report_text": "Full report END_OF_REPORT",
        "plan": plan,
        "user_id": None,
        "emitter": None,
        "is_partial_report": False,
        "failed_subjects": [],
    })

    assert captured["metadata"]["completeness"] == "complete"
    assert captured["metadata"]["failed_subjects"] == []


def test_storage_node_completeness_flag_partial(monkeypatch):
    """storage_node writes completeness='partial' when is_partial_report is True."""
    import research_graph as rg
    import sys
    from unittest.mock import MagicMock

    captured = {}
    mock_storage = MagicMock()
    mock_storage.store_report.side_effect = lambda **kw: (captured.update(kw) or "rpt-2")
    fake_module = MagicMock()
    fake_module.ReportStorage = lambda: mock_storage
    monkeypatch.setitem(sys.modules, "report_storage", fake_module)

    plan = MagicMock()
    plan.selected_subject_ids = ["news_catalysts", "company_overview"]
    plan.trade_context = ""
    plan.planner_reasoning = ""

    rg.storage_node({
        "ticker": "NVDA",
        "trade_type": "Swing Trade",
        "report_text": "[INCOMPLETE REPORT] partial content",
        "plan": plan,
        "user_id": None,
        "emitter": None,
        "is_partial_report": True,
        "failed_subjects": ["company_overview"],
    })

    assert captured["metadata"]["completeness"] == "partial"
    assert captured["metadata"]["failed_subjects"] == ["company_overview"]
