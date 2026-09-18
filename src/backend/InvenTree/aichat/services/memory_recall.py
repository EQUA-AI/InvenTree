"""Bounded PostgreSQL fact recall and immediate reauthorization.

All owner, role, client, consent, sharing, entity and lifecycle predicates are
inside each statement. The two shipped maintenance resolvers have an exact SQL
projection here; unknown/custom resolvers fail closed until explicitly adapted.
No provider call or write occurs in this service.
"""

import math
import uuid

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.db import connection, models
from django.db.models import Case, Exists, F, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Cast, Substr
from django.utils import timezone

from pgvector.django import CosineDistance

from ai.core.analysis.scope import MODE_LEGACY, scope_from_stored
from ai.core.config import get_settings
from aichat.models import (
    ChatThreadGrant,
    ChatThreadTombstone,
    ClientAISettings,
    ClientMemoryErasure,
    MemoryFact,
    MemoryFactClaim,
    MemoryFactTombstone,
    MemoryNoticeAcknowledgement,
    UserMemorySettings,
)
from aichat.services.memory_eligibility import notice_number
from assets.models import AssetMachine, Client, ClientScopeGrant
from InvenTree.restore_hold import restore_hold_enabled
from users.models import RuleSet

SUPPORTED_RESOLVERS = frozenset({
    'tasks.scope.single_site_scope_resolver',
    'tasks.scope.granted_client_scope_resolver',
})
MAX_FACTS = 8
MAX_PREFERENCES = 4
FIELDS = (
    'id',
    'owner_id',
    'version',
    'text',
    'text_lang',
    'client_code',
    'entity_kind',
    'entity_id',
    'memory_type',
    'topics',
    'verification_class',
    'shield_state',
    'classification',
    'last_verified_at',
    'source_thread_id',
    'source_message_id',
)


def _notice_number(field):
    # Cast only bounded, syntactically valid versions; malformed data cannot
    # abort a shared query or be interpreted as an acknowledged notice.
    return Case(
        When(
            **{f'{field}__regex': r'^memory-v[1-9][0-9]{0,7}$'},
            then=Cast(Substr(field, 9), models.IntegerField()),
        ),
        default=Value(0),
        output_field=models.IntegerField(),
    )


