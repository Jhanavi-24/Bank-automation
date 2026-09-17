from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    HARD_FAILURE = "hard_failure"
    CONFIRMATION_REQUIRED = "confirmation_required"
    DENIED_BY_OPERATOR = "denied_by_operator"


class ReplayResult(BaseModel):
    status: ReplayStatus
    capability_id: str
    outcome_code: Optional[str] = None       # set for BUSINESS_OUTCOME / some HARD_FAILURE cases
    outputs: Dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    failed_step_id: Optional[str] = None
    expected: Optional[str] = None
    observed: Optional[str] = None
    interrupts_fired: List[str] = Field(default_factory=list)
    evidence_dir: Optional[str] = None
    human_intervention: bool = False
