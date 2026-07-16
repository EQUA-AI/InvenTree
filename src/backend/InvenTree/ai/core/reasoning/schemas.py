"""Versioned canonical response models for typed and voice turns.

The spoken-summary consistency checks in this module are deliberately lexical.
They provide a deterministic, conservative boundary before text can be sent to
TTS: substantive summary tokens must already occur in user-visible response
text, uncertainty markers must remain represented, and material words from a
safety boundary must be repeated. This is not a semantic-equivalence proof. A
valid paraphrase can therefore be rejected and should be regenerated using the
same vocabulary as the visible response.

``reasoning_summary`` is user-facing provenance only. The schema intentionally
has no field for hidden reasoning, scratchpads, or chain-of-thought.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

CANONICAL_RESPONSE_VERSION = 1


class ResponseState(StrEnum):
    """Allowed lifecycle states for a canonical response."""

    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    CANCELED = "canceled"
    FAILED = "failed"


class ConfidenceLevel(StrEnum):
    """User-facing confidence levels for a canonical response."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


Confidence = ConfidenceLevel
ActionKind = Literal["proposed_action", "read_only"]


def _parse_response_state(value: object) -> object:
    """Parse a JSON string without enabling general Pydantic coercion."""

    if type(value) is str:
        return ResponseState(value)
    return value


def _parse_confidence_level(value: object) -> object:
    """Parse a JSON string without enabling general Pydantic coercion."""

    if type(value) is str:
        return ConfidenceLevel(value)
    return value


PositiveStrictInt = Annotated[StrictInt, Field(ge=1)]
StrictResponseState = Annotated[ResponseState, BeforeValidator(_parse_response_state)]
StrictConfidenceLevel = Annotated[ConfidenceLevel, BeforeValidator(_parse_confidence_level)]

_TOKEN_RE = re.compile(r"[^\W_]+(?:['-][^\W_]+)*", re.UNICODE)
_MARKDOWN_RE = re.compile(
    r"(?:"
    r"^\s{0,3}(?:#{1,6}\s|>\s?|[-+*]\s+|\d+[.)]\s+|`{3}|~{3})"
    r"|`"
    r"|!\["
    r"|\[[^\]\n]+\]\([^)\n]+\)"
    r"|\*"
    r"|__"
    r"|~~"
    r"|(?<!\w)_(?=\S)[^\n]*?(?<=\S)_(?!\w)"
    r"|</?[A-Za-z][^>\n]*>"
    r")",
    re.MULTILINE,
)

# Grammatical and conversational glue does not add a factual claim. Negations,
# modals, measurements, identifiers, and domain nouns are intentionally absent.
_LEXICAL_GLUE = frozenset({
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "but",
    "by",
    "for",
    "from",
    "had",
    "has",
    "have",
    "here",
    "i",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "our",
    "please",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
    "you",
    "your",
})

_UNCERTAINTY_GROUPS = (
    frozenset({
        "apparently",
        "appear",
        "appears",
        "could",
        "likely",
        "may",
        "might",
        "possible",
        "possibly",
        "potential",
        "potentially",
        "seem",
        "seems",
        "suggest",
        "suggested",
        "suggests",
        "suspect",
        "suspected",
    }),
    frozenset({
        "ambiguous",
        "inconclusive",
        "insufficient",
        "limited",
        "missing",
        "pending",
        "uncertain",
        "uncertainty",
        "unclear",
        "unconfirmed",
        "undetermined",
        "unknown",
        "unverified",
    }),
    frozenset({
        "about",
        "approximate",
        "approximately",
        "estimate",
        "estimated",
        "roughly",
    }),
)
_NEGATED_KNOWLEDGE_RE = re.compile(
    r"\b(?:can't|cannot|couldn't|no|not|never)\b"
    r"(?:[^\w]+[\w'-]+){0,4}[^\w]+"
    r"(?:available|confirm|confirmed|data|determine|determined|evidence|know|known|"
    r"verify|verified)\b",
    re.IGNORECASE,
)
_NO_SAFETY_BOUNDARY = frozenset({
    "none",
    "none identified",
    "no additional safety boundary",
    "no safety boundary",
})


def _tokens(value: str) -> frozenset[str]:
    """Return stable, case-insensitive lexical tokens."""

    return frozenset(match.casefold() for match in _TOKEN_RE.findall(value))


def _substantive_tokens(value: str) -> frozenset[str]:
    """Remove only grammatical glue from lexical tokens."""

    return _tokens(value) - _LEXICAL_GLUE


def _token_sequence(value: str) -> str:
    """Return normalized tokens in their original order for caveat matching."""
    return " ".join(match.casefold() for match in _TOKEN_RE.findall(value))


def _require_non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class _StrictCanonicalModel(BaseModel):
    """Base configuration shared by every canonical nested object."""

    model_config = ConfigDict(extra="forbid", strict=True)


