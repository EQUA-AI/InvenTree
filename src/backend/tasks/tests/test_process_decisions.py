"""O15 commands and projections exercise durable process facts offline."""

from datetime import timedelta
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from assets.health_models import HealthSource, MachineAnomaly, MachineSignalBinding
from assets.models import get_default_client
from assets.registry import ensure_pump, register_station
from assets.registry_models import AssetComponent, DictionaryPoint
from InvenTree.unit_test import InvenTreeAPITestCase
from part.models import Part
from tasks.investigation_models import InvestigationRequest
from tasks.models import WorkOrder, WorkOrderCloseout
from tasks.services.investigation_requests import command, escalation_options
from tasks.services.recurrence import decision, project
from tasks.services.scheduling import create_work_order
from tasks.services.work_orders import (
    IdempotencyConflict,
    StaleVersion,
    accept_handover,
    assign_work_order,
)
from tasks.work_execution.continuity import project as continuity
from tasks.work_execution.scope_revision import project as scope_project


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
    AIMMS_WORK_ORDERS_ENABLED=True,
)
class ProcessDecisionTests(InvenTreeAPITestCase):
    """Use the existing native roles, scope and command ledger."""

    roles = 'all'

    def setUp(self):
        """Two real synthetic actors and one exact parent Work."""
        super().setUp()
        self.assignRole('work_order.change')
        self.assignRole('work_order.add')
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='users', codename='view_staff_directory'
            ),
            Permission.objects.get(
                content_type__app_label='tasks', codename='execute_workorder'
            ),
            Permission.objects.get(
                content_type__app_label='tasks', codename='assign_workorder'
            ),
        )
        for name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.user.__dict__.pop(name, None)
        self.incoming = get_user_model().objects.create_superuser(
            username='incoming-o15', email='incoming@example.com', password='synthetic'
        )
        self.station = register_station(
            client=get_default_client(),
            name='O15 synthetic station',
            source_namespace='o15-synthetic',
            source_entity_uuid=uuid4(),
            source_key='O15',
        )
        self.machine = ensure_pump(self.station, 'P1')
        self.work = WorkOrder.objects.create(
            title='Synthetic process work',
            machine=self.machine,
            assigned_to=self.user,
            affected_component_ref='MOTOR-DE-BRG',
            lifecycle_status='in_progress',
        )

    def propose(self):
        """Assignment proposes transfer, never accepts it."""
        return assign_work_order(
            work_order_id=self.work.pk,
            assigned_to=self.incoming,
            actor=self.user,
            expected_version=1,
            idempotency_key='assign',
        )

    def accept(self, actor=None, version=2, key='accept'):
        """Submit the exact incoming actor/version/key."""
        return accept_handover(
            work_order_id=self.work.pk,
            actor=actor or self.incoming,
            expected_version=version,
            idempotency_key=key,
        )

    def test_acceptance_is_explicit_attributed_versioned_and_idempotent(self):
        """Reads and assignment keep the outgoing owner until acceptance."""
        self.propose()
        self.work.refresh_from_db()
        for _ in range(2):
            value = continuity(self.work, self.incoming)['accepted_handover']
            self.assertEqual(value['state'], 'pending')
            self.assertEqual(
                value['responsible_owner'],
                self.user.get_full_name().strip() or self.user.username,
            )
        result = self.accept()
        self.assertEqual(self.accept(), result)
        self.work.refresh_from_db()
        value = continuity(self.work, self.incoming)['accepted_handover']
        self.assertEqual(value['state'], 'accepted')
        self.assertEqual(value['accepted_by'], self.incoming.username)
        self.assertEqual(value['accepted_version'], 3)
        self.assertEqual(
            self.work.events.filter(event_type='HANDOVER_ACCEPTED').count(), 1
        )
        self.assertFalse(value['can_accept'])

    def test_wrong_actor_stale_and_conflicting_key_cannot_accept(self):
        """No assignment permission can impersonate the receiver."""
        self.propose()
        with self.assertRaises(PermissionDenied):
            self.accept(actor=self.user)
        with self.assertRaises(StaleVersion):
            self.accept(version=1)
        self.accept()
        with self.assertRaises(IdempotencyConflict):
            self.accept(version=3)
        self.assertEqual(self.work.handovers.get().accepted_by, self.incoming)

    def test_http_receipt_flag_and_revocation(self):
        """The same command endpoint enforces scope and live execution permission."""
        self.propose()
        self.client.force_login(self.incoming)
        body = {'expected_version': 2, 'idempotency_key': 'http-accept'}
        url = f'/api/tasks/work-orders/{self.work.pk}/accept_handover/'
        self.assertEqual(self.client.post(url, body, format='json').status_code, 200)
        self.assertEqual(self.client.post(url, body, format='json').status_code, 200)
        with override_settings(AIMMS_WORK_ORDERS_ENABLED=False):
            self.assertEqual(
                self.client.post(url, body, format='json').status_code, 404
            )
        self.incoming.is_active = False
        self.incoming.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.accept(key='http-accept')

    def request(self):
        """An actual unresolved contradiction, separate from its routing."""
        return InvestigationRequest.objects.create(
            work_order=self.work,
            purpose='Contradictory observations',
            next_check='Review retained evidence',
            status='disputed',
            blocked_reason='Persistent contradiction',
            created_by=self.user,
            updated_by=self.user,
        )

    def escalate(self, row, **fields):
        """Use the existing request's command service."""
        return command(
            work_order_id=self.work.pk,
            actor=self.user,
            expected_version=1,
            idempotency_key='expert',
            operation='escalate',
            fields={
                'owner_id': self.incoming.pk,
                'next_checkpoint': (timezone.now() + timedelta(days=1)).isoformat(),
                'blocked_reason': 'Persistent contradiction',
                **fields,
            },
            request_id=row.pk,
            request_version=row.version,
        )

    def test_escalation_requires_one_group_owner_checkpoint_and_retains_evidence(self):
        """Explicit routing keeps the contradiction unresolved with an audit trail."""
        row = self.request()
        with self.assertRaises(ValidationError):
            self.escalate(row)
        group, _ = Group.objects.get_or_create(name='Reliability engineers')
        self.incoming.groups.add(group)
        fields = {
            'owner_id': self.incoming.pk,
            'next_checkpoint': (timezone.now() + timedelta(days=1)).isoformat(),
            'blocked_reason': 'Persistent contradiction',
        }

        def apply():
            return command(
                work_order_id=self.work.pk,
                actor=self.user,
                expected_version=1,
                idempotency_key='expert',
                operation='escalate',
                fields=fields,
                request_id=row.pk,
                request_version=1,
            )

        first = apply()
        self.assertEqual(first, apply())
        row.refresh_from_db()
        self.assertEqual(
            (row.owner_id, row.version, row.status), (self.incoming.pk, 2, 'disputed')
        )
        self.assertEqual(first['request']['escalation']['state'], 'active')
        self.assertEqual(self.work.events.count(), 1)
        self.assertEqual(
            escalation_options(self.work, self.user)['owners'][0]['id'],
            self.incoming.pk,
        )
        with self.assertRaises(ValidationError):
            command(
                work_order_id=self.work.pk,
                actor=self.user,
                expected_version=1,
                idempotency_key='clear-owner',
                operation='revise',
                fields={'owner_id': None},
                request_id=row.pk,
                request_version=2,
            )

    def test_configured_group_migration_is_idempotent_and_grants_no_roles(self):
        """Existing routing groups and their membership survive installation."""
        migration = import_module('tasks.migrations.0035_process_decisions')
        with override_settings(AIMMS_EXPERT_GROUP_NAME='Site experts'):
            editor = SimpleNamespace(connection=connection)
            migration.expert_group(apps, editor)
            migration.expert_group(apps, editor)
            self.assertEqual(Group.objects.filter(name='Site experts').count(), 1)
            self.assertFalse(
                Group.objects.get(name='Site experts').permissions.exists()
            )

    def closed(self, accepted=True):
        """Record native verification through its command, never assign its fields."""
        self.work.lifecycle_status = 'completed'
        self.work.actual_completed_at = timezone.now() - timedelta(days=2)
        self.work.save()
        closeout = WorkOrderCloseout.objects.create(
            work_order=self.work,
            action='Original action',
            result='Original result',
            verification_summary='Original verification',
            completed_by=self.user,
            completed_at=self.work.actual_completed_at,
            content_hash='original-hash',
        )
        if accepted:
            from tasks.services.closeout_amend import verify_closeout

            with override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor'):
                verify_closeout(
                    work_order_id=self.work.pk,
                    actor=self.incoming,
                    expected_version=1,
                    idempotency_key='native-verify',
                )
            closeout.refresh_from_db()
        return closeout

    def deviation(self):
        """The occurrence retains its own observation timestamp."""
        return MachineAnomaly.objects.create(
            machine=self.machine,
            fingerprint='synthetic-recurrence',
            title='Synthetic later deviation',
            first_observed_at=timezone.now() - timedelta(days=1),
            last_observed_at=timezone.now(),
        )

    def test_recurrence_exact_component_time_and_acceptance_policy(self):
        """Machine identity alone never proves component recurrence."""
        self.closed(False)
        occurrence = self.deviation()
        self.assertEqual(decision(self.work, occurrence), 'ineligible')
        with patch.object(type(occurrence.bindings), 'filter') as linked:
            linked.return_value.exists.return_value = True
            self.assertEqual(decision(self.work, occurrence), 'create_linked')
            occurrence.first_observed_at = self.work.actual_completed_at + timedelta(
                days=31
            )
            self.assertEqual(decision(self.work, occurrence), 'ineligible')
            with override_settings(AIMMS_RECURRENCE_DAYS=60):
                self.assertEqual(decision(self.work, occurrence), 'create_linked')
            self.work.refresh_from_db()
            occurrence.first_observed_at = timezone.now()
            self.assertEqual(decision(self.work, occurrence), 'create_linked')
            with override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor'):
                self.assertEqual(decision(self.work, occurrence), 'reopen')

    def test_linked_create_keeps_accepted_closeout_unchanged_and_retries_once(self):
        """Only the explicit existing command writes; original accepted fields stay."""
        closeout = self.closed()
        occurrence = self.deviation()
        args = {
            'actor': self.user,
            'idempotency_key': 'recurrence',
            'title': 'New corrective work',
            'machine_id': self.machine.pk,
            'recurrence_of': self.work.pk,
            'recurrence_occurrence': occurrence.pk,
            'prior_version': 1,
        }
        self.bind(occurrence)
        first = create_work_order(**args)
        self.assertEqual(first, create_work_order(**args))
        new = WorkOrder.objects.get(pk=first.work_order_id)
        self.assertEqual(new.recurrence_of_id, self.work.pk)
        self.assertEqual(new.corrective_origin_kind, 'recurrence')
        self.assertEqual(new.recurrence_occurrence_id, occurrence.pk)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.work_order_id, new.pk)
        self.assertEqual(
            project(self.work, self.user)['items'][0]['work_order_id'], new.pk
        )
        self.assertEqual(new.affected_component_ref, self.work.affected_component_ref)
        closeout.refresh_from_db()
        self.work.refresh_from_db()
        self.assertEqual(
            (closeout.result, closeout.content_hash, self.work.lifecycle_status),
            ('Original result', 'original-hash', 'completed'),
        )

    @override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor')
    def test_unaccepted_reopens_only_explicitly_and_preserves_closeout(self):
        """Opening the monitoring projection never reopens a closed Work."""
        closeout = self.closed(False)
        occurrence = self.deviation()
        before = self.work.events.count()
        project(self.work, self.user)
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_status, 'completed')
        self.assertEqual(self.work.events.count(), before)
        self.bind(occurrence)
        result = create_work_order(
            actor=self.user,
            idempotency_key='reopen',
            title='Renewed check',
            machine_id=self.machine.pk,
            recurrence_of=self.work.pk,
            recurrence_occurrence=occurrence.pk,
            prior_version=1,
        )
        self.assertEqual(result.work_order_id, self.work.pk)
        self.work.refresh_from_db()
        closeout.refresh_from_db()
        self.assertEqual(self.work.lifecycle_status, 'in_progress')
        self.assertEqual(closeout.result, 'Original result')
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_scope_reassessment_uses_bindings_and_obsolete_is_not_executable(self):
        """Changed bearing dependencies are reassessed; casing evidence survives."""

        def line(ref, action, kind, evidence):
            return {
                'sequence': 1,
                'action': action,
                'affected_component_ref': ref,
                'dependencies': [
                    {
                        'kind': kind,
                        'id': kind + ref,
                        'affected_component_ref': ref,
                        'evidence_ids': [evidence],
                    }
                ],
            }

        old = {
            'id': 1,
            'version': 1,
            'current': False,
            'lines': [
                line('BRG', 'Inspect', 'tasks', 10),
                line('CASING', 'Inspect casing', 'materials', 11),
            ],
        }
        new = {
            'id': 2,
            'version': 2,
            'current': True,
            'lines': [
                line('BRG', 'Replace', 'tasks', 10),
                line('CASING', 'Inspect casing', 'materials', 11),
            ],
        }
        result = self.scope_projection([old, new])['selective_reassessment']
        self.assertEqual(result['changed_component_refs'], ['BRG'])
        self.assertEqual(
            [r['state'] for r in result['items']],
            ['obsolete', 'obsolete', 'reassess', 'retained'],
        )
        self.assertEqual(result['items'][-1]['retained_evidence_ids'], [11])
        self.assertFalse(result['items'][0]['executable'])
        self.assertFalse(result['items'][2]['executable'])
        self.assertTrue(result['items'][3]['executable'])

    def bind(self, occurrence):
        """Persist an exact synthetic component-to-signal-to-occurrence link."""
        component = AssetComponent.objects.create(
            machine=self.machine,
            part=Part.objects.create(name='Synthetic bearing'),
            code=self.work.affected_component_ref,
            name='Synthetic bearing',
        )
        point = DictionaryPoint.objects.create(
            station=self.station,
            machine=self.machine,
            component=component,
            path=f'/synthetic/{self.machine.pk}/temperature',
            raw_tag='TEMP',
            display_name='Synthetic temperature',
        )
        source = HealthSource.objects.create(
            name=f'Synthetic source {self.work.pk}',
            source_type='scada',
            connector_type='webhook',
        )
        binding = MachineSignalBinding.objects.create(
            machine=self.machine,
            source=source,
            dictionary_point=point,
            external_key=f'/synthetic/{self.machine.pk}/temperature',
            display_name='Synthetic temperature',
        )
        occurrence.bindings.add(binding)

    def test_real_component_binding_projects_eligibility_without_mutation(self):
        """The policy uses stored component identity, not same-machine timing."""
        self.closed()
        occurrence = self.deviation()
        self.bind(occurrence)
        result = project(self.work, self.user)
        self.assertEqual(result['items'][0]['action'], 'create_linked')
        self.assertEqual(result['items'][0]['occurrence_id'], occurrence.pk)
        self.assertEqual(self.work.events.count(), 1)
        self.assertFalse(self.work.corrective_recurrences.exists())

    @override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor')
    def test_reclosure_archives_unaccepted_account_and_blocks_acceptance_while_reopened(
        self,
    ):
        """Reopened unaccepted work can be completed with the old account retained."""
        from tasks.services.closeout import complete_work_order
        from tasks.services.closeout_amend import VerificationError, verify_closeout

        closeout = self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        create_work_order(
            actor=self.user,
            idempotency_key='reopen-reclose',
            title='Renewed check',
            machine_id=self.machine.pk,
            recurrence_of=self.work.pk,
            recurrence_occurrence=occurrence.pk,
            prior_version=1,
        )
        with override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor'):
            with self.assertRaises(VerificationError):
                verify_closeout(
                    work_order_id=self.work.pk,
                    actor=self.incoming,
                    expected_version=2,
                    idempotency_key='premature-accept',
                )
        self.work.refresh_from_db()
        self.work.lifecycle_status = 'verifying'
        self.work.save(update_fields=['lifecycle_status'])
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='tasks', codename='complete_workorder'
            )
        )
        for name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.user.__dict__.pop(name, None)
        with patch(
            'tasks.services.closeout.evaluate_work_order_readiness',
            return_value=SimpleNamespace(ready=True),
        ):
            complete_work_order(
                work_order_id=self.work.pk,
                actor=self.user,
                expected_version=2,
                idempotency_key='reclose',
                closeout={
                    'action': 'Revised action',
                    'result': 'Revised result',
                    'verification_summary': 'Revised verification',
                },
            )
        closeout.refresh_from_db()
        self.assertEqual(closeout.result, 'Revised result')
        retained = self.work.events.get(event_type='RECLOSURE_RETAINED').metadata[
            'previous_closeout'
        ]
        self.assertEqual(retained['result'], 'Original result')
        self.assertEqual(retained['content_hash'], 'original-hash')

    def test_procedure_binding_and_unmapped_change_do_not_invalidate_other_bindings(
        self,
    ):
        """A revised procedure affects its own dependencies; unrelated evidence stays."""
        before = {
            'id': 1,
            'version': 1,
            'current': False,
            'lines': [
                {'action': 'Old procedure', 'procedure_revision_id': 10},
                {'action': 'Unmapped old'},
            ],
        }
        after = {
            'id': 2,
            'version': 2,
            'current': True,
            'lines': [
                {
                    'action': 'New procedure',
                    'procedure_revision_id': 11,
                    'dependencies': [
                        {
                            'kind': 'criteria',
                            'id': 'test-1',
                            'procedure_revision_id': 10,
                            'evidence_ids': [1],
                        },
                        {
                            'kind': 'permissions',
                            'id': 'permit-2',
                            'procedure_revision_id': 20,
                            'evidence_ids': [2],
                        },
                    ],
                },
                {'action': 'Unmapped new'},
            ],
        }
        value = self.scope_projection([before, after])['selective_reassessment']
        self.assertEqual(
            [row['state'] for row in value['items']], ['reassess', 'retained']
        )
        self.assertTrue(value['unmapped_changes'])

    def test_scope_binding_validation_retains_metadata_and_rejects_malformed_links(
        self,
    ):
        """The actual scope writer preserves typed component/revision bindings."""
        from repair.investigation import InvestigationError, _normalized_scope_lines

        line = {
            'action': 'Inspect',
            'affected_component_ref': 'BRG',
            'procedure_revision_id': 10,
            'dependencies': [
                {
                    'kind': 'tasks',
                    'id': 'inspect',
                    'affected_component_ref': 'BRG',
                    'evidence_ids': [1],
                }
            ],
        }
        self.assertEqual(_normalized_scope_lines([line])[0], {'sequence': 1, **line})
        for invalid in [
            {'dependencies': [{'kind': 'wrong', 'id': 'x'}]},
            {'procedure_revision_id': True},
            {'affected_component_ref': ''},
        ]:
            with self.assertRaises(InvestigationError):
                _normalized_scope_lines([{'action': 'Inspect', **invalid}])

    def test_same_accepted_assignee_adds_no_new_handover_chore(self):
        """Redundant assignment cannot turn accepted responsibility back to pending."""
        self.propose()
        self.accept()
        assign_work_order(
            work_order_id=self.work.pk,
            assigned_to=self.incoming,
            actor=self.user,
            expected_version=3,
            idempotency_key='same-assignee',
        )
        self.assertEqual(self.work.handovers.count(), 1)
        self.work.refresh_from_db()
        self.assertEqual(
            continuity(self.work, self.incoming)['accepted_handover']['state'],
            'accepted',
        )

    def test_customer_parent_allows_authorized_acceptance(self):
        """O24 permits authorized handover acceptance across Work customer values."""
        from company.models import Company
        self.propose()
        self.work.customer = Company.objects.create(
            name='Foreign customer', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        self.accept()
        self.assertIsNotNone(self.work.handovers.get().accepted_at)

    def test_foreign_machine_parent_denies_acceptance(self):
        """New commands retain the native parent scope even for privileged actors.

        O24 moved the foreign boundary from the customer value to machine-only
        Work on another client's machine.
        """
        from assets.models import AssetMachine, Client
        from tasks.services.work_orders import WorkOrderScopeError

        self.propose()
        self.work.machine = AssetMachine.objects.create(
            name='O24 foreign handover pump',
            client=Client.objects.create(name='O24 foreign', code='o24-handover'),
        )
        self.work.save(update_fields=['machine'])
        with self.assertRaises(WorkOrderScopeError):
            self.accept()
        self.assertIsNone(self.work.handovers.get().accepted_at)

    def test_revoked_expert_actor_cannot_retry_a_saved_escalation(self):
        """Durable receipts do not bypass current native change permission."""
        from users.models import RuleSet

        row = self.request()
        group, _ = Group.objects.get_or_create(name='Reliability engineers')
        self.incoming.groups.add(group)
        fields = {
            'owner_id': self.incoming.pk,
            'next_checkpoint': (timezone.now() + timedelta(days=1)).isoformat(),
            'blocked_reason': 'Persistent contradiction',
        }
        args = {
            'work_order_id': self.work.pk,
            'actor': self.user,
            'expected_version': 1,
            'idempotency_key': 'revocation-expert',
            'operation': 'escalate',
            'fields': fields,
            'request_id': row.pk,
            'request_version': 1,
        }
        command(**args)
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=False
        )
        with self.assertRaises(PermissionDenied):
            command(**args)

    @override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor')
    def test_unaccepted_capture_provenance_is_not_replaced_by_reopening(self):
        """A native captured closure needs a retained revision provider first."""
        from tasks.closeout_models import CloseoutCapture

        self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        CloseoutCapture.objects.create(
            work_order=self.work,
            source_type='typed',
            created_by=self.user,
            completed_closeout=self.work.structured_closeout,
        )
        self.assertEqual(
            project(self.work, self.user)['items'][0]['action'], 'create_linked'
        )
        create_work_order(
            actor=self.user,
            idempotency_key='blocked-capture-reopen',
            title='Renewed check',
            machine_id=self.machine.pk,
            recurrence_of=self.work.pk,
            recurrence_occurrence=occurrence.pk,
            prior_version=1,
        )
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_status, 'completed')

    def test_replacing_pending_receiver_keeps_actual_outgoing_responsibility(self):
        """An unaccepted intermediary is an assignment, not a responsible owner."""
        self.propose()
        other = get_user_model().objects.create_superuser(
            username='third-o15', email='third@example.com', password='synthetic'
        )
        result = assign_work_order(
            work_order_id=self.work.pk,
            assigned_to=other,
            actor=self.user,
            expected_version=2,
            idempotency_key='replace-pending',
        )
        self.work.refresh_from_db()
        latest = self.work.handovers.order_by('-pk').first()
        self.assertEqual(latest.previous_owner_id, self.user.pk)
        self.assertEqual(result.metadata['previous_assigned_to_id'], self.incoming.pk)
        self.assertEqual(
            continuity(self.work, other)['accepted_handover']['incoming_owner'],
            other.username,
        )

    def test_later_unrelated_revision_cannot_revive_unreassessed_evidence(self):
        """A previous component change remains pending when its evidence is reused."""
        from copy import deepcopy

        def scope(pk, action, current=False):
            return {
                'id': pk,
                'version': pk,
                'current': current,
                'lines': [
                    {
                        'action': action,
                        'affected_component_ref': 'BRG',
                        'dependencies': [
                            {
                                'kind': 'tasks',
                                'id': 'bearing-task',
                                'affected_component_ref': 'BRG',
                                'evidence_ids': [1],
                            }
                        ],
                    }
                ],
            }

        original = scope(1, 'Inspect')
        changed = scope(2, 'Replace')
        later = scope(3, 'Replace', True)
        later['lines'].append({
            'action': 'Unrelated scope change',
            'affected_component_ref': 'CASING',
        })
        value = self.scope_projection([original, changed, later])[
            'selective_reassessment'
        ]
        self.assertEqual(value['items'][-1]['state'], 'reassess')
        reassessed = deepcopy(later)
        reassessed['lines'][0]['dependencies'][0]['evidence_ids'] = [2]
        value = self.scope_projection([original, changed, reassessed])[
            'selective_reassessment'
        ]
        self.assertEqual(value['items'][-1]['state'], 'retained')

    def test_escalation_retains_previous_outcome_and_evidence_boundary(self):
        """A new routing reason cannot erase the original unresolved outcome."""
        from tasks.investigation_models import InvestigationArtifact

        row = self.request()
        group, _ = Group.objects.get_or_create(name='Reliability engineers')
        self.incoming.groups.add(group)
        artifact = InvestigationArtifact.objects.create(
            request=row,
            kind='observation',
            payload={'text': 'Retained contradictory observation'},
            recorded_by=self.user,
        )
        before_reason = row.blocked_reason
        result = self.escalate(
            row, blocked_reason='Expert review of persistent conflicting observations'
        )
        event = self.work.events.get(event_type='investigation_escalate')
        revision = event.metadata['result_metadata']['request_revision']
        self.assertEqual(revision['before']['blocked_reason'], before_reason)
        self.assertEqual(revision['after']['blocked_reason'], before_reason)
        self.assertIsNone(revision['before']['owner_name'])
        self.assertEqual(revision['after']['owner_name'], self.incoming.username)
        self.assertEqual(revision['artifact_through_id'], artifact.pk)
        self.assertEqual(result['request_revision'], revision)
        artifact.refresh_from_db()
        self.assertEqual(
            artifact.payload, {'text': 'Retained contradictory observation'}
        )

    def test_existing_occurrence_work_owner_prevents_duplicate_response(self):
        """Native anomaly ownership is authoritative before recurrence creation."""
        self.closed()
        occurrence = self.deviation()
        self.bind(occurrence)
        existing = WorkOrder.objects.create(
            title='Existing response', machine=self.machine
        )
        occurrence.work_order = existing
        occurrence.save(update_fields=['work_order'])
        self.assertEqual(project(self.work, self.user)['items'], [])
        with self.assertRaises(ValidationError):
            create_work_order(
                actor=self.user,
                idempotency_key='duplicate-owner',
                title='Duplicate',
                machine_id=self.machine.pk,
                recurrence_of=self.work.pk,
                recurrence_occurrence=occurrence.pk,
                prior_version=1,
            )
        self.assertEqual(WorkOrder.objects.count(), 2)

    def test_existing_occurrence_packet_prevents_parallel_standalone_work(self):
        """A packet-owned deviation stays in its native response flow."""
        from repair.models import RepairPacket

        self.closed()
        occurrence = self.deviation()
        self.bind(occurrence)
        occurrence.repair_packet = RepairPacket.objects.create(
            machine=self.machine, fault_summary='Existing response'
        )
        occurrence.save(update_fields=['repair_packet'])
        self.assertEqual(project(self.work, self.user)['items'], [])
        with self.assertRaises(ValidationError):
            create_work_order(
                actor=self.user,
                idempotency_key='duplicate-packet',
                title='Duplicate',
                machine_id=self.machine.pk,
                recurrence_of=self.work.pk,
                recurrence_occurrence=occurrence.pk,
                prior_version=1,
            )
        self.assertEqual(WorkOrder.objects.count(), 1)

    def test_review_default_verification_off_routes_linked(self):
        """Disabled acceptance is unknown, never permission to reopen."""
        from tasks.services.closeout_amend import VerificationError, verify_closeout

        self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        with self.assertRaises(VerificationError):
            verify_closeout(
                work_order_id=self.work.pk,
                actor=self.incoming,
                expected_version=1,
                idempotency_key='off-verification',
            )
        self.assertEqual(
            project(self.work, self.user)['items'][0]['action'], 'create_linked'
        )

    def test_review_assign_then_delete_keeps_handover_snapshot(self):
        """Assignment audit cascades only after governed retention."""
        from tasks.models import WorkOrderDeletionRecord
        from tasks.services.scheduling import delete_work_order

        self.propose()
        result = delete_work_order(
            work_order_id=self.work.pk,
            actor=self.user,
            expected_version=2,
            idempotency_key='assigned-delete',
        )
        snapshot = WorkOrderDeletionRecord.objects.get(
            pk=result.deletion_record_id
        ).snapshot
        self.assertEqual(
            snapshot['handovers'][0]['incoming_owner_id'], self.incoming.pk
        )

    def test_review_reassign_back_has_no_self_acceptance(self):
        """A withdrawn proposal cannot require accepting one's own responsibility."""
        self.propose()
        assign_work_order(
            work_order_id=self.work.pk,
            assigned_to=self.user,
            actor=self.user,
            expected_version=2,
            idempotency_key='return-owner',
        )
        self.work.refresh_from_db()
        value = continuity(self.work, self.user)['accepted_handover']
        self.assertFalse(value['can_accept'])
        self.assertNotEqual(value['state'], 'pending')

    def test_review_escalation_preserves_cannot_perform(self):
        """Routing does not turn a recorded inability into a dispute."""
        row = self.request()
        row.status = 'cannot_perform'
        row.blocked_reason = 'No isolation available'
        row.save()
        group, _ = Group.objects.get_or_create(name='Reliability engineers')
        self.incoming.groups.add(group)
        self.escalate(row, blocked_reason='Route to expert')
        row.refresh_from_db()
        self.assertEqual(
            (row.status, row.blocked_reason),
            ('cannot_perform', 'No isolation available'),
        )

    def scope_projection(self, scopes):
        """Real native identities and evidence timestamps for projection fixtures."""
        from tasks.investigation_models import InvestigationArtifact
        from tasks.models import (
            Procedure,
            ProcedureRevision,
            WorkOrderProcedureApplication,
        )

        if self.work.procedure_applications.filter(idempotency_key='scope-10').exists():
            return scope_project(self.work, scopes)
        for code in ('BRG', 'CASING'):
            AssetComponent.objects.get_or_create(
                machine=self.machine,
                code=code,
                defaults={
                    'part': Part.objects.create(name='Synthetic ' + code),
                    'name': code,
                },
            )
        procedure = Procedure.objects.create(
            code='O15-SCOPE', name='Synthetic procedure', created_by=self.user
        )
        for pk in (10, 11, 20):
            revision = ProcedureRevision.objects.create(
                pk=pk,
                procedure=procedure,
                revision=pk,
                work_order_type='corrective',
                created_by=self.user,
            )
            WorkOrderProcedureApplication.objects.create(
                work_order=self.work,
                revision=revision,
                primary=pk == 10,
                snapshot={},
                snapshot_hash='synthetic',
                applied_by=self.user,
                idempotency_key='scope-' + str(pk),
            )
        request = self.request()
        for pk in (1, 2, 10, 11):
            InvestigationArtifact.objects.create(
                pk=pk,
                request=request,
                kind='observation',
                payload={'text': 'Synthetic retained evidence'},
                recorded_by=self.user,
            )
            InvestigationArtifact.objects.filter(pk=pk).update(
                created_at=timezone.now() - timedelta(days=0 if pk == 2 else 5)
            )
        for i, scope in enumerate(scopes):
            scope.setdefault(
                'approved_at',
                (timezone.now() - timedelta(days=3) + timedelta(hours=i)).isoformat(),
            )
        return scope_project(self.work, scopes)

    def review_recurrence(self, occurrence, **overrides):
        """Use the same explicit command as the HTTP creation rail."""
        return create_work_order(
            actor=self.user,
            idempotency_key='review-recurrence',
            title='Synthetic corrective response',
            machine_id=self.machine.pk,
            recurrence_of=self.work.pk,
            recurrence_occurrence=occurrence.pk,
            prior_version=self.work.lifecycle_version,
            **overrides,
        )

    def test_review_handover_return_after_acceptance_and_time_panel(self):
        """Returning to the accepted responsible owner withdraws the proposal."""
        from tasks.work_execution.time_assignment import project as time_project

        self.propose()
        self.accept()
        self.work.refresh_from_db()
        self.assertEqual(
            time_project(self.work)['accepted_handover']['reason'],
            'read_work_continuity',
        )
        self.assertEqual(
            continuity(self.work, self.incoming)['accepted_handover']['state'],
            'accepted',
        )
        other = get_user_model().objects.create_user(username='other-incoming')
        other.groups.add(self.group)
        other.user_permissions.add(Permission.objects.get(
            content_type__app_label='tasks', codename='execute_workorder'
        ))
        assign_work_order(
            work_order_id=self.work.pk,
            assigned_to=other,
            actor=self.user,
            expected_version=3,
            idempotency_key='third-owner',
        )
        assign_work_order(
            work_order_id=self.work.pk,
            assigned_to=self.incoming,
            actor=self.user,
            expected_version=4,
            idempotency_key='restore-accepted',
        )
        self.work.refresh_from_db()
        value = continuity(self.work, self.incoming)['accepted_handover']
        self.assertEqual(value['state'], 'restored')
        self.assertEqual(value['responsible_owner'], self.incoming.username)
        self.assertFalse(value['can_accept'])
        self.assertEqual(self.work.handovers.count(), 2)
        self.assertIsNotNone(self.work.handovers.order_by('-pk').first().withdrawn_at)

    def test_review_accepted_handover_delete_and_deleted_user_attribution(self):
        """Governed Work deletion cascades audit; deleting a user preserves names."""
        from tasks.models import WorkOrderDeletionRecord
        from tasks.services.scheduling import delete_work_order

        self.incoming.first_name = 'A' * 150
        self.incoming.last_name = 'B' * 150
        self.incoming.save(update_fields=['first_name', 'last_name'])
        self.propose()
        self.accept()
        name = self.incoming.get_full_name()
        handover = self.work.handovers.get()
        self.assertEqual(
            handover._meta.get_field('incoming_owner_name').clean(name, handover), name
        )
        self.incoming.delete()
        handover = self.work.handovers.get()
        self.assertIsNone(handover.incoming_owner_id)
        self.assertEqual(handover.incoming_owner_name, name)
        result = delete_work_order(
            work_order_id=self.work.pk,
            actor=self.user,
            expected_version=3,
            idempotency_key='accepted-delete',
        )
        row = WorkOrderDeletionRecord.objects.get(
            pk=result.deletion_record_id
        ).snapshot['handovers'][0]
        self.assertEqual(row['accepted_by_name'], name)
        self.assertFalse(WorkOrder.objects.filter(pk=self.work.pk).exists())

    def review_expert(self):
        """A normal expert needs independent native role and scope grants."""
        from users.models import RuleSet

        expert = get_user_model().objects.create_user(username='review-expert')
        group, _ = Group.objects.get_or_create(name='Reliability engineers')
        # Control the precondition even when a reused database has legacy grants.
        RuleSet.objects.filter(group=group, name='work_order').update(
            can_view=False, can_add=False, can_change=False, can_delete=False
        )
        expert.groups.add(group)
        readers = Group.objects.create(name='Review Work readers')
        RuleSet.objects.update_or_create(
            group=readers, name='work_order', defaults={'can_view': True}
        )
        expert.groups.add(readers)
        return expert, readers

    def test_review_escalation_preserves_all_recorded_unresolved_outcomes(self):
        """Routing changes only routing; native outcome and safety reason remain."""
        expert, _ = self.review_expert()
        self.incoming = expert
        for state in (
            'open',
            'answered',
            'cannot_perform',
            'pending_external',
            'disputed',
        ):
            with self.subTest(state=state):
                row = self.request()
                row.status, row.blocked_reason = state, 'Retained outcome reason'
                row.save()
                command(
                    work_order_id=self.work.pk,
                    actor=self.user,
                    expected_version=1,
                    idempotency_key='route-' + state,
                    operation='escalate',
                    request_id=row.pk,
                    request_version=1,
                    fields={
                        'owner_id': expert.pk,
                        'next_checkpoint': (
                            timezone.now() + timedelta(days=1)
                        ).isoformat(),
                        'blocked_reason': 'Separate expert routing reason',
                    },
                )
                row.refresh_from_db()
                self.assertEqual(
                    (row.status, row.blocked_reason), (state, 'Retained outcome reason')
                )
                self.assertEqual(
                    row.escalation_reason, 'Separate expert routing reason'
                )

    def test_review_expert_validation_and_http_permission_boundaries(self):
        """A group member alone is not a Work-authorized expert."""
        from users.models import RuleSet

        expert, readers = self.review_expert()
        self.incoming = expert
        row = self.request()
        expert.groups.remove(readers)
        with self.assertRaises(ValidationError):
            self.escalate(row)
        expert.groups.add(readers)
        expert.is_active = False
        expert.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            self.escalate(row)
        expert.is_active = True
        expert.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):
            self.escalate(
                row, next_checkpoint=(timezone.now() - timedelta(days=1)).isoformat()
            )
        row.status = 'resolved'
        row.save()
        with self.assertRaises(ValidationError):
            self.escalate(row)
        row.status = 'pending_external'
        row.save()
        expert.username = 'foreign-review-expert'
        expert.save(update_fields=['username'])
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_process_decisions.review_scope_resolver'
        ):
            with self.assertRaises(ValidationError):
                self.escalate(row)
        expert.username = 'review-expert'
        expert.save(update_fields=['username'])
        url = f'/api/kanban/work-orders/{self.work.pk}/investigation-requests/{row.pk}/'
        body = {
            'operation': 'escalate',
            'expected_version': 1,
            'request_version': 1,
            'idempotency_key': 'expert-http',
            'fields': {
                'owner_id': expert.pk,
                'next_checkpoint': (timezone.now() + timedelta(days=1)).isoformat(),
                'blocked_reason': 'HTTP expert routing',
            },
        }
        response = self.client.post(url, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['request']['status'], 'pending_external')
        self.assertEqual(
            response.data['request']['escalation']['reason'], 'HTTP expert routing'
        )
        body['fields']['owner_id'] = self.user.pk
        conflict = self.client.post(url, body, format='json')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.data['code'], 'IDEMPOTENCY_CONFLICT')
        options = f'/api/kanban/work-orders/{self.work.pk}/investigation-requests/?escalation_owners=1'
        self.assertEqual(self.client.get(options).status_code, 200)
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=False
        )
        self.assertEqual(self.client.get(options).status_code, 403)
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=True
        )
        from company.models import Company

        self.work.customer = Company.objects.create(
            name='Foreign review customer', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        self.assertEqual(self.client.get(options).status_code, 200)
        from assets.models import AssetMachine, Client

        self.work.customer = None
        self.work.machine = AssetMachine.objects.create(
            name='O24 foreign review pump',
            client=Client.objects.create(name='O24 review', code='o24-review'),
        )
        self.work.save(update_fields=['customer', 'machine'])
        self.assertEqual(self.client.get(options).status_code, 404)

    def test_review_missing_expert_group_is_unavailable(self):
        """Configuration absence is not a successful empty membership query."""
        with override_settings(AIMMS_EXPERT_GROUP_NAME='Missing review group'):
            value = escalation_options(self.work, self.user)
        self.assertEqual(
            (value['available'], value['reason']), (False, 'group_not_configured')
        )

    def test_review_recurrence_foreign_parent_and_subset_permissions(self):
        """Actually exercise recurrence scope denial and both reopen permissions."""
        from company.models import Company
        from users.models import RuleSet

        self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        self.work.customer = Company.objects.create(
            name='Foreign recurrence', is_customer=True
        )
        self.work.save(update_fields=['customer'])
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: []):
            from tasks.services.work_orders import WorkOrderScopeError

            # The shared creation boundary rejects the foreign machine before
            # the recurrence-specific validator can inspect its prior Work.
            with self.assertRaisesRegex(WorkOrderScopeError, 'no such machine'):
                self.review_recurrence(occurrence)
        self.assertEqual(WorkOrder.objects.count(), 1)
        self.work.customer = None
        self.work.save(update_fields=['customer'])
        with override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor'):
            RuleSet.objects.filter(group__user=self.user, name='work_order').update(
                can_change=False
            )
            self.assertFalse(project(self.work, self.user)['can_reopen'])
            with self.assertRaises(PermissionDenied):
                self.review_recurrence(occurrence)
            RuleSet.objects.filter(group__user=self.user, name='work_order').update(
                can_change=True
            )
            self.user.user_permissions.remove(
                Permission.objects.get(
                    content_type__app_label='tasks', codename='execute_workorder'
                )
            )
            for name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
                self.user.__dict__.pop(name, None)
            self.assertFalse(project(self.work, self.user)['can_reopen'])
            with self.assertRaises(PermissionDenied):
                self.review_recurrence(occurrence)
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_status, 'completed')
        # Add-only can create linked Work when acceptance is unknown; it cannot reopen.
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=False
        )
        self.assertNotEqual(
            self.review_recurrence(occurrence).work_order_id, self.work.pk
        )

    def test_review_recurrence_http_receipt_and_unknown_acceptance(self):
        """The real default creates linked Work through the existing HTTP command."""
        self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        body = {
            'title': 'Review linked corrective work',
            'machine': self.machine.pk,
            'recurrence_of': self.work.pk,
            'recurrence_occurrence': occurrence.pk,
            'prior_version': 1,
            'idempotency_key': 'recurrence-http',
        }
        url = '/api/kanban/work-orders/commands/create/'
        first = self.client.post(url, body, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        receipt = first.data['process_receipt']
        self.assertEqual(receipt['metadata']['closure_acceptance'], 'unknown')
        self.assertEqual(receipt['metadata']['outcome'], 'linked_created')
        self.assertNotEqual(receipt['work_order_id'], self.work.pk)
        self.assertEqual(
            self.client.post(url, body, format='json').data['process_receipt'], receipt
        )
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_status, 'completed')

        from tasks.models import WorkOrderDeletionRecord
        from tasks.services.scheduling import delete_work_order

        deleted = delete_work_order(
            work_order_id=receipt['work_order_id'],
            actor=self.user,
            expected_version=receipt['lifecycle_version'],
            idempotency_key='delete-linked-audit',
        )
        snapshot = WorkOrderDeletionRecord.objects.get(
            pk=deleted.deletion_record_id
        ).snapshot
        self.assertEqual(snapshot['recurrence_of_id'], self.work.pk)
        self.assertEqual(snapshot['recurrence_occurrence_id'], occurrence.pk)

    @override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor')
    def test_review_reopen_clears_completion_and_distinguishes_unowned_and_canceled(
        self,
    ):
        """Live duration is not completed; only its owned deviation is handled."""
        self.work.actual_started_at = timezone.now() - timedelta(days=3)
        self.work.save(update_fields=['actual_started_at'])
        closeout = self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        self.review_recurrence(occurrence)
        self.work.refresh_from_db()
        self.assertIsNone(self.work.actual_completed_at)
        self.assertIsNotNone(self.work.actual_started_at)
        self.assertFalse(
            WorkOrder.objects.filter(
                pk=self.work.pk,
                actual_started_at__isnull=False,
                actual_completed_at__isnull=False,
            ).exists()
        )
        self.assertEqual(
            self.work.events.get(event_type='RECURRENCE_REOPENED').metadata[
                'result_metadata'
            ]['retained_completed_at'],
            closeout.completed_at.isoformat(),
        )
        occurrence.refresh_from_db()
        self.assertEqual(decision(self.work, occurrence), 'already_reopened')
        other = MachineAnomaly.objects.create(
            machine=self.machine,
            fingerprint='second-unowned',
            title='New unowned deviation',
            first_observed_at=occurrence.first_observed_at,
            last_observed_at=occurrence.last_observed_at,
        )
        other.bindings.set(occurrence.bindings.all())
        self.assertEqual(decision(self.work, other), 'unowned_during_rework')
        self.assertEqual(
            {r['action'] for r in project(self.work, self.user)['items']},
            {'already_reopened', 'unowned_during_rework'},
        )
        self.work.lifecycle_status = 'canceled'
        self.work.save(update_fields=['lifecycle_status'])
        self.assertFalse(project(self.work, self.user)['available'])
        self.assertEqual(decision(self.work, occurrence), 'ineligible')

    def test_review_unbound_and_archived_recurrence_are_unavailable(self):
        """An unobservable reference or archived prior Work never reopens."""
        self.closed(False)
        occurrence = self.deviation()
        self.assertEqual(project(self.work, self.user)['reason'], 'component_not_bound')
        AssetComponent.objects.create(
            machine=self.machine,
            code='UNBOUND',
            name='Unbound synthetic component',
            part=Part.objects.create(name='Unbound synthetic part'),
        )
        self.work.affected_component_ref = 'UNBOUND'
        self.work.save(update_fields=['affected_component_ref'])
        self.assertEqual(project(self.work, self.user)['reason'], 'component_not_bound')
        self.work.affected_component_ref = 'MOTOR-DE-BRG'
        self.work.save(update_fields=['affected_component_ref'])
        self.bind(occurrence)
        self.work.affected_component_ref = 'Unregistered human label'
        self.work.save(update_fields=['affected_component_ref'])
        self.assertFalse(project(self.work, self.user)['available'])
        self.work.affected_component_ref = 'MOTOR-DE-BRG'
        self.work.is_active = False
        self.work.save(update_fields=['affected_component_ref', 'is_active'])
        with self.assertRaises(ValidationError):
            self.review_recurrence(occurrence)
        self.assertEqual(
            project(self.work, self.user)['reason'], 'prior_work_inactive_or_canceled'
        )

    @override_settings(
        AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor',
        AIMMS_CLOSEOUT_AMENDMENTS_ENABLED=True,
    )
    def test_followup2_amendment_refused_during_rework_and_reclosure_succeeds(self):
        """Retained history cannot acquire new amendments which dead-end reclosure."""
        from tasks.services.closeout import complete_work_order
        from tasks.services.closeout_amend import AmendmentError, propose_amendment

        closeout = self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        self.review_recurrence(occurrence)
        with self.assertRaises(AmendmentError):
            propose_amendment(
                work_order_id=self.work.pk,
                actor=self.incoming,
                changes={'result': 'New proposed observation'},
                reason='Retain correction',
                expected_version=2,
                idempotency_key='reopen-amendment',
            )
        self.assertFalse(closeout.amendments.exists())
        self.work.refresh_from_db()
        self.work.lifecycle_status = 'verifying'
        self.work.save(update_fields=['lifecycle_status'])
        with patch(
            'tasks.services.closeout.evaluate_work_order_readiness',
            return_value=SimpleNamespace(ready=True),
        ):
            complete_work_order(
                work_order_id=self.work.pk,
                actor=self.incoming,
                expected_version=2,
                idempotency_key='reclose-amended',
                closeout={
                    'action': 'New action',
                    'result': 'New result',
                    'verification_summary': 'New summary',
                },
            )
        closeout.refresh_from_db()
        self.work.refresh_from_db()
        self.assertEqual(closeout.result, 'New result')
        self.assertEqual(self.work.lifecycle_status, 'completed')
        self.assertIsNotNone(self.work.actual_completed_at)
        self.assertEqual(closeout.amendments.count(), 0)
        retained = self.work.events.get(event_type='RECLOSURE_RETAINED').metadata[
            'previous_closeout'
        ]
        self.assertEqual(retained['result'], 'Original result')
        self.assertEqual(retained['content_hash'], 'original-hash')
        # A genuine later completion makes the current closure amendable again.
        propose_amendment(
            work_order_id=self.work.pk,
            actor=self.incoming,
            changes={'result': 'Attributed correction after completion'},
            reason='Current closure correction',
            expected_version=3,
            idempotency_key='after-reclosure-amendment',
        )
        self.assertEqual(closeout.amendments.count(), 1)

    def test_review_binding_rebinds_and_removed_or_invalid_evidence_never_retain(self):
        """Binding identity is a change source, and evidence must resolve and be fresh."""
        from copy import deepcopy

        old = {
            'id': 1,
            'version': 1,
            'current': False,
            'lines': [
                {
                    'action': 'Same line',
                    'dependencies': [
                        {
                            'kind': 'criteria',
                            'id': 'vib',
                            'procedure_revision_id': 10,
                            'evidence_ids': [1],
                        }
                    ],
                }
            ],
        }
        new = deepcopy(old)
        new.update(id=2, version=2, current=True)
        new['lines'][0]['dependencies'][0]['procedure_revision_id'] = 11
        result = self.scope_projection([old, new])['selective_reassessment']
        self.assertEqual(result['changed_procedure_revision_ids'], [10, 11])
        self.assertEqual(result['items'][-1]['state'], 'reassess')
        for ids in ([], [999999]):
            with self.subTest(ids=ids):
                later = deepcopy(new)
                later.update(id=3, version=3, current=True)
                later['lines'][0]['dependencies'][0]['evidence_ids'] = ids
                new['current'] = False
                value = scope_project(self.work, [old, new, later])[
                    'selective_reassessment'
                ]['items'][-1]
                self.assertEqual(value['state'], 'reassess')
                self.assertFalse(value['executable'])
        old['lines'][0]['dependencies'][0] = {
            'kind': 'tasks',
            'id': 'bearing',
            'affected_component_ref': 'BRG',
            'evidence_ids': [1],
        }
        new = deepcopy(old)
        new.update(id=2, version=2, current=True)
        new['lines'][0]['dependencies'][0]['affected_component_ref'] = 'CASING'
        value = scope_project(self.work, [old, new])['selective_reassessment']
        self.assertEqual(value['changed_component_refs'], ['BRG', 'CASING'])
        self.assertEqual(value['items'][-1]['state'], 'reassess')

    @override_settings(AIMMS_PROCEDURES_ENABLED=True)
    def test_review_scope_approval_endpoint_validates_and_overview_keeps_current_boundary(
        self,
    ):
        """Bindings travel through the real approval endpoint into the overview."""
        from copy import deepcopy

        from django.urls import reverse

        from repair.models import ApprovedRepairScope, RepairPacket

        self.scope_projection([])
        packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work,
            fault_summary='Synthetic scope review',
        )
        url = reverse('repair-packet-approved-scope', kwargs={'pk': packet.pk})
        line = {
            'action': 'Inspect',
            'affected_component_ref': ' BRG ',
            'dependencies': [
                {
                    'kind': 'tasks',
                    'id': 'lockout',
                    'affected_component_ref': ' BRG ',
                    'evidence_ids': [1],
                }
            ],
        }
        first = self.client.post(url, {'scope_lines': [line]}, format='json')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(
            first.data['scope_lines'][0]['dependencies'][0]['affected_component_ref'],
            'BRG',
        )
        for patch_line in (
            {'affected_component_ref': 'Missing'},
            {'procedure_revision_id': 999999},
            {
                'dependencies': [
                    {'kind': 'tasks', 'id': 'bad', 'evidence_ids': [999999]}
                ]
            },
            {'dependencies': [line['dependencies'][0]] * 2},
            {'dependencies': [{'kind': 'tasks', 'id': 'x' * 65}]},
            {
                'dependencies': [
                    {'kind': 'tasks', 'id': 'x', 'affected_component_ref': 'x' * 65}
                ]
            },
            {'dependencies': [{'kind': 'tasks', 'id': str(i)} for i in range(51)]},
            {
                'dependencies': [
                    {'kind': 'tasks', 'id': 'x', 'evidence_ids': list(range(1, 52))}
                ]
            },
        ):
            with self.subTest(invalid=patch_line):
                self.assertEqual(
                    self.client.post(
                        url, {'scope_lines': [{**line, **patch_line}]}, format='json'
                    ).status_code,
                    400,
                )
        from tasks.investigation_models import InvestigationArtifact
        from tasks.models import ProcedureRevision

        foreign_work = WorkOrder.objects.create(
            title='Other Work', machine=self.machine
        )
        foreign_request = InvestigationRequest.objects.create(
            work_order=foreign_work,
            purpose='Other context',
            next_check='Inspect',
            created_by=self.user,
            updated_by=self.user,
        )
        foreign_artifact = InvestigationArtifact.objects.create(
            request=foreign_request,
            kind='observation',
            payload={'text': 'Foreign context'},
            recorded_by=self.user,
        )
        foreign_revision = ProcedureRevision.objects.create(
            procedure_id=self.work.procedure_applications.first().revision.procedure_id,
            revision=99,
            work_order_type='corrective',
            created_by=self.user,
        )
        for invalid in (
            {
                'dependencies': [
                    {
                        'kind': 'tasks',
                        'id': 'foreign',
                        'evidence_ids': [foreign_artifact.pk],
                    }
                ]
            },
            {'procedure_revision_id': foreign_revision.pk},
        ):
            self.assertEqual(
                self.client.post(
                    url, {'scope_lines': [{**line, **invalid}]}, format='json'
                ).status_code,
                400,
            )
        second = deepcopy(line)
        second['action'] = 'Replace'
        self.assertEqual(
            self.client.post(url, {'scope_lines': [second]}, format='json').status_code,
            201,
        )
        overview = reverse('kanban-card-overview', kwargs={'pk': self.work.pk})
        review = self.client.get(overview).data['prepared_package']['revision_review']
        self.assertEqual(
            review['selective_reassessment']['items'][-1]['state'], 'reassess'
        )
        self.assertEqual(
            self.client.post(
                url,
                {'scope_lines': ['Plain current scope with unknown bindings']},
                format='json',
            ).status_code,
            201,
        )
        review = self.client.get(overview).data['prepared_package']['revision_review']
        self.assertTrue(all(not r['available'] for r in review['dependency_impact']))
        self.assertTrue(
            all(
                r['state'] == 'obsolete' and not r['executable']
                for r in review['selective_reassessment']['items']
            )
        )
        ApprovedRepairScope.objects.filter(packet=packet).update(superseded_at=None)
        review = self.client.get(overview).data['prepared_package']['revision_review']
        self.assertEqual(review['state'], 'conflicting_scope')
        self.assertTrue(
            all(
                r['state'] == 'effective_unknown'
                for r in review['selective_reassessment']['items']
            )
        )

    def test_review_verification_setting_is_registered_default_off(self):
        """The native bridge exposes a configurable policy and preserves real default off."""
        from django.conf import settings

        from aimms_flags import REGISTRY

        entry = next(
            row for row in REGISTRY if row.env_name == 'AIMMS_CLOSEOUT_VERIFY_POLICY'
        )
        self.assertEqual(
            (entry.default, entry.kind, entry.planes), ('off', 'str', 'django')
        )
        self.assertEqual(settings.AIMMS_CLOSEOUT_VERIFY_POLICY, 'off')
        self.assertEqual(entry.config_key, 'aimms_closeout_verify_policy')

    @override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor')
    def test_review_all_retained_provenance_blockers_offer_linked_work(self):
        """Capture, effects, amendments and packet ownership all retain a response path."""
        from repair.models import RepairPacket
        from tasks.closeout_models import (
            CloseoutAmendment,
            CloseoutCapture,
            CloseoutEffect,
        )

        for index, kind in enumerate(('capture', 'effects', 'amendments', 'packet'), 2):
            with self.subTest(kind=kind):
                self.machine = ensure_pump(self.station, 'P' + str(index))
                self.work = WorkOrder.objects.create(
                    title='Retained ' + kind,
                    machine=self.machine,
                    affected_component_ref='MOTOR-DE-BRG',
                    assigned_to=self.user,
                )
                closeout = self.closed(False)
                occurrence = self.deviation()
                self.bind(occurrence)
                if kind == 'capture':
                    CloseoutCapture.objects.create(
                        work_order=self.work,
                        source_type='typed',
                        created_by=self.user,
                        completed_closeout=closeout,
                    )
                elif kind == 'effects':
                    CloseoutEffect.objects.create(
                        closeout=closeout,
                        effect_type='notification',
                        effect_key='review-' + str(self.work.pk),
                        payload_hash='original-hash',
                    )
                elif kind == 'amendments':
                    CloseoutAmendment.objects.create(
                        closeout=closeout,
                        changes={'result': {'to': 'Proposed correction'}},
                        base_content_hash='original-hash',
                        reason='Retained proposal',
                        requested_by=self.user,
                    )
                else:
                    RepairPacket.objects.create(
                        machine=self.machine,
                        work_order=self.work,
                        fault_summary='Retained packet',
                    )
                value = project(self.work, self.user)['items'][0]
                self.assertEqual(value['action'], 'create_linked')
                self.assertIsNotNone(value['reopen_blocked_reason'])
                result = create_work_order(
                    actor=self.user,
                    idempotency_key='blocked-' + kind,
                    title='Linked response',
                    machine_id=self.machine.pk,
                    recurrence_of=self.work.pk,
                    recurrence_occurrence=occurrence.pk,
                    prior_version=1,
                )
                self.assertNotEqual(result.work_order_id, self.work.pk)
                self.work.refresh_from_db()
                closeout.refresh_from_db()
                self.assertEqual(
                    (self.work.lifecycle_status, closeout.result),
                    ('completed', 'Original result'),
                )

    def test_review_shared_dependency_rebind_preserves_unrelated_component_evidence(
        self,
    ):
        """Rebinding one shared task cannot invalidate its unchanged component binding."""
        from copy import deepcopy

        AssetComponent.objects.create(
            machine=self.machine,
            code='OTHER',
            name='Other component',
            part=Part.objects.create(name='Other component part'),
        )
        old = {
            'id': 1,
            'version': 1,
            'current': False,
            'lines': [
                {
                    'action': 'Unchanged line',
                    'dependencies': [
                        {
                            'kind': 'tasks',
                            'id': 'shared',
                            'affected_component_ref': component,
                            'evidence_ids': [evidence],
                        }
                    ],
                }
                for component, evidence in [('BRG', 1), ('CASING', 11)]
            ],
        }
        new = deepcopy(old)
        new.update(id=2, version=2, current=True)
        new['lines'][0]['dependencies'][0]['affected_component_ref'] = 'OTHER'
        value = self.scope_projection([old, new])['selective_reassessment']
        self.assertEqual(value['changed_component_refs'], ['BRG', 'OTHER'])
        self.assertEqual(
            [row['state'] for row in value['items'][-2:]], ['reassess', 'retained']
        )
        self.assertEqual(value['items'][-1]['retained_evidence_ids'], [11])
        # No scope change still does not turn missing evidence into retained proof.
        original = deepcopy(old)
        original['current'] = True
        original['lines'][0]['dependencies'][0]['evidence_ids'] = []
        row = scope_project(self.work, [original])['selective_reassessment']['items'][0]
        self.assertEqual(row['state'], 'evidence_unknown')
        self.assertFalse(row['executable'])

    def test_review_fresh_default_group_has_no_legacy_role_grants(self):
        """The new group must be created after the legacy all-groups permission grant."""
        from django.db.migrations.loader import MigrationLoader

        from users.models import RuleSet

        plan = MigrationLoader(connection).graph.forwards_plan((
            'tasks',
            '0035_process_decisions',
        ))
        self.assertLess(
            plan.index(('users', '0016_work_order_ruleset_grant')),
            plan.index(('tasks', '0035_process_decisions')),
        )
        group = Group.objects.get(name='Reliability engineers')
        self.assertFalse(
            RuleSet.objects.filter(
                group=group, name='work_order', can_view=True
            ).exists()
        )
        self.assertFalse(group.permissions.exists())

    def test_review_deleted_owner_is_not_resurrected_by_later_assignment(self):
        """Historical names survive, while unavailable current responsibility stays unknown."""
        self.propose()
        self.accept()
        self.incoming.delete()
        self.work.refresh_from_db()
        self.assertIsNone(
            continuity(self.work, self.user)['accepted_handover']['responsible_owner']
        )
        replacement = get_user_model().objects.create_user(username='replacement-owner')
        replacement.groups.add(self.group)
        replacement.user_permissions.add(Permission.objects.get(
            content_type__app_label='tasks', codename='execute_workorder'
        ))
        assign_work_order(
            work_order_id=self.work.pk,
            assigned_to=replacement,
            actor=self.user,
            expected_version=3,
            idempotency_key='replace-deleted',
        )
        latest = self.work.handovers.order_by('-pk').first()
        self.assertIsNone(latest.previous_owner_id)
        # A released legacy assignment can also have a deleted historical actor id.
        released = get_user_model().objects.create_user(
            username='deleted-released-owner'
        )
        work = WorkOrder.objects.create(
            title='Released historical owner',
            machine=self.machine,
            assigned_to=released,
        )
        assign_work_order(
            work_order_id=work.pk,
            assigned_to=None,
            actor=self.user,
            expected_version=1,
            idempotency_key='release-before-delete',
        )
        released.delete()
        work.refresh_from_db()
        self.assertIsNone(
            continuity(work, self.user)['accepted_handover']['responsible_owner']
        )
        assign_work_order(
            work_order_id=work.pk,
            assigned_to=replacement,
            actor=self.user,
            expected_version=2,
            idempotency_key='replace-released-deleted',
        )
        self.assertIsNone(work.handovers.get().previous_owner_id)

    @override_settings(AIMMS_CLOSEOUT_AMENDMENTS_ENABLED=True)
    def test_followup2_amendment_decisions_require_current_completion(self):
        """Neither lifecycle text nor a retained closeout can reopen amendment writes."""
        from tasks.services.closeout_amend import (
            AmendmentError,
            decide_amendment,
            propose_amendment,
        )

        closeout = self.closed(False)
        proposal = propose_amendment(
            work_order_id=self.work.pk,
            actor=self.incoming,
            changes={'result': 'Attributed proposed correction'},
            reason='Completed closure correction',
            expected_version=1,
            idempotency_key='completed-proposal',
        )
        self.work.events.create(
            event_type='RECURRENCE_REOPENED',
            from_status='completed',
            to_status='in_progress',
            actor=self.user,
            correlation_id=uuid4(),
        )
        for lifecycle in ('in_progress', 'verifying', 'completed'):
            self.work.lifecycle_status = lifecycle
            self.work.save(update_fields=['lifecycle_status'])
            with self.subTest(lifecycle=lifecycle):
                with self.assertRaises(AmendmentError):
                    propose_amendment(
                        work_order_id=self.work.pk,
                        actor=self.incoming,
                        changes={'result': 'Must not be saved'},
                        reason='Retained closure is historical',
                        expected_version=1,
                        idempotency_key='rework-proposal-' + lifecycle,
                    )
                for approve in (True, False):
                    with self.assertRaises(AmendmentError):
                        decide_amendment(
                            work_order_id=self.work.pk,
                            amendment_id=proposal.metadata['amendment_id'],
                            actor=self.incoming,
                            approve=approve,
                            expected_version=1,
                            idempotency_key=f'rework-decision-{lifecycle}-{approve}',
                        )
        amendment = closeout.amendments.get()
        self.assertEqual(amendment.status, 'proposed')
        self.assertIsNone(amendment.effective_snapshot)
        self.assertFalse(closeout.effects.exists())
        self.work.events.create(
            event_type='COMPLETED',
            from_status='verifying',
            to_status='completed',
            actor=self.user,
            correlation_id=uuid4(),
        )
        self.user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label='tasks', codename='verify_closeout'
            )
        )
        for name in ('_perm_cache', '_user_perm_cache', '_group_perm_cache'):
            self.user.__dict__.pop(name, None)
        decide_amendment(
            work_order_id=self.work.pk,
            amendment_id=amendment.pk,
            actor=self.user,
            approve=True,
            expected_version=1,
            idempotency_key='current-completion-decision',
        )
        amendment.refresh_from_db()
        self.assertEqual(amendment.status, 'applied')
        closeout.refresh_from_db()
        self.assertEqual(closeout.result, 'Original result')

    def test_followup2_binding_reappearance_through_approval_requires_fresh_evidence(
        self,
    ):
        """Missing intermediate bindings cannot reset procedure/component history."""
        from copy import deepcopy

        from django.urls import reverse

        from repair.models import RepairPacket
        from tasks.investigation_models import InvestigationArtifact

        self.scope_projection([])
        packet = RepairPacket.objects.create(
            machine=self.machine, work_order=self.work, fault_summary='Scope gap'
        )
        url = reverse('repair-packet-approved-scope', kwargs={'pk': packet.pk})
        overview = reverse('kanban-card-overview', kwargs={'pk': self.work.pk})
        binding = {
            'kind': 'criteria',
            'id': 'vib',
            'procedure_revision_id': 10,
            'evidence_ids': [1],
        }

        def approve(bound=True):
            line = {'action': 'Vibration test', 'dependencies': [deepcopy(binding)]}
            response = self.client.post(
                url,
                {'scope_lines': [line if bound else 'Vibration test']},
                format='json',
            )
            self.assertEqual(response.status_code, 201, response.data)
            return self.client.get(overview).data['prepared_package'][
                'revision_review'
            ]['selective_reassessment']

        self.assertEqual(approve()['items'][-1]['state'], 'retained')
        approve(False)
        binding['procedure_revision_id'] = 11
        value = approve()
        self.assertEqual(value['changed_procedure_revision_ids'], [10, 11])
        self.assertEqual(value['items'][-1]['state'], 'reassess')
        self.assertFalse(value['items'][-1]['executable'])
        self.assertEqual(value['items'][-1]['retained_evidence_ids'], [])
        fresh = InvestigationArtifact.objects.create(
            request=InvestigationRequest.objects.filter(work_order=self.work).first(),
            kind='observation',
            payload={'text': 'Evidence recorded after the reappearance'},
            recorded_by=self.user,
        )
        binding['evidence_ids'] = [fresh.pk]
        self.assertEqual(approve()['items'][-1]['state'], 'retained')
        approve(False)
        # Even an unchanged identity needs fresh evidence after a new absence.
        value = approve()
        self.assertEqual(value['items'][-1]['state'], 'reassess')
        self.assertEqual(value['items'][-1]['retained_evidence_ids'], [])
        del binding['procedure_revision_id']
        binding['affected_component_ref'] = 'BRG'
        approve()
        approve(False)
        binding['affected_component_ref'] = 'CASING'
        value = approve()
        self.assertEqual(value['changed_component_refs'], ['BRG', 'CASING'])
        self.assertEqual(value['items'][-1]['state'], 'reassess')
        self.assertFalse(value['items'][-1]['executable'])

    def test_followup2_foreign_missing_and_inactive_recurrence_refuse_identically(self):
        """The HTTP command cannot reveal foreign Work on an in-scope machine."""
        from assets.models import AssetMachine, Client

        self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        foreign_client = Client.objects.create(name='Foreign recurrence client', code='o24-recurrence-other')
        self.work.machine = AssetMachine.objects.create(name='O24 foreign recurrence pump', client=foreign_client)
        self.work.save(update_fields=['machine'])
        body = {
            'title': 'Corrective response',
            'machine': self.machine.pk,
            'recurrence_of': self.work.pk,
            'recurrence_occurrence': occurrence.pk,
            'prior_version': 1,
            'idempotency_key': 'uniform-recurrence-refusal',
        }
        url = '/api/kanban/work-orders/commands/create/'
        before = WorkOrder.objects.count()
        foreign = self.client.post(url, body, format='json')
        missing = self.client.post(
            url, {**body, 'recurrence_of': self.work.pk + 999999}, format='json'
        )
        self.work.machine = self.machine
        self.work.is_active = False
        self.work.save(update_fields=['machine', 'is_active'])
        inactive = self.client.post(url, body, format='json')
        for response in (foreign, missing, inactive):
            self.assertEqual(response.status_code, 400, response.data)
            self.assertEqual(
                str(response.data[0]),
                'No eligible same-component deviation within the closure window.',
            )
        self.assertEqual(foreign.data, missing.data)
        self.assertEqual(foreign.data, inactive.data)
        self.assertEqual(WorkOrder.objects.count(), before)
        occurrence.refresh_from_db()
        self.assertIsNone(occurrence.work_order_id)
        self.assertFalse(
            self.work.events.filter(event_type='RECURRENCE_REOPENED').exists()
        )

    def test_followup2_long_titles_refuse_before_writes_and_bounded_link_succeeds(self):
        """SQLite must enforce the same title bound before PostgreSQL insertion."""
        from tasks.services.work_orders import WorkOrderCommandError

        self.closed(False)
        occurrence = self.deviation()
        self.bind(occurrence)
        occurrence.title = 'D' * 255
        occurrence.save(update_fields=['title'])
        before = WorkOrder.objects.count()
        with self.assertRaisesRegex(WorkOrderCommandError, 'at most 200'):
            create_work_order(
                actor=self.user,
                title=occurrence.title,
                machine_id=self.machine.pk,
                idempotency_key='overlong-native-create',
            )
        body = {
            'title': occurrence.title,
            'machine': self.machine.pk,
            'recurrence_of': self.work.pk,
            'recurrence_occurrence': occurrence.pk,
            'prior_version': 1,
            'idempotency_key': 'bounded-recurrence-title',
        }
        url = '/api/kanban/work-orders/commands/create/'
        for invalid in (occurrence.title, '🙂' * 201, ['Not text']):
            response = self.client.post(url, {**body, 'title': invalid}, format='json')
            self.assertEqual(response.status_code, 400, response.data)
            self.assertIn('at most 200', response.data['detail'])
        self.assertEqual(WorkOrder.objects.count(), before)
        occurrence.refresh_from_db()
        self.assertIsNone(occurrence.work_order_id)
        response = self.client.post(
            url, {**body, 'title': occurrence.title[:200]}, format='json'
        )
        self.assertEqual(response.status_code, 201, response.data)
        linked = WorkOrder.objects.get(pk=response.data['id'])
        self.assertEqual(linked.title, occurrence.title[:200])
        self.assertEqual(linked.recurrence_occurrence_id, occurrence.pk)
        self.assertEqual(occurrence.title, 'D' * 255)
        # A later validation rule cannot discard an original committed receipt.
        with patch.object(WorkOrder._meta.get_field('title'), 'max_length', 199):
            replay = self.client.post(
                url, {**body, 'title': occurrence.title[:200]}, format='json'
            )
        self.assertEqual(replay.status_code, 201, replay.data)
        self.assertEqual(
            replay.data['process_receipt'], response.data['process_receipt']
        )
        unicode_work = create_work_order(
            actor=self.user,
            title='🙂' * 200,
            machine_id=self.machine.pk,
            idempotency_key='unicode-native-title',
        )
        self.assertEqual(
            len(WorkOrder.objects.get(pk=unicode_work.work_order_id).title), 200
        )


def review_scope_resolver(actor):
    """Synthetic normal expert outside this Work's native client scope."""
    from tasks.scope import MaintenanceScope, single_site_scope_resolver

    if actor.username == 'foreign-review-expert':
        return [MaintenanceScope(customer_id=999999, site_key=None)]
    return single_site_scope_resolver(actor)
