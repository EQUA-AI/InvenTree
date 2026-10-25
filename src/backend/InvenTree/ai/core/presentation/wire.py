"""Raw OpenUI presentation envelope contract (strict optional wire slice).

Strict, backend-owned wire types (``AssistantPresentationV1`` /
``PresentationReferenceV1``) plus the safe optional normalization boundary
for stored presentation metadata. Generation, transport, persistence and
fetch/eval stay out of this module.

Boundaries this contract documents and enforces:

* envelope validation is not program semantic safety and not authorization;
  a syntactically valid program string is never proof that it is safe to
  render, and a validated envelope grants nothing;
* ``ref_id``/``source_key`` are opaque server-issued keys resolved only
  through an authorized per-reader projection — a client payload is never a
  grant, label, scope claim or fetchable URL;
* references that are missing or unauthorized later resolve
  unavailable, not zeros, so a reader never sees fabricated empty data;
* existing message content remains the canonical fallback: this envelope is
  strictly additive and carries no fallback field of its own;
* future historical library versions must be explicitly supported or fall back
  — never silently reinterpret old arguments;
* graph limits (200 reachable nodes / depth 12) belong to the future
  deterministic builder/parser, not here: raw string length proves nothing
  about the program graph.

Runtime imports stay limited to the standard library and Pydantic: no source
fetch/eval, no network, no payload log output, no persistence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

SCHEMA_VERSION = 1
FORMAT_OPENUI_LANG = "openui-lang"
LIBRARY_AIMMS_CHAT_1 = "aimms-chat@1"

#: Supported reference kinds, ordered (byte-deterministic wire contract).
REFERENCE_KINDS: tuple[str, ...] = ("entity", "evidence", "metric_dataset", "trend_dataset")

PresentationReferenceKind = Literal["entity", "evidence", "metric_dataset", "trend_dataset"]

MAX_REFERENCE_COUNT = 100
MAX_PROGRAM_BYTES = 64 * 1024
MAX_REF_ID_LENGTH = 128
MAX_SOURCE_KEY_LENGTH = 256

#: Safe, fixed-string diagnostic codes; never derived from payload content.
PRESENTATION_DIAGNOSTIC_CODES: tuple[str, ...] = (
    "valid",
    "absent",
    "unsupported_input_type",
    "unsupported_schema_version",
    "unsupported_format",
    "unsupported_library",
    "invalid_envelope",
)


class PresentationValidationError(ValueError):
    """A malformed or unsupported presentation envelope.

    ``reason`` and the message are drawn from PRESENTATION_DIAGNOSTIC_CODES,
    so both are safe to echo: no program text, answer text or labels.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"presentation envelope rejected: {reason}")
        self.reason = reason


def _bounded_identifier(value: str, bound: int) -> str:
    if not value.strip():
        raise ValueError("identifier must be nonempty")
    if len(value) > bound:
        raise ValueError("identifier is too long")
    return value


class PresentationReferenceV1(BaseModel):
    """One opaque, server-issued reference key. Never a grant or a label."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    ref_id: str
    kind: PresentationReferenceKind
    source_key: str

    @field_validator("ref_id")
    @classmethod
    def _check_ref_id(cls, value: str) -> str:
        return _bounded_identifier(value, MAX_REF_ID_LENGTH)

    @field_validator("source_key")
    @classmethod
    def _check_source_key(cls, value: str) -> str:
        return _bounded_identifier(value, MAX_SOURCE_KEY_LENGTH)


class AssistantPresentationV1(BaseModel):
    """The optional presentation envelope carried alongside an answer."""

    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    schema_version: Literal[1]
    format: Literal["openui-lang"]
    library: Literal["aimms-chat@1"]
    program: str
    references: list[PresentationReferenceV1]

    @field_validator("schema_version", mode="before")
    @classmethod
    def _check_schema_version(cls, value: object) -> object:
        if type(value) is not int or value != SCHEMA_VERSION:
            raise ValueError("schema_version must be the exact integer 1")
        return value

    @field_validator("program")
    @classmethod
    def _check_program(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("program must be nonempty")
        if len(value.encode("utf-8")) > MAX_PROGRAM_BYTES:
            raise ValueError("program exceeds the byte limit")
        return value

    @field_validator("references")
    @classmethod
    def _check_references(
        cls, value: list[PresentationReferenceV1]
    ) -> list[PresentationReferenceV1]:
        if len(value) > MAX_REFERENCE_COUNT:
            raise ValueError("too many references")
        seen: set[str] = set()
        for ref in value:
            if ref.ref_id in seen:
                raise ValueError("ref_id values must be unique")
            seen.add(ref.ref_id)
        return value


def _as_payload(data: object) -> Mapping:
    if isinstance(data, str):
        try:
            decoded = json.loads(data)
        except ValueError:
            raise PresentationValidationError("unsupported_input_type") from None
        if not isinstance(decoded, Mapping):
            raise PresentationValidationError("unsupported_input_type")
        return decoded
    if isinstance(data, Mapping):
        return data
    raise PresentationValidationError("unsupported_input_type")


def _check_shape(payload: Mapping) -> None:
    if set(payload) != {"schema_version", "format", "library", "program", "references"}:
        raise PresentationValidationError("invalid_envelope")
    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version != SCHEMA_VERSION:
        raise PresentationValidationError("unsupported_schema_version")
    fmt = payload.get("format")
    if fmt != FORMAT_OPENUI_LANG:
        code = "unsupported_format" if isinstance(fmt, str) else "invalid_envelope"
        raise PresentationValidationError(code)
    lib = payload.get("library")
    if lib != LIBRARY_AIMMS_CHAT_1:
        code = "unsupported_library" if isinstance(lib, str) else "invalid_envelope"
        raise PresentationValidationError(code)
    if not isinstance(payload.get("references"), list):
        raise PresentationValidationError("invalid_envelope")


def validate_presentation(data: object) -> AssistantPresentationV1:
    """Strictly validate one presentation envelope.

    Raises ``PresentationValidationError`` with a safe fixed-string reason on
    any malformed or unsupported input; the input is never mutated. Model
    instances are copied and re-validated against the current contract
    instead of being trusted via ``isinstance``.
    """
    if isinstance(data, AssistantPresentationV1):
        try:
            # Content-free extraction: serializer warnings carry raw payload
            # values, so suppress them per call here, never globally.
            data = data.model_dump(warnings=False)
        except Exception:
            raise PresentationValidationError("invalid_envelope") from None
    payload = _as_payload(data)
    _check_shape(payload)
    try:
        return AssistantPresentationV1.model_validate(payload)
    except ValidationError:
        raise PresentationValidationError("invalid_envelope") from None


def presentation_diagnostic(raw: object) -> str:
    """Classify optional presentation metadata with a safe fixed-string code.

    The result is always one of PRESENTATION_DIAGNOSTIC_CODES and never
    carries program, answer or label content.
    """
    if raw is None:
        return "absent"
    try:
        validate_presentation(raw)
    except PresentationValidationError as exc:
        return exc.reason
    except Exception:  # this boundary must stay total
        return "invalid_envelope"
    return "valid"


def normalize_presentation(raw: object) -> AssistantPresentationV1 | None:
    """Safely parse optional stored presentation metadata.

    Returns the validated envelope, or ``None`` for absent, invalid or
    unsupported metadata (including future schema/format/library versions) so
    an otherwise valid answer never fails: existing message content remains
    the canonical fallback. Never raises and never mutates the input.
    """
    try:
        return validate_presentation(raw)
    except Exception:  # optional metadata must never fail
        return None
