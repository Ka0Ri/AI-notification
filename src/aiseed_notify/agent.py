"""The agent: call the subscribed services' tools in a loop, then answer.

`run` is the daily check and ends with a verdict for Discord. `ask` answers one question
from `/ask` in prose. Both read their parameters and prompts from the config's `ai` block.
"""

from __future__ import annotations

import json
import os

from dataclasses import dataclass, field

from . import ai, mcp_client


@dataclass
class Transcript:
    """What the agent did, for the journal and for --show-transcript."""

    calls: list[str] = field(default_factory=list)
    turns: int = 0
    unreachable: dict[str, str] = field(default_factory=dict)
    tools_available: int = 0


def _openai_tools(tools: list[mcp_client.Tool]) -> list[dict]:
    """MCP tool definitions as OpenAI function tools. The JSON Schema carries over."""
    return [
        {
            "type": "function",
            "name": t.name,
            "description": f"[{t.service}] {t.description}",
            "parameters": t.input_schema,
        }
        for t in tools
    ]


def _context(registry: mcp_client.Registry, services: dict) -> str:
    lines = ["Subscribed services:"]
    for name, entry in services.items():
        note = registry.unreachable.get(name)
        state = f"UNREACHABLE - {note}" if note else "reachable"
        lines.append(f"- {name}: {entry.get('description', '')} [{state}]")
    return "\n".join(lines)


async def investigate(
    cfg: dict, registry: mcp_client.Registry, services: dict, opening: str
) -> tuple[list, Transcript]:
    """Run the bounded tool loop. Returns the conversation and what happened."""
    from openai import OpenAI

    client = OpenAI(timeout=cfg["timeout"])
    tools = _openai_tools(registry.tools)
    transcript = Transcript(unreachable=dict(registry.unreachable), tools_available=len(tools))

    conversation: list = [
        {"role": "user", "content": f"{_context(registry, services)}\n\n{opening}"}
    ]
    calls_made = 0

    for turn in range(cfg["max_turns"]):
        transcript.turns = turn + 1
        resp = client.responses.create(
            model=cfg["model"],
            instructions=cfg["instruction"],
            input=conversation,
            tools=tools,
            max_output_tokens=cfg["max_output_tokens"],
            reasoning={"effort": cfg["reasoning_effort"]},
        )
        conversation += resp.output

        pending = [item for item in resp.output if getattr(item, "type", None) == "function_call"]
        if not pending:
            break

        for call in pending:
            if calls_made >= cfg["max_tool_calls"]:
                output = json.dumps({"error": "tool call budget exhausted"})
            else:
                calls_made += 1
                args = json.loads(call.arguments or "{}")
                output = await registry.call(call.name, args)
                transcript.calls.append(f"{call.name}({call.arguments})")
            conversation.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output[: cfg["max_result_chars"]],
                }
            )

    return conversation, transcript


def report(cfg: dict, conversation: list, name: str) -> tuple[dict | None, str | None]:
    """Final call: no tools, strict schema. (result, error) - never raises."""
    from openai import OpenAI

    try:
        resp = OpenAI(timeout=cfg["timeout"]).responses.create(
            model=cfg["model"],
            instructions=cfg["instruction"],
            input=conversation + [{"role": "user", "content": cfg["report_prompt"]}],
            max_output_tokens=cfg["max_output_tokens"],
            reasoning={"effort": cfg["reasoning_effort"]},
            text={
                "format": {
                    "type": "json_schema",
                    "name": name.replace("-", "_"),
                    "schema": ai.schema(cfg["verdicts"]),
                    "strict": True,
                }
            },
        )
        result = json.loads(resp.output_text)
        bullets = [b.strip() for b in result.get("bullets", []) if b.strip()]
        bullets = [b for b in bullets if not b.lstrip("-* ").lower().startswith(("check first", "check_first"))]
        result["bullets"] = bullets[: cfg["max_bullets"]]
        if not result["bullets"]:
            return None, f"{cfg['model']} returned no findings"
        return result, None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]


def final_text(conversation: list) -> str:
    """The model's last prose message. Empty if it never wrote one."""
    for item in reversed(conversation):
        if getattr(item, "type", None) == "message":
            return "".join(c.text for c in item.content if getattr(c, "text", None)).strip()
    return ""


async def _connect(cfg: dict, services: dict) -> tuple[mcp_client.Registry | None, str | None, Transcript]:
    """Preflight shared by both entry points. (registry, error, transcript).

    These are runtime conditions, which are findings rather than mistakes in the config.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return None, "OPENAI_API_KEY is not set", Transcript()

    registry = await mcp_client.discover(services)
    if not registry.tools:
        detail = "; ".join(f"{k}: {v}" for k, v in registry.unreachable.items())
        return (
            None,
            f"no tools available - every service unreachable ({detail})",
            Transcript(unreachable=dict(registry.unreachable)),
        )
    return registry, None, Transcript()


async def run(cfg: dict, services: dict) -> tuple[dict | None, str | None, Transcript]:
    """Connect, investigate, report. (result, error, transcript)."""
    registry, error, transcript = await _connect(cfg, services)
    if error:
        return None, error, transcript

    conversation, transcript = await investigate(cfg, registry, services, cfg["opening"])
    result, error = report(cfg, conversation, cfg["name"])
    return result, error, transcript


async def ask(cfg: dict, services: dict, question: str) -> tuple[str | None, str | None, Transcript]:
    """Answer one question with the same tool loop, in prose. (answer, error, transcript).

    No report phase: a question wants an answer, not a verdict, and the strict schema
    would flatten it into bullets.
    """
    registry, error, transcript = await _connect(cfg, services)
    if error:
        return None, error, transcript

    conversation, transcript = await investigate(cfg, registry, services, question)
    answer = final_text(conversation)
    if not answer:
        return None, f"{cfg['model']} returned no answer", transcript
    return answer, None, transcript
