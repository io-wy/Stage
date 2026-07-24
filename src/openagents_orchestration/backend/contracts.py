"""Backend execution contracts for Stage."""

from __future__ import annotations

from typing import Any, Protocol


class CaseBackend(Protocol):
    execution_mode: str

    def run(
        self,
        *,
        case_id: str,
        run_id: str,
        prompt: str,
        route_plan: Any,
        audit_store: Any,
    ) -> dict[str, Any]:
        """Execute the backend and return a public case result."""
