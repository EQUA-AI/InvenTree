"""Attributed registry projections and explicit fitted-identity promotion."""

from django.utils import timezone

from tasks.work_execution.common import person

from .record_models import ComponentFit
from .registry_models import AssetComponent


def binding_signature(binding):
    """A decision applies only to the exact reviewed binding meaning."""
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            {
                key: getattr(binding, key)
                for key in (
                    'machine_id',
                    'source_id',
                    'external_key',
                    'dictionary_hash',
                    'unit',
                    'signal_kind',
                    'transform',
                )
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def current_designation(binding):
    """A remapped binding must be reviewed again, preserving prior decisions."""
    decisions = list(binding.designations.all())
    row = decisions[0] if decisions else None
    if row is None:
        return None
    applies = row.binding_signature == binding_signature(binding)
    if not applies and not row.critical:
        return None
    return {
        **designation(row),
        'applies_current': applies,
        'review_required': row.critical and not applies,
    }


def designation(row):
    """Serialize a retained decision, never infer one from limits."""
    return {
        'critical': row.critical,
        'version': row.version,
        'reason': row.reason,
        'recorded_by': person(row.recorded_by),
        'recorded_at': row.recorded_at.isoformat(),
    }


def fit_row(row):
    """Snapshot identity and recorder remain visible after replacement."""
    return {
        'id': row.pk,
        'stock_item': row.stock_item_id,
        'serial': row.serial or None,
        'lot': row.lot or None,
        'fitted_at': row.fitted_at.isoformat() if row.fitted_at else None,
        'recorded_by_id': row.recorded_by_id,
        'work_order': row.work_order_id,
        'recorded_by': person(row.recorded_by),
        'recorded_at': row.recorded_at.isoformat(),
        'verified_by': person(row.verified_by),
        'verified_at': row.verified_at.isoformat() if row.verified_at else None,
        'retracted_at': row.retracted_at.isoformat() if row.retracted_at else None,
        'retracted_by': person(row.retracted_by),
        'retraction_reason': row.retraction_reason or None,
        'source': row.source,
        'reason': row.reason,
    }


def fit_projection(rows):
    """Manual proposals do not replace a verified fit."""
    rows = sorted(
        rows,
        key=lambda row: (
            row.fitted_at is not None,
            row.fitted_at or row.recorded_at,
            row.pk,
        ),
        reverse=True,
    )
    verified = [
        row
        for row in rows
        if row.verified_at and row.fitted_at and not row.retracted_at
    ]
    return {
        'version': len(rows)
        + sum(row.retracted_at is not None for row in rows)
        + sum(row.verified_at is not None for row in rows),
        'current': fit_row(verified[0]) if verified else None,
        'history': [fit_row(row) for row in verified[1:]],
        'pending': [
            fit_row(row) for row in rows if not row.verified_at and not row.retracted_at
        ],
        'retracted': [fit_row(row) for row in rows if row.retracted_at],
    }


def fit_queryset():
    """One batch for actors and effective fit history."""
    return ComponentFit.objects.select_related(
        'recorded_by', 'verified_by', 'retracted_by'
    )


def promote_usage(row, actor, *, installed_decision=False):
    """Only an explicit installed decision may assert a fitted identity.

    Refresh and generic consumption never verify installation. Missing completion
    or an older Work leaves a pending identity, ordered by the Work completion
    rather than by when someone reconciled it. A mapping mismatch never blocks
    native material reconciliation.
    """
    existing = ComponentFit.objects.filter(usage=row).first()
    installed = (
        row.state == 'reconciled'
        and row.disposition == 'spare_installed'
        and row.resolved_by_id is not None
        and row.used_quantity
        and row.used_quantity > 0
    )
    item = row.stock_item if row.stock_item_id else None
    work = row.work_order
    slot = (
        AssetComponent.objects
        .select_for_update()
        .filter(
            machine_id=work.machine_id,
            code=work.affected_component_ref,
            part_id=item.part_id,
            part__virtual=False,
        )
        .first()
        if item
        and work.machine_id
        and work.affected_component_ref
        and row.part_id == item.part_id
        else None
    )
    matches = installed and slot is not None and item and (item.serial or item.batch)
    if existing and (
        not matches
        or existing.stock_item_id != row.stock_item_id
        or existing.component_id != slot.pk
    ):
        if existing.retracted_at is None:
            existing.retracted_at, existing.retracted_by = timezone.now(), actor
            existing.retraction_reason = (
                row.variance_reason
                or 'Installation reconciliation changed; fitted identity requires review.'
            )
            existing.save(
                update_fields=['retracted_at', 'retracted_by', 'retraction_reason']
            )
        return None
    if existing and existing.retracted_at:
        return None
    if not matches:
        return None
    if not installed_decision or row.resolved_by_id != actor.pk:
        return existing
    if existing and existing.verified_at:
        return existing
    # Completion is an explicit ordering basis, not a measured installation time.
    fitted_at = work.actual_completed_at
    latest = next(
        (
            r
            for r in fit_queryset().filter(component=slot)
            if r.verified_at and r.fitted_at and not r.retracted_at
        ),
        None,
    )
    eligible = bool(
        fitted_at
        and fitted_at <= timezone.now()
        and (latest is None or fitted_at > latest.fitted_at)
    )
    if existing:
        existing.fitted_at = fitted_at
        if eligible:
            existing.verified_by, existing.verified_at = actor, timezone.now()
        existing.save(update_fields=['fitted_at', 'verified_by', 'verified_at'])
        return existing
    return ComponentFit.objects.create(
        component=slot,
        stock_item=item,
        usage=row,
        work_order=work,
        fitted_at=fitted_at,
        recorded_by=actor,
        verified_by=actor if eligible else None,
        verified_at=timezone.now() if eligible else None,
        reason=row.variance_reason
        or 'Explicit installed disposition reconciled against custody',
        source='closeout',
        serial=item.serial or '',
        lot=item.batch or '',
    )
