"""Actual ASGI S3→S5→S7 composition; historical positives simulate attribution.

Only synthetic local source records/private metadata and actual native sessions,
roles and grants are used. No genuine historical proof or writer hook exists.
"""

from integration_state.tests.assertions import assert_literal_bool

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from importlib import import_module
from unittest.mock import patch
from uuid import uuid4

from django.test import TransactionTestCase

from ai.core.tests import test_integration_s3_content_boundary_db as boundary
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from integration_state import s7_changes, shared_delivery, shared_events
from integration_state.models import EventDelivery, SourceChange, SourceNamespace
from integration_state.s7_changes import delivery_change_from_claim
from integration_state.shared_delivery import claim_changes, configure_consumer


class SourceDeliveryFixture(boundary.S3AIRequestFixture):
    """Reuse genuine middleware transport and unchanged synthetic source setup."""

    def setUp(self):
        """Create only a disposable server-selected private bookkeeping namespace."""
        super().setUp()
        self.namespace = SourceNamespace.objects.create(
            environment='s3-test', active=True
        )

    def tearDown(self):
        """Deactivate this exact namespace before native destructive test flush."""
        try:
            SourceNamespace.objects.filter(pk=self.namespace.pk).update(active=False)
        finally:
            super().tearDown()

    def delivery(self):
        """Resolve the newly allocated entry without fabricating its behavior."""
        return import_module('tasks.integration_s3_delivery')

    def prepare(self, **changes):
        """Invoke the actual current-AI source entry, not a permission callback."""
        module = self.delivery()
        entry = getattr(module, 'prepare_source_delivery', None)
        self.assertTrue(callable(entry), 'S3 needs its current-source delivery factory')
        arguments = {
            'environment': 's3-test',
            'operation': 'work.core',
            'source_pk': self.work.pk,
        }
        arguments.update(changes)
        return entry(**arguments)

    def append(self, prepared, **changes):
        """Supply explicit namespace/epoch/event/fence, without native authority."""
        arguments = {
            'namespace_id': self.namespace.pk,
            'epoch': self.namespace.epoch,
            'event_key': 'synthetic-s3-read',
            'expected_fence': 0,
        }
        arguments.update(changes)
        return prepared.record(**arguments)

    def configure(self, **changes):
        """The actual existing owner, not the source read, grants private leases."""
        arguments = {
            'aspects': (('work', 'work.core'),),
            'configuration_epoch': uuid4(),
        }
        arguments.update(changes)
        return configure_consumer(self.namespace.pk, 'private-s3-reader', **arguments)

    def claim(self, configuration, **changes):
        """Claim once through S5; install no worker or broad discovery loop."""
        arguments = {
            'worker_key': 'private-s3-worker',
            'limit': 10,
            'lease_seconds': 300,
        }
        arguments.update(changes)
        return claim_changes(configuration.consumer_id, **arguments)

    def acknowledge(self, prepared, change, configuration, claim):
        """Reach the real decode and persistent acknowledgment owners."""
        return prepared.acknowledge(
            change,
            configuration=configuration,
            claim=claim,
            worker_key='private-s3-worker',
        )


