"""
Capability artifact schema.

This is the contract between three parties:
  - the discovery agent, which emits an artifact after a successful LLM-driven run
  - the replay engine, which executes an artifact deterministically
  - the calling AI agent (or a human reviewer), which needs to understand what a
    capability does, what it needs, and what it returns without reading the code

Design goals (see REPORT.md section 2 for the full rationale):
  1. Steps carry a *ranked list* of locator strategies, not one selector. Replay
     tries them in order. This is the seam that lets the same recorded flow
     survive small per-tenant/version differences without brittle re-recording.
  2. Interrupt rules are declared once per artifact, not per step, because the
     same "session expired" or "system busy" condition can occur after almost
     any step in a legacy app. Checking them opportunistically -- after every
     action -- is what separates "replay crashed" from "replay handled it."
  3. Every parameter (in or out) is typed and flagged sensitive or not, so the
     redaction rule ("never persist secrets/PII") is enforced structurally
     rather than left to convention.
  4. Nothing here is raw model transcript. An artifact is meant to be read and
     approved by a human reviewer, and invoked by another program, without
     either needing to understand how the LLM discovered it.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #

class SurfaceType(str, Enum):
    WEB = "web"
    LEGACY_WEB = "legacy_web"
    DESKTOP = "desktop"


class LocatorStrategy(str, Enum):
    TEST_ID = "test_id"            # data-testid / id explicitly meant for automation
    ROLE_NAME = "role_name"        # ARIA role + accessible name (e.g. button "Search")
    LABEL_TEXT = "label_text"      # associated <label> text for a form field (row-label -> input/select/textarea)
    ROW_VALUE_TEXT = "row_value_text"  # row-label -> the *other* table cell holding a (dynamic) value to read
    TEXT_EXACT = "text_exact"      # exact visible text content
    TEXT_CONTAINS = "text_contains"  # substring match -- used for messages with dynamic content
    CSS = "css"                    # structural CSS path -- last resort before coordinates
    COORDINATES = "coordinates"    # absolute viewport coordinates -- most fragile


# Robustness ranking used when the discovery agent proposes fallback order and
# when a human reviewer audits a step. Lower number = more robust.
LOCATOR_ROBUSTNESS_RANK = {
    LocatorStrategy.TEST_ID: 0,
    LocatorStrategy.ROLE_NAME: 1,
    LocatorStrategy.LABEL_TEXT: 2,
    LocatorStrategy.ROW_VALUE_TEXT: 3,
    LocatorStrategy.TEXT_EXACT: 4,
    LocatorStrategy.TEXT_CONTAINS: 5,
    LocatorStrategy.CSS: 6,
    LocatorStrategy.COORDINATES: 7,
}


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    SELECT_OPTION = "select_option"
    WAIT_FOR = "wait_for"
    EXTRACT = "extract"
    ASSERT_CHECKPOINT = "assert_checkpoint"


class RiskLevel(str, Enum):
    SAFE = "safe"                # read-only / freely repeatable
    REVERSIBLE = "reversible"    # writes state but can be undone or re-run harmlessly
    IRREVERSIBLE = "irreversible"  # e.g. opening an account, posting a transaction


class OutcomeClass(str, Enum):
    BUSINESS_OUTCOME = "business_outcome"  # legitimate answer, not a crash
    RECOVERABLE = "recoverable"            # known condition, can self-heal and continue
    HARD_FAILURE = "hard_failure"          # stop and surface a debuggable error


class ParamType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    SECRET_REF = "secret_ref"  # a reference to a credential/secret store key, never a raw value


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #

class Locator(BaseModel):
    """One way to find a control. Steps carry an ordered list of these."""
    strategy: LocatorStrategy
    value: str = Field(..., description="Meaning depends on strategy, e.g. role='button', name='Search'.")
    frame: Optional[str] = Field(
        None, description="Optional locator for the containing iframe/frame, if the target is nested."
    )
    notes: Optional[str] = Field(None, description="Why this strategy was chosen / robustness reasoning.")


class ParamSpec(BaseModel):
    name: str
    type: ParamType
    description: str = ""
    required: bool = True
    sensitive: bool = False
    example: Optional[str] = None

    @field_validator("name")
    @classmethod
    def _valid_identifier(cls, v: str) -> str:
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", v):
            raise ValueError(f"param name {v!r} must be a valid identifier")
        return v


class Step(BaseModel):
    id: str
    action: ActionType
    description: str = Field("", description="Human-readable: what this step accomplishes and why.")
    locators: List[Locator] = Field(
        default_factory=list, description="Ranked fallback chain. Empty for actions like NAVIGATE/WAIT_FOR."
    )
    value_template: Optional[str] = Field(
        None,
        description=(
            "Literal value or a {{param_name}} template for FILL/SELECT_OPTION/NAVIGATE. "
            "Never a literal secret -- reference input params instead."
        ),
    )
    extract_as: Optional[str] = Field(None, description="If action=EXTRACT, the output param name to populate.")
    timeout_ms: int = 5000
    risk: RiskLevel = RiskLevel.SAFE
    requires_confirmation: bool = Field(
        False, description="If true, replay must not execute this step unattended (see safety guardrails)."
    )


class InterruptRule(BaseModel):
    """
    A condition checked opportunistically after every step, independent of
    where in the flow it happens. This is how the artifact expresses
    "a session-timeout page can appear anywhere" without duplicating that
    logic onto every single step.
    """
    id: str
    description: str
    detect: Locator
    classification: OutcomeClass
    outcome_code: str = Field(..., description="Stable machine-readable code, e.g. MEMBER_NOT_FOUND.")
    dismiss_action: Optional[Locator] = Field(
        None, description="For RECOVERABLE: control to click to clear the interrupt (e.g. 'Continue', 'Retry')."
    )
    resume_current_step: bool = Field(
        True, description="For RECOVERABLE: after dismissing, retry the step that was interrupted."
    )
    max_occurrences: int = Field(2, description="Cap on how many times this rule may fire in one replay.")
    extract: List[ParamSpec] = Field(
        default_factory=list, description="Outputs to populate when this rule fires (e.g. an error message)."
    )


class Checkpoint(BaseModel):
    """Final success condition -- proof the goal was actually reached, not just that clicks happened."""
    description: str
    detect: Locator


class TargetFingerprint(BaseModel):
    """
    Lightweight signals used to detect drift / mismatch between the app this
    artifact was recorded against and the app it's about to be replayed
    against (e.g. a different tenant's version of the same vendor product).
    """
    app_key: str = Field(..., description="Logical app identifier, e.g. 'sterling-core-member-services'.")
    vendor_version: Optional[str] = None
    landmark_text: Optional[str] = Field(
        None, description="Stable page text (e.g. a banner/title) expected to be present; mismatch => warn."
    )


class TargetSpec(BaseModel):
    entry_url: str
    surface_type: SurfaceType = SurfaceType.LEGACY_WEB
    fingerprint: TargetFingerprint
    tenant_id: Optional[str] = Field(
        None, description="None = tenant-agnostic base artifact. Set when specialized/overridden per tenant."
    )


class DiscoveryMetadata(BaseModel):
    discovery_run_id: str
    goal_text: str
    model: str
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# --------------------------------------------------------------------------- #
# Top-level artifact
# --------------------------------------------------------------------------- #

class CapabilityArtifact(BaseModel):
    schema_version: str = "1.0"
    capability_id: str
    name: str
    description: str
    version: str = "1.0.0"

    target: TargetSpec
    discovery: DiscoveryMetadata

    input_params: List[ParamSpec] = Field(default_factory=list)
    output_params: List[ParamSpec] = Field(default_factory=list)

    steps: List[Step]
    interrupts: List[InterruptRule] = Field(default_factory=list)
    checkpoint: Checkpoint

    @field_validator("capability_id")
    @classmethod
    def _valid_slug(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9_-]*$", v):
            raise ValueError(f"capability_id {v!r} must be a lowercase slug")
        return v

    @property
    def risk_summary(self) -> RiskLevel:
        levels = [s.risk for s in self.steps]
        if RiskLevel.IRREVERSIBLE in levels:
            return RiskLevel.IRREVERSIBLE
        if RiskLevel.REVERSIBLE in levels:
            return RiskLevel.REVERSIBLE
        return RiskLevel.SAFE

    @property
    def sensitive_param_names(self) -> List[str]:
        return [p.name for p in self.input_params if p.sensitive]

    def to_json(self, **kwargs) -> str:
        return self.model_dump_json(indent=2, **kwargs)

    @classmethod
    def from_json_file(cls, path: str) -> "CapabilityArtifact":
        with open(path, "r") as f:
            return cls.model_validate_json(f.read())
