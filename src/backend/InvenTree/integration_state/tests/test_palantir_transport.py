"""Offline SDK-shaped worker tests; no real Foundry or client authorization."""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import venv
from dataclasses import replace
from pathlib import Path
from unittest import TestCase, mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from integration_state.palantir_transport import (
    Credentials,
    ObjectMapping,
    TransportConfig,
    TransportError,
    _invoke_worker,
    config_fingerprint,
    fetch_object,
    sdk_distribution,
    validate_config,
)

INCARNATION = '00000000-0000-4000-8000-000000000007'
CLIENT_INCARNATION = '2026-11-14T00:00:00+00:00'


def fixture_mapping(domain='machines'):
    """Explicit, distinct generated Python names for the synthetic SDK."""
    return ObjectMapping(
        domain,
        'Machine' if domain == 'machines' else 'Location',
        'native_pk',
        'client_pk',
        'client_birth',
        'incarnation',
        'namespace',
        'display_name',
    )


def fixture_config(
    *,
    mode='success',
    client=1,
    client_incarnation=CLIENT_INCARNATION,
    incarnation=INCARNATION,
    environment='pilot-synthetic',
):
    """Synthetic private data is carried through the real anonymous pipe."""
    return TransportConfig(
        origin='https://synthetic.example.test',
        environment='pilot-synthetic',
        distribution='aimms-synthetic-osdk',
        sdk_module='aimms_synthetic_osdk',
        sdk_version='2.1.0',
        mappings=(fixture_mapping(), fixture_mapping('locations')),
        secret_reference='synthetic-secret-reference',
        resolve_credentials=lambda _: Credentials(
            mode,
            json.dumps({
                'client': client,
                'client_incarnation': client_incarnation,
                'incarnation': incarnation,
                'environment': environment,
            }),
        ),
        authorize_fields=lambda *_: True,
        request_timeout=1,
        deadline=2,
    )


class WorkerFixture:
    """Install only a local SDK-shaped double in an owned disposable venv."""

    @classmethod
    def setUpClass(cls):
        """No pip, downloads, environment secret files or deployment services."""
        super().setUpClass()
        cls.scratch = tempfile.TemporaryDirectory(prefix='aimms-osdk-tests-')
        cls.addClassCleanup(cls.scratch.cleanup)
        root = Path(cls.scratch.name)
        venv.EnvBuilder(with_pip=False).create(root / 'venv')
        cls.python = str(root / 'venv/bin/python')
        site = (
            root
            / f'venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
        )
        package = site / 'aimms_synthetic_osdk'
        (package / 'ontology').mkdir(parents=True)
        fixture = Path(__file__).with_name('palantir_fixtures')
        shutil.copyfile(fixture / 'sdk.py', package / '__init__.py')
        shutil.copyfile(fixture / 'objects.py', package / 'ontology/objects.py')
        (package / 'ontology/__init__.py').write_text('')
        info = site / 'aimms_synthetic_osdk-2.1.0.dist-info'
        info.mkdir()
        (info / 'METADATA').write_text(
            'Metadata-Version: 2.1\nName: aimms-synthetic-osdk\nVersion: 2.1.0\n'
        )
        (info / 'RECORD').write_text('aimms_synthetic_osdk/__init__.py,,\n')

    def use_worker(self):
        """Replace only the interpreter; execute the unmodified product worker."""
        real_popen = subprocess.Popen
        self.workers = []

        def launch(command, **kwargs):
            process = real_popen([self.python, *command[1:]], **kwargs)
            self.workers.append(process)
            return process

        return mock.patch(
            'integration_state.palantir_transport.subprocess.Popen', side_effect=launch
        )


