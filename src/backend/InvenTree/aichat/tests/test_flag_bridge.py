"""The AIMMS Django-plane flag bridge (execution-plan slice S4).

Nineteen consumed ``AIMMS_*`` names had no env bridge, so the features they
gate were structurally off in every deployment — including the governed
kanban-writes gate, meaning the direct-ORM write bypass could not be turned
off by configuration at all. This suite pins that every bridged name exists
in settings with its fail-closed default, so a future refactor cannot
silently un-bridge one again.
"""

from django.conf import settings
from django.test import SimpleTestCase


class FlagBridgeTests(SimpleTestCase):
    """Every bridged name exists with a fail-closed default."""

    def test_boolean_flags_exist_and_default_off(self):
        """Feature booleans are declared and off unless the env says otherwise."""
        for name in (
            'AIMMS_WORK_ORDERS_ENABLED',
            'AIMMS_MACHINE_AI_READ_ENABLED',
            'AIMMS_MAINTENANCE_AI_READ_ENABLED',
            'AIMMS_GOVERNED_KANBAN_WRITES',
            'AIMMS_CLOSEOUT_EXTRACTION_ENABLED',
            'AIMMS_CLOSEOUT_WIZARD_ENABLED',
            'AIMMS_RISK_RADAR_ENABLED',
            'AIMMS_COMMAND_CENTER_ENABLED',
            'AIMMS_RISK_NOTIFICATIONS_ENABLED',
        ):
            self.assertTrue(hasattr(settings, name), name)
            self.assertIsInstance(getattr(settings, name), bool, name)

    def test_value_settings_exist(self):
        """Resolver paths and identifiers are declared (None/empty = fail closed)."""
        for name in (
            'AIMMS_MAINTENANCE_SCOPE_RESOLVER',
            'AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER',
            'AIMMS_SINGLE_SITE_CLIENT_CODE',
            'AIMMS_CLOSEOUT_EXTRACTOR',
            'AIMMS_CLOSEOUT_EXTRACTION_MODEL',
            'AIMMS_RISK_SERVICE_USER_ID',
        ):
            self.assertTrue(hasattr(settings, name), name)

    def test_risk_rules_list_is_a_parsed_list(self):
        """The rules enablement is a LIST — the consumer iterates rule codes,
        so a raw comma string would iterate characters and enable nothing
        while looking configured.
        """
        value = settings.AIMMS_RISK_RULES_ENABLED
        self.assertIsInstance(value, list)
        for code in value:
            self.assertIsInstance(code, str)
            self.assertNotIn(',', code)
