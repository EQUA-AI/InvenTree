"""Resolve the working calendar that governs a work order (S6).

A card resolves to exactly one calendar, in this order:

1. a calendar scoped to the card's machine,
2. else a calendar scoped to the card's effective customer (the card's own
   customer, or its machine's customer),
3. else the system default calendar (``is_default=True``),
4. else a hardcoded Mon-Fri 09:00-17:00 UTC fallback, so scheduling never fails
   for want of configuration.

Because every card is anchored to a machine (S3c), step 1 or 2 usually resolves;
the fallback exists so a fresh install with no calendars still schedules.
"""

from __future__ import annotations

from datetime import time

from tasks.models import WorkingCalendar
from tasks.services.working_time import CalendarSpec

# The out-of-the-box assumption, used when no calendar is configured at all.
_FALLBACK_SPEC = CalendarSpec(
    tzname='UTC',
    windows={day: ((time(9, 0), time(17, 0)),) for day in range(5)},
    holidays=frozenset(),
)


def calendar_for_card(card) -> WorkingCalendar | None:
    """Return the ``WorkingCalendar`` model governing ``card``, or None."""
    machine_id = getattr(card, 'machine_id', None)
    if machine_id:
        machine_cal = WorkingCalendar.objects.filter(machine_id=machine_id).first()
        if machine_cal is not None:
            return machine_cal

    customer_id = getattr(card, 'customer_id', None)
    if not customer_id and machine_id:
        customer_id = getattr(getattr(card, 'machine', None), 'customer_id', None)

    if customer_id:
        customer_cal = WorkingCalendar.objects.filter(customer_id=customer_id).first()
        if customer_cal is not None:
            return customer_cal

    return WorkingCalendar.objects.filter(is_default=True).first()


def spec_for_card(card) -> CalendarSpec:
    """Return the ``CalendarSpec`` governing ``card``, always non-null."""
    calendar = calendar_for_card(card)
    return calendar.to_spec() if calendar is not None else _FALLBACK_SPEC
