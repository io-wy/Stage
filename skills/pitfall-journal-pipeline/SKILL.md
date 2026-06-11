---
name: pitfall-journal-pipeline
description: Pitfall recording and evolution tracking. When AI makes a mistake or user corrects output, record a PIT entry. Same-type PITs reaching 2+ trigger rule extraction; 4+ trigger skill crystallization. Use when user says "踩坑/pitfall/PIT/记录错误/犯错日志/evolve mistake/踩坑记录".
---

# Pitfall Journal Pipeline

> Core: Every pitfall is a future constraint rule. Closed loop: pitfall -> rule -> skill.

## Trigger Conditions

- AI generated incorrect code and was corrected by user
- AI missed boundary conditions causing bugs
- AI used a non-existent API (hallucination)
- AI changed locally but missed global impact (incomplete change)
- AI fabricated information when uncertain
- User says: "踩坑", "记录 PIT", "pitfall", "犯错日志"

## Execution Flow

### Step 1: Record PIT

Call `run_openagent_skill` with `action="record"`:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `title` | yes | Short title |
| `pit_type` | yes | One of: incomplete_change, hallucination, env_blindness, pattern_over_verification, cant_say_idk, concurrency, resource_leak, missing_error_handling, protocol_drift, review_false_positive |
| `severity` | no | Critical / High / Medium (default Medium) |
| `scenario` | no | What task was being done |
| `phenomenon` | no | What went wrong |
| `root_cause` | no | Why (5 Why, at least 3 levels deep) |
| `fix` | no | How it was fixed |

### Step 2: Check Evolution

The pipeline automatically counts same-type PITs and reports evolution hints:

| Repeat Count | Action |
|--------------|--------|
| 1 | Record only, status: `new` |
| 2+ | Consider extracting a CLAUDE.md constraint rule, update status to `ruled` |
| 4+ | Consider crystallizing into an independent skill, update status to `skilled` |
| Rule ineffective | Mark status as `covered` (general knowledge sufficient) |

### Step 3: Notify User

Output one line:
```
PIT-xxx recorded (type/severity/status). Same-type cumulative N.
```

If evolution threshold reached, additionally notify:
```
Same-type PITs reached 2+, consider extracting a CLAUDE.md constraint.
```

## PIT Types

| Type | Typical Symptom | Base Severity |
|------|-----------------|---------------|
| **incomplete_change** | Changed function signature but missed call sites; added field but no migration | High |
| **hallucination** | Used non-existent API, fabricated function signature | Critical |
| **env_blindness** | Did not know project Python version, config, existing dependencies | Medium |
| **pattern_over_verification** | Asserted behavior from training memory without tool verification | High |
| **cant_say_idk** | Fabricated information when uncertain instead of admitting | Critical |
| **concurrency** | Unprotected shared state, improper lock granularity | High |
| **resource_leak** | Open without Close, unclosed connections | High |
| **missing_error_handling** | Swallowed errors `_ =`, errors without context | Medium |
| **protocol_drift** | Implementation inconsistent with reference docs | High |
| **review_false_positive** | Review false alarm rate too high | Medium |

## 5 Why Root Cause Example

```
PIT-005: StateBoard event log lost after resume

1 Why: resume did not restore events
2 Why: events were not included in to_dict()
3 Why: to_dict() only persisted tasks and agents
4 Why: CLAUDE.md had no persistence completeness constraint
5 Why: persistence design review did not cover event stream

-> Extract rule: "Any new StateBoard field must be included in to_dict() / from_dict() / snapshot()"
```

## Other Actions

- `action="list"` — List all recorded PITs, optionally filter by `pit_type`
- `action="evolve"` — Check evolution status for all types or a specific type

## Common Errors

| Error | Consequence | Prevention |
|-------|-------------|------------|
| Mistake not recorded | Same error repeats | Step 1 is mandatory |
| Record too vague ("code had a bug") | Cannot extract rules | Each PIT must have type and root cause |
| Record without evolution | PIT pile up, knowledge does not flow | Step 2 checks threshold every time |
| Root cause only surface-level | Same root cause, different symptoms | 5 Why at least 3 levels deep |
| Status not updated after evolution | PIT and rules coexist redundantly | Update status immediately after evolution |

## Verification Checklist

- [ ] PIT recorded after mistake
- [ ] PIT contains complete type / severity / root cause
- [ ] Same-type PIT >= 2 extracted into rule
- [ ] Status of ruled PITs updated
- [ ] User notified after evolution
