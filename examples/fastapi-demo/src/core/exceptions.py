from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str


class NotFoundError(Exception):
    def __init__(self, detail: str):
        self.detail = detail


class ConflictError(Exception):
    def __init__(self, detail: str):
        self.detail = detail


async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": exc.detail})


async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.detail})
