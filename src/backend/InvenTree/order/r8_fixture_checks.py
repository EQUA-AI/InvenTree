"""Supplemented SQLite fixture checks, explicitly separate from migration tests.

The existing legacy_boundary replacement omits R6 Work/RuleSet runtime columns
on fresh SQLite. Add only the missing fields needed by the unchanged linkage
and permission tests, inside the offline runner's disposable database. This is
not proof that the production migration lineage supplies these structures.
"""

from unittest.mock import patch

from django.db import connection

from tasks import models as task_models
from tasks.tests import test_job_kit_reconcile

from approvals import test_voice_purchasing
from users.models import RuleSet


def setUpModule():
    """Refuse all databases except the offline runner's synthetic SQLite file."""
    if (
        connection.vendor != 'sqlite'
        or str(connection.settings_dict['NAME']) != '/evidence/test.sqlite3'
    ):
        raise RuntimeError(
            'Schema supplement is restricted to the offline synthetic database'
        )
    for model in (task_models.WorkOrder, RuleSet):
        for field in model._meta.local_fields:
            # SQLite may rebuild a table using all current fields during one
            # add_field; refresh the physical column list before every addition.
            with connection.cursor() as cursor:
                columns = {
                    column.name
                    for column in connection.introspection.get_table_description(
                        cursor, model._meta.db_table
                    )
                }
            if field.column not in columns:
                with connection.schema_editor() as editor:
                    editor.add_field(model, field)


class SupplementedJobKitLinkTests(test_job_kit_reconcile.ReconcileJobKitTest):
    """Run original reconciliation/link tests with explicit synthetic structures."""


class SupplementedVoicePurchasingTests(test_voice_purchasing.PurchasingVoiceTests):
    """Preserve existing named review/voice contracts on the synthetic schema."""

    def setUp(self):
        """The offline runner intentionally has no deployed single-site policy key."""
        from ai.core.config import get_settings

        fixture_settings = get_settings().model_copy(
            update={'single_site_policy_key': 'r8-fixture-policy'}
        )
        self.enterContext(
            patch('ai.core.config.get_settings', return_value=fixture_settings)
        )
        super().setUp()
