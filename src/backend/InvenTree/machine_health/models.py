"""Database models for normalized machine health.

AIMMS federates industrial data; it does not replace the historian. SCADA, PLC,
DCS, MES, BAS/BMS, EMS and IIoT platforms remain the systems of record for raw,
high-frequency telemetry. What is stored here is deliberately bounded:

* which source owns which machine tag (:class:`MachineSignalBinding`),
* the latest normalized value for each binding (:class:`MachineSignalState`),
* detected anomaly lifecycle (:class:`MachineAnomaly`), and
* immutable evidence snapshots selected for a repair or an AI analysis
  (:class:`HealthEvidenceSnapshot`).

Raw retention, downsampling and protocol handling stay in the source platform.
Connections are read-only: nothing here writes back to a control system.
"""

import uuid

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class SourceType(models.TextChoices):
    """Industrial system families a health source can represent."""

    IOT = 'iot', _('IoT')
    SCADA = 'scada', _('SCADA')
    PLC = 'plc', _('PLC')
    DCS = 'dcs', _('DCS')
    MES = 'mes', _('MES')
    BAS_BMS = 'bas_bms', _('BAS / BMS')
    EMS = 'ems', _('EMS')
    IIOT = 'iiot', _('IIoT')
    HISTORIAN = 'historian', _('Historian')
    WEBHOOK = 'webhook', _('Webhook')
    MANUAL = 'manual', _('Manual')


class SignalQuality(models.TextChoices):
    """Normalized data-quality vocabulary shared by every connector."""

    GOOD = 'good', _('Good')
    UNCERTAIN = 'uncertain', _('Uncertain')
    BAD = 'bad', _('Bad')
    UNKNOWN = 'unknown', _('Unknown')


class HealthState(models.TextChoices):
    """Overall condition presented for a machine."""

    UNKNOWN = 'unknown', _('Unknown')
    NORMAL = 'normal', _('Normal')
    WARNING = 'warning', _('Warning')
    CRITICAL = 'critical', _('Critical')
    OFFLINE = 'offline', _('Offline')


class AnomalySeverity(models.TextChoices):
    """Severity ladder for detected anomalies."""

    INFO = 'info', _('Info')
    WARNING = 'warning', _('Warning')
    CRITICAL = 'critical', _('Critical')


class AnomalyStatus(models.TextChoices):
    """Anomaly lifecycle states."""

    OPEN = 'open', _('Open')
    ACKNOWLEDGED = 'acknowledged', _('Acknowledged')
    RESOLVED = 'resolved', _('Resolved')
    SUPPRESSED = 'suppressed', _('Suppressed')


#: Statuses in which an anomaly still demands attention. Exactly one anomaly per
#: (machine, fingerprint) may be in one of these at a time, which is what makes
#: repeated ingestion of the same alarm idempotent.
ACTIVE_ANOMALY_STATUSES = (AnomalyStatus.OPEN, AnomalyStatus.ACKNOWLEDGED)


class SnapshotReason(models.TextChoices):
    """Why an evidence snapshot was captured."""

    MANUAL_REPAIR = 'manual_repair', _('Manual repair')
    ANOMALY_REPAIR = 'anomaly_repair', _('Anomaly repair')
    AI_DIAGNOSIS = 'ai_diagnosis', _('AI diagnosis')
    CONFIRMATION_TEST = 'confirmation_test', _('Confirmation test')


