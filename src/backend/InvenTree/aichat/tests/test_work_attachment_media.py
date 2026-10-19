"""Native Work previews reuse fresh indexed-media role and client boundaries."""

import hashlib
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from rest_framework.test import APIClient
from tasks.models import WorkOrder

from aichat.models import (
    AttachmentIngest,
    AttachmentIngestPipeline,
    AttachmentIngestState,
)
from assets.models import AssetMachine, Client, ClientScopeGrant
from common.models import Attachment
from company.models import Company
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class WorkAttachmentMediaTests(TestCase):
    """Authenticate a normal stored-role reader, without scope/role mocks."""

    def setUp(self):
        """Create only synthetic work, indexed bytes, a session and a grant."""
        folder = tempfile.TemporaryDirectory(prefix='aimms-p28-work-media-')
        self.addCleanup(folder.cleanup)
        media = override_settings(MEDIA_ROOT=folder.name)
        media.enable()
        self.addCleanup(media.disable)
        self.actor = get_user_model().objects.create_user(
            username='work-media-reader', password='synthetic-only'
        )
        group = Group.objects.create(name='Work media readers')
        self.rules, _created = RuleSet.objects.update_or_create(
            name='work_order', group=group, defaults={'can_view': True}
        )
        self.actor.groups.add(group)
        self.tenant = Client.objects.create(name='Work media A', code='work-media-a')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        machine = AssetMachine.objects.create(
            name='Synthetic work pump', client=self.tenant
        )
        self.work = WorkOrder.objects.create(
            title='Synthetic work media owner', machine=machine
        )
        self.body = b'synthetic-indexed-image-bytes-not-a-codec-acceptance'
        self.attachment = self.attach(self.work)
        self.api = APIClient()
        self.assertTrue(
            self.api.login(username=self.actor.username, password='synthetic-only')
        )

    def attach(self, work):
        """Store an exact owner/hash while suppressing background ingestion."""
        with mock.patch('InvenTree.tasks.offload_task', return_value=True):
            attachment = Attachment.objects.create(
                model_type='workorder',
                model_id=work.pk,
                attachment=SimpleUploadedFile(f'work-{work.pk}.png', self.body),
            )
        AttachmentIngest.objects.create(
            attachment_id=attachment.pk,
            model_type='workorder',
            model_id=work.pk,
            source_sha256=hashlib.sha256(self.body).hexdigest(),
            pipeline=AttachmentIngestPipeline.IMAGE,
            state=AttachmentIngestState.INDEXED,
        )
        return attachment

    def paths(self, attachment):
        """Return both independently authorized evidence read endpoints."""
        base = f'/api/aichat/evidence/media/{attachment.pk}/'
        return (base, f'{base}metadata/')

    def assert_unavailable(self, attachment):
        """Both reads fail closed without returning a filename or owner."""
        for path in self.paths(attachment):
            with self.subTest(path=path):
                response = self.api.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(attachment.attachment.name, response.content.decode())

    def test_own_work_uses_authorized_revision_and_exact_bytes(self):
        """Metadata and bytes preserve indexed identity for a normal reader."""
        stream, metadata = self.paths(self.attachment)
        response = self.api.get(metadata)
        self.assertEqual(response.status_code, 200)
        revision = response.json()['revision']
        self.assertEqual(revision, hashlib.sha256(self.body).hexdigest())
        response = self.api.get(stream, {'revision': revision})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), self.body)

    def test_foreign_customer_attributed_and_unresolved_work_fail_closed(self):
        """A machine grant never widens to another client or sales owner."""
        foreign = Client.objects.create(name='Work media B', code='work-media-b')
        foreign_work = WorkOrder.objects.create(
            title='Foreign work',
            machine=AssetMachine.objects.create(name='Other pump', client=foreign),
        )
        customer_work = WorkOrder.objects.create(
            title='Customer-owned work',
            machine=self.work.machine,
            customer=Company.objects.create(name='Other sales owner', is_customer=True),
        )
        for work in (
            foreign_work,
            customer_work,
            WorkOrder.objects.create(title='Unresolved work'),
        ):
            self.assert_unavailable(self.attach(work))

    def test_client_grant_revocation_is_checked_again_on_both_reads(self):
        """A previous successful preview is not an enduring scope receipt."""
        self.assertEqual(self.api.get(self.paths(self.attachment)[1]).status_code, 200)
        self.grant.delete()
        self.assert_unavailable(self.attachment)

    def test_work_role_revocation_is_checked_again_on_both_reads(self):
        """Session role caches cannot keep media readable after revocation."""
        self.assertEqual(self.api.get(self.paths(self.attachment)[1]).status_code, 200)
        self.rules.can_view = False
        self.rules.save()
        self.assert_unavailable(self.attachment)

    def test_unindexed_work_file_has_no_original_storage_fallback(self):
        """An existing file alone is insufficient preview authority."""
        AttachmentIngest.objects.filter(attachment_id=self.attachment.pk).delete()
        self.assert_unavailable(self.attachment)
