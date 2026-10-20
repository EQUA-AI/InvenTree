"""O15 real-ORM command, permission and projection regressions."""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from tasks.closeout_models import CloseoutPartUsage
from tasks.models import WorkOrder

from assets.models import (
    AssetMachine,
    Client,
    ClientScopeGrant,
    HealthSource,
    MachineSignalBinding,
)
from assets.record_models import ComponentFit, SignalDesignation
from assets.recorded_facts import promote_usage
from assets.registry_models import AssetComponent
from part.models import Part
from stock.models import StockItem
from users.models import RuleSet


@override_settings(
    CORS_ALLOW_ALL_ORIGINS=False,
    CORS_ALLOWED_ORIGINS=['http://localhost:15205'],
    CORS_ALLOWED_ORIGIN_REGEXES=[],
)
class RecordedCorsTests(SimpleTestCase):
    """Command resolution uses the existing authorized-origin policy."""

    def preflight(self, origin):
        """Exercise middleware without authenticating or executing a command."""
        return self.client.options(
            '/api/assets/recorded/18/critical-signals/',
            HTTP_ORIGIN=origin,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type,x-aimms-reconcile-only',
        )

    def test_allowed_origin_can_send_the_resolution_header(self):
        """A permitted remote frontend can resolve an uncertain operation."""
        response = self.preflight('http://localhost:15205')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Access-Control-Allow-Origin'], 'http://localhost:15205'
        )
        self.assertIn(
            'x-aimms-reconcile-only', response['Access-Control-Allow-Headers']
        )

    def test_resolution_header_does_not_expand_allowed_origins(self):
        """An unapproved origin still receives no cross-origin permission."""
        response = self.preflight('https://outside.invalid')
        self.assertNotIn('Access-Control-Allow-Origin', response)


