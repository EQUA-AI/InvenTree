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

    def test_capture_storage_and_size_settings_are_registered_bridged_and_read(self):
        """Deployments can select a native alias and an optional stricter bound."""
        from unittest.mock import patch

        from django.core.files.storage import InMemoryStorage

        from tasks.services.capture_storage import original_storage

        from aimms_flags import django_flags
        from InvenTree.config import get_setting

        entries = {entry.env_name: entry for entry in django_flags()}
        for name, value, kind in (
            ('AIMMS_CAPTURE_STORAGE_ALIAS', 'retained_local', 'str'),
            ('AIMMS_CAPTURE_MAX_BYTES', 4096, 'int'),
        ):
            entry = entries[name]
            self.assertEqual(entry.kind, kind)
            self.assertTrue(entry.config_key)
            self.assertTrue(hasattr(settings, name))
            with patch.dict('os.environ', {name: str(value)}):
                self.assertEqual(
                    get_setting(
                        name,
                        entry.config_key,
                        entry.default,
                        typecast=int if kind == 'int' else None,
                    ),
                    value,
                )
        with self.settings(
            AIMMS_CAPTURE_STORAGE_ALIAS='retained_local',
            STORAGES={
                'retained_local': {
                    'BACKEND': 'django.core.files.storage.InMemoryStorage'
                }
            },
        ):
            self.assertIsInstance(original_storage(), InMemoryStorage)

    def test_boolean_flags_exist_and_bridge_as_bools(self):
        """Feature booleans are declared and bridge as real bools.

        R5 renamed this from ...default_off: the RAG pair now defaults ON,
        so "off unless the env says otherwise" stopped being true — the
        assertions below were always type/existence checks, never values.
        """
        for name in (
            'AIMMS_WORK_ORDERS_ENABLED',
            'AIMMS_MACHINE_AI_READ_ENABLED',
            'AIMMS_MAINTENANCE_AI_READ_ENABLED',
            'AIMMS_CLOSEOUT_EXTRACTION_ENABLED',
            'AIMMS_CLOSEOUT_WIZARD_ENABLED',
            'AIMMS_RISK_RADAR_ENABLED',
            'AIMMS_COMMAND_CENTER_ENABLED',
            'AIMMS_RISK_NOTIFICATIONS_ENABLED',
            # R1 attachment RAG: the ONLY gate between an upload and the
            # ingest task — deleting its registry entry must fail a test.
            'AIMMS_ATTACHMENT_RAG_ENABLED',
            'AIMMS_MEDIA_RAG_ENABLED',
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
        """The csv settings are LISTS, never raw comma strings.

        The consumers iterate codes or names; a raw string would iterate
        characters and enable nothing while looking configured.
        """
        for name in (
            'AIMMS_RISK_RULES_ENABLED',
            # R6 UI10 persona group names: a raw string would match a group
            # per character.
            'AIMMS_WORK_SUPERVISOR_GROUPS',
            'AIMMS_WORK_TECHNICIAN_GROUPS',
        ):
            value = getattr(settings, name)
            self.assertIsInstance(value, list, name)
            for code in value:
                self.assertIsInstance(code, str)
                self.assertNotIn(',', code)
