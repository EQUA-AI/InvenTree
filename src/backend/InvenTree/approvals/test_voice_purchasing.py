"""Canonical PO effects through the voice adapter, on isolated SQLite records."""

import os
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.test import override_settings

from ai.core.decisions.receipts import lookup_operation
from company.models import Company, SupplierPart
from order.models import PurchaseOrder
from order.status_codes import PurchaseOrderStatus
from part.models import Part
from voice.models import VoiceOperation

from .executors import PurchaseOrderExecutor, registry
from .models import Approval, ApprovalExecution, ExecutedEffect
from .review_evidence import review_units
from .test_voice_inbox import VoiceInboxTests


class PurchasingVoiceTests(VoiceInboxTests):
    """Review selection and typed/voice decisions reuse the real purchasing command."""

    def setUp(self):
        """No real supplier, mailbox or external order is involved."""
        super().setUp()
        self.enterContext(registry.replace_for_tests(PurchaseOrderExecutor()))
        self.supplier = Company.objects.create(
            name='VOICE-TEST supplier', is_supplier=True
        )
        self.part = Part.objects.create(
            name='VOICE-TEST bearing', purchaseable=True, active=True
        )
        self.supplier_part = SupplierPart.objects.create(
            supplier=self.supplier, part=self.part, SKU='VOICE-TEST-1'
        )
        from users.models import RuleSet

        group = Group.objects.create(name='VOICE-TEST purchasing readers')
        RuleSet.objects.update_or_create(
            group=group, name='purchase_order', defaults={'can_view': True}
        )
        self.user.groups.add(group)
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label='order',
                codename__in=(
                    'view_purchaseorder',
                    'add_purchaseorder',
                    'change_purchaseorder',
                    'add_purchaseorderlineitem',
                ),
            )
        )

    def draft_review(self):
        """One bounded spoken request prepares review, not an order."""
        reply = self.begin(
            f'create purchase order for supplier {self.supplier.pk} in USD, description, VOICE-TEST draft only'
        )
        self.assertEqual(reply.decision.kind, 'approval_review')
        return Approval.objects.get(pk=reply.decision.source_id)

    def acknowledge(self, approval, *, auditory=False):
        """Only recorded exact pages can support a voice acknowledgment."""
        if auditory:
            for index, _ in enumerate(review_units(approval)):
                if index:
                    self.say('next section')
                self.deliver()
        self.assertEqual(
            self.say('I have reviewed this request', touch=not auditory).event,
            'acknowledged',
        )
        self.say(f'approve {str(approval.pk)[:8]}')

    def test_real_voice_draft_effect_receipt_and_duplicate(self):
        """Controlled audio qualification is server-configured, never client asserted."""
        approval = self.draft_review()
        self.assertFalse(PurchaseOrder.objects.exists())
        self.acknowledge(approval, auditory=True)
        self.assertEqual(self.say('yes').event, 'refused')
        self.deliver()
        with (
            patch.dict(os.environ, {'CONTAINER_APP_NAME': 'aimms-experimental'}),
            override_settings(
                APPROVAL_VOICE_PILOT_ACTOR_IDS=[str(self.user.pk)],
                APPROVAL_VOICE_PILOT_REQUEST_IDS=[str(approval.pk)],
            ),
        ):
            from ai.core.decisions.adapters.approval_reference import spoken_reference

            result = self.say(f'approve {spoken_reference(approval.pk)}')
        self.assertEqual(result.decision.execution_state, 'succeeded', result.spoken)
        self.say(f'approve {str(approval.pk)[:8]}')
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(
            PurchaseOrder.objects.get().status, PurchaseOrderStatus.PENDING.value
        )
        self.assertEqual(ApprovalExecution.objects.count(), 1)
        self.assertEqual(ExecutedEffect.objects.count(), 1)
        self.assertEqual(VoiceOperation.objects.count(), 1)
        self.assertFalse(
            lookup_operation(actor=self.principal, thread_id=self.session.thread_id)[
                'receipt'
            ]['execution_result']['email_sent']
        )

    def test_add_line_and_issue_are_separate_exactly_reviewed_effects(self):
        """A new line never issues; issuing records the reviewed baseline once."""
        draft = self.draft_review()
        self.acknowledge(draft)
        self.say(f'approve {str(draft.pk)[:8]}', touch=True)
        order = PurchaseOrder.objects.get()
        line_reply = self.begin(
            f'add supplier part {self.supplier_part.pk} to purchase order {order.reference}, quantity 2, price 3.25 USD'
        )
        line = Approval.objects.get(pk=line_reply.decision.source_id)
        self.assertEqual(line.payload['total'], '6.50')
        self.acknowledge(line)
        self.say(f'approve {str(line.pk)[:8]}', touch=True)
        order.refresh_from_db()
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(order.status, PurchaseOrderStatus.PENDING.value)
        issue_reply = self.begin(f'issue purchase order {order.reference}')
        issue = Approval.objects.get(pk=issue_reply.decision.source_id)
        self.assertEqual(issue.payload['line_items'][0]['part_name'], self.part.name)
        self.acknowledge(issue)
        result = self.say(f'approve {str(issue.pk)[:8]}', touch=True)
        self.assertEqual(result.decision.execution_state, 'succeeded', result.spoken)
        order.refresh_from_db()
        self.assertEqual(order.status, PurchaseOrderStatus.PLACED.value)
        self.assertEqual(ExecutedEffect.objects.count(), 3)

    def test_disabled_or_incomplete_requests_never_create_effects(self):
        """Missing quantities/currency never use pricing or LLM guesses."""
        self.assertEqual(self.begin('create a purchase order').event, 'clarification')
        self.config.feature_voice_external_actions = False
        self.assertEqual(
            self.begin('issue purchase order VOICE-TEST').event, 'ineligible'
        )
        self.assertFalse(Approval.objects.exists())
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_order_notifications_require_screen_handoff(self):
        """A real subscriber cannot be silently emailed by a voice issue action."""
        from part.models import PartStar

        from .review_sections import voice_eligibility

        order = PurchaseOrder.objects.create(
            supplier=self.supplier,
            description='VOICE-TEST subscriber safety',
            created_by=self.user,
            order_currency='USD',
        )
        from order.models import PurchaseOrderLineItem

        PurchaseOrderLineItem.objects.create(
            order=order,
            part=self.supplier_part,
            quantity=1,
            purchase_price=2,
            purchase_price_currency='USD',
        )
        PartStar.objects.create(part=self.part, user=self.user2)
        result = self.begin(f'issue purchase order {order.reference}')
        approval = Approval.objects.get(pk=result.decision.source_id)
        self.assertTrue(approval.payload['notifications_possible'])
        self.assertFalse(voice_eligibility(approval)[0])
        self.assertFalse(result.decision.voice_eligible)
        self.assertFalse(ApprovalExecution.objects.exists())

    def test_dry_run_is_explicit_and_never_calls_dispatch(self):
        """The sandbox seam records no business success and performs no write."""
        approval = self.draft_review()
        self.acknowledge(approval)
        self.config.voice_action_dry_run = True
        with patch('approvals.services.approve') as command:
            result = self.say(f'approve {str(approval.pk)[:8]}', touch=True)
        command.assert_not_called()
        self.assertIn('Dry run', result.spoken)
        self.assertEqual(result.decision.execution_state, 'failed_before_effect')
        self.assertFalse(PurchaseOrder.objects.exists())
        self.assertFalse(ApprovalExecution.objects.exists())

    def test_unqualified_audio_route_hands_off_without_dispatch(self):
        """A review checkbox does not qualify the voice approval route."""
        approval = self.draft_review()
        self.acknowledge(approval)
        self.deliver()
        result = self.say(f'approve {str(approval.pk)[:8]}')
        self.assertEqual(result.event, 'ineligible')
        self.assertFalse(VoiceOperation.objects.exists())
        self.assertFalse(PurchaseOrder.objects.exists())

    def test_permission_revocation_at_execution_never_creates_order(self):
        """Reviewer permission cannot substitute for purchasing rights."""
        approval = self.draft_review()
        self.acknowledge(approval)
        self.user.user_permissions.remove(
            Permission.objects.get(
                content_type__app_label='order', codename='add_purchaseorder'
            )
        )
        result = self.say(f'approve {str(approval.pk)[:8]}', touch=True)
        self.assertEqual(result.decision.execution_state, 'failed_before_effect')
        self.assertFalse(PurchaseOrder.objects.exists())
