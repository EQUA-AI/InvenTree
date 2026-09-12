"""Immutable, JSON-safe models for the shared pending-decision rail.

The cached record is the server source of truth.  :meth:`to_public_dict`
deliberately projects only fields that may later appear on the voice wire;
actor, scope and executable bindings remain server-only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar

PENDING_DECISION_SCHEMA_VERSION = "pending-decision-v1"
MAX_DECISION_SOURCE_CONTENT_CHARS = 4000


class DecisionKind(StrEnum):
    """Supported coordinator contracts."""

    ACTION = "action"
    SELECTION = "selection"
    APPROVAL_REVIEW = "approval_review"
    APPROVAL_DECISION = "approval_decision"
    TRANSCRIPT_REVIEW = "transcript_review"
    CAPTURE_REVIEW = "capture_review"


class DecisionState(StrEnum):
    """Lifecycle of the interaction overlay, not of its durable source."""

    PRESENTED = "presented"
    SET_ASIDE = "set_aside"
    EXECUTING = "executing"
    RESOLVED = "resolved"
    DISARMED = "disarmed"
    EXPIRED = "expired"


class DecisionDeliveryState(StrEnum):
    """Delivery state of the decision's bound read-back utterance."""

    PENDING = "pending"
    REQUESTED = "requested"
    PLAYING = "playing"
    DONE = "done"
    CANCELED = "canceled"
    FAILED = "failed"


class DisarmReason(StrEnum):
    """Bounded reasons a coordinator may use when it disarms a decision."""

    DECLINED = "declined"
    CANCELLED_BY_USER = "cancelled_by_user"
    AMENDED = "amended"
    UNRELATED = "unrelated"
    TRANSCRIPT_REVIEW = "transcript_review"
    ACTOR_MISMATCH = "actor_mismatch"
    SESSION_MISMATCH = "session_mismatch"
    THREAD_MISMATCH = "thread_mismatch"
    SCOPE_MISMATCH = "scope_mismatch"
    REVISION_MISMATCH = "revision_mismatch"
    SOURCE_INVALIDATED = "source_invalidated"
    EXPIRED = "expired"
    QUESTION_CONTRADICTION = "question_contradiction"
    INJECTION_REFUSED = "injection_refused"
    SAFETY_REFUSED = "safety_refused"


PUBLIC_DECISION_FIELDS = (
    "decision_id",
    "kind",
    "source_id",
    "revision",
    "state",
    "target_label",
    "sections",
    "required_review_sections",
    "allowed_responses",
    "required_phrase",
    "locale",
    "voice_eligible",
    "voice_ineligible_reason",
    "preview_hash",
    "expires_at",
    "sequence",
    "utterance_id",
    "delivery_state",
    "review_acknowledged",
    "operation_id",
    "execution_state",
    "receipt_ref",
    "spoken_summary",
    "spoken_summary_hash",
)

SERVER_DECISION_FIELDS = (
    "actor_user_pk",
    "session_id",
    "thread_id",
    "scope_hash",
    "nonce",
    "executable",
    "source_content",
    "armed_at",
    "playback_completed_at",
    "review_turns",
    "playback_requested_at",
    "playback_started_at",
)


def _plain_json(value: Any) -> Any:
    """Convert immutable JSON containers back to ordinary containers."""

    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_json(item) for item in value]
    return value


