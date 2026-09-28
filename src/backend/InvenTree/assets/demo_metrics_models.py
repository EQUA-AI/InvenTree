"""Durable ledger models for the EQUA demo metrics session.

These are ordinary Django-managed tables in the existing PostgreSQL database.
They record what a synthetic demo session *claims*, *creates*, *references* and
*proves* — never credentials and never arbitrary provider payloads.

Design rules (implementation plan sections 5 and 7):

* ``plan`` is a local/protected artifact and never inserts a session row;
  ``apply`` claims the session atomically after verifying approval.
* Borrowed assets (machines, locations) are protected references, not owned
  objects: the FKs here are ``PROTECT`` so a cleanup must handle ledger rows
  explicitly and can never cascade evidence away.
* One active session may claim a machine at a time (transactional claim plus a
  partial unique constraint as the database backstop).
* Receipts are the durable import identity: unique per
  ``(session, operation_kind, item_key)`` with the canonical request hash, so a
  concurrent duplicate create loses the race and an exact replay returns the
  stored receipt instead of a new effect.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class DemoMetricsSession(models.Model):
    """One approved, applied synthetic demo session.

    Immutable approved inputs (fixture/mapping/plan hashes, anchor, target
    fingerprint) are fixed at apply time; only lifecycle status/timestamps
    change afterwards.
    """

    class Status(models.TextChoices):
        """Session lifecycle."""

        ACTIVE = 'active', _('Active')
        STOPPED = 'stopped', _('Stopped')
        EXPIRED = 'expired', _('Expired')
        CLEANED = 'cleaned', _('Cleaned')

    class Mode(models.TextChoices):
        """Which integrated demo the session carries."""

        CURRENT = 'current_demo', _('Integrated current demo')
        HISTORY = 'historical_demo', _('Integrated historical demo')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset_key = models.CharField(max_length=64, db_index=True)
    session_key = models.CharField(max_length=64)
    mode = models.CharField(max_length=32, choices=Mode.choices, default=Mode.CURRENT)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )

    #: Immutable input identity captured at apply.
    fixture_version = models.CharField(max_length=32)
    fixture_canonical_sha256 = models.CharField(max_length=64)
    fixture_file_sha256 = models.CharField(max_length=64)
    mapping_sha256 = models.CharField(max_length=64)
    plan_sha256 = models.CharField(max_length=64)
    target_fingerprint = models.CharField(max_length=255)
    code_identity = models.CharField(max_length=255, blank=True)

    #: Who the demo runs for. The FK is evidence, not authority.
    demo_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='demo_metrics_sessions',
    )
    demo_owner_identity = models.CharField(max_length=255, blank=True)

    anchor_at = models.DateTimeField()
    timezone_name = models.CharField(max_length=64, default='UTC')
    expires_at = models.DateTimeField()

    #: Narrow effect allowlist; the only supported value is ``database_write``.
    effect_policy = models.JSONField(default=list, blank=True)
    #: The canonical plan body (without its own hash/approval envelope).
    plan_body = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    applied_at = models.DateTimeField(null=True, blank=True)
    stopped_at = models.DateTimeField(null=True, blank=True)
    cleaned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        """One session per dataset/session key."""

        constraints = [
            models.UniqueConstraint(
                fields=['dataset_key', 'session_key'],
                name='demometrics_session_key_uniq',
            )
        ]
        indexes = [
            models.Index(
                fields=['status', 'expires_at'], name='demometrics_session_status'
            )
        ]
        verbose_name = _('Demo Metrics Session')
        verbose_name_plural = _('Demo Metrics Sessions')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.dataset_key}/{self.session_key} ({self.status})'


class DemoMetricsMachine(models.Model):
    """A borrowed machine claimed by one demo session.

    ``claim_active`` marks the machine as borrowed for the session's lifetime;
    the partial unique constraint makes overlapping claims impossible at the
    database level, and the apply service claims under a row lock so the loser
    of a race rolls back entirely.
    """

    session = models.ForeignKey(
        DemoMetricsSession, on_delete=models.CASCADE, related_name='machines'
    )
    machine = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.PROTECT,
        related_name='demo_metric_memberships',
    )
    alias = models.CharField(max_length=64)
    client_code = models.CharField(max_length=64)
    #: Physical placement observed at claim time (borrowed evidence, never moved).
    placement_id = models.IntegerField(null=True, blank=True)
    placement_version = models.BigIntegerField(default=0)
    ownership_evidence = models.JSONField(default=dict, blank=True)
    expected_fingerprint = models.CharField(max_length=64, blank=True)
    claim_active = models.BooleanField(default=True)

    class Meta:
        """Uniqueness for aliases, machines and active claims."""

        constraints = [
            models.UniqueConstraint(
                fields=['session', 'alias'], name='demometrics_machine_alias_uniq'
            ),
            models.UniqueConstraint(
                fields=['session', 'machine'], name='demometrics_machine_machine_uniq'
            ),
            models.UniqueConstraint(
                fields=['machine'],
                condition=models.Q(claim_active=True),
                name='demometrics_machine_active_claim',
            ),
        ]
        indexes = [
            models.Index(fields=['session', 'alias'], name='demometrics_machine_lookup')
        ]
        verbose_name = _('Demo Metrics Machine')
        verbose_name_plural = _('Demo Metrics Machines')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.alias} -> machine {self.machine_id} ({self.session_id})'


class DemoMetricsObject(models.Model):
    """One session-owned or session-referenced record with stable identity.

    ``fixture_key`` is the stable item key from the reviewed fixture (for
    example ``WO-05`` or ``A01/bearing_temperature``); the typed target FK is
    set according to ``kind`` and exactly one FK must be consistent with it.
    Dependent records (primary Kanban cards, provenance events) are tracked in
    ``dependent_refs`` rather than guessed from names later.
    """

    class Kind(models.TextChoices):
        """Supported bounded target kinds."""

        WORK_ORDER = 'work_order', _('Work Order')
        SOURCE = 'source', _('Health Source')
        BINDING = 'binding', _('Signal Binding')
        ANOMALY = 'anomaly', _('Anomaly')
        COVERAGE_INTERVAL = 'coverage_interval', _('Coverage Interval')
        DOWNTIME_INTERVAL = 'downtime_interval', _('Downtime Interval')

    class Origin(models.TextChoices):
        """Whether this session created the record or only references it."""

        CREATED = 'created', _('Created by this session')
        REFERENCED = 'referenced', _('Referenced existing record')

    session = models.ForeignKey(
        DemoMetricsSession, on_delete=models.CASCADE, related_name='ledger_objects'
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    fixture_key = models.CharField(max_length=128)
    origin = models.CharField(max_length=16, choices=Origin.choices)

    work_order = models.ForeignKey(
        'tasks.WorkOrder',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='demo_metric_objects',
    )
    source = models.ForeignKey(
        'assets.HealthSource',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='demo_metric_objects',
    )
    binding = models.ForeignKey(
        'assets.MachineSignalBinding',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='demo_metric_objects',
    )

    #: Fingerprint at seed time; changed values mean an operator edit to report.
    seed_fingerprint = models.CharField(max_length=64, blank=True)
    last_fingerprint = models.CharField(max_length=64, blank=True)
    target_version = models.IntegerField(default=0)
    #: Bounded dependent-record evidence: [{'kind': 'card', 'id': 12}, ...].
    dependent_refs = models.JSONField(default=list, blank=True)

    class Meta:
        """Stable per-session identity and one consistent typed target FK."""

        constraints = [
            models.UniqueConstraint(
                fields=['session', 'kind', 'fixture_key'],
                name='demometrics_object_key_uniq',
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(kind='work_order')
                        & models.Q(work_order__isnull=False)
                        & models.Q(source__isnull=True)
                        & models.Q(binding__isnull=True)
                    )
                    | (
                        models.Q(kind='source')
                        & models.Q(source__isnull=False)
                        & models.Q(work_order__isnull=True)
                        & models.Q(binding__isnull=True)
                    )
                    | (
                        models.Q(kind='binding')
                        & models.Q(binding__isnull=False)
                        & models.Q(work_order__isnull=True)
                        & models.Q(source__isnull=True)
                    )
                    | (
                        models.Q(
                            kind__in=[
                                'anomaly',
                                'coverage_interval',
                                'downtime_interval',
                            ]
                        )
                        & models.Q(work_order__isnull=True)
                        & models.Q(source__isnull=True)
                        & models.Q(binding__isnull=True)
                    )
                ),
                name='demometrics_object_target_consistent',
            ),
        ]
        indexes = [
            models.Index(fields=['session', 'kind'], name='demometrics_object_kind')
        ]
        verbose_name = _('Demo Metrics Object')
        verbose_name_plural = _('Demo Metrics Objects')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.kind}:{self.fixture_key} ({self.origin})'


class DemoMetricsReceipt(models.Model):
    """Durable receipt for exactly one session operation on one item.

    Unique per ``(session, operation_kind, item_key)``: this is the import
    identity that makes concurrent duplicate creates lose and exact replays
    return the stored receipt without new effects. Commit effect and receipt in
    the same transaction, always.
    """

    class Operation(models.TextChoices):
        """Supported receipted operations."""

        APPLY_SESSION = 'apply_session', _('Apply session')
        APPLY_SOURCE = 'apply_source', _('Create source')
        APPLY_BINDING = 'apply_binding', _('Create binding')
        APPLY_WORK_ORDER = 'apply_work_order', _('Create work order')
        APPLY_CONTROL = 'apply_control', _('Import synthetic control')
        APPLY_OBSERVATION = 'apply_observation', _('Ingest observation')
        REPLAY_OBSERVATION = 'replay_observation', _('Replay observation')
        STOP = 'stop', _('Stop session')
        CLEANUP = 'cleanup', _('Cleanup session')

    class Outcome(models.TextChoices):
        """What happened when the operation ran."""

        APPLIED = 'applied', _('Applied')
        REPLAYED = 'replayed', _('Replayed without new effects')

    session = models.ForeignKey(
        DemoMetricsSession, on_delete=models.CASCADE, related_name='receipts'
    )
    operation_kind = models.CharField(max_length=32, choices=Operation.choices)
    item_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    #: Bounded effect identifiers (row ids / event ids), never payloads.
    effect_ids = models.JSONField(default=list, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Receipt identity."""

        constraints = [
            models.UniqueConstraint(
                fields=['session', 'operation_kind', 'item_key'],
                name='demometrics_receipt_key_uniq',
            )
        ]
        indexes = [
            models.Index(
                fields=['operation_kind', 'item_key'],
                name='demometrics_receipt_identity',
            )
        ]
        verbose_name = _('Demo Metrics Receipt')
        verbose_name_plural = _('Demo Metrics Receipts')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.operation_kind}:{self.item_key} ({self.outcome})'