def _eligible(repository, thread_id):
    """Compose lazy SQL only; no evaluation or Python authorization side lookup."""
    from tasks.models import WorkOrder

    config = get_settings()
    now = timezone.now()
    actor_id = repository.actor_id
    clients = authorized_clients(actor_id)
    thread = repository._threads().filter(
        pk=thread_id, owner__is_active=True, analysis_scope__schema_version=1
    )
    shared = (
        ChatThreadGrant.objects
        .filter(thread_id=thread_id, created_at__lte=now)
        .filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=now))
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )
    thread = thread.filter(~Exists(shared)).filter(
        ~Exists(UserMemorySettings.objects.filter(user_id=actor_id, opted_out=True))
    )
    # An explicit scope contributes only currently owned selected machines.
    selected = (
        AssetMachine.objects
        .filter(client_id=OuterRef('pk'), client__active=True)
        .annotate(_thread_scope=Subquery(thread.values('analysis_scope')[:1]))
        .filter(
            _thread_scope__mode='explicit_assets',
            _thread_scope__machine_ids__contains=models.Func(
                F('pk'), function='jsonb_build_array', output_field=models.JSONField()
            ),
        )
    )
    all_scope = thread.filter(
        analysis_scope__mode='all_authorized_assets', analysis_scope__machine_ids=[]
    )
    clients = clients.filter(Q(Exists(all_scope)) | Q(Exists(selected)))
    current = notice_number(config.aimms_memory_notice_version)
    if current > 99_999_999:
        raise ValueError('memory_notice_unavailable')
    notices = (
        MemoryNoticeAcknowledgement.objects
        .filter(user_id=actor_id, acknowledged_at__lte=now)
        .annotate(_number=_notice_number('notice_version'))
        .filter(_number__gte=OuterRef('_minimum'), _number__lte=current)
    )
    enrollment = (
        ClientAISettings.objects
        .filter(client_id=OuterRef('pk'), memory_enabled=True)
        .annotate(_minimum=_notice_number('required_notice_version'))
        .filter(_minimum__gt=0, _minimum__lte=current)
        .filter(Exists(notices))
    )
    clients = clients.filter(Exists(enrollment)).exclude(
        code__in=ClientMemoryErasure.objects.values('client_code')
    )
    machines = AssetMachine.objects.annotate(
        _identity=Cast('pk', models.CharField())
    ).filter(
        _identity=OuterRef('entity_id'),
        client__code=OuterRef('client_code'),
        client__active=True,
    )
    orders = WorkOrder.objects.annotate(
        _identity=Cast('pk', models.CharField())
    ).filter(
        _identity=OuterRef('entity_id'),
        machine__client__code=OuterRef('client_code'),
        machine__client__active=True,
        customer__isnull=True,
    )
    entities = (
        Q(entity_kind='machine') & Q(Exists(machines))
        | Q(entity_kind='work_order') & Q(Exists(orders))
        | Q(entity_kind='client', entity_id=F('client_code'))
    )
    preferences = Q(
        memory_type='user_preference',
        entity_kind='user',
        entity_id=str(actor_id),
        client_code='',
        verification_class='user_confirmed',
        confirming_proposal__isnull=False,
        confirming_proposal__state='executed',
        confirming_proposal__owner_id=actor_id,
        confirming_proposal__target_memory_fact_id=F('pk'),
    )
    provenance = MemoryFactClaim.objects.filter(
        fact_id=OuterRef('pk'),
        source_class='tool_record',
        content_trust='trusted_record',
    )
    operational = (
        ~Q(memory_type='user_preference')
        & Q(client_code__in=clients.values('code'), verification_class='tool_verified')
        & entities
        & Q(Exists(provenance))
    )
    # The writer's keyed typed-slot gate prevents paraphrase revival. The read
    # path also excludes any deletion proof for this exact row, even if a stale
    # lifecycle field is resurrected independently of its tombstone.
    deleted = MemoryFactTombstone.objects.filter(owner_id=actor_id).filter(
        Q(fact_id=OuterRef('pk'))
        | (
            ~Q(reason='supersede')
            & Q(
                claim_fingerprint=OuterRef('claim_fingerprint'),
                deleted_at__gte=OuterRef('created_at'),
            )
        )
    )
    held_sources = ChatThreadTombstone.objects.filter(forget_confirmed_memories=True)
    held_claims = MemoryFactClaim.objects.filter(
        fact_id=OuterRef('pk'), source_thread_id__in=held_sources.values('thread_id')
    )
    fact_notice = (
        MemoryNoticeAcknowledgement.objects
        .filter(user_id=actor_id, acknowledged_at__lte=now)
        .annotate(_number=_notice_number('notice_version'))
        .filter(_number__gte=OuterRef('_fact_notice'), _number__lte=current)
    )
    return (
        MemoryFact.objects
        .filter(
            preferences | operational,
            Q(valid_until__isnull=True) | Q(valid_until__gt=now),
            owner_id=actor_id,
            lifecycle_state='active',
            valid_from__lte=now,
            last_verified_at__isnull=False,
            prohibited=False,
            shield_state__in=['clear', 'flagged'],
        )
        .annotate(_fact_notice=_notice_number('notice_version'))
        .filter(_fact_notice__gt=0)
        .exclude(source_thread_id__in=held_sources.values('thread_id'))
        .filter(~Exists(held_claims))
        .filter(Exists(thread), Exists(clients), ~Exists(deleted), Exists(fact_notice))
        .annotate(_analysis_scope=Subquery(thread.values('analysis_scope')[:1]))
    )


def available():
    """Flags and platform gate must precede any store query."""
    return (
        get_settings().feature_semantic_memory_recall
        and not restore_hold_enabled()
        and connection.vendor == 'postgresql'
    )


