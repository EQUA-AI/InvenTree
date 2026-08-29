"""Report AssetMachine serial coverage — the scope-enforce flip gate.

Under an enforced explicit analysis scope, controlled-document search
narrows by machine SERIAL; a machine with a blank serial silently drops
out of manual retrieval for scoped threads (narrowing, never widening).
This read-only audit exists so the rollout runbook can literally gate
``FEATURE_AI_THREAD_SCOPE_ENFORCE=1`` on exit code 0:

    manage.py audit_scope_serials --client <code> && <flip the flag>
"""

import json

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Per-client serial coverage report (read-only)."""

    help = (
        'Report blank/duplicate AssetMachine serials per client (read-only). '
        'Exits 1 when any audited machine has a blank serial — the gate for '
        'enabling FEATURE_AI_THREAD_SCOPE_ENFORCE.'
    )

    def add_arguments(self, parser):
        """Register the filter and output options."""
        parser.add_argument(
            '--client', default='', help='Limit the audit to one client code'
        )
        parser.add_argument(
            '--json', action='store_true', help='Emit machine-readable JSON'
        )

    def handle(self, *args, **options):
        """Build and print the coverage report."""
        import sys
        from collections import Counter, defaultdict

        from assets.models import AssetMachine

        rows = AssetMachine.objects.select_related('client').all()
        if options['client']:
            rows = rows.filter(client__code=options['client'])

        report: dict = {'clients': {}, 'blank_total': 0, 'duplicate_total': 0}
        serials_by_client: dict[str, Counter] = defaultdict(Counter)
        for machine in rows.iterator():
            code = getattr(machine.client, 'code', None) or '(clientless)'
            entry = report['clients'].setdefault(
                code, {'total': 0, 'blank': 0, 'blank_machines': [], 'duplicates': []}
            )
            entry['total'] += 1
            serial = (machine.serial or '').strip()
            if not serial:
                entry['blank'] += 1
                report['blank_total'] += 1
                entry['blank_machines'].append({'pk': machine.pk, 'name': machine.name})
            else:
                serials_by_client[code][serial] += 1

        for code, counts in serials_by_client.items():
            duplicates = sorted(s for s, n in counts.items() if n > 1)
            report['clients'][code]['duplicates'] = duplicates
            report['duplicate_total'] += len(duplicates)

        if options['json']:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            for code, entry in sorted(report['clients'].items()):
                self.stdout.write(
                    f'{code}: total={entry["total"]} blank={entry["blank"]} '
                    f'duplicate_serials={len(entry["duplicates"])}'
                )
                for machine in entry['blank_machines']:
                    self.stdout.write(
                        f'  blank serial: #{machine["pk"]} {machine["name"]}'
                    )
                for serial in entry['duplicates']:
                    self.stdout.write(f'  duplicate serial: {serial}')

        if report['blank_total']:
            self.stderr.write(
                self.style.WARNING(
                    f'{report["blank_total"]} machine(s) without a serial — '
                    'scoped threads would lose manual search for them under '
                    'FEATURE_AI_THREAD_SCOPE_ENFORCE. Fix the data first.'
                )
            )
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS('serial coverage OK'))
