"""Proposed repair scopes preserve case, evidence and pending authority."""

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings

from rest_framework.test import APIRequestFactory, force_authenticate

from common.models import Attachment
from repair.models import PacketStatus
from tasks.models import WorkOrderCommand, WorkOrderLifecycle
from tasks.repair_scope_draft_api import RepairScopeDraft
from tasks.services.repair_scope_drafts import (
    EVENT_TYPE,
    RepairScopeDraftError,
    read_repair_scope_draft,
    save_repair_scope_draft,
)
from tasks.services.work_orders import (
    IdempotencyConflict,
    StaleVersion,
    WorkOrderScopeError,
)
from tasks.tests.test_pump08_exercise import Pump08ExerciseTest


class DraftFixture:
    """Build an actual native event-owned exercise with prepared E03."""

    def setUp(self):
        """Verify setUp."""
        Pump08ExerciseTest.setUp(self)
        self.work.refresh_from_db()
        self.packet = self.work.repair_packet
        registry = self.work.events.get(event_type='EXERCISE_EVIDENCE_ATTACHED')
        self.e03_id = registry.metadata['result_metadata']['evidence']['P08-TEMP-E03'][
            'id'
        ]
        self.manual = Attachment.objects.create(
            model_type='assetmachine',
            model_id=self.machine.pk,
            upload_user=self.actor,
            comment='WEG related motor reference; applicability unverified',
            attachment=ContentFile(
                b'%PDF-1.4\nNative test reference\n%%EOF', name='motor-reference.pdf'
            ),
        )

    def payload(self):
        """Verify payload."""
        self.work.refresh_from_db()
        return {
            'expected_version': self.work.lifecycle_version,
            'idempotency_key': uuid.uuid4().hex,
            'machine_id': self.machine.pk,
            'repair_packet_id': self.packet.pk,
            'source_event_id': self.source.pk,
            'component_code': self.component.code,
            'e03_attachment_id': self.e03_id,
            'manual_attachment_ids': [self.manual.pk],
            'scope': {
                'suspected_cause': 'Restricted cooling is the leading hypothesis.',
                'steps': [
                    'Confirm the fitted cooler.',
                    'Use an approved cleaning method.',
                ],
                'tools_and_supplies': [
                    'Inspection light',
                    'Approved cleaning supplies',
                ],
                'required_ppe': ['Site-required PPE'],
                'isolation_checks': [
                    'Obtain site authorization and verify energy control.'
                ],
                'post_work_checks': ['Plan later temperature and stability checks.'],
                'replacement_parts': [],
                'parts_decision': 'None justified by E03; reassess if damage is found.',
                'open_questions': [
                    'Installed cooler type',
                    'Measurement path validity',
                ],
            },
        }

    def save(self, payload=None, actor=None):
        """Verify save."""
        return save_repair_scope_draft(
            work_order_id=self.work.pk,
            actor=actor or self.actor,
            **(payload or self.payload()),
        )

    def request(self, method, data=None, actor=True):
        """Verify request."""
        factory = APIRequestFactory()
        request = getattr(factory, method)(
            '/repair-scope-draft/', data or {}, format='json'
        )
        if actor:
            force_authenticate(request, self.actor)
        return RepairScopeDraft.as_view()(request, pk=self.work.pk)


