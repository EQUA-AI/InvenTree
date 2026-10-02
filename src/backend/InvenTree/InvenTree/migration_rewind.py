"""Explicit canonical rewinds for disposable migration rehearsals.

Django's default backwards traversal of a merged graph is not necessarily the
inverse of the forward traversal it uses to reconstruct historical states.
SQLite table rebuilds can consequently erase still-applied sibling fields and
constraints. Never change an already-applied migration to force an order.

This planner instead rewinds a complete *applied suffix* of the canonical
forward plan. It deliberately includes later migrations in other apps and on
sibling branches: retaining them while using an earlier historical schema is
unsafe. Callers must show and approve that wider plan before any execution.
This is not a production recovery policy; deployments remain forward-fix only.
"""

from django.db.migrations.executor import MigrationExecutor


def ordered_rewind_plan(executor, targets):
    """Return a reversible applied suffix, in inverse canonical forward order."""
    requested = executor.migration_plan(targets)
    if not requested:
        return []
    if any(not backwards for _, backwards in requested):
        raise ValueError('An ordered rewind requires a backwards-only plan.')

    canonical = executor.migration_plan(
        executor.loader.graph.leaf_nodes(), clean_start=True
    )
    positions = {
        (migration.app_label, migration.name): index
        for index, (migration, _) in enumerate(canonical)
    }
    first = min(
        positions[migration.app_label, migration.name] for migration, _ in requested
    )
    applied = executor.loader.applied_migrations
    plan = [
        (migration, True)
        for migration, _ in reversed(canonical[first:])
        if (migration.app_label, migration.name) in applied
    ]
    for migration, _ in plan:
        if any(not operation.reversible for operation in migration.operations):
            raise ValueError(
                f'Cannot rewind irreversible migration '
                f'{migration.app_label}.{migration.name}.'
            )
    return plan


def migrate_for_rehearsal(connection, targets):
    """Apply normally or rewind a canonical suffix on a disposable test database.

    Unlike ``migrate`` this may rewind more than the target's descendants.
    It is intended for migration tests which restore all leaves afterwards,
    not for shared or live databases.
    """
    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    requested = executor.migration_plan(targets)
    if any(backwards for _, backwards in requested):
        return executor.migrate(targets, plan=ordered_rewind_plan(executor, targets))
    return executor.migrate(targets)
