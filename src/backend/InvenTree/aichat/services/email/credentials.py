"""Encrypted mailbox secrets with explicit keys separate from the database."""

import json

from django.conf import settings

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from ai.core.integrations.email.contracts import MailboxError


def _cipher():
    keys = getattr(settings, 'AGENT_EMAIL_CREDENTIAL_KEYS', [])
    if not keys:
        raise MailboxError('credential_key_required')
    try:
        return MultiFernet([Fernet(key) for key in keys])
    except (ValueError, TypeError):
        raise MailboxError('credential_key_invalid') from None


def encrypt_credentials(credentials):
    """Encrypt using the first configured key; old keys support rotation."""
    if not isinstance(credentials, dict) or len(json.dumps(credentials)) > 32768:
        raise MailboxError('invalid_credentials')
    return _cipher().encrypt(json.dumps(credentials).encode()).decode()


def decrypt_credentials(ciphertext):
    """Never include ciphertext or secret values in errors."""
    if not ciphertext:
        return {}
    try:
        return json.loads(_cipher().decrypt(ciphertext.encode()))
    except (InvalidToken, ValueError, TypeError):
        raise MailboxError('credentials_unavailable') from None
