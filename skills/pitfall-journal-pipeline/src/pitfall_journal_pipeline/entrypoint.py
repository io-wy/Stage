"""Pitfall journal entrypoint.

Records PIT (pitfall) entries and tracks evolution:
pitfall -> rule -> skill.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PIT_TYPES = {
    "incomplete_change",
    "hallucination",
    "env_blindness",
    "pattern_over_verification",
    "cant_say_idk",
    "concurrency",
    "resource_leak",
    "missing_error_handling",
    "protocol_drift",
    "review_false_positive",
}


@dataclass
class PitEntry:
    pit_id: str
    title: str
    date: str
    pit_type: str
    severity: str
    scenario: str
    phenomenon: str
    root_cause: str
    fix: str
    rule_ref: str = ""
    status: str = "new"  # new | ruled | skilled | covered


_JOURNAL_PATH = Path(".claude/pitfall-journal.json")


def _load_journal() -> list[PitEntry]:
    if not _JOURNAL_PATH.exists():
        return []
    try:
        data = json.loads(_JOURNAL_PATH.read_text(encoding="utf-8"))
        return [PitEntry(**item) for item in data]
    except Exception:
        return []


def _save_journal(entries: list[PitEntry]) -> None:
    _JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _JOURNAL_PATH.write_text(
        json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _next_id(entries: list[PitEntry]) -> str:
    max_num = 0
    for e in entries:
        try:
            num = int(e.pit_id.replace("PIT-", ""))
            max_num = max(max_num, num)
        except ValueError:
            pass
    return f"PIT-{max_num + 1:03d}"


def _count_by_type(entries: list[PitEntry], pit_type: str) -> int:
    return sum(1 for e in entries if e.pit_type == pit_type)


async def run_openagent_skill(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the pitfall journal pipeline.

    Modes:
      - action="record": append a new PIT entry
      - action="list": list all entries (optionally filtered by type)
      - action="evolve": check if a type has reached evolution threshold

    Returns appropriate dict per action.
    """
    action = payload.get("action", "record")
    entries = _load_journal()

    if action == "record":
        pit_type = payload.get("pit_type", "")
        if pit_type not in PIT_TYPES:
            raise ValueError(
                f"Unknown pit_type: {pit_type}. Valid: {', '.join(sorted(PIT_TYPES))}"
            )

        entry = PitEntry(
            pit_id=_next_id(entries),
            title=payload.get("title", "Untitled"),
            date=datetime.now().strftime("%Y-%m-%d"),
            pit_type=pit_type,
            severity=payload.get("severity", "Medium"),
            scenario=payload.get("scenario", ""),
            phenomenon=payload.get("phenomenon", ""),
            root_cause=payload.get("root_cause", ""),
            fix=payload.get("fix", ""),
            rule_ref=payload.get("rule_ref", ""),
            status="new",
        )
        entries.append(entry)
        _save_journal(entries)

        total_same_type = _count_by_type(entries, pit_type)
        evolution_msg = ""
        if total_same_type >= 2:
            evolution_msg = (
                f"Same-type PIT count = {total_same_type} (>= 2). "
                "Consider extracting a CLAUDE.md constraint rule."
            )

        return {
            "pit_id": entry.pit_id,
            "total_entries": len(entries),
            "same_type_count": total_same_type,
            "evolution_hint": evolution_msg,
        }

    elif action == "list":
        pit_type = payload.get("pit_type")
        filtered = [e for e in entries if not pit_type or e.pit_type == pit_type]
        return {
            "count": len(filtered),
            "entries": [
                {
                    "pit_id": e.pit_id,
                    "title": e.title,
                    "date": e.date,
                    "type": e.pit_type,
                    "severity": e.severity,
                    "status": e.status,
                }
                for e in filtered
            ],
        }

    elif action == "evolve":
        pit_type = payload.get("pit_type")
        if pit_type:
            count = _count_by_type(entries, pit_type)
            return {
                "pit_type": pit_type,
                "count": count,
                "should_extract_rule": count >= 2,
                "should_skillify": count >= 4,
            }
        # All types summary
        summary: dict[str, int] = {}
        for e in entries:
            summary[e.pit_type] = summary.get(e.pit_type, 0) + 1
        return {
            "summary": summary,
            "types_ready_for_rule": [t for t, c in summary.items() if c >= 2],
            "types_ready_for_skill": [t for t, c in summary.items() if c >= 4],
        }

    else:
        raise ValueError(f"Unknown action: {action}. Use: record | list | evolve")
