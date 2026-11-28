"""Shared SQLAlchemy metadata for Alembic and persisted models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base for SQLAlchemy models."""
