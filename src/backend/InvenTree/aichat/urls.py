"""Authenticated AI chat API routes (mounted at /api/aichat/)."""

from django.urls import path

from aichat.api import (
    MessageFeedbackView,
    ProposalConfirmView,
    ProposalDetailView,
    ProposalListCreateView,
    ProposalRejectView,
)
from aichat.email_api import (
    MailboxAttachmentDownload,
    MailboxAttachments,
    MailboxDetail,
    MailboxDrafts,
    MailboxGrants,
    MailboxList,
    MailboxMessageDetail,
    MailboxMessages,
    MailboxOAuthBegin,
    MailboxOAuthCallback,
    MailboxSync,
)
from aichat.media_stream import EvidenceMediaStreamView

app_name = 'aichat'

urlpatterns = [
    path(
        'email/oauth/callback/',
        MailboxOAuthCallback.as_view(),
        name='mailbox-oauth-callback',
    ),
    path(
        'email/accounts/<uuid:account_id>/oauth/',
        MailboxOAuthBegin.as_view(),
        name='mailbox-oauth-begin',
    ),
    path(
        'email/accounts/<uuid:account_id>/sync/',
        MailboxSync.as_view(),
        name='mailbox-sync',
    ),
    path(
        'email/accounts/<uuid:account_id>/messages/',
        MailboxMessages.as_view(),
        name='mailbox-messages',
    ),
    path(
        'email/accounts/<uuid:account_id>/messages/<uuid:message_id>/',
        MailboxMessageDetail.as_view(),
        name='mailbox-message-detail',
    ),
    path(
        'email/accounts/<uuid:account_id>/attachments/',
        MailboxAttachments.as_view(),
        name='mailbox-attachments',
    ),
    path(
        'email/accounts/<uuid:account_id>/attachments/<uuid:attachment_id>/',
        MailboxAttachmentDownload.as_view(),
        name='mailbox-attachment-download',
    ),
    path('email/accounts/', MailboxList.as_view(), name='mailbox-list'),
    path(
        'email/accounts/<uuid:account_id>/',
        MailboxDetail.as_view(),
        name='mailbox-detail',
    ),
    path(
        'email/accounts/<uuid:account_id>/grants/',
        MailboxGrants.as_view(),
        name='mailbox-grants',
    ),
    path(
        'email/accounts/<uuid:account_id>/drafts/',
        MailboxDrafts.as_view(),
        name='mailbox-drafts',
    ),
    path('proposals/', ProposalListCreateView.as_view(), name='proposal-list'),
    path(
        'proposals/<uuid:proposal_id>/',
        ProposalDetailView.as_view(),
        name='proposal-detail',
    ),
    path(
        'proposals/<uuid:proposal_id>/confirm/',
        ProposalConfirmView.as_view(),
        name='proposal-confirm',
    ),
    path(
        'proposals/<uuid:proposal_id>/reject/',
        ProposalRejectView.as_view(),
        name='proposal-reject',
    ),
    path('feedback/', MessageFeedbackView.as_view(), name='message-feedback'),
    path(
        'evidence/media/<int:attachment_id>/',
        EvidenceMediaStreamView.as_view(),
        name='evidence-media-stream',
    ),
]
