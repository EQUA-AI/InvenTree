"""Risk Radar engine: scan orchestration, finding lifecycle, read model.

The engine owns every finding mutation. Rules are pure evaluators; the
functions here acquire tokened leases, stage candidates, and only after a
complete successful evaluation atomically promote candidates, resolve
absent findings (after grace), supersede older revisions, advance the
watermark, and insert pending notification-delivery intents.

A partial or failed scan can never resolve prior findings (RR-ADR-004).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import (
    Case,
    Count,
    F,
    IntegerField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
    Window,
)
from django.db.models.functions import RowNumber
from django.utils import timezone

from tasks.scope import MaintenanceScope, ScopeError, scope_for_actor

from .risk_models import (
    ACTIVE_FINDING_STATES,
    FINGERPRINT_SCHEMA_VERSION,
    SEVERITY_POLICY_VERSION,
    RiskActionLink,
    RiskFinding,
    RiskFindingEvent,
    RiskFindingState,
    RiskNotificationDelivery,
    RiskRuleConfigurationEvent,
    RiskRuleDefinition,
    RiskScanCandidate,
    RiskScanLease,
    RiskScanRun,
    RiskScanStatus,
)
from .risk_rules import CADENCES, RULE_SPECS, RiskCandidate, RuleSpec
from .risk_scope import (
    RiskScopeError,
    authorized_scope_keys,
    authorized_scopes,
    decode_scope_key,
    encode_scope,
    get_source_adapter,
    risk_service_user,
)

logger = logging.getLogger('inventree')

SUMMARY_VERSION = 1

AUTHORIZATION_POLICY_VERSION = 1

LEASE_SECONDS = 600

QUEUE_LIMIT = 50

RANK_POOL_LIMIT = 500

_RANK_BY_LABEL = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}

_LABEL_BY_RANK = {rank: label for label, rank in _RANK_BY_LABEL.items()}

# Stable error codes for command/scan envelopes (documented in the design
# contract; these strings are API surface and must not change casually).
FINDING_STATE_CONFLICT = 'FINDING_STATE_CONFLICT'
SNOOZE_INVALID = 'SNOOZE_INVALID'
DISMISS_REASON_REQUIRED = 'DISMISS_REASON_REQUIRED'
RULE_DISABLED = 'RULE_DISABLED'
SCAN_LEASE_HELD = 'SCAN_LEASE_HELD'
SCOPE_UNRESOLVED = 'SCOPE_UNRESOLVED'
SUMMARY_STALE = 'SUMMARY_STALE'
IDEMPOTENCY_CONFLICT = 'IDEMPOTENCY_CONFLICT'
ASSIGN_TARGET_NOT_VISIBLE = 'ASSIGN_TARGET_NOT_VISIBLE'
RECHECK_QUEUE_FAILED = 'RECHECK_QUEUE_FAILED'

_CADENCE_SECONDS = {'minutes_15': 15 * 60, 'hourly': 3600, 'daily': 86400}

PERM_VIEW = 'repair.view_riskfinding'
PERM_ACKNOWLEDGE = 'repair.acknowledge_riskfinding'
PERM_ASSIGN = 'repair.assign_riskfinding'
PERM_SNOOZE = 'repair.snooze_riskfinding'
PERM_DISMISS = 'repair.dismiss_riskfinding'
PERM_ADMINISTER = 'repair.administer_riskrules'
PERM_HEALTH = 'repair.view_riskrulehealth'


class RiskCommandError(Exception):
    """A command or scan failure with a stable error code."""

    def __init__(self, code: str, detail: str = ''):
        """Store the stable code and a human-readable detail."""
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


def radar_enabled() -> bool:
    """Return True when the Risk Radar master flag is on."""
    return bool(getattr(settings, 'AIMMS_RISK_RADAR_ENABLED', False))


def command_center_enabled() -> bool:
    """Return True when the Command Center surface flag is on."""
    return bool(getattr(settings, 'AIMMS_COMMAND_CENTER_ENABLED', False))


def notifications_enabled() -> bool:
    """Return True when transition notifications are enabled."""
    return bool(getattr(settings, 'AIMMS_RISK_NOTIFICATIONS_ENABLED', False))


def scan_candidate_cap() -> int:
    """Return the per-scan candidate cap (finding-storm breaker)."""
    return int(getattr(settings, 'AIMMS_RISK_SCAN_UPSERT_CAP', 1000))


def summary_cache_ttl() -> int:
    """Return the summary read-model cache TTL in seconds."""
    return int(getattr(settings, 'AIMMS_RISK_SUMMARY_CACHE_TTL_S', 60))


def _canonical_json(value) -> str:
    """Serialize a value deterministically for hashing."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def compute_fingerprint(
    scope_key: str, rule_code: str, rule_version: int, candidate: RiskCandidate
) -> str:
    """Compute the versioned, globally unique finding fingerprint.

    Includes the fingerprint schema version, scope, rule code, immutable
    rule-definition version, source identity, and condition discriminator —
    never mutable display text (RR-ADR-003, FR-RR-005).
    """
    payload = [
        FINGERPRINT_SCHEMA_VERSION,
        scope_key,
        rule_code,
        rule_version,
        candidate.source_model,
        candidate.source_id,
        list(candidate.fingerprint_parts),
    ]
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def derive_severity(factors: dict, policy: dict | None = None) -> str:
    """Derive a severity label from the documented factor tuple.

    Policy version 1: the rule's base severity, escalated by an explicit
    source criticality when that outranks it. Both the factors and the
    policy version are stored on the finding so the UI can always explain
    a ranking (RR-ADR-005).
    """
    policy = policy or {}
    base = str(factors.get('base') or policy.get('base') or 'medium')
    rank = _RANK_BY_LABEL.get(base, 2)
    criticality = factors.get('criticality')
    if criticality is not None:
        rank = max(rank, _RANK_BY_LABEL.get(str(criticality), 0))
    return _LABEL_BY_RANK[rank]


def _candidate_payload(candidate: RiskCandidate) -> dict:
    """Serialize a candidate DTO for staging."""
    return {
        'fingerprint_parts': list(candidate.fingerprint_parts),
        'source_model': candidate.source_model,
        'source_id': candidate.source_id,
        'title': candidate.title,
        'summary': candidate.summary,
        'severity_factors': candidate.severity_factors,
        'evidence': candidate.evidence,
        'source_as_of': candidate.source_as_of.isoformat(),
        'condition_started_at': candidate.condition_started_at.isoformat(),
        'due_at': candidate.due_at.isoformat() if candidate.due_at else None,
        'action_links': candidate.action_links,
    }


def _parse_dt(value) -> datetime | None:
    """Parse an ISO datetime from a staged payload."""
    if not value:
        return None
    from .risk_rules import normalize_datetime

    return normalize_datetime(datetime.fromisoformat(value))


# ---------------------------------------------------------------------------
# Rule definition provisioning, activation, and enablement
# ---------------------------------------------------------------------------


def ensure_rule_definitions() -> None:
    """Create the version-1 revision for any rule code without one.

    New definitions start disabled; enabling anything requires the audited
    configuration API plus the deployment enablement list (§13).
    """
    existing = set(RiskRuleDefinition.objects.values_list('code', flat=True).distinct())
    for code, spec in RULE_SPECS.items():
        if code in existing:
            continue
        try:
            with transaction.atomic():
                revision = RiskRuleDefinition.objects.create(
                    code=code,
                    version=1,
                    category=spec.category,
                    default_severity_policy={
                        'policy_version': SEVERITY_POLICY_VERSION,
                        'base': spec.severity_base,
                    },
                    schedule=spec.cadence,
                    config=dict(spec.default_config),
                    critical_rule=spec.critical_rule,
                    enabled=False,
                    is_current=True,
                    activation_generation=1,
                )
                RiskRuleConfigurationEvent.objects.create(
                    rule=revision,
                    rule_version=1,
                    actor=None,
                    action='created',
                    before={},
                    after={'config': revision.config, 'enabled': False},
                    reason='Initial rule registration',
                )
        except IntegrityError:  # pragma: no cover - concurrent provisioning
            continue


def current_rule_revision(code: str) -> RiskRuleDefinition | None:
    """Return the current immutable revision for a rule code."""
    return (
        RiskRuleDefinition.objects
        .filter(code=code, is_current=True)
        .order_by('-version')
        .first()
    )


