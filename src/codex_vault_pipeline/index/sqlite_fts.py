"""Deterministic SQLite FTS5 unit index.

Builds and queries an FTS5 index over unit records from the deterministic
unit extractor.

Usage::

    from codex_vault_pipeline.index.sqlite_fts import (
        build_units_fts_index,
        query_units_fts,
    )

    result = build_units_fts_index(
        unit_paths=[Path("units/doc-section")],
        db_path=Path("units-fts.sqlite"),
        occurrence_dir=Path(".runtime/occurrences"),
    )
    print(f"Indexed {result.unit_count} units from {result.source_count} sources")

    for hit in query_units_fts(result.db_path, "Hermes Agent"):
        print(hit["source_id"], hit["title"])
"""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FTSBuildResult:
    """Result of a successful FTS index build."""

    db_path: Path
    unit_count: int
    source_count: int


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

_JSON_SUFFIXES = frozenset({".json"})
_JSONL_SUFFIXES = frozenset({".jsonl"})


def iter_unit_jsonl(unit_paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    """Yield unit dicts from a mix of ``.json`` and ``.jsonl`` paths.

    * ``.json`` files are loaded as single unit records.
    * ``.jsonl`` files are streamed line-by-line.
    * Directories are walked recursively for ``.json`` files.
    """
    for path in unit_paths:
        if path.is_dir():
            yield from _iter_directory(path)
        elif path.suffix in _JSONL_SUFFIXES:
            yield from _iter_jsonl(path)
        elif path.suffix in _JSON_SUFFIXES:
            yield from _iter_json(path)


def _iter_directory(path: Path) -> Iterable[dict[str, Any]]:
    for f in sorted(path.rglob("*.json")):
        # skip validation reports or metadata files
        if f.name in ("unit-validation-report.json",):
            continue
        yield from _iter_json(f)


def _iter_json(path: Path) -> Iterable[dict[str, Any]]:
    try:
        yield json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARN: skipping unreadable unit file {path}: {exc}", file=sys.stderr)


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"WARN: skipping invalid JSONL line in {path}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Occurrence resolution
# ---------------------------------------------------------------------------


def _build_occurrence_lookup(
    occurrence_dir: Path,
) -> dict[str, tuple[str, str]]:
    """Build ``occurrence_id → (source_id, source_path)`` map.

    Occurrence files are named ``<hex>.json`` under subdirectories
    named by the safe source identifier.  The ``occurrence_id`` is
    stored inside the record as ``sha256:<hex>``.
    """
    lookup: dict[str, tuple[str, str]] = {}
    if not occurrence_dir.exists():
        return lookup
    for sub in sorted(occurrence_dir.iterdir()):
        if not sub.is_dir():
            continue
        for f in sub.glob("*.json"):
            try:
                occ = json.loads(f.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            oid = occ.get("occurrence_id", "")
            if not oid:
                continue
            lookup[oid] = (
                occ.get("source_id", ""),
                occ.get("source_path", ""),
            )
    return lookup


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS units (
    unit_id       TEXT PRIMARY KEY,
    source_id     TEXT NOT NULL DEFAULT '',
    source_path   TEXT NOT NULL DEFAULT '',
    unit_type     TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    text          TEXT NOT NULL DEFAULT '',
    artifact_id   TEXT NOT NULL DEFAULT '',
    content_sha256 TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL DEFAULT ''
);
"""

_CREATE_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
    title,
    text,
    source_id,
    source_path,
    unit_type,
    content='units',
    content_rowid='rowid'
);
"""

_INSERT_UNIT_SQL = """
INSERT OR IGNORE INTO units
    (unit_id, source_id, source_path, unit_type, title, text,
     artifact_id, content_sha256, extraction_method)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

_SYNC_FTS_SQL = """
INSERT INTO units_fts(units_fts) VALUES('rebuild');
"""

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_units_fts_index(
    unit_paths: Iterable[Path],
    db_path: Path,
    occurrence_dir: Optional[Path] = None,
) -> FTSBuildResult:
    """Build a deterministic FTS5 index from unit records.

    Arguments:
        unit_paths:     Paths to unit files — ``.json``, ``.jsonl``, or
                        directories of ``.json``.
        db_path:        Destination SQLite database path.  Parent
                        directories are created as needed.
        occurrence_dir: Optional path to occurrence records.  When
                        provided, ``source_id`` and ``source_path``
                        are resolved from the occurrence records via
                        the first entry in ``source_record_ids``.
                        If omitted, those fields are left empty in
                        the index.

    Returns:
        ``FTSBuildResult`` with the database path and record counts.

    The build is deterministic: the same unit files in the same order
    always produce the same database contents (insert order is stable).
    Duplicate ``unit_id`` values are silently skipped via
    ``INSERT OR IGNORE``.
    """
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Remove previous database at this path to avoid stale data
    if db_path.exists():
        db_path.unlink()

    # ---- occurrence lookup ----
    occ_lookup: dict[str, tuple[str, str]] = {}
    if occurrence_dir is not None:
        occ_lookup = _build_occurrence_lookup(occurrence_dir)

    # ---- build ----
    seen_sources: set[str] = set()
    unit_count = 0

    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA synchronous = OFF")
        con.execute("PRAGMA journal_mode = MEMORY")
        con.execute("PRAGMA cache_size = -4096")  # 4 MB
        con.execute(_CREATE_TABLE_SQL)
        con.execute(_CREATE_FTS_SQL)

        with con:
            for unit in iter_unit_jsonl(unit_paths):
                unit_id = unit.get("unit_id", "")
                if not unit_id:
                    continue  # skip records without a unit_id

                artifact_id = unit.get("artifact_id", "")
                unit_type = unit.get("unit_type", "")
                title = unit.get("title", "")
                text = unit.get("semantic_text") or unit.get("content") or ""
                extraction_method = unit.get("extraction_method") or unit.get("generator", "")

                # content_sha256 from fingerprints sub-dict
                fingerprints = unit.get("fingerprints", {})
                content_sha256 = ""
                if isinstance(fingerprints, dict):
                    content_sha256 = fingerprints.get("content_sha256", "")

                # source_id / source_path from occurrence resolution
                source_id = ""
                source_path = ""
                record_ids = unit.get("source_record_ids", [])
                if record_ids and occ_lookup:
                    oid = record_ids[0]
                    resolved = occ_lookup.get(oid)
                    if resolved:
                        source_id, source_path = resolved
                elif record_ids and not occ_lookup:
                    pass  # leave empty when no occurrence dir

                con.execute(
                    _INSERT_UNIT_SQL,
                    (
                        unit_id,
                        source_id,
                        source_path,
                        unit_type,
                        title,
                        text,
                        artifact_id,
                        content_sha256,
                        extraction_method,
                    ),
                )
                if con.total_changes > 0:
                    unit_count += 1
                    if source_id:
                        seen_sources.add(source_id)

        # Rebuild FTS index from the units content table.
        # Run inside an explicit transaction so Python sqlite3 commits before close.
        with con:
            con.execute(_SYNC_FTS_SQL)

    finally:
        con.close()

    return FTSBuildResult(
        db_path=db_path,
        unit_count=unit_count,
        source_count=len(seen_sources),
    )


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

_QUERY_SQL = """
SELECT
    u.unit_id,
    u.source_id,
    u.source_path,
    u.unit_type,
    u.title,
    snippet(units_fts, 1, '<<', '>>', '...', 48) AS text_preview,
    u.artifact_id,
    rank
FROM units_fts
JOIN units u ON units_fts.rowid = u.rowid
WHERE units_fts MATCH ?
ORDER BY rank
LIMIT ?
;
"""


def query_units_fts(
    db_path: Path,
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Query the FTS5 unit index.

    Arguments:
        db_path: Path to the SQLite database built by
                 :func:`build_units_fts_index`.
        query:   FTS5 query string (standard FTS5 syntax).
        limit:   Maximum results to return (default 10).

    Returns:
        A list of dicts with keys ``unit_id``, ``source_id``,
        ``source_path``, ``unit_type``, ``title``, ``text_preview``,
        ``artifact_id``, and ``rank``.
    """
    db_path = db_path.resolve()
    con = sqlite3.connect(str(db_path))
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(_QUERY_SQL, (query, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()
