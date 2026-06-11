---
name: impact-scan-pipeline
description: Change impact scan for Python projects. When modifying function signatures, interfaces, data models, config structs, or shared modules, automatically grep all call sites and dependent files to prevent incomplete changes. Outputs a structured markdown impact report.
---

# Impact Scan Pipeline

> Core: "Incomplete change" is a high-frequency AI coding bug — local perspective forgets global impact. This pipeline enforces global scanning.

## Trigger Conditions

- Modify function signatures or interfaces
- Modify data models / Pydantic schemas
- Modify config structures
- Modify shared modules (imported by 3+ files)
- Modify API routes or request/response structures
- User says: "scan impact", "impact analysis", "impact scan", "call sites", "change impact", "改接口", "改模型"

## Execution Flow

### Step 1: Identify Change Type

| Type | Indicator |
|------|-----------|
| Signature change | Parameters, return values, method changes |
| Data model | Pydantic model field add/remove/modify |
| Config change | Config dataclass field add/remove/modify |
| Route change | API path, HTTP method, middleware chain changes |

### Step 2: Run Impact Scan

This skill automatically executes the scan via `run_openagent_skill`.

**Input parameters:**
- `target_path` (str, required) — The file that was changed.
- `project_root` (str, optional) — Project root for scanning. Default `.`.
- `symbols` (list[str], optional) — Specific symbol names to scan. If omitted, extracts functions/classes from the target file automatically.
- `output_path` (str, required) — Where to write the impact report.

**Scan strategy:**
- Grep-based fast scan across all `.py` files
- Classify references: `import` | `call` | `attribute_access` | `string_ref`
- Exclude definitions in the target file itself

### Step 3: Generate Impact Report

The report has two sections per symbol:

| Section | Meaning | Action |
|---------|---------|--------|
| **Direct references** | Import or call sites | Must sync modify |
| **Indirect references** | String matches, attribute access | Needs confirmation |

### Step 4: Confirm with User Before Editing

Show the impact report to the user, confirm scope before starting modifications.

## Example Output

```markdown
# Change Impact Report

- **Target:** `src/openagents_orchestration/state_board.py`
- **Symbols scanned:** Budget, AgentState

## Symbol: `Budget`

### Direct references (must sync modify)

| File | Line | Type | Context |
|------|------|------|---------|
| `src/runner.py` | 89 | import | from state_board import Budget |
| `tests/test_state_board.py` | 15 | call | b = Budget(max_steps=10) |

### Indirect references (2 potential impacts)

| File | Line | Type | Context |
|------|------|------|---------|
| `docs/metrics.md` | 42 | string_ref | "Budget tracks token usage" |
```

## Common Errors

| Error | Consequence | Prevention |
|-------|-------------|------------|
| Only change function, not call sites | ImportError or runtime bug | Step 2 grep scan |
| Change code but not config | Config drift | Step 3 config sync check |
| Start editing before scan completes | Miss associated changes | Step 4 confirm first |

## Relationship to Other Skills

- **pre-verify** (before) — prevents structural violations before they happen
- **change-impact-scan** (after) — scans impact of changes already made

## Verification Checklist

- [ ] Change type identified
- [ ] Impact scan executed
- [ ] Report output generated
- [ ] User confirmed impact scope
