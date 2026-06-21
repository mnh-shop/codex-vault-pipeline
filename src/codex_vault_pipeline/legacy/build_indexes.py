#!/usr/bin/env python3
"""
build_indexes.py — deterministic Phase 6 indexer.

Builds:
  1. SQLite metadata DB (.runtime/db/codex-vault.db)
  2. SQLite FTS5 search index (.runtime/indexes/codex-vault-fts.db)
  3. LanceDB vector index (.runtime/indexes/codex-vault-vectors/)

Inputs (read-only):
  - .runtime/sources/*/source.v1.yaml     (32 Layer A source records)
  - .runtime/artifacts/**/*.json          (5,790 artifacts)
  - .runtime/occurrences/**/*.json        (5,815 occurrences)
  - .runtime/bundles/**/*.json            (77 bundles)
  - .runtime/units/**/*.json              (40k+ units)
  - .runtime/domain/**/*.json             (3,250 domain records)
  - .runtime/relations/*.yaml             (21 relations)
  - .runtime/knowledge-notes/*.json       (30 candidates)
  - .runtime/migration-reports/*.yaml     (30 migrations)

Outputs (write-only):
  - .runtime/db/codex-vault.db
  - .runtime/indexes/codex-vault-fts.db
  - .runtime/indexes/codex-vault-vectors/
  - .runtime/indexes/index-build-manifest.json
  - .runtime/reports/index-build-log.txt
  - .runtime/reports/dependency-blocker-report.md  (only if any dep blocked)

Constraints:
  - No new candidates, no promotions.
  - raw/ untouched. wiki/ non-candidate files untouched.
  - Never index blocked content.
  - Never index unredacted flagged content (use redacted-safe text or skip).
  - Deterministic: fixed model, fixed seeds, no LLM at index time.

Hygiene rules (per task spec):
  - Every INSERT uses explicit column names; no positional VALUES.
  - Dict/list values are json.dumps-ed before bind; never raw.
  - Optional deps (lancedb, numpy, sentence_transformers) are imported
    inside try/except at runtime; Pyright/IDE warnings are not fatal.
  - One cur.execute() call = exactly one SQL string + one tuple of binds.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

# ---------- paths ----------

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"

DB_PATH = RUNTIME / "db" / "codex-vault.db"
FTS_PATH = RUNTIME / "indexes" / "codex-vault-fts.db"
VECTORS_PATH = RUNTIME / "indexes" / "codex-vault-vectors"
MANIFEST_PATH = RUNTIME / "indexes" / "index-build-manifest.json"
LOG_PATH = RUNTIME / "reports" / "index-build-log.txt"
DEPENDENCY_REPORT_PATH = RUNTIME / "reports" / "dependency-blocker-report.md"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
EMBED_BATCH = 128

# ---------- helpers ----------

_log_lines: list[str] = []
_dependency_blockers: list[dict] = []


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    _log_lines.append(line)
    print(line, file=sys.stderr, flush=True)


def jdump(value) -> str:
    """JSON-encode a dict/list for safe SQLite TEXT binding.

    Deterministic via sort_keys=True.
    """
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def to_int(value, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return default


def to_float(value, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    return default


def to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return jdump(value)
    return str(value)


def load_yaml(p: Path):
    return yaml.safe_load(p.read_text())


def load_json(p: Path):
    return json.loads(p.read_text())


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- dependency detection ----------

def detect_dependencies() -> dict:
    """Try to import optional deps; record blockers; never raise."""
    deps = {}
    for name in ("lancedb", "numpy", "sentence_transformers"):
        try:
            mod = __import__(name)
            deps[name] = {
                "available": True,
                "version": getattr(mod, "__version__", "unknown"),
            }
        except Exception as e:
            deps[name] = {
                "available": False,
                "version": None,
                "error": str(e),
            }
            _dependency_blockers.append({
                "module": name,
                "error": str(e),
                "remediation": (
                    f"pip install --user {name}  (or via the project's "
                    "documented install step)"
                ),
            })
    return deps


# ---------- 1. Metadata DB ----------

SCHEMA_METADATA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    source_id         TEXT PRIMARY KEY,
    record_id         TEXT UNIQUE NOT NULL,
    source_role       TEXT,
    authority_level   TEXT,
    primary_domain    TEXT,
    related_domains   TEXT,        -- JSON list
    lifecycle_status  TEXT,
    canonical_url     TEXT,
    resolved_revision TEXT,
    fetched_at        TEXT,
    filename          TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id       TEXT PRIMARY KEY,
    record_id         TEXT UNIQUE NOT NULL,
    source_id         TEXT,
    artifact_role     TEXT,
    preservation_mode TEXT,
    content_sha256    TEXT,
    security_status   TEXT,
    media_type        TEXT,
    source_path       TEXT,
    file_size         INTEGER,
    redacted          INTEGER DEFAULT 0,
    run_id            TEXT,
    created_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_artifacts_source ON artifacts(source_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_sec    ON artifacts(security_status);

CREATE TABLE IF NOT EXISTS occurrences (
    occurrence_id         TEXT PRIMARY KEY,
    record_id             TEXT UNIQUE NOT NULL,
    source_id             TEXT,
    content_sha256        TEXT,
    source_path           TEXT,
    contributing_top_dir  TEXT,
    contributing_sub_dir  TEXT,
    run_id                TEXT,
    created_at            TEXT,
    redacted              INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_occ_source ON occurrences(source_id);
CREATE INDEX IF NOT EXISTS idx_occ_sha    ON occurrences(content_sha256);

CREATE TABLE IF NOT EXISTS bundles (
    bundle_id            TEXT PRIMARY KEY,
    record_id            TEXT UNIQUE NOT NULL,
    bundle_role          TEXT,
    artifact_role        TEXT,
    preservation_mode    TEXT,
    execution_relevance  TEXT,
    entrypoint           TEXT,
    source_id            TEXT,
    run_id               TEXT,
    created_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_bundles_source ON bundles(source_id);

CREATE TABLE IF NOT EXISTS units (
    unit_id        TEXT PRIMARY KEY,
    record_id      TEXT UNIQUE NOT NULL,
    unit_type      TEXT,
    title          TEXT,
    source_id      TEXT,
    artifact_id    TEXT,
    token_count    INTEGER,
    redacted       INTEGER DEFAULT 0,
    source_path    TEXT,
    source_anchor  TEXT,        -- JSON if dict
    run_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_units_type   ON units(unit_type);
CREATE INDEX IF NOT EXISTS idx_units_source ON units(source_id);

CREATE TABLE IF NOT EXISTS domain_records (
    domain_record_id    TEXT PRIMARY KEY,
    record_id           TEXT UNIQUE NOT NULL,
    domain_type         TEXT,
    title               TEXT,
    source_id           TEXT,
    workflow_name       TEXT,
    skill_name          TEXT,
    node_count          INTEGER,
    connection_count    INTEGER,
    valid_n8n_document  INTEGER,
    redacted            INTEGER DEFAULT 0,
    run_id              TEXT,
    created_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_domain_type   ON domain_records(domain_type);
CREATE INDEX IF NOT EXISTS idx_domain_source ON domain_records(source_id);

CREATE TABLE IF NOT EXISTS candidates (
    slug                   TEXT PRIMARY KEY,
    record_id              TEXT UNIQUE NOT NULL,
    schema                 TEXT,
    schema_version         TEXT,
    title                  TEXT,
    domain_family          TEXT,
    source_role            TEXT,
    authority_level        TEXT,
    knowledge_status       TEXT,
    lifecycle_status       TEXT,
    coverage_status        TEXT,
    coverage_ratio         REAL,
    content_hash           TEXT,
    run_id                 TEXT,
    created_at             TEXT,
    last_verified_at       TEXT,
    generator              TEXT,
    generator_version      TEXT,
    summary_len            INTEGER,
    body_len               INTEGER,
    unresolved_claims_count INTEGER,
    evidence_count         INTEGER,
    source_taxonomy        TEXT         -- JSON array of {source_id, primary_domain, related_domains, source_role, authority_level}
);

CREATE TABLE IF NOT EXISTS migration_reports (
    candidate_slug             TEXT PRIMARY KEY,
    record_id                  TEXT UNIQUE NOT NULL,
    schema                     TEXT,
    schema_version             TEXT,
    source_id                  TEXT,
    candidate_record_id        TEXT,
    validation_status          TEXT,
    promotion_eligible         INTEGER,
    run_id                     TEXT,
    generated_at               TEXT,
    preserved_sections_count   INTEGER,
    removed_sections_count     INTEGER,
    new_evidence_links_count   INTEGER,
    unresolved_claims_count    INTEGER,
    promotion_blockers_count   INTEGER
);

CREATE TABLE IF NOT EXISTS evidence_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_slug  TEXT NOT NULL,
    source_id       TEXT,
    artifact_id     TEXT,
    unit_id         TEXT,
    occurrence_id   TEXT,
    anchor          TEXT,
    relation        TEXT,
    FOREIGN KEY (candidate_slug) REFERENCES candidates(slug)
);
CREATE INDEX IF NOT EXISTS idx_evidence_candidate ON evidence_links(candidate_slug);
CREATE INDEX IF NOT EXISTS idx_evidence_source    ON evidence_links(source_id);

CREATE TABLE IF NOT EXISTS security_status (
    source_id       TEXT PRIMARY KEY,
    total_artifacts INTEGER,
    clean           INTEGER,
    flagged         INTEGER,
    blocked         INTEGER,
    not_scanned     INTEGER,
    orphan          INTEGER,
    redacted_units  INTEGER
);

CREATE TABLE IF NOT EXISTS source_coverage (
    source_id          TEXT PRIMARY KEY,
    coverage_status    TEXT,
    candidate_slugs    TEXT,        -- JSON list
    occurrence_count   INTEGER,
    layer_a_record_id  TEXT
);
"""


