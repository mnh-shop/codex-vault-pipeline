"""Tests for codex_vault_pipeline.graph.hub_generator."""

from pathlib import Path
from typing import Optional, Tuple

import pytest

from codex_vault_pipeline.graph.source_reader import GraphSourceRecord
from codex_vault_pipeline.graph.hub_generator import (
    HubSpec,
    collect_hubs,
    render_hub_markdown,
    safe_source_id,
    slugify_graph_value,
    write_hubs,
)


# ---------------------------------------------------------------------------
# slugify_graph_value
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_already_safe(self):
        assert slugify_graph_value("deep-research") == "deep-research"

    def test_lowercased(self):
        assert slugify_graph_value("LangChain") == "langchain"

    def test_strips_whitespace(self):
        assert slugify_graph_value("  n8n  ") == "n8n"

    def test_replaces_slashes(self):
        assert slugify_graph_value("AI/ML") == "ai-ml"

    def test_replaces_spaces(self):
        assert slugify_graph_value("n8n Workflows") == "n8n-workflows"

    def test_collapses_runs_of_separators(self):
        assert slugify_graph_value("foo // bar /// baz") == "foo-bar-baz"

    def test_strips_leading_trailing_separators(self):
        assert slugify_graph_value("__hello__") == "hello"

    def test_handles_mixed_case_and_special_chars(self):
        assert slugify_graph_value("Hermes Agent v3 (beta)") == "hermes-agent-v3-beta"


# ---------------------------------------------------------------------------
# safe_source_id
# ---------------------------------------------------------------------------


class TestSafeSourceId:
    def test_github_source(self):
        assert safe_source_id("github:org/repo") == "github-org-repo"

    def test_already_safe(self):
        assert safe_source_id("simple-id") == "simple-id"

    def test_backslash_replaced(self):
        assert safe_source_id("windows\\path") == "windows-path"

    def test_collapses_double_hyphens(self):
        assert safe_source_id("a::b") == "a-b"

    def test_strips_edge_hyphens(self):
        assert safe_source_id(":edge:") == "edge"


# ---------------------------------------------------------------------------
# Helper: factory for GraphSourceRecord
# ---------------------------------------------------------------------------


def _rec(
    source_id: str,
    *,
    primary_domain: Optional[str] = None,
    related_domains: Tuple[str, ...] = (),
    ecosystems: Tuple[str, ...] = (),
    capabilities: Tuple[str, ...] = (),
    artifact_role: Optional[str] = None,
    source_role: Optional[str] = None,
    authority_level: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    knowledge_status: Optional[str] = None,
) -> GraphSourceRecord:
    return GraphSourceRecord(
        source_id=source_id,
        source_path=Path("/dev/null"),
        primary_domain=primary_domain,
        related_domains=related_domains,
        ecosystems=ecosystems,
        capabilities=capabilities,
        artifact_role=artifact_role,
        source_role=source_role,
        authority_level=authority_level,
        lifecycle_status=lifecycle_status,
        knowledge_status=knowledge_status,
    )


# ---------------------------------------------------------------------------
# collect_hubs
# ---------------------------------------------------------------------------


