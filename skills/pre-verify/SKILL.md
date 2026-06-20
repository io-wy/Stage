---
name: pre-verify
description: >
  Pre-verification before structural operations. Before creating files in new
  locations or adding cross-package imports, verify layer legitimacy and naming
  conventions. Prevents violations before they happen rather than catching them
  after.
---

# Pre-Verify

> Core: Rather than writing 50 lines of code and getting blocked by a linter,
> spend 2 interactions confirming legitimacy before acting.

## Trigger Conditions

- Create files in new directories
- Add cross-package imports (especially cross-layer imports)
- Modify public interfaces / function signatures

## No Pre-Verification Needed

- Modify function bodies (no new imports)
- Add test files in same directory
- Fix typos / logs / pure documentation

## Pre-Verify Flow

### Step 1: Identify Operation Type

- Create file -> Check target directory level + naming convention
- Add import -> Check direction (higher -> lower is legal, reverse is illegal)

### Step 2: Execute Verification

```
Operation: Create file in src/openagents_orchestration/models/ and import from runner.py
Verification: models is a lower-level module, runner can import it -> OK

Operation: src/openagents_orchestration/models/ imports from runner.py
Verification: models -> runner is reverse dependency -> ILLEGAL
  Fix: Move the dependency logic to a higher layer, or pass as parameter
```

### Step 3: Error Messages Must Include Three Elements

1. What rule was violated
2. Why it is a problem
3. How to fix it

## Relationship to change-impact-scan

pre-verify (before) prevents violations from happening.
change-impact-scan (after) scans impact of changes already made.
