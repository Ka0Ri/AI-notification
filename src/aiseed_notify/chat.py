"""Discord slash command: `/ask` puts a question through the same investigation.

The daily agent runs on a timer and cannot be asked anything - it posts once and exits.
This is the other direction: a question typed in Discord opens the same tool loop, so
"is the NAS backup healthy?" is answered by calling the services' tools now.

A bot token, not a webhook: receiving needs a gateway connection. Slash commands carry
their own text, so no privileged intent is needed and the bot never sees a message it
was not addressed with.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands

from . import agent
from .errors import ConfigError

DEFAULTS = {
    # Discord accepts a deferred reply for 15 minutes; this also caps spend per question.
    "deadline": 600,
    "max_reply_chars": 1900,
}


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
    """The answer, plus the one thing a reader cannot see: services that never answered."""
    if transcript.unreachable:
        return f"{answer}\n\nUnreachable: {', '.join(transcript.unreachable)}"
    return answer


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
    """Registers `/ask` on startup, guild-scoped when configured so it appears at once."""

    def __init__(self, guild: discord.Object | None):
        super().__init__(intents=discord.Intents.none())
        self.guild = guild
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync(guild=self.guild)

    async def on_ready(self) -> None:
        scope = f"guild {self.guild.id}" if self.guild else "globally"
        print(f"connected as {self.user}, /ask registered {scope}", flush=True)


def build(cfg: dict, services: dict) -> Bot:
    """Wire `/ask` to the agent. Split from serve() so it can be built without a token."""
    chat = {**DEFAULTS, **(cfg.get("chat") or {})}
    ai_cfg = {**cfg["ai"], "name": cfg["name"]}
    guild = discord.Object(id=int(chat["guild_id"])) if chat.get("guild_id") else None
    client = Bot(guild)

    @client.tree.command(
        name="ask",
        description="Ask the monitoring agent about the subscribed services",
        guild=guild,
    )
    @app_commands.describe(question="What to check, e.g. is the NAS backup healthy?")
    async def ask(interaction: discord.Interaction, question: str) -> None:
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
            f"/ask {interaction.user}: {question!r} -> "
            f"{len(transcript.calls)} calls, {error or 'ok'}",
            flush=True,
        )
        for chunk in split(text, chat["max_reply_chars"]):
            await interaction.followup.send(chunk)

    return client


def serve(cfg: dict, services: dict) -> None:
    """Connect and stay connected. Returns only when the gateway closes."""
    token = (cfg.get("chat") or {}).get("token")
    if not token:
        raise ConfigError("chat.token is empty (is DISCORD_BOT_TOKEN set?)")
    build(cfg, services).run(token)
