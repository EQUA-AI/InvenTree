"""Database models for the assets (equipment machines) application."""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

import InvenTree.models


class Client(models.Model):
    """A client of this software - the tenant an internal asset belongs to.

    Deliberately not ``company.Company``. A Company is a sales relationship:
    somebody we manufacture for, buy from or sell to. A Client is who *uses this
    deployment*, and an internal plant asset has one even though nobody bought
    it. Conflating the two is what previously left internal machines with no
    resolvable scope at all, so chat and the canonical API refused to touch them.
    """

    name = models.CharField(
        max_length=255, unique=True, verbose_name=_('Name'), help_text=_('Client name')
    )

    #: Stable external identifier used in scope tokens and integrations. Unique
    #: and immutable in practice: it is what an actor's granted scope names.
    code = models.SlugField(
        max_length=64,
        unique=True,
        verbose_name=_('Client Code'),
        help_text=_('Stable identifier used for scope and integrations'),
    )

    active = models.BooleanField(default=True, db_index=True, verbose_name=_('Active'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['name']
        verbose_name = _('Client')
        verbose_name_plural = _('Clients')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return self.name

    def save(self, *args, **kwargs):
        """Enforce code immutability at the ORM layer.

        The serializer and admin already refuse edits; this belt covers direct
        ORM writes. Every granted scope and every stamped RAG ``client_codes``
        value names this slug — a rename orphans or cross-assigns them all. A
        governed rename needs a dedicated command that re-stamps everything.
        """
        if self.pk is not None:
            original = (
                Client.objects.filter(pk=self.pk).values_list('code', flat=True).first()
            )
            if original is not None and original != self.code:
                raise ValueError('Client.code is immutable once created')
        super().save(*args, **kwargs)


def get_default_client() -> Client:
    """Return the deployment's default internal tenant, creating it if absent.

    Single definition of the fallback identity shared by the backfill
    migration, the serializer default and the demo loader, so a machine
    created without an explicit client always lands in the same tenant.
    """
    client, _created = Client.objects.get_or_create(
        code='internal', defaults={'name': 'Internal'}
    )
    return client


class AssetMachine(
    InvenTree.models.InvenTreeBarcodeMixin,
    InvenTree.models.InvenTreeAttachmentMixin,
    models.Model,
):
    """An equipment asset / machine installed at a facility.

    This is separate from the InvenTree ``machine`` app which handles
    external integrations (e.g. label printers).

    S32a: the barcode mixin makes machines scannable — the printed QR on
    the physical asset resolves to this row through the standard barcode
    rail, and scanning one in the field opens the AI drawer with the
    machine as a visible routing hint.
    """

    @classmethod
    def barcode_model_type_code(cls):
        """Return the barcode model type code for machines."""
        return 'AM'

    @staticmethod
    def get_api_url():
        """Return the API detail URL base for machines."""
        return '/api/assets/machines/'

    def get_absolute_url(self):
        """Return the web URL for this machine's detail page."""
        return f'/web/machines/machine/{self.pk}/'

    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_('Name'),
        help_text=_('Name of the machine / asset'),
    )

    description = models.TextField(
        blank=True,
        verbose_name=_('Description'),
        help_text=_('Description of the machine'),
    )

    active = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name=_('Active'),
        help_text=_('Is this machine active?'),
    )

    location = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name=_('Location'),
        help_text=_('Free-text location (e.g. "Bay 4", "Sydney")'),
    )

    # The client is what makes a machine scope-resolvable. Machines carry no
    # sales-customer identity: that claim belongs to work orders and
    # procedures. A machine without a client is deliberately unreachable.
    client = models.ForeignKey(
        Client,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        db_index=True,
        related_name='machines',
        verbose_name=_('Client'),
        help_text=_('Client this internal asset belongs to'),
    )

    manufacturer = models.CharField(
        max_length=255, blank=True, verbose_name=_('Manufacturer')
    )

    model = models.CharField(max_length=255, blank=True, verbose_name=_('Model'))

    serial = models.CharField(
        max_length=255, blank=True, verbose_name=_('Serial Number')
    )

    # S25: operator-declared knowledge profile (aimms.machine_profile.v1).
    # Schema-validated in the serializer via assets/machine_profile.py, never
    # at the DB level -- existing rows and admin edits must not brick reads.
    profile = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Knowledge Profile'),
        help_text=_(
            'Structured machine knowledge (criticality, components, fault codes)'
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['name']
        verbose_name = _('Asset Machine')
        verbose_name_plural = _('Asset Machines')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return self.name


class MachinePart(models.Model):
    """Join table linking a Part to an AssetMachine with a quantity."""

    machine = models.ForeignKey(
        AssetMachine,
        on_delete=models.CASCADE,
        related_name='machine_parts',
        verbose_name=_('Machine'),
    )

    part = models.ForeignKey(
        'part.Part',
        on_delete=models.CASCADE,
        related_name='machine_installations',
        verbose_name=_('Part'),
    )

    quantity = models.PositiveIntegerField(default=1, verbose_name=_('Quantity'))

    notes = models.TextField(blank=True, verbose_name=_('Notes'))

    class Meta:
        """Model metadata."""

        unique_together = [('machine', 'part')]
        ordering = ['part__name']
        verbose_name = _('Machine Part')
        verbose_name_plural = _('Machine Parts')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.machine.name} — {self.part.name} x{self.quantity}'


