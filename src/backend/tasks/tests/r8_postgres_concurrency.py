"""Explicit private-PostgreSQL fixture cleanup for unchanged concurrency tests.

The inherited checkpoint retains retired FK tables outside Django model state.
Standard TransactionTestCase flush cannot truncate their referenced active tables.
Use Django's full available_apps scope to request TRUNCATE CASCADE in this private
throwaway database only. No schema supplement, assertion changes or fake locks.
"""

import unittest

from django.apps import apps
from django.db import connection
from django.test.runner import DiscoverRunner, iter_test_cases

LABELS = (
    'tasks.tests.test_stock_allocation_concurrency.ConcurrentReservationTest',
    'order.test_api.TransferOrderSerialAllocateConcurrencyTest',
    'order.test_sales_order.SalesOrderAllocateStockConcurrencyTest',
    'users.test_role_rebuild.ConcurrentRoleRebuildTests',
)


class PrivatePostgresCleanup:
    """Keep all installed apps available and cascade only disposable test rows."""

    available_apps = [config.name for config in apps.get_app_configs()]

    @classmethod
    def setUpClass(cls):
        """Refuse every engine/database except this integration's throwaway DB."""
        if (
            connection.vendor != 'postgresql'
            or connection.settings_dict['NAME'] != 'test_r8_integration'
            or connection.settings_dict['HOST'] != 'r8-pg'
        ):
            raise RuntimeError(
                'Concurrency cleanup requires private test_r8_integration PostgreSQL'
            )
        super().setUpClass()


def load_tests(loader, tests, pattern):
    """Run the original assertions with full-app cascading test-row cleanup."""
    original = list(iter_test_cases(DiscoverRunner().build_suite(LABELS)))
    suite = unittest.TestSuite()
    for base in dict.fromkeys(type(test) for test in original):
        wrapped = type(
            base.__name__, (PrivatePostgresCleanup, base), {'__module__': __name__}
        )
        suite.addTests(loader.loadTestsFromTestCase(wrapped))
    return suite
