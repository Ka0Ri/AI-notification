# Config reference

Three files, each with one job.

`${VAR}` anywhere is substituted from the environment. Secrets come from `env_file`, so
`notify` works from any directory and any service with no shell wrapper. An unset
variable becomes an empty string, so `--no-post` runs work without secrets.

| Variable | Effect |
|----------|--------|
| `NOTIFY_ENV_FILE` | Env file to load, overriding every config's `env_file`. |
| `NOTIFY_CONFIG_DIR` | Directory searched when a config is named rather than pathed. |
| `DISCORD_WEBHOOK_URL` | Where the daily verdict is posted. |
| `DISCORD_BOT_TOKEN` | The chat bot's token. Receiving needs a bot; the webhook is send-only. |
| `DISCORD_GUILD_ID` | Server `/ask` is registered in. Unset registers globally, which Discord may take an hour to publish. |

Values already in the real environment win over the env file, so a unit's
`Environment=` overrides the file.

## `configs/services.yaml` — the subscription list

```yaml
services:
  <name>:
    url: http://127.0.0.1:<port>/mcp   # required
    description: why the agent would call this service
```

`description` is passed to the model, so write it for a reader deciding whether to look
there. Tool names are namespaced `<name>__<tool>`, so two services may both expose
`disk_usage`.

A service that does not answer is recorded as unreachable and reported as a finding —
never silently omitted, and never fatal to the run.

## `configs/agent.yaml` — the agent

| Key | Required | Meaning |
|-----|----------|---------|
| `name` | yes | Names the systemd units (`notify-<name>.service`). |
| `description` | no | Unit description and report header. |
| `env_file` | no | `KEY=VALUE` file loaded before `${VAR}` expansion. |
| `registry` | no | Path to the subscription list. Default `configs/services.yaml`. |
| `ai` | yes | See below. |
| `notify.discord` | yes | Delivery. |
| `schedule` | for `install` | systemd timer settings. |

### `ai`

| Key | Default | Meaning |
|-----|---------|---------|
| `instruction` | — | How to investigate and what each verdict means. Required. |
| `model` | `gpt-5.4-mini` | OpenAI model id. |
| `verdicts` | `{OK: 0, WATCH: 1, ACTION: 1}` | Allowed verdicts, mapped to exit codes. |
| `max_turns` | `8` | Model round trips in the investigation. |
| `max_tool_calls` | `30` | Total tool calls across the run. |
| `max_result_chars` | `20000` | Truncation per tool result before it enters context. |
| `max_output_tokens` | `4000` | |
| `reasoning_effort` | `low` | |
| `max_bullets` | `4` | Bullets kept from the report. |
| `timeout` | `120` | Seconds per model call. |

`max_turns` and `max_tool_calls` are spend guards, not tuning knobs: a confused agent
would otherwise loop. Both are enforced and tested.

The run is two model calls minimum. The **investigate** phase has tools and no schema;
the **report** phase has the strict schema and no tools, so the verdict is written only
after the looking is done.

### `notify.discord`

| Key | Default | Meaning |
|-----|---------|---------|
| `webhook` | — | Use `${DISCORD_WEBHOOK_URL}`. |
| `username` | — | Webhook display name. |
| `title` | `{name} - {status}` | Placeholders: `name`, `status`. |
| `max_chars` | `1900` | Content truncated past this. |

Only the verdict, bullets and one footer line are posted. The tool transcript stays in
the journal (`journalctl -u notify-<name>`).

### `schedule`

| Key | Default | Meaning |
|-----|---------|---------|
| `on_calendar` | — | systemd `OnCalendar=`. Required for `install`. |
| `persistent` | `true` | Run on boot if the last trigger was missed. |
| `user` / `group` | — | `User=` / `Group=`. **Set these, or the unit runs as root.** |

## `configs/chat.yaml` — the on-demand agent

Same keys as the agent config, minus `notify` and `schedule` (it is a daemon, not a
timer) and plus `chat`. Its `ai.instruction` is the chat one: prose answers, narrow tool
use, no verdict. It is a separate file rather than a block in `agent.yaml` because one
config describes one run.

| Key | Default | Meaning |
|-----|---------|---------|
| `chat.token` | — | Use `${DISCORD_BOT_TOKEN}`. |
| `chat.guild_id` | — | Register `/ask` in one server, where it appears immediately. |
| `chat.allowed_channels` | `[]` | Channel ids that may ask. Empty allows every channel the command is visible in. |
| `chat.allowed_users` | `[]` | User ids that may ask. Empty allows everyone. |
| `chat.deadline` | `600` | Seconds before a question is abandoned. Discord stops accepting the deferred reply at 900. |
| `chat.max_reply_chars` | `1900` | A longer answer is split across messages. |

Both allowlists are the spend guard for a channel other people can see; `max_turns` and
`max_tool_calls` bound each individual question. A refusal is ephemeral, so only the
asker sees it.

`/ask` takes no privileged intent: a slash command carries its own text, so the bot
never receives a message it was not addressed with.

Run it as a daemon, not a timer — `notify install` refuses a config with no
`schedule.on_calendar`. Use the committed `service/notify-chat.service`.

## Writing tools for a subscribing service

- **The tool owns every fact.** Counts, sizes, percentages, gap hours and the matched
  time-of-day window are computed in code. The agent picks tools and writes prose; it
  never derives, recomputes or estimates a number. seedcam's `compare_days` returns the
  whole day-over-day diff in one call for exactly this reason — an unaligned window made
  a normal day read as a 19% collapse.
- **Return data, not verdicts.** The agent decides severity. A tool that returns
  "everything is fine" removes the agent's ability to disagree.
- **Never return a secret.** Results reach an external model and can reach Discord.
  seedcam redacts credentials from camera URLs before they leave the process.
- **Make failure a value, not an exception.** Return
  `{"available": false, "error": ...}` rather than raising, so a broken dependency
  becomes a finding instead of ending the investigation.
- **Take a date, and align the window yourself.** Today is only partly elapsed, so a
  tool comparing two days must cut both at the same hour rather than leaving that to the
  caller.
