"""Offline checks for Zotero identity, hierarchy, reading tags and native writes."""

import json
import sqlite3

from helpers import make_config, temp_dir

from litradar.model import Article
from litradar.zotero import ZoteroUnavailable, _resolve_collection, _sqlite_items, collection_tree
from litradar.zotero_workflow import (
    READING_ENDPOINT,
    LocalAPI,
    ensure_item,
    find_item,
    item_view,
    reading_status,
    set_status,
)


def test_collection_tree_normalizes_api_and_disambiguates_children():
    rows = collection_tree(
        [
            {"key": "PARENTAA", "data": {"name": "Project A", "parentCollection": False}},
            {"key": "PARENTBB", "data": {"name": "Project B"}},
            {"key": "CHILDAAA", "data": {"name": "Papers", "parentCollection": "PARENTAA"}},
            {"key": "CHILDBBB", "data": {"name": "Papers", "parentCollection": "PARENTBB"}},
        ]
    )
    assert _resolve_collection(rows, "Project A / Papers") == "CHILDAAA"
    assert _resolve_collection(rows, "CHILDBBB") == "CHILDBBB"
    assert [r for r in rows if r["key"] == "CHILDAAA"][0]["depth"] == 1
    try:
        _resolve_collection(rows, "Papers")
    except ZoteroUnavailable as exc:
        assert "不唯一" in str(exc)
    else:
        raise AssertionError("Must not pick the first same-named child folder")


def test_collection_tree_cycles_do_not_recurse_forever():
    assert (
        len(
            collection_tree(
                [{"key": "A", "name": "a", "parent": "B"}, {"key": "B", "name": "b", "parent": "A"}]
            )
        )
        == 2
    )


class FakeAPI:
    def __init__(self, entries=None):
        self.entries = entries or []
        self.writes = []

    def get(self, path):
        if path.startswith(READING_ENDPOINT):
            return {
                "provider": "reading-list",
                "statuses": [
                    {"value": v, "label": v}
                    for v in ("New", "To Read", "In Progress", "Read", "Not Reading", "自定义")
                ],
                "entries": self.entries,
                "legacyCount": 0,
            }
        if "/children" in path:
            return [
                {"key": "PDFKEYAA", "data": {"contentType": "application/pdf", "linkMode": "imported_url"}}
            ]
        if "/items/top?" in path:
            return self.entries
        if "/collections/" in path:
            return {"key": "FOLDERAA"}
        return self.entries[0]

    def write(self, path, body, **kwargs):
        self.writes.append((path, body, kwargs))
        if path == READING_ENDPOINT:
            row = self.entries[0]
            row["data"]["extra"] = "" if body.get("clear") else "Read_Status: " + body["status"]
            return row
        if kwargs.get("method") == "POST":
            row = {"key": "ITEMKEYA", "version": 2, "data": {**body[0], "key": "ITEMKEYA"}}
            self.entries = [row]
            return {"successful": {"0": row}, "failed": {}}
        self.entries[0]["data"].update(body)


def test_status_uses_reading_list_bridge_and_version_precondition():
    article = Article(title="Paper", journal="J", url="https://example.org/paper", doi="10.1/ABC")
    api = FakeAPI(
        [
            {
                "key": "ITEMKEYA",
                "version": 8,
                "data": {
                    "key": "ITEMKEYA",
                    "title": "Paper",
                    "DOI": "10.1/abc",
                    "tags": [{"tag": "my-project"}],
                },
            }
        ]
    )
    result = set_status(api, article, "Read")
    assert result["status"] == "Read"
    path, body, kwargs = api.writes[0]
    assert path == READING_ENDPOINT and body["status"] == "Read" and body["version"] == 8
    assert kwargs["method"] == "POST" and "tags" not in body
    result = set_status(api, article, "")
    assert result["status"] == "" and api.writes[-1][1]["clear"] is True
    assert api.entries[0]["data"]["tags"] == [{"tag": "my-project"}]


