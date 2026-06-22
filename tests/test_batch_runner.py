"""Tests for codex_vault_pipeline.ingest.batch."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from codex_vault_pipeline.ingest.batch import (
    BatchConfig,
    BatchRunResult,
    BatchSource,
    load_batch_config,
    run_batch,
    validate_batch_config,
)
from codex_vault_pipeline.ingest.source_runner import (
    SourceRunContext,
    SourceRunResult,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_YAML = """\
run_id: test-batch
metadata:
  description: Integration test batch
sources:
  - source_id: github:org/repo-a
    repo_url: https://github.com/org/repo-a
    metadata:
      domain: test
  - source_id: github:org/repo-b
    repo_url: https://github.com/org/repo-b
    metadata:
      domain: test
"""

SAMPLE_JSON = json.dumps({
    "run_id": "json-batch",
    "metadata": {"description": "JSON batch test"},
    "sources": [
        {"source_id": "github:org/repo-c", "repo_url": "https://github.com/org/repo-c"},
        {"source_id": "github:org/repo-d", "repo_url": "https://github.com/org/repo-d"},
    ],
})

SAMPLE_BAD_YAML = """\
run_id: ""
sources:
  - source_id: ""
    repo_url: ""
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passing_factory(_source: BatchSource) -> Dict[str, Any]:
    """Return handlers that always pass."""
    return {"test_stage": lambda ctx: {"status": "ok"}}


def _failing_factory(source: BatchSource) -> Dict[str, Any]:
    """Return handlers that fail for the first source, pass for others."""
    if source.source_id == "github:org/repo-a":
        return {"test_stage": lambda ctx: {"status": "fail", "message": "intentional"}}
    return {"test_stage": lambda ctx: {"status": "ok"}}


# ---------------------------------------------------------------------------
# Tests — load_batch_config
# ---------------------------------------------------------------------------


class TestLoadBatchConfig:
    def test_load_yaml(self, tmp_path: Path):
        p = tmp_path / "batch.yaml"
        p.write_text(SAMPLE_YAML)
        cfg = load_batch_config(p)
        assert cfg.run_id == "test-batch"
        assert len(cfg.sources) == 2
        assert cfg.sources[0].source_id == "github:org/repo-a"
        assert cfg.sources[0].repo_url == "https://github.com/org/repo-a"
        assert cfg.sources[0].metadata == {"domain": "test"}
        assert cfg.sources[1].source_id == "github:org/repo-b"

    def test_load_yml(self, tmp_path: Path):
        p = tmp_path / "batch.yml"
        p.write_text(SAMPLE_YAML)
        cfg = load_batch_config(p)
        assert cfg.run_id == "test-batch"
        assert len(cfg.sources) == 2

    def test_load_json(self, tmp_path: Path):
        p = tmp_path / "batch.json"
        p.write_text(SAMPLE_JSON)
        cfg = load_batch_config(p)
        assert cfg.run_id == "json-batch"
        assert len(cfg.sources) == 2
        assert cfg.sources[0].source_id == "github:org/repo-c"

    def test_unsupported_format_raises(self, tmp_path: Path):
        p = tmp_path / "batch.toml"
        p.write_text("[invalid]")
        try:
            load_batch_config(p)
            assert False, "Expected ValueError"
        except ValueError as exc:
            assert "Unsupported batch file format" in str(exc)

    def test_load_empty_sources(self, tmp_path: Path):
        p = tmp_path / "empty.yaml"
        p.write_text("run_id: empty\nsources: []")
        cfg = load_batch_config(p)
        assert cfg.run_id == "empty"
        assert cfg.sources == []

    def test_load_no_run_id_defaults_empty(self, tmp_path: Path):
        p = tmp_path / "no_id.yaml"
        p.write_text("sources: []")
        cfg = load_batch_config(p)
        assert cfg.run_id == ""


# ---------------------------------------------------------------------------
# Tests — validate_batch_config
# ---------------------------------------------------------------------------


class TestValidateBatchConfig:
    def test_valid_config_passes(self):
        config = BatchConfig(
            run_id="test",
            sources=[
                BatchSource(source_id="github:a/b", repo_url="https://github.com/a/b"),
                BatchSource(source_id="github:c/d", repo_url="https://github.com/c/d"),
            ],
        )
        assert validate_batch_config(config) == []

    def test_missing_run_id(self):
        config = BatchConfig(
            run_id="",
            sources=[
                BatchSource(source_id="github:a/b", repo_url="https://github.com/a/b"),
            ],
        )
        errors = validate_batch_config(config)
        assert any("run_id" in e for e in errors)

    def test_missing_source_id(self):
        config = BatchConfig(
            run_id="test",
            sources=[BatchSource(source_id="", repo_url="https://github.com/a/b")],
        )
        errors = validate_batch_config(config)
        assert any("source_id" in e for e in errors)

    def test_missing_repo_url(self):
        config = BatchConfig(
            run_id="test",
            sources=[BatchSource(source_id="github:a/b", repo_url="")],
        )
        errors = validate_batch_config(config)
        assert any("repo_url" in e for e in errors)

    def test_duplicate_source_id(self):
        config = BatchConfig(
            run_id="test",
            sources=[
                BatchSource(source_id="github:a/b", repo_url="https://github.com/a/b"),
                BatchSource(source_id="github:a/b", repo_url="https://github.com/a/b"),
            ],
        )
        errors = validate_batch_config(config)
        assert any("Duplicate" in e and "a/b" in e for e in errors)

    def test_multiple_issues_reported(self):
        config = BatchConfig(
            run_id="",
            sources=[BatchSource(source_id="", repo_url="")],
        )
        errors = validate_batch_config(config)
        assert len(errors) >= 2  # run_id + source_id + repo_url


