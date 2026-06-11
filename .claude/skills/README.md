# Xitai Project Skills

Project-level skills for the Xitai (戏台) multi-agent orchestration engine.
These are auto-discovered by Claude Code when working in this directory.

## Skill Index

| Skill | Type | Trigger | Description |
|-------|------|---------|-------------|
| [adversarial-review](adversarial-review.md) | Core | 5+ files / 200+ lines / "对抗审查" | Cross-model adversarial code review to catch single-model blind spots |
| [brainstorming](brainstorming.md) | Core | New feature / bug fix / refactor | Structured requirement clarification and solution design |
| [change-impact-scan](change-impact-scan.md) | Core | Modify signatures / models / configs | Grep scan all call sites to prevent incomplete changes |
| [pitfall-journal](pitfall-journal.md) | Core | AI mistake / user correction | Record PIT entries and track evolution: pitfall -> rule -> skill |
| [pre-verify](pre-verify.md) | Core | Create files / add imports | Pre-verify structural operations before they happen |
| [task-orchestrator-dev](task-orchestrator-dev/SKILL.md) | Core | Changes to src/openagents_orchestration/ | Development guide for the orchestration engine itself |

## Usage

Skills are triggered automatically by Claude Code based on keywords and context.
No manual invocation needed -- just describe what you want to do, and the
relevant skill guides the workflow.

## Borrowed from

- `adversarial-review`, `change-impact-scan`, `brainstorming`, `pitfall-journal`, `pre-verify`
  adapted from [Coding-Vibe-Go](https://github.com/io-wy/Coding-Vibe-Go.git)
