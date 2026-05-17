"""Top-level API router."""

from fastapi import APIRouter

api_router = APIRouter()


@api_router.get("/", tags=["root"])
def root() -> dict[str, str]:
    return {"message": "FastAPI 商城后端 API"}
