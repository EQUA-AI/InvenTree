"""Deferred memory-cluster routing, pressure and liveness regressions."""

import io
import json
import os
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from django_q.brokers.orm import ORM
from django_q.models import OrmQ, Schedule, Success

from aichat.services import memory_worker as worker
from aichat.services.threads import ThreadRepository
from InvenTree.setting.worker import get_worker_config


def worker_config(**overrides):
    """Build configuration from defaults without reading deployment overrides."""

    def setting(name, key, default):
        return overrides.get(name, default)

    with mock.patch('InvenTree.setting.worker.get_setting', side_effect=setting):
        return get_worker_config('postgresql')


class MemoryWorkerConfigTests(SimpleTestCase):
    """The alternative configuration is independent of default-worker execution."""

    def test_default_and_alternative_contracts_are_distinct(self):
        """Adding the cluster leaves current worker defaults in place."""
        config = worker_config(INVENTREE_BACKGROUND_TIMEOUT=900)
        self.assertEqual(config['name'], 'InvenTree')
        self.assertEqual(config['timeout'], 900)
        memory = config['ALT_CLUSTERS']['ai-memory']
        self.assertEqual(memory['timeout'], 300)
        self.assertEqual(memory['retry'], 420)
        self.assertEqual(memory['workers'], 1)
        self.assertEqual(memory['queue_limit'], 20)
        self.assertEqual(memory['orm'], 'default')
        self.assertFalse(memory['sync'])
        self.assertIsNone(memory['django_redis'])

    def test_timeout_override_and_invalid_bounds(self):
        """Retry remains above the configured execution deadline."""
        config = worker_config(INVENTREE_MEMORY_TIMEOUT=600)
        self.assertEqual(config['ALT_CLUSTERS']['ai-memory']['retry'], 720)
        for value in (0, 29, 901, 'bad'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    worker_config(INVENTREE_MEMORY_TIMEOUT=value)


class MemoryWorkerTests(TestCase):
    """Fixtures never execute compaction or start a worker/model call."""

    def setUp(self):
        """Pin the queue contract and process state without changing real settings."""
        self.flags = SimpleNamespace(aimms_memory_worker_enabled=True)
        self.broker = mock.Mock(spec=ORM)
        self.broker.list_key = worker.CLUSTER
        for patcher in (
            override_settings(Q_CLUSTER=worker_config()),
            mock.patch.object(worker, 'get_settings', return_value=self.flags),
            mock.patch.object(worker, 'get_broker', return_value=self.broker),
            mock.patch.object(worker.Conf, 'ORM', 'default'),
            mock.patch.object(worker.Conf, 'CLUSTER_NAME', worker.CLUSTER),
            mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}),
        ):
            self.enterContext(patcher)

    def success(
        self, *, seconds_ago=0, cluster=worker.CLUSTER, func=worker.HEARTBEAT_TASK
    ):
        """Simulate persisted, content-free heartbeat completion metadata."""
        now = timezone.now() - timedelta(seconds=seconds_ago)
        return Success.objects.create(
            id=uuid.uuid4().hex,
            name='fixture',
            func=func,
            group=worker.CLUSTER,
            cluster=cluster,
            started=now,
            stopped=now,
            result={'cluster': worker.CLUSTER, 'status': 'alive'},
            success=True,
        )

    def test_pressure_is_cluster_scoped_and_thresholds_are_strict(self):
        """Other queues and future leases never create false old-work alarms."""
        now = timezone.now()
        OrmQ.objects.create(
            key='InvenTree',
            lock=now - timedelta(days=1),
            payload='UNREAD_PRIVATE_PAYLOAD',
        )
        OrmQ.objects.bulk_create([
            OrmQ(key=worker.CLUSTER, lock=now, payload='UNREAD_PRIVATE_PAYLOAD')
            for _ in range(50)
        ])
        self.assertFalse(worker.queue_pressure(now=now)['backpressured'])
        row = OrmQ.objects.create(key=worker.CLUSTER, lock=now, payload='private')
        self.assertTrue(worker.queue_pressure(now=now)['backpressured'])
        row.delete()
        OrmQ.objects.filter(key=worker.CLUSTER).update(
            lock=now - timedelta(seconds=600)
        )
        self.assertFalse(worker.queue_pressure(now=now)['backpressured'])
        self.assertTrue(
            worker.queue_pressure(now=now + timedelta(seconds=1))['backpressured']
        )
        OrmQ.objects.filter(key=worker.CLUSTER).update(
            lock=now + timedelta(seconds=420)
        )
        self.assertEqual(worker.queue_pressure(now=now)['oldest_due_seconds'], 0)
        self.assertNotIn('PRIVATE', json.dumps(worker.queue_pressure(now=now)))

    def test_only_fresh_matching_heartbeat_qualifies_liveness(self):
        """Default-cluster success or unrelated tasks cannot light this signal."""
        self.success(cluster='InvenTree')
        self.success(func='unrelated.task')
        self.success(seconds_ago=601)
        self.assertFalse(worker.worker_status()['heartbeat_fresh'])
        self.success()
        self.assertTrue(worker.worker_status()['heartbeat_fresh'])

    def test_execution_checks_actual_cluster_and_restore_hold(self):
        """An accidentally misrouted task never calls the compaction model path."""
        with mock.patch('aichat.tasks.compact_thread_summary') as compact:
            with mock.patch.object(worker.Conf, 'CLUSTER_NAME', 'InvenTree'):
                with self.assertRaises(ImproperlyConfigured):
                    worker.run_compaction('thread_fixture')
            with mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '1'}):
                with self.assertRaises(ImproperlyConfigured):
                    worker.run_compaction('thread_fixture')
            compact.assert_not_called()
            worker.run_compaction('thread_fixture')
            compact.assert_called_once_with('thread_fixture')

    def test_admitted_work_has_explicit_orm_cluster_timeout_and_async_execution(self):
        """Even process-level SYNC cannot turn an admitted job into inline work."""
        self.success()
        with mock.patch.object(
            worker, 'async_task', return_value='fixture-task'
        ) as submit:
            with mock.patch.object(worker.Conf, 'SYNC', True):
                self.assertEqual(
                    worker.enqueue_compaction('thread_fixture')['status'], 'queued'
                )
        submit.assert_called_once_with(
            worker.COMPACTION_TASK,
            'thread_fixture',
            group=worker.CLUSTER,
            cluster=worker.CLUSTER,
            broker=self.broker,
            timeout=300,
            sync=False,
        )

    def test_missing_worker_pressure_disabled_routing_and_broker_errors_defer(self):
        """No rejected job leaks into another queue or the synchronous path."""
        with mock.patch.object(worker, 'async_task') as submit:
            self.assertEqual(
                worker.enqueue_compaction('thread_fixture')['reason'],
                'worker_unavailable',
            )
            self.success()
            with mock.patch.object(
                worker,
                'queue_pressure',
                return_value={
                    'depth': 51,
                    'oldest_due_seconds': 0,
                    'backpressured': True,
                },
            ):
                self.assertEqual(
                    worker.enqueue_compaction('thread_fixture')['reason'],
                    'backpressure',
                )
            self.flags.aimms_memory_worker_enabled = False
            self.assertEqual(
                worker.enqueue_compaction('thread_fixture')['reason'],
                'routing_disabled',
            )
            self.flags.aimms_memory_worker_enabled = True
            with mock.patch.object(
                worker, 'get_broker', side_effect=RuntimeError('PRIVATE')
            ):
                result = worker.enqueue_compaction('thread_fixture')
            self.assertEqual(result['reason'], 'queue_unavailable')
            self.assertNotIn('PRIVATE', json.dumps(result))
            submit.assert_not_called()

    def test_heartbeat_preview_and_idempotent_explicit_install(self):
        """No startup/import schedule mutation; installation requires execution."""
        output = io.StringIO()
        call_command('configure_memory_worker', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['status'], 'dry_run')
        self.assertFalse(Schedule.objects.filter(name=worker.HEARTBEAT_NAME).exists())
        worker.configure_heartbeat(execute=True)
        worker.configure_heartbeat(execute=True)
        row = Schedule.objects.get(name=worker.HEARTBEAT_NAME)
        self.assertEqual(row.cluster, worker.CLUSTER)
        self.assertEqual(row.func, worker.HEARTBEAT_TASK)
        self.assertEqual(row.minutes, 1)
        self.assertNotIn('thread_fixture', row.kwargs)
        row.func = 'foreign.function'
        row.save()
        with self.assertRaises(ImproperlyConfigured):
            worker.configure_heartbeat(execute=True)

    def test_broker_database_failure_preserves_callers_transaction(self):
        """A failed publication must not roll back or poison the chat turn."""
        self.success()

        def broken_publication(*args, **kwargs):
            OrmQ.objects.create(key=None, lock=timezone.now(), payload='fixture')

        with transaction.atomic():
            marker = OrmQ.objects.create(
                key='unrelated', lock=timezone.now(), payload='fixture'
            )
            with mock.patch.object(
                worker, 'async_task', side_effect=broken_publication
            ):
                result = worker.enqueue_compaction('thread_fixture')
            self.assertEqual(result['reason'], 'queue_unavailable')
            self.assertTrue(OrmQ.objects.filter(pk=marker.pk).exists())

    def test_repository_switch_preserves_default_and_never_falls_back(self):
        """A deferred dedicated job must not also enter the existing queue."""
        flags = SimpleNamespace(
            feature_thread_compaction_shadow=False,
            feature_thread_compaction=True,
            aimms_memory_worker_enabled=False,
        )
        thread = SimpleNamespace(
            pk='thread_fixture', next_sequence=17, summary_through_sequence=0
        )
        repository = ThreadRepository.__new__(ThreadRepository)
        with (
            mock.patch('ai.core.config.get_settings', return_value=flags),
            mock.patch('InvenTree.tasks.offload_task') as default_submit,
            mock.patch.object(
                worker,
                'enqueue_compaction',
                return_value={'status': 'deferred', 'reason': 'worker_unavailable'},
            ) as dedicated_submit,
        ):
            repository._maybe_schedule_compaction(thread)
            default_submit.assert_called_once()
            dedicated_submit.assert_not_called()
            default_submit.reset_mock()
            flags.aimms_memory_worker_enabled = True
            repository._maybe_schedule_compaction(thread)
            dedicated_submit.assert_called_once_with('thread_fixture')
            default_submit.assert_not_called()
            dedicated_submit.reset_mock()
            thread.next_sequence = 16
            repository._maybe_schedule_compaction(thread)
            dedicated_submit.assert_not_called()

    def test_readiness_command_does_not_use_unrelated_global_worker_status(self):
        """Readiness can qualify the consumer before the routing flag is enabled."""
        self.flags.aimms_memory_worker_enabled = False
        with self.assertRaises(CommandError):
            call_command(
                'memory_worker_status', fail_on_unready=True, stdout=io.StringIO()
            )
        self.success()
        output = io.StringIO()
        call_command('memory_worker_status', fail_on_unready=True, stdout=output)
        self.assertFalse(json.loads(output.getvalue())['routing_enabled'])
