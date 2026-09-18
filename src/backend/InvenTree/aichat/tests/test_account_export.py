"""Authored account projection and partial-artifact regression cases."""

import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from aichat.email_models import ConnectedMailbox
from aichat.models import RetrievalMiss
from aichat.services import account_export


class AccountExportTests(TestCase):
    """Only explicit projections enter a private, complete artifact."""

    def test_owner_projection_excludes_secrets_and_other_owner_queries(self):
        """Even owned credentials/options are not account-export fields."""
        owner = get_user_model().objects.create_user(username='export-owner')
        other = get_user_model().objects.create_user(username='export-other')
        ConnectedMailbox.objects.create(
            owner=owner,
            name='Fixture',
            provider='recording',
            address='fixture@example.invalid',
            encrypted_credentials='DO-NOT-EXPORT',
            options={'private': 'DO-NOT-EXPORT'},
        )
        RetrievalMiss.objects.create(user=owner, query='OWNED')
        RetrievalMiss.objects.create(user=other, query='FOREIGN')
        with (
            mock.patch.object(
                account_export.ThreadRepository,
                'export_transcript_records',
                return_value=iter(()),
            ),
            mock.patch.object(
                account_export.memory_reads,
                'list_facts',
                return_value={'results': [], 'next_cursor': None},
            ),
        ):
            rows = list(account_export.records(owner.pk, scope_key='fixture'))
        encoded = json.dumps(rows, default=str)
        self.assertIn('OWNED', encoded)
        self.assertNotIn('FOREIGN', encoded)
        self.assertNotIn('DO-NOT-EXPORT', encoded)
        self.assertNotIn('password', encoded)
        self.assertFalse(rows[-1]['complete_account_export'])

    def test_incomplete_file_is_removed_and_existing_file_never_overwritten(self):
        """Stream failure cannot leave a success-looking partial account artifact."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'export.jsonl'
            with (
                mock.patch.object(
                    account_export, 'records', return_value=iter([{'type': 'manifest'}])
                ),
                self.assertRaises(CommandError),
            ):
                call_command(
                    'ai_export_account',
                    user_id=1,
                    scope_key='fixture',
                    output=str(output),
                    stdout=StringIO(),
                )
            self.assertFalse(output.exists())
            output.write_text('KEEP')
            with self.assertRaises(CommandError):
                call_command(
                    'ai_export_account',
                    user_id=1,
                    scope_key='fixture',
                    output=str(output),
                    stdout=StringIO(),
                )
            self.assertEqual(output.read_text(), 'KEEP')
