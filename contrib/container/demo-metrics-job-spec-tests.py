#!/usr/bin/env python3
"""Offline tests for contrib/container/demo-metrics-job-spec.py.

Run from the repository root:

    python3 contrib/container/demo-metrics-job-spec-tests.py

These tests are stdlib-only and perform no network, database, or cloud work.
They validate the release artifact renderer only; they are not deployment
approval and they do not test the backend feature. Temporary files go to the
runtime scratch directory (TMPDIR), never /tmp.
"""

import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, '..', '..'))
RENDERER = os.path.join(HERE, 'demo-metrics-job-spec.py')
EXAMPLE = os.path.join(HERE, 'demo-metrics-job-input.example.json')

#: Tracked backend sources for the offline integration contract checks.
#: These are PARSED with stdlib ast only: Django is never imported, no
#: database is opened and no management command is executed.
BACKEND = os.path.join(REPO, 'src', 'backend', 'InvenTree')
COMMANDS_DIR = os.path.join(BACKEND, 'assets', 'management', 'commands')
FINGERPRINT = os.path.join(BACKEND, 'assets', 'demo_metrics', 'fingerprint.py')
PLANNER = os.path.join(BACKEND, 'assets', 'demo_metrics', 'planner.py')
APPLY_SERVICE = os.path.join(BACKEND, 'assets', 'demo_metrics', 'apply_service.py')

#: Scratch directory for transient probe files (never the system /tmp).
SCRATCH = os.environ.get('TMPDIR') or tempfile.gettempdir()


