"""Mock-only diagnostic safety checks; no Django setup or database connection."""

import copy
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from django.conf import settings

settings.configure(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'original-probe-test',
        }
    },
    DATABASES={
        'default': {
            'NAME': 'inventree_dm_e2e_mock',
            'HOST': 'db',
            'ENGINE': 'django.db.backends.postgresql',
        }
    },
    MFA_ENABLED=True,
)
from django.core.cache import caches

TARGET = Path(__file__).with_name('demo_metrics_e2e_session_regression.py')


class DiagnosticSafetyTests(unittest.TestCase):
    """Exercise refusal and exception cleanup without touching a database."""

    def setUp(self):
        """Setup."""
        self.original_caches = copy.deepcopy(settings.CACHES)
        self.original_connection = caches['default']
        self.original_connection.set('restore-sentinel', 'present')
        self.original_connections = caches._connections
        self.original_cached_settings = caches.__dict__.get('settings')
        self.user = Mock()
        self.user_model = Mock()
        self.user_model.objects.filter.return_value.first.return_value = self.user
        self.setting = Mock()
        self.marker = Mock(pk=17, key='login_enforce_mfa', value='False')
        self.setting.objects.filter.return_value.first.return_value = self.marker
        self.setting.objects.filter.return_value.exists.return_value = True
        self.original_save = self.setting.save
        self.ready = types.ModuleType('InvenTree.ready')
        self.ready.isReadOnlyCommand = lambda: True
        self.original_readonly = self.ready.isReadOnlyCommand
        self.modules = {}
        for name in ('common', 'allauth', 'allauth.usersessions', 'InvenTree'):
            module = types.ModuleType(name)
            module.__path__ = []
            self.modules[name] = module
        models = types.ModuleType('common.models')
        models.InvenTreeSetting = self.setting
        sessions = types.ModuleType('allauth.usersessions.models')
        sessions.UserSession = Mock()
        self.modules.update({
            'common.models': models,
            'allauth.usersessions.models': sessions,
            'InvenTree.ready': self.ready,
        })
        self.modules['InvenTree'].ready = self.ready
        self.client = Mock()
        self.client.force_login.side_effect = RuntimeError('injected before requests')

    def tearDown(self):
        """Teardown."""
        # Keep each mock-only test independent even against the defective script.
        settings.CACHES = self.original_caches
        caches._connections = self.original_connections
        if self.original_cached_settings is not None:
            caches.__dict__['settings'] = self.original_cached_settings
        else:
            caches.__dict__.pop('settings', None)

    def run_probe(self, cold=False):
        """Run probe."""
        with (
            patch.dict(sys.modules, self.modules),
            patch.dict(
                'os.environ',
                {
                    'DM_E2E_DB': 'inventree_dm_e2e_mock',
                    'DM_E2E_COLD': '1' if cold else '0',
                },
            ),
            patch('django.contrib.auth.get_user_model', return_value=self.user_model),
            patch('django.test.Client', return_value=self.client),
            patch.object(sys, 'argv', ['diagnostic']),
        ):
            runpy.run_path(str(TARGET), run_name='__main__')

    def assert_cache_restored(self):
        """Assert cache restored."""
        self.assertEqual(settings.CACHES, self.original_caches)
        self.assertEqual(caches['default'].get('restore-sentinel'), 'present')
        self.assertEqual(caches['default']._cache, self.original_connection._cache)
        self.assertEqual(
            caches['default']._expire_info, self.original_connection._expire_info
        )
        self.assertIs(self.ready.isReadOnlyCommand, self.original_readonly)
        self.assertIs(self.setting.save, self.original_save)

    def test_missing_user_does_not_change_cache_configuration(self):
        """Test missing user does not change cache configuration."""
        self.user_model.objects.filter.return_value.first.return_value = None
        with self.assertRaises(SystemExit):
            self.run_probe()
        self.assert_cache_restored()

    def test_enabled_mfa_refusal_does_not_change_cache_configuration(self):
        """Test enabled mfa refusal does not change cache configuration."""
        self.marker.value = 'True'
        with self.assertRaises(SystemExit):
            self.run_probe(cold=True)
        self.assert_cache_restored()
        self.setting.objects.filter.return_value.delete.assert_not_called()

    def test_cold_exception_restores_exact_marker_object(self):
        """Test cold exception restores exact marker object."""
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            self.run_probe(cold=True)
        self.setting.objects.bulk_create.assert_called_once_with([self.marker])
        self.assert_cache_restored()

    def test_restore_failure_still_restores_cache_and_patches(self):
        """Test restore failure still restores cache and patches."""
        self.setting.objects.bulk_create.side_effect = RuntimeError(
            'injected restore failure'
        )
        with self.assertRaisesRegex(RuntimeError, 'restore failure'):
            self.run_probe(cold=True)
        self.assert_cache_restored()

    def test_missing_setup_marker_refuses_before_initialization_or_login(self):
        """Test missing setup marker refuses before initialization or login."""
        self.setting.objects.filter.return_value.first.return_value = None
        with self.assertRaises(SystemExit):
            self.run_probe()
        self.setting.build_default_values.assert_not_called()
        self.client.force_login.assert_not_called()
        self.assert_cache_restored()

    def test_non_postgres_engine_substring_is_not_accepted(self):
        """Test non postgres engine substring is not accepted."""
        with patch.dict(
            settings.DATABASES['default'], {'ENGINE': 'custom.notpostgresql'}
        ):
            with self.assertRaisesRegex(SystemExit, 'non-postgresql engine'):
                self.run_probe()
        self.user_model.objects.filter.assert_not_called()

    def test_wrong_host_refuses_before_orm(self):
        """Test wrong host refuses before orm."""
        with patch.dict(settings.DATABASES['default'], {'HOST': 'remote.example'}):
            with self.assertRaisesRegex(SystemExit, 'database host'):
                self.run_probe()
        self.user_model.objects.filter.assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
