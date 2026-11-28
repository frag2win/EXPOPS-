"""Portable SQLAlchemy types used by the PostgreSQL-backed models."""

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON


def portable_json_type() -> JSON:
    """Use JSONB on PostgreSQL while keeping metadata portable for local tests."""
    return JSON().with_variant(JSONB(), "postgresql")
