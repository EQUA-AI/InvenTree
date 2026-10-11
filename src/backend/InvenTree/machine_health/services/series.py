"""Multi-signal history for the Performance blade, at a cost that fits a page.

A trend (:mod:`machine_health.services.trends`) reads every snapshot in its
window, which is the right thing for a sparkline and the wrong thing for a
dashboard: at five seconds a sample, a day is 17,280 whole-station documents,
and no page can wait for that. A *series* is the same federated read with one
change of shape - the caller names how many points it wants, and the window is
cut into that many slots, each answered by the first real snapshot in it.

Two rules carry over from trends unchanged, and one is added:

* **The client names a binding or a mapped key, never a raw tag.** Keys are
  resolved against the machine's own bindings before the connector sees them.
* **A source that cannot serve the window says so**, per binding, rather than
  getting a line synthesized from whatever it could serve.
* **Nothing is averaged.** Every point returned is a snapshot the plant wrote,
  carrying its own timestamp. Sampling drops readings; it never invents one.
  A window short enough to read whole is read whole, and the response says
  which of the two happened.

The scope of a series is the machine and its station: a station reads its own
points and its bays', and a pump reads its own and its station's. A pump cannot
read a sibling's - that is a different machine's telemetry.
"""

from __future__ import annotations

import logging
import statistics
from datetime import timedelta
from itertools import pairwise

from django.db.models import Q
from django.utils import timezone

from assets.activation import live_status
from assets.health_models import MachineSignalBinding
from machine_health.connectors.base import (
    COMPLETE_READ_MAX_DOCUMENTS,
    DEFAULT_SERIES_POINTS,
    EXPECTED_SAMPLE_INTERVAL_SECONDS,
    MAX_SERIES_POINTS,
    MAX_SERIES_WINDOW_SECONDS,
    MAX_TREND_SAMPLES,
    MIN_SERIES_POINTS,
    get_connector,
)
from machine_health.services.display_time import display_shift, to_display, to_source
from machine_health.services.trends import plottable, status_numbers

logger = logging.getLogger('inventree')

#: Look-back when the caller does not name one. An hour: long enough for a
#: direction, and at the source's cadence still within a complete read's reach
#: of the sampled path.
DEFAULT_SERIES_WINDOW_SECONDS = 3600

MODE_COMPLETE = 'complete'
MODE_SAMPLED = 'sampled'

#: Bindings one request may name. A pump page reads its whole parameter set at
#: once, which is fifty to seventy; a station page reads a handful per bay.
MAX_SERIES_TARGETS = 200

_LIMIT_FIELDS = (
    'normal_min',
    'normal_max',
    'warn_min',
    'warn_max',
    'critical_min',
    'critical_max',
)


class SeriesError(Exception):
    """The requested series is invalid."""

    code = 'SERIES_INVALID'

    def __init__(self, detail: str, code: str | None = None):
        """Carry a client-facing detail and an error code."""
        super().__init__(detail)
        if code:
            self.code = code


def series_scope(machine):
    """Return the bindings a series for ``machine`` may read.

    Its own, always. A station may also read its bays', because a station page
    compares them; a pump may also read its station's, because the forebay level
    and the running count are the context a pump operates in. Neither reaches a
    sibling machine.
    """
    scope = Q(machine=machine)
    if machine.asset_type == 'pumphouse':
        scope |= Q(machine__parent=machine)
    elif machine.parent_id is not None:
        scope |= Q(machine=machine.parent)
    return MachineSignalBinding.objects.select_related('source', 'machine').filter(
        scope, active=True
    )


def clamp_points(points) -> int:
    """Bound a requested point count to what a sampled read may issue."""
    try:
        value = int(points if points is not None else DEFAULT_SERIES_POINTS)
    except (TypeError, ValueError) as exc:
        raise SeriesError('points must be a whole number.') from exc
    return max(MIN_SERIES_POINTS, min(MAX_SERIES_POINTS, value))


