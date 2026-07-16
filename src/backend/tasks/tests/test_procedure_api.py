"""API contract tests for governed procedure authoring and review."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from approvals.models import ActionType, Approval
from company.models import Company
from tasks.models import (
    Procedure,
    ProcedureRevision,
    ProcedureRevisionStatus,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope


@override_settings(AIMMS_PROCEDURES_ENABLED=True)
class ProcedureAPITest(TestCase):
    """Exercise the scoped authoring API and approval boundary."""

    list_url = '/api/tasks/procedures/'

    def setUp(self):
        self.customer = Company.objects.create(name='Procedure API Customer', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='procedure-author', email='author@example.com', password='test'
        )
        self.reviewer = get_user_model().objects.create_superuser(
            username='procedure-reviewer', email='reviewer@example.com', password='test'
        )
        scope = {MaintenanceScope(customer_id=self.customer.pk, site_key=None)}
        self.actor.maintenance_scopes = scope
        self.reviewer.maintenance_scopes = scope
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def create_procedure_and_revision(self):
        response = self.client.post(
            self.list_url,
            {'code': 'PM-API-001', 'name': 'API inspection', 'customer': self.customer.pk},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        procedure_id = response.json()['id']
        response = self.client.post(f'/api/tasks/procedures/{procedure_id}/revisions/', {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return Procedure.objects.get(pk=procedure_id), ProcedureRevision.objects.get(pk=response.json()['id'])

    def add_step(self, revision, **overrides):
        data = {
            'expected_content_version': revision.content_version,
            'sequence': 1,
            'step_type': 'instruction',
            'title': 'Inspect housing',
            'instruction': 'Inspect housing for damage',
            'required': True,
            'value_type': 'none',
        }
        data.update(overrides)
        response = self.client.post(
            f'/api/tasks/procedure-revisions/{revision.pk}/steps/', data, format='json'
        )
        revision.refresh_from_db()
        return response

    def test_create_procedure_and_draft_revision(self):
        procedure, revision = self.create_procedure_and_revision()
        self.assertEqual(revision.procedure, procedure)
        self.assertEqual(revision.revision, 1)
        self.assertEqual(revision.status, ProcedureRevisionStatus.DRAFT)

    def test_add_steps_to_draft(self):
        _procedure, revision = self.create_procedure_and_revision()
        response = self.add_step(revision)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(revision.steps.count(), 1)
        self.assertEqual(revision.content_version, 2)

    def test_published_revision_generic_patch_is_conflict(self):
        procedure = Procedure.objects.create(
            code='PM-PUB', name='Published', customer=self.customer, created_by=self.actor
        )
        revision = ProcedureRevision.objects.create(
            procedure=procedure, revision=1, status=ProcedureRevisionStatus.PUBLISHED,
            work_order_type=WorkOrderType.PREVENTIVE, created_by=self.actor,
            published_by=self.actor, published_at=timezone.now(), content_hash='frozen',
        )
        response = self.client.patch(
            f'/api/tasks/procedure-revisions/{revision.pk}/',
            {'expected_content_version': 1, 'change_summary': 'mutated'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        revision.refresh_from_db()
        self.assertEqual(revision.change_summary, '')
        self.assertEqual(revision.content_hash, 'frozen')

    def test_reorder_rejects_duplicate_or_foreign_step_keys(self):
        _procedure, revision = self.create_procedure_and_revision()
        first = self.add_step(revision).json()
        second = self.add_step(revision, sequence=2, title='Second').json()
        for keys in ([first['key'], first['key']], [first['key'], '00000000-0000-0000-0000-000000000001']):
            with self.subTest(keys=keys):
                response = self.client.post(
                    f'/api/tasks/procedure-revisions/{revision.pk}/reorder-steps/',
                    {'expected_content_version': revision.content_version, 'step_keys': keys},
                    format='json',
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotEqual(first['key'], second['key'])

    def test_blockers_for_empty_invalid_revision_are_stable(self):
        _procedure, revision = self.create_procedure_and_revision()
        revision.revision = 2
        revision.save(update_fields=['revision'])
        response = self.client.get(f'/api/tasks/procedure-revisions/{revision.pk}/blockers/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {item['code'] for item in response.json()},
            {'REQUIRED_STEP_MISSING', 'CHANGE_SUMMARY_REQUIRED', 'AUTHOR_REVIEWER_CONFLICT'},
        )

    def test_review_freezes_payload_and_publish_returns_pending_approval(self):
        _procedure, revision = self.create_procedure_and_revision()
        self.add_step(revision)
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            f'/api/tasks/procedure-revisions/{revision.pk}/request-review/',
            {'expected_content_version': revision.content_version}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        revision.refresh_from_db()
        self.assertEqual(revision.status, ProcedureRevisionStatus.IN_REVIEW)
        self.assertEqual(len(revision.content_hash), 64)
        approval = Approval.objects.get(pk=response.json()['id'])
        self.assertEqual(approval.action_type, ActionType.PROCEDURE_PUBLISH)
        self.assertEqual(approval.payload['content_hash'], revision.content_hash)

        publish = self.client.post(
            f'/api/tasks/procedure-revisions/{revision.pk}/publish/', {}, format='json'
        )
        self.assertEqual(publish.status_code, status.HTTP_200_OK)
        self.assertEqual(publish.json()['id'], str(approval.pk))
        revision.refresh_from_db()
        self.assertEqual(revision.status, ProcedureRevisionStatus.IN_REVIEW)
