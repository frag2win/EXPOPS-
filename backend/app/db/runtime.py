"""Lifespan-owned SQLAlchemy async engine and explicit session scopes."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(frozen=True)
class DatabaseRuntime:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        await self.engine.dispose()


def create_database_runtime(database_url: SecretStr) -> DatabaseRuntime:
    """Construct a pooled async engine without opening a connection."""
    try:
        engine = create_async_engine(
            database_url.get_secret_value(),
            isolation_level="READ COMMITTED",
            pool_size=5,
            max_overflow=5,
            pool_timeout=10,
            pool_recycle=1800,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
            hide_parameters=True,
        )
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    except Exception:
        # Engine/plugin diagnostics may include the configured connection URL.
        raise RuntimeError("Database engine initialization failed; connection details are redacted") from None
    return DatabaseRuntime(engine=engine, session_factory=factory)


@asynccontextmanager
async def session_scope(
    session_factory: Callable[[], AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield one session, roll back failures, and always close it.

    This scope does not commit implicitly. Write callers must use
    ``transaction_scope`` or manage an explicit transaction themselves.
    """
    async with session_factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise


@asynccontextmanager
async def transaction_scope(
    session_factory: Callable[[], AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session in one explicit transaction with automatic rollback."""
    async with session_scope(session_factory) as session:
        async with session.begin():
            yield session
