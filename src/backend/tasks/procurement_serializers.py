"""R8 evidence commands. Decimal strings and timezone-qualified instants only."""

from datetime import timezone
from decimal import Decimal

from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_aware

from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    """Reject misspelled fields instead of silently dropping evidence."""

    def to_internal_value(self, data):
        """Preserve a closed command shape."""
        if isinstance(data, dict) and (unknown := set(data) - set(self.fields)):
            raise serializers.ValidationError({'unknown_fields': sorted(unknown)})
        return super().to_internal_value(data)


class InstantField(serializers.DateTimeField):
    """An instant must include its source timezone, even in a UTC deployment."""

    def __init__(self, *args, **kwargs):
        """Override the native API's minute-only display format for evidence."""
        kwargs.setdefault('format', 'iso-8601')
        super().__init__(*args, **kwargs)

    def default_timezone(self):
        """Keep JSON instants zoned even when Django's SQLite tests use naive DB time."""
        return timezone.utc

    def to_internal_value(self, value):
        """Refuse date-only and naive timestamps rather than guessing a zone."""
        try:
            parsed = parse_datetime(value) if isinstance(value, str) else value
        except ValueError as exc:
            raise serializers.ValidationError('Invalid ISO instant') from exc
        if parsed is None or not hasattr(parsed, 'utcoffset') or not is_aware(parsed):
            raise serializers.ValidationError(
                'An ISO instant with timezone is required'
            )
        return super().to_internal_value(value)


class EvidenceSerializer(StrictSerializer):
    """Exact original PDF identity and cited page; hash checked against bytes."""

    attachment_id = serializers.IntegerField(min_value=1)
    sha256 = serializers.RegexField(r'^[a-f0-9]{64}$')
    page = serializers.IntegerField(min_value=1)
    revision = serializers.CharField(max_length=128)


class ProvenanceSerializer(StrictSerializer):
    """Fixtures always retain their namespace; no invented live execution."""

    mode = serializers.ChoiceField(choices=['live_test', 'recorded_fixture'])
    method = serializers.ChoiceField(
        choices=['human_review', 'recorded_document', 'model_execution']
    )
    reference = serializers.CharField(max_length=255)
    fixture_namespace = serializers.CharField(max_length=128, allow_null=True)

    def validate(self, attrs):
        """Require an isolated namespace exactly when the evidence is a fixture."""
        fixture = attrs['mode'] == 'recorded_fixture'
        if fixture != bool(attrs['fixture_namespace']):
            raise serializers.ValidationError(
                'Fixture provenance needs a namespace; live evidence must not have one'
            )
        return attrs


def _decimal(*, positive=False, nullable=False):
    return serializers.DecimalField(
        max_digits=24,
        decimal_places=6,
        min_value=Decimal('0.000001') if positive else Decimal('0'),
        allow_null=nullable,
        coerce_to_string=True,
    )


class OfferSerializer(StrictSerializer):
    """Comparable full offer; null costs/promises remain unknown."""

    key = serializers.CharField(max_length=128)
    supplier_part_id = serializers.IntegerField(min_value=1)
    quote_reference = serializers.CharField(max_length=128)
    revision = serializers.CharField(max_length=128)
    order_quantity = _decimal(positive=True)
    order_uom = serializers.CharField(max_length=32)
    base_units_per_order_unit = _decimal(positive=True)
    unit_price = _decimal(nullable=True)
    discount = _decimal(nullable=True)
    freight = _decimal(nullable=True)
    tax = _decimal(nullable=True)
    other_charges = _decimal(nullable=True)
    delivered_total = _decimal(nullable=True)
    currency = serializers.RegexField(r'^[A-Z]{3}$')
    terms = serializers.CharField(max_length=4000, allow_null=True)
    promised_at = InstantField(allow_null=True)
    promise_basis = serializers.ChoiceField(
        choices=['supplier_quote', 'estimate', 'unknown']
    )
    valid_through = InstantField(allow_null=True)
    technical_eligibility = serializers.ChoiceField(
        choices=['supported', 'unresolved', 'not_eligible']
    )
    source = EvidenceSerializer()

    def validate(self, attrs):
        """Verify complete Decimal arithmetic; a known zero is never missing."""
        amounts = [
            attrs[k]
            for k in ('unit_price', 'discount', 'freight', 'tax', 'other_charges')
        ]
        if any(value is None for value in amounts):
            if attrs['delivered_total'] is not None:
                raise serializers.ValidationError(
                    'Delivered total is unknown when any cost is unknown'
                )
        else:
            goods = attrs['order_quantity'] * attrs['unit_price'] - attrs['discount']
            if (
                goods < 0
                or attrs['delivered_total']
                != goods + attrs['freight'] + attrs['tax'] + attrs['other_charges']
            ):
                raise serializers.ValidationError(
                    'Delivered total must equal goods less discount plus all charges'
                )
        if (attrs['promised_at'] is None) != (attrs['promise_basis'] == 'unknown'):
            raise serializers.ValidationError('Promise time and basis must agree')
        return attrs


