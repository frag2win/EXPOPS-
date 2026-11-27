from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.health import check_database
from app.db.asyncio_runner import run_with_database_event_loop
from app.db.runtime import DatabaseRuntime, create_database_runtime, session_scope, transaction_scope
from app.main import create_app


class _FakeConnection:
    def __init__(self, error=None):
        self.error = error
        self.executed = False

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, _statement):
        self.executed = True
        return SimpleNamespace(scalar_one=lambda: 1)


class _FakeEngine:
    def __init__(self, error=None):
        self.error = error
        self.connection = _FakeConnection(error)
        self.disposed = False

    def connect(self):
        return self.connection

    async def dispose(self):
        self.disposed = True


class _FakeSession:
    def __init__(self):
        self.rolled_back = 0
        self.closed = False
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    async def rollback(self):
        self.rolled_back += 1

    def begin(self):
        return _FakeTransaction(self)


class _FakeTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, exception_type, *_args):
        if exception_type is None:
            self.session.committed = True
        else:
            await self.session.rollback()
        return False


@pytest.mark.asyncio
async def test_database_health_reports_ready_only_after_successful_query():
    engine = _FakeEngine()
    result = await check_database(engine)
    assert result.status == "ready"
    assert result.configured and result.reachable
    assert engine.connection.executed


@pytest.mark.asyncio
async def test_database_health_reports_connection_failure_without_error_details():
    marker = "private-postgresql-connection-details"
    result = await check_database(_FakeEngine(RuntimeError(marker)))
    assert result.status == "unavailable"
    assert result.configured and result.reachable is False
    assert marker not in result.model_dump_json()


@pytest.mark.asyncio
async def test_database_health_does_not_claim_connectivity_when_unconfigured():
    result = await check_database(None)
    assert result.status == "not_configured"
    assert result.configured is False
    assert result.reachable is None


@pytest.mark.asyncio
async def test_database_runtime_disposes_engine():
    engine = _FakeEngine()
    runtime = DatabaseRuntime(engine=engine, session_factory=lambda: _FakeSession())
    await runtime.dispose()
    assert engine.disposed


@pytest.mark.asyncio
async def test_async_psycopg_engine_uses_bounded_pool_without_connecting():
    runtime = create_database_runtime(
        SecretStr("postgresql+psycopg://local-user:local-pass@localhost:5432/experimentops")
    )
    try:
        engine = runtime.engine
        assert engine.sync_engine.url.drivername == "postgresql+psycopg"
        assert engine.sync_engine.hide_parameters is True
        assert engine.pool.size() == 5
        assert engine.pool._max_overflow == 5
        assert engine.pool._timeout == 10
        assert runtime.session_factory.kw.get("expire_on_commit") is False
        session = runtime.session_factory()
        assert isinstance(session, AsyncSession)
        await session.close()
    finally:
        await runtime.dispose()


def test_engine_initialization_exception_is_redacted(monkeypatch):
    import app.db.runtime as runtime_module

    marker = "private-database-url"
    monkeypatch.setattr(
        runtime_module, "create_async_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError(marker)),
    )
    with pytest.raises(RuntimeError) as caught:
        create_database_runtime(SecretStr("postgresql+psycopg://user:secret@host/db"))
    assert "connection details are redacted" in str(caught.value)
    assert marker not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.asyncio
async def test_session_scope_rolls_back_failures_and_closes_session():
    session = _FakeSession()
    with pytest.raises(RuntimeError):
        async with session_scope(lambda: session):
            raise RuntimeError("synthetic transaction failure")
    assert session.rolled_back == 1
    assert session.closed


@pytest.mark.asyncio
async def test_transaction_scope_commits_success_and_rolls_back_failure():
    committed = _FakeSession()
    async with transaction_scope(lambda: committed):
        pass
    assert committed.committed
    assert committed.closed

    rolled_back = _FakeSession()
    with pytest.raises(RuntimeError):
        async with transaction_scope(lambda: rolled_back):
            raise RuntimeError("synthetic transaction failure")
    assert rolled_back.rolled_back >= 1
    assert rolled_back.closed


def test_database_health_endpoint_is_separate_and_missing_database_is_not_ready():
    with TestClient(create_app(Settings(_env_file=None))) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/health/database")
    assert response.status_code == 503
    assert response.json() == {
        "component": "database", "status": "not_configured",
        "configured": False, "reachable": None,
    }


def test_database_startup_failure_does_not_break_existing_health(monkeypatch, capsys):
    import app.main as main

    marker = "private-startup-database-url"
    def fail_engine(_url):
        raise RuntimeError(marker)
    monkeypatch.setattr(main, "create_database_runtime", fail_engine)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:pass@localhost:5432/db")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/health/database")
    output = capsys.readouterr().err
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert marker not in output
    assert marker not in response.text


def test_configured_database_runtime_lifecycle(monkeypatch):
    import app.main as main

    engine = _FakeEngine()
    runtime = SimpleNamespace(engine=engine, dispose=engine.dispose)
    captured = []
    monkeypatch.setattr(main, "create_database_runtime", lambda value: captured.append(value) or runtime)
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:pass@localhost:5432/db")
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/database")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
    assert len(captured) == 1
    assert isinstance(captured[0], SecretStr)
    assert engine.disposed


def test_alembic_metadata_has_no_business_tables():
    from app.db.base import Base

    assert dict(Base.metadata.tables) == {}


def test_platform_database_runner_runs_and_closes_its_loop():
    import asyncio

    async def active_loop():
        return asyncio.get_running_loop()

    loop = run_with_database_event_loop(active_loop())
    assert isinstance(loop, asyncio.SelectorEventLoop)
    assert loop.is_closed()
