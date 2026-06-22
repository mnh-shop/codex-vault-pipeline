"""Tests for codex_vault_pipeline.graph.wikilink_planner."""

from pathlib import Path
from typing import Optional, Tuple

import pytest

from codex_vault_pipeline.graph.source_reader import GraphSourceRecord
from codex_vault_pipeline.graph.wikilink_planner import (
    WikilinkInsertionPlan,
    extract_source_id_from_note,
    find_markdown_notes,
    plan_wikilink_insertions,
    render_graph_links_section,
)


# ---------------------------------------------------------------------------
# Helper: factory for GraphSourceRecord
# ---------------------------------------------------------------------------


def _rec(
    source_id: str,
    *,
    primary_domain: Optional[str] = None,
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
        source_path=Path("/fake/" + source_id.replace(":", "-")),
        primary_domain=primary_domain,
        ecosystems=ecosystems,
        capabilities=capabilities,
        artifact_role=artifact_role,
        source_role=source_role,
        authority_level=authority_level,
        lifecycle_status=lifecycle_status,
        knowledge_status=knowledge_status,
    )


# ---------------------------------------------------------------------------
# find_markdown_notes
# ---------------------------------------------------------------------------


class TestFindMarkdownNotes:
    def test_finds_regular_notes(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "note1.md").write_text("hello", encoding="utf-8")
        (wiki / "sub").mkdir(parents=True, exist_ok=True)
        (wiki / "sub" / "note2.md").write_text("hello", encoding="utf-8")

        notes = find_markdown_notes(wiki)
        assert len(notes) == 2

    def test_excludes_graph_subdir(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "note.md").write_text("hello", encoding="utf-8")
        graph = wiki / "_graph" / "domains"
        graph.mkdir(parents=True, exist_ok=True)
        (graph / "hermes.md").write_text("graph note", encoding="utf-8")
        sources = wiki / "_graph" / "sources"
        sources.mkdir(parents=True, exist_ok=True)
        (sources / "card.md").write_text("card", encoding="utf-8")

        notes = find_markdown_notes(wiki)
        assert len(notes) == 1
        assert "note.md" in str(notes[0])

    def test_deterministic_order(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Create in reverse alphabetical order.
        (wiki / "zeta.md").write_text("a", encoding="utf-8")
        (wiki / "alpha.md").write_text("b", encoding="utf-8")
        (wiki / "beta.md").write_text("c", encoding="utf-8")

        notes = find_markdown_notes(wiki)
        names = [p.name for p in notes]
        assert names == ["alpha.md", "beta.md", "zeta.md"]

    def test_returns_absolute_paths(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "note.md").write_text("hello", encoding="utf-8")

        notes = find_markdown_notes(wiki)
        assert notes[0].is_absolute()

    def test_empty_wiki(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        notes = find_markdown_notes(wiki)
        assert notes == ()


# ---------------------------------------------------------------------------
# extract_source_id_from_note
# ---------------------------------------------------------------------------


class TestExtractSourceIdFromNote:
    def test_bare_value(self):
        text = "---\nsource_id: github:owner/repo\n---\n# Content"
        assert extract_source_id_from_note(text) == "github:owner/repo"

    def test_quoted_value(self):
        text = '---\nsource_id: "github:owner/repo"\n---\n# Content'
        assert extract_source_id_from_note(text) == "github:owner/repo"

    def test_single_quoted_value(self):
        text = "---\nsource_id: 'github:owner/repo'\n---\n# Content"
        assert extract_source_id_from_note(text) == "github:owner/repo"

    def test_with_other_frontmatter(self):
        text = """---
title: My Note
source_id: github:org/foo
tags:
  - test
---
# Content
"""
        assert extract_source_id_from_note(text) == "github:org/foo"

    def test_no_frontmatter(self):
        text = "# Just a heading\n\nSome content."
        assert extract_source_id_from_note(text) is None

    def test_no_source_id_in_frontmatter(self):
        text = "---\ntitle: My Note\ntags: []\n---\n# Content"
        assert extract_source_id_from_note(text) is None

    def test_source_id_after_frontmatter_is_ignored(self):
        """Only source_id inside frontmatter is extracted."""
        text = "---\ntitle: My Note\n---\n# Content\n\nsource_id: github:should-not-match"
        assert extract_source_id_from_note(text) is None

    def test_empty_source_id(self):
        text = "---\nsource_id: \n---\n# Content"
        assert extract_source_id_from_note(text) == ""


# ---------------------------------------------------------------------------
# render_graph_links_section
# ---------------------------------------------------------------------------


class TestRenderGraphLinksSection:
    def test_contains_begin_marker(self):
        rec = _rec("github:org/test", primary_domain="hermes-agent")
        section = render_graph_links_section(rec)
        assert "<!-- BEGIN GENERATED CODEX GRAPH LINKS -->" in section

    def test_contains_end_marker(self):
        rec = _rec("github:org/test", primary_domain="hermes-agent")
        section = render_graph_links_section(rec)
        assert "<!-- END GENERATED CODEX GRAPH LINKS -->" in section

    def test_contains_source_card_link(self):
        rec = _rec("github:org/test", primary_domain="hermes-agent")
        section = render_graph_links_section(rec)
        assert "[[_graph/sources/github-org-test|source card]]" in section

    def test_contains_domain_link(self):
        rec = _rec("github:org/test", primary_domain="hermes-agent")
        section = render_graph_links_section(rec)
        assert "[[_graph/domains/hermes-agent|hermes-agent]]" in section

    def test_contains_ecosystem_link(self):
        rec = _rec("github:org/test", ecosystems=("python", "langchain"))
        section = render_graph_links_section(rec)
        assert "[[_graph/ecosystems/python|python]]" in section
        assert "[[_graph/ecosystems/langchain|langchain]]" in section

    def test_contains_capability_link(self):
        rec = _rec("github:org/test", capabilities=("deep-research",))
        section = render_graph_links_section(rec)
        assert "[[_graph/capabilities/deep-research|deep-research]]" in section

    def test_contains_artifact_role_link(self):
        rec = _rec("github:org/test", artifact_role="source-code")
        section = render_graph_links_section(rec)
        assert "[[_graph/artifact-roles/source-code|source-code]]" in section

    def test_contains_source_role_link(self):
        rec = _rec("github:org/test", source_role="core")
        section = render_graph_links_section(rec)
        assert "[[_graph/source-roles/core|core]]" in section

    def test_contains_authority_level_link(self):
        rec = _rec("github:org/test", authority_level="official")
        section = render_graph_links_section(rec)
        assert "[[_graph/authority-levels/official|official]]" in section

    def test_contains_lifecycle_status_link(self):
        rec = _rec("github:org/test", lifecycle_status="active")
        section = render_graph_links_section(rec)
        assert "[[_graph/lifecycle-statuses/active|active]]" in section

    def test_contains_knowledge_status_link(self):
        rec = _rec("github:org/test", knowledge_status="canonical")
        section = render_graph_links_section(rec)
        assert "[[_graph/knowledge-statuses/canonical|canonical]]" in section

    def test_deterministic_order(self):
        rec = _rec(
            "github:org/test",
            primary_domain="hermes-agent",
            ecosystems=("python",),
            capabilities=("deep-research",),
        )
        s1 = render_graph_links_section(rec)
        s2 = render_graph_links_section(rec)
        assert s1 == s2

    def test_has_graph_links_heading(self):
        rec = _rec("github:org/test", primary_domain="n8n")
        section = render_graph_links_section(rec)
        assert "## Graph Links" in section


# ---------------------------------------------------------------------------
# plan_wikilink_insertions
# ---------------------------------------------------------------------------


class TestPlanWikilinkInsertions:
    """Integration: notes + records → plans."""

    def test_plans_for_matching_notes(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "alpha.md").write_text(
            "---\nsource_id: github:org/alpha\n---\n# Alpha\n",
            encoding="utf-8",
        )
        (wiki / "beta.md").write_text(
            "---\nsource_id: github:org/beta\n---\n# Beta\n",
            encoding="utf-8",
        )

        records = {
            "github:org/alpha": _rec("github:org/alpha", primary_domain="hermes-agent"),
            "github:org/beta": _rec("github:org/beta", primary_domain="n8n"),
        }

        plans = plan_wikilink_insertions(wiki, records)
        assert len(plans) == 2

    def test_skips_notes_without_source_id(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "no-source.md").write_text(
            "---\ntitle: No source\n---\n# Content\n", encoding="utf-8"
        )
        records = {}
        plans = plan_wikilink_insertions(wiki, records)
        assert plans == ()

    def test_skips_notes_with_unknown_source_id(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "unknown.md").write_text(
            "---\nsource_id: github:org/unknown\n---\n# Content\n",
            encoding="utf-8",
        )
        records = {"github:org/other": _rec("github:org/other")}
        plans = plan_wikilink_insertions(wiki, records)
        assert plans == ()

    def test_excludes_graph_notes(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "regular.md").write_text(
            "---\nsource_id: github:org/alpha\n---\n# Regular\n",
            encoding="utf-8",
        )
        graph = wiki / "_graph" / "sources"
        graph.mkdir(parents=True, exist_ok=True)
        (graph / "card.md").write_text(
            "---\nsource_id: github:org/alpha\n---\n# Card\n",
            encoding="utf-8",
        )

        records = {"github:org/alpha": _rec("github:org/alpha")}
        plans = plan_wikilink_insertions(wiki, records)
        assert len(plans) == 1

    def test_marks_existing_section(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "done.md").write_text(
            "---\nsource_id: github:org/alpha\n---\n# Alpha\n\n"
            "<!-- BEGIN GENERATED CODEX GRAPH LINKS -->\n"
            "## Graph Links\n\n"
            "- **Source card**: [[_graph/sources/...]]\n"
            "<!-- END GENERATED CODEX GRAPH LINKS -->\n",
            encoding="utf-8",
        )
        records = {"github:org/alpha": _rec("github:org/alpha")}
        plans = plan_wikilink_insertions(wiki, records)
        assert len(plans) == 1
        assert plans[0].already_has_graph_section is True
        assert plans[0].graph_links_markdown == ""

    def test_no_existing_section(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "fresh.md").write_text(
            "---\nsource_id: github:org/alpha\n---\n# Alpha\n\nSome content.",
            encoding="utf-8",
        )
        records = {"github:org/alpha": _rec("github:org/alpha", primary_domain="hermes-agent")}
        plans = plan_wikilink_insertions(wiki, records)
        assert len(plans) == 1
        assert plans[0].already_has_graph_section is False
        assert "<!-- BEGIN GENERATED CODEX GRAPH LINKS -->" in plans[0].graph_links_markdown

    def test_deterministic_order(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "zeta.md").write_text(
            "---\nsource_id: github:org/z\n---\n# Z\n", encoding="utf-8"
        )
        (wiki / "alpha.md").write_text(
            "---\nsource_id: github:org/a\n---\n# A\n", encoding="utf-8"
        )
        records = {
            "github:org/a": _rec("github:org/a"),
            "github:org/z": _rec("github:org/z"),
        }
        plans = plan_wikilink_insertions(wiki, records)
        assert [p.source_id for p in plans] == ["github:org/a", "github:org/z"]

    def test_no_files_modified(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        note = wiki / "test.md"
        original = "---\nsource_id: github:org/test\n---\n# Test\n"
        note.write_text(original, encoding="utf-8")

        records = {"github:org/test": _rec("github:org/test")}
        plan_wikilink_insertions(wiki, records)

        # File content must be unchanged.
        assert note.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# WikilinkInsertionPlan dataclass
# ---------------------------------------------------------------------------


class TestWikilinkInsertionPlan:
    def test_is_frozen(self):
        plan = WikilinkInsertionPlan(
            note_path=Path("/p"),
            source_id="x",
            graph_links_markdown="links",
            already_has_graph_section=False,
        )
        with pytest.raises(AttributeError):
            plan.source_id = "y"  # type: ignore[misc]
