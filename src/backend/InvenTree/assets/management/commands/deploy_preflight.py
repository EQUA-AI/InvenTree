"""Post-migration deploy verifier and role-grant applier for aimms environments.

Audits the three risks left by the 2026-08 upstream sync:

1. Duplicate ``(part, serial)`` stock rows — these abort the partial unique
   constraint added by ``stock/0126`` (the audit here is a regression check;
   the *pre*-migration audit must run as raw SQL, see below).
2. Unapplied migrations (e.g. the ``oauth2_provider`` chain shipped by
   django-oauth-toolkit 3.4) — any pending migration crash-loops the deployed
   image via the INVE-W8 boot gate.
3. Role coverage after upstream #12529 started gating every ``*_detail``
   optional field on model view permission: the shared AI service token
   account needs view on all rulesets it reads, and technician groups holding
   only the fork ``work_order`` ruleset lose part/stock visibility.

Audit-only by default; writes happen only behind the explicit ``--grant-*``
flags and are idempotent (existing permissions are never downgraded).

IMPORTANT: this command cannot run on new code against an unmigrated
database — the INVE-W8 gate in ``InvenTree.apps`` exits before ``handle()``
is reached. Pre-migration checks belong in psql (see
LocalDocs/UpstreamSyncDeployRunbook.md).
"""

from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count

# Every ruleset the AI service token account must hold view on, derived from
# the endpoints InvenTreeClient hits plus the *_detail models the read tools
# consume. /attachment/ and /api/parameter/ are in the ruleset ignore list and
# need no grant.
SERVICE_ACCOUNT_REQUIRED_VIEWS = (
    'part',
    'part_category',
    'stock',
    'stock_location',
    'bom',
    'build',
    'purchase_order',
    'sales_order',
)

# Group that carries the service-account grants.
SERVICE_GROUP_NAME = 'ai-service-readers'

# View rulesets granted to technician groups so upstream #12529 does not blank
# their part/stock detail columns.
TECHNICIAN_VIEW_RULESETS = ('part', 'stock', 'stock_location')


def grant_ruleset(group, ruleset_name: str, **permissions) -> bool:
    """Idempotently ensure the given permission flags are set on a ruleset.

    Only ever escalates (False -> True); existing grants are never revoked.
    Returns True when a change was persisted.
    """
    from users.models import RuleSet

    ruleset, _created = RuleSet.objects.get_or_create(group=group, name=ruleset_name)

    changed = False
    for key, value in permissions.items():
        if value and not getattr(ruleset, key):
            setattr(ruleset, key, True)
            changed = True

    if changed:
        ruleset.save()

    return changed


