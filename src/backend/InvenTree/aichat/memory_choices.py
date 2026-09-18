"""Durable-memory vocabulary; summary-only lifecycle states stay separate."""

from django.db import models


class MemoryVerification(models.TextChoices):
    """Closed MemoryVerification values enforced by database constraints."""

    USER_CONFIRMED = 'user_confirmed', 'User confirmed'
    TOOL_VERIFIED = 'tool_verified', 'Tool verified'
    HUMAN_VERIFIED = 'human_verified', 'Human verified'
    INFERRED = 'inferred', 'Inferred'


class MemoryLifecycle(models.TextChoices):
    """Closed MemoryLifecycle values enforced by database constraints."""

    PROPOSED = 'proposed', 'Proposed'
    ACTIVE = 'active', 'Active'
    SUPERSEDED = 'superseded', 'Superseded'
    EXPIRED = 'expired', 'Expired'
    FORGOTTEN = 'forgotten', 'Forgotten'
    WITHDRAWN = 'withdrawn', 'Withdrawn'


class MemoryOrigin(models.TextChoices):
    """Closed MemoryOrigin values enforced by database constraints."""

    USER_EXPLICIT = 'user_explicit', 'User explicit'
    COMPACTION = 'compaction', 'Compaction'
    MEM0 = 'mem0', 'Mem0'
    TOOL_READ = 'tool_read', 'Tool read'
    CLOSEOUT = 'closeout', 'Closeout'
    INCIDENT = 'incident', 'Incident'


class MemoryVisibility(models.TextChoices):
    """Closed MemoryVisibility values enforced by database constraints."""

    USER_PRIVATE = 'user_private', 'User private'
    CLIENT_SHARED = 'client_shared', 'Client shared'


class MemoryClassification(models.TextChoices):
    """Closed MemoryClassification values enforced by database constraints."""

    PREFERENCE = 'preference', 'Preference'
    OPERATIONAL = 'operational', 'Operational'
    PERSONAL = 'personal', 'Personal'


class DurableMemoryType(models.TextChoices):
    """Closed DurableMemoryType values enforced by database constraints."""

    EQUIPMENT_FACT = 'equipment_fact', 'Equipment fact'
    PROCEDURE_NOTE = 'procedure_note', 'Procedure note'
    SITE_CONVENTION = 'site_convention', 'Site convention'
    SCHEDULE = 'schedule', 'Schedule'
    OPEN_ISSUE = 'open_issue', 'Open issue'
    CONTACT_ROLE = 'contact_role', 'Contact role'
    USER_PREFERENCE = 'user_preference', 'User preference'


class MemorySourceClass(models.TextChoices):
    """Closed MemorySourceClass values enforced by database constraints."""

    MAINTENANCE_AUTHORIZED = 'maintenance_authorized', 'Maintenance authorized'
    ATTACHMENT_UPLOADED = 'attachment_uploaded', 'Attachment uploaded'
    EVIDENCE_RECORDING = 'evidence_recording', 'Evidence recording'
    TOOL_RECORD = 'tool_record', 'Tool record'
    USER_UTTERANCE = 'user_utterance', 'User utterance'


class MemoryTrust(models.TextChoices):
    """Closed MemoryTrust values enforced by database constraints."""

    TRUSTED_RECORD = 'trusted_record', 'Trusted record'
    UNTRUSTED_FENCED = 'untrusted_fenced', 'Untrusted fenced'


class MemoryTopic(models.TextChoices):
    """Closed MemoryTopic values enforced by database constraints."""

    ELECTRICAL = 'electrical', 'Electrical'
    HYDRAULIC = 'hydraulic', 'Hydraulic'
    PNEUMATIC = 'pneumatic', 'Pneumatic'
    MECHANICAL = 'mechanical', 'Mechanical'
    THERMAL = 'thermal', 'Thermal'
    CHEMICAL = 'chemical', 'Chemical'
    GRAVITY = 'gravity', 'Gravity'
    CONTROLS = 'controls', 'Controls'
    SAFETY = 'safety', 'Safety'
    PARTS = 'parts', 'Parts'
    PLANNING = 'planning', 'Planning'
    DOCUMENTATION = 'documentation', 'Documentation'


class MemoryShieldState(models.TextChoices):
    """Closed MemoryShieldState values enforced by database constraints."""

    PENDING = 'pending', 'Pending'
    CLEAR = 'clear', 'Clear'
    FLAGGED = 'flagged', 'Flagged'
    UNAVAILABLE = 'unavailable', 'Unavailable'


class MemoryEventAction(models.TextChoices):
    """Closed MemoryEventAction values enforced by database constraints."""

    PROPOSE = 'propose', 'Propose'
    CONFIRM = 'confirm', 'Confirm'
    REJECT = 'reject', 'Reject'
    SUPERSEDE = 'supersede', 'Supersede'
    FORGET = 'forget', 'Forget'
    WITHDRAW = 'withdraw', 'Withdraw'
    RETAG = 'retag', 'Retag'
    SEVER = 'sever', 'Sever'
    MODE_CHANGE = 'mode_change', 'Mode change'
    OPT_OUT = 'opt_out', 'Opt out'


class ExtractionState(models.TextChoices):
    """Closed ExtractionState values enforced by database constraints."""

    PENDING = 'pending', 'Pending'
    CLAIMED = 'claimed', 'Claimed'
    COMPLETE = 'complete', 'Complete'
    DEFERRED = 'deferred', 'Deferred'
    FAILED = 'failed', 'Failed'
    SKIPPED = 'skipped', 'Skipped'
