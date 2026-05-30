from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openagents_orchestration.app.api.routes import health_router, items_router
from openagents_orchestration.app.core.config import get_settings
from openagents_orchestration.app.core.database import Base, engine

# Ensure models are imported so Base.metadata is populated.
import openagents_orchestration.app.models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Create DB tables on startup (for apps without migrations)."""

    Base.metadata.create_all(bind=engine)
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(items_router, prefix=settings.API_V1_STR)

    return app


app = create_app()
