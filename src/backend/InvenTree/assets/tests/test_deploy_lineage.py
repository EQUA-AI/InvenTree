"""The check that stops this code migrating a database it does not describe.

Two lines of this fork share migration numbers without sharing migrations, and
Django will happily apply one line's history beside the other's. The check has
to be right in both directions: a refusal that fires on this line's own
database, or on a rolled-back image, teaches an operator to ignore it - and one
that stays quiet on the other line's is the failure it exists to prevent.
"""

from io import StringIO

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase

from assets.deploy_lineage import lineage, report


class DeployLineageTests(TestCase):
    """Exercised against the real migration graph, not a mock of it."""

    def record(self, app_label, name):
        """Mark a migration applied, as another image's ``migrate`` would have."""
        MigrationRecorder(connection).record_applied(app_label, name)

    def make_pending(self, app_label='assets'):
        """Forget an app's newest migration, as a database one image behind."""
        [(_label, name)] = MigrationLoader(connection).graph.leaf_nodes(app_label)
        MigrationRecorder(connection).record_unapplied(app_label, name)
        return name

    def refused(self):
        """Run the report, expecting it to exit, and return what it printed."""
        out = StringIO()
        with self.assertRaises(SystemExit) as stopped:
            report(out=out)
        self.assertEqual(stopped.exception.code, 1)
        return out.getvalue()

    def test_a_database_on_this_line_has_nothing_to_say(self):
        """The false positive that would teach an operator to ignore the check.

        A freshly migrated database already holds applied names with no file
        behind them - Django records every migration a squash replaced - and a
        plain applied-minus-on-disk test called 17 of them unknown.
        """
        found = lineage()

        self.assertEqual(found.pending, ())
        self.assertEqual(found.unknown, ())
        self.assertEqual(found.leftover, ())
        self.assertFalse(found.diverged)

    def test_the_squashed_names_really_are_in_the_table(self):
        """Proves the test above is testing the exclusion, not an empty table."""
        loader = MigrationLoader(connection)
        orphaned = set(loader.applied_migrations) - set(loader.disk_migrations)

        self.assertTrue(
            orphaned,
            'no applied-but-not-on-disk names exist, so the squash exclusion '
            'is not being exercised by this suite',
        )

    def test_report_passes_a_clean_database_in_one_line(self):
        """The common case: a restart with nothing to do."""
        out = StringIO()

        self.assertTrue(report(out=out))
        self.assertEqual(out.getvalue(), 'lineage ok: nothing to apply.\n')

    def test_this_lines_own_rollout_is_allowed(self):
        """Migrations to apply, nothing unknown: the case the check must pass."""
        name = self.make_pending()
        out = StringIO()

        self.assertEqual(lineage().pending, (('assets', name),))
        self.assertTrue(report(out=out))
        self.assertEqual(
            out.getvalue(),
            'lineage ok: 1 migration(s) to apply (assets 1), and every applied '
            'migration is one this code knows.\n',
        )

    def test_the_other_lines_database_is_refused(self):
        """The real case: ``assets`` 0011 exists on both lines, differently."""
        self.make_pending()
        self.record('assets', '0011_assetmachine_profile')
        self.record('assets', '0013_clientscopegrant')

        found = lineage()
        self.assertTrue(found.diverged)
        self.assertEqual(
            found.unknown,
            (
                ('assets', '0011_assetmachine_profile'),
                ('assets', '0013_clientscopegrant'),
            ),
        )

        printed = self.refused()
        self.assertIn('STOP: this code has 1 migration(s) to apply', printed)
        self.assertIn('already holds 2 it has never seen', printed)
        self.assertIn('to apply: assets 1', printed)
        self.assertIn(
            '    assets 2 (0011_assetmachine_profile, 0013_clientscopegrant)\n', printed
        )
        self.assertIn('Do not run migrate', printed)

    def test_unknown_history_in_another_app_still_refuses(self):
        """Not judged app by app: one line's migrations lean on another's tables.

        The other line added migrations to thirteen apps. A pending migration
        here may touch any of them through a foreign key, so unknown history
        anywhere is reason enough not to add to this database.
        """
        self.make_pending('assets')
        self.record('part', '0155_some_other_lines_change')

        printed = self.refused()

        self.assertIn('to apply: assets 1', printed)
        self.assertIn('    part 1 (0155_some_other_lines_change)\n', printed)

    def test_a_database_ahead_of_its_image_is_not_refused(self):
        """A rollback: unknown migrations, nothing to apply, and it must start.

        Refusing here would turn the rollback of any release that carried a
        migration into a container that will not boot.
        """
        self.record('assets', '9999_from_the_release_rolled_back')
        out = StringIO()

        self.assertFalse(lineage().diverged)
        self.assertTrue(report(out=out))
        self.assertIn('ahead of this image, as after a rollback', out.getvalue())
        self.assertIn('assets 1', out.getvalue())
        self.assertTrue(out.getvalue().endswith('lineage ok: nothing to apply.\n'))

    def test_an_uninstalled_apps_rows_are_leftovers_not_a_refusal(self):
        """A removed plugin leaves rows behind and says nothing about lineage."""
        self.make_pending()
        self.record('some_removed_plugin', '0001_initial')
        out = StringIO()

        self.assertTrue(report(out=out))

        self.assertIn('some_removed_plugin 1', out.getvalue())
        self.assertIn('not a lineage problem', out.getvalue())
        self.assertIn('lineage ok: 1 migration(s) to apply', out.getvalue())
