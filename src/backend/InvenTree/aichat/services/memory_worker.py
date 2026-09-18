"""Opt-in ORM memory queue, aggregate pressure checks and cluster heartbeat.

This is a routing foundation, not the future extraction claim/sweep service.
No function here falls back to synchronous model work or the ingestion queue.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.db.models import Count, Min
from django.utils import timezone

from django_q.brokers import get_broker
from django_q.brokers.orm import ORM
from django_q.conf import Conf
from django_q.models import OrmQ, Schedule, Success
from django_q.tasks import async_task

from ai.core.config import get_settings
from InvenTree.restore_hold import restore_hold_enabled

CLUSTER = 'ai-memory'
COMPACTION_TASK = 'aichat.services.memory_worker.run_compaction'
HEARTBEAT_TASK = 'aichat.services.memory_worker.heartbeat'
HEARTBEAT_NAME = 'ai-memory-heartbeat-v1'
MAX_DEPTH = 50
MAX_DUE_AGE_SECONDS = 600
HEARTBEAT_MAX_AGE_SECONDS = 600
logger = logging.getLogger('inventree')


def cluster_config():
    """Require the installed consumer contract before routing work to it."""
    config = settings.Q_CLUSTER.get('ALT_CLUSTERS', {}).get(CLUSTER, {})
    timeout = config.get('timeout')
    if (
        config.get('orm') != 'default'
        or config.get('workers') != 1
        or config.get('queue_limit') != 20
        or config.get('sync') is not False
        or config.get('scheduler') is not True
        or type(timeout) is not int
        or not 30 <= timeout <= 900
        or config.get('retry') != timeout + 120
        or any(
            config.get(key)
            for key in (
                'broker_class',
                'iron_mq',
                'sqs',
                'mongo',
                'django_redis',
                'redis',
            )
        )
    ):
        raise ImproperlyConfigured('Memory worker configuration is unavailable')
    return config


def _broker():
    broker = get_broker(CLUSTER)
    if (
        not isinstance(broker, ORM)
        or broker.list_key != CLUSTER
        or Conf.ORM != 'default'
    ):
        raise ImproperlyConfigured('Memory work requires the default ORM broker')
    return broker


def queue_pressure(*, now=None):
    """Count metadata only; never decode task payloads or mistake lock for creation.

    ORM.lock starts at enqueue time but becomes a future lease on dequeue. The
    age below is therefore oldest due-lock age, not original task/lease age.
    Admission is advisory under concurrent producers, not a hard capacity cap.
    """
    now = now or timezone.now()
    rows = OrmQ.objects.using('default').filter(key=CLUSTER)
    aggregate = rows.aggregate(depth=Count('pk'), oldest=Min('lock'))
    oldest = aggregate['oldest']
    age = max(0, (now - oldest).total_seconds()) if oldest else 0
    return {
        'depth': aggregate['depth'],
        'oldest_due_seconds': age,
        'backpressured': aggregate['depth'] > MAX_DEPTH or age > MAX_DUE_AGE_SECONDS,
    }


def heartbeat():
    """Only an executing memory-cluster task may report memory-worker liveness."""
    cluster_config()
    _broker()
    if Conf.CLUSTER_NAME != CLUSTER or restore_hold_enabled():
        raise ImproperlyConfigured('Memory heartbeat requires an unheld memory worker')
    return {'cluster': CLUSTER, 'status': 'alive'}


def worker_status():
    """Report aggregate queue metadata and a recent, cluster-bound task completion."""
    now = timezone.now()
    cluster_config()
    _broker()
    recent = (
        Success.objects
        .using('default')
        .filter(
            func=HEARTBEAT_TASK,
            group=CLUSTER,
            cluster=CLUSTER,
            stopped__gte=now - timedelta(seconds=HEARTBEAT_MAX_AGE_SECONDS),
            stopped__lte=now,
        )
        .order_by('-stopped')
        .first()
    )
    alive = recent is not None and recent.result == {
        'cluster': CLUSTER,
        'status': 'alive',
    }
    return {
        'cluster': CLUSTER,
        'routing_enabled': get_settings().aimms_memory_worker_enabled,
        'serving_hold': restore_hold_enabled(),
        'heartbeat_fresh': alive,
        'heartbeat_age_seconds': (now - recent.stopped).total_seconds()
        if alive
        else None,
        **queue_pressure(now=now),
    }


def enqueue_compaction(thread_id):
    """Defer on pressure, missing heartbeat or configuration failure; never reroute."""
    if not get_settings().aimms_memory_worker_enabled:
        return {'status': 'deferred', 'reason': 'routing_disabled'}
    if restore_hold_enabled():
        return {'status': 'deferred', 'reason': 'restore_hold'}
    try:
        # Isolate broker/database failures from the caller's turn transaction.
        with transaction.atomic(using='default'):
            config = cluster_config()
            status = worker_status()
            reason = (
                'backpressure'
                if status['backpressured']
                else 'worker_unavailable'
                if not status['heartbeat_fresh']
                else None
            )
            if reason:
                logger.info(
                    'Memory work deferred reason=%s depth=%d oldest_due_seconds=%d',
                    reason,
                    status['depth'],
                    status['oldest_due_seconds'],
                )
                return {'status': 'deferred', 'reason': reason}
            # Explicit sync=False overrides django-q's process-level debug SYNC.
            # Direct async_task also avoids the generic offload batching path, which
            # could postpone publication beyond this admission check.
            task_id = async_task(
                COMPACTION_TASK,
                thread_id,
                group=CLUSTER,
                cluster=CLUSTER,
                broker=_broker(),
                timeout=config['timeout'],
                sync=False,
            )
            if not task_id:
                raise RuntimeError('Queue did not acknowledge publication')
            return {'status': 'queued'}
    except Exception:
        logger.warning('Memory work deferred reason=queue_unavailable')
        return {'status': 'deferred', 'reason': 'queue_unavailable'}


def run_compaction(thread_id):
    """Recheck placement and routing before calling existing compaction logic."""
    heartbeat()  # Enforces the actual consumer name, ORM contract and restore hold.
    if not get_settings().aimms_memory_worker_enabled:
        return {'status': 'deferred', 'reason': 'routing_disabled'}
    from aichat.tasks import compact_thread_summary

    compact_thread_summary(thread_id)
    return {'status': 'processed'}


def configure_heartbeat(*, execute=False):
    """Preview or install this service's explicit-cluster schedule, never at import."""
    cluster_config()
    rows = Schedule.objects.using('default').filter(name=HEARTBEAT_NAME)
    if rows.count() > 1 or rows.exclude(func=HEARTBEAT_TASK).exists():
        raise ImproperlyConfigured('Memory heartbeat schedule identity conflict')
    values = {
        'func': HEARTBEAT_TASK,
        'cluster': CLUSTER,
        'schedule_type': Schedule.MINUTES,
        'minutes': 1,
        'repeats': -1,
        'args': '',
        'hook': None,
        'kwargs': repr({
            'q_options': {
                'group': CLUSTER,
                'timeout': 30,
                'sync': False,
                'cached': False,
                'save': True,
            }
        }),
        'intended_date_kwarg': None,
    }
    if execute:
        Schedule.objects.using('default').update_or_create(
            name=HEARTBEAT_NAME, defaults=values
        )
    return {
        'status': 'configured' if execute else 'dry_run',
        'cluster': CLUSTER,
        'schedule': HEARTBEAT_NAME,
    }


def configure_recovery_sweep(*, execute=False):
    """Preview/install bounded extraction recovery on the same isolated cluster."""
    cluster_config()
    name = 'ai-memory-extraction-recovery-v1'
    function = 'aichat.services.memory_extraction.sweep'
    rows = Schedule.objects.using('default').filter(name=name)
    if rows.count() > 1 or rows.exclude(func=function).exists():
        raise ImproperlyConfigured('Memory recovery schedule identity conflict')
    values = {
        'func': function,
        'cluster': CLUSTER,
        'schedule_type': Schedule.MINUTES,
        'minutes': 1,
        'repeats': -1,
        'args': '',
        'hook': None,
        'kwargs': repr({
            'q_options': {
                'group': CLUSTER,
                'timeout': 60,
                'sync': False,
                'cached': False,
                'save': True,
            }
        }),
        'intended_date_kwarg': None,
    }
    if execute:
        Schedule.objects.using('default').update_or_create(name=name, defaults=values)
    return {
        'status': 'configured' if execute else 'dry_run',
        'cluster': CLUSTER,
        'schedule': name,
    }
