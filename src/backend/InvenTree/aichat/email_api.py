"""Authenticated mailbox APIs; credentials and message bytes are never serialized."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction

from drf_spectacular.utils import extend_schema
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai.core.integrations.email.contracts import MailboxError
from aichat.models import ConnectedMailbox, MailboxGrant
from aichat.services.email.access import account_ids, require_account, require_enabled
from aichat.services.email.accounts import (
    capabilities,
    disconnect,
    public_account,
    save_account,
)
from aichat.services.email.drafts import create_draft
from approvals.serializers import ApprovalDetailSerializer


class MailboxView(APIView):
    """Shared safe errors for account-scoped services."""

    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication]

    def initial(self, request, *args, **kwargs):
        """Authenticate before revealing feature status."""
        super().initial(request, *args, **kwargs)
        require_enabled()

    def handle_exception(self, exc):
        """Return stable codes without provider error strings or credentials."""
        if isinstance(exc, MailboxError):
            return Response(
                {'error': exc.code},
                status=404
                if exc.code in ('permission_denied', 'feature_disabled')
                else 400,
            )
        if isinstance(exc, (ValidationError, ValueError, TypeError)):
            return Response({'error': 'invalid_request'}, status=400)
        return super().handle_exception(exc)


class MailboxList(MailboxView):
    """List readable mailboxes or create an administrator-owned connection."""

    @extend_schema(operation_id='agent_mailbox_list', responses={200: dict})
    def get(self, request):
        """Publish mailbox summaries without configuration secrets."""
        from aichat.services.email.oauth import microsoft_shared_configured

        management = request.query_params.get('manage') == 'true'
        permissions = {
            action: set(account_ids(request.user, action))
            for action in ('read', 'draft', 'send', 'admin')
        }
        return Response({
            'setup': {
                'microsoft_shared': microsoft_shared_configured(),
                'send_paused': settings.AGENT_EMAIL_SEND_PAUSED,
            },
            'results': [
                {
                    **public_account(a, admin=management),
                    'permissions': {
                        action: a.pk in ids for action, ids in permissions.items()
                    },
                }
                for a in ConnectedMailbox.objects.filter(
                    pk__in=permissions['admin' if management else 'read']
                )[:100]
            ],
        })

    @extend_schema(request=dict, responses={201: dict})
    def post(self, request):
        """Store encrypted credentials and grant the creator account authority."""
        return Response(
            public_account(save_account(request.user, request.data), admin=True),
            status=201,
        )


class MailboxDetail(MailboxView):
    """Read account health and administer configuration."""

    @extend_schema(responses={200: dict})
    def get(self, request, account_id):
        """Expose capabilities for the explicitly selected mailbox."""
        account = require_account(
            request.user,
            account_id,
            'admin' if request.query_params.get('manage') == 'true' else 'read',
        )
        result = public_account(
            account, admin=account.pk in account_ids(request.user, 'admin')
        )
        result['capabilities'] = capabilities(account)
        return Response(result)

    @extend_schema(request=dict, responses={200: dict})
    def patch(self, request, account_id):
        """Change configuration with binding invalidation."""
        return Response(
            public_account(
                save_account(request.user, request.data, account_id), admin=True
            )
        )

    @extend_schema(responses={200: dict})
    def delete(self, request, account_id):
        """Disconnect while preserving correspondence and execution evidence."""
        return Response(public_account(disconnect(request.user, account_id)))


class MailboxGrants(MailboxView):
    """Explicit account authority managed separately from global roles."""

    @extend_schema(responses={200: dict})
    def get(self, request, account_id):
        """List current grants for an account administrator."""
        account = require_account(request.user, account_id, 'admin')
        return Response({
            'results': list(
                account.grants.values(
                    'id',
                    'user_id',
                    'group_id',
                    'can_read',
                    'can_draft',
                    'can_send',
                    'can_admin',
                )
            )
        })

    @extend_schema(request=dict, responses={200: dict})
    @transaction.atomic
    def put(self, request, account_id):
        """Replace one grantee's capabilities; revocation applies at the next claim."""
        account = require_account(request.user, account_id, 'admin')
        ConnectedMailbox.objects.select_for_update().get(pk=account.pk)
        data = request.data
        if not isinstance(data, dict) or set(data) - {
            'user_id',
            'group_id',
            'can_read',
            'can_draft',
            'can_send',
            'can_admin',
        }:
            raise MailboxError('invalid_grant')
        if bool(data.get('user_id')) == bool(data.get('group_id')):
            raise MailboxError('invalid_grant')
        flags = {
            k: data.get(k, False)
            for k in ('can_read', 'can_draft', 'can_send', 'can_admin')
        }
        if any(not isinstance(value, bool) for value in flags.values()):
            raise MailboxError('invalid_grant')
        grant = MailboxGrant(
            account=account,
            user_id=data.get('user_id'),
            group_id=data.get('group_id'),
            **flags,
        )
        grant.full_clean(validate_unique=False, validate_constraints=False)
        MailboxGrant.objects.update_or_create(
            account=account,
            user_id=grant.user_id,
            group_id=grant.group_id,
            defaults=flags,
        )
        return Response({'saved': True})


class MailboxDrafts(MailboxView):
    """Create a draft for the existing approval review UI."""

    @extend_schema(request=dict, responses={201: ApprovalDetailSerializer})
    def post(self, request, account_id):
        """Freeze a message without submitting it to a provider."""
        data = dict(request.data)
        request_id = data.pop('request_id', None)
        approval = create_draft(request.user, account_id, data, request_id)
        return Response(ApprovalDetailSerializer(approval).data, status=201)


