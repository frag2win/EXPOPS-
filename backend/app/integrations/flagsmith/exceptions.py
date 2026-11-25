"""Safe, typed failures raised by the Flagsmith integration."""


class FlagsmithError(Exception):
    """Base class for sanitized integration errors."""

    code = "FLAGSMITH_ERROR"
    status_code = 502

    def __init__(self, message: str = "Flagsmith request failed") -> None:
        super().__init__(message)


class FlagsmithConfigurationError(FlagsmithError):
    code = "FLAGSMITH_NOT_CONFIGURED"
    status_code = 503


class FlagsmithAuthenticationError(FlagsmithError):
    code = "FLAGSMITH_AUTHENTICATION_FAILED"
    status_code = 502


class FlagsmithPermissionError(FlagsmithError):
    code = "FLAGSMITH_PERMISSION_DENIED"
    status_code = 502


class FlagsmithNotFoundError(FlagsmithError):
    code = "FLAGSMITH_RESOURCE_NOT_FOUND"
    status_code = 404


class FlagsmithRateLimitError(FlagsmithError):
    code = "FLAGSMITH_RATE_LIMITED"
    status_code = 503


class FlagsmithUnavailableError(FlagsmithError):
    code = "FLAGSMITH_UNAVAILABLE"
    status_code = 503


class FlagsmithInvalidResponseError(FlagsmithError):
    code = "FLAGSMITH_INVALID_RESPONSE"
    status_code = 502
