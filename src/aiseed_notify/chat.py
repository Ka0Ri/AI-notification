"""Discord slash commands: every one puts a question through the same investigation.

`/ask` carries the question as an argument. The commands in the config's `chat.commands`
carry a fixed question instead, so a routine check is one word rather than a typed sentence.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from datetime import date, timedelta

import discord
from discord import app_commands

from . import agent, mcp_client
from .errors import ConfigError

# Discord rejects a select menu with more than this many options.
CHOICE_LIMIT = 25


def refusal(cfg: dict, channel_id: int | None, user_id: int) -> str | None:
    """Why this caller may not ask, or None. An empty allowlist restricts nothing."""
    channels = cfg.get("allowed_channels") or []
    users = cfg.get("allowed_users") or []
    if channels and channel_id not in channels:
        return "This channel is not allowed to ask the agent."
    if users and user_id not in users:
        return "You are not allowed to ask the agent."
    return None


def body(answer: str, transcript: agent.Transcript) -> str:
    """The answer, then what a reader cannot see: what was called, and what never answered.

    The chain is in call order, arguments included, because the same tool called twice
    with different arguments is two different questions. An answer with no calls behind
    it is the model talking rather than the tools, which is worth showing.
    """
    chain = "\n".join(f"{i}. {call}" for i, call in enumerate(transcript.calls, 1))
    parts = [answer, f"```\n{chain}\n```" if chain else "(no tools called)"]
    if transcript.unreachable:
        parts.append(f"Unreachable: {', '.join(transcript.unreachable)}")
    return "\n\n".join(parts)


def split(text: str, limit: int) -> list[str]:
    """Discord rejects a message over 2000 characters, so break on line boundaries."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) > limit:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [text]


class Bot(discord.Client):
    """Registers the commands on startup, guild-scoped when configured so they appear at once."""

    def __init__(self, guild: discord.Object | None):
        super().__init__(intents=discord.Intents.none())
        self.guild = guild
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=self.guild)

    async def on_ready(self) -> None:
        scope = f"guild {self.guild.id}" if self.guild else "globally"
        names = ", ".join(f"/{c.name}" for c in self.tree.get_commands(guild=self.guild))
        print(f"connected as {self.user}, {names} registered {scope}", flush=True)


def dated(template: str) -> str:
    """Fill `{yesterday}` in a fixed question.

    A tool that takes a date needs a real one, and the model has no clock.
    """
    return template.replace("{yesterday}", (date.today() - timedelta(days=1)).isoformat())


def filled(template: str, values: dict[str, str]) -> str:
    """Fill `{yesterday}` and whatever the command declared in `args`."""
    text = dated(template)
    for name, value in values.items():
        if value is not None:
            text = text.replace("{" + name + "}", value)
    return text


async def choices(services: dict, spec: dict) -> tuple[list[discord.SelectOption], str | None]:
    """The values an argument offers, read straight from a tool. (options, error).

    A list of what exists is data, not a judgement, so it is fetched in code rather than
    paid for as a model turn.
    """
    raw = await mcp_client.Registry(services=services).call(spec["tool"], {})
    try:
        items = json.loads(raw)[spec["items"]]
        keep = (spec.get("where") or {}).items()
        items = [i for i in items if all(i.get(k) == v for k, v in keep)]
        return [
            discord.SelectOption(label=i[spec["value"]], description=str(i.get(spec["label"], "")))
            for i in items
        ], None
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], f"{spec['tool']} returned nothing to choose from: {raw[:200]}"


class Owned(discord.ui.View):
    """A view only the operator who ran the command may touch.

    The reply is public, so without this anyone reading the channel could move a camera
    on someone else's command.
    """

    def __init__(self, owner: int):
        super().__init__(timeout=300)
        self.owner = owner

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner:
            return True
        await interaction.response.send_message("This is not your command.", ephemeral=True)
        return False


