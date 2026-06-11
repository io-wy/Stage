---
name: brainstorming
description: >
  Structured requirement clarification and solution design. You MUST use this
  before any new feature, bug fix, refactoring, or interface change -- explores
  codebase first, clarifies requirements, then produces design options with
  trade-offs before implementation. Triggered when user says
  "帮我做/实现/修/加功能/new feature/bug fix/refactor".
---

# Brainstorming with Context

> Core: AI does not code in a vacuum. Understand the codebase first, then the
> requirement boundaries, then produce the solution.

## Trigger Conditions

- New feature development, bug fix, refactoring, interface change
- User says "帮我做 xxx", "实现 xxx", "修 xxx"
- Any task requiring code should trigger this before implementation

## Execution Flow

### Phase 1: Code Exploration (cannot skip)

Search and read relevant code based on task keywords. Goal: understand the
status quo, not design the solution.

Must complete:

1. **Locate entry points**: runner -> state_board -> models, follow the call chain
2. **Read full implementations**: Not just function signatures, read the entire body
   - Understand data flow: input source -> intermediate processing -> output destination
   - Identify config items, environment variables, model structures
3. **Check tests**: Read existing tests to understand expected behavior
4. **Check dependencies**: Who calls this module, who does it call

After completion, report understanding to user (1-3 sentences), confirm correctness.

### Phase 2: Requirement Clarification (confirm at least these dimensions)

Ask user questions, do not assume answers:

1. **Boundary conditions**: How to handle empty/large/invalid input?
2. **Compatibility**: Need backward compatibility? Any callers depend on current behavior?
3. **Performance**: Expected QPS / data volume? Latency requirements?
4. **Error handling**: Fail -> retry / degrade / direct error?
5. **Observability**: Need logs / metrics / alerts?
6. **Config**: Need new config items? Defaults?

Question strategy: binary choice with recommendation, max 2 questions at a time.

### Phase 3: Solution Design

1. **At least 2 options**, each with pros and cons
2. **Recommended option + rationale**
3. **Change file list** (precise file paths)
4. **Change type labels**: add / modify / delete
5. **Impact scope labels**: Which APIs / modules affected

Save to `docs/plans/`, inform user of path.

## Common Errors

| Error | Consequence | Prevention |
|-------|-------------|------------|
| Produce solution without reading code | Solution detached from reality, misses existing logic | Phase 1 cannot skip |
| Do not ask questions before acting | Understanding bias leads to rework | Phase 2 at least one round |
| Only give one option | No comparison, cannot judge quality | Phase 3 at least 2 options |
| Solution does not list file list | Miss dependencies during execution | Phase 3 must include list |

## Verification Checklist

- [ ] All involved files read in full
- [ ] User confirmed understanding is correct
- [ ] At least 1 clarification question asked
- [ ] At least 2 options provided
- [ ] Options include precise file change list

## Chain Trigger

After user confirms the solution, automatically trigger `plan-to-tasks`.
