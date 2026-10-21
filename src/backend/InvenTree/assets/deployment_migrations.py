"""Apply an opt-in PostgreSQL rollout as one guarded transaction."""

import sys

from django.core.management import call_command
from django.db import connection as default_connection
from django.db import transaction
from django.db.migrations.executor import MigrationExecutor

from assets.deploy_lineage import report

# Shared by all revisions using this startup path; scoped to this database.
MIGRATION_LOCK = 0x41494D4D534D4947


def apply(connection=None, *, out=None):
    """Check lineage and commit the complete atomic forward plan together.

    An old worker must never observe focus columns without their persistent
    SQL defaults. Separate ``migrate`` transactions expose exactly that gap.
    Refuse plans containing non-atomic migrations: those require a separately
    reviewed maintenance procedure rather than an automatic startup rollout.
    """
    connection = connection or default_connection
    out = out or sys.stdout
    if connection.vendor != 'postgresql' or not connection.features.can_rollback_ddl:
        raise RuntimeError('Atomic startup migrations require PostgreSQL')
    if connection.in_atomic_block or not connection.get_autocommit():
        raise RuntimeError('Startup migration runner requires its own transaction')

    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s)', [MIGRATION_LOCK])
            if not cursor.fetchone()[0]:
                raise RuntimeError('Another startup migration runner holds the lock')
            cursor.execute("SET LOCAL lock_timeout = '5s'")

        # Both the verdict and plan are computed while holding the shared lock.
        # A refusal raises SystemExit and rolls back without executing any DDL.
        report(connection, out=out)
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        targets = executor.loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        if any(backwards or not migration.atomic for migration, backwards in plan):
            raise RuntimeError(
                'Startup plan must contain only atomic forward migrations'
            )

        # Preserve Django's normal pre/post-migrate signals, permissions and
        # content types as well as the schema/recorder operations.
        call_command(
            'migrate', database=connection.alias, interactive=False, stdout=out
        )
        remaining = MigrationExecutor(connection)
        if remaining.migration_plan(remaining.loader.graph.leaf_nodes()):
            raise RuntimeError('Migration plan is not empty after applying the rollout')

    out.write('Atomic migration step committed.\n')
