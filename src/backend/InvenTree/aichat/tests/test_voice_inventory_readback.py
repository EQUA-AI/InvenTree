"""E3-E6 inventory proposals use canonical stock effects and retained receipts."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from asgiref.sync import async_to_sync, sync_to_async
from tasks.tests.test_workorder_voice_readback import WorkOrderVoiceFixture

from ai.core.decisions import pipeline
from ai.core.decisions.inventory import parse_stock_intent, read_stock
from ai.core.questions.pending import InMemoryPendingQuestionStore
from ai.core.questions.promotion import consume_question_proposal
from ai.core.questions.schema import build_pending_record
from aichat.models import StockCommandReceipt
from aichat.services import proposals, stock_commands
from part.models import Part
from stock.models import StockItem, StockItemTracking, StockLocation


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.tests.test_workorder_voice_readback.cancellation_scope'
)
class VoiceInventoryJourneyTests(WorkOrderVoiceFixture, TestCase):
    """Real resolver, ownership, coordinator, question binding and operation lookup."""

    def setUp(self):
        """Keep live decision tests on private database records and in-memory stores."""
        super().setUp()
        self.part = Part.objects.create(
            name='E live gasket', IPN='E-LIVE', units='metres'
        )
        self.location = StockLocation.objects.create(name='E live shelf')
        self.stock = StockItem.objects.create(
            part=self.part, location=self.location, quantity=10
        )
        self.flags = SimpleNamespace(
            feature_voice_inventory_actions=True, feature_question_cards=True
        )
        self.enterContext(patch('ai.core.config.get_settings', return_value=self.flags))

    def test_voice_stock_review_confirm_and_receipt(self):
        """The strict remove phrase owns one receipt-backed inventory effect."""
        decision = self.coordinator.begin(
            f'Remove 2.5 from stock item {self.stock.pk}', **self.arguments
        ).decision
        self.assertEqual(decision.required_phrase, 'confirm remove')
        self.assertIn('E live gasket', decision.spoken_summary)
        self.assertIn('metres', decision.spoken_summary)
        self.assertIn(self.location.pathstring, decision.spoken_summary)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 10)
        reply = self.respond('confirm remove', self.deliver(decision))
        self.assertEqual(reply.decision.execution_state, 'succeeded', reply.spoken)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('7.5'))
        self.respond('confirm remove', reply.decision)
        self.assertEqual(StockCommandReceipt.objects.count(), 1)

    def test_flag_off_and_revocation_do_not_arm_or_submit(self):
        """A workflow switch is rechecked, not just evaluated at session creation."""
        self.flags.feature_voice_inventory_actions = False
        with self.assertRaisesMessage(proposals.ProposalError, 'disabled'):
            self.coordinator.begin(
                f'Add 2 to stock item {self.stock.pk}', **self.arguments
            )
        self.flags.feature_voice_inventory_actions = True
        decision = self.coordinator.begin(
            f'Add 2 to stock item {self.stock.pk}', **self.arguments
        ).decision
        self.flags.feature_voice_inventory_actions = False
        with self.assertRaisesMessage(proposals.ProposalError, 'disabled'):
            self.respond('yes', self.deliver(decision))
        self.assertFalse(StockCommandReceipt.objects.exists())

    def test_spoken_stock_slots_and_explicit_location_id(self):
        """Cardinals are exact quantities; digit sequences belong only to IDs."""
        for words, expected in [
            ('two', '2'),
            ('eleven', '11'),
            ('two point five', '2.5'),
            ('0.25', '0.25'),
        ]:
            with self.subTest(words=words):
                intent = parse_stock_intent(
                    f'Add {words} to stock item one three nine eight'
                )
                self.assertEqual(
                    intent.parameters, {'quantity': expected, 'stock_item_id': 1398}
                )
        for words in (
            'to',
            'a few',
            'one two',
            'minus two',
            'two or three',
            'two point ten',
            'one hundred and',
        ):
            with self.subTest(words=words):
                self.assertIsNone(parse_stock_intent(f'Add {words} to stock item 1398'))
        self.assertIsNone(
            parse_stock_intent(
                'Transfer two from stock item 1398 to location id approximately forty eight'
            )
        )
        destination = StockLocation.objects.create(
            name='Exact name / punctuation 20260914'
        )
        decision = self.coordinator.begin(
            f'Transfer two from stock item {self.stock.pk} to location id {destination.pk}',
            **self.arguments,
        ).decision
        self.assertIn(destination.pathstring, decision.spoken_summary)
        self.assertIn('Quantity: 2.', decision.spoken_summary)
        self.assertIn('Units: metres.', decision.spoken_summary)
        self.assertFalse(StockCommandReceipt.objects.exists())
        with patch.object(StockLocation, 'check_ownership', return_value=False):
            with self.assertRaisesMessage(proposals.ProposalError, 'unavailable'):
                self.coordinator.begin(
                    f'Transfer two from stock item {self.stock.pk} to location id {destination.pk}',
                    **self.arguments,
                )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 10)

    def test_deterministic_reads_resolve_names_ipn_and_pages(self):
        """Units and decimal quantities are never converted or generated by a model."""
        for name in (self.part.name, self.part.IPN):
            result = read_stock(self.actor, {'part_name': name})
            self.assertIn('E-LIVE', result)
            self.assertIn('10.00000 metres', result)
            self.assertIn(self.location.pathstring, result)
        for _ in range(6):
            StockItem.objects.create(
                part=self.part, location=self.location, quantity=Decimal('0.25')
            )
        result = read_stock(self.actor, {'part_name': 'E-LIVE'})
        self.assertEqual(result.count('Stock item'), 5)
        self.assertIn('page 2', result)
        from ai.core.turn.responses import _canonical_voice_write, _plain_spoken_text
        from ai.core.voice.presentation import chunks

        reply = self.coordinator.begin('Show stock of part E-LIVE', **self.arguments)
        canonical = _canonical_voice_write(reply.spoken, layout=reply.spoken_layout)
        self.assertEqual(canonical.detailed_response, result)
        self.assertEqual(canonical.spoken_summary, _plain_spoken_text(result))
        self.assertIsNone(reply.decision)

        pages = chunks(canonical.spoken_summary, layout=canonical.detailed_response)
        self.assertEqual(len(pages), 2)
        self.assertEqual(pages[0].count('Stock item'), 3)
        self.assertEqual(pages[1].count('Stock item'), 2)
        self.assertEqual(pages[0].count('Inventory page 1.'), 1)
        self.assertEqual(
            read_stock(self.actor, {'part_name': 'E-LIVE', 'page': '2'}).count(
                'Stock item'
            ),
            2,
        )

    def test_ambiguous_locations_produce_bound_question_and_selection_only_previews(
        self,
    ):
        """No best-guess write: a selected scoped location still requires confirmation."""
        root1 = StockLocation.objects.create(name='E north')
        root2 = StockLocation.objects.create(name='E south')
        StockLocation.objects.create(name='Shelf', parent=root1)
        StockLocation.objects.create(name='Shelf', parent=root2)
        content = f'Transfer 2 from stock item {self.stock.pk} to location Shelf'
        service = SimpleNamespace(
            question_store=InMemoryPendingQuestionStore(),
            _call_sync=lambda fn, *args, **kwargs: sync_to_async(
                fn, thread_sensitive=True
            )(*args, **kwargs),
            _canonical_for_voice_write=AsyncMock(return_value={}),
        )
        run = SimpleNamespace(
            content=content,
            modality='voice',
            trusted_context=SimpleNamespace(locale='en-US'),
            thread=SimpleNamespace(pk=self.session.thread_id),
            turn=SimpleNamespace(pk='inventory-question'),
            actor=self.principal,
            metadata={'voice_session_id': str(self.session.pk)},
            emitter=None,
        )
        with (
            patch.object(pipeline, 'enabled', return_value=True),
            patch.object(pipeline, 'get_coordinator', return_value=self.coordinator),
        ):
            self.assertTrue(async_to_sync(pipeline.resolve)(service, run))
            question = consume_question_proposal()
            self.assertEqual(question['source'], 'voice_inventory')
            record, _ = build_pending_record(
                thread_id=self.session.thread_id,
                turn_id=run.turn.pk,
                source=question['source'],
                question_text=question['question_text'],
                options=question['options'],
                origin_content=content,
                workflow='voice_decision',
                modality='voice',
            )
            service.question_store.save(self.session.thread_id, record)
            run.content = 'option two'
            run.turn.pk = 'inventory-answer'
            self.assertTrue(async_to_sync(pipeline.resolve)(service, run))
        self.assertEqual(
            run.write_canonical['decision_event']['kind'],
            'presented',
            run.write_canonical,
        )
        self.assertFalse(StockCommandReceipt.objects.exists())
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.location_id, self.location.pk)

    def test_stock_parser_does_not_accept_compound_effects_or_negative_quantities(self):
        """An exact numeric command, not an extracted prefix, is the intent."""
        self.assertIsNone(parse_stock_intent('remove 2 from stock item 1 and add 3'))
        self.assertIsNone(parse_stock_intent('remove -2 from stock item 1'))
        self.assertEqual(
            parse_stock_intent('stock of part E-LIVE page 2').parameters,
            {'part_name': 'E-LIVE', 'page': '2'},
        )


class VoiceStockCommandTests(TestCase):
    """Private stock fixtures; never seed or reset application inventory."""

    def setUp(self):
        """Provide two unambiguous stock-holding locations and an untracked part."""
        self.actor = get_user_model().objects.create_superuser(
            'stock-e', 'stock@example.invalid', 'test-only'
        )
        self.part = Part.objects.create(
            name='E test gasket', IPN='E-GASKET', units='metres'
        )
        self.source = StockLocation.objects.create(name='E source')
        self.destination = StockLocation.objects.create(name='E destination')
        self.stock = StockItem.objects.create(
            part=self.part, location=self.source, quantity=10, delete_on_deplete=False
        )

    def proposal(self, action, **params):
        """Create a durable server-reviewed stock movement without an effect."""
        return proposals.create_proposal(
            owner=self.actor,
            scope_key='test-stock',
            scope_hash='s' * 64,
            action_type=action,
            work_order_id=None,
            reason='isolated fixture',
            intent={'stock_item_id': self.stock.pk, 'quantity': '2.5', **params},
            idempotency_key=f'test:{StockCommandReceipt.objects.count()}:{action}',
            policy_version='stock-test',
        )

    def confirm(self, proposal):
        """Confirm precisely the reviewed hash and strict phrase."""
        result = proposals.confirm_proposal(
            owner=self.actor,
            scope_hash=proposal.scope_hash,
            proposal_id=proposal.pk,
            expected_preview_hash=proposal.preview_hash,
            confirm_phrase=proposal.preview.get('confirm_phrase', ''),
        )
        self.assertTrue(stock_commands.verified(result), result.receipt)
        return result

    def test_add_count_remove_and_exact_replay(self):
        """One audited effect for each confirmation, including duplicate responses."""
        for action, after in [
            ('stock.add', '12.5'),
            ('stock.count', '2.5'),
            ('stock.remove', '0'),
        ]:
            with self.subTest(action=action):
                proposal = self.proposal(action)
                self.assertEqual(proposal.preview['units'], 'metres')
                self.assertEqual(proposal.preview['part_name'], self.part.name)
                receipt = self.confirm(proposal).receipt
                self.assertEqual(self.confirm(proposal).receipt, receipt)
                self.stock.refresh_from_db()
                self.assertEqual(self.stock.quantity, Decimal(after))
        self.assertEqual(StockCommandReceipt.objects.count(), 3)

    def test_full_transfer(self):
        """Review the full destination path and keep the original stock identity."""
        proposal = self.proposal(
            'stock.transfer', quantity='10', location_id=self.destination.pk
        )
        self.assertEqual(
            proposal.preview['destination']['path'], self.destination.pathstring
        )
        receipt = self.confirm(proposal).receipt
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.location_id, self.destination.pk)
        self.assertEqual(receipt['stock_item_id'], self.stock.pk)
        self.assertEqual(Decimal(receipt['quantity_after']), 10)

    def test_partial_transfer(self):
        """A canonical split has both the retained source and new destination IDs."""
        receipt = self.confirm(
            self.proposal('stock.transfer', location_id=self.destination.pk)
        ).receipt
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, Decimal('7.5'))
        moved = StockItem.objects.get(pk=receipt['moved_stock_item_id'])
        self.assertEqual(moved.quantity, Decimal('2.5'))
        self.assertEqual(moved.location_id, self.destination.pk)

    def test_depletion_retains_receipt_and_direct_command_replay(self):
        """Deleted stock is not a missing receipt and must never be removed twice."""
        self.stock.delete_on_deplete = True
        self.stock.save()
        proposal = self.proposal('stock.remove', quantity='10')
        result = self.confirm(proposal)
        self.assertFalse(StockItem.objects.filter(pk=self.stock.pk).exists())
        self.assertTrue(result.receipt['depleted'])
        self.assertEqual(
            stock_commands.execute(
                actor=self.actor,
                action=proposal.action_type,
                intent=proposal.intent,
                preview=proposal.preview,
                idempotency_key=f'proposal:{proposal.pk}',
            ),
            result.receipt,
        )
        self.assertEqual(StockCommandReceipt.objects.count(), 1)

    def test_new_stock_addition_has_nullable_target_and_real_identity(self):
        """New stock creation is explicit and reviewed, not an inferred adjustment."""
        proposal = self.proposal(
            'stock.add',
            stock_item_id=None,
            part_id=self.part.pk,
            location_id=self.destination.pk,
        )
        self.assertIsNone(proposal.target_stock_item_id)
        result = self.confirm(proposal)
        row = StockItem.objects.get(pk=result.receipt['stock_item_id'])
        self.assertEqual(row.part_id, self.part.pk)
        self.assertEqual(row.quantity, Decimal('2.5'))
        self.assertEqual(row.location_id, self.destination.pk)

    def test_strict_remove_cannot_use_legacy_bypass(self):
        """Stock removal requires exactly confirm remove, including text confirmation."""
        proposal = self.proposal('stock.remove')
        with self.assertRaises(proposals.StrictConfirmationRequired):
            proposals.confirm_proposal(
                owner=self.actor,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.pk,
                strict_phrase_satisfied=True,
            )
        self.assertFalse(StockCommandReceipt.objects.exists())

    def test_quantity_and_destination_drift(self):
        """Current values, units and destination names must match the reviewed snapshot."""
        proposal = self.proposal('stock.transfer', location_id=self.destination.pk)
        StockLocation.objects.filter(pk=self.destination.pk).update(
            name='Changed destination'
        )
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.confirm(proposal)
        self.assertFalse(StockCommandReceipt.objects.exists())
        proposal = self.proposal('stock.add')
        StockItem.objects.filter(pk=self.stock.pk).update(quantity=11)
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.confirm(proposal)

    def test_no_clamping_or_nonfinite_quantities(self):
        """Never silently clamp a removal or silently normalize excessive precision."""
        for amount in ('11', 'NaN', 'Infinity', '-1', '0', '0.000001'):
            with (
                self.subTest(amount=amount),
                self.assertRaises(proposals.ProposalError),
            ):
                self.proposal('stock.remove', quantity=amount)
        self.assertFalse(StockCommandReceipt.objects.exists())

    def test_missing_tracking_rolls_back_stock_and_command(self):
        """No quantity mutation may commit without its canonical tracking evidence."""
        proposal = self.proposal('stock.add')
        with patch.object(StockItem, 'add_tracking_entry', return_value=None):
            with self.assertRaisesMessage(proposals.ProposalError, 'tracking receipt'):
                self.confirm(proposal)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.quantity, 10)
        self.assertFalse(StockCommandReceipt.objects.exists())

    def test_revoked_role_refuses_before_effect(self):
        """Fresh grants are required even if the actor presented the earlier preview."""
        proposal = self.proposal('stock.add')
        get_user_model().objects.filter(pk=self.actor.pk).update(is_superuser=False)
        with self.assertRaisesMessage(proposals.ProposalError, 'permission'):
            self.confirm(proposal)
        self.assertFalse(StockCommandReceipt.objects.exists())

    def test_missing_tracking_cannot_verify_a_successful_proposal(self):
        """A proposal success marker alone cannot prove the inventory effect."""
        result = self.confirm(self.proposal('stock.add'))
        StockItemTracking.objects.filter(pk=result.receipt['tracking_ref']).delete()
        self.assertFalse(stock_commands.verified(result))
