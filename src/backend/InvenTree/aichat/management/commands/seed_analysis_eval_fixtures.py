"""Seed the synthetic analysis-evaluation corpus (S14, §13.4).

Fixture set ``aimms-analysis-fixtures-v1``: two in-scope SI-3000 inverters
with >25 work orders each (deliberate date defects included), two
high-similarity distractor inverters, a water-plant forbidden entity, an
off-limits test bench, a superseded/current controlled-manual pair, an
exact-asset supplement, a fleet-wide bulletin, and one uncontrolled
attachment note. Everything derives from the ONE declared source,
``ai/core/evals/golden/fixtures/analysis/corpus.yaml`` — the fixture
battery asserts the same numbers, so router/retrieval passes without any
expected prose.

Idempotent by construction (``get_or_create`` everywhere; ``run_ingest``
short-circuits on sha). Controlled documents are seeded as REGISTRY rows
(states set to the declared lifecycle); the Azure Search CONTENT indexing
is the operator's real-ingestion runbook step — this command prints the
exact ``ingest_controlled_document`` invocations to run on the deployment.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

_FIXTURE_SET_VERSION = 'aimms-analysis-fixtures-v1'

#: Machines this seeder owns (fixture key -> identity). The HX-200 forbidden
#: entity is seeded by seed_attachment_eval_fixtures and only referenced.
_MACHINES = {
    'solar_a': ('Analysis Eval SI-3000 Inverter A', 'EVAL-SI3000-A', 'eval'),
    'solar_b': ('Analysis Eval SI-3000 Inverter B', 'EVAL-SI3000-B', 'eval'),
    'distractor_marine': (
        'Analysis Eval SI-3000M Marine Inverter',
        'EVAL-SI3000M',
        'eval',
    ),
    'distractor_string': ('Analysis Eval SI-300 String Inverter', 'EVAL-SI300', 'eval'),
    'water_asset': ('Analysis Eval WTP Influent Pump', 'EVAL-WTP-IP1', 'eval'),
    'test_bench': ('Analysis Eval Test Bench TB-1', 'EVAL-TB1', 'offlimits'),
}


def _fixtures_dir() -> Path:
    import ai.core.evals as evals_pkg

    return (
        Path(evals_pkg.__file__).resolve().parent / 'golden' / 'fixtures' / 'analysis'
    )


def _load_corpus() -> dict:
    import yaml

    with (_fixtures_dir() / 'corpus.yaml').open(encoding='utf-8') as handle:
        return yaml.safe_load(handle)


def _aware(day):
    return datetime.combine(day, dt_time(9, 0), tzinfo=timezone.utc)


class Command(BaseCommand):
    """Seed (idempotently) the declared synthetic analysis corpus."""

    help = (
        f'Seed the synthetic analysis eval corpus ({_FIXTURE_SET_VERSION}) '
        'from golden/fixtures/analysis/corpus.yaml.'
    )

    def add_arguments(self, parser):
        """Register scope, preview, and break-glass options."""
        parser.add_argument(
            '--scope-key',
            required=True,
            help='Controlled-document boundary key for the registry rows',
        )
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--break-glass', action='store_true')

    def handle(self, *args, **options):
        """Create the declared machines, work orders, documents, and note."""
        from aichat.services import eval_fixtures

        dry_run = options['dry_run']
        scope_key = options['scope_key']
        if not dry_run:
            eval_fixtures.refuse_production(break_glass=options['break_glass'])

        corpus = _load_corpus()
        if corpus.get('version') != _FIXTURE_SET_VERSION:
            raise CommandError(
                f'corpus.yaml declares {corpus.get("version")!r}; this command '
                f'seeds {_FIXTURE_SET_VERSION!r} — version them together'
            )
        missing = [
            doc['file']
            for doc in corpus.get('documents') or []
            if not (_fixtures_dir() / doc['file']).is_file()
        ]
        if missing:
            raise CommandError(f'Fixture documents missing: {", ".join(missing)}')

        if dry_run:
            self.stdout.write(f'DRY RUN — fixture set {_FIXTURE_SET_VERSION}')

        eval_client, offlimits = eval_fixtures.ensure_eval_clients(dry_run=dry_run)
        machines = self._seed_machines(eval_client, offlimits, dry_run)
        self._seed_work_orders(corpus, machines, dry_run)
        self._seed_documents(corpus, machines, scope_key, dry_run)
        self._seed_attachment(corpus, machines, dry_run)

        if not dry_run:
            created_pks = tuple(m.pk for m in machines.values() if m is not None)
            for line in eval_fixtures.restamp_fixture_scope(machine_pks=created_pks):
                self.stdout.write(line)
            self.stdout.write(
                self.style.SUCCESS(f'Fixture set {_FIXTURE_SET_VERSION} ensured')
            )

    # ------------------------------------------------------------------ #
    def _seed_machines(self, eval_client, offlimits, dry_run):
        from assets.models import AssetMachine

        machines = {}
        for key, (name, serial, owner) in _MACHINES.items():
            if dry_run:
                machines[key] = AssetMachine.objects.filter(name=name).first()
                state = 'present' if machines[key] else 'would create'
                self.stdout.write(f'machine {key}: {state}')
                continue
            client = offlimits if owner == 'offlimits' else eval_client
            machines[key], _ = AssetMachine.objects.get_or_create(
                name=name,
                defaults={
                    'client': client,
                    'serial': serial,
                    'manufacturer': 'Eval Fixtures',
                    'model': name.split()[-2] if key.startswith('solar') else 'EVAL',
                },
            )
        return machines

    def _seed_work_orders(self, corpus, machines, dry_run):
        from tasks.models import WorkOrder

        from assets.models import AssetMaintenanceRecord

        stages = corpus.get('maintenance_stages') or []
        for machine_key, plan in (corpus.get('work_orders') or {}).items():
            machine = machines.get(machine_key)
            total = int(plan['total'])
            if machine is None:
                self.stdout.write(
                    f'{machine_key}: machine absent (would seed {total} WOs)'
                )
                continue
            base = plan['base_date']
            step = int(plan.get('step_days', 10))
            missing_completion = int(plan.get('missing_completion', 0))
            conflicting = int(plan.get('conflicting_dates', 0))
            created_only = int(plan.get('created_only', 0))
            made = 0
            for index in range(total):
                reference = (
                    f'WO-EVAL-{machine.serial.replace("EVAL-", "")}-{index + 1:03d}'
                )
                if dry_run:
                    continue
                started = _aware(base + timedelta(days=index * step))
                completed = started + timedelta(days=2)
                # Deliberate defects, deterministic by index (§13.4):
                if index < created_only:
                    started = completed = None
                    status = WorkOrder.STATUS_BACKLOG
                elif index < created_only + missing_completion:
                    completed = None
                    status = WorkOrder.STATUS_IN_PROGRESS
                elif index < created_only + missing_completion + conflicting:
                    completed = started - timedelta(days=3)  # completed BEFORE started
                    status = WorkOrder.STATUS_DONE
                else:
                    status = WorkOrder.STATUS_DONE
                work_order, created = WorkOrder.objects.get_or_create(
                    reference=reference,
                    defaults={
                        'title': f'{machine.name} maintenance #{index + 1}',
                        'status': status,
                        'priority': WorkOrder.PRIORITY_MEDIUM,
                        'machine': machine,
                        'actual_started_at': started,
                        'actual_completed_at': completed,
                    },
                )
                if created:
                    made += 1
                if completed is not None and status == WorkOrder.STATUS_DONE and stages:
                    stage = stages[index % len(stages)]
                    AssetMaintenanceRecord.objects.get_or_create(
                        work_order=work_order,
                        defaults={
                            'machine': machine,
                            'date': completed.date(),
                            'summary': stage['symptom'],
                            'details': (
                                f'Symptom: {stage["symptom"]}. '
                                f'Action: {stage["action"]}. '
                                f'Outcome: {stage["outcome"]}.'
                            ),
                        },
                    )
            self.stdout.write(
                f'{machine_key}: {total} work orders declared'
                + ('' if dry_run else f', {made} created')
            )

        for key, spec in (corpus.get('special_work_orders') or {}).items():
            machine = machines.get(spec['machine_key'])
            if machine is None or dry_run:
                self.stdout.write(
                    f'{key}: {"declared" if dry_run else "machine absent"}'
                )
                continue
            from tasks.models import WorkOrder as SpecialWorkOrder

            completed = bool(spec.get('completed'))
            started = _aware(corpus['work_orders'][spec['machine_key']]['base_date'])
            work_order, _ = SpecialWorkOrder.objects.get_or_create(
                reference=spec['reference'],
                defaults={
                    'title': spec['title'],
                    'status': SpecialWorkOrder.STATUS_DONE
                    if completed
                    else SpecialWorkOrder.STATUS_IN_PROGRESS,
                    'priority': SpecialWorkOrder.PRIORITY_MEDIUM,
                    'machine': machine,
                    'actual_started_at': started,
                    'actual_completed_at': started + timedelta(days=1)
                    if completed
                    else None,
                },
            )
            if spec.get('maintenance_record'):
                from assets.models import AssetMaintenanceRecord

                AssetMaintenanceRecord.objects.get_or_create(
                    work_order=work_order,
                    defaults={
                        'machine': machine,
                        'date': started.date(),
                        'summary': spec['title'],
                        'details': 'Completed per the current service manual revision.',
                    },
                )
            self.stdout.write(f'{key}: {spec["reference"]} ensured')

    def _seed_documents(self, corpus, machines, scope_key, dry_run):
        from aichat.models import ControlledDocument, ControlledDocumentState

        scope_hash = hashlib.sha256(scope_key.encode('utf-8')).hexdigest()
        # INDEXED rows must name their index (aichat_ctrl_doc_indexed_source);
        # the CONTENT lands there via the operator's real-ingestion step.
        # An environment that indexes no governed corpus (aimms-dev) leaves
        # the setting EMPTY, and an empty name violates the same constraint
        # the comment above names — the seed aborted mid-transaction on dev
        # (2026-09-05). Fall back on empty as well as on failure.
        try:
            from ai.core.config import get_settings

            index_name = get_settings().azure_search_controlled_documents_index
        except Exception:
            index_name = ''
        index_name = index_name or 'aimms-controlled-documents'
        for doc in corpus.get('documents') or []:
            content = (_fixtures_dir() / doc['file']).read_bytes()
            sha = hashlib.sha256(content).hexdigest()
            asset_serial = ''
            if doc.get('asset_key'):
                machine = machines.get(doc['asset_key'])
                asset_serial = getattr(machine, 'serial', '') or ''
            current = bool(doc.get('current'))
            state = (
                ControlledDocumentState.INDEXED
                if current
                else ControlledDocumentState.SUPERSEDED
            )
            if dry_run:
                self.stdout.write(
                    f'document {doc["document_id"]} rev {doc["revision"]}: '
                    f'would ensure ({state})'
                )
                continue
            ControlledDocument.objects.get_or_create(
                scope_key=scope_key,
                document_id=doc['document_id'],
                revision=doc['revision'],
                defaults={
                    'title': doc['title'],
                    'document_class': doc['document_class'],
                    'scope_hash': scope_hash,
                    'access_class': 'internal',
                    'source_filename': doc['file'],
                    'source_location': f'evals/golden/fixtures/analysis/{doc["file"]}',
                    'source_sha256': sha,
                    'revision_date': doc['revision_date'],
                    'asset_id': asset_serial,
                    'state': state,
                    'is_current': current,
                    'search_index_name': index_name,
                    'indexed_at': datetime.now(timezone.utc) if current else None,
                },
            )
            # Registry now; CONTENT indexing is the operator's real-ingestion
            # step on the deployment (mounted controlled-source root + Azure
            # Search) — print the exact invocation.
            self.stdout.write(
                f'document {doc["document_id"]} rev {doc["revision"]} ensured '
                f'({state}); index content via: manage.py ingest_controlled_document '
                f'--source <root>/{doc["file"]} --document-id {doc["document_id"]} '
                f'--revision {doc["revision"]} --title "{doc["title"]}" '
                f'--document-class {doc["document_class"]} '
                f'--revision-date {doc["revision_date"]} --scope-key {scope_key} '
                f'--access-class internal'
                + (f' --asset-id {asset_serial}' if asset_serial else '')
            )

    def _seed_attachment(self, corpus, machines, dry_run):
        from django.conf import settings as django_settings

        from aichat.receivers import _any_ingest_effective

        if (
            not getattr(django_settings, 'AIMMS_ATTACHMENT_RAG_ENABLED', False)
            or not _any_ingest_effective()
        ):
            # R5: also skip on a degraded plane — the note would otherwise
            # seed with a PIPELINE_DISABLED registry row that fails the
            # fixture-index audit.
            self.stdout.write(
                'attachment note SKIPPED: AIMMS_ATTACHMENT_RAG_ENABLED is off '
                '(registry-only seeding; enable the flag to ingest the note)'
            )
            return
        from django.core.files.uploadedfile import SimpleUploadedFile

        from aichat.services.attachment_ingestion import run_ingest
        from common.models import Attachment

        for spec in corpus.get('attachments') or []:
            machine = machines.get(spec['machine_key'])
            if machine is None:
                self.stdout.write(f'{spec["file"]}: machine absent')
                continue
            if dry_run:
                self.stdout.write(f'{spec["file"]}: would attach + ingest')
                continue
            existing = Attachment.objects.filter(
                model_type='assetmachine',
                model_id=machine.pk,
                attachment__endswith=spec['file'],
            ).first()
            attachment = existing
            if attachment is None:
                content = (_fixtures_dir() / spec['file']).read_bytes()
                attachment = Attachment.objects.create(
                    model_type='assetmachine',
                    model_id=machine.pk,
                    attachment=SimpleUploadedFile(spec['file'], content),
                    comment=f'Analysis eval fixture ({_FIXTURE_SET_VERSION})',
                )
            row = run_ingest(attachment.pk)
            state = getattr(row, 'state', 'skipped-by-receiver')
            self.stdout.write(
                f'{spec["file"]}: attachment={attachment.pk} state={state}'
            )
