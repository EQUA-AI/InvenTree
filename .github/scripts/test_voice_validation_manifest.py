"""The registration guard itself must fail for dropped tests and fake evidence."""

# Test method names describe the controlled mutation under test.
# ruff: noqa: D102

import copy
import json
import unittest

import check_voice_validation_manifest as guard


class VoiceManifestTests(unittest.TestCase):
    """Mutate in-memory fixtures, never the product manifest."""

    def setUp(self):
        self.manifest = json.loads(guard.MANIFEST.read_text())

    def test_current_manifest_and_ci_are_registered(self):
        guard.validate(self.manifest)
        guard.validate_ci(self.manifest)

    def test_missing_test_fails(self):
        self.manifest['scenarios'][0]['tests'][0]['test'] = 'test_not_present'
        with self.assertRaisesRegex(ValueError, 'Missing test definition'):
            guard.validate(self.manifest)

    def test_unregistered_module_fails(self):
        self.manifest['suites'] = self.manifest['suites'][1:]
        with self.assertRaisesRegex(ValueError, 'Unregistered'):
            guard.validate(self.manifest)

    def test_missing_scenario_and_layer_fail(self):
        modified = copy.deepcopy(self.manifest)
        modified['scenarios'].pop()
        with self.assertRaisesRegex(ValueError, 'S01-S22'):
            guard.validate(modified)
        self.manifest['scenarios'][0]['required_layers'] = ['L1']
        with self.assertRaisesRegex(ValueError, 'layers changed'):
            guard.validate(self.manifest)

    def test_human_pass_cannot_be_inferred(self):
        row = self.manifest['scenarios'][17]['external_evidence'][1]
        row['status'] = 'PASS'
        with self.assertRaisesRegex(ValueError, 'real signed evidence'):
            guard.validate(self.manifest)

    def test_scenario_pass_needs_evidence(self):
        self.manifest['scenarios'][0]['status'] = 'PASS'
        with self.assertRaisesRegex(ValueError, 'actual evidence'):
            guard.validate(self.manifest)
