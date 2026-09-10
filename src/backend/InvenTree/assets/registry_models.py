"""Equipment occurrences and draft tag dictionaries; not a telemetry store."""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AssetComponent(models.Model):
    """One proposed or verified component occurrence at an equipment location."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    machine = models.ForeignKey(
        'assets.AssetMachine', on_delete=models.PROTECT, related_name='components'
    )
    part = models.ForeignKey('part.Part', on_delete=models.PROTECT)
    code = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=16,
        default='draft',
        choices=[('draft', 'Draft inference'), ('verified', 'Verified assignment')],
    )
    provenance = models.CharField(max_length=250, blank=True)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        """Stable occurrence slots, not one row per catalogue Part."""

        ordering = ['machine_id', 'code']
        constraints = [
            models.UniqueConstraint(
                fields=['machine', 'code'], name='registry_component_slot_unique'
            )
        ]


class DictionaryPoint(models.Model):
    """One exact source path owned by equipment, with an explicit review decision."""

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    station = models.ForeignKey(
        'assets.AssetMachine',
        on_delete=models.PROTECT,
        related_name='dictionary_points',
    )
    machine = models.ForeignKey(
        'assets.AssetMachine', on_delete=models.PROTECT, related_name='owned_points'
    )
    path = models.CharField(max_length=500)
    raw_tag = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    component = models.ForeignKey(
        AssetComponent, null=True, blank=True, on_delete=models.PROTECT
    )
    template = models.ForeignKey(
        'common.ParameterTemplate', null=True, blank=True, on_delete=models.PROTECT
    )
    match_method = models.CharField(max_length=16, default='unresolved')
    issue = models.CharField(max_length=250, blank=True)
    data_type = models.CharField(max_length=16, default='unknown')
    unit = models.CharField(max_length=32, blank=True)
    unit_status = models.CharField(max_length=16, default='unresolved')
    status = models.CharField(
        max_length=16,
        default='draft',
        choices=[
            ('draft', 'Draft'),
            ('approved', 'Approved'),
            ('unresolved', 'Unresolved'),
            ('rejected', 'Rejected'),
        ],
    )
    source_hash = models.CharField(max_length=64)
    review_note = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        """Reject point ownership outside its registered station and Client."""
        super().clean()
        if self.station.asset_type != 'pumphouse':
            raise ValidationError('Dictionary owner must be a registered station.')
        if (
            self.machine_id != self.station_id
            and self.machine.parent_id != self.station_id
        ):
            raise ValidationError('Equipment does not belong to this station.')
        if self.machine.client_id != self.station.client_id:
            raise ValidationError('Dictionary ownership cannot cross Clients.')
        if self.component_id and self.component.machine_id != self.machine_id:
            raise ValidationError(
                'Component does not belong to the selected equipment.'
            )

    def save(self, *args, **kwargs):
        """Enforce ownership when editing an existing point."""
        self.clean()
        return super().save(*args, **kwargs)

    class Meta:
        """Identity is station-scoped exact source path, independent of display names."""

        ordering = ['machine_id', 'path']
        constraints = [
            models.UniqueConstraint(
                fields=['station', 'path'], name='registry_dictionary_path_unique'
            )
        ]