class EvidenceCommandSerializer(StrictSerializer):
    """Stable operation identity reused across retries and reloads."""

    operation_key = serializers.CharField(max_length=128)
    expected_version = serializers.IntegerField(min_value=1)
    kit_line_id = serializers.IntegerField(min_value=1)
    source_at = InstantField()
    provenance = ProvenanceSerializer()


class SourceSelectionSerializer(EvidenceCommandSerializer):
    """An immutable decision tied to the current technical need."""

    requirement_version = serializers.IntegerField(min_value=1)
    configuration_revision = serializers.RegexField(r'^[a-f0-9]{64}$')
    needed_at = InstantField(allow_null=True)
    deadline_source = EvidenceSerializer(allow_null=True)
    dependent_step = serializers.CharField(max_length=255, allow_null=True)
    technical_evidence = EvidenceSerializer(many=True, allow_empty=False)
    offers = OfferSerializer(many=True, allow_empty=False)
    selected_offer_key = serializers.CharField(max_length=128)
    reason = serializers.CharField(max_length=4000)
    supersedes_event_id = serializers.IntegerField(min_value=1, allow_null=True)

    def validate(self, attrs):
        """Selection references exactly one offer and retains deadline provenance."""
        keys = [offer['key'] for offer in attrs['offers']]
        if len(set(keys)) != len(keys) or attrs['selected_offer_key'] not in keys:
            raise serializers.ValidationError(
                'Offer keys must be unique and include the selected offer'
            )
        if (attrs['needed_at'] is None) != (attrs['deadline_source'] is None):
            raise serializers.ValidationError(
                'A precise requirement deadline needs its own source'
            )
        return attrs


class AcknowledgmentSerializer(EvidenceCommandSerializer):
    """Explicit acceptance of a known native PO line, never a generic reply."""

    purchase_order_line_id = serializers.IntegerField(min_value=1)
    supplier_reference = serializers.CharField(max_length=255)
    accepted_quantity = _decimal(positive=True)
    terms = serializers.CharField(max_length=4000)
    promised_at = InstantField()
    source = EvidenceSerializer()
    deviations = serializers.ListField(child=serializers.CharField(max_length=1000))


class ProcurementActorSerializer(serializers.Serializer):
    """Recorded person, without substituting a service as an approver."""

    pk = serializers.IntegerField(allow_null=True)
    label = serializers.CharField(allow_null=True)
    kind = serializers.CharField(allow_null=True)


class ProcurementEventSerializer(serializers.Serializer):
    """Shared event envelope; snapshot shape follows its event's typed contract."""

    event_id = serializers.IntegerField()
    operation_key = serializers.CharField()
    event_type = serializers.CharField()
    source_at = serializers.DateTimeField(allow_null=True)
    recorded_at = serializers.DateTimeField()
    actor = ProcurementActorSerializer()
    snapshot = serializers.JSONField()