def enabled_rule_codes() -> list[str]:
    """Return the deployment's per-rule enablement list."""
    value = getattr(settings, 'AIMMS_RISK_RULES_ENABLED', [])
    return [str(code) for code in (value or [])]


def rule_enablement(code: str, scope_key: str | None = None) -> tuple[bool, str | None]:
    """Evaluate the rule enablement intersection (§13).

    Returns ``(enabled, first_failed_gate)``. An empty list at any layer
    enables nothing; a disabled source is reported as a failed gate rather
    than a zero-risk result.
    """
    return _rule_enablement(code, scope_key, revision=current_rule_revision(code))


def _rule_enablement(
    code: str, scope_key: str | None, *, revision: RiskRuleDefinition | None
) -> tuple[bool, str | None]:
    """Evaluate enablement with an already loaded current revision."""
    spec = RULE_SPECS.get(code)
    if spec is None:
        return False, 'unknown_rule'
    if not radar_enabled():
        return False, 'master_flag_disabled'
    if revision is None:
        return False, 'no_current_revision'
    if not revision.enabled:
        return False, 'revision_disabled'
    if code not in enabled_rule_codes():
        return False, 'not_in_enabled_rule_codes'
    if scope_key is not None and scope_key not in (revision.enabled_scopes or []):
        return False, 'scope_not_enabled'
    for flag in spec.requires_flags:
        if not bool(getattr(settings, flag, False)):
            return False, f'source_disabled:{flag}'
    if spec.evaluator is None:
        return False, 'dormant'
    return True, None


def update_rule_configuration(
    actor, code: str, *, changes: dict, reason: str
) -> RiskRuleDefinition:
    """Create and activate the next immutable revision of a rule.

    Rule configuration is privileged and audited: disabling or weakening a
    rule can hide operational risk (RR-ADR-010, FR-RR-013).
    """
    if actor is None or not actor.has_perm(PERM_ADMINISTER):
        raise PermissionDenied('Missing required permission: ' + PERM_ADMINISTER)
    if not (reason or '').strip():
        raise RiskCommandError(
            'CONFIG_REASON_REQUIRED', 'A reason is required for rule changes'
        )
    spec = RULE_SPECS.get(code)
    if spec is None:
        raise RiskCommandError(RULE_DISABLED, f'Unknown rule code {code!r}')
    allowed_keys = {
        'config',
        'enabled',
        'enabled_scopes',
        'notification_policy',
        'critical_rule',
    }
    unknown = set(changes) - allowed_keys
    if unknown:
        raise RiskCommandError(
            'CONFIG_INVALID', f'Unsupported configuration keys: {sorted(unknown)}'
        )
    with transaction.atomic():
        revisions = list(
            RiskRuleDefinition.objects.select_for_update().filter(code=code)
        )
        if not revisions:
            raise RiskCommandError(RULE_DISABLED, f'Rule {code!r} is not provisioned')
        current = next((rev for rev in revisions if rev.is_current), None)
        max_version = max(rev.version for rev in revisions)
        max_generation = max(rev.activation_generation for rev in revisions)
        before = {
            'config': current.config if current else {},
            'enabled': current.enabled if current else False,
            'enabled_scopes': current.enabled_scopes if current else [],
            'notification_policy': current.notification_policy if current else {},
            'critical_rule': current.critical_rule if current else False,
        }
        after = dict(before)
        after.update(changes)
        revision = RiskRuleDefinition.objects.create(
            code=code,
            version=max_version + 1,
            category=spec.category,
            default_severity_policy=(
                current.default_severity_policy
                if current
                else {'policy_version': SEVERITY_POLICY_VERSION}
            ),
            schedule=spec.cadence,
            config=after['config'],
            critical_rule=bool(after['critical_rule']),
            notification_policy=after['notification_policy'],
            enabled_scopes=after['enabled_scopes'],
            enabled=bool(after['enabled']),
            is_current=True,
            activation_generation=max_generation + 1,
            created_by=actor,
        )
        if current:
            current.is_current = False
            current.save(update_fields=['is_current'])
            RiskRuleConfigurationEvent.objects.create(
                rule=current,
                rule_version=current.version,
                actor=actor,
                action='superseded',
                before=before,
                after=after,
                reason=reason,
            )
        RiskRuleConfigurationEvent.objects.create(
            rule=revision,
            rule_version=revision.version,
            actor=actor,
            action='activated' if revision.enabled else 'disabled',
            before=before,
            after=after,
            reason=reason,
        )
    return revision


# ---------------------------------------------------------------------------
# Lease + scan execution
# ---------------------------------------------------------------------------


def _acquire_lease(rule_code: str, scope_key: str, owner: str) -> str:
    """Acquire or take over the per-(rule, scope) scan lease.

    Returns the random token persisted on the lease; a still-live foreign
    lease raises ``SCAN_LEASE_HELD``.
    """
    now = timezone.now()
    token = secrets.token_hex(16)
    with transaction.atomic():
        lease = (
            RiskScanLease.objects
            .select_for_update()
            .filter(rule_code=rule_code, scope_key=scope_key)
            .first()
        )
        if lease is None:
            try:
                RiskScanLease.objects.create(
                    rule_code=rule_code,
                    scope_key=scope_key,
                    owner=owner,
                    lease_token=token,
                    expires_at=now + timedelta(seconds=LEASE_SECONDS),
                    heartbeat_at=now,
                )
            except IntegrityError as exc:
                raise RiskCommandError(
                    SCAN_LEASE_HELD, 'Another scan holds this lease'
                ) from exc
            return token
        if lease.expires_at > now:
            raise RiskCommandError(SCAN_LEASE_HELD, 'Another scan holds this lease')
        lease.owner = owner
        lease.lease_token = token
        lease.expires_at = now + timedelta(seconds=LEASE_SECONDS)
        lease.heartbeat_at = now
        lease.save(update_fields=['owner', 'lease_token', 'expires_at', 'heartbeat_at'])
    return token


def _heartbeat_lease(rule_code: str, scope_key: str, token: str) -> bool:
    """Extend the lease if this worker still owns it."""
    now = timezone.now()
    updated = RiskScanLease.objects.filter(
        rule_code=rule_code, scope_key=scope_key, lease_token=token
    ).update(heartbeat_at=now, expires_at=now + timedelta(seconds=LEASE_SECONDS))
    return bool(updated)


def _release_lease(rule_code: str, scope_key: str, token: str) -> None:
    """Release the lease if this worker still owns it."""
    RiskScanLease.objects.filter(
        rule_code=rule_code, scope_key=scope_key, lease_token=token
    ).delete()


def _last_complete_run(rule_code: str, scope_key: str) -> RiskScanRun | None:
    """Return the most recent complete run for a rule and scope."""
    return (
        RiskScanRun.objects
        .filter(
            rule__code=rule_code, scope_key=scope_key, status=RiskScanStatus.COMPLETE
        )
        .order_by('-completed_at')
        .first()
    )


def run_scan_by_key(rule_code: str, scope_key: str) -> int | None:
    """Offload-friendly entrypoint: run one scan by canonical scope key."""
    try:
        scope = decode_scope_key(scope_key)
        run = run_rule_scan(rule_code, scope)
        return run.pk if run else None
    except (RiskCommandError, RiskScopeError) as exc:
        logger.warning('Risk scan %s@%s aborted: %s', rule_code, scope_key, exc)
        return None


