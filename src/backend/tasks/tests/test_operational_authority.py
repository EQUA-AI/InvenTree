"""O15 offline command/projection, authorization, replay and expiry regressions."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from assets.health_models import HealthSource, MachineSignalBinding, MachineSignalState
from assets.models import AssetMachine, DictionaryPoint, get_default_client
from InvenTree.unit_test import InvenTreeAPITestCase
from repair.authority_models import GateAuthorityRecord
from repair.investigation import approve_repair_scope
from repair.models import RepairPacket, RepairPacketGate
from tasks.authority_models import OperationalAuthorityRecord
from tasks.models import WorkOrder, WorkOrderCommand
from tasks.services.operational_authority import _context, command, project
from tasks.services.work_orders import IdempotencyConflict, StaleVersion
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_WORK_ORDERS_ENABLED=True,
)
class OperationalAuthorityTests(InvenTreeAPITestCase):
    """All records are synthetic and live only in disposable offline SQLite."""

    roles = 'all'

    def setUp(self):
        """Native Work change role and explicit maintenance/operations grants."""
        super().setUp()
        self.assignRole('work_order.change')
        self.user.user_permissions.add(
            *Permission.objects.filter(
                codename__in=('execute_workorder', 'record_operating_authority')
            )
        )
        self.station = AssetMachine.objects.create(
            name='Authority fixture station',
            client=get_default_client(),
            asset_type='pumphouse',
            source_namespace='authority',
            source_entity_uuid=uuid4(),
        )
        self.machine = AssetMachine.objects.create(
            name='Authority fixture',
            client=get_default_client(),
            asset_type='pump',
            parent=self.station,
            source_key='PUMP1',
        )
        self.work = WorkOrder.objects.create(
            title='Authority fixture', machine=self.machine
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine, work_order=self.work
        )
        self.gate = RepairPacketGate.objects.create(
            packet=self.packet,
            name='Existing gate',
            gate_type='isolation',
            status='confirmed',
            requires_second_person=True,
        )
        self.now = timezone.now() - timedelta(minutes=1)
        self.fields = {
            'kind': 'isolation',
            'outcome': 'active',
            'gate_id': self.gate.pk,
            'statement': 'Synthetic isolation point X applied',
            'source_reference': 'Synthetic site record X',
            'observed_at': self.now.isoformat(),
            'effective_from': self.now.isoformat(),
            'effective_until': (self.now + timedelta(hours=1)).isoformat(),
        }

    def apply(self, fields=None, key='record', **kwargs):
        """Use the current Work version unless the case deliberately fixes it."""
        self.work.refresh_from_db()
        if 'expected_context' not in kwargs:
            kwargs['expected_context'] = _context(self.work)[1]
        return command(
            work_order_id=self.work.pk,
            actor=kwargs.pop('actor', self.user),
            expected_version=kwargs.pop(
                'expected_version', self.work.lifecycle_version
            ),
            expected_context=kwargs.pop('expected_context'),
            idempotency_key=key,
            operation=kwargs.pop('operation', 'record'),
            fields=self.fields if fields is None else fields,
            **kwargs,
        )

    def operating(self, kind='operating_permission', outcome='granted'):
        """Separate operating intent with its own source/time."""
        return {
            **{key: value for key, value in self.fields.items() if key != 'gate_id'},
            'kind': kind,
            'outcome': outcome,
            'statement': 'Synthetic operating fact',
            'target_id': self.machine.pk,
        }

    def test_append_only_correction_replay_and_no_gate_or_lifecycle_inference(self):
        """An exact replay returns the same durable receipt after later corrections."""
        before = self.work.lifecycle_version
        result = self.apply(expected_version=before)
        row = GateAuthorityRecord.objects.get(pk=result['record']['id'])
        correction = self.apply(
            {**self.fields, 'statement': 'Corrected point'},
            key='correction',
            supersedes=row.pk,
        )
        self.assertEqual(self.apply(expected_version=before), result)
        self.assertEqual(GateAuthorityRecord.objects.count(), 2)
        row.refresh_from_db()
        self.assertEqual(row.statement, self.fields['statement'])
        self.assertEqual(correction['record']['supersedes'], row.pk)
        self.assertEqual(self.work.lifecycle_status, 'draft')
        self.assertEqual(self.gate.status, 'confirmed')
        self.assertFalse(self.gate.is_satisfied)
        with self.assertRaises(ValueError):
            row.save()
        with self.assertRaises(ValueError):
            row.delete()
        with self.assertRaises(ValueError):
            GateAuthorityRecord.objects.filter(pk=row.pk).update(statement='Rewrite')
        with self.assertRaises(ValueError):
            GateAuthorityRecord.objects.filter(pk=row.pk).delete()

    def test_stale_and_reused_intent_are_atomic(self):
        """A changed expected version or key payload never appends a fact."""
        before = self.work.lifecycle_version
        self.apply()
        with self.assertRaises(StaleVersion):
            self.apply(key='stale', expected_version=before)
        with self.assertRaises(IdempotencyConflict):
            self.apply(
                {**self.fields, 'statement': 'Changed payload'}, expected_version=before
            )
        self.assertEqual(GateAuthorityRecord.objects.count(), 1)
        self.assertEqual(
            WorkOrderCommand.objects.filter(command='operational_authority').count(), 1
        )

    def test_independent_verification_appends_and_expired_authority_blocks_gate(self):
        """The recorder cannot self-verify; expiry remains blocking after verification."""
        result = self.apply()
        verify = {'operation': 'verify', 'supersedes': result['record']['id']}
        with self.assertRaises(ValidationError):
            self.apply({'gate_id': self.gate.pk}, key='self', **verify)
        other = get_user_model().objects.create_user(
            username='second-person', is_superuser=True
        )
        verified = self.apply(
            {'gate_id': self.gate.pk}, key='verify', actor=other, **verify
        )
        self.assertEqual(verified['record']['recorded_by_id'], self.user.pk)
        self.assertEqual(
            verified['record']['recorded_at'], result['record']['recorded_at']
        )
        self.assertEqual(verified['record']['verified_by'], 'second-person')
        self.gate.refresh_from_db()
        # Existing gate verification is still a separate native requirement.
        self.assertFalse(self.gate.is_satisfied)
        self.gate.verified_by = other
        self.gate.save()
        self.assertTrue(self.gate.is_satisfied)
        from unittest.mock import patch

        with patch(
            'django.utils.timezone.now', return_value=self.now + timedelta(hours=2)
        ):
            self.assertFalse(self.gate.is_satisfied)
            self.assertEqual(
                project(self.work, self.user)['physical'][0]['effective_state'],
                'expired',
            )

    def test_maintenance_grant_cannot_record_operations(self):
        """Native edit and maintenance execution do not grant operating authority."""
        self.user.user_permissions.remove(
            Permission.objects.get(codename='record_operating_authority')
        )
        with self.assertRaises(PermissionDenied):
            self.apply(self.operating())
        result = self.apply(self.operating('test_acceptance', 'accepted'))
        self.assertEqual(result['record']['kind'], 'test_acceptance')
        self.assertFalse(
            project(self.work, self.user)['commands']['operating_permission']
        )
        self.assertEqual(
            OperationalAuthorityRecord.objects.filter(
                kind='operating_permission'
            ).count(),
            0,
        )

    def test_operations_grant_cannot_accept_maintenance_tests(self):
        """Operations and maintenance records have independent named grants."""
        self.user.user_permissions.remove(
            Permission.objects.get(codename='execute_workorder')
        )
        with self.assertRaises(PermissionDenied):
            self.apply(self.operating('test_acceptance', 'accepted'))
        self.apply(self.operating())
        self.assertFalse(project(self.work, self.user)['commands']['test_acceptance'])

    def test_revoked_role_or_gate_permission_refuses_exact_replay(self):
        """Current authorization is required before looking up any stored receipt."""
        before = self.work.lifecycle_version
        self.apply()
        self.gate.required_permission = 'tasks.complete_workorder'
        self.gate.save()
        with self.assertRaises(PermissionDenied):
            self.apply(expected_version=before)

    def test_native_reader_and_inactive_actor_cannot_write(self):
        """Record commands require current native view/change and active identity."""
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=False
        )
        with self.assertRaises(PermissionDenied):
            self.apply()
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=True
        )
        self.user.is_active = False
        self.user.save()
        with self.assertRaises(PermissionDenied):
            self.apply()

    def test_invalid_fields_missing_expiry_and_future_observation_refuse(self):
        """No default actor, permit validity or guessed observation is accepted."""
        for change in (
            {'recorded_by': self.user.pk},
            {'effective_until': None},
            {'outcome': 'healthy'},
            {'observed_at': (timezone.now() + timedelta(days=1)).isoformat()},
            {'effective_until': self.now.isoformat()},
        ):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.apply({**self.fields, **change})
        self.assertEqual(GateAuthorityRecord.objects.count(), 0)

    def test_test_permission_and_observed_return_stay_distinct(self):
        """An accepted test does not create permission; permission does not create observation."""
        self.apply(self.operating('test_acceptance', 'accepted'), key='test')
        data = project(self.work, self.user)
        self.assertEqual(
            [row['kind'] for row in data['operational']], ['test_acceptance']
        )
        self.apply(self.operating(), key='permission')
        self.assertFalse(
            OperationalAuthorityRecord.objects.filter(kind='observed_return').exists()
        )
        self.apply(
            self.operating('observed_return', 'observed_stopped'), key='observation'
        )
        self.assertEqual(OperationalAuthorityRecord.objects.count(), 3)

    def test_telemetry_snapshot_uses_its_clock_and_never_supplies_duty(self):
        """Zero/run context remains a reading; operator-supplied facts stay attributed."""
        source = HealthSource.objects.create(
            name='Synthetic read-only source', active=True
        )
        point = DictionaryPoint.objects.create(
            machine=self.machine,
            station=self.station,
            path='/st',
            raw_tag='st',
            display_name='Run status',
            status='approved',
        )
        binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key='/st',
            display_name='Run status',
            dictionary_point=point,
        )
        state = MachineSignalState.objects.create(
            binding=binding,
            value={'value': 0},
            observed_at=timezone.now(),
            quality='good',
            source_sequence=10,
        )
        data = project(self.work, self.user)
        self.assertEqual(
            data['telemetry'][0]['observed_at'], state.observed_at.isoformat()
        )
        self.assertEqual(data['telemetry'][0]['value'], {'value': 0})
        self.assertEqual(data['operational'], [])
        fields = {
            **self.operating('service_state', 'confirmed'),
            'duty': 'Synthetic duty recorded by operator',
            'binding_id': binding.pk,
            'telemetry_observed_at': state.observed_at.isoformat(),
            'telemetry_token': project(self.work, self.user)['telemetry'][0][
                'reading_token'
            ],
        }
        result = self.apply(fields)
        self.assertEqual(
            result['record']['basis']['telemetry_verification'],
            'reading_verified_current',
        )
        self.assertEqual(result['record']['basis']['telemetry']['value'], {'value': 0})
        self.assertNotEqual(result['record']['details']['duty'], '0')
        self.assertEqual(
            result['record']['basis']['telemetry']['observed_at'],
            state.observed_at.isoformat(),
        )
        self.assertIsNone(result['record']['details']['standby'])
        # A larger ingestion sequence can replace a value at the same source clock.
        state.value = {'value': 1}
        state.source_sequence = 11
        state.save()
        with self.assertRaises(StaleVersion):
            self.apply(fields, key='same-clock-different-reading')
        self.assertEqual(
            self.apply(fields, expected_version=result['work_version'] - 1), result
        )  # exact saved intent still replays
        state.observed_at += timedelta(seconds=1)
        state.save()
        with self.assertRaises(StaleVersion):
            self.apply(fields, key='stale-reading')

    def test_no_tag_or_unreviewed_tag_is_not_recorded_and_manual_fact_is_explicit(self):
        """No defaults or name matching turn absent tags into standby capacity."""
        self.assertEqual(project(self.work, self.user)['telemetry'], [])
        fields = {
            **self.operating('service_state', 'operator_recorded'),
            'standby': 'Synthetic unavailable standby',
        }
        self.apply(fields)
        row = OperationalAuthorityRecord.objects.get()
        self.assertIsNone(row.details['duty'])
        self.assertNotIn('telemetry', row.basis)

    def test_foreign_gate_target_and_correction_fail_closed(self):
        """Neither guessing another Work's gate nor its record grants access."""
        other = WorkOrder.objects.create(title='Other', machine=self.machine)
        packet = RepairPacket.objects.create(machine=self.machine, work_order=other)
        gate = RepairPacketGate.objects.create(packet=packet, name='Foreign')
        with self.assertRaises(ValidationError):
            self.apply({**self.fields, 'gate_id': gate.pk})
        with self.assertRaises(PermissionDenied):
            self.apply({**self.operating(), 'target_id': 999999})
        result = self.apply()
        with self.assertRaises(ValidationError):
            self.apply(
                {**self.fields, 'kind': 'permit'},
                key='wrong-kind',
                supersedes=result['record']['id'],
            )

    def test_overview_projections_are_read_only_and_show_attribution(self):
        """P21/P23 receive the recorded facts; a read never appends authority."""
        self.apply()
        self.apply(self.operating('test_acceptance', 'accepted'), key='test')
        count = WorkOrderCommand.objects.count()
        response = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk})
        )
        self.assertTrue(
            response.data['prepared_package']['physical_authority']['available']
        )
        self.assertTrue(
            response.data['verification_return']['technical_acceptance']['available']
        )
        self.assertFalse(
            response.data['verification_return']['operating_permission']['recorded']
        )
        self.assertEqual(
            response.data['operational_authority']['physical'][0]['recorded_by_id'],
            self.user.pk,
        )
        self.assertEqual(WorkOrderCommand.objects.count(), count)

    def test_api_requires_role_and_version_and_respects_feature_flag(self):
        """The actual API rejects incomplete requests and disabled writes."""
        url = reverse('work-operational-authority', kwargs={'pk': self.work.pk})
        self.post(url, {'fields': self.fields}, expected_code=400)
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=False
        )
        self.post(
            url,
            {
                'fields': self.fields,
                'expected_version': self.work.lifecycle_version,
                'expected_context': _context(self.work)[1],
                'idempotency_key': 'reader-post',
                'operation': 'record',
            },
            expected_code=403,
        )
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=True
        )
        with override_settings(AIMMS_WORK_ORDERS_ENABLED=False):
            self.get(url, expected_code=404)
            self.post(url, {}, expected_code=404)

    def test_migration_state_matches_models(self):
        """Reserved additive local migrations exactly describe the installed models."""
        call_command(
            'makemigrations',
            'tasks',
            'repair',
            check=True,
            dry_run=True,
            stdout=StringIO(),
            verbosity=0,
        )

    def test_packet_machine_and_work_drift_never_reuse_physical_authority(self):
        """Writable packet links cannot carry another asset's physical authority."""
        self.gate.requires_second_person = False
        self.gate.save()
        self.apply()
        self.assertTrue(self.gate.is_satisfied)
        other_machine = AssetMachine.objects.create(
            name='Other machine', client=get_default_client()
        )
        self.packet.machine = other_machine
        # Simulate legacy/out-of-band drift, bypassing the native owner guard.
        RepairPacket.objects.filter(pk=self.packet.pk).update(
            machine=self.packet.machine, work_order=self.packet.work_order
        )
        self.gate.refresh_from_db()
        self.assertFalse(self.gate.is_satisfied)
        value = project(WorkOrder.objects.get(pk=self.work.pk), self.user)
        self.assertEqual(value['gates'], [])
        self.packet.machine = self.machine
        self.packet.work_order = WorkOrder.objects.create(
            title='Other Work', machine=self.machine
        )
        # Simulate legacy/out-of-band drift, bypassing the native owner guard.
        RepairPacket.objects.filter(pk=self.packet.pk).update(
            machine=self.packet.machine, work_order=self.packet.work_order
        )
        self.gate.refresh_from_db()
        self.assertFalse(self.gate.is_satisfied)

    def test_authority_get_withholds_mismatched_foreign_packet_sources(self):
        """Standalone authority reads enforce the overview's packet boundary."""
        from assets.models import Client
        from company.models import Company

        scope = approve_repair_scope(
            self.packet, scope_lines=['Original packet scope'], actor=self.user
        )
        self.apply()
        self.work.refresh_from_db()
        original_version = self.work.lifecycle_version
        original_context = _context(self.work)[1]
        operating = self.operating()
        receipt = self.apply(
            operating,
            key='operating-scope',
            expected_version=original_version,
            expected_context=original_context,
        )
        url = reverse('work-operational-authority', kwargs={'pk': self.work.pk})
        own = self.get(url).data
        self.assertEqual([gate['id'] for gate in own['gates']], [self.gate.pk])
        self.assertEqual(len(own['physical']), 1)
        self.assertEqual(own['operational'][0]['basis']['scope_ids'], [scope.pk])

        self.work.customer = Company.objects.create(
            name='O24 authority customer', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        foreign = AssetMachine.objects.create(
            name='Foreign packet machine',
            client=Client.objects.create(
                name='Foreign authority', code='o24-authority'
            ),
        )
        # Native writers refuse this drift; the reader must also contain
        # historical/out-of-band mismatches without trusting shared Work.
        RepairPacket.objects.filter(pk=self.packet.pk).update(machine=foreign)
        self.gate.name = 'Foreign packet gate secret'
        self.gate.save(update_fields=['name'])
        shared = self.get(url).data
        self.assertEqual(shared['gates'], [])
        self.assertEqual(shared['physical'], [])
        self.assertEqual(shared['operational'][0]['basis']['scope_ids'], [])
        self.assertNotIn(self.gate.name, str(shared))
        self.assertNotEqual(shared['context_token'], own['context_token'])
        overview = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk})
        ).data
        self.assertEqual(
            overview['verification_return']['operating_permission']['records'][0][
                'basis'
            ]['scope_ids'],
            [],
        )
        replay = self.apply(
            operating,
            key='operating-scope',
            expected_version=original_version,
            expected_context=original_context,
        )
        self.assertEqual(
            replay,
            {
                **receipt,
                'record': {
                    **receipt['record'],
                    'basis': {**receipt['record']['basis'], 'scope_ids': []},
                },
            },
        )
        # An inaccessible gate cannot influence the reviewed command token.
        self.gate.name = 'Another foreign packet gate secret'
        self.gate.save(update_fields=['name'])
        self.assertEqual(self.get(url).data['context_token'], shared['context_token'])
        event = self.work.events.get(idempotency_key='operating-scope')
        self.assertEqual(event.metadata['result_metadata'], receipt)
        self.assertEqual(receipt['record']['basis']['scope_ids'], [scope.pk])
        self.user.user_permissions.add(
            Permission.objects.get(codename='view_workorder_audit')
        )
        events = self.get(
            reverse('work-order-events', kwargs={'pk': self.work.pk})
        ).data['results']
        self.assertTrue(events)
        self.assertTrue(
            all('result_metadata' not in event['metadata'] for event in events)
        )
        intent = {
            'operation': 'record',
            'expected_version': shared['work_version'],
            'idempotency_key': 'foreign-packet-context',
            'fields': operating,
        }
        # A caller cannot reconstruct the unkeyed source hash to promote a
        # foreign packet into the saved basis of a new operating fact.
        self.post(
            url,
            {
                **intent,
                'expected_context': _context(WorkOrder.objects.get(pk=self.work.pk))[1],
            },
            expected_code=409,
        )
        fresh = self.post(
            url,
            {
                **intent,
                'idempotency_key': 'own-target-context',
                'expected_context': shared['context_token'],
            },
            expected_code=200,
        )
        self.assertEqual(fresh.data['record']['basis']['scope_ids'], [])

    def test_expired_record_cannot_be_cleared_by_waiving_gate(self):
        """A gate waiver does not erase a separately recorded expired permit."""
        fields = {
            **self.fields,
            'effective_from': (self.now - timedelta(hours=2)).isoformat(),
            'effective_until': (self.now - timedelta(hours=1)).isoformat(),
        }
        self.apply(fields)
        self.gate.status = 'waived'
        self.gate.waiver_reason = 'Synthetic waiver'
        self.gate.waiver_authority = 'Synthetic authority'
        self.gate.save()
        self.assertFalse(self.gate.is_satisfied)

    def test_feature_disabled_projection_offers_no_commands(self):
        """The read projection never offers a command its API would disable."""
        with override_settings(AIMMS_WORK_ORDERS_ENABLED=False):
            value = project(self.work, self.user)
        self.assertFalse(any(value['commands'].values()))
        self.assertFalse(value['gates'][0]['can_record'])

    def test_correction_of_verified_record_requires_new_independent_verification(self):
        """Verification never silently carries forward to changed physical facts."""
        first = self.apply()
        other = get_user_model().objects.create_user(
            username='verifier', is_superuser=True
        )
        verified = self.apply(
            {'gate_id': self.gate.pk},
            key='verify',
            operation='verify',
            supersedes=first['record']['id'],
            actor=other,
        )
        corrected = self.apply(
            {**self.fields, 'statement': 'Synthetic corrected point'},
            key='correction',
            supersedes=verified['record']['id'],
        )
        self.assertIsNone(corrected['record']['verified_by'])
        self.assertEqual(
            corrected['record']['effective_state'], 'awaiting_verification'
        )

    def test_retest_marks_acceptance_basis_changed_without_rewriting_acceptance(self):
        """A later failed test does not masquerade as the test that was accepted."""
        import uuid

        from tasks.models import (
            Procedure,
            ProcedureRevision,
            WorkOrderProcedureApplication,
            WorkOrderStepExecution,
        )

        procedure = Procedure.objects.create(
            code='AUTH-TEST', name='Synthetic test', created_by=self.user
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure,
            revision=1,
            work_order_type='corrective',
            created_by=self.user,
        )
        application = WorkOrderProcedureApplication.objects.create(
            work_order=self.work,
            revision=revision,
            snapshot={},
            snapshot_hash='a' * 64,
            applied_by=self.user,
            idempotency_key='authority-test',
        )
        execution = WorkOrderStepExecution.objects.create(
            application=application,
            step_key=uuid.uuid4(),
            sequence=1,
            step_snapshot={'step_type': 'verification'},
            status='completed',
            value={'number': '0'},
            passed=True,
        )
        result = self.apply(self.operating('test_acceptance', 'accepted'))
        self.assertEqual(result['record']['basis_state'], 'current')
        reviewed_context = _context(self.work)[1]
        execution.version += 1
        execution.status = 'failed'
        execution.passed = False
        execution.save()
        count = OperationalAuthorityRecord.objects.count()
        with self.assertRaises(StaleVersion):
            self.apply(
                self.operating('test_acceptance', 'accepted'),
                key='old-test-view',
                expected_context=reviewed_context,
            )
        self.assertEqual(OperationalAuthorityRecord.objects.count(), count)
        data = project(self.work, self.user)['operational'][0]
        self.assertEqual(data['basis_state'], 'changed_or_unrecorded')
        self.assertEqual(data['outcome'], 'accepted')
        self.assertTrue(data['basis']['tests'][0]['passed'])

    def test_foreign_and_unresolved_work_api_returns_no_records(self):
        """Read and command endpoints use the canonical client boundary."""
        from assets.models import Client

        foreign = Client.objects.create(
            name='Foreign authority client', code='authority-foreign'
        )
        for machine in (
            AssetMachine.objects.create(name='Foreign', client=foreign),
            AssetMachine.objects.create(name='Unresolved'),
        ):
            work = WorkOrder.objects.create(
                title='Hidden authority Work', machine=machine
            )
            url = reverse('work-operational-authority', kwargs={'pk': work.pk})
            self.get(url, expected_code=404)
            self.post(
                url,
                {
                    'operation': 'record',
                    'expected_version': 1,
                    'idempotency_key': 'foreign',
                    'fields': self.operating(),
                },
                expected_code=404,
            )

    def test_inactive_source_and_bad_reading_require_an_operator_correction(self):
        """Confirm never promotes unreliable telemetry to a current service fact."""
        source = HealthSource.objects.create(
            name='Inactive synthetic source', active=False
        )
        point = DictionaryPoint.objects.create(
            machine=self.machine,
            station=self.station,
            path='/st',
            raw_tag='st',
            display_name='Status',
            status='approved',
        )
        binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key='/st',
            display_name='Status',
            dictionary_point=point,
        )
        state = MachineSignalState.objects.create(
            binding=binding, value={'value': 'I'}, observed_at=self.now, quality='bad'
        )
        fields = {
            **self.operating('service_state', 'confirmed'),
            'duty': 'Operator supplied duty',
            'binding_id': binding.pk,
            'telemetry_observed_at': state.observed_at.isoformat(),
            'telemetry_token': project(self.work, self.user)['telemetry'][0][
                'reading_token'
            ],
        }
        with self.assertRaises(ValidationError):
            self.apply(fields)
        result = self.apply({**fields, 'outcome': 'corrected'}, key='corrected-source')
        self.assertEqual(result['record']['basis']['telemetry']['quality'], 'bad')
        self.assertFalse(result['record']['basis']['telemetry']['source_active'])

    def test_commands_are_not_offered_without_an_authorized_asset_target(self):
        """The projection and command service share the same target-scope boundary."""
        orphan = AssetMachine.objects.create(name='Unresolved authority target')
        work = WorkOrder.objects.create(title='Target unresolved', machine=orphan)
        packet = RepairPacket.objects.create(machine=orphan, work_order=work)
        RepairPacketGate.objects.create(
            packet=packet, name='Unresolved gate', gate_type='isolation'
        )
        value = project(work, self.user)
        self.assertEqual(value['targets'], [])
        self.assertFalse(any(value['commands'].values()))
        # O24: packet gates are machine-scoped; without the machine they are
        # not projected at all (formerly listed with can_record False).
        self.assertEqual(value['gates'], [])

    def test_http_record_replay_stale_and_revoked_grant(self):
        """Exercise the actual JSON writer, receipt, conflict and current-role denial."""
        url = reverse('work-operational-authority', kwargs={'pk': self.work.pk})
        payload = {
            'operation': 'record',
            'expected_version': self.work.lifecycle_version,
            'expected_context': _context(self.work)[1],
            'idempotency_key': 'http-authority',
            'fields': self.fields,
        }
        first = self.post(url, payload, expected_code=200)
        self.assertEqual(first.data['record']['recorded_by_id'], self.user.pk)
        again = self.post(url, payload, expected_code=200)
        self.assertEqual(again.data, first.data)
        self.assertEqual(GateAuthorityRecord.objects.count(), 1)
        self.post(url, {**payload, 'idempotency_key': 'http-stale'}, expected_code=409)
        self.user.user_permissions.remove(
            Permission.objects.get(codename='execute_workorder')
        )
        self.post(url, payload, expected_code=403)
        self.assertEqual(GateAuthorityRecord.objects.count(), 1)

    def test_work_customer_scope_never_substitutes_for_physical_asset_scope(self):
        """A sales claim about Work cannot authorize recording at another client asset.

        O24: the customer-only reader still sees the shared Work, but the
        packet, its gates and every physical record stay machine-scoped: they
        are withheld rather than listed as unrecordable.
        """
        from company.models import Company
        from tasks.scope import MaintenanceScope

        context = _context(self.work)[1]
        saved = self.apply(expected_context=context)
        station_fields = {
            **self.operating('service_state', 'operator_recorded'),
            'target_id': self.station.pk,
            'statement': 'Synthetic restricted station detail',
            'source_reference': 'Synthetic restricted station document',
            'duty': 'Synthetic operator duty',
        }
        self.apply(station_fields, key='station-fact')
        self.user.user_permissions.add(
            Permission.objects.get(codename='view_workorder_audit')
        )
        customer = Company.objects.create(
            name='Synthetic job customer', is_customer=True
        )
        self.work.customer = customer
        self.work.save()
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: [
                MaintenanceScope(customer_id=customer.pk, site_key=None)
            ]
        ):
            value = project(self.work, self.user)
            self.assertEqual(value['gates'], [])
            self.assertEqual(value['physical'], [])
            with self.assertRaises(PermissionDenied):
                self.apply(
                    expected_version=saved['work_version'] - 1, expected_context=context
                )
            with self.assertRaises(PermissionDenied):
                self.apply(key='new-customer-only')
            for name in ('kanban-card-overview', 'work-order-events'):
                response = self.get(reverse(name, kwargs={'pk': self.work.pk}))
                if name == 'kanban-card-overview':
                    self.assertIsNone(response.data['repair_packet'])
                for secret in (
                    self.fields['statement'],
                    self.fields['source_reference'],
                    station_fields['statement'],
                    station_fields['source_reference'],
                ):
                    self.assertNotIn(secret, str(response.data))
            # Packets always need their machine's client scope (O24).
            self.get(
                reverse('repair-packet-detail', kwargs={'pk': self.packet.pk}),
                expected_code=404,
            )
        # Generic response redaction cannot mutate durable receipts or exact replay.
        event = self.work.events.get(
            event_type='authority_record', idempotency_key='record'
        )
        self.assertEqual(event.metadata['result_metadata'], saved)
        from tasks.workorder_serializers import WorkOrderEventSerializer

        self.assertNotIn(
            'result_metadata', WorkOrderEventSerializer(event).data['metadata']
        )
        event.event_type = (
            'unrelated_fixture_event'  # unsaved, generic response contract
        )
        self.assertEqual(
            WorkOrderEventSerializer(event).data['metadata'], event.metadata
        )
        self.work.customer = None
        self.work.save()
        self.assertEqual(
            self.apply(
                expected_version=saved['work_version'] - 1, expected_context=context
            ),
            saved,
        )
        self.assertEqual(GateAuthorityRecord.objects.count(), 1)

    def test_overview_gate_readiness_does_not_promote_confirmed_expired_authority(self):
        """The native summary must expose the same blocker as the transition gate."""
        self.gate.requires_second_person = False
        self.gate.save()
        fields = {
            **self.fields,
            'effective_from': (self.now - timedelta(hours=2)).isoformat(),
            'effective_until': (self.now - timedelta(hours=1)).isoformat(),
        }
        self.apply(fields)
        response = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work.pk})
        )
        gate = response.data['repair_packet']['gates'][0]
        self.assertEqual(gate['status'], 'confirmed')
        self.assertFalse(gate['is_satisfied'])
        self.assertEqual(
            gate['unsatisfied_reason'],
            'recorded authority outside its effective window',
        )

    def test_viewed_scope_and_gate_contract_are_required_before_a_new_save(self):
        """Native scope/gate changes do not advance Work's version but refuse old drafts."""
        version = self.work.lifecycle_version
        context = project(self.work, self.user)['context_token']
        saved = self.apply(expected_version=version, expected_context=context)
        approve_repair_scope(
            self.packet, scope_lines=['Synthetic revised scope'], actor=self.user
        )
        # Exact saved receipts remain recoverable after a later source change.
        self.assertEqual(
            self.apply(expected_version=version, expected_context=context), saved
        )
        current_version = self.work.lifecycle_version
        with self.assertRaises(StaleVersion):
            self.apply(key='old-scope', expected_context=context)
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_version, current_version)
        context = project(self.work, self.user)['context_token']
        self.gate.requires_photo = True
        self.gate.save()
        with self.assertRaises(StaleVersion):
            self.apply(key='old-gate', expected_context=context)
        self.assertEqual(GateAuthorityRecord.objects.count(), 1)
        fresh = self.apply(key='fresh-view')
        self.assertTrue(fresh['record']['basis']['gate']['requires_photo'])
        self.assertEqual(fresh['record']['basis_state'], 'current')

    def test_scope_change_after_context_check_never_recaptures_unseen_sources(self):
        """A concurrent native scope writer leaves the saved reviewed basis visibly stale."""
        original_context = _context

        def check_then_change(work):
            checked = original_context(work)
            approve_repair_scope(
                self.packet, scope_lines=['Concurrent synthetic scope'], actor=self.user
            )
            return checked

        context = _context(self.work)[1]
        with patch('tasks.services.operational_authority._context', check_then_change):
            result = self.apply(key='concurrent-scope', expected_context=context)
        self.assertEqual(result['record']['basis']['scope_ids'], [])
        self.assertEqual(result['record']['basis_state'], 'changed_or_unrecorded')
        self.assertFalse(self.gate.is_satisfied)

    def test_overview_context_uses_displayed_scope_when_a_native_writer_runs_during_read(
        self,
    ):
        """Panel order cannot pair the displayed old scope with a newer save token."""
        from tasks.work_execution.fields import WorkExecutionFieldsMixin

        scope = approve_repair_scope(
            self.packet, scope_lines=['Viewed synthetic scope'], actor=self.user
        )
        viewed_context = _context(self.work)[1]
        original = WorkExecutionFieldsMixin.get_prepared_package

        def panel_then_change(serializer, work):
            data = original(serializer, work)
            approve_repair_scope(
                self.packet, scope_lines=['Unseen concurrent scope'], actor=self.user
            )
            self.gate.requires_photo = True
            self.gate.save()
            return data

        with patch.object(
            WorkExecutionFieldsMixin, 'get_prepared_package', panel_then_change
        ):
            response = self.get(
                reverse('kanban-card-overview', kwargs={'pk': self.work.pk})
            )
        data = response.data
        self.assertEqual(data['repair_packet']['approved_scope']['id'], scope.pk)
        self.assertEqual(data['prepared_package']['scopes'][0]['id'], scope.pk)
        self.assertFalse(data['repair_packet']['gates'][0]['requires_photo'])
        self.assertEqual(data['operational_authority']['context_token'], viewed_context)
        self.assertNotEqual(_context(self.work)[1], viewed_context)
        self.post(
            reverse('work-operational-authority', kwargs={'pk': self.work.pk}),
            {
                'operation': 'record',
                'expected_version': self.work.lifecycle_version,
                'expected_context': viewed_context,
                'idempotency_key': 'mixed-read',
                'fields': self.fields,
            },
            expected_code=409,
        )
        self.assertEqual(GateAuthorityRecord.objects.count(), 0)

    def test_current_authority_refuses_packet_relink_and_delete(self):
        """Native owner edits and DELETE return conflicts while preserving history."""
        from repair.models import AuthorityOwnershipError

        self.apply()
        other = WorkOrder.objects.create(title='Other Work', machine=self.machine)
        self.packet.work_order = other
        with self.assertRaises(AuthorityOwnershipError):
            self.packet.save()
        self.packet.refresh_from_db()
        url = reverse('repair-packet-detail', kwargs={'pk': self.packet.pk})
        response = self.patch(url, {'work_order': other.pk}, expected_code=409)
        self.assertIn('current authority records', str(response.data))
        self.assignRole('work_order.delete')
        response = self.delete(url, expected_code=409)
        self.assertIn('retained authority history', str(response.data))
        self.packet.refresh_from_db()
        self.assertEqual(self.packet.work_order_id, self.work.pk)
        self.assertEqual(GateAuthorityRecord.objects.count(), 1)

    def test_unavailable_operating_corrections_have_uniform_conflicts(self):
        """Unknown, wrong-kind/target and already-corrected IDs disclose no distinctions."""
        acceptance = self.apply(self.operating('test_acceptance', 'accepted'))
        station = self.apply(
            {
                **self.operating('service_state', 'operator_recorded'),
                'target_id': self.station.pk,
                'duty': 'Station assignment',
            },
            key='station',
        )
        operating = self.apply(self.operating(), key='operating')
        self.apply(
            self.operating(),
            key='replace-operating',
            supersedes=operating['record']['id'],
        )
        errors = []
        cases = [
            (99999999, self.operating()),
            (acceptance['record']['id'], self.operating()),
            (
                station['record']['id'],
                {
                    **self.operating('service_state', 'operator_recorded'),
                    'duty': 'Machine assignment',
                },
            ),
            (operating['record']['id'], self.operating()),
        ]
        for index, (record_id, fields) in enumerate(cases):
            with self.assertRaises(StaleVersion) as caught:
                self.apply(
                    fields, key=f'invalid-correction-{index}', supersedes=record_id
                )
            errors.append(str(caught.exception))
        self.assertEqual(len(set(errors)), 1)
        self.assertEqual(OperationalAuthorityRecord.objects.count(), 4)

    def test_projection_provider_presence_is_separate_from_authored_outcome(self):
        """An available provider is never an acceptance or effective-authority claim."""
        from tasks.work_execution.verification import project as verification

        empty = verification(self.work, self.user)['technical_acceptance']
        self.assertTrue(empty['available'])
        self.assertFalse(empty['recorded'])
        accepted = self.apply(self.operating('test_acceptance', 'accepted'))
        rejected = self.apply(
            self.operating('test_acceptance', 'rejected'),
            key='rejected',
            supersedes=accepted['record']['id'],
        )
        value = verification(self.work, self.user)['technical_acceptance']
        self.assertTrue(value['available'])
        self.assertTrue(value['recorded'])
        self.assertEqual(len(value['records']), 2)
        self.assertEqual(
            [row['id'] for row in value['current_records']], [rejected['record']['id']]
        )
        self.assertEqual(value['current_records'][0]['outcome'], 'rejected')
        self.assertEqual(
            value['current_records'][0]['effective_state'], 'within_window'
        )

    def test_verified_current_reading_never_asserts_agreement_with_operator_text(self):
        """Typed telemetry and a differing operator statement remain distinct facts."""
        source = HealthSource.objects.create(name='Synthetic duty source', active=True)
        point = DictionaryPoint.objects.create(
            machine=self.machine,
            station=self.station,
            path='/duty',
            raw_tag='SYNTHETIC_DUTY',
            display_name='Duty',
            status='approved',
        )
        binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            external_key='/duty',
            display_name='Duty',
            dictionary_point=point,
            signal_kind='duty',
        )
        state = MachineSignalState.objects.create(
            binding=binding,
            value={'value': 'Telemetry duty A'},
            observed_at=timezone.now(),
            quality='good',
            source_sequence=1,
        )
        reading = project(self.work, self.user)['telemetry'][0]
        result = self.apply({
            **self.operating('service_state', 'confirmed'),
            'duty': 'Operator assignment B',
            'binding_id': binding.pk,
            'telemetry_observed_at': state.observed_at.isoformat(),
            'telemetry_token': reading['reading_token'],
        })['record']
        self.assertEqual(
            result['basis']['telemetry_verification'], 'reading_verified_current'
        )
        self.assertEqual(
            result['basis']['telemetry']['value'], {'value': 'Telemetry duty A'}
        )
        self.assertEqual(result['details']['duty'], 'Operator assignment B')
