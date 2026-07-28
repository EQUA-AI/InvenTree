"""Timestamps that mean the same thing on both database engines.

InvenTree runs tests with ``USE_TZ`` false (``settings.USE_TZ = bool(not
TESTING)``). PostgreSQL quietly accepts a timezone-aware datetime in that mode;
SQLite refuses it with ``SQLite backend does not support timezone-aware
datetimes when USE_TZ is False``. A test that hardcodes a ``Z`` suffix therefore
passes on one engine and errors on the other, which is how a suite comes to look
green while being untested on the engine most contributors run.

These helpers follow the setting instead of assuming UTC is always safe. A suite
that deliberately overrides ``USE_TZ=True`` does not need them - it has already
told the database what to expect.
"""

from django.conf import settings

#: The suffix an ISO 8601 instant carries when it is expressed in UTC.
UTC_SUFFIX = 'Z'


def iso(instant: str) -> str:
    """Return ``instant`` with the offset the current configuration accepts.

    ``instant`` is written in the aware form, so the intended moment stays
    readable at the call site; the suffix is dropped when the database wants
    naive values.
    """
    if settings.USE_TZ:
        return instant
    return instant.removesuffix(UTC_SUFFIX)
