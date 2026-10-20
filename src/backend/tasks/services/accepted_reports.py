"""Freeze the rendered maintenance account, never reconstruct accepted history."""

import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from tasks.report_models import AcceptedCloseoutReport


def canonical_export(report):
    """Exact UTF-8 download representation covered by the snapshot hash."""
    return json.dumps(
        {'document': report.document, 'text': report.text},
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
    )


def report_url(report):
    """Stable scoped identity, independent of subsequent account revisions."""
    return f'/api/tasks/work-orders/{report.closeout.work_order_id}/closeout/reports/{report.pk}/'


def report_summary(report):
    """Completion/revision attribution and separately sourced native verification."""
    completion = report.document['completion']
    closeout = report.closeout
    verifier = closeout.verified_by
    return {
        'id': report.pk,
        'revision': report.revision,
        'url': report_url(report),
        'content_hash': report.content_hash,
        'completed_by': completion['actor_id'],
        'completed_by_name': completion['actor_name'],
        'completed_at': completion['at'],
        'frozen_by': report.accepted_by_id,
        'frozen_by_name': report.document['revision_recorded']['actor_name'],
        'frozen_at': report.accepted_at,
        'verified_by': closeout.verified_by_id,
        'verified_by_name': (
            verifier.get_full_name().strip() or verifier.get_username()
        )
        if verifier
        else None,
        'verified_at': closeout.verified_at,
        'amendment_id': report.amendment_id,
    }