class EvidenceLocator(_StrictCanonicalModel):
    """A typed location within an authorized evidence source."""

    field: StrictStr | None = None
    page: PositiveStrictInt | None = None
    chunk: StrictStr | None = None

    @field_validator("field", "chunk")
    @classmethod
    def _validate_text_coordinate(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_non_blank(value, field_name="locator coordinate")

    @model_validator(mode="after")
    def _require_a_coordinate(self) -> EvidenceLocator:
        if self.field is None and self.page is None and self.chunk is None:
            raise ValueError("locator must contain at least one of field, page, or chunk")
        return self


class EvidenceEntry(_StrictCanonicalModel):
    """Revision-bound, authorization-aware evidence for a visible claim."""

    source_type: StrictStr
    source_id: StrictStr
    source_revision: StrictStr
    locator: EvidenceLocator
    as_of: AwareDatetime
    authorization_class: StrictStr
    claim: StrictStr

    @field_validator(
        "source_type",
        "source_id",
        "source_revision",
        "authorization_class",
        "claim",
    )
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_blank(value, field_name="evidence text")


class RecommendedAction(_StrictCanonicalModel):
    """An advisory proposed effect or a purely read-only recommendation."""

    kind: ActionKind
    title: StrictStr
    detail: StrictStr
    requires_approval: StrictBool

    @field_validator("title", "detail")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_blank(value, field_name="action text")

    @model_validator(mode="after")
    def _validate_approval_semantics(self) -> RecommendedAction:
        if self.kind == "proposed_action" and not self.requires_approval:
            raise ValueError("proposed_action requires requires_approval=true")
        if self.kind == "read_only" and self.requires_approval:
            raise ValueError("read_only must not imply an approval operation")
        return self


class CanonicalTurnResponse(_StrictCanonicalModel):
    """The complete version-1 response contract shared by typed and voice turns."""

    kind: StrictStr
    response_version: Literal[1]
    response_state: StrictResponseState
    detailed_response: StrictStr
    spoken_summary: StrictStr
    reasoning_summary: StrictStr
    confidence: StrictConfidenceLevel
    evidence: list[EvidenceEntry]
    next_questions: list[StrictStr]
    recommended_actions: list[RecommendedAction]
    safety_boundary: StrictStr
    speak: StrictBool

    @field_validator("kind", "detailed_response", "reasoning_summary", "safety_boundary")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_blank(value, field_name="response text")

    @field_validator("next_questions")
    @classmethod
    def _validate_questions(cls, value: list[str]) -> list[str]:
        for question in value:
            _require_non_blank(question, field_name="next question")
        return value

    @field_validator("spoken_summary")
    @classmethod
    def _validate_plain_spoken_text(cls, value: str) -> str:
        if any(unicodedata.category(character).startswith("C") for character in value):
            raise ValueError("spoken_summary must not contain control characters")
        if _MARKDOWN_RE.search(value):
            raise ValueError("spoken_summary must be plain text, not Markdown")
        return value

    @model_validator(mode="after")
    def _validate_state_and_spoken_summary(self) -> CanonicalTurnResponse:
        if self.response_state != "complete":
            if self.recommended_actions:
                raise ValueError("only a complete response may recommend actions")
            if self.speak:
                raise ValueError("non-complete responses cannot enable answer speech")
            if self.spoken_summary:
                raise ValueError("non-complete responses cannot carry an answer summary")
            return self

        if self.speak and not self.spoken_summary.strip():
            raise ValueError("a complete spoken response requires a non-empty summary")

        if self.spoken_summary:
            self._validate_lexical_spoken_consistency()
        return self

    def _validate_lexical_spoken_consistency(self) -> None:
        """Apply conservative lexical entailment and caveat-preservation checks."""

        visible_text = " ".join((
            self.detailed_response,
            self.reasoning_summary,
            self.safety_boundary,
        ))
        visible_tokens = _substantive_tokens(visible_text)
        spoken_tokens = _substantive_tokens(self.spoken_summary)

        added_tokens = spoken_tokens - visible_tokens
        if added_tokens:
            rendered = ", ".join(sorted(added_tokens))
            raise ValueError(
                f"spoken_summary adds substantive tokens absent from visible text: {rendered}"
            )

        all_visible_tokens = _tokens(visible_text)
        all_spoken_tokens = _tokens(self.spoken_summary)
        for uncertainty_group in _UNCERTAINTY_GROUPS:
            if all_visible_tokens & uncertainty_group and not (
                all_spoken_tokens & uncertainty_group
            ):
                raise ValueError("spoken_summary drops a material uncertainty marker")

        visible_negative_knowledge = _NEGATED_KNOWLEDGE_RE.search(visible_text)
        spoken_negative_knowledge = _NEGATED_KNOWLEDGE_RE.search(self.spoken_summary)
        if visible_negative_knowledge and not spoken_negative_knowledge:
            raise ValueError("spoken_summary drops a material uncertainty negation")

        normalized_boundary = " ".join(self.safety_boundary.casefold().split()).rstrip(".")
        if normalized_boundary and normalized_boundary not in _NO_SAFETY_BOUNDARY:
            boundary_sequence = _token_sequence(self.safety_boundary)
            spoken_sequence = _token_sequence(self.spoken_summary)
            if boundary_sequence not in spoken_sequence:
                raise ValueError("spoken_summary must preserve the safety boundary in order")
            missing_safety_tokens = _substantive_tokens(self.safety_boundary) - spoken_tokens
            if missing_safety_tokens:
                rendered = ", ".join(sorted(missing_safety_tokens))
                raise ValueError(
                    f"spoken_summary drops material safety-boundary tokens: {rendered}"
                )


__all__ = [
    "CANONICAL_RESPONSE_VERSION",
    "ActionKind",
    "CanonicalTurnResponse",
    "Confidence",
    "ConfidenceLevel",
    "EvidenceEntry",
    "EvidenceLocator",
    "RecommendedAction",
    "ResponseState",
]
