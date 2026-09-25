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
from machine_health.mimic_layout import load_layout
from machine_health.services.display_time import display_shift, to_display, to_source

logger = logging.getLogger('inventree')

#: Default look-back when the caller does not name one.
#:
#: Deliberately short. At PH_3's five-second cadence an hour is ~720 samples,
#: and every one of those is a whole-station snapshot the connector must fetch
#: and parse to extract a single tag. This default is what an unparameterised
#: caller gets - notably every sparkline on the machine page - so it is sized
#: for "enough to show a direction", not for the maximum the server permits.
DEFAULT_WINDOW_SECONDS = 3600


#: A status code plotted on a numeric axis. Running above idle so a line rises
#: when a machine starts, and fault below both so it cannot be mistaken for
#: either. Read from the mimic layout rather than written out here, because that
#: file is where the plant's status vocabulary is defined and a second copy
#: would drift from it silently.
_STATUS_PLOT_VALUES = {'running': 1.0, 'idle': 0.0, 'fault': -1.0}


def status_numbers() -> dict:
    """Map each status code the layout knows to a number a chart can draw."""
    try:
        vocabulary = load_layout().get('status_values') or {}
    except Exception:  # a malformed layout must not break a trend read
        return {}
    return {
        str(code): _STATUS_PLOT_VALUES[kind]
        for kind, codes in vocabulary.items()
        if kind in _STATUS_PLOT_VALUES
        for code in (codes or [])
    }


