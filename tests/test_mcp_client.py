"""Registry parsing, error flattening, and a real round trip against a live server."""

import json
import socket
import threading
import time

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

from aiseed_notify import ConfigError, mcp_client


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server():
    """A real MCP server over HTTP, so the client is tested against the real transport."""
    server = MCPServer(name="fixture")

    @server.tool()
    def echo(value: str) -> dict:
        """Echo a value back."""
        return {"echoed": value}

    @server.tool()
    def boom() -> dict:
        """Always raises."""
        raise RuntimeError("tool exploded")

    port = _free_port()
    cfg = uvicorn.Config(server.streamable_http_app(streamable_http_path="/mcp"),
                         host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(cfg)
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "fixture server did not start"
    yield {"fixture": {"url": f"http://127.0.0.1:{port}/mcp"}}
    srv.should_exit = True


# ------------------------------------------------------------------ registry

def test_registry_requires_services(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("services: {}\n")
    with pytest.raises(ConfigError, match="non-empty 'services'"):
        mcp_client.load_registry(p)


def test_registry_requires_url(tmp_path):
    p = tmp_path / "r.yaml"
    p.write_text("services:\n  a: {description: x}\n")
    with pytest.raises(ConfigError, match="needs a 'url'"):
        mcp_client.load_registry(p)


def test_registry_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="no such registry"):
        mcp_client.load_registry(tmp_path / "nope.yaml")


# ------------------------------------------------------- error flattening

def test_describe_flattens_exception_groups():
    """anyio buries the real cause; the outer message is always about a TaskGroup."""
    inner = ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("refused")])
    outer = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    assert mcp_client.describe(outer) == "ConnectionError: refused"
    assert "TaskGroup" not in mcp_client.describe(outer)


def test_describe_dedupes_identical_leaves():
    grp = ExceptionGroup("x", [ConnectionError("refused"), ConnectionError("refused")])
    assert mcp_client.describe(grp) == "ConnectionError: refused"


def test_describe_plain_exception():
    assert mcp_client.describe(ValueError("bad")) == "ValueError: bad"


# ------------------------------------------------------------ round trip

@pytest.mark.anyio
async def test_discover_and_call(live_server):
    reg = await mcp_client.discover(live_server)
    assert reg.unreachable == {}
    assert {t.name for t in reg.tools} == {"fixture__echo", "fixture__boom"}
    assert json.loads(await reg.call("fixture__echo", {"value": "hi"})) == {"echoed": "hi"}


@pytest.mark.anyio
async def test_unreachable_service_is_recorded_not_raised():
    """A dead service is a finding for the agent to report, never a crash."""
    services = {"ghost": {"url": f"http://127.0.0.1:{_free_port()}/mcp"}}
    reg = await mcp_client.discover(services, timeout=3.0)
    assert reg.tools == []
    assert "ghost" in reg.unreachable
    assert "TaskGroup" not in reg.unreachable["ghost"]


@pytest.mark.anyio
async def test_one_dead_service_does_not_poison_a_live_one(live_server):
    """The bug that forced one session per call: a shared exit stack broke everything."""
    services = {**live_server, "ghost": {"url": f"http://127.0.0.1:{_free_port()}/mcp"}}
    reg = await mcp_client.discover(services, timeout=3.0)
    assert "ghost" in reg.unreachable
    assert len(reg.tools) == 2
    assert json.loads(await reg.call("fixture__echo", {"value": "still works"})) == {"echoed": "still works"}


@pytest.mark.anyio
async def test_failing_tool_returns_an_error_not_an_exception(live_server):
    reg = await mcp_client.discover(live_server)
    out = await reg.call("fixture__boom", {})
    assert "error" in out.lower()


@pytest.mark.anyio
async def test_unknown_service(live_server):
    reg = await mcp_client.discover(live_server)
    assert "unknown service" in (await reg.call("nope__thing", {}))
