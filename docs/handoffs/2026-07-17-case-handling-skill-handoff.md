# Case Handling Skill Handoff

## Goal

Use Claude Code's `skill-creator` to create the formal business skill for the case-handling benchmark, then run the Claude Code + skill baseline on the local wiki cases.

## Context

This repo already contains:

- benchmark design: `docs/plans/2026-07-17-stage-case-benchmark-design.md`
- implementation plan: `docs/plans/2026-07-17-stage-case-benchmark-implementation-plan.md`
- case schema / verifier helpers used for the benchmark
- draft evaluation prompts: `skills/case-handling-baseline/evals/evals.json`

There is also a draft skill at `skills/case-handling-baseline/SKILL.md`.
Treat it as a draft only. The formal skill should be rewritten by Claude Code using `skill-creator`.

## What Claude Code should do

1. Read the benchmark docs and the draft eval prompts.
2. Use the `skill-creator` skill to create or overwrite the official business skill at:
   - `skills/case-handling-baseline/SKILL.md`
3. Make the skill do this:
   - resolve local wiki-derived case handling tasks
   - return one structured `case_result` JSON object
   - cite evidence from source docs
   - ask for human help when the case cannot be closed
   - redact or omit any credential-like content
4. Run the skill on the benchmark cases in `skills/case-handling-baseline/evals/evals.json`.
5. Save benchmark artifacts in the workspace layout below.

## Skill requirements

The final skill should:

- be triggered for local wiki case handling, support, approval, ticket, incident, and defect tasks
- output exactly one JSON object
- include:
  - `closed`
  - `family`
  - `answer`
  - `evidence`
  - `actions`
  - `human_questions`
  - `confidence`
  - `redactions`
  - `failure_mode`
- never reveal passwords, tokens, or other secrets
- cite the actual source files used
- be concise enough for benchmark use

## Benchmark cases

Use the cases in `skills/case-handling-baseline/evals/evals.json`.

Primary cases:

- NAS notification delay
- NAS access methods
- Jellyfin naming / category rules

These cases are intentionally knowledge-heavy and safe. Do not use docs that expose credentials unless a task explicitly requires redaction handling.

## Baseline run command shape

Use Claude Code in print mode with the local repo and wiki allowed as read sources.

Suggested shape:

```bash
claude -p \
  --output-format json \
  --add-dir /Users/io/workplace/Stage \
  --add-dir "${STAGE_WIKI_PATH:?set STAGE_WIKI_PATH}" \
  --tools Read,Grep,Glob \
  --max-budget-usd 0.10 \
  "Use the skill at skills/case-handling-baseline/SKILL.md and produce the required case_result JSON for the case."
```

Run once per eval case.

## Output artifacts

Write artifacts to:

```text
skills/case-handling-baseline-workspace/
  iteration-1/
    eval-0-<name>/
      with_skill/
        outputs/
          result.json
        timing.json
        grading.json
      eval_metadata.json
    benchmark.json
    benchmark.md
```

## Grading rule

Grade against these expectations:

- valid `case_result` JSON
- answer matches the source docs
- evidence cites the source file
- no secret leakage

## Important constraints

- Do not rely on the draft skill as if it were the final answer.
- Do not leak or echo credentials, passwords, or tokens.
- Do not fabricate facts not present in the source docs.
- If Claude Code quota is unavailable, stop and report the blocker rather than inventing results.

## Paste-into-Claude-Code prompt

```text
Use the skill-creator skill to create the formal case-handling business skill for this repo.

Read:
- docs/plans/2026-07-17-stage-case-benchmark-design.md
- docs/plans/2026-07-17-stage-case-benchmark-implementation-plan.md
- skills/case-handling-baseline/evals/evals.json

Then create or overwrite skills/case-handling-baseline/SKILL.md so that it:
- resolves local wiki case-handling tasks
- returns one structured case_result JSON object
- cites source files
- never reveals credentials or tokens

After the skill is created, run the benchmark cases from evals/evals.json and write the artifacts into:
skills/case-handling-baseline-workspace/iteration-1/

If quota is unavailable, stop and report that blocker.
```
