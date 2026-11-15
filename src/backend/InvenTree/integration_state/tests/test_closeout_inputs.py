"""Review-input coverage checks never become historical/source/erasure authority."""

import copy
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from integration_state.closeout_inputs import input_template, validate_inputs
from integration_state.contract_audit import build_inventory

MANIFEST = '1' * 64
INVENTORY = '2' * 64


def inventory():
    """Small declared metadata is a pure validator fixture, not a source oracle."""
    return {
        'schema': 'aimms.native-contract-audit.v1',
        'domains': [
            {
                'model': 'tasks.workorder',
                'fields': [
                    {'name': 'machine', 'state': 'no_reviewed_projection'},
                    {'name': 'private_payload', 'state': 'explicitly_withheld'},
                ],
                'policy_gaps': ['machine'],
            }
        ],
    }


def decision():
    """A proposed source decision is deliberately not a runtime policy change."""
    return {
        'model': 'tasks.workorder',
        'field_or_edge': 'machine',
        'disposition': 'approved_projection',
        'native_authority': 'synthetic:native-scope',
        'independent_dependent_authority': 'synthetic:machine-scope-and-grant',
        'semantics_reference': 'synthetic:source-semantics',
        'decision_reference': 'synthetic:decision-claim-not-reviewed',
        'original_clause_impact': 'Original oracle still requires qualification',
    }


class CloseoutInputTests(SimpleTestCase):
    """Exact types, source binding and complete gap accounting remain strict."""

    def check(self, value, current=None):
        """Validate review metadata only; no database, reference fetch or execution."""
        return validate_inputs(
            value,
            inventory=current or inventory(),
            manifest_sha256=MANIFEST,
            inventory_sha256=INVENTORY,
        )

    def test_partial_template_names_missing_work_without_inventing_facts(self):
        """Unknowns are null or missing rows, never evidence or empty completeness."""
        value = input_template(manifest_sha256=MANIFEST, inventory_sha256=INVENTORY)
        result = self.check(value)
        self.assertEqual(result['field_decisions_required'], 1)
        self.assertEqual(result['field_decisions_described'], 0)
        self.assertEqual(
            result['missing_historical_domains'], ['attachment', 'maintenance', 'work']
        )
        self.assertFalse(result['confers_runtime_authority'])
        self.assertFalse(result['source_facts_verified'])
        self.assertFalse(result['writer_and_copy_inventory_complete'])

    def test_every_described_field_is_still_only_an_unverified_claim(self):
        """Even a complete set of decision rows does not change source policy."""
        value = input_template(manifest_sha256=MANIFEST, inventory_sha256=INVENTORY)
        value['field_decisions'] = [decision()]
        result = self.check(value)
        self.assertEqual(result['missing_field_decisions'], [])
        self.assertEqual(result['claimed_dispositions'], {'approved_projection': 1})
        for name in (
            'evidence_references_verified',
            'changes_field_policy_or_original_scope',
            'erasure_authorized',
            'original_acceptance_complete',
        ):
            self.assertFalse(result[name])
        self.assertEqual(
            inventory()['domains'][0]['fields'][1]['state'], 'explicitly_withheld'
        )

    def test_stale_unknown_duplicate_protected_field_and_forged_coverage_are_rejected(
        self,
    ):
        """A review packet cannot silently drop gaps or redefine a withheld field."""
        base = input_template(manifest_sha256=MANIFEST, inventory_sha256=INVENTORY)
        base['field_decisions'] = [decision()]
        for mutation in (
            'manifest',
            'inventory',
            'schema',
            'extra',
            'duplicate',
            'unknown',
            'protected',
            'disposition',
            'type',
        ):
            value = copy.deepcopy(base)
            if mutation in ('manifest', 'inventory'):
                value[
                    'candidate_manifest_sha256'
                    if mutation == 'manifest'
                    else 'inventory_sha256'
                ] = '3' * 64
            elif mutation == 'schema':
                value['schema'] = 'future-unknown'
            elif mutation == 'extra':
                value['approved'] = True
            elif mutation == 'duplicate':
                value['field_decisions'].append(decision())
            else:
                value['field_decisions'][0][
                    'field_or_edge'
                    if mutation in ('unknown', 'protected')
                    else 'disposition'
                    if mutation == 'disposition'
                    else 'decision_reference'
                ] = (
                    'missing'
                    if mutation == 'unknown'
                    else 'private_payload'
                    if mutation == 'protected'
                    else 'grant_access'
                    if mutation == 'disposition'
                    else True
                )
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.check(value)
        forged = inventory()
        forged['domains'][0]['policy_gaps'] = []
        with self.assertRaises(ValueError):
            self.check(base, forged)

    def test_duplicate_or_unsupported_writer_copy_and_history_claims_are_rejected(self):
        """Each source domain/path/instance has one explicit bounded description."""
        base = input_template(manifest_sha256=MANIFEST, inventory_sha256=INVENTORY)
        writer = {
            'domain': 'work',
            'writer_reference': 'synthetic:writer',
            'operations': ['update'],
            'capture_reference': None,
            'qualification_reference': None,
        }
        copied = {
            'class': 'synthetic-cache',
            'instance_reference': 'synthetic:copy',
            'source_revision_binding': 'synthetic:revision',
            'owner_authority_reference': 'synthetic:authority',
            'holds_retention_reference': 'synthetic:indefinite',
            'readback_reference': None,
        }
        source = {
            'domain': 'work',
            'specification_reference': 'synthetic:spec',
            'incarnation_revision_basis': 'synthetic:incarnation',
            'ownership_effective_time_basis': 'synthetic:history',
            'conflict_restore_relink_policy': 'synthetic:policy',
            'qualification_reference': 'synthetic:qualification',
            'limitations': ['Not verified'],
        }
        base['writer_inventory']['paths'] = [writer]
        base['copy_inventory']['instances'] = [copied]
        base['historical_sources'] = [source]
        result = self.check(base)
        self.assertEqual(result['writer_paths_without_capture_or_qualification'], 1)
        self.assertEqual(result['copy_instances_without_readback'], 1)
        self.assertFalse(result['writer_and_copy_inventory_complete'])
        for section in ('writer', 'copy', 'history', 'operation'):
            value = copy.deepcopy(base)
            if section == 'writer':
                value['writer_inventory']['paths'].append(copy.deepcopy(writer))
            elif section == 'copy':
                value['copy_inventory']['instances'].append(copy.deepcopy(copied))
            elif section == 'history':
                value['historical_sources'].append(copy.deepcopy(source))
            else:
                value['writer_inventory']['paths'][0]['operations'] = [
                    'execute_arbitrary_command'
                ]
            with self.subTest(section=section), self.assertRaises(ValueError):
                self.check(value)


