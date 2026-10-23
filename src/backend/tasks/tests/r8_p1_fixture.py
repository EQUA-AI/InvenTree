"""Explicit offline-only schema fixture for the inherited SQLite checkpoint gap.

Run with backend_baseline.py and this label, never as a deployed migration.
The original unsupplemented failures remain recorded in P1's report. This suite
creates only missing runtime structures in the runner's private synthetic DB;
it neither edits migration history nor establishes deployment acceptance.
"""

import json
import unittest

from django.apps import apps
from django.db import connection

LABELS = (
    'tasks.tests.test_procurement',
    'tasks.tests.test_work_materials',
    'aichat.tests.test_flag_bridge',
    'tasks.tests.test_jobkit_api',
    'tasks.tests.test_job_kit_reservation',
    'tasks.tests.test_job_kit_custody',
    'tasks.tests.test_job_kit_reconcile',
)


def supplement_schema():
    """Refuse every DB except the offline runner's dedicated SQLite fixture."""
    if connection.vendor != 'sqlite' or str(connection.settings_dict['NAME']) != '/evidence/test.sqlite3':
        raise RuntimeError('R8 P1 schema fixture is restricted to backend_baseline.py SQLite')
    tables = set(connection.introspection.table_names())
    changes = []
    # These four apps own the inherited runtime structures this focused suite
    # touches. Existing columns, records, constraints and migrations are kept.
    models = [model for model in apps.get_models() if model._meta.app_label in {'tasks', 'assets', 'users', 'repair'} and model._meta.managed and not model._meta.proxy]
    with connection.schema_editor() as editor:
        for model in models:
            if model._meta.db_table not in tables:
                editor.create_model(model)
                tables.add(model._meta.db_table)
                changes.append({'table': model._meta.db_table})
    for model in models:
        with connection.cursor() as cursor:
            columns = {column.name for column in connection.introspection.get_table_description(cursor, model._meta.db_table)}
        for field in model._meta.local_fields:
            # SQLite may rebuild a table from the full runtime model while
            # adding a unique FK; that can also create later missing fields.
            with connection.cursor() as cursor:
                columns = {column.name for column in connection.introspection.get_table_description(cursor, model._meta.db_table)}
            if field.column not in columns:
                with connection.schema_editor() as editor:
                    editor.add_field(model, field)
                changes.append({'table': model._meta.db_table, 'column': field.column})
                columns.add(field.column)
    print('R8 P1 SYNTHETIC SCHEMA SUPPLEMENT: ' + json.dumps(changes), flush=True)


class OfflineSchemaMixin:
    """Install the fixture before Django opens each class's TestCase transaction."""

    @classmethod
    def setUpClass(cls):
        """Django flattens custom suites, so the guard belongs on test classes."""
        supplement_schema()
        super().setUpClass()


def load_tests(loader, tests, pattern):
    """Inherit unchanged methods; keep the production runner and assertions."""
    from django.test import TestCase
    from django.test.runner import DiscoverRunner, iter_test_cases

    original = list(iter_test_cases(DiscoverRunner().build_suite(LABELS)))
    classes = dict.fromkeys(type(test) for test in original)
    suite = unittest.TestSuite()
    for base in classes:
        wrapped = type(base.__name__, (OfflineSchemaMixin, base), {'__module__': __name__}) if issubclass(base, TestCase) else base
        suite.addTests(loader.loadTestsFromTestCase(wrapped))
    return suite
