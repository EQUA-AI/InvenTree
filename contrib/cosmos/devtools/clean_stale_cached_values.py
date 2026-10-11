"""Drop cached values that sit outside the span their station presents.

Left over from loads that predate the migrated window. They cannot be replaced
by re-seeding, because ingestion drops an older observation as a replay - so
the stale row wins forever and is presented as the current value.
"""

from datetime import datetime

from assets.health_models import HealthSource
from assets.health_models import MachineSignalState as S
from assets.models import AssetMachine

src = HealthSource.objects.get(pk=1)
ranges = (src.config or {}).get('data_ranges') or {}
for pk, name in ((17, 'Parvathi'), (60, 'Saraswati'), (78, 'Ranganayaka')):
    st = AssetMachine.objects.get(pk=pk)
    rng = ranges.get(str(st.source_entity_uuid))
    if not rng:
        continue
    ids = [pk, *AssetMachine.objects.filter(parent=st).values_list('pk', flat=True)]
    qs = S.objects.filter(binding__machine_id__in=ids)
    doomed = list(
        qs.filter(observed_at__gt=datetime.fromisoformat(rng['to'])).values_list(
            'pk', flat=True
        )
    ) + list(
        qs.filter(observed_at__lt=datetime.fromisoformat(rng['from'])).values_list(
            'pk', flat=True
        )
    )
    if doomed:
        S.objects.filter(pk__in=doomed).delete()
    print(f'  {name:12} removed {len(doomed)} out-of-range cached values')
