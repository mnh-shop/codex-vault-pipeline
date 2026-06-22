"""Tests for codex_vault_pipeline.ingest.source_validators."""

import json
from pathlib import Path

from codex_vault_pipeline.ingest import source_validators

SOURCE_ID = "github:org/repo"
SAFE_ID = "github_org_repo"

# Convenience: a valid-looking sha256 hex (64 chars)
VALID_HASH = "ab" * 32
ARTIFACT_ID = f"sha256:{VALID_HASH}"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _artifact_path(root: Path, artifact_id: str = ARTIFACT_ID) -> Path:
    hex_part = artifact_id.replace("sha256:", "", 1)
    return root / ".runtime" / "artifacts" / f"{hex_part}.json"


def _write_artifact(root: Path, artifact_id: str = ARTIFACT_ID,
                    extra: dict = None) -> Path:
    p = _artifact_path(root, artifact_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "artifact/v1",
        "record_id": artifact_id,
        "artifact_id": artifact_id,
        "content_sha256": VALID_HASH,
        "media_type": "text/plain",
        "size_bytes": 42,
    }
    if extra:
        data.update(extra)
    p.write_text(json.dumps(data, indent=2))
    return p


def _write_occurrence(root: Path, source_id: str = SOURCE_ID,
                      artifact_id: str = ARTIFACT_ID,
                      occ_hash: str = None) -> Path:
    if occ_hash is None:
        occ_hash = "cc" * 32
    sid = SAFE_ID
    d = root / ".runtime" / "occurrences" / sid
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{occ_hash}.json"
    data = {
        "schema": "artifact-occurrence/v1",
        "occurrence_id": f"sha256:{occ_hash}",
        "source_id": source_id,
        "source_path": "path/to/file.py",
        "artifact_id": artifact_id,
    }
    p.write_text(json.dumps(data, indent=2))
    return p


def _write_unit(root: Path, kind: str = "documents",
                source_id: str = SOURCE_ID,
                artifact_id: str = ARTIFACT_ID,
                unit_hash: str = None) -> Path:
    if unit_hash is None:
        unit_hash = "dd" * 32
    sid = SAFE_ID
    d = root / ".runtime" / "units" / kind / sid
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{unit_hash}.json"
    data = {
        "schema": "unit/v1",
        "unit_id": f"sha256:{unit_hash}",
        "source_id": source_id,
        "artifact_id": artifact_id,
    }
    p.write_text(json.dumps(data, indent=2))
    return p


def _write_source_record_yaml(root: Path, source_id: str = SOURCE_ID,
                              record_id: str = None) -> Path:
    if record_id is None:
        record_id = f"sha256:{'ee' * 32}"
    sid = SAFE_ID
    d = root / ".runtime" / "sources" / sid
    d.mkdir(parents=True, exist_ok=True)
    p = d / "source.v1.yaml"
    p.write_text(
        f"schema: source/v1\n"
        f"source_id: {source_id}\n"
        f"record_id: {record_id}\n"
    )
    return p


def _write_knowledge_note(root: Path, slug: str = "test-note",
                          source_id: str = SOURCE_ID,
                          artifact_id: str = ARTIFACT_ID,
                          occurrence_id: str = None) -> Path:
    if occurrence_id is None:
        occurrence_id = f"sha256:{'ff' * 32}"
    d = root / ".runtime" / "knowledge-notes"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.json"
    data = {
        "schema": "knowledge-note/v1",
        "slug": slug,
        "evidence": [
            {
                "source_id": source_id,
                "artifact_id": artifact_id,
                "occurrence_id": occurrence_id,
            }
        ],
    }
    p.write_text(json.dumps(data, indent=2))
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateArtifactExists:
    def test_artifact_exists(self, tmp_path: Path):
        _write_artifact(tmp_path)
        ok, msg = source_validators.validate_artifact_exists(
            tmp_path / ".runtime", ARTIFACT_ID
        )
        assert ok is True
        assert msg is None

    def test_artifact_missing(self, tmp_path: Path):
        ok, msg = source_validators.validate_artifact_exists(
            tmp_path / ".runtime", ARTIFACT_ID
        )
        assert ok is False
        assert "not found" in msg

    def test_bad_artifact_id_format(self, tmp_path: Path):
        ok, msg = source_validators.validate_artifact_exists(
            tmp_path / ".runtime", "not-sha256"
        )
        assert ok is False
        assert "does not start with" in msg


