"""Deterministic complexity routing for normalized voice turns.

This router classifies *how* a final transcript should be handled.  It does
not grant permission, create proposals, select records, or execute effects.
Only fields in :class:`VoiceRoutingContext` are trusted routing inputs;
authority-shaped fields retained on :class:`VoiceRoutingRequest` are
deliberately ignored as untrusted client content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Mapping


class RouteMode(StrEnum):
    """Supported voice complexity routes."""

    FAST_PATH = "fast_path"
    REASONING = "reasoning"
    ADVISORY_INTENT = "advisory_intent"


class ReasoningEffort(StrEnum):
    """Bounded reasoning effort values accepted by downstream runtimes."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RiskLevel(StrEnum):
    """Trusted operational risk attached to a normalized turn."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RouteReason(StrEnum):
    """Safe, enumerable explanations for a routing decision."""

    EFFECT_INTENT = "effect_intent"
    DIAGNOSTIC_INTENT = "diagnostic_intent"
    REPAIR_PLANNING = "repair_planning"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    MANUAL_HISTORY_COMPARISON = "manual_history_comparison"
    LOW_TRANSCRIPTION_CONFIDENCE = "low_transcription_confidence"
    ELEVATED_RISK = "elevated_risk"
    DIAGNOSTIC_EVIDENCE_AVAILABLE = "diagnostic_evidence_available"
    LIMITED_ACTOR_CONTEXT = "limited_actor_context"
    GREETING = "greeting"
    HELP = "help"
    ACKNOWLEDGEMENT = "acknowledgement"
    SIMPLE_LOOKUP = "simple_lookup"
    SIMPLE_FACT = "simple_fact"
    GENERAL_REQUEST = "general_request"


@dataclass(frozen=True, slots=True)
class VoiceRoutingPolicy:
    """Server-owned policy knobs used by complexity routing.

    Policy can make routing more cautious, but it cannot enable proposals or
    action execution.  Tool markers are matched only against the trusted tool
    and capability lists on :class:`VoiceRoutingContext`.
    """

    default_effort: ReasoningEffort = ReasoningEffort.MEDIUM
    low_confidence_threshold: float = 0.70
    elevated_risk_levels: tuple[RiskLevel, ...] = (
        RiskLevel.HIGH,
        RiskLevel.CRITICAL,
    )
    force_reasoning_on_low_confidence: bool = True
    force_reasoning_on_elevated_risk: bool = True
    diagnostic_tool_markers: tuple[str, ...] = (
        "diagnostic",
        "fault",
        "manual",
        "maintenance",
        "repair_history",
        "service_history",
        "telemetry",
        "sensor",
    )
    limited_actor_roles: tuple[str, ...] = ("anonymous", "guest", "viewer")
    unscoped_actor_scopes: tuple[str, ...] = ("",)

    def __post_init__(self) -> None:
        """Normalize and validate immutable policy values."""
        object.__setattr__(self, "default_effort", ReasoningEffort(self.default_effort))
        threshold = float(self.low_confidence_threshold)
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("low_confidence_threshold must be between 0 and 1")
        object.__setattr__(self, "low_confidence_threshold", threshold)
        object.__setattr__(
            self,
            "elevated_risk_levels",
            tuple(RiskLevel(level) for level in self.elevated_risk_levels),
        )
        object.__setattr__(
            self,
            "diagnostic_tool_markers",
            _normalized_tuple(self.diagnostic_tool_markers),
        )
        object.__setattr__(
            self,
            "limited_actor_roles",
            _normalized_tuple(self.limited_actor_roles),
        )
        object.__setattr__(
            self,
            "unscoped_actor_scopes",
            _normalized_tuple(self.unscoped_actor_scopes),
        )


@dataclass(frozen=True, slots=True)
class VoiceRoutingRequest:
    """Final transcript plus non-authoritative client metadata.

    ``workflow_hint``, client identifiers, client capabilities, and values in
    ``untrusted_context`` are retained for boundary compatibility only.  The
    router never reads them when making a decision.
    """

    final_content: str
    workflow_hint: str | None = None
    client_id: str | None = None
    client_capabilities: tuple[str, ...] = ()
    untrusted_context: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate content without interpreting untrusted metadata."""
        if not isinstance(self.final_content, str) or not self.final_content.strip():
            raise ValueError("final_content must be a non-empty string")
        object.__setattr__(self, "client_capabilities", tuple(self.client_capabilities))


