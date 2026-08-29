"""S15 (WP-B4): the §8.10 operations report and §16 shadow-review report.

Seeds the exact persisted shapes each metric reads and asserts the
sections — and that no message content ever reaches the output.
"""

import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone


def _report(command, *args):
    out = StringIO()
    call_command(command, '--json', *args, stdout=out)
    return json.loads(out.getvalue())


class ReportFixtureMixin:
    """One thread with the full metadata menagerie."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        from aichat.models import (
            AIRequestRejection,
            ChatMessage,
            ChatThread,
            ChatTurn,
            ControlledDocument,
            RetrievalMiss,
            TurnState,
        )

        cls.user = get_user_model().objects.create_user(
            username='ops-owner', password='unused'
        )
        cls.thread = ChatThread.objects.create(
            owner=cls.user, scope_key='site:pilot', scope_hash='0' * 64
        )

        def message(sequence, content, **metadata):
            return ChatMessage.objects.create(
                thread=cls.thread,
                role='assistant',
                content=content,
                sequence=sequence,
                metadata=metadata,
            )

        # A grounded analysis answer: usage + coverage + gate + grounding.
        cls.answer = message(
            1,
            'There are 28 recorded work orders. [1]',
            usage={'totals': {'input_tokens': 900, 'output_tokens': 100, 'total_tokens': 1000}},
            evidence_analysis={
                'coverage': {'population_count': 28, 'complete_population': True}
            },
            evidence_gate={'scan': 'prose-v1', 'intent': 'record_retrieval', 'would_fail': []},
            grounding={'verdict': 'pass'},
        )
        # A would-fail prose scan + a rehearsal verdict.
        message(
            2,
            'Prose answer with an unclosed value 42.',
            evidence_gate={
                'scan': 'prose-v1',
                'intent': 'fleet_aggregate',
                'would_fail': ['unclosed_value'],
            },
        )
        message(
            3,
            'Rehearsed.',
            evidence_gate={'mode': 'shadow_rehearsal', 'verdict': 'abstain', 'codes': ['C08']},
        )
        # An incomplete-coverage answer.
        message(
            4,
            'Partial view.',
            evidence_analysis={
                'coverage': {'population_count': 403, 'complete_population': False}
            },
            grounding={'verdict': 'would_downgrade', 'would_downgrade': True},
        )
        # A verbose refusal (safety-length violation).
        cls.long_refusal = message(5, 'word ' * 220)

        now = timezone.now()

        def turn(pk_suffix, *, state, modality='text', seconds=5.0, canonical=None, output=None):
            prompt = ChatMessage.objects.create(
                thread=cls.thread,
                role='user',
                content='question',
                sequence=100 + pk_suffix,
            )
            if output is None:
                output = ChatMessage.objects.create(
                    thread=cls.thread,
                    role='assistant',
                    content='answer',
                    sequence=200 + pk_suffix,
                )
            row = ChatTurn.objects.create(
                thread=cls.thread,
                modality=modality,
                request_fingerprint='f' * 64,
                idempotency_key=f'ops:{pk_suffix}',
                state=state,
                canonical_result=canonical if canonical is not None else {},
                input_message=prompt,
                output_message=output,
                completed_at=now + timedelta(seconds=seconds),
            )
            return row

        # Divergent: ANALYSIS intent, read-only, text, non-analysis mode.
        turn(
            1,
            state=TurnState.COMPLETE,
            seconds=4.0,
            canonical={
                'workflow_used': 'wf8',
                'route': {
                    'mode': 'wf8_lookup',
                    'task_intent': 'fleet_aggregate',
                    'effect_intent': 'read_only',
                },
            },
            output=cls.answer,
        )
        # Non-divergent: analysis mode.
        turn(
            2,
            state=TurnState.COMPLETE,
            seconds=8.0,
            canonical={
                'workflow_used': 'analysis_executor',
                'route': {
                    'mode': 'analysis',
                    'task_intent': 'record_retrieval',
                    'effect_intent': 'read_only',
                },
            },
        )
        # A failed voice turn and the verbose refusal turn.
        turn(3, state=TurnState.FAILED, modality='voice', seconds=2.0, canonical={})
        turn(
            4,
            state=TurnState.COMPLETE,
            seconds=1.0,
            canonical={'workflow_used': 'safety_refusal', 'route': None},
            output=cls.long_refusal,
        )

        RetrievalMiss.objects.create(
            user=cls.user,
            query='q',
            corpus='reader',
            scope_mode='explicit_assets',
            scope_enforced=False,
            out_of_scope_hits=3,
        )
        RetrievalMiss.objects.create(
            user=cls.user,
            query='q2',
            corpus='controlled_documents',
            scope_mode='explicit_assets',
            scope_enforced=True,
            out_of_scope_hits=1,
        )
        AIRequestRejection.objects.create(code='pilot_stopped', user=cls.user)
        AIRequestRejection.objects.create(code='rate_limited', user=cls.user)
        ControlledDocument.objects.create(
            document_id='DOC-1',
            revision='A',
            title='Doc',
            document_class='service_manual',
            scope_key='site:pilot',
            scope_hash='0' * 64,
            access_class='internal',
            source_filename='doc.md',
            source_location='x',
            state='indexed',
            is_current=True,
            source_sha256='1' * 64,
            search_index_name='idx',
        )


class PilotOpsReportTests(ReportFixtureMixin, TestCase):
    """Every §8.10 section reads its persisted source."""

    def test_sections_aggregate_the_seeded_shapes(self):
        """Counts, rates, and codes land where §8.10 names them."""
        report = _report('pilot_ops_report', '--days', '1')

        self.assertFalse(report['latch']['latched'])
        self.assertEqual(report['turns']['total'], 4)
        self.assertEqual(report['turns']['states']['complete'], 3)
        self.assertEqual(report['turns']['incomplete_or_failed_rate'], 0.25)
        self.assertEqual(report['turns']['latency_s']['text']['n'], 3)

        self.assertEqual(report['rejections']['total'], 2)
        self.assertEqual(report['rejections']['by_code']['pilot_stopped'], 1)

        divergence = report['routes']['divergence']
        self.assertEqual(divergence['count'], 1)
        self.assertEqual(divergence['by_intent'], {'fleet_aggregate': 1})
        self.assertEqual(report['routes']['workflow_distribution']['safety_refusal'], 1)

        self.assertEqual(report['scope']['scoped_searches'], 2)
        self.assertEqual(report['scope']['shadow_would_reject'], 1)
        self.assertEqual(report['scope']['enforced_filtered'], 1)

        self.assertEqual(report['coverage']['answers_with_coverage'], 2)
        self.assertEqual(report['coverage']['complete_population_rate'], 0.5)

        self.assertEqual(report['validator']['prose_scans'], 2)
        self.assertEqual(report['validator']['rehearsals'], 1)
        self.assertEqual(report['validator']['would_fail_codes']['unclosed_value'], 1)
        self.assertEqual(report['validator']['verdicts']['abstain'], 1)

        self.assertEqual(report['grounding']['turns_with_grounding'], 2)
        self.assertEqual(report['grounding']['mismatch_rate'], 0.5)

        self.assertEqual(report['safety_length'], {'checked': 1, 'violations': 1})
        self.assertEqual(report['usage']['totals']['total_tokens'], 1000)

    def test_output_is_content_free(self):
        """No message text, prompt, or excerpt reaches the report."""
        out = StringIO()
        call_command('pilot_ops_report', '--json', '--days', '1', stdout=out)
        rendered = out.getvalue()
        self.assertNotIn('work orders', rendered)
        self.assertNotIn('Prose answer', rendered)
        self.assertNotIn('Partial view', rendered)

    def test_retention_section_reports_job_health(self):
        """The S16 gate-11 evidence: last-run age, backlog, outbox counts."""
        report = _report('pilot_ops_report', '--days', '1')
        retention = report['retention']
        # Never run in this fixture: age is null, counts are concrete.
        self.assertIsNone(retention['last_run_age_days'])
        for family in ('threads', 'usage_messages', 'retrieval_misses', 'rejections'):
            self.assertIn(family, retention['backlog'])
        self.assertEqual(
            retention['outbox'], {'pending': 0, 'failed_permanent': 0}
        )
        self.assertEqual(retention['last_run_errors'], [])

        from aichat.services import retention as retention_service

        retention_service.run_all(dry_run=False, families={'rejections'})
        report = _report('pilot_ops_report', '--days', '1')
        self.assertIsNotNone(report['retention']['last_run_age_days'])


class ShadowReviewReportTests(ReportFixtureMixin, TestCase):
    """The six §16 items ride the same aggregators, run-scoped."""

    def test_six_items_present_with_the_frozen_baseline(self):
        """Every §16 inspection item lands in one document."""
        report = _report('shadow_review_report', '--days', '1')
        for key in (
            'route_vs_intent',
            'out_of_scope',
            'validator_reasons',
            'applicability_unresolved',
            'coverage',
            'latency_tokens',
        ):
            self.assertIn(key, report)
        self.assertEqual(
            report['latency_tokens']['frozen_baseline']['median_s'], 9.3
        )
        self.assertEqual(report['applicability_unresolved']['current_documents'], 1)
        self.assertEqual(report['applicability_unresolved']['blank_asset_binding'], 1)

    def test_bad_iso_window_is_a_typed_error(self):
        """Garbage windows fail loudly, not silently empty."""
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('shadow_review_report', '--since', 'not-a-date', stdout=StringIO())
