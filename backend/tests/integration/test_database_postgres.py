"""Opt-in PostgreSQL integration check for the M4.1 async runtime."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.db.asyncio_runner import run_with_database_event_loop
from app.db.runtime import create_database_runtime


pytestmark = pytest.mark.skipif(
    os.getenv("EXPOPS_POSTGRES_INTEGRATION") != "1"
    or not os.getenv("TEST_DATABASE_URL"),
    reason="Set EXPOPS_POSTGRES_INTEGRATION=1 and TEST_DATABASE_URL to opt in",
)


def test_postgresql_connectivity_and_transaction_rollback():
    run_with_database_event_loop(_verify_postgresql_connectivity_and_transaction_rollback())


async def _verify_postgresql_connectivity_and_transaction_rollback():
    settings = Settings(_env_file=None, database_url=os.environ["TEST_DATABASE_URL"])
    runtime = create_database_runtime(settings.database_url)
    table_name = f"m41_rollback_{uuid4().hex}"
    try:
        async with runtime.session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
            with pytest.raises(RuntimeError):
                async with session.begin():
                    await session.execute(text(f'CREATE TEMP TABLE "{table_name}" (id integer)'))
                    await session.execute(text(f'INSERT INTO "{table_name}" (id) VALUES (1)'))
                    raise RuntimeError("force rollback")
            result = await session.execute(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": f"pg_temp.{table_name}"},
            )
            assert result.scalar_one() is None
    finally:
        await runtime.dispose()
