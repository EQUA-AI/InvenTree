"""Deferred bounded-browser-export contracts; no model/provider calls."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from asgiref.sync import async_to_sync
from fastapi import HTTPException

from ai.core import app as ai_app
from ai.core.auth import AIPrincipal
from aichat.models import ChatThread
from aichat.services import ThreadRepository
from aichat.services import transcript_export as exporter


@override_settings(FEATURE_THREAD_SHARING=True)
class BrowserTranscriptExportTests(TestCase):
    """Artifacts are private, bounded, owner-bound and complete before delivery."""

    def setUp(self):
        """Create independent principals without executing lifecycle mutations."""
        users = get_user_model().objects
        self.owner = users.create_user(username='download-owner')
        self.other = users.create_user(username='download-other')
        from aichat.tests.memory_scope_fixtures import grant_shared_fixture_client

        grant_shared_fixture_client(self, self.owner, self.other)
        self.repo = ThreadRepository(self.owner.pk, 'site:main')
        self.principal = AIPrincipal(
            subject=f'user:{self.owner.pk}',
            actor=f'user:{self.owner.pk}',
            user_pk=str(self.owner.pk),
            username=self.owner.username,
            authentication_method='session',
            scope='site:main',
            policy_version='test',
            is_staff=False,
            is_superuser=False,
        )

    def transcript(self, repository=None, text='Private café 機械'):
        """Create stored text including multibyte Unicode and private metadata."""
        repository = repository or self.repo
        thread, _ = repository.get_or_create(title='Transcript fixture')
        repository.append(
            thread.pk, role='user', content=text, metadata={'omit': 'RAW_METADATA'}
        )
        return thread

    def test_api_download_uses_principal_and_explicit_projection(self):
        """Shared and other-scope data cannot enter a browser export."""
        own = self.transcript()
        foreign_repo = ThreadRepository(self.other.pk, 'site:main')
        foreign = self.transcript(foreign_repo, text='FOREIGN_CONTENT')
        foreign_repo.share(foreign.pk, grantee_id=self.owner.pk)
        self.transcript(
            ThreadRepository(self.owner.pk, 'site:other'), text='OTHER_SCOPE_CONTENT'
        )
        with mock.patch.object(ai_app, '_principal', return_value=self.principal):
            response = async_to_sync(ai_app.export_owned_transcripts)()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(
            response.headers['Content-Disposition'],
            'attachment; filename="aimms-conversations.json"',
        )
        records = json.loads(response.body)['records']
        self.assertEqual(records[0]['owner_id'], str(self.owner.pk))
        self.assertEqual(records[-1]['threads'], 1)
        self.assertEqual(records[-1]['messages'], 1)
        self.assertEqual(records[2]['thread_id'], own.pk)
        self.assertEqual(records[2]['content'], 'Private café 機械')
        for hidden in ('FOREIGN_CONTENT', 'OTHER_SCOPE_CONTENT', 'RAW_METADATA'):
            self.assertNotIn(hidden, response.body.decode())

    def test_utf8_byte_limit_refuses_whole_artifact(self):
        """The cap counts serialized UTF-8, not Python character count."""
        self.transcript()
        payload = exporter.browser_transcript_export(self.repo)
        # Fix the generator's timestamps so exact serialized lengths match.
        rows = json.loads(payload)['records']
        with mock.patch.object(
            self.repo, 'export_transcript_records', return_value=iter(rows)
        ):
            with mock.patch.object(exporter, 'MAX_EXPORT_BYTES', len(payload) - 1):
                with self.assertRaises(exporter.ExportTooLargeError):
                    exporter.browser_transcript_export(self.repo)
        with mock.patch.object(
            self.repo, 'export_transcript_records', return_value=iter(rows)
        ):
            with mock.patch.object(exporter, 'MAX_EXPORT_BYTES', len(payload)):
                self.assertEqual(exporter.browser_transcript_export(self.repo), payload)

    def test_record_limit_counts_manifest_and_completion(self):
        """An empty export is complete; oversized output is never truncated."""
        with mock.patch.object(exporter, 'MAX_EXPORT_RECORDS', 2):
            self.assertEqual(
                json.loads(exporter.browser_transcript_export(self.repo))['records'][
                    -1
                ]['threads'],
                0,
            )
            self.transcript()
            with self.assertRaises(exporter.ExportTooLargeError):
                exporter.browser_transcript_export(self.repo)

    def test_missing_completion_bad_counts_and_trailing_records_refuse(self):
        """A half-produced generator cannot be mistaken for a downloadable file."""
        self.transcript()
        rows = list(self.repo.export_transcript_records())
        for broken in (
            rows[:-1],
            [*rows, rows[1]],
            [*rows[:-1], {**rows[-1], 'messages': 9}],
        ):
            with self.subTest(length=len(broken)):
                with mock.patch.object(
                    self.repo, 'export_transcript_records', return_value=iter(broken)
                ):
                    with self.assertRaises(exporter.ExportIncompleteError):
                        exporter.browser_transcript_export(self.repo)

    def test_deactivation_during_read_cancels_response(self):
        """A disabled account must not receive buffered text after the final check."""
        self.transcript()
        rows = list(self.repo.export_transcript_records())

        def deactivate_during_read(**kwargs):
            yield rows[0]
            get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
            yield from rows[1:]

        with mock.patch.object(
            self.repo, 'export_transcript_records', side_effect=deactivate_during_read
        ):
            with self.assertRaises(exporter.ExportAccessError):
                exporter.browser_transcript_export(self.repo)
        with self.assertRaises(exporter.ExportAccessError):
            exporter.browser_transcript_export(self.repo)
        self.assertTrue(ChatThread.objects.exists())

    def test_api_errors_never_include_store_or_transcript_contents(self):
        """Limits and store failures return safe errors, not partial responses."""
        for failure, status in (
            (exporter.ExportTooLargeError('PRIVATE'), 413),
            (exporter.ExportAccessError('PRIVATE'), 403),
            (RuntimeError('PRIVATE'), 503),
        ):
            with mock.patch.object(ai_app, '_principal', return_value=self.principal):
                with mock.patch.object(
                    ai_app, 'browser_transcript_export', side_effect=failure
                ):
                    with self.assertRaises(HTTPException) as caught:
                        async_to_sync(ai_app.export_owned_transcripts)()
            self.assertEqual(caught.exception.status_code, status)
            self.assertNotIn('PRIVATE', caught.exception.detail)
            self.assertEqual(
                caught.exception.headers['Cache-Control'], 'private, no-store'
            )
