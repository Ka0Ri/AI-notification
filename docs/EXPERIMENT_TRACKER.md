# Experiment tracker

Append-only. One row per measurement. Never edit or delete a row; a correction is a new
row. Detail lives in the linked report, not here.

| Timestamp | Git Commit | Experiment | Key Metrics | Status | Detail Log |
|-----------|------------|------------|-------------|--------|------------|
| 2026-09-02 | ab5d025 | agent-cost | 17,151 tokens/run (15,492 in / 1,024 cached / 635 out), `configs/agent.yaml`, gpt-5.4-mini | Pass | [AGENT_COST.md](AGENT_COST.md) |
| 2026-09-02 | ab5d025 | agent-cost | $0.0151/run; $0.45/month at 1 run/day | Pass | [AGENT_COST.md](AGENT_COST.md) |
| 2026-09-02 | ab5d025 | agent-cost | 3 API calls, 5 tool calls, 2 turns per run | Pass | [AGENT_COST.md](AGENT_COST.md) |
| 2026-09-02 | ab5d025 | agent-cost-repeat | 17,385 tokens, $0.01574 (+1.4% vs run 1) | Pass | [AGENT_COST.md](AGENT_COST.md) |
