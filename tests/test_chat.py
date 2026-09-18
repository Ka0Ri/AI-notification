"""The chat bot's gatekeeping and formatting.

The gateway itself is Discord's; what this repo owns is who may spend money on a
question, whether a long answer survives Discord's message cap, and whether an
unreachable service is still visible in a chat reply.
"""

import asyncio
import datetime

import json

import discord
import pytest

from aiseed_notify import agent, chat, mcp_client


class _Response:
    def __init__(self):
        self.refusals = []
        self.edits = []

    async def defer(self, thinking=False):
        pass

    async def send_message(self, text, ephemeral=False):
        self.refusals.append(text)

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)


class _Followup:
    def __init__(self):
        self.sent = []

    async def send(self, text=None, view=None):
        self.sent.append((text, view))


class _User:
    id = 7

    def __str__(self):
        return "tester"


class _Interaction:
    """Just the surface a command handler touches."""

    channel_id = 10

    def __init__(self, user_id=7):
        self.user = _User()
        self.user.id = user_id
        self.response = _Response()
        self.followup = _Followup()
        self.edits = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


@pytest.fixture
def picker_cfg(cfg):
    """A command whose argument is filled from a tool rather than typed."""
    cfg["chat"]["commands"] = [
        {
            "name": "ptz",
            "description": "point a camera",
            "args": [
                {"name": "action", "description": "what to do"},
                {
                    "name": "camera",
                    "description": "Camera id. Leave it out to choose from a list.",
                    "optional": True,
                    "choices": {
                        "tool": "svc__camera_status",
                        "items": "cameras",
                        "value": "id",
                        "label": "site",
                        "where": {"type": "ptz"},
                    },
                    "confirm": {"tool": "svc__ptz_position", "argument": "camera"},
                },
            ],
            "prompt": "On {camera}: {action}.",
        }
    ]
    return cfg


FLEET = json.dumps(
    {
        "cameras": [
            {"id": "A/cam1", "site": "가", "type": "ptz"},
            {"id": "A/cam2", "site": "가", "type": "fixed"},
            {"id": "B/cam1", "site": "나", "type": "ptz"},
        ]
    }
)


def _serve(monkeypatch, results):
    """Answer every tool call from a dict, so no server is needed."""

    async def call(self, name, arguments):
        return results[name]

    monkeypatch.setattr(mcp_client.Registry, "call", call)


def _pick(view, value, interaction):
    """What Discord does when the operator chooses an option."""
    view.select._values = [value]
    asyncio.run(view.picked(interaction))


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


