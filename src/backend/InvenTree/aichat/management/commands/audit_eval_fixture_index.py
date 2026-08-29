"""Zero-stale-copy audit for the S6 eval-fixture ownership move (WP-A5)."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Verify no eval-fixture document still carries ``internal`` scope.

    THE merge/rollout gate for the S6 data operation — the seeders' own
    success output is not it (their restamp calls are best-effort and the
    async receivers are flag-gated). Walks every ``AttachmentIngest`` row
    whose owner is an ``eval-fixtures``-client machine (or a work order /
    part attached to one) and fails — nonzero exit — if any registry row's
    ``client_codes`` still names ``internal`` or misses ``eval-fixtures``.

    The registry row mirrors what ingest last WROTE to the search index; a
    stale index copy whose registry row is clean can only mean a failed
    merge, which the restamp services raise on. Re-run the relevant seeder
    (or ``restamp_machine_client_codes``) to repair, then audit again.
    """

    help = 'Fail (exit 1) if any eval-fixture document still carries internal scope.'

    def add_arguments(self, parser):
        """Register the S15 semi-automatic latch trigger."""
        parser.add_argument(
            '--latch-on-failure',
            action='store_true',
            help=(
                'Q50(a), opt-in: a stale eval-fixture stamp during an '
                'evaluation window is a potential cross-principal leak — '
                'engage the pilot-stop latch in addition to exiting 1. '
                'Schedule with this flag during eval windows only.'
            ),
        )

    def handle(self, *args, **options):
        """Walk eval-owned ingest rows and report stale scope stamps."""
        from tasks.models import WorkOrder

        from aichat.models import AttachmentIngest
        from aichat.services.eval_fixtures import EVAL_FIXTURES_CODE
        from assets.models import AssetMachine, MachinePart

        eval_machines = list(
            AssetMachine.objects.filter(client__code=EVAL_FIXTURES_CODE)
        )
        if not eval_machines:
            self.stdout.write('No eval-fixtures machines exist; nothing to audit.')
            return
        machine_pks = {machine.pk for machine in eval_machines}
        work_order_pks = set(
            WorkOrder.objects.filter(machine_id__in=machine_pks).values_list(
                'pk', flat=True
            )
        )
        part_pks = set(
            MachinePart.objects.filter(machine_id__in=machine_pks).values_list(
                'part_id', flat=True
            )
        )

        from django.db.models import Q

        rows = AttachmentIngest.objects.filter(
            Q(model_type='assetmachine', model_id__in=machine_pks)
            | Q(
                model_type__in=('workorder', 'workorderstepexecution'),
                model_id__in=work_order_pks,
            )
            | Q(model_type='part', model_id__in=part_pks)
        )

        stale = []
        audited = 0
        for row in rows:
            audited += 1
            codes = set(row.client_codes or [])
            if 'internal' in codes or EVAL_FIXTURES_CODE not in codes:
                stale.append(
                    f'ingest {row.pk} ({row.model_type}={row.model_id}, '
                    f'state={row.state}): client_codes={sorted(codes)}'
                )

        self.stdout.write(
            f'Audited {audited} eval-fixture ingest rows across '
            f'{len(machine_pks)} machines, {len(work_order_pks)} work orders, '
            f'{len(part_pks)} parts.'
        )
        if stale:
            for line in stale:
                self.stdout.write(self.style.ERROR(f'STALE: {line}'))
            if options.get('latch_on_failure'):
                from aichat.services.pilot_latch import engage_latch

                engage_latch(
                    reason_code='eval_fixture_leak',
                    source='automatic',
                    detail=f'{len(stale)} stale eval-fixture stamp(s)',
                )
                self.stdout.write(self.style.ERROR('Pilot-stop latch ENGAGED.'))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('Zero stale copies.'))
