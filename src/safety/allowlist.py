"""
Allowlist enforcement. Fail-closed: anything not explicitly listed is denied.

Both the discovery agent and the replay engine call `check_navigation` /
`check_action` before acting -- not just at the top of a run -- so a
capability that somehow drifted (or a compromised/hallucinated LLM step)
cannot walk itself outside the approved surface one step at a time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import yaml

from src.artifact.schema import ActionType, RiskLevel


class PolicyViolation(Exception):
    """Raised when an action would fall outside the configured allowlist."""


@dataclass
class Allowlist:
    allowed_domains: List[str] = field(default_factory=list)
    allowed_routes: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=list)
    irreversible_routes: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str) -> "Allowlist":
        with open(path, "r") as f:
            raw = yaml.safe_load(f) or {}
        return cls(
            allowed_domains=raw.get("allowed_domains", []),
            allowed_routes=raw.get("allowed_routes", []),
            allowed_actions=raw.get("allowed_actions", []),
            irreversible_routes=raw.get("irreversible_routes", []),
        )

    def check_action_type(self, action: ActionType) -> None:
        if action.value not in self.allowed_actions:
            raise PolicyViolation(f"Action type '{action.value}' is not in the allowlist.")

    def check_url(self, url: str) -> None:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if netloc not in self.allowed_domains:
            raise PolicyViolation(f"Domain '{netloc}' is not in the allowlist. URL: {url}")

        path = parsed.path or "/"
        if not any(re.fullmatch(pattern, path) for pattern in self.allowed_routes):
            raise PolicyViolation(f"Route '{path}' is not in the allowlist. URL: {url}")

    def is_irreversible_route(self, url: str) -> bool:
        path = urlparse(url).path or "/"
        return any(re.fullmatch(pattern, path) for pattern in self.irreversible_routes)

    def risk_for_url(self, url: str, action: ActionType) -> RiskLevel:
        if action in (ActionType.NAVIGATE, ActionType.WAIT_FOR, ActionType.EXTRACT, ActionType.ASSERT_CHECKPOINT):
            return RiskLevel.SAFE
        if self.is_irreversible_route(url):
            return RiskLevel.IRREVERSIBLE
        if action in (ActionType.FILL, ActionType.SELECT_OPTION):
            return RiskLevel.REVERSIBLE
        return RiskLevel.SAFE


DEFAULT_ALLOWLIST_PATH = str(Path(__file__).resolve().parents[2] / "config" / "allowlist.yaml")


def load_default_allowlist() -> Allowlist:
    return Allowlist.load(DEFAULT_ALLOWLIST_PATH)
