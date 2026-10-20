"""Explicit chat approval is the only bridge from private chat to Work evidence."""

import hashlib
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q

from rest_framework.exceptions import ValidationError

from aichat.models import ChatTurn
from aichat.services import proposals
from aichat.services.threads import ThreadRepository, ThreadRepositoryError
from aichat.services.work_focus import (
    WORK_FOCUS_SNAPSHOT_KEY,
    WorkFocusError,
    authorized_focus,
)

ACTION = 'investigation.artifact'
MAX_FILE_BYTES = 20 * 1024 * 1024


@transaction.atomic
def source(owner, *, work_order_id, intent, thread_id, source_turn_id, current=True):
    """Resolve the signed turn through both current policy and business scope."""
    from tasks.services.investigation_requests import _change_actor

    from ai.core.auth import principal_for_user

    try:
        owner = _change_actor(owner)
        principal = principal_for_user(owner)
        repository = ThreadRepository(owner, principal.scope)
        thread = repository._lock_thread(thread_id)
        turn = ChatTurn.objects.filter(pk=source_turn_id, thread=thread).first()
        if turn is None:
            raise proposals.ProposalNotFound(
                'Investigation source unavailable. Nothing was saved.'
            )
        snapshot = (turn.trusted_context or {}).get(WORK_FOCUS_SNAPSHOT_KEY)
        if not isinstance(snapshot, dict) or snapshot.get('schema_version') != 2:
            raise proposals.ProposalNotFound(
                'Request focus unavailable. Nothing was saved.'
            )
        if (
            type(work_order_id) is not int
            or snapshot.get('work_order_id') != work_order_id
            or type(intent.get('request_id')) is not int
            or type(intent.get('request_version')) is not int
            or snapshot.get('request_id') != intent.get('request_id')
            or snapshot.get('request_version') != intent.get('request_version')
        ):
            raise proposals.ProposalNotFound(
                'Investigation source unavailable. Nothing was saved.'
            )
        live = authorized_focus(owner.pk, work_order_id, intent['request_id'])
    except PermissionDenied as exc:
        raise proposals.CapabilityDenied(
            'Work change permission required. Nothing was saved.'
        ) from exc
    except (ThreadRepositoryError, WorkFocusError, ValueError) as exc:
        raise proposals.ProposalNotFound(
            'Investigation source unavailable. Nothing was saved.'
        ) from exc
    comparable = {**live, 'request_version': snapshot['request_version']}
    sibling_count = 0
    if current and comparable == snapshot:
        from tasks.investigation_models import InvestigationArtifact

        sibling_count = InvestigationArtifact.objects.filter(
            request_id=intent['request_id'],
            source_turn_id=turn.pk,
            source_proposal_id__isnull=False,
        ).count()
    if current and (
        comparable != snapshot
        or live['request_version'] != snapshot['request_version'] + sibling_count
    ):
        raise proposals.ProposalRevalidationFailed(
            'Work or request revision changed. Nothing was saved; return to Diagnose for a fresh review.'
        )
    if not current and any(
        live.get(k) != snapshot.get(k) for k in ('machine_id', 'boundary_hash')
    ):
        raise proposals.ProposalNotFound('Investigation source unavailable.')
    return owner, turn


