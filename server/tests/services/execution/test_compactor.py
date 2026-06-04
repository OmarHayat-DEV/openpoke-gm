import asyncio
from types import SimpleNamespace

import server.services.execution.compactor as compactor
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.roster import AgentRoster


def _settings(threshold: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        execution_roster_summary_threshold=threshold,
        execution_roster_summary_model=None,
        summarizer_model="test-model",
        openrouter_api_key="test-key",
    )


def _install_stores(monkeypatch, tmp_path, *, threshold: int = 1):
    roster = AgentRoster(tmp_path / "roster.json")
    log_store = ExecutionAgentLogStore(tmp_path)

    monkeypatch.setattr(compactor, "get_settings", lambda: _settings(threshold))
    monkeypatch.setattr(compactor, "get_agent_roster", lambda: roster)
    monkeypatch.setattr(compactor, "get_execution_agent_logs", lambda: log_store)
    return roster, log_store


def test_compactor_skips_when_threshold_disabled(monkeypatch, tmp_path):
    roster, log_store = _install_stores(monkeypatch, tmp_path, threshold=0)
    roster.replace_agents(["Agent A", "Agent B"])
    log_store.record_request("Agent A", "Do A")
    log_store.record_agent_response("Agent A", "Done A")

    called = False

    async def fake_request_chat_completion(**kwargs):  # noqa: ARG001
        nonlocal called
        called = True
        return {"choices": [{"message": {"content": "summary"}}]}

    monkeypatch.setattr(compactor, "request_chat_completion", fake_request_chat_completion)

    assert asyncio.run(compactor.maybe_compact_execution_agents()) is False
    assert called is False
    assert roster.get_agents() == ["Agent A", "Agent B"]


def test_compactor_skips_when_agent_in_flight(monkeypatch, tmp_path):
    roster, log_store = _install_stores(monkeypatch, tmp_path, threshold=1)
    roster.replace_agents(["Agent A", "Agent B"])
    log_store.record_request("Agent A", "Do A")
    log_store.record_request("Agent B", "Do B")
    log_store.record_agent_response("Agent B", "Done B")

    assert asyncio.run(compactor.maybe_compact_execution_agents()) is False
    assert roster.get_agents() == ["Agent A", "Agent B"]
    assert log_store.path_for_agent("Agent A").exists()


def test_compactor_summarizes_archives_and_clears_roster(monkeypatch, tmp_path):
    roster, log_store = _install_stores(monkeypatch, tmp_path, threshold=1)
    roster.replace_agents(["Agent A", "Agent B"])
    log_store.record_request("Agent A", "Do A")
    log_store.record_agent_response("Agent A", "Done A")
    log_store.record_request("Agent B", "Do B")
    log_store.record_agent_response("Agent B", "Done B")
    log_store.record_action("task-email-search", "pseudo telemetry")

    async def fake_request_chat_completion(**kwargs):
        assert kwargs["model"] == "test-model"
        assert "Agent A" in kwargs["messages"][0]["content"]
        assert "Agent B" in kwargs["messages"][0]["content"]
        return {"choices": [{"message": {"content": "Archived execution summary"}}]}

    monkeypatch.setattr(compactor, "request_chat_completion", fake_request_chat_completion)

    assert asyncio.run(compactor.maybe_compact_execution_agents()) is True
    assert roster.get_agents() == []
    assert log_store.load_summary() == "Archived execution summary"
    assert not log_store.path_for_agent("Agent A").exists()
    assert not log_store.path_for_agent("Agent B").exists()
    assert log_store.path_for_agent("task-email-search").exists()

    archives = list((tmp_path / "archive").iterdir())
    assert len(archives) == 1
    assert (archives[0] / "roster.json").exists()
    assert (archives[0] / "agent-a.log").exists()
    assert (archives[0] / "agent-b.log").exists()