class AssetMaintenanceRecord(models.Model):
    """A maintenance event recorded against an AssetMachine."""

    machine = models.ForeignKey(
        AssetMachine,
        on_delete=models.CASCADE,
        related_name='maintenance_records',
        verbose_name=_('Machine'),
    )

    date = models.DateField(
        verbose_name=_('Date'), help_text=_('Date the maintenance was performed')
    )

    summary = models.CharField(max_length=255, verbose_name=_('Summary'))

    details = models.TextField(blank=True, verbose_name=_('Details'))

    performed_by = models.CharField(
        max_length=255, blank=True, verbose_name=_('Performed By')
    )

    work_order = models.OneToOneField(
        'tasks.WorkOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='maintenance_record',
        verbose_name=_('Work Order'),
        help_text=_('Linked Kanban card / work order (optional)'),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Model metadata."""

        ordering = ['-date']
        indexes = [
            # S7 analytics: per-machine date windows over the record
            # population at the 25k envelope.
            models.Index(fields=['machine', 'date'], name='assets_maint_machine_date')
        ]
        verbose_name = _('Maintenance Record')
        verbose_name_plural = _('Maintenance Records')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.machine.name} — {self.summary} ({self.date})'


class ClientScopeGrant(models.Model):
    """One explicit user-to-client maintenance-scope grant (S6, WP-A5).

    The isolation model is purely positive-grant: retrieval filters are
    built from the actor's granted clients, so a client nobody is granted
    (``eval-offlimits``) is unreachable and a client exactly one evaluation
    user is granted (``eval-fixtures``) is invisible to everyone else.
    Until S6 no user->client relation existed — the single-site resolver
    granted every ordinary user the one deployment client. Grant rows are
    consumed by ``tasks.scope.granted_client_scope_resolver``: a user WITH
    rows gets exactly those clients; a user with none falls back to the
    single-site behavior unchanged.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='aimms_client_grants',
    )
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name='scope_grants'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """One grant per (user, client)."""

        constraints = [
            models.UniqueConstraint(
                fields=['user', 'client'], name='assets_client_grant_uniq'
            )
        ]

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.user} -> {self.client.code}'


# Machine health models live in their own module for readability but belong to
# this app; importing them here is what registers them.
from .health_models import (  # noqa: F401
    ACTIVE_ANOMALY_STATUSES,
    AnomalySeverity,
    AnomalyStatus,
    HealthEvidenceSnapshot,
    HealthSource,
    HealthState,
    MachineAnomaly,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
    SnapshotReason,
    SourceType,
)
