"""Serializers for the tasks application."""

from rest_framework import serializers

from .models import KanbanCard, KanbanCardPart


class KanbanCardPartSerializer(serializers.ModelSerializer):
    """Serializer for KanbanCardPart instances."""

    part_name = serializers.CharField(source='part.name', read_only=True)
    part_ipn = serializers.CharField(source='part.IPN', read_only=True, default='')
    part_thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = KanbanCardPart
        fields = (
            'id',
            'part',
            'part_name',
            'part_ipn',
            'part_thumbnail',
            'quantity',
            'allocated_quantity',
            'allocation_status',
            'allocation_note',
            'created_at',
            'updated_at',
        )
        read_only_fields = (
            'id',
            'allocated_quantity',
            'allocation_status',
            'allocation_note',
            'created_at',
            'updated_at',
        )

    def get_part_thumbnail(self, obj) -> str | None:
        """Return the thumbnail URL for the part."""
        if obj.part and obj.part.image:
            return obj.part.image.url
        return None


class KanbanCardSerializer(serializers.ModelSerializer):
    """Serializer for KanbanCard instances."""

    tags = serializers.ListField(
        child=serializers.CharField(max_length=32),
        allow_empty=True,
        required=False,
    )
    due_date = serializers.DateField(allow_null=True, required=False)

    parts = KanbanCardPartSerializer(source='card_parts', many=True, read_only=True)

    class Meta:
        model = KanbanCard
        fields = (
            'id',
            'title',
            'description',
            'status',
            'priority',
            'due_date',
            'assignee',
            'tags',
            'company',
            'company_contact_name',
            'company_contact_phone',
            'job_number',
            'service_quote',
            'is_active',
            'created_at',
            'updated_at',
            'parts',
        )
        read_only_fields = ('id', 'is_active', 'created_at', 'updated_at')

    def validate_tags(self, value):
        """Ensure tags are stored as unique values."""

        # Remove duplicates while preserving order
        seen = set()
        filtered = []

        for tag in value:
            if tag not in seen:
                seen.add(tag)
                filtered.append(tag)

        return filtered