# ---------------------------------------------------------------------------
# Tests — run_batch
# ---------------------------------------------------------------------------


class TestRunBatch:
    def test_all_pass_runs_sources_in_order(self, tmp_path: Path):
        config = BatchConfig(
            run_id="batch-test",
            sources=[
                BatchSource(source_id="github:org/one", repo_url="https://github.com/org/one"),
                BatchSource(source_id="github:org/two", repo_url="https://github.com/org/two"),
            ],
        )
        result = run_batch(
            vault_root=tmp_path,
            config=config,
            handlers_factory=_passing_factory,
            stages=("test_stage",),
        )
        assert result.status == "complete"
        assert len(result.source_results) == 2
        assert result.source_results[0].source_id == "github:org/one"
        assert result.source_results[0].status == "complete"
        assert result.source_results[1].source_id == "github:org/two"
        assert result.source_results[1].status == "complete"
        assert result.errors == []

    def test_failed_source_stops_batch(self, tmp_path: Path):
        config = BatchConfig(
            run_id="batch-fail-stop",
            sources=[
                BatchSource(source_id="github:org/repo-a", repo_url="https://github.com/org/repo-a"),
                BatchSource(source_id="github:org/repo-b", repo_url="https://github.com/org/repo-b"),
            ],
        )
        result = run_batch(
            vault_root=tmp_path,
            config=config,
            handlers_factory=_failing_factory,
            stages=("test_stage",),
            stop_on_first_failure=True,
        )
        assert result.status == "partial"
        assert len(result.source_results) == 1  # stopped after first failure
        assert result.source_results[0].source_id == "github:org/repo-a"
        assert result.source_results[0].status != "complete"
        assert len(result.errors) == 1

    def test_failed_source_continues(self, tmp_path: Path):
        config = BatchConfig(
            run_id="batch-fail-continue",
            sources=[
                BatchSource(source_id="github:org/repo-a", repo_url="https://github.com/org/repo-a"),
                BatchSource(source_id="github:org/repo-b", repo_url="https://github.com/org/repo-b"),
            ],
        )
        result = run_batch(
            vault_root=tmp_path,
            config=config,
            handlers_factory=_failing_factory,
            stages=("test_stage",),
            stop_on_first_failure=False,
        )
        assert result.status == "failed"
        assert len(result.source_results) == 2  # processed both
        assert result.source_results[0].status != "complete"
        assert result.source_results[1].status == "complete"
        assert len(result.errors) == 1  # one error for the failed source

    def test_batch_writes_checkpoints(self, tmp_path: Path):
        config = BatchConfig(
            run_id="batch-chk",
            sources=[
                BatchSource(source_id="github:org/ck-a", repo_url="https://github.com/org/ck-a"),
                BatchSource(source_id="github:org/ck-b", repo_url="https://github.com/org/ck-b"),
            ],
        )
        run_batch(
            vault_root=tmp_path,
            config=config,
            handlers_factory=_passing_factory,
            stages=("test_stage",),
        )
        # Check that checkpoint files were written
        cp_dir = (
            tmp_path
            / ".runtime"
            / "checkpoints"
            / "incremental-ingest"
            / "batch-chk"
        )
        files = sorted(cp_dir.glob("*.json"))
        assert len(files) == 2  # one per source

    def test_invalid_config_raises_value_error(self, tmp_path: Path):
        config = BatchConfig(
            run_id="",
            sources=[],
        )
        try:
            run_batch(
                vault_root=tmp_path,
                config=config,
                handlers_factory=_passing_factory,
            )
            assert False, "Expected ValueError"
        except ValueError as exc:
            assert "Invalid batch config" in str(exc)

    def test_empty_sources_returns_complete(self, tmp_path: Path):
        config = BatchConfig(
            run_id="empty-batch",
            sources=[],
        )
        result = run_batch(
            vault_root=tmp_path,
            config=config,
            handlers_factory=_passing_factory,
        )
        assert result.status == "complete"
        assert result.source_results == []
        assert result.errors == []

    def test_run_id_in_result(self, tmp_path: Path):
        config = BatchConfig(
            run_id="my-run",
            sources=[
                BatchSource(source_id="github:org/x", repo_url="https://github.com/org/x"),
            ],
        )
        result = run_batch(
            vault_root=tmp_path,
            config=config,
            handlers_factory=_passing_factory,
            stages=("test_stage",),
        )
        assert result.run_id == "my-run"