def plottable(value, statuses: dict):
    """Return a number for a sample, or None when it is not plottable.

    A status arrives as a code - "R", "I" - which is not a number but is not
    unplottable either: a run/stop trace is one of the more useful lines on
    this page. Anything else non-numeric is left alone rather than coerced,
    because inventing a number for a value nobody defined is worse than a gap.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return statuses.get(str(value).strip())


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

    # The caller asked in the clock the dashboard shows; the source answers in
    # the plant's. Both ends move together, so the window keeps its length.
    station = machine if machine.asset_type == 'pumphouse' else machine.parent
    shift = display_shift(station, binding.source, now=now)
    read_start, read_end = to_source(start, shift), to_source(end, shift)
    statuses = status_numbers()

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
        # Said plainly rather than left for the reader to infer: these times are
        # not the plant's. Subtract the shift to recover them.
        'display_shifted': bool(shift),
        'display_shift_seconds': int(shift.total_seconds()),
        'limits': {
            'max_window_seconds': MAX_TREND_WINDOW_SECONDS,
            'max_samples': MAX_TREND_SAMPLES,
        },
    }

    connector = get_connector(binding.source, machine=machine)
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
        #
        # Ask for one more sample than will be returned. A read that stops
        # exactly on the cap is otherwise indistinguishable from a window that
        # happened to hold exactly that many samples, so truncation could never
        # be reported and the chart would drop data while claiming to be
        # complete. The extra sample is trimmed below and never reaches the API.
        readings = connector.read_window(
            binding.external_key, read_start, read_end, max_samples=samples + 1
        )
    except NotImplementedError:
        return {
            **base,
            'available': False,
            'reason': 'HISTORY_UNSUPPORTED',
            'detail': 'This source cannot serve historical windows.',
            'samples': [],
        }
    except Exception as exc:
        # A connector failure is an outage, not a data point. Nothing is
        # synthesized to fill the gap.
        #
        # A timeout is reported separately from unreachability. Both leave
        # `available` false and no samples, but they send an operator to
        # different places: "could not be reached" points at the network, the
        # endpoint or credentials, whereas a timeout usually means the window
        # asked for more than the source could return in time. Measured against
        # the live account, an hour of ~95 KB documents at 400 RU/s times out
        # while fifteen minutes of the same data succeeds - so the remedy is a
        # narrower window or more throughput, not a connectivity hunt.
        #
        # Matched on class name so this service stays connector-agnostic: it
        # must not import the Cosmos SDK to classify a Cosmos error.
        timed_out = 'timeout' in type(exc).__name__.lower()

        logger.warning(
            'machine_health.trend_failed source=%s binding=%s error=%s timeout=%s',
            binding.source_id,
            binding.pk,
            type(exc).__name__,
            timed_out,
        )
        if timed_out:
            return {
                **base,
                'available': False,
                'reason': 'SOURCE_TIMEOUT',
                'detail': (
                    'The source did not return this window in time. It was '
                    'reachable - the read was too large or too slow. Try a '
                    'shorter window.'
                ),
                'samples': [],
            }
        return {
            **base,
            'available': False,
            'reason': 'SOURCE_UNAVAILABLE',
            'detail': 'The source could not be reached for this window.',
            'samples': [],
        }

    finally:
        try:
            connector.close()
        except Exception:
            logger.warning(
                'machine_health.trend_close_failed source=%s', binding.source_id
            )

    # Trim server-side even if the connector ignored the cap: the bound is ours
    # to enforce, not the remote platform's to respect.
    trimmed = list(readings)[:samples]

    return {
        **base,
        'available': True,
        'truncated': len(readings) > len(trimmed),
        'samples': [
            {
                'observed_at': to_display(reading.observed_at, shift).isoformat(),
                'value': reading.value,
                'plot_value': plottable(reading.value, statuses),
                'quality': reading.quality,
            }
            for reading in trimmed
        ],
    }


def read_trends(
    machine,
    *,
    binding_ids,
    start=None,
    end=None,
    max_samples: int | None = None,
    now=None,
) -> list[dict]:
    """Return bounded trends for several of the machine's signals at once.

    Same contract as :func:`read_trend` for each binding, and the same
    protections: bindings are resolved against ``machine`` first, so an id
    belonging to another asset is simply not returned, and the connector is
    handed mapped external keys rather than client strings.

    This exists because the per-binding call is the wrong shape for the page
    that actually uses it. A pump's signal table draws one sparkline per bound
    parameter - thirty to seventy of them - and each was a separate federated
    read of the same window, so the source served and parsed the same documents
    once per line. One call reads the window once.

    A failure is reported per binding rather than raised, so one unreadable tag
    does not blank the whole table.
    """
    now = now or timezone.now()

    bindings = list(
        MachineSignalBinding.objects.select_related('source', 'machine').filter(
            pk__in=list(binding_ids), machine=machine, active=True
        )
    )
    if not bindings:
        return []

    end = end or now
    start = start or (end - timezone.timedelta(seconds=DEFAULT_WINDOW_SECONDS))
    try:
        start, end, samples = bounded_window(start, end, max_samples=max_samples)
    except ValueError as exc:
        raise TrendError(str(exc)) from exc

    # One shift for the whole table: every binding here belongs to the same
    # machine, so they share a station and therefore a recorded data range.
    station = machine if machine.asset_type == 'pumphouse' else machine.parent
    shift = display_shift(station, bindings[0].source, now=now)
    read_start, read_end = to_source(start, shift), to_source(end, shift)
    statuses = status_numbers()

    def envelope(binding):
        return {
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
            'display_shifted': bool(shift),
            'display_shift_seconds': int(shift.total_seconds()),
            'limits': {
                'max_window_seconds': MAX_TREND_WINDOW_SECONDS,
                'max_samples': MAX_TREND_SAMPLES,
            },
        }

    def unavailable(binding, reason, detail):
        return {
            **envelope(binding),
            'available': False,
            'reason': reason,
            'detail': detail,
            'samples': [],
        }

    # Bindings may span several sources; each source is read once for its own
    # keys rather than once per binding.
    results: dict[int, dict] = {}
    by_source: dict[int, list] = {}
    for binding in bindings:
        by_source.setdefault(binding.source_id, []).append(binding)

    for group in by_source.values():
        connector = get_connector(group[0].source, machine=machine)
        if connector is None:
            for binding in group:
                results[binding.pk] = unavailable(
                    binding,
                    'NO_CONNECTOR',
                    'This source has no configured connector to read history from.',
                )
            continue
        try:
            readings = connector.read_windows(
                [binding.external_key for binding in group],
                read_start,
                read_end,
                max_samples=samples + 1,
            )
        except NotImplementedError:
            for binding in group:
                results[binding.pk] = unavailable(
                    binding,
                    'HISTORY_UNSUPPORTED',
                    'This source cannot serve historical windows.',
                )
            continue
        except Exception as exc:
            timed_out = 'timeout' in type(exc).__name__.lower()
            logger.warning(
                'machine_health.trends_failed source=%s bindings=%s error=%s timeout=%s',
                group[0].source_id,
                len(group),
                type(exc).__name__,
                timed_out,
            )
            for binding in group:
                results[binding.pk] = unavailable(
                    binding,
                    'SOURCE_TIMEOUT' if timed_out else 'SOURCE_UNAVAILABLE',
                    'The source did not return this window in time. It was '
                    'reachable - the read was too large or too slow. Try a '
                    'shorter window.'
                    if timed_out
                    else 'The source could not be reached for this window.',
                )
            continue
        finally:
            try:
                connector.close()
            except Exception:
                logger.warning(
                    'machine_health.trend_close_failed source=%s', group[0].source_id
                )

        for binding in group:
            found = list(readings.get(binding.external_key) or [])
            trimmed = found[:samples]
            results[binding.pk] = {
                **envelope(binding),
                'available': True,
                'truncated': len(found) > len(trimmed),
                'samples': [
                    {
                        'observed_at': to_display(
                            reading.observed_at, shift
                        ).isoformat(),
                        'value': reading.value,
                        'plot_value': plottable(reading.value, statuses),
                        'quality': reading.quality,
                    }
                    for reading in trimmed
                ],
            }

    return [results[b.pk] for b in bindings if b.pk in results]
