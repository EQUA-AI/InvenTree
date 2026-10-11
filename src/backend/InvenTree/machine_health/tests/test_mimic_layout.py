"""Geometry and pointer contracts remain aligned as the plant layout is edited."""

import json
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

    def test_the_bay_area_is_optional_but_never_malformed(self):
        """The page draws a station's bays inside it, so it has to be a real box.

        A layout without one is still a layout - the page falls back to a row of
        its own - but a box with no width would put every bay on one point.
        """
        layout = load_layout()
        self.assertGreater(layout['bays']['width'], 0)

        with TemporaryDirectory() as directory:
            path = Path(directory) / 'layout.json'

            def loaded(bays):
                edited = {**layout, 'bays': bays}
                if bays is None:
                    del edited['bays']
                path.write_text(json.dumps(edited), encoding='utf-8')
                return load_layout(path)

            self.assertNotIn('bays', loaded(None))
            for bays in (
                {'x': 240, 'y': 70, 'width': 0, 'height': 134},
                {'x': 240, 'y': 70, 'width': 590},
                {'x': -1, 'y': 70, 'width': 590, 'height': 134},
                {'x': '240', 'y': 70, 'width': 590, 'height': 134},
                {'x': True, 'y': 70, 'width': 590, 'height': 134},
            ):
                with self.subTest(bays=bays):
                    with self.assertRaises(ValidationError):
                        loaded(bays)

    def test_every_part_region_is_one_the_layout_names(self):
        """A pump's parts are drawn where the layout says, under the code it says.

        The page attaches a part's readings to the region carrying its catalogue
        code. A region the layout does not name would be drawn and never filled;
        a part the drawing does not have would be listed and never drawn.
        """
        assets = Path(__file__).resolve().parents[5] / 'src/frontend/src/assets/mimic'
        layout = load_layout()
        codes = [part['code'] for part in layout['parts']]
        self.assertIn('PS-MOTOR', codes)
        self.assertEqual(len(codes), len(set(codes)))

        with TemporaryDirectory() as directory:
            for filename in ('pump-unit.svg', 'pumphouse-overview.svg'):
                shutil.copy(assets / filename, directory)
            svg = Path(directory) / 'pump-unit.svg'
            original = svg.read_text(encoding='utf-8')

            svg.write_text(
                original.replace('data-part="PS-MOTOR"', 'data-part="PS-ELSE"'),
                encoding='utf-8',
            )
            with self.assertRaisesMessage(ValidationError, 'data-part'):
                validate_assets(layout, directory)

            # A station has no parts of its own to draw.
            svg.write_text(original, encoding='utf-8')
            station = Path(directory) / 'pumphouse-overview.svg'
            station.write_text(
                station.read_text(encoding='utf-8').replace(
                    '<g id="forebay"', '<g id="stray" data-part="PS-MOTOR"/><g id="forebay"'
                ),
                encoding='utf-8',
            )
            with self.assertRaisesMessage(ValidationError, 'pumphouse-overview.svg'):
                validate_assets(layout, directory)

            path = Path(directory) / 'layout.json'
            twice = {**layout, 'parts': [*layout['parts'], layout['parts'][0]]}
            path.write_text(json.dumps(twice), encoding='utf-8')
            with self.assertRaises(ValidationError):
                load_layout(path)

    def test_pointer_substitution_preserves_source_identity(self):
        """Keys are identifiers, not array positions; escape slash and tilde."""
        self.assertEqual(expand_pointer('/pd/{pump}/st', 'P17'), '/pd/P17/st')
        self.assertEqual(expand_pointer('/pd/{pump}/st', 'P/1~'), '/pd/P~11~0/st')
        self.assertEqual(
            expand_pointer('/dex/PUMP{pump_number}_CORE', 'P17'), '/dex/PUMP17_CORE'
        )
        with self.assertRaises(ValidationError):
            expand_pointer('/pd/{wrong}/st', 'P17')
