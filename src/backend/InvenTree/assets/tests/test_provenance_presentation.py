"""Pure presentation classification tests; no database or Django setup."""
import unittest
from assets.provenance import component_provenance_kind

class ProvenancePresentationTests(unittest.TestCase):
    def test_known_importers(self):
        for source, kind in [
            ('Workbook population f9006a91f8a9', 'register'),
            ('Inferred from source tags; physical assignment is unverified.', 'scada'),
            ('Explicit dictionary review crosswalk', 'tag_review'),
            ('Operator review', 'manual'),
        ]:
            with self.subTest(kind=kind):
                self.assertEqual(component_provenance_kind(source), kind)

    def test_unknown_is_never_verified(self):
        for source in ['', 'Verified', 'Workbook population invalid', 'New importer']:
            self.assertEqual(component_provenance_kind(source), 'unknown')
