"""R6 S0 capability, authority and exact-request completion contracts."""

from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from repair.models import (
    GateStatus,
    LockoutPoint,
    PacketStatus,
    RepairPacket,
    RepairPacketGate,
)
from tasks.models import WorkOrderCloseout, WorkOrderLifecycle
from tasks.tests.closeout_fixtures import (
    CLOSEOUT_FLAGS,
    VALID_CLOSEOUT,
    CloseoutEnvMixin,
)


@override_settings(**CLOSEOUT_FLAGS)
class S0CapabilityContractTest(CloseoutEnvMixin, TestCase):
    """Prove flags and request identity against the actual HTTP adapters."""

    def setUp(self):
        """Create only synthetic in-scope work and authenticate its actor."""
        self.build_env(username='r6-s0-capabilities')
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def url(self, suffix=''):
        """Return one canonical work-order resource or command URL."""
        return f'/api/tasks/work-orders/{self.work_order.pk}/{suffix}'

    def intent(self):
        """Keep the same version and logical key for exact-request replay."""
        return {
            **VALID_CLOSEOUT,
            'expected_version': self.work_order.lifecycle_version,
            'idempotency_key': 'r6-s0-logical-completion',
        }

    def test_work_order_and_capture_capability_matrix(self):
        """Both deployment flags gate captures; the overview stays legacy."""
        for work_enabled in (False, True):
            for capture_enabled in (False, True):
                with (
                    self.subTest(work=work_enabled, capture=capture_enabled),
                    override_settings(
                        AIMMS_WORK_ORDERS_ENABLED=work_enabled,
                        AIMMS_CLOSEOUT_WIZARD_ENABLED=capture_enabled,
                    ),
                ):
                    detail = self.client.get(self.url())
                    capture = self.client.get(self.url('closeout/captures/'))
                    overview = self.client.get(
                        f'/api/kanban/work-orders/{self.work_order.pk}/overview/'
                    )
                    self.assertEqual(detail.status_code, 200 if work_enabled else 404)
                    self.assertEqual(
                        capture.status_code,
                        200 if work_enabled and capture_enabled else 404,
                    )
                    self.assertEqual(overview.status_code, 200)
                    self.assertEqual(
                        overview.data['canonical_commands_enabled'], work_enabled
                    )

    def test_view_access_does_not_grant_capture_authority(self):
        """An authenticated scoped reader cannot create a governed capture."""
        viewer = self.make_scoped_user('r6-s0-viewer')
        self.client.force_authenticate(viewer)
        response = self.client.post(
            self.url('closeout/captures/'),
            {**self.intent(), 'narrative': 'Synthetic account'},
            format='json',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'PERMISSION_DENIED')
        self.assertFalse(self.work_order.closeout_captures.exists())

    def test_missing_completion_permission_writes_nothing(self):
        """A same-scope actor needs the explicit completion permission."""
        viewer = self.make_scoped_user('r6-s0-no-completion')
        self.client.force_authenticate(viewer)
        response = self.client.post(self.url('complete/'), self.intent(), format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'PERMISSION_DENIED')
        self.assertFalse(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).exists()
        )
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.VERIFYING)

    def test_same_request_replays_after_the_first_response_is_discarded(self):
        """An ignored first receipt does not require a new key or version."""
        intent = self.intent()
        first = self.client.post(self.url('complete/'), intent, format='json')
        self.assertEqual(first.status_code, 200)
        replay = self.client.post(self.url('complete/'), intent, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data, first.data)
        self.assertEqual(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).count(), 1
        )

    def test_changed_intent_with_the_same_key_is_a_conflict(self):
        """A retry key cannot silently accept a different maintenance account."""
        intent = self.intent()
        first = self.client.post(self.url('complete/'), intent, format='json')
        self.assertEqual(first.status_code, 200)
        changed = self.client.post(
            self.url('complete/'),
            {**intent, 'result': 'Different result'},
            format='json',
        )
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.data['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).count(), 1
        )

    def test_revoked_scope_is_checked_before_returning_a_saved_receipt(self):
        """Shared receipts survive client revocation; machine-only ones do not."""
        intent = self.intent()
        first = self.client.post(self.url('complete/'), intent, format='json')
        self.assertEqual(first.status_code, 200)
        self.actor.maintenance_scopes = set()
        shared_replay = self.client.post(self.url('complete/'), intent, format='json')
        self.assertEqual(shared_replay.status_code, 200)
        self.assertEqual(shared_replay.data, first.data)
        self.work_order.customer = None
        self.work_order.save(update_fields=['customer'])
        replay = self.client.post(self.url('complete/'), intent, format='json')
        self.assertEqual(replay.status_code, 404)
        self.assertEqual(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).count(), 1
        )

    def test_stale_version_does_not_create_a_closeout(self):
        """A different operation with an old version fails before persistence."""
        self.work_order.lifecycle_version = 2
        self.work_order.save(update_fields=['lifecycle_version'])
        intent = self.intent()
        intent['expected_version'] -= 1
        response = self.client.post(self.url('complete/'), intent, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'STALE_VERSION')
        self.assertFalse(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).exists()
        )


