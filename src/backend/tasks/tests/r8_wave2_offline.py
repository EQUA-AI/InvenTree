"""Explicit private SQLite runtime fixture; never migration/deployment acceptance."""

import unittest

from django.test import TestCase
from django.test.runner import DiscoverRunner, iter_test_cases

from tasks.tests.r8_integration_offline import OfflineSchemaMixin

LABELS = (
    'tasks.tests.test_r8_fixture',
    'tasks.tests.test_r8_acceptance',
    'tasks.tests.test_procurement_integration',
    'tasks.tests.test_maintenance_receipts',
    'tasks.tests.test_procurement',
    'approvals.test_procurement_r8',
    'order.test_procurement_evidence',
    'tasks.tests.test_work_materials',
    'tasks.tests.test_job_kit_reservation',
    'tasks.tests.test_job_kit_custody',
    'tasks.tests.test_job_kit_reconcile',
    'tasks.tests.test_closeout_reconcile',
    'tasks.tests.test_workorder_closeout',
    'order.test_api.PurchaseOrderReceiveTest',
)


def load_tests(loader, tests, pattern):
    """Retain original assertions and install only the declared offline supplement."""
    suite = unittest.TestSuite()
    classes = {}
    seen = set()
    for test in iter_test_cases(DiscoverRunner().build_suite(LABELS)):
        if test.id() in seen:
            continue
        seen.add(test.id())
        base = type(test)
        if isinstance(test, TestCase):
            if base not in classes:
                classes[base] = type(
                    base.__name__, (OfflineSchemaMixin, base), {'__module__': __name__}
                )
            suite.addTest(classes[base](test._testMethodName))
        else:
            suite.addTest(test)
    return suite
