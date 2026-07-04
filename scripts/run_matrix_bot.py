#!/usr/bin/env python3
"""Matrix bot entry point.

Environment variables:
    MATRIX_HOMESERVER      e.g. https://matrix.example.com
    MATRIX_USER_ID         e.g. @bot:example.com
    MATRIX_ACCESS_TOKEN    Matrix access token
    MATRIX_WORK_DIR        working directory per room (default: ./matrix_work)
    MATRIX_TOKEN_LIMIT     token budget per run (default: 100000)
    MATRIX_MAX_STEPS       max director steps per run (default: 50)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from openagents_orchestration.im_adapters.matrix import main

if __name__ == "__main__":
    asyncio.run(main())
