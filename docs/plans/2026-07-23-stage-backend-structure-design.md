# Stage Backend Structure Design

## Goal

Unify Stage around one governed backend execution point, while letting routing
select the concrete capability behind that point: `rag`, `human`, `subagent`,
or later backends. The backend layer must stay separate from the governance
layer, and external wiki / knowledge content must remain outside git as a
runtime input and runtime cache, not as source code.

## Scope

In scope:

- A single backend contract for governed case execution.
- Backend selection as a routing concern, not a separate product surface.
- RAG output conversion into governance evidence.
- Runtime cache handling for wiki-derived KB artifacts.
- Directory structure that separates adapters, application services, governance,
  backend executors, and knowledge ingestion.

Out of scope:

- Rewriting the UI beyond consuming the new backend shape.
- Moving external wiki content into the repository.
- Introducing a second orchestration engine.

## Architecture

Stage should behave like a service-governance runtime:

```text
request
  -> intent classification
  -> domain resolution
  -> route planning
  -> backend.execute()
  -> evidence / safety / permission / traceability
  -> closure decision
  -> audit + artifacts
```

The backend is one execution point. Routing decides whether that backend acts
as a RAG-backed answerer, a human handoff node, a subagent node, or a
code-oriented node. The governance pipeline stays above the backend and owns
final closure, not the backend itself.

## Backend Contract

Every backend should accept:

- case id and run id
- prompt or request text
- route plan
- audit store
- context and approvals when available

Every backend should return a public result with the same shape:

- `answer`
- `actions`
- `evidence`
- `human_questions`
- `failure_mode`
- `confidence`
- `metadata`

The backend may propose closure, but the governance layer decides whether the
case is actually closed.

## Data Boundaries

### Wiki and KB

- Wiki content stays outside code and outside git.
- Wiki paths are runtime inputs only.
- KB cache files are runtime artifacts.
- Cache paths may be persisted on disk, but they are not source-of-truth
  knowledge.

### RAG Evidence

- RAG passages become governance evidence.
- Evidence must keep source references, snippet summaries, tags, ranking, and
  scoring metadata.
- Public evidence summaries must be redaction-aware.

### Closure

- Closure is governed, not declared by the backend.
- Safety, permissions, verification, and traceability can all block closure.
- A backend may suggest `closed=true`, but the final decision belongs to the
  governance layer.

## Proposed Directory Shape

Keep the current `handler / service / control / rag` split, but make the backend
role explicit:

```text
src/openagents_orchestration/
  handler/            # HTTP + frontend adapters
  service/            # use cases / application entry points
  control/            # governance policy, routing, closure, safety, traceability
  backend/            # governed execution backends
  rag/                # knowledge ingestion, retrieval, chunking, embedding
  runtime/            # long-lived agent runtime / session machinery
```

Suggested responsibility split:

- `handler/`: transport only.
- `service/`: case orchestration entry points and file artifact wiring.
- `control/`: intent, domain, routing, permissions, safety, closure,
  traceability, evidence shaping.
- `backend/`: concrete executors behind one backend interface.
- `rag/`: wiki ingestion, KB build/load, retrieval, and run logs.
- `runtime/`: session/stateboard/orchestrator mechanics unrelated to governed
  case execution.

## Backend Roles

- `rag`: retrieve and summarize from external wiki sources.
- `human`: collect missing fields, approvals, or confirmations.
- `subagent`: handle multi-step service triage or synthesis when a case needs
  deeper reasoning.
- `claude_code`: handle code/workspace-oriented tasks and patch workflows.

These are all backend capabilities, but they are not all equally mature yet.
Some are runtime executors; some are currently planning targets that need a real
implementation path.

## Implementation Constraints

- Do not embed wiki knowledge into code.
- Do not treat KB cache as a source repository.
- Do not let backend-local `closed` override governance closure.
- Keep evidence selection conservative and traceable.
- Keep route planning explainable in audit output.

## Testing

Minimum coverage should verify:

- wiki path resolution uses runtime inputs or `STAGE_WIKI_PATH`
- KB cache paths are created as runtime artifacts
- RAG passages become evidence entries
- public evidence is redacted when needed
- closure can block backend-proposed closure
- route planning can select `rag`, `human_channel`, `subagent`, and
  `claude_code`

