"""S7 WP-C6: the 25k complete-population benchmark gate (Q9 envelope).

Tagged ``performance_test`` so ``invoke dev.test`` excludes it; run it
explicitly as the enforce-flip gate for the analytics intents:

    python manage.py test tasks.tests.test_analytics_load --keepdb

Pass criteria (owner-approved 2026-08-29), each an assertion below:

1. aggregate over 25k rows <= 2.0 s at a FIXED query count;
2. operand version scan <= 1.0 s;
3. 25k-member evidence-set persist <= 5.0 s;
4. the terminal transaction carrying it <= 8.0 s;
5. <= 4 MB per membership set.

Postgres-asserted: numbers on SQLite say nothing about the deployment.
The pre-agreed fallback if criterion 3 fails on real hardware is
digest-only membership above 2,500 operands — do NOT soften a number
here without recording that decision.
"""

from __future__ import annotations

import datetime
import json
import time
import unittest
import uuid

from django.apps import apps

if not apps.is_installed('assets'):
    raise unittest.SkipTest('requires the full InvenTree app registry')

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, override_settings, tag

from assets.models import AssetMachine, AssetMaintenanceRecord, Client
from tasks import ai_analytics
from tasks.models import WorkOrder
from tasks.scope import MaintenanceScope

MACHINES = 500
WORK_ORDERS = 25_000
RECORDS = 25_000
YEARS_OF_HISTORY = 10

READ_FLAGS = {
    'AIMMS_MAINTENANCE_AI_READ_ENABLED': True,
    'AIMMS_PLANT_TIMEZONE': 'UTC',
}


def _elapsed(fn):
    started = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - started


