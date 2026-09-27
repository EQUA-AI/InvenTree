"""The committed review packs must replay onto a deployment that has never seen them.

A pack exported straight out of `export_dictionary_review` carries a
`dictionary_hash`, and that hash covers each point's status, note, unit and
mapping - the state *after* review. A deployment bootstrapping from a fresh
snapshot has an unreviewed dictionary, so the hash cannot match and the apply is
refused. The packs in `contrib/cosmos/review` therefore ship without it, and
this test is what stops a future re-export quietly putting it back: it builds a
station from nothing, imports a dictionary, and replays the pack onto it.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from assets.health_models import HealthSource
from assets.models import Client
from assets.registry import import_dictionary, plan_dictionary, register_station
from assets.registry_models import DictionaryPoint

#: The smallest estate, so the test stays quick while exercising every rule.
PACK = (
    Path(__file__).resolve().parents[5]
    / 'contrib/cosmos/review/maple-grove.review.json'
)


def snapshot_for(paths):
    """A payload whose tags are exactly the ones a pack names.

    The values are placeholders - the dictionary is built from the *shape* of a
    snapshot, not from what it measured - but the paths must match a real one
    exactly or the pack has nothing to attach to.
    """
    payload: dict = {'dex': {}, 'pd': {}}
    for path in paths:
        parts = path.strip('/').split('/')
        if parts[0] == 'dex':
            payload['dex'][parts[1]] = '0'
        elif parts[0] == 'pd':
            payload['pd'].setdefault(parts[1], {})[parts[2]] = (
                'I' if parts[2] == 'st' else '0'
            )
        else:
            payload[parts[0]] = 'I' if parts[0] == 'st' else '0'
    return json.dumps(payload).encode()


class BootstrapReplayTests(TestCase):
    """What a fresh deployment does with a committed pack."""

    def setUp(self):
        """Stand up the least that `import_dictionary` and the pack require."""
        self.review = json.loads(PACK.read_text())
        # Step one of the bootstrap: the pack maps onto catalogue parts by IPN
        # and parameters by name, so without the catalogue every mapping in it
        # resolves to nothing.
        call_command('load_pump_catalogue', stdout=StringIO())
        client = Client.objects.create(name='Estate', code='estate')
        self.source = HealthSource.objects.create(
            name='Cosmos probe',
            source_type='iot',
            connector_type='cosmos_pumphouse',
            client=client,
            active=True,
            config={
                'endpoint': 'https://example.documents.azure.com:443/',
                'database': 'aimms',
                'readings_container': 'pumphouse_readings',
            },
        )
        # The pack names the station by its public UUID as well as its source
        # identity, and the estate manifest pins that UUID for exactly this
        # reason - a station keeps one identity across deployments.
        self.station = register_station(
            client=client,
            name='Maple Grove Lift Station',
            source_namespace=self.review['source_namespace'],
            source_key='PH_7',
            source_entity_uuid=self.review['station_source_uuid'],
            public_uuid=self.review['station_uuid'],
        )

    def paths(self):
        """Every path the pack decides - both halves; the tag file needs all of them."""
        return [
            path
            for section in ('approve', 'withhold')
            for entry in self.review.get(section, [])
            for path in entry['paths']
        ]

    def test_the_pack_carries_no_post_review_hash(self):
        """The hash encodes the reviewed state, so it can never match a fresh one."""
        self.assertNotIn('dictionary_hash', self.review)

    def test_a_pack_replays_onto_a_dictionary_it_has_never_seen(self):
        """The bootstrap this file exists to protect."""
        raw = snapshot_for(self.paths())
        plan = plan_dictionary(self.station, raw)
        import_dictionary(self.station, raw, expected_hash=plan['source_hash'])
        self.assertEqual(
            DictionaryPoint.objects.filter(
                station=self.station, status='approved'
            ).count(),
            0,
            'a freshly imported dictionary must start unreviewed',
        )

        call_command(
            'apply_dictionary_review', review=PACK, stdout=StringIO()
        )

        self.assertEqual(
            DictionaryPoint.objects.filter(
                station=self.station, status='approved'
            ).count(),
            sum(len(e['paths']) for e in self.review['approve']),
        )
        self.assertEqual(
            DictionaryPoint.objects
            .filter(station=self.station)
            .exclude(status='approved')
            .exclude(review_note='')
            .count(),
            sum(len(e['paths']) for e in self.review['withhold']),
            'the withheld half must carry its reasons across too',
        )
