"""Server-clock work intervals and confirmed telemetry downtime (O15)."""

import uuid
from collections import defaultdict

from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError

from tasks.scope import work_machine_authorized
from tasks.services.investigation_requests import _change_actor
from tasks.services.work_orders import (
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
    _require_version,
)
from tasks.time_models import WorkDowntime, WorkTimeInterval
from tasks.work_execution.common import person, record_instant
from users.permissions import check_user_role


def closure_time(work):
    """Use recorded closure evidence; never updated_at or the read clock."""
    if work.lifecycle_status == 'completed':
        return work.actual_completed_at
    if work.lifecycle_status == 'canceled':
        return (
            work.events
            .filter(to_status='canceled')
            .exclude(from_status='canceled')
            .order_by('-created_at', '-pk')
            .values_list('created_at', flat=True)
            .first()
        )
    return None


def time_projection(work, now=None):
    """Only recorded intervals count; everything between them is unclassified."""
    now = now or timezone.now()
    rows = list(
        WorkTimeInterval.objects
        .filter(work_order=work)
        .select_related('person', 'recorded_by', 'ended_by')
        .order_by('started_at', 'pk')
    )
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    current = [r for r in rows if r.pk not in superseded]
    people = defaultdict(list)
    for row in current:
        people[row.person_id].append(row)
    closed = work.lifecycle_status in {'completed', 'canceled'}
    cutoff = closure_time(work) if closed else now
    horizon = min(now, cutoff) if cutoff else None
    gaps = []
    for intervals in people.values() if horizon else []:
        cursor = work.created_at
        for row in intervals:
            start = min(row.started_at, horizon)
            if cursor < start:
                gaps.append({
                    'person': person(row.person),
                    'started_at': cursor.isoformat(),
                    'ended_at': start.isoformat(),
                })
            cursor = max(cursor, min(row.ended_at or horizon, horizon))
        if cursor < horizon:
            gaps.append({
                'person': person(intervals[0].person),
                'started_at': cursor.isoformat(),
                'ended_at': horizon.isoformat(),
            })
    return {
        'observed_at': now.isoformat(),
        'coverage': 'partial' if rows else 'unknown',
        'closed_at': cutoff.isoformat() if closed and cutoff else None,
        'open_at_closure': closed and any(r.ended_at is None for r in current),
        'recorded_seconds': sum(
            max(0, (min(r.ended_at or horizon, horizon) - r.started_at).total_seconds())
            for r in current
        )
        if current and horizon
        else None,
        'gaps': gaps,
        'intervals': [
            {
                'id': r.pk,
                'person_id': r.person_id,
                'person': person(r.person),
                'started_at': r.started_at.isoformat(),
                'ended_at': r.ended_at.isoformat() if r.ended_at else None,
                'end_action': r.end_action or None,
                'recorded_by': person(r.recorded_by),
                'recorded_at': r.recorded_at.isoformat(),
                'ended_by': person(r.ended_by),
                'ended_recorded_at': r.ended_recorded_at.isoformat()
                if r.ended_recorded_at
                else None,
                'reason': r.reason or None,
                'supersedes': r.supersedes_id,
                'superseded': r.pk in superseded,
            }
            for r in rows
        ],
    }


def _downtime_origin_matches(work, proposal, binding_id):
    """A saved sample retains its original Work, machine and binding identity."""
    return (
        isinstance(proposal, dict)
        and type(proposal.get('work')) is int
        and proposal['work'] == work.pk
        and type(proposal.get('machine')) is int
        and proposal['machine'] == work.machine_id
        and type(proposal.get('binding_id')) is int
        and type(binding_id) is int
        and proposal['binding_id'] == binding_id
    )


def downtime_projection(work, actor):
    """Confirmed machine stop windows, separate from service availability."""
    if not work_machine_authorized(actor, work):
        return []
    rows = list(
        WorkDowntime.objects
        .filter(work_order=work)
        .select_related('recorded_by', 'binding')
        .order_by('-pk')
    )
    superseded = {r.supersedes_id for r in rows if r.supersedes_id}
    return [
        {
            'id': r.pk,
            'binding_id': r.binding_id,
            'signal': r.binding.display_name,
            'started_at': r.started_at.isoformat(),
            'ended_at': r.ended_at.isoformat(),
            'recorded_by': person(r.recorded_by),
            'recorded_at': r.recorded_at.isoformat(),
            'reason': r.reason or None,
            'proposal': r.proposal,
            'supersedes': r.supersedes_id,
            'superseded': r.pk in superseded,
        }
        for r in rows
        if r.binding.machine_id == work.machine_id
        and _downtime_origin_matches(work, r.proposal, r.binding_id)
    ]


