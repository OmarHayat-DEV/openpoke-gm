# Trace: `send_message_to_agent`

This document traces how the interaction agent constructs the LLM input and how the resulting tool call eventually reaches `send_message_to_agent(...)`.

## Call Flow

1. HTTP chat requests enter `chat_send`.

   File: `server/routes/chat.py`

   ```py
   async def chat_send(payload: ChatRequest) -> JSONResponse:
       return await handle_chat_request(payload)
   ```

2. `handle_chat_request` extracts the latest user message and starts the interaction runtime.

   File: `server/services/conversation/chat_handler.py`

   ```py
   user_message = _extract_latest_user_message(payload)
   user_content = user_message.content.strip()
   runtime = InteractionAgentRuntime()
   asyncio.create_task(_run_interaction())
   ```

   Inside `_run_interaction`:

   ```py
   await runtime.execute(user_message=user_content)
   ```

3. `InteractionAgentRuntime.execute` loads prior conversation context, records the user message, and constructs the LLM input.

   File: `server/agents/interaction_agent/runtime.py`

   ```py
   transcript_before = self._load_conversation_transcript()
   self.conversation_log.record_user_message(user_message)

   system_prompt = build_system_prompt()
   messages = prepare_message_with_history(
       user_message, transcript_before, message_type="user"
   )

   summary = await self._run_interaction_loop(system_prompt, messages)
   ```

4. `_load_conversation_transcript` loads conversation memory, not execution-agent logs.

   File: `server/agents/interaction_agent/runtime.py`

   ```py
   if self.settings.summarization_enabled:
       rendered = self.working_memory_log.render_transcript()
       if rendered.strip():
           return rendered
   return self.conversation_log.load_transcript()
   ```

5. `build_system_prompt` loads the static interaction-agent system prompt.

   File: `server/agents/interaction_agent/agent.py`

   ```py
   def build_system_prompt() -> str:
       return SYSTEM_PROMPT
   ```

6. `prepare_message_with_history` builds the user message sent to the LLM.

   File: `server/agents/interaction_agent/agent.py`

   ```py
   sections.append(_render_conversation_history(transcript))
   sections.append(f"<active_agents>\n{_render_active_agents()}\n</active_agents>")
   sections.append(_render_current_turn(latest_text, message_type))

   return [{"role": "user", "content": content}]
   ```

7. `_render_active_agents` loads `roster.json` before the LLM call.

   File: `server/agents/interaction_agent/agent.py`

   ```py
   roster = get_agent_roster()
   roster.load()
   agents = roster.get_agents()
   ...
   rendered.append(f'<agent name="{name}" />')
   ```

   The interaction agent receives roster information as prompt text:

   ```xml
   <active_agents>
   <agent name="Some Agent" />
   </active_agents>
   ```

8. `_run_interaction_loop` sends that prompt/message bundle to the LLM.

   File: `server/agents/interaction_agent/runtime.py`

   ```py
   response = await self._make_llm_call(system_prompt, messages)
   assistant_message = self._extract_assistant_message(response)
   raw_tool_calls = assistant_message.get("tool_calls") or []
   parsed_tool_calls = self._parse_tool_calls(raw_tool_calls)
   ```

9. `_make_llm_call` includes the tool schemas.

   File: `server/agents/interaction_agent/runtime.py`

   ```py
   return await request_chat_completion(
       model=self.model,
       messages=messages,
       system=system_prompt,
       api_key=self.api_key,
       tools=self.tool_schemas,
   )
   ```

10. The `send_message_to_agent` tool schema tells the LLM to provide `agent_name` and `instructions`.

    File: `server/agents/interaction_agent/tools.py`

    ```py
    "name": "send_message_to_agent"
    ...
    "agent_name": {"type": "string", ...}
    "instructions": {"type": "string", ...}
    "required": ["agent_name", "instructions"]
    ```

11. The LLM constructs the actual `agent_name` and `instructions` in its returned tool call. The backend only parses them.

    File: `server/agents/interaction_agent/runtime.py`

    ```py
    function_block = raw.get("function") or {}
    name = function_block.get("name")
    arguments, error = self._parse_tool_arguments(function_block.get("arguments"))

    parsed.append(
        _ToolCall(identifier=raw.get("id"), name=name, arguments=arguments)
    )
    ```

12. `_run_interaction_loop` optionally records the chosen `agent_name` in its summary.

    File: `server/agents/interaction_agent/runtime.py`

    ```py
    if tool_call.name == "send_message_to_agent":
        agent_name = tool_call.arguments.get("agent_name")
    ```

13. `_execute_tool` passes the parsed arguments to `handle_tool_call`.

    File: `server/agents/interaction_agent/runtime.py`

    ```py
    result = handle_tool_call(tool_call.name, tool_call.arguments)
    ```

14. `handle_tool_call` invokes `send_message_to_agent(**args)`.

    File: `server/agents/interaction_agent/tools.py`

    ```py
    if name == "send_message_to_agent":
        return send_message_to_agent(**args)
    ```

15. `send_message_to_agent` loads the roster, reuses or creates the agent, records the request, and starts the execution agent asynchronously.

    File: `server/agents/interaction_agent/tools.py`

    ```py
    roster = get_agent_roster()
    roster.load()
    existing_agents = set(roster.get_agents())
    is_new = agent_name not in existing_agents

    if is_new:
        roster.add_agent(agent_name)

    get_execution_agent_logs().record_request(agent_name, instructions)
    loop.create_task(_execute_async())
    ```

16. `_execute_async` hands off to `ExecutionBatchManager.execute_agent(...)`.

    File: `server/agents/interaction_agent/tools.py`

    ```py
    result = await _EXECUTION_BATCH_MANAGER.execute_agent(agent_name, instructions)
    ```

17. `ExecutionBatchManager.execute_agent` creates the execution-agent runtime.

    File: `server/agents/execution_agent/batch_manager.py`

    ```py
    runtime = ExecutionAgentRuntime(agent_name=agent_name)
    result = await asyncio.wait_for(
        runtime.execute(instructions),
        timeout=self.timeout_seconds,
    )
    ```

## Key Takeaway

There is no deterministic backend function that assigns `agent_name` before `send_message_to_agent`.

The backend constructs LLM prompt context containing `<active_agents>` from `roster.json`, then the LLM chooses `agent_name` and `instructions` in the returned tool call. The backend parses and forwards that choice.

Roster information is available to the LLM as text context, but the tool call is not code-constrained to an existing roster entry.
