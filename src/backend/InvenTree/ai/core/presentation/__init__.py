"""Strict optional presentation envelope for OpenUI programs (wire contract).

Raw envelope types and the safe optional normalization/parsing boundary
only. No generation, transport, persistence or fetch/eval here.
"""

from __future__ import annotations

from . import wire
from .wire import (
    MAX_PROGRAM_BYTES,
    MAX_REF_ID_LENGTH,
    MAX_REFERENCE_COUNT,
    MAX_SOURCE_KEY_LENGTH,
    PRESENTATION_DIAGNOSTIC_CODES,
    REFERENCE_KINDS,
    SCHEMA_VERSION,
    AssistantPresentationV1,
    PresentationReferenceV1,
    PresentationValidationError,
    normalize_presentation,
    presentation_diagnostic,
    validate_presentation,
)

__all__ = [
    "MAX_PROGRAM_BYTES",
    "MAX_REFERENCE_COUNT",
    "MAX_REF_ID_LENGTH",
    "MAX_SOURCE_KEY_LENGTH",
    "PRESENTATION_DIAGNOSTIC_CODES",
    "REFERENCE_KINDS",
    "SCHEMA_VERSION",
    "AssistantPresentationV1",
    "PresentationReferenceV1",
    "PresentationValidationError",
    "normalize_presentation",
    "presentation_diagnostic",
    "validate_presentation",
    "wire",
]
