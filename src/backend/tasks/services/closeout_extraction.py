"""Closeout extraction contract: schema v1 validation and the extractor seam.

The narrative is adversarial input. This module is the Django-side authority
that decides whether an extractor's output is acceptable: schema-only (extra
keys rejected), every populated value anchored to a source span, candidates as
text with no identity resolution, and unknown schema versions failing closed.
Validation never executes anything from the output; hostile content can only
ever become inert, span-anchored strings inside an untrusted proposal row.
"""

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils.module_loading import import_string

from tasks.services.work_orders import WorkOrderCommandError

EXTRACTION_SCHEMA_VERSION = 1

# The only field paths extraction may propose (matches WorkOrderCloseout).
EXTRACTION_FIELDS = (
    'cause',
    'action',
    'result',
    'verification_summary',
    'downtime_minutes',
    'follow_up',
)

_TOP_LEVEL_KEYS = {
    'schema_version',
    'fields',
    'part_candidates',
    'reading_candidates',
    'warnings',
}
_FIELD_KEYS = {'value', 'spans', 'confidence', 'warnings'}
_PART_CANDIDATE_KEYS = {'text', 'spans', 'quantity_text', 'warnings'}
_READING_CANDIDATE_KEYS = {'text', 'spans', 'value_text', 'unit_text', 'warnings'}

# Identity resolution is forbidden by contract (FR-CO-003); any of these keys
# appearing anywhere in extractor output rejects the whole run.
_FORBIDDEN_ID_KEYS = {
    'id',
    'pk',
    'ids',
    'part_id',
    'part_pk',
    'stock_item_id',
    'stock_id',
    'user_id',
    'approval_id',
    'allocation_id',
}

_MAX_WARNING_LENGTH = 64
_MAX_WARNINGS = 32
_MAX_CANDIDATES = 64
_MAX_VALUE_LENGTH = 4000

_NUMBER_WORD_VALUES = {
    'zero': 0,
    'one': 1,
    'two': 2,
    'three': 3,
    'four': 4,
    'five': 5,
    'six': 6,
    'seven': 7,
    'eight': 8,
    'nine': 9,
    'ten': 10,
    'eleven': 11,
    'twelve': 12,
    'thirteen': 13,
    'fourteen': 14,
    'fifteen': 15,
    'sixteen': 16,
    'seventeen': 17,
    'eighteen': 18,
    'nineteen': 19,
    'twenty': 20,
    'thirty': 30,
    'forty': 40,
    'fifty': 50,
    'sixty': 60,
    'seventy': 70,
    'eighty': 80,
    'ninety': 90,
}
_ONES_PATTERN = r'(?:one|two|three|four|five|six|seven|eight|nine)'
_TENS_PATTERN = r'(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)'
_SMALL_PATTERN = (
    r'(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|'
    r'thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)'
)
_WORD_AMOUNT_PATTERN = rf'(?:{_TENS_PATTERN}(?:[- ]{_ONES_PATTERN})?|{_SMALL_PATTERN})'
_DURATION_AMOUNT_PATTERN = rf'(?:\d+(?:\.\d+)?|an?|half|{_WORD_AMOUNT_PATTERN})'
_DURATION_RE = re.compile(
    rf'(?<![\w.,/+&\-\u2013\u2014\u2212])'
    rf'(?P<amount>{_DURATION_AMOUNT_PATTERN})\s*'
    r'(?P<unit>minutes?|mins?|hours?|hrs?)\b',
    re.IGNORECASE,
)
_RANGE_BEFORE_RE = re.compile(
    rf'(?<!\w)(?:{_DURATION_AMOUNT_PATTERN})\s*'
    r'(?:-|/|&|\u2013|\u2014|to|or|and|through|until)\s*$',
    re.IGNORECASE,
)
_RANGE_AFTER_RE = re.compile(
    rf'^\s*(?:-|/|&|\u2013|\u2014|to|or|and|through|until)\s*'
    rf'(?:{_DURATION_AMOUNT_PATTERN})\b',
    re.IGNORECASE,
)
_CLAUSE_BOUNDARY_RE = re.compile(r'[.;!?\n]')
_DURATION_DISQUALIFIER_BEFORE_RE = re.compile(
    rf'(?:\b(?:not|no|about|approx\.?|approximately|roughly|around|circa|'
    rf'estimated\s+at|(?:a\s+)?(?:maximum|minimum)\s+of|'
    rf'half|(?:quarter|quarters)\s+of|'
    rf'less\s+than|more\s+than|greater\s+than|at\s+least|'
    rf'at\s+most|under|over|up\s+to|nearly|almost)\s*|'
    rf'[~\u2248<>\u2264\u2265]\s*|'
    rf'(?<!\w)(?:{_DURATION_AMOUNT_PATTERN})\s+and\s+(?:a\s+)?)$',
    re.IGNORECASE,
)
_DURATION_DISQUALIFIER_AFTER_RE = re.compile(
    r'^\s*(?:(?:about|approximately|roughly|or\s+so|or\s+(?:less|more)|'
    r'at\s+least|at\s+most|or\s+longer|or\s+shorter)\b|\+)',
    re.IGNORECASE,
)
_DURATION_CLAUSE_NEGATION_RE = re.compile(r'\b(?:not|never|without)\b', re.IGNORECASE)
_TEXT_DISQUALIFIER_BEFORE_RE = re.compile(
    r'(?:\b(?:not|no|never|without|maybe|perhaps|possibly)\s*|'
    r'\b(?:did|does|do|was|were|is|are|has|have|had|can|could|would|should)'
    r"n['\u2019]t\s*|\bfailed\s+to\s*)$",
    re.IGNORECASE,
)


