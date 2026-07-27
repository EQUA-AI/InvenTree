"""Fail-closed read-only tools for record-scoped diagnostic assistance.

The diagnostic registry is deliberately separate from the legacy inventory
tool collection.  A caller must supply a server-built :class:`DiagnosticContext`
and every execution obtains a new authorization grant before it asks a reader
for content.  The production reader is a thin, lazy adapter over domain service
functions; tests can inject an in-memory reader without loading Django models.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Annotated, Any, Literal, Protocol

from ai.core.auth import AIPrincipal
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

MACHINE_READ_CAPABILITY = "diagnostics.machine.read"
PACKET_READ_CAPABILITY = "diagnostics.packet.read"
MAINTENANCE_READ_CAPABILITY = "diagnostics.maintenance.read"
HEALTH_READ_CAPABILITY = "diagnostics.health.read"
MANUAL_READ_CAPABILITY = "diagnostics.manuals.read"
PLAYBOOK_READ_CAPABILITY = "diagnostics.playbooks.read"
PARTS_READ_CAPABILITY = "diagnostics.parts.read"
SAFETY_P0_CAPABILITY = "diagnostics.safety_p0.read"

BASE_DIAGNOSTIC_TOOL_NAMES = (
    "get_machine_context",
    "get_repair_packet",
    "get_recent_maintenance_history",
    "get_machine_health_summary",
    "get_machine_health_anomalies",
    "search_approved_manuals",
    "find_published_repair_playbooks",
    "get_parts_availability",
)
LIVE_SAFETY_TOOL_NAME = "get_live_safety_status"

UNTRUSTED_CONTENT_BEGIN = "[UNTRUSTED-CONTENT-BEGIN]"
UNTRUSTED_CONTENT_END = "[UNTRUSTED-CONTENT-END]"
NON_ENUMERATING_DENIAL = "Diagnostic record access was not authorized"
_ESCAPED_UNTRUSTED_MARKER = "[UNTRUSTED-CONTENT-MARKER-ESCAPED]"

_KNOWN_CAPABILITIES = frozenset({
    MACHINE_READ_CAPABILITY,
    PACKET_READ_CAPABILITY,
    MAINTENANCE_READ_CAPABILITY,
    HEALTH_READ_CAPABILITY,
    MANUAL_READ_CAPABILITY,
    PLAYBOOK_READ_CAPABILITY,
    PARTS_READ_CAPABILITY,
    SAFETY_P0_CAPABILITY,
})

StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictRevision = Annotated[str, Field(strict=True, min_length=1, max_length=128)]
StrictQuery = Annotated[str, Field(strict=True, min_length=1, max_length=500)]


class DiagnosticToolError(Exception):
    """Base class for safe diagnostic-tool failures."""


class DiagnosticToolNotFoundError(DiagnosticToolError):
    """Raised without reflecting an unavailable tool name."""


class DiagnosticArgumentsError(DiagnosticToolError):
    """Raised without reflecting rejected argument values."""


class DiagnosticAuthorizationError(DiagnosticToolError):
    """Non-enumerating actor, capability, scope, edge, or revision denial."""


class DiagnosticReaderError(DiagnosticToolError):
    """Raised when a reader violates the bounded read contract."""


class _StrictArguments(BaseModel):
    """Common fail-closed argument policy for every diagnostic tool."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class GetMachineContextArguments(_StrictArguments):
    """Arguments for the machine-context reader."""

    machine_id: StrictPositiveInt
    expected_revision: StrictRevision


class GetRepairPacketArguments(_StrictArguments):
    """Arguments for the redacted repair-packet reader."""

    repair_packet_id: StrictPositiveInt
    expected_revision: StrictRevision


class GetRecentMaintenanceHistoryArguments(_StrictArguments):
    """Arguments for recent authorized maintenance records."""

    machine_id: StrictPositiveInt
    expected_revision: StrictRevision
    limit: Annotated[int, Field(default=10, strict=True, ge=1, le=25)] = 10


class GetMachineHealthSummaryArguments(_StrictArguments):
    """Arguments for the current normalized machine condition."""

    machine_id: StrictPositiveInt
    expected_revision: StrictRevision


class GetMachineHealthAnomaliesArguments(_StrictArguments):
    """Arguments for the machine's active anomalies."""

    machine_id: StrictPositiveInt
    expected_revision: StrictRevision
    limit: Annotated[int, Field(default=10, strict=True, ge=1, le=25)] = 10


