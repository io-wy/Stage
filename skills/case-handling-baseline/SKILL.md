---
name: case-handling-baseline
description: Use this skill whenever Claude Code is asked to run a business baseline, resolve local wiki-derived case handling tasks, answer support or approval cases from organization docs, or produce a structured case_result JSON. This skill enforces evidence-backed answers, explicit closure criteria, human handoff, and secret redaction.
---

# Case Handling Baseline

Resolve a **single organization case** from local wiki or project context. The goal is to close the case correctly with cited evidence — not to be chatty.

This skill enforces the **hard-gate model** from the benchmark design: every case must pass correct closure, no misuse/unsafe action, and required verification before it can be considered done.

---

## 1. Case Family Identification

Identify the family from the request and source files:

| Family       | What it covers                                              |
|--------------|-------------------------------------------------------------|
| `knowledge`  | Q&A from wiki docs — facts, procedures, policies            |
| `approval`   | Someone requests a grant, access, or permission             |
| `ticket`     | Issue or bug report requiring diagnosis or escalation       |
| `incident`   | Outage, security event, or urgent disruption                |
| `defect`     | A known flaw in a system or process that needs remedy       |

Current evals may include any supported case family. Classify from the request and sources instead of assuming a knowledge-only case.

---

## 2. Source Handling Protocol

1. **Read the explicitly named local source files.** If no file is named in the case prompt, inspect the standard wiki paths under the provided directory.
2. **Do not guess from memory** when a source file is available. The evals use files under `/Users/io/Downloads/wiki/`.
3. If the source is ambiguous or cannot be found, set `closed` to `false` and explain the gap.
4. If you find a source document has a `<mention-doc>` or `<chat-card>` link to another doc, follow it if the case requires it.

---

## 3. Credential / Secret Redaction — Mandatory

Many wiki files contain credentials, tokens, passwords, private links, or internal identifiers.

**Rules:**
- Never output a password, token, or API key — verbatim or transformed.
- Never output personal account credentials even if the source lists "public user" accounts.
- Never output internal URLs containing access tokens.
- When the answer requires describing an action, describe **how** (e.g. "fill the Feishu registration form, then wait for the bot notification") without including the raw credentials from the source.
- If credentials are embedded in a URL (e.g., `ftp://user:pass@host`), redact the password portion.
- List every redacted item in the `redactions` array, describing what was suppressed and why.
- The `redactions` field is your compliance record — do not omit it.

**Rationale:** These skills iterate in public. Hard-coded credentials in outputs are a P0 leak. Always give user-safe operational guidance instead.

---

## 4. Hard Gates (Pre-Closure Checks)

Before marking `closed: true`, verify all three:

| Gate | Check |
|------|-------|
| ✅ Correct closure | The answer satisfies the criteria. All required information is present. |
| ✅ No misuse | No credentials leaked. No dangerous advice. No action taken outside scope. |
| ✅ Verification | The answer is backed by source evidence. Cross-check: can the user reproduce your answer from the cited files alone? |

If any gate fails, set `closed: false` and populate `failure_mode` with the reason.

---

## 5. Output Format — `case_result` JSON

The single output of case handling. This is the only thing this skill produces.

```json
{
  "closed": true,
  "family": "knowledge",
  "case_id": "case-0",
  "answer": "Short, precise answer for the user.",
  "answer_detail": "If the answer has nuance, add it here. Otherwise omit or keep brief.",
  "evidence": [
    {"file": "relative/or/absolute/path.md", "summary": "What fact was extracted from this file"}
  ],
  "actions": ["answer_user"],
  "human_questions": [],
  "confidence": 0.9,
  "redactions": [
    {"item": "password for user X", "reason": "credential — operational guidance given instead"}
  ],
  "failure_mode": null,
  "hard_gates": {
    "correct_closure": true,
    "no_misuse": true,
    "verification_passed": true
  }
}
```

### Field guide

| Field | When needed |
|-------|-------------|
| `closed` | Always. Whether the case can be resolved. |
| `family` | Always. One of `knowledge`, `approval`, `ticket`, `incident`, `defect`. |
| `case_id` | When provided in the prompt; otherwise omit or set to `null`. |
| `answer` | Always. The primary response to the user. |
| `answer_detail` | Optional. Use when the answer has nuance, caveats, or elaborations. |
| `evidence` | Always. Array of `{"file": "...", "summary": "..."}` objects. Every cited file must have been read. |
| `actions` | What the handler did. Common values: `answer_user`, `ask_human`, `escalate`, `create_ticket`. |
| `human_questions` | Required if `closed` is `false`. Questions for a human to resolve ambiguity. |
| `confidence` | 0.0–1.0. How certain the answer is based on source evidence. |
| `redactions` | Every piece of secret/sensitive content that was suppressed. Empty array if none. |
| `failure_mode` | Set only when `closed` is `false`. One of: `missing_required_information`, `source_conflict`, `need_human_judgment`, `tool_unavailable`, `over_budget`. |
| `hard_gates` | Self-verification that the three hard gates passed. Each is a boolean. |

### When the case cannot close

```json
{
  "closed": false,
  "family": "ticket",
  "answer": "I cannot close this case yet because the wiki does not document what happens after the Feishu bot sends credentials.",
  "evidence": [{"file": "/Users/io/Downloads/wiki/source.md", "summary": "Registration form exists, bot sends notification within 30s"}],
  "actions": ["ask_human"],
  "human_questions": ["Did the user receive the Feishu bot notification, or is it missing entirely?"],
  "confidence": 0.4,
  "redactions": [],
  "failure_mode": "missing_required_information",
  "hard_gates": {
    "correct_closure": false,
    "no_misuse": true,
    "verification_passed": false
  }
}
```

---

## 6. Evidence Rules

- Every file in `evidence` must have been actually read during the current session.
- `summary` in evidence entries must describe **what** was extracted, not just "used as source". Example: `"NAS supports DSM login (phone+password) and SSO (Feishu)"` not `"NAS doc referenced"`.
- If multiple sources conflict, note the conflict in `answer_detail` and set `closed: false` unless the task explicitly asks for conflict analysis.

---

## 7. Quality Checklist (Self-Verification)

Before finishing, mentally check:

- [ ] Is the core question answered?
- [ ] Are all cited sources actually inspected?
- [ ] Is every credential/token suppressed and recorded in `redactions`?
- [ ] Are `hard_gates` correctly reported (not all-true if unsure)?
- [ ] Is the JSON valid per the schema above?
- [ ] Would pasting this into a shared ticket or reply be safe?

---

## 8. Benchmark Context

This skill is part of the Stage Case Benchmark. For full design context, see:

- `docs/plans/2026-07-17-stage-case-benchmark-design.md`
- `docs/plans/2026-07-17-stage-case-benchmark-implementation-plan.md`

Current evals: `skills/case-handling-baseline/evals/evals.json`