def test_new_item_uses_selected_child_folder_and_pdf_attachment_key():
    article = Article(title="Paper", journal="J", url="https://example.org/paper", doi="10.1/a")
    api = FakeAPI()
    row = ensure_item(api, article, "FOLDERAA")
    assert api.writes[0][1][0]["collections"] == ["FOLDERAA"]
    result = item_view(api, row, with_pdf=True)
    assert result["pdfs"][0]["url"] == "zotero://open-pdf/library/items/PDFKEYAA"
    assert result["selectUrl"] == "zotero://select/library/items/ITEMKEYA"


def test_duplicate_doi_requires_resolution_in_zotero():
    article = Article(title="Paper", journal="J", url="https://example.org/paper", doi="10.1/a")
    api = FakeAPI([{"data": {"DOI": "10.1/a"}}, {"data": {"DOI": "10.1/a"}}])
    try:
        find_item(api, article)
    except ZoteroUnavailable as exc:
        assert "重复" in str(exc)
    else:
        raise AssertionError("expected an exception")


def test_none_does_not_create_missing_item():
    api = FakeAPI()
    result = set_status(api, Article(title="Paper", journal="J", url="https://example.org/paper"), "none")
    assert result["status"] == "" and not api.writes


def test_native_authorization_is_server_bound_and_not_returned():
    with temp_dir() as tmp:
        api = LocalAPI(make_config(tmp))
        api.auth_path = tmp / "auth.json"
        api.auth_path.write_text(json.dumps({"key": "old", "server_id": "other-server"}))
        requests = []

        def request(path, **kwargs):
            requests.append((path, kwargs))
            if path == "/api/":
                return {}, {"zotero-server-id": "current-server"}
            if path == "/api/local/authorize":
                assert kwargs["headers"]["Zotero-Server-ID"] == "current-server"
                return {"key": "new-secret", "remember": True}, {}
            assert kwargs["headers"]["Zotero-API-Key"] == "new-secret"
            assert kwargs["headers"]["If-Unmodified-Since-Version"] == "9"
            return None, {}

        api.request = request
        assert api.write("/api/users/0/items/ITEMKEYA", {"tags": []}, version=9) is None
        assert json.loads(api.auth_path.read_text())["server_id"] == "current-server"
        assert len(requests) == 3


