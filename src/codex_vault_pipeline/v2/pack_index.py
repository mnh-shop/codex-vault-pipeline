"""v2 pack index — SQLite/FTS index over Repomix packs.

Creates and queries a clean v2 index separate from legacy retrieval.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Pack-level metadata
CREATE TABLE IF NOT EXISTS packs (
    pack_id         TEXT PRIMARY KEY,
    source_id       TEXT NOT NULL,
    source_type     TEXT NOT NULL,           -- 'github' | 'local'
    repo_url        TEXT,
    local_path      TEXT,
    commit_or_revision TEXT,
    pack_path       TEXT NOT NULL,
    pack_format     TEXT NOT NULL DEFAULT 'markdown',
    pack_size_bytes INTEGER,
    token_estimate  INTEGER,
    file_count      INTEGER,
    security_status TEXT NOT NULL DEFAULT 'clean',
    quality_verdict TEXT NOT NULL DEFAULT 'PASS',
    recommended_retrieval_use TEXT,
    created_at      TEXT NOT NULL
);

-- Per-file records within each pack
CREATE TABLE IF NOT EXISTS pack_files (
    pack_file_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_id         TEXT NOT NULL REFERENCES packs(pack_id),
    source_id       TEXT NOT NULL,
    path            TEXT NOT NULL,
    file_extension  TEXT,
    artifact_role   TEXT NOT NULL DEFAULT 'unknown',
    is_readme       INTEGER NOT NULL DEFAULT 0,
    is_generated_catalog INTEGER NOT NULL DEFAULT 0,
    is_workflow_json INTEGER NOT NULL DEFAULT 0,
    is_skill_file   INTEGER NOT NULL DEFAULT 0,
    is_soul_file    INTEGER NOT NULL DEFAULT 0,
    is_code_file    INTEGER NOT NULL DEFAULT 0,
    is_docs_file    INTEGER NOT NULL DEFAULT 0,
    is_config_file  INTEGER NOT NULL DEFAULT 0,
    text_hash       TEXT,
    size_chars      INTEGER
);

-- Chunked content for FTS
CREATE TABLE IF NOT EXISTS pack_chunks (
    chunk_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pack_file_id    INTEGER NOT NULL REFERENCES pack_files(pack_file_id),
    pack_id         TEXT NOT NULL REFERENCES packs(pack_id),
    source_id       TEXT NOT NULL,
    path            TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL DEFAULT 0,
    heading_or_symbol TEXT,
    text            TEXT NOT NULL,
    text_hash       TEXT,
    token_estimate  INTEGER,
    artifact_role   TEXT NOT NULL DEFAULT 'unknown',
    priority_class  TEXT NOT NULL DEFAULT 'normal',
    safety_status   TEXT NOT NULL DEFAULT 'clean'
);

-- FTS5 virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS pack_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    source_id,
    path,
    heading_or_symbol,
    text,
    artifact_role,
    priority_class,
    content=pack_chunks,
    content_rowid=chunk_id
);

-- Index run log
CREATE TABLE IF NOT EXISTS pack_index_runs (
    run_id          TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    packs_indexed   INTEGER DEFAULT 0,
    files_indexed   INTEGER DEFAULT 0,
    chunks_indexed  INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0,
    warnings        INTEGER DEFAULT 0
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_packs_source ON packs(source_id);
CREATE INDEX IF NOT EXISTS idx_pack_files_pack ON pack_files(pack_id);
CREATE INDEX IF NOT EXISTS idx_pack_files_source ON pack_files(source_id);
CREATE INDEX IF NOT EXISTS idx_pack_files_role ON pack_files(artifact_role);
CREATE INDEX IF NOT EXISTS idx_pack_chunks_pack ON pack_chunks(pack_id);
CREATE INDEX IF NOT EXISTS idx_pack_chunks_source ON pack_chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_pack_chunks_role ON pack_chunks(artifact_role);
CREATE INDEX IF NOT EXISTS idx_pack_chunks_priority ON pack_chunks(priority_class);
"""

