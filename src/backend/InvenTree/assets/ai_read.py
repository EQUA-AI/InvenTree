"""The one authorized way an AI rail may read an asset.

Two very different surfaces need machine data: the record-pinned scoped chat
panel (``aichat``) and the unscoped voice / assistant tool surface
(``ai.core``). Both go through this module, and neither is allowed its own
reader, because the interesting part of "show the AI what is on the machine
page" is not the projection -- it is the authorization, and duplicating that
is how the two rails would drift apart.

Three rules shape everything here:

**The page is not the source.** Nothing the browser rendered is trusted or
reused; every field is re-read from the ORM under the acting user's authority
on every call. Browser content is confined to ``untrusted_content`` by
``ai.core.trusted_context`` and may never select a record.

**The REST layer is not the source either.** ``assets.api`` and
``machine_health.api`` authorize on the ``work_order`` role with no
customer/client narrowing at all (``AssetMachine.objects.all()``, bare
``get_object_or_404``). A tool proxying those endpoints would inherit a
cross-tenant read straight into a prompt. Everything below reads models
directly, behind ``tasks.scope.require_machine_scope``.

**Projections are allow-lists, not filters.** Each returns a literal dict of
named fields, so a column added to a model later is invisible here until
somebody decides it should be visible. The deliberate exclusions are listed in
``EXCLUDED_FIELDS`` and pinned by tests -- notably ``HealthSource.secret_ref``
/ ``config``, ``MachineSignalBinding.external_key`` / ``transform`` (the
tag-injection boundary: a model that can read a tag string could feed one back
to a connector), raw signal payloads, evidence sample arrays, and every
free-text note field.

Operator- and machine-authored free text is real prompt-injection surface --
``MachineAnomaly.evidence_summary`` in particular is ingested verbatim from
webhook alarm messages. Such values are wrapped with
``ai.core.tools.diagnostics``' fence markers, with marker escaping so stored
text cannot forge a boundary.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from assets.health_models import (
    ACTIVE_ANOMALY_STATUSES,
    HealthEvidenceSnapshot,
    MachineAnomaly,
)
from assets.models import AssetMachine, AssetMaintenanceRecord, MachinePart

#: Bounds on every list projection. A prompt gets a readable page, not a table
#: dump, and no single call can pull the database into the context window.
MAX_SEARCH_RESULTS = 25
MAX_PARTS = 50
MAX_MAINTENANCE_RECORDS = 25
MAX_ANOMALIES = 25
MAX_ATTACHMENTS = 50
MAX_SIGNALS = 50
MAX_TEXT_CHARS = 2000

#: Fields deliberately withheld from every projection, with the reason. Pinned
#: by ``test_ai_read.py`` so removing an exclusion has to be a decision.
EXCLUDED_FIELDS = {
    'HealthSource.secret_ref': 'credential pointer',
    'HealthSource.config': 'connector configuration, may embed endpoints/credentials',
    'HealthSource.connector_type': 'internal integration topology',
    'HealthSource.site_key': 'deployment boundary identifier',
    'MachineSignalBinding.external_key': 'tag-injection boundary',
    'MachineSignalBinding.transform': 'tag-injection boundary',
    'MachineSignalState.value.raw': 'raw connector payload',
    'MachineSignalState.payload_hash': 'internal integrity token',
    'MachineAnomaly.external_id': 'source-system identifier',
    'MachineAnomaly.fingerprint': 'internal dedupe token',
    'MachineAnomaly.acknowledgement_note': 'free-text operator note',
    'MachineAnomaly.resolution_note': 'free-text operator note',
    'MachinePart.notes': 'free-text note (a visible column, withheld on purpose)',
    'AssetMaintenanceRecord.details': 'free-text note (a visible column, withheld)',
    'HealthEvidenceSnapshot.samples': 'raw sample array',
    'HealthEvidenceSnapshot.source_references': 'source-system identifiers',
    'Attachment.attachment': 'file body / storage path',
    'Attachment.link': 'external URL',
    'Attachment.upload_user': 'uploader identity',
    'Client.code': 'scope-token identifier',
    'Client.name': 'system-only tenant identity, never rendered',
}


def machine_ai_read_enabled() -> bool:
    """Whether AI rails may read assets at all (fail closed).

    Checked here rather than only at catalog-build time because the capability
    invocation guard is attached to exactly one workflow
    (``wf8_lookup``); a flag enforced only there would not be a kill switch on
    the other rails. Enforcing it at the single shared reader makes it one.
    """
    return bool(getattr(settings, 'AIMMS_MACHINE_AI_READ_ENABLED', False))


#: Fence markers, deliberately byte-identical to the ones the diagnostics rail
#: uses (``ai.core.tools.diagnostics``) so a model sees one convention across
#: every AIMMS surface. They are redeclared rather than imported: this module
#: is loaded by the ``assets`` app, and reaching into ``ai.core`` for two string
#: constants would couple a Django app to the AI package -- and to its separate
#: test settings, which do not install ``assets`` at all.
UNTRUSTED_CONTENT_BEGIN = '[UNTRUSTED-CONTENT-BEGIN]'
UNTRUSTED_CONTENT_END = '[UNTRUSTED-CONTENT-END]'
_ESCAPED_UNTRUSTED_MARKER = '[UNTRUSTED-CONTENT-MARKER-ESCAPED]'


def fence(value: str | None, *, limit: int = MAX_TEXT_CHARS) -> str:
    """Wrap stored free text so a model reads it as data, never instructions.

    Escaping the markers first is what stops stored text from closing the fence
    and continuing as if it were trusted -- the whole point of the boundary.
    Empty input returns an empty string rather than an empty fence, so absent
    values do not read as present-but-blank.
    """
    text = (value or '').strip()
    if not text:
        return ''
    if len(text) > limit:
        text = f'{text[:limit]}…'
    text = text.replace(UNTRUSTED_CONTENT_BEGIN, _ESCAPED_UNTRUSTED_MARKER)
    text = text.replace(UNTRUSTED_CONTENT_END, _ESCAPED_UNTRUSTED_MARKER)
    return f'{UNTRUSTED_CONTENT_BEGIN}\n{text}\n{UNTRUSTED_CONTENT_END}'


def _iso(value) -> str | None:
    """Render a datetime/date for a prompt without assuming a timezone."""
    return value.isoformat() if value else None


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def authorized_machine(user, machine_id) -> AssetMachine | None:
    """Load one machine the actor is authorized for, or ``None``.

    ``None`` covers "no such machine", "not yours" and "feature off" alike:
    the caller cannot distinguish them, so no listing or error message
    discloses that an asset exists outside the actor's scope. An id supplied
    by a model is a *candidate*, never a grant -- authorization is re-derived
    here on every call rather than carried from a previous one.
    """
    if not machine_ai_read_enabled():
        return None
    if not getattr(user, 'is_authenticated', False):
        return None

    from tasks.scope import ScopeError, require_machine_scope

    try:
        pk = int(machine_id)
    except (TypeError, ValueError):
        return None

    machine = AssetMachine.objects.filter(pk=pk).first()
    if machine is None:
        return None
    try:
        require_machine_scope(user, machine)
    except ScopeError:
        return None
    return machine


def machines_in_scope(user, *, query: str | None = None, limit: int = 10):
    """Return the actor's machines, optionally narrowed by a search term.

    This is what makes a spoken machine name usable. The name is only a hint:
    the authority is ``machine_scope_filter``, so an unmatched or foreign name
    yields nothing rather than reaching another tenant's asset.
    """
    if not machine_ai_read_enabled():
        return []
    if not getattr(user, 'is_authenticated', False):
        return []

    from tasks.scope import ScopeError, machine_scope_filter

    try:
        predicate = machine_scope_filter(user)
    except ScopeError:
        return []

    rows = AssetMachine.objects.filter(predicate)
    if query:
        from django.db.models import Q

        term = str(query).strip()[:100]
        if term:
            rows = rows.filter(
                Q(name__icontains=term)
                | Q(serial__icontains=term)
                | Q(model__icontains=term)
                | Q(manufacturer__icontains=term)
                | Q(location__icontains=term)
            )
    bounded = max(1, min(int(limit or 10), MAX_SEARCH_RESULTS))
    return list(rows.order_by('name')[:bounded])


# ---------------------------------------------------------------------------
# Projections -- one per machine-page tab
# ---------------------------------------------------------------------------


def machine_identity(machine) -> dict[str, Any]:
    """The Details tab: what this asset is and where it lives.

    The client is a system-only scope identity -- neither its name nor its
    code belongs in a prompt, the same way the code already stayed out of
    every projection.
    """
    return {
        'machine_id': machine.pk,
        'name': fence(machine.name, limit=255),
        'active': machine.active,
        'manufacturer': fence(machine.manufacturer, limit=255),
        'model': fence(machine.model, limit=255),
        'serial': fence(machine.serial, limit=255),
        'location': fence(machine.location, limit=255),
        'description': fence(machine.description),
        'commissioned_at': _iso(machine.created_at),
        'last_updated_at': _iso(machine.updated_at),
    }


def machine_search_row(machine) -> dict[str, Any]:
    """A disambiguating identity line for name resolution.

    ``location`` is present because "the pump in bay 4" is how the floor
    disambiguates two similarly named assets, and a spoken name is the least
    precise selector this feature has.
    """
    return {
        'machine_id': machine.pk,
        'name': fence(machine.name, limit=255),
        'location': fence(machine.location, limit=255),
        'manufacturer': fence(machine.manufacturer, limit=255),
        'model': fence(machine.model, limit=255),
        'serial': fence(machine.serial, limit=255),
        'active': machine.active,
    }


def machine_health(machine) -> dict[str, Any]:
    """The Health tab header: current condition, freshness and source status.

    Source rows keep ``last_success_at`` / ``last_error_at`` because "the data
    looks stale, when did the connector last work" is the degraded-data
    question this blade exists to answer. ``last_error_code`` is a redacted
    classification, never a provider message.
    """
    from machine_health.services.summary import health_summary

    summary = health_summary(machine)
    return {
        'state': summary['state'],
        'configured': summary['configured'],
        'signal_count': summary['signal_count'],
        'stale_signal_count': summary['stale_signal_count'],
        'degraded_data': summary['degraded_data'],
        'last_observed_at': _iso(summary['last_observed_at']),
        'anomaly_counts': summary['anomaly_counts'],
        'active_anomaly_count': summary['active_anomaly_count'],
        'sources': [
            {
                'source_id': row['source_id'],
                'name': fence(row['name'], limit=200),
                'source_type': row['source_type'],
                'active': row['active'],
                'healthy': row['healthy'],
                'last_success_at': _iso(row['last_success_at']),
                'last_error_at': _iso(row['last_error_at']),
                'last_error_code': row['last_error_code'],
                'mapped_tag_count': row['mapped_tag_count'],
            }
            for row in summary['sources']
        ],
    }


def machine_signals(machine) -> dict[str, Any]:
    """The Health tab signal table: every mapped reading with its freshness.

    A signal ``value`` is whatever the source published, so a non-numeric value
    is fenced; a numeric one is passed through as a number because that is what
    makes it comparable against the limits alongside it.
    """
    from machine_health.services.summary import signal_rows

    rows = signal_rows(machine)[:MAX_SIGNALS]
    signals = []
    for row in rows:
        value = row['value']
        signals.append({
            'binding_id': row['binding_id'],
            'display_name': fence(row['display_name'], limit=200),
            'signal_kind': row['signal_kind'],
            'unit': fence(row['unit'], limit=32),
            'value': value
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else fence(str(value) if value is not None else '', limit=200) or None,
            'state': row['state'],
            'quality': row['quality'],
            'stale': row['stale'],
            'observed_at': _iso(row['observed_at']),
            'source_name': fence(row['source_name'], limit=200),
            'source_type': row['source_type'],
            'limits': row['limits'],
        })
    return {'signals': signals, 'total': len(signals)}


def machine_signal_trend(
    machine, *, binding_id: int, hours: int = 24
) -> dict[str, Any]:
    """A bounded history window for one mapped signal.

    "Is the temperature climbing" is the highest-value spoken question about a
    running asset and has no answer from current values alone. The binding is
    resolved against this machine inside ``read_trend``, so a binding id from
    another asset is not found rather than read, and an unavailable source
    reports why instead of synthesizing a line.
    """
    from django.utils import timezone

    from machine_health.services.trends import TrendError, read_trend

    window = max(1, min(int(hours or 24), 168))
    end = timezone.now()
    start = end - timezone.timedelta(hours=window)
    try:
        trend = read_trend(machine, binding_id=int(binding_id), start=start, end=end)
    except (TrendError, TypeError, ValueError) as exc:
        return {'available': False, 'reason': 'TREND_INVALID', 'detail': str(exc)}

    samples = trend.get('samples') or []
    numeric = [
        s.get('value')
        for s in samples
        if isinstance(s.get('value'), (int, float))
        and not isinstance(s.get('value'), bool)
    ]
    return {
        'binding_id': trend['binding_id'],
        'display_name': fence(trend['display_name'], limit=200),
        'unit': fence(trend['unit'], limit=32),
        'window_start': trend['window_start'],
        'window_end': trend['window_end'],
        'available': trend.get('available', True),
        'reason': trend.get('reason'),
        'detail': trend.get('detail'),
        'sample_count': len(samples),
        # Aggregates rather than the raw series: enough to answer "climbing?"
        # without pouring a historian window into the context.
        'first_value': numeric[0] if numeric else None,
        'last_value': numeric[-1] if numeric else None,
        'min_value': min(numeric) if numeric else None,
        'max_value': max(numeric) if numeric else None,
    }


def machine_anomalies(
    machine, *, include_resolved: bool = False, limit: int = 10
) -> dict[str, Any]:
    """The Health tab anomaly list: what is wrong, and what was wrong before.

    ``include_resolved`` is what makes "has this happened before / was it ever
    cleared" answerable; the page defaults to active alarms and so does this.
    ``metrics`` is included because the model documents it as bounded numeric
    context rather than a payload dump, and "peak 8.2 mm/s against a 6.0
    threshold" is the difference between naming an alarm and explaining it.
    """
    rows = MachineAnomaly.objects.filter(machine=machine).select_related(
        'source', 'acknowledged_by', 'work_order'
    )
    if not include_resolved:
        rows = rows.filter(status__in=[s.value for s in ACTIVE_ANOMALY_STATUSES])
    bounded = max(1, min(int(limit or 10), MAX_ANOMALIES))
    rows = rows.order_by('-last_observed_at')[:bounded]

    anomalies = []
    for anomaly in rows:
        metrics = anomaly.metrics if isinstance(anomaly.metrics, dict) else {}
        anomalies.append({
            'anomaly_id': anomaly.pk,
            'title': fence(anomaly.title, limit=255),
            # Ingested verbatim from webhook alarm messages: always fenced.
            'evidence_summary': fence(anomaly.evidence_summary),
            'severity': anomaly.severity,
            'status': anomaly.status,
            'alarm_code': fence(anomaly.alarm_code, limit=64),
            'detector': anomaly.detector,
            'metrics': {
                key: value
                for key, value in list(metrics.items())[:20]
                if isinstance(value, (int, float, str, bool, type(None)))
            },
            'first_observed_at': _iso(anomaly.first_observed_at),
            'last_observed_at': _iso(anomaly.last_observed_at),
            'acknowledged_at': _iso(anomaly.acknowledged_at),
            'acknowledged_by': anomaly.acknowledged_by.get_username()
            if anomaly.acknowledged_by_id
            else None,
            'resolved_at': _iso(anomaly.resolved_at),
            'source_name': fence(anomaly.source.name, limit=200)
            if anomaly.source_id
            else None,
            # The reference, not just a flag: "which job is already covering
            # this alarm" is the natural follow-up and the page links it.
            'work_order_reference': _visible_work_order_reference(anomaly.work_order),
            'has_repair_packet': anomaly.repair_packet_id is not None,
            'evidence_snapshot_count': HealthEvidenceSnapshot.objects.filter(
                anomaly=anomaly
            ).count(),
        })
    return {'anomalies': anomalies, 'total': len(anomalies)}


def _visible_work_order_reference(work_order) -> str | None:
    """Render a linked work order as its reference, or ``None``.

    Only the reference and title are ever exposed; the work order's own
    authority is not re-derived here because it belongs to the same machine
    and therefore the same scope that was already required.
    """
    if work_order is None:
        return None
    return work_order.reference or f'WO-{work_order.pk}'


def machine_installed_parts(machine, *, limit: int = MAX_PARTS) -> dict[str, Any]:
    """The Installed Parts tab: the bill of installed materials.

    ``part_id`` and ``ipn`` are included so the model can chain into the parts
    and stock tools -- "what spare does it take and do we have one" is the
    whole point, and a name-only row is a dead end. ``MachinePart.notes`` is a
    visible column that is deliberately withheld (see ``EXCLUDED_FIELDS``).
    """
    bounded = max(1, min(int(limit or MAX_PARTS), MAX_PARTS))
    rows = (
        MachinePart.objects
        .select_related('part')
        .filter(machine=machine)
        .order_by('part__name')
    )
    total = rows.count()
    parts = [
        {
            'part_id': row.part_id,
            'part_name': fence(row.part.name, limit=255),
            'ipn': fence(row.part.IPN or '', limit=100) or None,
            'quantity': row.quantity,
        }
        for row in rows[:bounded]
    ]
    return {'parts': parts, 'total': total, 'truncated': total > len(parts)}


def machine_maintenance_history(
    user, machine, *, limit: int = MAX_MAINTENANCE_RECORDS
) -> dict[str, Any]:
    """The Maintenance tab: what has been done to this asset.

    The linked work order is re-authorized per row *unconditionally*. The
    serializer that feeds the page only performs that check when
    ``AIMMS_WORK_ORDERS_ENABLED`` is on (``assets/serializers.py``); a prompt
    must not become the one surface where a flag being off exposes another
    tenant's job, so the flag does not gate the check here.
    """
    from tasks.scope import ScopeError, require_work_order_scope

    bounded = max(
        1, min(int(limit or MAX_MAINTENANCE_RECORDS), MAX_MAINTENANCE_RECORDS)
    )
    rows = (
        AssetMaintenanceRecord.objects
        .select_related('work_order')
        .filter(machine=machine)
        .order_by('-date')
    )
    total = rows.count()

    records = []
    for record in rows[:bounded]:
        work_order_reference = None
        work_order_title = None
        if record.work_order_id:
            try:
                require_work_order_scope(user, record.work_order)
            except ScopeError:
                work_order_reference = None
            else:
                work_order_reference = _visible_work_order_reference(record.work_order)
                work_order_title = fence(record.work_order.title, limit=255)
        records.append({
            'date': _iso(record.date),
            'summary': fence(record.summary, limit=255),
            # Operator-authored free text, so fenced like any other.
            'performed_by': fence(record.performed_by, limit=255),
            'work_order_reference': work_order_reference,
            'work_order_title': work_order_title,
        })
    return {'records': records, 'total': total, 'truncated': total > len(records)}


def machine_attachments(machine, *, limit: int = MAX_ATTACHMENTS) -> dict[str, Any]:
    """The Attachments tab: what documentation exists for this asset.

    Names and operator comments only. File bodies, storage paths, external
    URLs and uploader identity are all withheld -- the answer to "is the
    hydraulic schematic attached" is yes-or-no plus a label, not the document.

    ``Attachment`` exposes ``basename`` (not ``filename``), and it is ``None``
    for a link-only row, so each row is typed as ``file`` or ``link`` rather
    than rendering as an unnamed zero-byte entry.
    """
    from common.models import Attachment

    bounded = max(1, min(int(limit or MAX_ATTACHMENTS), MAX_ATTACHMENTS))
    rows = Attachment.objects.filter(
        model_type='assetmachine', model_id=machine.pk
    ).order_by('-upload_date', '-pk')
    total = rows.count()

    items = []
    for item in rows[:bounded]:
        basename = item.basename
        items.append({
            'kind': 'file' if basename else 'link',
            'name': fence(basename or '', limit=255) or None,
            # Usually the human label on the file ("hydraulic schematic rev C"),
            # which is the only thing that makes the row identifiable.
            'comment': fence(item.comment, limit=250),
            'is_image': item.is_image,
            'file_size': item.file_size,
            'uploaded_at': _iso(item.upload_date),
        })
    return {'attachments': items, 'total': total, 'truncated': total > len(items)}


def machine_overview(user, machine) -> dict[str, Any]:
    """Everything the machine page shows, in one call.

    Voice needs this: a hands-free turn cannot afford five round trips before
    it says anything. Lists are trimmed harder than their dedicated tools so
    the composite stays a briefing rather than a dump; the per-tab tools are
    how a caller drills in.
    """
    return {
        'identity': machine_identity(machine),
        'health': machine_health(machine),
        'signals': machine_signals(machine),
        'anomalies': machine_anomalies(machine, limit=5),
        'installed_parts': machine_installed_parts(machine, limit=10),
        'maintenance_history': machine_maintenance_history(user, machine, limit=5),
        'attachments': machine_attachments(machine, limit=10),
    }


__all__ = [
    'EXCLUDED_FIELDS',
    'authorized_machine',
    'fence',
    'machine_ai_read_enabled',
    'machine_anomalies',
    'machine_attachments',
    'machine_health',
    'machine_identity',
    'machine_installed_parts',
    'machine_maintenance_history',
    'machine_overview',
    'machine_search_row',
    'machine_signal_trend',
    'machine_signals',
    'machines_in_scope',
]