def test_a_configured_command_takes_the_arguments_it_declares(cfg):
    """An action needs a target, so a command may ask Discord for one."""
    cfg["chat"]["commands"] = [
        {
            "name": "ptz",
            "description": "move a camera",
            "args": [
                {"name": "camera", "description": "which camera"},
                {"name": "action", "description": "where to send it"},
            ],
            "prompt": "Move {camera}: {action}.",
        }
    ]
    client = chat.build(cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    assert [(p.name, p.description, p.required) for p in command.parameters] == [
        ("camera", "which camera", True),
        ("action", "where to send it", True),
    ]


def test_an_argument_reaches_the_prompt(cfg, monkeypatch):
    """What the operator typed must land in the question, not beside it."""
    cfg["chat"]["commands"] = [
        {
            "name": "ptz",
            "description": "move a camera",
            "args": [{"name": "camera", "description": "which camera"}],
            "prompt": "Move {camera} left.",
        }
    ]
    asked = []

    async def fake_ask(ai_cfg, services, question):
        asked.append(question)
        return "answer", None, agent.Transcript()

    monkeypatch.setattr(agent, "ask", fake_ask)
    client = chat.build(cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    asyncio.run(command.callback(_Interaction(), camera="site/ptz1"))
    assert asked == ["Move site/ptz1 left."]


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


def test_a_chooseable_argument_is_optional(picker_cfg):
    """Leaving it out is what opens the menu, so Discord must accept the command without it."""
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    assert [(p.name, p.required) for p in command.parameters] == [("action", True), ("camera", False)]


def test_omitting_it_offers_the_menu_the_tool_describes(picker_cfg, monkeypatch):
    """An operator who does not know what exists gets the list rather than a guess."""
    _serve(monkeypatch, {"svc__camera_status": FLEET})
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction()
    asyncio.run(command.callback(interaction, action="left", camera=None))

    text, view = interaction.followup.sent[-1]
    assert "Which camera?" in text
    assert [o.label for o in view.select.options] == ["A/cam1", "B/cam1"]


def test_a_filtered_out_camera_is_never_offered(picker_cfg, monkeypatch):
    """A fixed camera cannot be pointed, so offering it would only produce a failed move."""
    _serve(monkeypatch, {"svc__camera_status": FLEET})
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction()
    asyncio.run(command.callback(interaction, action="left", camera=None))
    assert "A/cam2" not in [o.label for o in interaction.followup.sent[-1][1].select.options]


def test_too_many_options_says_so_instead_of_dropping_some(picker_cfg, monkeypatch):
    """Silently truncating would hide cameras the operator is entitled to see."""
    fleet = {"cameras": [{"id": f"A/cam{i}", "site": "가", "type": "ptz"} for i in range(40)]}
    _serve(monkeypatch, {"svc__camera_status": json.dumps(fleet)})
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction()
    asyncio.run(command.callback(interaction, action="left", camera=None))

    text, view = interaction.followup.sent[-1]
    assert view is None and "40" in text and "camera" in text


def test_only_the_operator_who_asked_may_use_the_menu(picker_cfg, monkeypatch):
    """The reply is public, so otherwise a bystander could move a camera on their command."""
    _serve(monkeypatch, {"svc__camera_status": FLEET})
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction(user_id=7)
    asyncio.run(command.callback(interaction, action="left", camera=None))
    view = interaction.followup.sent[-1][1]

    stranger = _Interaction(user_id=99)
    assert asyncio.run(view.interaction_check(stranger)) is False
    assert asyncio.run(view.interaction_check(_Interaction(user_id=7))) is True


def test_choosing_stages_the_current_position_and_waits(picker_cfg, monkeypatch):
    """Nothing is asked of the model until Confirm: the choice alone must move nothing."""
    _serve(monkeypatch, {"svc__camera_status": FLEET, "svc__ptz_position": '{"pan": 0.5}'})
    asked = []

    async def fake_ask(ai_cfg, services, question):
        asked.append(question)
        return "answer", None, agent.Transcript()

    monkeypatch.setattr(agent, "ask", fake_ask)
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction()
    asyncio.run(command.callback(interaction, action="left", camera=None))

    chosen = _Interaction()
    _pick(interaction.followup.sent[-1][1], "B/cam1", chosen)
    staged = chosen.edits[-1]
    assert "B/cam1" in staged["content"] and '"pan": 0.5' in staged["content"]
    assert [b.label for b in staged["view"].children] == ["Confirm", "Cancel"]
    assert asked == []


def test_cancel_leaves_the_camera_alone(picker_cfg, monkeypatch):
    _serve(monkeypatch, {"svc__camera_status": FLEET, "svc__ptz_position": "{}"})
    asked = []

    async def fake_ask(ai_cfg, services, question):
        asked.append(question)
        return "answer", None, agent.Transcript()

    monkeypatch.setattr(agent, "ask", fake_ask)
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction()
    asyncio.run(command.callback(interaction, action="left", camera=None))
    chosen = _Interaction()
    _pick(interaction.followup.sent[-1][1], "B/cam1", chosen)

    pressed = _Interaction()
    asyncio.run(chosen.edits[-1]["view"].no.callback(pressed))
    assert asked == []
    assert "Cancelled" in pressed.response.edits[-1]["content"]


def test_confirm_runs_the_prompt_with_the_chosen_value(picker_cfg, monkeypatch):
    """What the operator picked must reach the question, not just the confirmation."""
    _serve(monkeypatch, {"svc__camera_status": FLEET, "svc__ptz_position": "{}"})
    asked = []

    async def fake_ask(ai_cfg, services, question):
        asked.append(question)
        return "answer", None, agent.Transcript()

    monkeypatch.setattr(agent, "ask", fake_ask)
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction()
    asyncio.run(command.callback(interaction, action="left", camera=None))
    chosen = _Interaction()
    _pick(interaction.followup.sent[-1][1], "B/cam1", chosen)

    asyncio.run(chosen.edits[-1]["view"].yes.callback(_Interaction()))
    assert asked == ["On B/cam1: left."]


def test_naming_the_camera_skips_the_menu_but_not_the_confirmation(picker_cfg, monkeypatch):
    """A move is a move however it was addressed."""
    _serve(monkeypatch, {"svc__ptz_position": "{}"})
    client = chat.build(picker_cfg, {"svc": {"url": "http://x/mcp"}})
    command = client.tree.get_command("ptz", guild=discord.Object(id=42))
    interaction = _Interaction()
    asyncio.run(command.callback(interaction, action="left", camera="B/cam1"))

    assert interaction.followup.sent == []
    assert "B/cam1" in interaction.edits[-1]["content"]
    assert [b.label for b in interaction.edits[-1]["view"].children] == ["Confirm", "Cancel"]
