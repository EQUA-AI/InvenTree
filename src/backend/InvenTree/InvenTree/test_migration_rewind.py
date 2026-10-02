"""Tests for an explicit, history-preserving migration rewind plan."""

import importlib
import io
import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from InvenTree.migration_rewind import ordered_rewind_plan


class OrderedRewindPlanTests(TestCase):
    """Rewind a canonical suffix rather than rebuilding through a retained branch."""

    def executor(self, *, applied=None, requested=None):
        """Build a graph-shaped planner without touching a database."""
        names = [
            ('assets', '0010_base'),
            ('assets', '0011_profile'),
            ('assets', '0016_placement'),
            ('assets', '0011_registry'),
            ('assets', '0014_activation'),
            ('assets', '0019_merge'),
            ('other', '0002_later'),
        ]
        migrations = [
            SimpleNamespace(
                app_label=app, name=name, operations=[SimpleNamespace(reversible=True)]
            )
            for app, name in names
        ]
        nodes = dict(zip(names, migrations, strict=True))
        if applied is None:
            applied = names
        if requested is None:
            requested = [names[5], names[2], names[1], names[4], names[3]]
        loader = SimpleNamespace(
            applied_migrations=dict.fromkeys(applied),
            graph=SimpleNamespace(leaf_nodes=lambda: [names[5], names[6]]),
        )

        def plan(targets, *, clean_start=False):
            if clean_start:
                return [(migration, False) for migration in migrations]
            return [(nodes[key], True) for key in requested]

        return SimpleNamespace(loader=loader, migration_plan=plan), names, nodes

    def test_rewinds_in_inverse_forward_order(self):
        """SQLite rebuilds see precisely the state whose schema still exists."""
        executor, names, _ = self.executor()
        result = ordered_rewind_plan(executor, [names[0]])
        self.assertEqual(
            [
                (migration.app_label, migration.name, backwards)
                for migration, backwards in result
            ],
            [(app, name, True) for app, name in reversed(names[1:])],
        )

    def test_includes_retained_later_migrations(self):
        """A sibling or other-app state cannot survive a rewind past its position."""
        executor, names, _ = self.executor(requested=[('assets', '0011_profile')])
        result = ordered_rewind_plan(executor, [names[0]])
        self.assertIn(
            ('other', '0002_later'), [(m.app_label, m.name) for m, _ in result]
        )
        self.assertIn(
            ('assets', '0011_registry'), [(m.app_label, m.name) for m, _ in result]
        )

    def test_does_not_unapply_migrations_missing_from_existing_history(self):
        """An IoT-only database need not have applied the target branch."""
        applied = [
            ('assets', '0010_base'),
            ('assets', '0011_registry'),
            ('assets', '0014_activation'),
        ]
        requested = [('assets', '0014_activation'), ('assets', '0011_registry')]
        executor, names, _ = self.executor(applied=applied, requested=requested)
        result = ordered_rewind_plan(executor, [names[0]])
        self.assertEqual(
            [(m.app_label, m.name) for m, _ in result], list(reversed(applied[1:]))
        )

    def test_empty_plan_is_read_only(self):
        """Already-at-target databases have nothing to rewind."""
        executor, names, _ = self.executor(requested=[])
        self.assertEqual(ordered_rewind_plan(executor, [names[0]]), [])

    def test_rejects_forward_or_mixed_plans(self):
        """This operation cannot apply schema changes under a rewind approval."""
        executor, names, nodes = self.executor()
        executor.migration_plan = lambda *args, **kwargs: [(nodes[names[1]], False)]
        with self.assertRaisesRegex(ValueError, 'backwards'):
            ordered_rewind_plan(executor, [names[0]])

    def test_rejects_irreversible_operations_before_execution(self):
        """A preview must fail before the first schema change if any step cannot reverse."""
        executor, names, nodes = self.executor()
        nodes[names[6]].operations[0].reversible = False
        with self.assertRaisesRegex(ValueError, 'irreversible'):
            ordered_rewind_plan(executor, [names[0]])


