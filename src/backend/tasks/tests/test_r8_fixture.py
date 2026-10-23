"""R8 seed rehearsal uses isolated databases; the showcase remains bytewise intact."""

import io
import json
from contextlib import nullcontext
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.forms.models import model_to_dict
from django.test import TestCase, override_settings

from company.models import Company, SupplierPart
from part.models import Part
from stock.models import StockItem
from tasks.management.commands.seed_r8_procurement import (
    GROUP,
    MARKER,
    NAMESPACE,
    REFERENCE,
)
from tasks.models import JobKit, WorkOrder
from tasks.tests.closeout_fixtures import CloseoutEnvMixin


@override_settings(DEBUG=True)
class R8FixtureTests(CloseoutEnvMixin, TestCase):
    """Exercise the actual command with preexisting showcase stock and kit."""

    def setUp(self):
        """Only synthetic test rows impersonate the approved target identifiers."""
        self.build_env(username='r8-fixture-reviewer')
        self.actor.first_name = 'Fixture'
        self.actor.last_name = 'Reviewer'
        self.actor.save()
        self.machine.name = 'Cedar Creek Pump 08'
        self.machine.save()
        self.work_order.reference = 'WO-003826'
        self.work_order.save()
        self.line = self.build_kit_line()
        self.before = model_to_dict(self.work_order)
        self.stock_before = list(StockItem.objects.values())
        self.kit_before = list(JobKit.objects.values())

    def command(self, *args):
        """Use the connected private test DB, never a shared database."""
        from django.db import connection

        out = io.StringIO()
        # The real guard stays active in the dedicated local verification profiles.
        # Standard CI uses its own Django test DB name; only this configuration
        # guard is mocked there, while its refusal cases are exercised separately.
        private = (
            connection.vendor == 'sqlite'
            and str(connection.settings_dict['NAME']) == '/evidence/test.sqlite3'
        ) or (
            connection.vendor == 'postgresql'
            and connection.settings_dict['NAME'] == 'test_r8_integration'
            and connection.settings_dict['HOST'] == 'r8-pg'
        )
        guard = (
            nullcontext()
            if private
            else patch(
                'tasks.management.commands.seed_r8_procurement.require_local_database'
            )
        )
        with guard:
            call_command(
                'seed_r8_procurement',
                '--confirm-db',
                str(connection.settings_dict['NAME']),
                '--approver',
                self.actor.username,
                *args,
                stdout=out,
            )
        return json.loads(out.getvalue())

    def assert_showcase_unchanged(self):
        """Original requirement, stock and Work are not adopted or rewritten."""
        self.work_order.refresh_from_db()
        self.assertEqual(model_to_dict(self.work_order), self.before)
        self.assertEqual(list(StockItem.objects.values()), self.stock_before)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            list(JobKit.objects.filter(pk=self.line.kit_id).values()), self.kit_before
        )

    def test_dry_run_idempotence_provenance_remove_and_repeat(self):
        """Only new marked catalog/kit records and one role membership are added."""
        counts = (Company.objects.count(), Part.objects.count(), Group.objects.count())
        self.assertEqual(self.command('--dry-run')['state'], 'would_create')
        self.assertEqual(
            counts,
            (Company.objects.count(), Part.objects.count(), Group.objects.count()),
        )
        seeded = self.command()
        self.assertEqual(seeded['state'], 'created')
        self.assertEqual(self.command()['work_id'], seeded['work_id'])
        fixture = WorkOrder.objects.get(reference=REFERENCE)
        from tasks.services.procurement import ProcurementError, _validate_provenance

        self.assertIn(f'r8-fixture:{NAMESPACE}', fixture.tags)
        provenance = {
            'provenance': {'mode': 'recorded_fixture', 'fixture_namespace': NAMESPACE}
        }
        _validate_provenance(fixture, provenance)
        with self.assertRaises(ProcurementError):
            _validate_provenance(self.work_order, provenance)
        self.assertNotEqual(fixture.pk, self.work_order.pk)
        self.assertEqual(fixture.machine_id, self.machine.pk)
        offers = SupplierPart.objects.filter(pk__in=seeded['offer_ids'])
        self.assertTrue(all(o.metadata['r8_fixture'] == MARKER for o in offers))
        self.assertEqual(
            self.actor.groups
            .get(name=GROUP)
            .permissions.get(codename='review')
            .content_type.app_label,
            'approvals',
        )
        self.assertEqual(self.command('--remove', '--dry-run')['state'], 'would_remove')
        self.assertEqual(self.command('--remove')['state'], 'removed')
        self.assertEqual(self.command('--remove')['state'], 'absent')
        self.assertFalse(self.actor.groups.filter(name=GROUP).exists())
        self.assertEqual(
            counts,
            (Company.objects.count(), Part.objects.count(), Group.objects.count()),
        )
        self.assert_showcase_unchanged()

    def test_remove_refuses_stock_or_completed_activity_atomically(self):
        """No reset deletes even fixture stock or progressed Work."""
        seeded = self.command()
        StockItem.objects.create(part_id=seeded['part_id'], quantity=1)
        with self.assertRaises(CommandError):
            self.command('--remove')
        self.assertTrue(WorkOrder.objects.filter(pk=seeded['work_id']).exists())
        self.assertTrue(Group.objects.filter(name=GROUP).exists())

    def test_unmarked_collision_and_actor_change_are_not_adopted(self):
        """Matching display names never establish fixture ownership."""
        Company.objects.create(name='R8 FIXTURE supplier A', is_supplier=True)
        with self.assertRaises(CommandError):
            self.command()
        self.assertFalse(WorkOrder.objects.filter(reference=REFERENCE).exists())
        self.assert_showcase_unchanged()

    def test_local_guard_refuses_owner_database_remote_host_and_wrong_confirmation(
        self,
    ):
        """A matching confirmation cannot bypass the local dedicated DB allowlist."""
        from tasks.management.commands.seed_r8_procurement import require_local_database

        for host, name, confirm in [
            ('localhost', 'aimms_azure_copy', 'aimms_azure_copy'),
            ('shared.internal', 'r8_demo', 'r8_demo'),
            ('localhost', 'r8_demo', 'other'),
        ]:
            with (
                self.subTest(host=host, name=name),
                patch(
                    'tasks.management.commands.seed_r8_procurement.connection'
                ) as conn,
            ):
                conn.vendor = 'postgresql'
                conn.settings_dict = {'HOST': host, 'NAME': name}
                with self.assertRaises(CommandError):
                    require_local_database(confirm)

    def test_seed_and_remove_preserve_completed_showcase_closeout(self):
        """A completed real scenario is never reopened for representative procurement."""
        from django.utils import timezone

        from tasks.models import WorkOrderCloseout

        closeout = WorkOrderCloseout.objects.create(
            work_order=self.work_order,
            cause='Fixture preexisting cause',
            action='Fixture preexisting work',
            result='Fixture preexisting result',
            verification_summary='Fixture preexisting verification',
            completed_by=self.actor,
            completed_at=timezone.now(),
            content_hash='f' * 64,
        )
        before = model_to_dict(closeout)
        WorkOrder.objects.filter(pk=self.work_order.pk).update(
            lifecycle_status='completed'
        )
        self.work_order.refresh_from_db()
        self.before = model_to_dict(self.work_order)
        self.command()
        self.command('--remove')
        closeout.refresh_from_db()
        self.assertEqual(model_to_dict(closeout), before)
        self.assert_showcase_unchanged()

    @override_settings(AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_JOB_KITS_ENABLED=True)
    def test_preparation_is_readable_by_native_work_materials_without_mutations(self):
        """Seed output mounts the same A/B Materials contract with no fabricated progress."""
        from rest_framework.test import APIClient

        seeded = self.command()
        api = APIClient()
        api.force_authenticate(self.actor)
        response = api.get(f'/api/tasks/work-orders/{seeded["work_id"]}/materials/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['source'], 'job_kit')
        requirement = response.data['requirements'][0]
        self.assertEqual(requirement['part']['pk'], seeded['part_id'])
        self.assertEqual(requirement['fulfillment_mode'], 'reserve_consume')
        self.assertEqual(requirement['reservations'], [])
        self.assertEqual(len(response.data['procurement']['lines']), 1)
        self.assertIsNone(response.data['procurement']['lines'][0]['source_selection'])
        self.assertEqual(self.command('--remove')['state'], 'removed')
        self.assert_showcase_unchanged()

    def test_missing_person_profile_refuses_before_any_fixture_write(self):
        """A preparation command never repairs or fabricates the existing user's account."""
        self.actor.profile.delete()
        with self.assertRaisesMessage(CommandError, 'existing native user profile'):
            self.command()
        self.assertFalse(WorkOrder.objects.filter(reference=REFERENCE).exists())
        self.assertFalse(Group.objects.filter(name=GROUP).exists())
        self.assert_showcase_unchanged()
