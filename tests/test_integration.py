"""
End-to-end integration tests against the live mock app and the real
(LLM-discovered) artifacts under artifacts/. No LLM calls happen here --
only deterministic replay, exactly the production path.

Requires: `python -m src.target_app.app` running on BASE_URL, and the two
artifacts already discovered (see README.md "Demo path"). Skipped
automatically if either precondition isn't met, so `pytest` stays runnable
without a live server.
"""
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from src.agent.discovery import login_operator
from src.artifact.schema import CapabilityArtifact
from src.common.evidence import EvidenceRecorder
from src.handoff.controller import HandoffController
from src.replay.engine import replay
from src.safety.allowlist import load_default_allowlist

BASE_URL = "http://127.0.0.1:5055"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _app_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5055), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _app_reachable(), reason="mock bank app is not running on 127.0.0.1:5055 (see README 'Setup')"
)


def _load(capability_id: str) -> CapabilityArtifact:
    path = REPO_ROOT / "artifacts" / f"{capability_id}.json"
    if not path.exists():
        pytest.skip(f"artifacts/{capability_id}.json not found -- run discovery first (see README)")
    return CapabilityArtifact.from_json_file(str(path))


def _replay(artifact, params, evidence_subdir, confirm_irreversible=False, handoff_mode=None, operator_script=None):
    allowlist = load_default_allowlist()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        evidence = EvidenceRecorder(str(REPO_ROOT / "evidence" / evidence_subdir), "replay")
        login_operator(page, BASE_URL)
        page.goto(artifact.target.entry_url)
        page.wait_for_load_state("load")
        handoff = None
        if handoff_mode:
            handoff = HandoffController(page=page, evidence=evidence, allowlist=allowlist,
                                          mode=handoff_mode, operator_script=operator_script or [])
        result = replay(artifact, params, page, allowlist, evidence,
                         confirm_irreversible=confirm_irreversible, handoff=handoff)
        browser.close()
    return result


def test_lookup_happy_path():
    artifact = _load("lookup_member_balance")
    result = _replay(artifact, {"member_id": "12345"}, "_pytest")
    assert result.status.value == "success"
    assert result.outputs["savings_balance"] == "$8214.53"


def test_lookup_generalizes_to_a_different_member():
    artifact = _load("lookup_member_balance")
    result = _replay(artifact, {"member_id": "12346"}, "_pytest")
    assert result.status.value == "success"
    assert result.outputs["savings_balance"] == "$530.10"


def test_lookup_business_outcome_not_found():
    artifact = _load("lookup_member_balance")
    result = _replay(artifact, {"member_id": "00000"}, "_pytest")
    assert result.status.value == "business_outcome"
    assert result.outcome_code == "MEMBER_NOT_FOUND"


def test_lookup_recoverable_transient_error():
    artifact = _load("lookup_member_balance")
    result = _replay(artifact, {"member_id": "50000"}, "_pytest")
    assert result.status.value == "success"
    assert "transient_system_busy" in result.interrupts_fired


def test_lookup_unhandled_failure_without_handoff():
    artifact = _load("lookup_member_balance")
    result = _replay(artifact, {"member_id": "66666"}, "_pytest")
    assert result.status.value == "hard_failure"
    assert result.human_intervention is False


def test_lookup_unhandled_failure_resolved_via_handoff():
    artifact = _load("lookup_member_balance")
    result = _replay(
        artifact, {"member_id": "66666"}, "_pytest",
        handoff_mode="scripted", operator_script=["click I have authorized access", "resume"],
    )
    assert result.status.value == "success"
    assert result.human_intervention is True


def test_subaccount_blocked_without_confirmation():
    artifact = _load("open_member_sub_account")
    result = _replay(artifact, {"member_id": "12345", "account_type": "Savings", "initial_deposit": "500"},
                      "_pytest")
    assert result.status.value == "confirmation_required"


def test_subaccount_succeeds_with_confirmation():
    artifact = _load("open_member_sub_account")
    result = _replay(artifact, {"member_id": "12345", "account_type": "Savings", "initial_deposit": "500"},
                      "_pytest", confirm_irreversible=True)
    assert result.status.value == "success"
    assert result.outputs["account_number"].startswith("SUB-")


def test_subaccount_invalid_deposit_business_outcome():
    artifact = _load("open_member_sub_account")
    result = _replay(artifact, {"member_id": "12345", "account_type": "Savings", "initial_deposit": "-10"},
                      "_pytest")
    assert result.status.value == "business_outcome"
    assert result.outcome_code == "INVALID_DEPOSIT_AMOUNT"
