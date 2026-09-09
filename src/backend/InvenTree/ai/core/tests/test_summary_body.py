"""M2 PR 3: the compaction summary body (ai.core.memory.summary_body).

Pure module, no Django: parsing, per-fact objects (§8.7), the v2 upgrade,
the plain-text rendering the assembler fences, and exclusions (GR-03).
"""

from __future__ import annotations

import json

import pytest
from ai.core.memory import summary_body as sb
from ai.core.memory.vocabulary import MEMORY_TYPE_BY_LIST, FactLifecycle, MemoryType


def _stored(label: str, body: dict) -> str:
    return label + "\n" + json.dumps(body, ensure_ascii=True)


def test_parse_summary_label_and_body():
    label, body = sb.parse_summary(_stored("  Pump 3 ", {"machine_facts": ["seal worn"]}))
    assert label == "Pump 3"
    assert body == {"machine_facts": ["seal worn"]}
    assert sb.parse_summary(None) == ("", {})
    assert sb.parse_summary("") == ("", {})


def test_parse_summary_rejects_non_dict_and_bad_json():
    assert sb.parse_summary("label only") == ("label only", {})
    assert sb.parse_summary("label\n[1, 2]") == ("label", {})
    assert sb.parse_summary('label\n"a string"') == ("label", {})
    assert sb.parse_summary("label\n{not json") == ("label", {})


def test_item_helpers_accept_str_and_dict():
    obj = {"id": "mf4", "text": "seal worn", "lifecycle": "superseded"}
    assert sb.is_legacy_item("seal worn") and not sb.is_legacy_item(obj)
    assert sb.item_text("seal worn") == "seal worn"
    assert sb.item_text(obj) == "seal worn"
    assert sb.item_text(7) == ""
    assert sb.item_id("seal worn") == "" and sb.item_id(obj) == "mf4"
    assert sb.item_lifecycle("seal worn") == "active"
    assert sb.item_lifecycle(obj) == "superseded"
    assert sb.item_lifecycle({"text": "x"}) == "active"
    assert sb.is_active("seal worn") and not sb.is_active(obj)
    assert not sb.is_active("   ") and not sb.is_active({"text": "", "lifecycle": "active"})
    body = {"machine_facts": ["a", obj, {"text": "b"}], "open_questions": "not-a-list"}
    assert [sb.item_text(i) for i in sb.active_items(body, "machine_facts")] == ["a", "b"]
    assert sb.active_items(body, "open_questions") == []
    assert sb.active_items(body, "corrections") == []


def test_fingerprint_normalizes_case_space_punctuation():
    one = sb.fingerprint("Pump-3 seal, worn")
    two = sb.fingerprint("pump 3   SEAL worn")
    assert one == two
    assert len(one) == 16 and int(one, 16) >= 0
    assert sb.fingerprint("pump 3 seal fine") != one
    assert sb.normalize_text("  Pump-3 seal,\nworn! ") == "pump 3 seal worn"


def test_upgrade_item_assigns_prefixed_ids_and_defaults():
    item, next_id = sb.upgrade_item(
        "[mf9] seal worn", field="machine_facts", next_id=1, created_seq=5
    )
    assert next_id == 2
    assert item == {
        "id": "mf1",
        "text": "seal worn",
        "verification": "inferred",
        "lifecycle": "active",
        "origin": "compaction",
        "memory_type": "equipment_fact",
        "directive_flags": [],
        "created_seq": 5,
        "superseded_by": None,
        "fingerprint": sb.fingerprint("seal worn"),
    }
    question, next_id = sb.upgrade_item(
        "why?", field="open_questions", next_id=next_id, created_seq=5
    )
    assert question["id"] == "oq2" and next_id == 3
    assert question["memory_type"] == MemoryType.OPEN_ISSUE
    proposal, _ = sb.upgrade_item("do it", field="pending_proposals", next_id=3, created_seq=5)
    assert proposal["id"] == "pp3" and proposal["memory_type"] == "schedule"
    correction, _ = sb.upgrade_item("fix", field="corrections", next_id=4, created_seq=5)
    assert correction["id"] == "co4" and correction["memory_type"] == "equipment_fact"


def test_upgrade_item_keeps_existing_identity():
    existing = {
        "id": "mf7",
        "text": "seal worn",
        "lifecycle": "superseded",
        "fingerprint": "0123456789abcdef",
        "memory_type": "procedure_note",
        "superseded_by": "co8",
        "unknown": "dropped",
    }
    item, next_id = sb.upgrade_item(existing, field="machine_facts", next_id=3, created_seq=9)
    assert next_id == 3  # an existing id never consumes the counter
    assert item["id"] == "mf7"
    assert item["lifecycle"] == "superseded"
    assert item["fingerprint"] == "0123456789abcdef"
    assert item["memory_type"] == "procedure_note"
    assert item["superseded_by"] == "co8"
    assert item["verification"] == "inferred" and item["origin"] == "compaction"
    assert item["directive_flags"] == [] and item["created_seq"] == 9
    assert "unknown" not in item
    # A dict without an id is assigned one from the counter.
    anonymous, next_id = sb.upgrade_item(
        {"text": "x"}, field="corrections", next_id=3, created_seq=1
    )
    assert anonymous["id"] == "co3" and next_id == 4
    assert anonymous["fingerprint"] == sb.fingerprint("x")