def load_renderer():
    """Load the standalone renderer without executing its CLI."""
    spec = importlib.util.spec_from_file_location('demo_metrics_job_spec', RENDERER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load_renderer()

DIGEST = 'sha256:' + 'a' * 64
IMAGE = 'aimmsexample.azurecr.io/experimental@' + DIGEST
SUBSCRIPTION = '1' * 8 + '-' + '2' * 4 + '-' + '3' * 4 + '-' + '4' * 4 + '-' + '5' * 12


def parse_module(path):
    """Parse a tracked backend source file with stdlib ast (offline)."""
    with open(path, encoding='utf-8') as handle:
        return ast.parse(handle.read(), filename=path)


def declared_options(path):
    """Return {flag: takes_value} from a command's add_arguments()."""
    options = {}
    for node in ast.walk(parse_module(path)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != 'add_argument' or not node.args:
            continue
        flag = node.args[0]
        if not isinstance(flag, ast.Constant) or not str(flag.value).startswith('-'):
            continue
        store_true = any(
            keyword.arg == 'action'
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value == 'store_true'
            for keyword in node.keywords
        )
        options[flag.value] = not store_true
    return options


def module_constants(path):
    """Return module-level literal constants of a tracked backend file."""
    constants = {}
    for node in parse_module(path).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        constants[target.id] = ast.literal_eval(node.value)
                    except ValueError:
                        continue
    return constants


def valid_input():
    """Build an isolated synthetic input for offline contract tests."""
    return {
        'job_name': 'aimms-demo-metrics',
        'resource_group': 'EpconChat',
        'location': 'eastus2',
        'environment_resource_id': (
            '/subscriptions/'
            + SUBSCRIPTION
            + '/resourceGroups/EpconChat/providers/Microsoft.App/managedEnvironments/epcon-ai-env'
        ),
        'git_commit': 'c' * 40,
        'image_reference': IMAGE,
        'expected_image_digest': DIGEST,
        'web_image_reference': IMAGE,
        'worker_image_reference': IMAGE,
        'python_interpreter': '/usr/bin/python3',
        'working_directory': '/home/inventree/src/backend/InvenTree',
        'command': 'plan_demo_metrics',
        'command_args': [
            '--fixture',
            '/inputs/demo_fixture.json',
            '--mapping',
            '/inputs/target_mapping.resolved.json',
            '--out',
            '/outputs/plan.json',
            '--actor',
            'release-operator',
        ],
        'replica_timeout_seconds': 600,
        'environment': [{'name': 'INVENTREE_AUTO_UPDATE', 'value': 'False'}],
        'secret_references': [
            {'name': 'INVENTREE_DB_PASSWORD', 'secret_ref': 'inventree-db-password'}
        ],
    }


class RenderHappyPath(unittest.TestCase):
    """Verify the fixed one-shot execution contract."""

    def test_manual_bounded_one_shot_spec(self):
        """Render one manual replica with a bounded timeout and no retries."""
        spec = R.build_job_spec(valid_input())
        configuration = spec['job']['properties']['configuration']
        self.assertEqual(configuration['triggerType'], 'Manual')
        self.assertEqual(configuration['replicaRetryLimit'], 0)
        self.assertEqual(
            configuration['manualTriggerConfig'],
            {'replicaCompletionCount': 1, 'parallelism': 1},
        )
        self.assertEqual(configuration['replicaTimeout'], 600)
        container = spec['job']['properties']['template']['containers'][0]
        self.assertEqual(container['image'], IMAGE)
        # command/args are split so BOTH the image ENTRYPOINT and CMD are
        # overridden; args carries the absolute manage.py and the command.
        self.assertEqual(container['command'], ['/usr/bin/python3'])
        self.assertEqual(container['args'][:2], [R.MANAGE_PY, 'plan_demo_metrics'])
        self.assertIn('--actor', container['args'])

    def test_init_sh_is_bypassed_and_migrations_disabled(self):
        """Override image startup and explicitly disable auto-update."""
        spec = R.build_job_spec(valid_input())
        container = spec['job']['properties']['template']['containers'][0]
        self.assertNotIn(
            'init.sh', json.dumps(container['command'] + container['args'])
        )
        self.assertTrue(spec['provenance']['bypasses_init_sh'])
        env = {entry['name']: entry.get('value') for entry in container['env']}
        self.assertEqual(env['INVENTREE_AUTO_UPDATE'], 'False')

    def test_secrets_appear_only_as_references(self):
        """Keep approved secret names separate from inline values."""
        spec = R.build_job_spec(valid_input())
        container = spec['job']['properties']['template']['containers'][0]
        secret_entries = [e for e in container['env'] if 'secretRef' in e]
        self.assertEqual(
            secret_entries,
            [{'name': 'INVENTREE_DB_PASSWORD', 'secretRef': 'inventree-db-password'}],
        )
        for entry in container['env']:
            self.assertTrue('secretRef' in entry or 'value' in entry)
            self.assertFalse('secretRef' in entry and 'value' in entry)

    def test_all_six_commands_render(self):
        """Render the supported command variants with required arguments."""
        base = valid_input()
        variants = {
            'apply_demo_metrics': [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--plan',
                '/inputs/plan.json',
                '--approved-plan-sha256',
                'd' * 64,
                '--actor',
                'release-operator',
            ],
            'verify_demo_metrics': [
                '--session',
                'equa-demo-1',
                '--actor',
                'release-operator',
            ],
            'replay_demo_metrics': [
                '--session',
                'equa-demo-1',
                '--actor',
                'release-operator',
                '--interval-seconds',
                '30',
                '--max-duration-seconds',
                '1800',
            ],
            'stop_demo_metrics': [
                '--session',
                'equa-demo-1',
                '--actor',
                'release-operator',
            ],
            'cleanup_demo_metrics': [
                '--session',
                'equa-demo-1',
                '--actor',
                'release-operator',
                '--out',
                '/outputs/cleanup.json',
            ],
        }
        for command, args in variants.items():
            data = copy.deepcopy(base)
            data['command'] = command
            data['command_args'] = args
            if command == 'replay_demo_metrics':
                data['replica_timeout_seconds'] = 2100
            spec = R.build_job_spec(data)
            rendered = spec['job']['properties']['template']['containers'][0]
            self.assertEqual(rendered['args'][1], command)

    def test_cleanup_apply_requires_approved_hash(self):
        """Accept cleanup mutation only with an explicit approval hash."""
        data = valid_input()
        data['command'] = 'cleanup_demo_metrics'
        data['command_args'] = [
            '--session',
            'equa-demo-1',
            '--actor',
            'release-operator',
            '--apply',
            '--approved-cleanup-sha256',
            'e' * 64,
        ]
        spec = R.build_job_spec(data)
        self.assertIn(
            '--apply', spec['job']['properties']['template']['containers'][0]['args']
        )

    def test_provenance_records_full_shared_image_references(self):
        """Retain each consumer's complete image reference in provenance."""
        spec = R.build_job_spec(valid_input())
        provenance = spec['provenance']
        self.assertEqual(provenance['image_reference'], IMAGE)
        self.assertEqual(provenance['web_image_reference'], IMAGE)
        self.assertEqual(provenance['worker_image_reference'], IMAGE)
        self.assertEqual(provenance['image_digest'], DIGEST)

    def test_envelope_declares_it_is_not_a_deployment_document(self):
        """Identify the output as a review artifact rather than deployment input."""
        spec = R.build_job_spec(valid_input())
        contract = spec['document_contract']
        self.assertIn('not an Azure ARM/CLI document', contract['kind'])
        self.assertIn('runbook', contract['consumption'])

    def test_working_directory_contract_is_honest(self):
        """Distinguish the project directory from the unchanged image workdir."""
        spec = R.build_job_spec(valid_input())
        provenance = spec['provenance']
        self.assertEqual(provenance['declared_working_directory'], R.BACKEND_DIR)
        self.assertFalse(provenance['working_directory_applied_by_runtime'])
        self.assertEqual(provenance['image_workdir'], '/home/inventree')

    def test_render_is_deterministic(self):
        """Produce identical specs for identical validated input."""
        self.assertEqual(
            json.dumps(R.build_job_spec(valid_input()), sort_keys=True),
            json.dumps(R.build_job_spec(valid_input()), sort_keys=True),
        )


class Refusals(unittest.TestCase):
    """Reject unsupported release identities and execution options."""

    def assert_refused(self, data, code):
        """Check that rejection exposes the expected stable code."""
        with self.assertRaises(R.SpecError) as caught:
            R.build_job_spec(data)
        self.assertEqual(caught.exception.code, code)

    def mutate(self, **changes):
        """Return a fresh valid input with explicit test changes."""
        data = valid_input()
        data.update(changes)
        return data

    def test_shipped_example_is_refused(self):
        """Keep the unresolved example impossible to execute unchanged."""
        with open(EXAMPLE, encoding='utf-8') as handle:
            example = json.load(handle)
        self.assert_refused(example, 'UNRESOLVED_PLACEHOLDER')

    def test_unresolved_placeholder_in_args(self):
        """Reject unresolved command arguments."""
        data = valid_input()
        data['command_args'][1] = '<fixture>'
        self.assert_refused(data, 'UNRESOLVED_PLACEHOLDER')

    def test_mutable_tag_refused(self):
        """Reject tag-only and combined tag/digest references."""
        self.assert_refused(
            self.mutate(image_reference='aimmsexample.azurecr.io/experimental:latest'),
            'MUTABLE_IMAGE',
        )
        self.assert_refused(
            self.mutate(
                image_reference='aimmsexample.azurecr.io/experimental:latest@' + DIGEST,
                expected_image_digest=DIGEST,
            ),
            'MUTABLE_IMAGE',
        )

    def test_wrong_image_digest_refused(self):
        """Reject an image which differs from its approved digest."""
        self.assert_refused(
            self.mutate(expected_image_digest='sha256:' + 'b' * 64),
            'IMAGE_DIGEST_MISMATCH',
        )

    def test_unmatched_web_worker_images_refused(self):
        """Reject different web or worker image digests."""
        self.assert_refused(
            self.mutate(
                web_image_reference='aimmsexample.azurecr.io/experimental@sha256:'
                + 'b' * 64
            ),
            'IMAGE_SET_MISMATCH',
        )
        self.assert_refused(
            self.mutate(
                worker_image_reference='aimmsexample.azurecr.io/experimental@sha256:'
                + 'c' * 64
            ),
            'IMAGE_SET_MISMATCH',
        )

    def test_unknown_input_key_refused(self):
        """Reject fields outside the reviewed input contract."""
        self.assert_refused(self.mutate(trigger_type='Event'), 'BAD_INPUT')

    def test_inline_secret_refused(self):
        """Reject credential-named inline settings."""
        data = valid_input()
        data['environment'].append({
            'name': 'INVENTREE_DB_PASSWORD',
            'value': 'hunter2',
        })
        self.assert_refused(data, 'INLINE_SECRET')
        data = valid_input()
        data['environment'].append({'name': 'INVENTREE_PLUGIN_TOKEN', 'value': 'abc'})
        self.assert_refused(data, 'INLINE_SECRET')

    def test_credential_looking_value_refused(self):
        """Reject credential-shaped values even under other setting names."""
        data = valid_input()
        data['environment'].append({
            'name': 'INVENTREE_EXTRA',
            'value': 'postgres://demo:***@epconchat-pg-dev.postgres.database.azure.com/inventree',
        })
        self.assert_refused(data, 'INLINE_SECRET')

    def test_secret_name_with_both_value_and_ref_refused(self):
        """Reject ambiguous inline and secret-reference bindings."""
        data = valid_input()
        data['environment'].append({'name': 'INVENTREE_DB_PASSWORD', 'value': ''})
        self.assert_refused(data, 'INLINE_SECRET')

    def test_non_approved_command_refused(self):
        """Reject commands outside the six demo operations."""
        self.assert_refused(self.mutate(command='migrate'), 'BAD_COMMAND')
        self.assert_refused(self.mutate(command='shell'), 'BAD_COMMAND')

    def test_migration_arguments_refused(self):
        """Reject attempts to request schema changes."""
        data = valid_input()
        data['command_args'].extend(['migrate'])
        self.assert_refused(data, 'MIGRATION_COMMAND')

    def test_actor_required(self):
        """Reject absent or blank actor identifiers."""
        data = valid_input()
        data['command_args'] = [
            '--fixture',
            '/inputs/demo_fixture.json',
            '--mapping',
            '/inputs/target_mapping.resolved.json',
            '--out',
            '/outputs/plan.json',
        ]
        self.assert_refused(data, 'MISSING_ACTOR')
        data = valid_input()
        data['command_args'][data['command_args'].index('--actor') + 1] = ' '
        self.assert_refused(data, 'MISSING_ACTOR')

    def test_required_flags_per_command(self):
        """Require the input artifacts appropriate to each command."""
        data = valid_input()
        data['command_args'] = [
            '--fixture',
            '/inputs/demo_fixture.json',
            '--mapping',
            '/inputs/target_mapping.resolved.json',
            '--actor',
            'release-operator',
        ]
        self.assert_refused(data, 'MISSING_FLAG')
        data = valid_input()
        data['command'] = 'apply_demo_metrics'
        data['command_args'] = [
            '--fixture',
            '/inputs/demo_fixture.json',
            '--mapping',
            '/inputs/target_mapping.resolved.json',
            '--plan',
            '/inputs/plan.json',
            '--actor',
            'release-operator',
        ]
        self.assert_refused(data, 'MISSING_FLAG')

    def test_bad_approved_hash_refused(self):
        """Reject malformed approval digests."""
        data = valid_input()
        data['command'] = 'apply_demo_metrics'
        data['command_args'] = [
            '--fixture',
            '/inputs/demo_fixture.json',
            '--mapping',
            '/inputs/target_mapping.resolved.json',
            '--plan',
            '/inputs/plan.json',
            '--approved-plan-sha256',
            'not-a-hash',
            '--actor',
            'release-operator',
        ]
        self.assert_refused(data, 'BAD_HASH')

    def test_replay_bounds(self):
        """Require an explicit replay duration and sufficient job timeout."""
        data = valid_input()
        data['command'] = 'replay_demo_metrics'
        data['command_args'] = [
            '--session',
            'equa-demo-1',
            '--actor',
            'release-operator',
        ]
        self.assert_refused(data, 'MISSING_FLAG')
        data['command_args'] = [
            '--session',
            'equa-demo-1',
            '--actor',
            'release-operator',
            '--max-duration-seconds',
            '1800',
        ]
        self.assert_refused(data, 'BAD_TIMEOUT')
        data['replica_timeout_seconds'] = 2100
        R.build_job_spec(data)

    def test_cleanup_apply_without_hash_refused(self):
        """Reject cleanup mutation without its approval digest."""
        data = valid_input()
        data['command'] = 'cleanup_demo_metrics'
        data['command_args'] = [
            '--session',
            'equa-demo-1',
            '--actor',
            'release-operator',
            '--apply',
        ]
        self.assert_refused(data, 'MISSING_FLAG')

    def test_auto_update_must_be_false(self):
        """Require explicit disabling of startup migration ownership."""
        data = valid_input()
        data['environment'] = []
        self.assert_refused(data, 'MIGRATIONS_NOT_DISABLED')
        data = valid_input()
        data['environment'] = [{'name': 'INVENTREE_AUTO_UPDATE', 'value': 'True'}]
        self.assert_refused(data, 'MIGRATIONS_NOT_DISABLED')

    def test_bad_identity_fields(self):
        """Reject malformed commit, environment and job identities."""
        self.assert_refused(self.mutate(git_commit='abc'), 'BAD_COMMIT')
        self.assert_refused(
            self.mutate(
                environment_resource_id='/subscriptions/x/resourceGroups/rg/other'
            ),
            'BAD_ENVIRONMENT_ID',
        )
        self.assert_refused(self.mutate(job_name='Aimms_Demo'), 'BAD_NAME')

    def test_bad_runtime_fields(self):
        """Reject unsupported interpreter and project paths."""
        self.assert_refused(
            self.mutate(python_interpreter='/bin/bash ./init.sh'), 'BAD_INTERPRETER'
        )
        self.assert_refused(self.mutate(python_interpreter='node'), 'BAD_INTERPRETER')
        self.assert_refused(
            self.mutate(working_directory='/home/inventree'), 'BAD_WORKDIR'
        )

    def test_timeout_bounds(self):
        """Require an integer timeout inside the bounded range."""
        self.assert_refused(self.mutate(replica_timeout_seconds=30), 'BAD_TIMEOUT')
        self.assert_refused(self.mutate(replica_timeout_seconds=7200), 'BAD_TIMEOUT')
        self.assert_refused(self.mutate(replica_timeout_seconds='600'), 'BAD_TIMEOUT')


class StrictTypeAndShapeChecks(unittest.TestCase):
    """Regression tests for str(None) coercion and regex-on-non-string crashes."""

    def assert_refused(self, data, code):
        """Assert a sanitized refusal instead of an unexpected exception."""
        with self.assertRaises(R.SpecError) as caught:
            R.build_job_spec(data)
        self.assertEqual(caught.exception.code, code)

    def test_scalar_none_values_refused_not_coerced(self):
        """Reject null and container values without string coercion."""
        for field in (
            'job_name',
            'resource_group',
            'location',
            'environment_resource_id',
            'git_commit',
            'image_reference',
            'expected_image_digest',
            'web_image_reference',
            'worker_image_reference',
            'python_interpreter',
            'working_directory',
            'command',
        ):
            data = valid_input()
            data[field] = None
            self.assert_refused(data, 'BAD_INPUT')
        self.assert_refused(dict(valid_input(), job_name=12345), 'BAD_INPUT')
        self.assert_refused(
            dict(valid_input(), resource_group={'name': 'EpconChat'}), 'BAD_INPUT'
        )

    def test_secret_reference_none_name_is_sanitized_not_a_typeerror(self):
        """Reject null secret metadata through the public error contract."""
        data = valid_input()
        data['secret_references'] = [{'name': None, 'secret_ref': 'db-password'}]
        self.assert_refused(data, 'BAD_SECRET_REFS')
        data = valid_input()
        data['secret_references'] = [
            {'name': 'INVENTREE_DB_PASSWORD', 'secret_ref': None}
        ]
        self.assert_refused(data, 'BAD_SECRET_REFS')

    def test_environment_none_name_is_sanitized(self):
        """Reject null environment names and values predictably."""
        data = valid_input()
        data['environment'].append({'name': None, 'value': 'x'})
        self.assert_refused(data, 'BAD_ENV')
        data = valid_input()
        data['environment'].append({'name': 'INVENTREE_LOG_LEVEL', 'value': None})
        self.assert_refused(data, 'BAD_ENV')

    def test_resource_id_requires_full_uuid_subscription(self):
        """Require UUID grouping in subscription identifiers."""
        data = valid_input()
        data['environment_resource_id'] = (
            '/subscriptions/'
            + 'a' * 36
            + '/resourceGroups/EpconChat/providers/Microsoft.App/managedEnvironments/epcon-ai-env'
        )
        self.assert_refused(data, 'BAD_ENVIRONMENT_ID')

    def test_credentials_anywhere_in_input_refused(self):
        """Scan identity fields as well as environment and arguments."""
        data = valid_input()
        data['resource_group'] = (
            'https://example-user:synthetic-password@example.invalid'
        )
        self.assert_refused(data, 'INLINE_SECRET')

    def test_interpreter_must_be_absolute(self):
        """Reject interpreter lookup through an implicit search path."""
        data = valid_input()
        data['python_interpreter'] = 'python3'
        self.assert_refused(data, 'BAD_INTERPRETER')


class StrictArgumentParsing(unittest.TestCase):
    """The per-command argv parser must be strict: no smuggling, no coercion."""

    def assert_args_refused(self, command, args, code, timeout=600):
        """Assert rejection of a particular command argument vector."""
        data = valid_input()
        data['command'] = command
        data['command_args'] = args
        data['replica_timeout_seconds'] = timeout
        with self.assertRaises(R.SpecError) as caught:
            R.build_job_spec(data)
        self.assertEqual(caught.exception.code, code)

    def test_settings_and_pythonpath_refused(self):
        """Prevent command arguments from replacing settings or code paths."""
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--out',
                '/outputs/plan.json',
                '--actor',
                'release-operator',
                '--settings',
                'unreviewed.settings',
            ],
            'FORBIDDEN_FLAG',
        )
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--out',
                '/outputs/plan.json',
                '--actor',
                'release-operator',
                '--pythonpath',
                '/unreviewed',
            ],
            'FORBIDDEN_FLAG',
        )

    def test_unknown_switch_refused(self):
        """Reject options not declared for the selected command."""
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--out',
                '/outputs/plan.json',
                '--actor',
                'release-operator',
                '--include-history',
            ],
            'UNKNOWN_FLAG',
        )

    def test_positional_extras_refused(self):
        """Reject unrecognized positional arguments."""
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--out',
                '/outputs/plan.json',
                '--actor',
                'release-operator',
                'sneaky-extra',
            ],
            'BAD_ARGS',
        )

    def test_duplicate_switches_refused(self):
        """Reject duplicate actors and duplicate input paths."""
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--out',
                '/outputs/plan.json',
                '--actor',
                'release-operator',
                '--actor',
                'different-operator',
            ],
            'DUPLICATE_FLAG',
        )
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--fixture',
                '/inputs/other.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--out',
                '/outputs/plan.json',
                '--actor',
                'release-operator',
            ],
            'DUPLICATE_FLAG',
        )

    def test_missing_flag_values_refused(self):
        """Reject absent, switch-shaped and blank option values."""
        # --actor swallowed --fixture as its value before; now it is a refusal.
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--actor',
                '--fixture',
                '/inputs/f.json',
                '--mapping',
                '/inputs/m.json',
                '--out',
                '/outputs/p.json',
            ],
            'MISSING_ACTOR',
        )
        # Trailing hash flag with no value.
        self.assert_args_refused(
            'apply_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--plan',
                '/inputs/plan.json',
                '--actor',
                'release-operator',
                '--approved-plan-sha256',
            ],
            'MISSING_FLAG',
        )
        # Empty value.
        self.assert_args_refused(
            'plan_demo_metrics',
            [
                '--fixture',
                ' ',
                '--mapping',
                '/inputs/m.json',
                '--out',
                '/outputs/p.json',
                '--actor',
                'release-operator',
            ],
            'MISSING_FLAG',
        )

    def test_numeric_flag_values_reject_type_nonfinite_overflow(self):
        """Reject nonnumeric and nonfinite replay durations."""
        base = ['--session', 'equa-demo-1', '--actor', 'release-operator']
        for bad in ('nan', 'inf', '-inf', '1e400', 'not-a-number'):
            self.assert_args_refused(
                'replay_demo_metrics',
                [*base, '--max-duration-seconds', bad],
                'BAD_ARGS',
                timeout=2100,
            )

    def test_interval_and_duration_bounds(self):
        """Reject replay intervals and durations outside their joint bounds."""
        base = ['--session', 'equa-demo-1', '--actor', 'release-operator']
        self.assert_args_refused(
            'replay_demo_metrics',
            [*base, '--interval-seconds', '-5', '--max-duration-seconds', '1800'],
            'BAD_ARGS',
            timeout=2100,
        )
        self.assert_args_refused(
            'replay_demo_metrics',
            [*base, '--interval-seconds', '30', '--max-duration-seconds', '7200'],
            'BAD_ARGS',
            timeout=2100,
        )
        self.assert_args_refused(
            'replay_demo_metrics',
            [*base, '--interval-seconds', '30', '--max-duration-seconds', '0'],
            'BAD_ARGS',
            timeout=2100,
        )
        self.assert_args_refused(
            'replay_demo_metrics',
            [*base, '--interval-seconds', '4000', '--max-duration-seconds', '1800'],
            'BAD_ARGS',
            timeout=2100,
        )
        self.assert_args_refused(
            'replay_demo_metrics',
            [*base, '--interval-seconds', '2000', '--max-duration-seconds', '1800'],
            'BAD_ARGS',
            timeout=2100,
        )

    def test_boolean_flags_are_bare_and_explicit(self):
        """Reject values attached to bare boolean switches."""
        # --include-history is store_true: it takes no value.
        self.assert_args_refused(
            'apply_demo_metrics',
            [
                '--fixture',
                '/inputs/demo_fixture.json',
                '--mapping',
                '/inputs/target_mapping.resolved.json',
                '--plan',
                '/inputs/plan.json',
                '--approved-plan-sha256',
                'd' * 64,
                '--actor',
                'release-operator',
                '--include-history',
                'True',
            ],
            'BAD_ARGS',
        )
        self.assert_args_refused(
            'cleanup_demo_metrics',
            [
                '--session',
                'equa-demo-1',
                '--actor',
                'release-operator',
                '--apply',
                'yes',
                '--approved-cleanup-sha256',
                'e' * 64,
            ],
            'BAD_ARGS',
        )

    def test_cleanup_hash_without_apply_refused(self):
        """Reject approval hashes in read-only cleanup mode."""
        self.assert_args_refused(
            'cleanup_demo_metrics',
            [
                '--session',
                'equa-demo-1',
                '--actor',
                'release-operator',
                '--approved-cleanup-sha256',
                'e' * 64,
            ],
            'BAD_ARGS',
        )

    def test_replay_timeout_boundary_is_duration_plus_120(self):
        """Accept exactly the documented replay shutdown allowance."""
        data = valid_input()
        data['command'] = 'replay_demo_metrics'
        data['command_args'] = [
            '--session',
            'equa-demo-1',
            '--actor',
            'release-operator',
            '--interval-seconds',
            '30',
            '--max-duration-seconds',
            '1800',
        ]
        data['replica_timeout_seconds'] = 1919
        with self.assertRaises(R.SpecError) as caught:
            R.build_job_spec(data)
        self.assertEqual(caught.exception.code, 'BAD_TIMEOUT')
        # Documented policy: timeout >= duration + 120.
        data['replica_timeout_seconds'] = 1920
        spec = R.build_job_spec(data)
        self.assertEqual(
            spec['job']['properties']['configuration']['replicaTimeout'], 1920
        )


