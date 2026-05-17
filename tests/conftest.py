"""pytest configuration."""

from __future__ import annotations

import sys
from pathlib import Path

# Add src/ to path for imports
_SRC = Path(__file__).parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
