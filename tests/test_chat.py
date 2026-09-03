"""The chat bot's gatekeeping and formatting.

The gateway itself is Discord's; what this repo owns is who may spend money on a
question, whether a long answer survives Discord's message cap, and whether an
unreachable service is still visible in a chat reply.
"""

import discord
import pytest

from aiseed_notify import agent, chat


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


def test_serve_refuses_without_a_token(cfg):
    cfg["chat"]["token"] = ""
    with pytest.raises(Exception, match="DISCORD_BOT_TOKEN"):
        chat.serve(cfg, {"svc": {"url": "http://x/mcp"}})
