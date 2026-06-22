"""Tests for the query-units CLI command."""

import json
from pathlib import Path

import pytest

from codex_vault_pipeline.cli import main
from codex_vault_pipeline.index.sqlite_fts import build_units_fts_index


# ── fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def fts_db(tmp_path: Path) -> Path:
    """Build a small FTS index with 4 units and 3 sources for CLI testing."""
    units_dir = tmp_path / "units"
    for t in ("doc-section", "configuration"):
        (units_dir / t).mkdir(parents=True)

    units = [
        {
            "unit_id": "sha256:aaa#h:intro",
            "artifact_id": "sha256:art1",
            "unit_type": "doc-section",
            "title": "Introduction",
            "semantic_text": "Hermes Agent is a coding agent framework.",
            "source_record_ids": ["sha256:occ1"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "aaa"},
        },
        {
            "unit_id": "sha256:bbb#h:workflow",
            "artifact_id": "sha256:art2",
            "unit_type": "doc-section",
            "title": "Creating an n8n Workflow",
            "semantic_text": "How to create an n8n workflow with AI agents.",
            "source_record_ids": ["sha256:occ2"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "bbb"},
        },
        {
            "unit_id": "sha256:ccc#h:osint",
            "artifact_id": "sha256:art3",
            "unit_type": "doc-section",
            "title": "OSINT Deep Research",
            "semantic_text": "Deep research techniques for OSINT with Agent Field.",
            "source_record_ids": ["sha256:occ3"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "ccc"},
        },
        {
            "unit_id": "sha256:ddd#cfg",
            "artifact_id": "sha256:art4",
            "unit_type": "configuration",
            "title": "n8n Config",
            "semantic_text": '{"nodes": []}',
            "source_record_ids": ["sha256:occ4"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "ddd"},
        },
    ]

    for u in units:
        (units_dir / u["unit_type"] / f'{u["unit_id"]}.json').write_text(json.dumps(u))

    # Write occurrence records for source_id resolution
    occ_dir = tmp_path / "occurrences"
    occ_src = occ_dir / "github_test_test"
    occ_src.mkdir(parents=True)

    occs = [
        ("sha256:occ1", "github:test/hermes", "docs/intro.md"),
        ("sha256:occ2", "github:test/n8n", "docs/workflow.md"),
        ("sha256:occ3", "github:test/osint", "docs/osint.md"),
        ("sha256:occ4", "github:test/n8n", "config/n8n.json"),
    ]
    for oid, sid, sp in occs:
        hex_part = oid.replace("sha256:", "")
        (occ_src / f"{hex_part}.json").write_text(
            json.dumps({"occurrence_id": oid, "source_id": sid, "source_path": sp})
        )

    # Build the FTS index in the standard live location
    db = tmp_path / ".runtime" / "indexes" / "units-fts.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    build_units_fts_index([units_dir], db, occurrence_dir=occ_dir)
    return db


# ── tests ────────────────────────────────────────────────────────────────


class TestQueryUnitsCli:
    def test_missing_db_exits_1(self, tmp_path: Path):
        """No FTS index at expected path → exit 1."""
        rc = main([
            "query-units",
            "--vault-root", str(tmp_path),
            "--query", "Hermes",
        ])
        assert rc == 1

    def test_empty_query_exits_2(self, fts_db: Path):
        """Empty --query string → exit 2."""
        vault = fts_db.parent.parent.parent  # back up from .runtime/indexes/
        rc = main([
            "query-units",
            "--vault-root", str(vault),
            "--query", "   ",
        ])
        assert rc == 2

    def test_query_returns_readable_output(self, fts_db: Path, capsys):
        """Readable output contains source_id, path, title."""
        vault = fts_db.parent.parent.parent
        rc = main([
            "query-units",
            "--vault-root", str(vault),
            "--query", "Hermes",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        out = captured.out
        assert "github:test/hermes" in out
        assert "Introduction" in out
        assert "HERMES" in out.upper() or "Hermes" in out

    def test_json_output_valid(self, fts_db: Path, capsys):
        """--json outputs a valid JSON array."""
        vault = fts_db.parent.parent.parent
        rc = main([
            "query-units",
            "--vault-root", str(vault),
            "--query", "OSINT",
            "--json",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["source_id"] == "github:test/osint"
        assert data[0]["unit_type"] == "doc-section"

    def test_limit_respected(self, fts_db: Path, capsys):
        """--limit caps the number of results."""
        vault = fts_db.parent.parent.parent
        rc = main([
            "query-units",
            "--vault-root", str(vault),
            "--query", "a",  # matches many tokens
            "--limit", "2",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        # Count result entries (each entry starts with a number like "  1.")
        result_count = sum(1 for l in lines if l.strip() and l.strip()[0].isdigit() and "." in l[:5])
        assert result_count <= 2

    def test_source_id_filter(self, fts_db: Path, capsys):
        """--source-id filters results to one source."""
        vault = fts_db.parent.parent.parent
        rc = main([
            "query-units",
            "--vault-root", str(vault),
            "--query", "n8n",
            "--source-id", "github:test/n8n",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        out = captured.out
        assert "github:test/n8n" in out
        assert "github:test/hermes" not in out
        assert "github:test/osint" not in out

    def test_no_results_prints_message(self, fts_db: Path, capsys):
        """No matching results prints 'No results.' not an error."""
        vault = fts_db.parent.parent.parent
        rc = main([
            "query-units",
            "--vault-root", str(vault),
            "--query", "xyznonexistent12345",
        ])
        assert rc == 0
        captured = capsys.readouterr()
        assert "No results" in captured.out

    def test_no_files_written_by_command(self, fts_db: Path, capsys):
        """Command does not create new files outside the pre-existing DB."""
        vault = fts_db.parent.parent.parent
        before = set(vault.rglob("*"))
        rc = main([
            "query-units",
            "--vault-root", str(vault),
            "--query", "Hermes",
        ])
        assert rc == 0
        after = set(vault.rglob("*"))
        new_files = after - before
        assert len(new_files) == 0, f"Unexpected new files: {new_files}"
