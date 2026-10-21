"""Check the narrowly proven transitive squash ancestry."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from assets.migration_aliases import (
    LEGACY_SQUASH_ANCESTORS,
    recognized_legacy_ancestors,
)


class LegacyMigrationAncestryTests(SimpleTestCase):
    """Missing source and coincident migration numbers remain unknown."""

    def test_proven_ancestors_require_shipped_squash_and_applied_anchor(self):
        """An applied historical merge is evidence for its parallel branch."""
        for outer, anchor, ancestors in LEGACY_SQUASH_ANCESTORS:
            with self.subTest(outer=outer):
                disk = {outer: SimpleNamespace(replaces=[anchor])}
                for applied in ({anchor}, {outer}):
                    self.assertEqual(
                        recognized_legacy_ancestors(disk, applied), ancestors
                    )
                self.assertEqual(recognized_legacy_ancestors(disk, ancestors), set())
                self.assertEqual(recognized_legacy_ancestors({}, {anchor}), set())
                self.assertEqual(
                    recognized_legacy_ancestors(
                        {outer: SimpleNamespace(replaces=[])}, {anchor}
                    ),
                    set(),
                )

    def test_other_numbered_branches_are_never_recognized(self):
        """The 17 missing operational migrations are not squash ancestors."""
        disk = {
            outer: SimpleNamespace(replaces=[anchor])
            for outer, anchor, _ in LEGACY_SQUASH_ANCESTORS
        }
        applied = {anchor for _, anchor, _ in LEGACY_SQUASH_ANCESTORS}
        known = recognized_legacy_ancestors(disk, applied)
        self.assertEqual(len(known), 7)
        self.assertNotIn(('aichat', '0054_investigation_proposal_action'), known)
        self.assertNotIn(('part', '0061_some_other_branch'), known)
