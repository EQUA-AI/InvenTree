"""Synthetic native auth, real future births/attestations and readiness state."""

from integration_state.tests.assertions import assert_literal_bool

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection
from django.db.migrations.exceptions import IrreversibleError
from django.test import TestCase, override_settings

from rest_framework.authentication import BasicAuthentication
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate
from tasks.integration_attachments import (
    read_work_attachment,
    read_work_attachment_content,
)
from tasks.integration_sources import read_work_order
from tasks.models import WorkOrder

from assets.integration_sources import (
    read_machine_attachment_content,
    read_maintenance_record,
)
from assets.models import AssetMachine, AssetMaintenanceRecord, Client, ClientScopeGrant
from common.models import Attachment
from company.models import Company
from integration_state import native_authority as service
from integration_state.models import (
    NativeAuthorityDecision,
    NativeAuthorityEpoch,
    NativeAuthorityRequest,
    NativeAuthorityWithdrawal,
    NativeSourceBirth,
    PrivateDeletionRequest,
    SourceNamespace,
)
from integration_state.s7_contracts import SourceAspectIdentity
from integration_state.s7_deletion_requests import CopyImpact
from users.models import RuleSet


class NativeAuthorityFixture:
    """Only native synthetic records; no source proof or authorization mocks."""

    def setUp(self):
        """Create native roles, independent grants, an old row and a new source epoch."""
        super().setUp()
        self.enterContext(
            override_settings(
                AUTHENTICATION_BACKENDS=['django.contrib.auth.backends.ModelBackend'],
                AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
                AIMMS_SINGLE_SITE_CLIENT_CODE='absent-native-authority-fallback',
                AIMMS_INTEGRATION_AUTHORITY_ENABLED=False,
            )
        )
        self.owner = get_user_model().objects.create_user(
            username='native-source-owner', password='synthetic-source-password'
        )
        self.reviewer = get_user_model().objects.create_user(
            username='native-source-reviewer'
        )
        owner_group = Group.objects.create(name='Native source administrators')
        viewer_group = Group.objects.create(name='Native source reviewers')
        self.owner.groups.add(owner_group, viewer_group)
        self.reviewer.groups.add(viewer_group)
        self.admin_rule, _ = RuleSet.objects.update_or_create(
            group=owner_group, name='admin', defaults={'can_change': True}
        )
        RuleSet.objects.update_or_create(
            group=viewer_group, name='work_order', defaults={'can_view': True}
        )
        self.review_permission = Permission.objects.get(
            content_type__app_label='approvals', codename='review'
        )
        self.reviewer.user_permissions.add(self.review_permission)
        self.client_a = Client.objects.create(
            name='Native future A', code='native-future-a'
        )
        self.client_b = Client.objects.create(
            name='Native future B', code='native-future-b'
        )
        self.owner_grant = ClientScopeGrant.objects.create(
            user=self.owner, client=self.client_a
        )
        self.reviewer_grant = ClientScopeGrant.objects.create(
            user=self.reviewer, client=self.client_a
        )
        self.machine = AssetMachine.objects.create(
            name='Native future machine', client=self.client_a
        )
        self.old_work = self.work('Old, unproven source')
        self.enterContext(override_settings(AIMMS_INTEGRATION_AUTHORITY_ENABLED=True))
        self.namespace = SourceNamespace.objects.create(
            environment='native-authority-fixture', active=True
        )
        self.epoch = service.start_attribution_epoch(self.namespace.pk)
        self.new_work = self.work('Future native Work')

    def work(self, title, **fields):
        """Insert a synthetic Work through its real native model save path."""
        values = {
            'title': title,
            'status': WorkOrder.STATUS_BACKLOG,
            'priority': WorkOrder.PRIORITY_MEDIUM,
            'machine': self.machine,
        }
        values.update(fields)
        return WorkOrder.objects.create(**values)

    def request(self, actor=None, *, method='post', token=None):
        """Build an actual DRF request using its explicit test authenticator."""
        raw = getattr(APIRequestFactory(), method)('/synthetic/native-authority/', {})
        force_authenticate(raw, user=actor or self.owner, token=token)
        return Request(raw)

    def attest(self, kind='work', pk=None, *, clients=None, key=None):
        """Submit a native revision-bound source claim without simulated proof."""
        pk = self.new_work.pk if pk is None else pk
        return service.request_attribution(
            self.request(),
            namespace_id=self.namespace.pk,
            kind=kind,
            source_pk=pk,
            client_ids=clients or (self.client_a.pk,),
            evidence_reference='synthetic-native-evidence:' + str(pk),
            request_key=key or kind + '-' + str(pk),
        )

    def approve(self, row, *, approved=True):
        """Review the actual current request and its server-computed impact hash."""
        request = self.request(self.reviewer)
        view = service.inspect_authority_request(request, request_pk=row.pk)
        return service.decide_authority_request(
            request,
            request_pk=row.pk,
            expected_request_digest=view['request_digest'],
            expected_impact_digest=view['impact_digest'],
            approved=approved,
        )

    def readiness(self, *, reference='synthetic-client-request', key='synthetic-ready'):
        """Bind a current client request to an existing S5 private intent."""
        scope = (
            SourceAspectIdentity(
                environment=self.namespace.environment,
                epoch=self.namespace.epoch,
                entity_kind='machine',
                aspect='metadata',
                source_id=self.machine.pk,
            ),
        )
        return service.request_deletion_readiness(
            self.request(),
            namespace_id=self.namespace.pk,
            client_pk=self.client_a.pk,
            client_request_reference=reference,
            request_key=key,
            scope=scope,
        )


