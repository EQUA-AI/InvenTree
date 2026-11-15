"""Independent PostgreSQL commit ordering and durable shared-state ownership."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from queue import Queue
from threading import Barrier, Event, Thread
from unittest import skipUnless
from uuid import uuid4

from django.db import close_old_connections, connection, transaction
from django.test import TransactionTestCase

from integration_state.tests.test_concurrency import (
    PROCESS_BOOTSTRAP as PLACEMENT_BOOTSTRAP,
)

PROCESS_BOOTSTRAP = (
    PLACEMENT_BOOTSTRAP.split(
        'from integration_state.tests.test_concurrency import process_probe', 1
    )[0]
    + """
from integration_state.tests.test_shared_concurrency import delivery_process_probe
print('S5_SHARED_PROBE=' + json.dumps(delivery_process_probe(data)))
"""
)


SCAN_PROCESS_BOOTSTRAP = (
    PLACEMENT_BOOTSTRAP.split(
        'from integration_state.tests.test_concurrency import process_probe', 1
    )[0]
    + """
from integration_state.tests.test_shared_concurrency import scan_process_probe
print('S5_SCAN_PROBE=' + json.dumps(scan_process_probe(data)))
"""
)


def scan_process_probe(data):
    """Recover retained scans in exited processes without emitting attempt values."""
    from integration_state import shared_reconciliation as durable
    from integration_state.models import ReconciliationAttempt, ReconciliationScan
    from integration_state.s7_reconciliation import ScanPage, ScanProgress
    from integration_state.shared_events import SharedConflictError

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_user, pg_backend_pid()')
        database, user, backend = cursor.fetchone()
    if database != 's5_control_test' or user != 'postgres':
        raise AssertionError('Scan process reached an unbound database')
    row = ReconciliationScan.objects.get(pk=data['scan_id'])
    binding = durable._binding_from(row.binding)
    current = durable.read_scan(row.pk, binding=binding, authorize=lambda value: True)
    result = {'pid': os.getpid(), 'backend_pid': backend, 'stage': data['stage']}
    if data['stage'] == 'reserve':
        current = durable.reserve_scan_page(current, authorize=lambda value: True)
    elif data['stage'] == 'recover':
        current = durable.abandon_scan_page(
            current,
            attempt=current.progress.pending_attempt,
            authorize=lambda value: True,
        )
        current = durable.retry_scan(current, authorize=lambda value: True)
        current = durable.reserve_scan_page(current, authorize=lambda value: True)
        current = durable.accept_scan_page(
            current,
            ScanPage(binding=binding, offset=0, total=0, items=(), next_offset=None),
            attempt=current.progress.pending_attempt,
            authorize=lambda value: True,
        )
    elif data['stage'] == 'stale':
        old = ReconciliationAttempt.objects.get(scan=row, ordinal=1)
        prior = durable.DurableScan(
            scan_id=row.pk,
            namespace_id=row.namespace_id,
            version=old.reserved_version,
            progress=ScanProgress(
                binding=binding,
                max_pages=row.max_pages,
                max_records=row.max_records,
                attempts_used=1,
                pending_attempt=old.attempt_id,
            ),
        )
        try:
            durable.accept_scan_page(
                prior,
                ScanPage(
                    binding=binding, offset=0, total=0, items=(), next_offset=None
                ),
                attempt=old.attempt_id,
                authorize=lambda value: True,
            )
        except SharedConflictError:
            result['refused'] = True
        else:
            result['refused'] = False
    elif data['stage'] not in ('pending', 'final'):
        raise ValueError('Unknown synthetic scan process stage')
    result.update(
        state=current.progress.state,
        used=current.progress.attempts_used,
        pending=current.progress.pending_attempt is not None,
        population_complete=current.progress.population_complete,
        attempts=ReconciliationAttempt.objects.filter(scan=row).count(),
    )
    return result


PRIVATE_PROCESS_BOOTSTRAP = (
    PLACEMENT_BOOTSTRAP.split(
        'from integration_state.tests.test_concurrency import process_probe', 1
    )[0]
    + """
