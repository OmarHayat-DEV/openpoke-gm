"""Recipient email sanity checks using syntax validation and an LLM typo judge."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from textwrap import dedent
from typing import Any, Optional

import httpx

from ...config import get_settings
from ...openrouter_client.client import OpenRouterBaseURL


EMAIL_JUDGE_TIMEOUT_SECONDS = 10.0
_EMAIL_PATTERN = re.compile(
    r"(?<![\w.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
    r"(?![\w-])"
)
_WELL_FORMED_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_VALID_STATUSES = {"LOOKS_OK", "SUSPICIOUS_TYPO", "MALFORMED", "UNKNOWN"}
_SYSTEM_PROMPT = dedent(
    """
    You judge whether an email address appears mistyped or suspicious.

    Return JSON only. Do not use Markdown or code fences.
    Use this exact shape:
    {
      "status": "LOOKS_OK | SUSPICIOUS_TYPO | MALFORMED | UNKNOWN",
      "suspicious": true,
      "suggested_email": "corrected@example.com or null",
      "message": "Short user-facing explanation"
    }

    Rules:
    - Do not claim the mailbox exists or is reachable.
    - Only judge whether the address appears malformed, mistyped, or suspicious.
    - Look for common domain typos such as gmial.com, hotnail.com, outlok.com, yaho.com.
    - If there is no obvious issue, return LOOKS_OK with suspicious=false.
    - If unsure, prefer LOOKS_OK or UNKNOWN over a confident warning.
    - If suggesting a correction, preserve the local part unless the typo is clearly elsewhere.
    """
).strip()


@dataclass(frozen=True)
class EmailVerificationResult:
    email: str
    suspicious: bool
    status: str
    message: str
    suggested_email: Optional[str] = None
    score: Optional[int] = None
    raw: Optional[dict[str, Any]] = None


def extract_email_addresses(text: str) -> list[str]:
    """Extract email-looking strings from text, deduping while preserving order."""

    seen: set[str] = set()
    emails: list[str] = []
    for match in _EMAIL_PATTERN.finditer(text or ""):
        email = match.group(0).strip(".,;:!?)]}\"'")
        key = email.lower()
        if email and key not in seen:
            seen.add(key)
            emails.append(email)
    return emails


def email_verifier(email: str) -> EmailVerificationResult:
    """Check whether an email looks suspicious before drafting."""

    normalized_email = (email or "").strip()
    if not normalized_email or not _is_well_formed_email(normalized_email):
        return EmailVerificationResult(
            email=normalized_email,
            suspicious=True,
            status="MALFORMED",
            message="This does not look like a well-formed email address.",
        )

    settings = get_settings()
    if not settings.email_verifier_enabled:
        return EmailVerificationResult(
            email=normalized_email,
            suspicious=False,
            status="DISABLED",
            message="Email verification disabled.",
        )

    try:
        payload = _judge_email_with_llm(normalized_email)
    except Exception:
        return _unknown_result(normalized_email)

    return _result_from_judgement(normalized_email, payload)


def _is_well_formed_email(email: str) -> bool:
    if not _WELL_FORMED_EMAIL_PATTERN.match(email):
        return False
    local, domain = email.rsplit("@", 1)
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if ".." in domain:
        return False
    return True


def _judge_email_with_llm(email: str) -> dict[str, Any]:
    settings = get_settings()
    model = settings.email_verifier_model or settings.interaction_agent_model
    api_key = (settings.openrouter_api_key or "").strip()
    if not api_key:
        raise RuntimeError("Missing OpenRouter API key")

    response = httpx.post(
        f"{OpenRouterBaseURL}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Judge this email address: {email}"},
            ],
            "stream": False,
        },
        timeout=EMAIL_JUDGE_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("Email judge returned a non-object payload")
    return parsed


def _result_from_judgement(email: str, payload: dict[str, Any]) -> EmailVerificationResult:
    status = payload.get("status")
    suspicious = payload.get("suspicious")
    message = payload.get("message")
    suggested_email = payload.get("suggested_email")

    if status not in _VALID_STATUSES or not isinstance(suspicious, bool) or not isinstance(message, str):
        return _unknown_result(email, raw=payload)

    if not isinstance(suggested_email, str) or not suggested_email.strip():
        suggested_email = None

    return EmailVerificationResult(
        email=email,
        suspicious=suspicious,
        status=status,
        message=message.strip() or _message_for_result(suspicious, suggested_email),
        suggested_email=suggested_email,
        raw=payload,
    )


def _unknown_result(email: str, raw: Optional[dict[str, Any]] = None) -> EmailVerificationResult:
    return EmailVerificationResult(
        email=email,
        suspicious=False,
        status="UNKNOWN",
        message="Email verification unavailable.",
        raw=raw,
    )


def _message_for_result(suspicious: bool, suggested_email: Optional[str]) -> str:
    if not suspicious:
        return "No obvious issue found with this email address."
    if suggested_email:
        return f"This address looks like it may have a typo. Did you mean {suggested_email}?"
    return "This address may need double-checking."


__all__ = ["EmailVerificationResult", "email_verifier", "extract_email_addresses"]
