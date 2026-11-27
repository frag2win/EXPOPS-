"""Shared SQLAlchemy metadata for Alembic and future persisted models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base for future database models. M4.1 intentionally defines no tables."""
