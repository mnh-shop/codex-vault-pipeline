"""Source-local invariant validators for incremental ingest.

Each function checks a specific invariant and returns a structured
result dict.  All functions are pure (no mutation), tolerate missing
directories, and do not require network access.

Runtime layout assumed (matching the existing vault pipeline convention):

    .runtime/artifacts/<sha256-hex>.json
    .runtime/occurrences/<safe-source-id>/<sha256-hex>.json
    .runtime/units/<unit-kind>/<safe-source-id>/<sha256-hex>.json
    .runtime/sources/<safe-source-id>/source.v1.yaml
    .runtime/knowledge-notes/<slug>.json
    .runtime/migration-reports/<slug>.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_id(source_id: str) -> str:
    """Deterministic filesystem-safe name from a source ID."""
    safe = source_id.replace(":", "_").replace("/", "_").replace("\\", "_")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_")


def _artifact_path(runtime_root: Path, artifact_id: str) -> Path:
    """Resolve an artifact_id (sha256:<hex>) to its JSON file path."""
    hex_part = artifact_id.replace("sha256:", "", 1)
    return runtime_root / "artifacts" / f"{hex_part}.json"


def _json_load(path: Path) -> Optional[Dict[str, Any]]:
    """Safely load a JSON file, returning None on any failure."""
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Single-invariant validators
# ---------------------------------------------------------------------------

Result = Tuple[bool, Optional[str]]


def validate_artifact_exists(runtime_root: Path, artifact_id: str) -> Result:
    """Check that an artifact JSON file exists on disk for the given ID.

    Returns (True, None) if the file exists, (False, error_message) otherwise.
    """
    if not artifact_id.startswith("sha256:"):
        return False, f"artifact_id does not start with 'sha256:': {artifact_id}"
    p = _artifact_path(runtime_root, artifact_id)
    if not p.is_file():
        return False, f"artifact file not found: {p}"
    return True, None


def validate_occurrence_artifact_links(
    runtime_root: Path,
    source_id: str,
) -> Dict[str, Any]:
    """Check that every occurrence's artifact_id resolves to a real artifact.

    Iterates over .runtime/occurrences/<safe-source-id>/*.json.
    """
    sid = _safe_id(source_id)
    occ_dir = runtime_root / "occurrences" / sid
    if not occ_dir.is_dir():
        return _result("fail", source_id, "occurrence_dir_missing",
                       [f"occurrences directory not found: {occ_dir}"])

    failures: List[str] = []
    warnings: List[str] = []
    checked = 0

    for p in sorted(occ_dir.glob("*.json")):
        rec = _json_load(p)
        if rec is None:
            warnings.append(f"unparseable occurrence file: {p}")
            continue
        checked += 1
        aid = rec.get("artifact_id", "")
        if not aid:
            warnings.append(f"occurrence missing artifact_id: {p.name}")
            continue
        ok, msg = validate_artifact_exists(runtime_root, aid)
        if not ok:
            failures.append(f"{p.name} -> {msg}")

    return _result(
        "fail" if failures else "pass",
        source_id,
        "occurrence_artifact_links",
        failures=failures,
        warnings=warnings,
        extra={"occurrence_files_checked": checked},
    )


def validate_unit_artifact_links(
    runtime_root: Path,
    source_id: str,
) -> Dict[str, Any]:
    """Check that every unit's artifact_id resolves to a real artifact.

    Iterates over .runtime/units/<kind>/<safe-source-id>/*.json
    for each unit kind subdirectory.
    """
    sid = _safe_id(source_id)
    units_root = runtime_root / "units"
    if not units_root.is_dir():
        return _result("pass", source_id, "unit_artifact_links",
                       warnings=["units directory not found — nothing to check"],
                       extra={"unit_kinds_checked": 0, "unit_files_checked": 0})

    failures: List[str] = []
    warnings: List[str] = []
    total_files = 0
    kinds_checked = 0

    for kind_dir in sorted(units_root.iterdir()):
        if not kind_dir.is_dir():
            continue
        source_unit_dir = kind_dir / sid
        if not source_unit_dir.is_dir():
            continue
        kinds_checked += 1
        for p in sorted(source_unit_dir.glob("*.json")):
            rec = _json_load(p)
            if rec is None:
                warnings.append(f"unparseable unit file: {p}")
                continue
            total_files += 1
            aid = rec.get("artifact_id", "")
            if not aid:
                # Units may be synthetic (no backing artifact); warn but don't fail
                warnings.append(f"unit missing artifact_id: {p}")
                continue
            ok, msg = validate_artifact_exists(runtime_root, aid)
            if not ok:
                failures.append(f"{p} -> {msg}")

    return _result(
        "fail" if failures else "pass",
        source_id,
        "unit_artifact_links",
        failures=failures,
        warnings=warnings,
        extra={
            "unit_kinds_checked": kinds_checked,
            "unit_files_checked": total_files,
        },
    )


def validate_source_record_ids(
    runtime_root: Path,
    source_record_id: str,
    source_id: str,
) -> Dict[str, Any]:
    """Check that a source_record_id resolves to an existing source record.

    The source record is expected at:

        .runtime/sources/<safe-source-id>/source.v1.yaml
    """
    sid = _safe_id(source_id)
    src_yaml = runtime_root / "sources" / sid / "source.v1.yaml"
    if not src_yaml.is_file():
        return _result("fail", source_id, "source_record_ids",
                       [f"source record file not found: {src_yaml}"])

    # Load and verify the record_id field matches
    try:
        import yaml
        rec = yaml.safe_load(src_yaml.read_text())
    except Exception:
        return _result("fail", source_id, "source_record_ids",
                       [f"source record unparseable: {src_yaml}"])

    if not isinstance(rec, dict):
        return _result("fail", source_id, "source_record_ids",
                       [f"source record is not a dict: {src_yaml}"])

    stored_id = rec.get("record_id", "")
    if stored_id != source_record_id:
        return _result("fail", source_id, "source_record_ids",
                       [f"source_record_id mismatch: expected {source_record_id}, "
                        f"found {stored_id} in {src_yaml}"])

    return _result("pass", source_id, "source_record_ids")


def validate_evidence_occurrences(
    runtime_root: Path,
    source_id: str,
) -> Dict[str, Any]:
    """Check knowledge-note and migration-report evidence for this source.

    Scans .runtime/knowledge-notes/*.json and .runtime/migration-reports/*.json
    for evidence entries referencing the given source_id, and verifies
    that each artifact_id and occurrence_id in the evidence resolves.
    """
    failures: List[str] = []
    warnings: List[str] = []
    checked = 0
    evidence_checked = 0

    for base_dir_name in ("knowledge-notes", "migration-reports"):
        base_dir = runtime_root / base_dir_name
        if not base_dir.is_dir():
            continue
        for p in sorted(base_dir.glob("*.json")):
            rec = _json_load(p)
            if rec is None:
                continue
            checked += 1
            evidence_list = rec.get("evidence", [])
            if not isinstance(evidence_list, list):
                continue
            for ev in evidence_list:
                ev_source = ev.get("source_id", "")
                if ev_source != source_id:
                    continue
                evidence_checked += 1
                aid = ev.get("artifact_id", "")
                if aid:
                    ok, msg = validate_artifact_exists(runtime_root, aid)
                    if not ok:
                        failures.append(
                            f"{base_dir_name}/{p.name}: evidence artifact {aid}: {msg}"
                        )
                occ_id = ev.get("occurrence_id", "")
                if occ_id:
                    ok, msg = _validate_occurrence_exists(runtime_root, occ_id, source_id)
                    if not ok:
                        failures.append(
                            f"{base_dir_name}/{p.name}: evidence occurrence {occ_id}: {msg}"
                        )

    if checked == 0:
        return _result("pass", source_id, "evidence_occurrences",
                       warnings=["no knowledge-notes or migration-reports found"],
                       extra={"records_checked": 0, "evidence_entries_checked": 0})

    return _result(
        "fail" if failures else "pass",
        source_id,
        "evidence_occurrences",
        failures=failures,
        warnings=warnings,
        extra={"records_checked": checked, "evidence_entries_checked": evidence_checked},
    )


# ---------------------------------------------------------------------------
# Aggregate validator
# ---------------------------------------------------------------------------

def validate_source_local(
    runtime_root: Path,
    source_id: str,
    source_record_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run all source-local validators and return an aggregate result.

    If *source_record_id* is None, the source-record-ID check is skipped.
    """
    checks: List[Dict[str, Any]] = []

    # 1. Occurrence artifact links
    r1 = validate_occurrence_artifact_links(runtime_root, source_id)
    checks.append(r1)

    # 2. Unit artifact links
    r2 = validate_unit_artifact_links(runtime_root, source_id)
    checks.append(r2)

    # 3. Source record IDs (only if source_record_id provided)
    if source_record_id is not None:
        r3 = validate_source_record_ids(runtime_root, source_record_id, source_id)
        checks.append(r3)

    # 4. Evidence occurrences
    r4 = validate_evidence_occurrences(runtime_root, source_id)
    checks.append(r4)

    # Aggregate
    all_failures: List[str] = []
    all_warnings: List[str] = []
    for c in checks:
        all_failures.extend(c.get("failures", []))
        all_warnings.extend(c.get("warnings", []))

    return _result(
        "fail" if all_failures else "pass",
        source_id,
        "source_local",
        failures=all_failures,
        warnings=all_warnings,
        extra={"sub_checks": len(checks)},
    )


# ---------------------------------------------------------------------------
# Internal helpers (continued)
# ---------------------------------------------------------------------------

def _validate_occurrence_exists(
    runtime_root: Path,
    occurrence_id: str,
    source_id: str,
) -> Result:
    """Check that an occurrence_id resolves to a file.  Pure, tolerates missing."""
    if not occurrence_id.startswith("sha256:"):
        return False, f"occurrence_id does not start with 'sha256:': {occurrence_id}"
    hex_part = occurrence_id.replace("sha256:", "", 1)
    sid = _safe_id(source_id)
    p = runtime_root / "occurrences" / sid / f"{hex_part}.json"
    if not p.is_file():
        # Also try without source_id subdir for broader search
        # (some occurrences may be organized differently)
        p2 = runtime_root / "occurrences" / f"{hex_part}.json"
        if not p2.is_file():
            return False, f"occurrence file not found in {sid}/ or root: {occurrence_id}"
    return True, None


def _result(
    status: str,
    source_id: str,
    check_name: str,
    failures: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a standardised validation result dict."""
    result: Dict[str, Any] = {
        "status": status,
        "source_id": source_id,
        "check": check_name,
        "failures": failures or [],
        "warnings": warnings or [],
    }
    if extra:
        result.update(extra)
    return result