def candidates(repository, thread_id, recall_filter, *, query_vector=None):
    """One recall statement, prefiltered before the cosine candidate pool.

    A missing query vector still permits the bounded confirmed preference slot.
    Operational similarity recall is explicitly unavailable without a compatible
    server-generated vector; no lexical or recency ranking masquerades as cosine.
    """
    if not available():
        return []
    rows = _eligible(repository, thread_id)
    preferences = (
        rows
        .filter(memory_type='user_preference')
        .order_by('-last_verified_at', 'pk')
        .values('pk')[:MAX_PREFERENCES]
    )
    selection = Q(pk__in=Subquery(preferences))
    rank = Value(-1.0, output_field=models.FloatField())
    if query_vector is not None:
        if (
            len(query_vector) != 1536
            or any(
                type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 3.4e38
                for v in query_vector
            )
            or not any(query_vector)
        ):
            raise ValueError('memory_query_vector_invalid')
        profile = f'{get_settings().memory_embedding_deployment}:1536'
        operational = rows.filter(
            memory_type__in=recall_filter.memory_types,
            embedding__isnull=False,
            embedding_profile=profile,
        )
        nearest = operational.order_by(
            CosineDistance('embedding', query_vector)
        ).values('pk')[: MAX_FACTS * 3]
        boost = (
            get_settings().aimms_memory_topic_boost
            if recall_filter.type_filter_enabled
            else 0.0
        )
        distance = CosineDistance('embedding', query_vector) - Case(
            When(topics__overlap=list(recall_filter.boost_topics), then=Value(boost)),
            default=Value(0.0),
            output_field=models.FloatField(),
        )
        best = (
            operational
            .filter(pk__in=Subquery(nearest))
            .annotate(_distance=distance)
            .order_by('_distance', '-last_verified_at', 'pk')
            .values('pk')[:MAX_FACTS]
        )
        selection |= Q(pk__in=Subquery(best))
        rank = Case(
            When(memory_type='user_preference', then=Value(-1.0)),
            default=distance,
            output_field=models.FloatField(),
        )
    return _materialize(
        rows
        .filter(selection)
        .annotate(_rank=rank)
        .order_by('_rank', '-last_verified_at', 'pk')[: MAX_FACTS + MAX_PREFERENCES]
    )


def reauthorize(repository, thread_id, candidates):
    """One fresh statement; changed versions or authorization disappear together."""
    if not available() or not candidates:
        return []
    if len(candidates) > MAX_FACTS + MAX_PREFERENCES:
        raise ValueError('memory_reauthorization_bounds')
    wanted = {uuid.UUID(str(row['id'])): row['version'] for row in candidates}
    current = {
        row['id']: row
        for row in _materialize(_eligible(repository, thread_id).filter(pk__in=wanted))
    }
    return [
        current[identity]
        for identity, version in wanted.items()
        if identity in current and current[identity]['version'] == version
    ]


def _materialize(rows):
    """Also reject malformed stored scope fields using the shared full validator."""
    result = []
    for row in rows.values(*FIELDS, '_analysis_scope'):
        scope = scope_from_stored(row.pop('_analysis_scope'))
        if scope.mode != MODE_LEGACY:
            result.append(row)
    return result


def query_admission(repository, thread_id):
    """Lazy authorization annotation on the existing history statement."""
    if not available():
        return Value(False, output_field=models.BooleanField())
    profile = f'{get_settings().memory_embedding_deployment}:1536'
    return Exists(
        _eligible(repository, thread_id)
        .exclude(memory_type='user_preference')
        .filter(embedding__isnull=False, embedding_profile=profile)
    )


def authorized_clients(actor_id):
    """Lazy SQL counterpart of the two shipped maintenance scope resolvers."""
    resolver = getattr(django_settings, 'AIMMS_MAINTENANCE_SCOPE_RESOLVER', None)
    if resolver not in SUPPORTED_RESOLVERS:
        raise ValueError('memory_scope_resolver_unavailable')
    role = RuleSet.objects.filter(
        group__user__pk=actor_id, name='work_order', can_view=True
    )
    owner = (
        get_user_model()
        .objects.filter(pk=actor_id, is_active=True)
        .filter(Q(is_superuser=True) | Q(Exists(role)))
    )
    grants = ClientScopeGrant.objects.filter(user_id=actor_id, client__active=True)
    clients = Client.objects.filter(active=True).filter(Exists(owner))
    fallback = Q(
        code=getattr(django_settings, 'AIMMS_SINGLE_SITE_CLIENT_CODE', 'internal')
    )
    if resolver.endswith('granted_client_scope_resolver'):
        clients = clients.filter(
            Q(pk__in=grants.values('client_id')) | (fallback & ~Q(Exists(grants)))
        )
    else:
        clients = clients.filter(fallback)
    return clients
