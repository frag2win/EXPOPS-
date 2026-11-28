"""Alembic environment using secret-backed application configuration."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.asyncio_runner import run_with_database_event_loop
from app.db.base import Base
import app.db.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    settings = get_settings()
    if settings.database_url is None:
        raise RuntimeError("DATABASE_URL is required to run database migrations")
    return settings.database_url.get_secret_value()


def _configured_url() -> str:
    url = _database_url()
    # ConfigParser treats percent as interpolation syntax.
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return url


def run_migrations_offline() -> None:
    url = _configured_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    _configured_url()
    connectable = None
    try:
        connectable = async_engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            connect_args={"connect_timeout": 5},
            hide_parameters=True,
        )
        async with connectable.connect() as connection:
            await connection.run_sync(_run_migrations)
    except Exception:
        # SQLAlchemy/driver exceptions can contain connection details.
        raise RuntimeError("Database migration failed; connection details are redacted") from None
    finally:
        if connectable is not None:
            await connectable.dispose()


def run_migrations_online() -> None:
    try:
        run_with_database_event_loop(run_async_migrations())
    except RuntimeError as error:
        if str(error) == "DATABASE_URL is required to run database migrations":
            raise
        if str(error) == "Database migration failed; connection details are redacted":
            raise
        raise RuntimeError("Database migration failed; connection details are redacted") from None


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