def run_rule_scan(
    rule_code: str, scope: MaintenanceScope, *, service_identity=None
) -> RiskScanRun:
    """Run one leased, watermarked scan of a rule against one scope.

    Follows the transaction shape of §5.1: enablement and scope checks up
    front, staged candidates during evaluation, and one short finalization
    transaction that requires the lease token, current revision, and
    activation generation to still match.
    """
    ensure_rule_definitions()
    scope_key = encode_scope(scope)
    enabled, gate = rule_enablement(rule_code, scope_key)
    if not enabled:
        raise RiskCommandError(RULE_DISABLED, f'Rule gate failed: {gate}')
    spec = RULE_SPECS[rule_code]
    revision = current_rule_revision(rule_code)
    adapter = get_source_adapter(spec.source_kind)
    identity = service_identity or risk_service_user()
    try:
        if scope not in scope_for_actor(identity):
            raise RiskCommandError(
                SCOPE_UNRESOLVED, 'Scope is not enumerated for the scanner principal'
            )
    except ScopeError as exc:
        raise RiskCommandError(SCOPE_UNRESOLVED, str(exc)) from exc

    owner = f'{identity.pk}:{secrets.token_hex(4)}'
    token = _acquire_lease(rule_code, scope_key, owner)
    prior = _last_complete_run(rule_code, scope_key)
    run = RiskScanRun.objects.create(
        rule=revision,
        rule_version=revision.version,
        activation_generation=revision.activation_generation,
        scope_key=scope_key,
        service_identity=identity,
        lease_token=token,
        watermark=prior.watermark if prior else {},
        started_at=timezone.now(),
        status=RiskScanStatus.RUNNING,
    )
    try:
        _execute_scan(
            run,
            revision,
            spec,
            adapter=adapter,
            scope=scope,
            scope_key=scope_key,
            rule_code=rule_code,
            identity=identity,
            token=token,
        )
    except Exception as exc:
        status = (
            RiskScanStatus.ABORTED
            if isinstance(exc, RiskCommandError)
            and exc.code in ('SCAN_CANDIDATE_CAP', SCAN_LEASE_HELD)
            else RiskScanStatus.FAILED
        )
        _mark_run_failed(run, revision, status, str(exc))
        _release_lease(rule_code, scope_key, token)
        if isinstance(exc, RiskCommandError):
            raise
        logger.exception('Risk scan %s@%s failed', rule_code, scope_key)
        raise RiskCommandError('SCAN_FAILED', str(exc)) from exc
    _release_lease(rule_code, scope_key, token)
    return run


def _execute_scan(
    run: RiskScanRun,
    revision: RiskRuleDefinition,
    spec: RuleSpec,
    *,
    adapter,
    scope: MaintenanceScope,
    scope_key: str,
    rule_code: str,
    identity,
    token: str,
) -> None:
    """Evaluate, stage, and finalize one leased scan (body of §5.1)."""
    queryset = adapter.queryset_for_scope(actor=identity, scope=scope)
    cap = scan_candidate_cap()
    staged = 0
    completed = False
    final_watermark: dict = {}
    final_as_of = None
    for page in spec.evaluator.evaluate(
        queryset=queryset,
        scope=scope,
        config=revision.config or {},
        watermark=run.watermark or {},
        actor=identity,
    ):
        rows = []
        for candidate in page.candidates:
            fingerprint = compute_fingerprint(
                scope_key, rule_code, revision.version, candidate
            )
            rows.append(
                RiskScanCandidate(
                    run=run,
                    fingerprint=fingerprint,
                    source_as_of=candidate.source_as_of,
                    payload=_candidate_payload(candidate),
                )
            )
        staged += len(rows)
        if staged > cap:
            raise RiskCommandError(
                'SCAN_CANDIDATE_CAP',
                f'Candidate cap {cap} exceeded; run aborted (finding storm)',
            )
        if rows:
            RiskScanCandidate.objects.bulk_create(rows)
        if not _heartbeat_lease(rule_code, scope_key, token):
            raise RiskCommandError(SCAN_LEASE_HELD, 'Lease was taken over mid-scan')
        if page.complete:
            completed = True
            final_watermark = page.next_watermark
            final_as_of = page.source_as_of
    if not completed:
        raise RiskCommandError(
            'SCAN_INCOMPLETE', 'Rule never produced a complete snapshot page'
        )
    _finalize_run(
        run,
        revision,
        spec,
        token=token,
        watermark=final_watermark,
        source_as_of=final_as_of or timezone.now(),
        candidate_total=staged,
    )


def _mark_run_failed(
    run: RiskScanRun, revision: RiskRuleDefinition, status: str, error_summary: str
) -> None:
    """Record a failed/aborted run and discard its staged candidates.

    Prior findings remain untouched; if the rule is marked critical, a
    scan-run transition notification intent is inserted atomically with the
    terminal transition (RR-ADR-007).
    """
    with transaction.atomic():
        run.status = status
        run.completed_at = timezone.now()
        run.error_summary = error_summary[:2000]
        run.save(update_fields=['status', 'completed_at', 'error_summary'])
        run.staged_candidates.all().delete()
        if revision.critical_rule:
            _stage_notification_intents(revision, transition='scan_failed', run=run)
    _bump_scope_epoch(run.scope_key)