class EnvAllowlists(unittest.TestCase):
    """Constrain settings and secret references to reviewed names."""

    def assert_refused(self, data, code):
        """Assert the expected environment validation error."""
        with self.assertRaises(R.SpecError) as caught:
            R.build_job_spec(data)
        self.assertEqual(caught.exception.code, code)

    def test_nonsecret_env_names_are_allowlisted(self):
        """Reject unknown, plugin, email and AI configuration."""
        data = valid_input()
        data['environment'].append({'name': 'INVENTREE_EXTRA', 'value': 'plain'})
        self.assert_refused(data, 'ENV_NOT_ALLOWED')
        data = valid_input()
        data['environment'].append({
            'name': 'INVENTREE_PLUGINS_ENABLED',
            'value': 'True',
        })
        self.assert_refused(data, 'ENV_NOT_ALLOWED')
        data = valid_input()
        data['environment'].append({
            'name': 'INVENTREE_EMAIL_HOST',
            'value': 'mail.invalid',
        })
        self.assert_refused(data, 'ENV_NOT_ALLOWED')
        data = valid_input()
        data['environment'].append({'name': 'INVENTREE_OPENAI_API_KEY', 'value': 'x'})
        self.assert_refused(data, 'INLINE_SECRET')

    def test_allowlisted_nonsecret_env_names_render(self):
        """Allow the explicitly reviewed database and code settings."""
        data = valid_input()
        data['environment'].extend([
            {'name': 'INVENTREE_DB_ENGINE', 'value': 'django.db.backends.postgresql'},
            {
                'name': 'INVENTREE_DB_HOST',
                'value': 'epconchat-pg-dev.postgres.database.azure.com',
            },
            {'name': 'INVENTREE_DB_NAME', 'value': 'inventree'},
            {'name': 'INVENTREE_DB_PORT', 'value': '5432'},
            {'name': 'INVENTREE_DB_OPTIONS', 'value': '{"sslmode": "require"}'},
            {'name': 'INVENTREE_COMMIT_HASH', 'value': 'c' * 40},
        ])
        spec = R.build_job_spec(data)
        names = {
            e['name']
            for e in spec['job']['properties']['template']['containers'][0]['env']
        }
        self.assertIn('INVENTREE_DB_HOST', names)

    def test_secret_reference_names_are_allowlisted(self):
        """Allow application secrets but reject unrelated mail credentials."""
        data = valid_input()
        data['secret_references'].append({
            'name': 'INVENTREE_MAIL_PASSWORD',
            'secret_ref': 'inventree-mail-password',
        })
        self.assert_refused(data, 'BAD_SECRET_REFS')
        data = valid_input()
        data['secret_references'] = [
            {'name': 'INVENTREE_SECRET_KEY', 'secret_ref': 'inventree-secret-key'}
        ]
        R.build_job_spec(data)


