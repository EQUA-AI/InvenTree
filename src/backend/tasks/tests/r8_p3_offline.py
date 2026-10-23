"""Explicit synthetic runtime schema supplement for the R8 P3 offline suite.

The legacy compatibility checkpoint omits active R6 columns on fresh SQLite.
This helper runs only in the network-none runner's disposable /evidence database.
It is not a deployment migration or migration acceptance test. Original test
classes and assertions run unchanged after the declared supplement.
"""

import unittest

from django.db import connection
from django.test import TestCase

LABELS = [
    'tasks.tests.test_maintenance_receipts',
    'tasks.tests.test_job_kit_reservation',
    'tasks.tests.test_job_kit_reconcile',
    'tasks.tests.test_jobkit_reservation_api',
    'tasks.tests.test_stock_allocation_concurrency',
    'tasks.tests.test_stock_allocation_accounting',
    'tasks.tests.test_job_kit_custody',
    'tasks.tests.test_work_materials',
    'order.test_api.PurchaseOrderReceiveTest',
]


class OfflineP3Schema(TestCase):
    """Supply only the R6 runtime columns absent from the SQLite checkpoint."""

    @classmethod
    def setUpClass(cls):
        """Refuse every database except the runner's disposable SQLite database."""
        if (
            connection.vendor != 'sqlite'
            or connection.settings_dict['NAME'] != '/evidence/test.sqlite3'
        ):
            raise RuntimeError(
                'P3 synthetic supplement requires the offline runner database'
            )
        from assets.record_models import ComponentFit
        from tasks.models import CloseoutCapture, WorkOrder
        from users.models import RuleSet

        added = []
        with connection.schema_editor() as editor:
            if (
                ComponentFit._meta.db_table
                not in connection.introspection.table_names()
            ):
                editor.create_model(ComponentFit)
                added.append(ComponentFit._meta.db_table + ' (table)')
            for model in (WorkOrder, CloseoutCapture, RuleSet):
                with connection.cursor() as cursor:
                    original = {
                        column.name
                        for column in connection.introspection.get_table_description(
                            cursor, model._meta.db_table
                        )
                    }
                for field in model._meta.local_fields:
                    # SQLite may rebuild the whole runtime table when adding a
                    # defaulted column. Re-read after every operation so fields
                    # included by that rebuild are not added twice.
                    with connection.cursor() as cursor:
                        columns = {
                            column.name
                            for column in connection.introspection.get_table_description(
                                cursor, model._meta.db_table
                            )
                        }
                    if field.column not in columns:
                        editor.add_field(model, field)
                with connection.cursor() as cursor:
                    final = {
                        column.name
                        for column in connection.introspection.get_table_description(
                            cursor, model._meta.db_table
                        )
                    }
                added.extend(
                    f'{model._meta.db_table}.{name}'
                    for name in sorted(final - original)
                )
        print(f'P3_SYNTHETIC_SCHEMA_SUPPLEMENT {added}', flush=True)
        super().setUpClass()

    def test_disposable_schema_is_complete(self):
        """Require the synthetic columns used by the unchanged behavioral suites."""
        with connection.cursor() as cursor:
            columns = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, 'tasks_workorder'
                )
            }
        self.assertIn('recurrence_of_id', columns)


def load_tests(loader, tests, pattern):
    """Wrap the declared unchanged suites after Django creates the test database."""
    return unittest.TestSuite([
        loader.loadTestsFromTestCase(OfflineP3Schema),
        loader.loadTestsFromNames(LABELS),
    ])
