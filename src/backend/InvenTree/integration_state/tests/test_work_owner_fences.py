"""Real future proof, exact owner identities and fresh post-projection checks."""

from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from tasks import integration_attachments as boundary
from tasks import integration_sources
from tasks.models import WorkOrder

from common.models import Attachment
from integration_state import native_authority
from integration_state.tests.test_native_authority import NativeAuthorityFixture


class WorkOwnerIdentityFenceTests(NativeAuthorityFixture, TestCase):
    """No fake actor, native scope, grants or source-attribution predicate."""

    def setUp(self):
        """Separately review an actual future Work and its original bytes."""
        super().setUp()
        self.work_claim = self.attest()
        self.approve(self.work_claim)
        self.original = Attachment(
            model_type='workorder',
            model_id=self.new_work.pk,
            attachment=SimpleUploadedFile('native-owner.bin', b'Native owner bytes'),
        )
        self.original.save(rebuild=False)
        self.addCleanup(self.original.attachment.delete, save=False)
        self.approve(self.attest('attachment', self.original.pk))

    def read_identity(self):
        """Use the real current native minimal owner boundary."""
        return boundary.read_work_attachment(
            self.owner,
            environment=self.namespace.environment,
            attachment_pk=self.original.pk,
        )

    def test_actual_independent_work_proof_is_still_required(self):
        """A current original claim cannot lend authority to its withdrawn Work."""
        value = self.read_identity()
        self.assertIsNotNone(value)
        self.assertEqual(value.identity.pk, self.original.pk)
        self.assertEqual(value.work.pk, self.new_work.pk)
        native_authority.withdraw_authority_request(
            self.request(),
            request_pk=self.work_claim.pk,
            expected_request_digest=self.work_claim.binding_digest,
            reason_reference='synthetic-owner-proof-withdrawal',
        )
        self.assertIsNone(self.read_identity())

    def test_constructor_revocation_stops_subsequent_original_io(self):
        """An admitted original is refused after grant loss during projection."""
        positive = boundary.read_work_attachment_content(
            self.owner,
            environment=self.namespace.environment,
            attachment_pk=self.original.pk,
            max_bytes=64,
        )
        self.assertEqual(positive.content, b'Native owner bytes')
        original_init = boundary.WorkAttachmentSourceRecord.__init__
        original_open = default_storage.open
        revoked = False
        later_opens = []

        def construct(value, *args, **kwargs):
            nonlocal revoked
            original_init(value, *args, **kwargs)
            self.owner_grant.delete()
            revoked = True

        def open_original(*args, **kwargs):
            if revoked:
                later_opens.append(True)
            return original_open(*args, **kwargs)

        with (
            patch.object(boundary.WorkAttachmentSourceRecord, '__init__', construct),
            patch.object(default_storage, 'open', side_effect=open_original),
        ):
            result = boundary.read_work_attachment_content(
                self.owner,
                environment=self.namespace.environment,
                attachment_pk=self.original.pk,
                max_bytes=64,
            )
        self.assertTrue(revoked)
        self.assertIsNone(result)
        self.assertEqual(later_opens, [])

    def test_constructor_cannot_rebind_or_coerce_the_observed_identity(self):
        """Actual class identity does not bless changed nested fields or booleans."""
        original_init = boundary.WorkAttachmentSourceRecord.__init__
        for mutation in ('attachment', 'owner', 'alias', 'schema'):
            with self.subTest(mutation=mutation):

                def construct(value, *args, mutation=mutation, **kwargs):
                    original_init(value, *args, **kwargs)
                    if mutation == 'attachment':
                        object.__setattr__(value.identity, 'pk', self.original.pk + 1)
                    elif mutation == 'owner':
                        object.__setattr__(value.work, 'pk', self.old_work.pk)
                    elif mutation == 'alias':
                        object.__setattr__(value.identity, 'pk', True)
                    else:
                        object.__setattr__(value, 'schema_version', 'unknown')

                with patch.object(
                    boundary.WorkAttachmentSourceRecord, '__init__', construct
                ):
                    self.assertIsNone(self.read_identity())
        self.assertIsNotNone(self.read_identity())

    def test_work_revision_change_before_final_scope_is_withheld(self):
        """Current native visibility alone cannot preserve an earlier Work proof."""
        actual = integration_sources._integration_scoped_work_orders
        calls = []

        def changed(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                WorkOrder.objects.filter(pk=self.new_work.pk).update(
                    title='Changed native owner material'
                )
            return actual(*args, **kwargs)

        with patch.object(
            integration_sources, '_integration_scoped_work_orders', side_effect=changed
        ):
            self.assertIsNone(self.read_identity())
        self.assertEqual(len(calls), 2)

    def test_constructor_fault_is_refused_and_process_failure_propagates(self):
        """A private construction fault is not successful identity or bytes."""
        with patch.object(
            boundary.WorkAttachmentSourceRecord,
            '__init__',
            side_effect=ValueError('Synthetic private constructor detail'),
        ):
            self.assertIsNone(self.read_identity())
        with (
            patch.object(
                boundary.WorkAttachmentSourceRecord,
                '__init__',
                side_effect=MemoryError('Synthetic process failure'),
            ),
            self.assertRaises(MemoryError),
        ):
            self.read_identity()
