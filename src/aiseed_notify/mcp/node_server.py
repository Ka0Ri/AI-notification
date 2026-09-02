"""MCP server for host-level health: disk and systemd units.

The diagram's "check disk" box. These have no service of their own to live in, so they
live here and run as their own daemon.

Paths and units are allowlisted in the config: the agent gets to look at what the
operator listed, and nothing else.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml
from mcp.server.mcpserver import MCPServer

_allowed_paths: list[str] = []
_allowed_units: list[str] = []

server = MCPServer(
    name="node",
    instructions=(
        "Host-level health for the machine running the camera services: disk usage of the "
        "storage mounts and the state of systemd units. Call with no arguments to get "
        "everything that is allowlisted."
    ),
)


def _du(path: str) -> dict:
    try:
        du = shutil.disk_usage(path)
    except OSError as exc:
        return {"path": path, "error": str(exc)}
    return {
        "path": path,
        "total_gb": round(du.total / 1073741824, 1),
        "used_gb": round(du.used / 1073741824, 1),
        "free_gb": round(du.free / 1073741824, 1),
        "used_pct": round(du.used / du.total * 100),
    }


@server.tool()
def disk_usage(path: str | None = None) -> dict:
    """Disk usage of the storage mounts. Omit `path` for every allowlisted mount."""
    if path is None:
        return {"mounts": [_du(p) for p in _allowed_paths]}
    if path not in _allowed_paths:
        return {"error": f"path not allowlisted: {path}", "allowed": _allowed_paths}
    return {"mounts": [_du(path)]}


def _unit(name: str) -> dict:
    fields = ("ActiveState", "SubState", "Result", "ExecMainStatus", "ActiveEnterTimestamp")
    proc = subprocess.run(
        ["systemctl", "show", name, "--property=" + ",".join(fields)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    out = {"unit": name}
    for line in proc.stdout.strip().splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


@server.tool()
def systemd_status(unit: str | None = None) -> dict:
    """State of the monitored systemd units. Omit `unit` for every allowlisted unit."""
    if unit is None:
        return {"units": [_unit(u) for u in _allowed_units]}
    if unit not in _allowed_units:
        return {"error": f"unit not allowlisted: {unit}", "allowed": _allowed_units}
    return {"units": [_unit(unit)]}


def load_node_config(path: str | Path) -> dict:
    cfg = yaml.safe_load(Path(path).read_text()) or {}
    global _allowed_paths, _allowed_units
    _allowed_paths = list(cfg.get("disk_paths") or [])
    _allowed_units = list(cfg.get("systemd_units") or [])
    return cfg


def serve(config_path: str | Path) -> None:
    cfg = load_node_config(config_path)
    server.run(
        transport="streamable-http",
        host=cfg.get("host", "127.0.0.1"),
        port=cfg.get("port", 8932),
    )
