"""Prepare isolated, reversible R8 catalog/requirement fixtures; never buy or receive."""

import json

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from company.models import Company, SupplierPart, SupplierPriceBreak
from part.models import Part
from tasks.models import JobKit, JobKitLine, WorkOrder
from users.models import RuleSet, UserProfile

NAMESPACE = 'r8-procurement-cedar-creek-v1'
PREFIX = 'R8 FIXTURE'
MARKER = {
    'fixture': True,
    'namespace': NAMESPACE,
    'scenario': 'WO-003826',
    'machine': 'Cedar Creek Pump 08',
    'email_policy': 'recorded_only',
}
REFERENCE = 'R8-FIXTURE-003826'
GROUP = f'{PREFIX} named PO reviewers'


def require_local_database(confirm_db):
    """Refuse shared/owner databases even if the caller knows their name."""
    db = connection.settings_dict
    name, host = str(db['NAME']), str(db.get('HOST', ''))
    allowed = name in {'r8_demo', 'r8_integration', 'test_r8_integration'}
    # This path exists only inside backend_baseline.py's private test container.
    offline = (
        settings.TESTING
        and connection.vendor == 'sqlite'
        and name == '/evidence/test.sqlite3'
    )
    if (
        not settings.DEBUG
        or confirm_db != name
        or not (
            offline
            or (
                connection.vendor == 'postgresql'
                and allowed
                and host in {'localhost', '127.0.0.1', '::1', 'r8-pg'}
            )
        )
    ):
        raise CommandError(
            'Only an explicitly confirmed local R8 demo/test database is allowed.'
        )


