"""Provider-independent types for structured generation."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GenerationUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class StructuredGeneration:
    output_json: str
    model: str
    usage: GenerationUsage


class StructuredOutputProvider(Protocol):
    provider_name: str
    model_name: str

    async def generate(self, *, instructions: str, input_text: str, schema: dict[str, object]) -> StructuredGeneration:
        """Generate JSON conforming to schema without exposing transport details."""
