"""Synthetic database tests for direct Work-owned attachment identities."""

import hashlib
import importlib
import importlib.util
import io
import os
import tempfile
from dataclasses import fields
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment
from company.models import Company
from tasks.models import WorkOrder
from tasks.scope import scoped_work_orders
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-work-attachment-fallback',
)
@assume_synthetic_attribution
class WorkAttachmentSourceTests(TestCase):
    """Use synthetic link-only attachment rows; no link is fetched."""

    def setUp(self):
        """Create a scoped Work viewer and synthetic shared/machine-only Work."""
        self.actor = get_user_model().objects.create_user(
            username='work-attachment-reader'
        )
        self.group = Group.objects.create(name='Work attachment readers')
        self.actor.groups.add(self.group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=self.group, name='work_order', defaults={'can_view': True}
        )
        self.client_a = Client.objects.create(name='Synthetic A', code='work-attach-a')
        self.client_b = Client.objects.create(name='Synthetic B', code='work-attach-b')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.client_a
        )
        self.machine_a = AssetMachine.objects.create(
            name='Work attachment A', client=self.client_a
        )
        self.machine_b = AssetMachine.objects.create(
            name='Work attachment B', client=self.client_b
        )
        customer = Company.objects.create(name='Synthetic customer', is_customer=True)
        self.work_a = WorkOrder.objects.create(
            title='Synthetic machine-only A',
            machine=self.machine_a,
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )
        self.work_b = WorkOrder.objects.create(
            title='Synthetic shared B',
            machine=self.machine_b,
            customer=customer,
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )
        self.machine_work_b = WorkOrder.objects.create(
            title='Synthetic machine-only B',
            machine=self.machine_b,
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
        )
        self.own = self._attachment(self.work_a.pk)
        self.foreign = self._attachment(self.work_b.pk)
        self.machine_foreign = self._attachment(self.machine_work_b.pk)

    def _attachment(self, work_pk):
        attachment = Attachment(
            model_type='workorder',
            model_id=work_pk,
            link='https://example.invalid/synthetic',
        )
        attachment.save(rebuild=False)
        return attachment

    def test_work_attachment_intersects_native_owner_and_matching_client_grant(self):
        """Shared Work stays native-visible but needs attribution for access."""
        name = 'tasks.integration_attachments'
        self.assertIsNotNone(
            importlib.util.find_spec(name), 'Work attachment source is missing'
        )
        reader = importlib.import_module(name).read_work_attachment
        self.assertEqual(
            set(scoped_work_orders(self.actor).values_list('pk', flat=True)),
            {self.work_a.pk, self.work_b.pk},
        )
        result = reader(self.actor, environment='test', attachment_pk=self.own.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.identity.pk, self.own.pk)
        self.assertEqual(result.work.pk, self.work_a.pk)
        self.assertEqual(
            {item.name for item in fields(result)},
            {'identity', 'work', 'schema_version'},
        )
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.foreign.pk)
        )
        self.grant.delete()
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.own.pk)
        )
        ClientScopeGrant.objects.create(user=self.actor, client=self.client_b)
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.foreign.pk)
        )
        self.assertIsNotNone(
            reader(
                self.actor, environment='test', attachment_pk=self.machine_foreign.pk
            )
        )

    def test_shared_granted_machine_attachment_never_opens_content(self):
        """A matching machine grant cannot prove a shared Work attachment owner."""
        from tasks.integration_attachments import (
            read_work_attachment,
            read_work_attachment_content,
        )

        customer = Company.objects.create(name='Synthetic A customer', is_customer=True)
        self.work_a.customer = customer
        self.work_a.save(update_fields=['customer'])
        Attachment.objects.filter(pk=self.own.pk).update(attachment='synthetic.bin')
        self.assertTrue(
            scoped_work_orders(self.actor).filter(pk=self.work_a.pk).exists()
        )
        self.assertIsNone(
            read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assertIsNone(
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=8,
                )
            )
            opened.assert_not_called()

    def test_reparent_during_second_owner_read_withholds_stale_identity(self):
        """The selected attachment must still belong to the authorized Work."""
        from tasks.integration_attachments import read_work_attachment
        from tasks.integration_sources import _integration_scoped_work_orders

        calls = []

        def reparent_after_read(*args, **kwargs):
            result = _integration_scoped_work_orders(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                Attachment.objects.filter(pk=self.own.pk).update(
                    model_id=self.work_b.pk
                )
            return result

        with patch(
            'tasks.integration_sources._integration_scoped_work_orders',
            side_effect=reparent_after_read,
        ):
            result = read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        self.assertEqual(len(calls), 2)
        self.assertIsNone(result)

    def test_work_machine_change_during_owner_read_withholds_stale_identity(self):
        """The final edge check must also retain the current client grant."""
        from tasks.integration_attachments import read_work_attachment
        from tasks.integration_sources import _integration_scoped_work_orders

        calls = []

        def move_work_after_read(*args, **kwargs):
            result = _integration_scoped_work_orders(*args, **kwargs)
            calls.append(result)
            if len(calls) == 2:
                WorkOrder.objects.filter(pk=self.work_a.pk).update(
                    machine=self.machine_b
                )
            return result

        with patch(
            'tasks.integration_sources._integration_scoped_work_orders',
            side_effect=move_work_after_read,
        ):
            result = read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        self.assertEqual(len(calls), 2)
        self.assertIsNone(result)

    def test_native_owner_permission_and_current_work_role_are_required(self):
        """Work scope alone does not replace the native attachment owner permission."""
        from tasks.integration_attachments import read_work_attachment

        permission = Permission.objects.get(
            content_type__app_label='tasks', codename='view_workorder'
        )
        self.assertTrue(self.group.permissions.filter(pk=permission.pk).exists())
        self.assertIsNotNone(
            read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )
        self.group.permissions.remove(permission)
        self.assertIsNone(
            read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )
        self.group.permissions.add(permission)
        self.assertIsNotNone(
            read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(
            read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )

    def test_ambiguous_registry_and_other_or_missing_owners_are_withheld(self):
        """Owner labels must resolve uniquely, and the owner must still exist."""
        from tasks.integration_attachments import read_work_attachment

        impostor = type('WorkOrder', (), {})
        for registry in ([WorkOrder, impostor], [impostor, WorkOrder], [impostor], []):
            with (
                self.subTest(registry=registry),
                patch(
                    'common.validators.attachment_model_types', return_value=registry
                ),
            ):
                self.assertIsNone(
                    read_work_attachment(
                        self.actor, environment='test', attachment_pk=self.own.pk
                    )
                )
        Attachment.objects.filter(pk=self.own.pk).update(model_type='assetmachine')
        self.assertIsNone(
            read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )
        Attachment.objects.filter(pk=self.own.pk).update(
            model_type='workorder', model_id=999999
        )
        self.assertIsNone(
            read_work_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )

    def test_direct_work_attachment_content_returns_exact_authorized_bytes(self):
        """A Work owner can read bounded local bytes without fetching links."""
        from tasks import integration_attachments

        reader = getattr(integration_attachments, 'read_work_attachment_content', None)
        self.assertTrue(callable(reader), 'Work attachment content reader is missing')
        assert callable(reader)
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            with override_settings(MEDIA_ROOT=directory):
                content = b'\x00synthetic work attachment bytes\xff'
                attachment = Attachment(
                    model_type='workorder',
                    model_id=self.work_a.pk,
                    attachment=SimpleUploadedFile('synthetic.bin', content),
                )
                attachment.save(rebuild=False)
                result = reader(
                    self.actor,
                    environment='test',
                    attachment_pk=attachment.pk,
                    max_bytes=64,
                )
                self.assertIsNotNone(result)
                assert result is not None
                self.assertEqual(result.content, content)
                self.assertEqual(
                    result.content_sha256, hashlib.sha256(content).hexdigest()
                )
                self.assertEqual(result.identity.pk, attachment.pk)
                self.assertEqual(result.work.pk, self.work_a.pk)
                self.grant.delete()
                self.assertIsNone(
                    reader(
                        self.actor,
                        environment='test',
                        attachment_pk=attachment.pk,
                        max_bytes=64,
                    )
                )

    def test_work_content_handles_short_reads_without_partial_success(self):
        """Short storage chunks must be read through EOF before returning bytes."""
        from tasks.integration_attachments import read_work_attachment_content

        Attachment.objects.filter(pk=self.own.pk).update(attachment='synthetic.bin')

        class ShortReads(io.BytesIO):
            def read(self, size: int | None = -1):
                return super().read(min(size, 2) if size is not None else 2)

        with patch('django.core.files.storage.default_storage.open') as opened:
            opened.return_value = ShortReads(b'abcdef')
            result = read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=6
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.content, b'abcdef')

    def test_work_content_distinguishes_absent_limit_and_denied(self):
        """An authorized absent file is distinct from an inaccessible owner."""
        from assets.integration_sources import (
            AttachmentContentAbsentError,
            AttachmentContentLimitError,
        )
        from tasks.integration_attachments import read_work_attachment_content

        with self.assertRaises(AttachmentContentAbsentError):
            read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=4
            )
        for limit in (True, 0, 1024 * 1024 + 1):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=limit,
                )
        Attachment.objects.filter(pk=self.own.pk).update(attachment='synthetic.bin')
        with patch('django.core.files.storage.default_storage.open') as opened:
            opened.return_value = io.BytesIO(b'12345')
            with self.assertRaises(AttachmentContentLimitError):
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=4,
                )
        self.grant.delete()
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assertIsNone(
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=4,
                )
            )
            opened.assert_not_called()

    def test_work_content_withholds_bytes_after_grant_revocation_during_read(self):
        """A fresh authorization check follows the selected synthetic read."""
        from tasks.integration_attachments import read_work_attachment_content

        Attachment.objects.filter(pk=self.own.pk).update(attachment='synthetic.bin')
        grant = self.grant

        class RevokeDuringRead(io.BytesIO):
            def read(self, size: int | None = -1):
                result = super().read(size)
                ClientScopeGrant.objects.filter(pk=grant.pk).delete()
                return result

        with patch('django.core.files.storage.default_storage.open') as opened:
            opened.return_value = RevokeDuringRead(b'synthetic')
            result = read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )
        self.assertIsNone(result)

    def test_work_content_withholds_changed_owner_or_filename_during_read(self):
        """A changed owner edge or storage reference cannot yield stale bytes."""
        from tasks.integration_attachments import read_work_attachment_content

        class ChangeDuringRead(io.BytesIO):
            def __init__(self, attachment_pk, changes):
                super().__init__(b'synthetic')
                self.attachment_pk = attachment_pk
                self.changes = changes

            def read(self, size: int | None = -1):
                result = super().read(size)
                Attachment.objects.filter(pk=self.attachment_pk).update(**self.changes)
                return result

        for changed in ({'model_id': self.work_b.pk}, {'attachment': 'new.bin'}):
            with self.subTest(changed=changed):
                Attachment.objects.filter(pk=self.own.pk).update(
                    model_id=self.work_a.pk, attachment='synthetic.bin'
                )
                attachment_pk = self.own.pk
                with patch('django.core.files.storage.default_storage.open') as opened:
                    opened.return_value = ChangeDuringRead(attachment_pk, changed)
                    result = read_work_attachment_content(
                        self.actor,
                        environment='test',
                        attachment_pk=attachment_pk,
                        max_bytes=64,
                    )
                self.assertIsNone(result)

    def test_work_content_rejects_unavailable_or_non_byte_storage(self):
        """An authorized missing or invalid storage response is not empty success."""
        from assets.integration_sources import AttachmentContentUnavailableError
        from tasks.integration_attachments import read_work_attachment_content

        Attachment.objects.filter(pk=self.own.pk).update(attachment='synthetic.bin')
        with patch('django.core.files.storage.default_storage.open') as opened:
            opened.side_effect = OSError('synthetic storage error')
            with self.assertRaises(AttachmentContentUnavailableError):
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=8,
                )
        with patch('django.core.files.storage.default_storage.open') as opened:
            opened.return_value = io.StringIO('not bytes')
            with self.assertRaises(AttachmentContentUnavailableError):
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=8,
                )

    def test_non_os_storage_failure_after_revocation_is_withheld(self):
        """Provider-specific failure after grant removal returns denial."""
        from tasks.integration_attachments import read_work_attachment_content

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/fail.bin'
        )

        def fail_after_revoke(*args, **kwargs):
            ClientScopeGrant.objects.filter(pk=self.grant.pk).delete()
            raise RuntimeError('synthetic provider-specific storage error')

        with patch(
            'django.core.files.storage.default_storage.open',
            side_effect=fail_after_revoke,
        ):
            result = read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=4
            )
        self.assertIsNone(result)

    def test_short_read_budget_is_owned_by_the_native_reader(self):
        """A reduced native read count cannot become successful partial content."""
        from tasks import integration_attachments

        class OneByte(io.BytesIO):
            def read(self, size=-1):
                return super().read(1)

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/short.bin'
        )
        with (
            patch.object(
                integration_attachments, 'MAX_MACHINE_ATTACHMENT_CONTENT_READS', 2
            ),
            patch(
                'django.core.files.storage.default_storage.open',
                return_value=OneByte(b'abc'),
            ),
            self.assertRaises(
                integration_attachments.AttachmentContentUnavailableError
            ),
        ):
            integration_attachments.read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=10
            )
