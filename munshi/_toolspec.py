"""Tiny helper for declaring tool schemas without repeating the JSON boilerplate."""

from __future__ import annotations

from .llm.base import ToolSpec


def spec(name: str, description: str, properties: dict,
         required: list[str] | None = None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required or [],
            "additionalProperties": False,
        },
    )
