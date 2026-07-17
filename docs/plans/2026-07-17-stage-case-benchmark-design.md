# Stage Case Benchmark Design

> **Goal:** Build a local wiki-derived benchmark to compare Claude Code + skill against Stage on real case-handling work, then use baseline failure modes to drive Stage optimization.
>
> **Scope:** v0 uses only local wiki data. Public benchmarks are deferred to later calibration.

## 1. Problem

We need a business wedge where Stage can plausibly outperform Claude Code + skill. Single-shot code generation is not that wedge. The right target is stateful case handling: a request enters, gets clarified, routed, resolved, verified, and closed.

## 2. Benchmark Scope

Primary case families:

- Knowledge Q&A
- Approval workflow
- Ticket / incident handling
- Defect repair / follow-up

Data source:

- Local wiki corpus only
- Existing docs, issue tables, approval docs, project notes, and reports

Evaluation boundary:

- Same cases for Stage and Claude Code + skill
- Same context pack
- Same tool permissions
- Same time / token budget
- Same verifier

## 3. Metric Hierarchy

Use layered metrics, not a single total score.

Hard gates:

- Correct case closure
- No severe misuse / wrong action
- Required verification passed

Primary metric:

- Case success rate

Efficiency metrics:

- Human intervention count
- Time to resolution
- Token / cost

Quality metrics:

- Evidence completeness
- State consistency
- Wrong-action rate

Reliability metrics:

- Multi-run variance on the same case

Rule:

- Hard gate failure means fail
- After that, compare systems by Pareto improvement, not by forcing one opaque composite score
- Self-consistency is diagnostic, not a north-star metric

## 4. Case Schema

Each case should be normalized to:

- `case_id`
- `family`
- `source`
- `objective`
- `context_pack`
- `tool_limits`
- `success_criteria`
- `verifier`
- `difficulty`
- `expected_human_touches`

This keeps Stage and Claude Code + skill on the same footing.

## 5. Case Construction

Preferred approach:

1. Semi-automatic extraction from wiki docs and tables
2. Human cleanup to normalize the schema
3. Optional synthetic variants for robustness later

Why:

- Pure manual case writing is too slow
- Pure synthetic tasks drift away from business reality
- Semi-auto extraction keeps the benchmark grounded

## 6. Baseline Protocol

Baseline options:

1. Family-specific skill pack, pre-registered in advance
2. One universal skill pack
3. Per-case hand-tuned skill selection

Recommended:

- Family-specific skill pack, frozen before testing

Run rules:

- Same case
- Same context
- Same tools
- Same budget
- Same verifier
- Three runs per case, report median
- No human patching during the run

## 7. Stage Optimization Strategy

Do not optimize Stage by feature count. Optimize from baseline failure modes.

Failure taxonomy:

- `retrieval_failure`
- `planning_failure`
- `state_tracking_failure`
- `verification_failure`
- `human_handoff_failure`
- `tool/action_failure`
- `over_cost_failure`
- `consistency_failure`

Optimization rule:

- Only improve the top failure classes observed in baseline runs
- Let the benchmark tell us whether Stage needs StateBoard, evidence tracking, HumanChannel, verifier hooks, or better routing

## 8. System Architecture

Pipeline components:

- `CaseBuilder`
- `ContextPacker`
- `BaselineRunner`
- `StageRunner`
- `Verifier`
- `HumanAudit`
- `FailureAnalyzer`
- `ReportBuilder`

Data flow:

`wiki data -> cases -> frozen context pack -> baseline/stage runs -> verification -> failure attribution -> optimization backlog`

## 9. Error Handling

- Missing success criteria: quarantine the case
- Verifier cannot decide: route to human audit
- Runner timeout: record as timeout failure
- Tool misuse: record as tool/action failure
- Incomplete output: still score it; do not patch manually
- Broken benchmark item: quarantine and log the reason

## 10. Testing

Test layers:

- Schema validation for all cases
- Golden tests for verifier behavior
- Smoke run on 3-5 cases
- Report consistency tests
- Sampled human audit against automatic scoring

## 11. Rollout Plan

Phase 1:

- Build 50-100 local wiki cases
- Freeze a test set
- Run Claude Code + skill baseline

Phase 2:

- Run current Stage
- Attribute failures
- Prioritize Stage changes from the miss taxonomy

Phase 3:

- Re-run the same frozen set after Stage changes
- Compare success rate, human intervention, time, and consistency