def _json_copy(value: Any) -> Any:
    """Validate JSON safety and return a detached value."""

    try:
        return json.loads(
            json.dumps(
                _plain_json(value),
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("pending decision values must be JSON-safe") from exc


def _freeze_json(value: Any) -> Any:
    """Detach and recursively freeze one JSON-compatible value."""

    def freeze(item: Any) -> Any:
        if isinstance(item, dict):
            return MappingProxyType({key: freeze(child) for key, child in item.items()})
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(_json_copy(value))


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _parse_datetime(value: Any, field_name: str, *, optional: bool = False) -> datetime | None:
    if value is None and optional:
        return None
    if isinstance(value, datetime):
        return _aware_utc(value, field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    return _aware_utc(parsed, field_name)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class PendingDecision:
    """One actor-, session-, thread- and scope-bound pending decision."""

    decision_id: str
    kind: DecisionKind
    source_id: str
    revision: int | str
    state: DecisionState
    target_label: str
    expires_at: datetime
    sequence: int
    actor_user_pk: str
    session_id: str
    thread_id: str
    scope_hash: str
    nonce: str
    armed_at: datetime
    source_content: str
    sections: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    required_review_sections: tuple[str, ...] = field(default_factory=tuple)
    allowed_responses: tuple[str, ...] = field(default_factory=tuple)
    required_phrase: str | None = None
    locale: str = "en"
    voice_eligible: bool = True
    voice_ineligible_reason: str | None = None
    preview_hash: str | None = None
    utterance_id: str | None = None
    delivery_state: DecisionDeliveryState = DecisionDeliveryState.PENDING
    review_acknowledged: bool = False
    operation_id: str | None = None
    execution_state: str | None = None
    receipt_ref: str | None = None
    executable: Mapping[str, Any] | None = None
    playback_completed_at: datetime | None = None
    review_turns: int = 0
    spoken_summary: str = ""
    spoken_summary_hash: str = ""
    playback_requested_at: datetime | None = None
    playback_started_at: datetime | None = None

    schema_version: ClassVar[str] = PENDING_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "source_id",
            "target_label",
            "actor_user_pk",
            "session_id",
            "thread_id",
            "scope_hash",
            "nonce",
            "source_content",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if len(self.source_content) > MAX_DECISION_SOURCE_CONTENT_CHARS:
            raise ValueError(
                f"source_content cannot exceed {MAX_DECISION_SOURCE_CONTENT_CHARS} characters"
            )

        if isinstance(self.revision, bool) or not isinstance(self.revision, (int, str)):
            raise ValueError("revision must be an integer or string")
        if isinstance(self.revision, int) and self.revision < 0:
            raise ValueError("revision cannot be negative")
        if isinstance(self.revision, str) and not self.revision.strip():
            raise ValueError("revision cannot be empty")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("sequence must be a positive integer")
        if isinstance(self.review_turns, bool) or not isinstance(self.review_turns, int):
            raise ValueError("review_turns must be a non-negative integer")
        if self.review_turns < 0:
            raise ValueError("review_turns must be a non-negative integer")
        if not isinstance(self.voice_eligible, bool):
            raise ValueError("voice_eligible must be a boolean")
        if not isinstance(self.review_acknowledged, bool):
            raise ValueError("review_acknowledged must be a boolean")
        if not isinstance(self.locale, str) or not self.locale.strip():
            raise ValueError("locale must be a non-empty string")
        try:
            kind = DecisionKind(self.kind)
            state = DecisionState(self.state)
            delivery_state = DecisionDeliveryState(self.delivery_state)
        except (TypeError, ValueError) as exc:
            raise ValueError("kind, state and delivery_state must be recognized values") from exc
        for name in (
            "required_phrase",
            "voice_ineligible_reason",
            "preview_hash",
            "utterance_id",
            "operation_id",
            "execution_state",
            "receipt_ref",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or null")
        if not self.voice_eligible and not (
            isinstance(self.voice_ineligible_reason, str) and self.voice_ineligible_reason.strip()
        ):
            raise ValueError("voice_ineligible_reason is required when voice_eligible is false")
        if not isinstance(self.sections, (tuple, list)) or not all(
            isinstance(section, Mapping) for section in self.sections
        ):
            raise ValueError("sections must contain JSON object values")
        for name in ("required_review_sections", "allowed_responses"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                raise ValueError(f"{name} must contain non-empty strings")
        section_ids: list[str] = []
        for section in self.sections:
            section_id = section.get("id")
            if not isinstance(section_id, str) or not section_id.strip():
                raise ValueError("each section must have a non-empty string id")
            section_ids.append(section_id)
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section ids must be unique")
        if missing_sections := set(self.required_review_sections) - set(section_ids):
            raise ValueError(
                f"required_review_sections reference unknown sections: {sorted(missing_sections)}"
            )
        if self.executable is not None and not isinstance(self.executable, Mapping):
            raise ValueError("executable must be a JSON object or null")

        armed_at = _aware_utc(self.armed_at, "armed_at")
        expires_at = _aware_utc(self.expires_at, "expires_at")
        playback_completed_at = (
            _aware_utc(self.playback_completed_at, "playback_completed_at")
            if self.playback_completed_at is not None
            else None
        )
        if expires_at < armed_at:
            raise ValueError("expires_at cannot precede armed_at")
        if playback_completed_at is not None and playback_completed_at < armed_at:
            raise ValueError("playback_completed_at cannot precede armed_at")
        if playback_completed_at is not None and playback_completed_at > expires_at:
            raise ValueError("playback_completed_at cannot follow expires_at")

        sections = _freeze_json(self.sections)
        executable = _freeze_json(self.executable) if self.executable is not None else None
        object.__setattr__(self, "sections", sections)
        object.__setattr__(
            self,
            "required_review_sections",
            tuple(str(value) for value in self.required_review_sections),
        )
        object.__setattr__(
            self, "allowed_responses", tuple(str(value) for value in self.allowed_responses)
        )
        object.__setattr__(self, "armed_at", armed_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "playback_completed_at", playback_completed_at)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "delivery_state", delivery_state)
        object.__setattr__(self, "executable", executable)
        for name in ("playback_requested_at", "playback_started_at"):
            value = getattr(self, name)
            if value is not None:
                value = _aware_utc(value, name)
                if value < armed_at:
                    raise ValueError(f"{name} cannot precede armed_at")
                object.__setattr__(self, name, value)

    def to_public_dict(self) -> dict[str, Any]:
        """Return only fields safe for the pending-decision wire contract."""

        return {
            "decision_id": self.decision_id,
            "kind": str(self.kind),
            "source_id": self.source_id,
            "revision": self.revision,
            "state": str(self.state),
            "target_label": self.target_label,
            "sections": _json_copy(list(self.sections)),
            "required_review_sections": list(self.required_review_sections),
            "allowed_responses": list(self.allowed_responses),
            "required_phrase": self.required_phrase,
            "locale": self.locale,
            "voice_eligible": self.voice_eligible,
            "voice_ineligible_reason": self.voice_ineligible_reason,
            "preview_hash": self.preview_hash,
            "expires_at": _iso(self.expires_at),
            "sequence": self.sequence,
            "utterance_id": self.utterance_id,
            "delivery_state": str(self.delivery_state),
            "review_acknowledged": self.review_acknowledged,
            "operation_id": self.operation_id,
            "execution_state": self.execution_state,
            "receipt_ref": self.receipt_ref,
            "spoken_summary": self.spoken_summary,
            "spoken_summary_hash": self.spoken_summary_hash,
        }

    def to_record(self) -> dict[str, Any]:
        """Return the complete JSON-safe cache record, including private bindings."""

        record = self.to_public_dict()
        record.update({
            "schema_version": self.schema_version,
            "actor_user_pk": self.actor_user_pk,
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "scope_hash": self.scope_hash,
            "nonce": self.nonce,
            "executable": _json_copy(self.executable),
            "source_content": self.source_content,
            "armed_at": _iso(self.armed_at),
            "playback_completed_at": _iso(self.playback_completed_at),
            "review_turns": self.review_turns,
            "playback_requested_at": _iso(self.playback_requested_at),
            "playback_started_at": _iso(self.playback_started_at),
        })
        return _json_copy(record)

    @classmethod
    def from_record(cls, record: Any) -> PendingDecision:
        """Validate and reconstruct one cache record, failing closed on drift."""

        if not isinstance(record, dict):
            raise ValueError("pending decision record must be a mapping")
        if record.get("schema_version") != PENDING_DECISION_SCHEMA_VERSION:
            raise ValueError("unsupported pending decision schema")
        expected_fields = {
            "schema_version",
            *PUBLIC_DECISION_FIELDS,
            *SERVER_DECISION_FIELDS,
        }
        actual_fields = set(record)
        if unknown := actual_fields - expected_fields:
            raise ValueError(f"pending decision record has unknown fields: {sorted(unknown)}")
        if missing := expected_fields - actual_fields:
            raise ValueError(f"pending decision record is missing fields: {sorted(missing)}")
        try:
            return cls(
                decision_id=record["decision_id"],
                kind=DecisionKind(record["kind"]),
                source_id=record["source_id"],
                revision=record["revision"],
                state=DecisionState(record["state"]),
                target_label=record["target_label"],
                sections=record["sections"],
                required_review_sections=record["required_review_sections"],
                allowed_responses=record["allowed_responses"],
                required_phrase=record["required_phrase"],
                locale=record["locale"],
                voice_eligible=record["voice_eligible"],
                voice_ineligible_reason=record["voice_ineligible_reason"],
                preview_hash=record["preview_hash"],
                expires_at=_parse_datetime(record["expires_at"], "expires_at"),
                sequence=record["sequence"],
                utterance_id=record["utterance_id"],
                delivery_state=DecisionDeliveryState(record["delivery_state"]),
                review_acknowledged=record["review_acknowledged"],
                operation_id=record["operation_id"],
                execution_state=record["execution_state"],
                receipt_ref=record["receipt_ref"],
                actor_user_pk=record["actor_user_pk"],
                session_id=record["session_id"],
                thread_id=record["thread_id"],
                scope_hash=record["scope_hash"],
                nonce=record["nonce"],
                executable=record["executable"],
                source_content=record["source_content"],
                armed_at=_parse_datetime(record["armed_at"], "armed_at"),
                playback_completed_at=_parse_datetime(
                    record["playback_completed_at"],
                    "playback_completed_at",
                    optional=True,
                ),
                review_turns=record["review_turns"],
                spoken_summary=record["spoken_summary"],
                spoken_summary_hash=record["spoken_summary_hash"],
                playback_requested_at=_parse_datetime(
                    record["playback_requested_at"], "playback_requested_at", optional=True
                ),
                playback_started_at=_parse_datetime(
                    record["playback_started_at"], "playback_started_at", optional=True
                ),
            )
        except KeyError as exc:
            raise ValueError(f"pending decision record is missing {exc.args[0]}") from exc


__all__ = [
    "MAX_DECISION_SOURCE_CONTENT_CHARS",
    "PENDING_DECISION_SCHEMA_VERSION",
    "PUBLIC_DECISION_FIELDS",
    "SERVER_DECISION_FIELDS",
    "DecisionDeliveryState",
    "DecisionKind",
    "DecisionState",
    "DisarmReason",
    "PendingDecision",
]
