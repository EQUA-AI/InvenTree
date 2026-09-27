"""Set the stator-winding limits, on the channels whose data can carry them.

    python3 contrib/cosmos/devtools/apply_winding_thresholds.py [--dry-run]

Run inside the server container via `manage.py shell <` - it needs the ORM and
the Cosmos connector.

The limits
----------
warn_max 125, critical_max 145, and nothing else. From IS/IEC 60034-1 Table 7
item 1a: 85 K rise by embedded detector for thermal class 130(B) on the 40 degC
reference coolant of cl. 6.1, so 125 degC is the highest reading the machine is
designed to produce at rated load. The trip is IEEE Std 3004.8-2016 cl. 8.5.2.1,
"5 degC to 10 degC below the insulation class maximum temperature rating", so
155 - 10.

ASSUMES thermal class 155(F) with rise limited to class 130(B). Nobody has
confirmed the nameplate, and it is the first question in BLOCKERS/THRESHOLDS.
The assumption is not symmetric and that is why it was acceptable to proceed:
full class F rise would put the true limits at 150/155, so 125/145 warns about
25 K early - conservative. Class B *insulation* would put them near 110/125, and
125/145 would warn late. A 2019 BHEL 40 MW machine with class B insulation would
be unusual, but that is the case to check, and one nameplate photograph settles
it.

No normal_min, no normal_max, no minima of any kind. normal_max would raise a
warning, and nothing in this estate establishes where a healthy loaded machine
sits - every bay is stopped and the one that runs is frozen on sentinels. A
normal_min or critical_min would be a data-quality gate written into a threshold
field, which fires on exactly the readings it exists to suppress; the over-range
marker is handled at coercion instead.

Why some channels are skipped
-----------------------------
Six winding channels at Saraswati emit readings a stopped machine cannot
produce - 191 and 192.8 degC against a 29 degC ambient, and deep negatives. Four
of them do so in every sample taken. Those are an instrumentation question
(BLOCKERS Ask 1), not a limit question, and giving them limits would open
persistent critical anomalies about measurements that never happened. So the
channels are classified from the data first and only the clean ones are set.

A channel is also left alone when too little of it has been read to tell the two
apart - a silent channel and an absent one look identical below a handful of
samples. That is a statement about the sampling, so the sampler has to be honest
about it: see :func:`_probe_station`.
"""

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

from assets.health_models import HealthSource, MachineSignalBinding
from assets.models import AssetMachine, DictionaryPoint
from machine_health.connectors.base import pumphouse_connector_class
from machine_health.connectors.cosmos_pumphouse import bucket_of, to_epoch_ms
from machine_health.connectors.pumphouse_payload import _pegged

WINDING = re.compile(r'MOTOR_WINDING_TEMP')
WARN_MAX = 125.0
CRITICAL_MAX = 145.0

#: A reading outside this cannot be a winding temperature on a machine sitting
#: in a pump hall: the insulation limit is 155 and ambient is about 29. Used to
#: *classify a channel*, never written into a threshold field.
PLAUSIBLE = (0.0, 125.0)

#: A channel needs at least this many usable samples before its silence counts
#: as evidence of health rather than absence of data.
MIN_SAMPLES = 20

#: Buckets to probe on the first pass over a station's span. Enough to catch an
#: intermittent fault anywhere in it without reading all 288 hours.
FIRST_PASS_PROBES = 70


def _sample(connector, uuid, moment, paths, station_id, seen, bad):
    """Fold one bucket's winding readings into the ``seen`` and ``bad`` counts."""
    document = connector.latest_document(uuid, bucket_of(to_epoch_ms(moment)))
    if not document:
        return
    extension = json.loads(document['data1_raw']).get('dex') or {}
    for tag, raw in extension.items():
        path = f'/dex/{tag}'
        if path not in paths:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if _pegged(value):
            continue  # already bad quality; not the channel's fault
        seen[station_id, path] += 1
        if not PLAUSIBLE[0] <= value <= PLAUSIBLE[1]:
            bad[station_id, path] += 1


