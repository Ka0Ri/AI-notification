"""Discord slash commands: every one puts a question through the same investigation.

`/ask` carries the question as an argument. The commands in the config's `chat.commands`
carry a fixed question instead, so a routine check is one word rather than a typed sentence.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import discord
from discord import app_commands

from . import agent
from .errors import ConfigError


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


def _register_fixed(client: Bot, guild, respond, entry: dict) -> None:
    """One no-argument command asking the config's fixed question.

    A function of its own so each entry closes over its own `entry`.
    """

    @client.tree.command(name=entry["name"], description=entry["description"], guild=guild)
    async def fixed(interaction: discord.Interaction) -> None:
        # Dated per invocation: the daemon outlives the day it started.
        await respond(interaction, entry["name"], dated(entry["prompt"]))


def build(cfg: dict, services: dict) -> Bot:
    """Wire the commands to the agent. Split from serve() so it can be built without a token."""
    chat = cfg.get("chat") or {}
    ai_cfg = {**cfg["ai"], "name": cfg["name"]}
    guild = discord.Object(id=int(chat["guild_id"])) if chat.get("guild_id") else None
    client = Bot(guild)

    async def respond(interaction: discord.Interaction, label: str, question: str) -> None:
        why = refusal(chat, interaction.channel_id, interaction.user.id)
        if why:
            await interaction.response.send_message(why, ephemeral=True)
            return

        # An investigation runs far longer than Discord's 3-second reply window.
        await interaction.response.defer(thinking=True)
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

    @client.tree.command(
        name="ask",
        description="Ask the monitoring agent about the subscribed services",
        guild=guild,
    )
    @app_commands.describe(question="What to check, e.g. is the NAS backup healthy?")
    async def ask(interaction: discord.Interaction, question: str) -> None:
        await respond(interaction, "ask", question)

    for entry in chat.get("commands") or []:
        _register_fixed(client, guild, respond, entry)

    return client


def serve(cfg: dict, services: dict) -> None:
    """Connect and stay connected. Returns only when the gateway closes."""
    token = (cfg.get("chat") or {}).get("token")
    if not token:
        raise ConfigError("chat.token is empty (is DISCORD_BOT_TOKEN set?)")
    build(cfg, services).run(token)
