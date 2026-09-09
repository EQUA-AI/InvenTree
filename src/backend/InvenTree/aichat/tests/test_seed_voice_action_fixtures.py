"""P0-10: the VOICE-TEST seeder is idempotent, resettable and estate-guarded."""

from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from approvals.models import Approval
from stock.models import StockItem, StockLocation
from tasks.models import WorkOrder
from tasks.procedure_models import WorkOrderStepExecution


class SeedVoiceActionFixturesTests(TestCase):
    """Run the command against the test database."""

    def setUp(self):
        self.actor = get_user_model().objects.create_superuser(
            username='voice-test-actor', email='vt@example.com', password='pw'
        )
        self.db_name = settings.DATABASES['default']['NAME']

    def _run(self, *extra):
        out = StringIO()
        call_command(
            'seed_voice_action_fixtures',
            '--actor',
            'voice-test-actor',
            '--confirm-db',
            self.db_name,
            *extra,
            stdout=out,
        )
        return out.getvalue()

    def test_seeds_and_is_idempotent(self):
        first = self._run()
        self.assertIn('VOICE-TEST fixtures ready', first)
        self.assertEqual(WorkOrder.objects.filter(title__startswith='VOICE-TEST').count(), 5)
        self.assertEqual(Approval.objects.filter(summary__startswith='VOICE-TEST:').count(), 6)
        location = StockLocation.objects.get(name='VOICE-TEST')
        self.assertEqual(StockItem.objects.filter(location=location).count(), 2)
        procedure_wo = WorkOrder.objects.get(title='VOICE-TEST procedure candidate')
        self.assertEqual(
            WorkOrderStepExecution.objects.filter(
                application__work_order=procedure_wo
            ).count(),
            3,
        )
        self.assertEqual(
            set(
                WorkOrder.objects.filter(title__startswith='VOICE-TEST').values_list(
                    'lifecycle_status', flat=True
                )
            ),
            {'in_progress', 'on_hold', 'planned'},
        )

        self._run()
        self.assertEqual(WorkOrder.objects.filter(title__startswith='VOICE-TEST').count(), 5)
        self.assertEqual(Approval.objects.filter(summary__startswith='VOICE-TEST:').count(), 6)
        self.assertEqual(StockItem.objects.filter(location=location).count(), 2)

    def test_reset_restores_versions(self):
        self._run()
        hold_wo = WorkOrder.objects.get(title='VOICE-TEST hold candidate')
        hold_wo.lifecycle_status = 'on_hold'
        hold_wo.lifecycle_version += 3
        hold_wo.save(update_fields=['lifecycle_status', 'lifecycle_version'])
        approval = Approval.objects.filter(summary__startswith='VOICE-TEST:').first()
        approval.summary = 'VOICE-TEST: mutated'
        approval.save(update_fields=['summary'])

        out = self._run('--reset')
        self.assertIn('reset: removed', out)
        fresh = WorkOrder.objects.get(title='VOICE-TEST hold candidate')
        self.assertNotEqual(fresh.pk, hold_wo.pk)
        self.assertEqual(fresh.lifecycle_status, 'in_progress')
        self.assertEqual(fresh.lifecycle_version, 1)
        self.assertFalse(Approval.objects.filter(summary='VOICE-TEST: mutated').exists())
        self.assertEqual(Approval.objects.filter(summary__startswith='VOICE-TEST:').count(), 6)

    def test_refuses_a_mismatched_database_name(self):
        with self.assertRaises(CommandError):
            call_command(
                'seed_voice_action_fixtures',
                '--actor',
                'voice-test-actor',
                '--confirm-db',
                'not-the-configured-db',
                stdout=StringIO(),
            )

    def test_refuses_estate_databases_other_than_dev(self):
        databases = {
            'default': {
                **settings.DATABASES['default'],
                'NAME': 'inventree',
                'HOST': 'epconchat-pg-dev.postgres.database.azure.com',
            }
        }
        with override_settings(DATABASES=databases):
            with self.assertRaises(CommandError) as ctx:
                call_command(
                    'seed_voice_action_fixtures',
                    '--actor',
                    'voice-test-actor',
                    '--confirm-db',
                    'inventree',
                    stdout=StringIO(),
                )
        self.assertIn('inventree_dev', str(ctx.exception))

    def test_unknown_actor_is_an_error(self):
        with self.assertRaises(CommandError):
            call_command(
                'seed_voice_action_fixtures',
                '--actor',
                'nobody',
                '--confirm-db',
                self.db_name,
                stdout=StringIO(),
            )
