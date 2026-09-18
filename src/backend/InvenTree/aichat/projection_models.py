"""Content-free projection drift evidence and durable recall stops."""

from django.db import models

CORPORA = [('attachment', 'Attachment documents'), ('media', 'Evidence media')]


class RagProjectionAudit(models.Model):
    """Aggregate sample evidence, never a certificate for the whole index."""

    corpus = models.CharField(max_length=16, choices=CORPORA)
    outcome = models.CharField(max_length=16)
    sampled = models.PositiveIntegerField(default=0)
    drift = models.PositiveIntegerField(default=0)
    critical = models.PositiveIntegerField(default=0)
    errors = models.PositiveIntegerField(default=0)
    truncated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)


class RagProjectionGate(models.Model):
    """Critical drift remains latched until an explicit operator release."""

    corpus = models.CharField(primary_key=True, max_length=16, choices=CORPORA)
    blocked = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)


class RagProjectionRepair(models.Model):
    """An idempotent work item; source deletion cascades away its identity."""

    ingest = models.OneToOneField('aichat.AttachmentIngest', on_delete=models.CASCADE)
    corpus = models.CharField(max_length=16, choices=CORPORA)
    reason = models.CharField(max_length=16)
    snapshot_hash = models.CharField(max_length=64)
    resolved = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)
