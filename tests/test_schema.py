import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.artifact.schema import (
    ActionType, CapabilityArtifact, Checkpoint, DiscoveryMetadata, Locator, LocatorStrategy,
    ParamSpec, ParamType, RiskLevel, Step, TargetFingerprint, TargetSpec,
)


def _make_artifact(risk=RiskLevel.SAFE) -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id="test_cap",
        name="Test",
        description="desc",
        target=TargetSpec(entry_url="http://x/search", fingerprint=TargetFingerprint(app_key="app")),
        discovery=DiscoveryMetadata(discovery_run_id="r1", goal_text="goal", model="m"),
        input_params=[ParamSpec(name="member_id", type=ParamType.STRING)],
        output_params=[ParamSpec(name="balance", type=ParamType.STRING)],
        steps=[Step(id="s1", action=ActionType.FILL,
                     locators=[Locator(strategy=LocatorStrategy.LABEL_TEXT, value="Member ID")],
                     value_template="{{member_id}}", risk=risk)],
        checkpoint=Checkpoint(description="done", detect=Locator(strategy=LocatorStrategy.ROLE_NAME,
                                                                    value="heading|Done")),
    )


def test_roundtrip_json():
    art = _make_artifact()
    art2 = CapabilityArtifact.model_validate_json(art.to_json())
    assert art2.capability_id == art.capability_id
    assert len(art2.steps) == 1
    assert art2.steps[0].value_template == "{{member_id}}"


def test_capability_id_must_be_slug():
    with pytest.raises(Exception):
        _bad = _make_artifact()
        CapabilityArtifact.model_validate(
            {**_bad.model_dump(), "capability_id": "Not A Valid Slug!"}
        )


def test_risk_summary_takes_the_max():
    assert _make_artifact(risk=RiskLevel.SAFE).risk_summary == RiskLevel.SAFE
    assert _make_artifact(risk=RiskLevel.REVERSIBLE).risk_summary == RiskLevel.REVERSIBLE
    assert _make_artifact(risk=RiskLevel.IRREVERSIBLE).risk_summary == RiskLevel.IRREVERSIBLE


def test_param_name_must_be_identifier():
    with pytest.raises(Exception):
        ParamSpec(name="not a valid name", type=ParamType.STRING)


def test_sensitive_param_names():
    art = _make_artifact()
    art.input_params.append(ParamSpec(name="password", type=ParamType.SECRET_REF, sensitive=True))
    assert "password" in art.sensitive_param_names
    assert "member_id" not in art.sensitive_param_names