def _finalize_run(
    run: RiskScanRun,
    revision: RiskRuleDefinition,
    spec: RuleSpec,
    *,
    token: str,
    watermark: dict,
    source_as_of: datetime,
    candidate_total: int,
) -> None:
    """Atomically promote staged candidates and resolve absent findings."""
    now = timezone.now()
    grace_seconds = float((revision.config or {}).get('resolution_grace_seconds', 0))
    open_min_age_seconds = (
        float((revision.config or {}).get('open_min_age_hours', 0)) * 3600
    )
    with transaction.atomic():
        # Lock the revision before the lease, matching activation's lock
        # order. This closes the check-then-activate window in which an old
        # revision could otherwise promote findings after being superseded.
        revision_ok = (
            RiskRuleDefinition.objects
            .select_for_update()
            .filter(
                pk=revision.pk,
                is_current=True,
                activation_generation=run.activation_generation,
            )
            .first()
            is not None
        )
        lease_ok = (
            RiskScanLease.objects
            .select_for_update()
            .filter(
                rule_code=revision.code,
                scope_key=run.scope_key,
                lease_token=token,
                expires_at__gt=now,
            )
            .first()
            is not None
        )
        run_ok = (
            RiskScanRun.objects
            .select_for_update()
            .filter(
                pk=run.pk,
                rule=revision,
                rule_version=revision.version,
                activation_generation=revision.activation_generation,
                lease_token=token,
                status=RiskScanStatus.RUNNING,
            )
            .first()
            is not None
        )
        if not lease_ok or not revision_ok or not run_ok:
            raise RiskCommandError(
                SCAN_LEASE_HELD,
                'Finalization fenced: run, lease, or rule revision is no longer current',
            )
        candidates = list(run.staged_candidates.all())
        fingerprints = [c.fingerprint for c in candidates]
        existing = {
            finding.fingerprint: finding
            for finding in RiskFinding.objects.select_for_update().filter(
                fingerprint__in=fingerprints
            )
        }
        upserts = 0
        for staged in candidates:
            payload = staged.payload
            severity = derive_severity(
                payload.get('severity_factors', {}),
                revision.default_severity_policy or {},
            )
            finding = existing.get(staged.fingerprint)
            if finding is None:
                # Per-rule opening age gate: conditions younger than the
                # configured episode threshold stay unopened (their
                # fingerprint is still in the candidate set, so nothing is
                # falsely resolved while they mature). This lets rules over
                # churn-prone sources emit every occurrence and keep an
                # already-open episode alive across identity churn.
                if open_min_age_seconds > 0:
                    started = _parse_dt(payload['condition_started_at']) or now
                    if (now - started).total_seconds() < open_min_age_seconds:
                        continue
                finding = RiskFinding.objects.create(
                    fingerprint=staged.fingerprint,
                    fingerprint_version=FINGERPRINT_SCHEMA_VERSION,
                    scope_key=run.scope_key,
                    rule_revision=revision,
                    rule_code=revision.code,
                    rule_version=revision.version,
                    category=revision.category,
                    severity=severity,
                    severity_factors={
                        **payload.get('severity_factors', {}),
                        'policy_version': SEVERITY_POLICY_VERSION,
                    },
                    source_model=payload['source_model'],
                    source_id=payload['source_id'],
                    title=payload['title'][:255],
                    summary=payload.get('summary', ''),
                    evidence=payload.get('evidence', {}),
                    state=RiskFindingState.OPEN,
                    first_seen=now,
                    last_seen=now,
                    condition_started_at=_parse_dt(payload['condition_started_at'])
                    or now,
                    last_seen_run=run,
                    source_as_of=_parse_dt(payload['source_as_of']) or now,
                    due_at=_parse_dt(payload.get('due_at')),
                )
                event = RiskFindingEvent.objects.create(
                    finding=finding,
                    event_type='detected',
                    metadata={'run_id': run.pk, 'severity': severity},
                )
                if severity == 'critical':
                    _stage_notification_intents(
                        revision, transition='new_finding', event=event, finding=finding
                    )
            else:
                previous_severity = finding.severity
                previous_state = finding.state
                condition_started = _parse_dt(payload['condition_started_at']) or now
                finding.last_seen = now
                finding.last_seen_run = run
                finding.source_as_of = _parse_dt(payload['source_as_of']) or now
                finding.title = payload['title'][:255]
                finding.summary = payload.get('summary', '')
                finding.evidence = payload.get('evidence', {})
                finding.severity = severity
                finding.severity_factors = {
                    **payload.get('severity_factors', {}),
                    'policy_version': SEVERITY_POLICY_VERSION,
                }
                finding.due_at = _parse_dt(payload.get('due_at'))
                reopening = previous_state == RiskFindingState.RESOLVED or (
                    previous_state == RiskFindingState.DISMISSED
                    and finding.dismiss_recheck_at is not None
                    and now >= finding.dismiss_recheck_at
                )
                if reopening:
                    # Recurrence after resolution is a NEW episode: the
                    # condition clock resets to the candidate's start.
                    finding.condition_started_at = condition_started
                else:
                    # Continuously active: preserve the original episode
                    # start across source-row churn (min, never later).
                    finding.condition_started_at = min(
                        finding.condition_started_at, condition_started
                    )
                event = None
                if reopening:
                    finding.state = RiskFindingState.OPEN
                    finding.reopen_count += 1
                    finding.snooze_until = None
                    finding.dismiss_recheck_at = None
                    event = RiskFindingEvent.objects.create(
                        finding=finding,
                        event_type='reopened',
                        metadata={'run_id': run.pk, 'previous_state': previous_state},
                    )
                    _stage_notification_intents(
                        revision, transition='reopened', event=event, finding=finding
                    )
                elif (
                    previous_state == RiskFindingState.SNOOZED
                    and finding.snooze_until is not None
                    and now >= finding.snooze_until
                ):
                    finding.state = RiskFindingState.OPEN
                    finding.snooze_until = None
                    event = RiskFindingEvent.objects.create(
                        finding=finding,
                        event_type='changed',
                        metadata={'run_id': run.pk, 'change': 'snooze_expired'},
                    )
                severity_increased = _RANK_BY_LABEL.get(
                    severity, 0
                ) > _RANK_BY_LABEL.get(previous_severity, 0)
                if severity_increased:
                    event = RiskFindingEvent.objects.create(
                        finding=finding,
                        event_type='changed',
                        metadata={
                            'run_id': run.pk,
                            'change': 'severity_increase',
                            'from': previous_severity,
                            'to': severity,
                        },
                    )
                    _stage_notification_intents(
                        revision,
                        transition='severity_increase',
                        event=event,
                        finding=finding,
                    )
                finding.version += 1
                finding.save()
            finding.action_links.all().delete()
            links = [
                RiskActionLink(
                    finding=finding,
                    label=link['label'][:128],
                    target_kind=link['target_kind'][:32],
                    target_id=str(link['target_id'])[:64],
                    route=link['route'][:255],
                )
                for link in payload.get('action_links', [])
            ]
            if links:
                RiskActionLink.objects.bulk_create(links, ignore_conflicts=True)
            upserts += 1

        # Complete-scan-only resolution (RR-ADR-004): the full snapshot has
        # been observed, so any still-active finding of this revision whose
        # fingerprint is absent has cleared at the source. Grace defers the
        # resolution of recently-seen findings to damp flapping.
        resolve_count = 0
        absent = (
            RiskFinding.objects
            .select_for_update()
            .filter(
                rule_code=revision.code,
                rule_version=revision.version,
                scope_key=run.scope_key,
            )
            .exclude(fingerprint__in=fingerprints)
            .exclude(state=RiskFindingState.RESOLVED)
        )
        for finding in absent:
            if grace_seconds > 0 and (
                (now - finding.last_seen).total_seconds() < grace_seconds
            ):
                continue
            # A dismissal carries an expiring recheck policy: absence does
            # not resolve it until the recheck window has passed, so a
            # flapping condition cannot launder itself out of dismissal.
            if (
                finding.state == RiskFindingState.DISMISSED
                and finding.dismiss_recheck_at is not None
                and now < finding.dismiss_recheck_at
            ):
                continue
            finding.state = RiskFindingState.RESOLVED
            finding.version += 1
            finding.save(update_fields=['state', 'version'])
            RiskFindingEvent.objects.create(
                finding=finding,
                event_type='resolved',
                reason='Source condition cleared on complete scan',
                metadata={'run_id': run.pk},
            )
            resolve_count += 1

        # First complete scan of a new revision atomically supersedes
        # active older-revision findings, avoiding a visibility gap while
        # preserving the exact policy behind history.
        superseded = RiskFinding.objects.select_for_update().filter(
            rule_code=revision.code,
            scope_key=run.scope_key,
            rule_version__lt=revision.version,
            state__in=[state.value for state in ACTIVE_FINDING_STATES]
            + [RiskFindingState.DISMISSED.value],
        )
        for finding in superseded:
            finding.state = RiskFindingState.RESOLVED
            finding.version += 1
            finding.save(update_fields=['state', 'version'])
            RiskFindingEvent.objects.create(
                finding=finding,
                event_type='superseded',
                reason=f'Superseded by rule revision v{revision.version}',
                metadata={'run_id': run.pk},
            )

        run.staged_candidates.all().delete()
        run.status = RiskScanStatus.COMPLETE
        run.completed_at = now
        run.watermark = watermark
        run.candidate_count = candidate_total
        run.upsert_count = upserts
        run.resolve_count = resolve_count
        run.save(
            update_fields=[
                'status',
                'completed_at',
                'watermark',
                'candidate_count',
                'upsert_count',
                'resolve_count',
            ]
        )
    _bump_scope_epoch(run.scope_key)


def dispatch_scans(cadence: str) -> int:
    """Fan out leased per-rule/scope scans for one cadence class.

    Fails closed: an unset or invalid scanner principal, or an unresolved
    scope enumeration, dispatches nothing (and rule health shows why).
    """
    if cadence not in CADENCES:
        raise ValueError(f'Unknown cadence {cadence!r}')
    if not radar_enabled():
        return 0
    ensure_rule_definitions()
    try:
        identity = risk_service_user()
        scopes = scope_for_actor(identity)
    except (RiskScopeError, ScopeError) as exc:
        logger.warning('Risk scan dispatch skipped: %s', exc)
        return 0
    from InvenTree.tasks import offload_task

    dispatched = 0
    for code, spec in RULE_SPECS.items():
        if spec.cadence != cadence:
            continue
        for scope in sorted(
            scopes, key=lambda s: (s.customer_id or 0, s.site_key or '')
        ):
            try:
                scope_key = encode_scope(scope)
            except RiskScopeError:
                continue
            enabled, _gate = rule_enablement(code, scope_key)
            if not enabled:
                continue
            offload_task(
                'repair.risk_services.run_scan_by_key',
                code,
                scope_key,
                group='risk-radar',
            )
            dispatched += 1
    return dispatched


# ---------------------------------------------------------------------------
# Visibility policy and finding queries
# ---------------------------------------------------------------------------


class RiskFindingVisibilityPolicy:
    """Server-side visibility policy applied after scope resolution.

    Version 1 grants every category to holders of ``view_riskfinding``;
    the policy object exists so category/ownership grants can tighten
    without touching call sites, and its version participates in the
    authorization fingerprint (FR-RR-009/014).
    """

    VERSION = 1

    def visible_categories(self, actor) -> set[str] | None:
        """Return visible categories for an actor (None means all)."""
        if actor is None or not actor.has_perm(PERM_VIEW):
            return set()
        return None

    def filter_findings(self, actor, queryset):
        """Apply category visibility to a findings queryset."""
        categories = self.visible_categories(actor)
        if categories is None:
            return queryset
        return queryset.filter(category__in=sorted(categories))

    def can_view(self, actor, finding: RiskFinding) -> bool:
        """Return True when the actor may see this finding."""
        categories = self.visible_categories(actor)
        return categories is None or finding.category in categories