@override_settings(
    USE_TZ=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
)
class RecordedFactsTests(TestCase):
    """No external service or installed fit is invented by an empty read."""

    def setUp(self):
        """Create isolated actors, scoped Work and catalogue records."""
        self.engineer = get_user_model().objects.create_superuser(
            'engineer', 'e@example.org', 'password'
        )
        self.other = get_user_model().objects.create_superuser(
            'verifier', 'v@example.org', 'password'
        )
        self.reader = get_user_model().objects.create_user('reader')
        group = Group.objects.create(name='record readers')
        self.reader.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group,
            name='work_order',
            defaults={'can_view': True, 'can_change': True},
        )
        RuleSet.objects.update_or_create(
            group=group, name='stock', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='Records', code='records')
        for actor in (self.engineer, self.other, self.reader):
            ClientScopeGrant.objects.create(user=actor, client=self.tenant)
        self.machine = AssetMachine.objects.create(name='Pump', client=self.tenant)
        self.foreign = AssetMachine.objects.create(
            name='Foreign', client=Client.objects.create(name='Other', code='other')
        )
        self.part = Part.objects.create(name='Bearing', active=True)
        self.slot = AssetComponent.objects.create(
            machine=self.machine, part=self.part, name='Bearing', code='BRG:1'
        )
        self.item = StockItem.objects.create(part=self.part, quantity=1, batch='LOT-17')
        self.source = HealthSource.objects.create(
            name='Manual fixture', source_type='manual'
        )
        self.binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=self.source,
            external_key='/st',
            display_name='State',
        )
        self.client.force_login(self.engineer)

    def endpoint(self, suffix):
        """Build the selected machine recording URL."""
        return f'/api/assets/recorded/{self.machine.pk}/{suffix}/'

    def command(self, suffix, operation, fields, version=0, key=None):
        """Post one exact versioned registry intent."""
        if suffix == 'critical-signals' and 'binding_signature' not in fields:
            from assets.recorded_facts import binding_signature

            binding = MachineSignalBinding.objects.get(pk=fields['binding_id'])
            fields = {**fields, 'binding_signature': binding_signature(binding)}
        return self.client.post(
            self.endpoint(suffix),
            {
                'operation': operation,
                'fields': fields,
                'expected_version': version,
                'idempotency_key': key or str(uuid.uuid4()),
            },
            content_type='application/json',
        )

    def test_manual_recorder_cannot_verify_their_own_fit(self):
        """Self-verification is refused without changing the pending record."""
        suffix = f'slots/{self.slot.pk}/fits'
        result = self.command(
            suffix,
            'record',
            {
                'stock_item': self.item.pk,
                'fitted_at': (timezone.now() - timedelta(days=1)).isoformat(),
                'reason': 'Existing fit evidence',
            },
        )
        fit_id = result.json()['pending'][0]['id']
        response = self.command(suffix, 'verify', {'fit_id': fit_id}, 1)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(ComponentFit.objects.get(pk=fit_id).verified_at)

    def test_consumption_cannot_assert_an_installed_identity(self):
        """Even attributed consumption is not an installed disposition."""
        work = WorkOrder.objects.create(
            title='Consumed',
            machine=self.machine,
            affected_component_ref=self.slot.code,
            actual_completed_at=timezone.now(),
        )
        row = CloseoutPartUsage.objects.create(
            work_order=work,
            part=self.part,
            stock_item=self.item,
            used_quantity=1,
            state='reconciled',
            disposition='consumed',
            resolved_by=self.engineer,
        )
        self.assertIsNone(promote_usage(row, self.engineer))
        self.assertFalse(ComponentFit.objects.exists())

    def test_late_older_installation_does_not_replace_current_fit(self):
        """Entry order cannot rewrite physical replacement order."""
        from tasks.services.closeout_reconcile import resolve_part_usage

        from assets.recorded_facts import fit_projection, fit_queryset

        now = timezone.now()
        current = ComponentFit.objects.create(
            component=self.slot,
            stock_item=self.item,
            fitted_at=now - timedelta(days=1),
            recorded_by=self.engineer,
            verified_by=self.other,
            verified_at=now,
            source='manual',
            reason='Newer fit',
        )
        work = WorkOrder.objects.create(
            title='Older Work',
            machine=self.machine,
            affected_component_ref=self.slot.code,
            actual_completed_at=now - timedelta(days=5),
        )
        row = CloseoutPartUsage.objects.create(
            work_order=work, part=self.part, stock_item=self.item, used_quantity=1
        )
        with override_settings(
            AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_CLOSEOUT_WIZARD_ENABLED=True
        ):
            resolve_part_usage(
                work_order_id=work.pk,
                row_id=row.pk,
                actor=self.engineer,
                disposition='spare_installed',
                used_quantity=1,
                reason='Late account',
            )
        result = fit_projection(fit_queryset().filter(component=self.slot))
        self.assertEqual(result['current']['id'], current.pk)
        late = ComponentFit.objects.get(usage=row)
        self.assertEqual(late.fitted_at, work.actual_completed_at)
        self.assertIsNone(late.verified_at)
        self.assertEqual(len(result['pending']), 1)

    def test_missing_completion_leaves_installed_identity_pending(self):
        """Recording time is not invented as installation/completion time."""
        from tasks.services.closeout_reconcile import resolve_part_usage

        work = WorkOrder.objects.create(
            title='Unknown completion',
            machine=self.machine,
            affected_component_ref=self.slot.code,
        )
        row = CloseoutPartUsage.objects.create(
            work_order=work, part=self.part, stock_item=self.item, used_quantity=1
        )
        with override_settings(
            AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_CLOSEOUT_WIZARD_ENABLED=True
        ):
            resolve_part_usage(
                work_order_id=work.pk,
                row_id=row.pk,
                actor=self.engineer,
                disposition='spare_installed',
                used_quantity=1,
                reason='Installed, date missing',
            )
        fit = ComponentFit.objects.get(usage=row)
        self.assertIsNone(fit.fitted_at)
        self.assertIsNone(fit.verified_by)
        self.assertIsNone(fit.verified_at)

    def test_pending_closeout_verification_advances_fit_projection_version(self):
        """Completion-supported promotion cannot keep a stale fit version."""
        from tasks.services.closeout_reconcile import resolve_part_usage

        from assets.recorded_facts import fit_projection, fit_queryset

        work = WorkOrder.objects.create(
            title='Completion later recorded',
            machine=self.machine,
            affected_component_ref=self.slot.code,
        )
        row = CloseoutPartUsage.objects.create(
            work_order=work, part=self.part, stock_item=self.item, used_quantity=1
        )
        with override_settings(
            AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_CLOSEOUT_WIZARD_ENABLED=True
        ):
            resolve_part_usage(
                work_order_id=work.pk,
                row_id=row.pk,
                actor=self.engineer,
                disposition='spare_installed',
                reason='Installed evidence, completion missing',
            )
            before = fit_projection(fit_queryset().filter(component=self.slot))
            self.assertEqual(before['version'], 1)
            self.assertIsNone(before['current'])
            work.actual_completed_at = timezone.now()
            work.save(update_fields=['actual_completed_at'])
            resolve_part_usage(
                work_order_id=work.pk,
                row_id=row.pk,
                actor=self.engineer,
                disposition='spare_installed',
                reason='Installed evidence rechecked after completion',
            )
            after = fit_projection(fit_queryset().filter(component=self.slot))
            self.assertEqual(after['version'], 2)
            self.assertEqual(after['current']['id'], before['pending'][0]['id'])

    def test_critical_requires_engineer_retains_versions_and_replays(self):
        """Critical requires engineer retains versions and replays."""
        fields = {
            'binding_id': self.binding.pk,
            'critical': True,
            'reason': 'Reviewed critical measurement',
        }
        self.client.force_login(self.reader)
        self.assertEqual(
            self.command('critical-signals', 'designate', fields).status_code, 403
        )
        self.client.force_login(self.engineer)
        first = self.command('critical-signals', 'designate', fields, key='one')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(
            self.command('critical-signals', 'designate', fields, key='one').json(),
            first.json(),
        )
        self.assertEqual(
            self.command('critical-signals', 'designate', fields).status_code, 409
        )
        fields['critical'] = False
        self.assertEqual(
            self.command('critical-signals', 'designate', fields, 1).status_code, 200
        )
        self.assertEqual(SignalDesignation.objects.count(), 2)
        self.assertTrue(self.binding.designations.last().critical)

    def test_unmarked_and_current_designation_in_health_projection(self):
        """Unmarked and current designation in health projection."""
        from machine_health.services.summary import signal_rows

        self.assertIsNone(signal_rows(self.machine)[0]['critical_designation'])
        self.command(
            'critical-signals',
            'designate',
            {'binding_id': self.binding.pk, 'critical': True, 'reason': 'Needed'},
        )
        self.assertTrue(
            signal_rows(self.machine)[0]['critical_designation']['critical']
        )
        self.assertEqual(signal_rows(self.machine)[0]['state'], 'unknown')

    def test_remapped_binding_requires_new_designation(self):
        """Remapped binding requires new designation."""
        from machine_health.services.summary import signal_rows

        self.command(
            'critical-signals',
            'designate',
            {
                'binding_id': self.binding.pk,
                'critical': True,
                'reason': 'Critical signal review',
            },
        )
        self.binding.external_key = 'different-meaning'
        self.binding.save()
        reviewed = signal_rows(self.machine)[0]['critical_designation']
        self.assertTrue(reviewed['critical'])
        self.assertTrue(reviewed['review_required'])
        self.assertFalse(reviewed['applies_current'])
        self.assertEqual(reviewed['recorded_by'], 'engineer')
        self.assertEqual(self.binding.designations.count(), 1)

    def test_manual_fit_is_pending_until_explicit_registry_verification(self):
        """Manual fit is pending until explicit registry verification."""
        suffix = f'slots/{self.slot.pk}/fits'
        fields = {
            'stock_item': self.item.pk,
            'fitted_at': (timezone.now() - timedelta(days=1)).isoformat(),
            'reason': 'Existing fit checked against lot label',
        }
        result = self.command(suffix, 'record', fields)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertIsNone(result.json()['current'])
        fit_id = result.json()['pending'][0]['id']
        self.client.force_login(self.other)
        result = self.command(suffix, 'verify', {'fit_id': fit_id}, 1)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()['current']['lot'], 'LOT-17')
        self.assertEqual(result.json()['current']['recorded_by'], 'engineer')
        self.assertEqual(result.json()['current']['verified_by'], 'verifier')

    def test_verification_rechecks_the_current_slot_catalogue_part(self):
        """A registry edit cannot promote an incompatible pending identity."""
        suffix = f'slots/{self.slot.pk}/fits'
        saved = self.command(
            suffix,
            'record',
            {
                'stock_item': self.item.pk,
                'fitted_at': (timezone.now() - timedelta(days=1)).isoformat(),
                'reason': 'Label checked before registry correction',
            },
        )
        fit_id = saved.json()['pending'][0]['id']
        self.slot.part = Part.objects.create(name='Corrected catalogue family')
        self.slot.save(update_fields=['part'])
        self.client.force_login(self.other)
        response = self.command(suffix, 'verify', {'fit_id': fit_id}, 1)
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(ComponentFit.objects.get(pk=fit_id).verified_at)

    def test_fit_rejects_wrong_catalogue_future_date_untracked_and_other_scope(self):
        """Fit rejects wrong catalogue future date untracked and other scope."""
        suffix = f'slots/{self.slot.pk}/fits'
        wrong = StockItem.objects.create(
            part=Part.objects.create(name='Wrong'), quantity=1, batch='OTHER'
        )
        fields = {
            'stock_item': wrong.pk,
            'fitted_at': timezone.now().isoformat(),
            'reason': 'Checked',
        }
        self.assertEqual(self.command(suffix, 'record', fields).status_code, 404)
        fields['stock_item'] = self.item.pk
        fields['fitted_at'] = '2026-10-18T09:00:00'
        self.assertEqual(self.command(suffix, 'record', fields).status_code, 400)
        fields['fitted_at'] = (timezone.now() + timedelta(days=1)).isoformat()
        self.assertEqual(self.command(suffix, 'record', fields).status_code, 400)
        self.client.force_login(self.reader)
        self.assertEqual(
            self.client.get(
                f'/api/assets/recorded/{self.foreign.pk}/critical-signals/'
            ).status_code,
            404,
        )

    def test_reconciled_exact_slot_fit_and_replacement_history(self):
        """Reconciled exact slot fit and replacement history."""
        work = WorkOrder.objects.create(
            title='Replace',
            machine=self.machine,
            affected_component_ref=self.slot.code,
            actual_completed_at=timezone.now(),
        )
        usage = CloseoutPartUsage.objects.create(
            work_order=work,
            part=self.part,
            stock_item=self.item,
            used_quantity=1,
            state='reconciled',
            disposition='spare_installed',
            resolved_by=self.engineer,
        )
        with __import__('django.db', fromlist=['transaction']).transaction.atomic():
            first = promote_usage(usage, self.engineer, installed_decision=True)
            self.assertEqual(
                promote_usage(usage, self.engineer, installed_decision=True).pk,
                first.pk,
            )
        self.assertEqual(ComponentFit.objects.count(), 1)
        work.affected_component_ref = 'OTHER'
        work.save()
        other = CloseoutPartUsage.objects.create(
            work_order=work,
            part=self.part,
            stock_item=self.item,
            used_quantity=1,
            state='reconciled',
            disposition='consumed',
        )
        with __import__('django.db', fromlist=['transaction']).transaction.atomic():
            self.assertIsNone(promote_usage(other, self.engineer))

    def test_shared_creator_ownership_defaults_and_machine_scope(self):
        """Shared creator ownership defaults and machine scope."""
        self.client.force_login(self.reader)
        result = self.command(
            'performance-views',
            'create',
            {
                'name': 'Shared channels',
                'config': {
                    'search': '',
                    'section': 'all',
                    'bindings': [self.binding.pk],
                },
            },
        )
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(
            self.command(
                'performance-views',
                'curate',
                {'id': result.json()['id'], 'is_default': True},
                1,
            ).status_code,
            403,
        )
        self.client.force_login(self.engineer)
        self.assertEqual(
            self.command(
                'performance-views', 'delete', {'id': result.json()['id']}, 1
            ).status_code,
            403,
        )
        curated = self.command(
            'performance-views',
            'curate',
            {'id': result.json()['id'], 'is_default': True},
            1,
        )
        self.assertEqual(curated.status_code, 200, curated.content)
        self.client.force_login(self.reader)
        self.assertTrue(
            self.client.get(self.endpoint('performance-views')).json()['results'][0][
                'is_default'
            ]
        )
        self.assertEqual(
            self.command(
                'performance-views', 'delete', {'id': result.json()['id']}, 2
            ).status_code,
            403,
        )
        self.client.force_login(self.engineer)
        self.assertEqual(
            self.command(
                'performance-views',
                'curate',
                {'id': result.json()['id'], 'is_default': False},
                2,
            ).status_code,
            200,
        )
        self.client.force_login(self.reader)
        self.assertEqual(
            self.command(
                'performance-views', 'delete', {'id': result.json()['id']}, 3
            ).status_code,
            200,
        )

    def test_shared_config_cannot_include_foreign_binding(self):
        """Shared config cannot include foreign binding."""
        binding = MachineSignalBinding.objects.create(
            machine=self.foreign,
            source=self.source,
            external_key='other',
            display_name='Other',
        )
        result = self.command(
            'performance-views',
            'create',
            {
                'name': 'Foreign',
                'config': {'search': '', 'section': 'all', 'bindings': [binding.pk]},
            },
        )
        self.assertEqual(result.status_code, 400)

    @override_settings(
        AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_CLOSEOUT_WIZARD_ENABLED=True
    )
    def test_closeout_reconciliation_records_fit_then_retains_retracted_original(self):
        """Closeout reconciliation records fit then retains retracted original."""
        from tasks.services.closeout_reconcile import resolve_part_usage

        work = WorkOrder.objects.create(
            actual_completed_at=timezone.now(),
            title='Installed reconciliation',
            machine=self.machine,
            affected_component_ref=self.slot.code,
        )
        row = CloseoutPartUsage.objects.create(
            work_order=work, part=self.part, stock_item=self.item, used_quantity=1
        )
        resolve_part_usage(
            work_order_id=work.pk,
            row_id=row.pk,
            actor=self.engineer,
            disposition='spare_installed',
            used_quantity=1,
            reason='Installed lot checked',
            expected_row_version=1,
        )
        self.assertEqual(
            ComponentFit.objects.get(usage=row).stock_item_id, self.item.pk
        )
        from tasks.materials_readout import material_readout

        projected = material_readout(self.engineer, work)['unmatched_usage'][0][
            'fitted_record'
        ]
        self.assertEqual(projected['lot'], 'LOT-17')
        self.assertEqual(projected['recorded_by'], 'engineer')
        resolve_part_usage(
            work_order_id=work.pk,
            row_id=row.pk,
            actor=self.engineer,
            disposition='returned',
            used_quantity=0,
            reason='Corrected installation claim; returned unused',
            expected_row_version=2,
        )
        fit = ComponentFit.objects.get(usage=row)
        self.assertIsNotNone(fit.retracted_at)
        self.assertEqual(fit.retracted_by, self.engineer)
        resolve_part_usage(
            work_order_id=work.pk,
            row_id=row.pk,
            actor=self.engineer,
            disposition='spare_installed',
            used_quantity=1,
            reason='Usage corrected again; physical fit still requires verification',
            expected_row_version=3,
        )
        row.refresh_from_db()
        fit.refresh_from_db()
        self.assertEqual(row.disposition, 'spare_installed')
        self.assertIsNotNone(fit.retracted_at)
        self.assertEqual(ComponentFit.objects.filter(usage=row).count(), 1)

    def test_deactivation_preserves_binding_decision_history(self):
        """Deactivation preserves binding decision history."""
        from assets.activation import _retire_bindings

        self.command(
            'critical-signals',
            'designate',
            {'binding_id': self.binding.pk, 'critical': True, 'reason': 'Reviewed'},
        )
        _retire_bindings(MachineSignalBinding.objects.filter(pk=self.binding.pk))
        self.binding.refresh_from_db()
        self.assertFalse(self.binding.active)
        from machine_health.services.summary import signal_rows

        rows = signal_rows(self.machine)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['critical_designation']['critical'])
        self.assertEqual(rows[0]['state'], 'unknown')
        self.assertTrue(rows[0]['stale'])
        self.assertEqual(self.binding.designations.count(), 1)

    def test_designation_rejects_a_binding_changed_during_engineer_review(self):
        """A designation cannot silently attach to another measurement meaning."""
        from assets.recorded_facts import binding_signature

        signature = binding_signature(self.binding)
        self.binding.external_key = 'changed-measurement'
        self.binding.save()
        response = self.command(
            'critical-signals',
            'designate',
            {
                'binding_id': self.binding.pk,
                'binding_signature': signature,
                'critical': True,
                'reason': 'Review began before remapping',
            },
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SignalDesignation.objects.count(), 0)

    @override_settings(
        AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_CLOSEOUT_WIZARD_ENABLED=True
    )
    def test_untracked_usage_does_not_invent_fit_or_block_native_reconciliation(self):
        """Native usage remains reconciled when fitted serial/lot is unavailable."""
        from tasks.services.closeout_reconcile import resolve_part_usage

        self.item.batch = ''
        self.item.save(update_fields=['batch'])
        work = WorkOrder.objects.create(
            title='Untracked usage',
            machine=self.machine,
            affected_component_ref=self.slot.code,
        )
        row = CloseoutPartUsage.objects.create(
            work_order=work, part=self.part, stock_item=self.item, used_quantity=1
        )
        result = resolve_part_usage(
            work_order_id=work.pk,
            row_id=row.pk,
            actor=self.engineer,
            disposition='spare_installed',
            used_quantity=1,
            reason='Native usage, identity not tracked',
        )
        self.assertEqual(result.state, 'reconciled')
        self.assertEqual(ComponentFit.objects.count(), 0)

    def test_registry_absence_resolution_is_a_terminal_receipt(self):
        """Resolving an absent intent cannot later execute it under that key."""
        from assets.recorded_facts import binding_signature

        body = {
            'operation': 'designate',
            'fields': {
                'binding_id': self.binding.pk,
                'binding_signature': binding_signature(self.binding),
                'critical': True,
                'reason': 'Reviewed',
            },
            'expected_version': 0,
            'idempotency_key': 'absent',
        }
        resolved = self.client.post(
            self.endpoint('critical-signals'),
            body,
            content_type='application/json',
            HTTP_X_AIMMS_RECONCILE_ONLY='1',
        )
        self.assertEqual(resolved.status_code, 200, resolved.content)
        self.assertEqual(resolved.json()['record_command_outcome'], 'not_committed')
        replay = self.client.post(
            self.endpoint('critical-signals'), body, content_type='application/json'
        )
        self.assertEqual(replay.json(), resolved.json())
        self.assertEqual(SignalDesignation.objects.count(), 0)

    def test_manual_fit_requires_an_independent_registry_reviewer(self):
        """The recorder is refused; a different authorized reviewer verifies the entry."""
        suffix = f'slots/{self.slot.pk}/fits'
        result = self.command(
            suffix,
            'record',
            {
                'stock_item': self.item.pk,
                'fitted_at': (timezone.now() - timedelta(days=1)).isoformat(),
                'reason': 'Existing label checked',
            },
        )
        fit_id = result.json()['pending'][0]['id']
        refused = self.command(suffix, 'verify', {'fit_id': fit_id}, 1)
        self.assertEqual(refused.status_code, 400)
        self.client.force_login(self.other)
        verified = self.command(suffix, 'verify', {'fit_id': fit_id}, 1)
        self.assertEqual(verified.status_code, 200, verified.content)
        self.assertEqual(verified.json()['current']['recorded_by'], 'engineer')
        self.assertEqual(verified.json()['current']['verified_by'], 'verifier')


class RecordedMigrationTests(SimpleTestCase):
    """Validate additive local model state without creating a database."""

    def test_model_migration_state_matches(self):
        """The reserved migrations account for the new recording models."""
        from django.apps import apps
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.state import ProjectState

        loader = MigrationLoader(None)
        changes = MigrationAutodetector(
            loader.project_state(), ProjectState.from_apps(apps)
        ).changes(graph=loader.graph, trim_to_apps={'assets', 'tasks'})
        self.assertFalse(
            changes,
            {
                app: [
                    operation.describe()
                    for migration in migrations
                    for operation in migration.operations
                ]
                for app, migrations in changes.items()
            },
        )
