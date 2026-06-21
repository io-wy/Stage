"""Contract tests for the OrchestrationEvent enum.

Module under test: ``src/openagents_orchestration/enterprise/events.py`` (deleted
``test_events.py``). A pure StrEnum with ``category`` / ``is_project_level`` /
``is_global_level`` classifiers. Low bug surface, but ``test_gap_*`` pin
classification inconsistencies that matter to event-routing code.
"""

from __future__ import annotations

from openagents_orchestration.projects.events import OrchestrationEvent

# ── contract ──────────────────────────────────────────────────────────────────


def test_category_is_dotted_prefix():
    assert OrchestrationEvent.PROJECT_CREATED.category == "project"
    assert OrchestrationEvent.AGENT_HEARTBEAT_TIMEOUT.category == "agent"
    assert OrchestrationEvent.CROSS_PROJECT_MESSAGE.category == "cross_project"
    assert OrchestrationEvent.DLQ_MESSAGE_DETECTED.category == "dlq"


def test_all_event_values_are_unique():
    """A duplicated value would silently become a StrEnum alias and shrink the
    member list — guard against that."""
    members = list(OrchestrationEvent)
    values = [e.value for e in members]
    assert len(set(values)) == len(members)


def test_representative_project_level_flags():
    assert OrchestrationEvent.TASK_COMPLETED.is_project_level is True
    assert OrchestrationEvent.MESSAGE_DLQ.is_project_level is True
    assert OrchestrationEvent.TRACE_STARTED.is_project_level is True


def test_global_level_set():
    assert OrchestrationEvent.PROJECT_FAILED.is_global_level is True
    assert OrchestrationEvent.BUDGET_EXHAUSTED.is_global_level is True
    assert OrchestrationEvent.TASK_COMPLETED.is_global_level is False


# ── GAP: 'global' events are NOT a subset of 'project-level' events ───────────


def test_gap_some_global_events_are_not_project_level():
    """Code that assumes global events bubble UP from the project level breaks
    for these two: they are flagged global but their category
    ('cross_project' / 'dlq') is absent from the project-level set, so they are
    never project-visible. The two classifiers are independent, not nested."""
    for ev in (
        OrchestrationEvent.CROSS_PROJECT_MESSAGE,
        OrchestrationEvent.DLQ_MESSAGE_DETECTED,
    ):
        assert ev.is_global_level is True
        assert ev.is_project_level is False  # GAP: global but not project-level


def test_gap_two_dlq_events_classified_inconsistently():
    """There are two dead-letter events with split classification: ``MESSAGE_DLQ``
    (message.dlq) is project-level but NOT global, while ``DLQ_MESSAGE_DETECTED``
    (dlq.message_detected) is global but NOT project-level. Any DLQ dashboard
    must subscribe to both, via different level filters — a dual source of
    truth for 'a message was dead-lettered'."""
    assert OrchestrationEvent.MESSAGE_DLQ.is_project_level is True
    assert OrchestrationEvent.MESSAGE_DLQ.is_global_level is False
    assert OrchestrationEvent.DLQ_MESSAGE_DETECTED.is_project_level is False
    assert OrchestrationEvent.DLQ_MESSAGE_DETECTED.is_global_level is True
