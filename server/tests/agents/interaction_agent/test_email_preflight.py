import asyncio
from types import SimpleNamespace

import server.agents.interaction_agent.runtime as runtime_module
from server.agents.interaction_agent.runtime import InteractionAgentRuntime, _LoopSummary
from server.services.email_validation import EmailVerificationResult


class _FakeConversationLog:
    def __init__(self):
        self.user_messages = []
        self.agent_messages = []
        self.replies = []

    def record_user_message(self, message):
        self.user_messages.append(message)

    def record_agent_message(self, message):
        self.agent_messages.append(message)

    def record_reply(self, message):
        self.replies.append(message)

    def load_transcript(self):
        return ""


def _settings():
    return SimpleNamespace(
        openrouter_api_key="test-key",
        interaction_agent_model="test-model",
        summarization_enabled=False,
    )


def _install_runtime_dependencies(monkeypatch):
    log = _FakeConversationLog()
    monkeypatch.setattr(runtime_module, "get_settings", _settings)
    monkeypatch.setattr(runtime_module, "get_conversation_log", lambda: log)
    monkeypatch.setattr(runtime_module, "get_working_memory_log", lambda: SimpleNamespace())
    monkeypatch.setattr(runtime_module, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(runtime_module, "maybe_compact_execution_agents", _noop_compactor)
    return log


async def _noop_compactor():
    return False


def test_execute_appends_email_warning_footnote(monkeypatch):
    log = _install_runtime_dependencies(monkeypatch)
    monkeypatch.setattr(runtime_module, "extract_email_addresses", lambda text: ["user@gmial.com"])
    monkeypatch.setattr(
        runtime_module,
        "email_verifier",
        lambda email: EmailVerificationResult(
            email=email,
            suspicious=True,
            status="SUSPICIOUS_TYPO",
            message="This looks like it may be a typo.",
            suggested_email="user@gmail.com",
        ),
    )

    async def fake_loop(self, system_prompt, messages):  # noqa: ARG001
        return _LoopSummary(last_assistant_text="What subject should I use?")

    monkeypatch.setattr(InteractionAgentRuntime, "_run_interaction_loop", fake_loop)

    runtime = InteractionAgentRuntime()
    result = asyncio.run(runtime.execute("Draft an email to user@gmial.com"))

    assert result.response == (
        "What subject should I use?\n\n"
        "Note: `user@gmial.com` might have a typo. Did you mean `user@gmail.com`?"
    )
    assert log.replies == [result.response]


def test_execute_no_footnote_for_non_suspicious_email(monkeypatch):
    log = _install_runtime_dependencies(monkeypatch)
    monkeypatch.setattr(runtime_module, "extract_email_addresses", lambda text: ["user@gmail.com"])
    monkeypatch.setattr(
        runtime_module,
        "email_verifier",
        lambda email: EmailVerificationResult(
            email=email,
            suspicious=False,
            status="LOOKS_OK",
            message="No obvious issue found.",
        ),
    )

    async def fake_loop(self, system_prompt, messages):  # noqa: ARG001
        return _LoopSummary(last_assistant_text="What subject should I use?")

    monkeypatch.setattr(InteractionAgentRuntime, "_run_interaction_loop", fake_loop)

    runtime = InteractionAgentRuntime()
    result = asyncio.run(runtime.execute("Draft an email to user@gmail.com"))

    assert result.response == "What subject should I use?"
    assert log.replies == ["What subject should I use?"]


def test_handle_agent_message_does_not_run_email_preflight(monkeypatch):
    _install_runtime_dependencies(monkeypatch)

    def fail_extract(text):  # noqa: ARG001
        raise AssertionError("email preflight should not run for execution-agent messages")

    monkeypatch.setattr(runtime_module, "extract_email_addresses", fail_extract)

    async def fake_loop(self, system_prompt, messages):  # noqa: ARG001
        return _LoopSummary(last_assistant_text="Agent result processed.")

    monkeypatch.setattr(InteractionAgentRuntime, "_run_interaction_loop", fake_loop)

    runtime = InteractionAgentRuntime()
    result = asyncio.run(runtime.handle_agent_message("Found user@gmial.com in search results"))

    assert result.response == "Agent result processed."