class SearchApprovedManualsArguments(_StrictArguments):
    """Arguments for explicitly approved machine manuals."""

    machine_id: StrictPositiveInt
    expected_revision: StrictRevision
    query: StrictQuery
    limit: Annotated[int, Field(default=5, strict=True, ge=1, le=10)] = 5


class FindPublishedRepairPlaybooksArguments(_StrictArguments):
    """Arguments for governed, published repair procedures."""

    machine_id: StrictPositiveInt
    expected_revision: StrictRevision
    query: StrictQuery
    limit: Annotated[int, Field(default=5, strict=True, ge=1, le=10)] = 5


class GetPartsAvailabilityArguments(_StrictArguments):
    """Arguments for a non-reserving availability observation."""

    machine_id: StrictPositiveInt
    expected_revision: StrictRevision
    part_ids: Annotated[list[StrictPositiveInt], Field(max_length=50)] = Field(default_factory=list)
    limit: Annotated[int, Field(default=20, strict=True, ge=1, le=50)] = 20


class GetLiveSafetyStatusArguments(_StrictArguments):
    """Arguments for the separately gated raw command-side status reader."""

    repair_packet_id: StrictPositiveInt
    expected_revision: StrictRevision


@dataclass(frozen=True, slots=True)
class DiagnosticRecordRoot:
    """Server-resolved record authority captured for one diagnostic turn."""

    entity_type: Literal["machine", "repair_packet"]
    entity_id: int
    expected_revision: str
    linked_machine_id: int | None = None
    authorization_class: str = "maintenance_scope"

    def __post_init__(self) -> None:
        """Reject ambiguous or mutable-looking root values."""
        if self.entity_type not in {"machine", "repair_packet"}:
            raise ValueError("Unsupported diagnostic record root")
        if type(self.entity_id) is not int or self.entity_id <= 0:
            raise ValueError("Record-root identifiers must be positive integers")
        if not isinstance(self.expected_revision, str) or not self.expected_revision:
            raise ValueError("Record roots require an expected revision")
        if len(self.expected_revision) > 128:
            raise ValueError("Record-root revision is too long")
        if self.entity_type == "machine" and self.linked_machine_id is not None:
            raise ValueError("Machine roots cannot declare a linked machine")
        if self.entity_type == "repair_packet" and (
            type(self.linked_machine_id) is not int or self.linked_machine_id <= 0
        ):
            raise ValueError("Repair-packet roots require a linked machine")
        if not self.authorization_class or len(self.authorization_class) > 64:
            raise ValueError("Record roots require an authorization class")


@dataclass(frozen=True, slots=True)
class DiagnosticContext:
    """Immutable authority derived only from a principal and server record roots."""

    principal: AIPrincipal
    actor: str
    capabilities: tuple[str, ...]
    record_roots: tuple[DiagnosticRecordRoot, ...]
    issued_at: datetime
    max_age_seconds: int
    context_id: str

    def root_for(self, entity_type: str, entity_id: int) -> DiagnosticRecordRoot | None:
        """Return an exact trusted root without accepting browser relationships."""
        return next(
            (
                root
                for root in self.record_roots
                if root.entity_type == entity_type and root.entity_id == entity_id
            ),
            None,
        )


def build_diagnostic_context(
    principal: AIPrincipal,
    *,
    server_record_roots: tuple[DiagnosticRecordRoot, ...],
    server_allowed_capabilities: tuple[str, ...],
    issued_at: datetime | None = None,
    max_age_seconds: int = 60,
    context_id: str | None = None,
) -> DiagnosticContext:
    """Build record authority from authenticated and server-owned inputs only."""
    if not isinstance(principal, AIPrincipal):
        raise TypeError("An authenticated AIPrincipal is required")
    if not server_record_roots:
        raise ValueError("At least one server record root is required")
    if not all(isinstance(root, DiagnosticRecordRoot) for root in server_record_roots):
        raise TypeError("Diagnostic record roots must be server-resolved roots")
    root_keys = {(root.entity_type, root.entity_id) for root in server_record_roots}
    if len(root_keys) != len(server_record_roots):
        raise ValueError("Diagnostic record roots must be unique")

    capabilities = tuple(dict.fromkeys(server_allowed_capabilities))
    if not capabilities or any(
        capability not in _KNOWN_CAPABILITIES for capability in capabilities
    ):
        raise ValueError("Diagnostic capabilities must be explicit and recognized")
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 300:
        raise ValueError("Diagnostic context freshness must be between 1 and 300 seconds")

    issued = issued_at or datetime.now(UTC)
    if issued.tzinfo is None or issued.utcoffset() is None:
        raise ValueError("Diagnostic context time must be timezone-aware")
    issued = issued.astimezone(UTC)

    if context_id is None:
        context_id = str(uuid.uuid4())
    else:
        try:
            context_id = str(uuid.UUID(context_id))
        except (AttributeError, ValueError) as exc:
            raise ValueError("Diagnostic context id must be a UUID") from exc

    return DiagnosticContext(
        principal=principal,
        actor=principal.actor,
        capabilities=capabilities,
        record_roots=tuple(server_record_roots),
        issued_at=issued,
        max_age_seconds=max_age_seconds,
        context_id=context_id,
    )


