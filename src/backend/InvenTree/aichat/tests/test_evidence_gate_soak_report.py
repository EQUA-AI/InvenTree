"""S10 WP-A7: the evidence-gate soak report aggregates stored blobs only."""

import json
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from aichat.models import MessageRole, TurnModality
from aichat.services import ThreadRepository


class EvidenceGateSoakReportTests(TestCase):
    """Read-only aggregates; content-free by construction."""

    def setUp(self) -> None:
        """One thread with a mix of scan, rehearsal, and unscanned messages."""
        user = get_user_model().objects.create_user(username='soak-owner')
        self.repository = ThreadRepository(user.pk, 'site:main')
        self.thread, _ = self.repository.get_or_create()

    def _message(self, metadata: dict) -> None:
        self.repository.append(
            self.thread.pk,
            role=MessageRole.ASSISTANT,
            content='answer text',
            modality=TurnModality.TEXT,
            metadata=metadata,
        )

    def test_report_aggregates_codes_intents_and_rehearsals(self) -> None:
        """Counts per would-fail code and rehearsal verdict; no content."""
        self._message({
            'evidence_gate': {
                'scan': 'prose-v1',
                'intent': 'record_retrieval',
                'would_fail': ['unclosed_identifier', 'unclosed_value'],
                'counts': {'unclosed_identifiers': 1, 'unclosed_values': 2},
            }
        })
        self._message({
            'evidence_gate': {
                'scan': 'prose-v1',
                'intent': 'manual_fact',
                'would_fail': [],
                'counts': {'unclosed_identifiers': 0, 'unclosed_values': 0},
            }
        })
        self._message({
            'evidence_gate': {'mode': 'shadow_rehearsal', 'verdict': 'pass'}
        })
        self._message({'other': True})

        out = StringIO()
        call_command('evidence_gate_soak_report', '--json', stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report['turns_with_gate_blobs'], 3)
        self.assertEqual(report['prose_scans'], 2)
        self.assertEqual(report['prose_would_fail_turns'], 1)
        self.assertEqual(report['prose_would_fail_rate'], 0.5)
        self.assertEqual(report['would_fail_codes']['unclosed_identifier'], 1)
        self.assertEqual(report['intents']['record_retrieval'], 1)
        self.assertEqual(report['rehearsals'], 1)
        self.assertEqual(report['rehearsal_verdicts']['pass'], 1)

    def test_empty_window_reports_zero(self) -> None:
        """No blobs -> honest zeros, no invented rates."""
        out = StringIO()
        call_command('evidence_gate_soak_report', '--json', stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report['turns_with_gate_blobs'], 0)
        self.assertIsNone(report['prose_would_fail_rate'])