class DemoMetricsCoverageInterval(models.Model):
    """Immutable synthetic planned-coverage interval (historical demo).

    Attribution is explicitly synthetic scenario data: it never claims a real
    placement at event time, and no placement is backdated to justify it.
    """

    session = models.ForeignKey(
        DemoMetricsSession, on_delete=models.CASCADE, related_name='coverage_intervals'
    )
    event_key = models.CharField(max_length=128)
    machine = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.PROTECT,
        related_name='demo_coverage_intervals',
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    fully_observed = models.BooleanField(default=True)
    planned = models.BooleanField(default=True)
    location_alias = models.CharField(max_length=64, blank=True)
    attribution_mode = models.CharField(max_length=32, default='synthetic_scenario')

    class Meta:
        """Unique keys, positive intervals and query indexes."""

        constraints = [
            models.UniqueConstraint(
                fields=['session', 'event_key'], name='demometrics_coverage_key_uniq'
            ),
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F('start_at')),
                name='demometrics_coverage_interval_positive',
            ),
        ]
        indexes = [
            models.Index(
                fields=['session', 'machine', 'start_at'],
                name='demometrics_coverage_query',
            )
        ]
        verbose_name = _('Demo Metrics Coverage Interval')
        verbose_name_plural = _('Demo Metrics Coverage Intervals')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.event_key} [{self.start_at} .. {self.end_at})'


