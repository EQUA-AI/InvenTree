"""Consent/control regressions prepared for the final qualification period."""

import os
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.test import APIRequestFactory, force_authenticate

from aichat.memory_api import MemoryNoticeView, MemoryOptOutView
from aichat.models import ChatThreadGrant, MemoryExtractionClaim
from aichat.services import memory_controls as service
from aichat.services.threads import InvalidBoundary, ThreadNotFound, ThreadRepository


class MemoryControlsTests(TestCase):
    """Choice, scope and stale-work exclusions need no remote services."""

    def setUp(self):
        """Create two independent owner boundaries and an off-by-default config."""
        self.owner = get_user_model().objects.create_user(username='controls-owner')
        self.other = get_user_model().objects.create_user(username='controls-other')
        self.repository = ThreadRepository(actor=self.owner, scope_key='site:main')
        self.thread, _ = self.repository.get_or_create()
        self.config = SimpleNamespace(
            aimms_memory_default_mode='off',
            aimms_memory_notice_version='memory-v1',
            feature_semantic_memory_extract_shadow=False,
            feature_semantic_memory_recall=False,
        )
        self.enterContext(
            mock.patch.object(service, 'get_settings', return_value=self.config)
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))

    def test_mode_change_skips_prior_input_and_revokes_work_lease(self):
        """Enabling learning never silently backfills the earlier conversation."""
        self.thread.next_sequence = 17
        self.thread.save(update_fields=['next_sequence'])
        claim = MemoryExtractionClaim.objects.create(
            thread=self.thread, through_sequence=16
        )
        changed = service.set_thread_mode(self.repository, self.thread.pk, 'extract')
        self.assertEqual(changed.memory_through_sequence, 16)
        claim.refresh_from_db()
        self.assertEqual(claim.state, 'skipped')
        with mock.patch.object(service, 'withdraw_proposals_for_thread') as withdraw:
            service.set_thread_mode(self.repository, self.thread.pk, 'off')
            withdraw.assert_called_once()

    def test_mode_change_requires_full_owner_scope(self):
        """A known thread id cannot cross owner or server scope boundaries."""
        for repository in (
            ThreadRepository(actor=self.other, scope_key='site:main'),
            ThreadRepository(actor=self.owner, scope_key='site:other'),
        ):
            with self.assertRaises(ThreadNotFound):
                service.set_thread_mode(repository, self.thread.pk, 'extract')
        with self.assertRaises(InvalidBoundary):
            service.set_thread_mode(self.repository, self.thread.pk, 'invalid')

    def test_shared_thread_cannot_enable_learning(self):
        """Shared conversations remain excluded even when the owner changes choice."""
        ChatThreadGrant.objects.create(
            thread=self.thread, grantee=self.other, granted_by=self.owner
        )
        with self.assertRaises(InvalidBoundary):
            service.set_thread_mode(self.repository, self.thread.pk, 'extract')

    def test_api_requires_exact_json_boolean(self):
        """A string cannot accidentally consent or withdraw consent by truthiness."""
        request = APIRequestFactory().put(
            '/memory/opt-out/', {'opted_out': 'false'}, format='json'
        )
        force_authenticate(request, self.owner)
        response = MemoryOptOutView.as_view()(request)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_unknown_notice_copy_cannot_be_acknowledged(self):
        """An environment version alone never substitutes for displayed text."""
        request = APIRequestFactory().post(
            '/memory/notice/', {'notice_version': 'memory-v99'}, format='json'
        )
        force_authenticate(request, self.owner)
        response = MemoryNoticeView.as_view()(request)
        self.assertEqual(response.status_code, 400)

    def test_status_exposes_only_owner_choice(self):
        """Status is default-off and contains no clients or provider configuration."""
        result = service.memory_status(self.owner)
        self.assertFalse(result['opted_out'])
        self.assertFalse(result['acknowledged'])
        self.assertFalse(result['extraction_enabled'])
        self.assertIn('abuse monitoring', result['notice_text'])
        self.assertNotIn('clients', result)
