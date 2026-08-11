"""B7 (S33): the voice walkthrough reads snapshots verbatim, writes via the rail."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from company.models import Company
from tasks.models import (
    Procedure,
    ProcedureRevision,
    ProcedureRevisionStatus,
    ProcedureStep,
    ProcedureStepType,
    WorkOrder,
    WorkOrderStepExecution,
    WorkOrderType,
)
from tasks.scope import MaintenanceScope
from tasks.services.procedure_execution import apply_procedure_revision

from ai.core.config import Settings
from ai.core.voice.procedure_walkthrough import (
    interpret_walkthrough_command,
    walkthrough_reply,
)


def _settings(**overrides):
    return Settings(
        _env_file=None, single_site_policy_key='site-under-test', **overrides
    )


class WalkthroughGrammarTests(TestCase):
    """The utterance grammar fails closed on anything unrecognized."""

    def test_commands_parse(self):
        self.assertEqual(interpret_walkthrough_command('Next step please'), 'next')
        self.assertEqual(interpret_walkthrough_command('repeat that'), 'repeat')
        self.assertEqual(interpret_walkthrough_command('go back'), 'previous')
        self.assertEqual(interpret_walkthrough_command('Done.'), 'complete')
        self.assertEqual(interpret_walkthrough_command('mark it complete'), 'complete')
        self.assertEqual(interpret_walkthrough_command('stop the walkthrough'), 'stop')
        self.assertEqual(interpret_walkthrough_command('what is the weather'), 'unknown')
        self.assertEqual(interpret_walkthrough_command(''), 'unknown')


class WalkthroughFlowTests(TestCase):
    """Step-through over a real applied procedure."""

    def setUp(self):
        self.customer = Company.objects.create(name='B7 Customer', is_customer=True)
        self.actor = get_user_model().objects.create_superuser(
            username='b7-tech', email='b7@example.com', password='pw'
        )
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=self.customer.pk, site_key=None)
        }
        self.work_order = WorkOrder.objects.create(
            title='B7 work order',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            customer=self.customer,
            assigned_to=self.actor,
            work_order_type=WorkOrderType.PREVENTIVE,
        )
        self.procedure = Procedure.objects.create(
            code='B7-PM',
            name='B7 procedure',
            customer=self.customer,
            created_by=self.actor,
        )
        self.revision = ProcedureRevision.objects.create(
            procedure=self.procedure,
            revision=1,
            status=ProcedureRevisionStatus.PUBLISHED,
            work_order_type=WorkOrderType.PREVENTIVE,
            created_by=self.actor,
            published_by=self.actor,
            published_at=timezone.now(),
        )
        for index, (title, instruction) in enumerate(
            (
                ('Isolate power', 'Lock out the main breaker.'),
                ('Inspect belt', 'Check the drive belt for wear.'),
            ),
            start=1,
        ):
            ProcedureStep.objects.create(
                revision=self.revision,
                sequence=index,
                step_type=ProcedureStepType.INSTRUCTION,
                title=title,
                instruction=instruction,
                required=False,
            )
        self.procedure.current_revision = self.revision
        self.procedure.save(update_fields=['current_revision'])
        apply_procedure_revision(
            work_order_id=self.work_order.pk,
            revision_id=self.revision.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='b7-apply',
        )

    def _reply(self, utterance, position=0, enabled=True):
        with mock.patch(
            'ai.core.config.get_settings',
            return_value=_settings(FEATURE_GUIDED_PROCEDURES=enabled),
        ):
            return walkthrough_reply(
                actor=self.actor,
                work_order_id=self.work_order.pk,
                utterance=utterance,
                position=position,
            )

    def test_flag_off_is_unavailable(self):
        reply = self._reply('next', enabled=False)
        self.assertEqual(reply.error, 'FEATURE_DISABLED')
        self.assertTrue(reply.done)

    def test_read_is_verbatim_snapshot_text(self):
        reply = self._reply('')
        self.assertEqual(reply.total, 2)
        self.assertIn('Isolate power. Lock out the main breaker.', reply.speak_text)
        self.assertTrue(reply.speak_text.startswith('Step 1 of 2'))

    def test_next_previous_and_repeat_move_the_cursor_read_only(self):
        forward = self._reply('next', position=0)
        self.assertEqual(forward.position, 1)
        self.assertIn('Inspect belt. Check the drive belt for wear.', forward.speak_text)
        back = self._reply('go back', position=1)
        self.assertEqual(back.position, 0)
        repeat = self._reply('repeat', position=1)
        self.assertEqual(repeat.position, 1)
        self.assertEqual(
            WorkOrderStepExecution.objects.filter(status='pending').count(), 2
        )

    def test_complete_posts_through_the_command_rail(self):
        reply = self._reply('done', position=0)
        self.assertTrue(reply.completed)
        executions = list(
            WorkOrderStepExecution.objects.order_by('sequence')
        )
        self.assertEqual(executions[0].status, 'completed')
        self.assertEqual(executions[0].completed_by, self.actor)
        self.assertEqual(executions[1].status, 'pending')
        # The reply moves on and reads the next step.
        self.assertEqual(reply.position, 1)
        self.assertIn('Inspect belt', reply.speak_text)

    def test_out_of_scope_actor_sees_nothing(self):
        outsider = get_user_model().objects.create_superuser(
            username='b7-outsider', email='b7o@example.com', password='pw'
        )
        other = Company.objects.create(name='B7 Other', is_customer=True)
        outsider.maintenance_scopes = {
            MaintenanceScope(customer_id=other.pk, site_key=None)
        }
        with mock.patch(
            'ai.core.config.get_settings',
            return_value=_settings(FEATURE_GUIDED_PROCEDURES=True),
        ):
            with self.assertRaises(Exception):
                walkthrough_reply(
                    actor=outsider,
                    work_order_id=self.work_order.pk,
                    utterance='',
                    position=0,
                )
