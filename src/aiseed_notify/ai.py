"""The report contract: the strict schema the model fills in, and how it renders.

The agent owns the model calls (see agent.py); this is only the shape of the answer.
"""

from __future__ import annotations


def schema(verdicts: list[str]) -> dict:
    """Strict JSON schema for a report: one verdict, a few bullets, one thing to check."""
    return {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": verdicts},
            "bullets": {"type": "array", "items": {"type": "string"}},
            "check_first": {"type": "string"},
        },
        "required": ["verdict", "bullets", "check_first"],
        "additionalProperties": False,
    }


def summary_text(result: dict) -> str:
    lines = [f"- {b.strip()}" for b in result["bullets"]]
    if result.get("check_first"):
        lines += ["", f"Check first: {result['check_first'].strip()}"]
    return "\n".join(lines)
