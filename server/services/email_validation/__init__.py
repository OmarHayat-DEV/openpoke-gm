"""Email validation helpers."""

from .verifier import EmailVerificationResult, email_verifier, extract_email_addresses

__all__ = ["EmailVerificationResult", "email_verifier", "extract_email_addresses"]
