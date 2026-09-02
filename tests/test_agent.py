"""The agent's tool plumbing and its spending bounds.

The reasoning itself is only meaningful against the real model, but the loop mechanics -
tool conversion, turn/call budgets, unreachable-service handling - are what protect the
run from spending unboundedly or reporting a dead service as healthy.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from aiseed_notify import agent, mcp_client


def _tool(name, service="svc", schema=None):
    return mcp_client.Tool(
        name=f"{service}__{name}",
        service=service,
        tool=name,
        description="does a thing",
        input_schema=schema or {"type": "object", "properties": {"x": {"type": "string"}}},
    )


class _Registry(mcp_client.Registry):
    """Records calls instead of making them."""

    def __init__(self, tools):
        super().__init__(services={"svc": {"url": "http://x/mcp"}}, tools=tools)
        self.seen = []

    async def call(self, name, arguments):
        self.seen.append((name, arguments))
        return json.dumps({"ok": name})


def _fn_call(name, call_id, args="{}"):
    return SimpleNamespace(type="function_call", name=name, call_id=call_id, arguments=args)


class _FakeOpenAI:
    """Emits scripted responses, one per turn."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0
        self.responses = SimpleNamespace(create=self._create)

    def __call__(self, *a, **kw):
        return self

    def _create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        out = self.scripts.pop(0) if self.scripts else []
        return SimpleNamespace(output=out, output_text="{}")


@pytest.fixture
def cfg():
    return {"instruction": "watch things", "model": "test-model", "verdicts": {"OK": 0, "ACTION": 1}}


def test_tool_conversion_preserves_schema():
    tools = agent._openai_tools([_tool("probe")])
    assert tools[0]["type"] == "function"
    assert tools[0]["name"] == "svc__probe"
    assert tools[0]["parameters"]["properties"] == {"x": {"type": "string"}}
    assert tools[0]["description"].startswith("[svc]")


def test_context_marks_unreachable_services():
    reg = mcp_client.Registry(unreachable={"dead": "ConnectError: refused"})
    text = agent._context(reg, {"dead": {"description": "a service"}, "live": {"description": "ok"}})
    assert "UNREACHABLE" in text and "ConnectError: refused" in text
    assert "live: ok [reachable]" in text


def test_loop_stops_when_the_model_stops_calling_tools(cfg, monkeypatch):
    fake = _FakeOpenAI([[_fn_call("svc__probe", "c1")], []])
    monkeypatch.setattr("openai.OpenAI", fake)
    reg = _Registry([_tool("probe")])
    _, t = asyncio.run(agent.investigate(cfg, reg, {"svc": {}}))
    assert t.turns == 2
    assert reg.seen == [("svc__probe", {})]


def test_max_turns_is_enforced(cfg, monkeypatch):
    """A model that never stops calling tools must still terminate."""
    fake = _FakeOpenAI([[_fn_call("svc__probe", f"c{i}")] for i in range(20)])
    monkeypatch.setattr("openai.OpenAI", fake)
    reg = _Registry([_tool("probe")])
    _, t = asyncio.run(agent.investigate({**cfg, "max_turns": 3}, reg, {"svc": {}}))
    assert t.turns == 3
    assert len(reg.seen) == 3


def test_max_tool_calls_is_enforced(cfg, monkeypatch):
    """The call budget bounds spend even inside a single turn."""
    burst = [_fn_call("svc__probe", f"c{i}") for i in range(5)]
    fake = _FakeOpenAI([burst, []])
    monkeypatch.setattr("openai.OpenAI", fake)
    reg = _Registry([_tool("probe")])
    conversation, _ = asyncio.run(agent.investigate({**cfg, "max_tool_calls": 2}, reg, {"svc": {}}))
    assert len(reg.seen) == 2
    exhausted = [c for c in conversation if isinstance(c, dict) and "budget exhausted" in str(c.get("output", ""))]
    assert len(exhausted) == 3


def test_tool_results_are_truncated(cfg, monkeypatch):
    """A huge tool result must not blow the context window."""

    class Fat(_Registry):
        async def call(self, name, arguments):
            return "x" * 5000

    fake = _FakeOpenAI([[_fn_call("svc__probe", "c1")], []])
    monkeypatch.setattr("openai.OpenAI", fake)
    conversation, _ = asyncio.run(
        agent.investigate({**cfg, "max_result_chars": 100}, Fat([_tool("probe")]), {"svc": {}})
    )
    outputs = [c["output"] for c in conversation if isinstance(c, dict) and c.get("type") == "function_call_output"]
    assert outputs == ["x" * 100]


def test_arguments_are_passed_through(cfg, monkeypatch):
    fake = _FakeOpenAI([[_fn_call("svc__probe", "c1", '{"x": "hello"}')], []])
    monkeypatch.setattr("openai.OpenAI", fake)
    reg = _Registry([_tool("probe")])
    asyncio.run(agent.investigate(cfg, reg, {"svc": {}}))
    assert reg.seen == [("svc__probe", {"x": "hello"})]


def test_run_refuses_without_an_instruction():
    result, error, _ = asyncio.run(agent.run({}, {"svc": {"url": "http://x"}}))
    assert result is None and "instruction" in error


def test_run_reports_when_every_service_is_down(monkeypatch):
    """MCP-only means no fallback, so this path must at least name what failed."""
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    async def all_dead(services, timeout=15.0):
        return mcp_client.Registry(unreachable={"svc": "ConnectError: refused"})

    monkeypatch.setattr(mcp_client, "discover", all_dead)
    result, error, t = asyncio.run(agent.run({"instruction": "x"}, {"svc": {"url": "http://x"}}))
    assert result is None
    assert "every service unreachable" in error and "ConnectError: refused" in error
    assert t.unreachable == {"svc": "ConnectError: refused"}