def redact_machine_telemetry(result, work, actor):
    """Withhold machine samples from a response without rewriting its receipt."""
    if 'downtime' not in result:
        return result
    if not work_machine_authorized(actor, work) or not isinstance(
        result['downtime'], list
    ):
        return {**result, 'downtime': []}
    from assets.health_models import MachineSignalBinding

    candidates = [
        row
        for row in result['downtime']
        if isinstance(row, dict)
        and _downtime_origin_matches(work, row.get('proposal'), row.get('binding_id'))
    ]
    binding_ids = set(
        MachineSignalBinding.objects.filter(
            pk__in={row['binding_id'] for row in candidates}, machine_id=work.machine_id
        ).values_list('pk', flat=True)
    )
    return {
        **result,
        'downtime': [row for row in candidates if row['binding_id'] in binding_ids],
    }


def _status_bindings(work):
    from django.db.models import Q

    from assets.health_models import MachineSignalBinding
    from machine_health.connectors.base import PUMPHOUSE_CONNECTOR_TYPES

    # Registry /pd/<bay>/st and /st are the documented status paths. Do not
    # guess status from motor speed, current, duty, names, or an arbitrary tag.
    return MachineSignalBinding.objects.filter(
        machine_id=work.machine_id, active=True, source__active=True
    ).filter(
        Q(signal_kind='run_stop')
        | Q(
            source__connector_type__in=PUMPHOUSE_CONNECTOR_TYPES,
            external_key__regex=r'^(/pd/[^/]+/st|/st)$',
        )
    )


def propose_downtime(work, actor, start=None, end=None):
    """Require observed run→stop→run transitions without unknown boundaries."""
    from machine_health.services.trends import TrendError, read_trend

    # O24: shared Work does not carry its machine's telemetry. Run/stop
    # samples are machine health, so they need the machine's client scope.
    if work.machine_id and not work_machine_authorized(actor, work):
        return {
            'proposals': [],
            'reason': 'Machine telemetry is outside your machine scope.',
        }
    candidates = list(_status_bindings(work)) if work.machine_id else []
    if len(candidates) != 1:
        return {'proposals': [], 'reason': 'No unambiguous run/stop binding recorded.'}
    binding = candidates[0]
    try:
        trend = read_trend(work.machine, binding_id=binding.pk, start=start, end=end)
    except TrendError as exc:
        raise ValidationError(str(exc)) from exc
    if (
        not trend.get('available')
        or trend.get('truncated')
        or trend.get('display_shifted')
    ):
        return {
            'proposals': [],
            'reason': 'Unshifted, complete run/stop telemetry not available for this window.',
        }
    proposals = []
    stopped = previous = previous_value = None
    from machine_health.connectors.base import EXPECTED_SAMPLE_INTERVAL_SECONDS

    cadence = (
        trend.get('cadence_seconds')
        or (trend.get('limits') or {}).get('expected_sample_interval_seconds')
        or EXPECTED_SAMPLE_INTERVAL_SECONDS
    )
    budget = min(binding.source.freshness_threshold_seconds, cadence * 1.5)
    for sample in trend['samples']:
        stamp = serializers.DateTimeField().run_validation(sample['observed_at'])
        value = sample.get('plot_value')
        good = sample.get('quality') == 'good' and value in (0.0, 1.0)
        if previous is not None and (
            stamp <= previous or (stamp - previous).total_seconds() > budget
        ):
            stopped = None
            previous_value = None
        if not good:
            stopped = None
        elif value == 0 and stopped is None and previous_value == 1:
            stopped = stamp
        elif value == 1 and stopped is not None:
            if stamp > stopped:
                from assets.recorded_facts import binding_signature

                proposal = {
                    'binding_signature': binding_signature(binding),
                    'work': work.pk,
                    'machine': work.machine_id,
                    'actor': actor.pk,
                    'binding_id': binding.pk,
                    'started_at': stopped.isoformat(),
                    'ended_at': stamp.isoformat(),
                    'window_start': trend['window_start'],
                    'window_end': trend['window_end'],
                    'source_id': binding.source_id,
                    'external_key': binding.external_key,
                    'telemetry_hash': _canonical_hash(
                        'downtime_telemetry', actor, trend
                    ),
                }
                proposals.append({
                    **proposal,
                    'token': signing.dumps(proposal, salt='o15-downtime'),
                    'signal': binding.display_name,
                })
            stopped = None
        previous = stamp
        previous_value = value if good else None
    return {
        'proposals': proposals,
        'reason': None
        if proposals
        else 'No complete stop-to-run window recorded; gaps and open windows remain unknown.',
    }