class HealthSource(models.Model):
    """A configured, read-only connection to an industrial data platform.

    Credentials never live here. ``secret_ref`` names a deployment-managed secret
    (Key Vault entry, environment key, connector config id); the connector
    resolves it at call time so no credential can reach an API response, an
    approval context or a browser.
    """

    name = models.CharField(max_length=200, unique=True, verbose_name=_('Name'))

    source_type = models.CharField(
        max_length=16,
        choices=SourceType.choices,
        db_index=True,
        verbose_name=_('Source Type'),
    )

    connector_type = models.CharField(
        max_length=64,
        blank=True,
        help_text=_('Registered connector adapter key'),
        verbose_name=_('Connector'),
    )

    secret_ref = models.CharField(
        max_length=200,
        blank=True,
        help_text=_('Deployment-managed secret reference; never a credential'),
        verbose_name=_('Secret Reference'),
    )

    #: Non-secret connector settings (endpoint, database name, poll interval).
    config = models.JSONField(default=dict, blank=True, verbose_name=_('Config'))

    active = models.BooleanField(default=True, db_index=True, verbose_name=_('Active'))

    #: Authoritative deployment/site boundary. Free-text machine location is
    #: never promoted into a security boundary (see tasks.scope).
    site_key = models.CharField(
        max_length=64, blank=True, db_index=True, verbose_name=_('Site Key')
    )

    customer = models.ForeignKey(
        'company.Company',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='health_sources',
        verbose_name=_('Customer'),
    )

    freshness_threshold_seconds = models.PositiveIntegerField(
        default=900,
        validators=[MinValueValidator(1)],
        help_text=_('Signals older than this are shown as stale'),
        verbose_name=_('Freshness Threshold'),
    )

    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    #: Redacted classification only - never a provider message or payload.
    last_error_code = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['name']
        verbose_name = _('Health Source')
        verbose_name_plural = _('Health Sources')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.name} ({self.get_source_type_display()})'

    @property
    def connection_healthy(self) -> bool:
        """Whether the last observed connector attempt succeeded."""
        if not self.active:
            return False
        if self.last_error_at is None:
            return self.last_success_at is not None
        if self.last_success_at is None:
            return False
        return self.last_success_at >= self.last_error_at


