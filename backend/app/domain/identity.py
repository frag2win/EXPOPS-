"""Canonical local principal representation."""

from pydantic import BaseModel, ConfigDict, field_validator

MAX_PRINCIPAL_SUBJECT_LENGTH = 255


def normalize_principal_subject(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Principal subject must be a string.")
    subject = value.strip()
    if (
        not subject
        or len(subject) > MAX_PRINCIPAL_SUBJECT_LENGTH
        or not subject.isprintable()
    ):
        raise ValueError("Principal subject is invalid.")
    return subject


class Principal(BaseModel):
    """Server-resolved principal used by authorization-aware services."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    subject: str

    @field_validator("subject", mode="before")
    @classmethod
    def validate_subject(cls, value: object) -> str:
        return normalize_principal_subject(value)
