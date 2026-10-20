"""P41 request records preserve scope, exact retries and typed evidence."""

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from django.urls import reverse

from rest_framework.exceptions import ValidationError

from tasks.investigation_models import InvestigationArtifact, InvestigationRequest
from tasks.models import WorkOrder, WorkOrderEvent
from tasks.services.investigation_requests import command
from tasks.services.work_orders import IdempotencyConflict, StaleVersion
from tasks.tests.test_work_execution_investigation import InvestigationProjectionTest


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver'
)
class InvestigationRequestTests(InvestigationProjectionTest):
    """Exercise the native command ledger and scoped HTTP reads offline."""

    roles = 'all'

    def setUp(self):
        """Add the native change role explicitly; the all-role fixture grants view."""
        super().setUp()
        self.assignRole('work_order.change')

    def apply(self, operation='create', fields=None, row=None, key='create', **extra):
        """Use one exact intent for both the first submission and replay."""
        return command(
            work_order_id=self.work_order.pk,
            actor=self.user,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
            operation=operation,
            fields=fields
            if fields is not None
            else {
                'purpose': 'Distinguish bearing from guard noise',
                'next_check': 'Record readings',
                'priority': 2,
                'required_artifacts': [
                    {
                        'kind': 'reading_set',
                        'description': 'DE envelope',
                        'unit': 'g',
                        'conditions': 'Approved accessible measurement point',
                    }
                ],
            },
            request_id=row.pk if row else None,
            request_version=row.version if row else None,
            **extra,
        )

    def test_unset_priority_is_nullable_explicit_priorities_sort_first(self):
        """Unprioritized creation never impersonates the next priority check."""
        fields = {
            'purpose': 'Synthetic unknown priority',
            'next_check': 'Check',
            'required_artifacts': [],
        }
        self.apply(fields=fields, key='unset')
        self.apply(fields={**fields, 'priority': 4}, key='priority4')
        self.apply(fields={**fields, 'priority': 2}, key='priority2')
        self.apply(fields={**fields, 'priority': None}, key='null')
        rows = list(InvestigationRequest.objects.all())
        self.assertEqual([r.priority for r in rows], [2, 4, None, None])
        self.assertLess(rows[2].pk, rows[3].pk)
        indexes = connection.introspection.get_constraints(
            connection.cursor(), InvestigationRequest._meta.db_table
        )
        self.assertEqual(
            indexes['tasks_inv_work_status_idx']['columns'], ['work_order_id', 'status']
        )

    def test_overview_supplies_recorded_component_for_native_capture(self):
        """Capture reuses the Work's own component fields rather than inferring fit."""
        self.work_order.affected_component = 'Synthetic retained coupling'
        self.work_order.affected_component_ref = 'SYN-COUPLING-18'
        self.work_order.save(
            update_fields=['affected_component', 'affected_component_ref']
        )
        overview = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work_order.pk}),
            expected_code=200,
        ).data
        self.assertEqual(overview['affected_component'], 'Synthetic retained coupling')
        self.assertEqual(overview['affected_component_ref'], 'SYN-COUPLING-18')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_version, 1)

    def test_choice_constraints_reject_unknown_orm_status_and_kind(self):
        """TextChoices and database checks protect non-serializer write paths."""
        self.apply()
        row = InvestigationRequest.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            InvestigationRequest.objects.filter(pk=row.pk).update(status='healthy')
        with self.assertRaises(IntegrityError), transaction.atomic():
            InvestigationArtifact.objects.create(
                request=row, recorded_by=self.user, kind='anything', payload={}
            )

    def test_create_replay_and_conflicting_intent(self):
        """An uncertain retry returns the original result and creates no duplicate."""
        first = self.apply()
        self.assertEqual(self.apply(), first)
        self.assertEqual(InvestigationRequest.objects.count(), 1)
        self.assertEqual(WorkOrderEvent.objects.count(), 1)
        with self.assertRaises(IdempotencyConflict):
            self.apply(fields={'purpose': 'Different'})

    def test_queue_names_the_recorded_updater_with_native_username_fallback(self):
        """The read projection returns a display name, never a numeric label."""
        from tasks.services.investigation_requests import project_requests

        self.user.first_name = 'Taylor'
        self.user.last_name = 'Reed'
        self.user.save(update_fields=['first_name', 'last_name'])
        self.apply()
        self.assertEqual(
            project_requests(self.work_order)['items'][0]['updated_by_name'],
            'Taylor Reed',
        )
        self.assertEqual(
            project_requests(self.work_order)['items'][0]['created_by_name'],
            'Taylor Reed',
        )
        self.user.first_name = ''
        self.user.last_name = ''
        self.user.save(update_fields=['first_name', 'last_name'])
        self.assertEqual(
            project_requests(self.work_order)['items'][0]['updated_by_name'],
            self.user.username,
        )

    def test_revision_preserves_before_after_and_rejects_stale(self):
        """Request revision has a compact audit receipt independent of Work lifecycle."""
        self.apply()
        row = InvestigationRequest.objects.get()
        result = self.apply(
            'revise', {'next_check': 'Second instrument'}, row, key='revise'
        )
        self.assertEqual((result['version_before'], result['version_after']), (1, 2))
        self.assertEqual(result['request']['version'], 2)
        with self.assertRaises(StaleVersion):
            self.apply('revise', {'next_check': 'Stale'}, row, key='stale')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_version, 1)

    def test_multiple_rounds_and_compound_lab_keep_provenance(self):
        """Repeated observations are append-only; evidence never resolves a request."""
        self.apply()
        row = InvestigationRequest.objects.get()
        lab = {
            'sample': 'S-01',
            'method': 'Named laboratory method',
            'observed_at': '2026-10-19T09:00:00Z',
            'source': 'Lab report',
            'status': 'pending',
            'results': [{'name': 'Particle count', 'value': None, 'unit': 'count/mL'}],
        }
        for i in range(5):
            self.apply(
                'artifact',
                {'kind': 'laboratory', 'payload': lab},
                row,
                key=f'round-{i}',
            )
            row.refresh_from_db()
        self.assertEqual(row.artifacts.count(), 5)
        self.assertEqual(row.artifacts.first().payload, lab)
        self.assertEqual(row.status, 'open')
        with self.assertRaises(ValueError):
            row.artifacts.first().save()

    def test_lab_cannot_be_replaced_with_snapshot_or_untyped_reading(self):
        """Type and compound provenance are required, including for absent results."""
        self.apply()
        row = InvestigationRequest.objects.get()
        for payload in ({'snapshot_id': '123'}, {'sample': 'S-1', 'results': [0]}):
            with self.assertRaises(ValidationError):
                self.apply(
                    'artifact',
                    {'kind': 'laboratory', 'payload': payload},
                    row,
                    key=str(payload),
                )
        self.assertFalse(InvestigationArtifact.objects.exists())

    def test_foreign_finding_proposal_owner_and_artifact_rejected(self):
        """Same client/machine does not prove ownership of evidence or finding."""
        self.apply()
        row = InvestigationRequest.objects.get()
        from django.contrib.auth.models import Group

        from assets.models import Client, ClientScopeGrant
        from repair.models import RepairInvestigationFinding, RepairPacket
        from users.models import RuleSet

        other_work = WorkOrder.objects.create(
            title='Real other Work', machine=self.machine
        )
        other_packet = RepairPacket.objects.create(
            machine=self.machine, work_order=other_work, fault_summary='Other'
        )
        other_packet.diagnosis = {
            'confirm_tests': ['Foreign named check'],
            'generated_at': '2026-10-19T09:00:00Z',
        }
        other_packet.save(update_fields=['diagnosis'])
        foreign_finding = RepairInvestigationFinding.objects.create(
            packet=other_packet,
            finding_key='foreign',
            category='measurement',
            observation='Other Work finding',
        )
        sibling = InvestigationRequest.objects.create(
            work_order=self.work_order,
            purpose='Sibling',
            next_check='Sibling',
            created_by=self.user,
            updated_by=self.user,
        )
        foreign_artifact = InvestigationArtifact.objects.create(
            request=sibling,
            kind='observation',
            payload={'text': 'Sibling artifact'},
            recorded_by=self.user,
        )
        with self.assertRaises(ValidationError):
            self.apply(
                'revise',
                {'source_finding_id': foreign_finding.pk},
                row,
                key='foreign-finding',
            )
        with self.assertRaises(ValidationError):
            self.apply(
                'revise',
                {
                    'source_proposal': {
                        'packet_id': other_packet.pk,
                        'check': 'Foreign named check',
                        'generated_at': '2026-10-19T09:00:00Z',
                    }
                },
                row,
                key='foreign-proposal',
            )
        # Distinct people keep the native role cache from masking the scope check.
        with self.assertRaises(ValidationError):
            self.apply('revise', {'owner_id': self.tech.pk}, row, key='viewless-owner')
        from django.contrib.auth import get_user_model

        from users.permissions import check_user_role

        scoped_owner = get_user_model().objects.create_user(
            username='foreign-scope-owner'
        )
        group = Group.objects.create(name='Foreign owner readers')
        scoped_owner.groups.add(group)
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_view': True}
        )
        client = Client.objects.create(name='Foreign owner', code='foreign-owner')
        ClientScopeGrant.objects.create(user=scoped_owner, client=client)
        self.assertTrue(check_user_role(scoped_owner, 'work_order', 'view'))
        with override_settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
        ):
            with self.assertRaises(ValidationError):
                self.apply(
                    'revise',
                    {'owner_id': scoped_owner.pk},
                    row,
                    key='out-of-scope-owner',
                )
        with self.assertRaises(ValidationError):
            self.apply(
                'revise',
                {
                    'source_proposal': {
                        'packet_id': self.packet.pk,
                        'check': 'Invented check',
                        'generated_at': None,
                    }
                },
                row,
                key='proposal',
            )
        with self.assertRaises(ValidationError):
            self.apply(
                'artifact',
                {
                    'kind': 'observation',
                    'payload': {'text': 'Correction'},
                    'supersedes_id': foreign_artifact.pk,
                },
                row,
                key='wrong-original',
            )

    def test_read_endpoint_and_overview_use_same_canonical_ids(self):
        """The queue and detail endpoint project the same saved request/evidence."""
        result = self.apply()
        url = reverse('work-investigation-requests', kwargs={'pk': self.work_order.pk})
        data = self.get(url, expected_code=200).data
        self.assertEqual(data['items'][0]['id'], result['request']['id'])
        overview = self.get(
            reverse('kanban-card-overview', kwargs={'pk': self.work_order.pk}),
            expected_code=200,
        ).data
        self.assertEqual(overview['investigation']['requests'], data)
        other = WorkOrder.objects.create(title='Other Work', machine=self.machine)
        self.get(
            reverse(
                'work-investigation-request',
                kwargs={'pk': other.pk, 'request_id': result['request']['id']},
            ),
            expected_code=404,
        )

    def test_cannot_perform_stays_unresolved_and_retains_other_evidence(self):
        """An unavailable check needs a reason, and only a later decision resolves it."""
        self.apply()
        row = InvestigationRequest.objects.get()
        self.apply(
            'artifact',
            {'kind': 'observation', 'payload': {'text': 'Original observation'}},
            row,
            key='observation',
        )
        row.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.apply(
                'revise', {'status': 'cannot_perform'}, row, key='missing-reason'
            )
        self.apply(
            'revise',
            {
                'status': 'cannot_perform',
                'blocked_reason': 'Calibrated instrument unavailable',
            },
            row,
            key='blocked',
        )
        row.refresh_from_db()
        self.assertEqual(row.status, 'cannot_perform')
        self.assertIsNone(row.owner_id)
        self.assertEqual(row.artifacts.count(), 1)
        with self.assertRaises(ValidationError):
            self.apply('revise', {'status': 'resolved'}, row, key='false-resolution')

    def test_command_rechecks_change_permission_on_replay(self):
        """A successful prior write is no authority after a role is revoked."""
        self.apply()
        from users.models import RuleSet

        self.user.is_superuser = False
        self.user.save(update_fields=['is_superuser'])
        RuleSet.objects.filter(group__user=self.user, name='work_order').update(
            can_change=False
        )
        with self.assertRaises(PermissionDenied):
            self.apply()

    def test_migration_state_matches_models(self):
        """An offline migration check covers the new schema without Azure access."""
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.state import ProjectState

        executor = MigrationExecutor(connection)
        changes = MigrationAutodetector(
            executor.loader.project_state(),
            ProjectState.from_apps(__import__('django').apps.apps),
        ).changes(graph=executor.loader.graph, trim_to_apps={'tasks', 'repair'})
        self.assertNotIn('tasks', changes)
        self.assertNotIn('repair', changes)
        # Pin the additive approval choice without absorbing unrelated app drift.
        from aichat.models import ChatActionProposal

        migrated = executor.loader.project_state().apps.get_model(
            'aichat', 'ChatActionProposal'
        )
        self.assertEqual(
            migrated._meta.get_field('action_type').deconstruct(),
            ChatActionProposal._meta.get_field('action_type').deconstruct(),
        )

    def test_unavailable_check_result_cannot_satisfy_required_check(self):
        """A cannot-perform response remains unresolved even with a resolution note."""
        self.apply(
            fields={
                'purpose': 'Resolve test ambiguity',
                'next_check': 'Named check',
                'priority': 1,
                'required_artifacts': [
                    {'kind': 'check_result', 'description': 'Named check result'}
                ],
            }
        )
        row = InvestigationRequest.objects.get()
        self.apply(
            'artifact',
            {
                'kind': 'check_result',
                'payload': {
                    'text': 'Cannot safely perform',
                    'status': 'cannot_perform',
                },
            },
            row,
            key='cannot-check',
        )
        row.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.apply(
                'revise',
                {
                    'status': 'resolved',
                    'resolution': 'A note alone cannot resolve it',
                    'owner_id': self.user.pk,
                },
                row,
                key='false-check-resolution',
            )
        row.refresh_from_db()
        self.assertEqual(row.status, 'cannot_perform')

    def test_resolved_request_requires_explicit_reopen_before_new_evidence(self):
        """A pending correction cannot silently replace evidence under Resolved."""
        self.apply(
            fields={
                'purpose': 'Read lab result',
                'next_check': 'Named lab method',
                'priority': 1,
                'required_artifacts': [
                    {'kind': 'laboratory', 'description': 'Lab result'}
                ],
            }
        )
        row = InvestigationRequest.objects.get()
        lab = {
            'sample': 'S-1',
            'method': 'Named method',
            'observed_at': '2026-10-19T09:00:00Z',
            'source': 'Named lab',
            'status': 'final',
            'results': [{'name': 'Particle count', 'value': 3, 'unit': 'count/mL'}],
        }
        self.apply(
            'artifact', {'kind': 'laboratory', 'payload': lab}, row, key='lab-final'
        )
        row.refresh_from_db()
        self.apply(
            'revise',
            {
                'status': 'resolved',
                'resolution': 'Qualified decision recorded',
                'owner_id': self.user.pk,
            },
            row,
            key='resolve',
        )
        row.refresh_from_db()
        lab['status'] = 'pending'
        with self.assertRaises(ValidationError):
            self.apply(
                'artifact',
                {
                    'kind': 'laboratory',
                    'payload': lab,
                    'supersedes_id': row.artifacts.first().pk,
                },
                row,
                key='pending-correction',
            )
        row.refresh_from_db()
        self.assertEqual(row.status, 'resolved')
        self.assertEqual(row.artifacts.count(), 1)

    def test_native_photo_and_report_links_stay_inside_work(self):
        """Multiple files use existing attachment ids, with exact retry identity."""
        from common.models import Attachment

        self.apply()
        row = InvestigationRequest.objects.get()
        photo = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            is_image=True,
            link='https://example.invalid/synthetic-photo',
        )
        # Synthetic metadata fixture only; no physical image/storage acceptance.
        Attachment.objects.filter(pk=photo.pk).update(is_image=True)
        report = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            link='https://example.invalid/synthetic-report',
        )
        for i, (kind, attachment) in enumerate([
            ('photo', photo),
            ('photo', photo),
            ('report', report),
        ]):
            data = {
                'kind': kind,
                'attachment_id': attachment.pk,
                'payload': {
                    'description': 'Synthetic file',
                    'source': 'Synthetic Work capture',
                    'status': 'final',
                },
            }
            first = self.apply('artifact', data, row, key=f'file-{i}')
            self.assertEqual(self.apply('artifact', data, row, key=f'file-{i}'), first)
            row.refresh_from_db()
        other = Attachment.objects.create(
            model_type='workorder',
            model_id=WorkOrder.objects.create(
                title='Real other Work', machine=self.machine
            ).pk,
            is_image=True,
            link='https://example.invalid/foreign',
        )
        with self.assertRaises(ValidationError):
            self.apply(
                'artifact',
                {
                    'kind': 'photo',
                    'attachment_id': other.pk,
                    'payload': {'description': 'Foreign'},
                },
                row,
                key='foreign-file',
            )
        self.assertEqual(row.artifacts.count(), 3)

    def test_scope_first_http_read_and_authenticated_command(self):
        """Foreign clients return 404, and the HTTP writer uses the native service."""
        from assets.models import AssetMachine, Client

        foreign = AssetMachine.objects.create(
            name='Foreign',
            client=Client.objects.create(name='Other', code='other-investigation'),
        )
        work = WorkOrder.objects.create(title='Foreign Work', machine=foreign)
        self.get(
            reverse('work-investigation-requests', kwargs={'pk': work.pk}),
            expected_code=404,
        )
        url = reverse('work-investigation-requests', kwargs={'pk': self.work_order.pk})
        data = {
            'operation': 'create',
            'expected_version': self.work_order.lifecycle_version,
            'idempotency_key': 'http-create',
            'fields': {
                'purpose': 'Synthetic decision',
                'next_check': 'Synthetic check',
                'priority': 1,
                'required_artifacts': [],
            },
        }
        response = self.post(url, data, expected_code=200)
        self.assertEqual(self.post(url, data, expected_code=200).data, response.data)
        self.assertEqual(InvestigationRequest.objects.count(), 1)

    def test_partial_revision_requires_complete_nested_requirements(self):
        """Partial applies to the request, never to an individual requirement."""
        self.apply()
        row = InvestigationRequest.objects.get()
        for item in (
            {},
            {'unit': 'g'},
            {'kind': 'reading_set'},
            {'description': 'Missing kind'},
        ):
            with self.subTest(item=item), self.assertRaises(ValidationError):
                self.apply('revise', {'required_artifacts': [item]}, row, key=str(item))
        row.refresh_from_db()
        self.assertEqual(row.version, 1)
        InvestigationRequest.objects.filter(pk=row.pk).update(required_artifacts=[{}])
        row.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.apply(
                'revise',
                {
                    'status': 'resolved',
                    'resolution': 'Review',
                    'owner_id': self.user.pk,
                },
                row,
                key='malformed-resolution',
            )

    def test_each_required_reading_claims_a_distinct_current_artifact(self):
        """One reading set cannot satisfy both listed measurement requirements."""
        self.apply()
        row = InvestigationRequest.objects.get()
        requirements = [
            *row.required_artifacts,
            {'kind': 'reading_set', 'description': 'NDE temperature', 'unit': 'C'},
        ]
        self.apply(
            'revise', {'required_artifacts': requirements}, row, key='two-required'
        )
        row.refresh_from_db()
        fields = {
            'kind': 'reading_set',
            'payload': {
                'readings': [{'name': 'DE envelope', 'value': 1, 'unit': 'g'}],
                'conditions': 'Accessible',
                'observed_at': '2026-10-19T09:00:00Z',
            },
        }
        self.apply('artifact', fields, row, key='first-required')
        row.refresh_from_db()
        resolution = {
            'status': 'resolved',
            'resolution': 'Owner reviewed',
            'owner_id': self.user.pk,
        }
        with self.assertRaises(ValidationError):
            self.apply('revise', resolution, row, key='missing-second')
        fields['payload']['readings'] = [
            {'name': 'NDE temperature', 'value': 24, 'unit': 'C'}
        ]
        self.apply('artifact', fields, row, key='second-required')
        row.refresh_from_db()
        self.apply('revise', resolution, row, key='both-present')

    def test_event_metadata_stays_compact_and_detail_queries_stay_constant(self):
        """Growing evidence does not copy prior payloads into every audit event."""
        from django.test.utils import CaptureQueriesContext

        from tasks.services.investigation_requests import project_requests

        self.apply()
        row = InvestigationRequest.objects.get()
        for i in range(8):
            self.apply(
                'artifact',
                {'kind': 'observation', 'payload': {'text': 'x' * 1000}},
                row,
                key=f'compact-{i}',
            )
            row.refresh_from_db()
        metadata = (
            WorkOrderEvent.objects.order_by('-pk').first().metadata['result_metadata']
        )
        self.assertNotIn('request', metadata)
        self.assertNotIn('before', metadata)
        self.assertEqual(metadata['version_after'], 9)
        with CaptureQueriesContext(connection) as queries:
            project_requests(self.work_order)
        self.assertLessEqual(len(queries), 3)
        self.apply(
            'revise', {'next_check': 'Revised check'}, row, key='compact-revision'
        )
        revision = (
            WorkOrderEvent.objects
            .order_by('-pk')
            .first()
            .metadata['result_metadata']['request_revision']
        )
        self.assertNotIn('artifacts', revision['before'])
        self.assertNotIn('artifacts', revision['after'])
        self.assertEqual(
            revision['artifact_through_id'], row.artifacts.order_by('-pk').first().pk
        )

    def test_native_attachment_delete_retains_evidence_snapshot(self):
        """Native file deletion succeeds while the attributed evidence stays saved."""
        from common.models import Attachment

        self.assignRole('work_order.delete')
        self.apply()
        row = InvestigationRequest.objects.get()
        saved = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            link='https://example.invalid/retained-report',
            comment='Saved description',
        )
        attachment_id = saved.pk
        self.apply(
            'artifact',
            {
                'kind': 'report',
                'attachment_id': saved.pk,
                'payload': {
                    'description': 'Retained',
                    'source': 'Technician',
                    'status': 'final',
                },
            },
            row,
            key='retained-file',
        )
        self.delete(
            reverse('api-attachment-detail', kwargs={'pk': saved.pk}), expected_code=204
        )
        artifact = InvestigationArtifact.objects.get()
        self.assertIsNone(artifact.attachment_id)
        self.assertEqual(artifact.attachment_snapshot['id'], attachment_id)
        self.assertEqual(
            artifact.attachment_snapshot['link'],
            'https://example.invalid/retained-report',
        )
        self.assertEqual(artifact.payload['description'], 'Retained')

    def test_work_deletion_refuses_before_any_files_are_removed(self):
        """Fileless, attached and corrected evidence all retain the same protection."""
        from unittest import mock

        from common.models import Attachment
        from tasks.services.scheduling import ProtectedWorkOrder, delete_work_order

        self.apply()
        row = InvestigationRequest.objects.get()
        first = InvestigationArtifact.objects.create(
            request=row,
            kind='observation',
            payload={'text': 'Original'},
            recorded_by=self.user,
        )
        InvestigationArtifact.objects.create(
            request=row,
            kind='observation',
            payload={'text': 'Corrected'},
            recorded_by=self.user,
            supersedes=first,
        )
        Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            link='https://example.invalid/unrelated',
        )
        with mock.patch.object(
            Attachment, 'delete', side_effect=AssertionError('file deletion began')
        ):
            with self.assertRaisesMessage(ProtectedWorkOrder, 'investigation'):
                delete_work_order(
                    work_order_id=self.work_order.pk,
                    actor=self.user,
                    expected_version=1,
                    idempotency_key='retain-investigation',
                )
        self.assertEqual(row.artifacts.count(), 2)

    def test_reopen_clears_current_blocker_but_retains_original_outcome(self):
        """An explicit reopen removes the active warning without erasing its evidence."""
        self.apply()
        row = InvestigationRequest.objects.get()
        self.apply(
            'artifact',
            {
                'kind': 'check_result',
                'payload': {
                    'text': 'Instrument unavailable',
                    'status': 'cannot_perform',
                },
            },
            row,
            key='unavailable',
        )
        row.refresh_from_db()
        self.apply('revise', {'status': 'open'}, row, key='reopen')
        row.refresh_from_db()
        self.assertEqual((row.status, row.blocked_reason), ('open', ''))
        self.assertEqual(row.artifacts.get().payload['text'], 'Instrument unavailable')

    def test_detail_get_query_count_does_not_grow_with_artifacts(self):
        """The actual detail endpoint uses the same prefetched projection as the queue."""
        from django.test.utils import CaptureQueriesContext

        self.apply()
        row = InvestigationRequest.objects.get()
        url = reverse(
            'work-investigation-request',
            kwargs={'pk': self.work_order.pk, 'request_id': row.pk},
        )
        InvestigationArtifact.objects.create(
            request=row,
            kind='observation',
            payload={'text': 'Baseline'},
            recorded_by=self.user,
        )
        # Warm authorization caches and compare nonempty queues on both reads.
        self.get(url, expected_code=200)
        with CaptureQueriesContext(connection) as small:
            self.get(url, expected_code=200)
        for i in range(8):
            InvestigationArtifact.objects.create(
                request=row,
                kind='observation',
                payload={'text': str(i)},
                recorded_by=self.user,
            )
        with CaptureQueriesContext(connection) as large:
            self.get(url, expected_code=200)
        self.assertLessEqual(len(large), len(small))

    def test_native_work_delete_returns_409_and_direct_delete_preflights(self):
        """Even an empty saved request protects Work before attachment deletion."""
        from unittest import mock

        from django.db.models.deletion import ProtectedError

        from common.models import Attachment

        self.assignRole('work_order.delete')
        self.apply()
        Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            link='https://example.invalid/native-unrelated',
        )
        with mock.patch.object(
            Attachment, 'delete', side_effect=AssertionError('file deletion began')
        ):
            self.post(
                reverse('kanban-command-delete', kwargs={'pk': self.work_order.pk}),
                {'expected_version': 1, 'idempotency_key': 'native-retain'},
                expected_code=409,
            )
            with self.assertRaises(ProtectedError):
                self.work_order.delete()
            with self.assertRaises(ProtectedError):
                WorkOrder.objects.filter(pk=self.work_order.pk).delete()

    def test_missing_reading_text_never_resolves_but_zero_is_recorded(self):
        """Blank/whitespace results are unknown; a supplied zero remains evidence."""
        self.apply()
        row = InvestigationRequest.objects.get()
        resolution = {
            'status': 'resolved',
            'resolution': 'Owner reviewed',
            'owner_id': self.user.pk,
        }
        for index, value in enumerate(('', '   ', None, 0)):
            self.apply(
                'artifact',
                {
                    'kind': 'reading_set',
                    'payload': {
                        'readings': [
                            {'name': 'DE envelope', 'value': value, 'unit': 'g'}
                        ],
                        'conditions': 'Technician supplied',
                        'observed_at': '2026-10-19T09:00:00Z',
                    },
                },
                row,
                key=f'missing-value-{index}',
            )
            row.refresh_from_db()
            if value == 0:
                self.apply('revise', resolution, row, key='recorded-zero')
            else:
                with self.assertRaises(ValidationError):
                    self.apply('revise', resolution, row, key=f'unknown-{index}')
        row.refresh_from_db()
        self.assertEqual(row.status, 'resolved')
