import asyncio
from datetime import datetime
from types import SimpleNamespace

import server.services.execution.compactor as compactor
from server.services.execution.log_store import ExecutionAgentLogStore
from server.services.execution.roster import AgentRoster


def _settings(threshold: int = 1, dormant_after_minutes: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        execution_roster_summary_threshold=threshold,
        execution_roster_dormant_after_minutes=dormant_after_minutes,
        execution_roster_summary_model=None,
        summarizer_model="test-model",
        openrouter_api_key="test-key",
    )


def _install_stores(monkeypatch, tmp_path, *, threshold: int = 1, dormant_after_minutes: int = 5):
    roster = AgentRoster(tmp_path / "roster.json")
    log_store = ExecutionAgentLogStore(tmp_path)

    monkeypatch.setattr(compactor, "get_settings", lambda: _settings(threshold, dormant_after_minutes))
    monkeypatch.setattr(compactor, "get_agent_roster", lambda: roster)
    monkeypatch.setattr(compactor, "get_execution_agent_logs", lambda: log_store)
    monkeypatch.setattr(compactor, "now_in_user_timezone", lambda: datetime(2026, 6, 4, 12, 0, 0))
    return roster, log_store


def _write_log(log_store: ExecutionAgentLogStore, agent_name: str, timestamp: str) -> None:
    log_store.path_for_agent(agent_name).write_text(
        f'<agent_request timestamp="{timestamp}">Do work</agent_request>\n',
        encoding="utf-8",
    )


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


def test_compactor_skips_when_no_agents_are_dormant(monkeypatch, tmp_path):
    roster, log_store = _install_stores(monkeypatch, tmp_path, threshold=1)
    roster.replace_agents(["Agent A", "Agent B"])
    _write_log(log_store, "Agent A", "2026-06-04 11:58:00")
    _write_log(log_store, "Agent B", "2026-06-04 11:59:00")

    assert asyncio.run(compactor.maybe_compact_execution_agents()) is False
    assert roster.get_agents() == ["Agent A", "Agent B"]
    assert log_store.path_for_agent("Agent A").exists()


def test_compactor_summarizes_archives_and_clears_roster(monkeypatch, tmp_path):
    roster, log_store = _install_stores(monkeypatch, tmp_path, threshold=1)
    roster.replace_agents(["Agent A", "Agent B"])
    _write_log(log_store, "Agent A", "2026-06-04 11:00:00")
    _write_log(log_store, "Agent B", "2026-06-04 11:01:00")
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


def test_compactor_archives_only_dormant_agents(monkeypatch, tmp_path):
    roster, log_store = _install_stores(monkeypatch, tmp_path, threshold=1)
    roster.replace_agents(["Dormant Agent", "Active Agent"])
    _write_log(log_store, "Dormant Agent", "2026-06-04 11:00:00")
    _write_log(log_store, "Active Agent", "2026-06-04 11:59:00")

    async def fake_request_chat_completion(**kwargs):
        content = kwargs["messages"][0]["content"]
        assert "Dormant Agent" in content
        assert "Active Agent" not in content
        return {"choices": [{"message": {"content": "Dormant summary"}}]}

    monkeypatch.setattr(compactor, "request_chat_completion", fake_request_chat_completion)

    assert asyncio.run(compactor.maybe_compact_execution_agents()) is True
    assert roster.get_agents() == ["Active Agent"]
    assert not log_store.path_for_agent("Dormant Agent").exists()
    assert log_store.path_for_agent("Active Agent").exists()

    archives = list((tmp_path / "archive").iterdir())
    assert len(archives) == 1
    assert (archives[0] / "dormant-agent.log").exists()
    assert not (archives[0] / "active-agent.log").exists()