def build_metadata_db() -> dict:
    log("Building SQLite metadata DB at " + str(DB_PATH))
    if DB_PATH.exists():
        DB_PATH.unlink()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.executescript(SCHEMA_METADATA)
    conn.commit()

    # ---- sources ----
    log("Loading sources...")
    src_count = 0
    for sub in sorted((RUNTIME / "sources").iterdir()):
        if not sub.is_dir():
            continue
        p = sub / "source.v1.yaml"
        if not p.exists():
            continue
        rec = load_yaml(p)
        cur.execute(
            "INSERT INTO sources "
            "(source_id, record_id, source_role, authority_level, primary_domain, "
            " related_domains, lifecycle_status, canonical_url, resolved_revision, "
            " fetched_at, filename) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                to_str(rec.get("source_id")),
                to_str(rec.get("record_id")),
                to_str(rec.get("source_role")),
                to_str(rec.get("authority_level")),
                to_str(rec.get("primary_domain")),
                jdump(rec.get("related_domains", [])),
                to_str(rec.get("lifecycle_status")),
                to_str(rec.get("canonical_url")),
                to_str(rec.get("resolved_revision")),
                to_str(rec.get("fetched_at")),
                to_str(str(p.relative_to(VAULT))),
            ),
        )
        src_count += 1
    conn.commit()
    log(f"  sources: {src_count}")

    # ---- artifacts ----
    log("Loading artifacts...")
    art_count = 0
    for p in (RUNTIME / "artifacts").rglob("*.json"):
        try:
            rec = load_json(p)
        except Exception:
            continue
        cs = rec.get("content_sha256") or rec.get("sha256")
        if not cs:
            continue
        sec = rec.get("security_scan", {}).get("status", "not-scanned")
        cur.execute(
            "INSERT OR IGNORE INTO artifacts "
            "(artifact_id, record_id, source_id, artifact_role, preservation_mode, "
            " content_sha256, security_status, media_type, source_path, file_size, "
            " redacted, run_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                to_str(rec.get("artifact_id")),
                to_str(rec.get("record_id")),
                to_str(rec.get("source_id")),
                to_str(rec.get("artifact_role")),
                to_str(rec.get("preservation_mode")),
                to_str(cs),
                to_str(sec) or "not-scanned",
                to_str(rec.get("media_type")),
                to_str(rec.get("source_path")),
                to_int(rec.get("file_size")),
                1 if rec.get("redacted") or rec.get("redaction_required") else 0,
                to_str(rec.get("run_id")),
                to_str(rec.get("created_at")),
            ),
        )
        art_count += 1
    conn.commit()
    log(f"  artifacts: {art_count}")

    # ---- occurrences ----
    log("Loading occurrences...")
    occ_count = 0
    for p in (RUNTIME / "occurrences").rglob("*.json"):
        try:
            rec = load_json(p)
        except Exception:
            continue
        oid = rec.get("occurrence_id")
        if not oid:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO occurrences "
            "(occurrence_id, record_id, source_id, content_sha256, source_path, "
            " contributing_top_dir, contributing_sub_dir, run_id, created_at, redacted) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                to_str(oid),
                to_str(rec.get("record_id")),
                to_str(rec.get("source_id")),
                to_str(rec.get("content_sha256")),
                to_str(rec.get("source_path")),
                to_str(rec.get("contributing_top_dir")),
                to_str(rec.get("contributing_sub_dir")),
                to_str(rec.get("run_id")),
                to_str(rec.get("created_at")),
                1 if rec.get("redacted") else 0,
            ),
        )
        occ_count += 1
    conn.commit()
    log(f"  occurrences: {occ_count}")

    # ---- bundles ----
    log("Loading bundles...")
    bundle_count = 0
    for p in (RUNTIME / "bundles").rglob("*.json"):
        try:
            rec = load_json(p)
        except Exception:
            continue
        bid = rec.get("bundle_id") or rec.get("record_id")
        if not bid:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO bundles "
            "(bundle_id, record_id, bundle_role, artifact_role, preservation_mode, "
            " execution_relevance, entrypoint, source_id, run_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                to_str(bid),
                to_str(rec.get("record_id")),
                to_str(rec.get("bundle_role")),
                to_str(rec.get("artifact_role")),
                to_str(rec.get("preservation_mode")),
                to_str(rec.get("execution_relevance")),
                to_str(rec.get("entrypoint")),
                to_str(rec.get("source_id")),
                to_str(rec.get("run_id")),
                to_str(rec.get("created_at")),
            ),
        )
        bundle_count += 1
    conn.commit()
    log(f"  bundles: {bundle_count}")

    # ---- units ----
    log("Loading units...")
    unit_count = 0
    for unit_dir in (RUNTIME / "units").iterdir():
        if not unit_dir.is_dir():
            continue
        unit_type = unit_dir.name
        for p in unit_dir.rglob("*.json"):
            try:
                rec = load_json(p)
            except Exception:
                continue
            uid = rec.get("record_id")
            if not uid:
                continue
            sri = rec.get("source_record_ids", [])
            sid = sri[0] if sri else None
            anchor = rec.get("source_anchor")
            if isinstance(anchor, dict):
                anchor = anchor.get("section") or anchor.get("json_pointer") or jdump(anchor)
            cur.execute(
                "INSERT OR IGNORE INTO units "
                "(unit_id, record_id, unit_type, title, source_id, artifact_id, "
                " token_count, redacted, source_path, source_anchor, run_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    to_str(uid),
                    to_str(rec.get("record_id")),
                    to_str(unit_type),
                    to_str(rec.get("title")),
                    to_str(sid),
                    to_str(rec.get("artifact_id")),
                    to_int(rec.get("token_count")),
                    1 if rec.get("redacted") else 0,
                    to_str(rec.get("source_path")),
                    to_str(anchor),
                    to_str(rec.get("run_id")),
                ),
            )
            unit_count += 1
    conn.commit()
    log(f"  units: {unit_count}")

    # ---- domain records ----
    log("Loading domain records...")
    dom_count = 0
    for dom_dir in (RUNTIME / "domain").iterdir():
        if not dom_dir.is_dir():
            continue
        dom_type = dom_dir.name
        for p in dom_dir.rglob("*.json"):
            try:
                rec = load_json(p)
            except Exception:
                continue
            rid = rec.get("record_id")
            if not rid:
                continue
            sid = rec.get("source_id")
            if not sid and rec.get("source_record_ids"):
                sid = rec["source_record_ids"][0]
            cur.execute(
                "INSERT OR IGNORE INTO domain_records "
                "(domain_record_id, record_id, domain_type, title, source_id, "
                " workflow_name, skill_name, node_count, connection_count, "
                " valid_n8n_document, redacted, run_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    to_str(rid),
                    to_str(rec.get("record_id")),
                    to_str(dom_type),
                    to_str(rec.get("title") or rec.get("workflow_name") or rec.get("skill_name")),
                    to_str(sid),
                    to_str(rec.get("workflow_name")),
                    to_str(rec.get("skill_name")),
                    to_int(rec.get("node_count")),
                    to_int(rec.get("connection_count")),
                    1 if rec.get("valid_n8n_document") else 0,
                    1 if rec.get("redacted") else 0,
                    to_str(rec.get("run_id")),
                    to_str(rec.get("created_at")),
                ),
            )
            dom_count += 1
    conn.commit()
    log(f"  domain records: {dom_count}")

    # ---- candidates ----
    log("Loading candidates...")
    cand_count = 0
    for p in sorted((RUNTIME / "knowledge-notes").glob("*.json")):
        rec = load_json(p)
        slug = rec.get("slug") or p.stem
        body = rec.get("body_markdown", "") or ""
        summary = rec.get("summary", "") or ""
        cur.execute(
            "INSERT OR REPLACE INTO candidates "
            "(slug, record_id, schema, schema_version, title, domain_family, "
            " source_role, authority_level, knowledge_status, lifecycle_status, "
            " coverage_status, coverage_ratio, content_hash, run_id, created_at, "
            " last_verified_at, generator, generator_version, summary_len, body_len, "
            " unresolved_claims_count, evidence_count, source_taxonomy) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                to_str(slug),
                to_str(rec.get("record_id")),
                to_str(rec.get("schema")),
                to_str(rec.get("schema_version")),
                to_str(rec.get("title")),
                to_str(rec.get("domain_family")),
                to_str(rec.get("source_role")),
                to_str(rec.get("authority_level")),
                to_str(rec.get("knowledge_status")),
                to_str(rec.get("lifecycle_status")),
                to_str(rec.get("coverage_status")),
                to_float(rec.get("coverage_ratio")),
                to_str(rec.get("content_hash")),
                to_str(rec.get("run_id")),
                to_str(rec.get("created_at")),
                to_str(rec.get("last_verified_at")),
                to_str(rec.get("generator")),
                to_str(rec.get("generator_version")),
                len(summary),
                len(body),
                len(rec.get("unresolved_claims", [])),
                len(rec.get("evidence", [])),
                jdump(rec.get("source_taxonomy", [])),
            ),
        )
        # evidence links (separate execute call, separate SQL string)
        for ev in rec.get("evidence", []):
            cur.execute(
                "INSERT INTO evidence_links "
                "(candidate_slug, source_id, artifact_id, unit_id, occurrence_id, anchor, relation) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    to_str(slug),
                    to_str(ev.get("source_id")),
                    to_str(ev.get("artifact_id")),
                    to_str(ev.get("unit_id")),
                    to_str(ev.get("occurrence_id")),
                    to_str(ev.get("anchor")),
                    to_str(ev.get("relation")),
                ),
            )
        cand_count += 1
    conn.commit()
    log(f"  candidates: {cand_count}")

    # ---- migration reports ----
    log("Loading migration reports...")
    mig_count = 0
    for p in sorted((RUNTIME / "migration-reports").glob("*.yaml")):
        rec = load_yaml(p)
        slug = rec.get("candidate_slug") or p.stem
        cur.execute(
            "INSERT OR REPLACE INTO migration_reports "
            "(candidate_slug, record_id, schema, schema_version, source_id, "
            " candidate_record_id, validation_status, promotion_eligible, run_id, "
            " generated_at, preserved_sections_count, removed_sections_count, "
            " new_evidence_links_count, unresolved_claims_count, promotion_blockers_count) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                to_str(slug),
                to_str(rec.get("record_id")),
                to_str(rec.get("schema")),
                to_str(rec.get("schema_version")),
                to_str(rec.get("source_id")),
                to_str(rec.get("candidate_record_id")),
                to_str(rec.get("validation_status")),
                1 if rec.get("promotion_eligible") else 0,
                to_str(rec.get("run_id")),
                to_str(rec.get("generated_at")),
                len(rec.get("preserved_sections", [])),
                len(rec.get("removed_sections", [])),
                len(rec.get("new_evidence_links", [])),
                len(rec.get("unresolved_claims", [])),
                len(rec.get("promotion_blockers", [])),
            ),
        )
        mig_count += 1
    conn.commit()
    log(f"  migration_reports: {mig_count}")

    # ---- security_status per source ----
    log("Computing security status per source...")
    cur.execute(
        "SELECT a.source_id, COUNT(*) AS total, "
        " SUM(CASE WHEN a.security_status = 'clean' THEN 1 ELSE 0 END) AS clean, "
        " SUM(CASE WHEN a.security_status = 'flagged' THEN 1 ELSE 0 END) AS flagged, "
        " SUM(CASE WHEN a.security_status = 'blocked' THEN 1 ELSE 0 END) AS blocked, "
        " SUM(CASE WHEN a.security_status NOT IN ('clean','flagged','blocked') OR a.security_status IS NULL THEN 1 ELSE 0 END) AS not_scanned "
        "FROM artifacts a GROUP BY a.source_id"
    )
    for row in cur.fetchall():
        sid, total, clean, flagged, blocked, ns = row
        cur.execute(
            "INSERT OR REPLACE INTO security_status "
            "(source_id, total_artifacts, clean, flagged, blocked, not_scanned, orphan, redacted_units) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (to_str(sid), to_int(total), to_int(clean), to_int(flagged),
             to_int(blocked), to_int(ns), 0, 0),
        )
    conn.commit()

    # ---- source_coverage ----
    log("Computing source coverage...")
    cur.execute("SELECT candidate_slug, source_id FROM evidence_links WHERE source_id IS NOT NULL")
    source_to_slugs: dict[str, set[str]] = defaultdict(set)
    for slug, sid in cur.fetchall():
        if sid:
            source_to_slugs[sid].add(slug)
    cur.execute("SELECT source_id, record_id FROM sources")
    for sid, rid in cur.fetchall():
        slugs = sorted(source_to_slugs.get(sid, set()))
        if not slugs:
            status = "needs-follow-up"
        elif len(slugs) > 1:
            status = "cross-covered"
        else:
            status = "covered"
        cur.execute("SELECT COUNT(*) FROM occurrences WHERE source_id = ?", (sid,))
        occ = cur.fetchone()[0]
        cur.execute(
            "INSERT OR REPLACE INTO source_coverage "
            "(source_id, coverage_status, candidate_slugs, occurrence_count, layer_a_record_id) "
            "VALUES (?,?,?,?,?)",
            (to_str(sid), to_str(status), jdump(slugs), to_int(occ), to_str(rid)),
        )
    conn.commit()

    summary: dict[str, int] = {}
    for tbl in [
        "sources", "artifacts", "occurrences", "bundles", "units",
        "domain_records", "candidates", "migration_reports",
        "evidence_links", "security_status", "source_coverage",
    ]:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        summary[tbl] = cur.fetchone()[0]
    log(f"Metadata DB row counts: {summary}")
    conn.close()
    return summary