def read_series(
    machine, *, binding_ids=(), keys=(), start=None, end=None, points=None, now=None
) -> dict:
    """Return sampled or complete history for several of a machine's signals.

    ``binding_ids`` and ``keys`` are both resolved inside :func:`series_scope`;
    an id or key outside it is simply absent from the reply, never read.
    """
    now = now or timezone.now()
    end = end or now
    start = start or (end - timedelta(seconds=DEFAULT_SERIES_WINDOW_SECONDS))
    if end <= start:
        raise SeriesError('Series window end must come after its start.')

    span = (end - start).total_seconds()
    if span > MAX_SERIES_WINDOW_SECONDS:
        raise SeriesError(
            f'A series window may not exceed {MAX_SERIES_WINDOW_SECONDS // 3600} hours.',
            'WINDOW_TOO_LONG',
        )
    points = clamp_points(points)

    wanted_ids = {int(value) for value in binding_ids}
    wanted_keys = {str(value) for value in keys}
    if len(wanted_ids) + len(wanted_keys) > MAX_SERIES_TARGETS:
        raise SeriesError(
            f'At most {MAX_SERIES_TARGETS} signals per series.', 'TOO_MANY_TARGETS'
        )

    selection = Q()
    if wanted_ids:
        selection |= Q(pk__in=wanted_ids)
    if wanted_keys:
        selection |= Q(external_key__in=wanted_keys)
    bindings = (
        list(series_scope(machine).filter(selection).order_by('pk'))
        if wanted_ids or wanted_keys
        else []
    )

    expected = int(span // EXPECTED_SAMPLE_INTERVAL_SECONDS)
    mode = MODE_COMPLETE if expected <= COMPLETE_READ_MAX_DOCUMENTS else MODE_SAMPLED

    station = machine if machine.asset_type == 'pumphouse' else machine.parent
    shift = display_shift(station, bindings[0].source if bindings else None, now=now)
    read_start, read_end = to_source(start, shift), to_source(end, shift)
    statuses = status_numbers()

    results: dict[int, dict] = {}
    by_source: dict[int, list] = {}
    for binding in bindings:
        by_source.setdefault(binding.source_id, []).append(binding)

    documents_read = 0
    for group in by_source.values():
        source = group[0].source
        connector = get_connector(source, machine=machine)
        if connector is None:
            for binding in group:
                results[binding.pk] = _unavailable(
                    binding,
                    'NO_CONNECTOR',
                    'This source has no configured connector to read history from.',
                )
            continue

        external_keys = sorted({binding.external_key for binding in group})
        try:
            if mode == MODE_COMPLETE:
                readings = connector.read_windows(
                    external_keys, read_start, read_end, max_samples=MAX_TREND_SAMPLES
                )
                fetched = len({
                    reading.observed_at
                    for found in readings.values()
                    for reading in found
                })
            else:
                sampled = connector.sample_windows(
                    external_keys, read_start, read_end, slots=points
                )
                readings, fetched = sampled.readings, sampled.documents_read
        except NotImplementedError:
            for binding in group:
                results[binding.pk] = _unavailable(
                    binding,
                    'HISTORY_UNSUPPORTED',
                    'This source cannot serve historical windows.',
                )
            continue
        except ValueError as exc:
            # The connector refused the window on its own terms - a source
            # without a native sampler cannot cover a day, and a sped-up replay
            # can map a short wall window onto too much plant time.
            for binding in group:
                results[binding.pk] = _unavailable(binding, 'WINDOW_TOO_LONG', str(exc))
            continue
        except Exception as exc:
            timed_out = 'timeout' in type(exc).__name__.lower()
            logger.warning(
                'machine_health.series_failed source=%s bindings=%s mode=%s error=%s timeout=%s',
                source.pk,
                len(group),
                mode,
                type(exc).__name__,
                timed_out,
            )
            for binding in group:
                results[binding.pk] = _unavailable(
                    binding,
                    'SOURCE_TIMEOUT' if timed_out else 'SOURCE_UNAVAILABLE',
                    'The source did not return this window in time. Try a '
                    'shorter window or fewer points.'
                    if timed_out
                    else 'The source could not be reached for this window.',
                )
            continue
        finally:
            try:
                connector.close()
            except Exception:
                logger.warning(
                    'machine_health.series_close_failed source=%s', source.pk
                )

        documents_read += fetched
        for binding in group:
            found = sorted(
                readings.get(binding.external_key) or [], key=lambda r: r.observed_at
            )
            results[binding.pk] = {
                **_envelope(binding),
                'available': True,
                'count': len(found),
                'samples': [_sample(reading, shift, statuses) for reading in found],
            }

    series = [results[binding.pk] for binding in bindings if binding.pk in results]

    status = live_status(station) if station is not None else None

    return {
        'machine': machine.pk,
        'station': {'pk': station.pk, 'name': station.name} if station else None,
        'window_start': start.isoformat(),
        'window_end': end.isoformat(),
        'window_seconds': int(span),
        'mode': mode,
        'slots': points if mode == MODE_SAMPLED else None,
        'resolution_seconds': _resolution(mode, span, points, series),
        'cadence_seconds': _measured_cadence(series) if mode == MODE_COMPLETE else None,
        'expected_documents': expected,
        'documents_read': documents_read,
        'display_shifted': bool(shift),
        'display_shift_seconds': int(shift.total_seconds()),
        'live': _live(status),
        'limits': {
            'max_window_seconds': MAX_SERIES_WINDOW_SECONDS,
            'complete_read_max_documents': COMPLETE_READ_MAX_DOCUMENTS,
            'min_points': MIN_SERIES_POINTS,
            'max_points': MAX_SERIES_POINTS,
            'expected_sample_interval_seconds': EXPECTED_SAMPLE_INTERVAL_SECONDS,
        },
        'series': series,
    }


def _envelope(binding) -> dict:
    return {
        'binding_id': binding.pk,
        'machine_id': binding.machine_id,
        'external_key': binding.external_key,
        'display_name': binding.display_name,
        'unit': binding.unit,
        'signal_kind': binding.signal_kind,
        'source_id': binding.source_id,
        'source_name': binding.source.name,
        'limits': {name: getattr(binding, name) for name in _LIMIT_FIELDS},
    }


def _unavailable(binding, reason: str, detail: str) -> dict:
    return {
        **_envelope(binding),
        'available': False,
        'reason': reason,
        'detail': detail,
        'count': 0,
        'samples': [],
    }


def _sample(reading, shift, statuses) -> dict:
    """One point: display-clock epoch milliseconds, a plottable number, quality.

    Epoch milliseconds rather than ISO text because the consumer is a chart
    with a numeric time axis, and a day of fifty series is enough points for the
    encoding to matter. ``raw`` is carried only when the value is not itself the
    number drawn - a status code, or something unplottable - so a reader can see
    what the plant said.
    """
    value = reading.value
    plotted = plottable(value, statuses)
    sample = {
        't': int(to_display(reading.observed_at, shift).timestamp() * 1000),
        'v': plotted,
        'q': reading.quality,
    }
    if plotted is None or isinstance(value, str):
        sample['raw'] = value
    return sample


def _resolution(mode, span, points, series) -> float:
    """Seconds between points: measured where the read was complete."""
    if mode == MODE_SAMPLED:
        return round(span / points, 3)
    return _measured_cadence(series) or float(EXPECTED_SAMPLE_INTERVAL_SECONDS)


def _measured_cadence(series) -> float | None:
    """Median gap between consecutive samples, from the fullest series."""
    fullest = max(
        (entry for entry in series if entry.get('available')),
        key=lambda entry: entry['count'],
        default=None,
    )
    if fullest is None or fullest['count'] < 3:
        return None
    stamps = [sample['t'] for sample in fullest['samples']]
    gaps = [later - earlier for earlier, later in pairwise(stamps)]
    return round(statistics.median(gaps) / 1000, 3)


def _live(status) -> dict:
    """What the page needs to say whether "live" is true, and no more."""
    from assets.tasks import POLL_INTERVAL_SECONDS

    if not status:
        return {
            'enabled': False,
            'last_poll_at': None,
            'last_error_code': '',
            'poll_interval_seconds': POLL_INTERVAL_SECONDS,
        }
    last_poll = status.get('last_poll_at')
    return {
        'enabled': bool(status.get('enabled')),
        'last_poll_at': last_poll.isoformat() if last_poll else None,
        'last_error_code': status.get('last_error_code') or '',
        'poll_interval_seconds': POLL_INTERVAL_SECONDS,
    }
