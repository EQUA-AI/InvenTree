"""Gunicorn configuration for InvenTree."""

import logging
import multiprocessing
import os

# Logger configuration
logger = logging.getLogger('inventree')
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('INVENTREE_LOG_LEVEL', 'warning').lower()
capture_output = True

# Worker configuration
#  TODO: Implement support for gevent
# worker_class = 'gevent'  # Allow multi-threading support
worker_tmp_dir = '/dev/shm'  # Write temp file to RAM (faster)
threads = 4


# Worker timeout (default = 90 seconds)
timeout = int(os.environ.get('INVENTREE_GUNICORN_TIMEOUT', '90'))

# Number of worker processes
workers = os.environ.get('INVENTREE_GUNICORN_WORKERS', None)

if workers is not None:
    try:
        workers = int(workers)
    except ValueError:
        workers = None

if workers is None:
    workers = multiprocessing.cpu_count() * 2 + 1

logger.info('Starting gunicorn server with %s workers', workers)

# A lone ASGI worker stops accepting health probes while request-count
# recycling waits for long-lived chat requests to finish. Keep it available;
# health-based restarts and deployments still replace unhealthy workers.
# Multi-worker installations retain the existing recycling defaults. Operators
# may override these values explicitly; monitor RSS for long-lived workers.
max_requests = int(
    os.environ.get('INVENTREE_GUNICORN_MAX_REQUESTS', '0' if workers == 1 else '1000')
)
max_requests_jitter = int(
    os.environ.get('INVENTREE_GUNICORN_MAX_REQUESTS_JITTER', '50')
)
if max_requests < 0 or max_requests_jitter < 0:
    raise ValueError('Gunicorn request recycling limits must be nonnegative')
if max_requests == 0:
    max_requests_jitter = 0

# preload app so that the ready functions are only executed once
preload_app = True


def post_fork(server, worker):
    """Post-fork hook called after each worker process is forked."""
    from django.db import connections

    # Close any DB connections inherited from the master process — PostgreSQL
    # connections are not fork-safe, so each worker must open its own.
    connections.close_all()

    from django.conf import settings

    if not settings.TRACING_ENABLED:
        return

    # Instrument gunicorm
    from InvenTree.tracing import setup_instruments, setup_tracing

    # Run tracing/logging instrumentation
    setup_tracing(**settings.TRACING_DETAILS)
    setup_instruments(settings.DB_ENGINE)
