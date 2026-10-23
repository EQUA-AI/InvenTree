"""Explicit merged-lane SQLite schema fixture, never migration acceptance."""

import unittest
from unittest.mock import patch

from django.test import TestCase
from django.test.runner import DiscoverRunner, iter_test_cases

from tasks.tests.r8_p1_fixture import supplement_schema

_supplemented = False

LABELS = (
    'tasks',
    'order',
    'approvals',
    'users',
    'aichat.tests.test_email_contracts',
    'aichat.tests.test_flag_bridge',
)


class OfflineSchemaMixin:
    """Apply the existing guarded supplement before each test transaction."""

    @classmethod
    def setUpClass(cls):
        """Retain each lane's original assertions and test runner behavior."""
        global _supplemented
        if not _supplemented:
            supplement_schema()
            _supplemented = True
        from ai.core.config import get_settings

        cls.policy_patch = patch(
            'ai.core.config.get_settings',
            return_value=get_settings().model_copy(
                update={'single_site_policy_key': 'r8-fixture-policy'}
            ),
        )
        cls.policy_patch.start()
        cls.addClassCleanup(cls.policy_patch.stop)
        super().setUpClass()


def load_tests(loader, tests, pattern):
    """Run all requested apps together; deduplicate inherited lane test cases."""
    original = list(iter_test_cases(DiscoverRunner().build_suite(LABELS)))
    suite = unittest.TestSuite()
    wrapped_classes = {}
    seen = set()
    for test in original:
        if test.id() in seen:
            continue
        seen.add(test.id())
        base = type(test)
        if isinstance(test, TestCase):
            if base not in wrapped_classes:
                wrapped_classes[base] = type(
                    base.__name__, (OfflineSchemaMixin, base), {'__module__': __name__}
                )
            suite.addTest(wrapped_classes[base](test._testMethodName))
        else:
            # Preserve discovery/import failures and selected method identities.
            # Reloading _FailedTest's class would silently collect zero tests.
            suite.addTest(test)
    return suite
