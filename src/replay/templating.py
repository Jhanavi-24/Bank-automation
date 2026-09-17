"""Trivial {{param_name}} substitution -- intentionally not a full template
engine. Artifact step values are either a literal or a single param
reference; anything more expressive belongs in the discovery/replay code,
not in data that a human reviewer has to audit for safety."""
from __future__ import annotations

import re
from typing import Any, Dict

_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class MissingParamError(Exception):
    pass


def render(template: str, params: Dict[str, Any]) -> str:
    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in params:
            raise MissingParamError(f"Template references undeclared/missing param '{name}'")
        return str(params[name])

    return _PATTERN.sub(_sub, template)
