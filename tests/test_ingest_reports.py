"""Tests for codex_vault_pipeline.ingest.reports."""

import json
from pathlib import Path

from codex_vault_pipeline.ingest import reports


class TestReportDir:
    def test_points_to_runtime_reports(self, tmp_path: Path):
        d = reports.report_dir(tmp_path)
        assert d == tmp_path / ".runtime" / "reports"
        assert d.is_dir()

    def test_creates_parents(self, tmp_path: Path):
        d = reports.report_dir(tmp_path)
        assert d.exists()


class TestReportPath:
    def test_default_suffix_json(self, tmp_path: Path):
        p = reports.report_path(tmp_path, "my-report")
        assert p == tmp_path / ".runtime" / "reports" / "my-report.json"

    def test_custom_suffix(self, tmp_path: Path):
        p = reports.report_path(tmp_path, "audit", ".md")
        assert p.suffix == ".md"

    def test_creates_parents(self, tmp_path: Path):
        p = reports.report_path(tmp_path, "test")
        assert p.parent.exists()


class TestWriteJsonReport:
    def test_roundtrip(self, tmp_path: Path):
        data = {"key": "value", "num": 42}
        p = reports.report_path(tmp_path, "roundtrip")
        reports.write_json_report(p, data)
        assert p.is_file()
        got = json.loads(p.read_text())
        assert got["key"] == "value"
        assert got["num"] == 42

    def test_sorted_keys(self, tmp_path: Path):
        data = {"z": 1, "a": 2, "m": 3}
        p = reports.report_path(tmp_path, "sorted")
        reports.write_json_report(p, data)
        text = p.read_text()
        a_pos = text.index('"a"')
        m_pos = text.index('"m"')
        z_pos = text.index('"z"')
        assert a_pos < m_pos < z_pos, "keys should be sorted alphabetically"

    def test_no_temp_file_left_after_success(self, tmp_path: Path):
        p = reports.report_path(tmp_path, "no-temp")
        reports.write_json_report(p, {"ok": True})
        leftovers = list(p.parent.glob("*.tmp"))
        assert len(leftovers) == 0


class TestWriteMarkdownReport:
    def test_contains_title_and_sections(self, tmp_path: Path):
        p = reports.report_path(tmp_path, "test", ".md")
        reports.write_markdown_report(p, "My Title", [
            ("Section One", "Body of section one."),
            ("Section Two", "Body of section two."),
        ])
        text = p.read_text()
        assert text.startswith("# My Title")
        assert "## Section One" in text
        assert "Body of section one." in text
        assert "## Section Two" in text
        assert "Body of section two." in text

    def test_no_temp_file_left_after_success(self, tmp_path: Path):
        p = reports.report_path(tmp_path, "no-temp", ".md")
        reports.write_markdown_report(p, "T", [("S", "B")])
        leftovers = list(p.parent.glob("*.tmp"))
        assert len(leftovers) == 0


class TestBuildIngestSummary:
    def test_basic_structure(self):
        sources = [
            {"source_id": "a:1", "status": "complete"},
            {"source_id": "b:2", "status": "complete"},
            {"source_id": "c:3", "status": "failed"},
            {"source_id": "d:4", "status": "skipped"},
        ]
        summary = reports.build_ingest_summary("run-001", sources)
        assert summary["run_id"] == "run-001"
        assert summary["sources_total"] == 4
        assert summary["sources_complete"] == 2
        assert summary["sources_failed"] == 1
        assert summary["sources_skipped"] == 1
        assert summary["final_status"] == "FAILED"  # because failed > 0

    def test_passes_validation(self):
        sources = [{"source_id": "a:1", "status": "complete"}]
        validation = {
            "status": "fail",
            "failures": ["something broke"],
            "warnings": [],
        }
        summary = reports.build_ingest_summary(
            "run-002", sources, validation=validation
        )
        assert "validation" in summary
        assert summary["validation"]["status"] == "fail"
        assert summary["validation"]["failures"] == 1

    def test_counts(self):
        sources = [{"source_id": "a:1", "status": "complete"}]
        summary = reports.build_ingest_summary(
            "run-003", sources, counts={"artifacts": 10, "occurrences": 99}
        )
        assert summary["counts"]["artifacts"] == 10
        assert summary["counts"]["occurrences"] == 99


class TestBuildFinalStatus:
    def test_validated(self):
        assert reports.build_final_status({"errors": 0, "warnings": 0, "blocked": False}) == "VALIDATED"

    def test_blocked(self):
        assert reports.build_final_status({"blocked": True, "errors": 0, "warnings": 0}) == "BLOCKED"

    def test_failed(self):
        assert reports.build_final_status({"errors": 2, "warnings": 0, "blocked": False}) == "FAILED"

    def test_partial(self):
        assert reports.build_final_status({"errors": 0, "warnings": 3, "blocked": False}) == "PARTIAL"

    def test_blocked_takes_precedence(self):
        assert reports.build_final_status({"blocked": True, "errors": 5, "warnings": 3}) == "BLOCKED"

    def test_failed_takes_precedence_over_partial(self):
        assert reports.build_final_status({"errors": 1, "warnings": 3, "blocked": False}) == "FAILED"

    def test_missing_keys_default_to_zero(self):
        assert reports.build_final_status({}) == "VALIDATED"
