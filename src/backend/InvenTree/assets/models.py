"""Database models for the assets (equipment machines) application."""

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


class AssetMachine(InvenTree.models.InvenTreeAttachmentMixin, models.Model):
    """An equipment asset / machine installed at a facility.

    This is separate from the InvenTree ``machine`` app which handles
    external integrations (e.g. label printers).
    """

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
        verbose_name = _('Maintenance Record')
        verbose_name_plural = _('Maintenance Records')

    def __str__(self) -> str:
        """Readable identity for admin and logs."""
        return f'{self.machine.name} — {self.summary} ({self.date})'


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