# ---------- 2. FTS5 search index ----------

SCHEMA_FTS = """
PRAGMA journal_mode = WAL;

CREATE VIRTUAL TABLE IF NOT EXISTS candidate_fts USING fts5(
    slug UNINDEXED,
    title,
    summary,
    body,
    scope_covers,
    scope_excludes,
    domain_family UNINDEXED,
    source_role UNINDEXED,
    authority_level UNINDEXED,
    source_taxonomy UNINDEXED,        -- JSON array as text; not searchable, retrievable as metadata
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS n8n_workflow_fts USING fts5(
    workflow_id UNINDEXED,
    workflow_name,
    description,
    semantic_text,
    source_id UNINDEXED,
    source_path UNINDEXED,
    occurrence_id UNINDEXED,
    artifact_id UNINDEXED,
    security_status UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS doc_section_fts USING fts5(
    unit_id UNINDEXED,
    title,
    semantic_text,
    source_id UNINDEXED,
    source_path UNINDEXED,
    artifact_id UNINDEXED,
    security_status UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS skill_fts USING fts5(
    record_id UNINDEXED,
    kind UNINDEXED,
    title,
    semantic_text,
    source_id UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS operational_fts USING fts5(
    record_id UNINDEXED,
    kind UNINDEXED,
    title,
    semantic_text,
    source_id UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE IF NOT EXISTS source_fts USING fts5(
    source_id UNINDEXED,
    source_role UNINDEXED,
    primary_domain UNINDEXED,
    title,
    source_path,
    tokenize = 'porter unicode61'
);
"""