class MailboxMessages(MailboxView):
    """Bounded local correspondence search, always scoped before filtering."""

    @extend_schema(operation_id='agent_mailbox_message_list', responses={200: dict})
    def get(self, request, account_id):
        """List messages and explicit sync coverage for the selected mailbox."""
        from aichat.models import MailMessage
        from aichat.services.email.receive import message_data

        account = require_account(request.user, account_id)
        query = MailMessage.objects.filter(
            account=account, deleted=False, expired=False
        ).order_by('-received_at', '-pk')
        if request.query_params.get('conversation'):
            query = query.filter(conversation_id=request.query_params['conversation'])
        if request.query_params.get('search'):
            query = query.filter(
                subject__icontains=request.query_params['search'][:200]
            )
        offset = max(0, min(int(request.query_params.get('offset', 0)), 10000))
        rows = list(query[offset : offset + 26])
        return Response({
            'results': [message_data(m) for m in rows[:25]],
            'next_offset': offset + 25 if len(rows) > 25 else None,
            'sync': public_account(account)['sync'],
        })


class MailboxMessageDetail(MailboxView):
    """Read correspondence or update local processing state without marking mail read."""

    def message(self, actor, account_id, message_id):
        """Use account scope for every lookup including direct UUID requests."""
        from aichat.models import MailMessage

        account = require_account(actor, account_id)
        message = MailMessage.objects.filter(
            pk=message_id, account=account, expired=False, deleted=False
        ).first()
        if not message:
            raise MailboxError('permission_denied')
        return message

    @extend_schema(responses={200: dict})
    def get(self, request, account_id, message_id):
        """Return plain content marked as untrusted correspondence."""
        from aichat.services.email.receive import message_data

        return Response(
            message_data(self.message(request.user, account_id, message_id))
        )

    @extend_schema(request=dict, responses={200: dict})
    def patch(self, request, account_id, message_id):
        """Store local processing status with draft authority."""
        require_account(request.user, account_id, 'draft')
        message = self.message(request.user, account_id, message_id)
        if set(request.data) != {'processed'} or not isinstance(
            request.data['processed'], bool
        ):
            raise MailboxError('invalid_request')
        message.processed = request.data['processed']
        message.save(update_fields=['processed'])
        return Response({'processed': message.processed})


class MailboxAttachments(MailboxView):
    """Private attachment upload and authorized download."""

    @extend_schema(request=dict, responses={201: dict})
    def post(self, request, account_id):
        """Bound and scan uploads before allowing attachment selection."""
        from aichat.services.email.receive import MAX_BYTES, upload

        require_account(request.user, account_id, 'draft')
        file = request.FILES.get('file')
        if file is None or file.size > MAX_BYTES:
            raise MailboxError('attachment_too_large')
        artifact = upload(request.user, account_id, file.name, file.read(MAX_BYTES + 1))
        return Response(
            {
                'id': str(artifact.pk),
                'filename': artifact.filename,
                'scan_state': artifact.scan_state,
            },
            status=201,
        )


class MailboxAttachmentDownload(MailboxView):
    """Downloads use the same live account grants as message reads."""

    @extend_schema(responses={200: bytes})
    def get(self, request, account_id, attachment_id):
        """Serve opaque bytes as an attachment with active content disabled."""
        from django.http import HttpResponse
        from django.utils.http import content_disposition_header

        from aichat.models import MailAttachment

        account = require_account(request.user, account_id)
        artifact = (
            MailAttachment.objects
            .filter(pk=attachment_id, account=account, scan_state='clean')
            .select_related('message')
            .first()
        )
        if not artifact or (
            artifact.message and (artifact.message.expired or artifact.message.deleted)
        ):
            raise MailboxError('permission_denied')
        if (
            not artifact.message_id
            and artifact.uploaded_by_id != request.user.pk
            and not artifact.drafts.exists()
        ):
            raise MailboxError('permission_denied')
        response = HttpResponse(
            bytes(artifact.content), content_type='application/octet-stream'
        )
        response['Content-Disposition'] = content_disposition_header(
            True, artifact.filename
        )
        response['X-Content-Type-Options'] = 'nosniff'
        response['Cache-Control'] = 'private, no-store'
        response['Content-Security-Policy'] = "default-src 'none'; sandbox"
        return response


class MailboxSync(MailboxView):
    """Request a bounded read-only sync without blocking the web request."""

    @extend_schema(request=dict, responses={202: dict})
    def post(self, request, account_id):
        """Use the durable sync state and periodic sweeper to recover queue outages."""
        from django_q.tasks import async_task

        account = require_account(request.user, account_id, 'admin')
        if not account.enabled or not account.receive_enabled:
            raise MailboxError('receive_disabled')
        for collection in {
            account.options.get('inbox', 'Inbox'),
            account.options.get('sent', 'Sent'),
        }:
            async_task(
                'aichat.services.email.receive.sync_account',
                str(account.pk),
                collection,
            )
        return Response({'scheduled': True}, status=202)


class MailboxOAuthBegin(MailboxView):
    """Start a user-bound provider consent flow."""

    @extend_schema(request=dict, responses={200: dict})
    def post(self, request, account_id):
        """Return a provider authorization URL with expiring state and PKCE."""
        from aichat.services.email.oauth import begin

        return Response(begin(request.user, account_id))


class MailboxOAuthCallback(MailboxView):
    """Exchange the callback only for the administrator who initiated consent."""

    @extend_schema(request=dict, responses={200: dict})
    def post(self, request):
        """Consume a callback code once without echoing any credentials."""
        from aichat.services.email.oauth import callback

        return Response(
            callback(request.user, request.data.get('state'), request.data.get('code'))
        )
