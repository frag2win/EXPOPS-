"""Application dependency providers."""

from fastapi import Request

from app.integrations.flagsmith.adapter import FlagsmithAdapter


def get_flagsmith_adapter(request: Request) -> FlagsmithAdapter:
    return request.app.state.flagsmith_adapter