@transaction.atomic
def command(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    operation,
    fields,
    reconcile_only=False,
):
    """Reauthorize and serialize an exact intent before versioned recording."""
    actor = _change_actor(actor)
    from django.contrib.auth import get_user_model

    # Start and supervisor correction serialize on the affected person, even
    # when the supervisor is correcting someone else's Work.
    person_id = actor.pk
    if (
        operation == 'correct'
        and isinstance(fields, dict)
        and type(fields.get('interval_id')) is int
    ):
        person_id = (
            WorkTimeInterval.objects
            .filter(pk=fields['interval_id'], work_order_id=work_order_id)
            .values_list('person_id', flat=True)
            .first()
            or actor.pk
        )
    get_user_model().objects.select_for_update().get(pk=person_id)
    work = _locked_work_order(work_order_id)
    _require_scope(actor, work)
    if operation == 'downtime' and not work_machine_authorized(actor, work):
        raise ValidationError('Machine telemetry is outside your machine scope.')
    if operation == 'correct' and not check_user_role(actor, 'admin', 'change'):
        raise PermissionDenied('Supervisor (admin change) permission required.')
    if (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 128
    ):
        raise ValidationError('A stable idempotency key is required.')
    if (
        not idempotency_key.startswith(f'{actor.pk}:')
        or not idempotency_key.split(':', 1)[1]
    ):
        raise ValidationError(
            'The recording key must be prefixed with the current actor id.'
        )
    if not isinstance(fields, dict):
        raise ValidationError('Command fields required.')
    digest = _canonical_hash(
        'work_time',
        actor,
        {
            'operation': operation,
            'fields': fields,
            'expected_version': expected_version,
        },
    )
    replay = _replay_or_none(work, idempotency_key, digest)
    if replay:
        if operation == 'downtime':
            # Exact committed retries need no fresh proposal age, but must
            # retain the original authorized machine and binding after edits.
            try:
                proposal = signing.loads(fields.get('token'), salt='o15-downtime')
            except (signing.BadSignature, TypeError) as exc:
                raise ValidationError(
                    'The saved telemetry origin is unavailable.'
                ) from exc
            from assets.health_models import MachineSignalBinding

            binding_id = (
                proposal.get('binding_id') if isinstance(proposal, dict) else None
            )
            if (
                not _downtime_origin_matches(work, proposal, binding_id)
                or proposal.get('actor') != actor.pk
                or not MachineSignalBinding.objects.filter(
                    pk=proposal['binding_id'], machine_id=work.machine_id
                ).exists()
            ):
                raise ValidationError('The saved telemetry origin is unavailable.')
        return redact_machine_telemetry(replay.metadata, work, actor)
    if reconcile_only:
        result = {
            'record_command_outcome': 'not_committed',
            'idempotency_key': idempotency_key,
            'version': work.lifecycle_version,
        }
        _append_result(
            work_order=work,
            actor=actor,
            command='work_time',
            event_type='WORK_TIME_NOT_APPLIED',
            from_status=work.lifecycle_status,
            to_status=work.lifecycle_status,
            reason='Original operation resolved without applying it.',
            correlation_id=uuid.uuid4(),
            idempotency_key=idempotency_key,
            request_hash=digest,
            result_metadata=result,
        )
        return result
    if type(expected_version) is not int:
        raise ValidationError('Integer expected Work version required.')
    _require_version(work, expected_version)
    now = timezone.now()
    intervals = list(WorkTimeInterval.objects.filter(work_order=work))
    replaced = {r.supersedes_id for r in intervals if r.supersedes_id}
    current = [r for r in intervals if r.pk not in replaced]
    open_rows = [r for r in current if r.person_id == actor.pk and r.ended_at is None]
    if operation in {'start', 'pause', 'stop'}:
        if work.lifecycle_status in {'completed', 'canceled'}:
            raise ValidationError(
                'Work is closed; an open interval requires supervisor correction.'
            )
        if fields:
            raise ValidationError(
                'Work clock commands do not accept manual time or activity fields.'
            )
        if operation == 'start':
            if WorkTimeInterval.objects.filter(
                person=actor, ended_at__isnull=True, replacement__isnull=True
            ).exists() or work.lifecycle_status in {'completed', 'canceled'}:
                raise ValidationError('An interval is already open or Work is closed.')
            WorkTimeInterval.objects.create(
                work_order=work, person=actor, started_at=now, recorded_by=actor
            )
        else:
            if len(open_rows) != 1 or now <= open_rows[0].started_at:
                raise ValidationError('No open interval for this person.')
            row = open_rows[0]
            row.ended_at, row.end_action, row.ended_by, row.ended_recorded_at = (
                now,
                operation,
                actor,
                now,
            )
            row.save(
                update_fields=[
                    'ended_at',
                    'end_action',
                    'ended_by',
                    'ended_recorded_at',
                ]
            )
    elif operation == 'correct':
        if (
            set(fields) != {'interval_id', 'started_at', 'ended_at', 'reason'}
            or type(fields.get('interval_id')) is not int
        ):
            raise ValidationError(
                'Provide interval_id, started_at, ended_at and correction reason.'
            )
        row = next((r for r in current if r.pk == fields['interval_id']), None)
        if row is None:
            raise ValidationError('Only a current interval can be corrected.')
        start = record_instant(fields['started_at'])
        end = record_instant(fields['ended_at'])
        reason = serializers.CharField(max_length=4000).run_validation(fields['reason'])
        if (
            start < work.created_at
            or end <= start
            or end > now
            or (closure_time(work) is not None and end > closure_time(work))
            or WorkTimeInterval.objects
            .filter(
                person_id=row.person_id, replacement__isnull=True, started_at__lt=end
            )
            .exclude(pk=row.pk)
            .filter(Q(ended_at__gt=start) | Q(ended_at__isnull=True))
            .exists()
        ):
            raise ValidationError(
                'Correction must be within Work, non-overlapping, and in the past.'
            )
        WorkTimeInterval.objects.create(
            work_order=work,
            person_id=row.person_id,
            started_at=start,
            ended_at=end,
            end_action=row.end_action,
            recorded_by=actor,
            reason=reason,
            supersedes=row,
        )
    elif operation == 'downtime':
        if (
            set(fields) - {'token', 'started_at', 'ended_at', 'reason', 'supersedes'}
            or 'token' not in fields
        ):
            raise ValidationError('A telemetry proposal is required.')
        try:
            proposal = signing.loads(fields['token'], salt='o15-downtime', max_age=900)
        except (signing.BadSignature, TypeError) as exc:
            raise ValidationError(
                'Proposal expired or invalid; read telemetry again.'
            ) from exc
        if (
            proposal['work'] != work.pk
            or proposal['actor'] != actor.pk
            or proposal['machine'] != work.machine_id
            or not _status_bindings(work)
            .filter(
                pk=proposal['binding_id'],
                source_id=proposal['source_id'],
                external_key=proposal['external_key'],
            )
            .exists()
        ):
            raise ValidationError(
                'Proposal does not match current Work, actor or binding.'
            )
        from assets.recorded_facts import binding_signature

        binding = (
            _status_bindings(work)
            .select_for_update(of=('self',))
            .filter(pk=proposal['binding_id'])
            .first()
        )
        if binding is None or proposal.get('binding_signature') != binding_signature(
            binding
        ):
            raise ValidationError('The run/stop binding changed; read telemetry again.')
        start = record_instant(fields.get('started_at', proposal['started_at']))
        end = record_instant(fields.get('ended_at', proposal['ended_at']))
        reason = serializers.CharField(
            max_length=4000, allow_blank=True
        ).run_validation(fields.get('reason', ''))
        corrected = (
            start.isoformat() != proposal['started_at']
            or end.isoformat() != proposal['ended_at']
            or fields.get('supersedes') is not None
        )
        if end <= start or end > now or (corrected and not reason.strip()):
            raise ValidationError(
                'A corrected window requires a reason and valid past times.'
            )
        original = None
        if fields.get('supersedes') is not None:
            original = WorkDowntime.objects.filter(
                pk=fields['supersedes'],
                work_order=work,
                binding_id=proposal['binding_id'],
                replacement__isnull=True,
            ).first()
            if original is None or not _downtime_origin_matches(
                work, original.proposal, original.binding_id
            ):
                raise ValidationError('Downtime original is no longer current.')
        elif WorkDowntime.objects.filter(work_order=work, proposal=proposal).exists():
            raise ValidationError(
                'This telemetry window is already recorded; correct the original.'
            )
        overlaps = WorkDowntime.objects.filter(
            work_order=work,
            binding_id=proposal['binding_id'],
            replacement__isnull=True,
            started_at__lt=end,
            ended_at__gt=start,
        )
        if original is not None:
            overlaps = overlaps.exclude(pk=original.pk)
        if overlaps.exists():
            raise ValidationError(
                'A confirmed stop window already overlaps this proposal; correct its original record.'
            )
        WorkDowntime.objects.create(
            work_order=work,
            binding_id=proposal['binding_id'],
            started_at=start,
            ended_at=end,
            proposal=proposal,
            recorded_by=actor,
            reason=reason,
            supersedes=original,
        )
    else:
        raise ValidationError('Unsupported time command.')
    work.lifecycle_version += 1
    work.save(update_fields=['lifecycle_version', 'updated_at'])
    result = {
        'version': work.lifecycle_version,
        'time': time_projection(work, now),
        'downtime': downtime_projection(work, actor),
    }
    _append_result(
        work_order=work,
        actor=actor,
        command='work_time',
        event_type='WORK_TIME_RECORDED',
        from_status=work.lifecycle_status,
        to_status=work.lifecycle_status,
        reason=fields.get('reason', ''),
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=digest,
        result_metadata=result,
    )
    return result
