"""Consent and historical sharing regressions for deferred qualification."""

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from ai.core.analysis.scope import AnalysisScope, scope_to_payload
from aichat.models import (
    ChatThread,
    ChatThreadGrant,
    ClientAISettings,
    MemoryNoticeAcknowledgement,
    UserMemorySettings,
)
from aichat.services import memory_eligibility as service
from assets.models import Client


class MemoryEligibilityTests(TestCase):
    """No extraction, model calls or runtime settings changes are involved."""

    def setUp(self):
        """Prepare explicit enrollment for only one of two authorized clients."""
        self.owner = get_user_model().objects.create_user(username='memory-owner')
        self.other = get_user_model().objects.create_user(username='memory-other')
        self.client_a = Client.objects.create(name='Memory client A', code='memory-a')
        self.client_b = Client.objects.create(name='Memory client B', code='memory-b')
        self.thread = ChatThread.objects.create(
            owner=self.owner,
            scope_key='fixture',
            scope_hash='fixture',
            memory_mode='extract',
            analysis_scope=scope_to_payload(
                AnalysisScope(mode='all_authorized_assets')
            ),
        )
        self.config = SimpleNamespace(
            feature_semantic_memory_extract_shadow=True,
            feature_semantic_memory_recall=True,
            aimms_memory_default_mode='off',
            aimms_memory_notice_version='memory-v10',
        )
        self.enterContext(
            mock.patch.object(service, 'get_settings', return_value=self.config)
        )
        self.enterContext(
            mock.patch.object(
                service,
                'client_codes_for_actor',
                return_value=frozenset({'memory-a', 'memory-b'}),
            )
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        ClientAISettings.objects.create(
            client=self.client_a,
            memory_enabled=True,
            enabled_at=timezone.now() - timedelta(days=1),
            required_notice_version='memory-v2',
        )
        MemoryNoticeAcknowledgement.objects.create(
            user=self.owner, notice_version='memory-v10'
        )
        MemoryNoticeAcknowledgement.objects.filter(user=self.owner).update(
            acknowledged_at=timezone.now() - timedelta(days=1)
        )

    def test_enrollment_is_per_client_and_notice_versions_are_numeric(self):
        """A disabled second client does not silence eligible preferences."""
        result = service.evaluate_memory_eligibility(self.owner, self.thread)
        self.assertTrue(result.allowed)
        self.assertEqual(result.clients, frozenset({'memory-a'}))
        self.assertEqual(result.notice_version, 'memory-v10')
        ClientAISettings.objects.filter(client=self.client_a).update(
            required_notice_version='memory-v11'
        )
        self.assertFalse(
            service.evaluate_memory_eligibility(self.owner, self.thread).allowed
        )

    def test_owner_activity_opt_out_and_legacy_scope_fail_closed(self):
        """Owner state is read fresh rather than trusting a queued user instance."""
        self.assertFalse(
            service.evaluate_memory_eligibility(self.other, self.thread).allowed
        )
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertEqual(
            service.evaluate_memory_eligibility(self.owner, self.thread).reason,
            'inactive_owner',
        )
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=True)
        UserMemorySettings.objects.create(user=self.owner, opted_out=True)
        self.assertEqual(
            service.evaluate_memory_eligibility(self.owner, self.thread).reason,
            'opted_out',
        )
        UserMemorySettings.objects.filter(user=self.owner).update(opted_out=False)
        self.thread.analysis_scope = {}
        self.assertEqual(
            service.evaluate_memory_eligibility(self.owner, self.thread).reason,
            'no_authorized_clients',
        )

    def test_memory_off_stops_extraction_but_retains_recall_policy(self):
        """Turning learning off is not an implicit forget of confirmed facts."""
        self.thread.memory_mode = 'inherit'
        self.assertEqual(
            service.evaluate_memory_eligibility(self.owner, self.thread).reason,
            'memory_off',
        )
        self.assertTrue(
            service.evaluate_memory_eligibility(
                self.owner, self.thread, purpose='recall'
            ).allowed
        )
        self.config.feature_semantic_memory_recall = False
        self.assertEqual(
            service.evaluate_memory_eligibility(
                self.owner, self.thread, purpose='recall'
            ).reason,
            'feature_disabled',
        )

    def test_shared_windows_are_permanent_extraction_gaps(self):
        """Revocation restores future eligibility without mining the shared window."""
        now = timezone.now()
        row = ChatThreadGrant.objects.create(
            thread=self.thread, grantee=self.other, granted_by=self.owner
        )
        ChatThreadGrant.objects.filter(pk=row.pk).update(
            created_at=now - timedelta(hours=2)
        )
        self.assertEqual(
            service.evaluate_memory_eligibility(self.owner, self.thread).reason,
            'shared_thread',
        )
        self.assertEqual(
            service.evaluate_memory_eligibility(
                self.owner, self.thread, purpose='recall'
            ).reason,
            'shared_thread',
        )
        ChatThreadGrant.objects.filter(pk=row.pk).update(
            revoked_at=now - timedelta(hours=1)
        )
        self.assertEqual(
            service.evaluate_memory_eligibility(
                self.owner, self.thread, source_time=now - timedelta(minutes=90)
            ).reason,
            'shared_source_window',
        )
        self.assertTrue(
            service.evaluate_memory_eligibility(
                self.owner, self.thread, source_time=now
            ).allowed
        )
        self.assertTrue(
            service.evaluate_memory_eligibility(
                self.owner, self.thread, source_time=now - timedelta(hours=3)
            ).allowed
        )

    def test_acknowledgement_requires_current_version_and_is_idempotent(self):
        """Neither old notice text nor an inactive actor can create consent."""
        with self.assertRaises(ValueError):
            service.acknowledge_notice(self.owner, 'memory-v2')
        first = service.acknowledge_notice(self.owner, 'memory-v10')
        second = service.acknowledge_notice(self.owner, 'memory-v10')
        self.assertEqual(first.pk, second.pk)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        with self.assertRaises(ValueError):
            service.acknowledge_notice(self.owner, 'memory-v10')

    def test_preconsent_sources_are_not_backfilled(self):
        """A new acknowledgement or enrollment applies only to later input."""
        result = service.evaluate_memory_eligibility(
            self.owner, self.thread, source_time=timezone.now() - timedelta(days=2)
        )
        self.assertEqual(result.reason, 'notice_required')
        ClientAISettings.objects.filter(client=self.client_a).update(
            enabled_at=timezone.now()
        )
        result = service.evaluate_memory_eligibility(
            self.owner, self.thread, source_time=timezone.now() - timedelta(minutes=5)
        )
        self.assertEqual(result.reason, 'no_enrolled_clients')
