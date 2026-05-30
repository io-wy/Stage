from __future__ import annotations

"""Explicit DB init script.

Usage:
  python -m openagents_orchestration.app.init_db
"""

from openagents_orchestration.app.core.database import Base, engine

# Ensure models are imported so metadata is populated.
import openagents_orchestration.app.models  # noqa: F401


def main() -> None:
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    main()