class ExtractionUnavailable(WorkOrderCommandError):  # noqa: N818 - established command error name
    """Extraction is disabled, unconfigured, or the extractor call failed."""

    code = 'EXTRACTION_UNAVAILABLE'


class ExtractionSchemaUnknown(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The extractor returned an unknown schema version; fail closed."""

    code = 'EXTRACTION_SCHEMA_UNKNOWN'


class ExtractionInvalidOutput(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The extractor output violated the schema-only contract."""

    code = 'EXTRACTION_INVALID_OUTPUT'


def extraction_enabled() -> bool:
    """Whether the deployment allows AI extraction at all."""
    return bool(getattr(settings, 'AIMMS_CLOSEOUT_EXTRACTION_ENABLED', False))


def resolve_extractor():
    """Return the configured extractor callable, failing closed when absent.

    ``AIMMS_CLOSEOUT_EXTRACTOR`` is a callable or dotted path taking
    ``(narrative, shape)`` and returning the schema-v1 output document. The
    deployment adapter is ``ai.core.capabilities.closeout_binding.extract``,
    which pins the tool-free capability to the configured model.
    """
    if not extraction_enabled():
        raise ExtractionUnavailable('Closeout extraction is disabled')
    extractor = getattr(settings, 'AIMMS_CLOSEOUT_EXTRACTOR', None)
    if isinstance(extractor, str):
        try:
            extractor = import_string(extractor)
        except ImportError as exc:
            raise ExtractionUnavailable(
                'Configured closeout extractor cannot be imported'
            ) from exc
    if not callable(extractor):
        raise ExtractionUnavailable('No closeout extractor is configured')
    return extractor


def content_hash(document) -> str:
    """Canonical hash of a validated extraction document."""
    return hashlib.sha256(
        json.dumps(
            document, sort_keys=True, separators=(',', ':'), default=str
        ).encode()
    ).hexdigest()


def _reject(message: str):
    raise ExtractionInvalidOutput(message)


def _require_no_identity_keys(mapping, where: str):
    for key in mapping:
        if str(key).lower() in _FORBIDDEN_ID_KEYS:
            _reject(f'Identity resolution is forbidden: {where} contains {key!r}')


def _validate_spans(spans, narrative: str, where: str, *, required: bool):
    if not isinstance(spans, list):
        _reject(f'{where}: spans must be a list')
    if required and not spans:
        _reject(f'{where}: a populated value requires at least one source span')
    previous_end = -1
    for span in spans:
        if (
            not isinstance(span, (list, tuple))
            or len(span) != 2
            or not all(type(edge) is int for edge in span)
        ):
            _reject(f'{where}: each span must be a [start, end] integer pair')
        start, end = span
        if start < 0 or end > len(narrative) or start >= end:
            _reject(f'{where}: span [{start}, {end}] is not anchored in the narrative')
        if start < previous_end:
            _reject(f'{where}: spans must be ascending and non-overlapping')
        previous_end = end
    return [list(span) for span in spans]


def _normalized_text(text: str) -> str:
    return ' '.join(text.split()).casefold()


def _value_pattern(value: str):
    """Word-boundary, whitespace-flexible pattern for one contiguous value."""
    normalized = _normalized_text(value)
    tokens = [re.escape(token) for token in normalized.split()]
    if not tokens:
        return None
    body = r'\s+'.join(tokens)
    prefix = r'(?<!\w)' if re.match(r'\w', normalized[0]) else ''
    suffix = r'(?!\w)' if re.match(r'\w', normalized[-1]) else ''
    return re.compile(f'{prefix}{body}{suffix}', re.IGNORECASE)


def _anchor_field_spans(value: str, spans, narrative: str, where: str):
    """Anchor a field value to real narrative coordinates, guards intact.

    The extractor claims character coordinates, but a language model cannot do
    character arithmetic reliably: live runs consistently produced the right
    value with wrong or over-wide spans and the exact-span rule refused every
    one of them (observed 2026-08-05, field ``cause``, WO-000128). The server
    now derives the coordinates itself: first from occurrences inside the
    claimed spans, then - flagged as realigned for the reviewing human - from
    the narrative as a whole. Every candidate occurrence is re-validated by
    the exact containment check, so all fabrication guards still apply:

    * a value absent from the narrative anchors nowhere and is rejected;
    * word boundaries hold (``safe`` finds no anchor inside ``unsafe``);
    * the negation/hedge lookbehind still fires - an occurrence inside
      ``was not safe`` is skipped, and if no clean occurrence exists the
      field is rejected;
    * values may not join separate fragments (single contiguous match only).

    Nothing here weakens review authority: every extracted field still
    requires an explicit human decision before anything is persisted.

    Returns ``(anchored_spans, realigned)``; raises via the containment check
    when no guarded occurrence exists anywhere.
    """
    pattern = _value_pattern(value)
    if pattern is None:
        return spans, False

    def _candidates(start: int, end: int):
        for match in pattern.finditer(narrative, start, end):
            yield [match.start(), match.end()]

    for span_start, span_end in spans:
        for candidate in _candidates(span_start, span_end):
            try:
                _require_value_in_spans(value, [candidate], narrative, where)
            except ExtractionInvalidOutput:
                continue
            return [candidate], False

    for candidate in _candidates(0, len(narrative)):
        try:
            _require_value_in_spans(value, [candidate], narrative, where)
        except ExtractionInvalidOutput:
            continue
        return [candidate], True

    # No guarded occurrence anywhere: surface the original rejection.
    _require_value_in_spans(value, spans, narrative, where)
    return spans, False  # pragma: no cover - the line above always raises


def _require_value_in_spans(
    value: str, spans, narrative: str, where: str, *, exact: bool = True
):
    """One source span must truthfully anchor the value it claims.

    Bounds checking alone (FR-CO-003's coordinate half) lets an extractor
    attach a fabricated value to any in-range span and have it *look*
    narrative-anchored to the reviewing human. Requiring the normalized value
    to appear in the joined span text makes a value without a real narrative
    source unrepresentable. Primary field/candidate text must equal a whole
    span; candidate auxiliary text may occur within that same span. Normalization
    is casefold plus whitespace collapse.
    Crucially, a value must occur within one contiguous source span: joining
    unrelated fragments can manufacture assertions such as ``not safe``.
    Word boundaries prevent a shorter value from borrowing truth from a
    different token (``safe`` from ``unsafe`` or ``20`` from ``120``).
    """
    normalized_value = _normalized_text(value)
    prefix = r'(?<!\w)' if re.match(r'\w', normalized_value[0]) else ''
    suffix = r'(?!\w)' if re.match(r'\w', normalized_value[-1]) else ''
    pattern = re.compile(f'{prefix}{re.escape(normalized_value)}{suffix}')
    for span_start, span_end in spans:
        raw_source = narrative[span_start:span_end]
        source = _normalized_text(raw_source)
        leading_space = len(raw_source) - len(raw_source.lstrip())
        trailing_space = len(raw_source) - len(raw_source.rstrip())
        visible_start = span_start + leading_space
        visible_end = span_end - trailing_space
        if exact and source != normalized_value:
            continue
        for match in pattern.finditer(source):
            start, end = match.span()
            # ``\w`` boundaries distinguish 20 from 120, but decimal/group
            # separators are non-word characters. Do not let 5 borrow
            # provenance from .5 (including at offset zero), or 000 from 1,000;
            # ordinary trailing sentence punctuation remains valid because it
            # is not followed by another digit.
            if (
                normalized_value[0].isdigit()
                and start > 0
                and source[start - 1] in '.,+-/\N{EN DASH}\N{EM DASH}\N{MINUS SIGN}'
            ):
                continue
            if (
                normalized_value[-1].isdigit()
                and end + 1 < len(source)
                and source[end] in '.,+-/\N{EN DASH}\N{EM DASH}\N{MINUS SIGN}'
                and source[end + 1].isdigit()
            ):
                continue
            # A hostile extractor can crop the span itself to hide the token
            # context that the in-span boundary checks need. Re-check the real
            # narrative characters immediately outside a narrowed span.
            if start == 0 and visible_start > 0:
                preceding = narrative[visible_start - 1]
                if (
                    re.match(r'\w', normalized_value[0]) and re.match(r'\w', preceding)
                ) or (
                    normalized_value[0].isdigit()
                    and preceding in '.,+-/\N{EN DASH}\N{EM DASH}\N{MINUS SIGN}'
                ):
                    continue
                if _TEXT_DISQUALIFIER_BEFORE_RE.search(
                    narrative[max(0, visible_start - 48) : visible_start]
                ):
                    continue
            if end == len(source) and visible_end < len(narrative):
                following = narrative[visible_end]
                if re.match(r'\w', normalized_value[-1]) and re.match(r'\w', following):
                    continue
                if normalized_value[-1].isdigit():
                    if following in '+-/\N{EN DASH}\N{EM DASH}\N{MINUS SIGN}':
                        continue
                    if (
                        following in '.,'
                        and visible_end + 1 < len(narrative)
                        and narrative[visible_end + 1].isdigit()
                    ):
                        continue
            return
    _reject(f'{where}: value is not present in its anchored narrative spans')


def _word_amount(value: str) -> Decimal | None:
    """Parse the deliberately small, deterministic duration-word vocabulary."""
    normalized = value.casefold().replace('-', ' ')
    if normalized in {'a', 'an'}:
        return Decimal(1)
    if normalized == 'half':
        return Decimal('0.5')
    words = normalized.split()
    if len(words) == 1 and words[0] in _NUMBER_WORD_VALUES:
        return Decimal(_NUMBER_WORD_VALUES[words[0]])
    if (
        len(words) == 2
        and _NUMBER_WORD_VALUES.get(words[0], 0) >= 20
        and _NUMBER_WORD_VALUES.get(words[1], 10) < 10
    ):
        return Decimal(_NUMBER_WORD_VALUES[words[0]] + _NUMBER_WORD_VALUES[words[1]])
    return None


def _duration_minutes_from_text(source: str) -> int | None:
    """Derive one unambiguous minute value from the exact cited source text.

    This is intentionally conservative. A single numeric or small English-word
    duration with an explicit minute/hour unit is accepted; ranges, alternatives,
    compound durations, unknown words, and fractional minutes fail closed for
    human correction rather than trusting model arithmetic.
    """
    matches = list(_DURATION_RE.finditer(source))
    if len(matches) != 1:
        return None
    match = matches[0]
    # The cited span itself must be the duration phrase, not a broader phrase
    # whose negation, qualification, or compound arithmetic the regex skipped.
    if source[: match.start()].strip() or source[match.end() :].strip():
        return None
    amount_start, amount_end = match.span('amount')
    if (
        amount_start > 0
        and source[amount_start - 1] in '.,/+&-\N{EN DASH}\N{EM DASH}\N{MINUS SIGN}'
    ) or (
        amount_end + 1 < len(source)
        and source[amount_end] in '.,'
        and source[amount_end + 1].isdigit()
    ):
        return None
    if _RANGE_BEFORE_RE.search(source[: match.start()]) or _RANGE_AFTER_RE.search(
        source[match.end() :]
    ):
        return None

    raw_amount = match.group('amount')
    try:
        amount = (
            Decimal(raw_amount) if raw_amount[0].isdigit() else _word_amount(raw_amount)
        )
    except InvalidOperation:
        return None
    if amount is None:
        return None
    multiplier = 60 if match.group('unit').casefold().startswith('h') else 1
    minutes = amount * multiplier
    if minutes < 0 or minutes != minutes.to_integral_value():
        return None
    return int(minutes)


def _duration_clause(narrative: str, start: int, end: int) -> str:
    """Return the whole clause containing the model-selected duration span."""
    prefix = narrative[:start]
    boundaries = list(_CLAUSE_BOUNDARY_RE.finditer(prefix))
    clause_start = boundaries[-1].end() if boundaries else 0

    suffix = narrative[end:]
    boundary = _CLAUSE_BOUNDARY_RE.search(suffix)
    clause_end = end + (boundary.start() if boundary else len(suffix))
    return narrative[clause_start:clause_end]


def _require_downtime_in_spans(value: int, spans, narrative: str):
    if len(spans) != 1:
        _reject('downtime_minutes requires one contiguous duration source span')
    first_start = spans[0][0]
    last_end = spans[-1][1]
    before = narrative[max(0, first_start - 64) : first_start]
    after = narrative[last_end : min(len(narrative), last_end + 64)]
    clause = _duration_clause(narrative, first_start, last_end)
    # Inspect outside the model-selected span too. Otherwise an extractor can
    # cite only ``20 minutes`` from ``10 to 20 minutes`` or omit the sign/comma
    # from ``-5`` / ``1,000`` and make an ambiguous or malformed value appear
    # deterministic.
    if (
        (before and re.search(r'[\w.,/+&<>~\-\u2013\u2014\u2212\u2264\u2265]$', before))
        or (after and re.match(r'\w', after))
        or _RANGE_BEFORE_RE.search(before)
        or _RANGE_AFTER_RE.search(after)
        or _DURATION_DISQUALIFIER_BEFORE_RE.search(before)
        or _DURATION_DISQUALIFIER_AFTER_RE.search(after)
    ):
        _reject('downtime_minutes source span omits adjacent numeric context')
    # One clause with two duration phrases is compound or comparative even
    # when the connector includes a unit (``1 hour and 30 minutes``). Looking
    # only beside the cropped span lets the model persist one component as the
    # exact total, so any multi-duration clause requires human correction.
    if len(
        list(_DURATION_RE.finditer(clause))
    ) != 1 or _DURATION_CLAUSE_NEGATION_RE.search(clause):
        _reject('downtime_minutes source clause is compound or ambiguous')
    source = ' '.join(narrative[start:end] for start, end in spans)
    if _duration_minutes_from_text(source) != value:
        _reject(
            'downtime_minutes must be deterministically derived from one '
            'unambiguous duration in its anchored narrative spans'
        )


def _validate_warnings(warnings, where: str):
    if not isinstance(warnings, list) or len(warnings) > _MAX_WARNINGS:
        _reject(f'{where}: warnings must be a bounded list')
    for warning in warnings:
        if not isinstance(warning, str) or len(warning) > _MAX_WARNING_LENGTH:
            _reject(f'{where}: warnings must be short strings')
    return list(warnings)


def _validate_field(name: str, payload, narrative: str):
    if not isinstance(payload, dict):
        _reject(f'field {name!r} must be an object')
    _require_no_identity_keys(payload, f'field {name!r}')
    extra = set(payload) - _FIELD_KEYS
    if extra:
        _reject(f'field {name!r} has unknown keys: {sorted(extra)}')
    if 'value' not in payload or 'spans' not in payload:
        _reject(f'field {name!r} must contain value and spans')

    value = payload['value']
    if name == 'downtime_minutes':
        if value is not None and (type(value) is not int or value < 0):
            _reject('downtime_minutes must be a non-negative integer or null')
        populated = value is not None
    else:
        if value is None:
            value = ''
        if not isinstance(value, str) or len(value) > _MAX_VALUE_LENGTH:
            _reject(f'field {name!r} must be a bounded string')
        populated = bool(value.strip())

    spans = _validate_spans(
        payload['spans'], narrative, f'field {name!r}', required=populated
    )
    realigned = False
    if populated:
        if name == 'downtime_minutes':
            _require_downtime_in_spans(value, spans, narrative)
        else:
            spans, realigned = _anchor_field_spans(
                value, spans, narrative, f'field {name!r}'
            )
    confidence = payload.get('confidence', 0.0)
    if type(confidence) not in (int, float) or not 0.0 <= float(confidence) <= 1.0:
        _reject(f'field {name!r}: confidence must be within [0, 1]')
    warnings = _validate_warnings(payload.get('warnings', []), f'field {name!r}')
    if realigned and 'span_realigned' not in warnings:
        # The extractor's claimed coordinates did not contain the value; the
        # anchor shown to the reviewer was derived server-side. Visible so a
        # reviewing human knows the highlight is ours, not the model's claim.
        warnings.append('span_realigned')
    return {
        'value': value,
        'spans': spans,
        'confidence': float(confidence),
        'warnings': warnings,
    }


def _validate_candidates(candidates, allowed_keys, narrative: str, where: str):
    if not isinstance(candidates, list) or len(candidates) > _MAX_CANDIDATES:
        _reject(f'{where} must be a bounded list')
    validated = []
    for index, candidate in enumerate(candidates):
        label = f'{where}[{index}]'
        if not isinstance(candidate, dict):
            _reject(f'{label} must be an object')
        _require_no_identity_keys(candidate, label)
        extra = set(candidate) - allowed_keys
        if extra:
            _reject(f'{label} has unknown keys: {sorted(extra)}')
        text = candidate.get('text', '')
        if not isinstance(text, str) or not text.strip() or len(text) > 255:
            _reject(f'{label}: text is required and bounded')
        claimed_spans = _validate_spans(
            candidate.get('spans', []), narrative, label, required=True
        )
        # Same live failure mode as the primary fields: correct candidate
        # text, unreliable model coordinates. Anchor the text server-side
        # with the full guard set; flag realignment for the reviewer.
        anchored_spans, realigned = _anchor_field_spans(
            text, claimed_spans, narrative, label
        )
        row = {
            'text': text,
            'spans': anchored_spans,
            'warnings': _validate_warnings(candidate.get('warnings', []), label),
        }
        # Auxiliary values (quantity/unit/value text) must share provenance
        # with the candidate phrase: first the spans the extractor claimed,
        # else the immediate neighbourhood of the anchored phrase itself.
        window = [
            max(0, anchored_spans[0][0] - 80),
            min(len(narrative), anchored_spans[-1][1] + 80),
        ]
        for key in allowed_keys - {'text', 'spans', 'warnings'}:
            value = candidate.get(key, '')
            if not isinstance(value, str) or len(value) > 255:
                _reject(f'{label}: {key} must be a bounded string')
            if value.strip():
                try:
                    _require_value_in_spans(
                        value, claimed_spans, narrative, f'{label}.{key}', exact=False
                    )
                except ExtractionInvalidOutput:
                    _require_value_in_spans(
                        value, [window], narrative, f'{label}.{key}', exact=False
                    )
                    realigned = True
            row[key] = value
        if realigned and 'span_realigned' not in row['warnings']:
            row['warnings'].append('span_realigned')
        validated.append(row)
    return validated


def validate_extraction_output(output, narrative: str) -> dict:
    """Validate one extractor document against the schema-v1 contract.

    Returns the normalized document. Raises ``ExtractionSchemaUnknown`` for a
    version this deployment does not understand and ``ExtractionInvalidOutput``
    for any structural violation, unanchored value, identity key, or oversized
    content. The output is data only; nothing in it is executed or resolved.
    """
    if not isinstance(output, dict):
        _reject('Extractor output must be a JSON object')
    extra = set(output) - _TOP_LEVEL_KEYS
    if extra:
        _reject(f'Extractor output has unknown keys: {sorted(extra)}')
    version = output.get('schema_version')
    if type(version) is not int or version != EXTRACTION_SCHEMA_VERSION:
        raise ExtractionSchemaUnknown(f'Unknown extraction schema version: {version!r}')

    raw_fields = output.get('fields')
    if not isinstance(raw_fields, dict):
        _reject('Extractor output requires a fields object')
    unknown_fields = set(raw_fields) - set(EXTRACTION_FIELDS)
    if unknown_fields:
        _reject(f'Extractor proposed unknown fields: {sorted(unknown_fields)}')

    fields = {
        name: _validate_field(name, raw_fields[name], narrative)
        for name in EXTRACTION_FIELDS
        if name in raw_fields
    }
    document = {
        'schema_version': EXTRACTION_SCHEMA_VERSION,
        'fields': fields,
        'part_candidates': _validate_candidates(
            output.get('part_candidates', []),
            _PART_CANDIDATE_KEYS,
            narrative,
            'part_candidates',
        ),
        'reading_candidates': _validate_candidates(
            output.get('reading_candidates', []),
            _READING_CANDIDATE_KEYS,
            narrative,
            'reading_candidates',
        ),
        'warnings': _validate_warnings(output.get('warnings', []), 'warnings'),
    }
    return document


NORMALIZATION_RULE_VERSION = 'co-norm-1'

_AMBIGUITY_MARKERS = ('–', '—', ' or ', ' to ', '/')  # noqa: RUF001 - dash variants are the detection targets


def normalize_reading(raw_text: str) -> tuple[Decimal | None, list[str]]:
    """Deterministically normalize one reading under rule set ``co-norm-1``.

    Returns ``(value, warnings)``. Ambiguous numerics (ranges, alternatives,
    multiple numbers, unparsable text) keep the raw text, return no value, and
    carry the ``numeric_ambiguity`` warning; a human must correct the source or
    enter an unambiguous manual value (FR-CO-009).
    """
    text = (raw_text or '').strip()
    if not text:
        return None, ['numeric_ambiguity']
    lowered = f' {text.lower()} '
    if any(marker in lowered for marker in _AMBIGUITY_MARKERS):
        return None, ['numeric_ambiguity']

    tokens = [
        token
        for token in text.replace(',', ' ').split()
        if any(char.isdigit() for char in token)
    ]
    if len(tokens) != 1:
        return None, ['numeric_ambiguity']
    candidate = tokens[0].strip('+')
    try:
        return Decimal(candidate), []
    except InvalidOperation:
        return None, ['numeric_ambiguity']
