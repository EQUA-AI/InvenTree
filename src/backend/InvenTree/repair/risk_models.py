"""Risk Radar / Command Center domain models (Features #4 and #16).

One scan engine, one finding store, two surfaces. These models are owned by
the radar and hold projections only: evidence pointers plus minimal display
snapshots. Source systems remain authoritative for every fact and action.

Design contract: ``LocalDocs/RiskRadarCommandCenterImplementation.md``.

Portability note (NFR-RR-009): every field here is portable across
PostgreSQL, MySQL/MariaDB and SQLite — JSONField only, no backend-specific
fields, no partial indexes.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

FINGERPRINT_SCHEMA_VERSION = 1

SEVERITY_POLICY_VERSION = 1

RISK_CATEGORIES = (
    'safety',
    'parts',
    'approvals',
    'procurement',
    'stock',
    'closeout',
    'assets',
    'operations',
    'sync',
)


class RiskSeverity(models.TextChoices):
    """Severity labels derived from documented factor tuples (RR-ADR-005)."""

    CRITICAL = 'critical', _('Critical')
    HIGH = 'high', _('High')
    MEDIUM = 'medium', _('Medium')
    LOW = 'low', _('Low')


SEVERITY_RANK = {
    RiskSeverity.CRITICAL: 4,
    RiskSeverity.HIGH: 3,
    RiskSeverity.MEDIUM: 2,
    RiskSeverity.LOW: 1,
}


class RiskFindingState(models.TextChoices):
    """Lifecycle states for a persisted risk finding."""

    OPEN = 'open', _('Open')
    ACKNOWLEDGED = 'acknowledged', _('Acknowledged')
    SNOOZED = 'snoozed', _('Snoozed')
    RESOLVED = 'resolved', _('Resolved')
    DISMISSED = 'dismissed', _('Dismissed')


ACTIVE_FINDING_STATES = (
    RiskFindingState.OPEN,
    RiskFindingState.ACKNOWLEDGED,
    RiskFindingState.SNOOZED,
)


class RiskScanStatus(models.TextChoices):
    """Terminal and running states for a scan run.

    A "partial scan" is any run that ends ``failed`` or ``aborted``; it is not
    a separate persisted state and can never resolve findings (RR-ADR-004).
    """

    RUNNING = 'running', _('Running')
    COMPLETE = 'complete', _('Complete')
    FAILED = 'failed', _('Failed')
    ABORTED = 'aborted', _('Aborted')


class RiskRuleDefinition(models.Model):
    """One immutable ``(code, version)`` revision of a deterministic rule.

    Configuration changes never rewrite history: they create a new revision
    plus an audit event, and the activation service selects exactly one
    ``is_current`` revision per code (RR-ADR-002/003/010).
    """

    code = models.CharField(max_length=64, db_index=True)
    version = models.PositiveIntegerField()
    category = models.CharField(max_length=32, db_index=True)
    default_severity_policy = models.JSONField(default=dict, blank=True)
    schedule = models.CharField(max_length=16)
    watermark_strategy = models.CharField(max_length=24, default='full_snapshot')
    config_schema = models.JSONField(default=dict, blank=True)
    config = models.JSONField(default=dict, blank=True)
    critical_rule = models.BooleanField(default=False)
    notification_policy = models.JSONField(default=dict, blank=True)
    enabled_scopes = models.JSONField(default=list, blank=True)
    enabled = models.BooleanField(default=False)
    is_current = models.BooleanField(default=False, db_index=True)
    activation_generation = models.PositiveIntegerField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['code', 'version'], name='unique_risk_rule_revision'
            )
        ]
        permissions = [
            ('administer_riskrules', 'Can administer risk rules'),
            ('view_riskrulehealth', 'Can view risk rule health'),
        ]

    def __str__(self) -> str:
        """Return the rule code and revision."""
        return f'{self.code} v{self.version}'


class RiskScanRun(models.Model):
    """One leased evaluation of a rule revision against one scope."""

    rule = models.ForeignKey(
        RiskRuleDefinition, on_delete=models.PROTECT, related_name='scan_runs'
    )
    rule_version = models.PositiveIntegerField()
    activation_generation = models.PositiveIntegerField()
    scope_key = models.CharField(max_length=128, db_index=True)
    service_identity = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='risk_scan_runs',
    )
    lease_token = models.CharField(max_length=128)
    watermark = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=RiskScanStatus.choices,
        default=RiskScanStatus.RUNNING,
        db_index=True,
    )
    candidate_count = models.PositiveIntegerField(default=0)
    upsert_count = models.PositiveIntegerField(default=0)
    resolve_count = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True)

    class Meta:
        """Model metadata."""

        ordering = ['-started_at']
        indexes = [models.Index(fields=['scope_key', 'status', 'started_at'])]

    def __str__(self) -> str:
        """Return a compact run description."""
        return f'{self.rule_id}@{self.scope_key} ({self.status})'


class RiskScanLease(models.Model):
    """Serialization token for scans of one ``(rule_code, scope)`` pair.

    Acquisition/takeover stores a random token on both the lease and the run;
    finalization requires the token to still match, so a late or superseded
    worker cannot promote, resolve, or advance watermarks.
    """

    rule_code = models.CharField(max_length=64)
    scope_key = models.CharField(max_length=128)
    owner = models.CharField(max_length=128, blank=True)
    lease_token = models.CharField(max_length=128)
    expires_at = models.DateTimeField(db_index=True)
    heartbeat_at = models.DateTimeField()

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['rule_code', 'scope_key'], name='unique_risk_scan_lease'
            )
        ]

    def __str__(self) -> str:
        """Return the leased pair."""
        return f'{self.rule_code}@{self.scope_key}'


class RiskScanCandidate(models.Model):
    """Staged candidate row: incomplete evaluation stays invisible.

    Only a successful, under-cap run atomically promotes staged candidates
    into findings; failed or aborted runs discard them untouched.
    """

    run = models.ForeignKey(
        RiskScanRun, on_delete=models.CASCADE, related_name='staged_candidates'
    )
    fingerprint = models.CharField(max_length=64)
    source_as_of = models.DateTimeField()
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['run', 'fingerprint'], name='unique_risk_candidate_per_run'
            )
        ]

    def __str__(self) -> str:
        """Return the staged fingerprint."""
        return f'{self.run_id}:{self.fingerprint[:12]}'


class RiskFinding(models.Model):
    """A fingerprinted, evidenced projection of one risky source condition.

    Acknowledgement is not resolution: resolution requires the source
    condition to clear on a complete successful scan (plus grace) or an
    authorized, reasoned, expiring disposition. Findings never grant
    authority over the source record (possession is not authorization).
    """

    fingerprint = models.CharField(max_length=64, unique=True)
    fingerprint_version = models.PositiveSmallIntegerField(
        default=FINGERPRINT_SCHEMA_VERSION
    )
    scope_key = models.CharField(max_length=128, db_index=True)
    rule_revision = models.ForeignKey(
        RiskRuleDefinition, on_delete=models.PROTECT, related_name='findings'
    )
    rule_code = models.CharField(max_length=64, db_index=True)
    rule_version = models.PositiveIntegerField()
    category = models.CharField(max_length=32, db_index=True)
    severity = models.CharField(
        max_length=16, choices=RiskSeverity.choices, db_index=True
    )
    severity_factors = models.JSONField(default=dict, blank=True)
    source_model = models.CharField(max_length=64)
    source_id = models.CharField(max_length=64)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    evidence = models.JSONField(default=dict, blank=True)
    state = models.CharField(
        max_length=16,
        choices=RiskFindingState.choices,
        default=RiskFindingState.OPEN,
        db_index=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='owned_risk_findings',
    )
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField()
    condition_started_at = models.DateTimeField()
    last_seen_run = models.ForeignKey(
        RiskScanRun, on_delete=models.PROTECT, related_name='last_seen_findings'
    )
    source_as_of = models.DateTimeField()
    due_at = models.DateTimeField(null=True, blank=True)
    snooze_until = models.DateTimeField(null=True, blank=True)
    dismiss_recheck_at = models.DateTimeField(null=True, blank=True)
    reopen_count = models.PositiveIntegerField(default=0)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        """Model metadata."""

        ordering = ['-last_seen']
        indexes = [
            models.Index(fields=['scope_key', 'state', 'severity']),
            models.Index(fields=['scope_key', 'category', 'state']),
            models.Index(fields=['rule_code', 'scope_key', 'state']),
        ]
        permissions = [
            ('acknowledge_riskfinding', 'Can acknowledge risk findings'),
            ('assign_riskfinding', 'Can assign risk findings'),
            ('snooze_riskfinding', 'Can snooze risk findings'),
            ('dismiss_riskfinding', 'Can dismiss risk findings'),
        ]

    def __str__(self) -> str:
        """Return the rule code and source identity."""
        return f'{self.rule_code}: {self.source_model}#{self.source_id}'

    @property
    def is_active(self) -> bool:
        """True while the finding still demands attention."""
        return self.state in ACTIVE_FINDING_STATES


class RiskFindingEvent(models.Model):
    """Immutable audit entry for every finding transition or command."""

    EVENT_TYPES = (
        'detected',
        'changed',
        'acknowledged',
        'assigned',
        'snoozed',
        'dismissed',
        'resolved',
        'reopened',
        'superseded',
        'recheck_requested',
    )

    finding = models.ForeignKey(
        RiskFinding, on_delete=models.PROTECT, related_name='events'
    )
    event_type = models.CharField(max_length=32, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    idempotency_key = models.CharField(
        max_length=128, null=True, blank=True, default=None
    )
    request_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Model metadata."""

        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['finding', 'idempotency_key'], name='unique_risk_command_key'
            )
        ]

    def __str__(self) -> str:
        """Return the event type and finding id."""
        return f'{self.finding_id}: {self.event_type}'


