"""Independent authorized-storage error controls for both direct owners."""

import io
from unittest.mock import patch

from django.test import TestCase, override_settings

from tasks.tests import test_integration_work_attachment_db as work_fixtures

from assets import integration_sources
from assets.models import ClientScopeGrant
from assets.tests import test_integration_machine_attachment_db as machine_fixtures
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-machine-attachment-fallback',
)
@assume_synthetic_attribution
class MachineStorageErrorTests(TestCase):
    """Exercise direct-machine attachment storage failures."""

    setUp = machine_fixtures.MachineAttachmentSourceTests.setUp
    _attachment = machine_fixtures.MachineAttachmentSourceTests._attachment

    def test_authorized_non_os_open_failure_is_generic_unavailable(self):
        """An authorized non-OSError open failure yields the generic error."""
        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/provider.bin'
        )
        with patch(
            'django.core.files.storage.default_storage.open',
            side_effect=RuntimeError('synthetic provider detail'),
        ):
            with self.assertRaises(
                integration_sources.AttachmentContentUnavailableError
            ) as ctx:
                integration_sources.read_machine_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=64,
                )
        self.assertEqual(str(ctx.exception), 'Stored content is unavailable')
        self.assertNotIn('provider detail', str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_authorized_non_os_read_failure_is_unavailable_and_closes_handle(self):
        """A non-OSError read failure is unavailable and the handle is closed."""

        class FailOnRead(io.BytesIO):
            def read(self, size: int | None = -1):
                raise RuntimeError('synthetic provider detail')

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/read-fail.bin'
        )
        handle = FailOnRead(b'synthetic bytes')
        with patch(
            'django.core.files.storage.default_storage.open', return_value=handle
        ):
            with self.assertRaises(
                integration_sources.AttachmentContentUnavailableError
            ):
                integration_sources.read_machine_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=64,
                )
        self.assertTrue(handle.closed)

    def test_non_os_read_failure_after_revocation_returns_denial(self):
        """Revocation seen during a failed read returns the denied outcome."""
        grant = self.grant

        class RevokeThenFail(io.BytesIO):
            def read(self, size: int | None = -1):
                ClientScopeGrant.objects.filter(pk=grant.pk).delete()
                raise RuntimeError('synthetic provider detail')

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/revoke-fail.bin'
        )
        handle = RevokeThenFail(b'synthetic bytes')
        with patch(
            'django.core.files.storage.default_storage.open', return_value=handle
        ):
            result = integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )
        self.assertIsNone(result)
        self.assertTrue(handle.closed)

    def test_non_os_read_failure_after_reference_change_returns_denial(self):
        """A changed stored reference during a failed read returns the denial."""
        attachment_pk = self.own.pk

        class ChangeThenFail(io.BytesIO):
            def read(self, size: int | None = -1):
                Attachment.objects.filter(pk=attachment_pk).update(
                    attachment='synthetic/new.bin'
                )
                raise RuntimeError('synthetic provider detail')

        Attachment.objects.filter(pk=attachment_pk).update(
            attachment='synthetic/old.bin'
        )
        handle = ChangeThenFail(b'synthetic bytes')
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

    def test_process_control_storage_failure_is_not_caught(self):
        """A BaseException-only storage failure must propagate unchanged."""

        class SyntheticProcessControl(BaseException):
            pass

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/process-control.bin'
        )
        with (
            patch(
                'django.core.files.storage.default_storage.open',
                side_effect=SyntheticProcessControl(),
            ),
            self.assertRaises(SyntheticProcessControl),
        ):
            integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )

    def test_post_failure_recheck_errors_are_not_masked(self):
        """An error raised by the post-failure recheck must propagate."""
        from django.db import DatabaseError

        real_owner_read = integration_sources.read_machine_attachment
        calls = []

        def owner_read(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise DatabaseError('synthetic recheck failure')
            return real_owner_read(*args, **kwargs)

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/recheck.bin'
        )
        with (
            patch(
                'assets.integration_sources.read_machine_attachment',
                side_effect=owner_read,
            ),
            patch(
                'django.core.files.storage.default_storage.open',
                side_effect=RuntimeError('synthetic provider detail'),
            ),
            self.assertRaises(DatabaseError),
        ):
            integration_sources.read_machine_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )
        self.assertEqual(len(calls), 3)

    def test_non_os_failure_for_denied_actor_never_opens_storage(self):
        """A denied owner returns the denial without any storage access."""
        Attachment.objects.filter(pk=self.foreign.pk).update(
            attachment='synthetic/denied.bin'
        )
        with patch(
            'django.core.files.storage.default_storage.open',
            side_effect=RuntimeError('synthetic provider detail'),
        ) as open_file:
            result = integration_sources.read_machine_attachment_content(
                self.actor,
                environment='test',
                attachment_pk=self.foreign.pk,
                max_bytes=64,
            )
        self.assertIsNone(result)
        open_file.assert_not_called()


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-work-attachment-fallback',
)
@assume_synthetic_attribution
class WorkStorageErrorTests(TestCase):
    """Exercise direct-Work attachment storage failures."""

    setUp = work_fixtures.WorkAttachmentSourceTests.setUp
    _attachment = work_fixtures.WorkAttachmentSourceTests._attachment

    def test_authorized_non_os_open_failure_is_generic_unavailable(self):
        """An authorized non-OSError open failure yields the generic error."""
        from tasks.integration_attachments import read_work_attachment_content

        from assets.integration_sources import AttachmentContentUnavailableError

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/provider.bin'
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            opened.side_effect = RuntimeError('synthetic provider detail')
            with self.assertRaises(AttachmentContentUnavailableError) as ctx:
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=64,
                )
        self.assertEqual(str(ctx.exception), 'Stored content is unavailable')
        self.assertNotIn('provider detail', str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertTrue(ctx.exception.__suppress_context__)

    def test_authorized_non_os_read_failure_is_unavailable_and_closes_handle(self):
        """A non-OSError read failure is unavailable and the handle is closed."""
        from tasks.integration_attachments import read_work_attachment_content

        from assets.integration_sources import AttachmentContentUnavailableError

        class FailOnRead(io.BytesIO):
            def read(self, size: int | None = -1):
                raise RuntimeError('synthetic provider detail')

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/read-fail.bin'
        )
        handle = FailOnRead(b'synthetic bytes')
        with patch(
            'django.core.files.storage.default_storage.open', return_value=handle
        ):
            with self.assertRaises(AttachmentContentUnavailableError):
                read_work_attachment_content(
                    self.actor,
                    environment='test',
                    attachment_pk=self.own.pk,
                    max_bytes=64,
                )
        self.assertTrue(handle.closed)

    def test_non_os_read_failure_after_revocation_returns_denial(self):
        """Revocation seen during a failed read returns the denied outcome."""
        from tasks.integration_attachments import read_work_attachment_content

        grant = self.grant

        class RevokeThenFail(io.BytesIO):
            def read(self, size: int | None = -1):
                ClientScopeGrant.objects.filter(pk=grant.pk).delete()
                raise RuntimeError('synthetic provider detail')

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/revoke-fail.bin'
        )
        handle = RevokeThenFail(b'synthetic bytes')
        with patch(
            'django.core.files.storage.default_storage.open', return_value=handle
        ):
            result = read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )
        self.assertIsNone(result)
        self.assertTrue(handle.closed)

    def test_non_os_read_failure_after_reference_change_returns_denial(self):
        """A changed reference or owner during a failed read is denied."""
        from tasks.integration_attachments import read_work_attachment_content

        for changed in (
            {'attachment': 'synthetic/new.bin'},
            {'model_id': self.work_b.pk},
        ):
            with self.subTest(changed=changed):
                Attachment.objects.filter(pk=self.own.pk).update(
                    model_id=self.work_a.pk, attachment='synthetic/old.bin'
                )
                attachment_pk = self.own.pk

                class ChangeThenFail(io.BytesIO):
                    def __init__(self, pk, changes):
                        super().__init__(b'synthetic bytes')
                        self.pk = pk
                        self.changes = changes

                    def read(self, size: int | None = -1):
                        Attachment.objects.filter(pk=self.pk).update(**self.changes)
                        raise RuntimeError('synthetic provider detail')

                handle = ChangeThenFail(attachment_pk, changed)
                with patch(
                    'django.core.files.storage.default_storage.open',
                    return_value=handle,
                ):
                    result = read_work_attachment_content(
                        self.actor,
                        environment='test',
                        attachment_pk=attachment_pk,
                        max_bytes=64,
                    )
                self.assertIsNone(result)

    def test_process_control_storage_failure_is_not_caught(self):
        """A BaseException-only storage failure must propagate unchanged."""
        from tasks.integration_attachments import read_work_attachment_content

        class SyntheticProcessControl(BaseException):
            pass

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/process-control.bin'
        )
        with (
            patch(
                'django.core.files.storage.default_storage.open',
                side_effect=SyntheticProcessControl(),
            ),
            self.assertRaises(SyntheticProcessControl),
        ):
            read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )

    def test_post_failure_recheck_errors_are_not_masked(self):
        """An error raised by the post-failure recheck must propagate."""
        from django.db import DatabaseError

        from tasks.integration_attachments import (
            read_work_attachment,
            read_work_attachment_content,
        )

        real_owner_read = read_work_attachment
        calls = []

        def owner_read(*args, **kwargs):
            calls.append(1)
            if len(calls) == 3:
                raise DatabaseError('synthetic recheck failure')
            return real_owner_read(*args, **kwargs)

        Attachment.objects.filter(pk=self.own.pk).update(
            attachment='synthetic/recheck.bin'
        )
        with (
            patch(
                'tasks.integration_attachments.read_work_attachment',
                side_effect=owner_read,
            ),
            patch(
                'django.core.files.storage.default_storage.open',
                side_effect=RuntimeError('synthetic provider detail'),
            ),
            self.assertRaises(DatabaseError),
        ):
            read_work_attachment_content(
                self.actor, environment='test', attachment_pk=self.own.pk, max_bytes=64
            )
        self.assertEqual(len(calls), 3)

    def test_non_os_failure_for_denied_actor_never_opens_storage(self):
        """A denied owner returns the denial without any storage access."""
        from tasks.integration_attachments import read_work_attachment_content

        Attachment.objects.filter(pk=self.foreign.pk).update(
            attachment='synthetic/denied.bin'
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            opened.side_effect = RuntimeError('synthetic provider detail')
            result = read_work_attachment_content(
                self.actor,
                environment='test',
                attachment_pk=self.foreign.pk,
                max_bytes=64,
            )
        self.assertIsNone(result)
        opened.assert_not_called()
