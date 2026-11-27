"""Select the one explicitly configured provider; never fall back implicitly."""

from app.core.config import Settings
from app.llm.errors import LLMNotConfiguredError
from app.llm.providers.gemini import GeminiProvider
from app.llm.protocols import StructuredOutputProvider
from fastapi import Request


def configured_provider(settings: Settings) -> StructuredOutputProvider:
    if settings.gemini_api_key is None or not settings.gemini_model.strip():
        raise LLMNotConfiguredError()
    api_key = settings.gemini_api_key.get_secret_value().strip()
    if not api_key or api_key.lower().startswith(("your_", "<", "placeholder", "replace")):
        raise LLMNotConfiguredError()
    return GeminiProvider(api_key, settings.gemini_model.strip(), settings.llm_timeout_seconds)


def get_structured_output_provider(request: Request) -> StructuredOutputProvider:
    return configured_provider(request.app.state.settings)