def selected_file(turn, file_id):
    """Read only an exact source-turn upload; never trust the stored absolute path."""
    if not file_id:
        return None, None
    uploads = (turn.modality_metadata or {}).get('uploaded_files', [])
    if (
        not isinstance(file_id, str)
        or not isinstance(uploads, list)
        or not any(
            isinstance(row, dict) and row.get('file_id') == file_id for row in uploads
        )
    ):
        raise proposals.ProposalNotFound('Chat file unavailable. Nothing was saved.')
    parts = file_id.split('/')
    allowed = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-'
    if (
        len(parts) != 2
        or parts[0] != turn.thread_id
        or any(p in ('', '.', '..') or any(c not in allowed for c in p) for p in parts)
    ):
        raise proposals.ProposalNotFound('Chat file unavailable. Nothing was saved.')
    root = Path(settings.MEDIA_ROOT).resolve() / 'ai_uploads'
    parent = root / turn.thread_id
    path = parent / parts[1]
    if (
        parent.is_symlink()
        or path.is_symlink()
        or path.resolve().parent != parent
        or not path.is_file()
    ):
        raise proposals.ProposalNotFound('Chat file unavailable. Nothing was saved.')
    if path.stat().st_size > MAX_FILE_BYTES:
        raise proposals.ProposalError(
            'File publication is not supported above 20 MB. Nothing was saved.'
        )
    with path.open('rb') as stream:
        contents = stream.read(MAX_FILE_BYTES + 1)
    if len(contents) > MAX_FILE_BYTES:
        raise proposals.ProposalError(
            'File publication is not supported above 20 MB. Nothing was saved.'
        )
    suffix = path.suffix.lower()
    supported = suffix in ('.png', '.jpg', '.jpeg', '.pdf')
    is_image = False
    if supported and suffix != '.pdf':
        try:
            from PIL import Image

            with Image.open(BytesIO(contents)) as image:
                actual_format = image.format
                image.verify()
            is_image = actual_format == ('PNG' if suffix == '.png' else 'JPEG')
            supported = is_image
        except (OSError, ValueError, Image.DecompressionBombError):
            supported = False
    elif suffix == '.pdf':
        supported = contents.startswith(b'%PDF-')
    return {
        'file_id': file_id,
        'filename': path.name,
        'size': len(contents),
        'sha256': hashlib.sha256(contents).hexdigest(),
        'is_image': is_image,
        'supported': supported,
    }, contents


def prepare(owner, work_order, intent, *, thread_id, source_turn_id):
    """Build a complete, version-bound review; save no evidence or media."""
    from tasks.investigation_models import InvestigationRequest
    from tasks.investigation_serializers import ArtifactFieldsSerializer

    owner, turn = source(
        owner,
        work_order_id=work_order.pk,
        intent=intent,
        thread_id=thread_id,
        source_turn_id=source_turn_id,
    )
    if set(intent) - {
        'request_id',
        'request_version',
        'kind',
        'payload',
        'file_id',
        'supersedes_id',
    }:
        raise proposals.ProposalError(
            'Unsupported publication fields. Nothing was saved.'
        )
    row = InvestigationRequest.objects.get(
        pk=intent['request_id'], work_order=work_order
    )
    if row.status == 'resolved':
        raise proposals.ProposalStateConflict(
            'Reopen the request before proposing evidence. Nothing was saved.'
        )
    file, _ = selected_file(turn, intent.get('file_id'))
    fields = {k: intent[k] for k in ('kind', 'payload', 'supersedes_id') if k in intent}
    validator = ArtifactFieldsSerializer(
        data=fields, context={'pending_chat_file': bool(file)}
    )
    try:
        validator.is_valid(raise_exception=True)
    except ValidationError as exc:
        raise proposals.ProposalError(
            f'Invalid typed artifact: {exc.detail}. Nothing was saved.'
        ) from exc
    supersedes = fields.get('supersedes_id')
    if supersedes and not row.artifacts.filter(pk=supersedes).exists():
        raise proposals.ProposalNotFound(
            'Evidence to correct unavailable. Nothing was saved.'
        )
    supported = file is None or (
        file['supported'] and (fields['kind'] != 'photo' or file['is_image'])
    )
    publication = (
        'Only this typed artifact will become shared Work evidence after you approve.'
        if file is None
        else 'This selected chat file and typed artifact will become visible to readers of this Work after you approve.'
        if supported
        else 'Publication is not supported for this file type or content. Nothing will be saved; choose a supported PNG, JPEG or PDF in a fresh turn.'
    )
    return {
        'action': ACTION,
        'work_order_id': work_order.pk,
        'reference': work_order.reference,
        'title': work_order.title,
        'request_id': row.pk,
        'request_version': intent['request_version'],
        'purpose': row.purpose,
        'required_artifacts': row.required_artifacts,
        'kind': fields['kind'],
        'payload': fields['payload'],
        'supersedes_id': supersedes,
        'source_turn_id': turn.pk,
        'file': file,
        'publication_supported': supported,
        'publication': publication,
        'warning': 'Records evidence only. The request stays unresolved until explicitly addressed.',
    }