def build_fts_index(metadata_summary: dict) -> dict:
    log("Building SQLite FTS5 search index at " + str(FTS_PATH))
    if FTS_PATH.exists():
        FTS_PATH.unlink()
    FTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fts = sqlite3.connect(str(FTS_PATH))
    fts.row_factory = sqlite3.Row
    fts.executescript(SCHEMA_FTS)
    fts.commit()

    # Open metadata DB to do security/path joins
    md = sqlite3.connect(str(DB_PATH))
    md.row_factory = sqlite3.Row

    def art_security(aid: str | None) -> str | None:
        if not aid:
            return None
        r = md.execute(
            "SELECT security_status FROM artifacts WHERE artifact_id = ?", (aid,)
        ).fetchone()
        return r["security_status"] if r else None

    def art_source_path(aid: str | None) -> str | None:
        if not aid:
            return None
        r = md.execute(
            "SELECT source_path FROM artifacts WHERE artifact_id = ?", (aid,)
        ).fetchone()
        return r["source_path"] if r else None

    def occ_id_for_artifact(aid: str | None) -> str | None:
        if not aid:
            return None
        r = md.execute(
            "SELECT occurrence_id FROM occurrences "
            "WHERE content_sha256 = "
            "(SELECT content_sha256 FROM artifacts WHERE artifact_id = ?) "
            "LIMIT 1",
            (aid,),
        ).fetchone()
        return r["occurrence_id"] if r else None

    # ---- candidate_fts ----
    log("  Indexing candidates...")
    cand_fts_count = 0
    for p in sorted((RUNTIME / "knowledge-notes").glob("*.json")):
        rec = load_json(p)
        slug = rec.get("slug") or p.stem
        scope = rec.get("scope") or {}
        fts.execute(
            "INSERT INTO candidate_fts "
            "(slug, title, summary, body, scope_covers, scope_excludes, "
            " domain_family, source_role, authority_level, source_taxonomy) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                to_str(slug),
                to_str(rec.get("title")),
                to_str(rec.get("summary")),
                to_str(rec.get("body_markdown")),
                to_str(scope.get("covers")) if isinstance(scope, dict) else "",
                to_str(scope.get("excludes")) if isinstance(scope, dict) else "",
                to_str(rec.get("domain_family")),
                to_str(rec.get("source_role")),
                to_str(rec.get("authority_level")),
                jdump(rec.get("source_taxonomy", [])),
            ),
        )
        cand_fts_count += 1
    fts.commit()
    log(f"  candidate_fts rows: {cand_fts_count}")

    # ---- n8n_workflow_fts ----
    log("  Indexing n8n-workflow units...")
    n8n_fts_count = 0
    n8n_skipped_flagged = 0
    n8n_skipped_blocked = 0
    n8n_redacted = 0
    for p in (RUNTIME / "units" / "n8n-workflow").rglob("*.json"):
        try:
            rec = load_json(p)
        except Exception:
            continue
        sem = (rec.get("semantic_text") or "").strip()
        title = to_str(rec.get("title"))
        sri = rec.get("source_record_ids", [])
        sid = sri[0] if sri else None
        aid = rec.get("artifact_id")
        sec = "not-scanned"
        if rec.get("redacted"):
            sec = "redacted"
            n8n_redacted += 1
        art_sec = art_security(aid)
        if art_sec:
            sec = art_sec
        if sec == "blocked":
            n8n_skipped_blocked += 1
            continue
        if sec == "flagged":
            indexed_text = title
            n8n_skipped_flagged += 1
        else:
            indexed_text = sem or title
        oid = occ_id_for_artifact(aid) or ""
        sp = art_source_path(aid) or ""
        if not indexed_text and not title:
            continue
        fts.execute(
            "INSERT INTO n8n_workflow_fts "
            "(workflow_id, workflow_name, description, semantic_text, "
            " source_id, source_path, occurrence_id, artifact_id, security_status) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                to_str(rec.get("record_id")),
                title,
                "",  # description not always present
                indexed_text,
                to_str(sid),
                sp,
                oid,
                to_str(aid),
                to_str(sec),
            ),
        )
        n8n_fts_count += 1
    fts.commit()
    log(f"  n8n_workflow_fts rows: {n8n_fts_count} "
        f"(skipped {n8n_skipped_blocked} blocked, {n8n_skipped_flagged} flagged downgraded, {n8n_redacted} redacted)")

    # ---- doc_section_fts ----
    log("  Indexing doc-section units (clean only)...")
    ds_fts_count = 0
    ds_skipped_flagged = 0
    ds_skipped_blocked = 0
    for p in (RUNTIME / "units" / "doc-section").rglob("*.json"):
        try:
            rec = load_json(p)
        except Exception:
            continue
        sem = (rec.get("semantic_text") or "").strip()
        title = to_str(rec.get("title"))
        sri = rec.get("source_record_ids", [])
        sid = sri[0] if sri else None
        aid = rec.get("artifact_id")
        art_sec = art_security(aid)
        sec = art_sec or "not-scanned"
        if sec == "blocked":
            ds_skipped_blocked += 1
            continue
        if sec == "flagged":
            indexed_text = title
            ds_skipped_flagged += 1
        else:
            indexed_text = sem
        if not indexed_text and not title:
            continue
        sp = art_source_path(aid) or ""
        fts.execute(
            "INSERT INTO doc_section_fts "
            "(unit_id, title, semantic_text, source_id, source_path, artifact_id, security_status) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                to_str(rec.get("record_id")),
                title,
                indexed_text,
                to_str(sid),
                sp,
                to_str(aid),
                to_str(sec),
            ),
        )
        ds_fts_count += 1
    fts.commit()
    log(f"  doc_section_fts rows: {ds_fts_count} "
        f"(skipped {ds_skipped_blocked} blocked, {ds_skipped_flagged} flagged downgraded)")

    # ---- skill_fts ----
    log("  Indexing hermes-skill + hermes-soul units...")
    skill_fts_count = 0
    for kind, sub in [("skill", "hermes-skill"), ("soul", "hermes-soul")]:
        for p in (RUNTIME / "units" / sub).rglob("*.json"):
            try:
                rec = load_json(p)
            except Exception:
                continue
            sem = (rec.get("semantic_text") or "").strip()
            title = to_str(rec.get("title"))
            sri = rec.get("source_record_ids", [])
            sid = sri[0] if sri else None
            aid = rec.get("artifact_id")
            art_sec = art_security(aid)
            sec = art_sec or "not-scanned"
            if sec == "blocked":
                continue
            if sec == "flagged":
                indexed_text = title
            else:
                indexed_text = sem
            if not indexed_text and not title:
                continue
            fts.execute(
                "INSERT INTO skill_fts "
                "(record_id, kind, title, semantic_text, source_id) "
                "VALUES (?,?,?,?,?)",
                (
                    to_str(rec.get("record_id")),
                    to_str(kind),
                    title,
                    indexed_text,
                    to_str(sid),
                ),
            )
            skill_fts_count += 1
    fts.commit()
    log(f"  skill_fts rows: {skill_fts_count}")

    # ---- operational_fts ----
    log("  Indexing operational units (config, deploy, scripts)...")
    op_fts_count = 0
    for kind, sub in [
        ("configuration", "configuration"),
        ("deployment", "deployment-component"),
        ("script", "script-and-supporting"),
    ]:
        for p in (RUNTIME / "units" / sub).rglob("*.json"):
            try:
                rec = load_json(p)
            except Exception:
                continue
            sem = (rec.get("semantic_text") or "").strip()
            title = to_str(rec.get("title"))
            sri = rec.get("source_record_ids", [])
            sid = sri[0] if sri else None
            aid = rec.get("artifact_id")
            art_sec = art_security(aid)
            sec = art_sec or "not-scanned"
            if sec == "blocked":
                continue
            if sec == "flagged":
                indexed_text = title
            else:
                indexed_text = sem
            if not indexed_text and not title:
                continue
            fts.execute(
                "INSERT INTO operational_fts "
                "(record_id, kind, title, semantic_text, source_id) "
                "VALUES (?,?,?,?,?)",
                (
                    to_str(rec.get("record_id")),
                    to_str(kind),
                    title,
                    indexed_text,
                    to_str(sid),
                ),
            )
            op_fts_count += 1
    fts.commit()
    log(f"  operational_fts rows: {op_fts_count}")

    # ---- source_fts ----
    log("  Indexing sources...")
    src_fts_count = 0
    for sub in sorted((RUNTIME / "sources").iterdir()):
        if not sub.is_dir():
            continue
        p = sub / "source.v1.yaml"
        if not p.exists():
            continue
        rec = load_yaml(p)
        rows = md.execute(
            "SELECT source_path FROM artifacts WHERE source_id = ? LIMIT 50",
            (rec.get("source_id"),),
        ).fetchall()
        paths = " ".join(r["source_path"] for r in rows if r["source_path"])
        title = rec.get("source_id", "").split("/")[-1] if rec.get("source_id") else ""
        fts.execute(
            "INSERT INTO source_fts "
            "(source_id, source_role, primary_domain, title, source_path) "
            "VALUES (?,?,?,?,?)",
            (
                to_str(rec.get("source_id")),
                to_str(rec.get("source_role")),
                to_str(rec.get("primary_domain")),
                to_str(title),
                to_str(paths),
            ),
        )
        src_fts_count += 1
    fts.commit()
    log(f"  source_fts rows: {src_fts_count}")

    summary: dict[str, int] = {}
    for tbl in [
        "candidate_fts", "n8n_workflow_fts", "doc_section_fts",
        "skill_fts", "operational_fts", "source_fts",
    ]:
        fts.execute(f"SELECT COUNT(*) FROM {tbl}")
        summary[tbl] = fts.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    log(f"FTS row counts: {summary}")
    md.close()
    fts.close()
    return summary


