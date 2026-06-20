"""Adversarial + contract tests for OrchestrationMetrics.

Module under test: ``src/openagents_orchestration/enterprise/metrics.py`` (deleted
``test_metrics.py``). A Prometheus-style registry of counters/gauges/histograms
plus a ``from_state`` snapshot updater. ``test_gap_*`` pin counting and
exposition bugs.
"""

from __future__ import annotations

from openagents_orchestration.enterprise.metrics import (
    OrchestrationMetrics,
    _Counter,
    _Gauge,
    _Histogram,
)

# ── primitive metric contracts ────────────────────────────────────────────────


def test_counter_inc_and_get():
    c = _Counter("c", "d", ["proj"])
    c.inc(labels={"proj": "p1"})
    c.inc(labels={"proj": "p1"}, amount=4)
    assert c.get(labels={"proj": "p1"}) == 5.0
    assert c.get(labels={"proj": "p2"}) == 0.0


def test_gauge_set_inc_dec():
    g = _Gauge("g", "d", ["s"])
    g.set(10, labels={"s": "x"})
    g.inc(labels={"s": "x"})
    g.dec(labels={"s": "x"}, amount=3)
    assert g.get(labels={"s": "x"}) == 8.0


def test_histogram_buckets_are_cumulative_with_sum_and_count():
    h = _Histogram("h", "d", [], buckets=(1.0, 5.0, 10.0))
    for v in (0.5, 2.0, 7.0):
        h.observe(v)
    samples = {(mv.name, mv.labels.get("le")): mv.value for mv in h.collect()}
    assert samples[("h_bucket", "1.0")] == 1.0  # only 0.5 <= 1
    assert samples[("h_bucket", "5.0")] == 2.0  # 0.5, 2.0
    assert samples[("h_bucket", "10.0")] == 3.0  # all three
    assert samples[("h_bucket", "+Inf")] == 3.0
    assert samples[("h_sum", None)] == 9.5
    assert samples[("h_count", None)] == 3.0


def test_to_prometheus_basic_shape():
    m = OrchestrationMetrics()
    m.projects_total.set(3, labels={"status": "running"})
    out = m.to_prometheus()
    assert "# TYPE orchestration_projects_total gauge" in out
    assert 'orchestration_projects_total{status="running"} 3' in out


def test_from_state_sets_task_and_budget_gauges():
    m = OrchestrationMetrics()

    class _T:
        def __init__(self, status: str) -> None:
            self.status = status

    class _Budget:
        token_used = 1234

    class _Board:
        project_id = "p1"
        tasks = {"t1": _T("completed"), "t2": _T("completed"), "t3": _T("failed")}
        agents: dict = {}
        budget = _Budget()

    m.from_state(_Board())
    assert m.tasks_total.get(labels={"project_id": "p1", "status": "completed"}) == 2
    assert m.tasks_total.get(labels={"project_id": "p1", "status": "failed"}) == 1
    assert m.budget_tokens_used.get(labels={"project_id": "p1"}) == 1234


# ── GAP: periodic from_state double-counts cumulative llm_calls ───────────────


def test_gap_from_state_double_counts_llm_calls_on_repeated_snapshots():
    """``from_state`` is documented to run periodically ('every 30s'). It does
    ``llm_calls.inc(amount=agent.llm_call_count)`` — but ``llm_call_count`` is
    ALREADY a cumulative running total, so each snapshot re-adds the whole total
    and the counter inflates without bound. (tasks/agents/budget correctly use
    ``.set()``; only llm_calls/llm_latency use the accumulating path.)"""
    m = OrchestrationMetrics()

    class _Agent:
        llm_call_count = 5
        avg_llm_latency_ms = 0

    class _Board:
        project_id = "p1"
        tasks: dict = {}
        agents = {"a1": _Agent()}
        budget = None

    m.from_state(_Board())
    m.from_state(_Board())  # second periodic snapshot
    got = m.llm_calls.get(labels={"project_id": "p1", "agent_id": "a1"})
    assert got == 10.0  # GAP: 5 real calls counted as 10


# ── GAP: typo'd / extra label names silently collapse to the empty series ─────


def test_gap_unknown_label_names_silently_collapse_to_empty_series():
    """``_key`` reads only declared ``label_names``; an extra or misspelled label
    is dropped and missing labels become ``""``. So an observation with a wrong
    label name lands in the empty-label series instead of raising — silently
    misattributed metrics."""
    c = _Counter("c", "d", ["project_id", "topology"])
    c.inc(labels={"projetc_id": "p1", "topology": "p2p"})  # 'project_id' misspelled
    assert c.get(labels={"project_id": "p1", "topology": "p2p"}) == 0.0
    assert c.get(labels={"project_id": "", "topology": "p2p"}) == 1.0  # GAP: landed at ''


# ── GAP: Prometheus label values are not escaped ──────────────────────────────


def test_gap_prometheus_label_values_not_escaped():
    """Label values are interpolated as ``f'{k}="{v}"'`` with no escaping, so a
    value containing a double-quote (or newline) emits malformed exposition that
    a Prometheus scraper rejects."""
    m = OrchestrationMetrics()
    m.budget_tokens_used.set(1, labels={"project_id": 'a"b'})
    out = m.to_prometheus()
    assert 'project_id="a"b"' in out  # GAP: unescaped quote breaks the line
