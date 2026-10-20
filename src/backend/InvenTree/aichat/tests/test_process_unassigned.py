"""O15 review: AI/voice unassigned remains an explicit native release."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from aichat.models import ProposalAction
from aichat.services import proposals
from assets.models import AssetMachine, get_default_client


@override_settings(
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class GovernedUnassignedRegressionTest(TestCase):
    """Confirmed AI/voice release uses the same native assignment command."""

    def test_confirmed_proposal_dispatch_releases_assignment_without_handover(self):
        """The governed dispatcher preserves explicit unassigned intent."""
        from tasks.models import WorkOrder
        from tasks.work_execution.continuity import project

        owner = get_user_model().objects.create_superuser(
            username='release-owner', email='release@example.com', password='synthetic'
        )
        machine = AssetMachine.objects.create(
            name='Synthetic release machine', client=get_default_client()
        )
        work = WorkOrder.objects.create(
            title='Synthetic release work', machine=machine, assigned_to=owner
        )
        proposals._dispatch_command(
            None,
            owner,
            ProposalAction.WORK_ORDER_ASSIGN,
            'ai-unassigned',
            work.pk,
            1,
            {'assigned_to': None},
            'Explicit unassigned proposal',
            None,
        )
        work.refresh_from_db()
        self.assertIsNone(work.assigned_to_id)
        self.assertEqual(work.lifecycle_version, 2)
        self.assertEqual(
            work.events.get(event_type='ASSIGNED').metadata['result_metadata'][
                'assigned_to_id'
            ],
            None,
        )
        self.assertFalse(work.handovers.exists())
        self.assertEqual(
            project(work, owner)['accepted_handover']['responsible_owner'],
            owner.username,
        )
        self.assertEqual(project(work, owner)['accepted_handover']['state'], 'released')