@override_settings(**CLOSEOUT_FLAGS)
class S0PacketCompletionContractTest(CloseoutEnvMixin, TestCase):
    """Pin packet HTTP replay separately from standalone command behavior."""

    def setUp(self):
        """Build synthetic packet-owned work with explicit scoped authority."""
        self.build_env(username='r6-s0-packet')
        self.packet = RepairPacket.objects.create(
            machine=self.machine,
            work_order=self.work_order,
            status=PacketStatus.EXECUTING,
            fault_summary='Synthetic completion contract',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.url = f'/api/repair/packets/{self.packet.pk}/close/'
        self.intent = {
            'expected_version': self.work_order.lifecycle_version,
            'idempotency_key': 'r6-s0-packet-completion',
            'closeout': VALID_CLOSEOUT,
        }

    def test_exact_packet_http_request_replays_once(self):
        """The packet adapter can replay the original nested intent."""
        first = self.client.post(self.url, self.intent, format='json')
        self.assertEqual(first.status_code, 200)
        replay = self.client.post(self.url, self.intent, format='json')
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data['work_order_id'], self.work_order.pk)
        self.assertEqual(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).count(), 1
        )
        self.assertEqual(
            self.packet.events.filter(event_type='return_to_service').count(), 1
        )

    def test_changed_packet_intent_with_same_key_is_refused(self):
        """The packet endpoint uses its existing HTTP 400 error envelope."""
        first = self.client.post(self.url, self.intent, format='json')
        self.assertEqual(first.status_code, 200)
        changed = self.client.post(
            self.url,
            {**self.intent, 'closeout': {**VALID_CLOSEOUT, 'result': 'Different'}},
            format='json',
        )
        self.assertEqual(changed.status_code, 400)
        self.assertEqual(changed.data['code'], 'IDEMPOTENCY_CONFLICT')

    def test_committed_packet_receipt_survives_changed_safety(self):
        """P26 reconciles the original receipt without executing today's gates."""
        first = self.client.post(self.url, self.intent, format='json')
        self.assertEqual(first.status_code, 200)
        gate = RepairPacketGate.objects.create(
            packet=self.packet,
            name='Synthetic LOTO',
            gate_type='loto',
            status=GateStatus.CONFIRMED,
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='Synthetic isolation',
            status=LockoutPoint.PointStatus.VERIFIED,
        )
        replay = self.client.post(self.url, self.intent, format='json')
        self.assertEqual(replay.status_code, 200)
        for field in ('event_id', 'work_order_id', 'lifecycle_version', 'metadata'):
            self.assertEqual(replay.data[field], first.data[field])
        self.assertEqual(
            self.packet.events.filter(event_type='return_to_service').count(), 1
        )
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.COMPLETED)
        self.assertEqual(
            WorkOrderCloseout.objects.filter(work_order=self.work_order).count(), 1
        )

    def test_current_packet_safety_still_refuses_a_new_completion(self):
        """A replay receipt never grants permission for a new unsafe update."""
        gate = RepairPacketGate.objects.create(
            packet=self.packet,
            name='Synthetic LOTO',
            gate_type='loto',
            status=GateStatus.CONFIRMED,
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source=LockoutPoint.EnergySource.ELECTRICAL,
            isolation_device='Synthetic isolation',
            status=LockoutPoint.PointStatus.VERIFIED,
        )
        response = self.client.post(self.url, self.intent, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['code'], 'REPAIR_CLOSEOUT_INVALID')
        self.assertFalse(WorkOrderCloseout.objects.exists())
        self.assertFalse(
            self.packet.events.filter(event_type='return_to_service').exists()
        )
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, WorkOrderLifecycle.VERIFYING)
