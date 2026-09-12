"""Account-bound OAuth state, PKCE, encrypted rotation and bounded token refresh."""

import base64
import hashlib
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ai.core.integrations.email.contracts import MailboxError
from aichat.models import ConnectedMailbox, MailboxOAuthAttempt

from .access import require_account, require_enabled
from .credentials import decrypt_credentials, encrypt_credentials
from .drafts import digest


def configuration(account):
    """Only provider-owned token endpoints can receive credentials."""
    if account.provider == 'google':
        return (
            'https://accounts.google.com/o/oauth2/v2/auth',
            'https://oauth2.googleapis.com/token',
            'https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send',
        )
    if account.provider == 'graph':
        tenant = account.options.get('tenant_id', '')
        if not re.fullmatch(r'[a-zA-Z0-9.-]{1,253}', tenant):
            raise MailboxError('invalid_tenant')
        root = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/'
        return (
            root + 'authorize',
            root + 'token',
            'offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.Send',
        )
    raise MailboxError('oauth_unsupported')


def exchange(account, fields):
    """Do not retry authorization-code or rotating-refresh-token exchanges."""
    import httpx

    _, url, _ = configuration(account)
    try:
        with httpx.Client(
            timeout=20, follow_redirects=False, trust_env=False
        ) as client:
            with client.stream('POST', url, data=fields) as response:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise MailboxError('oauth_failed')
                if response.status_code != 200:
                    raise MailboxError('reauthorization_required')
                import json

                result = json.loads(raw)
                if (
                    not isinstance(result.get('access_token'), str)
                    or result.get('token_type', '').lower() != 'bearer'
                ):
                    raise MailboxError('oauth_failed')
                return {
                    key: result[key]
                    for key in ('access_token', 'refresh_token', 'expires_in')
                    if key in result
                }
    except MailboxError:
        raise
    except Exception:
        raise MailboxError('oauth_failed') from None


@transaction.atomic
def begin(actor, account_id):
    """Issue state and PKCE bound to this user, mailbox and binding version."""
    require_enabled()
    account = require_account(actor, account_id, 'admin')
    redirect = settings.AGENT_EMAIL_OAUTH_REDIRECT_URI
    if urlsplit(redirect).scheme != 'https':
        raise MailboxError('oauth_redirect_required')
    auth_url, _, scope = configuration(account)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    MailboxOAuthAttempt.objects.create(
        state_hash=digest(state),
        account=account,
        actor=actor,
        binding_version=account.binding_version,
        encrypted_verifier=encrypt_credentials({
            'verifier': verifier,
            'redirect_uri': redirect,
        }),
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    fields = {
        'client_id': account.options.get('client_id', ''),
        'redirect_uri': redirect,
        'response_type': 'code',
        'scope': scope,
        'state': state,
        'code_challenge': base64
        .urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b'=')
        .decode(),
        'code_challenge_method': 'S256',
    }
    if account.provider == 'google':
        fields.update(access_type='offline', prompt='consent')
    return {'authorization_url': auth_url + '?' + urlencode(fields)}


def callback(actor, state, code):
    """Consume state once before exchanging a code, then bind the new credentials."""
    require_enabled()
    if not isinstance(state, str) or not isinstance(code, str) or len(code) > 8192:
        raise MailboxError('invalid_oauth_callback')
    with transaction.atomic():
        attempt = (
            MailboxOAuthAttempt.objects
            .select_for_update()
            .filter(
                state_hash=digest(state),
                actor=actor,
                used=False,
                expires_at__gt=timezone.now(),
            )
            .first()
        )
        if not attempt:
            raise MailboxError('invalid_oauth_state')
        account = require_account(actor, attempt.account_id, 'admin')
        if account.binding_version != attempt.binding_version:
            raise MailboxError('binding_changed')
        attempt.used = True
        attempt.save(update_fields=['used'])
        verifier = decrypt_credentials(attempt.encrypted_verifier)
        credentials = decrypt_credentials(account.encrypted_credentials)
    fields = {
        'grant_type': 'authorization_code',
        'code': code,
        'client_id': account.options.get('client_id', ''),
        'redirect_uri': verifier['redirect_uri'],
        'code_verifier': verifier['verifier'],
    }
    if credentials.get('client_secret'):
        fields['client_secret'] = credentials['client_secret']
    tokens = exchange(account, fields)
    tokens['expires_at'] = time.time() + int(tokens.pop('expires_in', 3600))
    with transaction.atomic():
        current = ConnectedMailbox.objects.select_for_update().get(pk=account.pk)
        require_account(actor, current.pk, 'admin')
        if current.binding_version != attempt.binding_version:
            raise MailboxError('binding_changed')
        current.encrypted_credentials = encrypt_credentials({**credentials, **tokens})
        current.binding_version += 1
        current.verified_send_at = current.verified_receive_at = None
        current.send_enabled = current.receive_enabled = False
        current.health = 'verification_required'
        current.save()
    return {'connected': True, 'verification_required': True}


def access_token(account_id, version):
    """Refresh outside the DB transaction, fenced against disconnect and rotation."""
    require_enabled()
    with transaction.atomic():
        account = ConnectedMailbox.objects.select_for_update().get(pk=account_id)
        if account.binding_version != version or not account.enabled:
            raise MailboxError('reauthorization_required')
        credentials = decrypt_credentials(account.encrypted_credentials)
        if (
            credentials.get('access_token')
            and credentials.get('expires_at', 0) > time.time() + 60
        ):
            return credentials['access_token']
        if account.oauth_refresh_until and account.oauth_refresh_until > timezone.now():
            raise MailboxError('refresh_in_progress')
        if account.provider == 'graph' and account.options.get('auth') == 'application':
            fields = {
                'grant_type': 'client_credentials',
                'scope': 'https://graph.microsoft.com/.default',
            }
        elif credentials.get('refresh_token'):
            fields = {
                'grant_type': 'refresh_token',
                'refresh_token': credentials['refresh_token'],
            }
        else:
            raise MailboxError('reauthorization_required')
        fields['client_id'] = account.options.get('client_id', '')
        if credentials.get('client_secret'):
            fields['client_secret'] = credentials['client_secret']
        lease = timezone.now() + timedelta(seconds=60)
        account.oauth_refresh_until = lease
        account.save(update_fields=['oauth_refresh_until'])
    try:
        tokens = exchange(account, fields)
        tokens['expires_at'] = time.time() + int(tokens.pop('expires_in', 3600))
        ciphertext = encrypt_credentials({**credentials, **tokens})
        updated = ConnectedMailbox.objects.filter(
            pk=account_id,
            binding_version=version,
            oauth_refresh_until=lease,
            enabled=True,
        ).update(encrypted_credentials=ciphertext, oauth_refresh_until=None)
        if not updated:
            raise MailboxError('binding_changed')
        return tokens['access_token']
    except Exception:
        ConnectedMailbox.objects.filter(
            pk=account_id, binding_version=version, oauth_refresh_until=lease
        ).update(oauth_refresh_until=None, health='reauthorization_required')
        raise
