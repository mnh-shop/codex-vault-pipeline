"""Tests for codex_vault_pipeline.graph.source_reader."""

from pathlib import Path
from typing import List, Optional

import pytest

from codex_vault_pipeline.graph.source_reader import (
    GraphSourceRecord,
    read_source_file,
    read_sources_from_runtime,
    summarize_graph_axes,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_source(
    root: Path,
    encoded_id: str,
    *,
    source_id: str,
    primary_domain: str = "",
    related_domains: Optional[List[str]] = None,
    ecosystems: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
    topics: Optional[List[str]] = None,
    integration_targets: Optional[List[str]] = None,
    project_use_cases: Optional[List[str]] = None,
    artifact_role: str = "",
    source_role: str = "",
    authority_level: str = "",
    lifecycle_status: str = "",
    repo_full_name: str = "",
    canonical_url: str = "",
) -> Path:
    """Write a fake source.v1.yaml under the runtime tree."""
    d = root / "sources" / encoded_id
    d.mkdir(parents=True, exist_ok=True)
    data: dict = {"source_id": source_id}

    if primary_domain:
        data["primary_domain"] = primary_domain
    if related_domains is not None:
        data["related_domains"] = related_domains
    if ecosystems is not None:
        data["ecosystems"] = ecosystems
    if capabilities is not None:
        data["capabilities"] = capabilities
    if topics is not None:
        data["topics"] = topics
    if integration_targets is not None:
        data["integration_targets"] = integration_targets
    if project_use_cases is not None:
        data["project_use_cases"] = project_use_cases
    if artifact_role:
        data["artifact_role"] = artifact_role
    if source_role:
        data["source_role"] = source_role
    if authority_level:
        data["authority_level"] = authority_level
    if lifecycle_status:
        data["lifecycle_status"] = lifecycle_status
    if canonical_url:
        data["canonical_url"] = canonical_url
    if repo_full_name:
        data["repo_identity"] = {"full_name": repo_full_name}

    import yaml

    (d / "source.v1.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return d / "source.v1.yaml"


# ---------------------------------------------------------------------------
# Tests — read_source_file
# ---------------------------------------------------------------------------


def _yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "source.v1.yaml"
    p.write_text(content)
    return p


class TestReadSourceFile:
    def test_minimal_record(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/minimal\n")
        rec = read_source_file(p)
        assert rec.source_id == "github:org/minimal"
        assert rec.primary_domain is None
        assert rec.ecosystems == ()
        assert rec.capabilities == ()
        assert rec.artifact_role is None
        assert rec.repo_url is None
        assert rec.title is None

    def test_full_record(self, tmp_path: Path):
        yaml_text = """\
source_id: github:org/full
primary_domain: deep-research
related_domains:
  - coding-agents
ecosystems:
  - langchain
  - mcp
capabilities:
  - deep-research
  - web-search
topics:
  - langgraph
integration_targets:
  - github
project_use_cases:
  - deep-research-system
artifact_role: agent-platform
source_role: official-extension
authority_level: official
lifecycle_status: active
canonical_url: https://github.com/org/full
repo_identity:
  full_name: org/full
"""
        p = _yaml(tmp_path, yaml_text)
        rec = read_source_file(p)
        assert rec.source_id == "github:org/full"
        assert rec.primary_domain == "deep-research"
        assert rec.related_domains == ("coding-agents",)
        assert rec.ecosystems == ("langchain", "mcp")
        assert rec.capabilities == ("deep-research", "web-search")
        assert rec.topics == ("langgraph",)
        assert rec.integration_targets == ("github",)
        assert rec.project_use_cases == ("deep-research-system",)
        assert rec.artifact_role == "agent-platform"
        assert rec.source_role == "official-extension"
        assert rec.authority_level == "official"
        assert rec.lifecycle_status == "active"
        assert rec.repo_url == "https://github.com/org/full"
        assert rec.title == "org/full"

    def test_missing_source_id_raises(self, tmp_path: Path):
        p = _yaml(tmp_path, "primary_domain: test\n")
        with pytest.raises(ValueError, match="no source_id"):
            read_source_file(p)

    def test_empty_source_id_raises(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: ''\n")
        with pytest.raises(ValueError, match="no source_id"):
            read_source_file(p)

    def test_null_fields_become_none(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/nulls\nprimary_domain: ~\nartifact_role:\n")
        rec = read_source_file(p)
        assert rec.source_id == "github:org/nulls"
        assert rec.primary_domain is None
        assert rec.artifact_role is None

    def test_null_lists_become_empty_tuple(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/nolist\necosystems:\ncapabilities: ~\n")
        rec = read_source_file(p)
        assert rec.ecosystems == ()
        assert rec.capabilities == ()

    def test_empty_lists_become_empty_tuple(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/emptylist\necosystems: []\ncapabilities: []\n")
        rec = read_source_file(p)
        assert rec.ecosystems == ()
        assert rec.capabilities == ()

    def test_path_is_resolved(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/path\n")
        rec = read_source_file(p)
        assert rec.source_path == p.resolve()

    def test_raw_dict_preserved(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/raw\nextra_field: hello\nnested: {a: 1}\n")
        rec = read_source_file(p)
        assert rec.raw["source_id"] == "github:org/raw"
        assert rec.raw["extra_field"] == "hello"
        assert rec.raw["nested"] == {"a": 1}

    def test_knowledge_status_not_in_yaml(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/ks\n")
        rec = read_source_file(p)
        assert rec.knowledge_status is None  # not in source v1, preserved for later use

    def test_title_falls_back_when_no_repo_identity(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/nt\ncanonical_url: https://example.com\n")
        rec = read_source_file(p)
        assert rec.title is None

    def test_no_repo_url_when_absent(self, tmp_path: Path):
        p = _yaml(tmp_path, "source_id: github:org/nourl\n")
        rec = read_source_file(p)
        assert rec.repo_url is None

    def test_integer_values_are_coerced(self, tmp_path: Path):
        """Some fields might be numeric; coerce to str."""
        p = _yaml(tmp_path, "source_id: github:org/int\nprimary_domain: 42\n")
        rec = read_source_file(p)
        assert rec.primary_domain == "42"

    def test_scalar_related_domains_becomes_empty(self, tmp_path: Path):
        """If related_domains is a scalar, treat as absent list."""
        p = _yaml(tmp_path, "source_id: github:org/badlist\nrelated_domains: not-a-list\n")
        rec = read_source_file(p)
        assert rec.related_domains == ()


# ---------------------------------------------------------------------------
# Tests — read_sources_from_runtime
# ---------------------------------------------------------------------------


class TestReadSourcesFromRuntime:
    def test_returns_empty_when_no_sources_dir(self, tmp_path: Path):
        records = read_sources_from_runtime(tmp_path)
        assert records == {}

    def test_returns_empty_when_no_yaml_files(self, tmp_path: Path):
        (tmp_path / "sources" / "empty_dir").mkdir(parents=True)
        records = read_sources_from_runtime(tmp_path)
        assert records == {}

    def test_reads_multiple_sources(self, tmp_path: Path):
        _write_source(tmp_path, "aaa_alpha", source_id="github:org/alpha", primary_domain="domain-a")
        _write_source(tmp_path, "bbb_beta", source_id="github:org/beta", primary_domain="domain-b")
        records = read_sources_from_runtime(tmp_path)
        assert len(records) == 2
        assert "github:org/alpha" in records
        assert "github:org/beta" in records

    def test_deterministic_order(self, tmp_path: Path):
        _write_source(tmp_path, "zzz_zulu", source_id="github:org/zulu", primary_domain="domain-z")
        _write_source(tmp_path, "aaa_alpha", source_id="github:org/alpha", primary_domain="domain-a")
        records = read_sources_from_runtime(tmp_path)
        keys = list(records.keys())
        assert keys == sorted(keys)  # deterministic by source_id

    def test_skips_non_yaml_files_in_source_dirs(self, tmp_path: Path):
        d = tmp_path / "sources" / "some_source"
        d.mkdir(parents=True)
        (d / "other.txt").write_text("not a source")
        # No source.v1.yaml → should be skipped silently
        records = read_sources_from_runtime(tmp_path)
        assert records == {}

    def test_fails_on_missing_source_id(self, tmp_path: Path):
        d = tmp_path / "sources" / "bad"
        d.mkdir(parents=True)
        import yaml
        (d / "source.v1.yaml").write_text(yaml.safe_dump({"primary_domain": "test"}))
        with pytest.raises(ValueError, match="no source_id"):
            read_sources_from_runtime(tmp_path)

    def test_tolerates_subdir_without_yaml(self, tmp_path: Path):
        """Non-source subdirectories should be ignored."""
        (tmp_path / "sources" / "not_a_source").mkdir(parents=True)
        records = read_sources_from_runtime(tmp_path)
        assert records == {}


# ---------------------------------------------------------------------------
# Tests — summarize_graph_axes
# ---------------------------------------------------------------------------


class TestSummarizeGraphAxes:
    def test_empty_records(self):
        summary = summarize_graph_axes({})
        for axis in (
            "primary_domain",
            "related_domains",
            "ecosystems",
            "capabilities",
            "artifact_role",
            "source_role",
            "authority_level",
            "lifecycle_status",
            "knowledge_status",
        ):
            assert summary[axis] == {}

    def test_counts_primary_domain(self):
        records = {
            "a": GraphSourceRecord(source_id="a", source_path=Path("/x"), primary_domain="hermes"),
            "b": GraphSourceRecord(source_id="b", source_path=Path("/x"), primary_domain="n8n"),
            "c": GraphSourceRecord(source_id="c", source_path=Path("/x"), primary_domain="hermes"),
        }
        summary = summarize_graph_axes(records)
        assert summary["primary_domain"] == {"hermes": 2, "n8n": 1}

    def test_counts_tuple_axes(self):
        records = {
            "a": GraphSourceRecord(
                source_id="a",
                source_path=Path("/x"),
                primary_domain="deep-research",
                ecosystems=("langchain", "mcp"),
            ),
            "b": GraphSourceRecord(
                source_id="b",
                source_path=Path("/x"),
                primary_domain="deep-research",
                ecosystems=("langchain",),
            ),
            "c": GraphSourceRecord(
                source_id="c",
                source_path=Path("/x"),
                primary_domain="n8n",
                ecosystems=(),
            ),
        }
        summary = summarize_graph_axes(records)
        assert summary["ecosystems"] == {"langchain": 2, "mcp": 1}
        # c has no ecosystems, so not counted

    def test_counts_capabilities(self):
        records = {
            "a": GraphSourceRecord(
                source_id="a",
                source_path=Path("/x"),
                primary_domain="hermes",
                capabilities=("deep-research", "web-search"),
            ),
            "b": GraphSourceRecord(
                source_id="b",
                source_path=Path("/x"),
                primary_domain="n8n",
                capabilities=("web-search",),
            ),
        }
        summary = summarize_graph_axes(records)
        assert summary["capabilities"] == {"deep-research": 1, "web-search": 2}

    def test_counts_artifact_role(self):
        records = {
            "a": GraphSourceRecord(source_id="a", source_path=Path("/x"), primary_domain="h",
                                   artifact_role="agent-platform"),
            "b": GraphSourceRecord(source_id="b", source_path=Path("/x"), primary_domain="n",
                                   artifact_role="reference"),
            "c": GraphSourceRecord(source_id="c", source_path=Path("/x"), primary_domain="d",
                                   artifact_role="agent-platform"),
        }
        summary = summarize_graph_axes(records)
        assert summary["artifact_role"] == {"agent-platform": 2, "reference": 1}

    def test_related_domains_counted(self):
        records = {
            "a": GraphSourceRecord(
                source_id="a", source_path=Path("/x"),
                primary_domain="h",
                related_domains=("n8n", "osint"),
            ),
            "b": GraphSourceRecord(
                source_id="b", source_path=Path("/x"),
                primary_domain="n",
                related_domains=("osint",),
            ),
        }
        summary = summarize_graph_axes(records)
        assert summary["related_domains"] == {"osint": 2, "n8n": 1}

    def test_knowledge_status_included(self):
        records = {
            "a": GraphSourceRecord(source_id="a", source_path=Path("/x"), primary_domain="h",
                                   knowledge_status="candidate"),
            "b": GraphSourceRecord(source_id="b", source_path=Path("/x"), primary_domain="n",
                                   knowledge_status="canonical"),
        }
        summary = summarize_graph_axes(records)
        assert summary["knowledge_status"] == {"candidate": 1, "canonical": 1}

    def test_descending_order(self):
        records = {
            "a": GraphSourceRecord(source_id="a", source_path=Path("/x"), primary_domain="z"),
            "b": GraphSourceRecord(source_id="b", source_path=Path("/x"), primary_domain="a"),
            "c": GraphSourceRecord(source_id="c", source_path=Path("/x"), primary_domain="z"),
            "d": GraphSourceRecord(source_id="d", source_path=Path("/x"), primary_domain="m"),
        }
        summary = summarize_graph_axes(records)
        assert list(summary["primary_domain"].items()) == [("z", 2), ("a", 1), ("m", 1)]


# ---------------------------------------------------------------------------
# Integration — end-to-end with runtime fixture
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_roundtrip(self, tmp_path: Path):
        _write_source(
            tmp_path, "org_platform",
            source_id="github:org/platform",
            primary_domain="deep-research",
            related_domains=["coding-agents"],
            ecosystems=["langchain", "mcp"],
            capabilities=["deep-research", "web-search"],
            artifact_role="agent-platform",
            source_role="official-extension",
            authority_level="official",
            lifecycle_status="active",
            repo_full_name="org/platform",
            canonical_url="https://github.com/org/platform",
        )
        _write_source(
            tmp_path, "org_ref",
            source_id="github:org/reference",
            primary_domain="deep-research",
            ecosystems=["langchain"],
            capabilities=["deep-research"],
            artifact_role="reference",
            source_role="community",
            repo_full_name="org/reference",
            canonical_url="https://github.com/org/reference",
        )

        records = read_sources_from_runtime(tmp_path)
        assert len(records) == 2

        # Check first source
        p = records["github:org/platform"]
        assert p.primary_domain == "deep-research"
        assert p.related_domains == ("coding-agents",)
        assert p.ecosystems == ("langchain", "mcp")
        assert p.capabilities == ("deep-research", "web-search")
        assert p.artifact_role == "agent-platform"
        assert p.source_role == "official-extension"
        assert p.authority_level == "official"
        assert p.lifecycle_status == "active"
        assert p.repo_url == "https://github.com/org/platform"
        assert p.title == "org/platform"

        # Check second source
        r = records["github:org/reference"]
        assert r.primary_domain == "deep-research"
        assert r.ecosystems == ("langchain",)
        assert r.capabilities == ("deep-research",)
        assert r.artifact_role == "reference"
        assert r.source_role == "community"

        # Summarize
        summary = summarize_graph_axes(records)
        assert summary["primary_domain"] == {"deep-research": 2}
        assert summary["ecosystems"] == {"langchain": 2, "mcp": 1}
        assert summary["capabilities"] == {"deep-research": 2, "web-search": 1}
        assert summary["artifact_role"] == {"agent-platform": 1, "reference": 1}

    def test_no_files_written_by_reader(self, tmp_path: Path):
        """Reader should never write files."""
        _write_source(tmp_path, "org_a", source_id="github:org/a")
        before = sorted(tmp_path.rglob("*"))
        _ = read_sources_from_runtime(tmp_path)
        after = sorted(tmp_path.rglob("*"))
        assert before == after
