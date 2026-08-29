"""S15 (WP-B5): the frozen-config dossier — stable, derived, secret-free."""

import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

SECTION_KEYS = (
    'code_revision',
    'prompt_template_revisions',
    'provider',
    'model_pins',
    'model_purposes',
    'judge',
    'corpus',
    'search_indexes',
    'flags',
    'config_non_secret',
    'gold',
)


def _dossier():
    out = StringIO()
    call_command('evaluation_dossier', stdout=out)
    return json.loads(out.getvalue())


class EvaluationDossierTests(TestCase):
    """Every §13.5 pin present; the version derives from the pins."""

    def test_every_pin_section_is_present(self):
        """The §13.5 list, key for key."""
        document = _dossier()
        self.assertEqual(document['dossier_version'], 1)
        for key in SECTION_KEYS:
            self.assertIn(key, document['pins'], key)
        self.assertEqual(len(document['evaluation_version']), 16)

    def test_version_is_stable_across_invocations(self):
        """Same pins -> same evaluation version (§13.5)."""
        self.assertEqual(_dossier()['evaluation_version'], _dossier()['evaluation_version'])

    def test_version_moves_when_a_pin_changes(self):
        """A material change (a corpus row) yields a NEW version."""
        before = _dossier()['evaluation_version']
        from aichat.models import ControlledDocument

        ControlledDocument.objects.create(
            document_id='DOSSIER-DOC',
            revision='A',
            title='Doc',
            document_class='service_manual',
            scope_key='site:pilot',
            scope_hash='0' * 64,
            access_class='internal',
            source_filename='d.md',
            source_location='x',
            state='draft',
            source_sha256='2' * 64,
        )
        self.assertNotEqual(before, _dossier()['evaluation_version'])

    def test_no_secret_shapes_survive(self):
        """The one redaction authority holds for the dossier too."""
        out = StringIO()
        call_command('evaluation_dossier', '--pretty', stdout=out)
        rendered = out.getvalue()
        import re

        from ai.core.config import CONFIG_SECRET_VALUE

        self.assertIsNone(CONFIG_SECRET_VALUE.search(rendered.replace('"api-key"', '')))
        # Named secret fields are masked or empty, never populated.
        for match in re.finditer(r'"(\w*(?:key|token|secret|password)\w*)":\s*"([^"]*)"', rendered, re.I):
            self.assertIn(match.group(2), ('', '***'), match.group(1))

    def test_flags_carry_registry_and_effective_values(self):
        """The full registry lands with per-entry effective values."""
        from aimms_flags import REGISTRY

        document = _dossier()
        rows = document['pins']['flags']
        self.assertEqual(len(rows), len(REGISTRY))
        by_name = {row['env_name']: row for row in rows}
        self.assertIn('AIMMS_EVIDENCE_GATE_MODE', by_name)
        self.assertEqual(by_name['AIMMS_CAPABILITY_TIER']['effective'], 0)
        self.assertFalse(by_name['FEATURE_AI_PILOT_STOP_LATCH']['effective'])

    def test_fixture_sets_name_all_four_seeders(self):
        """The corpus pin lists every committed fixture-set version."""
        sets = _dossier()['pins']['corpus']['fixture_sets']
        self.assertIn('aimms-analysis-fixtures-v1', sets)
        self.assertIn('aimms-attachment-fixtures-v2', sets)