# ---------------------------------------------------------------------------
# Artifact role classification
# ---------------------------------------------------------------------------

README_PATTERNS = re.compile(
    r"(^|/)readme(\.(md|txt|rst))?$", re.IGNORECASE
)
GENERATED_CATALOG_PATTERNS = re.compile(
    r"(^|/)(index\.(md|txt|yaml|json)|catalog\.(md|txt|yaml|json)|"
    r"package_index\.(md|txt)|file_list\.(md|txt))$", re.IGNORECASE
)
WORKFLOW_JSON_PATTERNS = re.compile(
    r"(^|/).*\.json$", re.IGNORECASE
)
SKILL_PATTERNS = re.compile(
    r"(^|/)skill\.md$", re.IGNORECASE
)
SOUL_PATTERNS = re.compile(
    r"(^|/)soul\.md$", re.IGNORECASE
)
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".rb", ".java",
    ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala",
    ".sh", ".bash", ".zsh", ".fish",
}
DOCS_EXTENSIONS = {
    ".md", ".mdx", ".rst", ".txt", ".adoc",
}
CONFIG_EXTENSIONS = {
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".json", ".lock",
}
N8N_WORKFLOW_INDICATORS = [
    '"nodes"', '"connections"', '"active"', '"settings"',
    '"n8n"', '" workflow"',
]


def _classify_artifact_role(path: str, content: str = "") -> str:
    """Classify a file's artifact role based on path and content."""
    path_lower = path.lower()

    # README / generated catalog
    if README_PATTERNS.search(path_lower):
        return "readme"
    if GENERATED_CATALOG_PATTERNS.search(path_lower):
        return "generated_catalog"

    # Skill / Soul
    if SKILL_PATTERNS.search(path_lower):
        return "skill"
    if SOUL_PATTERNS.search(path_lower):
        return "soul"

    # n8n workflow JSON
    if path_lower.endswith(".json") and content:
        try:
            data = json.loads(content[:10000])  # peek at first 10KB
            if isinstance(data, dict):
                has_workflow_keys = sum(
                    1 for k in N8N_WORKFLOW_INDICATORS if k in json.dumps(data)
                )
                if has_workflow_keys >= 2:
                    return "n8n_workflow"
        except (json.JSONDecodeError, ValueError):
            pass

    # Code files
    for ext in CODE_EXTENSIONS:
        if path_lower.endswith(ext):
            return "code"

    # Docs
    for ext in DOCS_EXTENSIONS:
        if path_lower.endswith(ext):
            return "docs"

    # Config
    for ext in CONFIG_EXTENSIONS:
        if path_lower.endswith(ext):
            return "config"

    return "other"


def _priority_from_role(role: str) -> str:
    """Map artifact role to priority class."""
    if role in ("readme", "generated_catalog"):
        return "low"
    if role in ("n8n_workflow", "skill", "soul"):
        return "high"
    return "normal"


def _file_flags(path: str, role: str) -> dict[str, int]:
    """Compute boolean flags for a file path."""
    return {
        "is_readme": 1 if role == "readme" else 0,
        "is_generated_catalog": 1 if role == "generated_catalog" else 0,
        "is_workflow_json": 1 if role == "n8n_workflow" else 0,
        "is_skill_file": 1 if role == "skill" else 0,
        "is_soul_file": 1 if role == "soul" else 0,
        "is_code_file": 1 if role == "code" else 0,
        "is_docs_file": 1 if role == "docs" else 0,
        "is_config_file": 1 if role == "config" else 0,
    }


# ---------------------------------------------------------------------------
# Repomix parser
# ---------------------------------------------------------------------------

# Matches: ## File: path/to/file
FILE_HEADER_RE = re.compile(r"^## File:\s+(.+)$")

# Matches code block open: ```lang or ````lang
CODE_BLOCK_OPEN_RE = re.compile(r"^(`{3,4})(\w*)\s*$")

# Matches code block close: ``` or ````
CODE_BLOCK_CLOSE_RE = re.compile(r"^(`{3,})\s*$")

