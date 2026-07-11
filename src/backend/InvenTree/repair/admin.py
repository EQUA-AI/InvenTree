"""Admin configuration for the Repair Packet application."""

from django.contrib import admin

from .models import (
    LockoutPoint,
    RepairPacket,
    RepairPacketApprovalLink,
    RepairPacketEvent,
    RepairPacketEvidence,
    RepairPacketGate,
    RepairPacketGenerationRun,
    SafetyEvidenceProof,
    SafetyGateTemplate,
)


class RepairPacketGateInline(admin.TabularInline):
    """Inline editor for safety gates."""

    model = RepairPacketGate
    extra = 0


class RepairPacketEvidenceInline(admin.TabularInline):
    """Inline editor for evidence items."""

    model = RepairPacketEvidence
    extra = 0


class RepairPacketEventInline(admin.TabularInline):
    """Read-only inline audit timeline."""

    model = RepairPacketEvent
    extra = 0
    can_delete = False
    readonly_fields = (
        'event_type',
        'from_status',
        'to_status',
        'actor',
        'reason',
        'created_at',
    )


@admin.register(RepairPacket)
class RepairPacketAdmin(admin.ModelAdmin):
    """Admin interface for repair packets."""

    list_display = (
        'reference',
        'status',
        'generation_status',
        'criticality',
        'machine',
        'created_at',
        'updated_at',
    )
    list_filter = ('status', 'generation_status', 'criticality')
    search_fields = ('reference', 'fault_summary', 'symptom')
    ordering = ('-created_at',)
    inlines = [
        RepairPacketGateInline,
        RepairPacketEvidenceInline,
        RepairPacketEventInline,
    ]


@admin.register(SafetyGateTemplate)
class SafetyGateTemplateAdmin(admin.ModelAdmin):
    """Admin interface for reusable safety gate templates."""

    list_display = (
        'name',
        'gate_type',
        'risk_tier',
        'is_blocking',
        'is_mandatory',
        'active',
    )
    list_filter = ('gate_type', 'risk_tier', 'is_blocking', 'active')
    search_fields = ('name', 'instructions')
    ordering = ('default_sequence', 'name')


@admin.register(LockoutPoint)
class LockoutPointAdmin(admin.ModelAdmin):
    """Admin interface for LOTO lockout points."""

    list_display = ('gate', 'energy_source', 'isolation_device', 'status')
    list_filter = ('energy_source', 'status')
    search_fields = ('isolation_device', 'lock_id', 'tag_id')


@admin.register(SafetyEvidenceProof)
class SafetyEvidenceProofAdmin(admin.ModelAdmin):
    """Admin interface for structured safety proof."""

    list_display = ('gate', 'proof_type', 'captured_by', 'captured_at')
    list_filter = ('proof_type',)


@admin.register(RepairPacketApprovalLink)
class RepairPacketApprovalLinkAdmin(admin.ModelAdmin):
    """Admin interface for packet-approval links."""

    list_display = ('packet', 'approval', 'purpose', 'created_at')
    list_filter = ('purpose',)


@admin.register(RepairPacketGenerationRun)
class RepairPacketGenerationRunAdmin(admin.ModelAdmin):
    """Admin interface for generation provenance runs."""

    list_display = (
        'agent_run_id',
        'packet',
        'provider',
        'status',
        'started_at',
        'finished_at',
    )
    list_filter = ('status', 'provider')
    search_fields = ('agent_run_id',)


@admin.register(RepairPacketEvent)
class RepairPacketEventAdmin(admin.ModelAdmin):
    """Admin interface for the packet audit timeline."""

    list_display = (
        'packet',
        'event_type',
        'from_status',
        'to_status',
        'actor',
        'created_at',
    )
    list_filter = ('event_type',)
    search_fields = ('packet__reference',)
