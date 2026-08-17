"""Attachment Search projection: zero-gap supersede, purge, scope merge (R1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ai.core.config import Settings
from ai.core.integrations.attachment_search import (
    AttachmentIndexingError,
    AttachmentSearchProjection,
)


class FakeSearchClient:
    """Records operations; serves scripted search pages."""

    def __init__(self) -> None:
        self.uploaded: list[list[dict]] = []
        self.deleted: list[list[dict]] = []
        self.merged: list[list[dict]] = []
        self.filters: list[str] = []
        self.search_pages: list[list[dict]] = []
        self.fail_upload = False

    def search(self, *, search_text, filter, select, top):
        self.filters.append(filter)
        return self.search_pages.pop(0) if self.search_pages else []

    def upload_documents(self, *, documents):
        if self.fail_upload:
            return [{"succeeded": False}]
        self.uploaded.append(list(documents))
        return [{"succeeded": True} for _ in documents]

    def delete_documents(self, *, documents):
        self.deleted.append(list(documents))
        return [{"succeeded": True} for _ in documents]

    def merge_documents(self, *, documents):
        self.merged.append(list(documents))
        return [{"succeeded": True} for _ in documents]


def make_projection() -> tuple[AttachmentSearchProjection, FakeSearchClient]:
    projection = AttachmentSearchProjection(
        endpoint="https://search.example", index_name="aimms-attachment-docs-v1"
    )
    fake = FakeSearchClient()
    projection._client = fake
    return projection, fake


def test_upsert_batches_at_one_hundred():
    projection, fake = make_projection()
    documents = [{"id": f"att-1-abc-c{i}"} for i in range(205)]
    projection.upsert_documents(documents)
    assert [len(batch) for batch in fake.uploaded] == [100, 100, 5]


def test_upsert_failure_is_value_free():
    projection, fake = make_projection()
    fake.fail_upload = True
    with pytest.raises(AttachmentIndexingError) as excinfo:
        projection.upsert_documents([{"id": "att-1-abc-c0"}])
    assert excinfo.value.code == "ATTACHMENT_SEARCH_UPLOAD_FAILED"


def test_prune_stale_sha_deletes_only_other_revisions():
    projection, fake = make_projection()
    fake.search_pages = [[{"id": "att-7-old-c0"}, {"id": "att-7-old-c1"}]]
    deleted = projection.prune_stale_sha(attachment_id=7, keep_sha256="newsha")
    assert deleted == 2
    assert fake.filters[0] == ("attachment_id eq 7 and source_sha256 ne 'newsha'")
    assert fake.deleted == [[{"id": "att-7-old-c0"}, {"id": "att-7-old-c1"}]]


def test_prune_pages_until_empty():
    projection, fake = make_projection()
    fake.search_pages = [[{"id": "a"}], [{"id": "b"}], []]
    assert projection.prune_stale_sha(attachment_id=1, keep_sha256="x") == 2
    assert len(fake.deleted) == 2


def test_purge_deletes_every_revision():
    projection, fake = make_projection()
    fake.search_pages = [[{"id": "att-9-a-c0"}]]
    assert projection.purge_attachment(attachment_id=9) == 1
    assert fake.filters[0] == "attachment_id eq 9"


def test_merge_client_codes_touches_only_stale_documents():
    projection, fake = make_projection()
    fake.search_pages = [
        [
            {"id": "d1", "client_codes": ["acme"]},
            {"id": "d2", "client_codes": ["acme", "zeta"]},
        ]
    ]
    merged = projection.merge_client_codes(attachment_id=3, client_codes=["acme", "zeta"])
    assert merged == 1
    assert fake.merged == [[{"id": "d1", "client_codes": ["acme", "zeta"]}]]


def test_from_settings_refuses_alias_of_governed_index(monkeypatch):
    # Settings itself refuses this pairing at construction (startup guard),
    # so the adapter's defense-in-depth check is exercised with a stub.
    stub = SimpleNamespace(
        azure_search_endpoint="https://search.example",
        azure_search_api_key="",
        azure_search_attachment_docs_index="eaits-manuals-v4a",
        azure_search_controlled_documents_index="eaits-manuals-v4a",
        azure_search_documents_index="",
    )
    monkeypatch.setattr("ai.core.config.get_settings", lambda: stub)
    with pytest.raises(AttachmentIndexingError) as excinfo:
        AttachmentSearchProjection.from_settings()
    assert excinfo.value.code == "ATTACHMENT_SEARCH_INDEX_ALIASED"


def test_from_settings_requires_endpoint(monkeypatch):
    settings = Settings(_env_file=None, AZURE_SEARCH_ENDPOINT="")
    monkeypatch.setattr("ai.core.config.get_settings", lambda: settings)
    with pytest.raises(AttachmentIndexingError) as excinfo:
        AttachmentSearchProjection.from_settings()
    assert excinfo.value.code == "ATTACHMENT_SEARCH_CONFIG_INVALID"


def test_page_cap_exhaustion_raises_loudly():
    """F-05: giving up with matches still streaming must be an error."""
    projection, fake = make_projection()
    fake.search_pages = [[{"id": f"doc-{i}"}] for i in range(60)]
    with pytest.raises(AttachmentIndexingError) as excinfo:
        projection.purge_attachment(attachment_id=1)
    assert excinfo.value.code == "ATTACHMENT_SEARCH_PAGE_CAP_EXHAUSTED"
    assert len(fake.deleted) == 50  # the cap's worth of pages was processed


def test_merge_page_cap_exhaustion_raises_loudly():
    """F-05 applies to the metadata merge loop too."""
    projection, fake = make_projection()
    fake.search_pages = [[{"id": f"doc-{i}", "client_codes": ["old"]}] for i in range(60)]
    with pytest.raises(AttachmentIndexingError) as excinfo:
        projection.merge_client_codes(attachment_id=1, client_codes=["new"])
    assert excinfo.value.code == "ATTACHMENT_SEARCH_PAGE_CAP_EXHAUSTED"


def test_mark_sha_stale_is_sha_scoped_and_merges_is_current_false():
    """F-09: the belt-and-braces merge targets exactly one revision."""
    projection, fake = make_projection()
    fake.search_pages = [[{"id": "att-7-old-c0"}, {"id": "att-7-old-c1"}], []]
    marked = projection.mark_sha_stale(attachment_id=7, source_sha256="oldsha")
    assert marked == 2
    assert fake.filters[0] == (
        "attachment_id eq 7 and source_sha256 eq 'oldsha' and is_current eq true"
    )
    assert fake.merged == [
        [
            {"id": "att-7-old-c0", "is_current": False},
            {"id": "att-7-old-c1", "is_current": False},
        ]
    ]


def test_purge_sha_deletes_exactly_one_revision():
    """Cross-sha losers clean up only after themselves (F-06)."""
    projection, fake = make_projection()
    fake.search_pages = [[{"id": "att-7-mine-c0"}]]
    deleted = projection.purge_sha(attachment_id=7, source_sha256="mysha")
    assert deleted == 1
    assert fake.filters[0] == "attachment_id eq 7 and source_sha256 eq 'mysha'"


def test_close_releases_the_client():
    """close() drops the cached client and calls its close when present."""
    projection, fake = make_projection()
    fake.closed = False
    fake.close = lambda: setattr(fake, "closed", True)
    projection.close()
    assert fake.closed is True
    assert projection._client is None
