"""Application entrypoint.

Business routes and domain logic will be added in later tasks.
"""

from fastapi import FastAPI

from app.config import settings

app = FastAPI(title=settings.app_name, debug=settings.debug)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Basic health endpoint for startup verification."""

    return {"status": "ok"}