@tag('performance_test')
@override_settings(**READ_FLAGS)
class AnalyticsLoadTests(TestCase):
    """The Q9-envelope numbers that gate the per-intent enforce flips."""

    @classmethod
    def setUpClass(cls):
        if connection.vendor != 'postgresql':
            raise unittest.SkipTest('benchmark is Postgres-asserted')
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        suffix = uuid.uuid4().hex[:6]
        cls.tenant = Client.objects.create(
            name=f'Load Plant {suffix}', code=f'load-{suffix}'
        )
        cls.actor = get_user_model().objects.create_superuser(
            username=f'load-actor-{suffix}', email='load@example.com', password='pw'
        )
        machines = AssetMachine.objects.bulk_create(
            AssetMachine(name=f'Load Machine {suffix} {index:03d}', client=cls.tenant)
            for index in range(MACHINES)
        )
        base = datetime.datetime(2016, 9, 1, 6, 0)
        day = datetime.timedelta(days=1)
        span_days = YEARS_OF_HISTORY * 365
        types = ('corrective', 'preventive', 'inspection', 'calibration')
        priorities = ('low', 'medium', 'high')
        WorkOrder.objects.bulk_create(
            (
                WorkOrder(
                    title=f'Load job {index}',
                    status=WorkOrder.STATUS_BACKLOG,
                    lifecycle_status='completed',
                    work_order_type=types[index % len(types)],
                    priority=priorities[index % len(priorities)],
                    machine=machines[index % MACHINES],
                    affected_component_ref=f'REF-{index % 40:03d}',
                    actual_started_at=base + (index % span_days) * day,
                    actual_completed_at=base
                    + (index % span_days) * day
                    + datetime.timedelta(hours=2),
                )
                for index in range(WORK_ORDERS)
            ),
            batch_size=2_000,
        )
        AssetMaintenanceRecord.objects.bulk_create(
            (
                AssetMaintenanceRecord(
                    machine=machines[index % MACHINES],
                    date=(base + (index % span_days) * day).date(),
                    summary=f'Load service {index}',
                )
                for index in range(RECORDS)
            ),
            batch_size=2_000,
        )

    def setUp(self):
        self.actor.maintenance_scopes = {
            MaintenanceScope(customer_id=None, site_key=None, client_id=self.tenant.pk)
        }

    def test_aggregate_within_budget_at_a_fixed_query_count(self):
        """Criterion 1: one grouped complete-population answer, five queries."""
        with self.assertNumQueries(5):
            result, seconds = _elapsed(
                lambda: ai_analytics.aggregate_work_orders(
                    self.actor, grouping='machine'
                )
            )
        self.assertEqual(result['population_count'], WORK_ORDERS)
        self.assertTrue(result['complete_population'])
        self.assertEqual(len(result['groups']), ai_analytics.HARD_GROUP_CAP)
        self.assertEqual(result['total_group_count'], MACHINES)
        self.assertLess(seconds, 2.0, f'aggregate took {seconds:.2f}s')

    def test_operand_version_scan_within_budget(self):
        """Criterion 2: the snapshot scan reads 25k version rows fast."""
        result, seconds = _elapsed(
            lambda: ai_analytics.work_order_operand_versions(
                self.actor, limit=25_000, require_machine=True
            )
        )
        self.assertTrue(result['available'])
        self.assertFalse(result['overflow'])
        self.assertEqual(len(result['rows']), WORK_ORDERS)
        self.assertLess(seconds, 1.0, f'version scan took {seconds:.2f}s')

    def test_terminal_persist_within_budget(self):
        """Criteria 3+4+5: the real terminal write carries 25k members."""
        from aichat.models import ChatEvidenceSetMember, TurnModality, TurnState
        from aichat.services import ThreadRepository, canonical_request_fingerprint

        versions = ai_analytics.work_order_operand_versions(
            self.actor, limit=25_000, require_machine=True
        )
        members = [
            (index + 1, 'work_order', str(pk), version)
            for index, (pk, version) in enumerate(versions['rows'])
        ]
        spec = {
            'id': 'set_' + uuid.uuid4().hex,
            'source_class': 'work_order',
            'filters': {'date_field': 'created_at'},
            'population_count': len(members),
            'evaluated_count': len(members),
            'displayed_count': ai_analytics.HARD_GROUP_CAP,
            'complete_population': True,
            'high_watermarks': {},
            'snapshot_hash': 'b' * 64,
            'supports_expansion': True,
            'member_cap': 25_000,
            'calculation': {'operation': 'group_count', 'result': str(MACHINES)},
            'members': members,
            'authorization_scope_hash': 'a' * 64,
            'analysis_scope_hash': 'c' * 64,
        }
        # Criterion 5 first: the membership payload itself stays bounded.
        payload_bytes = len(json.dumps(members).encode())
        self.assertLess(payload_bytes, 4 * 1024 * 1024)

        repository = ThreadRepository(self.actor.pk, 'site:load')
        thread, _ = repository.get_or_create()
        fingerprint = canonical_request_fingerprint(
            content='benchmark', modality=TurnModality.TEXT, trusted_context={}
        )
        turn = repository.begin_turn(
            thread.pk,
            content='benchmark',
            modality=TurnModality.TEXT,
            trusted_context={},
            modality_metadata={},
            idempotency_key='turn:load-benchmark',
            request_fingerprint=fingerprint,
            correlation_id='corr-load',
        ).turn

        def _terminal():
            with transaction.atomic():
                repository.terminal(
                    turn.pk,
                    state=TurnState.COMPLETE,
                    canonical_result={
                        'kind': 'evidence_analysis',
                        'response_version': 2,
                        'response_state': 'complete',
                        'detailed_response': 'benchmark',
                        'spoken_summary': '',
                    },
                    evidence_sets=[spec],
                )

        _result, seconds = _elapsed(_terminal)
        self.assertEqual(
            ChatEvidenceSetMember.objects.filter(set_id=spec['id']).count(),
            WORK_ORDERS,
        )
        self.assertLess(seconds, 8.0, f'terminal transaction took {seconds:.2f}s')
        # Criterion 3: the membership write alone, measured on a bare set.
        from aichat.models import ChatEvidenceSet

        bare = ChatEvidenceSet.objects.create(
            id='set_' + uuid.uuid4().hex,
            turn=turn,
            source_class='work_order',
            population_count=len(members),
            evaluated_count=len(members),
            complete_population=True,
        )
        rows = [
            ChatEvidenceSetMember(
                set=bare,
                ordinal=ordinal,
                source_class=source_class,
                source_object_id=object_id,
                source_version=version,
            )
            for ordinal, source_class, object_id, version in members
        ]
        _result, seconds = _elapsed(
            lambda: ChatEvidenceSetMember.objects.bulk_create(rows, batch_size=2_000)
        )
        self.assertLess(seconds, 5.0, f'membership persist took {seconds:.2f}s')
