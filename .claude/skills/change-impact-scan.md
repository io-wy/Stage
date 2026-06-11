---
name: change-impact-scan
description: >
  Change impact scan. When modifying function signatures, interfaces, data
  models, config structs, or shared modules, automatically grep all call sites
  and dependent files to prevent incomplete changes. Triggered when user says
  "改影响/影响范围/改不全/impact scan/调用点/影响分析/change impact/改接口/改模型".
---

# Change Impact Scan

> Core: "Incomplete change" is a high-frequency AI coding bug -- local
> perspective forgets global impact. This skill enforces global scanning.

## Trigger Conditions

- Modify function signatures or interfaces
- Modify data models / Pydantic schemas / dataclasses
- Modify config structures
- Modify shared modules (imported by 3+ files)
- Modify API routes or request/response structures
- User says: "扫描影响", "影响分析", "impact scan", "call sites", "change impact"

## Execution Flow

### Step 1: Identify Change Type

| Type | Indicator |
|------|-----------|
| Signature change | Parameters, return values, method changes |
| Data model | Pydantic model / dataclass field add/remove/modify |
| Config change | Config dataclass field add/remove/modify |
| Route change | API path, HTTP method, middleware chain changes |

### Step 2: Execute Grep Scan

**Signature change**:
```bash
grep -rn "funcName\|ClassName" src/
```

**Data model change**:
```bash
grep -rn "ModelName(" src/
grep -rn "ModelName\." src/
```

**Config change**:
```bash
grep -rn "config\.Field\|cfg\.Field" src/
```

### Step 3: Generate Impact Report

```markdown
# Change Impact Report

## Change Overview
- Type: Signature change
- File: src/openagents_orchestration/runner.py:45

## Direct Impact (must sync modify)
| File | Line | Reason | Needs Change |
|------|------|--------|-------------|
| state_board.py:67 | Call site | Parameter change | Yes |

## Indirect Impact (may need modification)
| File | Line | Reason | Needs Change |
|------|------|--------|-------------|
| tests/test_runner.py:12 | Indirect call | Needs confirmation | To confirm |

## Config/Doc Sync
| File | Needs Sync |
|------|-----------|
| agent.json | No |
```

### Step 4: Confirm with User Before Editing

Show the impact report to the user, confirm scope before starting modifications.

## Common Errors

| Error | Consequence | Prevention |
|-------|-------------|------------|
| Only change function, not call sites | ImportError or runtime bug | Step 2 Grep scan |
| Change code but not config | Config drift | Step 3 config sync check |
| Start editing before scan completes | Miss associated changes | Step 4 confirm first |

## Verification Checklist

- [ ] Change type identified
- [ ] Grep scan completed
- [ ] Impact report generated
- [ ] User confirmed impact scope