class CommandLine(unittest.TestCase):
    """Exercise real offline CLI execution and output-file handling."""

    def run_cli(self, input_path, out_path, check=False):
        """Run the renderer and capture its public exit code and output."""
        command = [sys.executable, RENDERER, '--input', input_path, '--out', out_path]
        if check:
            command.append('--check')
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_example_input_exits_nonzero(self):
        """Reject the shipped placeholders with a sanitized CLI response."""
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            out = os.path.join(tmp, 'spec.json')
            result = self.run_cli(EXAMPLE, out)
        self.assertEqual(result.returncode, 1)
        self.assertIn('refused: UNRESOLVED_PLACEHOLDER', result.stdout)
        self.assertNotIn('REPLACE_WITH', result.stdout)

    def test_valid_input_renders_spec_file(self):
        """Read back the exact spec written by a successful CLI invocation."""
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            input_path = os.path.join(tmp, 'input.json')
            out = os.path.join(tmp, 'spec.json')
            with open(input_path, 'w', encoding='utf-8') as handle:
                json.dump(valid_input(), handle)
            result = self.run_cli(input_path, out)
            self.assertEqual(result.returncode, 0, result.stdout)
            with open(out, encoding='utf-8') as handle:
                spec = json.load(handle)
        self.assertEqual(spec['artifact'], 'aimms-demo-metrics-job-spec')
        self.assertEqual(
            spec['job']['properties']['configuration']['triggerType'], 'Manual'
        )
        container = spec['job']['properties']['template']['containers'][0]
        self.assertEqual(container['command'], ['/usr/bin/python3'])
        self.assertEqual(container['args'][0], R.MANAGE_PY)

    def test_check_reports_validated_not_rendered(self):
        """Validate without creating an output artifact or claiming a write."""
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            input_path = os.path.join(tmp, 'input.json')
            out = os.path.join(tmp, 'spec.json')
            with open(input_path, 'w', encoding='utf-8') as handle:
                json.dump(valid_input(), handle)
            result = self.run_cli(input_path, out, check=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn('validated:', result.stdout)
            self.assertNotIn('rendered:', result.stdout)
            self.assertFalse(os.path.exists(out))

    def test_out_refuses_overwrite(self):
        """Preserve an existing reviewed artifact on output collision."""
        with tempfile.TemporaryDirectory(dir=SCRATCH) as tmp:
            input_path = os.path.join(tmp, 'input.json')
            out = os.path.join(tmp, 'spec.json')
            with open(input_path, 'w', encoding='utf-8') as handle:
                json.dump(valid_input(), handle)
            with open(out, 'w', encoding='utf-8') as handle:
                handle.write('reviewed-artifact-sentinel\n')
            result = self.run_cli(input_path, out)
            self.assertEqual(result.returncode, 1)
            self.assertIn('refused: OUTPUT_EXISTS', result.stdout)
            with open(out, encoding='utf-8') as handle:
                self.assertEqual(handle.read(), 'reviewed-artifact-sentinel\n')

    def test_renderer_stays_offline(self):
        """Reject accidental addition of common network clients."""
        with open(RENDERER, encoding='utf-8') as handle:
            source = handle.read()
        for banned in (
            'import urllib',
            'import socket',
            'import requests',
            'http.client',
        ):
            self.assertNotIn(banned, source)


class RuntimeAttestations(unittest.TestCase):
    """Runtime code/image attestation names, derivation and consistency.

    The backend apply preflight (assets/demo_metrics/fingerprint.py) requires
    the exact environment names AIMMS_APPROVED_COMMIT_SHA and
    AIMMS_APPROVED_IMAGE_DIGEST and refuses an absent or different
    attestation. The renderer must emit those exact names, derived from the
    reviewed input identities, and must refuse conflicting, blank or
    malformed declarations instead of attesting arbitrary code.
    """

    def assert_refused(self, data, code):
        """Assert the expected attestation validation error."""
        with self.assertRaises(R.SpecError) as caught:
            R.build_job_spec(data)
        self.assertEqual(caught.exception.code, code)

    def rendered_env(self, data):
        """Return the rendered container environment as a name->entry map."""
        spec = R.build_job_spec(data)
        container = spec['job']['properties']['template']['containers'][0]
        return {entry['name']: entry for entry in container['env']}

    def test_attestation_names_are_exact(self):
        """Pin the two environment names to the backend contract spelling."""
        self.assertEqual(R.COMMIT_ATTESTATION_ENV, 'AIMMS_APPROVED_COMMIT_SHA')
        self.assertEqual(R.IMAGE_ATTESTATION_ENV, 'AIMMS_APPROVED_IMAGE_DIGEST')
        self.assertEqual(
            tuple(R.ATTESTATION_ENV_NAMES),
            ('AIMMS_APPROVED_COMMIT_SHA', 'AIMMS_APPROVED_IMAGE_DIGEST'),
        )

    def test_attestations_are_derived_from_approved_input(self):
        """Always render both attestations from the reviewed identities."""
        env = self.rendered_env(valid_input())
        self.assertEqual(
            env[R.COMMIT_ATTESTATION_ENV]['value'], valid_input()['git_commit']
        )
        self.assertEqual(env[R.IMAGE_ATTESTATION_ENV]['value'], DIGEST)

    def test_attested_image_identity_matches_all_image_references(self):
        """Attest exactly the one digest shared by Job, web and worker."""
        spec = R.build_job_spec(valid_input())
        env = {
            e['name']: e
            for e in spec['job']['properties']['template']['containers'][0]['env']
        }
        attested = env[R.IMAGE_ATTESTATION_ENV]['value']
        self.assertEqual(attested, spec['provenance']['image_digest'])
        self.assertEqual(attested, IMAGE.rsplit('@', 1)[1])
        self.assertEqual(
            attested, spec['provenance']['web_image_reference'].rsplit('@', 1)[1]
        )
        self.assertEqual(
            attested, spec['provenance']['worker_image_reference'].rsplit('@', 1)[1]
        )

    def test_explicit_consistent_attestations_render_exactly_once(self):
        """Accept a restated attestation which matches the reviewed input."""
        data = valid_input()
        data['environment'].extend([
            {'name': 'AIMMS_APPROVED_COMMIT_SHA', 'value': data['git_commit']},
            {'name': 'AIMMS_APPROVED_IMAGE_DIGEST', 'value': DIGEST},
        ])
        env = self.rendered_env(data)
        self.assertEqual(env[R.COMMIT_ATTESTATION_ENV]['value'], data['git_commit'])
        self.assertEqual(env[R.IMAGE_ATTESTATION_ENV]['value'], DIGEST)
        spec = R.build_job_spec(data)
        names = [
            e['name']
            for e in spec['job']['properties']['template']['containers'][0]['env']
        ]
        self.assertEqual(names.count(R.COMMIT_ATTESTATION_ENV), 1)
        self.assertEqual(names.count(R.IMAGE_ATTESTATION_ENV), 1)

    def test_conflicting_attestations_refused(self):
        """Refuse attestations of code/image other than the reviewed input."""
        data = valid_input()
        data['environment'].append({
            'name': 'AIMMS_APPROVED_COMMIT_SHA',
            'value': 'f' * 40,
        })
        self.assert_refused(data, 'ATTESTATION_CONFLICT')
        data = valid_input()
        data['environment'].append({
            'name': 'AIMMS_APPROVED_IMAGE_DIGEST',
            'value': 'sha256:' + 'b' * 64,
        })
        self.assert_refused(data, 'ATTESTATION_CONFLICT')

    def test_malformed_attestation_values_refused(self):
        """Enforce strict SHA formats on both attestation values."""
        data = valid_input()
        data['environment'].append({
            'name': 'AIMMS_APPROVED_COMMIT_SHA',
            'value': 'not-a-commit',
        })
        self.assert_refused(data, 'BAD_ATTESTATION')
        data = valid_input()
        data['environment'].append({
            'name': 'AIMMS_APPROVED_COMMIT_SHA',
            'value': 'C' * 40,
        })
        self.assert_refused(data, 'BAD_ATTESTATION')
        data = valid_input()
        data['environment'].append({
            'name': 'AIMMS_APPROVED_IMAGE_DIGEST',
            'value': 'sha256:' + 'A' * 64,
        })
        self.assert_refused(data, 'BAD_ATTESTATION')
        data = valid_input()
        data['environment'].append({
            'name': 'AIMMS_APPROVED_IMAGE_DIGEST',
            'value': 'a' * 64,
        })
        self.assert_refused(data, 'BAD_ATTESTATION')

    def test_blank_attestation_values_refused(self):
        """A declared attestation must never be empty or whitespace."""
        data = valid_input()
        data['environment'].append({'name': 'AIMMS_APPROVED_COMMIT_SHA', 'value': ''})
        self.assert_refused(data, 'ATTESTATION_MISSING')
        data = valid_input()
        data['environment'].append({
            'name': 'AIMMS_APPROVED_IMAGE_DIGEST',
            'value': '  ',
        })
        self.assert_refused(data, 'ATTESTATION_MISSING')

    def test_derivation_refuses_missing_or_unusable_sources(self):
        """Never invent an attestation when the reviewed input cannot supply one."""
        with self.assertRaises(R.SpecError) as caught:
            R._derive_attestations({})
        self.assertEqual(caught.exception.code, 'ATTESTATION_MISSING')
        with self.assertRaises(R.SpecError) as caught:
            R._derive_attestations({
                'git_commit': 'c' * 40,
                'expected_image_digest': '',
            })
        self.assertEqual(caught.exception.code, 'ATTESTATION_MISSING')
        with self.assertRaises(R.SpecError) as caught:
            R._derive_attestations({'git_commit': '', 'expected_image_digest': DIGEST})
        self.assertEqual(caught.exception.code, 'ATTESTATION_MISSING')
        with self.assertRaises(R.SpecError) as caught:
            R._derive_attestations({
                'git_commit': 'nope',
                'expected_image_digest': DIGEST,
            })
        self.assertEqual(caught.exception.code, 'BAD_ATTESTATION')
        with self.assertRaises(R.SpecError) as caught:
            R._derive_attestations({
                'git_commit': 'c' * 40,
                'expected_image_digest': 'sha256:zz',
            })
        self.assertEqual(caught.exception.code, 'BAD_ATTESTATION')

    def test_example_declares_the_exact_attestation_names(self):
        """Keep the shipped template showing the reviewed attestation names."""
        with open(EXAMPLE, encoding='utf-8') as handle:
            example = json.load(handle)
        names = {entry['name'] for entry in example['environment']}
        self.assertIn('AIMMS_APPROVED_COMMIT_SHA', names)
        self.assertIn('AIMMS_APPROVED_IMAGE_DIGEST', names)

    def test_attestations_recorded_in_provenance(self):
        """Record the attestation names, values and their honest limitation."""
        spec = R.build_job_spec(valid_input())
        attestation = spec['provenance']['runtime_attestation']
        self.assertEqual(attestation['commit_env'], 'AIMMS_APPROVED_COMMIT_SHA')
        self.assertEqual(attestation['image_env'], 'AIMMS_APPROVED_IMAGE_DIGEST')
        self.assertEqual(attestation['commit_sha'], valid_input()['git_commit'])
        self.assertEqual(attestation['image_digest'], DIGEST)
        self.assertIn('operator declarations', attestation['note'])


class BackendCommandContract(unittest.TestCase):
    """Offline drift guards between renderer tables and backend declarations.

    These tests parse the tracked backend sources with stdlib ast only.
    They never import Django, never open a database and never execute a
    management command. If any of them fails, the backend contract moved:
    re-review the renderer tables, the runbook and this suite together.
    """

    def test_option_tables_exactly_match_backend_add_arguments(self):
        """Every declared flag of every command matches the backend parser."""
        self.assertEqual(len(R.ALLOWED_COMMANDS), 6)
        for command in R.ALLOWED_COMMANDS:
            path = os.path.join(COMMANDS_DIR, command + '.py')
            self.assertTrue(os.path.exists(path), f'missing backend command {command}')
            backend = declared_options(path)
            spec = R.OPTION_SPECS[command]
            rendered = {**spec['required'], **spec['optional']}
            self.assertEqual(
                set(rendered),
                set(backend),
                f'flag drift for {command}: renderer={sorted(rendered)} '
                f'backend={sorted(backend)}',
            )
            for flag, takes_value in backend.items():
                self.assertEqual(
                    rendered[flag] != 'bool',
                    takes_value,
                    f'flag kind drift for {command} {flag}',
                )

    def test_include_history_is_declared_by_apply_only(self):
        """The plan command has no --include-history flag; apply has it bare."""
        plan_flags = declared_options(
            os.path.join(COMMANDS_DIR, 'plan_demo_metrics.py')
        )
        apply_flags = declared_options(
            os.path.join(COMMANDS_DIR, 'apply_demo_metrics.py')
        )
        self.assertNotIn('--include-history', plan_flags)
        self.assertIn('--include-history', apply_flags)
        self.assertFalse(apply_flags['--include-history'])
        self.assertNotIn(
            '--include-history', R.OPTION_SPECS['plan_demo_metrics']['required']
        )
        self.assertNotIn(
            '--include-history', R.OPTION_SPECS['plan_demo_metrics']['optional']
        )
        self.assertEqual(
            R.OPTION_SPECS['apply_demo_metrics']['optional'].get('--include-history'),
            'bool',
        )

    def test_attestation_env_names_match_fingerprint_constants(self):
        """The renderer attestation names are the backend's exact constants."""
        constants = module_constants(FINGERPRINT)
        self.assertEqual(R.COMMIT_ATTESTATION_ENV, constants.get('CODE_IDENTITY_ENV'))
        self.assertEqual(R.IMAGE_ATTESTATION_ENV, constants.get('IMAGE_IDENTITY_ENV'))

    def test_rendered_env_carries_backend_attestation_names(self):
        """A rendered Job environment satisfies the backend name contract."""
        constants = module_constants(FINGERPRINT)
        spec = R.build_job_spec(valid_input())
        names = {
            e['name']
            for e in spec['job']['properties']['template']['containers'][0]['env']
        }
        self.assertIn(constants['CODE_IDENTITY_ENV'], names)
        self.assertIn(constants['IMAGE_IDENTITY_ENV'], names)

    def test_plan_body_history_flag_derives_from_mapping_approval(self):
        """The plan body records include_history from mapping approval."""
        found = False
        for node in ast.walk(parse_module(PLANNER)):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value == 'include_history':
                    self.assertIn(
                        'history_import_approved',
                        ast.dump(value),
                        'include_history no longer derives from '
                        'mapping.history_import_approved; re-review the '
                        'renderer and runbook history contract',
                    )
                    found = True
        self.assertTrue(
            found, 'planner no longer records include_history in the plan body'
        )

    def test_apply_refuses_history_without_plan_authorization(self):
        """Apply refuses --include-history when the plan body forbids it."""
        with open(APPLY_SERVICE, encoding='utf-8') as handle:
            source = handle.read()
        self.assertIn('HISTORY_NOT_APPROVED', source)
        self.assertIn("plan.get('include_history')", source)


if __name__ == '__main__':
    unittest.main()
