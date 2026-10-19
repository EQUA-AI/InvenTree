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

    def test_revision_preserves_before_after_and_rejects_stale(self):
        """Request revision is independent of Work lifecycle, with audit snapshots."""
        self.apply()
        row = InvestigationRequest.objects.get()
        result = self.apply(
            'revise', {'next_check': 'Second instrument'}, row, key='revise'
        )
        self.assertEqual(result['before']['next_check'], 'Record readings')
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
        with self.assertRaises(ValidationError):
            self.apply(
                'revise', {'source_finding_id': 999999}, row, key='foreign-finding'
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
                    'supersedes_id': 99999,
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
            model_id=self.work_order.pk + 1000,
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
