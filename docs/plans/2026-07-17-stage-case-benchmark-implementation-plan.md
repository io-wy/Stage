# Stage Case Benchmark Implementation Plan

> **Goal:** Implement a local wiki-derived `case_handling` evaluation suite that can run Stage, prepare Claude Code + skill baseline runs, verify outcomes, and report failure modes.
>
> **Architecture:** Add a new `eval/case_handling/` suite that reuses the existing `EvalTask`, `EvalResult`, and `EvalHarness` architecture. Keep Claude Code baseline support reproducible by generating fixed prompt/context packs and importing baseline run logs before automating any CLI runner.
>
> **Tech Stack:** Python, pytest, YAML/JSON case fixtures, existing `eval.base` primitives.

## Task 1: Case Schema And Fixtures

**Files:**

- Create: `eval/case_handling/__init__.py`
- Create: `eval/case_handling/schema.py`
- Create: `eval/case_handling/cases/README.md`
- Create: `tests/test_case_handling_schema.py`

**Steps:**

- [ ] Write failing tests for required case fields.
- [ ] Define typed structures for case family, success criteria, verifier config, and human touch expectations.
- [ ] Validate required fields: `case_id`, `family`, `source`, `objective`, `context_pack`, `success_criteria`, `verifier`, `difficulty`.
- [ ] Reject ambiguous or missing success criteria.
- [ ] Run targeted tests.

## Task 2: Local Case Loader

**Files:**

- Create: `eval/case_handling/loader.py`
- Create: `tests/test_case_handling_loader.py`

**Steps:**

- [ ] Write failing tests for loading YAML/JSON case fixtures.
- [ ] Load all cases under `eval/case_handling/cases/`.
- [ ] Support `limit`, `family`, and `difficulty` filters.
- [ ] Mark invalid cases as load errors, not silent skips.
- [ ] Run loader tests.

## Task 3: Context Pack Builder

**Files:**

- Create: `eval/case_handling/context.py`
- Create: `tests/test_case_handling_context.py`

**Steps:**

- [ ] Write tests for deterministic context pack rendering.
- [ ] Build a frozen context pack from case metadata and selected source excerpts.
- [ ] Ensure Stage and Claude Code baseline receive the same context.
- [ ] Include source references for evidence checking.
- [ ] Run context tests.

## Task 4: Verifier Framework

**Files:**

- Create: `eval/case_handling/verify.py`
- Create: `tests/test_case_handling_verify.py`

**Steps:**

- [ ] Write golden tests for pass/fail verifier examples.
- [ ] Implement hard-gate verification: case closure, required artifacts, required evidence, forbidden actions.
- [ ] Add family-specific checks for knowledge Q&A, approval, ticket/incident, and defect follow-up.
- [ ] Return structured errors for failure attribution.
- [ ] Run verifier tests.

## Task 5: Case Handling Harness

**Files:**

- Create: `eval/case_handling/harness.py`
- Modify: `eval/main.py`
- Create: `tests/test_case_handling_harness.py`

**Steps:**

- [ ] Write failing tests that the new suite is registered in `eval.main.SUITES`.
- [ ] Implement `CaseHandlingHarness.load_tasks()` by converting case fixtures into `EvalTask`.
- [ ] Implement `run_task()` by using existing `_run_orchestrator()` for Stage.
- [ ] Store run artifacts under the per-task work directory.
- [ ] Convert verifier scores into `EvalResult` fields.
- [ ] Run harness tests.

## Task 6: Claude Code Baseline Pack

**Files:**

- Create: `eval/case_handling/baseline.py`
- Create: `tests/test_case_handling_baseline.py`

**Steps:**

- [ ] Write tests for generating deterministic baseline prompt packs.
- [ ] Generate per-case baseline prompt files with objective, context, tools, and success criteria.
- [ ] Define a baseline result JSON schema for imported Claude Code + skill runs.
- [ ] Do not automate Claude Code execution in v0; first support reproducible prompt packs and run-log import.
- [ ] Run baseline tests.

## Task 7: Failure Attribution

**Files:**

- Create: `eval/case_handling/failure.py`
- Create: `tests/test_case_handling_failure.py`

**Steps:**

- [ ] Write tests mapping verifier errors to failure classes.
- [ ] Implement taxonomy: `retrieval_failure`, `planning_failure`, `state_tracking_failure`, `verification_failure`, `human_handoff_failure`, `tool/action_failure`, `over_cost_failure`, `consistency_failure`.
- [ ] Attach failure labels to `EvalResult.raw`.
- [ ] Run failure tests.

## Task 8: Reporting

**Files:**

- Create: `eval/case_handling/report.py`
- Create: `tests/test_case_handling_report.py`

**Steps:**

- [ ] Write tests for summary aggregation by family and difficulty.
- [ ] Report hard-gate pass rate, case success rate, human touches, duration, token cost, and failure class distribution.
- [ ] Support side-by-side comparison when both Stage and baseline logs exist.
- [ ] Run report tests.

## Task 9: Seed Pilot Cases

**Files:**

- Create: `eval/case_handling/cases/pilot/*.yaml`

**Steps:**

- [ ] Add 3-5 hand-cleaned pilot cases from local wiki data.
- [ ] Include at least one knowledge, one approval, and one ticket/incident case.
- [ ] Run schema, loader, context, verifier, and harness smoke tests.
- [ ] Quarantine any case with unclear success criteria.

## Task 10: End-To-End Smoke

**Files:**

- Modify only if tests reveal a gap.

**Steps:**

- [ ] Run `uv run pytest tests/test_case_handling_* -q`.
- [ ] Run `uv run python -m eval.main --suite case_handling --limit 3 --skip-judge`.
- [ ] Save the report path and inspect pass/fail details.
- [ ] Fix only defects found by the smoke test.

## Out Of Scope For V0

- Fully automated Claude Code CLI baseline execution
- Public benchmark integration
- Model-as-judge scoring for all cases
- Stage product changes based on assumed weaknesses
- Large synthetic case generation

