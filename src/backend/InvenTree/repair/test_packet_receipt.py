"""Packet receipt, replay and capture ownership through the native boundary."""

from unittest.mock import patch

from django.db.models.query import QuerySet
from django.test import override_settings

from tasks.closeout_models import CloseoutCapture
from tasks.models import WorkOrder, WorkOrderCloseout, WorkOrderEvent
from tasks.services.closeout_capture import create_capture, decision_version
from tasks.services.work_orders import CommandConflict, WorkOrderScopeError

from repair import services
from repair import test_packet_closeout as packet_tests
from repair.models import (
    GateStatus,
    LockoutPoint,
    RepairPacket,
    RepairPacketEvent,
    RepairPacketGate,
)

VALID_CLOSEOUT = packet_tests.VALID_CLOSEOUT


@override_settings(AIMMS_WORK_ORDERS_ENABLED=True, AIMMS_CLOSEOUT_WIZARD_ENABLED=True)
class PacketReceiptTest(packet_tests.PacketCloseoutApiTest):
    """The packet remains owner of the shared completion and capture services."""

    def body(self, **changes):
        """One immutable caller intent, including the packet-specific reason."""
        return {
            'expected_version': 1,
            'work_order_id': self.work_order.pk,
            'idempotency_key': 'packet-operation',
            'closeout': VALID_CLOSEOUT,
            'reason': 'Reviewed account',
            **changes,
        }

    def test_receipt_replays_before_changed_return_gates(self):
        """A committed result remains provable after today's safety facts change."""
        first = self.post(self.url, self.body(), expected_code=200).data
        event = WorkOrderEvent.objects.get(pk=first['event_id'])
        gate = RepairPacketGate.objects.create(
            packet=self.packet,
            name='LOTO',
            gate_type='loto',
            status=GateStatus.CONFIRMED,
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source='electrical',
            isolation_device='Changed after completion',
            status='verified',
        )
        replay = self.post(self.url, self.body(), expected_code=200).data
        for field in (
            'event_id',
            'command',
            'lifecycle_version',
            'idempotency_key',
            'metadata',
            'correlation_id',
        ):
            self.assertEqual(first[field], replay[field])
        self.assertEqual(first['lifecycle_version'], 2)
        self.assertEqual(first['metadata']['packet_id'], self.packet.pk)
        self.assertEqual(
            first['metadata']['closeout_id'], WorkOrderCloseout.objects.get().pk
        )
        self.assertEqual(
            WorkOrderEvent.objects.get(pk=event.pk).created_at, event.created_at
        )
        self.assertEqual(
            WorkOrderEvent.objects.filter(event_type='COMPLETED').count(), 1
        )
        self.assertEqual(
            self.packet.events.filter(event_type='return_to_service').count(), 1
        )

    def test_same_key_different_intent_is_refused(self):
        """Version, account, capture and packet reason cannot mutate a saved key."""
        self.post(self.url, self.body(), expected_code=200)
        for change in (
            {'expected_version': 2},
            {'reason': 'Different reason'},
            {'closeout': {**VALID_CLOSEOUT, 'result': 'Different'}},
            {'capture_id': 999},
        ):
            with self.subTest(change=change):
                response = self.post(self.url, self.body(**change), expected_code=400)
                self.assertEqual(response.data['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)

    def test_legacy_packet_key_replays_only_with_its_durable_packet_event(self):
        """Existing Work-only hashes retain the original packet binding."""
        from tasks.models import WorkOrderCommand
        from tasks.services.closeout import completion_request_hash

        first = self.post(self.url, self.body(), expected_code=200).data
        command = WorkOrderCommand.objects.get(
            work_order=self.work_order, idempotency_key=self.body()['idempotency_key']
        )
        command.request_hash = completion_request_hash(
            work_order_id=self.work_order.pk,
            actor=self.user,
            expected_version=1,
            closeout=VALID_CLOSEOUT,
        )
        command.save(update_fields=['request_hash'])
        event = WorkOrderEvent.objects.get(pk=first['event_id'])
        del event.metadata['result_metadata']['packet_id']
        event.save(update_fields=['metadata'])
        packet_event = self.packet.events.get(event_type='return_to_service')
        del packet_event.metadata['idempotency_key']
        packet_event.save(update_fields=['metadata'])
        replay = self.post(self.url, self.body(), expected_code=200).data
        self.assertEqual(first['event_id'], replay['event_id'])
        self.assertEqual(first['metadata'], replay['metadata'])
        refused = self.post(
            self.url, self.body(reason='Changed legacy reason'), expected_code=400
        )
        self.assertEqual(refused.data['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)

    def test_stale_precedes_new_packet_gate_refusal(self):
        """STALE_VERSION only means no original command committed."""
        self.work_order.lifecycle_version = 2
        self.work_order.save(update_fields=['lifecycle_version'])
        self.packet.status = 'canceled'
        self.packet.save(update_fields=['status'])
        response = self.post(self.url, self.body(), expected_code=400)
        self.assertEqual(response.data['code'], 'STALE_VERSION')
        self.assertFalse(WorkOrderCloseout.objects.exists())

    def test_repointed_packet_cannot_finalize_a_different_work(self):
        """A pending account is bound to its original Work identity."""
        from tasks.models import WorkOrder

        other = WorkOrder.objects.create(
            title='Other',
            customer=self.customer,
            machine=self.machine,
            assigned_to=self.user,
            lifecycle_status='verifying',
        )
        RepairPacketEvent.objects.create(
            packet=self.packet,
            event_type='return_to_service',
            metadata={'work_order_id': self.work_order.pk},
        )
        self.packet.work_order = other
        self.packet.save(update_fields=['work_order'])
        response = self.post(self.url, self.body(), expected_code=400)
        self.assertEqual(response.data['code'], 'PACKET_WORK_ORDER_CHANGED')
        self.assertFalse(WorkOrderCloseout.objects.exists())

    def test_original_receipt_survives_packet_repointing(self):
        """Reconciliation reads only the original ledger; it never closes new Work."""
        from tasks.models import WorkOrder

        first = self.post(self.url, self.body(), expected_code=200).data
        # Repointing stays within a valid packet/Work machine boundary. P32
        # deliberately withholds mismatched persisted packets before replay.
        other = WorkOrder.objects.create(
            title='Other', customer=self.customer, machine=self.machine
        )
        self.packet.work_order = other
        self.packet.save(update_fields=['work_order'])
        replay = self.post(self.url, self.body(), expected_code=200).data
        self.assertEqual(first['event_id'], replay['event_id'])
        self.assertEqual(first['work_order_id'], replay['work_order_id'])
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)
        refused = self.post(
            self.url, self.body(work_order_id=other.pk), expected_code=400
        )
        self.assertEqual(refused.data['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)

    def test_invalid_operation_keys_are_refused(self):
        """Malformed caller keys are never replaced by a random operation."""
        for key in ('', ' ', True, [], 'k' * 129):
            with self.subTest(key=key):
                self.post(self.url, self.body(idempotency_key=key), expected_code=400)
        self.assertFalse(WorkOrderCloseout.objects.exists())

    def test_packet_readiness_preserves_active_capture_and_return_locks(self):
        """Only packet ownership is suppressed in the packet readiness preview."""
        response = self.get(self.url, expected_code=200)
        self.assertTrue(response.data['ready'])
        self.assertEqual(response.data['work_order_id'], self.work_order.pk)
        self.assertEqual(response.data['packet_id'], self.packet.pk)
        self.create_story()
        response = self.get(self.url, expected_code=200)
        codes = [row['code'] for row in response.data['blockers']]
        self.assertIn('CLOSEOUT_REQUIRED', codes)
        self.assertNotIn('PACKET_OWNS_LIFECYCLE', codes)
        gate = RepairPacketGate.objects.create(
            packet=self.packet, name='LOTO', gate_type='loto'
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source='electrical',
            isolation_device='Not restored',
            status='verified',
        )
        response = self.get(self.url, expected_code=200)
        self.assertIn(
            'PACKET_CLOSEOUT_BLOCKED',
            [row['code'] for row in response.data['blockers']],
        )
        self.post(self.url, self.body(), expected_code=400)
        self.assertFalse(WorkOrderCloseout.objects.exists())

    def test_packet_readiness_lists_every_packet_refusal(self):
        """An illegal packet state never hides an unrestored lockout, or vice versa."""
        self.packet.status = 'canceled'
        self.packet.save(update_fields=['status'])
        gate = RepairPacketGate.objects.create(
            packet=self.packet, name='LOTO', gate_type='loto'
        )
        LockoutPoint.objects.create(
            gate=gate,
            energy_source='electrical',
            isolation_device='Still isolated',
            status='verified',
        )
        response = self.get(self.url, expected_code=200)
        self.assertFalse(response.data['ready'])
        messages = [
            row['message']
            for row in response.data['blockers']
            if row['code'] == 'PACKET_CLOSEOUT_BLOCKED'
        ]
        self.assertEqual(len(messages), 2)
        self.assertTrue(any('Illegal transition' in text for text in messages))
        self.assertTrue(any('Still isolated' in text for text in messages))

    def create_story(self):
        """Use the existing Work capture URL, dispatched through its packet."""
        response = self.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/',
            {
                'expected_version': 1,
                'idempotency_key': 'packet-story',
                'narrative': 'Inspected the recorded seal.',
                'voice_unavailable_reason': 'Microphone unavailable',
            },
            expected_code=200,
        )
        return CloseoutCapture.objects.get(pk=response.data['metadata']['capture_id'])

    def test_capture_decisions_and_close_are_owned_and_consumed_atomically(self):
        """Reviewed packet capture joins the same one final update transaction."""
        capture = self.create_story()
        self.assertEqual(self.create_story().pk, capture.pk)
        decisions = [
            {
                'field_path': name,
                'origin': 'manual',
                'decision': 'edited',
                'final_value': VALID_CLOSEOUT[name],
            }
            for name in ('action', 'result', 'verification_summary')
        ]
        self.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/{capture.pk}/decisions/',
            {
                'expected_version': 1,
                'idempotency_key': 'packet-decisions',
                'expected_decision_version': decision_version(capture),
                'decisions': decisions,
            },
            expected_code=200,
        )
        receipt = self.post(
            self.url, self.body(capture_id=capture.pk), expected_code=200
        ).data
        capture.refresh_from_db()
        self.assertEqual(capture.status, 'consumed')
        self.assertEqual(
            capture.completed_closeout_id, receipt['metadata']['closeout_id']
        )
        self.post(self.url, self.body(capture_id=capture.pk), expected_code=200)
        self.assertEqual(WorkOrderCloseout.objects.count(), 1)

    def test_capture_cannot_be_created_outside_its_owning_packet(self):
        """A forged token or another packet never opens packet-owned capture."""
        kwargs = {
            'work_order_id': self.work_order.pk,
            'actor': self.user,
            'expected_version': 1,
            'idempotency_key': 'forged',
            'narrative': 'Story',
        }
        with self.assertRaises(CommandConflict):
            create_capture(**kwargs, packet_finalization=True)
        other = RepairPacket.objects.create()
        with self.assertRaises(CommandConflict):
            services.packet_closeout_capture(packet_id=other.pk, **kwargs)
        self.assertFalse(CloseoutCapture.objects.exists())

    def test_another_works_capture_cannot_be_consumed_or_accepted(self):
        """The locked capture lookup remains bound to the owning Work."""
        from tasks.models import WorkOrder

        other = WorkOrder.objects.create(title='Other', customer=self.customer)
        capture = CloseoutCapture.objects.create(work_order=other, created_by=self.user)
        # Keep both ownership boundaries checked if a native timing assertion
        # fails on one request. Each request retains the original time budget.
        with self.subTest(operation='consume_foreign_capture'):
            self.post(self.url, self.body(capture_id=capture.pk), expected_code=400)
        with self.subTest(operation='accept_foreign_capture'):
            self.post(
                f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/{capture.pk}/decisions/',
                {
                    'expected_version': 1,
                    'idempotency_key': 'wrong-capture',
                    'expected_decision_version': 'a' * 64,
                    'decisions': [
                        {
                            'field_path': 'action',
                            'origin': 'manual',
                            'decision': 'edited',
                            'final_value': 'Unrelated',
                        }
                    ],
                },
                expected_code=400,
            )
        with self.subTest(operation='no_closeout_written'):
            self.assertFalse(WorkOrderCloseout.objects.exists())

    def test_standalone_complete_still_refuses_packet_work(self):
        """HTTP callers cannot rebind completion to the standalone command."""
        response = self.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/complete/',
            {
                **VALID_CLOSEOUT,
                'expected_version': 1,
                'idempotency_key': 'bypass',
                'packet_finalization': True,
            },
            expected_code=409,
        )
        self.assertEqual(response.data['code'], 'PACKET_OWNS_LIFECYCLE')

    def test_revoked_machine_scope_refuses_packet_but_retains_shared_work_capture(self):
        """O24 shares narrative capture, never packet close or machine-only Work."""
        packet_tests._GRANTED_CUSTOMER_IDS.clear()
        packet_tests._GRANTED_CLIENT_IDS.clear()
        self.get(self.url, expected_code=404)
        self.post(self.url, self.body(), expected_code=404)
        capture_url = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/'
        intent = {
            'expected_version': 1,
            'idempotency_key': 'revoked',
            'narrative': 'A shared Work narrative',
        }
        captured = self.post(capture_url, intent, expected_code=200)
        self.assertEqual(CloseoutCapture.objects.count(), 1)
        self.assertEqual(
            CloseoutCapture.objects.get().pk, captured.data['metadata']['capture_id']
        )
        self.work_order.customer = None
        self.work_order.save(update_fields=['customer'])
        self.post(
            capture_url,
            {**intent, 'idempotency_key': 'machine-only'},
            expected_code=404,
        )
        self.assertFalse(WorkOrderCloseout.objects.exists())
        self.assertEqual(CloseoutCapture.objects.count(), 1)

    def test_unrelated_and_unavailable_work_ids_have_identical_refusal_without_lock(
        self,
    ):
        """Work existence and tenant membership cannot be probed through a packet."""
        from assets.models import AssetMachine, Client

        tenant = Client.objects.create(name='Other tenant', code='o24-receipt-other')
        foreign = WorkOrder.objects.create(
            title='Foreign',
            machine=AssetMachine.objects.create(
                name='O24 receipt foreign pump', client=tenant
            ),
        )
        unresolved = WorkOrder.objects.create(title='Unresolved scope')
        unrelated = WorkOrder.objects.create(title='Unrelated', customer=self.customer)
        missing = max(foreign.pk, unrelated.pk) + 100000
        original_lock = QuerySet.select_for_update
        work_locks = []

        def observe_lock(queryset, *args, **kwargs):
            if queryset.model is WorkOrder:
                work_locks.append(queryset)
            return original_lock(queryset, *args, **kwargs)

        responses = []
        with patch.object(QuerySet, 'select_for_update', observe_lock):
            for work_id in (foreign.pk, unrelated.pk, unresolved.pk, missing):
                responses.append(
                    self.post(
                        self.url, self.body(work_order_id=work_id), expected_code=404
                    ).data
                )
            # Even a historical packet event cannot grant scope to another tenant.
            RepairPacketEvent.objects.create(
                packet=self.packet,
                event_type='return_to_service',
                metadata={'work_order_id': foreign.pk},
            )
            responses.append(
                self.post(
                    self.url, self.body(work_order_id=foreign.pk), expected_code=404
                ).data
            )
            # A formerly authorized historical id that no longer exists is identical.
            RepairPacketEvent.objects.create(
                packet=self.packet,
                event_type='return_to_service',
                metadata={'work_order_id': missing},
            )
            responses.append(
                self.post(
                    self.url, self.body(work_order_id=missing), expected_code=404
                ).data
            )
            # Fine-grained scope checks must also happen before any Work lock.
            with patch(
                'tasks.services.work_orders._require_scope',
                side_effect=WorkOrderScopeError('Scope unavailable'),
            ):
                responses.append(
                    self.post(self.url, self.body(), expected_code=404).data
                )
        self.assertTrue(all(row == responses[0] for row in responses))
        self.assertEqual(work_locks, [])
        self.assertFalse(WorkOrderCloseout.objects.exists())

    def test_fractional_or_negative_downtime_is_refused_without_receipt(self):
        """Packet persistence uses the same integer/minimum fields as /complete/."""
        for value in (1.5, -1, '1.5'):
            with self.subTest(value=value):
                response = self.post(
                    self.url,
                    self.body(closeout={**VALID_CLOSEOUT, 'downtime_minutes': value}),
                    expected_code=400,
                )
                self.assertEqual(response.data['code'], 'CLOSEOUT_INVALID')
        self.assertFalse(WorkOrderCloseout.objects.exists())
        self.assertFalse(WorkOrderEvent.objects.filter(event_type='COMPLETED').exists())

    def test_packet_manual_close_survives_every_flag_combination(self):
        """The packet's native final action never depends on the wizard gate."""
        for work_enabled, wizard_enabled in (
            (False, False),
            (False, True),
            (True, False),
            (True, True),
        ):
            with (
                self.subTest(work=work_enabled, wizard=wizard_enabled),
                override_settings(
                    AIMMS_WORK_ORDERS_ENABLED=work_enabled,
                    AIMMS_CLOSEOUT_WIZARD_ENABLED=wizard_enabled,
                ),
            ):
                # Use a fresh packet/Work per combination so none is only replay.
                self.work_order = WorkOrder.objects.create(
                    title='Flag combination',
                    customer=self.customer,
                    machine=self.machine,
                    assigned_to=self.user,
                    lifecycle_status='verifying',
                )
                self.packet = RepairPacket.objects.create(
                    machine=self.machine, work_order=self.work_order, status='executing'
                )
                self.url = f'/api/repair/packets/{self.packet.pk}/close/'
                self.assertEqual(
                    self.get(
                        f'/api/kanban/work-orders/{self.work_order.pk}/overview/',
                        expected_code=200,
                    ).data['closeout_wizard_enabled'],
                    work_enabled and wizard_enabled,
                )
                self.get(self.url, expected_code=200)
                if not (work_enabled and wizard_enabled):
                    self.get(
                        f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/',
                        expected_code=404,
                    )
                self.post(self.url, self.body(), expected_code=200)
        self.assertEqual(WorkOrderCloseout.objects.count(), 4)

    def test_packet_capture_revise_extract_and_abandon_use_owner_boundary(self):
        """All three existing capture routes dispatch through the packet wrappers."""
        capture = self.create_story()
        url = f'/api/tasks/work-orders/{self.work_order.pk}/closeout/captures/{capture.pk}/'
        body = {
            'expected_version': 1,
            'idempotency_key': 'revise-owned',
            'expected_revision': 1,
            'narrative': 'Inspected the seal again.',
        }
        first = self.patch(url, body, expected_code=200).data
        replay = self.patch(url, body, expected_code=200).data
        self.assertEqual(first['event_id'], replay['event_id'])
        document = {
            'schema_version': 1,
            'fields': {},
            'part_candidates': [],
            'reading_candidates': [],
            'warnings': [],
        }
        with patch(
            'tasks.services.closeout_capture.resolve_extractor',
            return_value=lambda *_: document,
        ):
            proposal = self.post(url + 'extract/', {}, expected_code=200).data
            self.assertEqual(
                self.post(url + 'extract/', {}, expected_code=200).data['id'],
                proposal['id'],
            )
        abandoned = self.patch(
            url,
            {
                'expected_version': 1,
                'idempotency_key': 'abandon-owned',
                'abandon': True,
            },
            expected_code=200,
        ).data
        self.assertEqual(
            self.patch(
                url,
                {
                    'expected_version': 1,
                    'idempotency_key': 'abandon-owned',
                    'abandon': True,
                },
                expected_code=200,
            ).data['event_id'],
            abandoned['event_id'],
        )
        capture.refresh_from_db()
        self.assertEqual(capture.status, 'abandoned')

    def test_packet_capture_mutations_refuse_missing_or_foreign_owner(self):
        """Direct services and a different packet cannot mutate this capture."""
        from tasks.services.closeout_capture import (
            CaptureError,
            abandon_capture,
            request_extraction,
            revise_capture,
        )

        capture = self.create_story()
        other = RepairPacket.objects.create()
        other_work = WorkOrder.objects.create(
            title='Other capture owner', customer=self.customer
        )
        foreign_capture = CloseoutCapture.objects.create(
            work_order=other_work, created_by=self.user
        )
        base = {
            'work_order_id': self.work_order.pk,
            'capture_id': capture.pk,
            'actor': self.user,
        }
        operations = (
            (
                revise_capture,
                services.packet_closeout_revise,
                {
                    'expected_version': 1,
                    'expected_revision': 1,
                    'idempotency_key': 'bad-revise',
                    'narrative': 'No write',
                },
            ),
            (
                abandon_capture,
                services.packet_closeout_abandon,
                {'expected_version': 1, 'idempotency_key': 'bad-abandon'},
            ),
            (request_extraction, services.packet_closeout_extract, {}),
        )
        for direct, owned, arguments in operations:
            with self.subTest(operation=direct.__name__):
                with self.assertRaises(CommandConflict):
                    direct(**base, **arguments)
                with self.assertRaises(CommandConflict):
                    owned(packet_id=other.pk, **base, **arguments)
                with self.assertRaises(CaptureError):
                    owned(
                        packet_id=self.packet.pk,
                        **{**base, 'capture_id': foreign_capture.pk},
                        **arguments,
                    )
        capture.refresh_from_db()
        self.assertEqual(capture.current_revision.revision, 1)
        self.assertEqual(capture.status, 'open')

    def test_extraction_rechecks_packet_owner_before_persisting(self):
        """A packet repointed during extraction cannot accept its result."""
        from tasks.closeout_models import CloseoutProposal

        capture = self.create_story()
        other = WorkOrder.objects.create(title='Replacement', customer=self.customer)

        def extractor(*_):
            RepairPacket.objects.filter(pk=self.packet.pk).update(work_order=other)
            return {
                'schema_version': 1,
                'fields': {},
                'part_candidates': [],
                'reading_candidates': [],
                'warnings': [],
            }

        with patch(
            'tasks.services.closeout_capture.resolve_extractor', return_value=extractor
        ):
            with self.assertRaises(CommandConflict):
                services.packet_closeout_extract(
                    packet_id=self.packet.pk,
                    work_order_id=self.work_order.pk,
                    capture_id=capture.pk,
                    actor=self.user,
                )
        self.assertFalse(CloseoutProposal.objects.exists())

    def test_disabled_wizard_does_not_bypass_an_existing_packet_capture_lock(self):
        """Manual fallback never treats an unfinished packet story as reviewed."""
        self.create_story()
        with override_settings(
            AIMMS_WORK_ORDERS_ENABLED=False, AIMMS_CLOSEOUT_WIZARD_ENABLED=False
        ):
            readiness = self.get(self.url, expected_code=200).data
            self.assertFalse(readiness['ready'])
            self.assertIn(
                'CLOSEOUT_REQUIRED', [row['code'] for row in readiness['blockers']]
            )
            self.post(self.url, self.body(), expected_code=400)
        self.assertFalse(WorkOrderCloseout.objects.exists())
