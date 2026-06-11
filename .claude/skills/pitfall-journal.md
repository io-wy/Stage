---
name: pitfall-journal
description: >
  Pitfall recording and evolution tracking. When AI makes a mistake or user
  corrects output, record a PIT entry. Same-type PITs reaching 2+ trigger rule
  extraction; 4+ trigger skill crystallization. Triggered when user says
  "踩坑/pitfall/PIT/记录错误/犯错日志/evolve mistake/踩坑记录".
---

# Pitfall Journal

> Core: Every pitfall is a future constraint rule. Closed loop:
> pitfall -> rule -> skill.

## Trigger Conditions

- AI generated incorrect code and was corrected by user
- AI missed boundary conditions causing bugs
- AI used a non-existent API (hallucination)
- AI changed locally but missed global impact (incomplete change)
- AI fabricated information when uncertain
- User says: "踩坑", "记录 PIT", "pitfall", "犯错日志"

## Execution Flow

### Step 1: Record PIT

Append a PIT entry immediately after each mistake, using the format below.

### Step 2: Check Evolution

Check if same-type PIT has appeared >= 2 times:
- **First occurrence**: Record only, status "new"
- **Repeat 2+**: Extract into CLAUDE.md coding constraint, update status to "ruled", label constraint ID (e.g. C-11)
- **Rule validated 2+**: Evaluate if crystallizing into independent skill is needed, update status to "skilled"
- **Rule ineffective**: Label "covered" (general knowledge sufficient)

### Step 3: Notify User

Output one line:
```
PIT-xxx recorded (type/severity/status). Same-type cumulative N.
```

If evolution threshold reached, additionally notify:
```
Same-type PITs reached 2+, suggest extracting a CLAUDE.md constraint.
```

## Record Format

```markdown
### PIT-XXX: <short title>

- **Date**: YYYY-MM-DD
- **Type**: incomplete_change | hallucination | env_blindness | pattern_over_verification | cant_say_idk | concurrency | resource_leak | missing_error_handling | protocol_drift | review_false_positive
- **Severity**: Critical | High | Medium
- **Scenario**: What task was being done
- **Phenomenon**: What went wrong
- **Root cause**: Why (5 Why)
- **Fix**: How it was fixed
- **Rule extraction**: Need new CLAUDE.md constraint?
- **Status**: new | ruled | skilled | covered
```

## 10 PIT Types

| Type | Typical Symptom | Base Severity |
|------|-----------------|---------------|
| **incomplete_change** | Changed function signature but missed call sites; added field but no migration | High |
| **hallucination** | Used non-existent API, fabricated function signature | Critical |
| **env_blindness** | Did not know project Python version, config, existing dependencies | Medium |
| **pattern_over_verification** | Asserted behavior from training memory without tool verification | High |
| **cant_say_idk** | Fabricated information when uncertain instead of admitting | Critical |
| **concurrency** | Unprotected shared state, improper lock granularity | High |
| **resource_leak** | Open without Close, unclosed async tasks | High |
| **missing_error_handling** | Swallowed errors, errors without context | Medium |
| **protocol_drift** | Implementation inconsistent with reference docs | High |
| **review_false_positive** | Review false alarm rate too high | Medium |

## 5 Why Example

```
PIT-005: StateBoard event log lost after resume

1 Why: resume did not restore events
2 Why: events were not included in to_dict()
3 Why: to_dict() only persisted tasks and agents
4 Why: CLAUDE.md had no persistence completeness constraint
5 Why: persistence design review did not cover event stream

-> Extract rule: "Any new StateBoard field must be included in to_dict() / from_dict() / snapshot()"
```

## Evolution Rules

| Repeat Count | Action |
|--------------|--------|
| 1 | Record only |
| 2+ | Extract rule, add to CLAUDE.md |
| Rule validated 2+ | Crystallize into independent skill |
| Rule ineffective | Label "covered" |

## Common Errors

| Error | Consequence | Prevention |
|-------|-------------|------------|
| Mistake not recorded | Same error repeats | Step 1 mandatory |
| Record too vague | Cannot extract rules | Each PIT must have type and root cause |
| Record without evolution | PIT pile up | Step 2 check threshold every time |
| Root cause only surface-level | Same root cause, different symptoms | 5 Why at least 3 levels deep |
| Status not updated after evolution | PIT and rules coexist redundantly | Update status immediately |

## Verification Checklist

- [ ] PIT recorded after mistake
- [ ] PIT contains complete type / severity / root cause
- [ ] Same-type PIT >= 2 extracted into rule
- [ ] Status of ruled PITs updated
- [ ] User notified after evolution
