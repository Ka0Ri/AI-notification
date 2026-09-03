"""The chat bot's gatekeeping and formatting.

The gateway itself is Discord's; what this repo owns is who may spend money on a
question, whether a long answer survives Discord's message cap, and whether an
unreachable service is still visible in a chat reply.
"""

import asyncio
import datetime

import discord
import pytest

from aiseed_notify import agent, chat


class _Response:
    async def defer(self, thinking=False):
        pass

    async def send_message(self, text, ephemeral=False):
        pass


class _Followup:
    async def send(self, text):
        pass


class _User:
    id = 7

    def __str__(self):
        return "tester"


class _Interaction:
    """Just the surface a command handler touches."""

    channel_id = 10
    user = _User()
    response = _Response()
    followup = _Followup()


@pytest.fixture
def cfg():
    return {
        "name": "chat",
        "ai": {"instruction": "answer things"},
        "chat": {"guild_id": 42, "deadline": 600, "max_reply_chars": 1900},
    }


def test_empty_allowlists_restrict_nothing():
    assert chat.refusal({}, channel_id=1, user_id=2) is None


def test_a_channel_outside_the_allowlist_is_refused():
    cfg = {"allowed_channels": [10, 11]}
    assert chat.refusal(cfg, channel_id=99, user_id=2) is not None
    assert chat.refusal(cfg, channel_id=10, user_id=2) is None


def test_a_user_outside_the_allowlist_is_refused():
    cfg = {"allowed_users": [7]}
    assert chat.refusal(cfg, channel_id=10, user_id=8) is not None
    assert chat.refusal(cfg, channel_id=10, user_id=7) is None


def test_an_unreachable_service_stays_visible_in_the_reply():
    t = agent.Transcript(unreachable={"seedcam": "ConnectError: refused"})
    assert "Unreachable: seedcam" in chat.body("/mnt/data is 78% used.", t)


def test_the_reply_shows_the_tool_chain_in_call_order():
    t = agent.Transcript(calls=['svc__compare_days({"date":null})', "svc__disk_usage({})"])
    out = chat.body("all fine", t)
    assert out.index("1. svc__compare_days") < out.index("2. svc__disk_usage")
    assert out.startswith("all fine")


def test_an_answer_with_no_tools_behind_it_says_so():
    """An answer built from no tool results is the model talking, not the fleet."""
    assert "(no tools called)" in chat.body("all fine", agent.Transcript())


def test_split_keeps_every_chunk_under_the_limit():
    text = "\n".join(f"line {i}" * 20 for i in range(40))
    chunks = chat.split(text, 200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks) == text


def test_split_breaks_a_single_overlong_line():
    chunks = chat.split("x" * 500, 200)
    assert [len(c) for c in chunks] == [200, 200, 100]


def test_split_leaves_a_short_answer_as_one_message():
    assert chat.split("all fine", 1900) == ["all fine"]


def test_build_registers_ask_on_the_configured_guild(cfg):
    client = chat.build(cfg, {"svc": {"url": "http://x/mcp"}})
    names = [c.name for c in client.tree.get_commands(guild=discord.Object(id=42))]
    assert names == ["ask"]


def test_a_configured_command_is_registered_beside_ask(cfg):
    """A routine check is an entry in the config, not a function in this module."""
    cfg["chat"]["commands"] = [
        {"name": "data-check", "description": "today's data", "prompt": "check today"}
    ]
    client = chat.build(cfg, {"svc": {"url": "http://x/mcp"}})
    names = [c.name for c in client.tree.get_commands(guild=discord.Object(id=42))]
    assert sorted(names) == ["ask", "data-check"]


def test_a_configured_command_takes_no_argument(cfg):
    """Its question comes from the config, so Discord must not ask the operator for one."""
    cfg["chat"]["commands"] = [
        {"name": "data-check", "description": "today's data", "prompt": "check today"}
    ]
    client = chat.build(cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("data-check", guild=discord.Object(id=42))
    assert command.parameters == []


def test_a_date_placeholder_becomes_a_real_date():
    """A tool needing a date must get one from the clock, not from the model."""
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    assert chat.dated("uploads for {yesterday} please") == f"uploads for {yesterday} please"


def test_a_prompt_without_a_placeholder_is_untouched():
    assert chat.dated("check today") == "check today"


def test_the_date_is_filled_when_the_command_runs_not_when_it_is_built(cfg, monkeypatch):
    """The daemon outlives the day it started, so a date frozen at build time goes stale."""
    cfg["chat"]["commands"] = [{"name": "nas-check", "description": "d", "prompt": "{yesterday}"}]
    asked = []

    async def fake_ask(ai_cfg, services, question):
        asked.append(question)
        return "answer", None, agent.Transcript()

    monkeypatch.setattr(agent, "ask", fake_ask)
    client = chat.build(cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("nas-check", guild=discord.Object(id=42))

    monkeypatch.setattr(chat, "dated", lambda template: template.replace("{yesterday}", "1999-12-31"))
    asyncio.run(command.callback(_Interaction()))
    assert asked == ["1999-12-31"]


def test_each_configured_command_keeps_its_own_prompt(cfg, monkeypatch):
    """Two entries must not share one closure over the loop variable."""
    cfg["chat"]["commands"] = [
        {"name": "one", "description": "d", "prompt": "first"},
        {"name": "two", "description": "d", "prompt": "second"},
    ]
    asked = []

    async def fake_ask(ai_cfg, services, question):
        asked.append(question)
        return "answer", None, agent.Transcript()

    monkeypatch.setattr(agent, "ask", fake_ask)
    client = chat.build(cfg, {"svc": {"url": "http://x/mcp"}})
    guild = discord.Object(id=42)
    for name in ("one", "two"):
        command = client.tree.get_command(name, guild=guild)
        asyncio.run(command.callback(_Interaction()))
    assert asked == ["first", "second"]


def test_serve_refuses_without_a_token(cfg):
    cfg["chat"]["token"] = ""
    with pytest.raises(Exception, match="DISCORD_BOT_TOKEN"):
        chat.serve(cfg, {"svc": {"url": "http://x/mcp"}})
