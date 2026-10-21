"""Work-scoped, idempotent calibration traceability commands and projections."""

import hashlib
import uuid
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from rest_framework.exceptions import ValidationError

from common.models import Attachment
from tasks.calibration_models import CalibrationTraceability
from tasks.calibration_serializers import CalibrationCommandSerializer
from tasks.closeout_models import CloseoutReading
from tasks.permissions import require_permission
from tasks.services.closeout_capture import _require_wizard
from tasks.services.closeout_reconcile import CAPTURE_CLOSEOUT
from tasks.services.work_orders import (
    StaleVersion,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
    _require_version,
)
from tasks.work_execution.common import person


def _certificate_fingerprint(certificate):
    """Hash stored bytes, not the mutable/cached attachment size or filename."""
    digest = hashlib.sha256()
    size = 0
    with certificate.attachment.storage.open(
        certificate.attachment.name, 'rb'
    ) as source:
        for chunk in source.chunks():
            digest.update(chunk)
            size += len(chunk)
    return {'sha256': digest.hexdigest(), 'size': size}


def _certificate_integrity(row, fingerprints):
    """Only offer the recorded file after verifying its current bytes."""
    certificate = row.certificate
    snapshot = row.certificate_snapshot
    if certificate is None or not certificate.attachment:
        return 'unavailable'
    if (
        certificate.model_type != 'workorder'
        or certificate.model_id != row.work_order_id
        or certificate.pk != snapshot.get('id')
        or certificate.attachment.name != snapshot.get('filename')
    ):
        return 'changed'
    expected_hash = snapshot.get('sha256')
    expected_size = snapshot.get('size')
    if (
        not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(c not in '0123456789abcdef' for c in expected_hash)
        or type(expected_size) is not int
        or expected_size < 0
    ):
        return 'identity_not_recorded'
    key = (certificate.pk, certificate.attachment.name)
    if key not in fingerprints:
        try:
            fingerprints[key] = _certificate_fingerprint(certificate)
        except Exception:
            # Missing storage, credentials or an unreadable object is unknown,
            # never evidence that the bytes still match the recorded certificate.
            fingerprints[key] = None
    actual = fingerprints[key]
    if actual is None:
        return 'unavailable'
    return (
        'unchanged'
        if actual == {'sha256': expected_hash, 'size': expected_size}
        else 'changed'
    )


def serialize(row, *, fingerprints=None):
    """Date validity is evaluated at the reading time, never at today's date.

    A date-only certificate is valid through that calendar date in the server's
    configured timezone. This comparison says nothing about test acceptance.
    """
    reading_at = row.reading.recorded_at
    validity = 'unknown'
    if row.valid_until is not None and reading_at is not None:
        reading_date = (
            timezone.localtime(reading_at, timezone.get_default_timezone()).date()
            if timezone.is_aware(reading_at)
            else reading_at.date()
        )
        validity = (
            'expired_at_reading'
            if row.valid_until < reading_date
            else 'within_date_at_reading'
        )
    integrity = _certificate_integrity(
        row, fingerprints if fingerprints is not None else {}
    )
    certificate_available = integrity == 'unchanged'
    return {
        'id': row.pk,
        'work_order': row.work_order_id,
        'reading': row.reading_id,
        'revision': row.revision,
        'supersedes': row.supersedes_id,
        'reference_instrument_id': row.reference_instrument_id,
        'certificate_number': row.certificate_number,
        'issuing_lab': row.issuing_lab,
        'valid_until': row.valid_until.isoformat() if row.valid_until else None,
        'validity_state': validity,
        'validity_timezone': timezone.get_default_timezone_name(),
        'reading_recorded_at': reading_at.isoformat() if reading_at else None,
        'certificate_id': row.certificate_id,
        'certificate_snapshot': row.certificate_snapshot,
        'certificate_integrity_state': integrity,
        'certificate_available': certificate_available,
        'certificate_url': row.certificate.attachment.url
        if certificate_available
        else None,
        'correction_reason': row.correction_reason,
        'recorded_by': row.recorded_by_id,
        'recorded_by_name': person(row.recorded_by),
        'recorded_at': row.recorded_at.isoformat(),
    }