class RiskActionLink(models.Model):
    """Deep link to an existing governed surface (RR-ADR-008).

    The radar advertises an action only when a named governed route exists;
    that surface retains its own permission, readiness and approval gates.
    """

    finding = models.ForeignKey(
        RiskFinding, on_delete=models.CASCADE, related_name='action_links'
    )
    label = models.CharField(max_length=128)
    target_kind = models.CharField(max_length=32)
    target_id = models.CharField(max_length=64)
    route = models.CharField(max_length=255)

    class Meta:
        """Model metadata."""

        constraints = [
            models.UniqueConstraint(
                fields=['finding', 'target_kind', 'target_id', 'label'],
                name='unique_risk_action_target',
            )
        ]

    def __str__(self) -> str:
        """Return the link target."""
        return f'{self.target_kind}#{self.target_id}'


class RiskRuleConfigurationEvent(models.Model):
    """Audit event for every rule configuration change (FR-RR-013)."""

    ACTIONS = ('created', 'activated', 'disabled', 'superseded')

    rule = models.ForeignKey(
        RiskRuleDefinition,
        on_delete=models.PROTECT,
        related_name='configuration_events',
    )
    rule_version = models.PositiveIntegerField()
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    action = models.CharField(max_length=24)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Model metadata."""

        ordering = ['-created_at']

    def __str__(self) -> str:
        """Return the audited action."""
        return f'{self.rule_id} v{self.rule_version}: {self.action}'


class RiskNotificationDelivery(models.Model):
    """Per-recipient/channel delivery intent bound to one transition.

    Rows are inserted in the same transaction as the finding event or scan-run
    terminal transition (RR-ADR-007); a lost post-commit queue hint cannot
    lose an occurrence because durable sweepers recover pending rows.
    """

    STATES = ('pending', 'sent', 'failed', 'suppressed')

    event = models.ForeignKey(
        RiskFindingEvent,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='notification_deliveries',
    )
    scan_run = models.ForeignKey(
        RiskScanRun,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='notification_deliveries',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    channel = models.CharField(max_length=24)
    occurrence_key = models.CharField(max_length=160, unique=True)
    state = models.CharField(max_length=16, default='pending', db_index=True)
    policy_snapshot = models.JSONField(default=dict, blank=True)
    not_before = models.DateTimeField()
    escalation_of = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='escalations',
    )
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    suppression_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Model metadata."""

        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(event__isnull=False, scan_run__isnull=True)
                    | Q(event__isnull=True, scan_run__isnull=False)
                ),
                name='risk_notification_exactly_one_source',
            )
        ]

    def __str__(self) -> str:
        """Return the occurrence key."""
        return self.occurrence_key


__all__ = [
    'ACTIVE_FINDING_STATES',
    'FINGERPRINT_SCHEMA_VERSION',
    'RISK_CATEGORIES',
    'SEVERITY_POLICY_VERSION',
    'SEVERITY_RANK',
    'RiskActionLink',
    'RiskFinding',
    'RiskFindingEvent',
    'RiskFindingState',
    'RiskNotificationDelivery',
    'RiskRuleConfigurationEvent',
    'RiskRuleDefinition',
    'RiskScanCandidate',
    'RiskScanLease',
    'RiskScanRun',
    'RiskScanStatus',
    'RiskSeverity',
]