class FutureAttributionTests(NativeAuthorityFixture, TestCase):
    """Actual proof creation replaces simulated attribution for future records."""

    def test_flag_without_installed_state_app_preserves_writes_and_withholds_reads(
        self,
    ):
        """Partial optional configuration cannot import unavailable state models."""
        from django.conf import settings

        installed = [
            name
            for name in settings.INSTALLED_APPS
            if not name.startswith('integration_state')
        ]
        # Registry startup inspects the real historical graph. The synthetic
        # model-sync profile's disabled migrations cannot serve that lookup.
        with (
            override_settings(MIGRATION_MODULES={}),
            override_settings(INSTALLED_APPS=installed),
        ):
            work = self.work('Native write without optional state app')
            self.assertIsNone(
                read_work_order(
                    self.owner, environment=self.namespace.environment, work_pk=work.pk
                )
            )
        self.assertFalse(
            NativeSourceBirth.objects.filter(
                epoch=self.epoch, entity_kind='work', source_pk=work.pk
            ).exists()
        )

    def test_default_off_does_not_capture_or_admit(self):
        """Optional authority enrollment must not change native default behavior."""
        with override_settings(AIMMS_INTEGRATION_AUTHORITY_ENABLED=False):
            work = self.work('Off-path native Work')
            self.assertFalse(
                NativeSourceBirth.objects.filter(
                    source_pk=work.pk, entity_kind='work'
                ).exists()
            )
            self.assertIsNone(
                read_work_order(
                    self.owner, environment=self.namespace.environment, work_pk=work.pk
                )
            )
            with self.assertRaises(service.NativeAuthorityError):
                self.attest(pk=work.pk)

    def test_failed_optional_capture_preserves_the_native_write(self):
        """A missing marker is a proof gap, not a broken native source operation."""
        with patch.object(
            NativeSourceBirth.objects,
            'get_or_create',
            side_effect=DatabaseError('synthetic capture fault'),
        ):
            work = self.work('Native write survives optional capture')
        self.assertTrue(WorkOrder.objects.filter(pk=work.pk).exists())
        self.assertFalse(
            NativeSourceBirth.objects.filter(
                source_pk=work.pk, entity_kind='work'
            ).exists()
        )
        with self.assertRaises(service.NativeAuthorityError):
            self.attest(pk=work.pk)

    def test_pre_epoch_records_cannot_be_backfilled_by_editing(self):
        """Editing an existing record cannot manufacture a future insertion marker."""
        self.old_work.title = 'Edited old record'
        self.old_work.save()
        self.assertFalse(
            NativeSourceBirth.objects.filter(
                source_pk=self.old_work.pk, entity_kind='work'
            ).exists()
        )
        with self.assertRaises(service.NativeAuthorityError):
            self.attest(pk=self.old_work.pk)

    def test_bulk_insert_without_capture_stays_unproven(self):
        """Unsupported writers remain explicit proof gaps rather than admitted history."""
        work = WorkOrder(
            title='Unsupported bulk writer',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=self.machine,
        )
        WorkOrder.objects.bulk_create([work])
        with self.assertRaises(service.NativeAuthorityError):
            self.attest(pk=work.pk)

    def test_actual_two_actor_future_attestation_admits_current_work(self):
        """Two current native actors establish a new exact source relationship."""
        self.assertTrue(
            NativeSourceBirth.objects.filter(
                source_pk=self.new_work.pk, entity_kind='work'
            ).exists()
        )
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        row = self.attest()
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        decision = self.approve(row)
        self.assertTrue(decision.approved)
        result = read_work_order(
            self.owner, environment=self.namespace.environment, work_pk=self.new_work.pk
        )
        self.assertEqual(result.title, self.new_work.title)
        self.assertEqual(result.identity.environment, self.namespace.environment)
        self.assertEqual(
            service.inspect_authority_request(
                self.request(self.reviewer), request_pk=row.pk
            )['request_reference'],
            'synthetic-native-evidence:' + str(self.new_work.pk),
        )

    def test_approved_fact_survives_signer_privilege_change_until_withdrawn(self):
        """Staff authority is checked at approval; reader access stays current."""
        row = self.attest()
        decision = self.approve(row)
        self.reviewer.user_permissions.clear()
        self.assertIsNotNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        withdrawal = service.withdraw_authority_request(
            self.request(),
            request_pk=row.pk,
            expected_request_digest=row.binding_digest,
            reason_reference='synthetic-source-owner-correction',
        )
        self.assertEqual(withdrawal.request_digest, row.binding_digest)
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        self.assertTrue(NativeAuthorityDecision.objects.filter(pk=decision.pk).exists())
        self.assertEqual(NativeAuthorityWithdrawal.objects.count(), 1)
        self.assertEqual(
            service.withdraw_authority_request(
                self.request(),
                request_pk=row.pk,
                expected_request_digest=row.binding_digest,
                reason_reference='synthetic-source-owner-correction',
            ).pk,
            withdrawal.pk,
        )
        with self.assertRaises(service.NativeAuthorityError):
            service.withdraw_authority_request(
                self.request(),
                request_pk=row.pk,
                expected_request_digest=row.binding_digest,
                reason_reference='conflicting-retry',
            )
        self.reviewer.user_permissions.add(self.review_permission)
        self.assertEqual(
            service.inspect_authority_request(
                self.request(self.reviewer), request_pk=row.pk
            )['review_status'],
            'withdrawn',
        )
        with self.assertRaises(service.NativeAuthorityError):
            self.approve(row)

    def test_withdrawal_requires_current_native_admin_and_all_client_grants(self):
        """A reviewer or stale requester cannot invalidate somebody else's claim."""
        row = self.attest()
        self.approve(row)
        arguments = {
            'request_pk': row.pk,
            'expected_request_digest': row.binding_digest,
            'reason_reference': 'synthetic-reason',
        }
        with self.assertRaises(service.NativeAuthorityError):
            service.withdraw_authority_request(self.request(self.reviewer), **arguments)
        self.owner_grant.delete()
        with self.assertRaises(service.NativeAuthorityError):
            service.withdraw_authority_request(self.request(), **arguments)
        self.assertFalse(NativeAuthorityWithdrawal.objects.exists())

    def test_shared_machine_less_and_foreign_machine_work_survive_independent_edges(
        self,
    ):
        """Proven shared Work does not disclose an independently inaccessible machine."""
        customer = Company.objects.create(
            name='Native shared customer', is_customer=True
        )
        foreign = AssetMachine.objects.create(
            name='Unrelated machine', client=self.client_b
        )
        for machine in (None, foreign):
            with self.subTest(machine=machine):
                work = self.work(
                    'Future shared Work', machine=machine, customer=customer
                )
                self.approve(self.attest(pk=work.pk))
                result = read_work_order(
                    self.owner, environment=self.namespace.environment, work_pk=work.pk
                )
                self.assertEqual(result.title, work.title)
                self.assertFalse(hasattr(result, 'machine'))

    def test_native_admin_change_and_current_grant_are_independent(self):
        """Neither native action permission nor a client grant substitutes for the other."""
        self.admin_rule.can_change = False
        self.admin_rule.save()
        with self.assertRaises(service.NativeAuthorityError):
            self.attest()
        self.admin_rule.can_change = True
        self.admin_rule.save()
        self.owner_grant.delete()
        with self.assertRaises(service.NativeAuthorityError):
            self.attest()

    def test_no_self_approval_or_read_action_substitution(self):
        """A different reviewer and an actual admitted write request are mandatory."""
        self.owner.user_permissions.add(self.review_permission)
        row = self.attest()
        with self.assertRaises(service.NativeAuthorityError):
            service.inspect_authority_request(self.request(), request_pk=row.pk)
        with self.assertRaises(service.NativeAuthorityError):
            service.request_attribution(
                self.request(method='get'),
                namespace_id=self.namespace.pk,
                kind='work',
                source_pk=self.new_work.pk,
                client_ids=(self.client_a.pk,),
                evidence_reference='synthetic',
                request_key='get-is-not-write',
            )
        with self.assertRaises(service.NativeAuthorityError):
            service.request_attribution(
                self.request(token=object()),
                namespace_id=self.namespace.pk,
                kind='work',
                source_pk=self.new_work.pk,
                client_ids=(self.client_a.pk,),
                evidence_reference='synthetic',
                request_key='token-is-not-native-write',
            )

    def test_reviewer_permission_grant_and_revision_are_current(self):
        """Permission, grant and source changes cannot reuse a prior review view."""
        row = self.attest()
        view = service.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        self.reviewer.user_permissions.clear()
        with self.assertRaises(service.NativeAuthorityError):
            self.approve(row)
        self.reviewer.user_permissions.add(self.review_permission)
        self.reviewer_grant.delete()
        with self.assertRaises(service.NativeAuthorityError):
            self.approve(row)
        self.reviewer_grant = ClientScopeGrant.objects.create(
            user=self.reviewer, client=self.client_a
        )
        WorkOrder.objects.filter(pk=self.new_work.pk).update(
            title='Changed after review'
        )
        with self.assertRaises(service.NativeAuthorityError):
            service.decide_authority_request(
                self.request(self.reviewer),
                request_pk=row.pk,
                expected_request_digest=view['request_digest'],
                expected_impact_digest=view['impact_digest'],
                approved=True,
            )
        self.assertFalse(NativeAuthorityDecision.objects.filter(request=row).exists())

    def test_source_change_and_revocation_withhold_later_reads(self):
        """New source revisions need new proof and current reads need current grants."""
        row = self.attest()
        self.approve(row)
        self.assertIsNotNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        WorkOrder.objects.filter(pk=self.new_work.pk).update(
            title='A different revision'
        )
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        self.approve(self.attest(key='new-revision'))
        self.assertIsNotNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        self.owner_grant.delete()
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )

    def test_namespace_restore_and_retained_key_reuse_cannot_adopt_proof(self):
        """A reused native key or changed source epoch cannot inherit old evidence."""
        self.approve(self.attest())
        pk = self.new_work.pk
        self.new_work.delete()
        self.work('Reused key', pk=pk)
        self.assertTrue(
            NativeSourceBirth.objects.get(
                epoch=self.epoch, entity_kind='work', source_pk=pk
            ).ambiguous
        )
        self.assertIsNone(
            read_work_order(
                self.owner, environment=self.namespace.environment, work_pk=pk
            )
        )

        SourceNamespace.objects.filter(pk=self.namespace.pk).update(epoch=uuid4())
        self.assertIsNone(
            read_work_order(
                self.owner, environment=self.namespace.environment, work_pk=pk
            )
        )

    def test_source_epoch_rotation_independently_withholds_valid_proof(self):
        """An otherwise valid attestation cannot cross a restored source epoch."""
        self.approve(self.attest())
        self.assertIsNotNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )
        SourceNamespace.objects.filter(pk=self.namespace.pk).update(epoch=uuid4())
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment=self.namespace.environment,
                work_pk=self.new_work.pk,
            )
        )

    def test_server_environment_rename_cannot_relabel_existing_proof(self):
        """Environment is part of the captured source epoch, not a mutable alias."""
        self.approve(self.attest())
        SourceNamespace.objects.filter(pk=self.namespace.pk).update(
            environment='renamed-without-new-incarnation'
        )
        self.assertIsNone(
            read_work_order(
                self.owner,
                environment='renamed-without-new-incarnation',
                work_pk=self.new_work.pk,
            )
        )
        with self.assertRaises(service.NativeAuthorityError):
            service.start_attribution_epoch(self.namespace.pk)

    def test_restore_hold_prevents_births_attestations_and_admission(self):
        """Native writes continue during a hold without granting source proof."""
        import os

        self.approve(self.attest())
        with patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': 'true'}):
            held = self.work('Created while source restoration is held')
            self.assertIsNone(
                read_work_order(
                    self.owner,
                    environment=self.namespace.environment,
                    work_pk=self.new_work.pk,
                )
            )
            with self.assertRaises(service.NativeAuthorityError):
                self.attest(key='held-review')
        self.assertFalse(
            NativeSourceBirth.objects.filter(
                entity_kind='work', source_pk=held.pk
            ).exists()
        )

    def test_conflicting_approved_client_claims_are_withheld(self):
        """Contradictory current ownership is ambiguous rather than unioned authority."""
        for user in (self.owner, self.reviewer):
            ClientScopeGrant.objects.create(user=user, client=self.client_b)
        customer = Company.objects.create(
            name='Conflicting shared customer', is_customer=True
        )
        work = self.work('Conflicting future source', customer=customer)
        self.approve(self.attest(pk=work.pk, key='claim-a'))
        self.approve(
            self.attest(pk=work.pk, clients=(self.client_b.pk,), key='claim-b')
        )
        self.assertIsNone(
            read_work_order(
                self.owner, environment=self.namespace.environment, work_pk=work.pk
            )
        )

    def test_maintenance_and_original_bytes_require_separate_future_proof(self):
        """A proven Work does not automatically attribute its maintenance or documents."""
        self.approve(self.attest())
        record = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=date(2020, 1, 2),
            summary='Synthetic dated record',
            work_order=self.new_work,
        )
        self.assertIsNone(
            read_maintenance_record(
                self.owner, environment=self.namespace.environment, record_pk=record.pk
            )
        )
        self.approve(self.attest('maintenance', record.pk))
        self.assertEqual(
            read_maintenance_record(
                self.owner, environment=self.namespace.environment, record_pk=record.pk
            ).date,
            record.date,
        )
        attachment = Attachment(
            model_type='workorder',
            model_id=self.new_work.pk,
            attachment=SimpleUploadedFile(
                'future-proof.txt', b'Actual synthetic bytes'
            ),
        )
        attachment.save(rebuild=False)
        self.addCleanup(attachment.attachment.delete, save=False)
        self.assertIsNone(
            read_work_attachment(
                self.owner,
                environment=self.namespace.environment,
                attachment_pk=attachment.pk,
            )
        )
        self.approve(self.attest('attachment', attachment.pk))
        actual = read_work_attachment_content(
            self.owner,
            environment=self.namespace.environment,
            attachment_pk=attachment.pk,
            max_bytes=100,
        )
        self.assertEqual(actual.content, b'Actual synthetic bytes')
        with attachment.attachment.storage.open(
            attachment.attachment.name, 'wb'
        ) as handle:
            handle.write(b'A new byte revision')
        self.assertIsNone(
            read_work_attachment_content(
                self.owner,
                environment=self.namespace.environment,
                attachment_pk=attachment.pk,
                max_bytes=100,
            )
        )

    def test_direct_machine_attachment_also_binds_original_bytes(self):
        """A direct native machine document still needs independent byte-bound proof."""
        attachment = Attachment(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment=SimpleUploadedFile(
                'machine-proof.txt', b'Synthetic machine bytes'
            ),
        )
        attachment.save(rebuild=False)
        self.addCleanup(attachment.attachment.delete, save=False)
        self.approve(self.attest('attachment', attachment.pk))
        actual = read_machine_attachment_content(
            self.owner,
            environment=self.namespace.environment,
            attachment_pk=attachment.pk,
            max_bytes=100,
        )
        self.assertEqual(actual.content, b'Synthetic machine bytes')

    def test_source_file_io_is_outside_authority_metadata_ownership(self):
        """Preparation and review never open content inside their state locks."""
        attachment = Attachment(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment=SimpleUploadedFile('io-proof.txt', b'Owned synthetic bytes'),
        )
        attachment.save(rebuild=False)
        self.addCleanup(attachment.attachment.delete, save=False)
        storage = attachment.attachment.storage
        native_open = storage.open
        observations = []

        def opened(*args, **kwargs):
            self.assertFalse(
                any(not block._from_testcase for block in connection.atomic_blocks)
            )
            observations.append(True)
            return native_open(*args, **kwargs)

        with patch.object(storage, 'open', side_effect=opened):
            self.approve(self.attest('attachment', attachment.pk))
        self.assertGreaterEqual(len(observations), 3)

    def test_storage_fault_does_not_create_an_attestation_or_expose_details(self):
        """An unreadable original is unqualified rather than an empty success."""
        attachment = Attachment(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment=SimpleUploadedFile('unreadable-proof.txt', b'Synthetic bytes'),
        )
        attachment.save(rebuild=False)
        self.addCleanup(attachment.attachment.delete, save=False)
        before = NativeAuthorityRequest.objects.count()
        with patch.object(
            attachment.attachment.storage,
            'open',
            side_effect=OSError('synthetic-private-storage-path'),
        ):
            with self.assertRaises(service.NativeAuthorityError) as refused:
                self.attest('attachment', attachment.pk)
        self.assertNotIn('synthetic-private-storage-path', str(refused.exception))
        self.assertEqual(NativeAuthorityRequest.objects.count(), before)

    def test_real_basic_authentication_can_create_the_request(self):
        """Actual Basic authentication resolves the native requester independently of JSON."""
        import base64

        header = base64.b64encode(
            b'native-source-owner:synthetic-source-password'
        ).decode()
        raw = APIRequestFactory().post(
            '/synthetic/native-authority/', {}, HTTP_AUTHORIZATION='Basic ' + header
        )
        request = Request(raw, authenticators=[BasicAuthentication()])
        row = service.request_attribution(
            request,
            namespace_id=self.namespace.pk,
            kind='work',
            source_pk=self.new_work.pk,
            client_ids=(self.client_a.pk,),
            evidence_reference='synthetic-basic-proof',
            request_key='basic-request',
        )
        self.assertEqual(row.requester_pk, self.owner.pk)


