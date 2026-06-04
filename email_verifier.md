# Email Verifier

## High-Level Overview

The email verifier is now an email-address sanity checker, not a mailbox reachability verifier. It does not try to prove that an inbox exists. Instead, it catches malformed addresses and asks an LLM judge whether a well-formed address appears mistyped or suspicious.

There are two enforcement points:

- automatic preflight in `InteractionAgentRuntime.execute(...)`, which checks email addresses typed by the user and appends warning footnotes to the user-facing response when needed.
- Gmail draft guard in `gmail_create_draft(...)`, which remains a deterministic final safety net before creating a draft.

The old Rapid Email Verifier dependency has been retired because it could not reliably prove mailbox existence for providers like Gmail.

## Runtime Flow

1. User sends a message.
2. `InteractionAgentRuntime.execute(...)` extracts email-looking strings from the raw user message.
3. Each extracted email is passed to `email_verifier(...)`.
4. Malformed addresses are flagged immediately using deterministic syntax validation.
5. Well-formed addresses are judged by an LLM for likely typo/suspicion only.
6. The normal interaction-agent flow runs.
7. If any user-provided email was suspicious, the final user-facing response gets a short warning footnote.
8. Later, if an execution agent calls `gmail_create_draft(...)`, the Gmail draft guard calls the same `email_verifier(...)` before creating the draft.
9. If the draft guard sees a suspicious result, it blocks draft creation and returns `needs_recipient_confirmation`.

## New Behavior

For a suspicious user-provided email, the response is decorated with a note like:

```text
What subject should I use?

Note: `omar@gmial.com` might have a typo. Did you mean `omar@gmail.com`?
```

If no suspicious issue is found, no note is shown.

## Files And Functions

### `server/services/email_validation/verifier.py`

Public API:

```py
def email_verifier(email: str) -> EmailVerificationResult
def extract_email_addresses(text: str) -> list[str]
```

Result type:

```py
@dataclass(frozen=True)
class EmailVerificationResult:
    email: str
    suspicious: bool
    status: str
    message: str
    suggested_email: Optional[str] = None
    score: Optional[int] = None
    raw: Optional[dict[str, Any]] = None
```

Important internal helpers:

```py
def _is_well_formed_email(email: str) -> bool
def _judge_email_with_llm(email: str) -> dict[str, Any]
def _result_from_judgement(email: str, payload: dict[str, Any]) -> EmailVerificationResult
def _unknown_result(email: str, raw: Optional[dict[str, Any]] = None) -> EmailVerificationResult
```

### `server/services/email_validation/__init__.py`

Exports:

```py
EmailVerificationResult
email_verifier
extract_email_addresses
```

### `server/agents/interaction_agent/runtime.py`

Adds automatic preflight for user-authored messages only.

Key helper methods:

```py
def _collect_email_warnings(self, text: str) -> List[EmailVerificationResult]
def _format_email_warning_footnote(self, warnings: List[EmailVerificationResult]) -> str
def _format_single_email_warning(self, warning: EmailVerificationResult) -> str
def _append_email_warning_footnote(self, message: str) -> str
def _decorate_send_message_tool_call(self, tool_call: _ToolCall) -> None
```

`_decorate_send_message_tool_call(...)` ensures footnotes are appended even when the interaction agent uses `send_message_to_user(...)`, which records the message directly.

### `server/agents/execution_agent/tools/gmail.py`

Keeps the Gmail draft guard.

Behavior:

- calls `email_verifier(recipient_email)` before creating a draft.
- blocks draft creation if `verification.suspicious` is true.
- proceeds if verifier result is `UNKNOWN` or non-suspicious.
- supports `skip_email_verification=True` after explicit user confirmation.

### `server/config.py`

Email verifier controls:

```py
email_verifier_enabled: bool
email_verifier_model: Optional[str]
```

Environment variables:

