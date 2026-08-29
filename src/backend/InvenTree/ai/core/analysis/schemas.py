"""Canonical evidence-analysis response v2 and the synthesis claim schema (S10).

Version 1 (``ai.core.reasoning.schemas.CanonicalTurnResponse``) is preserved
byte-untouched; this module adds the version-discriminated v2 used by the
ANALYSIS rail. The two deliberate contract deltas from v1 (§7.5 + review
record improvement 9):

- **No confidence field exists.** Claims carry a typed
  ``evidence_classification`` (``documented / calculated / inferred /
  insufficient``); v1's diagnostic ``confidence`` stays quarantined as
  legacy diagnostic metadata and is never presented as evidence strength.
- **The model never authors values.** ``SynthesisClaimSet`` — the only
  shape the synthesis model may emit — has claim slots with refs, template
  keys, and a bounded paraphrase; fields for numbers, dates, identifiers,
  and rendered text simply do not exist. The deterministic renderer
  (``renderer.py``) inserts exact values and citation ordinals; the
  validator (``validator.py``) closes over every visible token.

``incomplete_reasons`` is a top-level addition over the §7.5 example: a
non-safety timeout with at least one validated facet returns
``response_state="partial"`` (durable ``TurnState.INCOMPLETE``) with typed
reasons instead of dropping the whole answer (review record Q27).
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

from ai.core.reasoning.schemas import (
    CanonicalTurnResponse as CanonicalTurnResponseV1,
)
from ai.core.reasoning.schemas import (
    EvidenceEntry,
    RecommendedAction,
)
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)

ANALYSIS_RESPONSE_VERSION = 2

#: Upper bound for a model-authored paraphrase slot. Long free prose defeats
#: the closure scan's precision; a bounded clause cannot smuggle a table.
MAX_PARAPHRASE_CHARS = 240


class AnalysisResponseState(StrEnum):
    """Lifecycle states for a v2 response; ``partial`` is new over v1."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    INCOMPLETE = "incomplete"
    CANCELED = "canceled"
    FAILED = "failed"


class EvidenceClassification(StrEnum):
    """The §7.5 evidence classification enum — replaces model confidence."""

    DOCUMENTED = "documented"
    CALCULATED = "calculated"
    INFERRED = "inferred"
    INSUFFICIENT = "insufficient"


class ClaimType(StrEnum):
    """Semantic claim type, independent of evidence classification."""

    DIRECT_SOURCE_FACT = "direct_source_fact"
    CALCULATION = "calculation"
    DERIVED_GROUPING = "derived_grouping"
    INFERENCE = "inference"
    LIMITATION = "limitation"
    UNKNOWN = "unknown"


class FacetStatus(StrEnum):
    """Terminal status of one requested facet (§8.6 check C08)."""

    ANSWERED = "answered"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    INCOMPLETE = "incomplete"


def _parse_str_enum(enum_cls: type[StrEnum]):
    """Accept the JSON string form without enabling general coercion."""

    def _parse(value: object) -> object:
        if type(value) is str:
            return enum_cls(value)
        return value

    return _parse


StrictAnalysisResponseState = Annotated[
    AnalysisResponseState, BeforeValidator(_parse_str_enum(AnalysisResponseState))
]
StrictEvidenceClassification = Annotated[
    EvidenceClassification, BeforeValidator(_parse_str_enum(EvidenceClassification))
]
StrictClaimType = Annotated[ClaimType, BeforeValidator(_parse_str_enum(ClaimType))]
StrictFacetStatus = Annotated[FacetStatus, BeforeValidator(_parse_str_enum(FacetStatus))]

# Deliberately conservative: the spoken summary joins a TTS surface, so any
# markup marker rejects. Mirrors the v1 posture without importing v1's
# private helpers (that module must stay byte-untouched).
_SPOKEN_MARKDOWN_RE = re.compile(
    r"(?:^\s{0,3}(?:#{1,6}\s|>\s?|[-+*]\s+|\d+[.)]\s+|`{3})|`|!\[|\[[^\]\n]+\]\([^)\n]+\)|\*|__|~~|</?[A-Za-z][^>\n]*>)",
    re.MULTILINE,
)


