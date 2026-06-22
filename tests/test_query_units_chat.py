"""Tests for the chat-safe query wrapper (scripts/query_units_chat.py)."""

import json
from pathlib import Path

import pytest

from codex_vault_pipeline.index.sqlite_fts import build_units_fts_index

# We test the script via its main() rather than subprocess for speed.
from scripts.query_units_chat import main


# ── helpers ────────────────────────────────────────────────────────────


def _build_fixture_index(tmp_path: Path) -> Path:
    """Build a small 4-unit / 3-source FTS index under *tmp_path*."""
    units_dir = tmp_path / "units"
    for t in ("doc-section", "configuration"):
        (units_dir / t).mkdir(parents=True)

    units = [
        {
            "unit_id": "sha256:aaa#h:intro",
            "artifact_id": "sha256:art1",
            "unit_type": "doc-section",
            "title": "Introduction to Hermes Agent Telegram",
            "semantic_text": "Hermes Agent is a coding agent framework with Telegram support.",
            "source_record_ids": ["sha256:occ1"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "aaa"},
        },
        {
            "unit_id": "sha256:bbb#h:workflow",
            "artifact_id": "sha256:art2",
            "unit_type": "doc-section",
            "title": "Creating an n8n Workflow",
            "semantic_text": "How to create an n8n workflow with AI agents and webhooks.",
            "source_record_ids": ["sha256:occ2"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "bbb"},
        },
        {
            "unit_id": "sha256:ccc#h:telegram",
            "artifact_id": "sha256:art3",
            "unit_type": "doc-section",
            "title": "Telegram Bot Configuration",
            "semantic_text": "Configure the Telegram bot token and webhook URL for notifications.",
            "source_record_ids": ["sha256:occ3"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "ccc"},
        },
        {
            "unit_id": "sha256:ddd#cfg",
            "artifact_id": "sha256:art4",
            "unit_type": "configuration",
            "title": "Hermes Telegram Settings",
            "semantic_text": '{"telegram_token": "env:TELEGRAM_BOT_TOKEN"}',
            "source_record_ids": ["sha256:occ4"],
            "extraction_method": "det",
            "fingerprints": {"content_sha256": "ddd"},
        },
    ]

    for u in units:
        (units_dir / u["unit_type"] / f'{u["unit_id"]}.json').write_text(json.dumps(u))

    # Occurrence records for source_id resolution
    occ_dir = tmp_path / "occurrences"
    occ_src = occ_dir / "github_test_test"
    occ_src.mkdir(parents=True)

    occs = [
        ("sha256:occ1", "github:test/hermes", "README.md"),
        ("sha256:occ2", "github:test/n8n", "docs/workflow.md"),
        ("sha256:occ3", "github:test/telegram", "config/bot.md"),
        ("sha256:occ4", "github:test/hermes", "config/settings.json"),
    ]
    for oid, sid, sp in occs:
        hex_part = oid.replace("sha256:", "")
        (occ_src / f"{hex_part}.json").write_text(
            json.dumps({"occurrence_id": oid, "source_id": sid, "source_path": sp})
        )

    db = tmp_path / ".runtime" / "indexes" / "units-fts.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    build_units_fts_index([units_dir], db, occurrence_dir=occ_dir)
    return db


# ── tests ──────────────────────────────────────────────────────────────


class TestQueryUnitsChat:
    def test_readable_chat_output(self, tmp_path: Path, capsys):
        """Chat output starts with header, includes results and footer."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        rc = main([
            "--vault-root", str(vault),
            "--query", "Hermes",
            "--limit", "5",
            "--max-chars", "5000",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Codex Vault results for: Hermes" in out
        assert "github:test/hermes" in out
        assert "Showing " in out

    def test_respects_max_chars(self, tmp_path: Path, capsys):
        """Output is truncated when max_chars is small."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        rc = main([
            "--vault-root", str(vault),
            "--query", "Hermes",
            "--limit", "5",
            "--max-chars", "150",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "truncated" in out

    def test_truncation_indicator_present(self, tmp_path: Path, capsys):
        """Truncated output includes the truncation message."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        rc = main([
            "--vault-root", str(vault),
            "--query", "Hermes",
            "--limit", "5",
            "--max-chars", "100",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "truncated" in out
        assert "refine query" in out

    def test_json_output(self, tmp_path: Path, capsys):
        """--json returns valid JSON array."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        rc = main([
            "--vault-root", str(vault),
            "--query", "Telegram",
            "--limit", "5",
            "--max-chars", "5000",
            "--json",
        ])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        assert len(data) >= 1
        assert all("source_id" in r for r in data)
        assert any(r["source_id"] == "github:test/telegram" for r in data)

    def test_source_id_filter(self, tmp_path: Path, capsys):
        """--source-id filters results to one source."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        rc = main([
            "--vault-root", str(vault),
            "--query", "telegram",
            "--source-id", "github:test/hermes",
            "--limit", "5",
            "--max-chars", "5000",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "github:test/hermes" in out
        assert "github:test/telegram" not in out

    def test_missing_db_exits_1(self, tmp_path: Path):
        """No FTS index → exit 1 with error on stderr."""
        rc = main([
            "--vault-root", str(tmp_path),
            "--query", "Hermes",
            "--limit", "5",
            "--max-chars", "5000",
        ])
        assert rc == 1

    def test_empty_query_exits_2(self, tmp_path: Path):
        """Empty --query → exit 2."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        rc = main([
            "--vault-root", str(vault),
            "--query", "   ",
            "--limit", "5",
            "--max-chars", "5000",
        ])
        assert rc == 2

    def test_no_results_shows_zero(self, tmp_path: Path, capsys):
        """No matching results shows 0 in footer."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        rc = main([
            "--vault-root", str(vault),
            "--query", "xyznonexistent12345",
            "--limit", "5",
            "--max-chars", "5000",
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Showing 0 results" in out

    def test_no_files_written(self, tmp_path: Path, capsys):
        """Script does not create any new files beyond the pre-existing DB."""
        db = _build_fixture_index(tmp_path)
        vault = db.parent.parent.parent
        before = set(vault.rglob("*"))
        rc = main([
            "--vault-root", str(vault),
            "--query", "Hermes",
            "--limit", "5",
            "--max-chars", "5000",
        ])
        assert rc == 0
        after = set(vault.rglob("*"))
        new_files = after - before
        assert len(new_files) == 0, f"Unexpected new files: {new_files}"