class MachineSignalBinding(models.Model):
    """Maps one opaque external tag to one machine signal AIMMS understands."""

    machine = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.CASCADE,
        related_name='signal_bindings',
        verbose_name=_('Machine'),
    )

    source = models.ForeignKey(
        HealthSource,
        on_delete=models.CASCADE,
        related_name='bindings',
        verbose_name=_('Source'),
    )

    #: Opaque to AIMMS. Never interpolated into a query built from client input.
    external_key = models.CharField(
        max_length=255,
        db_index=True,
        help_text=_('Tag / point identifier in the source system'),
        verbose_name=_('External Key'),
    )

    display_name = models.CharField(max_length=200, verbose_name=_('Display Name'))

    signal_kind = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_('e.g. current, vibration, pressure, temperature, level'),
        verbose_name=_('Signal Kind'),
    )

    unit = models.CharField(max_length=32, blank=True, verbose_name=_('Unit'))

    normal_min = models.FloatField(null=True, blank=True)
    normal_max = models.FloatField(null=True, blank=True)
    warn_min = models.FloatField(null=True, blank=True)
    warn_max = models.FloatField(null=True, blank=True)
    critical_min = models.FloatField(null=True, blank=True)
    critical_max = models.FloatField(null=True, blank=True)

    #: Scale/offset or unit-conversion metadata applied on ingest.
    transform = models.JSONField(default=dict, blank=True, verbose_name=_('Transform'))

    active = models.BooleanField(default=True, db_index=True, verbose_name=_('Active'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['display_name']
        constraints = [
            models.UniqueConstraint(
                fields=['machine', 'source', 'external_key'],
                name='machine_health_binding_unique',
            )
        ]
        indexes = [models.Index(fields=['machine', 'active'])]
        verbose_name = _('Machine Signal Binding')
        verbose_name_plural = _('Machine Signal Bindings')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.machine.name} - {self.display_name}'

    def classify(self, value) -> str:
        """Return the health state implied by a numeric value and its bounds.

        Unknown rather than normal when no bounds are configured: an unbounded
        signal has no opinion, and presenting one as healthy would be a guess.
        """
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return HealthState.UNKNOWN

        if (self.critical_min is not None and value < self.critical_min) or (
            self.critical_max is not None and value > self.critical_max
        ):
            return HealthState.CRITICAL

        if (self.warn_min is not None and value < self.warn_min) or (
            self.warn_max is not None and value > self.warn_max
        ):
            return HealthState.WARNING

        if self.normal_min is not None or self.normal_max is not None:
            below = self.normal_min is not None and value < self.normal_min
            above = self.normal_max is not None and value > self.normal_max
            return HealthState.WARNING if (below or above) else HealthState.NORMAL

        has_bounds = any(
            bound is not None
            for bound in (
                self.warn_min,
                self.warn_max,
                self.critical_min,
                self.critical_max,
            )
        )
        return HealthState.NORMAL if has_bounds else HealthState.UNKNOWN


class MachineSignalState(models.Model):
    """The latest normalized value for one binding.

    One row per binding: this is a current-state cache, not a time series. Trend
    data is read back from the source through the connector, bounded per request.
    """

    binding = models.OneToOneField(
        MachineSignalBinding,
        on_delete=models.CASCADE,
        related_name='state',
        verbose_name=_('Binding'),
    )

    #: Normalized payload: {"value": ..., "raw": ...}. Bounded on ingest.
    value = models.JSONField(default=dict, blank=True, verbose_name=_('Value'))

    observed_at = models.DateTimeField(
        db_index=True, help_text=_('When the source observed the value')
    )
    received_at = models.DateTimeField(
        default=timezone.now, help_text=_('When AIMMS accepted the value')
    )

    quality = models.CharField(
        max_length=12,
        choices=SignalQuality.choices,
        default=SignalQuality.UNKNOWN,
        db_index=True,
        verbose_name=_('Quality'),
    )

    #: Monotonic source sequence where the platform provides one. Used to reject
    #: out-of-order and replayed updates.
    source_sequence = models.BigIntegerField(null=True, blank=True)

    #: Hash of the source payload, not the payload itself.
    payload_hash = models.CharField(max_length=64, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        verbose_name = _('Machine Signal State')
        verbose_name_plural = _('Machine Signal States')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.binding} @ {self.observed_at.isoformat()}'

    def is_stale(self, threshold_seconds: int, *, now=None) -> bool:
        """Whether this observation is older than the source's freshness budget."""
        now = now or timezone.now()
        return (now - self.observed_at).total_seconds() > threshold_seconds


class MachineAnomaly(models.Model):
    """A detected abnormal condition on a machine.

    Detection is deterministic: source-provided alarms and configured threshold
    rules. AI may summarize an anomaly but never raises one on its own, so a
    critical alarm always traces back to a policy record or the source system.
    """

    machine = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.CASCADE,
        related_name='anomalies',
        verbose_name=_('Machine'),
    )

    source = models.ForeignKey(
        HealthSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='anomalies',
        verbose_name=_('Source'),
    )

    bindings = models.ManyToManyField(
        MachineSignalBinding,
        blank=True,
        related_name='anomalies',
        verbose_name=_('Signals'),
    )

    #: Human/external reference retained for cross-system traceability. Never
    #: used as an internal key.
    external_id = models.CharField(max_length=128, blank=True, db_index=True)
    alarm_code = models.CharField(max_length=64, blank=True)

    #: Stable identity for "the same problem". Repeated ingestion of one alarm
    #: updates the open anomaly rather than creating another.
    fingerprint = models.CharField(max_length=128, db_index=True)

    severity = models.CharField(
        max_length=12,
        choices=AnomalySeverity.choices,
        default=AnomalySeverity.WARNING,
        db_index=True,
        verbose_name=_('Severity'),
    )

    status = models.CharField(
        max_length=16,
        choices=AnomalyStatus.choices,
        default=AnomalyStatus.OPEN,
        db_index=True,
        verbose_name=_('Status'),
    )

    title = models.CharField(max_length=255, verbose_name=_('Title'))
    evidence_summary = models.TextField(blank=True, verbose_name=_('Evidence'))

    #: Bounded numeric context (peak, threshold, duration). Not a payload dump.
    metrics = models.JSONField(default=dict, blank=True, verbose_name=_('Metrics'))

    detector = models.CharField(
        max_length=64, blank=True, help_text=_('Rule or model that raised this anomaly')
    )
    detector_version = models.CharField(max_length=32, blank=True)

    first_observed_at = models.DateTimeField(db_index=True)
    last_observed_at = models.DateTimeField(db_index=True)

    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='acknowledged_anomalies',
    )
    acknowledgement_note = models.TextField(blank=True)

    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True)

    work_order = models.ForeignKey(
        'tasks.KanbanCard',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='anomalies',
        verbose_name=_('Work Order'),
    )
    repair_packet = models.ForeignKey(
        'repair.RepairPacket',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='anomalies',
        verbose_name=_('Repair Packet'),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['-last_observed_at']
        constraints = [
            models.UniqueConstraint(
                fields=['machine', 'fingerprint'],
                condition=models.Q(
                    status__in=[s.value for s in ACTIVE_ANOMALY_STATUSES]
                ),
                name='machine_health_anomaly_open_unique',
            )
        ]
        indexes = [
            models.Index(fields=['machine', 'status']),
            models.Index(fields=['machine', 'severity', 'status']),
        ]
        verbose_name = _('Machine Anomaly')
        verbose_name_plural = _('Machine Anomalies')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.machine.name}: {self.title}'

    @property
    def is_active(self) -> bool:
        """Whether the anomaly still demands attention."""
        return self.status in {s.value for s in ACTIVE_ANOMALY_STATUSES}


