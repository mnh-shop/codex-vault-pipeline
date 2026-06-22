"""Tests for the deterministic SQLite FTS5 unit index."""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Generator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from codex_vault_pipeline.index.sqlite_fts import (
    build_units_fts_index,
    query_units_fts,
    iter_unit_jsonl,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "index" / "units-fts.sqlite"


@pytest.fixture
def unit_fixtures(tmp_path: Path) -> Path:
    """Write a small set of unit .json files resembling real unit output."""
    base = tmp_path / "units"
    types = {"doc-section", "configuration", "hermes-skill"}
    for t in types:
        (base / t).mkdir(parents=True)

    units = [
        {
            "unit_id": "sha256:aaa#heading:introduction",
            "artifact_id": "sha256:art1",
            "unit_type": "doc-section",
            "title": "Introduction",
            "semantic_text": "Hermes Agent is a coding agent framework. It supports tools and MCP servers.",
            "source_record_ids": ["sha256:occ1"],
            "extraction_method": "codex-vault/deterministic-unit-extractor",
            "fingerprints": {"content_sha256": "aaa"},
        },
        {
            "unit_id": "sha256:bbb#heading:workflow",
            "artifact_id": "sha256:art2",
            "unit_type": "doc-section",
            "title": "Creating an n8n Workflow",
            "semantic_text": "This section explains how to create an n8n workflow with AI agents.",
            "source_record_ids": ["sha256:occ2"],
            "extraction_method": "codex-vault/deterministic-unit-extractor",
            "fingerprints": {"content_sha256": "bbb"},
        },
        {
            "unit_id": "sha256:ccc#config",
            "artifact_id": "sha256:art3",
            "unit_type": "configuration",
            "title": "n8n Config",
            "semantic_text": '{"connections": {}, "nodes": []}',
            "source_record_ids": ["sha256:occ3"],
            "extraction_method": "codex-vault/deterministic-unit-extractor",
            "fingerprints": {"content_sha256": "ccc"},
        },
        {
            "unit_id": "sha256:ddd#heading:deep-research",
            "artifact_id": "sha256:art4",
            "unit_type": "doc-section",
            "title": "Deep Research OSINT",
            "semantic_text": "Deep research techniques for OSINT investigations using Agent Field.",
            "source_record_ids": ["sha256:occ4"],
            "extraction_method": "codex-vault/deterministic-unit-extractor",
            "fingerprints": {"content_sha256": "ddd"},
        },
    ]

    for u in units:
        path = base / u["unit_type"] / f'{u["unit_id"]}.json'
        path.write_text(json.dumps(u))

    return base


@pytest.fixture
def occ_fixtures(tmp_path: Path) -> Path:
    """Write occurrence records that match the unit fixtures."""
    base = tmp_path / "occurrences"
    (base / "github_test_test").mkdir(parents=True)

    occs = [
        {
            "occurrence_id": "sha256:occ1",
            "source_id": "github:test/hermes",
            "source_path": "docs/intro.md",
        },
        {
            "occurrence_id": "sha256:occ2",
            "source_id": "github:test/n8n",
            "source_path": "docs/workflow.md",
        },
        {
            "occurrence_id": "sha256:occ3",
            "source_id": "github:test/n8n",
            "source_path": "config/n8n.json",
        },
        {
            "occurrence_id": "sha256:occ4",
            "source_id": "github:test/osint",
            "source_path": "docs/osint.md",
        },
    ]

    for o in occs:
        oid_hex = o["occurrence_id"].replace("sha256:", "")
        path = base / "github_test_test" / f"{oid_hex}.json"
        path.write_text(json.dumps(o))

    return base


# ── iter_unit_jsonl ──────────────────────────────────────────────────────


class TestIterUnitJsonl:
    def test_iterates_json_files(self, unit_fixtures: Path):
        units = list(iter_unit_jsonl([unit_fixtures]))
        assert len(units) == 4

    def test_skips_validation_report(self, tmp_path: Path):
        d = tmp_path / "units"
        d.mkdir()
        (d / "unit-validation-report.json").write_text('{"meta": true}')
        (d / "real.json").write_text(json.dumps({"unit_id": "x", "title": "real"}))
        units = list(iter_unit_jsonl([d]))
        assert len(units) == 1
        assert units[0]["unit_id"] == "x"

    def test_jsonl_files(self, tmp_path: Path):
        p = tmp_path / "units.jsonl"
        p.write_text(
            json.dumps({"unit_id": "a"}) + "\n"
            + json.dumps({"unit_id": "b"}) + "\n"
        )
        units = list(iter_unit_jsonl([p]))
        assert len(units) == 2

    def test_empty_jsonl_skips(self, tmp_path: Path):
        p = tmp_path / "empty.jsonl"
        p.write_text("\n\n\n")
        units = list(iter_unit_jsonl([p]))
        assert len(units) == 0

    def test_bad_json_file_warns(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json")
        units = list(iter_unit_jsonl([p]))
        assert len(units) == 0  # warning printed, no crash


# ── build_units_fts_index ────────────────────────────────────────────────


class TestBuildUnitsFtsIndex:
    def test_build_and_query(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        """Build index and query returns relevant results."""
        result = build_units_fts_index(
            [unit_fixtures],
            tmp_db,
            occurrence_dir=occ_fixtures,
        )
        assert result.unit_count == 4
        assert result.source_count == 3  # hermes, n8n, osint
        assert result.db_path == tmp_db
        assert tmp_db.exists()

        hits = query_units_fts(tmp_db, "Hermes Agent")
        assert len(hits) >= 1
        # FTS5 snippet highlights individual tokens with << >> markers
        assert any("Hermes" in h["text_preview"] for h in hits)
        assert any(h["source_id"] for h in hits)

    def test_query_n8n_workflow(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        hits = query_units_fts(tmp_db, "n8n workflow")
        assert len(hits) >= 1

    def test_query_osint(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        hits = query_units_fts(tmp_db, "OSINT")
        assert len(hits) >= 1
        assert all(h["source_id"] for h in hits)

    def test_source_id_in_results(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        hits = query_units_fts(tmp_db, "Hermes")
        assert all(isinstance(h["source_id"], str) for h in hits)
        assert any("github:test/hermes" == h["source_id"] for h in hits)

    def test_limit_respected(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        # Query something that matches everything
        hits = query_units_fts(tmp_db, '"n8n" OR "Hermes" OR "Deep Research" OR "Introduction"', limit=2)
        assert len(hits) == 2

    def test_deterministic_rebuild(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        """Rebuilding produces the same unit count."""
        r1 = build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        r2 = build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        assert r1.unit_count == r2.unit_count == 4
        assert r1.source_count == r2.source_count == 3

    def test_duplicate_unit_id_ignored(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        """Duplicate unit_id does not create duplicate rows."""
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)

        # Add a duplicate unit file
        dup_dir = tmp_db.parent / "dup-units"
        dup_dir.mkdir()
        dup = {
            "unit_id": "sha256:aaa#heading:introduction",  # same as first fixture
            "artifact_id": "sha256:dup",
            "unit_type": "doc-section",
            "title": "Duplicate",
            "semantic_text": "Duplicate entry",
            "source_record_ids": ["sha256:occ1"],
            "extraction_method": "test",
            "fingerprints": {"content_sha256": "dup"},
        }
        (dup_dir / "dup.json").write_text(json.dumps(dup))

        build_units_fts_index(
            [unit_fixtures, dup_dir],
            tmp_db,
            occurrence_dir=occ_fixtures,
        )
        con = sqlite3.connect(str(tmp_db))
        count = con.execute("SELECT COUNT(*) FROM units").fetchone()[0]
        con.close()
        assert count == 4  # still 4, duplicate was ignored

    def test_parent_dirs_created(self, unit_fixtures: Path, occ_fixtures: Path, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c" / "idx.sqlite"
        result = build_units_fts_index(
            [unit_fixtures], deep, occurrence_dir=occ_fixtures,
        )
        assert result.db_path.exists()
        assert result.unit_count == 4

    def test_no_files_outside_db_path(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        before = set(tmp_db.parent.rglob("*"))
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        after = set(tmp_db.parent.rglob("*"))
        new_files = after - before
        # Only the db file should be new (with journal files cleaned up)
        for f in new_files:
            assert f.name.endswith(".sqlite") or "-wal" in f.name or "-shm" in f.name
        assert tmp_db in new_files or tmp_db.name in {f.name for f in new_files}

    def test_without_occurrence_dir(self, unit_fixtures: Path, tmp_db: Path):
        """Building without occurrence dir leaves source fields empty."""
        result = build_units_fts_index([unit_fixtures], tmp_db)
        assert result.unit_count == 4
        hits = query_units_fts(tmp_db, "Hermes")
        # source_id can be empty string
        assert all(h["source_id"] == "" for h in hits)
        assert all(h["source_path"] == "" for h in hits)

    def test_empty_paths(self, tmp_db: Path):
        result = build_units_fts_index([], tmp_db)
        assert result.unit_count == 0
        assert result.source_count == 0


# ── query_units_fts ──────────────────────────────────────────────────────


class TestQueryUnitsFts:
    def test_empty_result(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        hits = query_units_fts(tmp_db, "xyznonexistent12345")
        assert len(hits) == 0

    def test_returns_expected_keys(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        hits = query_units_fts(tmp_db, "Hermes")
        expected_keys = {
            "unit_id", "source_id", "source_path", "unit_type",
            "title", "text_preview", "artifact_id", "rank",
        }
        for h in hits:
            assert set(h.keys()) == expected_keys

    def test_snippet_includes_match(self, unit_fixtures: Path, occ_fixtures: Path, tmp_db: Path):
        build_units_fts_index([unit_fixtures], tmp_db, occurrence_dir=occ_fixtures)
        hits = query_units_fts(tmp_db, "OSINT")
        assert any("OSINT" in h.get("text_preview", "") for h in hits)
        assert any("OSINT" in h.get("title", "") for h in hits)
