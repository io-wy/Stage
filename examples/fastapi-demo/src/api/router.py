from __future__ import annotations

from fastapi import APIRouter

from openagents_orchestration.app.api.routes import agents_router, health_router, items_router, runs_router, workflows_router

api_router = APIRouter()
api_router.include_router(agents_router)
api_router.include_router(runs_router)
api_router.include_router(workflows_router)
api_router.include_router(items_router)

root_router = APIRouter()
root_router.include_router(health_router)
