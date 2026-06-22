"""Tests for codex_vault_pipeline.ingest.source_runner."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from codex_vault_pipeline.ingest.source_runner import (
    DEFAULT_SOURCE_STAGES,
    SourceRunContext,
    SourceRunResult,
    run_source_stages,
)
from codex_vault_pipeline.ingest.checkpoints import (
    load_checkpoint,
    safe_source_filename,
)


SOURCE_ID = "github:org/repo"
RUN_ID = "test-run-001"


def _ctx(tmp_path: Path, source_id: str = SOURCE_ID) -> SourceRunContext:
    return SourceRunContext(
        vault_root=tmp_path,
        run_id=RUN_ID,
        source_id=source_id,
        repo_url="https://github.com/org/repo",
    )


def _passing_handler(_ctx: SourceRunContext) -> Optional[Dict[str, Any]]:
    return {"status": "ok"}


def _failing_handler(_ctx: SourceRunContext) -> Optional[Dict[str, Any]]:
    return {"status": "fail", "message": "something went wrong"}


def _raising_handler(_ctx: SourceRunContext) -> Optional[Dict[str, Any]]:
    raise RuntimeError("kaboom")


def _checkpoint_data(tmp_path: Path) -> Optional[Dict[str, Any]]:
    return load_checkpoint(tmp_path, RUN_ID, SOURCE_ID)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDefaultStages:
    def test_has_expected_stages(self):
        assert "acquire_source" in DEFAULT_SOURCE_STAGES
        assert "checkpoint_complete" in DEFAULT_SOURCE_STAGES
        assert len(DEFAULT_SOURCE_STAGES) == 11

    def test_order_is_correct(self):
        idx = {name: i for i, name in enumerate(DEFAULT_SOURCE_STAGES)}
        assert idx["acquire_source"] < idx["write_source_record"]
        assert idx["write_source_record"] < idx["create_artifacts"]
        assert idx["create_artifacts"] < idx["create_occurrences"]
        assert idx["create_occurrences"] < idx["validate_artifacts_occurrences"]
        assert idx["validate_artifacts_occurrences"] < idx["extract_units"]
        assert idx["extract_units"] < idx["validate_unit_artifact_links"]
        assert idx["validate_unit_artifact_links"] < idx["generate_candidate_note"]
        assert idx["generate_candidate_note"] < idx["generate_migration_report"]
        assert idx["generate_migration_report"] < idx["source_local_validation"]
        assert idx["source_local_validation"] < idx["checkpoint_complete"]


class TestRunSourceStages:
    def test_stages_run_in_order(self, tmp_path: Path):
        order: list[str] = []

        def handler_a(_ctx):
            order.append("a")
            return {"status": "ok"}

        def handler_b(_ctx):
            order.append("b")
            return {"status": "ok"}

        ctx = _ctx(tmp_path)
        result = run_source_stages(
            ctx,
            handlers={"stage_a": handler_a, "stage_b": handler_b},
            stages=("stage_a", "stage_b"),
        )
        assert order == ["a", "b"]
        assert result.status == "complete"

    def test_checkpoints_are_written(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        handlers = {s: _passing_handler for s in ("s1", "s2")}
        run_source_stages(ctx, handlers, stages=("s1", "s2"))
        cp = _checkpoint_data(tmp_path)
        assert cp is not None
        assert cp["source_id"] == SOURCE_ID
        assert cp["status"] == "complete"
        assert cp["last_successful_step"] == "s2"

    def test_missing_handler_fails(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        try:
            run_source_stages(
                ctx,
                handlers={"a": _passing_handler},
                stages=("a", "b"),
            )
            assert False, "Expected KeyError"
        except KeyError:
            pass

    def test_exception_in_handler_fails_and_stops(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        handlers = {"s1": _passing_handler, "s2": _raising_handler, "s3": _passing_handler}
        result = run_source_stages(ctx, handlers, stages=("s1", "s2", "s3"))
        assert result.status == "failed"
        assert result.failed_stage == "s2"
        assert result.completed_stages == ["s1"]
        assert len(result.errors) == 1
        assert "Stage 's2' raised:" in result.errors[0]
        assert "kaboom" in result.errors[0]

    def test_returned_fail_status_stops(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        handlers = {"s1": _passing_handler, "s2": _failing_handler, "s3": _passing_handler}
        result = run_source_stages(ctx, handlers, stages=("s1", "s2", "s3"))
        assert result.status == "failed"
        assert result.failed_stage == "s2"
        assert result.completed_stages == ["s1"]
        assert "something went wrong" in result.errors[0]

    def test_all_pass_returns_complete(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        handlers = {s: _passing_handler for s in DEFAULT_SOURCE_STAGES}
        result = run_source_stages(ctx, handlers)
        assert result.status == "complete"
        assert result.failed_stage is None
        assert len(result.errors) == 0
        assert result.completed_stages == list(DEFAULT_SOURCE_STAGES)

    def test_custom_stage_list(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        my_stages = ("fetch", "parse", "store")
        handlers = {s: _passing_handler for s in my_stages}
        result = run_source_stages(ctx, handlers, stages=my_stages)
        assert result.status == "complete"
        assert result.completed_stages == ["fetch", "parse", "store"]

    def test_failure_checkpoint_has_status_failed(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        handlers = {"s1": _failing_handler}
        run_source_stages(ctx, handlers, stages=("s1",))
        cp = _checkpoint_data(tmp_path)
        assert cp is not None
        assert cp["status"] == "failed"
        assert cp["stage"] == "s1"

    def test_complete_checkpoint_has_status_complete(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        handlers = {"s1": _passing_handler}
        run_source_stages(ctx, handlers, stages=("s1",))
        cp = _checkpoint_data(tmp_path)
        assert cp is not None
        assert cp["status"] == "complete"
        assert cp["stage"] == "final"

    def test_continue_on_failure(self, tmp_path: Path):
        order: list[str] = []

        def handler_a(_ctx):
            order.append("a")
            return {"status": "ok"}

        def handler_b(_ctx):
            order.append("b")
            return {"status": "fail", "message": "b-bad"}

        def handler_c(_ctx):
            order.append("c")
            return {"status": "ok"}

        ctx = _ctx(tmp_path)
        result = run_source_stages(
            ctx,
            handlers={"a": handler_a, "b": handler_b, "c": handler_c},
            stages=("a", "b", "c"),
            stop_on_first_failure=False,
        )
        assert order == ["a", "b", "c"]
        assert result.status == "failed"
        assert result.failed_stage == "b"  # first failure recorded
        assert result.completed_stages == ["a", "c"]
        assert len(result.errors) == 1
        assert "b-bad" in result.errors[0]
