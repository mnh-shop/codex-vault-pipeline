"""Tests for the deterministic unit validator (unit_validator.py)."""

import json
import hashlib
from pathlib import Path

import pytest

from codex_vault_pipeline.ingest.unit_validator import (
    UnitValidationIssue,
    UnitValidationReport,
    load_units_jsonl,
    load_units_from_directory,
    validate_unit_record,
    validate_units,
    write_unit_validation_report,
    _normalize_content,
    _is_chinese,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ID = "sha256:" + "a" * 64


def _make_unit(**overrides) -> dict:
    """Build a minimally valid unit/v1 record."""
    base = {
        "schema": "unit/v1",
        "schema_version": "1.0.0",
        "record_id": "sha256:" + "b" * 64,
        "created_at": "2026-06-22T02:41:03+00:00",
        "generator": "codex-vault/deterministic-unit-extractor",
        "generator_version": "1.0.0",
        "run_id": "test-run",
        "content_hash": "sha256:" + "c" * 64,
        "source_record_ids": ["github:owner/repo"],
        "parser_name": "codex-vault/deterministic-unit-extractor",
        "parser_version": "1.0.0",
        "unit_id": _VALID_ID + "#heading:intro",
        "artifact_id": "sha256:" + "d" * 64,
        "source_anchor": {
            "section": "intro",
            "line_start": 1,
            "line_end": 10,
            "json_pointer": None,
        },
        "unit_type": "doc-section",
        "title": "Introduction",
        "semantic_text": "This is the introduction section.",
        "token_count": 5,
        "fingerprints": {
            "content_sha256": "d" * 64,
            "normalized_hash": "e" * 64,
            "structural_hash": "f" * 64,
            "semantic_signature": "sha256:..." + "g" * 10,
        },
        "duplicate_of": None,
        "variant_of": None,
        "derived_from": None,
        "dedup_group": "",
        "redacted": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test: valid unit passes
# ---------------------------------------------------------------------------


class TestValidUnit:
    def test_valid_unit_returns_no_issues(self):
        unit = _make_unit()
        issues = validate_unit_record(unit)
        assert len(issues) == 0, f"Expected no issues, got: {issues}"

    def test_valid_units_batch(self):
        units = [_make_unit(unit_id=_VALID_ID + "#heading:a",
                            semantic_text="Introduction section."),
                 _make_unit(unit_id=_VALID_ID + "#heading:b",
                            semantic_text="Getting started guide.")]
        report = validate_units(units)
        assert report.total_units == 2
        assert report.valid_units == 2
        assert report.issue_count == 0


# ---------------------------------------------------------------------------
# Test: missing / empty required fields
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_missing_unit_id(self):
        unit = _make_unit()
        del unit["unit_id"]
        issues = validate_unit_record(unit)
        codes = {i.code for i in issues}
        assert "empty-unit-id" in codes

    def test_empty_unit_id(self):
        unit = _make_unit(unit_id="")
        issues = validate_unit_record(unit)
        codes = {i.code for i in issues}
        assert "empty-unit-id" in codes

    def test_missing_source_record_ids(self):
        unit = _make_unit()
        del unit["source_record_ids"]
        issues = validate_unit_record(unit)
        codes = {i.code for i in issues}
        assert "empty-source-record-ids" in codes

    def test_empty_source_record_ids(self):
        unit = _make_unit(source_record_ids=[])
        issues = validate_unit_record(unit)
        codes = {i.code for i in issues}
        assert "empty-source-record-ids" in codes

    def test_missing_provenance(self):
        unit = _make_unit()
        del unit["artifact_id"]
        # Also clear content_hash
        del unit["content_hash"]
        issues = validate_unit_record(unit)
        codes = {i.code for i in issues}
        assert "missing-provenance" in codes

    def test_missing_content_field(self):
        unit = _make_unit()
        del unit["semantic_text"]
        issues = validate_unit_record(unit)
        codes = {i.code for i in issues}
        assert "missing-content-field" in codes

    def test_missing_unit_type(self):
        unit = _make_unit()
        del unit["unit_type"]
        issues = validate_unit_record(unit)
        codes = {i.code for i in issues}
        assert "empty-unit-type" in codes


# ---------------------------------------------------------------------------
# Test: line spans
# ---------------------------------------------------------------------------


class TestLineSpan:
    def test_valid_line_span(self):
        unit = _make_unit()
        unit["source_anchor"] = {"line_start": 1, "line_end": 50}
        issues = validate_unit_record(unit)
        assert not any(i.code == "invalid-line-span" for i in issues)

    def test_line_start_greater_than_end(self):
        unit = _make_unit()
        unit["source_anchor"] = {"line_start": 50, "line_end": 10}
        issues = validate_unit_record(unit)
        assert any(i.code == "invalid-line-span" for i in issues)

    def test_non_integer_line_span(self):
        unit = _make_unit()
        unit["source_anchor"] = {"line_start": "abc", "line_end": "def"}
        issues = validate_unit_record(unit)
        assert any(i.code == "non-integer-line-span" for i in issues)


# ---------------------------------------------------------------------------
# Test: duplicate unit IDs
# ---------------------------------------------------------------------------


class TestDuplicateUnitId:
    def test_duplicate_unit_id_detected(self):
        uid = _VALID_ID + "#same"
        units = [
            _make_unit(unit_id=uid, semantic_text="first version"),
            _make_unit(unit_id=uid, semantic_text="second version"),
            _make_unit(unit_id=_VALID_ID + "#other", semantic_text="unique"),
        ]
        report = validate_units(units)
        assert report.duplicate_unit_ids == 1  # one duplicated ID
        assert any(i.code == "duplicate-unit-id" for i in report.issues)

    def test_no_false_positive_for_unique_ids(self):
        units = [
            _make_unit(unit_id=_VALID_ID + "#a"),
            _make_unit(unit_id=_VALID_ID + "#b"),
            _make_unit(unit_id=_VALID_ID + "#c"),
        ]
        report = validate_units(units)
        assert report.duplicate_unit_ids == 0
        assert not any(i.code == "duplicate-unit-id" for i in report.issues)


# ---------------------------------------------------------------------------
# Test: duplicate normalized content
# ---------------------------------------------------------------------------


class TestDuplicateContent:
    def test_duplicate_content_detected(self):
        units = [
            _make_unit(
                unit_id=_VALID_ID + "#a",
                source_record_ids=["github:owner/repo"],
                semantic_text="Hello World",
            ),
            _make_unit(
                unit_id=_VALID_ID + "#b",
                source_record_ids=["github:owner/repo"],
                semantic_text="  Hello   World  ",
            ),
        ]
        report = validate_units(units)
        assert report.duplicate_content_groups == 1
        assert any(i.code == "duplicate-normalized-content" for i in report.issues)

    def test_different_content_no_false_positive(self):
        units = [
            _make_unit(
                unit_id=_VALID_ID + "#a",
                source_record_ids=["github:owner/repo"],
                semantic_text="Hello World",
            ),
            _make_unit(
                unit_id=_VALID_ID + "#b",
                source_record_ids=["github:owner/repo"],
                semantic_text="Goodbye Moon",
            ),
        ]
        report = validate_units(units)
        assert report.duplicate_content_groups == 0

    def test_different_source_no_false_positive(self):
        units = [
            _make_unit(
                unit_id=_VALID_ID + "#a",
                source_record_ids=["github:alpha/proj"],
                semantic_text="Same content",
            ),
            _make_unit(
                unit_id=_VALID_ID + "#b",
                source_record_ids=["github:beta/proj"],
                semantic_text="Same content",
            ),
        ]
        report = validate_units(units)
        assert report.duplicate_content_groups == 0


# ---------------------------------------------------------------------------
# Test: Chinese text handling
# ---------------------------------------------------------------------------


class TestChineseText:
    def test_chinese_in_content_is_allowed(self):
        """Chinese characters in semantic_text must NOT be flagged."""
        unit = _make_unit(semantic_text="这是一个中文文档。介绍如何使用。")
        issues = validate_unit_record(unit)
        assert not any(i.code == "chinese-in-metadata-field" for i in issues)

    def test_chinese_in_metadata_is_flagged(self):
        """Chinese characters in metadata fields (e.g. schema_version) must be flagged."""
        unit = _make_unit(schema_version="版本1.0")
        issues = validate_unit_record(unit)
        assert any(i.code == "chinese-in-metadata-field" for i in issues)

    def test_non_chinese_metadata_not_flagged(self):
        unit = _make_unit()
        issues = validate_unit_record(unit)
        assert not any(i.code == "chinese-in-metadata-field" for i in issues)


# ---------------------------------------------------------------------------
# Test: report writer
# ---------------------------------------------------------------------------


class TestReportWriter:
    def test_write_report_creates_file(self, tmp_path):
        report = validate_units([
            _make_unit(unit_id=_VALID_ID + "#a",
                       semantic_text="First unit content."),
            _make_unit(unit_id=_VALID_ID + "#b", token_count=-1,
                       semantic_text="Second unit content."),
        ])
        out = tmp_path / "report.json"
        result = write_unit_validation_report(report, out)
        assert result == out
        assert out.exists()

        data = json.loads(out.read_text())
        assert data["summary"]["total_units"] == 2
        assert data["summary"]["issue_count"] == 1
        assert "manifest" in data

    def test_write_report_no_files_besides_report(self, tmp_path):
        """Verify no stray files are created."""
        report = validate_units([_make_unit()])
        before = set(tmp_path.rglob("*"))
        out = tmp_path / "validation-report.json"
        write_unit_validation_report(report, out)
        after = set(tmp_path.rglob("*"))
        new_files = after - before
        assert len(new_files) == 1
        assert list(new_files)[0].name == "validation-report.json"


# ---------------------------------------------------------------------------
# Test: loading
# ---------------------------------------------------------------------------


class TestLoading:
    def test_load_units_jsonl(self, tmp_path):
        path = tmp_path / "units.jsonl"
        path.write_text(
            json.dumps(_make_unit(unit_id=_VALID_ID + "#a")) + "\n"
            + json.dumps(_make_unit(unit_id=_VALID_ID + "#b")) + "\n"
        )
        units = load_units_jsonl(path)
        assert len(units) == 2

    def test_load_units_jsonl_empty_lines(self, tmp_path):
        path = tmp_path / "units.jsonl"
        path.write_text(
            json.dumps(_make_unit(unit_id=_VALID_ID + "#a")) + "\n\n\n"
            + json.dumps(_make_unit(unit_id=_VALID_ID + "#b")) + "\n"
        )
        units = load_units_jsonl(path)
        assert len(units) == 2

    def test_load_units_jsonl_invalid_json(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        path.write_text("{bad json}\n")
        with pytest.raises(ValueError, match="invalid JSON"):
            load_units_jsonl(path)

    def test_load_units_from_directory(self, tmp_path):
        d = tmp_path / "units"
        d.mkdir()
        for i, lid in enumerate(["x", "y", "z"]):
            unit = _make_unit(unit_id=_VALID_ID + f"#{lid}")
            (d / f"unit_{i}.json").write_text(json.dumps(unit))
        units = load_units_from_directory(d)
        assert len(units) == 3


# ---------------------------------------------------------------------------
# Test: unit_id format
# ---------------------------------------------------------------------------


class TestUnitIdFormat:
    def test_invalid_unit_id_format(self):
        unit = _make_unit(unit_id="not-a-sha256")
        issues = validate_unit_record(unit)
        assert any(i.code == "invalid-unit-id-format" for i in issues)

    def test_valid_unit_id_format(self):
        unit = _make_unit()
        issues = validate_unit_record(unit)
        assert not any(i.code == "invalid-unit-id-format" for i in issues)

    def test_duplicate_unit_id_no_false_negative_on_format(self):
        """Duplicate check still works alongside format check."""
        uid = _VALID_ID + "#dup"
        units = [
            _make_unit(unit_id=uid),
            _make_unit(unit_id=uid),
        ]
        report = validate_units(units)
        assert report.duplicate_unit_ids == 1


# ---------------------------------------------------------------------------
# Test: unknown unit_type
# ---------------------------------------------------------------------------


class TestUnknownUnitType:
    def test_unknown_unit_type_warns(self):
        unit = _make_unit(unit_type="something-unknown")
        issues = validate_unit_record(unit)
        assert any(i.code == "unknown-unit-type" for i in issues)

    def test_allowed_unit_type_no_warning(self):
        for ut in ("n8n-workflow", "doc-section", "code-symbol",
                   "configuration", "deployment-component",
                   "hermes-skill", "script-and-supporting"):
            unit = _make_unit(unit_type=ut)
            issues = validate_unit_record(unit)
            assert not any(i.code == "unknown-unit-type" for i in issues)


# ---------------------------------------------------------------------------
# Test: empty source_record_ids entries
# ---------------------------------------------------------------------------


class TestEmptySourceRecordIds:
    def test_empty_entry_in_source_record_ids(self):
        unit = _make_unit(source_record_ids=[""])
        issues = validate_unit_record(unit)
        assert any(i.code == "empty-source-record-id-entry" for i in issues)

    def test_mixed_empty_and_valid(self):
        unit = _make_unit(source_record_ids=["github:owner/repo", ""])
        issues = validate_unit_record(unit)
        assert any(i.code == "empty-source-record-id-entry" for i in issues)


# ---------------------------------------------------------------------------
# Test: negative token_count
# ---------------------------------------------------------------------------


class TestTokenCount:
    def test_negative_token_count(self):
        unit = _make_unit(token_count=-5)
        issues = validate_unit_record(unit)
        assert any(i.code == "negative-token-count" for i in issues)

    def test_zero_token_count(self):
        unit = _make_unit(token_count=0)
        issues = validate_unit_record(unit)
        assert not any(i.code == "negative-token-count" for i in issues)
        assert not any(i.code == "non-integer-token-count" for i in issues)


# ---------------------------------------------------------------------------
# Test: _normalize_content
# ---------------------------------------------------------------------------


class TestNormalizeContent:
    def test_collapses_whitespace(self):
        assert _normalize_content("  Hello   World  ") == "hello world"

    def test_lowercase(self):
        assert _normalize_content("Hello WORLD") == "hello world"

    def test_strips_punctuation_effectively(self):
        result = _normalize_content("Hello, World!!!")
        assert result == "hello, world!!!"  # only case+space normalized


# ---------------------------------------------------------------------------
# Test: _is_chinese
# ---------------------------------------------------------------------------


class TestIsChinese:
    def test_english_not_chinese(self):
        assert not _is_chinese("Hello World")

    def test_chinese_detected(self):
        assert _is_chinese("这是一个测试")

    def test_mixed_content(self):
        assert _is_chinese("Hello 世界")


# ---------------------------------------------------------------------------
# Test: no filesystem mutation
# ---------------------------------------------------------------------------


class TestNoFilesystemMutation:
    def test_validator_does_not_write_files(self, tmp_path):
        """validate_units must not create any files."""
        before = set(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
        units = [_make_unit(unit_id=_VALID_ID + "#a"),
                 _make_unit(unit_id=_VALID_ID + "#b")]
        validate_units(units)
        after = set(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
        assert before == after

    def test_validate_unit_record_no_side_effects(self, tmp_path):
        """Single record validation must not create files."""
        before = set(tmp_path.rglob("*"))
        unit = _make_unit()
        validate_unit_record(unit)
        after = set(tmp_path.rglob("*"))
        assert before == after


# ---------------------------------------------------------------------------
# Test: empty source_record_ids entry warning (not error)
# ---------------------------------------------------------------------------


class TestSeverityLevels:
    def test_empty_srids_is_warning_not_error(self):
        unit = _make_unit(source_record_ids=[""])
        issues = validate_unit_record(unit)
        for i in issues:
            if i.code == "empty-source-record-id-entry":
                assert i.severity == "warning"

    def test_missing_provenance_is_error(self):
        unit = _make_unit()
        del unit["artifact_id"]
        del unit["content_hash"]
        issues = validate_unit_record(unit)
        for i in issues:
            if i.code == "missing-provenance":
                assert i.severity == "error"
