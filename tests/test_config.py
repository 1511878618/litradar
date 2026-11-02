"""Configuration: defaults, per-journal files, and honest validation errors."""

from pathlib import Path

import yaml
from helpers import temp_dir

from litradar.config import (
    DEFAULTS,
    Config,
    find_journal,
    load_config,
    load_journals,
    validate_journal,
    write_skeleton,
)
from litradar.util import ConfigError


def test_defaults_are_layered_in():
    with temp_dir() as tmp:
        (tmp / ".litradar").mkdir(parents=True)
        (tmp / ".litradar" / "config.yaml").write_text("direction: 进化\n", encoding="utf-8")
        config = load_config(tmp)
        assert config.direction == "进化"
        assert config.get("report.highlights") == DEFAULTS["report"]["highlights"]
        assert config.get("llm.model") == "deepseek-chat"


def test_missing_config_raises():
    with temp_dir() as tmp:
        try:
            load_config(tmp)
        except ConfigError as exc:
            assert "init" in str(exc)
            return
    raise AssertionError("expected ConfigError")


def test_missing_config_optional():
    with temp_dir() as tmp:
        config = load_config(tmp, require=False)
        assert config.get("report.highlights") == 8


def test_bad_yaml_raises():
    with temp_dir() as tmp:
        (tmp / ".litradar").mkdir(parents=True)
        (tmp / ".litradar" / "config.yaml").write_text("a: [unclosed\n", encoding="utf-8")
        try:
            load_config(tmp)
        except ConfigError as exc:
            assert "YAML" in str(exc)
            return
    raise AssertionError("expected ConfigError")


def test_journal_files_override_inline():
    with temp_dir() as tmp:
        write_skeleton(tmp)
        config = load_config(tmp)
        inline = dict(config.raw)
        inline["journals"] = [{"name": "Nature Genetics", "adapter": "crossref", "issn": "0000-0000"}]
        (tmp / ".litradar" / "config.yaml").write_text(
            yaml.safe_dump(inline, allow_unicode=True), encoding="utf-8"
        )
        journals = load_journals(load_config(tmp))
        nature = find_journal(journals, "Nature Genetics")
        assert nature["adapter"] == "rss"


def test_journal_validation_errors():
    cases = [
        ({"name": "X", "adapter": "nope"}, "adapter"),
        ({"name": "X", "adapter": "rss"}, "feed"),
        ({"name": "X", "adapter": "crossref"}, "issn"),
        ({"name": "X", "adapter": "html", "url": "https://x"}, "selectors"),
    ]
    for entry, needle in cases:
        try:
            validate_journal(entry, "test")
        except ConfigError as exc:
            assert needle in str(exc), (entry, exc)
        else:
            raise AssertionError(f"expected ConfigError for {entry}")


def test_journal_slug_is_derived():
    validated = validate_journal({"name": "The Plant Cell", "adapter": "rss", "feed": "u"})
    assert validated["slug"] == "the-plant-cell"


def test_find_journal_by_slug_and_name():
    journals = [validate_journal({"name": "Nature Genetics", "adapter": "rss", "feed": "u"})]
    assert find_journal(journals, "nature-genetics")["name"] == "Nature Genetics"
    assert find_journal(journals, "Nature Genetics")["name"] == "Nature Genetics"
    try:
        find_journal(journals, "Nope")
    except ConfigError:
        pass
    else:
        raise AssertionError("expected ConfigError")


def test_skeleton_is_idempotent():
    with temp_dir() as tmp:
        write_skeleton(tmp)
        config_path = tmp / ".litradar" / "config.yaml"
        config_path.write_text(config_path.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        write_skeleton(tmp)
        assert "# edited" in config_path.read_text(encoding="utf-8")
        assert len(list((tmp / ".litradar" / "journals").glob("*.yaml"))) == 17


def test_skeleton_writes_readmes():
    with temp_dir() as tmp:
        write_skeleton(tmp)
        assert (tmp / ".litradar" / "journals" / "README.md").exists()
        assert (tmp / ".litradar" / "README.md").exists()
        assert (tmp / ".litradar" / "state" / "README.md").exists()


def test_vault_path_is_quoted_safely():
    with temp_dir() as tmp:
        weird = tmp / "with space and 'quote'"
        weird.mkdir()
        write_skeleton(weird)
        config = load_config(weird)
        assert config.raw["vault"] == str(weird.resolve())


def test_config_get_dotted():
    config = Config({"a": {"b": {"c": 1}}}, Path("/tmp"))
    assert config.get("a.b.c") == 1
    assert config.get("a.b.missing", "fallback") == "fallback"
    assert config.get("nope", 5) == 5


def test_biorxiv_server_validation():
    try:
        validate_journal({"name": "bioRxiv", "adapter": "biorxiv", "server": "arxiv"}, "test")
    except ConfigError as exc:
        assert "server" in str(exc)
    else:
        raise AssertionError("expected ConfigError")
    validated = validate_journal({"name": "medRxiv", "adapter": "biorxiv", "server": "medrxiv"}, "test")
    assert validated["server"] == "medrxiv"
    defaulted = validate_journal({"name": "bioRxiv", "adapter": "biorxiv"}, "test")
    assert defaulted["server"] == "biorxiv"


def test_metadata_field_validation():
    base = {"name": "X", "adapter": "rss", "feed": "https://example.org/feed"}
    for bad in ({"impact_factor": "high"}, {"impact_factor": -1}, {"heading_level": 9},
                {"heading_level": "x"}, {"date_filter": "published"}):
        try:
            validate_journal({**base, **bad}, "test")
        except ConfigError:
            continue
        raise AssertionError(f"expected ConfigError for {bad}")


def test_metadata_fields_roundtrip():
    entry = validate_journal({"name": "Nature", "adapter": "rss", "feed": "https://x",
                              "impact_factor": 48.5, "heading_level": 3, "preprint": False}, "t")
    assert entry["impact_factor"] == 48.5
    assert entry["heading_level"] == 3