@dataclass(frozen=True, slots=True)
class VoiceRoutingContext:
    """Trusted server context which may influence complexity and effort."""

    actor_role: str = "unknown"
    actor_scope: str = "unscoped"
    transcription_confidence: float = 1.0
    risk: RiskLevel = RiskLevel.LOW
    policy: VoiceRoutingPolicy = field(default_factory=VoiceRoutingPolicy)
    allowed_tools: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Normalize trusted scalar and tuple values."""
        confidence = float(self.transcription_confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("transcription_confidence must be between 0 and 1")
        if not isinstance(self.policy, VoiceRoutingPolicy):
            raise TypeError("policy must be a VoiceRoutingPolicy")

        object.__setattr__(self, "actor_role", str(self.actor_role).strip().lower())
        object.__setattr__(self, "actor_scope", str(self.actor_scope).strip().lower())
        object.__setattr__(self, "transcription_confidence", confidence)
        object.__setattr__(self, "risk", RiskLevel(self.risk))
        object.__setattr__(self, "allowed_tools", _normalized_tuple(self.allowed_tools))
        object.__setattr__(
            self,
            "allowed_capabilities",
            _normalized_tuple(self.allowed_capabilities),
        )

    @property
    def risk_level(self) -> RiskLevel:
        """Return the normalized risk under its descriptive alias."""
        return self.risk


@dataclass(frozen=True, slots=True)
class VoiceRouteDecision:
    """Immutable, non-authorizing result of voice complexity routing."""

    mode: RouteMode
    effort: ReasoningEffort
    reason_codes: tuple[RouteReason, ...]
    target_workflow_id: str | None
    proposal_creation_allowed: bool = field(default=False, init=False)
    action_execution_allowed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        """Constrain values to the public enums and stable tuples."""
        object.__setattr__(self, "mode", RouteMode(self.mode))
        object.__setattr__(self, "effort", ReasoningEffort(self.effort))
        object.__setattr__(
            self,
            "reason_codes",
            tuple(RouteReason(reason) for reason in self.reason_codes),
        )
        if self.mode is RouteMode.ADVISORY_INTENT and self.target_workflow_id is not None:
            raise ValueError("advisory intent cannot select an execution workflow")

    @property
    def route_mode(self) -> RouteMode:
        """Return ``mode`` under an explicit compatibility alias."""
        return self.mode

    @property
    def workflow_id(self) -> str | None:
        """Return the suggested read/reasoning workflow, if any."""
        return self.target_workflow_id

    @property
    def advisory_only(self) -> bool:
        """Return whether effect wording was isolated as advisory intent."""
        return self.mode is RouteMode.ADVISORY_INTENT

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe routing record without free-form reasoning."""
        return {
            "mode": self.mode.value,
            "effort": self.effort.value,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "target_workflow_id": self.target_workflow_id,
            "proposal_creation_allowed": self.proposal_creation_allowed,
            "action_execution_allowed": self.action_execution_allowed,
        }


def _normalized_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    """Return lower-cased, de-duplicated non-empty string values."""
    normalized = (str(value).strip().lower() for value in values)
    return tuple(dict.fromkeys(value for value in normalized if value))


