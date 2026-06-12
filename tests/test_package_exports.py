"""Tests that all public API classes are importable from the package root."""

from __future__ import annotations

import pytest


class TestPackageExports:
    def test_lazy_imports(self):
        from openagents_orchestration import (
            AgentIdentity,
            AuditLog,
            CapabilityToken,
            ChannelPolicy,
            GlobalOrchestrator,
            HumanChannel,
            MonitorAgent,
            OrchestrationEvent,
            OrchestrationMetrics,
            OrchestratorRunner,
            Project,
            StateBoard,
            Team,
            TeamSpec,
        )

        assert OrchestratorRunner is not None
        assert StateBoard is not None
        assert GlobalOrchestrator is not None
        assert Project is not None
        assert Team is not None
        assert TeamSpec is not None
        assert HumanChannel is not None
        assert MonitorAgent is not None
        assert ChannelPolicy is not None
        assert OrchestrationEvent is not None
        assert CapabilityToken is not None
        assert AgentIdentity is not None
        assert AuditLog is not None
        assert OrchestrationMetrics is not None

    def test_invalid_name_raises(self):
        import openagents_orchestration as pkg

        with pytest.raises(AttributeError):
            _ = pkg.NonExistentClass
