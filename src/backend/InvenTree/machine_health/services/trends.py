"""Bounded historical reads for one mapped signal.

AIMMS does not store a time series - the historian does. A trend is therefore a
*federated* read: the connector is asked for a window, and the answer is bounded
before it is returned.

Two rules make this safe to expose:

* **The client names a binding, never a tag.** The external key comes from the
  mapping row, so a caller cannot reach an arbitrary point in the source system
  by supplying its name. This is the tag-injection boundary.
* **A source that cannot serve history says so.** No connector, or a connector
  without ``read_window``, yields ``available: false`` rather than a trend
  synthesized from the current value - a fabricated line is worse than no line.

Windows, sample counts and per-request time are all capped, so one request
cannot pull a historian dry or hang the worker pool.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from assets.health_models import MachineSignalBinding
from machine_health.connectors.base import (
    MAX_TREND_SAMPLES,
    MAX_TREND_WINDOW_SECONDS,
    bounded_window,
    get_connector,
)

logger = logging.getLogger('inventree')

#: Default look-back when the caller does not name one.
DEFAULT_WINDOW_SECONDS = 24 * 3600


class TrendError(Exception):
    """The requested trend window is invalid."""

    code = 'TREND_INVALID'


def read_trend(
    machine,
    *,
    binding_id: int,
    start=None,
    end=None,
    max_samples: int | None = None,
    now=None,
) -> dict:
    """Return a bounded trend for one of the machine's mapped signals.

    The binding is resolved against ``machine`` first, so a binding id belonging
    to another asset is simply not found rather than read.
    """
    now = now or timezone.now()

    binding = (
        MachineSignalBinding.objects
        .select_related('source', 'machine')
        .filter(pk=binding_id, machine=machine, active=True)
        .first()
    )
    if binding is None:
        raise TrendError('No active signal binding matches that id for this machine.')

    end = end or now
    start = start or (end - timezone.timedelta(seconds=DEFAULT_WINDOW_SECONDS))

    try:
        start, end, samples = bounded_window(start, end, max_samples=max_samples)
    except ValueError as exc:
        raise TrendError(str(exc)) from exc

    base = {
        'binding_id': binding.pk,
        'display_name': binding.display_name,
        'unit': binding.unit,
        'signal_kind': binding.signal_kind,
        'source_id': binding.source_id,
        'source_name': binding.source.name,
        'source_type': binding.source.source_type,
        'window_start': start.isoformat(),
        'window_end': end.isoformat(),
        'max_samples': samples,
        'limits': {
            'max_window_seconds': MAX_TREND_WINDOW_SECONDS,
            'max_samples': MAX_TREND_SAMPLES,
        },
    }

    connector = get_connector(binding.source)
    if connector is None:
        return {
            **base,
            'available': False,
            'reason': 'NO_CONNECTOR',
            'detail': 'This source has no configured connector to read history from.',
            'samples': [],
        }

    try:
        # The connector receives the *mapped* external key, never a client string.
        readings = connector.read_window(
            binding.external_key, start, end, max_samples=samples
        )
    except NotImplementedError:
        return {
            **base,
            'available': False,
            'reason': 'HISTORY_UNSUPPORTED',
            'detail': 'This source cannot serve historical windows.',
            'samples': [],
        }
    except Exception:
        # A connector failure is an outage, not a data point. Nothing is
        # synthesized to fill the gap.
        logger.warning(
            'machine_health.trend_failed source=%s binding=%s',
            binding.source_id,
            binding.pk,
            exc_info=True,
        )
        return {
            **base,
            'available': False,
            'reason': 'SOURCE_UNAVAILABLE',
            'detail': 'The source could not be reached for this window.',
            'samples': [],
        }

    # Trim server-side even if the connector ignored the cap: the bound is ours
    # to enforce, not the remote platform's to respect.
    trimmed = list(readings)[:samples]

    return {
        **base,
        'available': True,
        'truncated': len(readings) > len(trimmed),
        'samples': [
            {
                'observed_at': reading.observed_at.isoformat(),
                'value': reading.value,
                'quality': reading.quality,
            }
            for reading in trimmed
        ],
    }
