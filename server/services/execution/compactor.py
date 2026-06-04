"""Compact stale execution-agent roster state into summary memory."""

from __future__ import annotations

import json
import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from textwrap import dedent
from typing import List, Optional

from ...config import get_settings
from ...logging_config import logger
from ...openrouter_client import request_chat_completion
from ...utils.timezones import now_in_user_timezone
from .log_store import ExecutionAgentLogStore, get_execution_agent_logs
from .roster import get_agent_roster


_SYSTEM_PROMPT = dedent(
    """
    You are compacting archived execution-agent work into durable memory for a future interaction agent.

    Produce a fresh, complete summary. Do not append a delta.

    Preserve:
    - unresolved tasks and their next steps
    - recent important completed tasks
    - people, companies, email threads, draft IDs, links, identifiers, and other concrete references
    - follow-up requirements, blockers, deadlines, and current status
    - context useful for deciding whether future work resembles previous execution-agent work

    Omit:
    - low-level tool noise
    - repeated tool responses
    - obsolete completed details
    - implementation logs not useful for future task routing

    Keep the result concise, factual, and information-dense.
    """
).strip()

_compaction_lock = asyncio.Lock()


async def maybe_compact_execution_agents() -> bool:
    """Summarize and archive roster-managed execution agents when threshold is exceeded."""

    async with _compaction_lock:
        try:
            return await _compact_execution_agents_locked()
        except Exception as exc:  # pragma: no cover - defensive maintenance path
            logger.warning(
                "execution roster compaction failed; continuing without compaction",
                extra={"error": str(exc)},
            )
            return False


async def _compact_execution_agents_locked() -> bool:
    settings = get_settings()
    threshold = settings.execution_roster_summary_threshold
    if threshold <= 0:
        return False

    roster = get_agent_roster()
    roster.load()
    agents = roster.get_agents()
    if len(agents) <= threshold:
        return False

    log_store = get_execution_agent_logs()
    dormant_after_minutes = max(settings.execution_roster_dormant_after_minutes, 0)
    dormant_agents = [
        agent for agent in agents if _is_dormant_agent(log_store, agent, dormant_after_minutes)
    ]
    dormant_agent_set = set(dormant_agents)
    active_agents = [agent for agent in agents if agent not in dormant_agent_set]
    if not dormant_agents:
        logger.info(
            "execution roster compaction skipped; no dormant agents",
            extra={
                "count": len(agents),
                "threshold": threshold,
                "dormant_after_minutes": dormant_after_minutes,
            },
        )
        return False

    previous_summary = log_store.load_summary()
    transcripts = _load_roster_transcripts(log_store, dormant_agents)
    summary = await _summarize_execution_agents(previous_summary, transcripts)
    log_store.write_summary(summary)
    _archive_agents(log_store, dormant_agents)
    roster.replace_agents(active_agents)

    logger.info(
        "execution roster compacted",
        extra={
            "archived_agents": len(dormant_agents),
            "kept_active_agents": len(active_agents),
            "threshold": threshold,
            "dormant_after_minutes": dormant_after_minutes,
        },
    )
    return True


def _is_dormant_agent(
    log_store: ExecutionAgentLogStore,
    agent_name: str,
    dormant_after_minutes: int,
) -> bool:
    latest_timestamp = _latest_log_timestamp(log_store, agent_name)
    if latest_timestamp is None:
        return True

    now = now_in_user_timezone()
    if isinstance(now, str):  # pragma: no cover - defensive; called without fmt
        now = datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
    cutoff = now.replace(tzinfo=None) - timedelta(minutes=dormant_after_minutes)
    return latest_timestamp < cutoff


def _latest_log_timestamp(log_store: ExecutionAgentLogStore, agent_name: str) -> Optional[datetime]:
    latest_timestamp: Optional[datetime] = None
    for _, timestamp, _ in log_store.iter_entries(agent_name):
        parsed = _parse_log_timestamp(timestamp)
        if parsed is not None:
            latest_timestamp = parsed
    return latest_timestamp


def _parse_log_timestamp(timestamp: str) -> Optional[datetime]:
    if not timestamp:
        return None
    try:
        return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _load_roster_transcripts(log_store: ExecutionAgentLogStore, agents: List[str]) -> str:
    sections: List[str] = []
    for agent_name in agents:
        transcript = log_store.load_transcript(agent_name).strip()
        if not transcript:
            transcript = "(no log entries)"
        sections.append(f'<execution_agent name="{agent_name}">\n{transcript}\n</execution_agent>')
    return "\n\n".join(sections)


async def _summarize_execution_agents(previous_summary: str, transcripts: str) -> str:
    settings = get_settings()
    model = settings.execution_roster_summary_model or settings.summarizer_model
    content = dedent(
        f"""
        Existing execution-agent memory:
        {previous_summary.strip() or "None"}

        Execution-agent logs to archive:
        {transcripts}
        """
    ).strip()

    response = await request_chat_completion(
        model=model,
        messages=[{"role": "user", "content": content}],
        system=_SYSTEM_PROMPT,
        api_key=settings.openrouter_api_key,
    )
    choices = response.get("choices") or []
    message = choices[0].get("message") if choices else None
    summary = (message or {}).get("content", "") if isinstance(message, dict) else ""
    summary = summary.strip()
    if not summary:
        raise RuntimeError("Execution roster summarizer returned empty content")
    return summary


def _archive_agents(log_store: ExecutionAgentLogStore, agents: List[str]) -> None:
    archive_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    archive_dir = _unique_archive_dir(log_store.archive_dir(archive_id))
    archive_dir.mkdir(parents=True, exist_ok=False)

    roster_payload = json.dumps(agents, indent=2)
    (archive_dir / "roster.json").write_text(roster_payload, encoding="utf-8")

    for agent_name in agents:
        path = log_store.path_for_agent(agent_name)
        if not path.exists():
            continue
        destination = archive_dir / path.name
        shutil.move(str(path), str(destination))


def _unique_archive_dir(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.name}-{index}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Unable to allocate execution-agent archive directory")


__all__ = ["maybe_compact_execution_agents"]
