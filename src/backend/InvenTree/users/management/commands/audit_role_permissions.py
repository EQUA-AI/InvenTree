"""Read-only desired-versus-actual managed permission diff."""

import json

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from users.tasks import group_permission_plan


class Command(BaseCommand):
    """No repair mode: unexplained deltas require administrator review."""

    help = 'Print role-derived permission deltas without writing rules or grants'

    def add_arguments(self, parser):
        """Optionally restrict the audit to exact group primary keys."""
        parser.add_argument('--group', type=int, action='append', dest='groups')

    def handle(self, *args, **options):
        """Compute pure diffs; group names and member identities are omitted."""
        groups = Group.objects.all().order_by('pk')
        if options['groups']:
            groups = groups.filter(pk__in=options['groups'])
            if groups.count() != len(set(options['groups'])):
                raise CommandError('An explicitly requested group does not exist')
        rows = []
        for group in groups:
            plan = group_permission_plan(group)
            rows.append({
                'group_id': group.pk,
                'add': plan.add,
                'remove': plan.remove,
                'missing': plan.missing,
            })
        self.stdout.write(
            json.dumps({'read_only': True, 'groups': rows}, sort_keys=True)
        )
