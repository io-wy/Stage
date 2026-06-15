"""Tests for Team and TeamSpec."""

from __future__ import annotations

import pytest

from openagents_orchestration.core.state_board import StateBoard
from openagents_orchestration.core.sub_state_board import SubStateBoard
from openagents_orchestration.enterprise.team import Team, TeamSpec, TeamStatus
from openagents_orchestration.transport.channel_policy import (
    ChannelPolicy,
)


class TestTeamSpec:
    def test_defaults(self):
        spec = TeamSpec(name="backend")
        assert spec.name == "backend"
        assert spec.agent_types == ["coder", "reviewer"]
        assert spec.max_workers == 5
        assert spec.channel_policy is None

    def test_roundtrip_dict(self):
        spec = TeamSpec(
            name="docs",
            agent_types=["coder"],
            max_workers=3,
            channel_policy=ChannelPolicy({"coder-*": {"team_leader"}}),
        )
        d = spec.to_dict()
        restored = TeamSpec.from_dict(d)
        assert restored.name == "docs"
        assert restored.agent_types == ["coder"]
        assert restored.max_workers == 3
        assert restored.channel_policy is not None


class TestTeam:
    def _make_team(self, name="backend"):
        parent = StateBoard("test objective", echo=False)
        sub = SubStateBoard(parent=parent, objective="test")
        return Team(
            team_id="team-abc123",
            project_id="proj-xyz",
            name=name,
            sub_state_board=sub,
        )

    def test_creation(self):
        team = self._make_team()
        assert team.team_id == "team-abc123"
        assert team.project_id == "proj-xyz"
        assert team.name == "backend"
        assert team.status == TeamStatus.IDLE
        assert team.leader_id is None
        assert len(team.workers) == 0

    def test_lifecycle(self):
        team = self._make_team()
        team.start()
        assert team.status == TeamStatus.RUNNING
        team.pause()
        assert team.status == TeamStatus.PAUSED
        team.resume()
        assert team.status == TeamStatus.RUNNING

    def test_can_communicate_default_policy(self):
        team = self._make_team()
        assert team.can_communicate("team_leader", "coder-1") is True
        assert team.can_communicate("coder-1", "team_leader") is True
        assert team.can_communicate("coder-1", "reviewer-1") is True
        assert team.can_communicate("coder-1", "director") is False

    def test_add_remove_worker(self):
        team = self._make_team()
        from unittest.mock import MagicMock

        worker = MagicMock()
        worker.resident_id = "coder-t1"
        worker.state.status = "idle"
        team.add_worker(worker)
        assert "coder-t1" in team.workers
        assert len(team.list_workers()) == 1

        removed = team.remove_worker("coder-t1")
        assert removed is worker
        assert len(team.workers) == 0

    def test_list_workers_by_status(self):
        team = self._make_team()
        from unittest.mock import MagicMock

        w1 = MagicMock()
        w1.resident_id = "c1"
        w1.state.status = "busy"
        w2 = MagicMock()
        w2.resident_id = "c2"
        w2.state.status = "idle"
        team.add_worker(w1)
        team.add_worker(w2)

        busy = team.list_workers(status="busy")
        assert len(busy) == 1
        assert busy[0].resident_id == "c1"

    def test_worker_limit(self):
        team = self._make_team()
        from unittest.mock import MagicMock

        for i in range(10):
            w = MagicMock()
            w.resident_id = f"c{i}"
            w.state.status = "idle"
            team.add_worker(w)

        with pytest.raises(RuntimeError):
            w = MagicMock()
            w.resident_id = "overflow"
            w.state.status = "idle"
            team.add_worker(w)

    def test_roundtrip_dict(self):
        team = self._make_team()
        team.start()
        team.leader_id = "tl-1"
        d = team.to_dict()
        restored = Team.from_dict(d)
        assert restored.team_id == team.team_id
        assert restored.project_id == team.project_id
        assert restored.name == team.name
        assert restored.status == team.status
        assert restored.leader_id == "tl-1"
