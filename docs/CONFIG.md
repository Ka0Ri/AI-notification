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

**Every key is required — there are no defaults in the code.** A config is the whole
definition of its run, so a missing key raises `ConfigError` naming it, before the first
model call is paid for. The chat config needs only the keys the ask path reads: no
`verdicts`, `max_bullets`, `opening` or `report_prompt`.

| Key | Meaning | Read by |
|-----|---------|---------|
| `instruction` | How to investigate and what each verdict means. | both |
| `model` | OpenAI model id. | both |
| `timeout` | Seconds per model call. | both |
| `max_turns` | Model round trips in the investigation. | both |
| `max_tool_calls` | Total tool calls across the run. | both |
| `max_output_tokens` | | both |
| `reasoning_effort` | | both |
| `max_result_chars` | Truncation per tool result before it enters context. | both |
| `opening` | The message that opens the daily investigation. | `agent` |
| `report_prompt` | Closes the investigation and opens the report phase. | `agent` |
| `verdicts` | List of verdicts the model may return. | `agent` |
| `max_bullets` | Bullets kept from the report. | `agent` |

`verdicts` is the report schema's `enum`, so `instruction` must define every name listed.
It is a list, not a mapping: the verdict is the report's content and never the run's exit
code (see below).

`max_turns` and `max_tool_calls` are spend guards, not tuning knobs: a confused agent
would otherwise loop. Both are enforced and tested.

Both prompts live here rather than in `agent.py` for the same reason as the numbers — a
run that cannot be reproduced from its config file is not defined by it.

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

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | The agent reached a verdict and delivered it. Any verdict, including `ACTION`. |
| `2` | The run failed: no verdict, an incomplete config, or Discord refused the post. |

The verdict does not affect the exit code. Fleet health is what Discord reports; the
unit's state answers a different question — whether the monitor itself worked. Mapping
`WATCH`/`ACTION` to a non-zero exit made systemd log a successful run as
`Failed to start notify-agent.service`, which hid the case that matters: the agent dying
without posting anything. To alert on unit failure, use `OnFailure=` on the unit — not
the reporting run's exit status.

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
| `chat.deadline` | required | Seconds before a question is abandoned. Discord stops accepting the deferred reply at 900. |
| `chat.max_reply_chars` | required | A longer answer is split across messages. |

Both allowlists are the spend guard for a channel other people can see; `max_turns` and
`max_tool_calls` bound each individual question. A refusal is ephemeral, so only the
asker sees it.

`chat.commands` is a list of commands, each a `name`, a `description` and the `prompt`
that becomes the question. `{yesterday}` in a prompt becomes a real date when the command
runs. An entry may also carry `args`, a list of `name`/`description` pairs: each becomes a
Discord argument and replaces `{name}` in the prompt, which is what lets a command act on
a target the operator names rather than ask a fixed question.

An argument may add `optional: true` plus `choices` and `confirm`:

```yaml
args:
  - name: camera
    description: Camera id. Leave it out to choose from a list.
    optional: true
    choices:
      tool: seedcam__cameras         # namespaced tool called with no arguments
      items: cameras                 # key in its JSON holding the list
      value: id                      # field used as the value
      label: site                    # field shown beside it
      where: {type: ptz}             # rows to keep
    confirm:
      tool: seedcam__ptz_position    # called with {argument: <chosen value>}
      argument: camera               # and its result shown before the command runs
```

Omitting the argument makes the bot call `choices.tool` and offer the rows as a menu;
`confirm` then holds the command behind a Confirm/Cancel button showing that tool's
result for the chosen value. Both are called in code rather than by the model: a list of
what exists is data, not a judgement. Only the caller may use the menu or the buttons.
Discord caps a menu at 25 options — a longer list is reported, never truncated.

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
