"""Google Gemini adapter with schema adaptation and redacted error categories."""

import asyncio
from copy import deepcopy
from typing import Callable

from app.llm.errors import (
    GeminiAuthenticationError,
    GeminiBlockedResponseError,
    GeminiEmptyResponseError,
    GeminiModelNotFoundError,
    GeminiProviderError,
    GeminiRateLimitedError,
    GeminiRequestRejectedError,
    GeminiSchemaRejectedError,
    GeminiServiceUnavailableError,
    GeminiTimeoutError,
)
from app.llm.protocols import GenerationUsage, StructuredGeneration


class GeminiProvider:
    provider_name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float,
        *,
        client_factory: Callable[..., object] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model_name = model
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory

    async def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        schema: dict[str, object],
    ) -> StructuredGeneration:
        from google import genai
        from google.genai import errors, types

        client_factory = self._client_factory or genai.Client
        client = None
        normalized_error: GeminiProviderError | None = None
        try:
            try:
                response_schema = to_gemini_json_schema(schema)
                client = client_factory(
                    api_key=self._api_key,
                    http_options=types.HttpOptions(
                        timeout=max(1, int(self.timeout_seconds * 1000)),
                        retry_options=types.HttpRetryOptions(attempts=1),
                    ),
                )
                async with client.aio as async_client:
                    async with asyncio.timeout(self.timeout_seconds):
                        response = await async_client.models.generate_content(
                            model=self.model_name,
                            contents=input_text,
                            config=types.GenerateContentConfig(
                                system_instruction=instructions,
                                response_mime_type="application/json",
                                response_json_schema=response_schema,
                                temperature=0,
                                max_output_tokens=2200,
                            ),
                        )
                output_json = _extract_text(response)
                usage = getattr(response, "usage_metadata", None)
                model = getattr(response, "model_version", None)
                return StructuredGeneration(
                    output_json=output_json,
                    model=model if isinstance(model, str) and model else self.model_name,
                    usage=GenerationUsage(
                        input_tokens=_token_count(getattr(usage, "prompt_token_count", None)),
                        output_tokens=_token_count(getattr(usage, "candidates_token_count", None)),
                    ),
                )
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                normalized_error = GeminiTimeoutError()
            except GeminiProviderError:
                raise
            except errors.APIError as sdk_error:
                normalized_error = _map_api_error(sdk_error)
            except Exception as sdk_error:
                if _is_sdk_timeout(sdk_error):
                    normalized_error = GeminiTimeoutError()
                else:
                    # SDK exceptions may include keys, prompts, or provider response text.
                    normalized_error = GeminiServiceUnavailableError()
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    # Cleanup detail must not replace a normalized provider result.
                    pass
        # Raise outside the SDK-exception handler so no raw SDK exception remains
        # attached as a cause or context to the safe normalized error.
        if normalized_error is not None:
            raise normalized_error


def to_gemini_json_schema(schema: dict[str, object]) -> dict[str, object]:
    """Inline local Pydantic refs and translate `const` to supported constraints.

    This changes only the transport schema. The generated response is still
    validated against the original strict Pydantic models by the draft agent.
    """
    definitions = schema.get("$defs", {})
    if not isinstance(definitions, dict):
        raise ValueError("schema definitions must be an object")

    def transform(node: object, stack: tuple[str, ...] = ()) -> object:
        if isinstance(node, list):
            return [transform(item, stack) for item in node]
        if not isinstance(node, dict):
            return node

        ref = node.get("$ref")
        if ref is not None:
            prefix = "#/$defs/"
            if not isinstance(ref, str) or not ref.startswith(prefix):
                raise ValueError("only local Pydantic definition references are supported")
            name = ref.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
            if name in stack or name not in definitions:
                raise ValueError("unknown or recursive provider schema reference")
            target = definitions[name]
            if not isinstance(target, dict):
                raise ValueError("provider schema definition must be an object")
            resolved = transform(target, (*stack, name))
            siblings = {key: value for key, value in node.items() if key != "$ref"}
            if siblings and isinstance(resolved, dict):
                resolved = {**resolved, **transform(siblings, stack)}
            return resolved

        result = {
            key: transform(value, stack)
            for key, value in node.items()
            if key != "$defs"
        }
        const_present = "const" in result
        const_value = result.pop("const", None)
        if const_present:
            if const_value is None and result.get("type") == "null":
                result["type"] = ["null"]
            else:
                result["enum"] = [const_value]
        if result.get("type") == "null":
            result["type"] = ["null"]
        return result

    converted = transform(schema)
    if not isinstance(converted, dict) or converted.get("type") != "object":
        raise ValueError("Gemini response schema root must be an object")
    return converted


