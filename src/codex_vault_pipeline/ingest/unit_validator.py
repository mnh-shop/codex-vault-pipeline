"""Deterministic validation and dedup reporting for extracted units (Layer C).

Validates unit/v1 records against the schema, checks field invariants,
detects duplicate unit_ids and duplicate content within a source, and
produces a structured report.  No filesystem side effects except the
explicit report-write path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNIT_V1_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema",
    "schema_version",
    "record_id",
    "created_at",
    "generator",
    "generator_version",
    "run_id",
    "content_hash",
    "source_record_ids",
    "parser_name",
    "parser_version",
    "unit_id",
    "artifact_id",
    "source_anchor",
    "unit_type",
    "title",
    "semantic_text",
    "token_count",
    "fingerprints",
)

UNIT_V1_ALLOWED_TYPES: Tuple[str, ...] = (
    "n8n-workflow",
    "n8n-node",
    "doc-section",
    "code-symbol",
    "configuration",
    "deployment-component",
    "hermes-skill",
    "hermes-soul",
    "hermes-tool",
    "hermes-provider",
    "hermes-profile",
    "agentfield-agent",
    "agentfield-service",
    "agentfield-control-plane",
    "script-and-supporting",
)

SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
UNIT_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}#[^ ]+$")
HEX64_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ISO_DT_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
)

# Fields whose values must not contain Chinese characters (status/metadata).
# Content fields (semantic_text, title) are allowed to contain Chinese.
NON_CHINESE_FIELDS: Tuple[str, ...] = (
    "schema",
    "schema_version",
    "record_id",
    "created_at",
    "generator",
    "generator_version",
    "run_id",
    "content_hash",
    "parser_name",
    "parser_version",
    "unit_id",
    "artifact_id",
    "unit_type",
    "token_count",
    "dedup_group",
    "duplicate_of",
    "variant_of",
    "derived_from",
    "redacted",
)

CHINESE_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")

# Alias map: what user spec calls → what unit/v1 actually uses
CONTENT_FIELD_ALIASES: Tuple[str, ...] = (
    "semantic_text",
    "text",
    "content",
    "summary",
    "value",
)
EXTRACTION_METHOD_ALIASES: Tuple[str, ...] = (
    "generator",
    "parser_name",
    "extraction_method",
)
PROVENANCE_FIELD_ALIASES: Tuple[str, ...] = (
    "artifact_id",
    "content_sha256",
    "content_hash",
    "artifact_sha256",
)
SOURCE_ID_ALIASES: Tuple[str, ...] = (
    "source_record_ids",
    "source_id",
)
SOURCE_PATH_ALIASES: Tuple[str, ...] = (
    "source_anchor",
    "source_path",
)

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitValidationIssue:
    """A single issue found during validation.

    Attributes:
        severity:    ``"error"`` or ``"warning"``.
        code:        Machine-readable issue code.
        unit_id:     The unit's ``unit_id`` (may be ``None`` if missing).
        source_id:   Resolved source identifier, or ``None``.
        source_path: Resolved source path, or ``None``.
        message:     Human-readable description.
    """

    severity: str
    code: str
    unit_id: Optional[str]
    source_id: Optional[str]
    source_path: Optional[str]
    message: str


@dataclass(frozen=True)
class UnitValidationReport:
    """Structured validation report.

    Attributes:
        total_units:           Number of units examined.
        valid_units:           Number of units with zero errors.
        issue_count:           Total number of issues (errors + warnings).
        duplicate_unit_ids:    Count of unit_id values that appear >1x.
        duplicate_content_groups:
                               Count of content-fingerprint groups that
                               have >1 unit sharing the same source + normalised
                               content.
        issues:                All issues found, in occurrence order.
        unit_type_counts:      Breakdown of unit_type values.
        extraction_method_counts:
                               Breakdown of generator/parser_name values.
    """

    total_units: int
    valid_units: int
    issue_count: int
    duplicate_unit_ids: int
    duplicate_content_groups: int
    issues: Tuple[UnitValidationIssue, ...] = field(default_factory=tuple)
    unit_type_counts: Dict[str, int] = field(default_factory=dict)
    extraction_method_counts: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_units_jsonl(path: Path) -> Tuple[Dict[str, Any], ...]:
    """Load unit records from a JSONL file (one JSON object per line).

    Raises:
        FileNotFoundError:  If *path* does not exist.
        ValueError:         If a line is not valid JSON.
    """
    units: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                units.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {e}")
    return tuple(units)


def load_units_from_directory(path: Path) -> Tuple[Dict[str, Any], ...]:
    """Load unit records from all ``*.json`` files under *path*."""
    units: List[Dict[str, Any]] = []
    for f in sorted(path.rglob("*.json")):
        try:
            units.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, OSError) as e:
            raise ValueError(f"{f}: {e}")
    return tuple(units)


# ---------------------------------------------------------------------------
# Resolve provenance
# ---------------------------------------------------------------------------


def _resolve_source_id(unit: Dict[str, Any]) -> Optional[str]:
    """Best-effort resolution of a source identifier from a unit record."""
    for key in SOURCE_ID_ALIASES:
        val = unit.get(key)
        if val:
            if isinstance(val, (list, tuple)) and val:
                item = val[0]
                if isinstance(item, str) and item:
                    return item
            elif isinstance(val, str) and val:
                return val
    return None


def _resolve_source_path(unit: Dict[str, Any]) -> Optional[str]:
    """Best-effort resolution of a source path from a unit record."""
    anchor = unit.get("source_anchor") or {}
    section = anchor.get("section") if isinstance(anchor, dict) else None
    if section:
        return section
    sp = unit.get("source_path")
    if sp:
        return sp
    return None


def _is_chinese(text: str) -> bool:
    """Return True if *text* contains Chinese characters."""
    return bool(CHINESE_RE.search(text))


# ---------------------------------------------------------------------------
# Per-record validation
# ---------------------------------------------------------------------------


def validate_unit_record(
    unit: Dict[str, Any],
    seen_unit_ids: Optional[Dict[str, int]] = None,
    seen_content: Optional[Dict[str, Dict[str, List[str]]]] = None,
) -> Tuple[UnitValidationIssue, ...]:
    """Validate a single unit/v1 record.

    Arguments:
        unit:            Unit record dict.
        seen_unit_ids:   Mutable dict tracking ``unit_id → occurrence-count``
                         for duplicate detection.  Pass ``None`` (or omit) to
                         skip cross-record duplicate checks.
        seen_content:    Mutable dict tracking
                         ``source_id → {content_fingerprint → [unit_id, ...]}``
                         for duplicate-content detection.  Pass ``None`` to
                         skip.

    Returns a tuple of issues (empty if the record is clean).
    """
    issues: List[UnitValidationIssue] = []
    uid = unit.get("unit_id", "") or ""
    sid = _resolve_source_id(unit) or ""
    spath = _resolve_source_path(unit) or ""

    # ── Required fields (schema-level) ────────────────────────────────────
    for field in UNIT_V1_REQUIRED_FIELDS:
        if field not in unit:
            issues.append(
                UnitValidationIssue(
                    severity="error",
                    code="missing-required-field",
                    unit_id=uid or None,
                    source_id=sid or None,
                    source_path=spath or None,
                    message=f"Missing required field: {field!r}",
                )
            )

    # ── unit_id ───────────────────────────────────────────────────────────
    if not uid:
        issues.append(
            UnitValidationIssue(
                severity="error",
                code="empty-unit-id",
                unit_id=None,
                source_id=sid or None,
                source_path=spath or None,
                message="unit_id is empty or missing",
            )
        )
    elif not UNIT_ID_PATTERN.match(uid):
        issues.append(
            UnitValidationIssue(
                severity="error",
                code="invalid-unit-id-format",
                unit_id=uid,
                source_id=sid or None,
                source_path=spath or None,
                message=f"unit_id does not match sha256:xxx#anchor pattern: {uid!r}",
            )
        )

    # ── Duplicate unit_id ────────────────────────────────────────────────
    if uid and seen_unit_ids is not None:
        seen_unit_ids[uid] = seen_unit_ids.get(uid, 0) + 1
        if seen_unit_ids[uid] == 2:
            issues.append(
                UnitValidationIssue(
                    severity="error",
                    code="duplicate-unit-id",
                    unit_id=uid,
                    source_id=sid or None,
                    source_path=spath or None,
                    message=f"Duplicate unit_id: {uid}",
                )
            )

    # ── source_record_ids (proxy for source_id check) ────────────────────
    srids = unit.get("source_record_ids")
    if srids is None or (isinstance(srids, (list, tuple)) and len(srids) == 0):
        issues.append(
            UnitValidationIssue(
                severity="error",
                code="empty-source-record-ids",
                unit_id=uid or None,
                source_id=sid or None,
                source_path=spath or None,
                message="source_record_ids is empty or missing",
            )
        )
    elif isinstance(srids, (list, tuple)):
        # Check for empty-string entries
        for i, srid in enumerate(srids):
            if not srid or (isinstance(srid, str) and not srid.strip()):
                issues.append(
                    UnitValidationIssue(
                        severity="warning",
                        code="empty-source-record-id-entry",
                        unit_id=uid or None,
                        source_id=sid or None,
                        source_path=spath or None,
                        message=f"source_record_ids[{i}] is empty",
                    )
                )

    # ── Provenance (artifact_id / content_hash / etc) ─────────────────────
    has_provenance = False
    for key in PROVENANCE_FIELD_ALIASES:
        val = unit.get(key)
        if val and isinstance(val, str) and val.strip():
            has_provenance = True
            if key in ("artifact_id", "content_hash") and not SHA256_PATTERN.match(val):
                issues.append(
                    UnitValidationIssue(
                        severity="warning",
                        code="invalid-provenance-format",
                        unit_id=uid or None,
                        source_id=sid or None,
                        source_path=spath or None,
                        message=f"{key} does not match sha256:hex pattern: {val!r}",
                    )
                )
            break
    if not has_provenance:
        issues.append(
            UnitValidationIssue(
                severity="error",
                code="missing-provenance",
                unit_id=uid or None,
                source_id=sid or None,
                source_path=spath or None,
                message="No provenance field found (artifact_id, content_sha256, content_hash, "
                "or artifact_sha256)",
            )
        )

    # ── source_anchor (proxy for source_path) ─────────────────────────────
    anchor = unit.get("source_anchor")
    if anchor is not None and isinstance(anchor, dict):
        line_start = anchor.get("line_start")
        line_end = anchor.get("line_end")
        if line_start is not None and line_end is not None:
            if not isinstance(line_start, int) or not isinstance(line_end, int):
                issues.append(
                    UnitValidationIssue(
                        severity="warning",
                        code="non-integer-line-span",
                        unit_id=uid or None,
                        source_id=sid or None,
                        source_path=spath or None,
                        message=f"source_anchor.line_start ({line_start!r}) "
                        f"or line_end ({line_end!r}) is not an integer",
                    )
                )
            elif line_start > line_end:
                issues.append(
                    UnitValidationIssue(
                        severity="error",
                        code="invalid-line-span",
                        unit_id=uid or None,
                        source_id=sid or None,
                        source_path=spath or None,
                        message=f"source_anchor.line_start ({line_start}) > "
                        f"line_end ({line_end})",
                    )
                )
    else:
        issues.append(
            UnitValidationIssue(
                severity="warning",
                code="missing-source-anchor",
                unit_id=uid or None,
                source_id=sid or None,
                source_path=spath or None,
                message="source_anchor is missing or not a dict",
            )
        )

    # ── unit_type ─────────────────────────────────────────────────────────
    utype = unit.get("unit_type")
    if not utype:
        issues.append(
            UnitValidationIssue(
                severity="error",
                code="empty-unit-type",
                unit_id=uid or None,
                source_id=sid or None,
                source_path=spath or None,
                message="unit_type is empty or missing",
            )
        )
    elif utype not in UNIT_V1_ALLOWED_TYPES:
        issues.append(
            UnitValidationIssue(
                severity="warning",
                code="unknown-unit-type",
                unit_id=uid or None,
                source_id=sid or None,
                source_path=spath or None,
                message=f"unit_type {utype!r} is not in the allowed enum",
            )
        )

    # ── Content field (semantic_text or aliases) ──────────────────────────
    has_content = False
    for key in CONTENT_FIELD_ALIASES:
        val = unit.get(key)
        if val is not None:
            has_content = True
            break
    if not has_content:
        issues.append(
            UnitValidationIssue(
                severity="error",
                code="missing-content-field",
                unit_id=uid or None,
                source_id=sid or None,
                source_path=spath or None,
                message="No content field found (semantic_text, text, content, summary, or value)",
            )
        )

    # ── Extraction method ─────────────────────────────────────────────────
    has_method = False
    for key in EXTRACTION_METHOD_ALIASES:
        val = unit.get(key)
        if val and isinstance(val, str) and val.strip():
            has_method = True
            break
    if not has_method:
        issues.append(
            UnitValidationIssue(
                severity="warning",
                code="missing-extraction-method",
                unit_id=uid or None,
                source_id=sid or None,
                source_path=spath or None,
                message="No extraction method field (generator, parser_name, or extraction_method)",
            )
        )

    # ── Fingerprints ──────────────────────────────────────────────────────
    fp = unit.get("fingerprints")
    if fp is not None and isinstance(fp, dict):
        for fk in ("content_sha256", "normalized_hash", "structural_hash", "semantic_signature"):
            fv = fp.get(fk)
            if not fv:
                issues.append(
                    UnitValidationIssue(
                        severity="warning",
                        code="missing-fingerprint-field",
                        unit_id=uid or None,
                        source_id=sid or None,
                        source_path=spath or None,
                        message=f"fingerprints.{fk} is empty or missing",
                    )
                )
            elif fk != "semantic_signature" and isinstance(fv, str) and not HEX64_PATTERN.match(fv):
                issues.append(
                    UnitValidationIssue(
                        severity="warning",
                        code="invalid-fingerprint-format",
                        unit_id=uid or None,
                        source_id=sid or None,
                        source_path=spath or None,
                        message=f"fingerprints.{fk} does not match hex64: {fv!r}",
                    )
                )

    # ── token_count ───────────────────────────────────────────────────────
    tc = unit.get("token_count")
    if tc is not None:
        if not isinstance(tc, int):
            issues.append(
                UnitValidationIssue(
                    severity="warning",
                    code="non-integer-token-count",
                    unit_id=uid or None,
                    source_id=sid or None,
                    source_path=spath or None,
                    message=f"token_count is not an integer: {tc!r}",
                )
            )
        elif tc < 0:
            issues.append(
                UnitValidationIssue(
                    severity="error",
                    code="negative-token-count",
                    unit_id=uid or None,
                    source_id=sid or None,
                    source_path=spath or None,
                    message=f"token_count is negative: {tc}",
                )
            )

    # ── Chinese status leakage ────────────────────────────────────────────
    for key in NON_CHINESE_FIELDS:
        val = unit.get(key)
        if val is not None and isinstance(val, str) and _is_chinese(val):
            issues.append(
                UnitValidationIssue(
                    severity="warning",
                    code="chinese-in-metadata-field",
                    unit_id=uid or None,
                    source_id=sid or None,
                    source_path=spath or None,
                    message=f"Field {key!r} contains Chinese characters: {val[:80]!r}",
                )
            )

    # ── Duplicate normalized content within same source ───────────────────
    if uid and seen_content is not None and srids:
        _detect_duplicate_content(
            unit, uid, sid or srids[0] if isinstance(srids, (list, tuple)) and srids else sid,
            spath, seen_content, issues,
        )

    return tuple(issues)


def _detect_duplicate_content(
    unit: Dict[str, Any],
    unit_id: str,
    source_id: str,
    source_path: Optional[str],
    seen_content: Dict[str, Dict[str, List[str]]],
    issues: List[UnitValidationIssue],
) -> None:
    """Detect units that share the same normalised content within a source."""
    # Build content fingerprint from semantic_text or best content alias
    content = None
    for key in CONTENT_FIELD_ALIASES:
        val = unit.get(key)
        if val is not None and isinstance(val, str) and val.strip():
            content = val.strip()
            break
    if not content or not source_id:
        return

    norm = _normalize_content(content)
    fp = hashlib.sha256(norm.encode()).hexdigest()

    if source_id not in seen_content:
        seen_content[source_id] = {}

    group = seen_content[source_id]
    if fp in group:
        group[fp].append(unit_id)
        if len(group[fp]) == 2:
            prev_id = group[fp][0]
            issues.append(
                UnitValidationIssue(
                    severity="warning",
                    code="duplicate-normalized-content",
                    unit_id=unit_id,
                    source_id=source_id,
                    source_path=source_path,
                    message=f"Duplicate normalised content as {prev_id} "
                    f"(sha256:{fp[:16]}...) in source {source_id}",
                )
            )
    else:
        group[fp] = [unit_id]


def _normalize_content(text: str) -> str:
    """Normalise text for duplicate-content comparison."""
    # Lowercase, collapse whitespace, strip
    return " ".join(text.lower().split())


# ---------------------------------------------------------------------------
# Batch validation
# ---------------------------------------------------------------------------


def validate_units(
    units: Iterable[Dict[str, Any]],
    *,
    check_duplicates: bool = True,
) -> UnitValidationReport:
    """Validate an iterable of unit/v1 records.

    Arguments:
        units:               Iterable of unit record dicts.
        check_duplicates:    If ``True`` (default), run cross-record
                             duplicate detection (unit_id and content).
                             Set to ``False`` for streaming where the
                             caller manages dedup externally.

    Returns a :class:`UnitValidationReport`.
    """
    seen_unit_ids: Dict[str, int] = {} if check_duplicates else None  # type: ignore
    seen_content: Dict[str, Dict[str, List[str]]] = {} if check_duplicates else None  # type: ignore

    all_issues: List[UnitValidationIssue] = []
    valid_count = 0
    unit_type_counts: Dict[str, int] = {}
    extraction_method_counts: Dict[str, int] = {}
    total = 0

    for unit in units:
        total += 1

        # Count unit_type
        ut = unit.get("unit_type", "?")
        unit_type_counts[ut] = unit_type_counts.get(ut, 0) + 1

        # Count extraction method
        for ek in EXTRACTION_METHOD_ALIASES:
            ev = unit.get(ek)
            if ev and isinstance(ev, str):
                extraction_method_counts[ek] = extraction_method_counts.get(ek, 0) + 1
                break
        else:
            extraction_method_counts["(none)"] = extraction_method_counts.get("(none)", 0) + 1

        issues = validate_unit_record(
            unit,
            seen_unit_ids=seen_unit_ids,
            seen_content=seen_content,
        )
        all_issues.extend(issues)

        # valid = zero errors (warnings are OK for validity)
        has_error = any(i.severity == "error" for i in issues)
        if not has_error:
            valid_count += 1

    # Count duplicates from tracking dicts
    dup_id_count = 0
    if seen_unit_ids is not None:
        dup_id_count = sum(1 for c in seen_unit_ids.values() if c > 1)

    dup_content_count = 0
    if seen_content is not None:
        for src_id, groups in seen_content.items():
            dup_content_count += sum(1 for ids in groups.values() if len(ids) > 1)

    return UnitValidationReport(
        total_units=total,
        valid_units=valid_count,
        issue_count=len(all_issues),
        duplicate_unit_ids=dup_id_count,
        duplicate_content_groups=dup_content_count,
        issues=tuple(all_issues),
        unit_type_counts=unit_type_counts,
        extraction_method_counts=extraction_method_counts,
    )


# ---------------------------------------------------------------------------
# Report writing
# ---------------------------------------------------------------------------


def write_unit_validation_report(
    report: UnitValidationReport,
    path: Path,
    *,
    pretty: bool = True,
) -> Path:
    """Write a validation report as a JSON file.

    The report includes a ``manifest`` entry with generator metadata so
    the output is self-describing.

    Arguments:
        report:  The validation report.
        path:    Destination path.
        pretty:  If ``True`` (default), indent the JSON.

    Returns *path* for convenience.

    Raises:
        OSError:  If the file cannot be written.
    """
    issues_list = [
        {
            "severity": i.severity,
            "code": i.code,
            "unit_id": i.unit_id,
            "source_id": i.source_id,
            "source_path": i.source_path,
            "message": i.message,
        }
        for i in report.issues
    ]

    indent = 2 if pretty else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    body = {
        "manifest": {
            "generator": "codex-vault/unit-validator",
            "generator_version": "1.0.0",
            "created_at": now,
        },
        "summary": {
            "total_units": report.total_units,
            "valid_units": report.valid_units,
            "issue_count": report.issue_count,
            "duplicate_unit_ids": report.duplicate_unit_ids,
            "duplicate_content_groups": report.duplicate_content_groups,
        },
        "unit_type_counts": dict(report.unit_type_counts),
        "extraction_method_counts": dict(report.extraction_method_counts),
        "issues": issues_list,
    }

    path.write_text(json.dumps(body, indent=indent, ensure_ascii=False) + "\n")
    return path
