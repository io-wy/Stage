"""pytest configuration."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Add src/ to path for imports
_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


@pytest.fixture(autouse=True)
def _set_test_env_vars():
    """Ensure agent.json env placeholders are resolvable in CI."""
    os.environ.setdefault("LLM_API_BASE", "http://localhost:9999")
    os.environ.setdefault("LLM_MODEL", "test-model")
