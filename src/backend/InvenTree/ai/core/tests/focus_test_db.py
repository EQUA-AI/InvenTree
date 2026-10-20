"""Test-only schema bootstrap for focused ai/core ORM tests (NO migrations).

The isolated settings now load the real application and migration registry.
``test_migration_registry`` separately exercises fresh, intermediate-history
upgrade and repeat migrations, each in a fresh process with an empty disposable
default database. Serialize those CPU-heavy cases within their timeout budget.

This helper deliberately keeps focused repository/lock/replay tests lightweight
by creating installed apps' tables directly through Django's schema editor. It
exercises real ORM/SQL behavior, not migration history, data migrations or fresh
installation correctness. Its file-backed SQLite state belongs to the per-run
scratch directory in ``ai.core.tests.settings``, never a business database.
"""

from __future__ import annotations

from django.apps import apps
from django.db import connection

_BOOTSTRAPPED = False


class IslandSchemaBootstrapError(RuntimeError):
    """Raised when required managed tables remain unresolved (fail closed)."""


def ensure_island_tables() -> None:
    """Create any missing tables for installed island apps (idempotent).

    Fail closed: if any required managed table still does not exist after the
    creation passes, raise ``IslandSchemaBootstrapError`` and leave
    ``_BOOTSTRAPPED`` False. Required coverage includes automatically created
    managed models (implicit many-to-many through tables): plain
    ``apps.get_models()`` excludes them, so verification deliberately uses
    ``include_auto_created=True`` while the creation loop keeps creating them
    as side effects of their parent models (no duplicate implicit creation).
    Unmanaged models are a deliberate exclusion - their tables are owned by
    other provisioning, not this bootstrap.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return
    existing = set(connection.introspection.table_names())
    pending = [
        model
        for model in apps.get_models()
        if model._meta.managed and model._meta.db_table not in existing
    ]
    with connection.schema_editor() as schema_editor:
        # FK targets can require an earlier table; retry until a pass makes
        # no progress (the unmanaged/deferred remainder is not needed here).
        for _ in range(6):
            remaining = []
            for model in pending:
                try:
                    schema_editor.create_model(model)
                except Exception:
                    remaining.append(model)
            pending = remaining
            if not pending:
                break
    # Reconcile against fresh introspection, never the pending list: a
    # retried create_model that kept raising can still have produced its
    # table (auto-created M2M through tables, proxy or duplicate db_table
    # models create it as a side effect of another model). The required
    # cohort must include automatically created managed models (implicit
    # M2M through tables) - apps.get_models() silently excludes them and a
    # missing required table would then pass this gate. Unmanaged models
    # stay a deliberate exclusion. Error text names model labels and table
    # names only - no connection/config values.
    present = set(connection.introspection.table_names())
    unresolved = sorted({
        f"{model._meta.app_label}.{model.__name__}[{model._meta.db_table}]"
        for model in apps.get_models(include_auto_created=True)
        if model._meta.managed and model._meta.db_table not in present
    })
    if unresolved:
        raise IslandSchemaBootstrapError(
            "focused schema bootstrap left required managed tables unresolved: "
            + ", ".join(unresolved)
        )
    _BOOTSTRAPPED = True
