#!/bin/sh
# Export one station's dictionary review pack, plus the reasons already recorded.
#
#   sh contrib/cosmos/devtools/export_review_pack.sh <station_pk>
#
# Writes /tmp/fresh_<pk>.json (the pack) and /tmp/notes_<pk>.json (every reason
# currently on a non-approved point).
#
# The notes dump is the point of this script. A fresh export re-lists every
# withheld point under `pending` with no reason attached, so a pack rebuilt from
# the export alone silently erases them - hundreds of recorded findings, gone,
# with the apply reporting success. Rebuilds read reasons from here instead.
#
# The export itself goes to stdout, where it is preceded by a large unrelated
# JSON diagnostic that every manage.py command in this deployment prints. Rather
# than matching on that, the pack is recovered by scanning for the first JSON
# object that carries a station_source_uuid.
set -e

PK=${1:?give a station pk}
CONTAINER=${CONTAINER:-inventree-inventree-dev-server-1}
MANAGE=/home/inventree/src/backend/InvenTree/manage.py

docker exec "$CONTAINER" python3 "$MANAGE" export_dictionary_review \
    --station "$PK" > "/tmp/exp_$PK.raw" 2>/dev/null

cat > "/tmp/notes_$PK.py" <<PY
import json
from assets.models import DictionaryPoint
json.dump(
    {p.path: p.review_note
     for p in DictionaryPoint.objects.filter(station_id=$PK).exclude(status='approved')},
    open('/tmp/notes_$PK.json', 'w', encoding='utf-8'),
)
PY
docker cp "/tmp/notes_$PK.py" "$CONTAINER:/tmp/" >/dev/null
docker exec "$CONTAINER" python3 "$MANAGE" shell \
    -c "exec(open('/tmp/notes_$PK.py').read())" >/dev/null 2>&1
docker cp "$CONTAINER:/tmp/notes_$PK.json" "/tmp/notes_$PK.json" >/dev/null

python3 - "$PK" <<'PY'
import json, sys

pk = sys.argv[1]
with open(f'/tmp/exp_{pk}.raw', encoding='utf-8') as handle:
    raw = handle.read()

decoder, index, pack = json.JSONDecoder(), 0, None
while True:
    index = raw.find('{', index)
    if index < 0:
        break
    try:
        candidate, end = decoder.raw_decode(raw, index)
    except ValueError:
        index += 1
        continue
    if isinstance(candidate, dict) and 'station_source_uuid' in candidate:
        pack = candidate
        break
    index = end

if pack is None:
    raise SystemExit(f'no review pack in the output for station {pk}')

with open(f'/tmp/fresh_{pk}.json', 'w', encoding='utf-8') as handle:
    json.dump(pack, handle, indent=1)
counts = {section: len(pack[section]) for section in ('approve', 'withhold', 'pending')}
print(f'station {pk}: {counts}')
PY
