"""
Redaction utilities. Enforces "never persist secrets or raw sensitive data
into artifacts or logs" structurally rather than by convention:

  - Artifacts never contain literal parameter values at all (see
    src/artifact/schema.py Step.value_template) -- they contain
    `{{param_name}}` templates. A saved artifact is safe to commit to a
    reviewable repo even though the discovery run that produced it touched
    real-looking member IDs and a demo password.
  - Logs and evidence *do* need to show what happened for debugging, so this
    module redacts any parameter flagged `sensitive=True` (and a fixed list
    of well-known secret-like field names) before anything is written to
    disk or stdout.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable

REDACTED = "***REDACTED***"

_ALWAYS_SENSITIVE_KEYS = {"password", "secret", "token", "api_key", "apikey", "credential"}


def redact_params(params: Dict[str, Any], sensitive_names: Iterable[str]) -> Dict[str, Any]:
    """Return a copy of params with sensitive fields replaced by a redaction marker."""
    sensitive = set(sensitive_names) | _ALWAYS_SENSITIVE_KEYS
    out = copy.deepcopy(params)
    for key in list(out.keys()):
        if key.lower() in {s.lower() for s in sensitive}:
            out[key] = REDACTED
    return out


def redact_text(text: str, secret_values: Iterable[str]) -> str:
    """Scrub any raw secret value that might have leaked into free-form text (e.g. an error message)."""
    result = text
    for value in secret_values:
        if value:
            result = result.replace(value, REDACTED)
    return result
