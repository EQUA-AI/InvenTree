"""Cross-database helpers for JSON lookups.

Django's JSONField ``contains``/``contained_by`` lookups are only supported on
PostgreSQL, MySQL and MariaDB. SQLite (used by several CI jobs) raises
NotSupportedError, so portable call sites must go through these helpers.
"""

from django.db import connection
from django.db.models import TextField
from django.db.models.functions import Cast


def filter_json_array_contains(queryset, field: str, value: str):
    """Filter a queryset on membership of ``value`` in a JSON array of strings.

    Uses native JSON containment where the backend supports it. On SQLite the
    serialized JSON text is matched against the quoted element instead, which is
    exact for arrays of simple strings (elements are quote-delimited).
    """
    if connection.vendor == 'sqlite':
        annotation = f'_{field}_json_text'
        return queryset.annotate(**{annotation: Cast(field, TextField())}).filter(**{
            f'{annotation}__contains': f'"{value}"'
        })

    return queryset.filter(**{f'{field}__contains': [value]})
