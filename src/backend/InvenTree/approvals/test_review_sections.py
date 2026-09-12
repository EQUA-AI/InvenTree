"""Stable hashes and complete, explicitly bounded auditory-review contracts."""

from copy import deepcopy
from types import SimpleNamespace

from django.test import SimpleTestCase

from .models import ActionType
from .review_sections import (
    build_review_sections,
    compute_review_hash,
    voice_eligibility,
)


def request_for(action, payload=None):
    """Build a transport-independent stored request projection."""
    return SimpleNamespace(
        pk='request-1',
        action_type=action,
        risk_tier=2,
        summary='Review this request',
        payload=payload or {},
        current_revision_number=0,
        baseline_context={},
        preconditions={},
    )


class ApprovalReviewSectionsTests(SimpleTestCase):
    """Every action has a deterministic screen contract and explicit eligibility."""

    def test_every_action_has_unique_required_sections(self):
        """No empty or duplicate IDs can satisfy section-delivery evidence."""
        for action in ActionType.values:
            with self.subTest(action=action):
                sections = build_review_sections(request_for(action))
                self.assertTrue(sections)
                self.assertEqual(len(sections), len({s['id'] for s in sections}))
                self.assertTrue(all(s['required'] for s in sections))

    def test_email_covers_all_recipients_and_complete_body(self):
        """CC/BCC, long body, attachments and external effect remain visible."""
        body = 'Message detail. ' * 80
        sections = {
            s['id']: s
            for s in build_review_sections(
                request_for(
                    ActionType.EMAIL,
                    {
                        'to': ['lokesh@equa.work'],
                        'cc': ['cc@equa.work'],
                        'bcc': ['bcc@equa.work'],
                        'subject': 'VOICE-TEST',
                        'body': body,
                    },
                )
            )
        }
        self.assertIn('lokesh@equa.work', sections['recipients']['text'])
        self.assertIn('cc@equa.work', sections['cc']['text'])
        self.assertIn('bcc@equa.work', sections['bcc']['text'])
        self.assertEqual(sections['body']['text'], body)
        self.assertIn(str(len(body)), sections['body_summary']['text'])
        self.assertIn('cannot be undone', sections['external_effect']['text'])

    def test_orders_cover_every_line_quantity_unit_price_total_currency(self):
        """No purchase or sales line is omitted from auditory review."""
        payload = {
            'supplier_id': 12,
            'customer_id': 13,
            'currency': 'USD',
            'total': '6.00',
            'line_items': [
                {'part_id': 1, 'quantity': 2, 'unit': 'each', 'unit_price': '3.00'}
            ],
        }
        for action in (ActionType.PURCHASE_ORDER, ActionType.SALES_ORDER):
            sections = {
                s['id']: s for s in build_review_sections(request_for(action, payload))
            }
            self.assertIn(
                'quantity 2; unit each; unit price 3.00; currency USD',
                sections['line_1']['text'],
            )
            self.assertEqual(sections['total']['text'], '6.00 USD')

    def test_hash_is_stable_and_binds_complete_payload_and_revision(self):
        """Hidden body tails and revision/baseline changes invalidate evidence."""
        request = request_for(
            ActionType.EMAIL, {'body': 'x' * 1000, 'to': ['lokesh@equa.work']}
        )
        original = compute_review_hash(request)
        reordered = deepcopy(request)
        reordered.payload = dict(reversed(list(request.payload.items())))
        self.assertEqual(original, compute_review_hash(reordered))
        for attribute, value in [
            ('payload', {**request.payload, 'body': 'x' * 999 + 'y'}),
            ('current_revision_number', 1),
            ('baseline_context', {'changed': True}),
        ]:
            changed = deepcopy(request)
            setattr(changed, attribute, value)
            self.assertNotEqual(original, compute_review_hash(changed))

    def test_zero_price_and_malformed_lines_remain_visible(self):
        """Zero is a price, and malformed stored content is not silently dropped."""
        request = request_for(
            ActionType.PURCHASE_ORDER,
            {'line_items': [{'part_id': 1, 'quantity': 1, 'unit_price': 0}]},
        )
        self.assertIn('unit price 0;', build_review_sections(request)[2]['text'])
        request.payload['line_items'] = 42
        self.assertEqual(build_review_sections(request)[2]['id'], 'invalid_lines')

    def test_named_screen_only_and_missing_executor_reasons(self):
        """Risk, attachments and unsupported types never acquire eligibility."""
        for action in [
            ActionType.SAFETY_GATE,
            ActionType.PROCEDURE_PUBLISH,
            ActionType.JOB_KIT_SUBSTITUTION,
            ActionType.WORKFLOW,
            ActionType.NOTIFICATION,
            ActionType.SALES_ORDER,
            ActionType.STOCK_UPDATE,
            'future_action',
        ]:
            eligible, reason = voice_eligibility(request_for(action))
            self.assertFalse(eligible)
            self.assertTrue(reason)
        request = request_for(
            ActionType.EMAIL, {'attachments': [{'filename': 'evidence.pdf'}]}
        )
        self.assertIn('Attachments', voice_eligibility(request)[1])
        request.payload = {}
        request.risk_tier = 3
        self.assertIn('Tier-3', voice_eligibility(request)[1])