class Choose(Owned):
    """Pick the argument's value from the list, then hand over to Confirm."""

    def __init__(self, owner: int, options: list[discord.SelectOption], placeholder: str, confirm):
        super().__init__(owner)
        self.confirm = confirm
        self.select = discord.ui.Select(placeholder=placeholder[:150], options=options)
        self.select.callback = self.picked
        self.add_item(self.select)

    async def picked(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.confirm(interaction, self.select.values[0])
        self.stop()


class Confirm(Owned):
    """Nothing happens until this is pressed. Cancel leaves the camera untouched."""

    def __init__(self, owner: int, value: str, run):
        super().__init__(owner)
        self.value = value
        self.run = run

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(view=None)
        self.stop()
        await self.run(interaction, self.value)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled - nothing was changed.", view=None)
        self.stop()


def _parameter(arg: dict) -> inspect.Parameter:
    """One Discord argument. An omitted optional one is what opens the picker."""
    if arg.get("optional"):
        return inspect.Parameter(
            arg["name"], inspect.Parameter.KEYWORD_ONLY, annotation=str | None, default=None
        )
    return inspect.Parameter(arg["name"], inspect.Parameter.KEYWORD_ONLY, annotation=str)


def _register_fixed(client: Bot, guild, entry: dict, gate, answer, services: dict) -> None:
    """One command asking the config's fixed question, with the arguments it declared.

    A function of its own so each entry closes over its own `entry`. The signature is
    built here because Discord reads the parameters off the callback, and the config
    only learns them at load time.

    An argument carrying `choices` may be left out: the list is then read from the tool
    it names and offered as a menu, so an operator who does not know what exists can
    still run the command. `confirm` beside it holds the command until it is pressed.
    """
    args = entry.get("args") or []
    chooser = next((a for a in args if a.get("choices")), None)

    async def fixed(interaction: discord.Interaction, **values: str) -> None:
        if not await gate(interaction):
            return
        await interaction.response.defer(thinking=True)

        async def run(inter: discord.Interaction, chosen: str | None) -> None:
            picked = {**values, chooser["name"]: chosen} if chooser else values
            # Dated per invocation: the daemon outlives the day it started.
            await answer(inter, entry["name"], filled(entry["prompt"], picked))

        async def stage(inter: discord.Interaction, chosen: str | None) -> None:
            """Show what is about to happen, and wait for the button."""
            spec = (chooser or {}).get("confirm")
            if not spec:
                await run(inter, chosen)
                return
            detail = await mcp_client.Registry(services=services).call(
                spec["tool"], {spec["argument"]: chosen}
            )
            await inter.edit_original_response(
                content=f"`/{entry['name']}` on **{chosen}** - now at:\n```\n{detail[:800]}\n```",
                view=Confirm(inter.user.id, chosen, run),
            )

        if not chooser or values.get(chooser["name"]):
            await stage(interaction, values.get(chooser["name"]) if chooser else None)
            return

        options, error = await choices(services, chooser["choices"])
        if error or not options:
            await interaction.followup.send(error or f"no {chooser['name']} to choose from")
        elif len(options) > CHOICE_LIMIT:
            await interaction.followup.send(
                f"{len(options)} to choose from, over Discord's {CHOICE_LIMIT} - "
                f"pass `{chooser['name']}` directly."
            )
        else:
            await interaction.followup.send(
                f"Which {chooser['name']}?",
                view=Choose(interaction.user.id, options, chooser["description"], stage),
            )

    fixed.__signature__ = inspect.Signature(
        [inspect.Parameter("interaction", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=discord.Interaction)]
        + [_parameter(a) for a in args]
    )
    if args:
        app_commands.describe(**{a["name"]: a["description"] for a in args})(fixed)
    client.tree.command(name=entry["name"], description=entry["description"], guild=guild)(fixed)


def build(cfg: dict, services: dict) -> Bot:
    """Wire the commands to the agent. Split from serve() so it can be built without a token."""
    chat = cfg.get("chat") or {}
    ai_cfg = {**cfg["ai"], "name": cfg["name"]}
    guild = discord.Object(id=int(chat["guild_id"])) if chat.get("guild_id") else None
    client = Bot(guild)

    async def gate(interaction: discord.Interaction) -> bool:
        """Whether this caller may spend money here. A refusal is ephemeral."""
        why = refusal(chat, interaction.channel_id, interaction.user.id)
        if why:
            await interaction.response.send_message(why, ephemeral=True)
        return why is None

    async def answer(interaction: discord.Interaction, label: str, question: str) -> None:
        """Investigate and post the reply. The interaction must already be responded to."""
        try:
            answer, error, transcript = await asyncio.wait_for(
                agent.ask(ai_cfg, services, question), timeout=chat["deadline"]
            )
        except asyncio.TimeoutError:
            answer, error, transcript = None, f"gave up after {chat['deadline']}s", agent.Transcript()

        text = body(answer, transcript) if answer else f"Could not answer: {error}"
        print(
            f"/{label} {interaction.user}: {question!r} -> "
            f"{len(transcript.calls)} calls, {error or 'ok'}",
            flush=True,
        )
        for chunk in split(text, chat["max_reply_chars"]):
            await interaction.followup.send(chunk)

    async def respond(interaction: discord.Interaction, label: str, question: str) -> None:
        if not await gate(interaction):
            return
        # An investigation runs far longer than Discord's 3-second reply window.
        await interaction.response.defer(thinking=True)
        await answer(interaction, label, question)

    @client.tree.command(
        name="ask",
        description="Ask the monitoring agent about the subscribed services",
        guild=guild,
    )
    @app_commands.describe(question="What to check, e.g. is the NAS backup healthy?")
    async def ask(interaction: discord.Interaction, question: str) -> None:
        await respond(interaction, "ask", question)

    for entry in chat.get("commands") or []:
        _register_fixed(client, guild, entry, gate, answer, services)

    return client


def serve(cfg: dict, services: dict) -> None:
    """Connect and stay connected. Returns only when the gateway closes."""
    token = (cfg.get("chat") or {}).get("token")
    if not token:
        raise ConfigError("chat.token is empty (is DISCORD_BOT_TOKEN set?)")
    build(cfg, services).run(token)
