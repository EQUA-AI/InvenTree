"""Owned future-only native source subjects; review is performed over real HTTP."""

from datetime import date


def create_subjects(runtime, reader_pk, machine):
    """Create future subjects and distinct ordinary requester/reviewer accounts."""
    from assets.models import AssetMaintenanceRecord, ClientScopeGrant
    from common.models import Attachment
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group, Permission
    from django.core.files.uploadedfile import SimpleUploadedFile
    from integration_state.models import SourceNamespace
    from integration_state.native_authority import start_attribution_epoch
    from tasks.models import WorkOrder
    from users.models import RuleSet

    settings.AIMMS_INTEGRATION_AUTHORITY_ENABLED = True
    namespace = SourceNamespace.objects.create(environment="synthetic-browser", active=True)
    start_attribution_epoch(namespace.pk)
    group = Group.objects.create(name="Synthetic source request administrators")
    RuleSet.objects.update_or_create(
        group=group, name="admin", defaults={"can_view": True, "can_change": True}
    )
    RuleSet.objects.update_or_create(group=group, name="work_order", defaults={"can_view": True})
    reviewers = Group.objects.create(name="Synthetic independent source reviewers")
    RuleSet.objects.update_or_create(
        group=reviewers, name="work_order", defaults={"can_view": True}
    )
    requester = get_user_model().objects.create_user(
        username="native-browser-requester", password="synthetic-browser-only"
    )
    reviewer = get_user_model().objects.create_user(
        username="native-browser-reviewer", password="synthetic-browser-only"
    )
    requester.groups.add(group)
    reviewer.groups.add(reviewers)
    reviewer.user_permissions.add(
        Permission.objects.get(content_type__app_label="approvals", codename="review")
    )
    for actor in [requester, reviewer]:
        ClientScopeGrant.objects.create(user=actor, client=machine.client)
    work = WorkOrder.objects.create(
        title="Future browser source Work",
        machine=machine,
        status=WorkOrder.STATUS_BACKLOG,
        priority=WorkOrder.PRIORITY_MEDIUM,
    )
    maintenance = AssetMaintenanceRecord.objects.create(
        machine=machine,
        work_order=work,
        date=date(2025, 2, 3),
        summary="Future browser maintenance source",
        details="Synthetic independent native maintenance details",
    )
    attachment = Attachment(
        model_type="workorder",
        model_id=work.pk,
        comment="Synthetic protected original",
        attachment=SimpleUploadedFile(
            "synthetic-original.txt", b"Synthetic protected original bytes"
        ),
    )
    attachment.save(rebuild=False)
    large = Attachment(
        model_type="workorder",
        model_id=work.pk,
        comment="Synthetic maximum bounded original",
        attachment=SimpleUploadedFile("synthetic-maximum.bin", b"X" * (1024 * 1024)),
    )
    large.save(rebuild=False)
    return {
        "large_attachment": large.pk,
        "work": work.pk,
        "maintenance": maintenance.pk,
        "attachment": attachment.pk,
        "client": machine.client_id,
        "namespace": namespace.pk,
        "reader": reader_pk,
    }
