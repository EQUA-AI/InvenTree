"""Bootstrap regression: the real migration graph, on a real disposable DB.

These tests pin ``ai.core.tests.settings`` to the REAL Django migration
graph and the REAL migration history. They fail if any registered app's
migrations reference a parent node that is not registered (the
``aichat.0042_memory_consent`` -> ``assets.0015`` defect this file exists
to prevent), and they migrate a fresh file-backed SQLite database with
``run_syncdb=False`` so no table can appear without its actual migration.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

import django.apps  # noqa: E402
from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connection  # noqa: E402
from django.db.migrations.loader import MigrationLoader  # noqa: E402

# The real parent chain that the minimal settings historically lost.
CONSENT_MIGRATION = ("aichat", "0042_memory_consent")
CONSENT_PARENT = ("assets", "0015_alter_machineanomaly_work_order")


def _load_graph():
    """Load the real on-disk migration graph; never a synthesized one."""
    try:
        return MigrationLoader(connection)
    except Exception as exc:  # pragma: no cover - only on a broken registry
        raise AssertionError(f"real migration graph failed to load from disk: {exc!r}") from exc


def test_migration_graph_has_no_missing_parent_nodes():
    loader = _load_graph()
    graph = loader.graph

    assert CONSENT_MIGRATION in graph.nodes, (
        "aichat.0042_memory_consent must be part of the registered migration graph"
    )
    assert CONSENT_PARENT in graph.nodes, "its real assets parent migration must be registered too"

    missing = []
    for key, node in graph.node_map.items():
        for parent in node.parents:
            if parent not in graph.nodes:
                missing.append((key, parent))
    assert missing == [], f"migration graph has missing parent nodes: {missing}"

    # Validate real graph nodes and cycles using Django's public graph API.
    graph.validate_consistency()
    graph.ensure_not_cyclic()


def test_fresh_sqlite_migrate_builds_durable_memory_schema():
    # Real migrations only: no syncdb shortcut and no --fake history.
    call_command("migrate", verbosity=0, interactive=False, run_syncdb=False)

    tables = set(connection.introspection.table_names())

    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM django_migrations WHERE app = %s", ["aichat"])
        applied = {row[0] for row in cursor.fetchall()}
    assert "0042_memory_consent" in applied, (
        "memory-consent history must come from the real migration record"
    )

    model = django.apps.apps.get_model("aichat", "ClientAISettings")
    table = model._meta.db_table
    assert table in tables, f"{table} missing after real migrate"

    with connection.cursor() as cursor:
        cursor.execute(f'PRAGMA foreign_key_list("{table}")')
        fks = cursor.fetchall()
    targets = {row[2] for row in fks}

    # Enrollment must stay wired to the real tenant and user tables, so a
    # registry that silently drops assets/users cannot look green here.
    client_table = django.apps.apps.get_model("assets", "Client")._meta.db_table
    from django.contrib.auth import get_user_model

    user_table = get_user_model()._meta.db_table
    assert client_table in targets, f"missing FK to {client_table}: {targets}"
    assert user_table in targets, f"missing FK to {user_table}: {targets}"

    dangling = sorted(targets - tables)
    assert dangling == [], f"foreign keys reference tables that do not exist: {dangling}"


def test_registry_uses_disposable_file_backed_sqlite():
    # File-backed so sync_to_async executor threads share one database.
    assert connection.settings_dict["NAME"]
    assert settings.DATABASES["default"]["NAME"].endswith("test.sqlite3")
