"""O15 attributed registry facts. Commands retain their original saved results."""

from django.conf import settings
from django.db import models


class RegistryCommand(models.Model):
    """Durable actor-bound intent and result for exact command replay/audit."""

    machine = models.ForeignKey('assets.AssetMachine', on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    result = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """One intent per machine, actor and key."""

        constraints = [
            models.UniqueConstraint(
                fields=['machine', 'actor', 'key'], name='registry_command_key'
            )
        ]


class SignalDesignation(models.Model):
    """Append-only engineer decisions; absence means not designated."""

    binding = models.ForeignKey(
        'assets.MachineSignalBinding',
        on_delete=models.PROTECT,
        related_name='designations',
    )
    version = models.PositiveIntegerField()
    critical = models.BooleanField()
    binding_signature = models.CharField(max_length=64)
    reason = models.TextField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """A binding's decisions have a monotonic version."""

        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(
                fields=['binding', 'version'], name='signal_designation_version'
            )
        ]


class ComponentFit(models.Model):
    """A physical fitted identity, separate from the slot's catalogue family."""

    component = models.ForeignKey(
        'assets.AssetComponent', on_delete=models.PROTECT, related_name='fits'
    )
    stock_item = models.ForeignKey('stock.StockItem', on_delete=models.PROTECT)
    work_order = models.ForeignKey(
        'tasks.WorkOrder', null=True, blank=True, on_delete=models.PROTECT
    )
    usage = models.OneToOneField(
        'tasks.CloseoutPartUsage', null=True, blank=True, on_delete=models.PROTECT
    )
    fitted_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    reason = models.TextField()
    retracted_at = models.DateTimeField(null=True, blank=True)
    retracted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    retraction_reason = models.TextField(blank=True)
    source = models.CharField(
        max_length=16,
        choices=[('manual', 'Manual entry'), ('closeout', 'Reconciled usage')],
    )
    # Snapshot tracked identity: subsequent stock edits cannot rewrite fit history.
    serial = models.CharField(max_length=100, blank=True)
    lot = models.CharField(max_length=100, blank=True)

    class Meta:
        """Effective fit order is independent of entry/verification order."""

        ordering = ['-fitted_at', '-pk']


class SharedPerformanceView(models.Model):
    """Machine-readable shared display choices; ownership never grants scope."""

    machine = models.ForeignKey('assets.AssetMachine', on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    name = models.CharField(max_length=80)
    config = models.JSONField()
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    version = models.PositiveIntegerField(default=1)
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+'
    )
    updated_at = models.DateTimeField(auto_now=True)
