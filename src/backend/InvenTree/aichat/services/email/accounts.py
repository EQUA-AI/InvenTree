"""Mailbox configuration and verified connection lifecycle."""

import re
from dataclasses import asdict

from django.conf import settings
from django.db import transaction

from ai.core.integrations.email.contracts import AccountConfig, MailboxError
from ai.core.integrations.email.factory import get_provider
from ai.core.integrations.email.policy import normalize_recipients
from aichat.models import ConnectedMailbox, MailboxGrant, MailSyncState

from .access import current_user, require_account, require_enabled
from .credentials import decrypt_credentials, encrypt_credentials

PROVIDERS = ('smtp_imap', 'graph', 'google', 'recording')
OPTION_KEYS = {
    'smtp_imap': {
        'smtp_host',
        'smtp_port',
        'smtp_tls',
        'imap_host',
        'imap_port',
        'inbox',
        'sent',
        'sent_copy',
        'auth',
    },
    'graph': {'tenant_id', 'client_id', 'inbox', 'sent', 'auth', 'oauth_application'},
    'google': {'client_id', 'inbox', 'sent'},
    'recording': set(),
}


def provider_for(account):
    """Resolve only this account's credentials into a fresh adapter."""
    from .oauth import access_token

    return get_provider(
        AccountConfig(
            str(account.pk),
            account.provider,
            account.address,
            account.options,
            decrypt_credentials(account.encrypted_credentials),
            (lambda: access_token(account.pk, account.binding_version))
            if account.provider in ('graph', 'google')
            else None,
        ),
        allow_recording=bool(getattr(settings, 'TESTING', False)),
    )


def public_account(account, *, admin=False):
    """Keep tokens, ciphertext, and credential metadata out of all responses."""
    data = {
        key: getattr(account, key)
        for key in (
            'name',
            'provider',
            'address',
            'aliases',
            'binding_version',
            'enabled',
            'send_enabled',
            'receive_enabled',
            'health',
            'signature',
            'retention_days',
            'backfill_days',
            'recovery_days',
        )
    }
    data['id'] = str(account.pk)
    data['verified_send'] = account.verified_send_at is not None
    data['verified_receive'] = account.verified_receive_at is not None
    if admin:
        data['options'] = account.options
        data['recipient_allowlist'] = account.recipient_allowlist
    data['sync'] = list(
        account.sync_states.values(
            'collection', 'status', 'has_gap', 'coverage_start', 'last_success', 'error'
        )
    )
    return data


def _validate(account):
    if account.provider not in PROVIDERS or (
        account.provider == 'recording' and not getattr(settings, 'TESTING', False)
    ):
        raise MailboxError('unsupported_provider')
    addresses = normalize_recipients(account.address)
    if len(addresses) != 1:
        raise MailboxError('invalid_sender')
    account.address = addresses[0]
    account.aliases = list(normalize_recipients(account.aliases))
    if (
        not isinstance(account.options, dict)
        or set(account.options) - OPTION_KEYS[account.provider]
    ):
        raise MailboxError('invalid_options')
    if len(str(account.options)) > 4096 or len(account.signature) > 10000:
        raise MailboxError('configuration_too_large')
    options = account.options
    if account.provider == 'graph' and 'oauth_application' in options:
        from .oauth import microsoft_shared_configured

        if (
            options['oauth_application'] != 'shared'
            or not microsoft_shared_configured()
            or options.get('auth', 'delegated') != 'delegated'
            or options.get('client_id', settings.AGENT_EMAIL_MICROSOFT_CLIENT_ID)
            != settings.AGENT_EMAIL_MICROSOFT_CLIENT_ID
            or options.get('tenant_id', 'common') != 'common'
        ):
            raise MailboxError('oauth_application_unavailable')
        options.update(
            client_id=settings.AGENT_EMAIL_MICROSOFT_CLIENT_ID,
            tenant_id='common',
            auth='delegated',
        )
    if any(
        not isinstance(value, (str, int)) or isinstance(value, bool)
        for value in options.values()
    ):
        raise MailboxError('invalid_options')
    for key in ('inbox', 'sent'):
        if key in options and (
            not isinstance(options[key], str)
            or not 1 <= len(options[key]) <= 255
            or any(c in options[key] for c in '\r\n\x00')
        ):
            raise MailboxError('invalid_collection')
    if account.provider == 'smtp_imap':
        for key in ('smtp_host', 'imap_host'):
            if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', options.get(key, '')):
                raise MailboxError('invalid_endpoint')
        for key in ('smtp_port', 'imap_port'):
            if key in options and (
                not isinstance(options[key], int) or not 1 <= options[key] <= 65535
            ):
                raise MailboxError('invalid_endpoint')
        if (
            options.get('smtp_tls', 'starttls') not in ('starttls', 'implicit')
            or options.get('auth', 'password') != 'password'
        ):
            raise MailboxError('unsupported_authentication')
        if options.get('sent_copy', 'none') not in ('none', 'append'):
            raise MailboxError('invalid_sent_policy')
    if account.provider == 'graph' and options.get('auth', 'delegated') not in (
        'delegated',
        'application',
    ):
        raise MailboxError('unsupported_authentication')
    if account.recipient_allowlist is not None:
        if (
            not isinstance(account.recipient_allowlist, list)
            or len(account.recipient_allowlist) > 100
        ):
            raise MailboxError('invalid_recipient_policy')
        for entry in account.recipient_allowlist:
            if not isinstance(entry, str):
                raise MailboxError('invalid_recipient_policy')
            normalize_recipients('policy' + entry if entry.startswith('@') else entry)
    for value in (account.retention_days, account.backfill_days, account.recovery_days):
        if not isinstance(value, int) or not 1 <= value <= 3650:
            raise MailboxError('invalid_retention')
    if account.backfill_days > account.recovery_days:
        raise MailboxError('invalid_recovery_window')


