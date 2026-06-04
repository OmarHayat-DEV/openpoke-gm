"""Recipient email reachability checks using Rapid Email Verifier."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx


RAPID_EMAIL_VERIFIER_BASE_URL = "https://rapid-email-verifier.fly.dev"
RAPID_EMAIL_VERIFIER_TIMEOUT_SECONDS = 5.0
SUSPICIOUS_SCORE_THRESHOLD = 70

_SUSPICIOUS_STATUSES = {
    "INVALID_FORMAT",
    "INVALID_DOMAIN",
    "NO_MX_RECORDS",
    "DISPOSABLE",
}


@dataclass(frozen=True)
class EmailVerificationResult:
    email: str
    suspicious: bool
    status: str
    message: str
    suggested_email: Optional[str] = None
    score: Optional[int] = None
    raw: Optional[dict[str, Any]] = None


def email_verifier(email: str) -> EmailVerificationResult:
    """Check whether an email looks suspicious before drafting."""

    normalized_email = (email or "").strip()
    if not normalized_email:
        return EmailVerificationResult(
            email=normalized_email,
            suspicious=True,
            status="INVALID_FORMAT",
            message="This recipient address is empty. Are you sure you want to use this email?",
        )

    try:
        payload = _verify_with_rapid(normalized_email)
    except Exception:
        return EmailVerificationResult(
            email=normalized_email,
            suspicious=False,
            status="UNKNOWN",
            message="Email verification unavailable.",
        )

    return _result_from_rapid(normalized_email, payload)


def _verify_with_rapid(email: str) -> dict[str, Any]:
    query = urlencode({"email": email})
    url = f"{RAPID_EMAIL_VERIFIER_BASE_URL}/api/validate?{query}"
    response = httpx.get(url, timeout=RAPID_EMAIL_VERIFIER_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Rapid Email Verifier returned a non-object payload")
    return payload


def _result_from_rapid(email: str, payload: dict[str, Any]) -> EmailVerificationResult:
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        return EmailVerificationResult(
            email=email,
            suspicious=False,
            status="UNKNOWN",
            message="Email verification unavailable.",
            raw=payload,
        )

    score = payload.get("score")
    normalized_score = score if isinstance(score, int) else None
    suggested_email = payload.get("typoSuggestion")
    if not isinstance(suggested_email, str) or not suggested_email.strip():
        suggested_email = None

    suspicious = _is_suspicious(payload)
    return EmailVerificationResult(
        email=email,
        suspicious=suspicious,
        status=status,
        message=_message_for_result(suspicious, suggested_email),
        suggested_email=suggested_email,
        score=normalized_score,
        raw=payload,
    )


def _is_suspicious(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    if isinstance(status, str) and status in _SUSPICIOUS_STATUSES:
        return True

    score = payload.get("score")
    if isinstance(score, int) and score < SUSPICIOUS_SCORE_THRESHOLD:
        return True

    validations = payload.get("validations")
    if not isinstance(validations, dict):
        return False

    for key in ("syntax", "domain_exists", "mx_records"):
        if validations.get(key) is False:
            return True

    return False


def _message_for_result(suspicious: bool, suggested_email: Optional[str]) -> str:
    if not suspicious:
        return "Email appears reachable."
    if suggested_email:
        return f"This recipient might not be reachable. Did you mean {suggested_email}?"
    return "This recipient might not be reachable. Are you sure you want to use this email?"


__all__ = ["EmailVerificationResult", "email_verifier"]
