"""The hub side: connect to every subscribed service and expose their tools as one set.

A service subscribes by running an MCP server and getting an entry in the registry
(`configs/services.yaml`). It never learns about Discord, OpenAI or notification policy.

Tool names are namespaced `<service>__<tool>` so two services can each have a
`disk_usage` without colliding.

**One session per operation, deliberately.** The MCP transport runs an anyio task group,
and a task group must be exited by the task that entered it. Holding sessions open in an
AsyncExitStack across a loop violates that ("Attempted to exit cancel scope in a
different task") and lets one dead service poison every other connection at teardown.
Opening a fresh session inside a single `async with` keeps enter and exit in the same
task. Over loopback that costs a few milliseconds per call, and these servers are
stateless health probes, so there is no session state to preserve.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from .errors import ConfigError

SEP = "__"
DEFAULT_TIMEOUT = 15.0


@dataclass
class Tool:
    """One callable tool, already namespaced."""

    name: str  # e.g. "seedcam__file_counts"
    service: str
    tool: str  # the bare name on the server
    description: str
    input_schema: dict


def describe(exc: BaseException) -> str:
    """Flatten an ExceptionGroup to its leaf causes.

    anyio wraps transport failures in a task group, so the outer message is always
    "unhandled errors in a TaskGroup" - useless in a Discord alert. The leaf is the part
    a human needs ("ConnectError: All connection attempts failed").
    """
    leaves: list[str] = []

    def walk(e: BaseException) -> None:
        inner = getattr(e, "exceptions", None)
        if inner:
            for sub in inner:
                walk(sub)
        else:
            leaves.append(f"{type(e).__name__}: {e}".strip().rstrip(": "))

    walk(exc)
    return "; ".join(dict.fromkeys(leaves))[:300] or type(exc).__name__


@asynccontextmanager
async def session_for(url: str, timeout: float = DEFAULT_TIMEOUT):
    """An initialized MCP session. Entered and exited within one task."""
    async with create_mcp_http_client(timeout=httpx2.Timeout(timeout)) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session


@dataclass
class Registry:
    """Every subscribed service, and whatever we could reach."""

    services: dict = field(default_factory=dict)
    timeout: float = DEFAULT_TIMEOUT
    tools: list[Tool] = field(default_factory=list)
    unreachable: dict[str, str] = field(default_factory=dict)

    async def call(self, name: str, arguments: dict) -> str:
        """Call a namespaced tool and return its result as text for the model."""
        service, _, tool = name.partition(SEP)
        entry = self.services.get(service)
        if entry is None:
            return json.dumps({"error": f"unknown service '{service}'"})
        try:
            async with session_for(entry["url"], self.timeout) as session:
                result = await session.call_tool(tool, arguments or {})
        except Exception as exc:  # a failing tool must not end the investigation
            return json.dumps({"error": describe(exc)})

        structured = getattr(result, "structured_content", None)
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False, default=str)
        texts = [c.text for c in result.content if getattr(c, "text", None)]
        return "\n".join(texts) if texts else json.dumps({"result": "no content"})


def load_registry(path: str | Path) -> dict:
    """Parse configs/services.yaml."""
    try:
        raw = yaml.safe_load(Path(path).read_text()) or {}
    except FileNotFoundError:
        raise ConfigError(f"no such registry: {path}")
    services = raw.get("services")
    if not isinstance(services, dict) or not services:
        raise ConfigError(f"{path}: expected a non-empty 'services' mapping")
    for name, entry in services.items():
        if not isinstance(entry, dict) or not entry.get("url"):
            raise ConfigError(f"{path}: service '{name}' needs a 'url'")
    return services


async def discover(services: dict, timeout: float = DEFAULT_TIMEOUT) -> Registry:
    """List the tools of every service. An unreachable one is recorded, not raised.

    A service being down is a finding the agent should report, not a crash.
    """
    registry = Registry(services=services, timeout=timeout)
    for name, entry in services.items():
        try:
            async with session_for(entry["url"], timeout) as session:
                listed = await session.list_tools()
        except Exception as exc:
            registry.unreachable[name] = describe(exc)
            continue
        for t in listed.tools:
            registry.tools.append(
                Tool(
                    name=f"{name}{SEP}{t.name}",
                    service=name,
                    tool=t.name,
                    description=(t.description or "").strip(),
                    input_schema=t.input_schema or {"type": "object", "properties": {}},
                )
            )
    return registry