def _extract_text(response: object) -> str:
    if response is None:
        raise GeminiEmptyResponseError()

    feedback = getattr(response, "prompt_feedback", None)
    block_reason = _enum_name(getattr(feedback, "block_reason", None))
    if block_reason and block_reason not in {"BLOCK_REASON_UNSPECIFIED", "UNSPECIFIED", "0"}:
        raise GeminiBlockedResponseError(reason=block_reason)

    candidates = getattr(response, "candidates", None)
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise GeminiEmptyResponseError()
    candidate = candidates[0]
    finish_reason = _enum_name(getattr(candidate, "finish_reason", None))
    blocked_finishes = {
        "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY",
        "IMAGE_PROHIBITED_CONTENT", "IMAGE_RECITATION", "MODEL_ARMOR",
    }
    if finish_reason in blocked_finishes:
        raise GeminiBlockedResponseError(reason=finish_reason)

    content = getattr(candidate, "content", None)
    parts = getattr(content, "parts", None)
    if not isinstance(parts, (list, tuple)):
        raise GeminiEmptyResponseError()
    text_parts = [
        part.text
        for part in parts
        if isinstance(getattr(part, "text", None), str)
        and not bool(getattr(part, "thought", False))
    ]
    text = "".join(text_parts)
    if not text.strip():
        raise GeminiEmptyResponseError()
    return text


def _map_api_error(error: Exception) -> GeminiProviderError:
    code = getattr(error, "code", None)
    try:
        status_code = int(code)
    except (TypeError, ValueError):
        status_code = None
    rpc_status = _safe_rpc_status(getattr(error, "status", None))
    reason = _find_safe_reason(getattr(error, "details", None))

    context = {
        "upstream_status_code": status_code,
        "rpc_status": rpc_status,
        "reason": reason,
    }
    if status_code in {401, 403} or rpc_status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}:
        return GeminiAuthenticationError(**context)
    if reason in {"API_KEY_INVALID", "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED"}:
        return GeminiAuthenticationError(**context)
    if status_code == 404 or rpc_status == "NOT_FOUND" or reason == "MODEL_NOT_FOUND":
        return GeminiModelNotFoundError(**context)
    if status_code == 429 or rpc_status == "RESOURCE_EXHAUSTED" or reason == "RATE_LIMIT_EXCEEDED":
        return GeminiRateLimitedError(**context)
    if status_code in {408, 504} or rpc_status == "DEADLINE_EXCEEDED":
        return GeminiTimeoutError(**context)
    if status_code is not None and status_code >= 500 or rpc_status in {"UNAVAILABLE", "INTERNAL"}:
        return GeminiServiceUnavailableError(**context)
    if status_code in {400, 422}:
        if _mentions_schema(error):
            return GeminiSchemaRejectedError(**context)
        return GeminiRequestRejectedError(**context)
    if status_code is not None and 400 <= status_code < 500:
        return GeminiRequestRejectedError(**context)
    return GeminiServiceUnavailableError(**context)


def _safe_rpc_status(value: object) -> str | None:
    from app.llm.errors import _SAFE_RPC_STATUSES
    text = str(value).upper() if value is not None else ""
    return text if text in _SAFE_RPC_STATUSES else None


def _find_safe_reason(value: object) -> str | None:
    from app.llm.errors import _SAFE_REASONS
    if isinstance(value, dict):
        reason = value.get("reason")
        if isinstance(reason, str) and reason.upper() in _SAFE_REASONS:
            return reason.upper()
        for child in value.values():
            found = _find_safe_reason(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_safe_reason(child)
            if found:
                return found
    return None


def _mentions_schema(error: Exception) -> bool:
    # The message is inspected only to choose a category; it is never logged or returned.
    message = getattr(error, "message", "")
    if not isinstance(message, str):
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in ("schema", "response_json_schema", "response schema", "responseschema"))


def _enum_name(value: object) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    text = name if isinstance(name, str) else str(value)
    return text.rsplit(".", 1)[-1].upper()


def _is_sdk_timeout(error: Exception) -> bool:
    return type(error).__name__ in {"TimeoutException", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout"}


def _token_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
