from litradar.zotero import ZoteroUnavailable
from litradar.zotero_health import diagnose
from litradar.zotero_workflow import item_view


class API:
    def __init__(self, *, offline=False, legacy=False):
        self.offline, self.legacy = offline, legacy
        self.calls = []

    def request(self, path, **kwargs):
        self.calls.append((path, kwargs))
        assert kwargs.get("method", "GET") == "GET"
        if self.offline:
            raise ZoteroUnavailable("无法连接 Zotero")
        if path == "/api/":
            return {}, {}
        if path.endswith("health"):
            if self.legacy:
                raise ZoteroUnavailable("HTTP 404")
            return {"bridgeVersion": "0.3.1", "zoteroVersion": "10.0.7",
                    "reading": {"installed": True, "enabled": True, "version": "1.7.2", "ready": False},
                    "llm": {"installed": True, "enabled": True, "version": "3.9.19", "ready": False,
                            "error": "llm-for-zotero 3.9.19 尚不受此上下文适配器支持"}}, {}
        if "reading" in path:
            raise ZoteroUnavailable("Zotero HTTP 503：请安装并启用 Reading List")
        raise ZoteroUnavailable("Zotero HTTP 503：llm-for-zotero 3.9.19 尚不受此上下文适配器支持")


def test_diagnostics_distinguish_initialization_from_installation_and_version():
    api = API()
    rows = {row["id"]: row for row in diagnose(api)["checks"]}
    assert rows["zotero"]["state"] == rows["bridge"]["state"] == "ok"
    assert rows["reading"]["message"] == "已启用，但初始化未完成"
    assert rows["reading"]["version"] == "1.7.2"
    assert rows["llm"]["message"] == "当前版本尚未适配"
    assert rows["llm"]["version"] == "3.9.19"
    assert len(api.calls) == 2


def test_offline_does_not_claim_other_plugins_missing():
    api = API(offline=True)
    rows = diagnose(api)["checks"]
    assert rows[0]["state"] == "error"
    assert all(row["state"] == "unknown" for row in rows[1:])
    assert len(api.calls) == 1


def test_legacy_503_is_not_misreported_as_zotero_offline():
    rows = {row["id"]: row for row in diagnose(API(legacy=True))["checks"]}
    assert rows["zotero"]["state"] == rows["bridge"]["state"] == "ok"
    assert rows["llm"]["version"] == "3.9.19"
    assert rows["reading"]["message"] == "尚未就绪"


def test_pdf_metadata_without_local_file_is_not_readable():
    class FilesAPI:
        def get(self, path):
            if "litradar/files" in path:
                return {"key": "ITEM0001", "files": [{"key": "FILE0001", "available": False}], "downloading": False}
            return [{"key": "FILE0001", "data": {"contentType": "application/pdf", "linkMode": "imported_file"}}]
    result = item_view(FilesAPI(), {"key": "ITEM0001", "data": {"title": "Test"}}, with_pdf=True)
    assert result["pdfChecked"] and not result["pdfs"][0]["available"]
    assert result["pdfs"][0]["url"].endswith("FILE0001")


def test_legacy_pdf_check_keeps_unknown_separate_from_missing():
    class FilesAPI:
        def get(self, path):
            if "litradar/files" in path:
                raise ZoteroUnavailable("HTTP 404")
            return [{"key": "FILE0001", "data": {"contentType": "application/pdf", "linkMode": "imported_file"}}]
    result = item_view(FilesAPI(), {"key": "ITEM0001", "data": {"title": "Test"}}, with_pdf=True)
    assert not result["pdfChecked"] and result["pdfs"][0]["available"] is None
    assert "升级" in result["pdfError"]