# Compressed delimiter
COMPRESSED_DELIM = "⋮----"

MAX_CHUNK_CHARS = 8000  # ~2000 tokens


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (chars / 4)."""
    return max(1, len(text) // 4)


def _text_hash(text: str) -> str:
    """SHA-256 of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def parse_repomix_pack(
    pack_path: str,
    source_id: str,
    pack_id: str,
    repo_url: str | None = None,
    local_path: str | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Parse a Repomix output.md into structured file/chunk records.

    Returns dict with keys: pack_meta, files, chunks.
    """
    path = Path(pack_path)
    if not path.exists():
        raise FileNotFoundError(f"Pack not found: {pack_path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.split("\n")

    files: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []

    current_file_path: str | None = None
    current_file_lines: list[str] = []
    in_code_block = False
    code_block_fence = ""

    def _flush_file() -> None:
        nonlocal current_file_path, current_file_lines
        if current_file_path is None:
            return
        content = "\n".join(current_file_lines)
        role = _classify_artifact_role(current_file_path, content)
        flags = _file_flags(current_file_path, role)
        ext = Path(current_file_path).suffix or ""
        files.append({
            "path": current_file_path,
            "file_extension": ext,
            "artifact_role": role,
            "text_hash": _text_hash(content),
            "size_chars": len(content),
            **flags,
        })

        # Chunk the file content
        _chunk_file_content(
            content, current_file_path, pack_id, source_id, role, chunks
        )

        current_file_path = None
        current_file_lines = []

    for line in lines:
        # Detect file header
        m = FILE_HEADER_RE.match(line)
        if m:
            _flush_file()
            current_file_path = m.group(1).strip()
            in_code_block = False
            continue

        if current_file_path is None:
            continue

        # Track code block state
        if not in_code_block:
            m_open = CODE_BLOCK_OPEN_RE.match(line)
            if m_open:
                in_code_block = True
                code_block_fence = m_open.group(1)
                continue
        else:
            m_close = CODE_BLOCK_CLOSE_RE.match(line)
            if m_close and len(m_close.group(1)) >= len(code_block_fence):
                in_code_block = False
                continue

        current_file_lines.append(line)

    _flush_file()

    pack_meta = {
        "pack_id": pack_id,
        "source_id": source_id,
        "source_type": "github" if source_id.startswith("github:") else "local",
        "repo_url": repo_url,
        "local_path": local_path,
        "commit_or_revision": revision,
        "pack_path": str(path),
        "pack_format": "markdown",
        "pack_size_bytes": path.stat().st_size,
        "token_estimate": None,
        "file_count": len(files),
        "security_status": "clean",
        "quality_verdict": "PASS",
        "recommended_retrieval_use": _infer_retrieval_use(source_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return {"pack_meta": pack_meta, "files": files, "chunks": chunks}


def _infer_retrieval_use(source_id: str) -> str:
    """Infer recommended retrieval use from source_id."""
    sid = source_id.lower()
    if "n8n-workflow" in sid or "n8n_workflows" in sid:
        return "workflow context"
    if "n8n" in sid and "doc" in sid:
        return "docs context"
    return "code context"


def _chunk_file_content(
    content: str,
    file_path: str,
    pack_id: str,
    source_id: str,
    role: str,
    chunks: list[dict[str, Any]],
) -> None:
    """Chunk file content into indexed units."""
    if not content.strip():
        return

    priority = _priority_from_role(role)

    # For small files, create a single chunk
    if len(content) <= MAX_CHUNK_CHARS:
        chunks.append({
            "pack_file_id": None,  # filled during insert
            "pack_id": pack_id,
            "source_id": source_id,
            "path": file_path,
            "chunk_index": 0,
            "heading_or_symbol": None,
            "text": content,
            "text_hash": _text_hash(content),
            "token_estimate": _estimate_tokens(content),
            "artifact_role": role,
            "priority_class": priority,
            "safety_status": "clean",
        })
        return

    # Split into sections by headings or by size
    sections = _split_into_sections(content)

    chunk_index = 0
    for heading, section_text in sections:
        # Further split if too large
        sub_chunks = _split_by_size(section_text, MAX_CHUNK_CHARS)
        for sub_text in sub_chunks:
            if not sub_text.strip():
                continue
            chunks.append({
                "pack_file_id": None,  # filled during insert
                "pack_id": pack_id,
                "source_id": source_id,
                "path": file_path,
                "chunk_index": chunk_index,
                "heading_or_symbol": heading,
                "text": sub_text,
                "text_hash": _text_hash(sub_text),
                "token_estimate": _estimate_tokens(sub_text),
                "artifact_role": role,
                "priority_class": priority,
                "safety_status": "clean",
            })
            chunk_index += 1


def _split_into_sections(content: str) -> list[tuple[str | None, str]]:
    """Split content into (heading, text) sections by markdown headings."""
    lines = content.split("\n")
    sections: list[tuple[str | None, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if line.startswith("#"):
            if current_lines:
                sections.append((current_heading, "\n".join(current_lines)))
            current_heading = line.lstrip("#").strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_heading, "\n".join(current_lines)))

    return sections if sections else [(None, content)]


def _split_by_size(text: str, max_chars: int) -> list[str]:
    """Split text into chunks of at most max_chars."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    lines = text.split("\n")
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def get_db(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) the v2 pack index database."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL)
    return conn


def index_pack(conn: sqlite3.Connection, parsed: dict[str, Any]) -> dict[str, Any]:
    """Index a parsed pack into the database.

    Returns stats dict.
    """
    meta = parsed["pack_meta"]
    files = parsed["files"]
    chunks = parsed["chunks"]

    stats = {
        "pack_id": meta["pack_id"],
        "files_indexed": 0,
        "files_skipped": 0,
        "chunks_indexed": 0,
        "warnings": 0,
        "errors": 0,
    }

    # Upsert pack
    conn.execute(
        """INSERT OR REPLACE INTO packs
           (pack_id, source_id, source_type, repo_url, local_path,
            commit_or_revision, pack_path, pack_format, pack_size_bytes,
            token_estimate, file_count, security_status, quality_verdict,
            recommended_retrieval_use, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            meta["pack_id"], meta["source_id"], meta["source_type"],
            meta["repo_url"], meta["local_path"], meta["commit_or_revision"],
            meta["pack_path"], meta["pack_format"], meta["pack_size_bytes"],
            meta["token_estimate"], meta["file_count"],
            meta["security_status"], meta["quality_verdict"],
            meta["recommended_retrieval_use"], meta["created_at"],
        ),
    )

    # Index files
    for f in files:
        try:
            cur = conn.execute(
                """INSERT INTO pack_files
                   (pack_id, source_id, path, file_extension, artifact_role,
                    is_readme, is_generated_catalog, is_workflow_json,
                    is_skill_file, is_soul_file, is_code_file,
                    is_docs_file, is_config_file, text_hash, size_chars)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    meta["pack_id"], meta["source_id"], f["path"],
                    f["file_extension"], f["artifact_role"],
                    f["is_readme"], f["is_generated_catalog"],
                    f["is_workflow_json"], f["is_skill_file"],
                    f["is_soul_file"], f["is_code_file"],
                    f["is_docs_file"], f["is_config_file"],
                    f["text_hash"], f["size_chars"],
                ),
            )
            pack_file_id = cur.lastrowid
            stats["files_indexed"] += 1

            # Index chunks for this file
            for c in chunks:
                if c["path"] != f["path"]:
                    continue
                try:
                    conn.execute(
                        """INSERT INTO pack_chunks
                           (pack_file_id, pack_id, source_id, path,
                            chunk_index, heading_or_symbol, text, text_hash,
                            token_estimate, artifact_role, priority_class,
                            safety_status)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            pack_file_id, c["pack_id"], c["source_id"],
                            c["path"], c["chunk_index"],
                            c["heading_or_symbol"], c["text"],
                            c["text_hash"], c["token_estimate"],
                            c["artifact_role"], c["priority_class"],
                            c["safety_status"],
                        ),
                    )
                    stats["chunks_indexed"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    stats["warnings"] += 1

        except Exception as e:
            stats["errors"] += 1

    conn.commit()
    return stats


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS5 index from pack_chunks."""
    conn.execute("INSERT INTO pack_chunks_fts(pack_chunks_fts) VALUES('rebuild')")
    conn.commit()


def search_fts(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 5,
    source_id: str | None = None,
) -> list[dict[str, Any]]:
    """Search the v2 FTS index."""
    # Build FTS5 query: use individual words with OR for better recall
    # This handles multi-word queries better than strict AND
    words = query.split()
    if not words:
        return []

    # Escape special FTS5 characters and join with OR
    safe_words = []
    for w in words:
        # Remove FTS5 special chars but keep dots for terms like SOUL.md
        w_clean = w.replace('"', '').replace("'", "").replace('*', '').replace('?', '').replace(':', '')
        # Skip very short words (stop words)
        if len(w_clean) > 2:
            safe_words.append(f'"{w_clean}"')

    if not safe_words:
        return []

    # Use OR for better recall
    safe_query = " OR ".join(safe_words)

    sql = """
        SELECT
            f.chunk_id, f.source_id, f.path, f.heading_or_symbol,
            f.artifact_role, f.priority_class,
            snippet(pack_chunks_fts, 4, '<b>', '</b>', '...', 64) as snippet,
            rank
        FROM pack_chunks_fts f
        WHERE pack_chunks_fts MATCH ?
    """
    params: list[Any] = [safe_query]

    if source_id:
        sql += " AND f.source_id = ?"
        params.append(source_id)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    results = []
    for row in rows:
        results.append({
            "chunk_id": row[0],
            "source_id": row[1],
            "path": row[2],
            "heading_or_symbol": row[3],
            "artifact_role": row[4],
            "priority_class": row[5],
            "snippet": row[6],
            "rank": row[7],
        })
    return results


def get_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Get index statistics."""
    stats: dict[str, Any] = {}

    # Pack counts
    row = conn.execute("SELECT COUNT(*) FROM packs").fetchone()
    stats["total_packs"] = row[0]

    row = conn.execute(
        "SELECT source_id, COUNT(*) FROM packs GROUP BY source_id"
    ).fetchall()
    stats["packs_by_source"] = {r[0]: r[1] for r in row}

    # File counts
    row = conn.execute("SELECT COUNT(*) FROM pack_files").fetchone()
    stats["total_files"] = row[0]

    row = conn.execute(
        "SELECT source_id, COUNT(*) FROM pack_files GROUP BY source_id"
    ).fetchall()
    stats["files_by_source"] = {r[0]: r[1] for r in row}

    row = conn.execute(
        "SELECT artifact_role, COUNT(*) FROM pack_files GROUP BY artifact_role"
    ).fetchall()
    stats["files_by_role"] = {r[0]: r[1] for r in row}

    # Chunk counts
    row = conn.execute("SELECT COUNT(*) FROM pack_chunks").fetchone()
    stats["total_chunks"] = row[0]

    row = conn.execute(
        "SELECT source_id, COUNT(*) FROM pack_chunks GROUP BY source_id"
    ).fetchall()
    stats["chunks_by_source"] = {r[0]: r[1] for r in row}

    row = conn.execute(
        "SELECT artifact_role, COUNT(*) FROM pack_chunks GROUP BY artifact_role"
    ).fetchall()
    stats["chunks_by_role"] = {r[0]: r[1] for r in row}

    row = conn.execute(
        "SELECT priority_class, COUNT(*) FROM pack_chunks GROUP BY priority_class"
    ).fetchall()
    stats["chunks_by_priority"] = {r[0]: r[1] for r in row}

    # FTS row count
    row = conn.execute(
        "SELECT COUNT(*) FROM pack_chunks_fts"
    ).fetchone()
    stats["fts_rows"] = row[0]

    return stats
