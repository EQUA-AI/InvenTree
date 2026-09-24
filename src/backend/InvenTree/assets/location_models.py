"""Physical locations and effective history, separate from stock storage."""

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _


class AssetLocation(models.Model):
    """A client-owned physical node which can hold machines at any level."""

    class Kind(models.TextChoices):
        """Initial location types; hierarchy depth is independent of type."""

        SITE = 'site', _('Site')
        FACILITY = 'facility', _('Facility')
        BUILDING = 'building', _('Building')
        AREA = 'area', _('Area')
        LINE = 'line', _('Production Line')
        CELL = 'cell', _('Cell')
        ROOM = 'room', _('Room')
        OTHER = 'other', _('Other')

    client = models.ForeignKey('assets.Client', on_delete=models.PROTECT)
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.PROTECT, related_name='children'
    )
    name = models.CharField(max_length=255)
    code = models.SlugField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.SITE)
    description = models.TextField(blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    archived = models.BooleanField(default=False, db_index=True)
    version = models.PositiveBigIntegerField(default=1, editable=False)
    name_key = models.CharField(max_length=255, editable=False)
    # Non-null root key gives portable uniqueness for root siblings too.
    sibling_key = models.CharField(max_length=32, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Unique codes per client and normalized names per parent."""

        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['client', 'code'], name='assets_location_code'
            ),
            models.UniqueConstraint(
                fields=['client', 'sibling_key', 'name_key'],
                name='assets_location_sibling',
            ),
            models.CheckConstraint(
                condition=~Q(parent=F('pk')), name='assets_location_not_self'
            ),
        ]

    def __str__(self):
        """Return the physical node's name."""
        return self.name


class LocationParentHistory(models.Model):
    """Effective ancestry; a reparent never rewrites earlier membership."""

    location = models.ForeignKey(
        AssetLocation, on_delete=models.PROTECT, related_name='parent_history'
    )
    parent = models.ForeignKey(
        AssetLocation, null=True, blank=True, on_delete=models.PROTECT
    )
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    reason = models.CharField(max_length=1000)
    # Preserve the metadata in force at every structural edit, including names.
    snapshot = models.JSONField(default=dict)

    class Meta:
        """Bound each history interval and index event-time lookups."""

        constraints = [
            models.UniqueConstraint(
                fields=['location', 'valid_from'], name='assets_parent_start'
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gt=F('valid_from')),
                name='assets_parent_interval',
            ),
        ]


class MachineLocationTransfer(models.Model):
    """Audited, replayable all-or-nothing transfer command."""

    client = models.ForeignKey('assets.Client', on_delete=models.PROTECT)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL
    )
    idempotency_key = models.UUIDField()
    payload = models.JSONField()
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """A key is scoped to its initiating actor and client."""

        constraints = [
            models.UniqueConstraint(
                fields=['client', 'actor', 'idempotency_key'],
                name='assets_transfer_key',
            )
        ]


class MachinePlacementHistory(models.Model):
    """Known physical placement from the first explicit assignment onward."""

    machine = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.PROTECT,
        related_name='placement_history',
    )
    location = models.ForeignKey(
        AssetLocation, null=True, blank=True, on_delete=models.PROTECT
    )
    transfer = models.ForeignKey(MachineLocationTransfer, on_delete=models.PROTECT)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        """Nonempty half-open intervals with an indexed machine/start pair."""

        constraints = [
            models.UniqueConstraint(
                fields=['machine', 'valid_from'], name='assets_placement_start'
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gt=F('valid_from')),
                name='assets_placement_interval',
            ),
        ]
