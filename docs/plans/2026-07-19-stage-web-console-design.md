# Stage Web Console Design

## Goal

Build a local, browser-based console for Stage service governance. The console
should let a user run a real demo case, run a RAG query, inspect each governance
node, and turn feedback labels into governance artifacts without using shell
commands.

## Scope

This is a local demo console, not a production multi-user backend.

In scope:

- FastAPI HTTP adapter over the existing governance and RAG modules.
- Static HTML/CSS/JS served by FastAPI.
- Hard-v2 demo case execution through recorded Claude Code baseline artifacts.
- Real or mock RAG query execution, with Ollama as the default real embedding path.
- Feedback labels written as feedback JSON, governance patch YAML, and regression case JSON.
- File-based artifact storage under `docs/reports/`.

Out of scope:

- User authentication.
- Database persistence.
- Deployment packaging.
- Live Claude Code process control.

## Architecture

The web layer lives under `src/openagents_orchestration/interfaces/`. It is an
adapter layer. It does not own governance decisions, RAG ranking, permission
rules, closure gates, or feedback patch generation.

```text
browser
  -> FastAPI routes
  -> web services
  -> governance / rag domain modules
  -> artifact files
  -> browser timeline / evidence / feedback views
```

## HTTP API

- `GET /api/health`
  - Returns service status and default artifact paths.

- `GET /api/demo-cases`
  - Reads hard-v2 eval specs and returns case id, name, prompt, expected family,
    business process, and expected closure.

- `POST /api/demo-cases/run`
  - Runs one hard-v2 case through `run_stage_governance_eval`.
  - Returns the governed case result, full governance payload, route, permissions,
    evidence, closure, and artifact paths.

- `POST /api/rag/query`
  - Builds or loads a RAG KB for a wiki path.
  - Runs query retrieval through the existing RAG pipeline.
  - Returns top passages with source, score, tags, and score breakdown.

- `POST /api/feedback`
  - Reads a governance artifact and case result artifact.
  - Applies labels and note.
  - Writes feedback artifacts through `write_feedback_artifacts`.

## UI

The first screen is the working console. It has no landing page.

Primary panes:

- Case runner: choose a demo case and run it.
- Pipeline timeline: show intent, domain, route, permission, backend, evidence,
  safety, closure, traceability, and audit status.
- Evidence panel: show sources, summaries, sensitivity, and trace links.
- RAG panel: run a wiki query against a selected wiki path and embedding mode.
- Feedback panel: apply labels to the active run and write feedback artifacts.

The visual style should be quiet and operational: dense, scannable, and built
for repeated inspection. The signature interaction is a node timeline that makes
governance decisions visible.

## Testing

Focused tests cover:

- Health and demo case listing.
- Running a single governance case.
- Running a mock RAG query against a temporary wiki directory.
- Writing feedback artifacts for a run.
