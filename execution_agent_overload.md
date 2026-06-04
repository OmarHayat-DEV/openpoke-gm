# Execution Agent Roster Compaction

## High-Level Overview

The interaction agent receives two execution-agent context blocks before each new prompt:

- `<execution_agent_memory>`: summarized historical context from archived execution agents.
- `<active_agents>`: currently active/reusable execution agents from `roster.json`.

Roster compaction prevents `<active_agents>` from growing indefinitely. When the active roster exceeds a configured threshold, the system identifies dormant execution agents, summarizes their logs into `summary.log`, archives their raw logs, and removes them from `roster.json`.

Recently active agents are not compacted. They remain in `roster.json` so ongoing work is not interrupted.

## Compaction Flow

1. The interaction runtime receives a user message or execution-agent message.
2. It records the current message in the conversation log.
3. It calls `await maybe_compact_execution_agents()` before `prepare_message_with_history(...)`.
4. The compactor loads `roster.json`.
5. If roster size is at or below the configured threshold, it returns immediately.
6. If roster size exceeds the threshold, it partitions agents into:
   - dormant agents: latest log timestamp is older than the configured dormant window, or the agent has no log file.
   - active agents: latest log timestamp is still within the configured dormant window.
7. Dormant agents are summarized into `summary.log`.
8. Dormant agents' `.log` files and a copy of the compacted roster are moved into `archive/<timestamp>/`.
9. `roster.json` is rewritten to contain only non-dormant active agents.
10. `prepare_message_with_history(...)` then builds the prompt using the updated `summary.log` and updated `roster.json`.

## New Files And Modules

### `server/services/execution/compactor.py`

New execution-agent roster compaction module.

Primary public function:

```py
async def maybe_compact_execution_agents() -> bool
```

Important internal helpers:

```py
async def _compact_execution_agents_locked() -> bool
def _is_dormant_agent(log_store, agent_name, dormant_after_minutes) -> bool
def _latest_log_timestamp(log_store, agent_name) -> Optional[datetime]
def _parse_log_timestamp(timestamp: str) -> Optional[datetime]
def _load_roster_transcripts(log_store, agents: List[str]) -> str
async def _summarize_execution_agents(previous_summary: str, transcripts: str) -> str
def _archive_agents(log_store, agents: List[str]) -> None
def _unique_archive_dir(path: Path) -> Path
```

The module also uses an async lock so multiple interaction turns cannot run roster compaction concurrently.

## Existing Files Updated

### `server/config.py`

Added compaction controls:

```py
execution_roster_summary_threshold: int = Field(default=2)
execution_roster_dormant_after_minutes: int = Field(default=5)
execution_roster_summary_model: Optional[str] = Field(default=None)
```

### `server/services/execution/roster.py`

Added:

```py
def replace_agents(self, agent_names: list[str]) -> None
```

This rewrites `roster.json` while keeping the file present. Compaction uses this to replace the active roster with only non-dormant agents.

### `server/services/execution/log_store.py`

Added helpers for paths and summary memory:

```py
def path_for_agent(self, agent_name: str) -> Path
def summary_path(self) -> Path
def archive_dir(self, archive_id: str) -> Path
def load_summary(self) -> str
def write_summary(self, summary: str) -> None
```

### `server/services/execution/__init__.py`

Exports:

```py
maybe_compact_execution_agents
```

### `server/agents/interaction_agent/runtime.py`

Calls compaction before prompt construction in both interaction paths:

```py
await maybe_compact_execution_agents()
```

This happens before `prepare_message_with_history(...)` in:

```py
InteractionAgentRuntime.execute(...)
InteractionAgentRuntime.handle_agent_message(...)
```

### `server/agents/interaction_agent/agent.py`

Adds an execution-agent memory section to the prompt input:

```xml
<execution_agent_memory>
...
</execution_agent_memory>
```

This section reads from `server/data/execution_agents/summary.log`.