def authorize(proposal, owner, *, current=True):
    """Use before details, execution and executed replay can disclose content."""
    from aichat.services.scope_strings import scope_strings

    try:
        current_scope = scope_strings(owner)
    except proposals.ProposalError as exc:
        raise proposals.ProposalNotFound(
            'Investigation scope unavailable. Nothing was saved.'
        ) from exc
    if (proposal.scope_key, proposal.scope_hash) != current_scope:
        raise proposals.ProposalNotFound(
            'Investigation scope unavailable. Nothing was saved.'
        )
    return source(
        owner,
        work_order_id=proposal.target_work_order_id,
        intent=proposal.intent,
        thread_id=proposal.thread_id,
        source_turn_id=proposal.source_turn_id,
        current=current,
    )


def execute(proposal, owner):
    """The existing executor calls one scoped idempotent request command."""
    from tasks.models import WorkOrder
    from tasks.services.investigation_requests import command

    from common.models import Attachment

    owner, turn = authorize(proposal, owner)
    work = WorkOrder.objects.select_for_update().get(pk=proposal.target_work_order_id)
    preview = prepare(
        owner,
        work,
        proposal.intent,
        thread_id=proposal.thread_id,
        source_turn_id=proposal.source_turn_id,
    )
    if proposals.compute_preview_hash(preview) != proposal.preview_hash:
        raise proposals.ProposalPreviewChanged(
            'The proposed content or file changed. Nothing was saved; request a fresh review.'
        )
    if not preview['publication_supported']:
        raise proposals.ProposalStateConflict(preview['publication'])
    fields = {
        k: proposal.intent[k]
        for k in ('kind', 'payload', 'supersedes_id')
        if k in proposal.intent
    }
    file, contents = selected_file(turn, proposal.intent.get('file_id'))
    if file != preview['file']:
        raise proposals.ProposalPreviewChanged('Chat file changed. Nothing was saved.')
    saved = None
    try:
        if file:
            description = fields['payload'].get('description')
            saved = Attachment(
                model_type='workorder',
                model_id=work.pk,
                upload_user=owner,
                is_image=file['is_image'],
                comment=(description if isinstance(description, str) else '')[
                    : Attachment._meta.get_field('comment').max_length
                ],
            )
            saved.attachment.save(file['filename'], ContentFile(contents), save=False)
            saved.save(rebuild=False)
            proposal._published_file = (saved.attachment.storage, saved.attachment.name)
            fields['attachment_id'] = saved.pk
        result = command(
            work_order_id=work.pk,
            actor=owner,
            expected_version=proposal.target_version,
            idempotency_key=f'proposal:{proposal.pk}',
            operation='artifact',
            fields=fields,
            request_id=proposal.intent['request_id'],
            request_version=preview_live_version(proposal),
            provenance={
                'source_turn_id': turn.pk,
                'source_proposal_id': str(proposal.pk),
                'source_file': file,
            },
        )
    except Exception:
        if saved and saved.attachment:
            saved.attachment.storage.delete(saved.attachment.name)
        raise
    artifact = result['artifact']
    return {
        'command': ACTION,
        'work_order_id': work.pk,
        'request_id': proposal.intent['request_id'],
        'request_version': result['request']['version'],
        'artifact_id': artifact['id'],
        'attachment_id': artifact['attachment'],
        'source_turn_id': turn.pk,
        'source_proposal_id': str(proposal.pk),
        'recorded_by': owner.pk,
        'recorded_by_name': owner.get_full_name() or owner.get_username(),
        'idempotency_key': f'proposal:{proposal.pk}',
    }


