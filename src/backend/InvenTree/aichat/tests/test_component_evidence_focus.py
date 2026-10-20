"""P02 exact reference focus uses native scope at acknowledgement and intake."""

import hashlib
from datetime import datetime, timezone
from unittest import mock
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings

from tasks.closeout_models import CloseoutReading

from aichat.models import AttachmentIngest, TurnModality, TurnState
from aichat.services.work_focus import WorkFocusError, public_focus, reading_revision
from aichat.tests import test_work_focus as work_focus_tests
from assets.equipment_positions import position_reference
from assets.registry_models import AssetComponent
from common.attachment_revision import attachment_revision
from common.models import Attachment
from part.models import Part


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_CLOSEOUT_WIZARD_ENABLED=True,
)
class ComponentEvidenceFocusTests(TestCase):
    """Normal readers, real receipts, real durable turns and live source records."""

    setUp = work_focus_tests.WorkFocusTests.setUp
    begin = work_focus_tests.WorkFocusTests.begin

    def component(self, position='bearing', slot=None, machine=None):
        """Read the same reference revision as the position API."""
        from assets.models import AssetMachine

        machine = machine or self.machine
        if machine.asset_type != 'pump':
            station = AssetMachine.objects.create(
                name='Focus station',
                client=machine.client,
                asset_type='pumphouse',
                source_namespace='p02',
                source_entity_uuid=uuid4(),
            )
            machine.asset_type = 'pump'
            machine.parent = station
            machine.source_key = 'p02-pump'
            machine.save()
        return position_reference(machine, position, slot)

    def ack(self, component=None, evidence=None, standalone=False, **kwargs):
        """Acknowledge the current native selection."""
        return self.repository.acknowledge_work_focus(
            self.thread.pk,
            None if standalone else self.work.pk,
            expected_version=None if standalone else self.work.lifecycle_version,
            component=component,
            evidence=evidence,
            **kwargs,
        )

    def test_standalone_component_uses_existing_receipt_and_public_identity(self):
        """Reference focus needs no invented Work record or case engine."""
        c = self.component()
        ack = self.ack(component=c, standalone=True)
        self.assertEqual(
            public_focus(ack['focus']),
            {
                'schema_version': 3,
                'kind': 'reference_position',
                'work_order_id': None,
                'lifecycle_version': None,
                'machine_id': self.machine.pk,
                'component': c,
            },
        )
        result = self.begin(ack['receipt'])
        self.assertEqual(result.work_focus_snapshot['component'], c)
        self.assertEqual(
            self.repository.get_turn_work_focus(self.thread.pk, result.turn.pk)[
                'focus'
            ],
            ack['focus'],
        )

    def test_invalid_foreign_unmapped_and_stale_position_rejected(self):
        """Unknown IDs and mismatched mapping revisions all refuse generically."""
        c = self.component()
        for change in [
            {'position_id': 'made-up'},
            {'machine_id': True},
            {'rule': 'reference_positions.v2'},
            {'slot_code': 'foreign-slot'},
            {'revision': '0' * 64},
        ]:
            with (
                self.subTest(change=change),
                self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'),
            ):
                self.ack(component={**c, **change}, standalone=True)

    def test_foreign_machine_with_its_correct_revision_requires_scope(self):
        """A valid reference fails only for scope, and succeeds after a grant."""
        from assets.models import ClientScopeGrant

        foreign = self.foreign_work.machine
        component = self.component(machine=foreign)
        self.assertEqual(component, position_reference(foreign, 'bearing'))
        with self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'):
            self.ack(component=component, standalone=True)
        ClientScopeGrant.objects.create(user=self.actor, client=foreign.client)
        self.assertEqual(
            self.ack(component=component, standalone=True)['focus']['component'],
            component,
        )

    def test_native_slot_is_exact_and_replacement_refuses_intake(self):
        """A code on a different family and a replaced slot cannot bind."""
        part = Part.objects.create(name='Bearing', IPN='PS-MOTOR-DE-BRG')
        slot = AssetComponent.objects.create(
            machine=self.machine, part=part, code='bearing:1', name='Bearing'
        )
        c = self.component(slot=slot.code)
        receipt = self.ack(component=c)['receipt']
        slot.delete()
        AssetComponent.objects.create(
            machine=self.machine, part=part, code='bearing:1', name='Replacement'
        )
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        with self.assertRaises(WorkFocusError):
            self.ack(component=c)
        with self.assertRaises(ValueError):
            position_reference(self.machine, 'motor', 'bearing:1')

    def test_revoked_machine_scope_and_role_refuse_intake(self):
        """Fresh checks after acknowledgement create no question turn."""
        receipt = self.ack(component=self.component(), standalone=True)['receipt']
        self.grant.delete()
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        from aichat.models import ChatTurn

        self.assertFalse(ChatTurn.objects.exists())

    def reading(self, work=None):
        """Create a native recorded zero, preserving its qualifiers."""
        return CloseoutReading.objects.create(
            work_order=work or self.work,
            label='Recorded temperature',
            raw_text='0',
            value=0,
            unit='degC',
            normalization_rule_version='test.v1',
            recorded_by=self.actor,
        )

    def test_reading_owner_revision_and_disposition_rechecked(self):
        """A reading FK, content and disposition are exact evidence facts."""
        r = self.reading()
        e = {'kind': 'closeout_reading', 'id': r.pk, 'revision': reading_revision(r)}
        receipt = self.ack(evidence=e)['receipt']
        r.verification_state = 'failed'
        r.save(update_fields=['verification_state'])
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        with self.assertRaises(WorkFocusError):
            self.ack(evidence=e)
        foreign = self.reading(self.foreign_work)
        with self.assertRaises(WorkFocusError):
            self.ack(
                evidence={**e, 'id': foreign.pk, 'revision': reading_revision(foreign)}
            )

    def test_attachment_scope_and_native_metadata_revision(self):
        """Readable Work metadata is enough for attachment, never for media."""
        a = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work.pk,
            comment='source',
            link='https://example.invalid/evidence',
        )
        e = {'kind': 'attachment', 'id': a.pk, 'revision': attachment_revision(a)}
        receipt = self.ack(evidence=e)['receipt']
        with self.assertRaises(WorkFocusError):
            self.ack(evidence={**e, 'kind': 'media'})
        a.comment = 'corrected'
        a.save(update_fields=['comment'])
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        a.model_id = self.foreign_work.pk
        a.save(update_fields=['model_id'])
        with self.assertRaises(WorkFocusError):
            self.ack(evidence={**e, 'revision': attachment_revision(a)})

    def test_readable_sibling_work_attachment_cannot_bind_to_this_work(self):
        """Native readability is necessary but does not establish Work binding."""
        from common.api import _maintenance_attachment_scope

        attachment = Attachment.objects.create(
            model_type='workorder',
            model_id=self.second_work.pk,
            link='https://example.invalid/sibling',
        )
        self.assertTrue(
            _maintenance_attachment_scope(
                Attachment.objects.filter(pk=attachment.pk), self.actor
            ).exists()
        )
        self.assertTrue(attachment.check_permission('view', self.actor))
        evidence = {
            'kind': 'attachment',
            'id': attachment.pk,
            'revision': attachment_revision(attachment),
        }
        with self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'):
            self.ack(evidence=evidence)
        self.repository.acknowledge_work_focus(
            self.thread.pk,
            self.second_work.pk,
            expected_version=self.second_work.lifecycle_version,
            evidence=evidence,
        )

    def media_source(self):
        """Build the indexed source and its real native ingestion stamp."""
        from aichat.services.attachment_ingestion import _build_stamp, storage_mtime

        body = b'P02 synthetic media source'
        attachment = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work.pk,
            attachment=ContentFile(body, name='p02-focus.mp4'),
        )
        self.addCleanup(default_storage.delete, attachment.attachment.name)
        digest = hashlib.sha256(body).hexdigest()
        ingest = AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='workorder',
            model_id=self.work.pk,
            state='indexed',
            pipeline='video',
            source_sha256=digest,
        )
        # Production ingestion stamps the metadata using the same builder.
        Attachment.objects.filter(pk=attachment.pk).update(
            metadata={
                'ai_ingest': _build_stamp(
                    attachment, digest, 'indexed', mtime=storage_mtime(attachment)
                )
            }
        )
        attachment.refresh_from_db()
        return (
            attachment,
            ingest,
            {'kind': 'media', 'id': attachment.pk, 'revision': digest},
        )

    def test_media_revision_checks_index_and_storage_metadata_without_reading(self):
        """Focus checks read metadata; stale storage and inactive indexes refuse."""
        attachment, ingest, evidence = self.media_source()
        with mock.patch.object(
            default_storage, 'open', side_effect=AssertionError('focus read bytes')
        ):
            receipt = self.ack(evidence=evidence)['receipt']
            self.repository.verify_work_focus(self.thread.pk, receipt)
            self.begin(receipt)
            with mock.patch.object(
                default_storage, 'size', return_value=attachment.file_size + 1
            ):
                with self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'):
                    self.ack(evidence=evidence)
            with mock.patch.object(
                default_storage,
                'get_modified_time',
                return_value=datetime(2000, 1, 1, tzinfo=timezone.utc),
            ):
                with self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'):
                    self.ack(evidence=evidence)
            ingest.state = 'failed'
            ingest.save(update_fields=['state'])
            with self.assertRaises(WorkFocusError):
                self.repository.verify_work_focus(self.thread.pk, receipt)

    def answer(self, receipt, key):
        """Persist a real completed answer for reload checks."""
        run = self.begin(
            receipt, key=key, fingerprint=hashlib.sha256(key.encode()).hexdigest()
        )
        self.repository.terminal(
            run.turn.pk,
            state=TurnState.COMPLETE,
            canonical_result={'message': 'Answer'},
            output_content='Answer',
        )
        run.turn.refresh_from_db()
        return run.turn

    def test_reload_resolves_each_media_snapshot_once_without_byte_reads(self):
        """Two saved answers about one snapshot cost one bounded media check."""
        from aichat.services import work_focus

        _, _, evidence = self.media_source()
        receipt = self.ack(component=self.component(), evidence=evidence)['receipt']
        one, two = self.answer(receipt, 'media-one'), self.answer(receipt, 'media-two')
        with (
            mock.patch.object(
                default_storage,
                'open',
                side_effect=AssertionError('reload hashed media'),
            ),
            mock.patch.object(
                work_focus,
                'indexed_media_revision',
                wraps=work_focus.indexed_media_revision,
            ) as media,
        ):
            states = self.repository.work_focus_states(
                self.thread.pk, [one.output_message_id, two.output_message_id]
            )
            self.assertEqual(media.call_count, 1)
            self.assertEqual(
                states,
                {one.output_message_id: 'current', two.output_message_id: 'current'},
            )
            self.assertEqual(
                states.references[one.output_message_id],
                {'component': 'current', 'evidence': 'current'},
            )
            saved = self.repository.get_turn_work_focus(self.thread.pk, one.pk)
            self.assertFalse(saved['stale'])
            self.assertEqual(saved['reference_states']['evidence'], 'current')

    def test_media_missing_stamp_or_storage_metadata_is_unavailable(self):
        """No expensive fallback or invented current state for unknown metadata."""
        attachment, _, evidence = self.media_source()
        with mock.patch.object(
            default_storage,
            'open',
            side_effect=AssertionError('metadata fallback read bytes'),
        ):
            with mock.patch.object(
                default_storage, 'size', side_effect=OSError('private storage path')
            ):
                with self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'):
                    self.ack(evidence=evidence)
            for metadata in ({}, [], 'unknown', {'ai_ingest': []}):
                Attachment.objects.filter(pk=attachment.pk).update(metadata=metadata)
                with (
                    self.subTest(metadata=metadata),
                    self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'),
                ):
                    self.ack(evidence=evidence)

    def test_reload_states_include_component_and_evidence_change_and_unavailability(
        self,
    ):
        """The saved answer is never relabelled current after source changes."""
        r = self.reading()
        e = {'kind': 'closeout_reading', 'id': r.pk, 'revision': reading_revision(r)}
        run = self.begin(self.ack(component=self.component(), evidence=e)['receipt'])
        self.repository.terminal(
            run.turn.pk,
            state=TurnState.COMPLETE,
            canonical_result={'message': 'Answer'},
            output_content='Answer',
        )
        run.turn.refresh_from_db()
        message_id = run.turn.output_message_id
        self.assertEqual(
            self.repository.work_focus_states(self.thread.pk, [message_id])[message_id],
            'current',
        )
        r.raw_text = '1'
        r.save(update_fields=['raw_text'])
        states = self.repository.work_focus_states(self.thread.pk, [message_id])
        self.assertEqual(states[message_id], 'current')
        self.assertEqual(states.references[message_id]['evidence'], 'changed')
        r.delete()
        states = self.repository.work_focus_states(self.thread.pk, [message_id])
        self.assertEqual(states[message_id], 'current')
        self.assertEqual(
            states.references[message_id],
            {'component': 'current', 'evidence': 'unavailable'},
        )
        saved = self.repository.get_turn_work_focus(self.thread.pk, run.turn.pk)
        self.assertEqual(saved['focus']['work_order_id'], self.work.pk)
        self.assertTrue(saved['stale'])
        self.assertEqual(saved['reference_states']['evidence'], 'unavailable')

    def test_request_plus_component_preserves_request_version(self):
        """Schema three composes with the existing exact investigation request."""
        from tasks.investigation_models import InvestigationRequest

        request = InvestigationRequest.objects.create(
            work_order=self.work,
            purpose='check',
            created_by=self.actor,
            updated_by=self.actor,
        )
        ack = self.ack(
            component=self.component(),
            request_id=request.pk,
            request_version=request.version,
        )
        self.assertEqual(public_focus(ack['focus'])['request_id'], request.pk)
        self.begin(ack['receipt'])

    def test_reading_revision_is_stable_across_create_and_reload(self):
        """Decimal scale normalization keeps a just-recorded row focusable."""
        row = self.reading()
        initial = reading_revision(row)
        from tasks.closeout_serializers import CloseoutReadingSerializer

        self.assertEqual(CloseoutReadingSerializer(row).data['focus_revision'], initial)
        row.refresh_from_db()
        self.assertEqual(initial, reading_revision(row))

    def test_reference_reload_states_are_independent(self):
        """A changed reading never labels a current component as changed."""
        from aichat.services.work_focus import reference_states

        row = self.reading()
        evidence = {
            'kind': 'closeout_reading',
            'id': row.pk,
            'revision': reading_revision(row),
        }
        snapshot = self.ack(component=self.component(), evidence=evidence)['focus']
        row.raw_text = '1'
        row.save(update_fields=['raw_text'])
        self.assertEqual(
            reference_states(self.actor.pk, snapshot),
            {'component': 'current', 'evidence': 'changed'},
        )
        row.delete()
        self.assertEqual(
            reference_states(self.actor.pk, snapshot),
            {'component': 'current', 'evidence': 'unavailable'},
        )

    def test_media_storage_failure_is_generic_refusal(self):
        """Storage outages cannot expose a path or break thread reload."""
        a = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work.pk,
            link='https://example.invalid/evidence',
        )
        with mock.patch(
            'aichat.services.work_focus.indexed_media_revision',
            side_effect=WorkFocusError('work_focus_rejected'),
        ):
            with self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'):
                self.ack(evidence={'kind': 'media', 'id': a.pk, 'revision': 'a' * 64})

    def test_malformed_evidence_and_component_never_become_unfocused(self):
        """Unknown fields and noncanonical values fail closed."""
        for evidence in [
            {'kind': [], 'id': 1, 'revision': 'a' * 64},
            {'kind': 'attachment', 'id': True, 'revision': 'a' * 64},
            {},
        ]:
            with self.subTest(evidence=evidence), self.assertRaises(WorkFocusError):
                self.ack(evidence=evidence)
        for component in [{}, {**self.component(), 'extra': True}]:
            with self.subTest(component=component), self.assertRaises(WorkFocusError):
                self.ack(component=component, standalone=True)

    def test_evidence_stays_current_when_selected_slot_is_deleted(self):
        """Work and standalone evidence states do not borrow slot availability."""
        from aichat.services.work_focus import reference_states

        part = Part.objects.create(name='Motor', IPN='PS-MOTOR')
        for standalone in (False, True):
            with self.subTest(standalone=standalone):
                slot = AssetComponent.objects.create(
                    machine=self.machine, part=part, code='motor:1', name='Motor'
                )
                component = self.component('motor', slot.code)
                a = Attachment.objects.create(
                    model_type='assetmachine' if standalone else 'workorder',
                    model_id=self.machine.pk if standalone else self.work.pk,
                    comment='Source evidence',
                    link='https://example.invalid/evidence',
                )
                evidence = {
                    'kind': 'attachment',
                    'id': a.pk,
                    'revision': attachment_revision(a),
                }
                snapshot = self.ack(
                    component=component, evidence=evidence, standalone=standalone
                )['focus']
                turn = self.answer(
                    self.ack(
                        component=component, evidence=evidence, standalone=standalone
                    )['receipt'],
                    f'slot-{standalone}',
                )
                slot.delete()
                states = self.repository.work_focus_states(
                    self.thread.pk, [turn.output_message_id]
                )
                self.assertEqual(states[turn.output_message_id], 'current')
                self.assertEqual(
                    self.repository.get_turn_work_focus(self.thread.pk, turn.pk)[
                        'reference_states'
                    ]['component'],
                    'unavailable',
                )
                self.assertEqual(
                    reference_states(self.actor.pk, snapshot),
                    {'component': 'unavailable', 'evidence': 'current'},
                )

    def test_disabled_closeout_surface_refuses_reading_focus(self):
        """A hidden evidence provider cannot be revived by a focus receipt."""
        row = self.reading()
        with (
            override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False),
            self.assertRaises(WorkFocusError),
        ):
            self.ack(
                evidence={
                    'kind': 'closeout_reading',
                    'id': row.pk,
                    'revision': reading_revision(row),
                }
            )


