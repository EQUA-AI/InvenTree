"""Verified closeout context, sharing the diagnostic reader's source policy.

Lazy PostgreSQL annotations batch provenance and latest applied amendments into
history SQL. Current scope/role/source content is rechecked in the same statement
as fact reauthorization. No episode store, embeddings or learned authority.
"""

import hashlib
import json

from django.conf import settings as django_settings
from django.contrib.postgres.expressions import ArraySubquery
from django.db import models
from django.db.models import Exists, F, OuterRef, Q, Subquery, Value
from django.db.models.fields.json import KeyTextTransform, KeyTransform
from django.db.models.functions import JSONObject, Left
from django.utils import timezone

from ai.core.analysis.scope import MODE_EXPLICIT, scope_from_stored
from ai.core.config import get_settings
from aichat.models import ChatThreadGrant, UserMemorySettings
from aichat.services import memory_recall
from assets.models import AssetMachine

LIMIT = 5
FIELD_LIMIT = 1200
DIAGNOSTIC_RESOLVER = (
    'repair.diagnostic_scope.single_site_diagnostic_capability_resolver'
)


def eligible(repository, thread_id):
    """Same authority as the shipped diagnostic resolver; custom resolvers deny."""
    from tasks.closeout_models import CloseoutAmendment

    from repair.services import similar_repair_candidates

    resolver = getattr(django_settings, 'AIMMS_DIAGNOSTIC_CAPABILITY_RESOLVER', None)
    if resolver is None:
        resolver = get_settings().diagnostic_capability_resolver
    if not memory_recall.available() or resolver != DIAGNOSTIC_RESOLVER:
        return None
    now = timezone.now()
    shared = (
        ChatThreadGrant.objects
        .filter(thread_id=thread_id, created_at__lte=now)
        .filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=now))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )
    thread = (
        repository
        ._threads()
        .filter(
            pk=thread_id,
            owner__is_active=True,
            analysis_scope__schema_version=1,
            analysis_scope__mode=MODE_EXPLICIT,
        )
        .filter(
            ~Exists(shared),
            ~Exists(
                UserMemorySettings.objects.filter(
                    user_id=repository.actor_id, opted_out=True
                )
            ),
        )
    )
    machines = (
        AssetMachine.objects
        .filter(
            active=True,
            client_id__in=memory_recall.authorized_clients(repository.actor_id).values(
                'pk'
            ),
        )
        .annotate(_scope=Subquery(thread.values('analysis_scope')[:1]))
        .filter(
            _scope__machine_ids__contains=models.Func(
                F('pk'), function='jsonb_build_array', output_field=models.JSONField()
            )
        )
    )
    amendments = CloseoutAmendment.objects.filter(
        closeout_id=OuterRef('work_order__structured_closeout__pk'), status='applied'
    ).order_by('-applied_at', '-pk')
    # Bound prose in SQL before it enters Python; the governed effective snapshot
    # supplies corrected fields, with a missing field falling back to the base.
    rows = similar_repair_candidates(machines).annotate(
        _scope=Subquery(thread.values('analysis_scope')[:1]),
        _amendment=Subquery(amendments.values('effective_snapshot')[:1]),
        _amendment_id=Subquery(amendments.values('pk')[:1]),
        _amendment_hash=Subquery(amendments.values('effective_snapshot_hash')[:1]),
    )
    fields = {}
    for name in ('cause', 'action', 'result', 'verification_summary'):
        # CASE distinguishes a missing key from an explicitly blank correction.
        fields[name] = Left(
            models.Case(
                models.When(
                    _amendment__closeout__has_key=name,
                    then=KeyTextTransform(
                        name, KeyTransform('closeout', F('_amendment'))
                    ),
                ),
                default=F(f'work_order__structured_closeout__{name}'),
                output_field=models.TextField(),
            ),
            FIELD_LIMIT + 1,
        )
    return rows.annotate(
        _payload=JSONObject(
            id=F('work_order__structured_closeout__pk'),
            work_order_id=F('work_order_id'),
            machine_id=F('machine_id'),
            client_code=F('machine__client__code'),
            version=F('work_order__structured_closeout__version'),
            content_hash=F('work_order__structured_closeout__content_hash'),
            verified_at=F('work_order__structured_closeout__verified_at'),
            amendment_id=F('_amendment_id'),
            amendment_hash=F('_amendment_hash'),
            analysis_scope=F('_scope'),
            **fields,
        )
    )


def annotation(repository, thread_id):
    """A bounded JSON array inside the existing history statement."""
    rows = eligible(repository, thread_id)
    if rows is None:
        return Value([], output_field=models.JSONField())
    return ArraySubquery(
        rows.order_by('-work_order__structured_closeout__verified_at', '-pk').values(
            '_payload'
        )[:LIMIT]
    )


def materialize(payloads):
    """Validate full stored scope and whole prose fields; abstain on overflow."""
    result = []
    for row in payloads[:LIMIT]:
        if (
            not isinstance(row, dict)
            or scope_from_stored(row.get('analysis_scope')).mode != MODE_EXPLICIT
        ):
            continue
        if any(
            not isinstance(row.get(key), str) or len(row[key]) > FIELD_LIMIT
            for key in ('cause', 'action', 'result', 'verification_summary')
        ):
            continue
        if row.get('amendment_id') and not row.get('amendment_hash'):
            continue
        result.append(row)
    return result


def fingerprint(row):
    """Include amended prose and source identity; a changed record is discarded."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(',', ':')).encode()
    ).hexdigest()


def reauthorize(repository, thread_id, facts, episodes):
    """One SQL UNION rechecks both source families, keeping GR-31 at three reads."""
    if not memory_recall.available():
        return [], []
    if len(facts) > 12 or len(episodes) > LIMIT:
        raise ValueError('memory_reauthorization_bounds')
    wanted = {str(row['id']): row['version'] for row in facts}
    fact_rows = (
        memory_recall
        ._eligible(repository, thread_id)
        .filter(pk__in=wanted)
        .annotate(
            _kind=Value('fact'),
            _payload=JSONObject(
                id=F('pk'), version=F('version'), scope=F('_analysis_scope')
            ),
        )
        .order_by()
        .values('_kind', '_payload')
    )
    rows = eligible(repository, thread_id)
    if rows is None:
        episodes = []
    if episodes:
        episode_rows = (
            rows
            .filter(
                work_order__structured_closeout__pk__in=[row['id'] for row in episodes]
            )
            .annotate(_kind=Value('episode'))
            .order_by()
            .values('_kind', '_payload')
        )
        query = fact_rows.union(episode_rows, all=True)
    else:
        query = fact_rows
    allowed_facts, allowed_episodes = set(), set()
    for row in query:
        payload = row['_payload']
        if row['_kind'] == 'fact':
            from ai.core.analysis.scope import MODE_LEGACY

            if (
                scope_from_stored(payload['scope']).mode != MODE_LEGACY
                and wanted.get(str(payload['id'])) == payload['version']
            ):
                allowed_facts.add(str(payload['id']))
        elif materialize([payload]):
            allowed_episodes.add(fingerprint(payload))
    return (
        [row for row in facts if str(row['id']) in allowed_facts],
        [row for row in episodes if fingerprint(row) in allowed_episodes],
    )