def test_sqlite_child_key_never_falls_back_to_entire_library():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
      create table items(itemID integer, dateAdded text);
      create table itemData(itemID integer, fieldID integer, valueID integer);
      create table itemDataValues(valueID integer, value text);
      create table itemAttachments(itemID integer);
      create table itemNotes(itemID integer);
      create table deletedItems(itemID integer);
      create table collections(collectionID integer, key text);
      create table collectionItems(collectionID integer, itemID integer);
      insert into items values(1, '2026-11-01'), (2, '2026-11-02');
      insert into itemData values(1, 1, 1), (2, 1, 2);
      insert into itemDataValues values(1, 'Selected child paper'), (2, 'Unrelated paper');
      insert into collections values(9, 'CHILDAAA');
      insert into collectionItems values(9, 1);
    """)
    try:
        rows = _sqlite_items(connection, "CHILDAAA", 100)
        assert len(rows) == 1 and rows[0]["data"]["title"] == "Selected child paper"
        try:
            _sqlite_items(connection, "MISSINGA", 100)
        except ZoteroUnavailable:
            pass
        else:
            raise AssertionError("Missing collection must not return the whole library")
    finally:
        connection.close()


def test_new_reading_item_is_created_then_passed_to_plugin():
    api = FakeAPI()
    row = set_status(
        api,
        Article(title="New", journal="J", url="https://example.org", doi="10.1/new"),
        "To Read",
        "FOLDERAA",
    )
    assert row["status"] == "To Read"
    assert len(api.writes) == 2
    assert "tags" not in api.writes[0][1][0]
    assert api.writes[1][0] == READING_ENDPOINT


def test_custom_status_and_exact_extra_parsing():
    api = FakeAPI(
        [{"key": "ITEMKEYA", "version": 2, "data": {"key": "ITEMKEYA", "title": "Paper", "DOI": "10.1/a"}}]
    )
    article = Article(title="Paper", journal="J", url="", doi="10.1/a")
    assert set_status(api, article, "自定义")["status"] == "自定义"
    assert reading_status("Read_Status_Date: 2026-01-01\nRead_Status: In Progress") == "In Progress"
    assert reading_status("Read_Status: Read\nRead_Status: To Read") == ""
    try:
        set_status(api, article, "not configured")
    except ZoteroUnavailable:
        pass
    else:
        raise AssertionError("expected an exception")


def test_native_root_accepts_plain_text_and_keeps_server_id():
    import litradar.zotero_workflow as module

    original = module.urllib.request.urlopen

    class Response:
        headers = {"Zotero-Server-ID": "local-server"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"Zotero Local API"

    module.urllib.request.urlopen = lambda *args, **kwargs: Response()
    try:
        with temp_dir() as tmp:
            data, headers = LocalAPI(make_config(tmp)).request("/api/")
            assert data == {} and headers["zotero-server-id"] == "local-server"
    finally:
        module.urllib.request.urlopen = original


class ArchiveAPI(FakeAPI):
    def __init__(self, entries=None):
        from pathlib import Path

        super().__init__(entries)
        self.config = make_config(Path("."))
        self.collections = []

    def get(self, path):
        if path == "/api/users/0/collections":
            return list(self.collections)
        return super().get(path)

    def write(self, path, body, **kwargs):
        if path == "/api/users/0/collections":
            self.writes.append((path, body, kwargs))
            key = f"COL{len(self.collections) + 1:05d}"
            row = {"key": key, "data": {"key": key, **body[0]}}
            self.collections.append(row)
            return {"successful": {"0": row}, "failed": {}}
        return super().write(path, body, **kwargs)


def test_daily_archive_creates_root_and_date_idempotently():
    from litradar.zotero_workflow import DAILY_COLLECTION_ROOT, daily_collection

    api = ArchiveAPI()
    key = daily_collection(api, "2026-11-04")
    assert len(api.collections) == 2
    root, child = api.collections
    assert root["data"]["name"] == DAILY_COLLECTION_ROOT and root["data"]["parentCollection"] is False
    assert child["data"]["name"] == "2026-11-04" and child["data"]["parentCollection"] == root["key"]
    assert daily_collection(api, "2026-11-04") == key and len(api.collections) == 2
    assert daily_collection(api, "2026-11-03") != key and len(api.collections) == 3


def test_daily_archive_reuses_item_and_preserves_existing_memberships():
    article = Article(title="Existing", journal="J", url="", doi="10.1/existing")
    api = ArchiveAPI(
        [
            {
                "key": "ITEMKEYA",
                "version": 7,
                "data": {
                    "key": "ITEMKEYA",
                    "title": "Existing",
                    "DOI": article.doi,
                    "collections": ["MYFOLDER"],
                },
            }
        ]
    )
    row = ensure_item(api, article, collection_date="2026-11-04")
    assert row["data"]["collections"] == ["MYFOLDER", api.collections[1]["key"]]
    assert api.writes[-1][2]["version"] == 7
    writes = len(api.writes)
    ensure_item(api, article, collection_date="2026-11-04")
    assert len(api.writes) == writes
    assert not any(path == "/api/users/0/items" for path, _, _ in api.writes)


def test_daily_archive_failure_never_creates_unfiled_article():
    api = ArchiveAPI()

    def fail(*args, **kwargs):
        raise ZoteroUnavailable("collection denied")

    api.write = fail
    try:
        ensure_item(api, Article(title="New", journal="J", url=""), collection_date="2026-11-04")
    except ZoteroUnavailable as error:
        assert "denied" in str(error)
    else:
        raise AssertionError("expected an exception")
    assert not api.entries


def test_manual_folder_bypasses_daily_creation():
    api = ArchiveAPI()
    row = ensure_item(api, Article(title="New", journal="J", url=""), "FOLDERAA", "2026-11-04")
    assert row["data"]["collections"] == ["FOLDERAA"] and not api.collections


def test_daily_archive_invalid_date_has_no_writes():
    from litradar.zotero_workflow import daily_collection

    api = ArchiveAPI()
    try:
        daily_collection(api, "2026-13-04")
    except ZoteroUnavailable:
        pass
    else:
        raise AssertionError("expected an exception")
    assert not api.writes
