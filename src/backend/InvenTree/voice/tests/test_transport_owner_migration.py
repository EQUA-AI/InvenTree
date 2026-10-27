"""PostgreSQL upgrade and old-writer compatibility for transport ownership."""

import importlib
from unittest import skipUnless

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.state import ProjectState
from django.test import TransactionTestCase

from voice.models import VoiceSession


@skipUnless(connection.vendor == 'postgresql', 'Requires native PostgreSQL DDL')
class TransportOwnerMigrationTests(TransactionTestCase):
    """Apply the actual additive operation to an existing native session."""

    def test_upgrade_preserves_sessions_and_omitting_owner_uses_database_default(self):
        """Old application writers remain valid after the schema upgrade."""
        operation = importlib.import_module(
            'voice.migrations.0012_voicesession_transport_owner'
        ).Migration.operations[0]
        latest = ProjectState.from_apps(apps)
        previous = latest.clone()
        previous.remove_field('voice', 'voicesession', 'transport_owner')
        old_model = previous.apps.get_model('voice', 'VoiceSession')
        user = get_user_model().objects.create_user(username='old-voice-writer')
        fields = {
            'owner_id': user.pk,
            'thread_id': 'transport-owner-migration',
            'scope_key': 'site:fixture',
            'scope_hash': 'a' * 64,
            'policy_version': 'fixture',
        }
        with connection.schema_editor() as editor:
            operation.database_backwards('voice', editor, latest, previous)
        try:
            existing = old_model.objects.create(**fields)
        finally:
            with connection.schema_editor() as editor:
                operation.database_forwards('voice', editor, previous, latest)

        self.assertEqual(VoiceSession.objects.get(pk=existing.pk).transport_owner, '')
        self.assertEqual(VoiceSession.objects.get(pk=existing.pk).owner_id, user.pk)
        self.assertEqual(VoiceSession.objects.count(), 1)
        old_writer = old_model.objects.create(**fields)
        self.assertEqual(VoiceSession.objects.get(pk=old_writer.pk).transport_owner, '')