def verified(proposal):
    """Executed replay verifies the saved command and attributed artifact."""
    from tasks.investigation_models import InvestigationArtifact
    from tasks.models import WorkOrderCommand, WorkOrderEvent

    receipt = proposal.receipt or {}
    key = f'proposal:{proposal.pk}'
    ledger = WorkOrderCommand.objects.filter(
        work_order_id=proposal.target_work_order_id,
        idempotency_key=key,
        command='investigation',
        status='succeeded',
    ).first()
    return bool(
        ledger
        and receipt.get('command') == ACTION
        and receipt.get('request_id') == proposal.intent.get('request_id')
        and receipt.get('source_turn_id') == proposal.source_turn_id
        and receipt.get('source_proposal_id') == str(proposal.pk)
        and receipt.get('recorded_by') == proposal.owner_id
        and InvestigationArtifact.objects
        .filter(
            pk=receipt.get('artifact_id'),
            request_id=proposal.intent.get('request_id'),
            request__work_order_id=proposal.target_work_order_id,
            recorded_by_id=proposal.owner_id,
            source_turn_id=proposal.source_turn_id,
            source_proposal_id=proposal.pk,
            kind=proposal.intent.get('kind'),
            payload=proposal.intent.get('payload'),
        )
        .filter(
            Q(attachment_id=receipt.get('attachment_id'))
            | Q(attachment_snapshot__id=receipt.get('attachment_id'))
        )
        .exists()
        and WorkOrderEvent.objects.filter(
            pk=ledger.result_ref,
            actor_id=proposal.owner_id,
            work_order_id=proposal.target_work_order_id,
            idempotency_key=key,
            event_type='investigation_artifact',
        ).exists()
    )


def preview_live_version(proposal):
    """The Work lock serializes sibling approvals; source() checked their lineage."""
    from tasks.investigation_models import InvestigationRequest

    return InvestigationRequest.objects.get(pk=proposal.intent['request_id']).version


def visible_proposals(rows, owner):
    """Batch current read authorization without taking actor or thread write locks."""
    from tasks.services.investigation_requests import _change_actor

    from ai.core.auth import principal_for_user
    from aichat.services.scope_strings import scope_strings

    if not rows:
        return []
    try:
        current_scope = scope_strings(owner)
        owner = _change_actor(owner)
        repository = ThreadRepository(owner, principal_for_user(owner).scope)
    except (proposals.ProposalError, PermissionDenied, ValueError):
        return []
    turns = {
        turn.pk: turn
        for turn in ChatTurn.objects.filter(pk__in=[r.source_turn_id for r in rows])
    }
    threads = {}
    focuses = {}
    visible = []
    for row in rows:
        try:
            if (row.scope_key, row.scope_hash) != current_scope:
                continue
            if row.thread_id not in threads:
                try:
                    threads[row.thread_id] = repository._get_thread(row.thread_id)
                except ThreadRepositoryError:
                    threads[row.thread_id] = None
            turn = turns.get(row.source_turn_id)
            if (
                threads[row.thread_id] is None
                or turn is None
                or turn.thread_id != row.thread_id
            ):
                continue
            snapshot = (turn.trusted_context or {}).get(WORK_FOCUS_SNAPSHOT_KEY)
            intent = row.intent
            if (
                not isinstance(snapshot, dict)
                or snapshot.get('schema_version') != 2
                or any(
                    snapshot.get(key) != value
                    for key, value in (
                        ('work_order_id', row.target_work_order_id),
                        ('request_id', intent.get('request_id')),
                        ('request_version', intent.get('request_version')),
                    )
                )
            ):
                continue
            key = (row.target_work_order_id, intent['request_id'])
            if key not in focuses:
                try:
                    focuses[key] = authorized_focus(owner.pk, *key)
                except WorkFocusError:
                    focuses[key] = None
            live = focuses[key]
            if live is not None and all(
                live.get(k) == snapshot.get(k) for k in ('machine_id', 'boundary_hash')
            ):
                visible.append(row)
        except (KeyError, TypeError, ValueError):
            continue
    return visible
