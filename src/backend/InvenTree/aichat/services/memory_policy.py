"""Deterministic admission and native-field verification for durable memory.

Model metadata never grants authority. Source fields are re-read under current
record scope, and verified prose is rendered from that typed value server-side.
"""

import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from django.core.exceptions import ObjectDoesNotExist

from tasks.scope import ScopeError, require_machine_scope, require_work_order_scope

from ai.core.memory.scrub import classify_directive
from ai.core.redaction import entropy_flags, redact_text
from aichat.memory_choices import DurableMemoryType, MemoryTopic

MAX_FACT_CHARS = 2000
SUPPORTED_LANGUAGES = frozenset({'en', 'es', 'de', 'fr'})
# Every exported typed field is classified here. Everything outside these
# projections is excluded, including names, serials, titles and closeout prose.
MEMORY_VERIFIABLE_FIELDS = {
    'assets.AssetMachine': frozenset({'id', 'client_id', 'active', 'created_at'}),
    'tasks.WorkOrder': frozenset({
        'id',
        'machine_id',
        'lifecycle_status',
        'work_order_type',
        'priority',
        'is_active',
        'scheduled_start',
        'scheduled_end',
        'actual_started_at',
        'actual_completed_at',
        'estimated_minutes',
    }),
}
MEMORY_EXCLUDED_FIELDS = {
    'assets.AssetMachine': frozenset({
        'name',
        'description',
        'location',
        'manufacturer',
        'model',
        'serial',
        'profile',
    }),
    'tasks.WorkOrder': frozenset({
        'title',
        'description',
        'status',
        'assignee',
        'tags',
        'company',
        'company_contact_name',
        'company_contact_phone',
        'hold_reason',
    }),
}
_TYPED_SETTING_WORDS = re.compile(
    r'\b(language|locale|verbosity|verbose|concise|units?|celsius|fahrenheit|'
    r'idioma|lenguaje|unidades|sprache|einheiten|langue|unites)\b',
    re.IGNORECASE,
)
_SENSITIVE_WORDS = re.compile(
    r'\b(health|injur\w*|diagnos\w*|medical|disabil\w*|religio\w*|'
    r'union membership|home address|lives at|disciplin\w*|password|passcode|'
    r'mfa|otp|secret|credential\w*|salud|lesion\w*|domicilio|sindicat\w*|'
    r'contrasena|krank\w*|verletz\w*|gewerkschaft\w*|wohnadresse|passwort|'  # codespell:ignore adresse
    r'sante|blessur\w*|adresse personnelle|mot de passe)\b',  # codespell:ignore adresse,mot
    re.IGNORECASE,
)
_ACTION_DIRECTIVE = re.compile(
    r'\b(always|automatically|never|siempre|automaticamente|immer|automatisch|'
    r'toujours|automatiquement)\b.{0,90}\b(approv\w*|purchas\w*|execut\w*|'  # codespell:ignore execut
    r'delet\w*|bypass\w*|aprob\w*|compr\w*|genehmig\w*|kauf\w*|'
    r'approuv\w*|achat\w*|supprim\w*)\b',
    re.IGNORECASE | re.DOTALL,
)
_FACTORY_LOCK = threading.Lock()


class MemoryPolicyError(ValueError):
    """Content-free reason code suitable for rejection counts."""


@dataclass(frozen=True)
class VerifiedValue:
    """Native record identity, authorized client and canonical scalar."""

    source_model: str
    source_id: int
    source_field: str
    client_code: str
    entity_kind: str
    value: bool | int | str


def _normalized(text):
    return ''.join(
        char
        for char in unicodedata.normalize('NFKD', text.casefold())
        if not unicodedata.combining(char)
    )


@lru_cache(maxsize=1)
def _language_factory():
    # Instance-local seed avoids mutating another caller's detector. Sorting
    # packaged profiles makes ordering stable across filesystem/image layouts.
    from langdetect.detector_factory import PROFILES_DIRECTORY, DetectorFactory

    with _FACTORY_LOCK:
        factory = DetectorFactory()
        factory.set_seed(0)
        factory.load_json_profile([
            path.read_text(encoding='utf-8')
            for path in sorted(Path(PROFILES_DIRECTORY).iterdir())
            if path.is_file() and not path.name.startswith('.')
        ])
        return factory


def language_matches(text, locale):
    """Ambiguity, missing profiles/dependency and mismatches all refuse admission."""
    if locale not in SUPPORTED_LANGUAGES:
        return False
    try:
        detector = _language_factory().create()
        detector.append(text)
        probabilities = detector.get_probabilities()
        return bool(
            probabilities
            and probabilities[0].lang == locale
            and probabilities[0].prob >= 0.90
        )
    except Exception:
        return False


