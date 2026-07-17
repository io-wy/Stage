"""Build deterministic context packs for case-handling tasks."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from eval.case_handling.schema import CaseSpec


def _normalize_source_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, str):
        refs = [value]
    elif isinstance(value, Iterable):
        refs = [str(item) for item in value if str(item).strip()]
    return list(dict.fromkeys(refs))


def _extract_source_refs(context_pack: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    if "source_refs" in context_pack:
        refs.extend(_normalize_source_refs(context_pack.get("source_refs")))
    if "sources" in context_pack:
        refs.extend(_normalize_source_refs(context_pack.get("sources")))
    return list(dict.fromkeys(refs))


def render_context_prompt(case: CaseSpec) -> str:
    """Render a stable prompt block for a single case."""

    context_json = json.dumps(
        case.context_pack, ensure_ascii=False, indent=2, sort_keys=True
    )
    verifier_json = json.dumps(case.verifier, ensure_ascii=False, indent=2, sort_keys=True)
    criteria_block = "\n".join(f"- {item}" for item in case.success_criteria)
    tool_block = "\n".join(f"- {item}" for item in case.tool_limits) or "- (none)"

    return (
        f"case_id: {case.case_id}\n"
        f"family: {case.family}\n"
        f"difficulty: {case.difficulty}\n"
        f"source: {case.source}\n"
        f"objective: {case.objective}\n\n"
        "context_pack:\n"
        f"{context_json}\n\n"
        "success criteria:\n"
        f"{criteria_block}\n\n"
        "tool limits:\n"
        f"{tool_block}\n\n"
        "verifier:\n"
        f"{verifier_json}\n"
    )


def build_context_pack(case: CaseSpec) -> dict[str, Any]:
    """Build a deterministic, serializable context pack."""

    source_refs = _extract_source_refs(case.context_pack)
    prompt = render_context_prompt(case)
    return {
        "case_id": case.case_id,
        "family": case.family,
        "difficulty": case.difficulty,
        "source": case.source,
        "objective": case.objective,
        "source_refs": source_refs,
        "tool_limits": list(case.tool_limits),
        "success_criteria": list(case.success_criteria),
        "verifier": json.loads(json.dumps(case.verifier, ensure_ascii=False, sort_keys=True)),
        "context_pack": json.loads(
            json.dumps(case.context_pack, ensure_ascii=False, sort_keys=True)
        ),
        "prompt": prompt,
    }

