import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.artifact.schema import ActionType, RiskLevel
from src.safety.allowlist import PolicyViolation, load_default_allowlist


@pytest.fixture()
def allowlist():
    return load_default_allowlist()


def test_allows_known_route(allowlist):
    allowlist.check_url("http://127.0.0.1:5055/members/12345")  # should not raise


def test_denies_unknown_domain(allowlist):
    with pytest.raises(PolicyViolation):
        allowlist.check_url("http://evil.example.com/members/12345")


def test_denies_unknown_route(allowlist):
    with pytest.raises(PolicyViolation):
        allowlist.check_url("http://127.0.0.1:5055/admin/delete-everything")


def test_action_type_allowed(allowlist):
    allowlist.check_action_type(ActionType.CLICK)  # should not raise


def test_irreversible_route_detection(allowlist):
    assert allowlist.is_irreversible_route("http://127.0.0.1:5055/members/123/sub-accounts/confirm")
    assert not allowlist.is_irreversible_route("http://127.0.0.1:5055/members/123")


def test_risk_for_url_matches_irreversible_routes(allowlist):
    risk = allowlist.risk_for_url("http://127.0.0.1:5055/members/123/sub-accounts/confirm", ActionType.CLICK)
    assert risk == RiskLevel.IRREVERSIBLE
