"""Read-only SQL query tools bound to InvenTree's RBAC.

Direct PostgreSQL reads for questions the REST tools cannot answer
(aggregations, rankings, joins). Enforcement is layered and fail-closed:

1. Only a single SELECT/WITH statement passes lexical validation.
2. ``EXPLAIN (FORMAT JSON)`` resolves every relation the plan touches —
   including through joins and views — and each one must map to an
   InvenTree ruleset the *current authenticated user* holds ``view``
   permission for (``users.ruleset`` / ``users.permissions``). Unmapped
   relations (Django internals, sessions, tokens) are always denied.
3. The query runs inside a ``READ ONLY`` transaction with a statement
   timeout and row/byte caps, so even a validation bypass cannot write.

The current user comes from the AI boundary principal; the model is never
a principal and an absent principal denies everything.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ai.core.maf_compat import ai_function
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

MAX_ROWS = 200
MAX_RESULT_BYTES = 48_000
STATEMENT_TIMEOUT_MS = 5_000

#: Set-returning helpers that legitimately appear as Function Scans without
#: touching any relation. Anything else fails closed.
_ALLOWED_FUNCTION_SCANS = frozenset({
    "generate_series",
    "unnest",
    "jsonb_array_elements",
    "json_array_elements",
    "string_to_table",
})

_FORBIDDEN_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|"
    r"vacuum|merge|call|do|lock|listen|notify|prepare|deallocate|reindex|"
    r"cluster|refresh|comment|security|reset|into)\b",
    re.IGNORECASE,
)


def _validate_sql(sql: str) -> str:
    """Return the cleaned statement or raise ``ValueError`` (fail closed)."""
    text = (sql or "").strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    if not text:
        raise ValueError("empty query")
    if ";" in text:
        raise ValueError("only a single statement is allowed")
    head = text.split(None, 1)[0].lower()
    if head not in ("select", "with"):
        raise ValueError("only SELECT queries are allowed")
    match = _FORBIDDEN_RE.search(text)
    if match:
        raise ValueError(f"forbidden keyword: {match.group(0).lower()}")
    return text


def _relations_from_plan(plan: Any) -> tuple[set[str], set[str]]:
    """Collect every relation name and function-scan name from a plan tree."""
    relations: set[str] = set()
    functions: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            relation = node.get("Relation Name")
            if isinstance(relation, str) and relation:
                relations.add(relation)
            if node.get("Node Type") == "Function Scan":
                functions.add(str(node.get("Function Name", "")))
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(plan)
    return relations, functions


def _table_ruleset_map() -> dict[str, str]:
    from users.ruleset import get_ruleset_models

    mapping: dict[str, str] = {}
    for ruleset, tables in get_ruleset_models().items():
        for table in tables:
            mapping[str(table)] = str(ruleset)
    return mapping


def _denied_relations(user, relations: set[str]) -> list[str]:
    """Return relations the user may NOT read; empty means permitted."""
    from users.permissions import check_user_role

    mapping = _table_ruleset_map()
    denied: list[str] = []
    checked_roles: dict[str, bool] = {}
    for relation in sorted(relations):
        role = mapping.get(relation)
        if role is None:
            # Unmapped tables (auth internals, sessions, tokens, Django
            # bookkeeping) are never readable through this tool.
            denied.append(relation)
            continue
        if role not in checked_roles:
            checked_roles[role] = bool(check_user_role(user, role, "view"))
        if not checked_roles[role]:
            denied.append(relation)
    return denied


def _current_user():
    from ai.core.auth import get_current_principal
    from django.contrib.auth import get_user_model

    principal = get_current_principal()
    if principal is None:
        return None
    try:
        return get_user_model().objects.get(pk=int(principal.user_pk))
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _run_query(sql: str) -> dict[str, Any]:
    """Synchronous core: validate, authorize, execute, bound."""
    from django.db import connection, transaction

    try:
        text = _validate_sql(sql)
    except ValueError as exc:
        return {"error": f"Query rejected: {exc}"}

    if connection.vendor != "postgresql":
        return {"error": "The database query tool requires PostgreSQL."}

    user = _current_user()
    if user is None:
        return {"error": "No authenticated user context; query denied."}

    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SET LOCAL transaction_read_only = on")
            cursor.execute(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")

            cursor.execute(f"EXPLAIN (FORMAT JSON) {text}")
            plan_row = cursor.fetchone()
            plan = plan_row[0] if plan_row else []
            if isinstance(plan, str):
                import json as _json

                plan = _json.loads(plan)
            relations, functions = _relations_from_plan(plan)

            unexpected_functions = {
                name for name in functions if name not in _ALLOWED_FUNCTION_SCANS
            }
            if unexpected_functions:
                return {
                    "error": (
                        "Query rejected: function access is not permitted: "
                        + ", ".join(sorted(unexpected_functions))
                    )
                }

            denied = _denied_relations(user, relations)
            if denied:
                return {"error": ("Permission denied for table(s): " + ", ".join(denied))}

            cursor.execute(text)
            columns = [col[0] for col in cursor.description or []]
            fetched = cursor.fetchmany(MAX_ROWS + 1)
            truncated = len(fetched) > MAX_ROWS
            rows: list[list[Any]] = []
            budget = MAX_RESULT_BYTES
            for raw in fetched[:MAX_ROWS]:
                row = [_json_safe(value) for value in raw]
                budget -= sum(len(str(value)) for value in row) + 8
                if budget <= 0:
                    truncated = True
                    break
                rows.append(row)
    except Exception as exc:
        # Bounded, content-free failure: SQL text and DB error details may
        # carry customer data or schema internals.
        logger.info("Database query tool failed", extra={"error_type": type(exc).__name__})
        return {"error": f"Query failed ({type(exc).__name__}). Check the SQL syntax."}

    logger.info(
        "Database query tool executed",
        extra={"user": str(user.pk), "relations": sorted(relations), "rows": len(rows)},
    )
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


def _list_tables() -> dict[str, Any]:
    """Synchronous core for the schema listing, filtered by permissions."""
    from django.apps import apps

    user = _current_user()
    if user is None:
        return {"error": "No authenticated user context; listing denied."}

    from users.permissions import check_user_role

    mapping = _table_ruleset_map()
    role_ok: dict[str, bool] = {}
    tables: list[dict[str, Any]] = []
    for model in apps.get_models():
        table = model._meta.db_table
        role = mapping.get(table)
        if role is None:
            continue
        if role not in role_ok:
            role_ok[role] = bool(check_user_role(user, role, "view"))
        if not role_ok[role]:
            continue
        tables.append({
            "table": table,
            "model": model.__name__,
            "columns": [field.column for field in model._meta.concrete_fields],
        })
    return {"tables": sorted(tables, key=lambda entry: entry["table"])}


@ai_function
async def query_database(sql: str) -> dict[str, Any]:
    """Run one read-only SQL SELECT against the InvenTree PostgreSQL database.

    Use this for aggregations, rankings, and joins the other read tools
    cannot express (for example: which part has the highest total stock).
    Every table the query touches is checked against the current user's
    InvenTree role permissions, and the query runs read-only with a row
    limit and timeout. Use list_database_tables first to see the tables
    and columns available to you.

    Args:
        sql: A single PostgreSQL SELECT statement. No semicolons, no writes.

    Returns:
        {"columns": [...], "rows": [[...]], "row_count": int, "truncated": bool}
        or {"error": "..."} when the query is rejected, denied, or fails.
    """
    return await sync_to_async(_run_query, thread_sensitive=True)(sql)


@ai_function
async def list_database_tables() -> dict[str, Any]:
    """List database tables and columns the current user may query.

    Only tables covered by the user's InvenTree role permissions are
    included; use these exact table and column names with query_database.

    Returns:
        {"tables": [{"table": str, "model": str, "columns": [str, ...]}]}
    """
    return await sync_to_async(_list_tables, thread_sensitive=True)()
