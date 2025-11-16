"""Serializers for the tasks application."""

from rest_framework import serializers

from .models import KanbanCard


class KanbanCardSerializer(serializers.ModelSerializer):
    """Serializer for KanbanCard instances."""

    tags = serializers.ListField(
        child=serializers.CharField(max_length=32),
        allow_empty=True,
        required=False,
    )
    due_date = serializers.DateField(allow_null=True, required=False)

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