class TestValidateOccurrenceArtifactLinks:
    def test_occurrence_referencing_existing_artifact_passes(self, tmp_path: Path):
        _write_artifact(tmp_path)
        _write_occurrence(tmp_path)
        result = source_validators.validate_occurrence_artifact_links(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "pass"
        assert len(result["failures"]) == 0

    def test_occurrence_referencing_missing_artifact_fails(self, tmp_path: Path):
        _write_occurrence(tmp_path)
        result = source_validators.validate_occurrence_artifact_links(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "fail"
        assert len(result["failures"]) >= 1
        assert "not found" in result["failures"][0]

    def test_missing_occurrence_dir_returns_fail(self, tmp_path: Path):
        result = source_validators.validate_occurrence_artifact_links(
            tmp_path / ".runtime", "no:source"
        )
        assert result["status"] == "fail"
        assert "occurrences directory not found" in str(result["failures"])


class TestValidateUnitArtifactLinks:
    def test_unit_referencing_existing_artifact_passes(self, tmp_path: Path):
        _write_artifact(tmp_path)
        _write_unit(tmp_path)
        result = source_validators.validate_unit_artifact_links(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "pass"
        assert len(result["failures"]) == 0

    def test_unit_referencing_missing_artifact_fails(self, tmp_path: Path):
        _write_unit(tmp_path)
        result = source_validators.validate_unit_artifact_links(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "fail"
        assert len(result["failures"]) >= 1
        assert "not found" in result["failures"][0]

    def test_missing_units_dir_returns_pass_with_warning(self, tmp_path: Path):
        result = source_validators.validate_unit_artifact_links(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "pass"
        assert len(result["warnings"]) >= 1


class TestValidateSourceRecordIds:
    def test_matching_record_id_passes(self, tmp_path: Path):
        rid = f"sha256:{'ee' * 32}"
        _write_source_record_yaml(tmp_path, record_id=rid)
        result = source_validators.validate_source_record_ids(
            tmp_path / ".runtime", rid, SOURCE_ID
        )
        assert result["status"] == "pass"

    def test_mismatched_record_id_fails(self, tmp_path: Path):
        rid = f"sha256:{'ee' * 32}"
        _write_source_record_yaml(tmp_path, record_id=rid)
        result = source_validators.validate_source_record_ids(
            tmp_path / ".runtime", "sha256:" + "ff" * 32, SOURCE_ID
        )
        assert result["status"] == "fail"
        assert "mismatch" in result["failures"][0]

    def test_missing_source_yaml_fails(self, tmp_path: Path):
        result = source_validators.validate_source_record_ids(
            tmp_path / ".runtime", "sha256:" + "ee" * 32, "no:source"
        )
        assert result["status"] == "fail"
        assert "not found" in result["failures"][0]


class TestValidateEvidenceOccurrences:
    def test_evidence_with_valid_artifact_passes(self, tmp_path: Path):
        occ_id = f"sha256:{'ff' * 32}"
        _write_artifact(tmp_path)
        _write_occurrence(tmp_path, occ_hash="ff" * 32)
        _write_knowledge_note(tmp_path, occurrence_id=occ_id)
        result = source_validators.validate_evidence_occurrences(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "pass"

    def test_evidence_with_missing_artifact_fails(self, tmp_path: Path):
        _write_knowledge_note(tmp_path)
        result = source_validators.validate_evidence_occurrences(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "fail"
        assert len(result["failures"]) >= 1

    def test_no_notes_dir_returns_pass_with_warning(self, tmp_path: Path):
        result = source_validators.validate_evidence_occurrences(
            tmp_path / ".runtime", SOURCE_ID
        )
        assert result["status"] == "pass"
        assert len(result["warnings"]) >= 1


class TestValidateSourceLocal:
    def test_aggregate_passes_when_all_valid(self, tmp_path: Path):
        rid = f"sha256:{'ee' * 32}"
        _write_artifact(tmp_path)
        _write_occurrence(tmp_path)
        _write_unit(tmp_path)
        _write_source_record_yaml(tmp_path, record_id=rid)
        result = source_validators.validate_source_local(
            tmp_path / ".runtime", SOURCE_ID, source_record_id=rid
        )
        assert result["status"] == "pass"

    def test_aggregate_fails_when_one_invariant_fails(self, tmp_path: Path):
        # Artifact exists, but occurrence references a DIFFERENT missing artifact
        _write_artifact(tmp_path)  # artifact for VALID_HASH
        _write_occurrence(tmp_path, artifact_id="sha256:" + "00" * 32)  # different = missing
        rid = f"sha256:{'ee' * 32}"
        _write_source_record_yaml(tmp_path, record_id=rid)
        result = source_validators.validate_source_local(
            tmp_path / ".runtime", SOURCE_ID, source_record_id=rid
        )
        assert result["status"] == "fail"
        assert len(result["failures"]) >= 1

    def test_missing_runtime_dirs_no_exception(self, tmp_path: Path):
        """All dirs missing — must return structured fail/warning, not crash."""
        result = source_validators.validate_source_local(
            tmp_path / ".runtime", SOURCE_ID
        )
        # Should not raise; result type should be dict
        assert isinstance(result, dict)
        assert "status" in result
        assert "failures" in result
        assert "warnings" in result
