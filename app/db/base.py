"""SQLAlchemy declarative base and model imports."""

from app.db.session import Base

# Import models here so Alembic can discover metadata.
from app.models import user  # noqa: F401
