"""Tests for codex_vault_pipeline.graph.source_card_generator."""

from pathlib import Path
from typing import Optional, Tuple

import pytest

from codex_vault_pipeline.graph.source_reader import GraphSourceRecord
from codex_vault_pipeline.graph.source_card_generator import (
    SourceCardSpec,
    build_source_card,
    render_source_card_markdown,
    safe_source_slug,
    write_source_cards,
)
from codex_vault_pipeline.graph.hub_generator import safe_source_id as hub_safe_source_id


# ---------------------------------------------------------------------------
# safe_source_slug
# ---------------------------------------------------------------------------


class TestSafeSourceSlug:
    def test_matches_hub_generator(self):
        """Must produce identical slugs to hub_generator.safe_source_id."""
        assert safe_source_slug("github:org/repo") == hub_safe_source_id("github:org/repo")

    def test_github_style(self):
        assert safe_source_slug("github:owner/repo") == "github-owner-repo"

    def test_simple_id(self):
        assert safe_source_slug("simple") == "simple"

    def test_leading_trailing_separators(self):
        assert safe_source_slug(":foo:") == "foo"


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
    title: Optional[str] = None,
    repo_url: Optional[str] = None,
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
        title=title,
        repo_url=repo_url,
    )


# ---------------------------------------------------------------------------
# build_source_card
# ---------------------------------------------------------------------------


class TestBuildSourceCard:
    def test_minimal_record(self):
        rec = _rec("github:org/minimal")
        card = build_source_card(rec, Path("/out"))
        assert card.source_id == "github:org/minimal"
        assert card.title == "github:org/minimal"  # falls back to source_id
        assert card.path == Path("/out/sources/github-org-minimal.md")
        assert card.primary_domain is None
        assert "graph/source" in card.tags

    def test_full_record(self):
        rec = _rec(
            source_id="github:org/full",
            primary_domain="deep-research",
            related_domains=("coding-agents",),
            ecosystems=("langchain", "mcp"),
            capabilities=("deep-research", "web-search"),
            artifact_role="agent-platform",
            source_role="official-extension",
            authority_level="official",
            lifecycle_status="active",
            title="org/full",
        )
        card = build_source_card(rec, Path("/out"))
        assert card.source_id == "github:org/full"
        assert card.title == "org/full"
        assert card.primary_domain == "deep-research"
        assert card.path == Path("/out/sources/github-org-full.md")
        # Tags
        tag_strs = set(card.tags)
        assert "graph/source" in tag_strs
        assert "graph/domain/deep-research" in tag_strs
        assert "graph/artifact-role/agent-platform" in tag_strs
        assert "graph/source-role/official-extension" in tag_strs
        assert "graph/authority-level/official" in tag_strs
        assert "graph/lifecycle-status/active" in tag_strs

    def test_hub_links_include_all_axes(self):
        rec = _rec(
            source_id="github:org/a",
            primary_domain="deep-research",
            related_domains=("coding-agents",),
            ecosystems=("langchain",),
            capabilities=("deep-research", "web-search"),
            artifact_role="agent-platform",
            knowledge_status="candidate",
        )
        card = build_source_card(rec, Path("/out"))
        link_str = " ".join(card.hub_links)
        assert "_graph/domains/deep-research" in link_str
        assert "_graph/related-domains/coding-agents" in link_str
        assert "_graph/ecosystems/langchain" in link_str
        assert "_graph/capabilities/deep-research" in link_str
        assert "_graph/capabilities/web-search" in link_str
        assert "_graph/artifact-roles/agent-platform" in link_str
        assert "_graph/knowledge-statuses/candidate" in link_str

    def test_deterministic_tags(self):
        rec = _rec(
            source_id="github:org/a",
            primary_domain="hermes",
            artifact_role="agent-platform",
        )
        card1 = build_source_card(rec, Path("/out"))
        card2 = build_source_card(rec, Path("/out"))
        assert card1.tags == card2.tags

    def test_title_from_record(self):
        rec = _rec("github:org/titled", title="Org/Titled")
        card = build_source_card(rec, Path("/out"))
        assert card.title == "Org/Titled"

    def test_title_fallback_to_source_id(self):
        rec = _rec("github:org/fallback")
        card = build_source_card(rec, Path("/out"))
        assert card.title == "github:org/fallback"

    def test_empty_tuples_produce_no_links(self):
        rec = _rec(
            source_id="github:org/empty",
            primary_domain="test",
            ecosystems=(),
            capabilities=(),
        )
        card = build_source_card(rec, Path("/out"))
        # No ecosystem or capability links
        link_text = " ".join(card.hub_links)
        assert "Ecosystem" not in link_text
        assert "Capability" not in link_text
        assert "_graph/domains/" in link_text


# ---------------------------------------------------------------------------
# render_source_card_markdown
# ---------------------------------------------------------------------------


