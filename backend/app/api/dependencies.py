"""Application dependency providers."""

from fastapi import HTTPException, Request

from app.domain.identity import Principal
from app.integrations.flagsmith.adapter import FlagsmithAdapter


def get_flagsmith_adapter(request: Request) -> FlagsmithAdapter:
    return request.app.state.flagsmith_adapter


def get_current_principal(request: Request) -> Principal:
    if "owner_subject" in request.query_params:
        raise HTTPException(
            status_code=422,
            detail="The plan owner is determined by server configuration.",
        )
    settings = getattr(request.app.state, "settings", None)
    subject = getattr(settings, "local_owner_subject", None)
    if subject is None:
        raise HTTPException(
            status_code=503,
            detail="The local principal is not configured.",
        )
    try:
        return Principal(subject=subject)
    except ValueError:
        raise HTTPException(
            status_code=503,
            detail="The local principal is unavailable.",
        ) from None
