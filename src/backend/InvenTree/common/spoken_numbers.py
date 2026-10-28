"""Exact English numeric slots; no homophones, units or identifier repair."""

import re

DIGITS = dict(
    zip(
        (
            'zero',
            'one',
            'two',
            'three',
            'four',
            'five',
            'six',
            'seven',
            'eight',
            'nine',
        ),
        range(10),
        strict=True,
    )
)
SMALL = {
    **DIGITS,
    **dict(
        zip(
            (
                'ten',
                'eleven',
                'twelve',
                'thirteen',
                'fourteen',
                'fifteen',
                'sixteen',
                'seventeen',
                'eighteen',
                'nineteen',
            ),
            range(10, 20),
            strict=True,
        )
    ),
}
TENS = dict(
    zip(
        ('twenty', 'thirty', 'forty', 'fifty', 'sixty', 'seventy', 'eighty', 'ninety'),
        range(20, 100, 10),
        strict=True,
    )
)
NUMBER_WORDS_PATTERN = '|'.join((*SMALL, *TENS, 'hundred', 'thousand'))


def _under_hundred(words: list[str]) -> int | None:
    if len(words) == 1:
        return SMALL.get(words[0], TENS.get(words[0]))
    if len(words) == 2 and words[0] in TENS and words[1] in DIGITS and DIGITS[words[1]]:
        return TENS[words[0]] + DIGITS[words[1]]
    return None


def _under_thousand(words: list[str]) -> int | None:
    if (
        len(words) >= 2
        and words[0] in DIGITS
        and DIGITS[words[0]]
        and words[1] == 'hundred'
    ):
        rest = words[2:]
        if rest[:1] == ['and']:
            rest = rest[1:]
            if not rest:
                return None
        tail = _under_hundred(rest) if rest else 0
        return None if tail is None else DIGITS[words[0]] * 100 + tail
    return _under_hundred(words)


def integer_slot(value: str, *, digit_sequence: bool = True) -> str | None:
    """Preserve digit spelling for IDs; quantities require cardinal wording."""
    value = value.strip()
    if re.fullmatch(r'[0-9]+', value):
        return value
    if re.fullmatch(r'[1-9][0-9]{0,2}(?:,[0-9]{3}){1,2}', value):
        return value.replace(',', '')
    if not re.fullmatch(r'[a-z]+(?:(?:\s+|-)[a-z]+)*', value, re.I):
        return None
    words = value.casefold().replace('-', ' ').split()
    if not words or len(words) > 16:
        return None
    if digit_sequence and all(word in DIGITS for word in words):
        return ''.join(str(DIGITS[word]) for word in words)
    if words.count('thousand') == 1:
        split = words.index('thousand')
        head = _under_thousand(words[:split])
        rest = words[split + 1 :]
        if rest[:1] == ['and']:
            rest = rest[1:]
            if not rest:
                return None
        tail = _under_thousand(rest) if rest else 0
        number = head * 1000 + tail if head and tail is not None else None
    else:
        number = _under_thousand(words)
    return str(number) if number is not None else None


def decimal_slot(value: str, *, digit_sequence: bool = False) -> str | None:
    """Parse a complete nonnegative quantity without approximation or units."""
    value = value.strip().casefold()
    if re.fullmatch(
        r'(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,2})(?:\.[0-9]+)?', value
    ):
        return value.replace(',', '')
    pieces = value.split(' point ')
    if len(pieces) > 2:
        return None
    whole = integer_slot(pieces[0], digit_sequence=digit_sequence)
    if whole is None:
        return None
    if len(pieces) == 1:
        return whole
    if not re.fullmatch(r'[a-z]+(?:(?:\s+|-)[a-z]+)*', pieces[1]):
        return None
    fraction = pieces[1].replace('-', ' ').split()
    if (
        not fraction
        or len(fraction) > 8
        or any(word not in DIGITS for word in fraction)
    ):
        return None
    return whole + '.' + ''.join(str(DIGITS[word]) for word in fraction)


_EQUIPMENT_NUMBER = re.compile(
    r'\b(?:(?P<marker>no\.?|number)\s*|'
    r'(?P<equipment>pump|blower|compressor|fan|motor|valve|turbine|generator|'
    r'conveyor|boiler|chiller|centrifuge|clarifier|basin|station|train|unit|'
    r'line|skid|bench|breaker)(?:\s+(?P<hash>#\s*)?|(?=[0-9])))'
    rf'(?P<number>[0-9]{{1,4}}|(?:{NUMBER_WORDS_PATTERN})\b'
    rf'(?:[ -]+(?:{NUMBER_WORDS_PATTERN}|and)\b){{0,7}})(?![\w.-])',
    re.I,
)


def _equipment_number_match(
    term: str, match: re.Match[str]
) -> tuple[int, int, str] | None:
    number = integer_slot(match['number'])
    if number is None or len(number) > 4:
        return None
    start = (
        match.start('marker')
        if match['marker']
        else match.start('hash')
        if match['hash']
        else match.start('number')
    )
    if not re.search(r'[a-z]', term[:start], re.I):
        return None
    return start, match.end(), number


def equipment_number_references(term: str) -> tuple[tuple[int, int, str], ...]:
    """Locate named equipment-number slots, never serials or bare IDs."""
    return tuple(
        reference
        for match in _EQUIPMENT_NUMBER.finditer(term)
        if (reference := _equipment_number_match(term, match)) is not None
    )


def equipment_number_reference(term: str) -> tuple[int, int, str] | None:
    """Return the first explicit equipment number, retaining its literal digits."""
    return next(iter(equipment_number_references(term)), None)


def canonical_equipment_name(term: str) -> str:
    """Comparison-only form; callers retain the original name and transcript."""
    pieces, cursor = [], 0
    for start, end, number in equipment_number_references(term):
        pieces.extend((term[cursor:start].rstrip(), ' ', str(int(number))))
        cursor = end
    return ''.join(pieces) + term[cursor:]