def test_upgrade_body_is_idempotent_and_drops_unknown_keys():
    body = {
        "label": "Pump 3",
        "machine_facts": ["seal worn", "", {"id": "mf5", "text": "OEM seal"}, 12],
        "open_questions": ["[oq2] why?"],
        "citation_keys": ["k1", "", "k1", 3],
        "narrative": "n",
        "facts": ["smuggled"],
        "exclusions": [{"fingerprint": "abcd", "item_id": "mf1"}, "bad", {}],
        "next_item_id": "2",
    }
    once = sb.upgrade_body(body, created_seq=4)
    assert set(once) == {
        "label",
        *sb.ITEM_LISTS,
        "citation_keys",
        "narrative",
        "exclusions",
        "next_item_id",
        "body_version",
    }
    assert once["body_version"] == 2
    # Lists are upgraded in ITEM_LISTS order; the counter is body-wide and
    # starts past the highest id already present; blanks consume no id.
    assert [i["id"] for i in once["open_questions"]] == ["oq6"]
    assert once["open_questions"][0]["text"] == "why?"
    assert [i["id"] for i in once["machine_facts"]] == ["mf7", "mf5"]
    assert once["next_item_id"] == 8
    assert once["citation_keys"] == ["k1", "3"]
    assert once["exclusions"] == [
        {"fingerprint": "abcd", "item_id": "mf1", "created_seq": 0, "reason": ""}
    ]
    assert once["pending_proposals"] == [] and once["corrections"] == []
    assert "facts" not in once
    twice = sb.upgrade_body(once, created_seq=99)
    assert twice == once
    assert sb.upgrade_body({}, created_seq=0)["next_item_id"] == 1


def test_render_lists_active_items_only_as_plain_text():
    body = sb.upgrade_body(
        {
            "label": "Pump 3",
            "open_questions": ["is the seal OEM?"],
            "pending_proposals": [],
            "machine_facts": [
                "seal worn",
                {"text": "old seal", "lifecycle": "superseded"},
                {"text": "gone", "lifecycle": "forgotten"},
            ],
            "corrections": ["seal is OEM"],
            "citation_keys": ["k1", "k2"],
            "narrative": "the pump",
        },
        created_seq=1,
    )
    text = sb.render_for_context(_stored("Pump 3", body))
    assert text == (
        "Pump 3\n"
        "Open questions:\n- is the seal OEM?\n"
        "Machine facts:\n- seal worn\n"
        "Corrections:\n- seal is OEM\n"
        "Citations: k1, k2\n"
        "Narrative: the pump"
    )
    assert "old seal" not in text and "gone" not in text
    assert "{" not in text and "}" not in text
    assert "mf1" not in text and "fingerprint" not in text and "superseded" not in text
    assert "Pending proposals" not in text


def test_render_is_deterministic_and_collapses_newlines():
    stored = _stored(
        "Pump 3",
        {"machine_facts": ["seal\n  worn\ttoday"], "narrative": "line one\n\nline two"},
    )
    first = sb.render_for_context(stored)
    assert first == sb.render_for_context(stored)
    assert first == "Pump 3\nMachine facts:\n- seal worn today\nNarrative: line one line two"
    # Legacy strings and v2 objects render identically.
    v2 = _stored("Pump 3", sb.upgrade_body(sb.parse_summary(stored)[1], created_seq=1))
    assert sb.render_for_context(v2) == first


def test_render_unparsable_body_is_label_only():
    assert sb.render_for_context("Pump 3\n{not json") == "Pump 3"
    assert sb.render_for_context("Pump 3") == "Pump 3"
    assert sb.render_for_context('\n{"machine_facts": ["seal worn"]}') == (
        "Machine facts:\n- seal worn"
    )
    assert sb.render_for_context("") == "" and sb.render_for_context(None) == ""
    assert sb.render_for_context('Pump 3\n["seal worn"]') == "Pump 3"


def test_render_max_chars_truncates():
    stored = _stored("Pump 3", {"machine_facts": ["seal worn"]})
    assert sb.render_for_context(stored, max_chars=6) == "Pump 3"
    assert sb.render_for_context(stored, max_chars=0) == ""
    assert sb.render_for_context(stored, max_chars=10_000) == sb.render_for_context(stored)