@dataclass(frozen=True, slots=True)
class ReadAuthorization:
    """Content-free result of a fresh domain authorization check."""

    check_id: str
    actor_id: str
    capability: str
    entity_type: Literal["machine", "repair_packet"]
    entity_id: int
    current_revision: str
    authorization_class: str
    scoped: bool
    linked_machine_id: int | None
    checked_at: datetime


class EvidenceClaim(BaseModel):
    """One citation-ready claim returned by an authorized domain reader."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    source_type: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    id: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    revision: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    locator: Annotated[str, Field(strict=True, min_length=1, max_length=500)]
    as_of: datetime
    authorization_class: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    claim: Annotated[str, Field(strict=True, min_length=1, max_length=65536)]
    untrusted: bool = False

    @field_validator("as_of")
    @classmethod
    def _as_of_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence time must be timezone-aware")
        return value.astimezone(UTC)


class ReaderResult(BaseModel):
    """Strict transfer object accepted from an injected reader backend."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    evidence: tuple[EvidenceClaim, ...] = ()
    abstention_reason: Annotated[str, Field(strict=True, max_length=300)] = ""


class DiagnosticReader(Protocol):
    """Minimal backend contract; authorization must not retrieve content."""

    def rehydrate_actor(self, principal: AIPrincipal) -> object | None:
        """Return the current authenticated actor, or ``None``."""

    def authorize(
        self,
        *,
        actor: object,
        principal: AIPrincipal,
        context: DiagnosticContext,
        tool_name: str,
        capability: str,
        root: DiagnosticRecordRoot,
        arguments: _StrictArguments,
        check_id: str,
    ) -> ReadAuthorization | None:
        """Perform fresh owner, scope, capability, entity, edge and revision checks."""

    def read(
        self,
        *,
        actor: object,
        tool_name: str,
        arguments: _StrictArguments,
        authorization: ReadAuthorization,
    ) -> ReaderResult | dict[str, Any]:
        """Retrieve bounded content only after authorization succeeds."""


@dataclass(frozen=True, slots=True)
class DiagnosticToolDefinition:
    """Immutable registry entry."""

    name: str
    arguments_model: type[_StrictArguments]
    capability: str
    root_type: Literal["machine", "repair_packet"]
    root_argument: Literal["machine_id", "repair_packet_id"]


_BASE_DEFINITIONS = (
    DiagnosticToolDefinition(
        "get_machine_context",
        GetMachineContextArguments,
        MACHINE_READ_CAPABILITY,
        "machine",
        "machine_id",
    ),
    DiagnosticToolDefinition(
        "get_repair_packet",
        GetRepairPacketArguments,
        PACKET_READ_CAPABILITY,
        "repair_packet",
        "repair_packet_id",
    ),
    DiagnosticToolDefinition(
        "get_recent_maintenance_history",
        GetRecentMaintenanceHistoryArguments,
        MAINTENANCE_READ_CAPABILITY,
        "machine",
        "machine_id",
    ),
    DiagnosticToolDefinition(
        "get_machine_health_summary",
        GetMachineHealthSummaryArguments,
        HEALTH_READ_CAPABILITY,
        "machine",
        "machine_id",
    ),
    DiagnosticToolDefinition(
        "get_machine_health_anomalies",
        GetMachineHealthAnomaliesArguments,
        HEALTH_READ_CAPABILITY,
        "machine",
        "machine_id",
    ),
    DiagnosticToolDefinition(
        "search_approved_manuals",
        SearchApprovedManualsArguments,
        MANUAL_READ_CAPABILITY,
        "machine",
        "machine_id",
    ),
    DiagnosticToolDefinition(
        "find_published_repair_playbooks",
        FindPublishedRepairPlaybooksArguments,
        PLAYBOOK_READ_CAPABILITY,
        "machine",
        "machine_id",
    ),
    DiagnosticToolDefinition(
        "get_parts_availability",
        GetPartsAvailabilityArguments,
        PARTS_READ_CAPABILITY,
        "machine",
        "machine_id",
    ),
)

