# FastAPI TODO API

FastAPI TODO API project scaffold. This repository currently contains only the base project structure, dependency configuration, environment variable example, and minimal startup health endpoint.

> Current stage: only project structure and configuration are complete. TODO business logic, authentication flows, CRUD endpoints, and persistence behavior are intentionally not implemented yet.

## Tech stack

- FastAPI
- SQLite
- SQLAlchemy 2.x
- Pydantic 2.x / pydantic-settings
- JWT authentication dependencies (`python-jose`, `passlib[bcrypt]`)
- Uvicorn ASGI server

## Project structure

```text
.
├── app/
│   ├── api/
│   │   └── v1/          # Future versioned API route modules
│   ├── core/            # Future security and shared core utilities
│   ├── models/          # Future SQLAlchemy models
│   ├── schemas/         # Future Pydantic schemas
│   ├── services/        # Future business services
│   ├── config.py        # Environment-based settings
│   ├── database.py      # SQLAlchemy engine/session/base setup
│   └── main.py          # FastAPI application entrypoint
├── tests/               # Test package placeholder
├── .env.example         # Environment variable template
├── pyproject.toml       # Python package and dependency configuration
├── requirements.txt     # Runtime dependency pins/ranges
└── README.md
```

## Setup

Requires Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
# Optional editable install with development tools:
python -m pip install -e ".[dev]"
```

## Environment variables

Copy the example file and update secrets before running locally:

```bash
cp .env.example .env
```

Available variables:

| Variable | Default | Description |
| --- | --- | --- |
| `APP_NAME` | `FastAPI TODO API` | FastAPI application title |
| `APP_ENV` | `development` | Runtime environment name |
| `DEBUG` | `true` in `.env.example` | Enables FastAPI debug mode |
| `DATABASE_URL` | `sqlite:///./todo.db` | SQLAlchemy database URL |
| `JWT_SECRET_KEY` | `change-me-in-production` | Secret used for JWT signing; replace in real environments |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | Access token lifetime |

## Run

```bash
uvicorn app.main:app --reload
```

Open:

- API docs: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

## Development checks

```bash
ruff check .
pytest
```
