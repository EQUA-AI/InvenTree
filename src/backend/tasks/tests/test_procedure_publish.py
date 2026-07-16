"""Tests for approval-backed governed procedure publication."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from approvals.executors import registry
from approvals.models import ActionType
from company.models import Company
from tasks.models import (
    Procedure,
    ProcedureRevision,
    ProcedureRevisionStatus,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.procedures import ProcedurePublishError, publish_revision


def _scope_resolver(actor):
    """Return scopes attached to the persisted actor for executor tests."""
    customer_id = getattr(actor, '_test_customer_id', None)
    if customer_id is None:
        customer_id = (
            Company.objects.filter(name='Procedure customer')
            .values_list('pk', flat=True)
            .first()
        )
    return {MaintenanceScope(customer_id=customer_id, site_key=None)}


@override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=_scope_resolver)
class ProcedurePublishTest(TestCase):
    """Exercise atomic publication, replay, drift, and registration."""

    def setUp(self):
        """Create a scoped family with one published and one reviewed revision."""
        self.actor = get_user_model().objects.create_superuser(
            username='procedure-publisher',
            email='publisher@example.com',
            password='test',
        )
        self.customer = Company.objects.create(
            name='Procedure customer', is_customer=True
        )
        self.actor._test_customer_id = self.customer.pk
        self.procedure = Procedure.objects.create(
            code='PM-001',
            name='Routine inspection',
            customer=self.customer,
            created_by=self.actor,
        )
        self.prior = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=1,
            status=ProcedureRevisionStatus.PUBLISHED,
            work_order_type=WorkOrderType.PREVENTIVE,
            content_hash='old-hash',
            content_version=1,
            created_by=self.actor,
            published_by=self.actor,
            published_at=timezone.now(),
        )
        self.procedure.current_revision = self.prior
        self.procedure.save(update_fields=['current_revision'])
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=2,
            status=ProcedureRevisionStatus.IN_REVIEW,
            work_order_type=WorkOrderType.PREVENTIVE,
            content_hash='reviewed-hash',
            content_version=7,
            created_by=self.actor,
        )

    def publish(self, **overrides):
        """Invoke publication with the reviewed payload defaults."""
        values = {
            'procedure_id': self.procedure.pk,
            'revision_id': self.revision.pk,
            'revision_number': self.revision.revision,
            'content_hash': self.revision.content_hash,
            'content_version': self.revision.content_version,
            'actor': self.actor,
            'scope': {'customer_id': self.customer.pk},
        }
        values.update(overrides)
        return publish_revision(**values)

    def test_publish_reviewed_revision(self):
        """Publication supersedes the prior revision and updates the family."""
        effect_ref = self.publish()

        self.procedure.refresh_from_db()
        self.prior.refresh_from_db()
        self.revision.refresh_from_db()
        self.assertEqual(
            effect_ref,
            f'procedure-publish:{self.revision.pk}:{self.revision.content_hash}',
        )
        self.assertEqual(self.revision.status, ProcedureRevisionStatus.PUBLISHED)
        self.assertEqual(self.revision.published_by, self.actor)
        self.assertIsNotNone(self.revision.published_at)
        self.assertEqual(self.procedure.current_revision, self.revision)
        self.assertEqual(self.prior.status, ProcedureRevisionStatus.SUPERSEDED)

    def test_publish_replay_is_idempotent(self):
        """A replay returns the same reference and performs no second transition."""
        first_ref = self.publish()
        self.revision.refresh_from_db()
        first_published_at = self.revision.published_at
        second_ref = self.publish()

        self.revision.refresh_from_db()
        self.prior.refresh_from_db()
        self.assertEqual(first_ref, second_ref)
        self.assertEqual(self.revision.published_at, first_published_at)
        self.assertEqual(self.prior.status, ProcedureRevisionStatus.SUPERSEDED)
        self.assertEqual(
            ProcedureRevision.objects.filter(
                procedure=self.procedure,
                status=ProcedureRevisionStatus.PUBLISHED,
            ).count(),
            1,
        )

    def test_reviewed_content_mismatch_does_not_publish(self):
        """Hash and version drift both fail without changing publication state."""
        for override in ({'content_hash': 'stale'}, {'content_version': 6}):
            with self.subTest(override=override), self.assertRaises(
                ProcedurePublishError
            ):
                self.publish(**override)

        self.revision.refresh_from_db()
        self.prior.refresh_from_db()
        self.assertEqual(self.revision.status, ProcedureRevisionStatus.IN_REVIEW)
        self.assertEqual(self.prior.status, ProcedureRevisionStatus.PUBLISHED)

    def test_database_rejects_second_published_revision(self):
        """The conditional unique constraint backstops service serialization."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProcedureRevision.objects.filter(pk=self.revision.pk).update(
                status=ProcedureRevisionStatus.PUBLISHED,
                published_by=self.actor,
                published_at=timezone.now(),
            )

    def test_executor_is_registered_at_app_startup(self):
        """TasksConfig registers the required publication executor."""
        self.assertTrue(registry.has(ActionType.PROCEDURE_PUBLISH))
