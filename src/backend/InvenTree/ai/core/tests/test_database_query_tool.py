"""RBAC and safety tests for the read-only database query tool.

Execution against PostgreSQL is deployment-only (dev/test databases are
SQLite); these tests cover the fail-closed layers that decide whether a
query may run at all: lexical validation, plan-based relation extraction,
ruleset permission mapping, and principal requirements.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402
from ai.core.tools.inventree.read.database import (  # noqa: E402
    _denied_relations,
    _relations_from_plan,
    _run_query,
    _validate_sql,
)


class TestValidateSql:
    def test_plain_select_passes(self):
        assert _validate_sql("SELECT * FROM part_part").startswith("SELECT")

    def test_trailing_semicolon_is_stripped(self):
        assert ";" not in _validate_sql("SELECT 1;")

    def test_with_cte_passes(self):
        assert _validate_sql("WITH t AS (SELECT 1) SELECT * FROM t")

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO part_part VALUES (1)",
            "UPDATE part_part SET name = 'x'",
            "DELETE FROM part_part",
            "DROP TABLE part_part",
            "SELECT 1; DELETE FROM part_part",
            "WITH t AS (DELETE FROM part_part RETURNING *) SELECT * FROM t",
            "SELECT * INTO evil FROM part_part",
            "GRANT ALL ON part_part TO public",
            "",
        ],
    )
    def test_mutations_and_multistatement_are_rejected(self, sql):
        with pytest.raises(ValueError):
            _validate_sql(sql)

    def test_wordlike_columns_are_not_false_positives(self):
        # 'created', 'updated_at' contain forbidden stems but are words.
        assert _validate_sql("SELECT created, updated_at, offset_x FROM part_part")


class TestRelationExtraction:
    def test_collects_relations_across_joins_and_subplans(self):
        plan = [
            {
                "Plan": {
                    "Node Type": "Hash Join",
                    "Plans": [
                        {"Node Type": "Seq Scan", "Relation Name": "part_part"},
                        {
                            "Node Type": "Hash",
                            "Plans": [
                                {
                                    "Node Type": "Seq Scan",
                                    "Relation Name": "stock_stockitem",
                                }
                            ],
                        },
                    ],
                }
            }
        ]
        relations, functions = _relations_from_plan(plan)
        assert relations == {"part_part", "stock_stockitem"}
        assert functions == set()

    def test_collects_function_scans(self):
        plan = [
            {
                "Plan": {
                    "Node Type": "Function Scan",
                    "Function Name": "pg_read_file",
                }
            }
        ]
        _relations, functions = _relations_from_plan(plan)
        assert functions == {"pg_read_file"}


class _User:
    pk = 42
    is_active = True
    is_superuser = False


def _site_single():
    # The minimal test settings lack InvenTree's SITE_MULTI flag consumed by
    # users.ruleset; the mapping itself is identical either way.
    return patch("users.ruleset.settings.SITE_MULTI", False, create=True)


class TestPermissionMapping:
    def test_unmapped_relations_are_always_denied(self):
        with (
            _site_single(),
            patch("users.permissions.check_user_role", return_value=True),
        ):
            denied = _denied_relations(_User(), {"django_session", "part_part"})
        assert "django_session" in denied
        assert "part_part" not in denied

    def test_role_denial_blocks_mapped_tables(self):
        with (
            _site_single(),
            patch("users.permissions.check_user_role", return_value=False),
        ):
            denied = _denied_relations(_User(), {"part_part", "stock_stockitem"})
        assert set(denied) == {"part_part", "stock_stockitem"}

    def test_auth_tables_require_admin_role(self):
        calls = []

        def _check(user, role, permission):
            calls.append(role)
            return role != "admin"

        with (
            _site_single(),
            patch("users.permissions.check_user_role", side_effect=_check),
        ):
            denied = _denied_relations(_User(), {"auth_user"})
        assert denied == ["auth_user"]
        assert "admin" in calls


class TestRunQueryGates:
    def test_rejects_before_touching_the_database(self):
        result = _run_query("DROP TABLE part_part")
        assert "rejected" in result["error"]

    def test_non_postgres_backend_is_refused(self):
        # The test database is SQLite, so a valid SELECT must be refused
        # before any principal or permission machinery runs.
        result = _run_query("SELECT 1")
        assert "PostgreSQL" in result["error"]

    def test_missing_principal_denies(self):
        with patch("django.db.connection") as fake_connection:
            fake_connection.vendor = "postgresql"
            result = _run_query("SELECT 1")
        assert "denied" in result["error"].lower()
