---
name: adversarial-review-pipeline
description: Multi-model adversarial code review for agent outputs. Use when reviewing core logic changes, 5+ files, 200+ lines, or when user says "对抗审查/adversarial review/cross-model review". The reviewer agent inspects coder outputs through a cross-validation lens to catch single-model blind spots.
---

# Adversarial Review Pipeline

> Core: Code written and reviewed by the same model tends to share the same blind spots. Cross-model review covers what a single model misses.

## Trigger Conditions

- Core business logic changes
- Change touches >= 5 files or >= 200 lines
- User says: "对抗审查", "adversarial review", "cross-model review", "多模型审查"
- Reviewing agent (coder/reviewer/tester) output quality is suspect

## Three Blind Spots

| Blind Spot | Symptom | Root Cause |
|-----------|---------|------------|
| **Knowledge gap** | Incomplete understanding of a framework/pattern | Training data differences |
| **Attention decay** | Review quality drops in the second half of large diffs | Limited context window |
| **Confirmation bias** | Deepens along one direction after finding the first issue | Cognitive inertia |

## Execution Flow

### Step 1: Collect Changes

Gather all artifacts produced by the agent under review:
- Files claimed in `StateBoard.artifacts`
- Agent transcript (tool calls, reasoning)
- Test results from `project_context.test_reports`

### Step 2: Primary Review

Review dimensions:

| Dimension | Check Points |
|-----------|-------------|
| Correctness | Logic correctness, boundary conditions, error paths |
| Security | Injection risks, auth bypass, secret leakage, SSRF |
| Concurrency | Shared state protection, lock granularity, deadlocks |
| Resources | Connection leaks, file handle leaks, memory growth |
| Performance | Hot path overhead, N+1 queries, unnecessary allocations |
| Observability | Error logging, key path tracing, metrics |

Each issue must be labeled with: level (Critical/Major/Minor/Suggestion) + file:line + category + fix suggestion.

Output issue set A.

### Step 3: Cross-Model Review

Spawn a **different** agent type (or same type with different temperature/system prompt) to independently review the same artifacts. Do NOT share the primary review output with the cross reviewer.

Output issue set B.

### Step 4: Cross-Validation

| Scenario | Action |
|----------|--------|
| Found by both A and B | High confidence, confirm the issue |
| Only A found | Label `[primary-only — needs confirmation]` |
| Only B found | Label `[cross-only — needs confirmation]` |

### Step 5: Merge Report

```markdown
# Adversarial Review Report

## Summary
| Level | Confirmed | Needs Confirmation |
|-------|-----------|-------------------|
| Critical | x | x |
| Major | x | x |
| Minor | x | x |

## Confirmed Issues (high confidence)
### C1. [category] <title>
- File: path.py:123
- Found by: primary + cross
- Issue: ...
- Suggestion: ...

## Needs Confirmation
### M3. [category] <title> [primary-only]
- File: path.py:45
- Issue: ...
```

## Review Discipline

- **This change** vs **historical debt** (only review what the agent introduced)
- **Style preference** vs **actual defect** (only flag actual defects)
- **Correct code mislabeled** vs **real error** (uncertain → label Suggestion)

## Prohibited Behaviors

- Do NOT use the same model to write and review (must use different model/agent)
- Do NOT mix historical debt into this review
- Do NOT label style preferences as Critical/Major
- Do NOT review only the first half of large diffs (chunk into <= 200 lines per batch)

## Verification Checklist

- [ ] Primary review completed
- [ ] Cross-model review spawned with different configuration
- [ ] Cross-validation completed
- [ ] Report output to user
