"""Offline checks for the container verifier.

These run with no Azure access at all: the comparison logic is the part that has
to be right, and it should be provable without a live account.
"""

import json
import unittest
from pathlib import Path

from provision import compare, load_expected, normalise

SCHEMA = Path(__file__).parent / 'schema' / 'pumphouse_readings.container.json'
INDEXING = Path(__file__).parent / 'schema' / 'pumphouse_readings.indexing.json'


def az_show_output(**overrides):
    """The shape `az cosmosdb sql container show --query ...` returns."""
    live = {
        'pk': {
            'paths': ['/station_uuid', '/hour_bucket'],
            'kind': 'MultiHash',
            'version': 2,
        },
        'ttl': -1,
        'indexing': {
            'indexingMode': 'consistent',
            'automatic': True,
            'includedPaths': [
                {'path': '/station_uuid/?'},
                {'path': '/hour_bucket/?'},
                {'path': '/sub_time_period/?'},
                {'path': '/egt/?'},
                {'path': '/month/?'},
            ],
            'excludedPaths': [{'path': '/*'}, {'path': '/"_etag"/?'}],
        },
    }
    live.update(overrides)
    return live


class VerifierTests(unittest.TestCase):
    """Drift detection against the checked-in container definition."""

    def setUp(self):
        """Load the definition the repository treats as authoritative."""
        self.expected = load_expected(SCHEMA)

    def test_matching_container_reports_no_drift(self):
        """A container created from this definition compares clean."""
        self.assertEqual(compare(self.expected, az_show_output()), [])

    def test_sdk_and_az_shapes_normalise_alike(self):
        """The same container read three ways must compare identically."""
        az = az_show_output()
        sdk = {
            'partitionKey': az['pk'],
            'defaultTtl': az['ttl'],
            'indexingPolicy': az['indexing'],
        }
        wrapped = {'resource': sdk}

        self.assertEqual(normalise(az), normalise(sdk))
        self.assertEqual(normalise(az), normalise(wrapped))

    def test_single_level_partition_key_is_caught(self):
        """Forgetting the hierarchical tick box is the expensive mistake."""
        live = az_show_output(
            pk={'paths': ['/station_uuid'], 'kind': 'Hash', 'version': 2}
        )
        drift = compare(self.expected, live)
        self.assertTrue(any('partition key paths' in line for line in drift))
        self.assertTrue(any('recreated' in line for line in drift))

    def test_wrong_partition_key_entirely_is_caught(self):
        """The pre-existing /maintenance_pk container must not pass."""
        live = az_show_output(
            pk={'paths': ['/maintenance_pk'], 'kind': 'Hash', 'version': 2}
        )
        self.assertTrue(compare(self.expected, live))

    def test_ttl_off_is_distinguished_from_ttl_none_default(self):
        """A container with TTL off omits defaultTtl; that is not -1."""
        live = az_show_output()
        del live['ttl']
        drift = compare(self.expected, live)
        self.assertTrue(any('switched off' in line for line in drift))

        live['ttl'] = 86400
        self.assertTrue(compare(self.expected, live))

    def test_missing_indexed_path_is_reported_by_name(self):
        """Drift messages name the path so the fix is obvious."""
        live = az_show_output()
        live['indexing']['includedPaths'] = [{'path': '/station_uuid/?'}]
        drift = compare(self.expected, live)
        self.assertIn('indexed path missing: /hour_bucket/?', drift)
        self.assertIn('indexed path missing: /sub_time_period/?', drift)

    def test_indexing_the_payload_is_reported(self):
        """Default indexing would index every dex tag on every write."""
        live = az_show_output()
        live['indexing']['excludedPaths'] = [{'path': '/"_etag"/?'}]
        drift = compare(self.expected, live)
        self.assertTrue(any('payload is being indexed' in line for line in drift))

    def test_empty_response_does_not_crash(self):
        """A container that could not be read reports drift, not a traceback."""
        self.assertTrue(compare(self.expected, {}))


class DefinitionTests(unittest.TestCase):
    """The checked-in definition itself must stay self-consistent."""

    def test_definition_carries_no_deployment_identity(self):
        """No endpoint, account, database id or credential in the artefact.

        The tokens are credential- and deployment-shaped rather than the bare
        word "key": ``partitionKey`` is exactly what this file is supposed to
        contain.
        """
        text = json.dumps(load_expected(SCHEMA)).lower()
        forbidden = [
            'endpoint',
            'accountkey',
            'primarykey',
            'masterkey',
            'documents.azure.com',
            'resourcegroup',
            'aimms',
            'epconchat',
        ]
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, text)

    def test_hierarchical_key_is_declared_correctly(self):
        """MultiHash and version 2 are what make a key hierarchical."""
        definition = load_expected(SCHEMA)
        self.assertEqual(
            definition['partitionKey']['paths'], ['/station_uuid', '/hour_bucket']
        )
        self.assertEqual(definition['partitionKey']['kind'], 'MultiHash')
        self.assertEqual(definition['partitionKey']['version'], 2)
        self.assertEqual(definition['defaultTtl'], -1)

    def test_standalone_indexing_file_matches_the_container_definition(self):
        """`az ... --idx @file` needs the policy alone; it must not drift.

        Two files describing one policy is a duplication we accept because the
        CLI cannot extract a sub-object, but only while this test makes the
        duplication impossible to get wrong.
        """
        self.assertEqual(
            load_expected(INDEXING), load_expected(SCHEMA)['indexingPolicy']
        )


if __name__ == '__main__':
    unittest.main()
