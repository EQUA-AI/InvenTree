"""A complete field register is distinct from semantic/source-owner acceptance."""

import copy
import json
import os
from pathlib import Path

from django.test import TestCase

from integration_state.contract_audit import (
    build_inventory,
    check_baseline,
    encode_inventory,
)


class NativeContractAuditTests(TestCase):
    """Actual native/contract metadata stays complete, stable and non-authorizing."""

    def test_actual_inventory_queries_no_rows_and_retains_unreviewed_domains(self):
        """Source fields remain visible as policy gaps rather than erased rows."""
        with self.assertNumQueries(0):
            result = build_inventory()
        self.assertEqual(len(result['domains']), 13)
        self.assertFalse(result['confers_authority'])
        self.assertFalse(result['complete_semantic_acceptance'])
        for row in result['domains']:
            self.assertTrue(row['fields'])
        self.assertTrue(
            any(f['reverse'] for row in result['domains'] for f in row['fields'])
        )
        for row in result['domains']:
            self.assertFalse(row['source_owner_reviewed'])
        work = next(
            row for row in result['domains'] if row['model'] == 'tasks.workorder'
        )
        self.assertEqual(
            next(f for f in work['fields'] if f['name'] == 'machine')['state'],
            'independent_projection',
        )
        self.assertTrue(
            next(
                row
                for row in result['domains']
                if row['model'] == 'repair.repairpacket'
            )['policy_gaps']
        )
        output = Path(os.environ['TMPDIR']) / 'native-contract-inventory.json'
        output.write_text(encode_inventory(result))
        self.assertEqual(json.loads(output.read_text()), result)

    def test_missing_changed_duplicate_fields_and_unknown_schema_fail_drift_check(self):
        """A new source field or reclassified edge requires explicit reconciliation."""
        result = build_inventory()
        self.assertTrue(check_baseline(result, copy.deepcopy(result)))
        for mutation in ['missing', 'changed', 'duplicate', 'schema']:
            baseline = copy.deepcopy(result)
            if mutation == 'missing':
                baseline['domains'][0]['fields'].pop()
            elif mutation == 'changed':
                baseline['domains'][0]['fields'][0]['state'] = 'corrupted_disposition'
            elif mutation == 'duplicate':
                baseline['domains'].append(baseline['domains'][0])
            else:
                baseline['schema'] = 'invented'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                check_baseline(result, baseline)