class NativeCloseoutInputCommandTests(TestCase):
    """The actual command binds metadata/files without querying client/source rows."""

    def setUp(self):
        """Use a disposable non-secret manifest and actual native model metadata."""
        super().setUp()
        self.directory = self.enterContext(
            tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        )
        self.root = Path(self.directory)
        self.manifest = self.root / 'manifest.json'
        self.manifest.write_text(
            json.dumps({
                'files': [{'path': 'synthetic.py', 'sha256': '4' * 64, 'size': 1}]
            })
        )
        self.current = self.root / 'inventory.json'
        self.current.write_text(json.dumps(build_inventory()))
        self.submission = self.root / 'inputs.json'
        self.kwargs = {'manifest': str(self.manifest), 'inventory': str(self.current)}

    def test_actual_template_and_partial_check_query_no_rows_and_reject_missing_decisions(
        self,
    ):
        """Successful syntax checking remains an explicit unverified partial result."""
        out = io.StringIO()
        with self.assertNumQueries(0):
            call_command(
                'check_closeout_inputs',
                **self.kwargs,
                write_template=str(self.submission),
                stdout=out,
            )
        result = json.loads(out.getvalue())
        self.assertGreater(result['field_decisions_required'], 0)
        self.assertEqual(result['field_decisions_described'], 0)
        self.assertFalse(result['original_acceptance_complete'])
        with self.assertNumQueries(0), self.assertRaises(CommandError):
            call_command(
                'check_closeout_inputs',
                **self.kwargs,
                input=str(self.submission),
                require_all_field_decisions=True,
                stdout=io.StringIO(),
            )
        with self.assertRaises(CommandError):
            call_command(
                'check_closeout_inputs',
                **self.kwargs,
                write_template=str(self.submission),
                stdout=io.StringIO(),
            )
        self.assertEqual(
            json.loads(self.submission.read_text())['candidate_manifest_sha256'],
            hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
        )

    def test_actual_inventory_drift_and_forged_gap_list_cannot_hide_missing_fields(
        self,
    ):
        """Model/DTO drift and a dishonest derived gap list fail before credit."""
        call_command(
            'check_closeout_inputs',
            **self.kwargs,
            write_template=str(self.submission),
            stdout=io.StringIO(),
        )
        original = json.loads(self.current.read_text())
        for mutation in ('field', 'gaps'):
            value = copy.deepcopy(original)
            if mutation == 'field':
                value['domains'][0]['fields'].pop()
            else:
                target = next(x for x in value['domains'] if x['policy_gaps'])
                target['policy_gaps'] = []
            self.current.write_text(json.dumps(value))
            submitted = json.loads(self.submission.read_text())
            submitted['inventory_sha256'] = hashlib.sha256(
                self.current.read_bytes()
            ).hexdigest()
            self.submission.write_text(json.dumps(submitted))
            with (
                self.subTest(mutation=mutation),
                self.assertNumQueries(0),
                self.assertRaises(CommandError),
            ):
                call_command(
                    'check_closeout_inputs',
                    **self.kwargs,
                    input=str(self.submission),
                    stdout=io.StringIO(),
                )

    def test_duplicate_json_keys_and_invalid_selected_manifest_are_rejected(self):
        """Earlier contradictory input cannot disappear under JSON last-key wins."""
        call_command(
            'check_closeout_inputs',
            **self.kwargs,
            write_template=str(self.submission),
            stdout=io.StringIO(),
        )
        original = self.submission.read_text()
        self.submission.write_text('{"schema":"hidden-contradiction",' + original[1:])
        with self.assertNumQueries(0), self.assertRaises(CommandError):
            call_command(
                'check_closeout_inputs',
                **self.kwargs,
                input=str(self.submission),
                stdout=io.StringIO(),
            )
        self.submission.write_text(original)
        self.manifest.write_text(
            json.dumps({
                'files': [{'path': '../outside.py', 'sha256': '4' * 64, 'size': True}]
            })
        )
        with self.assertNumQueries(0), self.assertRaises(CommandError):
            call_command(
                'check_closeout_inputs',
                **self.kwargs,
                input=str(self.submission),
                stdout=io.StringIO(),
            )
