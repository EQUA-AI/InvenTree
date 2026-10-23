"""Lane B focused fixtures with the declared legacy SQLite supplement."""

import unittest

from django.test.runner import DiscoverRunner, iter_test_cases

from tasks.tests.r8_integration_offline import OfflineSchemaMixin

LABELS = (
    'tasks.tests.test_maintenance_receipts',
    'tasks.tests.test_closeout_reconcile',
    'tasks.tests.test_job_kit_custody',
    'tasks.tests.test_work_materials',
    'tasks.tests.test_closeout_completion_integration',
    'tasks.tests.test_workorder_closeout',
    'tasks.tests.test_closeout_update_contract',
    'order.test_api.PurchaseOrderReceiveTest',
)


def load_tests(loader, tests, pattern):
    """Keep original assertions, wrap only the explicit disposable fixture."""
    suite = unittest.TestSuite()
    wrapped = {}
    for test in iter_test_cases(DiscoverRunner().build_suite(LABELS)):
        base = type(test)
        if base not in wrapped:
            wrapped[base] = type(
                base.__name__, (OfflineSchemaMixin, base), {'__module__': __name__}
            )
        suite.addTest(wrapped[base](test._testMethodName))
    return suite