# ---------- 3. Vector index ----------

def build_vector_index(deps: dict) -> dict:
    log("Building LanceDB vector index at " + str(VECTORS_PATH))
    VECTORS_PATH.mkdir(parents=True, exist_ok=True)
    if not all(d.get("available") for d in deps.values()):
        blockers = ", ".join(
            k for k, v in deps.items() if not v.get("available")
        )
        log(f"  Vector index blocked by missing deps: {blockers}")
        return {"status": "blocked", "reason": f"missing deps: {blockers}", "tables": {}}
    # All deps available; import here
    import numpy as np
    import lancedb
    from sentence_transformers import SentenceTransformer

    log(f"  Loading model {EMBEDDING_MODEL}...")
    t0 = time.time()
    model = SentenceTransformer(EMBEDDING_MODEL)
    log(f"  Model loaded in {time.time()-t0:.1f}s")
    db = lancedb.connect(str(VECTORS_PATH))

    md = sqlite3.connect(str(DB_PATH))
    md.row_factory = sqlite3.Row
    art_sec: dict[str, str | None] = {}
    for row in md.execute("SELECT artifact_id, security_status FROM artifacts"):
        art_sec[row["artifact_id"]] = row["security_status"]

    def occ_id_for_artifact(aid: str | None) -> str | None:
        if not aid:
            return None
        r = md.execute(
            "SELECT occurrence_id FROM occurrences "
            "WHERE content_sha256 = "
            "(SELECT content_sha256 FROM artifacts WHERE artifact_id = ?) "
            "LIMIT 1",
            (aid,),
        ).fetchone()
        return r["occurrence_id"] if r else None

    def art_source_path(aid: str | None) -> str | None:
        if not aid:
            return None
        r = md.execute(
            "SELECT source_path FROM artifacts WHERE artifact_id = ?", (aid,)
        ).fetchone()
        return r["source_path"] if r else None

    # ---- candidates ----
    log("  Encoding candidates...")
    cand_rows: list[dict] = []
    for p in sorted((RUNTIME / "knowledge-notes").glob("*.json")):
        rec = load_json(p)
        slug = rec.get("slug") or p.stem
        text = f"{rec.get('title','')}\n\n{rec.get('summary','')}\n\n{(rec.get('body_markdown') or '')[:2000]}"
        source_ids = sorted({ev.get("source_id") for ev in rec.get("evidence", []) if ev.get("source_id")})
        source_taxonomy = rec.get("source_taxonomy", [])
        cand_rows.append({
            "id": f"cand:{slug}",
            "kind": "candidate",
            "slug": to_str(slug),
            "title": to_str(rec.get("title")),
            "source_id": to_str(source_ids[0] if source_ids else ""),
            "source_ids": to_str(source_ids),  # JSON list as text
            "source_taxonomy": to_str(source_taxonomy),  # JSON list as text
            "domain_family": to_str(rec.get("domain_family")),
            "source_role": to_str(rec.get("source_role")),
            "authority_level": to_str(rec.get("authority_level")),
            "text": text,
            "security_status": "clean",
        })
    cand_vecs = model.encode(
        [r["text"] for r in cand_rows],
        show_progress_bar=False, convert_to_numpy=True, normalize_embeddings=True,
    )
    for r, v in zip(cand_rows, cand_vecs):
        r["vector"] = v.tolist()
        del r["text"]
    db.create_table("candidates", data=cand_rows, mode="overwrite")
    log(f"  candidates table: {len(cand_rows)} rows")

    # ---- n8n workflows ----
    log("  Encoding n8n-workflow units (clean + redacted-safe)...")
    n8n_rows: list[dict] = []
    n8n_skipped_flagged = 0
    n8n_skipped_blocked = 0
    for p in (RUNTIME / "units" / "n8n-workflow").rglob("*.json"):
        try:
            rec = load_json(p)
        except Exception:
            continue
        aid = rec.get("artifact_id")
        sec = art_sec.get(aid) or "not-scanned"
        if sec == "blocked":
            n8n_skipped_blocked += 1
            continue
        if sec == "flagged":
            text = to_str(rec.get("title"))
            n8n_skipped_flagged += 1
        else:
            text = (rec.get("semantic_text") or rec.get("title") or "").strip()
        if not text:
            continue
        sri = rec.get("source_record_ids", [])
        sid = sri[0] if sri else None
        oid = occ_id_for_artifact(aid) or ""
        sp = art_source_path(aid) or ""
        n8n_rows.append({
            "id": f"n8n:{rec.get('record_id')}",
            "kind": "n8n_workflow",
            "record_id": to_str(rec.get("record_id")),
            "title": to_str(rec.get("title")),
            "source_id": to_str(sid),
            "source_path": sp,
            "occurrence_id": oid,
            "artifact_id": to_str(aid),
            "security_status": to_str(sec),
            "text": text,
        })
    log(f"  n8n-workflow rows to encode: {len(n8n_rows)} "
        f"(skipped {n8n_skipped_blocked} blocked, downgraded {n8n_skipped_flagged} flagged)")
    vectors = []
    for i in range(0, len(n8n_rows), EMBED_BATCH):
        batch = n8n_rows[i:i + EMBED_BATCH]
        vecs = model.encode(
            [r["text"] for r in batch],
            show_progress_bar=False, convert_to_numpy=True,
            normalize_embeddings=True, batch_size=EMBED_BATCH,
        )
        vectors.append(vecs)
    if vectors:
        all_vecs = np.concatenate(vectors, axis=0)
    else:
        all_vecs = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    for r, v in zip(n8n_rows, all_vecs):
        r["vector"] = v.tolist()
        del r["text"]
    db.create_table("n8n_workflows", data=n8n_rows, mode="overwrite")
    log(f"  n8n_workflows table: {len(n8n_rows)} rows")

    # ---- skills, souls, operational ----
    log("  Encoding skills, souls, operational units...")
    op_rows: list[dict] = []
    for kind, sub in [
        ("skill", "hermes-skill"),
        ("soul", "hermes-soul"),
        ("configuration", "configuration"),
        ("deployment", "deployment-component"),
        ("script", "script-and-supporting"),
    ]:
        for p in (RUNTIME / "units" / sub).rglob("*.json"):
            try:
                rec = load_json(p)
            except Exception:
                continue
            aid = rec.get("artifact_id")
            sec = art_sec.get(aid) or "not-scanned"
            if sec == "blocked":
                continue
            if sec == "flagged":
                text = to_str(rec.get("title"))
            else:
                text = (rec.get("semantic_text") or rec.get("title") or "").strip()
            if not text:
                continue
            sri = rec.get("source_record_ids", [])
            sid = sri[0] if sri else None
            op_rows.append({
                "id": f"{kind}:{rec.get('record_id')}",
                "kind": to_str(kind),
                "record_id": to_str(rec.get("record_id")),
                "title": to_str(rec.get("title")),
                "source_id": to_str(sid),
                "security_status": to_str(sec),
                "text": text,
            })
    log(f"  op rows to encode: {len(op_rows)}")
    vectors = []
    for i in range(0, len(op_rows), EMBED_BATCH):
        batch = op_rows[i:i + EMBED_BATCH]
        vecs = model.encode(
            [r["text"] for r in batch],
            show_progress_bar=False, convert_to_numpy=True,
            normalize_embeddings=True, batch_size=EMBED_BATCH,
        )
        vectors.append(vecs)
    if vectors:
        all_vecs = np.concatenate(vectors, axis=0)
    else:
        all_vecs = np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    for r, v in zip(op_rows, all_vecs):
        r["vector"] = v.tolist()
        del r["text"]
    db.create_table("operational", data=op_rows, mode="overwrite")
    log(f"  operational table: {len(op_rows)} rows")

    md.close()
    summary = {
        "status": "built",
        "tables": {
            "candidates": len(cand_rows),
            "n8n_workflows": len(n8n_rows),
            "operational": len(op_rows),
        },
        "model": EMBEDDING_MODEL,
        "dim": EMBEDDING_DIM,
        "n8n_skipped_blocked": n8n_skipped_blocked,
        "n8n_skipped_flagged": n8n_skipped_flagged,
    }
    log(f"Vector index summary: {summary}")
    return summary


