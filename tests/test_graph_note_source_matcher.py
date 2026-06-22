"""Tests for codex_vault_pipeline.graph.note_source_matcher."""

from pathlib import Path
from typing import Optional

import pytest

from codex_vault_pipeline.graph.source_reader import GraphSourceRecord
from codex_vault_pipeline.graph.note_source_matcher import (
    NoteSourceMatch,
    match_note_to_source,
    match_notes_to_sources,
    source_match_tokens,
)


# ---------------------------------------------------------------------------
# Helper: factory for GraphSourceRecord
# ---------------------------------------------------------------------------


def _rec(
    source_id: str,
    *,
    repo_url: Optional[str] = None,
) -> GraphSourceRecord:
    return GraphSourceRecord(
        source_id=source_id,
        source_path=Path("/fake/" + source_id.replace(":", "-").replace("/", "-")),
        repo_url=repo_url,
    )


# ---------------------------------------------------------------------------
# source_match_tokens
# ---------------------------------------------------------------------------


class TestSourceMatchTokens:
    def test_source_id_always_present(self):
        tokens = source_match_tokens(_rec("github:org/foo"))
        assert "github:org/foo" in tokens

    def test_repo_url_included(self):
        tokens = source_match_tokens(
            _rec("github:org/foo", repo_url="https://github.com/org/foo")
        )
        assert "https://github.com/org/foo" in tokens

    def test_owner_repo_extracted(self):
        tokens = source_match_tokens(_rec("github:org/foo"))
        assert "org/foo" in tokens

    def test_safe_slug_included(self):
        tokens = source_match_tokens(_rec("github:org/foo"))
        assert "github-org-foo" in tokens

    def test_no_owner_repo_for_website_source(self):
        tokens = source_match_tokens(_rec("website:docs.example.com"))
        assert "docs.example.com" not in tokens  # not an owner/repo

    def test_no_repo_url_when_none(self):
        tokens = source_match_tokens(_rec("github:org/foo"))
        # Only source_id, owner/repo, safe slug.
        assert len(tokens) == 3

    def test_deterministic_order(self):
        t1 = source_match_tokens(_rec("github:org/foo", repo_url="https://github.com/org/foo"))
        t2 = source_match_tokens(_rec("github:org/foo", repo_url="https://github.com/org/foo"))
        assert t1 == t2


# ---------------------------------------------------------------------------
# match_note_to_source — individual rules
# ---------------------------------------------------------------------------


class TestMatchNoteToSource:
    """Single-note, single-record matching."""

    def test_exact_source_id_confidence_100(self):
        text = "This note references github:org/alpha."
        rec = _rec("github:org/alpha")
        m = match_note_to_source(Path("/note.md"), text, rec)
        assert m is not None
        assert m.confidence == 100
        assert "exact_source_id" in m.reasons

    def test_exact_repo_url_confidence_98(self):
        text = "See https://github.com/org/alpha for details."
        rec = _rec("github:org/alpha", repo_url="https://github.com/org/alpha")
        m = match_note_to_source(Path("/note.md"), text, rec)
        assert m is not None
        assert m.confidence == 98
        assert "exact_repo_url" in m.reasons

    def test_exact_owner_repo_confidence_95(self):
        text = "The org/alpha project is maintained by..."
        rec = _rec("github:org/alpha")
        m = match_note_to_source(Path("/note.md"), text, rec)
        assert m is not None
        assert m.confidence == 95
        assert "exact_owner_repo" in m.reasons

    def test_filename_safe_slug_confidence_92(self):
        text = "# Some unrelated content"
        rec = _rec("github:org/alpha")
        # Filename stem contains the safe slug "github-org-alpha".
        note_path = Path("/wiki/github-org-alpha.md")
        m = match_note_to_source(note_path, text, rec)
        assert m is not None
        assert m.confidence == 92
        assert "filename_safe_source_slug" in m.reasons

    def test_filename_slug_partial_match(self):
        """Slug matching works even when filename has extra characters."""
        text = "# Just content"
        rec = _rec("github:org/alpha")
        # Filename is longer but contains the slug.
        note_path = Path("/wiki/github-org-alpha-reference.md")
        m = match_note_to_source(note_path, text, rec)
        assert m is not None
        assert "filename_safe_source_slug" in m.reasons

    def test_repo_name_alone_does_not_match(self):
        """Just the repo name without owner is not enough."""
        text = "The alpha project does cool things."
        rec = _rec("github:org/alpha")
        m = match_note_to_source(Path("/note.md"), text, rec)
        assert m is None

    def test_no_match_returns_none(self):
        text = "Completely unrelated content."
        rec = _rec("github:org/alpha")
        m = match_note_to_source(Path("/note.md"), text, rec)
        assert m is None

    def test_multiple_reasons_all_recorded(self):
        text = "See github:org/alpha at https://github.com/org/alpha"
        rec = _rec("github:org/alpha", repo_url="https://github.com/org/alpha")
        m = match_note_to_source(Path("/note.md"), text, rec)
        assert m is not None
        assert "exact_source_id" in m.reasons
        assert "exact_repo_url" in m.reasons
        assert "exact_owner_repo" in m.reasons
        # Highest confidence wins.
        assert m.confidence == 100


