"""The release gate must reject healthy-but-incompatible applications."""

import unittest
from urllib.parse import parse_qs, urlsplit

from check_aimms_release import SmokeError, check_release


class ReleaseSmokeTests(unittest.TestCase):
    """Check frontend provenance, authorization, and every metric contract."""

    def read(self, path, expected=200):
        """Return a valid server contract, with overridable failures."""
        if path == '/health/live':
            return {'status': 'alive'}
        if path == '/health/ai-ready':
            return {'status': 'ready'}
        if path.endswith('capabilities/'):
            return {
                'version': 1,
                'backend_commit': 'abc',
                'maintenance_metrics': 1,
                'risk_radar': False,
            }
        if path.endswith('build-info.json'):
            return {'commit': self.frontend_commit, 'dirty': False, 'ui_contract': 1}
        if path.endswith('/threads'):
            return {'threads': []}
        if path.endswith('/capability'):
            return {'enabled': False}
        if path.endswith('/proposals/'):
            return {'results': []}
        if path.endswith('/risk-scopes/'):
            self.assertEqual(expected, 404)
            return {}
        metric = parse_qs(urlsplit(path).query)['metric'][0]
        self.metrics.append(metric)
        return {'id': metric, 'version': 1, 'state': self.metric_state, 'records': []}

    def setUp(self):
        """Prepare a matching release with a scoped operator."""
        self.frontend_commit = 'abc'
        self.metric_state = 'ready'
        self.metrics = []

    def test_checks_every_metric_and_disabled_features(self):
        """Disabled voice/radar is valid while available metrics remain checked."""
        self.assertEqual(check_release(self.read, 'abc')['status'], 'passed')
        self.assertEqual(len(set(self.metrics)), 15)

    def test_rejects_mixed_frontend_even_when_both_health_probes_pass(self):
        """A successful health probe cannot hide a stale frontend bundle."""
        self.frontend_commit = 'old'
        with self.assertRaisesRegex(SmokeError, 'Frontend commit mismatch'):
            check_release(self.read, 'abc')

    def test_requires_an_operator_with_real_maintenance_scope(self):
        """Unavailable must not count as a successful metric verification."""
        self.metric_state = 'unavailable'
        with self.assertRaisesRegex(SmokeError, 'Maintenance scope unavailable'):
            check_release(self.read, 'abc')
