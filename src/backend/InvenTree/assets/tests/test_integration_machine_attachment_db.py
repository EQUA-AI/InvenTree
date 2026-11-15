"""Database controls for machine-owned attachment integration identities."""

import hashlib
import io
import os
import tempfile
from dataclasses import fields
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from tasks import scope as native_scope

from assets import integration_sources
from assets.integration_sources import read_machine_attachment
from assets.models import AssetMachine, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-machine-attachment-fallback',
)
@assume_synthetic_attribution
class MachineAttachmentSourceTests(TestCase):
    """Use only synthetic metadata; no file is accessed and no link is fetched."""

    def setUp(self):
        """Create two clients and a scoped actor with native Work view."""
        self.actor = get_user_model().objects.create_user(username='attachment-reader')
        self.group = Group.objects.create(name='Machine attachment test readers')
        self.actor.groups.add(self.group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=self.group, name='work_order', defaults={'can_view': True}
        )
        self.client_a = Client.objects.create(name='Synthetic A', code='attach-a')
        self.client_b = Client.objects.create(name='Synthetic B', code='attach-b')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.client_a
        )
        self.machine_a = AssetMachine.objects.create(
            name='Synthetic attachment machine A', client=self.client_a
        )
        self.machine_b = AssetMachine.objects.create(
            name='Synthetic attachment machine B', client=self.client_b
        )
        self.own = self._attachment('assetmachine', self.machine_a.pk)
        self.foreign = self._attachment('assetmachine', self.machine_b.pk)

    def _attachment(self, owner_type, owner_pk):
        attachment = Attachment(
            model_type=owner_type,
            model_id=owner_pk,
            link='https://example.invalid/synthetic',
        )
        attachment.save(rebuild=False)
        return attachment

    def test_machine_attachment_identity_requires_current_owner_authority(self):
        """Return only a machine-owned identity after owner and actor checks."""
        reader = read_machine_attachment
        result = reader(self.actor, environment='test', attachment_pk=self.own.pk)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.identity.pk, self.own.pk)
        self.assertEqual(result.machine.pk, self.machine_a.pk)
        self.assertEqual(result.identity.environment, 'test')
        self.assertEqual(result.machine.environment, 'test')
        self.assertEqual(
            {field.name for field in fields(result)},
            {'identity', 'machine', 'schema_version'},
        )
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.foreign.pk)
        )
        self.assertIsNone(reader(self.actor, environment='test', attachment_pk=999999))
        self.grant.delete()
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.own.pk)
        )

    def test_requires_native_named_permission_and_work_role(self):
        """Owner-model permission and Work role are independent requirements."""
        reader = read_machine_attachment
        self.assertIsNotNone(
            reader(self.actor, environment='test', attachment_pk=self.own.pk)
        )
        permission = Permission.objects.get(
            content_type__app_label='assets', codename='view_assetmachine'
        )
        self.assertTrue(self.group.permissions.filter(pk=permission.pk).exists())
        self.group.permissions.remove(permission)
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.own.pk)
        )
        self.group.permissions.add(permission)
        self.assertIsNotNone(
            reader(self.actor, environment='test', attachment_pk=self.own.pk)
        )
        self.rule.can_view = False
        self.rule.save(update_fields=['can_view'])
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.own.pk)
        )

    def test_non_machine_and_missing_owners_are_withheld(self):
        """The owner pair must name a currently authorized live machine."""
        reader = read_machine_attachment
        other_kind = self._attachment('workorder', self.machine_a.pk)
        orphan = self._attachment('assetmachine', 999999)
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=other_kind.pk)
        )
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=orphan.pk)
        )
        AssetMachine.objects.filter(pk=self.machine_a.pk).delete()
        self.assertTrue(Attachment.objects.filter(pk=self.own.pk).exists())
        self.assertIsNone(
            reader(self.actor, environment='test', attachment_pk=self.own.pk)
        )

    def test_reparent_between_checks_withholds_foreign_owner(self):
        """Re-evaluate the actual owner after a sequential reparent operation."""
        reader = read_machine_attachment
        original = native_scope.scoped_machines
        calls = []

        def scope(actor):
            calls.append(actor.pk)
            if len(calls) == 2:
                Attachment.objects.filter(pk=self.own.pk).update(
                    model_id=self.machine_b.pk
                )
            return original(actor)

        with patch('tasks.scope.scoped_machines', side_effect=scope):
            result = reader(self.actor, environment='test', attachment_pk=self.own.pk)
        self.assertEqual(calls, [self.actor.pk, self.actor.pk])
        self.assertIsNone(result)
        self.own.refresh_from_db()
        self.assertEqual(self.own.model_id, self.machine_b.pk)

    def test_ambiguous_or_missing_native_owner_type_is_withheld(self):
        """A colliding registry label cannot establish the attachment's owner."""
        other_model = type('AssetMachine', (), {})
        possible_registries = (
            [AssetMachine, other_model],
            [other_model, AssetMachine],
            [other_model],
            [],
        )
        self.assertIsNotNone(
            read_machine_attachment(
                self.actor, environment='test', attachment_pk=self.own.pk
            )
        )
        for registry in possible_registries:
            with (
                self.subTest(registry=registry),
                patch(
                    'common.validators.attachment_model_types', return_value=registry
                ),
            ):
                self.assertIsNone(
                    read_machine_attachment(
                        self.actor, environment='test', attachment_pk=self.own.pk
                    )
                )

    def test_direct_machine_content_returns_exact_synthetic_bytes(self):
        """The internal bounded reader returns real bytes, not a link or path."""
        with tempfile.TemporaryDirectory(dir=os.environ['TMPDIR']) as directory:
            with override_settings(MEDIA_ROOT=directory):
                content = b'\x00synthetic attachment bytes\xff'
                attachment = Attachment(
                    model_type='assetmachine',
                    model_id=self.machine_a.pk,
                    attachment=SimpleUploadedFile('synthetic.bin', content),
                )
                attachment.save(rebuild=False)
                result = integration_sources.read_machine_attachment_content(
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
                self.assertEqual(result.machine.pk, self.machine_a.pk)

    def test_partial_storage_reads_do_not_masquerade_as_complete_content(self):
        """A storage handle may return short reads before end-of-file."""

        class ShortRead(io.BytesIO):
            def read(self, size: int | None = -1):
                return super().read(2 if size is None or size < 0 else min(size, 2))

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/path.bin'
        )
        with patch(
            'django.core.files.storage.default_storage.open',
            return_value=ShortRead(b'abcdef'),
        ):
            result = integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=6
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.content, b'abcdef')

    def test_foreign_content_does_not_open_storage(self):
        """A denied owner cannot trigger a read of that attachment's bytes."""
        Attachment.objects.filter(pk=self.foreign.pk).update(
            attachment='synthetic/foreign.bin'
        )
        with patch('django.core.files.storage.default_storage.open') as open_file:
            result = integration_sources.read_machine_attachment_content(
                self.actor,
                environment='test',
                attachment_pk=self.foreign.pk,
                max_bytes=64,
            )
        self.assertIsNone(result)
        open_file.assert_not_called()

    def test_absent_missing_and_oversize_content_are_not_empty_success(self):
        """Distinguish absent file, unavailable file and over-budget content."""
        reader = integration_sources.read_machine_attachment_content
        with self.assertRaises(integration_sources.AttachmentContentAbsentError):
            reader(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=4
            )
        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/missing.bin'
        )
        with (
            patch(
                'django.core.files.storage.default_storage.open',
                side_effect=FileNotFoundError,
            ),
            self.assertRaises(integration_sources.AttachmentContentUnavailableError),
        ):
            reader(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=4
            )
        with (
            patch(
                'django.core.files.storage.default_storage.open',
                return_value=io.BytesIO(b'abcde'),
            ),
            self.assertRaises(integration_sources.AttachmentContentLimitError),
        ):
            reader(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=4
            )

    def test_grant_revocation_during_storage_read_withholds_content(self):
        """Withhold bytes when the post-read check observes grant revocation."""
        grant = self.grant

        class RevokeOnRead(io.BytesIO):
            def read(self, size: int | None = -1):
                if grant.pk is not None:
                    grant.delete()
                return super().read(size)

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/revoked.bin'
        )
        handle = RevokeOnRead(b'secret synthetic bytes')
        with patch(
            'django.core.files.storage.default_storage.open', return_value=handle
        ):
            result = integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )
        self.assertIsNone(result)
        self.assertTrue(handle.closed)

    def test_changed_file_reference_during_read_withholds_content(self):
        """Do not return bytes for a path that is no longer the current reference."""
        attachment_pk = self.own.pk

        class ChangeOnRead(io.BytesIO):
            def read(self, size: int | None = -1):
                Attachment.objects.filter(pk=attachment_pk).update(
                    attachment='synthetic/new.bin'
                )
                return super().read(size)

        Attachment.objects.filter(pk=attachment_pk).update(
            attachment='synthetic/old.bin'
        )
        handle = ChangeOnRead(b'old synthetic bytes')
        with patch(
            'django.core.files.storage.default_storage.open', return_value=handle
        ):
            result = integration_sources.read_machine_attachment_content(
                self.actor,
                environment='test',
                attachment_pk=attachment_pk,
                max_bytes=64,
            )
        self.assertIsNone(result)
        self.assertTrue(handle.closed)

    def test_short_read_budget_exhaustion_is_explicit(self):
        """A short-read loop that does not reach EOF cannot return partial bytes."""

        class OneByte(io.BytesIO):
            def read(self, size: int | None = -1):
                return super().read(1 if size is None or size < 0 else min(size, 1))

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/short.bin'
        )
        with (
            patch('assets.integration_sources.MAX_MACHINE_ATTACHMENT_CONTENT_READS', 2),
            patch(
                'django.core.files.storage.default_storage.open',
                return_value=OneByte(b'abc'),
            ),
            self.assertRaises(integration_sources.AttachmentContentUnavailableError),
        ):
            integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=10
            )

    def test_empty_stored_file_is_not_confused_with_link_only(self):
        """A row with both a link and an empty stored file returns its bytes only."""
        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/empty.bin'
        )
        with patch(
            'django.core.files.storage.default_storage.open',
            return_value=io.BytesIO(b''),
        ):
            result = integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=1
            )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.content, b'')
        self.assertEqual(result.content_sha256, hashlib.sha256(b'').hexdigest())
        self.assertEqual(
            {item.name for item in fields(result)},
            {'identity', 'machine', 'content', 'content_sha256', 'schema_version'},
        )

    def test_invalid_byte_budgets_fail_before_authorization_or_storage(self):
        """Boolean, zero and over-maximum budgets cannot cause source reads."""
        with (
            patch('assets.integration_sources.read_machine_attachment') as owner_read,
            patch('django.core.files.storage.default_storage.open') as open_file,
        ):
            for value in (
                True,
                0,
                -1,
                integration_sources.MAX_MACHINE_ATTACHMENT_CONTENT_BYTES + 1,
            ):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    integration_sources.read_machine_attachment_content(
                        self.actor,
                        environment='test',
                        attachment_pk=self.own.pk,
                        max_bytes=value,
                    )
        owner_read.assert_not_called()
        open_file.assert_not_called()

    def test_storage_failure_after_revocation_does_not_disclose_error_state(self):
        """If authority is lost during a failed read, return the denied outcome."""
        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/unavailable.bin'
        )

        def fail_after_revoke(*args, **kwargs):
            self.grant.delete()
            raise OSError('synthetic storage failure')

        with patch(
            'django.core.files.storage.default_storage.open',
            side_effect=fail_after_revoke,
        ):
            result = integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=4
            )
        self.assertIsNone(result)

    def test_non_os_storage_failure_after_revocation_is_withheld(self):
        """A storage-specific error after revocation must not disclose its detail."""
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
            result = integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=4
            )
        self.assertIsNone(result)
