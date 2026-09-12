"""Tests for the opt-in Cosmos emulator in the development stack.

The emulator is what lets the pumphouse connector be developed and tested with
no Azure account, so the properties that make it usable are worth pinning. Each
one here has a specific failure in mind rather than being a restatement of the
YAML: an unpinned image breaks CI on someone else's release schedule, a missing
profile slows the stack down for people who never touch Cosmos, and a renamed
service silently disables the local credential path.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from django.test import SimpleTestCase

from machine_health.connectors.cosmos_pumphouse import EMULATOR_HOSTS

# tests/ -> machine_health/ -> InvenTree/ -> backend/ -> src/ -> the repository
# root, where contrib/ lives alongside src/.
COMPOSE = (
    Path(__file__).resolve().parents[5]
    / 'contrib'
    / 'container'
    / 'dev-docker-compose.yml'
)

SERVICE = 'cosmos-emulator'


def compose() -> dict:
    """Parse the development compose file."""
    return yaml.safe_load(COMPOSE.read_text(encoding='utf-8'))


class EmulatorServiceTests(SimpleTestCase):
    """The emulator service definition."""

    def setUp(self):
        """Load the service under test."""
        self.services = compose()['services']
        self.emulator = self.services[SERVICE]

    def test_the_emulator_is_defined(self):
        """Without it, every Cosmos test would need the real account."""
        self.assertIn(SERVICE, self.services)

    def test_it_is_opt_in_behind_a_profile(self):
        """`docker compose up` must not get slower for unrelated work."""
        self.assertEqual(self.emulator['profiles'], ['cosmos'])

    def test_the_image_is_pinned_to_a_dated_tag(self):
        """A floating tag turns an unrelated pull request red overnight."""
        image = self.emulator['image']
        tag = image.rsplit(':', 1)[1]

        self.assertNotIn(tag, {'latest', 'stable', 'vnext-preview', 'vnext-latest'})
        self.assertTrue(tag.startswith('vnext-EN'), tag)

    def test_it_waits_on_a_real_readiness_probe(self):
        """A fixed sleep would be flaky on CI and wasteful on a laptop."""
        healthcheck = self.emulator['healthcheck']

        self.assertIn('/ready', ' '.join(healthcheck['test']))
        self.assertGreaterEqual(healthcheck['retries'], 20)

    def test_the_data_plane_port_is_published(self):
        """8081 is the SDK endpoint; without it the host cannot seed."""
        self.assertIn('8081:8081', self.emulator['ports'])


class EndpointTests(SimpleTestCase):
    """How the rest of the stack is told where the emulator is."""

    def setUp(self):
        """Load the shared environment block."""
        self.environment = compose()['services']['inventree-dev-server'][
            'environment'
        ]

    def test_the_default_endpoint_points_at_the_emulator_service(self):
        """Container-to-container, the hostname is the compose service name."""
        self.assertIn(SERVICE, self.environment['INVENTREE_COSMOS_ENDPOINT'])

    def test_the_endpoint_is_http_not_https(self):
        """The vNext emulator serves plain HTTP.

        Writing https here would fail in a way that looks like a certificate
        problem and send someone off installing a root CA they do not need.
        """
        endpoint = self.environment['INVENTREE_COSMOS_ENDPOINT']

        self.assertIn('http://', endpoint)
        self.assertNotIn('https://', endpoint)

    def test_the_emulator_hostname_is_one_the_connector_accepts(self):
        """A renamed service would silently disable the local key path.

        The connector accepts a key credential only against an emulator host. If
        the service were renamed without updating EMULATOR_HOSTS, every local
        read would fail with a configuration error that says nothing about the
        rename - so the two files are checked against each other here.
        """
        self.assertIn(SERVICE, EMULATOR_HOSTS)

    def test_every_cosmos_setting_can_be_overridden(self):
        """Nobody should have to edit a tracked file to use a real account."""
        for name in (
            'INVENTREE_COSMOS_ENDPOINT',
            'INVENTREE_COSMOS_DATABASE',
            'INVENTREE_COSMOS_CONTAINER',
            'COSMOS_EMULATOR_KEY',
        ):
            with self.subTest(setting=name):
                self.assertTrue(self.environment[name].startswith('${'))


class CredentialTests(SimpleTestCase):
    """The local key must not become a pattern for real accounts."""

    def test_the_worker_shares_the_servers_environment(self):
        """The poller runs in the worker, so it needs the same coordinates."""
        services = compose()['services']

        self.assertEqual(
            services['inventree-dev-worker']['environment'],
            services['inventree-dev-server']['environment'],
        )

    def test_no_cosmos_key_is_configured_for_a_real_account(self):
        """Only the emulator variable exists; a real account uses Entra ID.

        If a `COSMOS_KEY` or similar ever appears here it means someone has
        started authenticating to a real account with an account key, which
        would undo the read-only guarantee the Data Reader role provides.
        """
        environment = compose()['services']['inventree-dev-server']['environment']
        key_names = {
            name
            for name in environment
            if 'KEY' in name.upper() and 'COSMOS' in name.upper()
        }

        self.assertEqual(key_names, {'COSMOS_EMULATOR_KEY'})