@override_settings(
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_TEST_ALARMS_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
class RepairScopeDraftTest(DraftFixture, TestCase):
    """Real native persistence and strict rejection of unbound proposals."""

    def test_durable_receipt_and_readback_preserve_pending_authority(self):
        """Verify durable receipt and readback preserve pending authority."""
        original = (self.work.lifecycle_status, self.packet.status, self.source.status)
        version = self.work.lifecycle_version
        receipt = self.save()
        self.work.refresh_from_db()
        self.packet.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(receipt['id'], self.work.events.get(event_type=EVENT_TYPE).pk)
        self.assertEqual(receipt['actor_id'], self.actor.pk)
        self.assertEqual(receipt['revision'], 1)
        self.assertEqual(self.work.lifecycle_version, version + 1)
        self.assertEqual(receipt['status'], 'proposed')
        self.assertEqual(receipt['scope']['replacement_parts'], [])
        self.assertTrue(receipt['sources']['e03']['prepared'])
        self.assertEqual(receipt['sources']['e03']['id'], self.e03_id)
        self.assertEqual(len(receipt['sources']['manuals'][0]['sha256']), 64)
        self.assertEqual(
            receipt['sources']['manuals'][0]['applicability'],
            'related_reference_unverified',
        )
        self.assertEqual(
            receipt['gates'],
            {
                'supervisor_review': 'pending',
                'site_safety_authorization': 'pending',
                'physical_execution': 'not_started',
                'post_repair_verification': 'not_started',
            },
        )
        self.assertEqual(
            (self.work.lifecycle_status, self.packet.status, self.source.status),
            original,
        )
        self.assertEqual(
            read_repair_scope_draft(self.work.pk, self.actor)['draft'], receipt
        )

    def test_duplicate_command_returns_original_receipt_once(self):
        """Verify duplicate command returns original receipt once."""
        payload = self.payload()
        first = self.save(payload)
        self.assertEqual(self.save(payload), first)
        self.assertEqual(self.work.events.filter(event_type=EVENT_TYPE).count(), 1)
        self.assertEqual(
            WorkOrderCommand.objects.filter(
                work_order=self.work, idempotency_key=payload['idempotency_key']
            ).count(),
            1,
        )

    def test_changed_payload_cannot_reuse_command_key(self):
        """Verify changed payload cannot reuse command key."""
        payload = self.payload()
        self.save(payload)
        payload['scope']['suspected_cause'] = 'Another proposed hypothesis.'
        with self.assertRaises(IdempotencyConflict):
            self.save(payload)

    def test_reviewed_source_hashes_are_checked_inside_atomic_save(self):
        """Verify reviewed source hashes are checked inside atomic save."""
        with self.assertRaises(RepairScopeDraftError):
            save_repair_scope_draft(
                work_order_id=self.work.pk,
                actor=self.actor,
                expected_source_hashes={
                    'e03': {'id': self.e03_id, 'sha256': '0' * 64},
                    'manuals': [],
                },
                **self.payload(),
            )
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_stale_work_version_does_not_save(self):
        """Verify stale work version does not save."""
        payload = self.payload()
        payload['expected_version'] += 1
        with self.assertRaises(StaleVersion):
            self.save(payload)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_new_revision_requires_current_version_and_keeps_first_snapshot(self):
        """Verify new revision requires current version and keeps first snapshot."""
        first = self.save()
        payload = self.payload()
        payload['scope']['steps'].append('Review the draft with the supervisor.')
        second = self.save(payload)
        self.assertEqual(second['revision'], 2)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(len(first['scope']['steps']), 2)
        self.assertEqual(
            read_repair_scope_draft(self.work.pk, self.actor)['draft'], second
        )

    def test_wrong_case_machine_packet_event_and_component_are_rejected(self):
        """Verify wrong case machine packet event and component are rejected."""
        for field in (
            'machine_id',
            'repair_packet_id',
            'source_event_id',
            'e03_attachment_id',
        ):
            payload = self.payload()
            payload[field] += 10000
            with self.subTest(field=field), self.assertRaises(RepairScopeDraftError):
                self.save(payload)
        payload = self.payload()
        payload['component_code'] = 'WRONG-COMPONENT'
        with self.assertRaises(RepairScopeDraftError):
            self.save(payload)

    def test_no_role_and_inactive_actors_cannot_write(self):
        """Verify no role and inactive actors cannot write."""
        stranger = get_user_model().objects.create_user(username='no-draft-role')
        with self.assertRaises(PermissionDenied):
            self.save(actor=stranger)
        self.actor.is_active = False
        self.actor.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):
            self.save()

    def test_native_work_change_role_suffices_without_superuser(self):
        """Verify native work change role suffices without superuser."""
        actor = get_user_model().objects.create_user(username='draft-technician')
        group = Group.objects.create(name='Draft technicians')
        rules = group.rule_sets.get(name='work_order')
        rules.can_view = True
        rules.can_change = True
        rules.save()
        actor.groups.add(group)
        receipt = self.save(actor=actor)
        self.assertEqual(receipt['actor_id'], actor.pk)

    def test_prepared_evidence_must_retain_original_bytes(self):
        """Verify prepared evidence must retain original bytes."""
        evidence = Attachment.objects.get(pk=self.e03_id)
        evidence.attachment.save('changed-e03.md', ContentFile(b'Changed finding'))
        with self.assertRaises(RepairScopeDraftError):
            self.save()

    def test_manual_must_belong_to_current_equipment_or_case(self):
        """Verify manual must belong to current equipment or case."""
        self.manual.model_id = self.machine.pk + 10000
        self.manual.save(update_fields=['model_id'])
        with self.assertRaises(RepairScopeDraftError):
            self.save()

    def test_missing_and_non_pdf_manuals_are_rejected(self):
        """Verify missing and non pdf manuals are rejected."""
        payload = self.payload()
        payload['manual_attachment_ids'] = []
        with self.assertRaises(RepairScopeDraftError):
            self.save(payload)
        self.manual.attachment.save('not-a-manual.txt', ContentFile(b'Notes'))
        with self.assertRaises(RepairScopeDraftError):
            self.save()

    def test_pdf_extension_without_pdf_bytes_is_rejected(self):
        """Verify pdf extension without pdf bytes is rejected."""
        self.manual.attachment.save('fake-manual.pdf', ContentFile(b'Not a PDF'))
        with self.assertRaises(RepairScopeDraftError):
            self.save()

    def test_replay_rejects_changed_source_bytes(self):
        """Verify replay rejects changed source bytes."""
        payload = self.payload()
        self.save(payload)
        self.manual.attachment.save(
            'motor-reference.pdf', ContentFile(b'%PDF-1.4\nChanged source\n%%EOF')
        )
        with self.assertRaises(RepairScopeDraftError):
            self.save(payload)

    def test_current_scope_revocation_denies_save_and_readback(self):
        """Verify current scope revocation denies save and readback."""
        self.save()
        with override_settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None):
            with self.assertRaises(WorkOrderScopeError):
                read_repair_scope_draft(self.work.pk, self.actor)
            with self.assertRaises(WorkOrderScopeError):
                self.save()

    def test_readback_never_reopens_or_modifies_a_closed_case(self):
        """Verify readback never reopens or modifies a closed case."""
        receipt = self.save()
        self.work.lifecycle_status = WorkOrderLifecycle.COMPLETED
        self.work.save(update_fields=['lifecycle_status'])
        self.packet.status = PacketStatus.CLOSED
        self.packet.save(update_fields=['status'])
        self.assertEqual(
            read_repair_scope_draft(self.work.pk, self.actor)['draft'], receipt
        )
        self.work.refresh_from_db()
        self.packet.refresh_from_db()
        self.assertEqual(self.work.lifecycle_status, WorkOrderLifecycle.COMPLETED)
        self.assertEqual(self.packet.status, PacketStatus.CLOSED)

    def test_terminal_work_and_packet_are_immutable(self):
        """Verify terminal work and packet are immutable."""
        self.work.lifecycle_status = WorkOrderLifecycle.COMPLETED
        self.work.save(update_fields=['lifecycle_status'])
        with self.assertRaises(RepairScopeDraftError):
            self.save()
        self.work.lifecycle_status = WorkOrderLifecycle.PLANNED
        self.work.save(update_fields=['lifecycle_status'])
        self.packet.status = PacketStatus.CLOSED
        self.packet.save(update_fields=['status'])
        with self.assertRaises(RepairScopeDraftError):
            self.save()

    def test_extra_authority_fields_and_replacement_parts_are_rejected(self):
        """Verify extra authority fields and replacement parts are rejected."""
        for field, value in (
            ('supervisor_review', 'approved'),
            ('replacement_parts', [1]),
        ):
            payload = self.payload()
            payload['scope'][field] = value
            with self.subTest(field=field), self.assertRaises(RepairScopeDraftError):
                self.save(payload)

    def test_failure_rolls_back_work_version_and_receipt(self):
        """Verify failure rolls back work version and receipt."""
        version = self.work.lifecycle_version
        with patch(
            'tasks.services.repair_scope_drafts._append_result',
            side_effect=RuntimeError('Unavailable'),
        ):
            with self.assertRaises(RuntimeError):
                self.save()
        self.work.refresh_from_db()
        self.assertEqual(self.work.lifecycle_version, version)
        self.assertFalse(self.work.events.filter(event_type=EVENT_TYPE).exists())

    def test_api_save_and_get_return_the_same_real_draft(self):
        """Verify api save and get return the same real draft."""
        empty = self.request('get')
        self.assertEqual(empty.status_code, 200)
        self.assertIsNone(empty.data['draft'])
        response = self.request('post', self.payload())
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.request('get').data, response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_api_authentication_and_strict_payload(self):
        """Verify api authentication and strict payload."""
        self.assertIn(
            self.request('post', self.payload(), actor=False).status_code, (401, 403)
        )
        payload = self.payload()
        payload['actor_id'] = self.reviewer.pk
        response = self.request('post', payload)
        self.assertEqual(response.status_code, 400)


@override_settings(
    AIMMS_PROCEDURES_ENABLED=True,
    AIMMS_TEST_ALARMS_ENABLED=True,
    AIMMS_WORK_ORDERS_ENABLED=True,
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.single_site_scope_resolver',
)
@skipUnless(
    connection.vendor == 'postgresql', 'Concurrent draft commands require PostgreSQL.'
)
class RepairScopeDraftConcurrencyTest(DraftFixture, TransactionTestCase):
    """Native row locks serialize simultaneous retry of one spoken command."""

    def test_concurrent_duplicate_has_one_effective_draft(self):
        """Verify concurrent duplicate has one effective draft."""
        payload = self.payload()
        barrier = Barrier(2)
        actor_id = self.actor.pk
        work_id = self.work.pk

        def save_once():
            """Verify save once."""
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=actor_id)
                barrier.wait(timeout=10)
                return save_repair_scope_draft(
                    work_order_id=work_id, actor=actor, **copy.deepcopy(payload)
                )
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: save_once(), range(2)))
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.work.events.filter(event_type=EVENT_TYPE).count(), 1)
