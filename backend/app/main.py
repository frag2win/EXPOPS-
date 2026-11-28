"""ExperimentOps FastAPI application factory."""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import router
from app.api.experiment_routes import router as experiment_router
from app.core.request_limits import DraftRequestSizeLimitMiddleware
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.runtime import DatabaseRuntime, create_database_runtime
from app.integrations.flagsmith.adapter import FlagsmithAdapter
from app.integrations.flagsmith.client import FlagsmithClient
from app.integrations.flagsmith.exceptions import FlagsmithError
from app.llm.errors import GeminiProviderError, LLMError
from app.persistence.experiment_plans import PlanIntegrityError

logger = logging.getLogger("experimentops")


def create_app(settings: Settings | None = None, adapter: FlagsmithAdapter | None = None) -> FastAPI:
    """Create the API and wire its Flagsmith adapter."""
    config = settings or get_settings()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_runtime: DatabaseRuntime | None = None
        app.state.database_startup_failed = False
        if config.database_url is not None:
            try:
                database_runtime = create_database_runtime(config.database_url)
            except Exception:
                # Engine construction errors may include the database URL.
                logger.warning("Database runtime initialization failed")
                app.state.database_startup_failed = True
        app.state.database_runtime = database_runtime
        try:
            yield
        finally:
            if database_runtime is not None:
                try:
                    await database_runtime.dispose()
                except Exception:
                    logger.warning("Database engine disposal failed")
            try:
                await app.state.flagsmith_adapter.client.close()
            except Exception:
                logger.warning("Flagsmith client cleanup failed")

    application = FastAPI(title="ExperimentOps API", version="0.1.0", lifespan=lifespan)
    application.state.settings = config
    application.state.database_runtime = None
    application.state.database_startup_failed = False
    application.add_middleware(DraftRequestSizeLimitMiddleware)
    api_key = config.flagsmith_api_key.get_secret_value() if config.flagsmith_api_key else None
    application.state.flagsmith_adapter = adapter or FlagsmithAdapter(
        FlagsmithClient(config.flagsmith_api_url, api_key, config.flagsmith_timeout_seconds)
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(FlagsmithError)
    async def flagsmith_error_handler(request: Request, exc: FlagsmithError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning("Flagsmith read failed", extra={"request_id": request_id, "error_code": exc.code})
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": str(exc)}, "request_id": request_id},
        )

    @application.exception_handler(LLMError)
    async def llm_error_handler(request: Request, exc: LLMError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        diagnostic = exc.diagnostic if isinstance(exc, GeminiProviderError) else {"category": exc.code}
        logger.warning("Language model request failed", extra={"request_id": request_id, **diagnostic})
        error = {"code": exc.code, "message": str(exc)}
        if isinstance(exc, GeminiProviderError) and exc.upstream_status_code is not None:
            error["upstream_status_code"] = exc.upstream_status_code
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error, "request_id": request_id},
        )

    @application.exception_handler(SQLAlchemyError)
    async def database_error_handler(request: Request, _exc: SQLAlchemyError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning("Database operation failed", extra={"request_id": request_id})
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "DATABASE_OPERATION_FAILED",
                    "message": "The database operation could not be completed.",
                },
                "request_id": request_id,
            },
        )

    @application.exception_handler(PlanIntegrityError)
    async def plan_integrity_error_handler(request: Request, _exc: PlanIntegrityError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        logger.error("Persisted plan integrity verification failed", extra={"request_id": request_id})
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "PLAN_INTEGRITY_FAILED",
                    "message": "Stored plan integrity verification failed.",
                },
                "request_id": request_id,
            },
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", "unknown")
        # Never serialize Pydantic's `input` or `ctx`: request bodies can contain secrets.
        issues = [{"location": [str(part) for part in error.get("loc", ())], "message": "Field has an invalid value.", "type": error.get("type", "value_error")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "REQUEST_VALIDATION_FAILED", "message": "Request validation failed", "issues": issues}, "request_id": request_id})

    application.include_router(router)
    application.include_router(experiment_router)
    return application


app = create_app()
