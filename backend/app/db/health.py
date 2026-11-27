"""Safe, separately reported database readiness checks."""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


DatabaseHealthStatus = Literal["ready", "not_configured", "unavailable"]


class DatabaseHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: Literal["database"] = "database"
    status: DatabaseHealthStatus
    configured: bool
    reachable: bool | None


async def check_database(engine: AsyncEngine | None) -> DatabaseHealthResponse:
    if engine is None:
        return DatabaseHealthResponse(
            status="not_configured", configured=False, reachable=None,
        )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        # Driver exceptions can contain connection details. The response and
        # log message intentionally report only the component and safe status.
        return DatabaseHealthResponse(
            status="unavailable", configured=True, reachable=False,
        )
    return DatabaseHealthResponse(status="ready", configured=True, reachable=True)
