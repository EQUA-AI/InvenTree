"""Pure recorded-window projection, not native actor or physical source proof."""

from integration_state.tests.assertions import assert_literal_bool

import json
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from machine_health.services import integration_s6_scan as scan
from machine_health.tests.test_integration_s6_delivery import payload
from machine_health.tests.test_integration_s6_wire import observation


class HistoryScanProjectionTests(TestCase):
    """Use actual fixture/wire production and independent literal source truth."""

    def test_literal_identities_and_original_bytes_have_distinct_private_bindings(self):
        """Do not coerce a padded source ID or publish raw values as scan metadata."""
        records = tuple(
            observation(value={'private-scan-body': 7}, source_observation_id=value)
            for value in ('7', ' 7 ')
        )
        original = payload(records=records)
        result = scan._window_from_payload(original, 1)
        self.assertEqual(result.identities, (' 7 ', '7'))
        self.assertEqual(result.client_pk, 7)
        assert_literal_bool(self, result.complete, True)
        self.assertNotIn('private-scan-body', repr(result))
        changed = payload(records=(observation(value=8, source_observation_id='7'),))
        self.assertNotEqual(scan._window_from_payload(changed, 1).digest, result.digest)

    def test_empty_and_gapped_windows_remain_distinct(self):
        """A genuine empty transport is not a missing/partial source population."""
        empty = scan._window_from_payload(payload(records=()), 1)
        ties = tuple(
            observation(value=i, source_observation_id=f'tie:{i}') for i in range(3)
        )
        incomplete = scan._window_from_payload(payload(records=ties, cap=2), 1)
        self.assertEqual(empty.identities, ())
        assert_literal_bool(self, empty.complete, True)
        assert_literal_bool(self, incomplete.complete, False)

    def test_constructor_cannot_relabel_valid_source_context(self):
        """An independently qualified input cannot certify a forged constructor result."""
        original = payload()
        expected = scan._window_from_payload(original, 1)
        for forged in (
            replace(expected, actor_pk=expected.actor_pk + 1),
            replace(expected, client_pk=expected.client_pk + 1),
            replace(expected, generation='foreign-generation'),
            replace(expected, identities=('foreign-observation',)),
            replace(expected, complete=False),
        ):
            with self.subTest(field=repr(forged)):
                with patch.object(
                    scan, '_WindowCoordinates', return_value=forged
                ) as constructor:
                    with self.assertRaises(scan.HistoryScanError):
                        scan._window_from_payload(original, 1)
                    constructor.assert_called_once()
                self.assertEqual(scan._window_from_payload(original, 1), expected)

    def test_unsupported_ids_and_page_budgets_never_become_empty_success(self):
        """Reject unrepresentable coordinates without repairing literal source IDs."""
        original = payload()
        for page_size in (False, 0, -1, scan.MAX_PAGE_RECORDS + 1):
            with self.subTest(page_size=page_size), self.assertRaises(ValueError):
                scan._window_from_payload(original, page_size)
        for identity in (None, False, 7, '', ' ', 'x' * 257):
            with self.subTest(identity=identity):
                body = json.loads(original)
                body['observations'][0]['sample']['source_observation_id'] = identity
                with self.assertRaises(ValueError):
                    scan._window_from_payload(json.dumps(body).encode(), 1)
        body = json.loads(original)
        body['observations'].append(body['observations'][0])
        with self.assertRaises(scan.HistoryScanError):
            scan._window_from_payload(json.dumps(body).encode(), 1)
        self.assertTrue(scan._window_from_payload(original, 1).identities)
