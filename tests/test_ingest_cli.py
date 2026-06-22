"""Tests for CLI ingest commands (ingest-batch, ingest-status)."""

from pathlib import Path

import pytest

from codex_vault_pipeline.cli import main

# Sample valid batch YAML
VALID_BATCH_YAML = """\
run_id: test-run
sources:
  - source_id: github:org/repo-a
    repo_url: https://github.com/org/repo-a
"""

# Sample invalid batch YAML (missing repo_url)
INVALID_BATCH_YAML = """\
run_id: test-run
sources:
  - source_id: github:org/repo-a
    repo_url: ""
"""

MISSING_RUN_ID_YAML = """\
run_id: ""
sources:
  - source_id: github:org/repo-a
    repo_url: https://github.com/org/repo-a
"""


def _write_batch(tmp_path: Path, content: str, name: str = "batch.yaml") -> Path:
    p = tmp_path / name
    p.write_text(content)
    return p


# ---------------------------------------------------------------------------
# ingest-batch — dry-run (valid)
# ---------------------------------------------------------------------------


class TestIngestBatch:
    def test_dry_run_valid_yaml_exits_0(self, tmp_path: Path):
        batch = _write_batch(tmp_path, VALID_BATCH_YAML)
        rc = main([
            "ingest-batch",
            "--vault-root", str(tmp_path),
            "--batch-file", str(batch),
            "--dry-run",
        ])
        assert rc == 0

    def test_dry_run_invalid_yaml_exits_1(self, tmp_path: Path):
        batch = _write_batch(tmp_path, INVALID_BATCH_YAML)
        rc = main([
            "ingest-batch",
            "--vault-root", str(tmp_path),
            "--batch-file", str(batch),
            "--dry-run",
        ])
        assert rc == 1

    def test_dry_run_missing_run_id_exits_1(self, tmp_path: Path):
        batch = _write_batch(tmp_path, MISSING_RUN_ID_YAML)
        rc = main([
            "ingest-batch",
            "--vault-root", str(tmp_path),
            "--batch-file", str(batch),
            "--dry-run",
        ])
        assert rc == 1

    def test_without_dry_run_exits_2(self, tmp_path: Path):
        batch = _write_batch(tmp_path, VALID_BATCH_YAML)
        rc = main([
            "ingest-batch",
            "--vault-root", str(tmp_path),
            "--batch-file", str(batch),
        ])
        assert rc == 2

    def test_missing_batch_file_exits_2(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.yaml"
        rc = main([
            "ingest-batch",
            "--vault-root", str(tmp_path),
            "--batch-file", str(missing),
            "--dry-run",
        ])
        assert rc == 2

    def test_unrecognized_format_exits_2(self, tmp_path: Path):
        batch = _write_batch(tmp_path, "not: valid", name="batch.toml")
        rc = main([
            "ingest-batch",
            "--vault-root", str(tmp_path),
            "--batch-file", str(batch),
            "--dry-run",
        ])
        assert rc == 2


# ---------------------------------------------------------------------------
# ingest-status
# ---------------------------------------------------------------------------


class TestIngestStatus:
    def test_empty_vault_exits_0(self, tmp_path: Path):
        rc = main([
            "ingest-status",
            "--vault-root", str(tmp_path),
            "--run-id", "nonexistent-run",
        ])
        assert rc == 0

    def test_with_checkpoints_exits_0(self, tmp_path: Path):
        # Write a single checkpoint via the checkpoint module
        from codex_vault_pipeline.ingest.checkpoints import write_checkpoint

        write_checkpoint(tmp_path, "my-run", "github:a/b", {
            "source_id": "github:a/b",
            "stage": "final",
            "status": "complete",
        })

        rc = main([
            "ingest-status",
            "--vault-root", str(tmp_path),
            "--run-id", "my-run",
        ])
        assert rc == 0

    def test_run_id_required(self):
        with pytest.raises(SystemExit) as exc_info:
            main([
                "ingest-status",
                "--vault-root", "/tmp",
            ])
        assert exc_info.value.code == 2
