from openagents_orchestration import backend as backend_pkg
from openagents_orchestration.backend import (
    ClaudeCodeAdapter,
    RagGovernanceBackend,
    default_kb_path,
    query_rag,
)
from openagents_orchestration.backend import rag as backend_rag
from openagents_orchestration.service import rag as service_rag


def test_backend_package_exports_governed_backends() -> None:
    assert backend_pkg.RagGovernanceBackend is RagGovernanceBackend
    assert backend_pkg.ClaudeCodeAdapter is ClaudeCodeAdapter
    assert backend_pkg.query_rag is query_rag
    assert backend_pkg.default_kb_path is default_kb_path


def test_service_rag_is_a_thin_wrapper_over_backend() -> None:
    assert service_rag.query_rag is backend_rag.query_rag
    assert service_rag.RagGovernanceBackend is backend_rag.RagGovernanceBackend
    assert service_rag.default_kb_path is backend_rag.default_kb_path
