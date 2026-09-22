"""UI discovery must describe availability without granting data access."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings


class UICapabilitiesTests(TestCase):
    """Session authentication, disabled features, and private cache policy."""

    url = '/api/aichat/ui/capabilities/'

    def test_anonymous_discovery_is_denied(self):
        """Feature discovery is not an unauthenticated configuration export."""
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    @override_settings(
        AIMMS_RISK_RADAR_ENABLED=False, AIMMS_COMMAND_CENTER_ENABLED=True
    )
    def test_disabled_radar_also_disables_command_center(self):
        """Ordinary users can discover a contract without receiving data grants."""
        actor = get_user_model().objects.create_user(username='capability-reader')
        self.client.force_login(actor)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        body = response.json()
        self.assertEqual(body['version'], 1)
        self.assertFalse(body['risk_radar'])
        self.assertFalse(body['command_center'])
        self.assertEqual(body['maintenance_metrics'], 1)
        self.assertTrue(body['proposals']['session_required'])
        self.assertEqual(
            self.client.get(
                '/api/aichat/ui/maintenance-metrics/', {'metric': 'open'}
            ).status_code,
            403,
        )
        with override_settings(AIMMS_RISK_RADAR_ENABLED=True):
            self.assertTrue(self.client.get(self.url).json()['risk_radar'])
