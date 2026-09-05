"""CR-3: the PgBouncer-safe database settings are honoured from the environment."""

import os
from unittest import mock

from django.test import SimpleTestCase

from InvenTree.setting.db_backend import get_db_backend

_BASE_ENV = {
    'INVENTREE_DB_ENGINE': 'sqlite3',
    'INVENTREE_DB_NAME': '/tmp/aimms-db-backend-test.sqlite',
}


class DbBackendSettingsTest(SimpleTestCase):
    """``get_db_backend`` honours the PgBouncer-safe keys from the environment."""

    def _backend(self, **env):
        """Build the DATABASES dict from a controlled environment."""
        with mock.patch.dict(os.environ, {**_BASE_ENV, **env}, clear=False):
            return get_db_backend()

    def test_server_side_cursors_stay_enabled_by_default(self):
        """Absent env leaves the Django default in place."""
        self.assertFalse(self._backend().get('DISABLE_SERVER_SIDE_CURSORS'))

    def test_server_side_cursors_can_be_disabled_for_pgbouncer(self):
        """The PgBouncer trio lands as top-level DATABASES keys."""
        backend = self._backend(
            INVENTREE_DB_DISABLE_SERVER_SIDE_CURSORS='true',
            INVENTREE_DB_CONN_HEALTH_CHECKS='true',
            INVENTREE_DB_CONN_MAX_AGE='300',
        )
        # A top-level DATABASES key, never smuggled into OPTIONS.
        self.assertTrue(backend['DISABLE_SERVER_SIDE_CURSORS'])
        self.assertNotIn('DISABLE_SERVER_SIDE_CURSORS', backend['OPTIONS'])
        self.assertTrue(backend['CONN_HEALTH_CHECKS'])
        self.assertEqual(backend['CONN_MAX_AGE'], 300)