@assume_synthetic_attribution
class SimulatedSourceDeliveryTests(SourceDeliveryFixture, TransactionTestCase):
    """Only historical attribution is simulated; current authority/storage are real."""

    def test_content_file_io_stays_outside_private_metadata_transactions(self):
        """Observe actual storage opens before/after real append and committed ack."""
        from django.core.files.storage import default_storage
        from django.db import connection

        self.prepare_operations()
        for owner, name in (
            ('workorder', 'work_document.content'),
            ('assetmachine', 'machine_document.content'),
        ):
            with self.subTest(operation=name):
                self.select_owner(owner)

                def operation(name=name):
                    prepared = self.prepare(
                        operation=name, source_pk=self.attachment.pk, max_bytes=128
                    )
                    original_open = default_storage.open
                    original_append = shared_events.record_observation
                    after_append = []
                    opens = []

                    def opened(*args, **kwargs):
                        opens.append((bool(after_append), connection.in_atomic_block))
                        return original_open(*args, **kwargs)

                    def appended(*args, **kwargs):
                        result = original_append(*args, **kwargs)
                        after_append.append(result.change_id)
                        return result

                    with (
                        patch.object(default_storage, 'open', side_effect=opened),
                        patch.object(
                            shared_events, 'record_observation', side_effect=appended
                        ) as stored,
                    ):
                        change = self.append(prepared, event_key=name)
                        configuration = self.configure(aspects=(('attachment', name),))
                        (claim,) = self.claim(configuration)
                        self.assertEqual(
                            self.acknowledge(
                                prepared, change, configuration, claim
                            ).change_id,
                            change.change_id,
                        )
                        stored.assert_called_once()
                    self.assertTrue(opens)
                    self.assertTrue(any(after for after, _ in opens))
                    self.assertFalse(
                        any(active for _, active in opens),
                        'Actual content IO ran inside a private metadata transaction: '
                        + repr(opens),
                    )
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_caller_transaction_refuses_before_source_io_or_private_append(self):
        """A caller cannot retain ownership locks around these source operations."""
        from django.core.files.storage import default_storage
        from django.db import transaction

        self.prepare_operations()
        self.select_owner('workorder')
        arguments = {
            'operation': 'work_document.content',
            'source_pk': self.attachment.pk,
            'max_bytes': 128,
        }

        def operation():
            prepared = self.prepare(**arguments)
            with (
                transaction.atomic(),
                patch.object(
                    default_storage, 'open', wraps=default_storage.open
                ) as opened,
                patch.object(
                    shared_events,
                    'record_observation',
                    wraps=shared_events.record_observation,
                ) as stored,
            ):
                with self.assertRaises(self.delivery().SourceDeliveryError):
                    self.prepare(**arguments)
                with self.assertRaises(self.delivery().SourceDeliveryError):
                    self.append(prepared)
                opened.assert_not_called()
                stored.assert_not_called()
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_post_commit_content_change_preserves_event_before_owner_readback(self):
        """Full byte validation after commit refuses stale bytes without undoing it."""
        from django.db import connection

        self.prepare_operations()
        self.select_owner('workorder')

        def operation():
            prepared = self.prepare(
                operation='work_document.content',
                source_pk=self.attachment.pk,
                max_bytes=128,
            )
            original = shared_events.shared_transaction
            depth = 0
            changed = []

            @contextmanager
            def after_commit(*args, **kwargs):
                nonlocal depth
                depth += 1
                with original(*args, **kwargs):
                    yield
                depth -= 1
                if depth == 0 and not changed:
                    self.assertFalse(connection.in_atomic_block)
                    with self.attachment.attachment.storage.open(
                        self.attachment.attachment.name, 'wb'
                    ) as handle:
                        handle.write(b'X' * len(self.blob))
                    changed.append(True)

            try:
                with (
                    patch.object(
                        shared_events, 'shared_transaction', side_effect=after_commit
                    ),
                    patch.object(
                        SourceChange.objects,
                        'select_related',
                        wraps=SourceChange.objects.select_related,
                    ) as read,
                ):
                    with self.assertRaises(
                        self.delivery().SourceDeliveryUnavailableError
                    ):
                        self.append(prepared)
                    read.assert_not_called()
                self.assertEqual(changed, [True])
                self.assertEqual(SourceChange.objects.count(), 1)
                event_id = SourceChange.objects.get().pk
            finally:
                with self.attachment.attachment.storage.open(
                    self.attachment.attachment.name, 'wb'
                ) as handle:
                    handle.write(self.blob)
            self.assertEqual(self.append(prepared).change_id, event_id)
            self.assertEqual(SourceChange.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_caller_atomic_or_disabled_autocommit_cannot_reach_ack_source_io(self):
        """Caller-owned transactions refuse before source/decode/ack; recovery works."""
        from django.core.files.storage import default_storage
        from django.db import connection, transaction

        self.prepare_operations()
        self.select_owner('workorder')

        def operation():
            prepared = self.prepare(
                operation='work_document.content',
                source_pk=self.attachment.pk,
                max_bytes=128,
            )
            change = self.append(prepared)
            configuration = self.configure(
                aspects=(('attachment', 'work_document.content'),)
            )
            (claim,) = self.claim(configuration)
            state = EventDelivery.objects.get(pk=claim.delivery_id).state

            @contextmanager
            def caller_transaction(kind):
                if kind == 'atomic':
                    with transaction.atomic():
                        yield
                else:
                    self.assertTrue(connection.get_autocommit())
                    connection.set_autocommit(False)
                    try:
                        yield
                    finally:
                        connection.rollback()
                        connection.set_autocommit(True)

            for kind in ('atomic', 'disabled-autocommit'):
                with self.subTest(kind=kind):
                    with (
                        caller_transaction(kind),
                        patch.object(
                            default_storage, 'open', wraps=default_storage.open
                        ) as opened,
                        patch.object(
                            s7_changes,
                            'delivery_change_from_claim',
                            wraps=s7_changes.delivery_change_from_claim,
                        ) as decoded,
                        patch.object(
                            shared_delivery,
                            'ack_change',
                            wraps=shared_delivery.ack_change,
                        ) as acknowledged,
                    ):
                        with self.assertRaises(self.delivery().SourceDeliveryError):
                            self.acknowledge(prepared, change, configuration, claim)
                        opened.assert_not_called()
                        decoded.assert_not_called()
                        acknowledged.assert_not_called()
                    self.assertEqual(
                        EventDelivery.objects.get(pk=claim.delivery_id).state, state
                    )
            self.assertEqual(
                self.acknowledge(prepared, change, configuration, claim).change_id,
                change.change_id,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_current_source_reaches_actual_owner_decode_and_committed_ack(self):
        """One native producer observation binds actual private metadata and receipt."""

        def operation():
            original = self.ai_read().outcome
            self.assertEqual(original.state, 'available')
            prepared = self.prepare()
            self.assertNotIn(self.work.title, repr(prepared))
            change = self.append(prepared)
            self.assertEqual(
                (change.key.entity_kind, change.key.aspect), ('work', 'work.core')
            )
            self.assertIs(type(change.key.source_id), int)
            self.assertEqual(change.key.source_id, self.work.pk)
            self.assertIsNone(change.key.declared_incarnation)
            self.assertEqual(
                change.observation.content_digest,
                hashlib.sha256(original.payload).hexdigest(),
            )
            self.assertIsNone(change.observation.source_revision)
            self.assertIsNone(change.observation.client_ids)
            assert_literal_bool(self, change.observation.coverage_complete, False)
            assert_literal_bool(self, change.observation.source_deleted, False)
            self.assertEqual(SourceChange.objects.count(), 1)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            decoded = delivery_change_from_claim(
                environment='s3-test',
                worker_key='private-s3-worker',
                configuration=configuration,
                claim=claim,
            )
            self.assertEqual(decoded.change.event_id, change.change_id)
            self.assertEqual(decoded.change.observation, change.observation)
            assert_literal_bool(self, decoded.native_authorized, False)
            assert_literal_bool(self, decoded.population_complete, False)
            assert_literal_bool(self, decoded.deletion_applied, False)
            self.assertEqual(decoded.tombstones, ())
            receipt = self.acknowledge(prepared, change, configuration, claim)
            self.assertEqual(receipt.change_id, change.change_id)
            self.assertEqual(receipt.outcome, 'applied')
            assert_literal_bool(self, receipt.erasure_complete, False)
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'acknowledged'
            )
            self.assertEqual(self.claim(configuration), ())
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_all_six_actual_producers_keep_distinct_domains_aspects_and_bytes(self):
        """Equal numeric IDs/owner moves never alias source aspects or digest kinds."""
        operations = self.prepare_operations()
        names = {
            'read_work_document_metadata_json': 'work_document.metadata',
            'read_machine_document_metadata_json': 'machine_document.metadata',
            'read_work_document_content_json': 'work_document.content',
            'read_machine_document_content_json': 'machine_document.content',
            'read_work_details_json': 'work.core',
            'read_maintenance_details_json': 'maintenance.direct',
        }
        for name, arguments, owner in operations:
            with self.subTest(operation=name):
                self.select_owner(owner)
                operation_name = names[name]
                source_pk = next(
                    value for key, value in arguments.items() if key != 'max_bytes'
                )
                changes = {'operation': operation_name, 'source_pk': source_pk}
                if 'max_bytes' in arguments:
                    changes['max_bytes'] = arguments['max_bytes']

                def operation(
                    changes=changes, operation_name=operation_name, source_pk=source_pk
                ):
                    original = self.ai_read(**changes).outcome.payload
                    prepared = self.prepare(**changes)
                    change = self.append(prepared, event_key=operation_name)
                    domain = (
                        'work'
                        if operation_name == 'work.core'
                        else 'maintenance'
                        if operation_name == 'maintenance.direct'
                        else 'attachment'
                    )
                    self.assertEqual(
                        (
                            change.key.entity_kind,
                            change.key.aspect,
                            change.key.source_id,
                        ),
                        (domain, operation_name, source_pk),
                    )
                    self.assertEqual(
                        change.observation.content_digest,
                        hashlib.sha256(original).hexdigest(),
                    )
                    self.assertIsNone(change.observation.source_revision)
                    self.assertIsNone(change.observation.client_ids)
                    if operation_name.endswith('.content'):
                        self.assertNotEqual(
                            change.observation.content_digest,
                            hashlib.sha256(self.blob).hexdigest(),
                        )
                        self.assertNotEqual(
                            change.observation.content_digest,
                            json.loads(original)['record']['metadata'][
                                'metadata_revision'
                            ],
                        )
                    configuration = self.configure(aspects=((domain, operation_name),))
                    (claim,) = self.claim(configuration)
                    self.assertEqual(
                        self.acknowledge(
                            prepared, change, configuration, claim
                        ).change_id,
                        change.change_id,
                    )
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)
        self.assertEqual(SourceChange.objects.count(), 6)

    def test_replay_cas_and_bulk_native_edit_do_not_invent_a_source_revision(self):
        """The existing owner owns CAS; lifecycle versions miss this native bulk edit."""
        from tasks.models import WorkOrder

        def operation():
            prepared = self.prepare()
            first = self.append(prepared)
            self.assertEqual(self.append(prepared), first)
            version = self.work.lifecycle_version
            WorkOrder.objects.filter(pk=self.work.pk).update(
                title='Changed synthetic direct source'
            )
            self.assertEqual(
                WorkOrder.objects.get(pk=self.work.pk).lifecycle_version, version
            )
            with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
                self.append(prepared)
            current = self.prepare()
            with self.assertRaises(shared_events.SharedConflictError):
                self.append(current)
            second = self.append(current, event_key='changed-source', expected_fence=1)
            self.assertEqual(second.previous, first.observation)
            self.assertEqual(second.fence, 2)
            self.assertIsNone(second.observation.source_revision)
            self.assertNotEqual(
                second.observation.content_digest, first.observation.content_digest
            )
            with self.assertRaises(shared_events.SharedConflictError):
                self.append(current, event_key='stale-fence', expected_fence=0)
            self.assertEqual(SourceChange.objects.count(), 2)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_current_role_grant_account_and_source_revoke_before_private_append(self):
        """Revoke one actual authority/binding at a time, then restore a positive."""
        from assets.models import ClientScopeGrant
        from tasks.models import WorkOrder
        from users.models import RuleSet

        for kind in ('role', 'grant', 'account', 'source'):
            with self.subTest(kind=kind):

                def operation(kind=kind):
                    prepared = self.prepare()
                    try:
                        if kind == 'role':
                            RuleSet.objects.filter(pk=self.rule.pk).update(
                                can_view=False
                            )
                        elif kind == 'grant':
                            self.grant.delete()
                        elif kind == 'account':
                            type(self.actor).objects.filter(pk=self.actor.pk).update(
                                is_active=False
                            )
                        else:
                            WorkOrder.objects.filter(pk=self.work.pk).update(
                                title='Changed source'
                            )
                        with patch.object(
                            shared_events,
                            'record_observation',
                            wraps=shared_events.record_observation,
                        ) as called:
                            with self.assertRaises(
                                self.delivery().SourceDeliveryUnavailableError
                            ):
                                self.append(prepared)
                            called.assert_not_called()
                    finally:
                        RuleSet.objects.filter(pk=self.rule.pk).update(can_view=True)
                        type(self.actor).objects.filter(pk=self.actor.pk).update(
                            is_active=True
                        )
                        WorkOrder.objects.filter(pk=self.work.pk).update(
                            title=self.work.title
                        )
                        if kind == 'grant':
                            self.grant = ClientScopeGrant.objects.create(
                                user=self.actor, client=self.tenant
                            )
                    self.assertEqual(self.append(prepared).fence, 1)
                    self.assertEqual(SourceChange.objects.count(), 1)
                    return True

                assert_literal_bool(self, self.run_boundary(operation), True)

    def test_session_revocation_and_invocation_exit_cannot_reuse_prepared_bytes(self):
        """An active user/grant is insufficient after credential or lifetime loss."""
        from assets.models import ClientScopeGrant

        def operation():
            prepared = self.prepare()
            self.session.delete(self.session.session_key)
            self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
            with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
                self.append(prepared)
            return prepared

        retained = self.run_boundary(operation)
        with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
            self.append(retained)
        self.assertEqual(SourceChange.objects.count(), 0)

    def test_same_reference_changed_file_bytes_refuse_old_handle_without_metadata_change(
        self,
    ):
        """A native editing token/file reference is not an original-byte revision."""
        self.prepare_operations()
        self.select_owner('workorder')
        changes = {
            'operation': 'work_document.content',
            'source_pk': self.attachment.pk,
            'max_bytes': 128,
        }

        def operation():
            prepared = self.prepare(**changes)
            metadata = self.read_json(
                'read_work_document_metadata_json', attachment_pk=self.attachment.pk
            )
            try:
                with self.attachment.attachment.storage.open(
                    self.attachment.attachment.name, 'wb'
                ) as handle:
                    handle.write(b'X' * len(self.blob))
                self.assertEqual(
                    self.read_json(
                        'read_work_document_metadata_json',
                        attachment_pk=self.attachment.pk,
                    ),
                    metadata,
                )
                with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
                    self.append(prepared)
                self.assertEqual(SourceChange.objects.count(), 0)
                self.assertEqual(self.append(self.prepare(**changes)).fence, 1)
            finally:
                with self.attachment.attachment.storage.open(
                    self.attachment.attachment.name, 'wb'
                ) as handle:
                    handle.write(self.blob)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_store_faults_are_sanitized_rolled_back_and_process_controls_preserved(
        self,
    ):
        """Ordinary faults disclose no body; fatal/control exceptions keep identity."""

        def operation():
            prepared = self.prepare()
            original = shared_events.record_observation
            for error in (
                RuntimeError('synthetic-source-fault-sentinel'),
                MemoryError(),
                KeyboardInterrupt(),
            ):
                with self.subTest(error=type(error).__name__):

                    def fault(*args, error=error, **kwargs):
                        original(*args, **kwargs)
                        raise error

                    with patch.object(
                        shared_events, 'record_observation', side_effect=fault
                    ) as called:
                        expected = (
                            self.delivery().SourceDeliveryError
                            if type(error) is RuntimeError
                            else type(error)
                        )
                        with self.assertRaises(expected) as raised:
                            self.append(prepared)
                        called.assert_called_once()
                        if type(error) is RuntimeError:
                            self.assertNotIn(
                                'synthetic-source-fault-sentinel', str(raised.exception)
                            )
                        else:
                            self.assertIs(raised.exception, error)
                    self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_revocation_during_private_result_validation_stops_readback_and_rolls_back(
        self,
    ):
        """The second metadata projection is fallible work before owner readback."""

        def operation():
            module = self.delivery()
            prepared = self.prepare()
            original = module.metadata_from_source_payload
            calls = []

            def revoked(payload):
                result = original(payload)
                calls.append(True)
                if len(calls) == 2:
                    self.grant.delete()
                return result

            with (
                patch.object(
                    module, 'metadata_from_source_payload', side_effect=revoked
                ),
                patch.object(
                    SourceChange.objects,
                    'select_related',
                    wraps=SourceChange.objects.select_related,
                ) as read,
            ):
                with self.assertRaises(module.SourceDeliveryUnavailableError):
                    self.append(prepared)
                self.assertEqual(len(calls), 2)
                read.assert_not_called()
            # This sequential mutation occurred inside the same outer rollback.
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_post_commit_record_refusal_preserves_exact_event_for_restored_replay(self):
        """A refusal after the outer metadata commit cannot erase or resubmit it."""
        from assets.models import ClientScopeGrant

        def operation():
            prepared = self.prepare()
            original = shared_events.shared_transaction
            depth = 0
            activated = []

            @contextmanager
            def after_commit(*args, **kwargs):
                nonlocal depth
                depth += 1
                with original(*args, **kwargs):
                    yield
                depth -= 1
                if depth == 0 and not activated:
                    activated.append(True)
                    self.grant.delete()

            with patch.object(
                shared_events, 'shared_transaction', side_effect=after_commit
            ):
                with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
                    self.append(prepared)
            self.assertEqual(activated, [True])
            self.assertEqual(SourceChange.objects.count(), 1)
            event_id = SourceChange.objects.get().pk
            self.grant = ClientScopeGrant.objects.create(
                user=self.actor, client=self.tenant
            )
            self.assertEqual(self.append(prepared).change_id, event_id)
            self.assertEqual(SourceChange.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_foreign_coherent_decode_cannot_ack_original_consumer_claim(self):
        """A valid same-event result for another consumer is not the original binding."""

        def operation():
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            foreign = configure_consumer(
                self.namespace.pk,
                'foreign-s3-consumer',
                aspects=(('work', 'work.core'),),
                configuration_epoch=uuid4(),
            )
            (foreign_claim,) = self.claim(foreign)
            substituted = delivery_change_from_claim(
                environment='s3-test',
                worker_key='private-s3-worker',
                configuration=foreign,
                claim=foreign_claim,
            )
            self.assertEqual(substituted.change.event_id, change.change_id)
            with patch.object(
                s7_changes, 'delivery_change_from_claim', return_value=substituted
            ) as called:
                with self.assertRaises(self.delivery().SourceDeliveryError):
                    self.acknowledge(prepared, change, configuration, claim)
                called.assert_called_once()
            self.assertEqual(
                EventDelivery.objects.filter(state='acknowledged').count(), 0
            )
            self.assertEqual(
                self.acknowledge(prepared, change, configuration, claim).change_id,
                change.change_id,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_post_commit_ack_refusal_preserves_receipt_without_automatic_retry(self):
        """Source loss cannot retroactively undo S5's independently committed ack."""
        from assets.models import ClientScopeGrant

        def operation():
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            original = shared_delivery.ack_change

            def revoked(*args, **kwargs):
                receipt = original(*args, **kwargs)
                self.grant.delete()
                return receipt

            with patch.object(
                shared_delivery, 'ack_change', side_effect=revoked
            ) as called:
                with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
                    self.acknowledge(prepared, change, configuration, claim)
                called.assert_called_once()
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'acknowledged'
            )
            self.assertEqual(SourceChange.objects.count(), 1)
            self.grant = ClientScopeGrant.objects.create(
                user=self.actor, client=self.tenant
            )
            self.assertEqual(
                self.acknowledge(prepared, change, configuration, claim).change_id,
                change.change_id,
            )
            self.assertEqual(SourceChange.objects.count(), 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_foreign_carriers_are_refused_before_properties_or_owner_ack(self):
        """Opaque caller objects never become source/claim capabilities via duck typing."""
        from tasks import integration_s3_reader as reader

        accessed = []

        class Foreign:
            @property
            def outcome(self):
                accessed.append('outcome')
                raise AssertionError('foreign source property was evaluated')

            @property
            def consumer_id(self):
                accessed.append('consumer')
                raise AssertionError('foreign claim property was evaluated')

        def operation():
            module = self.delivery()
            with patch.object(
                reader, 'read_source_for_current_ai', return_value=Foreign()
            ):
                with self.assertRaises(module.SourceDeliveryUnavailableError):
                    self.prepare()
            prepared = self.prepare()
            change = self.append(prepared)
            with patch.object(
                shared_delivery, 'ack_change', wraps=shared_delivery.ack_change
            ) as called:
                with self.assertRaises(module.SourceDeliveryError):
                    prepared.acknowledge(
                        change,
                        configuration=Foreign(),
                        claim=Foreign(),
                        worker_key='private-s3-worker',
                    )
                called.assert_not_called()
            self.assertEqual(accessed, [])
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_valid_but_substituted_key_and_change_cannot_escape_owner_binding(self):
        """Schema validity cannot replace source/namespace/fence equality or readback."""

        def operation():
            module = self.delivery()
            prepared = self.prepare()
            from integration_state.shared_contracts import SharedSourceKey

            key = SharedSourceKey(
                namespace_id=self.namespace.pk,
                environment='s3-test',
                epoch=self.namespace.epoch,
                entity_kind='work',
                aspect='work.core',
                source_id=self.work.pk,
            )
            for changes in (
                {'namespace_id': self.namespace.pk + 1},
                {'source_id': self.work.pk + 1},
                {'aspect': 'maintenance.direct'},
                {'declared_incarnation': 'unproved-incarnation'},
            ):
                with self.subTest(fields=tuple(changes)):
                    substituted_key = replace(key, **changes)
                    with (
                        patch.object(
                            module, 'SharedSourceKey', return_value=substituted_key
                        ),
                        patch.object(
                            shared_events,
                            'record_observation',
                            wraps=shared_events.record_observation,
                        ) as called,
                    ):
                        with self.assertRaises(module.SourceDeliveryError):
                            self.append(prepared)
                        called.assert_not_called()
            self.assertEqual(SourceChange.objects.count(), 0)
            original = shared_events.record_observation

            def substituted(*args, **kwargs):
                return replace(original(*args, **kwargs), fence=2)

            with patch.object(
                shared_events, 'record_observation', side_effect=substituted
            ):
                with self.assertRaises(module.SourceDeliveryError):
                    self.append(prepared)
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(prepared).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_receipt_validation_revocation_stops_post_commit_private_readback(self):
        """Reauthorize after the last receipt validator, not only after S5 commits."""
        from assets.models import ClientScopeGrant
        from integration_state.shared_contracts import DeliveryAcknowledgement

        def operation():
            prepared = self.prepare()
            change = self.append(prepared)
            configuration = self.configure()
            (claim,) = self.claim(configuration)
            original = DeliveryAcknowledgement.__post_init__
            validated = []

            def revoked(receipt):
                original(receipt)
                validated.append(True)
                if len(validated) == 2:
                    self.grant.delete()

            with (
                patch.object(DeliveryAcknowledgement, '__post_init__', new=revoked),
                patch.object(
                    EventDelivery.objects, 'filter', wraps=EventDelivery.objects.filter
                ) as read,
            ):
                with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
                    self.acknowledge(prepared, change, configuration, claim)
                self.assertEqual(len(validated), 2)
                read.assert_not_called()
            self.assertEqual(
                EventDelivery.objects.get(pk=claim.delivery_id).state, 'acknowledged'
            )
            self.grant = ClientScopeGrant.objects.create(
                user=self.actor, client=self.tenant
            )
            self.assertEqual(
                self.acknowledge(prepared, change, configuration, claim).change_id,
                change.change_id,
            )
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_scalar_aliases_unsupported_operations_and_future_outcomes_never_append(
        self,
    ):
        """Neither permissive equality nor the same class admits a future source schema."""
        from tasks import integration_s3_reader as reader

        class Alias(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        def operation():
            self.assertEqual(self.ai_read().outcome.state, 'available')
            for changes in (
                {'operation': 'unsupported.operation'},
                {'operation': Alias('work.core')},
                {'environment': Alias('s3-test')},
                {'source_pk': True},
                {'source_pk': str(self.work.pk)},
            ):
                with self.subTest(changes=tuple(changes)):
                    with patch.object(
                        reader,
                        'read_source_for_current_ai',
                        wraps=reader.read_source_for_current_ai,
                    ) as read:
                        with self.assertRaises(self.delivery().SourceDeliveryError):
                            self.prepare(**changes)
                        read.assert_not_called()
            observed = self.ai_read()
            outcome = observed.outcome
            for field, value in (
                ('schema_version', 'aimms.s3.read-result.future'),
                ('population_complete', True),
                ('revision_complete', True),
            ):
                with self.subTest(field=field):
                    before = getattr(outcome, field)
                    try:
                        object.__setattr__(outcome, field, value)
                        with patch.object(
                            reader, 'read_source_for_current_ai', return_value=observed
                        ):
                            with self.assertRaises(self.delivery().SourceDeliveryError):
                                self.prepare()
                    finally:
                        object.__setattr__(outcome, field, before)
            self.assertEqual(SourceChange.objects.count(), 0)
            self.assertEqual(self.append(self.prepare()).fence, 1)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)


class RealPolicySourceDeliveryTests(SourceDeliveryFixture, TransactionTestCase):
    """No historical-attribution filter is patched for these native refusals."""

    def test_actual_positive_actor_and_native_scope_still_withhold_unproved_history(
        self,
    ):
        """Authentication/native visibility do not supply missing historical proof."""
        from assets.integration_authority import integration_actor_from_current_ai
        from tasks.scope import scoped_work_orders

        def operation():
            self.assertEqual(
                integration_actor_from_current_ai(required_role='work_order').pk,
                self.actor.pk,
            )
            self.assertTrue(
                scoped_work_orders(self.actor).filter(pk=self.work.pk).exists()
            )
            with (
                patch.object(shared_events, 'record_observation') as stored,
                patch('django.core.files.storage.default_storage.open') as opened,
            ):
                with self.assertRaises(self.delivery().SourceDeliveryUnavailableError):
                    self.prepare()
                stored.assert_not_called()
                opened.assert_not_called()
            self.assertEqual(SourceChange.objects.count(), 0)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)
