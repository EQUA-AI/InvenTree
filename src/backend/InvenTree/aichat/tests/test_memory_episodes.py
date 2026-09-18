"""Authored PostgreSQL verified-closeout recall and final reauthorization cases."""

import os
from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.closeout_models import CloseoutAmendment
from tasks.models import WorkOrder, WorkOrderCloseout

from ai.core.analysis.scope import AnalysisScope, scope_to_payload
from aichat.services import memory_episodes, memory_recall
from aichat.services.threads import ThreadRepository
from assets.models import AssetMachine, Client, ClientScopeGrant
from repair.models import RepairPacket


@skipUnless(connection.vendor == 'postgresql', 'Episode annotations require PostgreSQL')
@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER=memory_episodes.DIAGNOSTIC_RESOLVER,
)
class MemoryEpisodeTests(TestCase):
    """Native source authority needs no newly inferred or confirmed memory fact."""

    def setUp(self):
        """One explicit machine, authorized client and verified closeout."""
        self.owner = get_user_model().objects.create_superuser(
            username='episode-owner', email='episode@example.test', password='fixture'
        )
        self.client = Client.objects.create(
            name='Episode fixture', code='episode-fixture'
        )
        ClientScopeGrant.objects.create(user=self.owner, client=self.client)
        self.machine = AssetMachine.objects.create(
            name='Fixture machine', client=self.client, model='Fixture model'
        )
        self.repo = ThreadRepository(self.owner.pk, 'site:main')
        self.thread, _ = self.repo.get_or_create()
        self.thread.analysis_scope = scope_to_payload(
            AnalysisScope(mode='explicit_assets', machine_ids=(self.machine.pk,))
        )
        self.thread.save(update_fields=['analysis_scope'])
        self.repo.append(
            self.thread.pk, role='user', content='Current diagnostic query'
        )
        self.work_order = WorkOrder.objects.create(
            title='Fixture repair', machine=self.machine
        )
        self.packet = RepairPacket.objects.create(
            machine=self.machine, work_order=self.work_order, status='closed'
        )
        self.closeout = WorkOrderCloseout.objects.create(
            work_order=self.work_order,
            cause='Worn seal',
            action='Replaced seal',
            result='Stable',
            verification_summary='Verified stable operation',
            completed_by=self.owner,
            completed_at=timezone.now(),
            verified_by=self.owner,
            verified_at=timezone.now(),
            content_hash='a' * 64,
        )
        self.config = SimpleNamespace(
            feature_semantic_memory_recall=True,
            aimms_memory_notice_version='memory-v1',
            memory_embedding_deployment='fixture',
            aimms_memory_topic_boost=0.0,
        )
        self.enterContext(
            mock.patch.object(memory_recall, 'get_settings', return_value=self.config)
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))

    def candidates(self):
        """The episode projection shares the history statement, even on turn one."""
        with self.assertNumQueries(1):
            window = self.repo.recall_window(
                self.thread.pk, limit=12, memory_query=True, episode_recall=True
            )
        return memory_episodes.materialize(list(window.episode_candidates))

    def test_verified_source_is_reauthorized_in_one_statement(self):
        """The final statement can carry repair and memory authority together."""
        rows = self.candidates()
        self.assertEqual([row['id'] for row in rows], [self.closeout.pk])
        with self.assertNumQueries(1):
            facts, episodes = memory_episodes.reauthorize(
                self.repo, self.thread.pk, [], rows
            )
        self.assertEqual(facts, [])
        self.assertEqual(episodes, rows)

    def test_amendment_appearing_after_candidate_read_discards_old_prose(self):
        """No immutable closeout revision can hide a newer governed correction."""
        rows = self.candidates()
        CloseoutAmendment.objects.create(
            closeout=self.closeout,
            changes={},
            base_content_hash=self.closeout.content_hash,
            reason='Fixture correction',
            requested_by=self.owner,
            status='applied',
            applied_at=timezone.now(),
            effective_snapshot={'closeout': {'cause': 'Misaligned seal'}},
            effective_snapshot_hash='b' * 64,
        )
        self.assertEqual(
            memory_episodes.reauthorize(self.repo, self.thread.pk, [], rows)[1], []
        )
        self.assertEqual(self.candidates()[0]['cause'], 'Misaligned seal')

    def test_access_loss_and_unverified_closeouts_abstain(self):
        """Client grants and verification are current authorizing fields."""
        rows = self.candidates()
        ClientScopeGrant.objects.filter(user=self.owner).delete()
        self.assertEqual(
            memory_episodes.reauthorize(self.repo, self.thread.pk, [], rows)[1], []
        )
        ClientScopeGrant.objects.create(user=self.owner, client=self.client)
        WorkOrderCloseout.objects.filter(pk=self.closeout.pk).update(
            verified_by=None, verified_at=None
        )
        self.assertEqual(self.candidates(), [])