class TestCollectHubs:
    def test_collects_domain_hubs(self):
        records = {
            "a": _rec("a", primary_domain="hermes"),
            "b": _rec("b", primary_domain="n8n"),
            "c": _rec("c", primary_domain="hermes"),
        }
        hubs = collect_hubs(records, Path("/out"))
        domain_hubs = [h for h in hubs if h.axis == "domains"]
        assert len(domain_hubs) == 2
        d0 = domain_hubs[0]
        d1 = domain_hubs[1]
        # Ordered by value
        assert d0.value == "hermes"
        assert d0.source_ids == ("a", "c")
        assert d0.count == 2
        assert d1.value == "n8n"
        assert d1.source_ids == ("b",)

    def test_collects_ecosystem_hubs(self):
        records = {
            "a": _rec("a", primary_domain="h", ecosystems=("langchain", "mcp")),
            "b": _rec("b", primary_domain="n", ecosystems=("langchain",)),
            "c": _rec("c", primary_domain="d", ecosystems=("rag",)),
        }
        hubs = collect_hubs(records, Path("/out"))
        eco_hubs = {h.value: h for h in hubs if h.axis == "ecosystems"}
        assert set(eco_hubs.keys()) == {"langchain", "mcp", "rag"}
        assert eco_hubs["langchain"].source_ids == ("a", "b")
        assert eco_hubs["mcp"].source_ids == ("a",)
        assert eco_hubs["rag"].source_ids == ("c",)

    def test_collects_capability_hubs(self):
        records = {
            "a": _rec("a", primary_domain="h", capabilities=("deep-research", "web-search")),
            "b": _rec("b", primary_domain="n", capabilities=("web-search",)),
        }
        hubs = collect_hubs(records, Path("/out"))
        cap_hubs = {h.value: h for h in hubs if h.axis == "capabilities"}
        assert set(cap_hubs.keys()) == {"deep-research", "web-search"}

    def test_collects_artifact_role_hubs(self):
        records = {
            "a": _rec("a", primary_domain="h", artifact_role="agent-platform"),
            "b": _rec("b", primary_domain="n", artifact_role="reference"),
            "c": _rec("c", primary_domain="d", artifact_role="agent-platform"),
        }
        hubs = collect_hubs(records, Path("/out"))
        role_hubs = {h.value: h for h in hubs if h.axis == "artifact-roles"}
        assert role_hubs["agent-platform"].source_ids == ("a", "c")

    def test_collects_related_domains(self):
        records = {
            "a": _rec("a", primary_domain="h", related_domains=("n8n", "osint")),
            "b": _rec("b", primary_domain="n", related_domains=("osint",)),
        }
        hubs = collect_hubs(records, Path("/out"))
        rd_hubs = {h.value: h for h in hubs if h.axis == "related-domains"}
        assert rd_hubs["osint"].source_ids == ("a", "b")
        assert rd_hubs["n8n"].source_ids == ("a",)

    def test_empty_records(self):
        hubs = collect_hubs({}, Path("/out"))
        assert hubs == []

    def test_hub_path_derived_from_output_dir(self):
        records = {"a": _rec("a", primary_domain="deep-research")}
        hubs = collect_hubs(records, Path("/tmp/graph"))
        assert hubs[0].path == Path("/tmp/graph/domains/deep-research.md")

    def test_deterministic_order(self):
        records = {
            "z": _rec("z", primary_domain="zebra"),
            "a": _rec("a", primary_domain="alpha"),
            "m": _rec("m", primary_domain="mango"),
        }
        hubs = collect_hubs(records, Path("/out"))
        values = [h.value for h in hubs]
        assert values == sorted(values)

    def test_min_sources_filters(self):
        records = {
            "a": _rec("a", primary_domain="unique"),
            "b": _rec("b", primary_domain="shared"),
            "c": _rec("c", primary_domain="shared"),
        }
        hubs = collect_hubs(records, Path("/out"), min_sources=2)
        values = {h.value for h in hubs}
        assert "shared" in values
        assert "unique" not in values

    def test_source_ids_are_sorted(self):
        records = {
            "b": _rec("b", primary_domain="test"),
            "a": _rec("a", primary_domain="test"),
            "c": _rec("c", primary_domain="test"),
        }
        hubs = collect_hubs(records, Path("/out"))
        d = [h for h in hubs if h.axis == "domains"][0]
        assert d.source_ids == ("a", "b", "c")

    def test_knowledge_status_hub(self):
        records = {
            "a": _rec("a", primary_domain="h", knowledge_status="candidate"),
            "b": _rec("b", primary_domain="n", knowledge_status="canonical"),
            "c": _rec("c", primary_domain="d", knowledge_status="candidate"),
        }
        hubs = collect_hubs(records, Path("/out"))
        ks_hubs = {h.value: h for h in hubs if h.axis == "knowledge-statuses"}
        assert ks_hubs["candidate"].source_ids == ("a", "c")
        assert ks_hubs["canonical"].source_ids == ("b",)

    def test_all_nine_axes_generated(self):
        """Verify all HUB_AXES produce hub specs."""
        records = {
            "a": _rec(
                "a",
                primary_domain="h",
                related_domains=("n8n",),
                ecosystems=("langchain",),
                capabilities=("rag",),
                artifact_role="agent-platform",
                source_role="official",
                authority_level="official",
                lifecycle_status="active",
                knowledge_status="candidate",
            ),
        }
        hubs = collect_hubs(records, Path("/out"))
        axes_found = {h.axis for h in hubs}
        expected_axes = {
            "domains",
            "related-domains",
            "ecosystems",
            "capabilities",
            "artifact-roles",
            "source-roles",
            "authority-levels",
            "lifecycle-statuses",
            "knowledge-statuses",
        }
        assert axes_found == expected_axes


# ---------------------------------------------------------------------------
# render_hub_markdown
# ---------------------------------------------------------------------------