def _require_non_blank(value: str, *, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class _StrictAnalysisModel(BaseModel):
    """Base configuration shared by every v2 nested object."""

    model_config = ConfigDict(extra="forbid", strict=True)


class AnalysisClaim(_StrictAnalysisModel):
    """One validated claim slot; values live in referenced facts, never here."""

    claim_id: StrictStr
    claim_role: Literal["answer", "limitation", "context"]
    claim_type: StrictClaimType
    evidence_classification: StrictEvidenceClassification
    fact_refs: list[StrictStr]
    calculation_output_refs: list[StrictStr]
    evidence_refs: list[StrictStr]
    entity_refs: list[StrictStr]
    render_template: StrictStr
    paraphrase: StrictStr = ""

    @field_validator("claim_id", "render_template")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_blank(value, field_name="claim text")

    @field_validator("paraphrase")
    @classmethod
    def _validate_paraphrase_bound(cls, value: str) -> str:
        if len(value) > MAX_PARAPHRASE_CHARS:
            raise ValueError(f"paraphrase exceeds {MAX_PARAPHRASE_CHARS} characters")
        return value

    @model_validator(mode="after")
    def _validate_classification_basis(self) -> AnalysisClaim:
        """Structural half of the typed-basis check (§8.6 C03)."""
        classification = self.evidence_classification
        if classification is EvidenceClassification.DOCUMENTED and not self.fact_refs:
            raise ValueError("a documented claim must reference at least one fact")
        if classification is EvidenceClassification.CALCULATED and not self.calculation_output_refs:
            raise ValueError("a calculated claim must reference a calculation output")
        if classification is EvidenceClassification.INFERRED and not (
            self.fact_refs or self.calculation_output_refs
        ):
            raise ValueError("an inferred claim must cite documented/calculated premises")
        return self


class AnalysisFacet(_StrictAnalysisModel):
    """One requested facet and the claims that resolve it."""

    name: StrictStr
    status: StrictFacetStatus
    claim_ids: list[StrictStr]

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _require_non_blank(value, field_name="facet name")


class IncompleteReason(_StrictAnalysisModel):
    """A typed reason a facet did not complete (partial-state contract)."""

    code: Literal[
        "retrieval_timeout",
        "synthesis_timeout",
        "facet_budget_exhausted",
        "capability_boundary",
        "population_cap_exceeded",
    ]
    facet: StrictStr

    @field_validator("facet")
    @classmethod
    def _validate_facet(cls, value: str) -> str:
        return _require_non_blank(value, field_name="incomplete facet")


class EvidenceAnalysisPayload(_StrictAnalysisModel):
    """The typed v2 payload: facets, claims, and bounded framing lists."""

    payload_type: Literal["evidence_analysis_v2"]
    facets: list[AnalysisFacet]
    claims: list[AnalysisClaim]
    assumptions: list[StrictStr]
    inclusion_rules: list[StrictStr]
    exclusion_rules: list[StrictStr]
    unknowns: list[StrictStr]

    @model_validator(mode="after")
    def _validate_claim_closure(self) -> EvidenceAnalysisPayload:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim ids must be unique")
        known = set(claim_ids)
        for facet in self.facets:
            missing = [ref for ref in facet.claim_ids if ref not in known]
            if missing:
                raise ValueError(f"facet '{facet.name}' references unknown claims")
        return self


class CanonicalEvidenceAnalysisResponseV2(_StrictAnalysisModel):
    """The complete version-2 evidence-analysis response contract.

    There is intentionally no ``confidence`` field; ``extra="forbid"`` makes
    supplying one a validation error, which the conformance suite pins.
    """

    kind: Literal["evidence_analysis"]
    response_version: Literal[2]
    response_state: StrictAnalysisResponseState
    detailed_response: StrictStr
    spoken_summary: StrictStr
    reasoning_summary: StrictStr
    evidence: list[EvidenceEntry]
    next_questions: list[StrictStr]
    recommended_actions: list[RecommendedAction]
    safety_boundary: StrictStr
    speak: StrictBool
    payload: EvidenceAnalysisPayload
    incomplete_reasons: list[IncompleteReason] = []

    @field_validator("detailed_response", "reasoning_summary", "safety_boundary")
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
        if _SPOKEN_MARKDOWN_RE.search(value):
            raise ValueError("spoken_summary must be plain text, not Markdown")
        return value

    @model_validator(mode="after")
    def _validate_state_invariants(self) -> CanonicalEvidenceAnalysisResponseV2:
        complete = self.response_state is AnalysisResponseState.COMPLETE
        degraded = self.response_state in (
            AnalysisResponseState.PARTIAL,
            AnalysisResponseState.INCOMPLETE,
        )
        if not complete:
            if self.recommended_actions:
                raise ValueError("only a complete response may recommend actions")
            if self.speak:
                raise ValueError("non-complete responses cannot enable answer speech")
            if self.spoken_summary:
                raise ValueError("non-complete responses cannot carry an answer summary")
        if degraded and not self.incomplete_reasons:
            raise ValueError("partial/incomplete responses require typed reasons")
        if complete and self.incomplete_reasons:
            raise ValueError("a complete response cannot carry incomplete reasons")
        if complete and self.speak and not self.spoken_summary.strip():
            raise ValueError("a complete spoken response requires a non-empty summary")
        return self


#: Version-discriminated canonical union — one projector/replay path serves
#: both versions; ``response_version`` (1 vs 2) is the discriminator.
CanonicalResponse = Annotated[
    CanonicalTurnResponseV1 | CanonicalEvidenceAnalysisResponseV2,
    Field(discriminator="response_version"),
]

_CANONICAL_ADAPTER: TypeAdapter[Any] = TypeAdapter(CanonicalResponse)


def parse_canonical_response(
    data: Mapping[str, Any],
) -> CanonicalTurnResponseV1 | CanonicalEvidenceAnalysisResponseV2:
    """Parse a stored/wire canonical dict into its versioned model.

    Validation runs in JSON mode: stored ``canonical_result`` dicts come
    from ``model_dump(mode="json")``/JSONField round-trips, so datetimes
    are ISO strings — exactly what strict JSON-mode accepts and strict
    python-mode would reject.
    """
    import json

    return _CANONICAL_ADAPTER.validate_json(json.dumps(dict(data)))


class SynthesisClaimSlot(_StrictAnalysisModel):
    """What the synthesis model may emit for one claim: refs and framing only.

    No field for a number, date, identifier, unit, or rendered sentence
    exists — the schema is the fence (§7.5: the model organizes documented
    facts; the server inserts exact values).
    """

    claim_id: StrictStr
    claim_role: Literal["answer", "limitation", "context"]
    claim_type: StrictClaimType
    evidence_classification: StrictEvidenceClassification
    fact_refs: list[StrictStr]
    calculation_output_refs: list[StrictStr]
    evidence_refs: list[StrictStr]
    entity_refs: list[StrictStr]
    render_template: StrictStr
    paraphrase: StrictStr = ""

    @field_validator("claim_id", "render_template")
    @classmethod
    def _validate_required_text(cls, value: str) -> str:
        return _require_non_blank(value, field_name="claim text")

    @field_validator("paraphrase")
    @classmethod
    def _validate_paraphrase_bound(cls, value: str) -> str:
        if len(value) > MAX_PARAPHRASE_CHARS:
            raise ValueError(f"paraphrase exceeds {MAX_PARAPHRASE_CHARS} characters")
        return value


class SynthesisClaimSet(_StrictAnalysisModel):
    """The complete synthesis emission: facets + claim slots + unknowns."""

    facets: list[AnalysisFacet]
    claims: list[SynthesisClaimSlot]
    assumptions: list[StrictStr]
    unknowns: list[StrictStr]


__all__ = [
    "ANALYSIS_RESPONSE_VERSION",
    "MAX_PARAPHRASE_CHARS",
    "AnalysisClaim",
    "AnalysisFacet",
    "AnalysisResponseState",
    "CanonicalEvidenceAnalysisResponseV2",
    "CanonicalResponse",
    "CanonicalTurnResponseV1",
    "ClaimType",
    "EvidenceAnalysisPayload",
    "EvidenceClassification",
    "FacetStatus",
    "IncompleteReason",
    "SynthesisClaimSet",
    "SynthesisClaimSlot",
    "parse_canonical_response",
]
