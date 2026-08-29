"""S1: the analysis-scope contract module (pure, no database).

Repository/API behavior (optimistic versions, owner-only writes, the
begin_turn snapshot) is covered by the Django-runner suite in
``aichat/tests/test_thread_analysis_scope.py``; these tests pin the
normalization, hashing, and authorization-intersection semantics both
planes share.
"""

from __future__ import annotations

import pytest
from ai.core.analysis import scope as contract


def _explicit(machine_ids, **overrides):
    payload = {"mode": contract.MODE_EXPLICIT, "machine_ids": machine_ids}
    payload.update(overrides)
    return payload


class TestNormalization:
    def test_explicit_request_dedupes_and_sorts_ids(self) -> None:
        scope = contract.normalize_scope_request(_explicit([13, 12, 13]))
        assert scope.mode == contract.MODE_EXPLICIT
        assert scope.machine_ids == (12, 13)
        assert scope.source_classes == contract.SOURCE_CLASSES
        assert scope.date_from is None and scope.date_to is None

    def test_all_authorized_request(self) -> None:
        scope = contract.normalize_scope_request({"mode": contract.MODE_ALL_AUTHORIZED})
        assert scope.mode == contract.MODE_ALL_AUTHORIZED
        assert scope.machine_ids == ()

    @pytest.mark.parametrize(
        "ids",
        [
            [True],
            [0],
            [-4],
            ["12"],
            "12",
            [None],
            list(range(1, contract.MAX_EXPLICIT_MACHINES + 2)),
        ],
    )
    def test_invalid_machine_ids_rejected(self, ids) -> None:
        with pytest.raises(contract.ScopeValidationError):
            contract.normalize_scope_request(_explicit(ids))

    def test_explicit_requires_ids_and_fleet_forbids_them(self) -> None:
        with pytest.raises(contract.ScopeValidationError):
            contract.normalize_scope_request(_explicit([]))
        with pytest.raises(contract.ScopeValidationError):
            contract.normalize_scope_request({
                "mode": contract.MODE_ALL_AUTHORIZED,
                "machine_ids": [1],
            })

    @pytest.mark.parametrize("mode", ["site", "everything", "", None, contract.MODE_LEGACY])
    def test_unknown_and_readonly_modes_rejected(self, mode) -> None:
        with pytest.raises(contract.ScopeValidationError):
            contract.normalize_scope_request({"mode": mode})

    def test_site_group_is_a_typed_rejection(self) -> None:
        """Reserved multi-site mode fails closed until the upgrade exists."""
        with pytest.raises(contract.SiteGroupUnavailable):
            contract.normalize_scope_request({"mode": contract.MODE_SITE_GROUP})

    @pytest.mark.parametrize(
        "window",
        [
            {"from": "not-a-date"},
            {"to": 20260101},
            {"from": "2026-02-01", "to": "2026-01-01"},
            {"from": "2026-01-01", "to": "2026-01-01"},
            "2026-01-01",
        ],
    )
    def test_invalid_date_windows_rejected(self, window) -> None:
        with pytest.raises(contract.ScopeValidationError):
            contract.normalize_scope_request(_explicit([1], date_window=window))

    def test_half_open_date_window_accepted(self) -> None:
        scope = contract.normalize_scope_request(
            _explicit([1], date_window={"from": "2025-01-01", "to": "2026-01-01"})
        )
        assert (scope.date_from, scope.date_to) == ("2025-01-01", "2026-01-01")

    def test_source_classes_validated_and_canonically_ordered(self) -> None:
        scope = contract.normalize_scope_request(
            _explicit([1], source_classes=["work_order", "controlled_document"])
        )
        assert scope.source_classes == ("controlled_document", "work_order")
        for bad in (["telemetry"], [], "work_order"):
            with pytest.raises(contract.ScopeValidationError):
                contract.normalize_scope_request(_explicit([1], source_classes=bad))

    def test_display_label_bounds(self) -> None:
        assert (
            contract.normalize_scope_request(
                _explicit([1], display_label="Solar central inverters")
            ).display_label
            == "Solar central inverters"
        )
        with pytest.raises(contract.ScopeValidationError):
            contract.normalize_scope_request(
                _explicit([1], display_label="x" * (contract.MAX_DISPLAY_LABEL + 1))
            )

    def test_non_mapping_rejected(self) -> None:
        with pytest.raises(contract.ScopeValidationError):
            contract.normalize_scope_request(["not", "a", "mapping"])


class TestHashing:
    def test_hash_is_order_independent_and_content_sensitive(self) -> None:
        one = contract.normalize_scope_request(_explicit([12, 13]))
        two = contract.normalize_scope_request(_explicit([13, 12]))
        relabeled = contract.normalize_scope_request(_explicit([12, 13], display_label="A"))
        assert contract.scope_hash(one) == contract.scope_hash(two)
        assert len(contract.scope_hash(one)) == 64
        assert contract.scope_hash(one) != contract.scope_hash(relabeled)


class TestStoredRoundTrip:
    def test_empty_payload_is_legacy_unconfirmed(self) -> None:
        assert contract.scope_from_stored({}).mode == contract.MODE_LEGACY
        assert contract.scope_from_stored(None).mode == contract.MODE_LEGACY

    def test_payload_round_trips(self) -> None:
        scope = contract.normalize_scope_request(_explicit([7, 3], display_label="Two inverters"))
        assert contract.scope_from_stored(contract.scope_to_payload(scope)) == scope

    def test_unknown_schema_or_corruption_fails_closed_to_legacy(self) -> None:
        payload = contract.scope_to_payload(contract.normalize_scope_request(_explicit([1])))
        assert (
            contract.scope_from_stored({**payload, "schema_version": 99}).mode
            == contract.MODE_LEGACY
        )
        assert (
            contract.scope_from_stored({**payload, "machine_ids": ["x"]}).mode
            == contract.MODE_LEGACY
        )


class TestAuthorizationIntersection:
    def test_all_authorized_ids_pass(self) -> None:
        assert contract.require_all_authorized((1, 2), lambda _mid: True) == (1, 2)

    def test_one_failure_rejects_generically(self) -> None:
        with pytest.raises(contract.ScopeRejected) as excinfo:
            contract.require_all_authorized((1, 2), lambda mid: mid != 2)
        # The message must not disclose which id failed.
        assert "2" not in str(excinfo.value)


class TestDisplaySummary:
    def test_summaries(self) -> None:
        assert contract.display_summary(contract.legacy_scope()) == "Scope unconfirmed"
        assert (
            contract.display_summary(
                contract.normalize_scope_request({"mode": contract.MODE_ALL_AUTHORIZED})
            )
            == "Authorized fleet"
        )
        assert (
            contract.display_summary(contract.normalize_scope_request(_explicit([1])))
            == "1 selected asset"
        )
        assert (
            contract.display_summary(
                contract.normalize_scope_request(_explicit([1, 2], display_label="Pair"))
            )
            == "Pair"
        )
