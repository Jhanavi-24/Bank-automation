import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.safety.redaction import REDACTED, redact_params, redact_text


def test_redact_params_masks_flagged_and_wellknown_keys():
    out = redact_params({"member_id": "12345", "password": "hunter2", "api_key": "abc"}, sensitive_names=[])
    assert out["member_id"] == "12345"
    assert out["password"] == REDACTED
    assert out["api_key"] == REDACTED


def test_redact_params_masks_declared_sensitive_names():
    out = redact_params({"ssn": "123-45-6789"}, sensitive_names=["ssn"])
    assert out["ssn"] == REDACTED


def test_redact_text_scrubs_known_secret_values():
    out = redact_text("login failed for user with password hunter2 on retry", ["hunter2"])
    assert "hunter2" not in out
    assert REDACTED in out


def test_redact_text_noop_without_match():
    assert redact_text("no secrets here", ["hunter2"]) == "no secrets here"