class RewindCommandTests(TestCase):
    """Require a read-only preview and explicit approval of its exact wider plan."""

    def setUp(self):
        """Provide a disposable, graph-shaped connection without database access."""
        self.module = importlib.import_module(
            'InvenTree.management.commands.rewind_migrations'
        )
        self.executor, self.names, nodes = OrderedRewindPlanTests().executor()
        self.executor.loader.graph.nodes = nodes
        self.executor.loader.check_consistent_history = Mock()
        self.executor.migrate = Mock()
        self.connection = SimpleNamespace(
            vendor='sqlite', settings_dict={'NAME': ':memory:'}
        )
        self.output = io.StringIO()
        self.command = self.module.Command(stdout=self.output)
        for attribute, value in [
            ('connections', {'default': self.connection}),
            ('MigrationExecutor', Mock(return_value=self.executor)),
        ]:
            replacement = patch.object(self.module, attribute, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.options = {
            'app_label': 'assets',
            'migration_name': '0010_base',
            'database': 'default',
            'apply': False,
            'disposable_database': False,
            'expected_plan_sha256': None,
        }

    def preview(self):
        """Return the exact read-only command report."""
        self.command.handle(**self.options)
        return json.loads(self.output.getvalue())

    def test_preview_never_executes_migrations(self):
        """Default invocation reports the complete suffix including other apps."""
        report = self.preview()
        self.executor.migrate.assert_not_called()
        self.assertEqual(report['plan'][0], ['other', '0002_later'])
        self.assertIn(['other', '0002_later'], report['additional_rewinds'])

    def test_apply_requires_disposable_database_acknowledgement(self):
        """An apply flag alone cannot change a configured database."""
        self.options['apply'] = True
        with self.assertRaisesRegex(self.module.CommandError, 'disposable'):
            self.command.handle(**self.options)
        self.executor.migrate.assert_not_called()

    def test_apply_rejects_stale_plan_hash(self):
        """Changes between preview and execution require a new approval."""
        self.options.update(
            apply=True, disposable_database=True, expected_plan_sha256='stale'
        )
        with self.assertRaisesRegex(self.module.CommandError, 'hash'):
            self.command.handle(**self.options)
        self.executor.migrate.assert_not_called()

    def test_hash_is_bound_to_the_configured_database(self):
        """A preview for one disposable database cannot authorize another."""
        report = self.preview()
        self.connection.settings_dict['NAME'] = 'another.sqlite3'
        self.options.update(
            apply=True,
            disposable_database=True,
            expected_plan_sha256=report['plan_sha256'],
        )
        with self.assertRaisesRegex(self.module.CommandError, 'hash'):
            self.command.handle(**self.options)
        self.executor.migrate.assert_not_called()

    def test_matching_hash_executes_the_previewed_order(self):
        """Only the explicitly approved canonical suffix reaches the executor."""
        report = self.preview()
        self.options.update(
            apply=True,
            disposable_database=True,
            expected_plan_sha256=report['plan_sha256'],
        )
        self.command.handle(**self.options)
        (targets,) = self.executor.migrate.call_args.args
        plan = self.executor.migrate.call_args.kwargs['plan']
        self.assertEqual(targets, [self.names[0]])
        self.assertEqual(
            [(m.app_label, m.name) for m, _ in plan],
            [tuple(key) for key in report['plan']],
        )

    def test_command_refuses_non_sqlite_execution(self):
        """Deployment PostgreSQL recovery is not authorized by this rehearsal tool."""
        self.connection.vendor = 'postgresql'
        self.options.update(apply=True, disposable_database=True)
        with self.assertRaisesRegex(self.module.CommandError, 'SQLite'):
            self.command.handle(**self.options)
        self.executor.migrate.assert_not_called()

    def test_target_must_be_applied_and_exact(self):
        """Numeric prefixes and unapplied target names are not silently repaired."""
        self.options['migration_name'] = '0010'
        with self.assertRaisesRegex(self.module.CommandError, 'exact'):
            self.command.handle(**self.options)
        self.executor.loader.applied_migrations.pop(self.names[0])
        self.options['migration_name'] = '0010_base'
        with self.assertRaisesRegex(self.module.CommandError, 'applied'):
            self.command.handle(**self.options)
        self.executor.migrate.assert_not_called()