class Command(BaseCommand):
    """Audit (and optionally repair) deploy readiness after the upstream sync."""

    help = (
        'Post-migration deploy audit: duplicate serials, pending migrations, '
        'and #12529 role coverage. Writes only behind --grant-* flags.'
    )

    def add_arguments(self, parser) -> None:
        """Register audit scoping and grant options."""
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )
        parser.add_argument(
            '--service-user', help='Username the AI service token authenticates as'
        )
        parser.add_argument(
            '--grant-service-roles',
            action='store_true',
            help=(
                f'Ensure the {SERVICE_GROUP_NAME!r} group holds view on '
                'all required rulesets and add --service-user to it'
            ),
        )
        parser.add_argument(
            '--grant-technician-views',
            nargs='*',
            metavar='GROUP',
            help=(
                'Grant part/stock/stock_location view to the named groups '
                '(no names: every group flagged by the audit)'
            ),
        )

    def handle(self, *args, **options) -> None:
        """Run the audits, apply any requested grants, and report."""
        report = {
            'duplicate_serials': self.audit_duplicate_serials(),
            'pending_migrations': self.audit_pending_migrations(),
            'service_account': self.audit_service_account(options.get('service_user')),
            'technician_groups': self.audit_technician_groups(),
            'grants_applied': [],
        }

        if options['grant_service_roles']:
            report['grants_applied'] += self.grant_service_roles(
                options.get('service_user')
            )

        if options['grant_technician_views'] is not None:
            report['grants_applied'] += self.grant_technician_views(
                options['grant_technician_views'], report['technician_groups']
            )

        # Grants change the audit facts - refresh the role sections
        if report['grants_applied']:
            report['service_account'] = self.audit_service_account(
                options.get('service_user')
            )
            report['technician_groups'] = self.audit_technician_groups()

        if options['json']:
            self.stdout.write(json.dumps(report, indent=2, default=str))
            return

        self.print_report(report)

    # --- audits -----------------------------------------------------------

    def audit_duplicate_serials(self) -> list[dict]:
        """Find (part, serial) pairs that violate stock/0126's constraint.

        Mirrors the partial-index condition exactly: rows with NULL or ''
        serial are out of scope.
        """
        from stock.models import StockItem

        serialized = StockItem.objects.exclude(serial=None).exclude(serial='')

        duplicates = (
            serialized
            .values('part', 'serial')
            .annotate(rows=Count('id'))
            .filter(rows__gt=1)
            .order_by('-rows')
        )

        findings = []
        for dup in duplicates:
            rows = serialized.filter(part=dup['part'], serial=dup['serial']).values(
                'pk', 'quantity', 'location', 'batch'
            )
            findings.append({
                'part': dup['part'],
                'serial': dup['serial'],
                'rows': list(rows),
            })

        return findings

    def audit_pending_migrations(self) -> list[str]:
        """List migrations not yet applied to this database."""
        from InvenTree.tasks import get_migration_plan

        return [
            f'{migration.app_label}.{migration.name}'
            for migration, _backwards in get_migration_plan()
        ]

    def audit_service_account(self, username: str | None) -> dict | None:
        """Diff the service account's effective roles against the contract."""
        if not username:
            return None

        from users.serializers import get_user_roles

        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            return {'username': username, 'error': 'user not found'}

        roles = get_user_roles(user)
        missing = [
            name
            for name in SERVICE_ACCOUNT_REQUIRED_VIEWS
            if not user.is_superuser and 'view' not in (roles.get(name) or [])
        ]

        return {
            'username': username,
            'is_active': user.is_active,
            'is_superuser': user.is_superuser,
            'missing_views': missing,
            'satisfied': user.is_superuser or not missing,
        }

    def audit_technician_groups(self) -> list[dict]:
        """Flag groups that hold work_order access but lack part/stock views.

        Post-#12529 these groups see 403s or blanked detail columns on the
        upstream Part/Stock tables. Expect a long list: users/0016 granted
        work_order to every group that existed at migration time.
        """
        findings = []

        for group in Group.objects.all().order_by('name'):
            rulesets = {rs.name: rs for rs in group.rule_sets.all()}

            work_order = rulesets.get('work_order')
            if work_order is None or not any([
                work_order.can_view,
                work_order.can_add,
                work_order.can_change,
                work_order.can_delete,
            ]):
                continue

            missing = [
                name
                for name in TECHNICIAN_VIEW_RULESETS
                if name not in rulesets or not rulesets[name].can_view
            ]

            if missing:
                findings.append({'group': group.name, 'missing_views': missing})

        return findings

    # --- grants -----------------------------------------------------------

    def grant_service_roles(self, username: str | None) -> list[str]:
        """Ensure the service group exists, is fully granted, and holds the user."""
        if not username:
            raise CommandError('--grant-service-roles requires --service-user')

        user = get_user_model().objects.filter(username=username).first()
        if user is None:
            raise CommandError(f'No user named {username!r}')

        applied = []
        group, created = Group.objects.get_or_create(name=SERVICE_GROUP_NAME)
        if created:
            applied.append(f'created group {SERVICE_GROUP_NAME}')

        for name in SERVICE_ACCOUNT_REQUIRED_VIEWS:
            if grant_ruleset(group, name, can_view=True):
                applied.append(f'{SERVICE_GROUP_NAME}: {name}.view')

        if not user.groups.filter(pk=group.pk).exists():
            user.groups.add(group)
            applied.append(f'added {username} to {SERVICE_GROUP_NAME}')

        return applied

    def grant_technician_views(
        self, names: list[str], flagged: list[dict]
    ) -> list[str]:
        """Grant part/stock views to the named (or all flagged) groups."""
        targets = names or [finding['group'] for finding in flagged]

        applied = []
        for name in targets:
            group = Group.objects.filter(name=name).first()
            if group is None:
                raise CommandError(f'No group named {name!r}')

            for ruleset_name in TECHNICIAN_VIEW_RULESETS:
                if grant_ruleset(group, ruleset_name, can_view=True):
                    applied.append(f'{name}: {ruleset_name}.view')

        return applied

    # --- output -----------------------------------------------------------

    def print_report(self, report: dict) -> None:
        """Human-readable summary of the audit and any grants."""
        dups = report['duplicate_serials']
        self.stdout.write(f'Duplicate (part, serial) pairs: {len(dups)}')
        for dup in dups:
            pks = ', '.join(str(row['pk']) for row in dup['rows'])
            self.stdout.write(
                f'  part={dup["part"]} serial={dup["serial"]!r} pks=[{pks}]'
            )

        pending = report['pending_migrations']
        self.stdout.write(f'Pending migrations: {len(pending)}')
        for name in pending:
            self.stdout.write(f'  {name}')

        service = report['service_account']
        if service is None:
            self.stdout.write('Service account: not checked (--service-user)')
        elif 'error' in service:
            self.stdout.write(
                f'Service account {service["username"]}: ERROR - {service["error"]}'
            )
        else:
            status = (
                'OK'
                if service['satisfied']
                else ('MISSING ' + ', '.join(service['missing_views']))
            )
            suffix = ' (superuser)' if service['is_superuser'] else ''
            self.stdout.write(
                f'Service account {service["username"]}: {status}{suffix}'
            )

        flagged = report['technician_groups']
        self.stdout.write(f'Technician groups missing views: {len(flagged)}')
        for finding in flagged:
            self.stdout.write(
                f'  {finding["group"]}: missing {", ".join(finding["missing_views"])}'
            )

        for grant in report['grants_applied']:
            self.stdout.write(f'GRANTED: {grant}')
