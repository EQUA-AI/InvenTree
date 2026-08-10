"""The grounding soak report aggregates persisted assessments read-only."""

import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from aichat.models import ChatMessage, ChatThread, MessageRole, TurnModality


def _assessment(**overrides):
    base = {
        'mode': 'shadow',
        'applied': True,
        'heuristic_grounded': False,
        'audit_ran': True,
        'audit_grounded': True,
        'audit_error': False,
        'would_downgrade': False,
        'downgraded': False,
        'citation_count': 2,
        'ungrounded_identifiers': [],
    }
    base.update(overrides)
    return base


class GroundingSoakReportTests(TestCase):
    """The command reports aggregates and every would-downgrade turn."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username='soak-reader', password='x'
        )
        cls.thread = ChatThread.objects.create(
            owner=cls.user,
            scope_key='site:test',
            scope_hash='0' * 64,
            title='soak',
        )

    def _message(self, sequence, grounding=None, **kwargs):
        metadata = {'grounding': grounding} if grounding is not None else {}
        return ChatMessage.objects.create(
            thread=self.thread,
            sequence=sequence,
            role=MessageRole.ASSISTANT,
            content='answer',
            modality=kwargs.get('modality', TurnModality.TEXT),
            metadata=metadata,
        )

    def _run(self, *args):
        out = StringIO()
        call_command('grounding_soak_report', *args, stdout=out)
        return out.getvalue()

    def test_counts_and_downgrade_rows(self):
        self._message(1, _assessment())
        self._message(
            2,
            _assessment(
                audit_grounded=False,
                would_downgrade=True,
                citation_count=0,
                ungrounded_identifiers=['PS-100'],
            ),
        )
        self._message(3)  # no assessment: not counted

        report = json.loads(self._run('--json'))
        self.assertEqual(report['assessed_turns'], 2)
        self.assertEqual(report['modes'], {'shadow': 2})
        self.assertEqual(report['validator_applied'], 2)
        self.assertEqual(report['audit_verdicts'], {'True': 1, 'False': 1})
        self.assertEqual(report['would_downgrade_count'], 1)
        row = report['would_downgrade'][0]
        self.assertEqual(row['ungrounded_identifiers'], ['PS-100'])
        self.assertFalse(row['downgraded'])

    def test_read_only_and_human_output(self):
        self._message(1, _assessment(would_downgrade=True, audit_grounded=False))
        before = list(
            ChatMessage.objects.order_by('sequence').values_list('metadata', flat=True)
        )
        text = self._run()
        after = list(
            ChatMessage.objects.order_by('sequence').values_list('metadata', flat=True)
        )
        self.assertEqual(before, after)
        self.assertIn('would-downgrade: 1', text)