class DemoMetricsDowntimeInterval(models.Model):
    """Immutable synthetic downtime event (historical demo).

    Overlaps are allowed and unioned at aggregation time, never double-counted.
    """

    session = models.ForeignKey(
        DemoMetricsSession, on_delete=models.CASCADE, related_name='downtime_intervals'
    )
    event_key = models.CharField(max_length=128)
    machine = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.PROTECT,
        related_name='demo_downtime_intervals',
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    loss_category = models.CharField(max_length=64)
    failure_key = models.CharField(max_length=128, blank=True)
    coverage = models.ForeignKey(
        DemoMetricsCoverageInterval,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='downtime_events',
    )
    location_alias = models.CharField(max_length=64, blank=True)
    attribution_mode = models.CharField(max_length=32, default='synthetic_scenario')

    class Meta:
        """Unique keys, positive intervals and query indexes."""

        constraints = [
            models.UniqueConstraint(
                fields=['session', 'event_key'], name='demometrics_downtime_key_uniq'
            ),
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F('start_at')),
                name='demometrics_downtime_interval_positive',
            ),
        ]
        indexes = [
            models.Index(
                fields=['session', 'machine', 'start_at'],
                name='demometrics_downtime_query',
            )
        ]
        verbose_name = _('Demo Metrics Downtime Interval')
        verbose_name_plural = _('Demo Metrics Downtime Intervals')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.event_key} [{self.start_at} .. {self.end_at})'
