# CLAUDE.md

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Don't write "troubleshoot" as comments next to the code
- If you gonna write comments next to the code, maximum 20 words
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Git Issues

**Keep issues atomic, under 100 words. Use exactly three sections:**

```
**Issue:** One sentence describing the problem.
**Tag:** `Bug` | `Feature` | `Enhancement` | `Refactor`

**Suggested Change:** What should be modified and how.

**Resolve Criteria (only one):**
- [ ] Criterion one
```

- Issues are managed in tree with at most 2 layers (issue and sub-issue), more than this relationship will connect via link/relationship. 
- No background paragraphs, no reproduction steps, no long explanations. If you can't describe it in one issue, split it into sub issues.
- Do not automatically close the issue with commit.

**Do not create a new branch for commit**


## 6. Experimental Tracking Rules

**Two tiers: one index table, detail in separate files. Never mix them.**

Tier 1 — `docs/EXPERIMENT_TRACKER.md`. Append-only table, one row per measurement:

| Timestamp | Git Commit | Experiment | Key Metrics | Status | Detail Log |
|-----------|------------|------------|-------------|--------|------------|
| YYYY-MM-DD | short sha | short name | the numbers | Pass / Fail / Diverged | link or — |

- One row = one finding. A run producing five numbers gets five rows, not one row with a paragraph.
- Metrics must be numbers with units/context, not adjectives ("4.67 videos/min", not "faster").
- `Status` is about the measurement, not the outcome you hoped for. A clean run proving the model
  fails is `Fail`; a metric contradicting an earlier one is `Diverged`.
- Never edit or delete old rows. A wrong number gets a new row plus a corrected `Diverged` entry.
- Just logs/summary experiment

Tier 2 — `docs/<RUN_NAME>.md`, one file per run, linked from the row's `Detail Log`:

- Header line: date, commit, config/model
- Then numbered sections: what was run, the inputs, the results, per-artifact detail.
- Tables over prose. Cross-link sibling reports so a reader can walk the sequence.
- The report holds the evidence (crops, counts, file paths). The tracker holds only the claim.
- Do not add own judgment, do not add suggestion.
- Be concise and brief as much as possible.


## 7. Config Files

**One run = one config file. The config is the run's definition, not a convenience.**

- Location `configs/<run-name>.yaml`. One file per run, committed with the run.
- The config holds every setting and hyperparameter that shapes the result: model, backend,
  dataset/split, sampling params, batch/precision, seed, output dir.
- Code reads its parameters from the config. No hardcoded values in scripts, no settings that
  live only in a shell command or in someone's head.
- CLI flags may override a config for a one-off, but anything logged in the tracker must come
  from a committed config.
- Never edit a config that a past run used — copy it to a new name. Old configs stay
  reproducible.
- No secrets, no absolute paths tied to one machine.
- The tracker row and report header name the config file used.

The test: a maintainer opens `configs/<run-name>.yaml` and knows exactly how that run was
executed, without reading the code.

---