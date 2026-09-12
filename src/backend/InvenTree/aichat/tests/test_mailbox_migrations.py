"""Forward/reverse mailbox schema validation on a fresh test database."""

from django.conf import settings
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag('migration_test')
class MailboxMigrationTests(TransactionTestCase):
    """Legacy recovery records must survive both directions of the new schema."""

    def test_legacy_execution_identity_survives_schema_roundtrip(self):
        """New mailbox tables do not rewrite legacy approvals or operation keys."""
        executor = MigrationExecutor(connection)
        # The exact predecessor is read from the generated migration dependency.
        migration = executor.loader.get_migration(
            'aichat', '0036_connectedmailbox_mailattachment_mailconversation_and_more'
        )
        old = [
            dependency
            for dependency in migration.dependencies
            if dependency[0] == 'aichat'
        ]
        latest = executor.loader.graph.leaf_nodes()
        old += [target for target in latest if target[0] != 'aichat']
        try:
            executor.migrate(old)
            apps = executor.loader.project_state(old).apps
            user = apps.get_model(settings.AUTH_USER_MODEL).objects.create(
                username='migration-email-user'
            )
            Approval = apps.get_model('approvals', 'Approval')
            # approvals can be outside the target closure; use the loaded graph's current state.
            approval = Approval.objects.create(
                action_type='email',
                status='executing',
                summary='Legacy recording',
                payload={'to': 'recipient@example.test', 'subject': 'Legacy'},
                agent_run_id='legacy-run',
                tool_call_id='legacy-call',
                agent_checkpoint_id='legacy-checkpoint',
                idempotency_key='a' * 64,
            )
            Execution = apps.get_model('approvals', 'ApprovalExecution')
            Execution.objects.create(
                idempotency_key='a' * 64,
                approval=approval,
                actor=user,
                revision=0,
                review_hash='b' * 64,
                state='unknown',
            )
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            self.assertEqual(
                apps.get_model('approvals', 'ApprovalExecution')
                .objects.get(pk='a' * 64)
                .state,
                'unknown',
            )
            self.assertEqual(apps.get_model('aichat', 'MailDraft').objects.count(), 0)
            executor = MigrationExecutor(connection)
            executor.migrate(old)
            self.assertNotIn(
                'aichat_connectedmailbox', connection.introspection.table_names()
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT state FROM approvals_approvalexecution WHERE idempotency_key = %s',
                    ['a' * 64],
                )
                self.assertEqual(cursor.fetchone()[0], 'unknown')
        finally:
            MigrationExecutor(connection).migrate(latest)
