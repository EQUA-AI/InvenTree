"""Re-encrypt mailbox secrets with the first configured key, without disclosure."""

from django.core.management.base import BaseCommand
from django.db import transaction

from aichat.models import ConnectedMailbox, MailboxOAuthAttempt
from aichat.services.email.credentials import decrypt_credentials, encrypt_credentials


class Command(BaseCommand):
    """Keep old keys configured until every stored credential can use the new key."""

    help = 'Rotate encrypted mailbox credentials using the configured key ring.'

    @transaction.atomic
    def handle(self, *args, **options):
        """Fail the entire rotation if any record cannot be decrypted."""
        count = 0
        for account in (
            ConnectedMailbox.objects
            .select_for_update()
            .exclude(encrypted_credentials='')
            .iterator()
        ):
            account.encrypted_credentials = encrypt_credentials(
                decrypt_credentials(account.encrypted_credentials)
            )
            account.save(update_fields=['encrypted_credentials'])
            count += 1
        for attempt in (
            MailboxOAuthAttempt.objects
            .select_for_update()
            .filter(used=False)
            .iterator()
        ):
            attempt.encrypted_verifier = encrypt_credentials(
                decrypt_credentials(attempt.encrypted_verifier)
            )
            attempt.save(update_fields=['encrypted_verifier'])
        self.stdout.write(f'Re-encrypted {count} mailbox credential records.')