def validate_candidate(candidate, *, locale):
    """Validate untrusted candidate shape/content before any provider call.

    The caller supplies locale from resolve_actor_locale, never the model. This
    finite deny policy is defense in depth, not a semantic privacy classifier;
    red-team and human adjudication remain required release gates.
    """
    allowed = {
        'text',
        'slot_key',
        'memory_type',
        'topics',
        'classification',
        'prohibited',
        'entity_kind',
        'entity_id',
        'source_model',
        'source_id',
        'source_field',
    }
    required = {
        'text',
        'slot_key',
        'memory_type',
        'topics',
        'classification',
        'prohibited',
        'entity_kind',
        'entity_id',
    }
    if (
        not isinstance(candidate, dict)
        or set(candidate) - allowed
        or not required <= set(candidate)
    ):
        raise MemoryPolicyError('candidate_shape')
    if any(
        not isinstance(candidate[key], str)
        for key in ('memory_type', 'classification', 'entity_kind')
    ):
        raise MemoryPolicyError('candidate_shape')
    if any(
        key in candidate and not isinstance(candidate[key], str)
        for key in ('source_model', 'source_field')
    ):
        raise MemoryPolicyError('candidate_shape')
    if 'source_id' in candidate and (
        type(candidate['source_id']) is not int or candidate['source_id'] < 1
    ):
        raise MemoryPolicyError('candidate_shape')
    text = candidate['text']
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_FACT_CHARS:
        raise MemoryPolicyError('candidate_text')
    if type(candidate['prohibited']) is not bool or candidate['prohibited']:
        raise MemoryPolicyError('prohibited')
    kind = candidate['memory_type']
    classification = candidate['classification']
    if kind not in DurableMemoryType.values or classification not in {
        'preference',
        'operational',
        'personal',
    }:
        raise MemoryPolicyError('candidate_classification')
    preference = kind == 'user_preference'
    if preference != (classification == 'preference') or (
        classification == 'personal' and kind != 'contact_role'
    ):
        raise MemoryPolicyError('candidate_classification')
    topics = candidate['topics']
    if (
        not isinstance(topics, list)
        or len(topics) > 3
        or any(
            not isinstance(topic, str) or topic not in MemoryTopic.values
            for topic in topics
        )
        or len(set(topics)) != len(topics)
    ):
        raise MemoryPolicyError('candidate_topics')
    slot = candidate['slot_key']
    if not isinstance(slot, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,127}', slot):
        raise MemoryPolicyError('candidate_slot')
    entity = candidate['entity_kind']
    entity_id = candidate['entity_id']
    if (
        entity not in {'user', 'client', 'machine', 'work_order'}
        or preference != (entity == 'user')
        or not isinstance(entity_id, str)
        or not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', entity_id)
    ):
        raise MemoryPolicyError('candidate_entity')
    if any(unicodedata.category(char) in {'Cf', 'Cs'} for char in text):
        raise MemoryPolicyError('candidate_text')
    normalized = _normalized(text)
    redacted, counts = redact_text(text)
    if counts or redacted != text or '[REDACTED:' in text or entropy_flags(text):
        raise MemoryPolicyError('sensitive_content')
    if _SENSITIVE_WORDS.search(normalized):
        raise MemoryPolicyError('sensitive_content')
    if classify_directive(text) or _ACTION_DIRECTIVE.search(normalized):
        raise MemoryPolicyError('directive_content')
    if preference and (
        _TYPED_SETTING_WORDS.search(normalized)
        or slot.split('.')[0] in {'language', 'locale', 'units', 'verbosity'}
    ):
        raise MemoryPolicyError('typed_setting_required')
    if not language_matches(text, locale):
        raise MemoryPolicyError('language_mismatch')
    result = dict(candidate)
    result['text'] = text.strip()
    return result


def verify_native_field(
    actor, *, source_model, source_id, source_field, eligible_clients
):
    """Re-read allow-listed typed values; arbitrary tool JSON is not accepted."""
    if (
        not isinstance(source_model, str)
        or not isinstance(source_field, str)
        or source_field not in MEMORY_VERIFIABLE_FIELDS.get(source_model, ())
        or type(source_id) is not int
        or source_id < 1
    ):
        raise MemoryPolicyError('unverified_field')
    from tasks.models import WorkOrder

    from assets.models import AssetMachine

    try:
        if source_model == 'assets.AssetMachine':
            record = AssetMachine.objects.select_related('client').get(pk=source_id)
            require_machine_scope(actor, record)
            client = record.client
            entity_kind = 'machine'
        else:
            record = WorkOrder.objects.select_related('machine__client').get(
                pk=source_id
            )
            require_work_order_scope(actor, record)
            client = record.machine.client if record.machine_id else None
            entity_kind = 'work_order'
        if client is None or not client.active or client.code not in eligible_clients:
            raise MemoryPolicyError('source_scope')
        value = getattr(record, source_field)
        # An enum-shaped CharField must still hold one of its declared codes.
        field = record._meta.get_field(
            source_field.removesuffix('_id')
            if source_field.endswith('_id')
            else source_field
        )
        if field.choices and value not in dict(field.flatchoices):
            raise MemoryPolicyError('source_value')
        if isinstance(value, (datetime, date)):
            value = value.isoformat()
        elif type(value) not in (bool, int) and not (
            isinstance(value, str) and field.choices
        ):
            raise MemoryPolicyError('source_value')
        return VerifiedValue(
            source_model, source_id, source_field, client.code, entity_kind, value
        )
    except (ObjectDoesNotExist, ScopeError, AttributeError):
        raise MemoryPolicyError('source_scope') from None


def render_verified(value, locale):
    """Only a native value, never model prose, receives operational authority."""
    templates = {
        'en': 'The authorized record {kind} {id} has the recorded field {field}: {value}.',
        'es': 'El registro autorizado {kind} {id} tiene el campo registrado {field}: {value}.',
        'de': 'Der autorisierte Datensatz {kind} {id} enthält das gespeicherte Feld {field}: {value}.',  # codespell:ignore feld
        'fr': 'Le dossier autorisé {kind} {id} contient le champ enregistré {field} : {value}.',
    }
    if locale not in templates:
        raise MemoryPolicyError('language_mismatch')
    return templates[locale].format(
        kind=value.entity_kind,
        id=value.source_id,
        field=value.source_field,
        value=value.value,
    )