def test_apply_exclusions_by_id_and_fingerprint_blanks_narrative():
    body = sb.upgrade_body(
        {
            "machine_facts": ["seal worn", "bearing hot", "Seal, worn!"],
            "open_questions": ["why?"],
            "narrative": "restates the seal",
            "exclusions": [
                {
                    "fingerprint": sb.fingerprint("seal worn"),
                    "item_id": "",
                    "created_seq": 1,
                    "reason": "forget",
                },
                {"fingerprint": "", "item_id": "oq1", "created_seq": 1, "reason": "wrong"},
            ],
        },
        created_seq=1,
    )
    assert [i["id"] for i in body["open_questions"]] == ["oq1"]
    before = json.dumps(body, sort_keys=True)
    applied, hits = sb.apply_exclusions(body)
    assert hits == 3
    assert json.dumps(body, sort_keys=True) == before  # pure
    assert [i["lifecycle"] for i in applied["machine_facts"]] == [
        "forgotten",
        "active",
        "forgotten",
    ]
    assert applied["open_questions"][0]["lifecycle"] == "forgotten"
    assert applied["narrative"] == ""
    assert applied["exclusions"] == body["exclusions"]
    again, hits = sb.apply_exclusions(applied)
    assert hits == 0 and again == applied
    assert sb.excluded_keys(body) == (
        frozenset({"oq1"}),
        frozenset({sb.fingerprint("seal worn")}),
    )
    # Legacy strings hit by fingerprint are minted into objects when flipped.
    legacy, hits = sb.apply_exclusions({
        "machine_facts": ["seal worn", "x"],
        "exclusions": [{"fingerprint": sb.fingerprint("seal worn")}],
    })
    assert hits == 1
    assert legacy["machine_facts"][0]["lifecycle"] == FactLifecycle.FORGOTTEN
    assert legacy["machine_facts"][0]["id"] == "mf1" and legacy["next_item_id"] == 2
    assert legacy["machine_facts"][1] == "x"
    assert "seal worn" not in sb.render_for_context(_stored("L", legacy))
    untouched, hits = sb.apply_exclusions({"machine_facts": ["x"]})
    assert hits == 0 and untouched == {"machine_facts": ["x"]}


def _all_ids(body: dict) -> list[str]:
    return [sb.item_id(i) for f in sb.ITEM_LISTS for i in body.get(f) or [] if sb.item_id(i)]


def test_apply_exclusions_never_mints_an_id_the_body_already_holds():
    # A mixed body (object beside a legacy string) without a counter: the
    # minted id must skip past the ids already present, as upgrade_body does.
    mixed = {
        "machine_facts": [
            "seal worn",
            {"id": "mf1", "text": "bearing hot", "lifecycle": "active"},
        ],
        "exclusions": [{"fingerprint": sb.fingerprint("seal worn")}],
    }
    applied, hits = sb.apply_exclusions(mixed)
    assert hits == 1
    ids = _all_ids(applied)
    assert ids == ["mf2", "mf1"]
    assert len(ids) == len(set(ids))
    assert applied["next_item_id"] == 3
    # The mint agrees with an upgrade-first pass on the same body.
    upgraded, _ = sb.apply_exclusions(sb.upgrade_body(mixed, created_seq=0))
    assert _all_ids(upgraded) == ids
    # A corrupt counter beside higher ids across other lists is ignored too.
    corrupt = {
        "open_questions": [{"id": "oq7", "text": "why?"}],
        "machine_facts": [{"id": "mf1", "text": "a"}, "b"],
        "exclusions": [{"fingerprint": sb.fingerprint("b")}],
        "next_item_id": "garbage",
    }
    applied, hits = sb.apply_exclusions(corrupt)
    assert hits == 1
    ids = _all_ids(applied)
    assert ids == ["oq7", "mf1", "mf8"] and len(ids) == len(set(ids))
    assert applied["next_item_id"] == 9
    # Two legacy strings hit in one pass consume consecutive fresh ids.
    two = {
        "machine_facts": [{"id": "mf3", "text": "keep"}, "seal worn", "Seal, worn!"],
        "exclusions": [{"fingerprint": sb.fingerprint("seal worn")}],
    }
    applied, hits = sb.apply_exclusions(two)
    assert hits == 2
    assert _all_ids(applied) == ["mf3", "mf4", "mf5"] and applied["next_item_id"] == 6


def test_strip_id_prefix():
    assert sb.strip_id_prefix("[mf3] seal worn") == "seal worn"
    assert sb.strip_id_prefix("  [oq12]   why?") == "why?"
    assert sb.strip_id_prefix("[co1] [mf2] x") == "[mf2] x"
    assert sb.strip_id_prefix("[xx1] keep") == "[xx1] keep"
    assert sb.strip_id_prefix("seal [mf3] worn") == "seal [mf3] worn"


def test_new_item_type_defaults_follow_the_list_map():
    for field, prefix in sb.ID_PREFIX_BY_LIST.items():
        item = sb.new_item("t", field=field, item_id=f"{prefix}1", created_seq=0)
        assert item["memory_type"] == MEMORY_TYPE_BY_LIST[field]
    typed = sb.new_item(
        "t", field="corrections", item_id="co2", created_seq=0, memory_type="schedule"
    )
    assert typed["memory_type"] == "schedule"
    with pytest.raises(KeyError):
        sb.new_item("t", field="citation_keys", item_id="x1", created_seq=0)
