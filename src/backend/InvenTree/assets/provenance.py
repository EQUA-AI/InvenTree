"""Read-only component assignment-source presentation, independent of stored data."""

import re
from typing import Literal

ProvenanceKind = Literal['register', 'scada', 'tag_review', 'manual', 'unknown']


def component_provenance_kind(provenance: str) -> ProvenanceKind:
    """Classify importer/reviewer vocabulary without claiming verification."""
    if re.fullmatch(r'Workbook population [0-9a-f]{12}', provenance):
        return 'register'
    if provenance in {
        'Inferred from source tags; physical assignment is unverified.',
        'SCADA tag mapping',
    }:
        return 'scada'
    if provenance == 'Explicit dictionary review crosswalk':
        return 'tag_review'
    if provenance in {'Manual entry', 'Operator review', 'Operator register review'}:
        return 'manual'
    return 'unknown'