# ---------------------------------------------------------------------------
# match_notes_to_sources — batch
# ---------------------------------------------------------------------------


class TestMatchNotesToSources:
    """Batch matching against a wiki tree."""

    def test_finds_matches(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "alpha.md").write_text(
            "---\ntitle: Alpha\n---\nSee github:org/alpha\n",
            encoding="utf-8",
        )
        records = {
            "github:org/alpha": _rec("github:org/alpha"),
        }
        matches = match_notes_to_sources(wiki, records)
        assert len(matches) == 1
        assert matches[0].source_id == "github:org/alpha"

    def test_skips_graph_subdir(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "regular.md").write_text(
            "---\ntitle: Regular\n---\ngithub:org/alpha\n",
            encoding="utf-8",
        )
        graph = wiki / "_graph" / "sources"
        graph.mkdir(parents=True, exist_ok=True)
        (graph / "github-org-alpha.md").write_text(
            "---\nsource_id: github:org/alpha\n---\n",
            encoding="utf-8",
        )
        records = {"github:org/alpha": _rec("github:org/alpha")}
        matches = match_notes_to_sources(wiki, records)
        assert len(matches) == 1

    def test_no_match_returns_empty(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "note.md").write_text("# Unrelated\n", encoding="utf-8")
        records = {"github:org/alpha": _rec("github:org/alpha")}
        matches = match_notes_to_sources(wiki, records)
        assert matches == ()

    def test_deterministic_order(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "zeta.md").write_text("see github:org/z\n", encoding="utf-8")
        (wiki / "alpha.md").write_text("see github:org/a\n", encoding="utf-8")
        records = {
            "github:org/a": _rec("github:org/a"),
            "github:org/z": _rec("github:org/z"),
        }
        matches = match_notes_to_sources(wiki, records)
        assert [m.source_id for m in matches] == ["github:org/a", "github:org/z"]

    def test_no_files_modified(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        note = wiki / "test.md"
        original = "---\n---\ngithub:org/test\n"
        note.write_text(original, encoding="utf-8")

        records = {"github:org/test": _rec("github:org/test")}
        match_notes_to_sources(wiki, records)
        assert note.read_text(encoding="utf-8") == original

    def test_min_confidence_filters(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        # Filename-only match (confidence 92) would be at 90 threshold but
        # filtered at 95.
        (wiki / "github-org-alpha.md").write_text("# No other clues\n", encoding="utf-8")
        records = {"github:org/alpha": _rec("github:org/alpha")}

        matches_90 = match_notes_to_sources(wiki, records, min_confidence=90)
        assert len(matches_90) == 1

        matches_95 = match_notes_to_sources(wiki, records, min_confidence=95)
        assert len(matches_95) == 0

    def test_multiple_records_can_match_same_note(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        # Both records mention org/alpha repo.
        (wiki / "alpha.md").write_text(
            "github:org/alpha\n", encoding="utf-8"
        )
        records = {
            "github:org/alpha": _rec("github:org/alpha"),
            "github:org/alpha-mirror": _rec(
                "github:org/alpha-mirror",
                repo_url="https://github.com/org/alpha",
            ),
        }
        matches = match_notes_to_sources(wiki, records)
        assert len(matches) >= 1
        # Both may match depending on overlap.
        assert all(isinstance(m, NoteSourceMatch) for m in matches)

    def test_empty_wiki(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        records = {"github:org/alpha": _rec("github:org/alpha")}
        matches = match_notes_to_sources(wiki, records)
        assert matches == ()

    def test_empty_records(self, tmp_path: Path):
        wiki = tmp_path / "wiki"
        wiki.mkdir(parents=True)
        (wiki / "note.md").write_text("github:org/alpha\n", encoding="utf-8")
        matches = match_notes_to_sources(wiki, {})
        assert matches == ()


# ---------------------------------------------------------------------------
# NoteSourceMatch dataclass
# ---------------------------------------------------------------------------


class TestNoteSourceMatch:
    def test_is_frozen(self):
        m = NoteSourceMatch(
            note_path=Path("/p"),
            source_id="x",
            confidence=100,
            reasons=("exact_source_id",),
        )
        with pytest.raises(AttributeError):
            m.source_id = "y"  # type: ignore[misc]
