"""Exercise checkpoint adoption and mismatches against real catalog metadata."""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase

from aimms_testing import requires_postgres
from assets.deploy_lineage import lineage, report
from legacy_boundary.checkpoint import (
    LEGACY_MIGRATIONS,
    BoundaryMismatchError,
    check_renderings,
    install_checkpoint,
    validate_recorded_boundary,
    verify_schema,
)


@requires_postgres
class LegacyCheckpointTests(TestCase):
    """Recognized names never substitute for a verified physical boundary."""

    def test_complete_boundary_is_recognized(self):
        """A cold-installed checkpoint has its real schema and recorded names."""
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertLessEqual(set(LEGACY_MIGRATIONS), set(applied))
        verify_schema(connection, require_focus=True)
        self.assertEqual(lineage().unknown, ())

    def test_partial_batch_is_refused(self):
        """Losing one historical record cannot silently select another graph."""
        MigrationRecorder(connection).record_unapplied(*LEGACY_MIGRATIONS[0])
        with self.assertRaisesRegex(BoundaryMismatchError, 'Partial legacy'):
            report(out=StringIO())

    def test_unrecorded_existing_tables_are_refused(self):
        """Fresh bootstrap cannot adopt tables that have no complete ledger."""
        for key in LEGACY_MIGRATIONS:
            MigrationRecorder(connection).record_unapplied(*key)
        with self.assertRaisesRegex(BoundaryMismatchError, 'Unrecorded legacy'):
            validate_recorded_boundary(connection)

    def test_changed_authority_default_is_refused(self):
        """A permissive default must not match the recorded deny-by-default field."""
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE users_ruleset ALTER COLUMN can_record_operating_authority SET DEFAULT true'
            )
        with self.assertRaisesRegex(BoundaryMismatchError, 'defaults differ'):
            report(out=StringIO())

    def test_missing_index_is_refused(self):
        """The guard verifies indexes as well as columns and migration names."""
        with connection.cursor() as cursor:
            cursor.execute('DROP INDEX tasks_workorder_corrective_step_id_ffccaf72')
        with self.assertRaisesRegex(BoundaryMismatchError, 'indexes differ'):
            verify_schema(connection)

    def test_extra_constraint_is_refused(self):
        """Unknown restrictions on archived records require a new review."""
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE tasks_investigationrequest ADD CONSTRAINT unexpected_boundary_rule CHECK (id > 0)'
            )
        with self.assertRaisesRegex(BoundaryMismatchError, 'constraints differ'):
            verify_schema(connection)

    def test_installer_refuses_existing_compatibility_tables(self):
        """Reapplying bootstrap never rewrites an existing authority record."""
        with self.assertRaisesRegex(BoundaryMismatchError, 'requires absent'):
            install_checkpoint(None, SimpleNamespace(connection=connection))

    def test_changed_checkpoint_artifact_is_refused(self):
        """Artifact integrity is checked before metadata drives acceptance."""
        with patch('legacy_boundary.checkpoint.MANIFEST_SHA256', 'invalid'):
            with self.assertRaisesRegex(BoundaryMismatchError, 'checksum mismatch'):
                verify_schema(connection)

    def test_equivalent_postgresql_enum_deparsing_is_accepted(self):
        """The observed live array-cast form preserves exactly the same rule."""
        definition = check_renderings()[
            'tasks_investigationrequest.tasks_inv_request_status'
        ][1]
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE tasks_investigationrequest DROP CONSTRAINT tasks_inv_request_status'
            )
            cursor.execute(
                f'ALTER TABLE tasks_investigationrequest ADD CONSTRAINT tasks_inv_request_status {definition}'
            )
        verify_schema(connection)

    def test_extra_enum_value_with_the_same_constraint_name_is_refused(self):
        """Canonicalization must never admit a new state or authority kind."""
        with connection.cursor() as cursor:
            cursor.execute(
                'ALTER TABLE tasks_investigationrequest DROP CONSTRAINT tasks_inv_request_status'
            )
            cursor.execute(
                "ALTER TABLE tasks_investigationrequest ADD CONSTRAINT tasks_inv_request_status CHECK (status IN ('open','answered','cannot_perform','pending_external','disputed','resolved','unexpected'))"
            )
        with self.assertRaisesRegex(BoundaryMismatchError, 'constraints differ'):
            verify_schema(connection)