class PalantirConfigurationTests(TestCase):
    """Invalid config and offline checks must perform no credential/network I/O."""

    def test_explicit_mapping_and_configuration_are_accepted(self):
        """Use no broad property or domain inference."""
        config = fixture_config()
        self.assertIs(validate_config(config), config)
        self.assertNotEqual(
            config_fingerprint(config),
            config_fingerprint(replace(config, environment='other')),
        )
        self.assertNotIn('synthetic-secret', repr(config))
        self.assertNotIn('private', repr(Credentials('client', 'private')))

    def test_bad_origins_limits_imports_and_versions_are_rejected(self):
        """Prevent permissive origin/import/time-budget interpretations."""
        config = fixture_config()
        changes = [
            {'origin': origin}
            for origin in (
                'http://synthetic.example.test',
                'https://user:pass@synthetic.example.test',
                'https://synthetic.example.test/',
                'https://synthetic.example.test:443',
                'https://synthetic.example.test/path',
                'https://synthetic.example.test?x=1',
                'https://synthetic.example.test#fragment',
                'https://localhost',
            )
        ] + [
            {'request_timeout': True},
            {'deadline': 1},
            {'deadline': 61},
            {'sdk_module': 'os.path'},
            {'sdk_version': '>=2.0'},
            {'mappings': []},
            {'mappings': (config.mappings[0],) * 2},
            {'mappings': (replace(config.mappings[0], name='client_pk'),)},
            {'mappings': (replace(config.mappings[0], incarnation='_private'),)},
            {'mappings': (replace(config.mappings[0], domain='history'),)},
            {'resolve_credentials': None},
            {'authorize_fields': None},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(TransportError):
                validate_config(replace(config, **change))

    def test_exact_version_and_distribution_layout_required(self):
        """A similarly named import or different generated SDK is insufficient."""
        for version, files in [
            ('2.2.0', ['aimms_synthetic_osdk/__init__.py']),
            ('2.1.0', ['other/__init__.py']),
        ]:
            dist = mock.Mock(version=version, files=files)
            with (
                mock.patch('importlib.metadata.distribution', return_value=dist),
                self.assertRaises(TransportError),
            ):
                sdk_distribution(fixture_config())

    def test_offline_command_never_resolves_credentials_or_imports_sdk(self):
        """Configured metadata is not labeled as a live test."""
        config = replace(
            fixture_config(),
            resolve_credentials=mock.Mock(
                side_effect=AssertionError('no secrets offline')
            ),
        )
        dist = mock.Mock(version='2.1.0', files=['aimms_synthetic_osdk/__init__.py'])
        output = io.StringIO()
        with (
            override_settings(AIMMS_PALANTIR_TRANSPORT=config),
            mock.patch('importlib.metadata.distribution', return_value=dist),
            mock.patch(
                'importlib.import_module', side_effect=AssertionError('no SDK import')
            ),
        ):
            # Instantiate directly: call_command itself imports the command.
            from integration_state.management.commands.check_palantir_transport import (
                Command,
            )

            Command(stdout=output).handle(require_configured=True)
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                'status': 'configured',
                'domains': ['machines', 'locations'],
                'live_verified': False,
            },
        )
        config.resolve_credentials.assert_not_called()

    @override_settings(AIMMS_PALANTIR_TRANSPORT=None)
    def test_offline_disabled_is_valid_but_required_configuration_fails(self):
        """No remote initialization or implicit enablement at startup."""
        output = io.StringIO()
        call_command('check_palantir_transport', stdout=output)
        self.assertEqual(
            json.loads(output.getvalue()),
            {'status': 'disabled', 'live_verified': False},
        )
        with self.assertRaises(CommandError):
            call_command('check_palantir_transport', require_configured=True)


