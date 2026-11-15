"""Django test resets for retained tables and installed source capture.

Retired models keep their tables and foreign keys as compatibility storage
(``assets.0020`` retires the DemoMetrics ledger, whose rows reference
``tasks_workorder``). Django flushes only model tables, so PostgreSQL refuses
the TRUNCATE that ends every ``TransactionTestCase``, and the rows the failed
flush leaves behind break the next test. Under PostgreSQL tests the flush
cascades into those tables. SQLite's DELETE reset temporarily suspends verified
S5 triggers so the reset cannot create new captured rows while emptying tables.
Other calls retain Django's behavior.
"""

from django.apps import apps
from django.conf import settings
from django.core.management.commands.flush import Command as FlushCommand
from django.db import connections


class Command(FlushCommand):
    """Flush the database, cascading into retained tables during tests."""

    def handle(self, **options):
        """Keep native test resets from leaving retained rows or capture events."""
        database = options['database']
        connection = connections[database]
        if settings.TESTING and connection.vendor == 'postgresql':
            options['allow_cascade'] = True
        if (
            settings.TESTING
            and connection.vendor == 'sqlite'
            and apps.is_installed('integration_state')
        ):
            from integration_state.capture import (
                capture_is_installed,
                install_capture,
                uninstall_capture,
            )

            # SQLite implements flush as DELETE. Its installed capture triggers
            # can create state/events after those tables have already been
            # cleared, leaving deferred foreign keys at commit. PostgreSQL's
            # native TRUNCATE reset does not fire these DELETE triggers.
            # Suspend only the verified S5 catalogue during a test reset, then
            # restore it even when reset fails. Ordinary writes and non-test
            # flush calls retain their existing capture/authority behavior.
            if capture_is_installed(connection):
                uninstall_capture(connection)
                try:
                    return super().handle(**options)
                finally:
                    install_capture(connection)
        return super().handle(**options)
