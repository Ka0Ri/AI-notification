"""Post a notification to a Discord webhook."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .errors import DeliveryError

USER_AGENT = "ai-notification/0.1 (+https://github.com/aiseed/AI-notification)"


def post(cfg: dict, title: str, summary: str, footer: str) -> None:
    """Post the verdict and summary. Detailed tables stay in the journal.

    Raises DeliveryError if the notification did not reach Discord.
    """
    webhook = cfg.get("webhook")
    if not webhook:
        raise DeliveryError("notify.discord.webhook is empty (is DISCORD_WEBHOOK_URL set?)")

    content = "\n".join([f"**{title}**", summary, "", footer])
    limit = cfg.get("max_chars", 1900)
    if len(content) > limit:
        content = content[:limit] + "\n...(truncated)"

    body = {"content": content}
    if cfg.get("username"):
        body["username"] = cfg["username"]

    # Cloudflare fronts Discord and 403s (error 1010) on urllib's default
    # User-Agent, so send a real one.
    req = urllib.request.Request(
        webhook,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        urllib.request.urlopen(req, timeout=cfg.get("timeout", 30)).read()
    except urllib.error.HTTPError as exc:
        raise DeliveryError(f"Discord returned {exc.code}: {exc.read()[:200]!r}")
    except OSError as exc:
        raise DeliveryError(f"could not reach Discord: {exc}")
