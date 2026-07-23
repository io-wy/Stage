"""FastAPI app for the Stage local web console."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from openagents_orchestration.handler.http.schemas import (
    AuditEventResponse,
    DemoCaseSummary,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    RagQueryRequest,
    RagQueryResponse,
    RunDemoCaseRequest,
    RunDemoCaseResponse,
    RunDetailResponse,
    RunGovernanceRequest,
    RunGovernanceResponse,
    RunHistoryItem,
)
from openagents_orchestration.service.console import (
    get_run_audit,
    get_run_detail,
    health_payload,
    list_demo_cases,
    list_run_history,
    query_rag,
    record_feedback,
    run_demo_case,
    run_governance_case,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Stage Local Governance Console")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> dict[str, str]:
        return health_payload()

    @app.get("/api/demo-cases", response_model=list[DemoCaseSummary])
    def demo_cases() -> list[DemoCaseSummary]:
        try:
            return list_demo_cases()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs", response_model=list[RunHistoryItem])
    def runs(output_root: str | None = None) -> list[RunHistoryItem]:
        return list_run_history(output_root)

    @app.get("/api/runs/{run_key}", response_model=RunDetailResponse)
    def run_detail(run_key: str, output_root: str | None = None) -> RunDetailResponse:
        try:
            return get_run_detail(run_key, output_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_key}/audit", response_model=list[AuditEventResponse])
    def run_audit(
        run_key: str,
        output_root: str | None = None,
    ) -> list[AuditEventResponse]:
        try:
            return get_run_audit(run_key, output_root)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/demo-cases/run", response_model=RunDemoCaseResponse)
    def run_case(request: RunDemoCaseRequest) -> RunDemoCaseResponse:
        try:
            return run_demo_case(request)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/governance/run", response_model=RunGovernanceResponse)
    def run_governance(request: RunGovernanceRequest) -> RunGovernanceResponse:
        try:
            return run_governance_case(request)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/rag/query", response_model=RagQueryResponse)
    async def run_rag(request: RagQueryRequest) -> RagQueryResponse:
        try:
            return await query_rag(request)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/feedback", response_model=FeedbackResponse)
    def feedback(request: FeedbackRequest) -> FeedbackResponse:
        try:
            return record_feedback(request)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "openagents_orchestration.handler.http.app:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
    )


if __name__ == "__main__":
    main()
