import os

import pytest

from server.agents.interaction_agent.runtime import InteractionAgentRuntime


@pytest.mark.integration
def test_interaction_runtime_init_with_real_env() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set in environment")

    runtime = InteractionAgentRuntime()

    assert runtime.api_key
    assert runtime.model
    assert isinstance(runtime.tool_schemas, list)