class ReferenceFocusWireTests(TestCase):
    """Exercise the real HTTP mirror and workflow without a live model."""

    def test_work_coordinates_remain_strict_and_paired(self):
        """Standalone references must not loosen the existing Work boundary."""
        from pydantic import ValidationError

        from ai.core.app import WorkFocusAcknowledgeRequest

        for body in (
            {'work_order_id': '73', 'expected_version': 4},
            {'work_order_id': True, 'expected_version': 4},
            {'work_order_id': 73, 'expected_version': 4.0},
            {'work_order_id': 0, 'expected_version': 4},
            {'work_order_id': 73, 'expected_version': -1},
            {'work_order_id': 73},
            {'expected_version': 4},
        ):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                WorkFocusAcknowledgeRequest.model_validate(body)

    def test_typed_wire_preserves_exact_references(self):
        """No boundary hash or unknown schema field crosses the public mirror."""
        from pydantic import ValidationError

        from ai.core.analysis.wire import WorkFocusIdentity

        focus = {
            'schema_version': 3,
            'kind': 'reference_position',
            'work_order_id': None,
            'lifecycle_version': None,
            'machine_id': 18,
            'component': {
                'machine_id': 18,
                'rule': 'reference_positions.v1',
                'position_id': 'bearing',
                'slot_code': None,
                'revision': 'a' * 64,
            },
        }
        value = WorkFocusIdentity.model_validate(focus).model_dump(exclude_unset=True)
        self.assertEqual(value, focus)
        with self.assertRaises(ValidationError):
            WorkFocusIdentity.model_validate({**focus, 'boundary_hash': 'private'})

    def test_existing_workflow_keeps_reference_identity_fenced_and_honest(self):
        """A position is not a fitted part and evidence IDs are not readings."""
        from ai.core.workflows.wf8_lookup import T1LookupWorkflow as LookupWorkflow

        focus = {
            'schema_version': 3,
            'kind': 'reference_position',
            'work_order_id': None,
            'lifecycle_version': None,
            'machine_id': 18,
            'component': {
                'machine_id': 18,
                'rule': 'reference_positions.v1',
                'position_id': 'bearing',
                'slot_code': None,
                'revision': 'a' * 64,
            },
            'evidence': {'kind': 'media', 'id': 9, 'revision': 'b' * 64},
        }
        self.assertEqual(LookupWorkflow._work_focus({'work_focus': focus}), focus)
        messages = LookupWorkflow._with_work_focus(
            'what is this?', {'work_focus': focus}
        )
        note = messages[-1].contents[-1].text
        self.assertIn('Untrusted reference identifiers', note)
        self.assertIn('not a verified fitted physical part', note)
        self.assertIn('source facts are unavailable', note)
        self.assertNotIn('work order None', note)

    def test_existing_acknowledgement_route_forwards_references_and_hides_boundary(
        self,
    ):
        """The existing HTTP route accepts standalone focus without a new endpoint."""
        from asgiref.sync import async_to_sync

        from ai.core.app import WorkFocusAcknowledgeRequest, acknowledge_work_focus

        component = {
            'machine_id': 18,
            'rule': 'reference_positions.v1',
            'position_id': 'bearing',
            'slot_code': None,
            'revision': 'a' * 64,
        }
        snapshot = {
            'schema_version': 3,
            'kind': 'reference_position',
            'work_order_id': None,
            'lifecycle_version': None,
            'machine_id': 18,
            'component': component,
            'boundary_hash': 'private',
        }
        repository = mock.Mock()
        repository.acknowledge_work_focus.return_value = {
            'schema_version': 1,
            'thread_id': 't',
            'focus': snapshot,
            'receipt': 'opaque',
            'expires_in': 300,
        }
        with (
            mock.patch('ai.core.app._principal', return_value=object()),
            mock.patch('ai.core.app._repository', return_value=repository),
        ):
            result = async_to_sync(acknowledge_work_focus)(
                't', WorkFocusAcknowledgeRequest(component=component)
            )
        repository.get_or_create.assert_called_once_with('t')
        repository.acknowledge_work_focus.assert_called_once_with(
            't', None, expected_version=None, component=component, evidence=None
        )
        self.assertEqual(result['focus'], public_focus(snapshot))
        self.assertNotIn('boundary_hash', result['focus'])

    def test_reference_text_and_uploads_use_their_existing_consumers(self):
        """Reference-only text uses lookup; uploaded files still reach wf6."""
        from types import SimpleNamespace

        from ai.core.turn.execution import _effective_pinned_workflow
        from ai.core.workflows.wf8_lookup import T1LookupWorkflow

        references = {
            'component': {
                'machine_id': 18,
                'rule': 'reference_positions.v1',
                'position_id': 'bearing',
                'slot_code': None,
                'revision': 'a' * 64,
            },
            'evidence': {'kind': 'closeout_reading', 'id': 51, 'revision': 'b' * 64},
        }
        for reference, coordinate in references.items():
            run = SimpleNamespace(
                server_pinned_workflow=None,
                modality=TurnModality.TEXT,
                work_focus={'schema_version': 3, reference: coordinate},
                metadata={},
            )
            self.assertEqual(_effective_pinned_workflow(run), 'wf8')
            run.metadata = {
                'uploaded_files': [
                    {
                        'file_id': 'thread/report.pdf',
                        'filename': 'report.pdf',
                        'path': '/private/source',
                        'size': 42,
                    }
                ]
            }
            self.assertEqual(_effective_pinned_workflow(run), 'wf6')
        # Request focus keeps explicit publication and explicitly acknowledges
        # the upload without claiming its contents have been interpreted.
        focus = {
            'schema_version': 3,
            'kind': 'work_order',
            'work_order_id': 73,
            'request_id': 5,
            'request_version': 2,
        }
        run.work_focus = focus
        self.assertEqual(_effective_pinned_workflow(run), 'wf8')
        note = (
            T1LookupWorkflow
            ._with_work_focus('Record', {'work_focus': focus, **run.metadata})[-1]
            .contents[-1]
            .text
        )
        self.assertIn('thread/report.pdf', note)
        self.assertIn('File content has not been interpreted', note)
        self.assertNotIn('/private/source', note)
        run.server_pinned_workflow = 'wf7'
        self.assertEqual(_effective_pinned_workflow(run), 'wf7')
