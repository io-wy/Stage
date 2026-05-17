"""FastAPI application entrypoint."""

from fastapi import FastAPI

from app.api.routes import api_router

app = FastAPI(title="FastAPI 商城后端", version="0.1.0")
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}