class Command(BaseCommand):
    """Create an empty fixture kit beside, never on top of, the showcase."""

    help = (
        'Seed representative R8 suppliers/offers and an isolated Cedar Creek fixture Work. '
        'No PO, email, stock, allocation, source/fit assertion or Closeout is created.'
    )

    def add_arguments(self, parser):
        """Expose explicit target and reversible operations."""
        parser.add_argument('--confirm-db', required=True)
        parser.add_argument(
            '--approver', required=True, help='Existing active named username.'
        )
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--remove', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        """Validate provenance and ownership before making any write."""
        require_local_database(options['confirm_db'])
        actor = (
            get_user_model()
            .objects.select_for_update()
            .filter(username=options['approver'], is_active=True)
            .first()
        )
        if actor is None or not actor.get_full_name().strip():
            raise CommandError(
                'An existing active person with a full name is required.'
            )
        if not UserProfile.objects.filter(user=actor).exists():
            raise CommandError(
                'The named person needs an existing native user profile before fixture preparation.'
            )
        target = (
            WorkOrder.objects
            .select_related('machine')
            .filter(reference='WO-003826')
            .first()
        )
        if (
            target is None
            or not target.machine
            or target.machine.name != MARKER['machine']
        ):
            raise CommandError(
                'Expected Cedar Creek Pump 08 / WO-003826; no target is guessed.'
            )
        supplier = (
            Company.objects
            .select_for_update()
            .filter(name=f'{PREFIX} supplier A')
            .first()
        )
        if supplier and supplier.metadata.get('r8_fixture') != MARKER:
            raise CommandError(
                'Unmarked supplier collision; no existing record is adopted.'
            )
        manifest = supplier.metadata.get('manifest') if supplier else None
        if manifest and (
            manifest['approver_id'] != actor.pk or manifest['target_id'] != target.pk
        ):
            raise CommandError(
                'Fixture actor/target differs from the retained manifest.'
            )
        if options['remove']:
            result = self.remove(supplier, manifest, actor, options['dry_run'])
        elif manifest:
            self.validate(manifest)
            result = {'state': 'existing', **manifest}
        else:
            if (
                supplier
                or WorkOrder.objects.filter(reference=REFERENCE).exists()
                or Group.objects.filter(name=GROUP).exists()
            ):
                raise CommandError(
                    'Incomplete/colliding fixture namespace; refusing to overwrite.'
                )
            if (
                Part.objects.filter(IPN='R8-FIXTURE-SEAL').exists()
                or Company.objects.filter(name=f'{PREFIX} supplier B').exists()
            ):
                raise CommandError('Catalog fixture collision; refusing to overwrite.')
            if options['dry_run']:
                result = {
                    'state': 'would_create',
                    'provenance': MARKER,
                    'target_id': target.pk,
                    'approver_id': actor.pk,
                    'fixture_reference': REFERENCE,
                }
            else:
                result = self.seed(target, actor)
        self.stdout.write(json.dumps(result, sort_keys=True))

    def seed(self, target, actor):
        """Use only new, explicitly marked identities and native role rows."""
        group = Group.objects.create(name=GROUP)
        permissions = Permission.objects.filter(
            content_type__app_label='approvals', codename='review'
        )
        if permissions.count() != 1:
            raise CommandError('Native approvals.review permission is missing.')
        for name in ('purchase_order',):
            RuleSet.objects.update_or_create(
                group=group,
                name=name,
                defaults={'can_view': True, 'can_add': True, 'can_change': True},
            )
        RuleSet.objects.update_or_create(
            group=group, name='work_order', defaults={'can_review_approvals': True}
        )
        actor.groups.add(group)
        part = Part.objects.create(
            name=f'{PREFIX} seal/service kit',
            IPN='R8-FIXTURE-SEAL',
            description='Representative only; technical fit unverified.',
            component=True,
            purchaseable=True,
            units='ea',
            metadata={'r8_fixture': MARKER},
        )
        suppliers, offers = [], []
        for suffix, goods, freight in [
            ('A', '620.00', '45.00'),
            ('B', '560.00', '20.00'),
        ]:
            supplier = Company.objects.create(
                name=f'{PREFIX} supplier {suffix}',
                is_supplier=True,
                metadata={'r8_fixture': MARKER},
            )
            offer = SupplierPart.objects.create(
                part=part,
                supplier=supplier,
                SKU=f'R8-FIXTURE-{suffix}',
                packaging='kit',
                pack_quantity='1',
                metadata={
                    'r8_fixture': MARKER,
                    'representative_offer': {
                        'quote_reference': f'R8-FIXTURE-{suffix}',
                        'currency': 'USD',
                        'goods': goods,
                        'freight': freight,
                        'delivered_total': '665.00' if suffix == 'A' else '580.00',
                        'promise': None,
                        'technical_fit': None,
                    },
                },
            )
            SupplierPriceBreak.objects.create(
                part=offer, quantity=1, price=goods, price_currency='USD'
            )
            suppliers.append(supplier)
            offers.append(offer)
        work = WorkOrder.objects.create(
            reference=REFERENCE,
            title=f'{PREFIX} Cedar Creek Pump 08 / WO-003826',
            description='Representative procurement rehearsal. Original showcase remains untouched.',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=target.machine,
            customer=target.customer,
            assigned_to=actor,
            tags=[NAMESPACE, f'r8-fixture:{NAMESPACE}', 'fixture'],
            job_number=target.reference,
        )
        kit = JobKit.objects.create(work_order=work, created_by=actor)
        line = JobKitLine.objects.create(
            kit=kit,
            sequence=1,
            kind='part',
            requested_part=part,
            selected_part=part,
            required_quantity=1,
            fulfillment_mode='reserve_consume',
            source='manual',
            source_snapshot={'r8_fixture': MARKER, 'target_id': target.pk},
            note='Fixture only; no verified fit or physical event.',
        )
        manifest = {
            'provenance': MARKER,
            'target_id': target.pk,
            'approver_id': actor.pk,
            'group_id': group.pk,
            'part_id': part.pk,
            'supplier_ids': [s.pk for s in suppliers],
            'offer_ids': [o.pk for o in offers],
            'work_id': work.pk,
            'kit_id': kit.pk,
            'line_id': line.pk,
            'card_id': work.cards.get().pk,
        }
        suppliers[0].metadata['manifest'] = manifest
        suppliers[0].save(update_fields=['metadata'])
        return {'state': 'created', **manifest}

    def validate(self, manifest):
        """Fail closed on changed ownership or any downstream business effects."""
        work = WorkOrder.objects.get(pk=manifest['work_id'])
        part = Part.objects.get(pk=manifest['part_id'])
        suppliers = Company.objects.filter(pk__in=manifest['supplier_ids'])
        offers = SupplierPart.objects.filter(pk__in=manifest['offer_ids'])
        if (
            work.reference != REFERENCE
            or NAMESPACE not in work.tags
            or f'r8-fixture:{NAMESPACE}' not in work.tags
            or work.job_number != MARKER['scenario']
            or part.metadata.get('r8_fixture') != MARKER
            or suppliers.count() != 2
            or offers.count() != 2
            or any(s.metadata.get('r8_fixture') != MARKER for s in suppliers)
            or any(o.metadata.get('r8_fixture') != MARKER for o in offers)
        ):
            raise CommandError(
                'Fixture provenance has changed; manual review required.'
            )
        return work, part, suppliers, offers

    def remove(self, supplier, manifest, actor, dry_run):
        """Remove only untouched fixture records; business evidence is never reset."""
        if supplier is None:
            return {'state': 'absent', 'provenance': MARKER}
        if not manifest:
            raise CommandError('Fixture manifest missing; refusing removal.')
        work, part, suppliers, offers = self.validate(manifest)
        from order.models import PurchaseOrder
        from stock.models import StockItem
        from tasks.models import JobKitAllocation, WorkOrderCloseout

        if (
            StockItem.objects.filter(part=part).exists()
            or PurchaseOrder.objects.filter(supplier__in=suppliers).exists()
            or JobKitAllocation.objects.filter(line__kit__work_order=work).exists()
            or WorkOrderCloseout.objects.filter(work_order=work).exists()
            or work.events.exists()
            or work.cards.count() != 1
            or not work.cards.filter(
                pk=manifest['card_id'], status=WorkOrder.STATUS_BACKLOG
            ).exists()
            or work.attachments.exists()
            or work.lifecycle_status != 'draft'
            or work.lifecycle_version != 1
        ):
            raise CommandError(
                'Fixture has downstream activity; no stock, commitments or evidence will be deleted.'
            )
        if (
            part.attachments.exists()
            or any(s.attachments.exists() for s in suppliers)
            or any(o.attachments.exists() for o in offers)
        ):
            raise CommandError('Fixture has retained attachments; refusing removal.')
        line = JobKitLine.objects.get(pk=manifest['line_id'])
        group = Group.objects.get(pk=manifest['group_id'])
        if (
            group.name != GROUP
            or group.user_set.exclude(pk=actor.pk).exists()
            or JobKitLine.objects.filter(kit_id=manifest['kit_id']).count() != 1
            or line.source_snapshot.get('r8_fixture') != MARKER
            or line.shortages.exists()
            or line.selected_part_id != part.pk
            or line.requested_part_id != part.pk
        ):
            raise CommandError('Fixture dependencies changed; refusing removal.')
        if not dry_run:
            # PROTECT on any unanticipated external reference rolls back everything.
            work.delete()
            offers.delete()
            # Native deletion requires deactivation; this is the verified owned fixture only.
            part.active = False
            part.save(update_fields=['active'])
            part.delete()
            suppliers.delete()
            group.delete()
        return {'state': 'would_remove' if dry_run else 'removed', **manifest}