class VoiceComplexityRouter:
    """Classify final voice content using deterministic safety precedence."""

    FAST_WORKFLOW_ID: ClassVar[str] = "wf8"
    REASONING_WORKFLOW_ID: ClassVar[str] = "wf1"

    _EFFECT_VERB = (
        r"(?:add|allocate|approve|archive|assign|attach|build|cancel|change|close|"
        r"complete|consume|create|delete|dispatch|edit|email|hold|issue|mark|move|"
        r"open|order|procure|publish|purchase|receive|release|remove|reorder|reserve|"
        r"restore|resume|schedule|send|set|start|submit|transfer|unassign|update|upload)"
    )
    _EFFECT_PATTERNS: ClassVar[tuple[re.Pattern[str], ...]] = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            rf"^\s*(?:(?:please|kindly)\s+)?{_EFFECT_VERB}\b",
            rf"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?{_EFFECT_VERB}\b",
            rf"\b(?:i|we)\s+(?:want|need|would like)\s+(?:you\s+)?to\s+{_EFFECT_VERB}\b",
            rf"\b(?:go ahead and|let(?:'s| us)|we should)\s+{_EFFECT_VERB}\b",
            rf"\b(?:should|may|can)\s+(?:i|we)\s+{_EFFECT_VERB}\b",
            rf"\bhow\s+(?:do|can|should)\s+(?:i|we|you)\s+{_EFFECT_VERB}\b",
            rf"\bhelp\s+me\s+(?:to\s+)?{_EFFECT_VERB}\b",
            rf"\bi\s+{_EFFECT_VERB}\b",
        )
    )
    _NON_EFFECT_PATTERNS: ClassVar[tuple[re.Pattern[str], ...]] = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"^\s*(?:(?:please|kindly)\s+)?update\s+(?:me|us)\s+(?:on|about|with)\b",
            r"^\s*(?:(?:please|kindly)\s+)?complete\s+(?:repair\s+)?(?:list|history|records?|details|information|overview)\b",
            r"^\s*(?:(?:please|kindly)\s+)?start\s+(?:date|time|of)\b",
        )
    )
    _REPAIR_EFFECT_PATTERNS: ClassVar[tuple[re.Pattern[str], ...]] = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"^\s*(?:(?:please|kindly)\s+)?(?:repair|fix)\s+(?!(?:orders?|records?|history|manual|plan|status)\b)",
            r"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:repair|fix)\b",
            r"\b(?:i|we)\s+(?:want|need|would like)\s+(?:you\s+)?to\s+(?:repair|fix)\b",
            r"\b(?:go ahead and|let(?:'s| us))\s+(?:repair|fix)\b",
        )
    )
    _DIAGNOSTIC_PATTERNS: ClassVar[tuple[re.Pattern[str], ...]] = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\bdiagnos(?:e|is|tic|tics)\b",
            r"\btroubleshoot(?:ing)?\b",
            r"\broot causes?\b",
            r"\bpossible causes?\b",
            r"\bwhat (?:could|might|may|would) cause\b",
            r"\bwhy (?:is|does|did|has|would)\b",
            r"\bsymptoms?\b",
            r"\b(?:fault|failure|malfunction|overheat(?:ing)?|vibrat(?:e|es|ing|ion)|leak(?:s|ing)?|intermittent|jammed|stalled)\b",
            r"\b(?:not|isn't|is not|won't|will not|stopped) working\b",
            r"\b(?:abnormal|unusual|grinding|rattling) (?:noise|sound)\b",
        )
    )
    _REPAIR_PLANNING_PATTERNS: ClassVar[tuple[re.Pattern[str], ...]] = tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:draft|develop|outline|prepare)\s+(?:a\s+|the\s+)?repair plan\b",
            r"\bplan\s+(?:a\s+|the\s+)?repair\b",
            r"\bwhat (?:is|should be)\s+(?:a\s+|the\s+)?repair plan\b",
            r"\b(?:steps|procedure|approach|strategy)\s+(?:for|to)\s+(?:the\s+)?(?:repair|fix)\b",
            r"\bhow should (?:i|we|you)\s+(?:repair|fix)\b",
        )
    )
    _CONFLICT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:conflicting|contradictory|inconsistent|discrepancy|doesn't match|does not match)\b",
        re.IGNORECASE,
    )
    _MANUAL_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:manual|documentation|service guide)\b", re.IGNORECASE
    )
    _HISTORY_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\b(?:history|historical|maintenance log|service log|past repairs?|records?)\b",
        re.IGNORECASE,
    )
    _GREETING_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\s*(?:hi|hello|hey|good morning|good afternoon|good evening)[!. ,]*\s*",
        re.IGNORECASE,
    )
    _HELP_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\s*(?:help|help me|what can you do|how can you help)[?.! ]*\s*",
        re.IGNORECASE,
    )
    _ACK_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"\s*(?:ok(?:ay)?|thanks?|thank you|got it|understood|sounds good|acknowledged)[!. ]*\s*",
        re.IGNORECASE,
    )
    _SIMPLE_LOOKUP_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(?:show|list|find|look up|lookup|check|get|where (?:is|are)|how (?:many|much))\b",
        re.IGNORECASE,
    )
    _SIMPLE_FACT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"^\s*(?:what (?:is|are|does)|who (?:is|are)|when (?:is|was|does)|tell me about|define)\b",
        re.IGNORECASE,
    )

    def route(
        self,
        request: VoiceRoutingRequest | str,
        context: VoiceRoutingContext,
    ) -> VoiceRouteDecision:
        """Return a deterministic route for final content and trusted context."""
        if isinstance(request, str):
            request = VoiceRoutingRequest(final_content=request)
        if not isinstance(request, VoiceRoutingRequest):
            raise TypeError("request must be VoiceRoutingRequest or final content")
        if not isinstance(context, VoiceRoutingContext):
            raise TypeError("context must be VoiceRoutingContext")

        content = request.final_content.strip()
        low_confidence = context.transcription_confidence < context.policy.low_confidence_threshold
        elevated_risk = context.risk in context.policy.elevated_risk_levels
        diagnostic_evidence = self._has_diagnostic_evidence(context)
        limited_actor = self._has_limited_actor_context(context)

        # Effect-shaped wording has first precedence.  Even trusted write-like
        # capabilities do not turn this advisory classification into authority.
        if self._is_effect_intent(content):
            reasons = [RouteReason.EFFECT_INTENT]
            self._append_context_reasons(
                reasons,
                low_confidence=low_confidence,
                elevated_risk=elevated_risk,
                diagnostic_evidence=False,
                limited_actor=limited_actor,
            )
            return self._decision(
                RouteMode.ADVISORY_INTENT,
                self._cautious_effort(context, reasons),
                reasons,
                target_workflow_id=None,
            )

        complex_reasons = self._complex_content_reasons(content)
        if complex_reasons:
            self._append_context_reasons(
                complex_reasons,
                low_confidence=low_confidence,
                elevated_risk=elevated_risk,
                diagnostic_evidence=diagnostic_evidence,
                limited_actor=limited_actor,
            )
            return self._decision(
                RouteMode.REASONING,
                self._cautious_effort(context, complex_reasons),
                complex_reasons,
                target_workflow_id=self.REASONING_WORKFLOW_ID,
            )

        # Exact social turns remain fast.  Anchoring avoids treating a greeting
        # followed by a diagnostic request as merely social.
        social_reason = self._social_reason(content)
        if social_reason is not None:
            return self._decision(
                RouteMode.FAST_PATH,
                ReasoningEffort.LOW,
                [social_reason],
                target_workflow_id=self.FAST_WORKFLOW_ID,
            )

        simple_reason = self._simple_reason(content)
        if simple_reason is not None:
            reasons = [simple_reason]
            if low_confidence:
                reasons.append(RouteReason.LOW_TRANSCRIPTION_CONFIDENCE)
            if elevated_risk:
                reasons.append(RouteReason.ELEVATED_RISK)
            effort = (
                ReasoningEffort.HIGH if low_confidence or elevated_risk else ReasoningEffort.LOW
            )
            return self._decision(
                RouteMode.FAST_PATH,
                effort,
                reasons,
                target_workflow_id=self.FAST_WORKFLOW_ID,
            )

        contextual_reasons: list[RouteReason] = []
        if low_confidence and context.policy.force_reasoning_on_low_confidence:
            contextual_reasons.append(RouteReason.LOW_TRANSCRIPTION_CONFIDENCE)
        if elevated_risk and context.policy.force_reasoning_on_elevated_risk:
            contextual_reasons.append(RouteReason.ELEVATED_RISK)
        if contextual_reasons:
            if diagnostic_evidence:
                contextual_reasons.append(RouteReason.DIAGNOSTIC_EVIDENCE_AVAILABLE)
            if limited_actor:
                contextual_reasons.append(RouteReason.LIMITED_ACTOR_CONTEXT)
            return self._decision(
                RouteMode.REASONING,
                ReasoningEffort.HIGH,
                contextual_reasons,
                target_workflow_id=self.REASONING_WORKFLOW_ID,
            )

        return self._decision(
            RouteMode.FAST_PATH,
            context.policy.default_effort,
            [RouteReason.GENERAL_REQUEST],
            target_workflow_id=self.FAST_WORKFLOW_ID,
        )

    def decide(
        self,
        request: VoiceRoutingRequest | str,
        context: VoiceRoutingContext,
    ) -> VoiceRouteDecision:
        """Compatibility alias for :meth:`route`."""
        return self.route(request, context)

    @classmethod
    def _complex_content_reasons(cls, content: str) -> list[RouteReason]:
        """Return stable codes for explicit complex-content signals."""
        reasons: list[RouteReason] = []
        if cls._matches_any(content, cls._DIAGNOSTIC_PATTERNS):
            reasons.append(RouteReason.DIAGNOSTIC_INTENT)
        if cls._matches_any(content, cls._REPAIR_PLANNING_PATTERNS):
            reasons.append(RouteReason.REPAIR_PLANNING)
        if cls._CONFLICT_PATTERN.search(content):
            reasons.append(RouteReason.CONFLICTING_EVIDENCE)
        if cls._MANUAL_PATTERN.search(content) and cls._HISTORY_PATTERN.search(content):
            reasons.append(RouteReason.MANUAL_HISTORY_COMPARISON)
        return reasons

    @classmethod
    def _is_effect_intent(cls, content: str) -> bool:
        """Match effect syntax while excluding common informational phrases."""
        if cls._matches_any(content, cls._NON_EFFECT_PATTERNS):
            return False
        return cls._matches_any(content, cls._EFFECT_PATTERNS) or cls._matches_any(
            content, cls._REPAIR_EFFECT_PATTERNS
        )

    @classmethod
    def _matches_any(cls, content: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
        """Return whether any compiled pattern matches content."""
        return any(pattern.search(content) for pattern in patterns)

    @classmethod
    def _social_reason(cls, content: str) -> RouteReason | None:
        """Classify exact greeting, help, and acknowledgement turns."""
        if cls._GREETING_PATTERN.fullmatch(content):
            return RouteReason.GREETING
        if cls._HELP_PATTERN.fullmatch(content):
            return RouteReason.HELP
        if cls._ACK_PATTERN.fullmatch(content):
            return RouteReason.ACKNOWLEDGEMENT
        return None

    @classmethod
    def _simple_reason(cls, content: str) -> RouteReason | None:
        """Classify explicit read-only lookup and fact forms."""
        if cls._SIMPLE_LOOKUP_PATTERN.search(content):
            return RouteReason.SIMPLE_LOOKUP
        if cls._SIMPLE_FACT_PATTERN.search(content):
            return RouteReason.SIMPLE_FACT
        return None

    @staticmethod
    def _has_diagnostic_evidence(context: VoiceRoutingContext) -> bool:
        """Check trusted tools/capabilities for policy-defined evidence sources."""
        available = (*context.allowed_tools, *context.allowed_capabilities)
        return any(
            marker in item
            for item in available
            for marker in context.policy.diagnostic_tool_markers
        )

    @staticmethod
    def _has_limited_actor_context(context: VoiceRoutingContext) -> bool:
        """Return whether trusted role or scope calls for extra caution."""
        return (
            context.actor_role in context.policy.limited_actor_roles
            or context.actor_scope in context.policy.unscoped_actor_scopes
        )

    @staticmethod
    def _append_context_reasons(
        reasons: list[RouteReason],
        *,
        low_confidence: bool,
        elevated_risk: bool,
        diagnostic_evidence: bool,
        limited_actor: bool,
    ) -> None:
        """Append trusted context codes in stable order without duplication."""
        candidates = (
            (low_confidence, RouteReason.LOW_TRANSCRIPTION_CONFIDENCE),
            (elevated_risk, RouteReason.ELEVATED_RISK),
            (diagnostic_evidence, RouteReason.DIAGNOSTIC_EVIDENCE_AVAILABLE),
            (limited_actor, RouteReason.LIMITED_ACTOR_CONTEXT),
        )
        for include, reason in candidates:
            if include and reason not in reasons:
                reasons.append(reason)

    @staticmethod
    def _cautious_effort(
        context: VoiceRoutingContext, reasons: list[RouteReason]
    ) -> ReasoningEffort:
        """Raise effort when trusted uncertainty, risk, or evidence warrants it."""
        high_effort_reasons = {
            RouteReason.LOW_TRANSCRIPTION_CONFIDENCE,
            RouteReason.ELEVATED_RISK,
            RouteReason.DIAGNOSTIC_EVIDENCE_AVAILABLE,
            RouteReason.LIMITED_ACTOR_CONTEXT,
            RouteReason.CONFLICTING_EVIDENCE,
            RouteReason.MANUAL_HISTORY_COMPARISON,
            RouteReason.REPAIR_PLANNING,
        }
        if any(reason in high_effort_reasons for reason in reasons):
            return ReasoningEffort.HIGH
        return context.policy.default_effort

    @staticmethod
    def _decision(
        mode: RouteMode,
        effort: ReasoningEffort,
        reasons: list[RouteReason],
        *,
        target_workflow_id: str | None,
    ) -> VoiceRouteDecision:
        """Construct a non-authorizing immutable decision."""
        return VoiceRouteDecision(
            mode=mode,
            effort=effort,
            reason_codes=tuple(reasons),
            target_workflow_id=target_workflow_id,
        )


_DEFAULT_ROUTER = VoiceComplexityRouter()


def route_voice_turn(
    request: VoiceRoutingRequest | str,
    context: VoiceRoutingContext,
) -> VoiceRouteDecision:
    """Route one normalized voice turn with the stateless default router."""
    return _DEFAULT_ROUTER.route(request, context)


# Descriptive aliases keep root-service integration terse without shadowing the
# legacy ``RoutingDecision`` in ``agents.routing``.
ComplexityRouteMode = RouteMode
ComplexityRoutingContext = VoiceRoutingContext
ComplexityRoutingDecision = VoiceRouteDecision
ComplexityRoutingPolicy = VoiceRoutingPolicy
ComplexityRoutingRequest = VoiceRoutingRequest


__all__ = [
    "ComplexityRouteMode",
    "ComplexityRoutingContext",
    "ComplexityRoutingDecision",
    "ComplexityRoutingPolicy",
    "ComplexityRoutingRequest",
    "ReasoningEffort",
    "RiskLevel",
    "RouteMode",
    "RouteReason",
    "VoiceComplexityRouter",
    "VoiceRouteDecision",
    "VoiceRoutingContext",
    "VoiceRoutingPolicy",
    "VoiceRoutingRequest",
    "route_voice_turn",
]
