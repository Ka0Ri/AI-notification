# AI-notification

A notification channel with an agent behind it. Services **subscribe** by running an MCP
server that answers questions about their own health. This repo runs the MCP
**client**: an agent that calls those tools, follows up on anything odd, correlates
across services, and reports. It reports two ways - one verdict a day on a timer, and an
answer whenever someone asks `/ask` in Discord.


```mermaid
flowchart LR
    TIMER[["notify-agent.timer<br/>fires 21:00 daily"]] --> AGENT
    ASK[["/ask in Discord<br/>notify-chat.service"]] --> AGENT

    subgraph CLIENT["MCP CLIENT (this repo)"]
        REG[("configs/services.yaml<br/>registry of server URLs")]
        AGENT["agent.py<br/>investigate: tool loop<br/>run: verdict | ask: prose"]
        REG -. "where to connect" .-> AGENT
    end

    subgraph S1["MCP SERVER: seedcam.service<br/>127.0.0.1:8931/mcp"]
        T1["camera_status()"]
        T2["compare_days(date, baseline, until_hour)"]
        T3["disk_usage()"]
        T4["streaming_status()"]
    end

    AGENT ==> |"list_tools / call_tool"| S1
    AGENT --> |"reasoning"| AI["OpenAI API"]
    AGENT --> |"webhook | reply"| DISCORD(["Discord"])
    CC["Claude Code<br/>second MCP client"] -. "on demand" .-> S1
```

| | Role | Tools | Lifetime |
|---|---|---|---|
| `agent.py` + registry | **MCP client** — picks tools, writes prose, computes nothing | none; it calls them | oneshot, fired by the timer |
| `chat.py` | The same client behind a Discord `/ask` command | none; it calls them | daemon, always connected |
| `seedcam.service` | **MCP server** in a daemon thread beside the camera loop | 4, inside the live process | always on |
| Claude Code | **MCP client**, read-only, via `.mcp.json` | none | interactive |

Tools always live **on the servers**, never in the hub. Adding a server adds tools the
agent can reach; the hub itself never grows. Servers bind `127.0.0.1` only, so
there is no auth to get wrong. Transport is MCP streamable HTTP, one short-lived session
per call.

## Subscribing a service

Serve MCP from the service, then add it to `configs/services.yaml`:

```yaml
services:
  my-service:
    url: http://127.0.0.1:8933/mcp
    description: What this service is, so the agent knows why it would call it
```

That is the whole subscription — nothing in `configs/agent.yaml` changes. See
`src/seedcam/mcp_server.py` in the seedcam repo for a reference server, and
[docs/CONFIG.md](docs/CONFIG.md) for tool design rules and every config key.

## Command line

```bash
uv run notify services                            # which servers answer, and their tools
uv run notify agent --no-post --show-transcript   # investigate, print every tool call
uv run notify agent                               # investigate and post to Discord
uv run notify chat                                # stay connected, answer /ask
uv run notify install configs/agent.yaml          # regenerate the systemd timer
```

## The Discord bot

The timer answers one question a day, the one written into `configs/agent.yaml`.
`notify-chat.service` answers the ones you type. It holds a gateway connection, and every
`/ask` runs the same investigation - the same MCP tools, the same rule that tools own the
facts - scoped to what you asked, and replies in the channel.

### Commands

| Command | Argument | What happens |
|---------|----------|--------------|
| `/ask` | `question` (required) | Investigates and replies in the channel. Public - everyone who can see the channel sees the answer. |

One command, deliberately. The agent decides which tools a question needs, so a second
command would only be a worse way of saying the same thing.

```
/ask check the NAS data
/ask check NAS data in Naju today
/ask is any camera offline right now?
/ask which cameras dropped compared to yesterday?
```

The reply is prose, not a verdict: no OK/WATCH/ACTION label, no bullets. It quotes the
site and camera names and the exact numbers the tools returned, including the window they
cover, and says a number is unavailable rather than deriving one. If a subscribed service
did not answer, the reply says so instead of quietly leaving it out.

Discord is told to wait as soon as the command arrives, because an investigation takes
longer than the three seconds it allows; the answer replaces the "thinking" state a few
seconds later. Over `max_reply_chars` it is split across messages.

**Each question is independent.** Nothing is remembered between them, so a follow-up like
"and yesterday?" needs restating in full. That is a deliberate limit, not a gap - health
lookups are one-shot, and a per-channel history would grow context and cost with nothing
to show for it.

### Setup

1. Create an application at discord.com/developers, add a bot, copy its token.
   **Give the monitor an application of its own.** Two programs sharing one bot identity
   collide: Discord routes an interaction to whichever gateway session it picks, so
   commands land on the process that cannot serve them, and resetting the token for one
   program silently breaks the other.
2. Invite it with the `applications.commands` scope. No privileged intent is needed - a
   slash command carries its own text, so the bot never receives a message it was not
   addressed with.
3. Put `DISCORD_BOT_TOKEN` and `DISCORD_GUILD_ID` in the env file. `DISCORD_GUILD_ID`
   registers `/ask` in that one server, where it appears at once; without it registration
   is global and Discord may take an hour to publish it.
4. Install the unit:

```bash
sudo cp service/notify-chat.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now notify-chat
```

### Who may ask

`chat.allowed_channels` and `chat.allowed_users` in [configs/chat.yaml](configs/chat.yaml)
are the spend guard: every question costs OpenAI credit, so fill at least one of them if
the server has members who should not be able to start an investigation. Empty means
anyone who can see the command. A refusal is ephemeral - only the asker sees it.

Per question, `max_turns` and `max_tool_calls` bound the loop and `chat.deadline` abandons
one that will not finish. They are spend guards, not tuning knobs.

### Running it

```bash
journalctl -u notify-chat -f          # one line per question
sudo systemctl restart notify-chat    # after changing the token or the instruction
```

```
connected as botman#7395, /ask registered guild 1206993192386568283
/ask dangthanhvu.: 'check the NAS data' -> 2 calls, ok
```

The question, who asked, how many tools it took and whether it worked - the answer itself
stays in Discord. To rotate the token, replace the line in the env file and restart:
`/ask` is registered against the application, not the token, so it stays in the command
list and needs no re-registration.