_SAFETY_DEFINITION = DiagnosticToolDefinition(
    LIVE_SAFETY_TOOL_NAME,
    GetLiveSafetyStatusArguments,
    SAFETY_P0_CAPABILITY,
    "repair_packet",
    "repair_packet_id",
)

_TOOL_DESCRIPTIONS = {
    "get_machine_context": "Read current authorized machine context.",
    "get_repair_packet": "Read a current authorized and safety-redacted repair packet.",
    "get_recent_maintenance_history": "Read recent authorized maintenance history.",
    "get_machine_health_summary": (
        "Read a machine's current normalized condition, including data freshness "
        "and quality. Stale telemetry must never be reported as current."
    ),
    "get_machine_health_anomalies": (
        "Read a machine's active anomalies with the rule or alarm that raised "
        "them. Reading cannot raise, escalate or clear a condition."
    ),
    "search_approved_manuals": "Search explicitly approved manuals for an authorized machine.",
    "find_published_repair_playbooks": "Find governed published repair playbooks.",
    "get_parts_availability": "Observe current linked-part availability without reserving stock.",
    LIVE_SAFETY_TOOL_NAME: "Read raw command-side status under the safety-P0 capability.",
}


def _fence_untrusted_content(value: str) -> str:
    """Wrap data while preventing stored text from forging a fence boundary."""
    escaped = value.replace(UNTRUSTED_CONTENT_BEGIN, _ESCAPED_UNTRUSTED_MARKER)
    escaped = escaped.replace(UNTRUSTED_CONTENT_END, _ESCAPED_UNTRUSTED_MARKER)
    return f"{UNTRUSTED_CONTENT_BEGIN}\n{escaped}\n{UNTRUSTED_CONTENT_END}"


class ProductionDiagnosticReader:
    """Lazy adapter to read-only functions owned by the repair domain service."""

    @staticmethod
    def _services():
        from repair import services

        return services

    def rehydrate_actor(self, principal: AIPrincipal) -> object | None:
        """Reload the actor on every invocation."""
        return self._services().diagnostic_rehydrate_actor(principal.user_pk)

    def authorize(
        self,
        *,
        actor: object,
        principal: AIPrincipal,
        context: DiagnosticContext,
        tool_name: str,
        capability: str,
        root: DiagnosticRecordRoot,
        arguments: _StrictArguments,
        check_id: str,
    ) -> ReadAuthorization | None:
        """Delegate the fresh entity ACL check without retrieving result content."""
        del principal, context, tool_name, arguments
        decision = self._services().authorize_diagnostic_read(
            actor=actor,
            capability=capability,
            entity_type=root.entity_type,
            entity_id=root.entity_id,
            expected_revision=root.expected_revision,
            linked_machine_id=root.linked_machine_id,
            check_id=check_id,
        )
        if not decision:
            return None
        try:
            return ReadAuthorization(**decision)
        except (TypeError, ValueError):
            return None

    def read(
        self,
        *,
        actor: object,
        tool_name: str,
        arguments: _StrictArguments,
        authorization: ReadAuthorization,
    ) -> ReaderResult | dict[str, Any]:
        """Dispatch only exact registry names to corresponding domain readers."""
        service_name = {
            "get_machine_context": "read_diagnostic_machine_context",
            "get_repair_packet": "read_diagnostic_repair_packet",
            "get_recent_maintenance_history": "read_diagnostic_maintenance_history",
            "get_machine_health_summary": "read_diagnostic_health_summary",
            "get_machine_health_anomalies": "read_diagnostic_health_anomalies",
            "search_approved_manuals": "read_diagnostic_approved_manuals",
            "find_published_repair_playbooks": "read_diagnostic_published_playbooks",
            "get_parts_availability": "read_diagnostic_parts_availability",
            LIVE_SAFETY_TOOL_NAME: "read_diagnostic_live_safety_status",
        }.get(tool_name)
        if service_name is None:
            raise DiagnosticToolNotFoundError("Diagnostic tool is unavailable")
        reader = getattr(self._services(), service_name)
        return reader(
            actor=actor,
            authorization=authorization,
            **arguments.model_dump(),
        )


