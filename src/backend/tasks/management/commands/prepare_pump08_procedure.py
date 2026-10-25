"""Prepare a governed draft only; never publish, approve or execute it."""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError

from assets.models import AssetMachine
from tasks.scope import ScopeError
from tasks.services.pump08_procedure import prepare_procedure
from tasks.services.work_orders import WorkOrderCommandError


class Command(BaseCommand):
    """Resolve actual configured identities; never infer review actors or statuses."""

    help = 'Author the Pump08 exercise procedure draft. Independent native review/publication remains required.'

    def add_arguments(self, parser):
        """Require the caller to name the exact machine and authorized author."""
        parser.add_argument('--machine', type=int, required=True)
        parser.add_argument('--author', required=True)

    def handle(self, *args, **options):
        """Create or return a draft and emit actual generated identities."""
        if not getattr(settings, 'AIMMS_PROCEDURES_ENABLED', False):
            raise CommandError('Governed procedures are disabled in this deployment.')
        author = (
            get_user_model()
            .objects.filter(username=options['author'], is_active=True)
            .first()
        )
        machine = (
            AssetMachine.objects
            .select_related('client', 'parent')
            .filter(pk=options['machine'])
            .first()
        )
        if author is None or machine is None:
            raise CommandError('An active author and actual machine are required.')
        try:
            procedure, revision, created = prepare_procedure(machine, author)
        except (PermissionDenied, ScopeError, WorkOrderCommandError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            json.dumps({
                'procedure_id': procedure.pk,
                'revision_id': revision.pk if revision else None,
                'content_version': revision.content_version if revision else None,
                'status': revision.status if revision else None,
                'created': created,
                'published': False
                if revision is None
                else revision.status == 'published',
                'next_action': 'Independent permitted reviewer requests review, then uses the normal publication approval. No check or operating authority was created.',
            })
        )
