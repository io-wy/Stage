---
name: case-handling-baseline
description: Use this skill whenever Claude Code is asked to run a business baseline, resolve local wiki-derived case handling tasks, answer support or approval cases from organization docs, or produce a structured case_result JSON. This skill enforces evidence-backed answers, explicit closure criteria, human handoff, and secret redaction.
---

# Case Handling Baseline

Use this skill to resolve a single organization case from local wiki/project context.
The goal is not to be chatty; the goal is to close the case correctly with cited evidence.

## Workflow

1. Identify the case family:
   - `knowledge`
   - `approval`
   - `ticket`
   - `incident`
   - `defect`

2. Read only the provided context and explicitly named local source files.
   Do not guess from memory when a source file is available.

3. Extract concise evidence:
   - source path or title
   - facts used
   - any uncertainty or missing information

4. Decide whether the case can close:
   - Close only when the answer satisfies the success criteria.
   - If required information is missing, set `closed` to `false` and ask concrete human questions.

5. Produce a structured result.

## Output Format

Always output one JSON object with this shape:

```json
{
  "closed": true,
  "family": "knowledge",
  "answer": "Short answer for the user.",
  "evidence": ["relative/or/absolute/source/path.md"],
  "actions": ["answer_user"],
  "human_questions": [],
  "confidence": 0.9,
  "redactions": [],
  "failure_mode": null
}
```

If the case cannot close:

```json
{
  "closed": false,
  "family": "ticket",
  "answer": "I cannot close this case yet because ...",
  "evidence": ["source.md"],
  "actions": ["ask_human"],
  "human_questions": ["Specific question needed to proceed."],
  "confidence": 0.4,
  "redactions": [],
  "failure_mode": "missing_required_information"
}
```

## Evidence Rules

- Cite source files in `evidence`.
- Include only evidence actually used.
- Do not cite a file you did not inspect.
- If multiple sources conflict, set `closed` to `false` unless the task explicitly asks for conflict analysis.

## Secret Handling

Local wiki files may contain credentials, tokens, passwords, private IDs, internal links, or personal contact details.

- Do not output credentials or tokens.
- Do not output passwords even if the source contains them.
- Do not output personal contact details unless the case explicitly requires routing to that person.
- Mention redactions in `redactions`, e.g. `"redacted credential in source"`.
- Prefer user-safe operational guidance over raw internal secrets.

## Quality Bar

The result should be:

- correct against the provided docs
- concise
- source-backed
- explicit about missing information
- safe to paste into a ticket or reply