```env
OPENPOKE_EMAIL_VERIFIER_ENABLED=1
OPENPOKE_EMAIL_VERIFIER_MODEL=<optional-model-name>
```

If `OPENPOKE_EMAIL_VERIFIER_MODEL` is unset, the verifier falls back to `interaction_agent_model`.

## LLM Judge Contract

The LLM judge is asked to return JSON only:

```json
{
  "status": "LOOKS_OK | SUSPICIOUS_TYPO | MALFORMED | UNKNOWN",
  "suspicious": true,
  "suggested_email": "corrected@example.com or null",
  "message": "Short user-facing explanation"
}
```

Rules given to the judge:

- do not claim the mailbox exists.
- only judge whether the address appears malformed, mistyped, or suspicious.
- look for common domain typos such as `gmial.com`, `hotnail.com`, `outlok.com`, `yaho.com`.
- if there is no obvious issue, return `LOOKS_OK` with `suspicious=false`.
- if unsure, prefer `LOOKS_OK` or `UNKNOWN` over a confident warning.

If the LLM call fails or returns invalid JSON/shape, the verifier returns:

```json
{
  "status": "UNKNOWN",
  "suspicious": false,
  "message": "Email verification unavailable."
}
```

This is intentionally fail-open.

## Gmail Draft Guard Result

Suspicious recipient result:

```json
{
  "status": "needs_recipient_confirmation",
  "recipient_email": "user@gmial.com",
  "suggested_email": "user@gmail.com",
  "message": "This looks like it may be a typo. Did you mean user@gmail.com?",
  "verification": {
    "status": "SUSPICIOUS_TYPO",
    "score": null,
    "suspicious": true
  }
}
```

Override after explicit user confirmation:

```py
gmail_create_draft(
    recipient_email="user@gmial.com",
    subject="...",
    body="...",
    skip_email_verification=True,
)
```

## Logging

Runtime preflight logs:

- `Checking email <email>`
- `Email judgement: <status>`
- `Email sanity check passed`
- `Prompt user to double check email`

Gmail draft guard logs:

- `Email verifier check completed`
- `Email verifier flagged suspicious recipient; draft creation blocked`
- `Email verifier unavailable; continuing draft creation`
- `Email verifier skipped by explicit user confirmation`

## What This Does Not Do

- It does not prove mailbox existence.
- It does not ping the inbox.
- It does not guarantee deliverability.
- It does not validate `cc`, `bcc`, or `extra_recipients` yet.
- It does not run preflight on execution-agent messages, only user-authored messages.

## Test Coverage

### Email Verifier Service Tests

`server/tests/services/email_validation/test_verifier.py` covers:

- email extraction and dedupe.
- malformed syntax returning `MALFORMED` without calling the LLM judge.
- disabled verifier.
- LLM `LOOKS_OK` result.
- LLM `SUSPICIOUS_TYPO` result with suggestion.
- malformed LLM payload returning `UNKNOWN`.
- LLM failure returning `UNKNOWN`.

### Interaction Runtime Preflight Tests

`server/tests/agents/interaction_agent/test_email_preflight.py` covers:

- suspicious user-provided email appends a warning footnote.
- non-suspicious email does not append a footnote.
- execution-agent messages do not run email preflight.

### Gmail Draft Guard Tests

`server/tests/agents/execution_agent/tools/test_gmail_email_verifier.py` covers:

- valid recipient proceeds to draft creation.
- suspicious recipient blocks draft creation.
- `UNKNOWN` verifier status proceeds to draft creation.
- explicit `skip_email_verification=True` proceeds and skips verifier call.
- Gmail not connected returns the existing error and skips verifier call.

## Verification Commands

```sh
python -m pytest server/tests/services/email_validation
python -m pytest server/tests/agents/interaction_agent/test_email_preflight.py
python -m pytest server/tests/agents/execution_agent/tools/test_gmail_email_verifier.py
python -m compileall server
```