VISIBILITY_POLICY = RiskFindingVisibilityPolicy()


def require_view_permission(actor) -> None:
    """Require the base finding view permission."""
    if actor is None or not actor.has_perm(PERM_VIEW):
        raise PermissionDenied('Missing required permission: ' + PERM_VIEW)


def visible_findings(actor, scope: MaintenanceScope):
    """Return the scope's findings the actor may see (FR-RR-014)."""
    require_view_permission(actor)
    scope_key = encode_scope(scope)
    queryset = (
        RiskFinding.objects
        .filter(scope_key=scope_key)
        .select_related('owner')
        .prefetch_related('action_links')
    )
    return VISIBILITY_POLICY.filter_findings(actor, queryset)


def attention_filter(now: datetime) -> Q:
    """Findings that currently demand attention in queues and counts.

    Open and acknowledged findings always; snoozed findings only once the
    snooze has expired (they return to the queue visibly).
    """
    return Q(state__in=[RiskFindingState.OPEN, RiskFindingState.ACKNOWLEDGED]) | Q(
        state=RiskFindingState.SNOOZED, snooze_until__lte=now
    )


def rank_key(finding: RiskFinding, now: datetime) -> tuple:
    """Return the documented lexicographic ranking tuple (§6).

    Severity descending, due-breach first, configured criticality tier
    descending, age descending, then primary key ascending as the
    deterministic tie-breaker.
    """
    severity_rank = _RANK_BY_LABEL.get(finding.severity, 0)
    due_breached = bool(finding.due_at and finding.due_at <= now)
    criticality = _RANK_BY_LABEL.get(
        str((finding.severity_factors or {}).get('criticality', '')), 0
    )
    age_seconds = max((now - finding.condition_started_at).total_seconds(), 0)
    return (-severity_rank, not due_breached, -criticality, -age_seconds, finding.pk)


def rank_ordered(queryset, now: datetime):
    """Order a findings queryset by the DB-computable rank-tuple prefix.

    Severity and due-breach (the two leading tuple components) plus
    condition age are annotated so slicing a bounded pool can never drop a
    higher-severity finding in favor of a lower one; the JSON-held
    criticality tie-break is applied afterwards in Python via
    :func:`rank_key` and only reorders rows within an equal
    (severity, due-breach) stratum.
    """
    severity_rank = Case(
        *[
            When(severity=label, then=Value(rank))
            for label, rank in _RANK_BY_LABEL.items()
        ],
        default=Value(0),
        output_field=IntegerField(),
    )
    criticality_rank = Case(
        *[
            When(severity_factors__criticality=label, then=Value(rank))
            for label, rank in _RANK_BY_LABEL.items()
        ],
        default=Value(0),
        output_field=IntegerField(),
    )
    due_rank = Case(
        When(due_at__isnull=False, due_at__lte=now, then=Value(1)),
        default=Value(0),
        output_field=IntegerField(),
    )
    return queryset.annotate(
        _severity_rank=severity_rank,
        _due_rank=due_rank,
        _criticality_rank=criticality_rank,
    ).order_by(
        '-_severity_rank',
        '-_due_rank',
        '-_criticality_rank',
        'condition_started_at',
        'pk',
    )


def ranked_pool(queryset, now: datetime, limit: int = RANK_POOL_LIMIT):
    """Materialize the top-ranked bounded pool, fully tuple-sorted."""
    pool = list(rank_ordered(queryset, now)[:limit])
    pool.sort(key=lambda finding: rank_key(finding, now))
    return pool


def ranked_findings(actor, scope: MaintenanceScope, limit: int = QUEUE_LIMIT):
    """Return the ranked attention queue for one authorized scope."""
    now = timezone.now()
    queryset = visible_findings(actor, scope).filter(attention_filter(now))
    return ranked_pool(queryset, now)[:limit]


# ---------------------------------------------------------------------------
# Finding lifecycle commands
# ---------------------------------------------------------------------------

_COMMAND_PERMS = {
    'acknowledge': PERM_ACKNOWLEDGE,
    'assign': PERM_ASSIGN,
    'snooze': PERM_SNOOZE,
    'dismiss': PERM_DISMISS,
}


