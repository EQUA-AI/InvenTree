"""Geometry and pointer contracts remain aligned as the plant layout is edited."""

import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from machine_health.mimic_layout import expand_pointer, load_layout, validate_assets


class MimicLayoutTests(SimpleTestCase):
    """Check real assets, drift detection and sparse source keys."""

    def test_checked_in_assets_match_the_provisional_contract(self):
        """Every annotated SVG element has the same exact pointer in JSON."""
        assets = Path(__file__).resolve().parents[5] / 'src/frontend/src/assets/mimic'
        layout = load_layout()
        self.assertEqual(layout['review_status'], 'provisional')
        validate_assets(layout, assets)
        with TemporaryDirectory() as directory:
            for filename in ('pump-unit.svg', 'pumphouse-overview.svg'):
                shutil.copy(assets / filename, directory)
            svg = Path(directory) / 'pump-unit.svg'
            svg.write_text(
                svg.read_text(encoding='utf-8').replace('/st"', '/wrong"'),
                encoding='utf-8',
            )
            with self.assertRaisesMessage(ValidationError, 'pump-unit.svg'):
                validate_assets(layout, directory)

    def test_pointer_substitution_preserves_source_identity(self):
        """Keys are identifiers, not array positions; escape slash and tilde."""
        self.assertEqual(expand_pointer('/pd/{pump}/st', 'P17'), '/pd/P17/st')
        self.assertEqual(expand_pointer('/pd/{pump}/st', 'P/1~'), '/pd/P~11~0/st')
        with self.assertRaises(ValidationError):
            expand_pointer('/pd/{wrong}/st', 'P17')