# ---------- 4. Manifest, log, dependency report ----------

def write_dependency_blocker_report(deps: dict) -> None:
    if not _dependency_blockers:
        return
    lines = ["# Phase 6 — Dependency Blocker Report", ""]
    lines.append("Some optional Python packages are not importable. "
                 "The metadata DB and FTS5 index always build, but the vector index requires these.")
    lines.append("")
    lines.append("| Module | Available | Version | Error | Remediation |")
    lines.append("|---|---|---|---|---|")
    for blocker in _dependency_blockers:
        name = blocker["module"]
        d = deps.get(name, {})
        lines.append(
            f"| {name} | {'no' if not d.get('available') else 'yes'} | "
            f"{d.get('version') or '—'} | {blocker['error'][:80]} | {blocker['remediation']} |"
        )
    lines.append("")
    lines.append("## Impact")
    lines.append("")
    lines.append("- `.runtime/db/codex-vault.db` (SQLite metadata): unaffected")
    lines.append("- `.runtime/indexes/codex-vault-fts.db` (FTS5 search): unaffected")
    lines.append("- `.runtime/indexes/codex-vault-vectors/` (LanceDB vector): **blocked**")
    lines.append("")
    DEPENDENCY_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEPENDENCY_REPORT_PATH.write_text("\n".join(lines))
    log(f"Wrote dependency blocker report: {DEPENDENCY_REPORT_PATH.relative_to(VAULT)}")


