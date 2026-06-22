"""Tests for codex_vault_pipeline.ingest.checkpoints."""

import json
import os
from pathlib import Path

from codex_vault_pipeline.ingest import checkpoints


class TestSafeSourceFilename:
    def test_github_repo(self):
        result = checkpoints.safe_source_filename("github:owner/repo")
        assert result == "github_owner_repo"

    def test_colon_only(self):
        result = checkpoints.safe_source_filename("website:docs.example.com")
        assert result == "website_docs.example.com"

    def test_already_safe(self):
        result = checkpoints.safe_source_filename("simple-name")
        assert result == "simple-name"

    def test_backslash_replaced(self):
        result = checkpoints.safe_source_filename("foo\\bar:baz")
        assert "_" in result
        assert "\\" not in result
        assert ":" not in result


class TestCheckpointPath:
    def test_under_runtime_checkpoints(self, tmp_path: Path):
        run_id = "test-run-001"
        source_id = "github:owner/repo"
        cp = checkpoints.checkpoint_path(tmp_path, run_id, source_id)
        rel = cp.relative_to(tmp_path)
        parts = rel.parts
        # Must be under .runtime/checkpoints/incremental-ingest/<run-id>/
        assert parts[0] == ".runtime"
        assert parts[1] == "checkpoints"
        assert parts[2] == "incremental-ingest"
        assert parts[3] == run_id

    def test_filename_is_json(self, tmp_path: Path):
        cp = checkpoints.checkpoint_path(tmp_path, "rid", "a:b")
        assert cp.suffix == ".json"

    def test_deterministic(self, tmp_path: Path):
        a = checkpoints.checkpoint_path(tmp_path, "rid", "x:y")
        b = checkpoints.checkpoint_path(tmp_path, "rid", "x:y")
        assert a == b


class TestWriteAndLoad:
    def test_write_returns_path(self, tmp_path: Path):
        result = checkpoints.write_checkpoint(
            tmp_path, "run-1", "src:test",
            {"source_id": "src:test", "status": "running"},
        )
        assert result.is_file()
        assert result.suffix == ".json"

    def test_write_adds_updated_at(self, tmp_path: Path):
        checkpoints.write_checkpoint(
            tmp_path, "run-1", "src:test",
            {"source_id": "src:test"},
        )
        loaded = checkpoints.load_checkpoint(tmp_path, "run-1", "src:test")
        assert loaded is not None
        assert "updated_at" in loaded
        assert loaded["source_id"] == "src:test"

    def test_load_returns_same_data(self, tmp_path: Path):
        data = {
            "source_id": "github:org/repo",
            "status": "complete",
            "files_seen": 42,
            "errors": [],
        }
        checkpoints.write_checkpoint(tmp_path, "run-1", "github:org/repo", data)
        loaded = checkpoints.load_checkpoint(tmp_path, "run-1", "github:org/repo")
        assert loaded is not None
        assert loaded["source_id"] == "github:org/repo"
        assert loaded["status"] == "complete"
        assert loaded["files_seen"] == 42
        assert loaded["errors"] == []

    def test_load_missing_returns_none(self, tmp_path: Path):
        result = checkpoints.load_checkpoint(tmp_path, "no-such-run", "no:source")
        assert result is None

    def test_load_corrupt_returns_none(self, tmp_path: Path):
        cp = checkpoints.checkpoint_path(tmp_path, "r", "s")
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text("not valid json")
        result = checkpoints.load_checkpoint(tmp_path, "r", "s")
        assert result is None

    def test_overwrite_preserves_new_data(self, tmp_path: Path):
        checkpoints.write_checkpoint(
            tmp_path, "r", "s",
            {"source_id": "s", "stage": "acquire"},
        )
        checkpoints.write_checkpoint(
            tmp_path, "r", "s",
            {"source_id": "s", "stage": "extract", "extra": True},
        )
        loaded = checkpoints.load_checkpoint(tmp_path, "r", "s")
        assert loaded is not None
        assert loaded["stage"] == "extract"
        assert loaded["extra"] is True


class TestAtomicWrite:
    def test_no_temp_file_left_after_success(self, tmp_path: Path):
        checkpoints.write_checkpoint(tmp_path, "r", "s", {"k": "v"})
        cp_dir = checkpoints.checkpoint_dir(tmp_path, "r")
        leftovers = [p for p in cp_dir.iterdir() if p.suffix == ".tmp"]
        assert len(leftovers) == 0


class TestListCheckpoints:
    def test_empty_run(self, tmp_path: Path):
        result = checkpoints.list_checkpoints(tmp_path, "no-run")
        assert result == []

    def test_returns_written_checkpoints(self, tmp_path: Path):
        checkpoints.write_checkpoint(tmp_path, "r", "a:1", {"source_id": "a:1"})
        checkpoints.write_checkpoint(tmp_path, "r", "b:2", {"source_id": "b:2"})
        result = checkpoints.list_checkpoints(tmp_path, "r")
        assert len(result) == 2

    def test_skips_non_dict_json(self, tmp_path: Path):
        checkpoints.write_checkpoint(tmp_path, "r", "good", {"source_id": "good"})
        # Write a non-dict JSON file directly
        bad = checkpoints.checkpoint_path(tmp_path, "r", "bad")
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text(json.dumps(["not", "a", "dict"]))
        result = checkpoints.list_checkpoints(tmp_path, "r")
        assert len(result) == 1
        assert result[0]["source_id"] == "good"
