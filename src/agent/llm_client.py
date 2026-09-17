"""Thin wrapper around the Anthropic API for the discovery agent's decide step."""
from __future__ import annotations

import base64
import os
from typing import Any, Dict, List, Optional

import anthropic

from src.agent.tools import TOOLS

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")


class AgentLLM:
    def __init__(self, model: Optional[str] = None):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it before running a discovery run "
                "(see README.md 'Setup')."
            )
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or DEFAULT_MODEL

    def decide(self, system_prompt: str, messages: List[Dict[str, Any]]) -> anthropic.types.Message:
        return self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system_prompt,
            tools=TOOLS,
            tool_choice={"type": "any"},
            messages=messages,
        )


def image_block(png_bytes: bytes) -> Dict[str, Any]:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(png_bytes).decode("ascii"),
        },
    }


def text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}
