"""Sanitized language-model errors and Gemini-specific safe diagnostics."""


class LLMError(Exception):
    code = "LLM_ERROR"
    status_code = 502

    def __init__(self, message: str = "Language model request failed") -> None:
        super().__init__(message)
        self.upstream_status_code: int | None = None


class LLMNotConfiguredError(LLMError):
    code = "LLM_NOT_CONFIGURED"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("Gemini is not configured; set GEMINI_API_KEY and GEMINI_MODEL in the backend environment")


# Retained generic errors for provider-independent callers and tests.
class LLMAuthenticationError(LLMError):
    code = "LLM_AUTHENTICATION_FAILED"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("Configured language model provider rejected its credential")


class LLMRateLimitError(LLMError):
    code = "LLM_RATE_LIMITED"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("Configured language model provider is rate limited")


class LLMTimeoutError(LLMError):
    code = "LLM_TIMEOUT"
    status_code = 504

    def __init__(self) -> None:
        super().__init__("Language model request timed out")


class LLMUnavailableError(LLMError):
    code = "LLM_UNAVAILABLE"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("Configured language model provider is unavailable")


class LLMInvalidResponseError(LLMError):
    code = "LLM_INVALID_RESPONSE"
    status_code = 502

    def __init__(self) -> None:
        super().__init__("Language model returned an invalid or incomplete structured response")


_SAFE_RPC_STATUSES = {
    "INVALID_ARGUMENT", "NOT_FOUND", "UNAUTHENTICATED", "PERMISSION_DENIED",
    "RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "DEADLINE_EXCEEDED",
    "CANCELLED", "UNKNOWN", "FAILED_PRECONDITION", "SERVICE_DISABLED",
}
_SAFE_REASONS = {
    "API_KEY_INVALID", "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED",
    "SERVICE_DISABLED", "RATE_LIMIT_EXCEEDED", "RESOURCE_EXHAUSTED",
    "MODEL_NOT_FOUND", "INVALID_ARGUMENT", "PERMISSION_DENIED", "UNAUTHENTICATED",
    "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION", "MODEL_ARMOR",
}


class GeminiProviderError(LLMError):
    """Gemini error containing only allowlisted status/reason metadata."""

    stage = "provider_request"

    def __init__(
        self,
        message: str,
        *,
        upstream_status_code: int | None = None,
        rpc_status: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.upstream_status_code = upstream_status_code if isinstance(upstream_status_code, int) and 100 <= upstream_status_code <= 599 else None
        self.rpc_status = rpc_status if rpc_status in _SAFE_RPC_STATUSES else None
        self.reason = reason if reason in _SAFE_REASONS else None

    @property
    def diagnostic(self) -> dict[str, object]:
        """Structured, redacted metadata safe for internal logging."""
        result: dict[str, object] = {
            "provider": "gemini",
            "category": self.code,
            "stage": self.stage,
        }
        if self.upstream_status_code is not None:
            result["upstream_status_code"] = self.upstream_status_code
        if self.rpc_status is not None:
            result["rpc_status"] = self.rpc_status
        if self.reason is not None:
            result["reason"] = self.reason
        return result


class GeminiAuthenticationError(GeminiProviderError):
    code = "GEMINI_AUTHENTICATION_ERROR"
    status_code = 503
    def __init__(self, **kwargs) -> None:
        super().__init__("Gemini rejected the configured API credential", **kwargs)


class GeminiModelNotFoundError(GeminiProviderError):
    code = "GEMINI_MODEL_NOT_FOUND"
    status_code = 502
    def __init__(self, **kwargs) -> None:
        super().__init__("The configured Gemini model was not found", **kwargs)


class GeminiRequestRejectedError(GeminiProviderError):
    code = "GEMINI_REQUEST_REJECTED"
    status_code = 502
    def __init__(self, **kwargs) -> None:
        super().__init__("Gemini rejected the generation request", **kwargs)


class GeminiSchemaRejectedError(GeminiRequestRejectedError):
    code = "GEMINI_SCHEMA_REJECTED"
    stage = "schema_validation"
    def __init__(self, **kwargs) -> None:
        GeminiProviderError.__init__(self, "Gemini rejected the structured response schema", **kwargs)


class GeminiRateLimitedError(GeminiProviderError):
    code = "GEMINI_RATE_LIMITED"
    status_code = 503
    def __init__(self, **kwargs) -> None:
        super().__init__("Gemini rate limit was reached", **kwargs)


class GeminiServiceUnavailableError(GeminiProviderError):
    code = "GEMINI_SERVICE_UNAVAILABLE"
    status_code = 503
    def __init__(self, **kwargs) -> None:
        super().__init__("Gemini is temporarily unavailable", **kwargs)


class GeminiEmptyResponseError(GeminiProviderError):
    code = "GEMINI_EMPTY_RESPONSE"
    status_code = 502
    stage = "response_extraction"
    def __init__(self) -> None:
        super().__init__("Gemini returned no candidate text")


class GeminiBlockedResponseError(GeminiProviderError):
    code = "GEMINI_BLOCKED_RESPONSE"
    status_code = 502
    stage = "response_extraction"
    def __init__(self, *, reason: str | None = None) -> None:
        super().__init__("Gemini blocked the generated response", reason=reason)


class GeminiInvalidJsonError(GeminiProviderError):
    code = "GEMINI_INVALID_JSON"
    status_code = 502
    stage = "json_parsing"
    def __init__(self) -> None:
        GeminiProviderError.__init__(self, "Gemini returned text that is not valid JSON")


class GeminiOutputValidationError(GeminiProviderError):
    code = "GEMINI_OUTPUT_VALIDATION_FAILED"
    status_code = 502
    stage = "pydantic_validation"
    def __init__(
        self,
        validation_errors: list[dict[str, object]] | None = None,
        structural_errors: list[dict[str, object]] | None = None,
    ) -> None:
        GeminiProviderError.__init__(self, "Gemini JSON did not satisfy the experiment draft schema")
        self.validation_errors = validation_errors or []
        self.structural_errors = structural_errors or []

    @property
    def diagnostic(self) -> dict[str, object]:
        result = super().diagnostic
        if self.validation_errors:
            result["validation_errors"] = self.validation_errors
        if self.structural_errors:
            result["structural_errors"] = self.structural_errors
        return result


class GeminiTimeoutError(GeminiProviderError):
    code = "GEMINI_TIMEOUT"
    status_code = 504
    stage = "provider_request"
    def __init__(self, **kwargs) -> None:
        super().__init__("Gemini generation timed out", **kwargs)
