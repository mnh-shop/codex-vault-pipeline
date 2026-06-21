#!/usr/bin/env python3
"""
retrieval.py — deterministic Phase 6 retrieval CLI.

Usage:
  python3 retrieval.py search --query "..." --k 10 --mode fts
  python3 retrieval.py search --query "..." --k 10 --mode vector
  python3 retrieval.py search --query "..." --k 10 --mode hybrid
  python3 retrieval.py by-source --source "github:Zie619/n8n-workflows" --k 20
  python3 retrieval.py by-candidate --slug "n8n-workflow-search-guide"
  python3 retrieval.py provenance --row-id "cand:foo"

Each result row preserves: source_id, source_path, occurrence_id,
artifact_id, candidate slug, security_status, retriever, raw_score.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import yaml
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
DB_PATH = RUNTIME / "db" / "codex-vault.db"
FTS_PATH = RUNTIME / "indexes" / "codex-vault-fts.db"
VECTORS_PATH = RUNTIME / "indexes" / "codex-vault-vectors"

# ---------- helpers ----------

def open_metadata():
    return sqlite3.connect(str(DB_PATH))

def open_fts():
    return sqlite3.connect(str(FTS_PATH))

def open_vectors():
    try:
        import lancedb
        return lancedb.connect(str(VECTORS_PATH)), None
    except Exception as e:
        return None, str(e)

def get_model():
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        return model, None
    except Exception as e:
        return None, str(e)

# ---------- FTS search ----------

def fts_search(conn, query, k, table, *, fields):
    """Search an FTS5 table. `fields` is list of column names to return."""
    # FTS5 query: sanitize
    fts_query = query.replace('"', '""')
    sql = f"SELECT {', '.join(fields)} FROM {table} WHERE {table} MATCH ? LIMIT ?"
    cur = conn.execute(sql, (fts_query, k))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

# ---------- Vector search ----------

def vector_search(db, model, query, k, table):
    """LanceDB vector search. Returns list of result dicts."""
    if db is None or model is None or table is None:
        return []
    try:
        tbl = db.open_table(table)
        import numpy as np
        vec = model.encode([query], show_progress_bar=False, convert_to_numpy=True, normalize_embeddings=True)
        results = tbl.search(vec[0].tolist()).limit(k).to_list()
        return results
    except Exception as e:
        return [{"error": str(e)}]

# ---------- Hybrid (RRF) ----------

def hybrid_search(fts_results, vec_results, k):
    """Reciprocal Rank Fusion. Inputs: list of (result, score) tuples."""
    scores = {}
    metadata = {}
    # FTS contribution
    for rank, (r, _) in enumerate(fts_results, start=1):
        rid = r.get("slug") or r.get("record_id") or r.get("unit_id") or r.get("workflow_id") or r.get("id")
        if rid is None:
            continue
        scores[rid] = scores.get(rid, 0) + 1.0 / (60 + rank)
        metadata[rid] = r
    # Vector contribution
    for rank, (r, _) in enumerate(vec_results, start=1):
        rid = r.get("id") or r.get("slug") or r.get("record_id")
        if rid is None:
            continue
        scores[rid] = scores.get(rid, 0) + 1.0 / (60 + rank)
        if rid not in metadata:
            metadata[rid] = r
    # Sort by score
    sorted_ids = sorted(scores.keys(), key=lambda x: -scores[x])
    out = []
    for rid in sorted_ids[:k]:
        r = dict(metadata[rid])
        r["fused_score"] = scores[rid]
        out.append(r)
    return out

# ---------- Subcommands ----------

def cmd_search(args):
    """Search across indexes."""
    query = args.query
    k = args.k
    mode = args.mode

    results = {"query": query, "k": k, "mode": mode, "rows": []}

    fts_conn = open_fts()
    md = open_metadata()

    if mode in ("fts", "hybrid"):
        # Decide which FTS tables to query based on the kind
        # For now, search candidates and n8n_workflows
        fts_candidates = fts_search(fts_conn, query, k, "candidate_fts",
                                    fields=["slug", "title", "summary", "body", "domain_family", "source_role", "authority_level"])
        for r in fts_candidates:
            r["retriever"] = "fts5:candidate_fts"
            r["row_kind"] = "candidate"
            results["rows"].append(r)
        fts_n8n = fts_search(fts_conn, query, k, "n8n_workflow_fts",
                             fields=["workflow_id", "workflow_name", "description", "source_id", "source_path", "occurrence_id", "artifact_id", "security_status"])
        for r in fts_n8n:
            r["retriever"] = "fts5:n8n_workflow_fts"
            r["row_kind"] = "n8n_workflow"
            results["rows"].append(r)
        fts_skill = fts_search(fts_conn, query, k, "skill_fts",
                              fields=["record_id", "kind", "title", "source_id"])
        for r in fts_skill:
            r["retriever"] = "fts5:skill_fts"
            r["row_kind"] = "skill"
            results["rows"].append(r)
        fts_op = fts_search(fts_conn, query, k, "operational_fts",
                            fields=["record_id", "kind", "title", "source_id"])
        for r in fts_op:
            r["retriever"] = "fts5:operational_fts"
            r["row_kind"] = "operational"
            results["rows"].append(r)

    if mode in ("vector", "hybrid"):
        db, err = open_vectors()
        if err:
            results["vector_error"] = err
        model, mdl_err = get_model()
        if mdl_err:
            results["model_error"] = mdl_err
        if db is not None and model is not None:
            for tname, kind in [("candidates", "candidate"), ("n8n_workflows", "n8n_workflow"), ("operational", "operational")]:
                try:
                    tbl = db.open_table(tname)
                    import numpy as np
                    vec = model.encode([query], show_progress_bar=False, convert_to_numpy=True, normalize_embeddings=True)
                    raw = tbl.search(vec[0].tolist()).limit(k).to_list()
                    # LanceDB returns _distance; convert to similarity
                    for r in raw:
                        d = r.pop("_distance", None)
                        r["raw_score"] = 1.0 - d if d is not None else None
                        r["retriever"] = f"lancedb:{tname}"
                        r["row_kind"] = kind
                        results["rows"].append(r)
                except Exception as e:
                    results["vector_table_error"] = str(e)

    # Trim to k overall
    results["rows"] = results["rows"][:k]
    md.close()
    fts_conn.close()
    print(json.dumps(results, indent=2, default=str))


def cmd_by_source(args):
    """Find all candidate slugs that reference a given source."""
    md = open_metadata()
    cur = md.execute(
        "SELECT candidate_slug, source_id, artifact_id, unit_id, occurrence_id, anchor, relation FROM evidence_links WHERE source_id = ?",
        (args.source,),
    )
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    result = {"source_id": args.source, "k": args.k, "rows": [dict(zip(cols, r)) for r in rows[:args.k]]}
    # Also count candidates
    cur = md.execute(
        "SELECT COUNT(DISTINCT candidate_slug) FROM evidence_links WHERE source_id = ?",
        (args.source,),
    )
    result["distinct_candidates"] = cur.fetchone()[0]
    cur = md.execute(
        "SELECT COUNT(*) FROM occurrences WHERE source_id = ?",
        (args.source,),
    )
    result["occurrences"] = cur.fetchone()[0]
    cur = md.execute(
        "SELECT coverage_status FROM source_coverage WHERE source_id = ?",
        (args.source,),
    )
    row = cur.fetchone()
    result["coverage_status"] = row[0] if row else None
    md.close()
    print(json.dumps(result, indent=2))


def cmd_by_candidate(args):
    """Get a candidate by slug with full evidence chain."""
    md = open_metadata()
    cur = md.execute("SELECT * FROM candidates WHERE slug = ?", (args.slug,))
    row = cur.fetchone()
    if not row:
        print(json.dumps({"error": "candidate not found", "slug": args.slug}))
        return
    cols = [d[0] for d in cur.description]
    cand = dict(zip(cols, row))
    # Evidence links
    cur = md.execute("SELECT * FROM evidence_links WHERE candidate_slug = ?", (args.slug,))
    cols = [d[0] for d in cur.description]
    cand["evidence_links"] = [dict(zip(cols, r)) for r in cur.fetchall()]
    # Migration report
    cur = md.execute("SELECT * FROM migration_reports WHERE candidate_slug = ?", (args.slug,))
    row = cur.fetchone()
    if row:
        cols = [d[0] for d in cur.description]
        cand["migration_report"] = dict(zip(cols, row))
    md.close()
    print(json.dumps(cand, indent=2))


def cmd_provenance(args):
    """Look up a row in metadata DB by its primary key."""
    md = open_metadata()
    rid = args.row_id
    # Try candidates
    if rid.startswith("cand:"):
        slug = rid.split(":", 1)[1]
        cur = md.execute("SELECT * FROM candidates WHERE slug = ?", (slug,))
    elif rid.startswith("art:"):
        cur = md.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (rid.split(":",1)[1],))
    elif rid.startswith("occ:"):
        cur = md.execute("SELECT * FROM occurrences WHERE occurrence_id = ?", (rid.split(":",1)[1],))
    elif rid.startswith("src:"):
        cur = md.execute("SELECT * FROM sources WHERE source_id = ?", (rid.split(":",1)[1],))
    elif rid.startswith("unit:"):
        cur = md.execute("SELECT * FROM units WHERE unit_id = ?", (rid.split(":",1)[1],))
    else:
        cur = md.execute("SELECT * FROM candidates WHERE slug = ?", (rid,))
    row = cur.fetchone()
    if not row:
        print(json.dumps({"error": "row not found", "row_id": rid}))
        return
    cols = [d[0] for d in cur.description]
    print(json.dumps(dict(zip(cols, row)), indent=2))
    md.close()


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search")
    sp.add_argument("--query", required=True)
    sp.add_argument("--k", type=int, default=10)
    sp.add_argument("--mode", choices=["fts", "vector", "hybrid"], default="fts")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("by-source")
    sp.add_argument("--source", required=True)
    sp.add_argument("--k", type=int, default=20)
    sp.set_defaults(func=cmd_by_source)

    sp = sub.add_parser("by-candidate")
    sp.add_argument("--slug", required=True)
    sp.set_defaults(func=cmd_by_candidate)

    sp = sub.add_parser("provenance")
    sp.add_argument("--row-id", required=True)
    sp.set_defaults(func=cmd_provenance)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