### `server/agents/interaction_agent/system_prompt.md`

Explains the distinction between:

- archived execution-agent memory, used only as historical context.
- active agents, which are the only directly reusable execution agents.

The prompt now tells the interaction agent to create a new execution agent if relevant archived context exists but no matching active agent exists.

### `server/tests/services/execution/test_compactor.py`

Adds focused tests for:

- disabled threshold.
- no dormant agents.
- all dormant agents.
- mixed dormant and active agents.
- pseudo/tool logs staying untouched when not present in `roster.json`.

## Trigger For Compaction

Compaction is not a standalone scheduler. It runs opportunistically before interaction-agent prompt construction.

The trigger point is:

```py
await maybe_compact_execution_agents()
```

inside:

```py
InteractionAgentRuntime.execute(...)
InteractionAgentRuntime.handle_agent_message(...)
```

Compaction only proceeds if:

- `execution_roster_summary_threshold > 0`
- `len(roster.json) > execution_roster_summary_threshold`
- at least one roster-managed agent is dormant

If no agents are dormant, compaction does nothing and the roster remains unchanged.

## User-Controlled Arguments

These config fields control compaction behavior:

### `execution_roster_summary_threshold`

Default:

```py
2
```

Meaning:

- if roster size is `<= threshold`, do nothing.
- if roster size is `> threshold`, evaluate agents for dormancy and compact dormant ones.
- set to `0` to disable execution-agent roster compaction.

### `execution_roster_dormant_after_minutes`

Default:

```py
5
```

Meaning:

- if an agent's latest log timestamp is older than this many minutes, it is dormant and eligible for compaction.
- if an agent's latest log timestamp is within this window, it remains active and stays in `roster.json`.
- agents with no log entries are treated as dormant.

### `execution_roster_summary_model`

Default:

```py
None
```

Meaning:

- if set, this model is used for roster compaction summaries.
- if unset, the compactor falls back to `summarizer_model`.

## Data Layout

Active execution-agent directory:

```text
server/data/execution_agents/
  roster.json
  summary.log
  <active-agent>.log
  archive/
    <timestamp>/
      roster.json
      <archived-agent>.log
```

After compaction:

- `summary.log` contains summarized archived execution-agent context.
- dormant roster-managed logs move into `archive/<timestamp>/`.
- `roster.json` contains only agents that are not dormant.
- pseudo logs that are not listed in `roster.json`, such as `gmail-execution-agent.log` or `task-email-search.log`, are not archived by this process.

## Important Operational Details

- Compaction failures are non-fatal. If summarization or archiving fails, the app logs a warning and continues with the existing roster.
- Compaction happens before `prepare_message_with_history(...)` because that function reads both `summary.log` and `roster.json` for prompt construction.
- `build_system_prompt()` is static and does not depend on compaction state.
- The raw archive is retained for records, but the interaction agent does not read archive directories.
- The compactor rewrites `summary.log` with a fresh complete summary rather than appending endless deltas.
- Dormancy is based on latest log timestamp, not whether the log ends with `agent_response`. This avoids treating old logs ending in `agent_action` or `tool_response` as permanently in-flight.
- Since settings are cached by `get_settings()`, a running server may need a restart to pick up config default changes.

## Example

Given:

```json
[
  "Email Scanner",
  "Job Recommendations",
  "Weekly Glassdoor Summary",
  "Agent 1",
  "Agent 2"
]
```

With:

```py
execution_roster_summary_threshold = 2
execution_roster_dormant_after_minutes = 5
```

If all five agents have latest log timestamps older than five minutes, compaction will:

- summarize all five agents into `summary.log`.
- move all five logs into `archive/<timestamp>/`.
- rewrite `roster.json` to `[]`.

If `Weekly Glassdoor Summary` has a log update within the last five minutes, compaction will:

- summarize/archive the other four agents.
- keep `Weekly Glassdoor Summary` in `roster.json`.
