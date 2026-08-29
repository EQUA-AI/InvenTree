"""Grant or revoke one user's explicit client maintenance scope (S6)."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from assets.models import Client, ClientScopeGrant


class Command(BaseCommand):
    """Provision ``ClientScopeGrant`` rows auditably from the shell.

    The S6 use: grant the dedicated solar-evaluation user BOTH ``internal``
    and ``eval-fixtures`` so the golden set stays whole, while every
    ungranted user resolves through the single-site fallback and never sees
    ``eval-fixtures``. Effective only when
    ``AIMMS_MAINTENANCE_SCOPE_RESOLVER`` names
    ``tasks.scope.granted_client_scope_resolver``.
    """

    help = 'Grant (or --revoke) a client maintenance scope to one user.'

    def add_arguments(self, parser):
        """Username, client code, and the revoke switch."""
        parser.add_argument('username')
        parser.add_argument('client_code')
        parser.add_argument(
            '--revoke', action='store_true', help='Remove the grant instead'
        )

    def handle(self, *args, **options):
        """Create or delete the (user, client) grant row."""
        user = get_user_model().objects.filter(username=options['username']).first()
        if user is None:
            raise CommandError(f'Unknown user: {options["username"]}')
        client = Client.objects.filter(code=options['client_code']).first()
        if client is None:
            raise CommandError(f'Unknown client code: {options["client_code"]}')

        if options['revoke']:
            deleted, _ = ClientScopeGrant.objects.filter(
                user=user, client=client
            ).delete()
            verb = 'revoked' if deleted else 'was not granted'
            self.stdout.write(f'{user.username} {verb} {client.code}')
            return
        _grant, created = ClientScopeGrant.objects.get_or_create(
            user=user, client=client
        )
        verb = 'granted' if created else 'already holds'
        self.stdout.write(self.style.SUCCESS(f'{user.username} {verb} {client.code}'))
