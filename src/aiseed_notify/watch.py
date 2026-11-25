"""Alarm watch: poll each service's `alarms` tool and post only what changed.

No model is involved. Each server decides what is wrong; this compares the answer with
the last check, so a problem fires once, stays silent while it lasts, and resolves once.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import mcp_client

ICON = {"critical": "🔴", "warning": "🟡"}


async def current(services: dict) -> tuple[dict, set]:
    """Every active alarm by namespaced id, and the subjects nobody could judge this time."""
    registry = await mcp_client.discover(services)
    found, quiet = {}, set()
    for name in services:
        if name in registry.unreachable:
            found[f"{name}:unreachable"] = {
                "subject": name, "severity": "critical",
                "message": f"{name} MCP server unreachable: {registry.unreachable[name]}",
            }
            # Its own alarms cannot be read, so they are held rather than resolved.
            quiet.add(f"{name}:*")
            continue
        if not any(t.service == name and t.tool == "alarms" for t in registry.tools):
            continue
        raw = await registry.call(f"{name}{mcp_client.SEP}alarms", {})
        try:
            data = json.loads(raw)
            alarms, held = data["alarms"], data.get("quiet", [])
        except (json.JSONDecodeError, KeyError, TypeError):
            found[f"{name}:alarms-failed"] = {
                "subject": name, "severity": "critical",
                "message": f"{name} alarms tool failed: {raw[:200]}",
            }
            quiet.add(f"{name}:*")
            continue
        for a in alarms:
            found[f"{name}:{a['id']}"] = {**a, "subject": f"{name}:{a['subject']}"}
        quiet.update(f"{name}:{s}" for s in held)
    return found, quiet


def step(state: dict, found: dict, quiet: set, confirm: int, now: str) -> tuple[list, list]:
    """Advance the state by one check. Returns (fired, resolved); `state` is updated in place.

    An alarm fires after `confirm` checks in a row and resolves after `confirm` clear ones,
    so one dropped probe neither pages nor clears. A held subject keeps its alarm as it is.
    """
    fired, resolved = [], []
    for key, alarm in found.items():
        entry = state.setdefault(key, {"since": now, "seen": 0, "fired": False})
        entry.update(alarm=alarm, seen=entry["seen"] + 1, clear=0)
        if not entry["fired"] and entry["seen"] >= confirm:
            entry["fired"] = True
            fired.append(entry)
    for key in [k for k in state if k not in found]:
        entry = state[key]
        subject = entry["alarm"]["subject"]
        if subject in quiet or f"{subject.split(':')[0]}:*" in quiet:
            continue
        if not entry["fired"]:
            del state[key]
            continue
        entry["clear"] = entry.get("clear", 0) + 1
        if entry["clear"] >= confirm:
            resolved.append(state.pop(key))
    return fired, resolved


def message(fired: list, resolved: list) -> str:
    lines = [f"{ICON.get(e['alarm']['severity'], '🔴')} {e['alarm']['message']} (since {e['since']})" for e in fired]
    lines += [f"🟢 resolved: {e['alarm']['message']} (since {e['since']})" for e in resolved]
    return "\n".join(lines)


def load_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(path)


def now_label() -> str:
    return datetime.now().strftime("%m-%d %H:%M")
