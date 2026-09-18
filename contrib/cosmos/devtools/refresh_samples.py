"""Rewrite committed pilot snapshots onto the current clock.

The committed samples carry their real July-2025 sample times, which is correct
for a fixture but useless for exercising the UI: every reading would arrive
already outside the 300 s freshness window and render as stale. This rebases the
same payloads onto the last few minutes, preserving every invariant the seeder
validates - the half-open hour bucket, ext = egt + 300000, and the dex TIMESTAMP
restatement of egt.

The source file is an argument rather than a constant because *which* payload is
rebased decides what the UI can show. The trimmed pilot excerpt carries ~31 tags,
so it populates only a few dozen of the 581 bindings; every other binding stays
empty and a parameter picker built over them offers choices that never plot. The
full snapshot carries all 845 tags and populates the lot. Both are legitimate -
the excerpt is the smaller, faster fixture - so the caller chooses.

The default names the *tracked, hash-pinned* snapshot under contrib/, not a
working copy under data/: two copies of one artefact drift, and the dictionary
records a provenance hash over the tracked one.

Everything is inside main() behind a __main__ guard. That is not decoration: this
file used to live in data/, which is on the interpreter's path in the dev
container, so a module that parses arguments at import time would abort any
process that merely imported it - including manage.py, which then could not start
the server at all. It now lives under contrib/ for a second reason: data/ is
gitignored, so nothing kept there can be handed over.

Run from the repository root. Local development only. It invents no values, it
only moves the clock.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HOUR_MS = 3_600_000
EXT_OFFSET_MS = 300_000

#: The tracked, hash-pinned full snapshot - all 845 tags.
DEFAULT_SOURCE = Path('contrib/pump-cassandra/PH_3.full-snapshot.json')
DEFAULT_TARGET = Path('data/ph3_snapshots.fresh.json')


def rebase(source: Path, target: Path) -> int:
    """Rewrite ``source`` onto the current clock and write it to ``target``."""
    doc = json.loads(source.read_text(encoding='utf-8'))
    snapshots = doc['snapshots'] if isinstance(doc, dict) else doc

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)

    # Space the snapshots a minute apart, ending ~30 s ago so nothing is
    # future-dated.
    step_ms = 60_000
    base = now_ms - 30_000 - (len(snapshots) - 1) * step_ms

    for index, snap in enumerate(snapshots):
        sample_ms = base + index * step_ms
        bucket_ms = (sample_ms // HOUR_MS) * HOUR_MS

        snap['time_period'] = str(bucket_ms)
        snap['sub_time_period'] = sample_ms

        payload = snap['data1']
        payload['egt'] = sample_ms
        payload['ext'] = sample_ms + EXT_OFFSET_MS
        if isinstance(payload.get('dex'), dict) and 'TIMESTAMP' in payload['dex']:
            payload['dex']['TIMESTAMP'] = repr(sample_ms / 1000)

        assert bucket_ms <= sample_ms < bucket_ms + HOUR_MS, 'bucket invariant broken'

    if isinstance(doc, dict):
        doc['snapshots'] = snapshots
        out = doc
    else:
        out = snapshots

    target.write_text(json.dumps(out, indent=2), encoding='utf-8')
    print(f'wrote {target} with {len(snapshots)} snapshots', file=sys.stderr)
    print(
        'sample times:',
        [
            datetime.fromtimestamp(
                s['sub_time_period'] / 1000, tz=timezone.utc
            ).isoformat()
            for s in snapshots
        ],
        file=sys.stderr,
    )
    return len(snapshots)


def main() -> None:
    """Parse arguments and rebase the requested snapshot file."""
    parser = argparse.ArgumentParser(description='Rebase snapshots onto now.')
    parser.add_argument(
        '--source',
        type=Path,
        default=DEFAULT_SOURCE,
        help=(
            'Snapshot file to rebase (default: the tracked full PH_3 snapshot, '
            'all 845 tags)'
        ),
    )
    parser.add_argument(
        '--target',
        type=Path,
        default=DEFAULT_TARGET,
        help='Where to write the rebased copy',
    )
    args = parser.parse_args()
    rebase(args.source, args.target)


if __name__ == '__main__':
    main()