@transaction.atomic
def save_account(actor, data, account_id=None):
    """Only administrators configure connections; identity changes invalidate review."""
    require_enabled()
    user = current_user(actor)
    if account_id:
        require_account(user, account_id, 'admin')
        account = ConnectedMailbox.objects.select_for_update().get(pk=account_id)
    else:
        if not user.has_perm('aichat.add_connectedmailbox'):
            raise MailboxError('permission_denied')
        account = ConnectedMailbox(owner=user)
    allowed = {
        'name',
        'provider',
        'address',
        'aliases',
        'options',
        'credentials',
        'enabled',
        'send_enabled',
        'receive_enabled',
        'recipient_allowlist',
        'signature',
        'retention_days',
        'backfill_days',
        'recovery_days',
    }
    if not isinstance(data, dict) or set(data) - allowed:
        raise MailboxError('invalid_configuration')
    changed_identity = account_id and any(
        k in data and data[k] != getattr(account, k)
        for k in ('provider', 'address', 'aliases', 'options')
    )
    for key, value in data.items():
        if key != 'credentials':
            setattr(account, key, value)
    if 'credentials' in data:
        account.encrypted_credentials = encrypt_credentials(data['credentials'])
        changed_identity = bool(account_id)
    try:
        _validate(account)
    except (ValueError, IndexError, TypeError):
        raise MailboxError('invalid_configuration') from None
    if changed_identity:
        account.binding_version += 1
        account.verified_send_at = account.verified_receive_at = None
        account.send_enabled = False
        account.receive_enabled = False
        account.health = 'verification_required'
        MailSyncState.objects.filter(account=account).update(
            checkpoint=None, continuation=None, has_gap=True, status='resync_required'
        )
    if account.send_enabled and (
        not account.verified_send_at or not account.verified_receive_at
    ):
        raise MailboxError('verification_required')
    account.full_clean(
        exclude=['encrypted_credentials', 'aliases', 'options', 'recipient_allowlist']
    )
    account.save()
    if not account_id:
        MailboxGrant.objects.create(
            account=account,
            user=user,
            can_admin=True,
            can_read=True,
            can_draft=True,
            can_send=True,
        )
    return account


def capabilities(account):
    """Publish feature declarations without exposing credentials."""
    provider = get_provider(
        AccountConfig(
            str(account.pk), account.provider, account.address, account.options
        ),
        allow_recording=bool(getattr(settings, 'TESTING', False)),
    )
    result = asdict(provider.capabilities)
    result['features'] = sorted(result['features'])
    return result


@transaction.atomic
def disconnect(actor, account_id):
    """Stop future claims and erase credentials while preserving historical authority."""
    require_account(actor, account_id, 'admin')
    account = ConnectedMailbox.objects.select_for_update().get(pk=account_id)
    account.enabled = account.send_enabled = account.receive_enabled = False
    account.encrypted_credentials = ''
    account.binding_version += 1
    account.verified_send_at = account.verified_receive_at = None
    account.health = 'disconnected'
    account.save()
    return account