def freeze_report(*, work_order, closeout, actor, amendment=None):
    """Called while the parent Work is locked, inside its atomic command."""
    from tasks.closeout_serializers import (
        CloseoutCaptureSerializer,
        CloseoutPartUsageSerializer,
        CloseoutProposalSerializer,
        CloseoutReadingSerializer,
    )
    from tasks.services.closeout_amend import effective_closeout

    previous = closeout.reports.order_by('-revision').first()
    if previous is None and amendment is not None:
        # Legacy completions have no historical snapshot. Do not backdate one.
        return None
    captures = list(
        work_order.closeout_captures.filter(completed_closeout_id=closeout.pk).order_by(
            'pk'
        )
    )
    other_captures = work_order.closeout_captures.exclude(
        completed_closeout_id=closeout.pk
    ).order_by('pk')
    now = amendment.applied_at if amendment else closeout.completed_at
    document = {
        'schema': 'accepted_closeout_report.v1',
        'revision': previous.revision + 1 if previous else 1,
        'previous_report_id': previous.pk if previous else None,
        'work': {
            'id': work_order.pk,
            'reference': work_order.reference,
            'title': work_order.title,
            'machine_id': work_order.machine_id,
            'machine_name': work_order.machine.name if work_order.machine_id else None,
        },
        'completion': {
            'actor_id': closeout.completed_by_id,
            'actor_name': closeout.completed_by.get_full_name().strip()
            or closeout.completed_by.get_username(),
            'at': closeout.completed_at,
        },
        'revision_recorded': {
            'actor_id': actor.pk,
            'actor_name': actor.get_full_name().strip() or actor.get_username(),
            'at': now,
        },
        'verification': {
            'actor_id': closeout.verified_by_id,
            'actor_name': closeout.verified_by.get_full_name().strip()
            or closeout.verified_by.get_username(),
            'at': closeout.verified_at,
        }
        if closeout.verified_by_id and closeout.verified_at
        else None,
        'closeout': effective_closeout(closeout),
        'closeout_id': closeout.pk,
        'captures': CloseoutCaptureSerializer(captures, many=True).data,
        'other_captures': CloseoutCaptureSerializer(other_captures, many=True).data,
        'proposals': [
            CloseoutProposalSerializer(proposal).data
            for capture in captures
            for proposal in capture.current_revision.proposals.all().order_by('pk')
        ],
        'materials': CloseoutPartUsageSerializer(
            work_order.closeout_part_usage.all().order_by('pk'), many=True
        ).data,
        'readings': CloseoutReadingSerializer(
            work_order.closeout_readings.all().order_by('pk'), many=True
        ).data,
        'person_work_time': None,
        'person_work_time_status': 'unavailable_coverage_unknown',
        'operating_service_return': 'not_established_by_this_account',
        'amendment': {
            'id': amendment.pk,
            'reason': amendment.reason,
            'requested_by': amendment.requested_by_id,
            'requested_by_name': amendment.requested_by.get_full_name().strip()
            or amendment.requested_by.get_username(),
            'changes': amendment.changes,
        }
        if amendment
        else None,
    }
    if previous:
        # Supporting evidence is the accepted evidence, never today's mutable
        # materials/readings/labels. Only attributed amendment fields change.
        for key in (
            'work',
            'completion',
            'captures',
            'other_captures',
            'proposals',
            'materials',
            'readings',
            'person_work_time',
            'person_work_time_status',
            'operating_service_return',
        ):
            document[key] = previous.document[key]
    document = json.loads(json.dumps(document, cls=DjangoJSONEncoder))
    fields = document['closeout']
    labels = {
        'cause': 'Cause',
        'action': 'Action taken',
        'result': 'Result',
        'verification_summary': 'Verification summary',
        'downtime_minutes': 'Downtime (minutes)',
        'follow_up_required': 'Follow-up required',
        'follow_up': 'Follow-up',
    }
    text = '\n\n'.join([
        f'{document["work"]["reference"]} · {document["work"]["title"]}',
        f'Frozen closeout report · Revision {document["revision"]}',
        f'Completed by {document["completion"]["actor_name"]} · {document["completion"]["at"]}',
        f'Revision recorded by {document["revision_recorded"]["actor_name"]} · {document["revision_recorded"]["at"]}',
        f'Verified by {document["verification"]["actor_name"]} · {document["verification"]["at"]}'
        if document['verification']
        else 'Acceptance: Not recorded',
        *[
            f'{labels[key]}: {value if value is not None and value != "" else "Not recorded"}'
            for key, value in fields.items()
        ],
        'Person-work time: Unavailable — coverage unknown',
        'Operating / service return: Not established by this account',
        *[
            f'Story source: Capture {row["id"]} · Revision {row["revision"]} · Recorded by {row["created_by_name"]}\n{row["narrative"]}\nTranscript reference: {row["transcript_reference"]}\nOriginal media and source spans: {json.dumps({"originals": row["originals"], "source_spans": row["source_spans"]}, ensure_ascii=False, default=str)}'
            for row in document['captures']
        ],
        'Other retained stories (not account sources)',
        *[
            f'Capture {row["id"]} · Status {row["status"]} · Revision {row["revision"]} · Recorded by {row["created_by_name"]}\n{row["narrative"]}'
            for row in document['other_captures']
        ],
        *[
            f'Material usage row {row["id"]}: {json.dumps(row, ensure_ascii=False)}'
            for row in document['materials']
        ],
        *[
            f'Reading {row["label"]}: {row["raw_text"]} {row["unit"]} · {row["verification_state"]} · Recorded by {row["recorded_by_name"]} · {row["recorded_at"]}'
            for row in document['readings']
        ],
        'Saved decisions and source references: '
        + json.dumps(document['proposals'], ensure_ascii=False),
        'Amendment: ' + json.dumps(document['amendment'], ensure_ascii=False)
        if amendment
        else 'Frozen at completion; no amendment in this revision',
    ])
    digest = hashlib.sha256(
        json.dumps(
            {'document': document, 'text': text},
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    return AcceptedCloseoutReport.objects.create(
        closeout=closeout,
        revision=document['revision'],
        previous=previous,
        amendment=amendment,
        document=document,
        text=text,
        content_hash=digest,
        accepted_by=actor,
        accepted_at=now or timezone.now(),
    )