def _probe_station(connector, uuid, start, hours, paths, station_id, seen, bad):
    """Probe a station's span, spread wide first and densified only if needed.

    The stride is what a station's *span* divides into, but the samples come
    from its *populated hours*, and those are not the same number. Saraswati and
    Parvathi hold data in about 80% of their 288 hours, so one strided pass
    leaves every channel far above :data:`MIN_SAMPLES`. Ranganayaka holds data in
    50 of them, and the same pass landed on a dozen - which read as "not
    observed enough to judge" and left 38 real channels without limits. The
    shortfall was in the sampler, not in the plant.

    So each pass covers the whole span at the current stride, and the stride only
    halves while some channel is still short. A station whose data is dense is
    read exactly as before, with no extra requests; a sparse one is walked as
    densely as it takes, down to every hour. Halving rather than early-exiting
    keeps every pass spread across the full span: stopping the moment a counter
    reached 20 would judge each channel on the first 20 hours alone and miss the
    intermittent faults this classification exists to catch.
    """
    probed = set()
    stride = max(1, hours // FIRST_PASS_PROBES)
    while True:
        for index in range(0, hours, stride):
            if index in probed:
                continue
            probed.add(index)
            _sample(
                connector,
                uuid,
                start + timedelta(hours=index),
                paths,
                station_id,
                seen,
                bad,
            )
        if stride == 1:
            return
        if all(seen[station_id, path] >= MIN_SAMPLES for path in paths):
            return
        stride //= 2


def classify_channels():
    """Return (clean, faulty, unobserved) keys and the per-channel sample count."""
    points = [
        point
        for point in DictionaryPoint.objects.filter(
            status='approved', unit='degC'
        ).select_related('station')
        if WINDING.search(point.path)
    ]
    wanted = {}
    for point in points:
        wanted.setdefault(point.station_id, set()).add(point.path)

    source = HealthSource.objects.filter(connector_type='cosmos_pumphouse').first()
    connector_class = pumphouse_connector_class(source.connector_type)
    ranges = (source.config or {}).get('data_ranges') or {}
    seen, bad = Counter(), Counter()

    for station in AssetMachine.objects.filter(
        source_entity_uuid__isnull=False
    ).order_by('pk'):
        uuid = str(station.source_entity_uuid)
        if uuid not in ranges or station.pk not in wanted:
            continue
        paths = wanted[station.pk]
        start = datetime.fromisoformat(ranges[uuid]['from'])
        end = datetime.fromisoformat(ranges[uuid]['to'])
        connector = connector_class(source, station_uuid=uuid)
        try:
            _probe_station(
                connector,
                uuid,
                start,
                int((end - start).total_seconds() // 3600),
                paths,
                station.pk,
                seen,
                bad,
            )
        finally:
            connector.close()

    keys = {(point.station_id, point.path) for point in points}
    faulty = {key for key in keys if bad[key]}
    clean = {key for key in keys if seen[key] >= MIN_SAMPLES and not bad[key]}
    return clean, faulty, keys - clean - faulty, seen


def apply(*, dry_run):
    """Set the limits on every clean winding binding."""
    clean, faulty, unobserved, seen = classify_channels()
    bindings = MachineSignalBinding.objects.filter(
        active=True, dictionary_point__status='approved'
    ).select_related('dictionary_point')
    targets = [
        binding
        for binding in bindings
        if (binding.dictionary_point.station_id, binding.dictionary_point.path) in clean
    ]
    print(
        f'winding points: {len(clean)} clean, {len(faulty)} faulty, '
        f'{len(unobserved)} not observed enough to judge'
    )
    print(f'bindings to set: {len(targets)}')
    for key in sorted(faulty):
        print(f'   skipped (emits impossible readings): station {key[0]} {key[1]}')
    for key in sorted(unobserved):
        print(
            f'   skipped (only {seen[key]} of {MIN_SAMPLES} samples): '
            f'station {key[0]} {key[1]}'
        )
    if dry_run:
        print('Dry run; nothing written.')
        return
    for binding in targets:
        binding.warn_max = WARN_MAX
        binding.critical_max = CRITICAL_MAX
    MachineSignalBinding.objects.bulk_update(
        targets, ['warn_max', 'critical_max'], batch_size=200
    )
    print(f'Set warn_max={WARN_MAX} critical_max={CRITICAL_MAX} on {len(targets)}.')


apply(dry_run='--dry-run' in sys.argv)