class TestRenderSourceCard:
    def test_contains_frontmatter(self):
        rec = _rec("github:org/fm", primary_domain="deep-research")
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        assert md.startswith("---")
        assert "graph_node_type: source" in md
        assert "source_id: github:org/fm" in md
        assert "primary_domain: deep-research" in md

    def test_contains_tags_in_frontmatter(self):
        rec = _rec("github:org/tags", primary_domain="hermes", artifact_role="agent-platform")
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        assert "  - graph/source" in md
        assert "  - graph/domain/hermes" in md
        assert "  - graph/artifact-role/agent-platform" in md

    def test_contains_graph_links_section(self):
        rec = _rec("github:org/gl", primary_domain="deep-research", ecosystems=("langchain",))
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        assert "## Graph Links" in md
        assert "**Domain**:" in md
        assert "**Ecosystem**:" in md
        assert "[[_graph/domains/deep-research|deep-research]]" in md
        assert "[[_graph/ecosystems/langchain|langchain]]" in md

    def test_contains_source_metadata_section(self):
        rec = _rec("github:org/md", primary_domain="deep-research", artifact_role="agent-platform")
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        assert "## Source Metadata" in md
        assert "**Source ID**:" in md
        assert "**Primary domain**:" in md

    def test_missing_fields_no_crash(self):
        rec = _rec("github:org/none")
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        assert "graph_node_type: source" in md
        assert "graph/source" in md

    def test_multiple_ecosystem_links(self):
        rec = _rec(
            "github:org/multi",
            primary_domain="test",
            ecosystems=("langchain", "mcp"),
        )
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        assert "[[_graph/ecosystems/langchain|langchain]]" in md
        assert "[[_graph/ecosystems/mcp|mcp]]" in md

    def test_metadata_from_raw_field(self):
        """When raw dict is available, prefer raw field values."""
        raw = {
            "source_id": "github:org/rawtest",
            "primary_domain": "deep-research",
            "ecosystems": ["langchain", "mcp"],
            "capabilities": ["deep-research"],
            "artifact_role": "agent-platform",
        }
        rec = GraphSourceRecord(
            source_id="github:org/rawtest",
            source_path=Path("/dev/null"),
            primary_domain="deep-research",
            ecosystems=("langchain", "mcp"),
            capabilities=("deep-research",),
            artifact_role="agent-platform",
            raw=raw,
        )
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        # Should show raw-based list fields
        assert "langchain, mcp" in md
        assert "deep-research" in md

    def test_metadata_fallback_when_no_raw(self):
        rec = _rec("github:org/fall", primary_domain="test", artifact_role="reference")
        card = build_source_card(rec, Path("/out"))
        md = render_source_card_markdown(card, rec)
        # Fallback uses record attributes
        assert "test" in md
        assert "reference" in md


# ---------------------------------------------------------------------------
# write_source_cards
# ---------------------------------------------------------------------------


class TestWriteSourceCards:
    def test_writes_files_under_output_dir(self, tmp_path: Path):
        records = {
            "a": _rec("github:org/alpha", primary_domain="hermes"),
            "b": _rec("github:org/beta", primary_domain="n8n"),
        }
        written = write_source_cards(records, tmp_path)
        assert len(written) == 2
        for p in written:
            assert p.parent.name == "sources"
            assert p.suffix == ".md"
            assert p.is_file()

    def test_deterministic_order(self, tmp_path: Path):
        records = {
            "z": _rec("github:org/zulu", primary_domain="z"),
            "a": _rec("github:org/alpha", primary_domain="a"),
        }
        written = write_source_cards(records, tmp_path)
        # Sorted by source_id → alpha first, zulu second
        names = [p.name for p in written]
        assert names == sorted(names)

    def test_idempotent_write(self, tmp_path: Path):
        records = {"a": _rec("github:org/a", primary_domain="hermes")}
        written1 = write_source_cards(records, tmp_path)
        written2 = write_source_cards(records, tmp_path)
        assert len(written1) == 1
        assert written2 == []  # no changes detected

    def test_content_change_triggers_rewrite(self, tmp_path: Path):
        records = {"a": _rec("github:org/a", primary_domain="hermes")}
        written1 = write_source_cards(records, tmp_path)
        assert len(written1) == 1
        written1[0].write_text("---\nmodified: true\n---\n")
        written2 = write_source_cards(records, tmp_path)
        assert len(written2) == 1

    def test_no_writes_for_empty_records(self, tmp_path: Path):
        written = write_source_cards({}, tmp_path)
        assert written == []

    def test_no_writes_outside_output_dir(self, tmp_path: Path):
        records = {"a": _rec("github:org/a", primary_domain="test")}
        output = tmp_path / "graph"
        written = write_source_cards(records, output)
        for p in written:
            assert str(p).startswith(str(output))
        # No files in tmp_path root
        root_files = [p for p in tmp_path.iterdir() if p.is_file()]
        assert len(root_files) == 0

    def test_writes_all_records(self, tmp_path: Path):
        records = {
            "a": _rec("github:org/a", primary_domain="d1"),
            "b": _rec("github:org/b", primary_domain="d2"),
            "c": _rec("github:org/c", primary_domain="d3"),
        }
        written = write_source_cards(records, tmp_path)
        assert len(written) == 3

    def test_artifact_role_in_frontmatter(self, tmp_path: Path):
        records = {"a": _rec("github:org/a", primary_domain="test", artifact_role="agent-platform")}
        write_source_cards(records, tmp_path)
        md = (tmp_path / "sources" / "github-org-a.md").read_text()
        assert "artifact_role: agent-platform" in md
