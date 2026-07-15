"""Metrics — lightweight observability for enterprise orchestration.

Collects counters, gauges, and histograms without external dependencies.
Compatible with Prometheus exposition format for easy integration.

Metrics:
- orchestration_projects_total (gauge)     — by status
- orchestration_teams_total (gauge)        — by project_id, status
- orchestration_agents_total (gauge)       — by project_id, team_id, status
- orchestration_tasks_total (gauge)        — by project_id, status
- orchestration_heartbeat_latency_ms       — by project_id, agent_id
- orchestration_budget_tokens_used         — by project_id
- orchestration_llm_calls_total            — by project_id, agent_id
- orchestration_llm_latency_ms             — by project_id, agent_id
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricValue:
    """A single metric sample with labels."""

    name: str
    labels: dict[str, str]
    value: float
    timestamp: float = field(default_factory=time.time)


class _Counter:
    """Monotonically increasing counter."""

    def __init__(self, name: str, description: str, label_names: list[str]):
        self.name = name
        self.description = description
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}

    def inc(self, *, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        self._values[key] = self._values.get(key, 0.0) + amount

    def set(self, value: float, *, labels: dict[str, str] | None = None) -> None:
        """Set the counter to an absolute value.

        Counters are normally incremented, but ``from_state`` is called
        periodically with already-cumulative values; ``set`` avoids double
        counting on each snapshot.
        """
        self._values[self._key(labels)] = value

    def get(self, *, labels: dict[str, str] | None = None) -> float:
        return self._values.get(self._key(labels), 0.0)

    def collect(self) -> list[MetricValue]:
        return [
            MetricValue(
                name=self.name,
                labels=dict(zip(self.label_names, key, strict=True)),
                value=v,
            )
            for key, v in self._values.items()
        ]

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if labels is None:
            labels = {}
        return tuple(str(labels.get(k, "")) for k in self.label_names)


class _Gauge:
    """Gauge that can go up and down."""

    def __init__(self, name: str, description: str, label_names: list[str]):
        self.name = name
        self.description = description
        self.label_names = label_names
        self._values: dict[tuple[str, ...], float] = {}

    def set(self, value: float, *, labels: dict[str, str] | None = None) -> None:
        self._values[self._key(labels)] = value

    def inc(self, *, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, *, labels: dict[str, str] | None = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        self._values[key] = self._values.get(key, 0.0) - amount

    def get(self, *, labels: dict[str, str] | None = None) -> float:
        return self._values.get(self._key(labels), 0.0)

    def collect(self) -> list[MetricValue]:
        return [
            MetricValue(
                name=self.name,
                labels=dict(zip(self.label_names, key, strict=True)),
                value=v,
            )
            for key, v in self._values.items()
        ]

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if labels is None:
            labels = {}
        return tuple(str(labels.get(k, "")) for k in self.label_names)


class _Histogram:
    """Histogram for latency / size distributions."""

    DEFAULT_BUCKETS = (
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
        2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0,
    )

    def __init__(
        self,
        name: str,
        description: str,
        label_names: list[str],
        buckets: tuple[float, ...] | None = None,
    ):
        self.name = name
        self.description = description
        self.label_names = label_names
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._counts: dict[tuple[str, ...], list[int]] = {}
        self._sums: dict[tuple[str, ...], float] = {}
        self._totals: dict[tuple[str, ...], int] = {}

    def observe(self, value: float, *, labels: dict[str, str] | None = None) -> None:
        key = self._key(labels)
        if key not in self._counts:
            self._counts[key] = [0] * len(self.buckets)
            self._sums[key] = 0.0
            self._totals[key] = 0

        for i, bucket in enumerate(self.buckets):
            if value <= bucket:
                self._counts[key][i] += 1
        self._sums[key] += value
        self._totals[key] += 1

    def collect(self) -> list[MetricValue]:
        results: list[MetricValue] = []
        for key, counts in self._counts.items():
            labels = dict(zip(self.label_names, key, strict=True))
            for i, bucket in enumerate(self.buckets):
                bucket_labels = {**labels, "le": str(bucket)}
                results.append(
                    MetricValue(
                        name=f"{self.name}_bucket",
                        labels=bucket_labels,
                        value=float(counts[i]),
                    )
                )
            # +Inf bucket = total count
            results.append(
                MetricValue(
                    name=f"{self.name}_bucket",
                    labels={**labels, "le": "+Inf"},
                    value=float(self._totals[key]),
                )
            )
            results.append(
                MetricValue(
                    name=f"{self.name}_sum",
                    labels=labels,
                    value=self._sums[key],
                )
            )
            results.append(
                MetricValue(
                    name=f"{self.name}_count",
                    labels=labels,
                    value=float(self._totals[key]),
                )
            )
        return results

    def _key(self, labels: dict[str, str] | None) -> tuple[str, ...]:
        if labels is None:
            labels = {}
        return tuple(str(labels.get(k, "")) for k in self.label_names)


class OrchestrationMetrics:
    """Central metrics registry for the orchestration engine.

    Usage::

        metrics = OrchestrationMetrics()
        metrics.projects_total.set(3, labels={"status": "running"})
        metrics.messages_delivered.inc(labels={"project_id": "p1", "topology": "p2p"})
        metrics.heartbeat_latency.observe(0.045, labels={"project_id": "p1", "agent_id": "a1"})
    """

    def __init__(self) -> None:
        self.projects_total = _Gauge(
            "orchestration_projects_total",
            "Number of projects by status",
            ["status"],
        )
        self.teams_total = _Gauge(
            "orchestration_teams_total",
            "Number of teams by project and status",
            ["project_id", "status"],
        )
        self.agents_total = _Gauge(
            "orchestration_agents_total",
            "Number of agents by project, team and status",
            ["project_id", "team_id", "status"],
        )
        self.tasks_total = _Gauge(
            "orchestration_tasks_total",
            "Number of tasks by project and status",
            ["project_id", "status"],
        )
        self.heartbeat_latency = _Histogram(
            "orchestration_heartbeat_latency_ms",
            "Heartbeat latency in milliseconds",
            ["project_id", "agent_id"],
            buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
        )
        self.budget_tokens_used = _Gauge(
            "orchestration_budget_tokens_used",
            "Token budget consumed",
            ["project_id"],
        )
        self.llm_calls = _Counter(
            "orchestration_llm_calls_total",
            "Total LLM API calls",
            ["project_id", "agent_id"],
        )
        self.llm_latency = _Histogram(
            "orchestration_llm_latency_ms",
            "LLM call latency in milliseconds",
            ["project_id", "agent_id"],
            buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
        )

    def collect(self) -> list[MetricValue]:
        """Collect all metric samples."""
        results: list[MetricValue] = []
        for metric in (
            self.projects_total,
            self.teams_total,
            self.agents_total,
            self.tasks_total,
            self.heartbeat_latency,
            self.budget_tokens_used,
            self.llm_calls,
            self.llm_latency,
        ):
            results.extend(metric.collect())
        return results

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text exposition format."""
        lines: list[str] = []
        metric_groups: dict[str, list[MetricValue]] = {}
        for mv in self.collect():
            # Strip suffixes for grouping (_bucket, _sum, _count)
            base = mv.name.split("_bucket")[0].split("_sum")[0].split("_count")[0]
            metric_groups.setdefault(base, []).append(mv)

        # Map metric name -> (type, description)
        type_map: dict[str, tuple[str, str]] = {}
        for metric in (
            self.projects_total, self.teams_total, self.agents_total,
            self.tasks_total, self.heartbeat_latency, self.budget_tokens_used,
            self.llm_calls, self.llm_latency,
        ):
            mtype = "gauge"
            if isinstance(metric, _Counter):
                mtype = "counter"
            elif isinstance(metric, _Histogram):
                mtype = "histogram"
            type_map[metric.name] = (mtype, metric.description)

        for name, values in metric_groups.items():
            if not values:
                continue
            mtype, desc = type_map.get(name, ("gauge", ""))
            lines.append(f"# HELP {name} {desc}")
            lines.append(f"# TYPE {name} {mtype}")
            for mv in values:
                label_str = ",".join(
                    f'{k}="{v}"' for k, v in mv.labels.items()
                )
                if label_str:
                    lines.append(f"{mv.name}{{{label_str}}} {mv.value}")
                else:
                    lines.append(f"{mv.name} {mv.value}")
            lines.append("")
        return "\n".join(lines)

    def from_state(
        self,
        state_board: Any,
        project_id: str = "",
    ) -> None:
        """Update metrics from a StateBoard snapshot.

        Call this periodically (e.g. every 30s) to keep metrics current.
        """
        pid = project_id or getattr(state_board, "project_id", "") or "default"

        # Task counts by status
        tasks_by_status: dict[str, int] = {}
        for task in getattr(state_board, "tasks", {}).values():
            status = str(getattr(task, "status", "unknown"))
            tasks_by_status[status] = tasks_by_status.get(status, 0) + 1
        for status, count in tasks_by_status.items():
            self.tasks_total.set(count, labels={"project_id": pid, "status": status})

        # Agent counts by status
        agents_by_status: dict[str, int] = {}
        for agent in getattr(state_board, "agents", {}).values():
            status = str(getattr(agent, "status", "unknown"))
            agents_by_status[status] = agents_by_status.get(status, 0) + 1
        for status, count in agents_by_status.items():
            self.agents_total.set(
                count, labels={"project_id": pid, "team_id": "", "status": status}
            )

        # Budget tokens
        budget = getattr(state_board, "budget", None)
        if budget is not None:
            self.budget_tokens_used.set(
                getattr(budget, "token_used", 0),
                labels={"project_id": pid},
            )

        # LLM metrics from agent states (cumulative values, so use set() instead
        # of inc() to avoid double-counting on periodic snapshots).
        for agent_id, agent in getattr(state_board, "agents", {}).items():
            llm_count = getattr(agent, "llm_call_count", 0)
            if llm_count > 0:
                self.llm_calls.set(
                    float(llm_count),
                    labels={"project_id": pid, "agent_id": agent_id},
                )
            avg_latency = getattr(agent, "avg_llm_latency_ms", 0)
            if avg_latency > 0:
                self.llm_latency.observe(
                    avg_latency,
                    labels={"project_id": pid, "agent_id": agent_id},
                )
