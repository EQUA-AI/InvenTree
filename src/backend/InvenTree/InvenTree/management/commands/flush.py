"""Django's flush command, cascading during PostgreSQL test runs.

Retired models keep their tables and foreign keys as compatibility storage
(``assets.0020`` retires the DemoMetrics ledger, whose rows reference
``tasks_workorder``). Django flushes only model tables, so PostgreSQL refuses
the TRUNCATE that ends every ``TransactionTestCase``, and the rows the failed
flush leaves behind break the next test. Under test the flush cascades into
those tables as well; everywhere else this is Django's command unchanged.
"""

from django.conf import settings
from django.core.management.commands.flush import Command as FlushCommand
from django.db import connections


class Command(FlushCommand):
    """Flush the database, cascading into retained tables during tests."""

    def handle(self, **options):
        """Request ``TRUNCATE ... CASCADE`` only for a PostgreSQL test run."""
        database = options['database']
        if settings.TESTING and connections[database].vendor == 'postgresql':
            options['allow_cascade'] = True
        return super().handle(**options)
