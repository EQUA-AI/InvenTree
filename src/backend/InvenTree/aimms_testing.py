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


def restore_leaf_schema() -> None:
    """Migrate every app to its leaf nodes (undo a migration round-trip test).

    Django orders ``TransactionTestCase`` classes after every ``TestCase``
    and runs them in discovery order, so a migration test that ends at an old
    migration hands the NEXT transaction test a rolled-back schema: its
    ``setUp`` dies with ``column … does not exist``, and on PostgreSQL the
    flush itself can fail when the old schema still holds a table the
    current models no longer own (``TRUNCATE auth_user`` refused because
    the resurrected ``aichat_scopedconversation`` references it).
    """
    from django.db.migrations.executor import MigrationExecutor

    connection.close()
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


class MigrationRoundTripMixin:
    """Mix into every ``TransactionTestCase`` that calls ``executor.migrate``.

    Registers :func:`restore_leaf_schema` as a cleanup, so the schema is back
    at the leaves before Django's flush — also when an assertion fails halfway
    through the round trip.
    """

    def setUp(self) -> None:
        """Register the leaf-schema restore before the test's own setup runs."""
        super().setUp()
        self.addCleanup(restore_leaf_schema)


__all__ = [
    'POSTGRES',
    'MigrationRoundTripMixin',
    'requires_postgres',
    'restore_leaf_schema',
]