def execute_finding_command(
    actor,
    finding_id: int,
    command: str,
    *,
    expected_version: int,
    idempotency_key: str,
    arguments: dict | None = None,
) -> dict:
    """Execute one ownership command on a finding (FR-RR-006/007).

    Commands are permissioned, optimistically versioned, idempotent, and
    recorded as immutable events. None of them touches the source system.
    """
    arguments = arguments or {}
    permission = _COMMAND_PERMS.get(command)
    if permission is None:
        raise RiskCommandError('COMMAND_INVALID', f'Unknown command {command!r}')
    require_view_permission(actor)
    if not actor.has_perm(permission):
        raise PermissionDenied('Missing required permission: ' + permission)
    request_hash = hashlib.sha256(
        _canonical_json([command, arguments]).encode()
    ).hexdigest()
    now = timezone.now()
    with transaction.atomic():
        finding = RiskFinding.objects.select_for_update().filter(pk=finding_id).first()
        if finding is None:
            raise RiskCommandError('FINDING_NOT_FOUND', 'Finding does not exist')
        scope = decode_scope_key(finding.scope_key)
        if scope not in authorized_scopes(actor):
            raise RiskScopeError('Finding scope is not authorized for this actor')
        if not VISIBILITY_POLICY.can_view(actor, finding):
            raise RiskScopeError('Finding category is not visible to this actor')
        replay = RiskFindingEvent.objects.filter(
            finding=finding, idempotency_key=idempotency_key
        ).first()
        if replay is not None:
            if replay.request_hash != request_hash:
                raise RiskCommandError(
                    IDEMPOTENCY_CONFLICT,
                    'Idempotency key was already used with a different request',
                )
            return _command_result(finding, replay, replayed=True)
        if finding.version != int(expected_version):
            raise RiskCommandError(
                FINDING_STATE_CONFLICT,
                f'Expected version {expected_version}, current {finding.version}',
            )
        if not finding.is_active:
            raise RiskCommandError(
                FINDING_STATE_CONFLICT,
                f'Command {command!r} is not valid in state {finding.state!r}',
            )
        metadata: dict = {}
        reason = str(arguments.get('reason', '') or '')
        if command == 'acknowledge':
            finding.state = RiskFindingState.ACKNOWLEDGED
        elif command == 'assign':
            owner = _validated_assignee(arguments.get('owner_id'), finding)
            finding.owner = owner
            metadata['owner_id'] = owner.pk if owner else None
        elif command == 'snooze':
            snooze_until = _parse_dt(arguments.get('snooze_until'))
            if snooze_until is None or snooze_until <= now:
                raise RiskCommandError(
                    SNOOZE_INVALID, 'snooze_until must be a future timestamp'
                )
            finding.state = RiskFindingState.SNOOZED
            finding.snooze_until = snooze_until
            metadata['snooze_until'] = snooze_until.isoformat()
        elif command == 'dismiss':
            if not reason.strip():
                raise RiskCommandError(
                    DISMISS_REASON_REQUIRED, 'Dismissal requires a reason'
                )
            recheck_hours = float(
                arguments.get('recheck_hours')
                or (
                    (
                        current_rule_revision(finding.rule_code)
                        or finding.rule_revision
                    ).config
                    or {}
                ).get('dismiss_recheck_hours', 168)
            )
            finding.state = RiskFindingState.DISMISSED
            finding.dismiss_recheck_at = now + timedelta(hours=recheck_hours)
            metadata['dismiss_recheck_at'] = finding.dismiss_recheck_at.isoformat()
        finding.version += 1
        finding.save()
        event_type = {
            'acknowledge': 'acknowledged',
            'assign': 'assigned',
            'snooze': 'snoozed',
            'dismiss': 'dismissed',
        }[command]
        event = RiskFindingEvent.objects.create(
            finding=finding,
            event_type=event_type,
            actor=actor,
            reason=reason,
            metadata=metadata,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if command == 'assign' and finding.owner is not None:
            _stage_notification_intents(
                finding.rule_revision,
                transition='assigned',
                event=event,
                finding=finding,
                extra_recipients=[finding.owner],
            )
    _bump_scope_epoch(finding.scope_key)
    return _command_result(finding, event, replayed=False)


def _validated_assignee(owner_id, finding: RiskFinding):
    """Resolve and validate an assignment target.

    Assignment rejects a target user who cannot view that finding's scope
    and category, preventing assignment notices from becoming a disclosure
    channel (§2.3).
    """
    if owner_id in (None, ''):
        return None
    target = get_user_model().objects.filter(pk=int(owner_id), is_active=True).first()
    if target is None:
        raise RiskCommandError(
            ASSIGN_TARGET_NOT_VISIBLE, 'Assignment target does not exist'
        )
    if not target.has_perm(PERM_VIEW) or not VISIBILITY_POLICY.can_view(
        target, finding
    ):
        raise RiskCommandError(
            ASSIGN_TARGET_NOT_VISIBLE, 'Assignment target cannot view this finding'
        )
    try:
        if decode_scope_key(finding.scope_key) not in scope_for_actor(target):
            raise RiskCommandError(
                ASSIGN_TARGET_NOT_VISIBLE, 'Assignment target does not hold this scope'
            )
    except ScopeError as exc:
        raise RiskCommandError(
            ASSIGN_TARGET_NOT_VISIBLE, 'Assignment target does not hold this scope'
        ) from exc
    return target


def _command_result(finding: RiskFinding, event: RiskFindingEvent, *, replayed: bool):
    """Build the command response DTO."""
    return {
        'finding_id': finding.pk,
        'state': finding.state,
        'version': finding.version,
        'owner_id': finding.owner_id,
        'event_id': event.pk,
        'event_type': event.event_type,
        'replayed': replayed,
    }


def enqueue_recheck(
    actor, finding: RiskFinding, *, expected_version: int, idempotency_key: str
) -> bool:
    """Enqueue the complete current rule+scope full-snapshot scan.

    A recheck never resolves from a targeted or delta absence — it simply
    runs the same complete scan sooner (FR-RR-003). The request is recorded
    before queuing so retries cannot fan out duplicate scans.

    Returns ``True`` for an idempotent replay and ``False`` for a newly
    accepted request.
    """
    require_view_permission(actor)
    request_hash = hashlib.sha256(_canonical_json(['recheck', {}]).encode()).hexdigest()
    with transaction.atomic():
        finding = RiskFinding.objects.select_for_update().filter(pk=finding.pk).first()
        if finding is None:
            raise RiskCommandError('FINDING_NOT_FOUND', 'Finding does not exist')
        scope = decode_scope_key(finding.scope_key)
        if scope not in authorized_scopes(actor):
            raise RiskScopeError('Finding scope is not authorized for this actor')
        if not VISIBILITY_POLICY.can_view(actor, finding):
            raise RiskScopeError('Finding category is not visible to this actor')
        replay = RiskFindingEvent.objects.filter(
            finding=finding, idempotency_key=idempotency_key
        ).first()
        if replay is not None:
            if replay.request_hash != request_hash:
                raise RiskCommandError(
                    IDEMPOTENCY_CONFLICT,
                    'Idempotency key was already used with a different request',
                )
            return True
        if finding.version != int(expected_version):
            raise RiskCommandError(
                FINDING_STATE_CONFLICT,
                f'Expected version {expected_version}, current {finding.version}',
            )
        enabled, gate = rule_enablement(finding.rule_code, finding.scope_key)
        if not enabled:
            raise RiskCommandError(RULE_DISABLED, f'Rule gate failed: {gate}')
        RiskFindingEvent.objects.create(
            finding=finding,
            event_type='recheck_requested',
            actor=actor,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        # Queue while the request ledger transaction is still open. An
        # immediate queue-adapter failure rolls the ledger row back, allowing
        # the caller to retry instead of replaying a request that was never
        # actually queued.
        from InvenTree.tasks import offload_task

        queued = offload_task(
            'repair.risk_services.run_scan_by_key',
            finding.rule_code,
            finding.scope_key,
            group='risk-radar',
        )
        if queued is False:
            raise RiskCommandError(
                RECHECK_QUEUE_FAILED,
                'The recheck could not be queued; retry the command',
            )
    return False


# ---------------------------------------------------------------------------
# Read model: authorization fingerprint, cache, summary, health
# ---------------------------------------------------------------------------


def authorization_fingerprint(actor) -> str:
    """Compute the server-side authorization fingerprint (FR-RR-009).

    Hashes actor id, effective Django permissions, sorted scope
    memberships, and the authorization/visibility policy versions. Cached
    views keyed by it can never survive an authorization change.
    """
    try:
        scope_keys = authorized_scope_keys(actor)
    except RiskScopeError:
        scope_keys = []
    payload = [
        actor.pk if actor else None,
        sorted(actor.get_all_permissions()) if actor else [],
        scope_keys,
        RiskFindingVisibilityPolicy.VERSION,
        AUTHORIZATION_POLICY_VERSION,
        SUMMARY_VERSION,
    ]
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()[:32]


def _epoch_key(scope_key: str) -> str:
    """Cache key holding the invalidation epoch for one scope."""
    return f'risk-radar:epoch:{scope_key}'


def _bump_scope_epoch(scope_key: str) -> None:
    """Invalidate cached read models for one scope."""
    key = _epoch_key(scope_key)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=None)


def _scope_epoch(scope_key: str) -> int:
    """Return the current cache epoch for one scope."""
    return int(cache.get(_epoch_key(scope_key)) or 0)


def _cadence_seconds(cadence: str) -> int:
    """Return the nominal cadence interval in seconds."""
    return _CADENCE_SECONDS.get(cadence, 3600)


def rule_freshness(scope_key: str, now: datetime | None = None) -> list[dict]:
    """Per-rule freshness with degraded and source-disabled flags.

    No successful scan older than twice its cadence may pass without a
    visible degradation flag (NFR-RR-003); a disabled source is reported
    as unavailable, never as a zero-risk result.
    """
    rows, _revisions = _rule_freshness_rows(scope_key, now or timezone.now())
    return rows


def _rule_freshness_rows(
    scope_key: str, now: datetime
) -> tuple[list[dict], dict[str, RiskRuleDefinition]]:
    """Build freshness rows with one portable query for all rule codes."""
    latest_run = RiskScanRun.objects.filter(
        rule__code=OuterRef('code'), scope_key=scope_key
    ).order_by('-started_at')
    latest_complete = RiskScanRun.objects.filter(
        rule__code=OuterRef('code'), scope_key=scope_key, status=RiskScanStatus.COMPLETE
    ).order_by('-completed_at')
    revisions = {
        revision.code: revision
        for revision in RiskRuleDefinition.objects.filter(
            code__in=RULE_SPECS, is_current=True
        ).annotate(
            risk_last_complete=Subquery(latest_complete.values('completed_at')[:1]),
            risk_last_status=Subquery(latest_run.values('status')[:1]),
        )
    }
    rows = []
    for code, spec in RULE_SPECS.items():
        revision = revisions.get(code)
        enabled, gate = _rule_enablement(code, scope_key, revision=revision)
        last_complete = (
            getattr(revision, 'risk_last_complete', None) if revision else None
        )
        last_status = getattr(revision, 'risk_last_status', None) if revision else None
        stale = last_complete is None or (
            (now - last_complete).total_seconds() > 2 * _cadence_seconds(spec.cadence)
        )
        source_disabled = bool(gate and gate.startswith('source_disabled'))
        rows.append({
            'rule': code,
            'enabled': enabled,
            'gate': gate,
            'last_complete': last_complete.isoformat() if last_complete else None,
            'last_status': last_status,
            'degraded': enabled
            and (
                stale or last_status in (RiskScanStatus.FAILED, RiskScanStatus.ABORTED)
            ),
            'source_disabled': source_disabled,
            'dormant': gate == 'dormant',
        })
    return rows, revisions


def finding_actions_available(
    finding: RiskFinding, now: datetime | None = None
) -> bool:
    """Return True only when the finding's current rule data is actionable."""
    enabled, _gate = rule_enablement(finding.rule_code, finding.scope_key)
    spec = RULE_SPECS.get(finding.rule_code)
    if not enabled or spec is None:
        return False
    latest = (
        RiskScanRun.objects
        .filter(rule__code=finding.rule_code, scope_key=finding.scope_key)
        .order_by('-started_at')
        .only('status', 'completed_at')
        .first()
    )
    if (
        latest is None
        or latest.status != RiskScanStatus.COMPLETE
        or latest.completed_at is None
    ):
        return False
    now = now or timezone.now()
    return (now - latest.completed_at).total_seconds() <= 2 * _cadence_seconds(
        spec.cadence
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    """Return a simple nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return round(ordered[index], 1)


def command_center_summary(actor, scope: MaintenanceScope) -> dict:
    """Compose the one-scope Command Center read model (§6, FR-RR-008).

    The short-TTL cache is a read projection only, keyed by
    ``(scope, authorization fingerprint, summary version, epoch)`` — never
    cached globally then filtered client-side (RR-ADR-006).
    """
    require_view_permission(actor)
    scope_key = encode_scope(scope)
    fingerprint = authorization_fingerprint(actor)
    epoch = _scope_epoch(scope_key)
    cache_key = (
        f'risk-radar:summary:{scope_key}:{fingerprint}:{SUMMARY_VERSION}:{epoch}'
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    now = timezone.now()
    attention = visible_findings(actor, scope).filter(attention_filter(now))
    # Exact aggregates come from the database, never from a bounded pool.
    headline = {'critical': 0, 'high': 0, 'medium': 0, 'low': 0}
    for row in attention.values('severity').annotate(count=Count('pk')):
        headline[row['severity']] = row['count']
    by_category = {
        row['category']: row['count']
        for row in attention.values('category').annotate(count=Count('pk'))
    }
    findings = ranked_pool(attention, now)
    queue = [
        {
            'finding_id': finding.pk,
            'severity': finding.severity,
            'category': finding.category,
            'rule': finding.rule_code,
            'title': finding.title,
            'state': finding.state,
            'age_hours': round(
                max((now - finding.condition_started_at).total_seconds(), 0) / 3600, 1
            ),
            'due_breached': bool(finding.due_at and finding.due_at <= now),
            'source_as_of': finding.source_as_of.isoformat(),
        }
        for finding in findings[:QUEUE_LIMIT]
    ]
    freshness = rule_freshness(scope_key, now)
    enabled_freshness = [row for row in freshness if row['enabled']]
    stale = not enabled_freshness or any(row['degraded'] for row in enabled_freshness)

    summary = {
        'as_of': now.isoformat(),
        'scope': scope_key,
        'stale': stale,
        'freshness': freshness,
        'source_freshness': source_freshness(now),
        'headline': headline,
        'by_category': by_category,
        'queue': queue,
        'flow': _flow_counts(actor, scope, now),
        'aging': _aging(actor, scope, now),
        'return_to_service': []
        if stale
        else [
            {
                'finding_id': finding.pk,
                'packet': (finding.evidence or {}).get('packet_id'),
                'code': finding.rule_code,
                'reason_snapshot': finding.summary,
                'source_as_of': finding.source_as_of.isoformat(),
            }
            for finding in ranked_pool(
                attention.filter(rule_code='WO_BLOCKED_SAFETY'), now, limit=20
            )
        ],
    }
    cache.set(cache_key, summary, timeout=summary_cache_ttl())
    return summary


def source_freshness(now: datetime | None = None) -> list[dict]:
    """Per-source availability derived from the gating feature flags."""
    now = now or timezone.now()
    return [
        {
            'source': 'work_orders',
            'as_of': now.isoformat(),
            'degraded': not bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False)),
        },
        {
            'source': 'job_kits',
            'as_of': now.isoformat(),
            'degraded': not bool(getattr(settings, 'AIMMS_JOB_KITS_ENABLED', False)),
        },
    ]


def _flow_counts(actor, scope: MaintenanceScope, now: datetime) -> dict:
    """Authoritative flow counts via the scoped source adapters.

    Flow cannot be inferred from findings; the summary queries the same
    scope-proven querysets the rules use, under the viewer's identity.
    """
    flow: dict = {'packets': {}, 'work_orders': {}}
    packets = get_source_adapter('repair_packet').queryset_for_scope(
        actor=actor, scope=scope
    )
    for row in packets.values('status').annotate(count=Count('pk')):
        flow['packets'][row['status']] = row['count']
    closed_cutoff = now - timedelta(days=7)
    flow['packets']['closed_7d'] = packets.filter(
        status='closed', updated_at__gte=closed_cutoff
    ).count()
    if bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False)):
        work_orders = get_source_adapter('work_order').queryset_for_scope(
            actor=actor, scope=scope
        )
        for row in work_orders.values('lifecycle_status').annotate(count=Count('pk')):
            flow['work_orders'][row['lifecycle_status']] = row['count']
    else:
        flow['work_orders'] = {'source_disabled': True}
    return flow


def _aging(actor, scope: MaintenanceScope, now: datetime) -> dict:
    """Aging aggregates from the authoritative sources (not findings)."""
    from approvals.models import ApprovalStatus

    aging: dict = {}
    approvals = (
        get_source_adapter('approval')
        .queryset_for_scope(actor=actor, scope=scope)
        .filter(status=ApprovalStatus.IN_REVIEW)
    )
    approval_hours = [
        max((now - created).total_seconds(), 0) / 3600
        for created in approvals.values_list('created_at', flat=True)[:1000]
    ]
    aging['approvals_in_review'] = {
        'p50_hours': _percentile(approval_hours, 0.5),
        'max_hours': round(max(approval_hours), 1) if approval_hours else None,
    }
    if bool(getattr(settings, 'AIMMS_JOB_KITS_ENABLED', False)):
        shortages = (
            get_source_adapter('job_kit_shortage')
            .queryset_for_scope(actor=actor, scope=scope)
            .filter(status='open')
        )
        shortage_days = [
            max((now - created).total_seconds(), 0) / 86400
            for created in shortages.values_list('created_at', flat=True)[:1000]
        ]
        aging['shortages_open'] = {
            'p50_days': _percentile(shortage_days, 0.5),
            'max_days': round(max(shortage_days), 1) if shortage_days else None,
        }
    else:
        aging['shortages_open'] = {'source_disabled': True}
    return aging


def rule_health(actor, scope: MaintenanceScope) -> list[dict]:
    """Per-rule scan health for administrators (§3.5)."""
    if actor is None or not actor.has_perm(PERM_HEALTH):
        raise PermissionDenied('Missing required permission: ' + PERM_HEALTH)
    scope_key = encode_scope(scope)
    now = timezone.now()
    freshness, revisions = _rule_freshness_rows(scope_key, now)
    recent_runs: dict[str, list[dict]] = {}
    runs = (
        RiskScanRun.objects
        .filter(rule__code__in=RULE_SPECS, scope_key=scope_key)
        .annotate(
            risk_row_number=Window(
                expression=RowNumber(),
                partition_by=[F('rule__code')],
                order_by=F('started_at').desc(),
            )
        )
        .filter(risk_row_number__lte=5)
        .order_by('rule__code', '-started_at')
        .values('rule__code', 'status', 'started_at', 'completed_at', 'error_summary')
    )
    for run in runs:
        recent_runs.setdefault(run['rule__code'], []).append(run)

    visible_counts = VISIBILITY_POLICY.filter_findings(
        actor, RiskFinding.objects.filter(scope_key=scope_key)
    )
    finding_counts: dict[str, dict[str, int]] = {}
    for count in (
        visible_counts
        .values('rule_code', 'state')
        .annotate(count=Count('pk'))
        .order_by()
    ):
        finding_counts.setdefault(count['rule_code'], {})[count['state']] = count[
            'count'
        ]

    rows = []
    for entry in freshness:
        code = entry['rule']
        revision = revisions.get(code)
        rule_runs = recent_runs.get(code, [])
        failure_streak = 0
        for run in rule_runs:
            if run['status'] in (RiskScanStatus.FAILED, RiskScanStatus.ABORTED):
                failure_streak += 1
            else:
                break
        spec = RULE_SPECS.get(code)
        rows.append({
            **entry,
            'version': revision.version if revision else None,
            'cadence': spec.cadence if spec else None,
            'critical_rule': revision.critical_rule if revision else False,
            'config': revision.config if revision else {},
            'enabled_scopes': revision.enabled_scopes if revision else [],
            'dormant_reason': spec.dormant_reason if spec else '',
            'failure_streak': failure_streak,
            'recent_runs': [
                {
                    'status': run['status'],
                    'started_at': run['started_at'].isoformat(),
                    'completed_at': run['completed_at'].isoformat()
                    if run['completed_at']
                    else None,
                    'error_summary': run['error_summary'],
                }
                for run in rule_runs
            ],
            'finding_counts': finding_counts.get(code, {}),
        })
    return rows


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

_DEFAULT_NOTIFICATION_POLICY = {
    'transitions': [
        'new_finding',
        'reopened',
        'severity_increase',
        'assigned',
        'scan_failed',
    ],
    'severity_floor': 'high',
    'recipient_user_ids': [],
    'channels': ['ui'],
    'quiet_hours': None,
    'rate_limit_per_hour': 20,
}


def _notification_policy(revision: RiskRuleDefinition) -> dict:
    """Merge the revision's notification policy over defaults."""
    policy = dict(_DEFAULT_NOTIFICATION_POLICY)
    policy.update(revision.notification_policy or {})
    return policy


def _stage_notification_intents(
    revision: RiskRuleDefinition,
    *,
    transition: str,
    event: RiskFindingEvent | None = None,
    run: RiskScanRun | None = None,
    finding: RiskFinding | None = None,
    extra_recipients: list | None = None,
) -> int:
    """Insert pending delivery intents atomically with the transition.

    Candidate rows never notify directly (RR-ADR-007); the sweeper
    revalidates visibility immediately before each send.
    """
    if not notifications_enabled():
        return 0
    policy = _notification_policy(revision)
    if transition not in policy.get('transitions', []):
        return 0
    if finding is not None and transition in ('new_finding', 'severity_increase'):
        floor = _RANK_BY_LABEL.get(str(policy.get('severity_floor', 'high')), 3)
        if _RANK_BY_LABEL.get(finding.severity, 0) < floor:
            return 0
    recipients: dict[int, object] = {}
    users = get_user_model().objects.filter(
        pk__in=[int(pk) for pk in policy.get('recipient_user_ids', [])], is_active=True
    )
    for user in users:
        recipients[user.pk] = user
    for user in extra_recipients or []:
        if user is not None and user.is_active:
            recipients[user.pk] = user
    created = 0
    source_ref = f'evt:{event.pk}' if event is not None else f'run:{run.pk}'
    now = timezone.now()
    for user in recipients.values():
        for channel in policy.get('channels', ['ui']):
            occurrence_key = f'{source_ref}:{transition}:{user.pk}:{channel}'[:160]
            _, was_created = RiskNotificationDelivery.objects.get_or_create(
                occurrence_key=occurrence_key,
                defaults={
                    'event': event,
                    'scan_run': run,
                    'recipient': user,
                    'channel': channel,
                    'state': 'pending',
                    'policy_snapshot': {
                        'transition': transition,
                        'quiet_hours': policy.get('quiet_hours'),
                        'rate_limit_per_hour': policy.get('rate_limit_per_hour'),
                    },
                    'not_before': now,
                },
            )
            created += int(was_created)
    return created


def _in_quiet_hours(policy_snapshot: dict, now: datetime) -> datetime | None:
    """Return the quiet-hours end if ``now`` falls inside the window.

    The window is evaluated in the policy's ``timezone`` (§9) when one is
    configured and ``now`` is timezone-aware; an unknown zone falls back to
    the server timezone rather than silently shifting the window.
    """
    window = (policy_snapshot or {}).get('quiet_hours')
    if not window:
        return None
    try:
        start_h, start_m = (int(x) for x in str(window['start']).split(':'))
        end_h, end_m = (int(x) for x in str(window['end']).split(':'))
    except (KeyError, ValueError, TypeError):
        return None
    local_now = now
    zone_name = window.get('timezone') if isinstance(window, dict) else None
    if zone_name and timezone.is_aware(now):
        try:
            from zoneinfo import ZoneInfo

            local_now = now.astimezone(ZoneInfo(str(zone_name)))
        except (KeyError, ValueError, OSError):
            local_now = now
    start = local_now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = local_now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    result = None
    if start <= end:
        result = end if start <= local_now < end else None
    elif local_now >= start:  # Window wraps midnight.
        result = end + timedelta(days=1)
    elif local_now < end:
        result = end
    if result is not None and timezone.is_aware(now):
        result = result.astimezone(now.tzinfo)
    return result


def deliver_pending_notifications(limit: int = 50) -> int:
    """Deliver due pending intents through the notification framework.

    Revalidates recipient visibility immediately before sending; revoked
    recipients become ``suppressed`` with an audited reason. Uses
    ``check_recent=False`` because the occurrence key already owns dedupe —
    the framework's one-day category suppression could skip a later
    recipient/channel row.
    """
    if not notifications_enabled():
        return 0
    import common.notifications

    now = timezone.now()
    sent = 0
    pending = (
        RiskNotificationDelivery.objects
        .filter(state='pending', not_before__lte=now)
        .select_related('event__finding', 'scan_run', 'recipient')
        .order_by('created_at')[:limit]
    )
    for delivery in pending:
        finding = delivery.event.finding if delivery.event_id else None
        quiet_until = _in_quiet_hours(delivery.policy_snapshot, now)
        if quiet_until is not None:
            delivery.not_before = quiet_until
            delivery.save(update_fields=['not_before'])
            continue
        rate_limit = int(
            (delivery.policy_snapshot or {}).get('rate_limit_per_hour') or 0
        )
        if rate_limit > 0:
            recent = RiskNotificationDelivery.objects.filter(
                recipient=delivery.recipient,
                state='sent',
                sent_at__gte=now - timedelta(hours=1),
            ).count()
            if recent >= rate_limit:
                delivery.not_before = now + timedelta(minutes=15)
                delivery.save(update_fields=['not_before'])
                continue
        recipient_visible = (
            _recipient_may_view(delivery.recipient, finding)
            if finding is not None
            else _recipient_may_view_scan(delivery.recipient, delivery.scan_run)
        )
        if not recipient_visible:
            delivery.state = 'suppressed'
            delivery.suppression_reason = (
                'Recipient lost scope or category visibility before delivery'
            )
            delivery.save(update_fields=['state', 'suppression_reason'])
            continue
        target = finding if finding is not None else delivery.scan_run.rule
        title = (
            finding.title
            if finding is not None
            else (f'Risk rule scan failed: {delivery.scan_run.rule.code}')
        )
        try:
            # Title-only preview: never evidence payloads in notifications.
            common.notifications.trigger_notification(
                target,
                'risk.finding_transition',
                targets=[delivery.recipient],
                check_recent=False,
                context={'name': 'Risk Radar', 'message': title, 'slug': 'risk_radar'},
            )
        except Exception as exc:  # pragma: no cover - transport failure
            delivery.state = 'failed' if delivery.attempts >= 4 else 'pending'
            delivery.attempts += 1
            delivery.last_error = str(exc)[:2000]
            delivery.not_before = now + timedelta(minutes=5)
            delivery.save(
                update_fields=['state', 'attempts', 'last_error', 'not_before']
            )
        else:
            delivery.state = 'sent'
            delivery.sent_at = timezone.now()
            delivery.attempts += 1
            delivery.save(update_fields=['state', 'sent_at', 'attempts'])
            sent += 1
    return sent


def _recipient_may_view(recipient, finding: RiskFinding) -> bool:
    """Return True when a recipient may still see the finding."""
    if not recipient.is_active or not recipient.has_perm(PERM_VIEW):
        return False
    if not VISIBILITY_POLICY.can_view(recipient, finding):
        return False
    try:
        return decode_scope_key(finding.scope_key) in scope_for_actor(recipient)
    except (ScopeError, RiskScopeError):
        return False


def _recipient_may_view_scan(recipient, run: RiskScanRun) -> bool:
    """Return True when a recipient may see a failed-scan occurrence."""
    if not recipient.is_active:
        return False
    categories = VISIBILITY_POLICY.visible_categories(recipient)
    if categories is not None and run.rule.category not in categories:
        return False
    try:
        return decode_scope_key(run.scope_key) in scope_for_actor(recipient)
    except (ScopeError, RiskScopeError):
        return False