def write_manifest(metadata_summary: dict, fts_summary: dict,
                   vector_summary: dict, deps: dict) -> dict:
    manifest = {
        "build_id": f"index-build-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "build_at": datetime.now(timezone.utc).isoformat(),
        "generator": "build_indexes.py",
        "model": EMBEDDING_MODEL,
        "dependencies": deps,
        "dependency_blockers": _dependency_blockers,
        "metadata_db": {
            "path": str(DB_PATH.relative_to(VAULT)),
            "row_counts": metadata_summary,
        },
        "fts_index": {
            "path": str(FTS_PATH.relative_to(VAULT)),
            "row_counts": fts_summary,
        },
        "vector_index": {
            "path": str(VECTORS_PATH.relative_to(VAULT)),
            "summary": vector_summary,
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    log(f"Wrote manifest: {MANIFEST_PATH.relative_to(VAULT)}")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(_log_lines) + "\n")
    log(f"Wrote log: {LOG_PATH.relative_to(VAULT)}")
    return manifest


def main(argv: list | None = None) -> dict:
    import argparse
    if argv is None:
        argv = sys.argv[1:]
    _ap = argparse.ArgumentParser(add_help=True)
    _ap.add_argument("--no-vector", action="store_true", help="Skip LanceDB vector index construction")
    _ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""), help="Path to vault root")
    _args, _rest = _ap.parse_known_args(argv)
    no_vector = _args.no_vector
    t0 = time.time()
    log("=== Phase 6 index build ===")
    deps = detect_dependencies()
    for name, d in deps.items():
        log(f"  dep {name}: available={d.get('available')} version={d.get('version')}")
    md_summary = build_metadata_db()
    fts_summary = build_fts_index(md_summary)
    vec_summary = ({"skipped": True, "reason": "--no-vector"} if no_vector else build_vector_index(deps))
    write_dependency_blocker_report(deps)
    manifest = write_manifest(md_summary, fts_summary, vec_summary, deps)
    log(f"=== Done in {time.time()-t0:.1f}s ===")
    return manifest


if __name__ == "__main__":
    main()