class TestRenderHub:
    def test_contains_frontmatter(self):
        hub = HubSpec(
            axis="domains",
            value="deep-research",
            title="Domain: deep-research",
            path=Path("/out/domains/deep-research.md"),
            source_ids=("a", "b"),
        )
        md = render_hub_markdown(hub)
        assert md.startswith("---")
        assert "graph_node_type: hub" in md
        assert "graph_axis: domains" in md
        assert "graph_value: deep-research" in md
        assert "source_count: 2" in md

    def test_contains_tags(self):
        hub = HubSpec(
            axis="ecosystems",
            value="langchain",
            title="Ecosystem: langchain",
            path=Path("/out/ecosystems/langchain.md"),
            source_ids=("a",),
        )
        md = render_hub_markdown(hub)
        assert "graph/hub" in md
        assert "graph/axis/ecosystems" in md
        assert "graph/ecosystems/langchain" in md

    def test_contains_section_heading(self):
        hub = HubSpec(
            axis="domains",
            value="hermes",
            title="Domain: hermes",
            path=Path("/out/domains/hermes.md"),
            source_ids=("a",),
        )
        md = render_hub_markdown(hub)
        assert "# Domain: hermes" in md
        assert "## Sources" in md

    def test_source_links_point_to_future_source_cards(self):
        hub = HubSpec(
            axis="domains",
            value="test",
            title="Domain: test",
            path=Path("/out/domains/test.md"),
            source_ids=("github:org/repo",),
        )
        md = render_hub_markdown(hub)
        assert "_graph/sources/github-org-repo" in md
        assert "github:org/repo" in md

    def test_multiple_sources_listed(self):
        hub = HubSpec(
            axis="domains",
            value="test",
            title="Domain: test",
            path=Path("/out/domains/test.md"),
            source_ids=("a", "b", "c"),
        )
        md = render_hub_markdown(hub)
        # All three sources listed
        assert "a" in md
        assert "b" in md
        assert "c" in md

    def test_count_in_frontmatter_is_correct(self):
        hub = HubSpec(
            axis="domains",
            value="test",
            title="Domain: test",
            path=Path("/out/domains/test.md"),
            source_ids=("a", "b", "c"),
        )
        md = render_hub_markdown(hub)
        # Count should be 3
        assert "source_count: 3" in md


# ---------------------------------------------------------------------------
# write_hubs
# ---------------------------------------------------------------------------


class TestWriteHubs:
    def test_writes_files_under_output_dir(self, tmp_path: Path):
        records = {
            "a": _rec("a", primary_domain="hermes"),
            "b": _rec("b", primary_domain="n8n"),
        }
        written = write_hubs(records, tmp_path)
        assert len(written) >= 2  # at least domain hubs
        for p in written:
            assert p.parent == tmp_path / "domains"
            assert p.suffix == ".md"
            assert p.is_file()

    def test_writes_nine_axes_subdirs(self, tmp_path: Path):
        records = {
            "a": _rec(
                "a",
                primary_domain="test",
                related_domains=("r1",),
                ecosystems=("e1",),
                capabilities=("c1",),
                artifact_role="ar1",
                source_role="sr1",
                authority_level="al1",
                lifecycle_status="ls1",
                knowledge_status="ks1",
            ),
        }
        written = write_hubs(records, tmp_path)
        subdirs = {p.parent.name for p in written}
        expected = {
            "domains",
            "related-domains",
            "ecosystems",
            "capabilities",
            "artifact-roles",
            "source-roles",
            "authority-levels",
            "lifecycle-statuses",
            "knowledge-statuses",
        }
        assert subdirs == expected

    def test_idempotent_write(self, tmp_path: Path):
        records = {"a": _rec("a", primary_domain="hermes")}
        written1 = write_hubs(records, tmp_path)
        written2 = write_hubs(records, tmp_path)
        # Second call should detect no changes and not re-write
        assert written2 == []
        # Files still exist from first call
        assert written1[0].is_file()

    def test_no_writes_for_empty_records(self, tmp_path: Path):
        written = write_hubs({}, tmp_path)
        assert written == []

    def test_no_writes_outside_output_dir(self, tmp_path: Path):
        records = {"a": _rec("a", primary_domain="hermes")}
        output = tmp_path / "graph"
        written = write_hubs(records, output)
        for p in written:
            assert str(p).startswith(str(output))
        # No files in tmp_path root
        root_files = [p for p in tmp_path.iterdir() if p.is_file()]
        assert len(root_files) == 0

    def test_min_sources_filter_applied(self, tmp_path: Path):
        records = {
            "a": _rec("a", primary_domain="shared"),
            "b": _rec("b", primary_domain="shared"),
            "c": _rec("c", primary_domain="lonely"),
        }
        written = write_hubs(records, tmp_path, min_sources=2)
        written_paths = {str(p) for p in written}
        # "lonely" has only 1 source → should not be written
        assert not any("lonely" in str(p) for p in written)
        assert any("shared" in str(p) for p in written)

    def test_content_changed_triggers_rewrite(self, tmp_path: Path):
        records = {"a": _rec("a", primary_domain="hermes")}
        written1 = write_hubs(records, tmp_path)
        assert len(written1) == 1

        # Modify the file manually
        written1[0].write_text("---\nmodified: true\n---\n")
        written2 = write_hubs(records, tmp_path)
        assert len(written2) == 1  # re-written because content changed

    def test_hub_path_in_write_matches_slug(self, tmp_path: Path):
        records = {"a": _rec("a", primary_domain="Deep Research")}
        written = write_hubs(records, tmp_path)
        domain_paths = [p for p in written if "domains" in str(p)]
        assert len(domain_paths) == 1
        assert domain_paths[0].name == "deep-research.md"
