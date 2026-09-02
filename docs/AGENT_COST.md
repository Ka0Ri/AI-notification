# Agent run cost

2026-09-02 | `ab5d025` (this repo), `767e85f` (seedcam) | `configs/agent.yaml` | `gpt-5.4-mini`, `reasoning_effort: low`

## 1. What was run

Two consecutive `notify agent` runs against the live registry, measured by wrapping
`openai.resources.responses.Responses.create` to record `usage` per API call. No repo
code was changed; the wrapper lived in a throwaway script.

Neither run posted to Discord: the script called `agent.run` directly, bypassing
`cmd_agent`'s post step.

## 2. Inputs

| | |
|---|---|
| Services | `seedcam` :8931 (4 tools), `node` :8932 (2 tools) |
| Config bounds | `max_turns: 8`, `max_tool_calls: 30`, `max_output_tokens: 4000`, `max_result_chars: 20000` |
| seedcam thresholds | `drop_ratio: 0.7`, `drop_min_prev: 10` |
| Price per 1M tokens | input $0.75, cached input $0.075, output $4.50 |

Price source: developers.openai.com/api/docs/pricing, read 2026-09-02, gpt-5.4-mini
standard short-context tier.

seedcam's `src/config.yaml` is gitignored, so its threshold values are recorded above
rather than by reference.

## 3. Results

Run 1, per API call:

| # | Phase | Input | Cached | Output | Reasoning |
|---|-------|------:|-------:|-------:|----------:|
| 1 | investigate | 1,220 | 0 | 144 | 34 |
| 2 | investigate | 7,731 | 1,024 | 276 | 91 |
| 3 | report | 7,565 | 0 | 215 | 68 |
| | total | 16,516 | 1,024 | 635 | 193 |

| | Run 1 | Run 2 |
|---|------:|------:|
| Total tokens | 17,151 | 17,385 |
| Uncached input | 15,492 | 16,665 |
| Cached input | 1,024 | 0 |
| Output | 635 | 720 |
| API calls | 3 | 3 |
| Tool calls | 5 | 6 |
| Turns | 2 | 2 |
| Verdict | ACTION | ACTION |

## 4. Cost

| | Run 1 | Run 2 |
|---|------:|------:|
| Input | $0.01162 | $0.01250 |
| Cached input | $0.00008 | $0.00000 |
| Output | $0.00286 | $0.00324 |
| **Total** | **$0.01455** | **$0.01574** |

Mean $0.0151 per run. At the configured `*-*-* 21:00:00` schedule, one run per day:
$0.45 per month, $5.53 per year.

Output is 3.7% of tokens and 19% of cost.

## 5. Tool calls made

Run 1, in order:

```
seedcam__compare_days({"date":null,"baseline":null,"until_hour":null})
seedcam__camera_status({})
seedcam__streaming_status({})
node__disk_usage({"path":null})
node__systemd_status({"unit":null})
```

The 1,220 -> 7,731 input jump between call 1 and call 2 is these five results entering
the context. The report call carries the same conversation a second time.

## 6. Derived bound

Not measured. Computed from the config bounds in section 2, assuming all 8 turns run,
each adding the 6,500 tokens of tool results measured in section 5, and each emitting the
full 4,000 output tokens: ~191,600 input + 36,000 output = $0.35 per run.
