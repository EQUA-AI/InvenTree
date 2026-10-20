"""O14 private chat evidence reaches Work only through an exact approved review."""

import hashlib
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient
from tasks.investigation_models import InvestigationArtifact, InvestigationRequest
from tasks.models import WorkOrderCommand, WorkOrderEvent
from tasks.services.investigation_requests import command

from ai.core.auth import AIBoundaryPolicy, principal_for_user
from aichat.models import ChatActionProposal, TurnModality
from aichat.services import proposals
from aichat.services.scope_strings import scope_strings
from aichat.services.threads import ThreadRepository
from aichat.tests.test_work_focus import WorkFocusTests


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class InvestigationProposalTests(TestCase):
    """Real source turns, signed request focus and native command/storage paths."""

    def setUp(self):
        """Grant normal scoped technician rights and mint one acknowledged turn."""
        WorkFocusTests.setUp(self)
        policy = mock.patch.object(
            AIBoundaryPolicy,
            'from_settings',
            return_value=AIBoundaryPolicy(
                single_site_policy_key='site:focus',
                policy_version='test',
                signed_subject_max_age_seconds=300,
                signed_subject_salt='synthetic-investigation',
                signed_subject_audience='synthetic-investigation',
                allowed_origins=(),
            ),
        )
        policy.start()
        self.addCleanup(policy.stop)
        self.role.can_change = True
        self.role.save()
        self.scope_key, self.scope_hash = scope_strings(self.actor)
        self.repository = ThreadRepository(
            self.actor, principal_for_user(self.actor).scope
        )
        self.thread, _ = self.repository.get_or_create()
        self.row = InvestigationRequest.objects.create(
            work_order=self.work,
            purpose='Synthetic decision',
            next_check='Synthetic check',
            required_artifacts=[],
            created_by=self.actor,
            updated_by=self.actor,
        )
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.media = override_settings(MEDIA_ROOT=self.temp.name)
        self.media.enable()
        self.addCleanup(self.media.disable)
        self.turn = self.begin()

    def begin(self, metadata=None, key='evidence-turn', component=None, evidence=None):
        """The source snapshot is created through the same signed flow as chat."""
        self.row.refresh_from_db()
        receipt = self.repository.acknowledge_work_focus(
            self.thread.pk,
            self.work.pk,
            expected_version=self.work.lifecycle_version,
            request_id=self.row.pk,
            request_version=self.row.version,
            **({'component': component} if component is not None else {}),
            **({'evidence': evidence} if evidence is not None else {}),
        )
        return self.repository.begin_turn(
            self.thread.pk,
            content='Synthetic technician evidence',
            modality=TurnModality.TEXT,
            trusted_context={},
            modality_metadata=metadata or {},
            idempotency_key=key,
            request_fingerprint=hashlib.sha256(key.encode()).hexdigest(),
            correlation_id='source-correlation',
            work_focus_receipt=receipt['receipt'],
        ).turn

    def create(self, **over):
        """Prepare a proposal, never call the artifact command implicitly."""
        params = {
            'owner': self.actor,
            'scope_key': self.scope_key,
            'scope_hash': self.scope_hash,
            'action_type': 'investigation.artifact',
            'work_order_id': self.work.pk,
            'reason': 'Synthetic review',
            'idempotency_key': 'artifact-proposal',
            'policy_version': 'investigation-publication-v1',
            'thread_id': self.thread.pk,
            'source_turn_id': self.turn.pk,
            'intent': {
                'request_id': self.row.pk,
                'request_version': self.row.version,
                'kind': 'observation',
                'payload': {'text': 'Technician observation'},
            },
        }
        params.update(over)
        return proposals.create_proposal(**params)

    def approve(self, proposal, **over):
        """Only this authenticated approval executes, with exact preview identity."""
        params = {
            'owner': self.actor,
            'scope_hash': self.scope_hash,
            'proposal_id': proposal.pk,
            'expected_preview_hash': proposal.preview_hash,
        }
        params.update(over)
        return proposals.confirm_proposal(**params)

    def upload(self, name, contents):
        """A synthetic private file belongs to this exact source turn."""
        root = Path(self.temp.name) / 'ai_uploads' / self.thread.pk
        root.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(contents)
        file_id = f'{self.thread.pk}/{name}'
        self.turn = self.begin(
            {
                'uploaded_files': [
                    {
                        'file_id': file_id,
                        'filename': name,
                        'path': '/untrusted/ignored',
                        'extension': Path(name).suffix,
                        'size': len(contents),
                    }
                ]
            },
            key=f'file-{name}',
        )
        return file_id

    def test_schema_three_request_focus_preserves_explicit_publication(self):
        """Adding a reference selection never bypasses or breaks approval."""
        from aichat.tests import test_component_evidence_focus as reference_tests

        component = reference_tests.ComponentEvidenceFocusTests.component(self)
        self.turn = self.begin(component=component, key='component-publication')
        proposal = self.create()
        self.assertFalse(InvestigationArtifact.objects.exists())
        receipt = self.approve(proposal).receipt
        self.assertEqual(InvestigationArtifact.objects.count(), 1)
        self.assertEqual(receipt['request_id'], self.row.pk)
        self.assertEqual(
            InvestigationArtifact.objects.get().source_turn_id, self.turn.pk
        )

    def test_changed_schema_three_reference_refuses_publication(self):
        """Full reference reauthorization remains required at approval."""
        from aichat.tests import test_component_evidence_focus as reference_tests

        component = reference_tests.ComponentEvidenceFocusTests.component(self)
        self.turn = self.begin(component=component, key='stale-component-publication')
        proposal = self.create()
        self.machine.active = False
        self.machine.save(update_fields=['active'])
        with self.assertRaises(proposals.ProposalNotFound):
            self.approve(proposal)
        self.assertFalse(InvestigationArtifact.objects.exists())

    def test_schema_three_reference_loss_preserves_read_and_reject_not_approval(self):
        """Child source loss never hides a readable Work/request proposal."""
        from django.core.files.storage import default_storage

        from aichat.services.investigation_proposals import authorize
        from aichat.tests.test_component_evidence_focus import (
            ComponentEvidenceFocusTests,
        )
        from common.attachment_revision import attachment_revision
        from common.models import Attachment

        for damage in ('attachment_deleted', 'media_replaced', 'machine_inactive'):
            with self.subTest(damage=damage):
                self.machine.active = True
                self.machine.save(update_fields=['active'])
                component = ComponentEvidenceFocusTests.component(self)
                if damage == 'media_replaced':
                    attachment, _, evidence = ComponentEvidenceFocusTests.media_source(
                        self
                    )
                else:
                    attachment = Attachment.objects.create(
                        model_type='workorder',
                        model_id=self.work.pk,
                        link='https://example.invalid/record',
                    )
                    evidence = {
                        'kind': 'attachment',
                        'id': attachment.pk,
                        'revision': attachment_revision(attachment),
                    }
                self.turn = self.begin(
                    component=component, evidence=evidence, key=damage
                )
                proposal = self.create(idempotency_key=damage)
                if damage == 'attachment_deleted':
                    attachment.delete()
                elif damage == 'media_replaced':
                    with default_storage.open(
                        attachment.attachment.name, 'wb'
                    ) as stream:
                        stream.write(b'Replaced media')
                else:
                    self.machine.active = False
                    self.machine.save(update_fields=['active'])
                with mock.patch(
                    'aichat.services.investigation_proposals.refresh_focus',
                    side_effect=AssertionError('read/reject checked child reference'),
                ):
                    authorize(proposal, self.actor, current=False)
                    self.assertEqual(
                        proposals.get_owned_proposal(
                            owner=self.actor,
                            scope_hash=self.scope_hash,
                            proposal_id=proposal.pk,
                        ).pk,
                        proposal.pk,
                    )
                    self.assertIn(
                        proposal.pk,
                        [
                            p.pk
                            for p in proposals.list_owned_proposals(
                                owner=self.actor, scope_hash=self.scope_hash
                            )
                        ],
                    )
                    rejected = proposals.reject_proposal(
                        owner=self.actor,
                        scope_hash=self.scope_hash,
                        proposal_id=proposal.pk,
                    )
                    self.assertEqual(rejected.state, 'rejected')
                # Full revalidation is still required for publication, even
                # though reads/rejection remain available.
                with self.assertRaises(proposals.ProposalNotFound):
                    authorize(proposal, self.actor, current=True)
        self.grant.delete()
        with self.assertRaises(proposals.ProposalNotFound):
            authorize(proposal, self.actor, current=False)
        self.assertEqual(
            proposals.list_owned_proposals(
                owner=self.actor, scope_hash=self.scope_hash
            ),
            [],
        )
        self.assertFalse(InvestigationArtifact.objects.exists())

    def test_approval_executes_once_and_replay_returns_saved_result(self):
        """Proposal and repeated approval retain one artifact/event and real recorder."""
        p = self.create()
        self.assertFalse(InvestigationArtifact.objects.exists())
        self.assertEqual(self.create().pk, p.pk)
        first = self.approve(p)
        second = self.approve(p)
        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual(first.receipt['recorded_by_name'], self.actor.get_username())
        self.assertEqual(InvestigationArtifact.objects.count(), 1)
        a = InvestigationArtifact.objects.get()
        self.assertEqual(
            (a.recorded_by, a.source_turn_id, str(a.source_proposal_id)),
            (self.actor, self.turn.pk, str(p.pk)),
        )
        self.assertEqual(WorkOrderCommand.objects.count(), 1)
        self.assertEqual(WorkOrderEvent.objects.get().actor, self.actor)
        self.row.refresh_from_db()
        self.assertEqual((self.row.version, self.row.status), (2, 'open'))
        # Replay remains valid after another properly scoped evidence round.
        command(
            work_order_id=self.work.pk,
            actor=self.actor,
            expected_version=1,
            idempotency_key='later-round',
            operation='artifact',
            fields={'kind': 'observation', 'payload': {'text': 'Next round'}},
            request_id=self.row.pk,
            request_version=2,
        )
        self.assertEqual(self.approve(p).receipt, first.receipt)

    def test_foreign_work_request_and_turn_return_404(self):
        """REST never reveals another Work/request/thread via proposal creation."""
        api = APIClient()
        api.force_authenticate(self.actor)
        base = {
            'action_type': 'investigation.artifact',
            'work_order_id': self.work.pk,
            'thread_id': self.thread.pk,
            'source_turn_id': self.turn.pk,
            'intent': {
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'observation',
                'payload': {'text': 'Test'},
            },
        }
        other = InvestigationRequest.objects.create(
            work_order=self.second_work,
            purpose='Other',
            next_check='Other',
            created_by=self.actor,
            updated_by=self.actor,
        )
        for override in (
            {'work_order_id': self.foreign_work.pk},
            {'intent': {**base['intent'], 'request_id': other.pk}},
            {'source_turn_id': 'turn_foreign'},
            {'thread_id': 'thread_foreign'},
        ):
            with self.subTest(override=override):
                response = api.post(
                    '/api/aichat/proposals/', {**base, **override}, format='json'
                )
                self.assertEqual(response.status_code, 404, response.data)
        self.assertFalse(ChatActionProposal.objects.exists())
        self.assertFalse(InvestigationArtifact.objects.exists())

    def test_assistant_tool_prepares_only_and_same_local_key_works_across_rounds(self):
        """Only bound source turns propose; retries and later rounds keep distinct ids."""
        from asgiref.sync import async_to_sync

        from ai.core.auth import principal_context
        from ai.core.integrations.investigation_tools import (
            bind_investigation_source,
            propose_investigation_artifact,
        )

        token = principal_context.set(principal_for_user(self.actor))
        self.addCleanup(principal_context.reset, token)
        args = {
            'kind': 'observation',
            'payload': {'text': 'Synthetic technician observation'},
            'request_key': 'reading',
        }
        self.assertFalse(
            async_to_sync(propose_investigation_artifact)(**args)['success']
        )
        with bind_investigation_source(self.thread.pk, self.turn.pk):
            first = async_to_sync(propose_investigation_artifact)(**args)
            replay = async_to_sync(propose_investigation_artifact)(**args)
        self.assertTrue(first['success'], first)
        self.assertFalse(first['executed'])
        self.assertEqual(first['proposal_id'], replay['proposal_id'])
        self.assert_nothing_published()
        self.approve(ChatActionProposal.objects.get(pk=first['proposal_id']))
        self.turn = self.begin(key='next-round')
        with bind_investigation_source(self.thread.pk, self.turn.pk):
            later = async_to_sync(propose_investigation_artifact)(**args)
        self.assertTrue(later['success'], later)
        self.assertNotEqual(first['proposal_id'], later['proposal_id'])
        self.approve(ChatActionProposal.objects.get(pk=later['proposal_id']))
        self.assertEqual(InvestigationArtifact.objects.count(), 2)
        self.assertEqual(ChatActionProposal.objects.count(), 2)

    def test_changed_request_revision_refuses_without_any_publication(self):
        """A changed decision cannot receive the artifact reviewed for its old revision."""
        file_id = self.upload('synthetic.pdf', b'%PDF-1.4\nSynthetic fixture\n')
        p = self.create(
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'report',
                'payload': {
                    'description': 'Synthetic report',
                    'source': 'Technician',
                    'status': 'preliminary',
                },
                'file_id': file_id,
            }
        )
        InvestigationRequest.objects.filter(pk=self.row.pk).update(version=2)
        with self.assertRaisesMessage(
            proposals.ProposalRevalidationFailed, 'Nothing was saved'
        ):
            self.approve(p)
        self.assert_nothing_published()

    def assert_nothing_published(self):
        """No artifact, native attachment or Work command exists for refused work."""
        from common.models import Attachment

        self.assertFalse(InvestigationArtifact.objects.exists())
        self.assertFalse(
            Attachment.objects.filter(
                model_type='workorder', model_id=self.work.pk
            ).exists()
        )
        self.assertFalse(WorkOrderCommand.objects.exists())
        self.assertFalse(
            any(
                path.is_file()
                for path in (Path(self.temp.name) / 'attachments').glob('**/*')
            )
        )

    def test_rejection_expiry_and_missing_exact_preview_save_nothing(self):
        """Existing refusal and expiry flow never promotes text or files."""
        p = self.create()
        with self.assertRaises(proposals.ProposalPreviewChanged):
            self.approve(p, expected_preview_hash=None)
        proposals.reject_proposal(
            owner=self.actor, scope_hash=self.scope_hash, proposal_id=p.pk
        )
        with self.assertRaises(proposals.ProposalStateConflict):
            self.approve(p)
        q = self.create(idempotency_key='expire')
        ChatActionProposal.objects.filter(pk=q.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(proposals.ProposalExpired):
            self.approve(q)
        self.assert_nothing_published()

    def test_scope_revocation_blocks_pending_approval_details_and_saved_replay(self):
        """Generic executed replay is narrowed by fresh source and Work permission."""
        p = self.create()
        self.approve(p)
        qturn = self.begin(key='pending')
        q = self.create(
            idempotency_key='pending',
            source_turn_id=qturn.pk,
            intent={
                'request_id': self.row.pk,
                'request_version': self.row.version,
                'kind': 'observation',
                'payload': {'text': 'Pending'},
            },
        )
        self.grant.delete()
        for proposal in (p, q):
            with self.assertRaises(proposals.ProposalNotFound):
                self.approve(proposal)
            with self.assertRaises(proposals.ProposalNotFound):
                proposals.get_owned_proposal(
                    owner=self.actor,
                    scope_hash=self.scope_hash,
                    proposal_id=proposal.pk,
                )
        self.assertEqual(
            proposals.list_owned_proposals(
                owner=self.actor, scope_hash=self.scope_hash
            ),
            [],
        )
        self.assertEqual(InvestigationArtifact.objects.count(), 1)

    def test_approved_file_is_copied_once_as_native_work_attachment(self):
        """Same reviewed bytes; private original stays; recorder is approving actor."""
        from common.models import Attachment

        contents = b'%PDF-1.4\nSynthetic report bytes\n'
        file_id = self.upload('report.pdf', contents)
        p = self.create(
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'report',
                'payload': {
                    'description': 'Synthetic report',
                    'source': 'Named laboratory',
                    'status': 'pending',
                },
                'file_id': file_id,
            }
        )
        self.assertIn('visible to readers', p.preview['publication'])
        self.assert_nothing_published()
        first = self.approve(p)
        self.assertEqual(self.approve(p).receipt, first.receipt)
        a = Attachment.objects.get(pk=first.receipt['attachment_id'])
        self.assertEqual(
            (a.model_type, a.model_id, a.upload_user_id),
            ('workorder', self.work.pk, self.actor.pk),
        )
        with a.attachment.open('rb') as stream:
            self.assertEqual(stream.read(), contents)
        self.assertTrue((Path(self.temp.name) / 'ai_uploads' / file_id).exists())
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(
            InvestigationArtifact.objects.get().source_file['sha256'],
            p.preview['file']['sha256'],
        )

    def test_multiple_readings_photos_reports_and_lab_rounds_keep_exact_evidence(self):
        """Every signed round needs approval; repeated sets/files retain typed identity."""
        from io import BytesIO

        from PIL import Image

        from common.models import Attachment

        reading = {
            'readings': [{'name': 'Synthetic point', 'value': None, 'unit': 'g'}],
            'observed_at': '2026-10-19T09:00:00Z',
            'conditions': 'Synthetic technician-supplied conditions',
        }
        laboratory = {
            'sample': 'SYN-7',
            'method': 'Synthetic named method',
            'observed_at': '2026-10-19T09:00:00Z',
            'source': 'Synthetic laboratory',
            'status': 'pending',
            'results': [{'name': 'Synthetic count', 'value': None, 'unit': 'count/mL'}],
        }
        rounds = [
            ('reading_set', reading, None, None),
            (
                'reading_set',
                {
                    **reading,
                    'readings': [{'name': 'Synthetic point', 'value': 0, 'unit': 'g'}],
                },
                None,
                None,
            ),
        ]
        for extension, image_format in (('png', 'PNG'), ('jpg', 'JPEG')):
            contents = BytesIO()
            Image.new('RGB', (1, 1)).save(contents, format=image_format)
            rounds.append((
                'photo',
                {'description': 'Synthetic image fixture'},
                f'fixture.{extension}',
                contents.getvalue(),
            ))
        for i in range(2):
            rounds.append((
                'report',
                {
                    'description': f'Synthetic report {i}',
                    'source': 'Synthetic laboratory',
                    'status': 'pending',
                },
                f'report-{i}.pdf',
                b'%PDF-1.4\nSynthetic report\n',
            ))
        rounds.append(('laboratory', laboratory, None, None))
        for i, (kind, payload, filename, contents) in enumerate(rounds):
            with self.subTest(round=i, kind=kind):
                file_id = self.upload(filename, contents) if filename else None
                if not filename:
                    self.turn = self.begin(key=f'round-{i}')
                intent = {
                    'request_id': self.row.pk,
                    'request_version': self.row.version,
                    'kind': kind,
                    'payload': payload,
                }
                if file_id:
                    intent['file_id'] = file_id
                p = self.create(idempotency_key=f'round-{i}', intent=intent)
                self.assertEqual(InvestigationArtifact.objects.count(), i)
                saved = self.approve(p)
                self.assertEqual(self.approve(p).receipt, saved.receipt)
                artifact = InvestigationArtifact.objects.get(
                    pk=saved.receipt['artifact_id']
                )
                self.assertEqual((artifact.kind, artifact.payload), (kind, payload))
                self.assertEqual(artifact.source_turn_id, self.turn.pk)
        self.row.refresh_from_db()
        self.assertEqual((self.row.version, self.row.status), (8, 'open'))
        self.assertEqual(InvestigationArtifact.objects.count(), 7)
        self.assertEqual(Attachment.objects.count(), 4)
        self.assertEqual(Attachment.objects.filter(is_image=True).count(), 2)
        self.assertEqual(
            InvestigationArtifact.objects.get(kind='laboratory').payload, laboratory
        )

    def test_unsupported_changed_foreign_and_symlink_files_never_publish(self):
        """File disclosure is explicit and exact; no arbitrary local path can enter."""
        file_id = self.upload('readings.csv', b'name,value\ntest,1\n')
        p = self.create(
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'report',
                'payload': {
                    'description': 'Synthetic CSV',
                    'source': 'Technician',
                    'status': 'pending',
                },
                'file_id': file_id,
            }
        )
        self.assertFalse(p.preview['publication_supported'])
        with self.assertRaisesMessage(proposals.ProposalStateConflict, 'not supported'):
            self.approve(p)
        self.assert_nothing_published()
        for bad in (
            'thread_foreign/report.pdf',
            f'{self.thread.pk}/not-on-turn.pdf',
            '../secret.pdf',
        ):
            with self.assertRaises(proposals.ProposalNotFound):
                self.create(idempotency_key=bad, intent={**p.intent, 'file_id': bad})
        file_id = self.upload('changed.pdf', b'%PDF-1.4\nOriginal\n')
        q = self.create(
            idempotency_key='changed-file', intent={**p.intent, 'file_id': file_id}
        )
        (Path(self.temp.name) / 'ai_uploads' / file_id).write_bytes(
            b'%PDF-1.4\nChanged\n'
        )
        with self.assertRaisesMessage(proposals.ProposalPreviewChanged, 'changed'):
            self.approve(q)
        self.assert_nothing_published()
        path = Path(self.temp.name) / 'ai_uploads' / file_id
        path.unlink()
        path.symlink_to(Path(self.temp.name) / 'outside.pdf')
        with self.assertRaises(proposals.ProposalNotFound):
            self.approve(q)
        self.assert_nothing_published()

    def test_invalid_typed_artifact_never_creates_a_proposal(self):
        """A laboratory result cannot silently lose method/time/status."""
        with self.assertRaises(proposals.ProposalError):
            self.create(
                intent={
                    'request_id': self.row.pk,
                    'request_version': 1,
                    'kind': 'laboratory',
                    'payload': {'sample': 'S-1'},
                }
            )
        self.assertFalse(ChatActionProposal.objects.exists())
        self.assert_nothing_published()

    def test_explicit_cannot_perform_stays_unresolved_without_invalidating_other_requests(
        self,
    ):
        """Approval retains the technician's reason and affects only the exact request."""
        other = InvestigationRequest.objects.create(
            work_order=self.work,
            purpose='Unaffected decision',
            next_check='Other',
            created_by=self.actor,
            updated_by=self.actor,
        )
        p = self.create(
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'check_result',
                'payload': {
                    'text': 'Synthetic calibrated instrument unavailable',
                    'status': 'cannot_perform',
                },
            }
        )
        self.approve(p)
        self.row.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(
            (self.row.status, self.row.blocked_reason),
            ('cannot_perform', 'Synthetic calibrated instrument unavailable'),
        )
        self.assertIsNone(self.row.owner_id)
        self.assertIsNone(self.row.next_checkpoint)
        self.assertEqual((other.status, other.version), ('open', 1))
        self.turn = self.begin(key='follow-up')
        q = self.create(
            idempotency_key='follow-up',
            intent={
                'request_id': self.row.pk,
                'request_version': self.row.version,
                'kind': 'observation',
                'payload': {'text': 'More context'},
            },
        )
        self.approve(q)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, 'cannot_perform')

    def test_receipt_failure_rolls_back_evidence_and_removes_copied_file(self):
        """Storage cleanup covers failure after dispatch but before receipt persistence."""
        file_id = self.upload('rollback.pdf', b'%PDF-1.4\nSynthetic fixture\n')
        p = self.create(
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'report',
                'payload': {
                    'description': 'Synthetic report',
                    'source': 'Technician',
                    'status': 'pending',
                },
                'file_id': file_id,
            }
        )
        original = ChatActionProposal.save

        def fail_receipt(instance, *args, **kwargs):
            if instance.pk == p.pk and instance.state == 'executed':
                raise RuntimeError('Synthetic receipt failure')
            return original(instance, *args, **kwargs)

        with (
            mock.patch.object(ChatActionProposal, 'save', fail_receipt),
            self.assertRaisesMessage(RuntimeError, 'Synthetic receipt failure'),
        ):
            self.approve(p)
        self.assert_nothing_published()
        self.assertEqual(
            [path.name for path in Path(self.temp.name).rglob('*') if path.is_file()],
            ['rollback.pdf'],
        )

    def test_long_description_keeps_full_payload_and_valid_native_comment(self):
        """SQLite proves the attachment field fits using its model validators."""
        from common.models import Attachment

        file_id = self.upload('long.pdf', b'%PDF-1.4\nSynthetic\n')
        description = 'Long technician report ' * 30
        p = self.create(
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'report',
                'payload': {
                    'description': description,
                    'source': 'Technician',
                    'status': 'final',
                },
                'file_id': file_id,
            }
        )
        receipt = self.approve(p).receipt
        saved = Attachment.objects.get(pk=receipt['attachment_id'])
        Attachment._meta.get_field('comment').clean(saved.comment, saved)
        self.assertEqual(saved.comment, description[:250])
        self.assertEqual(
            InvestigationArtifact.objects.get().payload['description'], description
        )

    def test_sibling_proposals_and_later_proposal_from_same_turn_publish(self):
        """Only sibling publications may advance the signed request revision."""
        first_file = self.upload('first.pdf', b'%PDF-1.4\nFirst\n')
        second_file = f'{self.thread.pk}/second.pdf'
        (Path(self.temp.name) / 'ai_uploads' / second_file).write_bytes(
            b'%PDF-1.4\nSecond\n'
        )
        self.turn = self.begin(
            {'uploaded_files': [{'file_id': first_file}, {'file_id': second_file}]},
            key='multi-file-turn',
        )
        intent = {
            'request_id': self.row.pk,
            'request_version': 1,
            'kind': 'report',
            'payload': {
                'description': 'First report',
                'source': 'Technician',
                'status': 'final',
            },
            'file_id': first_file,
        }
        first = self.create(intent=intent)
        second = self.create(
            idempotency_key='sibling', intent={**intent, 'file_id': second_file}
        )
        self.assertEqual(self.approve(first).receipt['request_version'], 2)
        self.assertEqual(self.approve(second).receipt['request_version'], 3)
        third = self.create(
            idempotency_key='late-sibling',
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'observation',
                'payload': {'text': 'Third artifact'},
            },
        )
        self.assertEqual(self.approve(third).receipt['request_version'], 4)
        self.assertEqual(InvestigationArtifact.objects.count(), 3)
        self.row.refresh_from_db()
        self.turn = self.begin(key='next-request-message')
        self.assertEqual(
            self.turn.trusted_context['work_focus_snapshot']['request_version'], 4
        )

    def test_bomb_image_creates_nonexecutable_review(self):
        """Pillow's dimensional guard becomes a supported refusal, never a 500."""
        from io import BytesIO

        from PIL import Image

        content = BytesIO()
        Image.new('RGB', (2, 2)).save(content, format='PNG')
        file_id = self.upload('bomb.png', content.getvalue())
        with mock.patch.object(Image, 'MAX_IMAGE_PIXELS', 1):
            p = self.create(
                intent={
                    'request_id': self.row.pk,
                    'request_version': 1,
                    'kind': 'photo',
                    'payload': {'description': 'Oversized dimensions'},
                    'file_id': file_id,
                }
            )
            self.assertFalse(p.preview['publication_supported'])
            with self.assertRaises(proposals.ProposalStateConflict):
                self.approve(p)
        self.assert_nothing_published()

    def test_visibility_reads_batch_without_thread_locks(self):
        """Refreshing proposals reauthorizes once per focus, without write locks."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.create()
        for i in range(4):
            self.create(idempotency_key=f'visible-{i}')
        with mock.patch.object(
            ThreadRepository,
            '_lock_thread',
            side_effect=AssertionError('read took lock'),
        ):
            with CaptureQueriesContext(connection) as queries:
                rows = proposals.list_owned_proposals(
                    owner=self.actor, scope_hash=self.scope_hash
                )
            self.assertEqual(len(rows), 5)
            self.assertLess(len(queries), 25)
            proposals.get_owned_proposal(
                owner=self.actor, scope_hash=self.scope_hash, proposal_id=rows[0].pk
            )

    def test_real_sibling_artifact_and_unsupported_owner_finding_refuse(self):
        """Proposal intake cannot correct a real artifact of another request."""
        sibling = InvestigationRequest.objects.create(
            work_order=self.work,
            purpose='Sibling',
            next_check='Sibling',
            created_by=self.actor,
            updated_by=self.actor,
        )
        artifact = InvestigationArtifact.objects.create(
            request=sibling,
            kind='observation',
            payload={'text': 'Sibling'},
            recorded_by=self.actor,
        )
        base = {
            'request_id': self.row.pk,
            'request_version': 1,
            'kind': 'observation',
            'payload': {'text': 'Correction'},
        }
        from django.contrib.auth import get_user_model

        from assets.models import ClientScopeGrant
        from common.models import Attachment
        from repair.models import RepairInvestigationFinding, RepairPacket

        outside = get_user_model().objects.create_user(
            username='foreign-proposal-owner'
        )
        outside.groups.add(self.group)
        ClientScopeGrant.objects.create(
            user=outside, client=self.foreign_work.machine.client
        )

        packet = RepairPacket.objects.create(
            work_order=self.second_work, machine=self.machine, fault_summary='Foreign'
        )
        finding = RepairInvestigationFinding.objects.create(
            packet=packet,
            finding_key='foreign',
            category='inspection',
            observation='Foreign Work',
        )
        attachment = Attachment.objects.create(
            model_type='workorder',
            model_id=self.second_work.pk,
            link='https://example.invalid/foreign-report',
        )
        # These links are not accepted by the publication schema at all.
        for extra in (
            {'supersedes_id': artifact.pk},
            {'owner_id': outside.pk},
            {'source_finding_id': finding.pk},
            {'attachment_id': attachment.pk},
        ):
            with self.subTest(extra=extra), self.assertRaises(proposals.ProposalError):
                self.create(idempotency_key=str(extra), intent={**base, **extra})
        self.assertFalse(ChatActionProposal.objects.exists())

    def test_deleted_native_file_keeps_executed_replay_and_evidence(self):
        """A retained snapshot proves the saved result after native attachment removal."""
        from common.models import Attachment

        file_id = self.upload('removed.pdf', b'%PDF-1.4\nSynthetic\n')
        p = self.create(
            intent={
                'request_id': self.row.pk,
                'request_version': 1,
                'kind': 'report',
                'payload': {
                    'description': 'Original',
                    'source': 'Technician',
                    'status': 'final',
                },
                'file_id': file_id,
            }
        )
        saved = self.approve(p)
        Attachment.objects.get(pk=saved.receipt['attachment_id']).delete()
        self.assertEqual(self.approve(p).receipt, saved.receipt)
        self.assertIsNone(InvestigationArtifact.objects.get().attachment_id)

    def test_optional_nontext_description_is_not_a_native_comment(self):
        """Extra facts on file-backed observations are retained without type coercion."""
        from common.models import Attachment

        file_id = self.upload('observation.pdf', b'%PDF-1.4\nSynthetic\n')
        for index, description in enumerate((None, 42)):
            payload = {'text': 'Technician observation', 'description': description}
            proposal = self.create(
                idempotency_key=f'optional-description-{index}',
                intent={
                    'request_id': self.row.pk,
                    'request_version': 1,
                    'kind': 'observation',
                    'payload': payload,
                    'file_id': file_id,
                },
            )
            saved = self.approve(proposal)
            self.assertEqual(
                Attachment.objects.get(pk=saved.receipt['attachment_id']).comment, ''
            )
            self.assertEqual(
                InvestigationArtifact.objects.get(
                    pk=saved.receipt['artifact_id']
                ).payload,
                payload,
            )