def project(work_order):
    """One bounded query retains all revisions; no sibling's rows can join."""
    result = defaultdict(list)
    rows = work_order.calibration_traceability.select_related(
        'reading', 'recorded_by', 'certificate'
    )
    fingerprints = {}
    for row in rows:
        result[row.reading_id].append(serialize(row, fingerprints=fingerprints))
    return result


@transaction.atomic
def command(*, work_order_id, reading_id, actor, **intent):
    """Reauthorize retries and append one revision plus the native audit receipt."""
    _require_wizard()
    # Fetch current permissions, including revocation, even for an exact retry.
    live_actor = (
        get_user_model()
        .objects.filter(pk=getattr(actor, 'pk', None), is_active=True)
        .first()
    )
    require_permission(live_actor, CAPTURE_CLOSEOUT)
    work = _locked_work_order(work_order_id)
    _require_scope(actor, work)
    reading = CloseoutReading.objects.filter(pk=reading_id, work_order=work).first()
    if reading is None:
        raise ValidationError('Reading is unavailable in this Work.')
    validator = CalibrationCommandSerializer(data=intent)
    validator.is_valid(raise_exception=True)
    clean = validator.validated_data
    request_hash = _canonical_hash(
        'calibration_traceability', actor, {'reading_id': reading_id, **clean}
    )
    replay = _replay_or_none(work, clean['idempotency_key'], request_hash)
    if replay is not None:
        return replay.metadata
    if work.work_order_type != 'calibration':
        raise ValidationError('Traceability can only be recorded on calibration Work.')
    _require_version(work, clean['expected_version'])
    latest = work.calibration_traceability.filter(reading=reading).first()
    revision = latest.revision if latest else 0
    if clean['expected_revision'] != revision:
        raise StaleVersion(
            'Calibration traceability changed; refresh before correcting.'
        )
    if (clean['operation'] == 'record') != (latest is None):
        raise ValidationError(
            'Record needs no earlier traceability; correct needs an earlier revision.'
        )
    if latest is not None and not clean.get('correction_reason'):
        raise ValidationError('A correction reason is required.')
    certificate = (
        Attachment.objects
        .select_for_update()
        .filter(pk=clean['certificate_id'], model_type='workorder', model_id=work.pk)
        .first()
    )
    if certificate is None or not certificate.attachment:
        raise ValidationError(
            'Select an uploaded certificate file attached to this Work.'
        )
    try:
        fingerprint = _certificate_fingerprint(certificate)
    except Exception as exc:
        raise ValidationError({
            'certificate_id': 'Certificate bytes could not be read. Traceability was not recorded.'
        }) from exc
    row = CalibrationTraceability.objects.create(
        work_order=work,
        reading=reading,
        revision=revision + 1,
        supersedes=latest,
        reference_instrument_id=clean['reference_instrument_id'],
        certificate_number=clean['certificate_number'],
        issuing_lab=clean['issuing_lab'],
        valid_until=clean['valid_until'],
        certificate=certificate,
        certificate_snapshot={
            'id': certificate.pk,
            'filename': certificate.attachment.name,
            'comment': certificate.comment,
            **fingerprint,
        },
        correction_reason=clean.get('correction_reason', ''),
        recorded_by=actor,
    )
    result = {
        'traceability': serialize(
            row,
            fingerprints={(certificate.pk, certificate.attachment.name): fingerprint},
        ),
        'lifecycle_version': work.lifecycle_version,
    }
    _append_result(
        work_order=work,
        actor=actor,
        command='calibration_traceability',
        event_type='calibration_' + clean['operation'],
        from_status=work.lifecycle_status,
        to_status=work.lifecycle_status,
        reason=clean.get('correction_reason') or 'Calibration traceability recorded',
        correlation_id=uuid.uuid4(),
        idempotency_key=clean['idempotency_key'],
        request_hash=request_hash,
        result_metadata=result,
    )
    return result
