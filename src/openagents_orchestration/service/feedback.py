"""Feedback capture services for the Stage console."""

from __future__ import annotations

from openagents_orchestration.control.feedback import (
    CaseFeedbackRecord,
    write_feedback_artifacts,
)
from openagents_orchestration.handler.http.schemas import (
    FeedbackRequest,
    FeedbackResponse,
)
from openagents_orchestration.service.common import (
    read_json,
    resolve_path,
    resolve_required_path,
)
from openagents_orchestration.service.settings import DEFAULT_FEEDBACK_ROOT


def record_feedback(request: FeedbackRequest) -> FeedbackResponse:
    governance_path = resolve_required_path(request.governance_path)
    case_result_path = resolve_required_path(request.case_result_path)
    governance = read_json(governance_path)
    case_result = read_json(case_result_path)
    case_id = str(
        governance.get("case_id")
        or case_result.get("case_id")
        or governance_path.parent.name
    )
    record = CaseFeedbackRecord.from_governance(
        case_id=case_id,
        prompt=str(governance.get("routing_prompt", "")),
        governance=governance,
        governed_case_result=case_result,
        labels=request.labels,
        note=request.note,
    )
    output_dir = resolve_path(request.output_dir, DEFAULT_FEEDBACK_ROOT)
    paths = write_feedback_artifacts(record, output_dir=output_dir)
    return FeedbackResponse(
        case_id=case_id,
        labels=record.labels,
        artifact_paths=paths,
    )