class HealthEvidenceSnapshot(models.Model):
    """An immutable, bounded observation captured for a repair decision.

    A snapshot is the citation behind every preliminary result and every
    evidence-backed repair. It never changes after creation: amendments are new
    snapshots, so what a technician or an approver saw stays reconstructable.
    Retention must preserve snapshots referenced by a repair even if the live
    source or anomaly is later removed.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    machine = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.PROTECT,
        related_name='health_snapshots',
        verbose_name=_('Machine'),
    )

    anomaly = models.ForeignKey(
        MachineAnomaly,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='snapshots',
        verbose_name=_('Anomaly'),
    )

    source = models.ForeignKey(
        HealthSource,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='snapshots',
        verbose_name=_('Source'),
    )

    binding = models.ForeignKey(
        MachineSignalBinding,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='snapshots',
        verbose_name=_('Binding'),
    )

    #: Denormalized so the citation still reads correctly after a binding or
    #: source is deleted.
    signal_label = models.CharField(max_length=200, blank=True)
    unit = models.CharField(max_length=32, blank=True)

    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    captured_at = models.DateTimeField(default=timezone.now, db_index=True)

    #: Bounded normalized samples and/or summary statistics.
    samples = models.JSONField(default=list, blank=True, verbose_name=_('Samples'))
    statistics = models.JSONField(
        default=dict, blank=True, verbose_name=_('Statistics')
    )

    quality = models.CharField(
        max_length=12,
        choices=SignalQuality.choices,
        default=SignalQuality.UNKNOWN,
        verbose_name=_('Quality'),
    )
    stale = models.BooleanField(
        default=False, help_text=_('The window was already stale when captured')
    )

    reason = models.CharField(
        max_length=24, choices=SnapshotReason.choices, db_index=True
    )

    #: External identifiers only; no credentials, no raw provider payload.
    source_references = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='health_snapshots',
    )
    #: Set when a system actor (connector, scheduled analysis) captured it.
    system_actor = models.CharField(max_length=64, blank=True)

    class Meta:
        """Model metadata."""

        ordering = ['-captured_at']
        indexes = [
            models.Index(fields=['machine', 'captured_at']),
            models.Index(fields=['anomaly', 'captured_at']),
        ]
        verbose_name = _('Health Evidence Snapshot')
        verbose_name_plural = _('Health Evidence Snapshots')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.signal_label or self.machine.name} @ {self.captured_at}'

    def save(self, *args, **kwargs):
        """Reject any update: a captured snapshot is evidence, not a record."""
        if not self._state.adding:
            raise ValueError(
                'Health evidence snapshots are immutable; capture a new one instead'
            )
        super().save(*args, **kwargs)
