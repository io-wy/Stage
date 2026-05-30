# FastAPI Implementation Plan

## 1. Existing FastAPI app structure

Current web layer is under `src/openagents_orchestration/app`:

```text
src/openagents_orchestration/app/
├── __init__.py
├── main.py
├── init_db.py
├── api/
│   ├── __init__.py
│   └── routes/
│       ├── __init__.py
│       ├── health.py
│       └── items.py
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   └── security.py
├── models/
│   ├── __init__.py
│   └── item.py
└── schemas/
    ├── __init__.py
    └── item.py
```

## 2. Existing capabilities

- `main.py`: `create_app()`, FastAPI lifespan, CORS, router registration, startup table creation.
- `core/config.py`: Pydantic settings loaded from env / `.env`; project name, API prefix, CORS, SQLite DB URL, JWT settings.
- `core/database.py`: SQLAlchemy `Base`, engine, `SessionLocal`, `get_db()` dependency, `session_scope()` helper.
- `core/security.py`: bcrypt password hashing/verification and JWT create/decode helpers.
- `api/routes/health.py`: `GET /health` returns `{"status": "ok"}`.
- `api/routes/items.py`: sample DB-backed item endpoints under `/api/v1/items/`:
  - `POST /api/v1/items/`
  - `GET /api/v1/items/`
- `models/item.py`, `schemas/item.py`: minimal example ORM and Pydantic schemas.
- `init_db.py`: explicit metadata table creation script.

## 3. Assumptions / constraints

- Keep implementation self-contained for local and CI use.
- No external DB is required: use configured SQLite by default, and prefer in-memory SQLite for API tests.
- Available memory/state services can be exposed without adding infrastructure:
  - `StateBoard` is an in-process orchestration state panel.
  - `CoreCoderMemory` stores session memory as local JSON files.
  - Existing persistence modules are file/event oriented and should remain optional.
- Avoid long-running orchestration work inside request handlers unless explicitly marked as a background operation.

## 4. Proposed t2-t9 implementation scope

| Task | Implementation scope | API path suggestions | Test scope | Docs scope |
| --- | --- | --- | --- | --- |
| t2 | Add API package conventions: shared response/error schemas, dependency helpers, router registration pattern. | Keep `/health`; reserve `/api/v1/*` for versioned API. | App factory imports, OpenAPI generation, health smoke test. | Document local run command and API prefix. |
| t3 | Add orchestration domain schemas for tasks, agents, artifacts, budget, events, and delivery reports based on existing dataclasses. | `GET /api/v1/state`, `GET /api/v1/tasks`, `GET /api/v1/tasks/{task_id}` | Schema serialization and empty-state responses. | Document state/task response shapes. |
| t4 | Expose read-only StateBoard-backed endpoints. Use a process-local board provider/dependency with safe defaults. | `GET /api/v1/agents`, `GET /api/v1/artifacts`, `GET /api/v1/events`, `GET /api/v1/budget` | In-memory board fixtures; verify filtering and 404 behavior. | Document in-memory lifecycle and non-persistence caveat. |
| t5 | Add controlled task lifecycle endpoints for adding tasks and updating status, without launching external agents. | `POST /api/v1/tasks`, `PATCH /api/v1/tasks/{task_id}` | Validation, dependency IDs, status transitions, error cases. | Document task creation/update examples. |
| t6 | Add message/human-question endpoints backed by StateBoard mailbox/question queues. | `GET /api/v1/messages`, `POST /api/v1/messages`, `GET /api/v1/human-questions`, `POST /api/v1/human-questions/{id}/reply` | Queue behavior, idempotent reads/replies, invalid IDs. | Document mailbox and human reply workflow. |
| t7 | Add memory endpoints for local CoreCoderMemory inspection with path/session safeguards. | `GET /api/v1/memory/{session_id}`, `GET /api/v1/memory/{session_id}/search?q=...` | Safe session IDs, missing memory, search results. | Document local JSON storage and privacy note. |
| t8 | Add optional DB-backed sample/resources cleanup or replace `items` with a clearly marked example module. | Keep `GET/POST /api/v1/items/` as example or move under `/api/v1/examples/items/`. | SQLite in-memory CRUD tests and DB dependency override. | Document sample endpoint status. |
| t9 | Final integration hardening: app startup, CORS/settings, error handling, test fixtures, README/API docs. | Verify all routes in generated OpenAPI. | Full FastAPI test suite with `TestClient`, no external services. | Create/update API usage docs and implementation notes. |

## 5. Testing strategy

- Use `fastapi.testclient.TestClient` against `create_app()`.
- Override DB dependency for tests using SQLite in-memory or temporary SQLite files.
- Use in-memory/process-local StateBoard fixtures for orchestration endpoints.
- Cover:
  - health endpoint;
  - OpenAPI generation;
  - CRUD/sample item endpoints;
  - state/task/agent/event serialization;
  - validation and 404/422 errors;
  - memory endpoint safety and missing-session behavior.

## 6. Documentation deliverables

- This plan: `docs/fastapi_implementation_plan.md`.
- Later docs should include:
  - local run instructions (`uvicorn openagents_orchestration.app.main:app --reload` or equivalent with `PYTHONPATH=src`);
  - env variables (`APP_DATABASE_URL`, JWT settings, CORS);
  - endpoint summary and examples;
  - explicit note that default operation requires no external DB/service.
