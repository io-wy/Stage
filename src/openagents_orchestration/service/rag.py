"""Compatibility wrapper for backend RAG services."""

from openagents_orchestration.backend.rag import (
    RagGovernanceBackend,
    default_kb_path,
    query_rag,
)

__all__ = ["RagGovernanceBackend", "default_kb_path", "query_rag"]