class PalantirWorkerTests(WorkerFixture, TestCase):
    """Exercise the actual isolated process, package loader and SDK calls."""

    def read(self, config=None):
        """Use a fixed already-authorized synthetic target, no native claim."""
        config = config or fixture_config()
        return fetch_object(
            config,
            config.mappings[0],
            pk=7,
            client_pk=1,
            client_incarnation=CLIENT_INCARNATION,
            incarnation=INCARNATION,
        )

    def test_real_worker_projection_secret_environment_and_cleanup(self):
        """Drop noisy SDK output, unapproved properties and inherited secrets."""
        with (
            self.use_worker(),
            mock.patch.dict(
                os.environ,
                {
                    'AIMMS_TEST_INHERITED_SECRET': 'private',
                    'HTTPS_PROXY': 'https://proxy.example.test',
                },
            ),
        ):
            row = self.read()
        self.assertEqual(row, {'pk': 7, 'client': 1, 'name': 'Synthetic remote asset'})
        self.assertEqual(len(self.workers), 1)
        self.assertIsNotNone(self.workers[0].returncode)

    def test_integer_primary_key_is_supported_explicitly(self):
        """No runtime key coercion heuristics from source metadata."""
        config = fixture_config(mode='integer_key')
        config = replace(
            config, mappings=(replace(config.mappings[0], key_type='int'),)
        )
        with self.use_worker():
            self.assertEqual(self.read(config)['pk'], 7)

    def test_wrong_identity_owner_incarnation_environment_and_types_fail(self):
        """Never disclose another identity's selected data as the requested row."""
        cases = [
            fixture_config(mode='wrong_pk'),
            fixture_config(mode='string_client'),
            fixture_config(client=2),
            fixture_config(client_incarnation='different-client-lifecycle'),
            fixture_config(incarnation='different'),
            fixture_config(environment='other-environment'),
        ]
        for config in cases:
            with (
                self.subTest(config=config),
                self.use_worker(),
                self.assertRaises(TransportError),
            ):
                self.read(config)

    def test_sdk_errors_are_sanitized_and_never_retried_by_parent(self):
        """Even exception URLs/tokens must not enter the parent error message."""
        with self.use_worker(), self.assertRaises(TransportError) as raised:
            self.read(fixture_config(mode='denied'))
        self.assertEqual(str(raised.exception), 'Palantir read refused: unavailable')
        self.assertEqual(len(self.workers), 1)

    def test_sdk_timeout_has_fixed_code(self):
        """A timeout carries no original provider exception detail."""
        with self.use_worker(), self.assertRaises(TransportError) as raised:
            self.read(fixture_config(mode='timeout'))
        self.assertEqual(raised.exception.code, 'timeout')

    def test_hard_deadline_kills_and_reaps_a_hung_sdk(self):
        """The outer deadline includes SDK initialization and authentication."""
        for mode in ('slow', 'auth_slow'):
            started = time.monotonic()
            with (
                self.subTest(mode=mode),
                self.use_worker(),
                self.assertRaises(TransportError) as raised,
            ):
                self.read(fixture_config(mode=mode))
            self.assertEqual(raised.exception.code, 'timeout')
            self.assertLess(time.monotonic() - started, 6)
            self.assertEqual(self.workers[0].returncode, -9)

    def test_oversized_output_is_refused(self):
        """Do not expose truncated or partial source data."""
        with self.use_worker(), self.assertRaises(TransportError) as raised:
            self.read(fixture_config(mode='oversize'))
        self.assertEqual(raised.exception.code, 'budget')

    def test_pinned_generated_package_must_be_installed_in_worker(self):
        """Never install dependencies or silently switch package versions."""
        with self.use_worker(), self.assertRaises(TransportError):
            self.read(replace(fixture_config(), sdk_version='2.2.0'))

    def test_unknown_credential_result_and_failures_are_private(self):
        """No worker dispatch on a failed/invalid secret resolution."""
        for resolver in (
            lambda _: {'client_secret': 'private'},
            mock.Mock(side_effect=RuntimeError('private')),
        ):
            with (
                mock.patch(
                    'integration_state.palantir_transport._invoke_worker'
                ) as invoke,
                self.assertRaises(TransportError),
            ):
                self.read(replace(fixture_config(), resolve_credentials=resolver))
            invoke.assert_not_called()

    def test_secret_resolver_is_called_again_for_each_read(self):
        """Credential rotation is not hidden by a process/token cache."""
        config = fixture_config()
        resolver = mock.Mock(wraps=config.resolve_credentials)
        config = replace(config, resolve_credentials=resolver)
        with self.use_worker():
            self.read(config)
            self.read(config)
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(len(self.workers), 2)

    def test_worker_refuses_oversized_input_before_dispatch(self):
        """Caller-side pipe budget applies before any process launch."""
        with mock.patch('subprocess.Popen') as popen, self.assertRaises(TransportError):
            _invoke_worker({'data': 'x' * 8192}, 2)
        popen.assert_not_called()

    def test_busy_worker_budget_refuses_before_secret_resolution(self):
        """At most four credential/SDK reads run in each server process."""
        from integration_state.palantir_transport import _WORKER_SLOTS

        resolver = mock.Mock(side_effect=AssertionError('no secret access when busy'))
        acquired = 0
        try:
            while _WORKER_SLOTS.acquire(blocking=False):
                acquired += 1
            with self.assertRaises(TransportError) as raised:
                self.read(replace(fixture_config(), resolve_credentials=resolver))
            self.assertEqual(raised.exception.code, 'budget')
            resolver.assert_not_called()
        finally:
            for _ in range(acquired):
                _WORKER_SLOTS.release()
