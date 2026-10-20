"""Small helpers shared by the Work execution projections."""

from django.conf import settings


def person(user) -> str | None:
    """Return a display name for a recorded actor, or None when unrecorded."""
    if user is None:
        return None
    return user.get_full_name().strip() or user.get_username()


def closeout_wizard_enabled() -> bool:
    """Whether the closeout command surface (and its records) is enabled.

    Mirrors ``CloseoutEnabledMixin``: the closeout API sits on the canonical
    work-order API, so both deployment flags must be on.
    """
    return bool(getattr(settings, 'AIMMS_WORK_ORDERS_ENABLED', False)) and bool(
        getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False)
    )


def procedures_enabled() -> bool:
    """Whether governed procedures (applications, steps, deviations) are enabled."""
    return bool(getattr(settings, 'AIMMS_PROCEDURES_ENABLED', False))


def packet_of(work_order):
    """Return the repair packet owning this work order, if any."""
    try:
        return work_order.repair_packet
    except Exception:  # RelatedObjectDoesNotExist
        return None


def closeout_of(work_order):
    """Return the structured closeout of this work order, if any."""
    try:
        return work_order.structured_closeout
    except Exception:  # RelatedObjectDoesNotExist
        return None


def record_instant(value):
    """Require an explicit zone for entered physical/event timestamps."""
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    from rest_framework import serializers
    from rest_framework.exceptions import ValidationError

    try:
        parsed = parse_datetime(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or timezone.is_naive(parsed):
        raise ValidationError(
            'Provide an ISO timestamp with an explicit timezone offset.'
        )
    return serializers.DateTimeField().run_validation(value)