class DiagnosticToolRegistry:
    """Exact-name, strict-argument, read-only diagnostic registry."""

    def __init__(
        self,
        *,
        reader: DiagnosticReader | None = None,
        safety_p0_enabled: bool = False,
        max_result_bytes: int = 16 * 1024,
        max_evidence: int = 20,
        clock=None,
    ) -> None:
        if type(safety_p0_enabled) is not bool:
            raise TypeError("The safety-P0 gate must be an explicit boolean")
        if type(max_result_bytes) is not int or not 1024 <= max_result_bytes <= 65536:
            raise ValueError("Diagnostic result bound must be between 1 and 64 KiB")
        if type(max_evidence) is not int or not 1 <= max_evidence <= 50:
            raise ValueError("Diagnostic evidence bound must be between 1 and 50")
        definitions = _BASE_DEFINITIONS + ((_SAFETY_DEFINITION,) if safety_p0_enabled else ())
        self._definitions = definitions
        self._by_name = MappingProxyType({
            definition.name: definition for definition in definitions
        })
        self._reader = reader or ProductionDiagnosticReader()
        self._max_result_bytes = max_result_bytes
        self._max_evidence = max_evidence
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def names(self) -> tuple[str, ...]:
        """Return an immutable deterministic registry snapshot."""
        return tuple(definition.name for definition in self._definitions)

    @property
    def definitions(self) -> tuple[DiagnosticToolDefinition, ...]:
        """Return immutable tool metadata."""
        return self._definitions

    def snapshot(self) -> tuple[str, ...]:
        """Return the exact exposed tool-name snapshot."""
        return self.names

    def provider_tools(self, *, context: DiagnosticContext) -> list[dict[str, Any]]:
        """Build strict provider definitions only for server-authorized root types."""
        if not isinstance(context, DiagnosticContext):
            return []
        if (
            not isinstance(context.principal, AIPrincipal)
            or not isinstance(context.issued_at, datetime)
            or context.issued_at.tzinfo is None
            or context.issued_at.utcoffset() is None
        ):
            return []
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("Diagnostic registry clock must be timezone-aware")
        age = (now.astimezone(UTC) - context.issued_at).total_seconds()
        if age < -5 or age > context.max_age_seconds or context.actor != context.principal.actor:
            return []

        root_types = {root.entity_type for root in context.record_roots}
        result = []
        for definition in self._definitions:
            if (
                definition.capability not in context.capabilities
                or definition.root_type not in root_types
            ):
                continue
            parameters = definition.arguments_model.model_json_schema()
            parameters["additionalProperties"] = False
            result.append({
                "type": "function",
                "name": definition.name,
                "description": _TOOL_DESCRIPTIONS[definition.name],
                "parameters": parameters,
                "strict": True,
            })
        return result

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: DiagnosticContext,
    ) -> dict[str, Any]:
        """Validate, freshly authorize, read, cite, fence, and byte-bound a call."""
        if type(name) is not str or name not in self._by_name:
            raise DiagnosticToolNotFoundError("Diagnostic tool is unavailable")
        definition = self._by_name[name]
        if type(arguments) is not dict:
            raise DiagnosticArgumentsError("Diagnostic tool arguments were invalid")
        try:
            parsed = definition.arguments_model.model_validate(arguments)
        except ValidationError as exc:
            raise DiagnosticArgumentsError("Diagnostic tool arguments were invalid") from exc

        if not isinstance(context, DiagnosticContext):
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)
        if (
            not isinstance(context.principal, AIPrincipal)
            or not isinstance(context.issued_at, datetime)
            or context.issued_at.tzinfo is None
            or context.issued_at.utcoffset() is None
        ):
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise RuntimeError("Diagnostic registry clock must be timezone-aware")
        now = now.astimezone(UTC)
        age = (now - context.issued_at).total_seconds()
        if age < -5 or age > context.max_age_seconds:
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)
        if context.actor != context.principal.actor:
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)
        if definition.capability not in context.capabilities:
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)

        root_id = getattr(parsed, definition.root_argument)
        root = context.root_for(definition.root_type, root_id)
        if root is None or parsed.expected_revision != root.expected_revision:
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)

        actor = self._reader.rehydrate_actor(context.principal)
        if actor is None:
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)
        check_id = str(uuid.uuid4())
        authorization = self._reader.authorize(
            actor=actor,
            principal=context.principal,
            context=context,
            tool_name=name,
            capability=definition.capability,
            root=root,
            arguments=parsed,
            check_id=check_id,
        )
        if not self._valid_authorization(
            authorization,
            context=context,
            definition=definition,
            root=root,
            check_id=check_id,
            now=now,
        ):
            raise DiagnosticAuthorizationError(NON_ENUMERATING_DENIAL)

        raw_result = self._reader.read(
            actor=actor,
            tool_name=name,
            arguments=parsed,
            authorization=authorization,
        )
        result = self._coerce_result(raw_result)
        return self._bounded_result(name, result, authorization, now)

    async def aexecute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        context: DiagnosticContext,
    ) -> dict[str, Any]:
        """Run the synchronous domain facade safely from an async adapter."""
        return await asyncio.to_thread(
            self.execute,
            name,
            arguments,
            context=context,
        )

    @staticmethod
    def _valid_authorization(
        authorization: ReadAuthorization | None,
        *,
        context: DiagnosticContext,
        definition: DiagnosticToolDefinition,
        root: DiagnosticRecordRoot,
        check_id: str,
        now: datetime,
    ) -> bool:
        """Verify every material grant property without exposing which failed."""
        if not isinstance(authorization, ReadAuthorization):
            return False
        checked_at = authorization.checked_at
        if (
            not isinstance(checked_at, datetime)
            or checked_at.tzinfo is None
            or checked_at.utcoffset() is None
        ):
            return False
        checked_age = (now - checked_at.astimezone(UTC)).total_seconds()
        return all((
            authorization.check_id == check_id,
            authorization.actor_id == context.actor,
            authorization.capability == definition.capability,
            authorization.entity_type == root.entity_type,
            authorization.entity_id == root.entity_id,
            authorization.current_revision == root.expected_revision,
            authorization.authorization_class == root.authorization_class,
            authorization.scoped is True,
            authorization.linked_machine_id == root.linked_machine_id,
            -5 <= checked_age <= 5,
        ))

    @staticmethod
    def _coerce_result(raw_result: ReaderResult | dict[str, Any]) -> ReaderResult:
        """Fail closed to abstention if content is not a strict reader result."""
        if isinstance(raw_result, ReaderResult):
            return raw_result
        try:
            return ReaderResult.model_validate(raw_result)
        except ValidationError:
            return ReaderResult(
                abstention_reason="Authorized sources did not provide valid citation-ready evidence."
            )

    def _bounded_result(
        self,
        name: str,
        result: ReaderResult,
        authorization: ReadAuthorization,
        now: datetime,
    ) -> dict[str, Any]:
        """Fence content and enforce evidence-count and serialized-byte limits."""
        if name == LIVE_SAFETY_TOOL_NAME and any(
            not self._valid_safety_claim(item.claim) for item in result.evidence
        ):
            result = ReaderResult(
                abstention_reason=(
                    "Raw command-side status evidence did not satisfy the safety-P0 contract."
                )
            )
        selected = result.evidence[: self._max_evidence]
        truncated = len(result.evidence) > len(selected)
        bounded_abstention = ""
        evidence: list[dict[str, Any]] = []
        for item in selected:
            claim = item.claim
            if item.untrusted:
                claim = _fence_untrusted_content(claim)
            citation = {
                "source_type": item.source_type,
                "id": item.id,
                "revision": item.revision,
                "locator": item.locator,
                "as_of": item.as_of.astimezone(UTC).isoformat(),
                "authorization_class": authorization.authorization_class,
                "claim": claim,
                "content_trust": "untrusted_fenced" if item.untrusted else "trusted_record",
            }
            candidate = self._payload(
                name=name,
                evidence=[*evidence, citation],
                truncated=truncated,
                abstention_reason="",
                now=now,
            )
            if self._json_size(candidate) <= self._max_result_bytes:
                evidence.append(citation)
                continue
            truncated = True
            if name == LIVE_SAFETY_TOOL_NAME:
                bounded_abstention = (
                    "Raw command-side status exceeded the complete bounded result; "
                    "check the authoritative safety surface."
                )
                break
            shortened = self._fit_citation(
                name=name,
                existing=evidence,
                citation=citation,
                now=now,
            )
            if shortened is not None:
                evidence.append(shortened)
            break

        if evidence:
            payload = self._payload(
                name=name,
                evidence=evidence,
                truncated=truncated,
                abstention_reason="",
                now=now,
            )
        else:
            reason = (
                result.abstention_reason
                or bounded_abstention
                or ("No authorized citation-ready evidence was available.")
            )
            payload = self._payload(
                name=name,
                evidence=[],
                truncated=truncated,
                abstention_reason=reason,
                now=now,
            )
        if self._json_size(payload) > self._max_result_bytes:
            raise DiagnosticReaderError("Diagnostic result could not satisfy its byte bound")
        return payload

    @classmethod
    def _valid_safety_claim(cls, claim: str) -> bool:
        """Accept only raw status coverage and reject positive safety inference."""
        if re.search(
            r"\b(?:safe|cleared|approved)\s+(?:to|for)\s+"
            r"(?:operate|operation|proceed|service|work)\b",
            claim,
            re.IGNORECASE,
        ):
            return False
        try:
            value = json.loads(claim)
        except (TypeError, ValueError):
            return False
        if not isinstance(value, dict):
            return False
        required = {
            "packet_status",
            "gate_statuses",
            "lockout_point_statuses",
            "coverage",
            "caveat",
        }
        if set(value) != required or value["packet_status"] not in {
            "draft",
            "diagnosed",
            "approved",
            "executing",
            "closed",
            "canceled",
        }:
            return False
        gate_keys = {"id", "pk", "gate_type", "status"}
        point_keys = {"id", "pk", "gate_id", "energy_source", "status"}
        gates = value["gate_statuses"]
        points = value["lockout_point_statuses"]
        if not isinstance(gates, list) or not isinstance(points, list):
            return False
        if any(
            not isinstance(item, dict)
            or not set(item).issubset(gate_keys)
            or "status" not in item
            or item["status"] not in {"pending", "confirmed", "waived"}
            for item in gates
        ):
            return False
        if any(
            not isinstance(item, dict)
            or not set(item).issubset(point_keys)
            or "status" not in item
            or item["status"] not in {"identified", "isolated", "locked", "verified", "restored"}
            for item in points
        ):
            return False

        coverage = value["coverage"]
        allowed_coverage = {
            "gate_count",
            "lockout_point_count",
            "gate_returned",
            "lockout_point_returned",
            "gate_truncated",
            "lockout_point_truncated",
        }
        if (
            not isinstance(coverage, dict)
            or not {"gate_count", "lockout_point_count"}.issubset(coverage)
            or not set(coverage).issubset(allowed_coverage)
        ):
            return False
        for key, item in coverage.items():
            if key.endswith("_truncated"):
                if type(item) is not bool:
                    return False
            elif type(item) is not int or item < 0:
                return False

        for prefix, items in (("gate", gates), ("lockout_point", points)):
            count = coverage[f"{prefix}_count"]
            returned = coverage.get(f"{prefix}_returned", len(items))
            truncated = coverage.get(f"{prefix}_truncated", False)
            if returned != len(items):
                return False
            if truncated:
                if count <= returned:
                    return False
            elif count != returned:
                return False

        caveat = value["caveat"]
        caveat_tokens = (
            set(re.findall(r"[^\W_]+", caveat.casefold())) if isinstance(caveat, str) else set()
        )
        return {"raw", "verify"}.issubset(
            caveat_tokens
        ) and not cls._contains_positive_safety_inference(value)

    @classmethod
    def _contains_positive_safety_inference(cls, value: Any) -> bool:
        """Find a nested positive assertion under a safety-verdict key."""
        if isinstance(value, dict):
            return any(
                (
                    str(key).strip().lower()
                    in {
                        "approved_for_operation",
                        "cleared",
                        "operationally_safe",
                        "ready_to_operate",
                        "safe",
                        "safety_status",
                    }
                    and (item is True or (isinstance(item, str) and bool(item.strip())))
                )
                or cls._contains_positive_safety_inference(item)
                for key, item in value.items()
            )
        if isinstance(value, (list, tuple)):
            return any(cls._contains_positive_safety_inference(item) for item in value)
        return False

    def _fit_citation(
        self,
        *,
        name: str,
        existing: list[dict[str, Any]],
        citation: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Fit one claim while preserving complete untrusted-content fences."""
        original = citation["claim"]
        fenced = citation["content_trust"] == "untrusted_fenced"
        if fenced:
            prefix = f"{UNTRUSTED_CONTENT_BEGIN}\n"
            suffix = f"\n{UNTRUSTED_CONTENT_END}"
            if not original.startswith(prefix) or not original.endswith(suffix):
                return None
            original = original[len(prefix) : -len(suffix)]

        low, high = 0, len(original)
        best: dict[str, Any] | None = None
        while low <= high:
            midpoint = (low + high) // 2
            content = original[:midpoint]
            if midpoint < len(original):
                content = f"{content}…"
            claim = (
                f"{UNTRUSTED_CONTENT_BEGIN}\n{content}\n{UNTRUSTED_CONTENT_END}"
                if fenced
                else content
            )
            candidate_citation = {**citation, "claim": claim}
            candidate = self._payload(
                name=name,
                evidence=[*existing, candidate_citation],
                truncated=True,
                abstention_reason="",
                now=now,
            )
            if claim and self._json_size(candidate) <= self._max_result_bytes:
                best = candidate_citation
                low = midpoint + 1
            else:
                high = midpoint - 1
        return best

    @staticmethod
    def _payload(
        *,
        name: str,
        evidence: list[dict[str, Any]],
        truncated: bool,
        abstention_reason: str,
        now: datetime,
    ) -> dict[str, Any]:
        status = "ok" if evidence else "abstain"
        return {
            "tool": name,
            "status": status,
            "as_of": now.isoformat(),
            "truncated": truncated,
            "evidence": evidence,
            "abstention_reason": abstention_reason if status == "abstain" else "",
        }

    @staticmethod
    def _json_size(value: dict[str, Any]) -> int:
        return len(
            json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            )
        )


def get_diagnostic_tool_registry(
    *,
    reader: DiagnosticReader | None = None,
    safety_p0_enabled: bool = False,
    max_result_bytes: int = 16 * 1024,
    max_evidence: int = 20,
    clock=None,
) -> DiagnosticToolRegistry:
    """Construct an isolated registry with an explicit safety-P0 exposure gate."""
    return DiagnosticToolRegistry(
        reader=reader,
        safety_p0_enabled=safety_p0_enabled,
        max_result_bytes=max_result_bytes,
        max_evidence=max_evidence,
        clock=clock,
    )


DIAGNOSTIC_TOOL_REGISTRY = get_diagnostic_tool_registry()
DIAGNOSTIC_TOOL_NAMES = DIAGNOSTIC_TOOL_REGISTRY.names

__all__ = [
    "BASE_DIAGNOSTIC_TOOL_NAMES",
    "DIAGNOSTIC_TOOL_NAMES",
    "DIAGNOSTIC_TOOL_REGISTRY",
    "LIVE_SAFETY_TOOL_NAME",
    "MACHINE_READ_CAPABILITY",
    "MAINTENANCE_READ_CAPABILITY",
    "MANUAL_READ_CAPABILITY",
    "NON_ENUMERATING_DENIAL",
    "PACKET_READ_CAPABILITY",
    "PARTS_READ_CAPABILITY",
    "PLAYBOOK_READ_CAPABILITY",
    "SAFETY_P0_CAPABILITY",
    "UNTRUSTED_CONTENT_BEGIN",
    "UNTRUSTED_CONTENT_END",
    "DiagnosticArgumentsError",
    "DiagnosticAuthorizationError",
    "DiagnosticContext",
    "DiagnosticReader",
    "DiagnosticReaderError",
    "DiagnosticRecordRoot",
    "DiagnosticToolDefinition",
    "DiagnosticToolError",
    "DiagnosticToolNotFoundError",
    "DiagnosticToolRegistry",
    "EvidenceClaim",
    "FindPublishedRepairPlaybooksArguments",
    "GetLiveSafetyStatusArguments",
    "GetMachineContextArguments",
    "GetPartsAvailabilityArguments",
    "GetRecentMaintenanceHistoryArguments",
    "GetRepairPacketArguments",
    "ProductionDiagnosticReader",
    "ReadAuthorization",
    "ReaderResult",
    "SearchApprovedManualsArguments",
    "build_diagnostic_context",
    "get_diagnostic_tool_registry",
]