class DeletionReadinessTests(NativeAuthorityFixture, TestCase):
    """Actual requester/reviewer permission mapping; no deletion effects."""

    def test_readiness_request_replays_exactly_and_uses_existing_private_intent(self):
        """Exact retries retain one native request and one existing private intent."""
        row = self.readiness()
        replay = self.readiness()
        self.assertEqual(row.pk, replay.pk)
        self.assertEqual(PrivateDeletionRequest.objects.count(), 1)
        self.assertEqual(
            NativeAuthorityRequest.objects.filter(purpose='deletion_readiness').count(),
            1,
        )
        self.assertTrue(WorkOrder.objects.filter(pk=self.new_work.pk).exists())
        with self.assertRaises(PermissionError):
            self.readiness(reference='A different client request')

    def test_review_is_exact_revision_and_no_self_approval(self):
        """Readiness review records a decision without approving any erasure."""
        row = self.readiness()
        self.owner.user_permissions.add(self.review_permission)
        with self.assertRaises(service.NativeAuthorityError):
            service.inspect_authority_request(self.request(), request_pk=row.pk)
        view = service.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        self.assertFalse(view['impact']['coverage_complete'])
        self.assertEqual(
            view['request_details']['declared_scope'],
            [
                {
                    'environment': self.namespace.environment,
                    'epoch': str(self.namespace.epoch),
                    'entity_kind': 'machine',
                    'aspect': 'metadata',
                    'source_id': self.machine.pk,
                }
            ],
        )
        assert_literal_bool(self, view['request_details']['source_ownership_verified'], False)
        self.assertTrue(view['impact']['gaps'])
        self.assertFalse(view['impact']['erasure_authorized'])
        with self.assertRaises(service.NativeAuthorityError):
            service.decide_authority_request(
                self.request(self.reviewer),
                request_pk=row.pk,
                expected_request_digest=view['request_digest'],
                expected_impact_digest='0' * 64,
                approved=True,
            )
        result = self.approve(row)
        self.assertTrue(result.approved)
        self.assertEqual(result.pk, self.approve(row).pk)
        final = service.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        self.assertEqual(final['review_status'], 'approved_review')
        self.assertFalse(final['impact']['erasure_authorized'])
        self.assertTrue(AssetMachine.objects.filter(pk=self.machine.pk).exists())
        self.assertTrue(WorkOrder.objects.filter(pk=self.old_work.pk).exists())

    def test_inconsistent_private_intent_cannot_supply_review_scope(self):
        """A self-consistent row hash cannot replace the original intent binding."""
        row = self.readiness()
        private = PrivateDeletionRequest.objects.get(
            pk=row.source_binding['private_request_pk']
        )
        private.payload['scope'][0]['source_id'] = self.machine.pk + 100
        private.payload_digest = service.fingerprint(private.payload)
        private.save(update_fields=['payload', 'payload_digest'])
        with self.assertRaises(service.NativeAuthorityError):
            service.inspect_authority_request(
                self.request(self.reviewer), request_pk=row.pk
            )
        self.assertFalse(NativeAuthorityDecision.objects.exists())

    def test_overloaded_digest_values_cannot_bypass_exact_review_binding(self):
        """Review hashes require builtin strings, not coercing equality hooks."""

        class EqualDigest(str):
            def __eq__(self, other):
                return True

            __hash__ = None

        row = self.readiness()
        view = service.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        with self.assertRaises(service.NativeAuthorityError):
            service.decide_authority_request(
                self.request(self.reviewer),
                request_pk=row.pk,
                expected_request_digest=EqualDigest('0' * 64),
                expected_impact_digest=view['impact_digest'],
                approved=True,
            )
        self.assertFalse(NativeAuthorityDecision.objects.filter(request=row).exists())

    def test_revoked_requester_does_not_leave_an_approvable_pending_request(self):
        """A cached pending record cannot adopt a requester who lost authority."""
        row = self.readiness()
        self.owner_grant.delete()
        with self.assertRaises(service.NativeAuthorityError):
            self.approve(row)
        self.assertFalse(NativeAuthorityDecision.objects.filter(request=row).exists())

    def test_foreign_client_or_revoked_reviewer_cannot_review(self):
        """Only a current independent reviewer for the exact client can act."""
        row = self.readiness()
        self.reviewer_grant.delete()
        with self.assertRaises(service.NativeAuthorityError):
            self.approve(row)
        ClientScopeGrant.objects.create(user=self.reviewer, client=self.client_b)
        with self.assertRaises(service.NativeAuthorityError):
            self.approve(row)
        self.assertFalse(NativeAuthorityDecision.objects.filter(request=row).exists())

    def test_impact_change_and_shared_protected_copy_remain_explicit(self):
        """A shared protected copy changes review hashes and never grants removal."""
        from integration_state.shared_requests import _intent_from, record_copy_impact

        row = self.readiness()
        view = service.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        private = PrivateDeletionRequest.objects.get(
            pk=row.source_binding['private_request_pk']
        )
        intent = _intent_from(private.payload)
        owners = tuple(
            type(intent.client)(self.namespace.environment, c.pk)
            for c in (self.client_a, self.client_b)
        )
        impact = CopyImpact(
            copy_key='synthetic-shared-private-key',
            source=intent.scope[0],
            copy_revision='synthetic-v1',
            owners=owners,
            attribution='source_claimed',
            retention='protected',
        )
        record_copy_impact(
            private.pk,
            impact,
            intent=intent,
            copy_kind='backup',
            authorize=lambda _: True,
        )
        with self.assertRaises(service.NativeAuthorityError):
            service.decide_authority_request(
                self.request(self.reviewer),
                request_pk=row.pk,
                expected_request_digest=view['request_digest'],
                expected_impact_digest=view['impact_digest'],
                approved=True,
            )
        current = service.inspect_authority_request(
            self.request(self.reviewer), request_pk=row.pk
        )
        claim = current['impact']['copy_claims'][0]
        self.assertTrue(claim['shared_or_unknown_owner'])
        self.assertFalse(claim['removal_authorized'])
        self.assertEqual(claim['retention'], 'protected')
        self.assertNotIn('synthetic-shared-private-key', str(current))
        self.assertTrue(self.approve(row).approved)
        self.assertTrue(PrivateDeletionRequest.objects.filter(pk=private.pk).exists())

    def test_retained_authority_history_blocks_additive_backout(self):
        """Empty-only schema backout cannot erase retained birth or review history."""
        from django.apps import apps

        from integration_state.native_authority_migrations import guard_native_authority

        with self.assertRaises(IrreversibleError):
            guard_native_authority(apps, SimpleNamespace(connection=connection))
        self.assertEqual(NativeAuthorityEpoch.objects.count(), 1)