from integration_state.tests.test_shared_concurrency import private_process_probe
print('S5_PRIVATE_PROBE=' + json.dumps(private_process_probe(data)))
"""
)


def private_process_probe(data):
    """Recover only synthetic private metadata in the exact owned database."""
    from dataclasses import replace

    from integration_state import shared_requests as durable
    from integration_state.models import (
        PrivateCopyAttempt,
        PrivateCopyImpact,
        PrivateDeletionRequest,
    )
    from integration_state.shared_events import SharedConflictError

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_user, pg_backend_pid()')
        database, user, backend = cursor.fetchone()
    if database != 's5_control_test' or user != 'postgres':
        raise AssertionError('Private process reached an unbound database')
    impact = PrivateCopyImpact.objects.get(pk=data['impact_pk'])
    request = PrivateDeletionRequest.objects.get(pk=impact.request_id)
    intent, claim = (
        durable._intent_from(request.payload),
        durable._impact_from(impact.payload),
    )
    current = durable.read_private_copy_progress(
        impact.pk, intent=intent, impact=claim, authorize=lambda value: True
    )
    result = {'pid': os.getpid(), 'backend_pid': backend, 'stage': data['stage']}
    if data['stage'] == 'pending':
        current = durable.record_private_copy_progress(
            current,
            replace(current.progress, sequence=1, status='pending'),
            intent=intent,
            impact=claim,
            authorize=lambda value: True,
        )
    elif data['stage'] == 'recover':
        current = durable.record_private_copy_progress(
            current,
            replace(current.progress, sequence=2, status='uncertain'),
            intent=intent,
            impact=claim,
            authorize=lambda value: True,
        )
        current = durable.retry_private_copy_attempt(
            current,
            intent=intent,
            impact=claim,
            attempt=uuid4(),
            authorize=lambda value: True,
        )
    elif data['stage'] == 'stale':
        old = PrivateCopyAttempt.objects.get(impact=impact, ordinal=1)
        snapshot = durable.DurableCopyProgress(
            attempt_pk=old.pk,
            impact_pk=impact.pk,
            namespace_id=request.namespace_id,
            version=old.version,
            progress=durable._progress_from(old.checkpoint),
        )
        try:
            durable.record_private_copy_progress(
                snapshot,
                snapshot.progress,
                intent=intent,
                impact=claim,
                authorize=lambda value: True,
            )
        except SharedConflictError:
            result['refused'] = True
        else:
            result['refused'] = False
    elif data['stage'] not in ('inspect', 'final'):
        raise ValueError('Unknown synthetic private process stage')
    result.update(
        status=current.progress.status,
        used=current.progress.attempts_used,
        attempts=PrivateCopyAttempt.objects.filter(impact=impact).count(),
        native_authorized=current.native_authorized,
        erasure_complete=current.erasure_complete,
    )
    return result


def delivery_process_probe(data):
    """Operate only on the inherited exact owned DB; emit no payload/token values."""
    from unittest.mock import patch

    from django.utils import timezone

    from integration_state.models import (
        ConsumerAspectProgress,
        EventConsumer,
        EventDelivery,
    )
    from integration_state.shared_delivery import _claim, ack_change, claim_changes
    from integration_state.shared_events import SharedConflictError

    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_user, pg_backend_pid()')
        database, user, backend = cursor.fetchone()
    if database != 's5_control_test' or user != 'postgres':
        raise AssertionError('Shared process reached an unbound database')
    consumer = EventConsumer.objects.get(pk=data['consumer_id'])
    result = {'pid': os.getpid(), 'backend_pid': backend, 'stage': data['stage']}
    if data['stage'] == 'reserve':
        claims = claim_changes(
            consumer.pk, worker_key='exited-worker', limit=1, lease_seconds=60
        )
        if len(claims) != 1:
            raise AssertionError('Expected one initial durable process reservation')
        result.update(
            delivery_id=claims[0].delivery_id, fence=claims[0].ownership_fence
        )
    elif data['stage'] == 'pending':
        result['new_claims'] = len(
            claim_changes(
                consumer.pk, worker_key='other-worker', limit=1, lease_seconds=60
            )
        )
        result['state'] = EventDelivery.objects.get(consumer=consumer).state
    elif data['stage'] == 'recover_ack':
        with patch.object(
            timezone, 'now', return_value=datetime.fromisoformat(data['clock'])
        ):
            claims = claim_changes(
                consumer.pk, worker_key='restarted-worker', limit=1, lease_seconds=60
            )
            if len(claims) != 1:
                raise AssertionError('Expected one expired durable process reservation')
            receipt = ack_change(claims[0], outcome='applied')
            result.update(fence=claims[0].ownership_fence, outcome=receipt.outcome)
    elif data['stage'] == 'final':
        result.update(
            attempts=EventDelivery.objects.filter(consumer=consumer).count(),
            acknowledged=EventDelivery.objects.filter(
                consumer=consumer, state='acknowledged'
            ).count(),
            revision=ConsumerAspectProgress.objects.get(consumer=consumer).revision,
        )
    elif data['stage'] == 'old_ack':
        old = EventDelivery.objects.filter(consumer=consumer).order_by('fence').first()
        try:
            ack_change(_claim(old, consumer), outcome='applied')
        except SharedConflictError:
            result['refused'] = True
        else:
            result['refused'] = False
    else:
        raise ValueError('Unknown synthetic process stage')
    return result


@skipUnless(
    connection.vendor == 'postgresql', 'Requires independent PostgreSQL sessions'
)
class SharedDeliveryConcurrencyTests(TransactionTestCase):
    """Actual committed native writes, not callbacks pretending to be sessions."""

    def setUp(self):
        """Each testcase owns its synthetic active source and private consumers."""
        super().setUp()
        from integration_state.models import SourceNamespace

        self.namespace = SourceNamespace.objects.create(
            environment='synthetic-commit-order', active=True
        )
        self.worker_pids = set()

    def start_connection(self, operation):
        """Capture each actual backend PID and transport worker failures to parent."""
        outcome = Queue()

        def run():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SET statement_timeout = 8000')
                    cursor.execute('SELECT pg_backend_pid()')
                    pid = cursor.fetchone()[0]
                try:
                    outcome.put((pid, operation(), None))
                except Exception as exc:
                    outcome.put((pid, None, exc))
            finally:
                connection.close()

        thread = Thread(target=run, daemon=True)
        thread.start()
        return thread, outcome

    def finish_connection(self, task):
        """Drain every worker before test database flush; assert independent PID."""
        thread, outcome = task
        thread.join(12)
        self.assertFalse(thread.is_alive(), 'Owned shared-state worker did not stop')
        self.assertFalse(
            outcome.empty(), 'Owned shared-state worker returned no result'
        )
        pid, value, error = outcome.get_nowait()
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid()')
            self.assertNotEqual(pid, cursor.fetchone()[0])
        self.assertNotIn(pid, self.worker_pids)
        self.worker_pids.add(pid)
        return value, error

    def test_lower_native_event_committing_after_higher_ack_remains_deliverable(self):
        """An allocated ID is not a committed prefix; selection has no watermark."""
        from assets.models import AssetMachine, Client
        from integration_state.models import EventDelivery, SourceChange
        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )

        owner = Client.objects.create(
            name='Synthetic commit-order source', code='commit-order'
        )
        consumer = configure_consumer(
            self.namespace.pk,
            'commit-order-consumer',
            aspects=(('machine', 'placement'),),
            configuration_epoch=uuid4(),
        )
        entered, release = Event(), Event()
        staged = {}

        def late_native_commit():
            with transaction.atomic():
                machine = AssetMachine.objects.create(
                    name='Synthetic lower late commit', client=owner
                )
                staged['event_id'] = SourceChange.objects.get(source_id=machine.pk).pk
                staged['machine_id'] = machine.pk
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Owned late-commit barrier timed out')
            return staged['event_id']

        task = self.start_connection(late_native_commit)
        try:
            self.assertTrue(entered.wait(10), 'Actual native capture was not reached')
            higher = AssetMachine.objects.create(
                name='Synthetic higher early commit', client=owner
            )
            higher_event = SourceChange.objects.get(source_id=higher.pk)
            self.assertGreater(higher_event.pk, staged['event_id'])
            self.assertFalse(
                SourceChange.objects.filter(pk=staged['event_id']).exists()
            )
            first = claim_changes(
                consumer.consumer_id,
                worker_key='early-worker',
                limit=1,
                lease_seconds=60,
            )
            self.assertEqual([claim.change_id for claim in first], [higher_event.pk])
            self.assertEqual(ack_change(first[0], outcome='applied').outcome, 'applied')
        finally:
            release.set()
            value, error = self.finish_connection(task)
        self.assertIsNone(error, repr(error))
        self.assertEqual(value, staged['event_id'])
        later = claim_changes(
            consumer.consumer_id, worker_key='late-worker', limit=1, lease_seconds=60
        )
        self.assertEqual([claim.change_id for claim in later], [staged['event_id']])
        self.assertEqual(ack_change(later[0], outcome='applied').outcome, 'applied')
        self.assertEqual(EventDelivery.objects.filter(state='acknowledged').count(), 2)
        self.assertEqual(
            AssetMachine.objects.filter(
                pk__in=(higher.pk, staged['machine_id']), client=owner
            ).count(),
            2,
        )

    def generic_consumer(self):
        """One real committed generic event for independent ownership controls."""
        from integration_state.shared_contracts import (
            SharedSourceKey,
            SourceObservation,
        )
        from integration_state.shared_delivery import configure_consumer
        from integration_state.shared_events import record_observation

        key = SharedSourceKey(
            namespace_id=self.namespace.pk,
            environment=self.namespace.environment,
            epoch=self.namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id='restart-7',
        )
        record_observation(
            key,
            event_key='committed-process-event',
            expected_fence=0,
            observation=SourceObservation(state='available'),
        )
        return configure_consumer(
            self.namespace.pk,
            'independent-consumer',
            aspects=(('synthetic_document', 'metadata'),),
            configuration_epoch=uuid4(),
        )

    def test_two_independent_claimers_cannot_own_the_same_event(self):
        """Actual metadata locks serialize concurrent leases on the same consumer."""
        from integration_state.models import EventDelivery
        from integration_state.shared_delivery import claim_changes

        consumer = self.generic_consumer()
        barrier = Barrier(3, timeout=10)

        def claim(worker):
            barrier.wait()
            return claim_changes(
                consumer.consumer_id, worker_key=worker, limit=1, lease_seconds=60
            )

        first = self.start_connection(lambda: claim('first-worker'))
        second = self.start_connection(lambda: claim('second-worker'))
        try:
            barrier.wait()
        finally:
            a, ea = self.finish_connection(first)
            b, eb = self.finish_connection(second)
        self.assertIsNone(ea, repr(ea))
        self.assertIsNone(eb, repr(eb))
        self.assertEqual(sorted((len(a), len(b))), [0, 1])
        self.assertEqual(EventDelivery.objects.filter(state='claimed').count(), 1)

    def test_five_exited_processes_recover_reservation_expiry_ack_and_stale_owner(self):
        """Restart evidence is fresh Django interpreters, not connection reopen."""
        from integration_state.models import EventDelivery

        consumer = self.generic_consumer()
        processes, backends = set(), set()

        def probe(stage, **extra):
            payload = {
                'root': '/source/src/backend/InvenTree',
                'consumer_id': consumer.consumer_id,
                'stage': stage,
                **extra,
            }
            completed = subprocess.run(
                [sys.executable, '-B', '-c', PROCESS_BOOTSTRAP],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            lines = [
                line.removeprefix('S5_SHARED_PROBE=')
                for line in completed.stdout.splitlines()
                if line.startswith('S5_SHARED_PROBE=')
            ]
            self.assertEqual(len(lines), 1)
            result = json.loads(lines[0])
            self.assertNotEqual(result['pid'], os.getpid())
            self.assertNotIn(result['pid'], processes)
            self.assertNotIn(result['backend_pid'], backends)
            processes.add(result['pid'])
            backends.add(result['backend_pid'])
            return result

        first = probe('reserve')
        self.assertEqual(first['fence'], 1)
        pending = probe('pending')
        self.assertEqual(pending['state'], 'claimed')
        self.assertEqual(pending['new_claims'], 0)
        expires = EventDelivery.objects.get(
            consumer_id=consumer.consumer_id
        ).lease_until
        recovered = probe(
            'recover_ack', clock=(expires + timedelta(seconds=1)).isoformat()
        )
        self.assertEqual(recovered['fence'], 2)
        self.assertEqual(recovered['outcome'], 'applied')
        final = probe('final')
        self.assertEqual(
            (final['attempts'], final['acknowledged'], final['revision']), (2, 1, 1)
        )
        self.assertTrue(probe('old_ack')['refused'])
        self.assertEqual(len(processes), 5)

    def private_coordinates(self):
        """Commit real private fixture metadata without native source privileges."""
        from assets.integration_contracts import ClientSourceIdentity
        from integration_state import shared_requests as durable
        from integration_state.s7_contracts import SourceAspectIdentity
        from integration_state.s7_deletion_requests import CopyImpact, DeletionIntent

        source = SourceAspectIdentity(
            environment=self.namespace.environment,
            epoch=self.namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            source_id='private-fixture-7',
        )
        intent = DeletionIntent(
            request_id=uuid4(),
            request_key='independent-private-request',
            request_revision=1,
            actor_key='synthetic-owner',
            client=ClientSourceIdentity(self.namespace.environment, 7),
            scope=(source,),
        )
        request = durable.record_private_intent(
            self.namespace.pk, intent, authorize=lambda value: True
        )
        impact = CopyImpact(
            copy_key='independent-private-copy',
            source=source,
            copy_revision='fixture-r1',
            owners=(intent.client,),
            attribution='source_claimed',
            retention='reviewable',
        )
        stored = durable.record_copy_impact(
            request.request_pk,
            impact,
            intent=intent,
            copy_kind='derivative',
            authorize=lambda value: True,
        )
        initial = durable.start_private_copy_attempt(
            stored.impact_pk,
            intent=intent,
            impact=impact,
            attempt=uuid4(),
            max_attempts=2,
            authorize=lambda value: True,
        )
        return intent, impact, initial

    def test_concurrent_private_copy_updates_keep_one_exact_sequence_winner(self):
        """Different same-sequence responses cannot both commit in separate sessions."""
        from dataclasses import replace

        from integration_state import shared_requests as durable
        from integration_state.models import PrivateCopyAttempt
        from integration_state.shared_events import SharedConflictError

        intent, impact, initial = self.private_coordinates()
        barrier = Barrier(3, timeout=10)

        def update(status):
            barrier.wait()
            return durable.record_private_copy_progress(
                initial,
                replace(initial.progress, sequence=1, status=status),
                intent=intent,
                impact=impact,
                authorize=lambda value: True,
            )

        first = self.start_connection(lambda: update('pending'))
        second = self.start_connection(lambda: update('uncertain'))
        try:
            barrier.wait()
        finally:
            a, ea = self.finish_connection(first)
            b, eb = self.finish_connection(second)
        errors = [error for error in (ea, eb) if error is not None]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], SharedConflictError)
        winner = a if a is not None else b
        row = PrivateCopyAttempt.objects.get()
        self.assertEqual(row.version, 2)
        self.assertEqual(row.checkpoint['status'], winner.progress.status)

    def test_five_exited_private_processes_keep_uncertainty_history_and_stale_fence(
        self,
    ):
        """A lost synthetic worker does not erase its pending/uncertain predecessor."""
        _, _, initial = self.private_coordinates()
        processes, backends = set(), set()

        def probe(stage):
            completed = subprocess.run(
                [sys.executable, '-B', '-c', PRIVATE_PROCESS_BOOTSTRAP],
                input=json.dumps({
                    'root': '/source/src/backend/InvenTree',
                    'impact_pk': initial.impact_pk,
                    'stage': stage,
                }),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            lines = [
                line.removeprefix('S5_PRIVATE_PROBE=')
                for line in completed.stdout.splitlines()
                if line.startswith('S5_PRIVATE_PROBE=')
            ]
            self.assertEqual(len(lines), 1)
            result = json.loads(lines[0])
            self.assertNotEqual(result['pid'], os.getpid())
            self.assertNotIn(result['pid'], processes)
            self.assertNotIn(result['backend_pid'], backends)
            processes.add(result['pid'])
            backends.add(result['backend_pid'])
            return result

        self.assertEqual(probe('pending')['status'], 'pending')
        self.assertEqual(probe('inspect')['used'], 1)
        retry = probe('recover')
        self.assertEqual(
            (retry['used'], retry['attempts'], retry['status']), (2, 2, 'planned')
        )
        final = probe('final')
        self.assertFalse(final['native_authorized'])
        self.assertFalse(final['erasure_complete'])
        self.assertEqual(final['attempts'], 2)
        self.assertTrue(probe('stale')['refused'])
        self.assertEqual(len(processes), 5)

    def scan_coordinates(self):
        """An actor-bound synthetic scan, not native population authority."""
        from integration_state.s7_reconciliation import ScanBinding
        from integration_state.shared_reconciliation import begin_scan

        binding = ScanBinding(
            environment=self.namespace.environment,
            epoch=self.namespace.epoch,
            entity_kind='synthetic_document',
            aspect='metadata',
            actor_key='synthetic-scan-owner',
            client_id=7,
            filter_key='fixture-filter',
            generation='fixture-generation',
        )
        return begin_scan(
            self.namespace.pk,
            'independent-scan-request',
            binding,
            max_pages=2,
            max_records=5,
            authorize=lambda value: True,
        )

    def test_concurrent_scan_reservations_retain_one_committed_attempt(self):
        """Separate sessions cannot export two page leases from one checkpoint."""
        from integration_state.models import ReconciliationAttempt
        from integration_state.shared_events import SharedConflictError
        from integration_state.shared_reconciliation import reserve_scan_page

        initial = self.scan_coordinates()
        barrier = Barrier(3, timeout=10)

        def reserve():
            barrier.wait()
            return reserve_scan_page(initial, authorize=lambda value: True)

        first = self.start_connection(reserve)
        second = self.start_connection(reserve)
        try:
            barrier.wait()
        finally:
            a, ea = self.finish_connection(first)
            b, eb = self.finish_connection(second)
        errors = [error for error in (ea, eb) if error is not None]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], SharedConflictError)
        winner = a if a is not None else b
        self.assertEqual(winner.progress.attempts_used, 1)
        self.assertEqual(
            ReconciliationAttempt.objects.filter(scan_id=initial.scan_id).count(), 1
        )

    def test_epoch_writer_commits_while_source_owner_callback_is_held(self):
        """Owner IO holds no metadata lock and an old epoch cannot reserve."""
        from integration_state.models import ReconciliationAttempt, SourceNamespace
        from integration_state.shared_events import SharedStateError
        from integration_state.shared_reconciliation import reserve_scan_page

        initial = self.scan_coordinates()
        entered, release = Event(), Event()
        depths = []

        def owner(binding):
            depths.append(len(connection.atomic_blocks))
            if not entered.is_set():
                entered.set()
                if not release.wait(10):
                    raise AssertionError('Scan owner callback barrier timed out')
            return True

        task = self.start_connection(
            lambda: reserve_scan_page(initial, authorize=owner)
        )
        try:
            self.assertTrue(entered.wait(10))
            with connection.cursor() as cursor:
                cursor.execute('SET statement_timeout = 8000')
            SourceNamespace.objects.filter(pk=self.namespace.pk).update(epoch=uuid4())
        finally:
            release.set()
            value, error = self.finish_connection(task)
        self.assertIsNone(value)
        self.assertIsInstance(error, SharedStateError)
        self.assertTrue(depths)
        self.assertEqual(set(depths), {0})
        self.assertEqual(
            ReconciliationAttempt.objects.filter(scan_id=initial.scan_id).count(), 0
        )

    def test_five_exited_scan_processes_keep_pending_budget_and_stale_response_fence(
        self,
    ):
        """Fresh Django processes recover the same retained scan and attempt history."""
        initial = self.scan_coordinates()
        processes, backends = set(), set()

        def probe(stage):
            completed = subprocess.run(
                [sys.executable, '-B', '-c', SCAN_PROCESS_BOOTSTRAP],
                input=json.dumps({
                    'root': '/source/src/backend/InvenTree',
                    'scan_id': initial.scan_id,
                    'stage': stage,
                }),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            lines = [
                line.removeprefix('S5_SCAN_PROBE=')
                for line in completed.stdout.splitlines()
                if line.startswith('S5_SCAN_PROBE=')
            ]
            self.assertEqual(len(lines), 1)
            result = json.loads(lines[0])
            self.assertNotEqual(result['pid'], os.getpid())
            self.assertNotIn(result['pid'], processes)
            self.assertNotIn(result['backend_pid'], backends)
            processes.add(result['pid'])
            backends.add(result['backend_pid'])
            return result

        first = probe('reserve')
        self.assertEqual(
            (first['used'], first['attempts'], first['pending']), (1, 1, True)
        )
        pending = probe('pending')
        self.assertEqual((pending['used'], pending['pending']), (1, True))
        recovered = probe('recover')
        self.assertEqual(
            (recovered['used'], recovered['attempts'], recovered['state']),
            (2, 2, 'transport_complete'),
        )
        final = probe('final')
        self.assertFalse(final['population_complete'])
        self.assertEqual(final['attempts'], 2)
        self.assertTrue(probe('stale')['refused'])
        self.assertEqual(len(processes), 5)

    def test_concurrent_configurations_keep_one_active_owner_and_reject_old_lease(self):
        """Independent configuration writers cannot merge or inherit old receipts."""
        from integration_state.models import EventConsumer
        from integration_state.shared_delivery import (
            ack_change,
            claim_changes,
            configure_consumer,
        )
        from integration_state.shared_events import SharedStateError

        original = self.generic_consumer()
        old = claim_changes(
            original.consumer_id, worker_key='old-worker', limit=1, lease_seconds=60
        )[0]
        barrier = Barrier(3, timeout=10)

        def replace_configuration():
            barrier.wait()
            return configure_consumer(
                self.namespace.pk,
                'independent-consumer',
                aspects=(('synthetic_document', 'metadata'),),
                configuration_epoch=uuid4(),
            )

        first = self.start_connection(replace_configuration)
        second = self.start_connection(replace_configuration)
        try:
            barrier.wait()
        finally:
            a, ea = self.finish_connection(first)
            b, eb = self.finish_connection(second)
        self.assertIsNone(ea, repr(ea))
        self.assertIsNone(eb, repr(eb))
        current = EventConsumer.objects.get(active=True)
        self.assertIn(current.pk, (a.consumer_id, b.consumer_id))
        self.assertEqual(EventConsumer.objects.count(), 3)
        with self.assertRaises(SharedStateError):
            ack_change(old, outcome='applied')
        self.assertEqual(
            len(
                claim_changes(
                    current.pk, worker_key='current-worker', limit=1, lease_seconds=60
                )
            ),
            1,
        )
