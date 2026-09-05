"""Shared test-only helpers for the AIMMS fork apps (M1 prerequisite B2).

The fork's own CI lane runs the Django-runner suites on SQLite for speed and
on PostgreSQL (``pgvector/pgvector:pg15``) for fidelity. A handful of tests
exercise behaviour SQLite cannot reproduce — timezone-aware column round
trips on the RAG rows, the calendar-month arithmetic behind usage-aggregate
retention, vector columns — and would otherwise report as noise on the
SQLite lane. Mark those with :data:`requires_postgres` so the SQLite lane
skips them HONESTLY (a visible skip, not a silenced failure) while the
PostgreSQL lane still gates them.

Never mark a test with this decorator to hide a real regression: the
PostgreSQL lane must stay green for every marked test.
"""

from __future__ import annotations

from unittest import skipUnless

from django.db import connection

#: True when the active default connection is PostgreSQL.
POSTGRES = connection.vendor == 'postgresql'

#: Class or method decorator: skip unless the default database is PostgreSQL.
requires_postgres = skipUnless(
    POSTGRES,
    'PostgreSQL-only behaviour (timezone-aware rows / month arithmetic / '
    'vector columns); runs on the fork-postgres CI lane',
)

__all__ = ['POSTGRES', 'requires_postgres']
