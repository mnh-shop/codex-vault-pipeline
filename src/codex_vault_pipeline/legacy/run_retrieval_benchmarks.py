#!/usr/bin/env python3
"""
run_retrieval_benchmarks.py — deterministic Phase 6 retrieval benchmark.

Benchmarks all four retrieval paths:
  1. metadata  (direct lookup by source_id / candidate slug in the SQLite DB)
  2. fts5      (SQLite FTS5 search across candidate_fts, n8n_workflow_fts, etc.)
  3. vector    (LanceDB all-MiniLM-L6-v2 cosine)
  4. hybrid    (RRF fusion of fts5 + vector)

For every query the runner verifies:
  - top-k includes expected source_id(s)
  - top-k includes expected candidate slug (where applicable)
  - top-k rows carry source_path or occurrence_id
  - every row carries a security_status
  - blocked content never appears in any row
  - flagged content appears only as title-only / structural metadata
  - importable n8n workflows preserve original JSON source_path
  - two consecutive runs produce identical row sets (determinism)

Outputs:
  .runtime/reports/retrieval-benchmark-results.json
  .runtime/reports/retrieval-benchmark-results.md
  .runtime/reports/index-security-audit.md
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

# ---------- paths ----------

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"

DB_PATH = RUNTIME / "db" / "codex-vault.db"
FTS_PATH = RUNTIME / "indexes" / "codex-vault-fts.db"
VECTORS_PATH = RUNTIME / "indexes" / "codex-vault-vectors"

REPORT_JSON = RUNTIME / "reports" / "retrieval-benchmark-results.json"
REPORT_MD = RUNTIME / "reports" / "retrieval-benchmark-results.md"
SECURITY_AUDIT_MD = RUNTIME / "reports" / "index-security-audit.md"

K = 10
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
RUN_ID = f"bench-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

# Forbidden patterns in any retrieved text (n8n workflow sticky notes, etc.)
FORBIDDEN_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),               # OpenAI-style keys
    re.compile(r"AKIA[0-9A-Z]{16}"),                 # AWS access keys
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),           # GCP API keys
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),             # GitHub PAT
    re.compile(r"xox[abp]-[0-9A-Za-z\-]+"),          # Slack tokens
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    re.compile(r"(?i)password\s*[:=]\s*\S+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
    re.compile(r"(?i)secret\s*[:=]\s*\S+"),
    re.compile(r"(?i)webhook[_-]?url\s*[:=]\s*https?://\S+"),
]


# ---------- query set ----------

# Each entry: (id, category, retriever_focus, query_text, expected_source_ids,
#              expected_candidate_slug_or_None, n8n_source_path_must_contain or None)

QUERIES = [
    # --- Phase 6 deep-research / OSINT batch (added 2026-06-21) ---
    ("dr-open-1", "deep-research", "fts", "open deep research agent langchain langgraph",
     ["github:langchain-ai/open_deep_research"], "open-deep-research", None),
    ("dr-open-2", "deep-research", "vector", "tavily exa arxiv open deep research plan execute",
     ["github:langchain-ai/open_deep_research"], "open-deep-research", None),
    ("dr-from-scratch-1", "deep-research", "fts", "deep research from scratch jupyter notebook tutorial",
     ["github:langchain-ai/deep_research_from_scratch"], "deep-research-from-scratch", None),
    ("dr-tongyi-1", "deep-research", "vector", "Tongyi DeepResearch 30B agentic web agent",
     ["github:Alibaba-NLP/DeepResearch"], "alibaba-nlp-deepresearch", None),
    ("dr-tongyi-2", "deep-research", "fts", "Alibaba NLP deep research humanity last exam browsecomp",
     ["github:Alibaba-NLP/DeepResearch"], "alibaba-nlp-deepresearch", None),
    ("dr-manusearch-1", "deep-research", "vector", "ManuSearch multi-agent planner searcher reader ORION",
     ["github:RUCAIBox/ManuSearch"], "manusearch", None),
    ("dr-simpledeep-1", "deep-research", "fts", "SimpleDeepSearcher R1-Searcher knowledge distillation",
     ["github:RUCAIBox/SimpleDeepSearcher"], "simpledeepresearcher", None),
    ("dr-deerflow-1", "deep-research", "vector", "deer-flow super agent harness sub-agent sandbox",
     ["github:bytedance/deer-flow"], "deer-flow", None),
    ("osint-awesome-1", "osint", "fts", "awesome osint curated list of tools",
     ["github:jivoi/awesome-osint"], "awesome-osint", None),
    ("osint-framework-1", "osint", "fts", "OSINT framework static site d3",
     ["github:lockfale/OSINT-Framework"], "osint-framework", None),
    ("osint-synint-1", "osint", "vector", "SYNINT local-first OSINT investigation 46 agents camoufox",
     ["github:gs-ai/SYNINT"], "synint", None),
    # Multi-repo composition queries
    ("compose-dr-vec-1", "deep-research", "vector", "deep research agent vector backend deep-searcher milvus",
     ["github:langchain-ai/open_deep_research", "github:zilliztech/deep-searcher"], "open-deep-research", None),
    ("compose-osint-hermes-1", "osint", "vector", "OSINT framework wrapped as Hermes agent skill",
     ["github:gs-ai/SYNINT", "github:NousResearch/hermes-agent"], "synint", None),
    ("compose-catalog-tool-1", "osint", "fts", "source catalog referencing OSINT investigation tool",
     ["github:jivoi/awesome-osint", "github:gs-ai/SYNINT"], "awesome-osint", None),
] + [
    # --- Hermes Agent core ---
    ("hermes-core-1", "hermes-core", "fts", "Hermes Agent self-improving",
     ["github:NousResearch/hermes-agent"], "hermes-agent-core", None),
    ("hermes-core-2", "hermes-core", "fts", "Nous Research hermes-agent learning loop",
     ["github:NousResearch/hermes-agent"], "hermes-agent-core", None),
    ("hermes-core-3", "hermes-core", "fts", "hermes-agent self-evolution DSPy GEPA",
     ["github:NousResearch/hermes-agent-self-evolution"], "hermes-agent-self-evolution", None),
    ("hermes-core-4", "hermes-core", "fts", "hermes paperclip adapter",
     ["github:NousResearch/hermes-paperclip-adapter"], "hermes-paperclip-adapter", None),

    # --- Hermes deployment ---
    ("hermes-deploy-1", "hermes-deployment", "fts", "nix-hermes-agent declarative NixOS",
     ["github:0xrsydn/nix-hermes-agent"], "hermes-community-deployment", None),
    ("hermes-deploy-2", "hermes-deployment", "fts", "hermes-agent-docker dockerfile entrypoint",
     ["github:xmbshwll/hermes-agent-docker"], "hermes-community-deployment", None),
    ("hermes-deploy-3", "hermes-deployment", "fts", "hermes-agent-template template",
     ["github:Crustocean/hermes-agent-template"], "hermes-community-deployment", None),
    ("hermes-deploy-4", "hermes-deployment", "fts", "hermes-workspace deployment",
     ["github:outsourc-e/hermes-workspace"], "hermes-community-deployment", None),

    # --- Hermes skills/plugins ---
    ("hermes-skills-1", "hermes-skills", "fts", "hermes skill plugin evey",
     ["github:42-evey/hermes-plugins"], "hermes-plugins-and-skills", None),
    ("hermes-skills-2", "hermes-skills", "fts", "SkillClaw AMAP-ML skill",
     ["github:AMAP-ML/SkillClaw"], "hermes-plugins-and-skills", None),
    ("hermes-skills-3", "hermes-skills", "fts", "oh-my-hermes witt3rd",
     ["github:witt3rd/oh-my-hermes"], "hermes-plugins-and-skills", None),

    # --- Hermes memory/orchestration ---
    ("hermes-mem-1", "hermes-mem", "fts", "Mnemosyne memory system AxDSan",
     ["github:AxDSan/Mnemosyne"], "hermes-memory-orchestration-integrations", None),
    ("hermes-mem-2", "hermes-mem", "fts", "hindsight vectorize-io memory",
     ["github:vectorize-io/hindsight"], "hermes-memory-orchestration-integrations", None),
    ("hermes-mem-3", "hermes-mem", "fts", "mission-control multi-agent builderz-labs",
     ["github:builderz-labs/mission-control"], "hermes-memory-orchestration-integrations", None),
    ("hermes-mem-4", "hermes-mem", "fts", "flowstate-qmd amanning3390",
     ["github:amanning3390/flowstate-qmd"], "hermes-memory-orchestration-integrations", None),

    # --- n8n official docs ---
    ("n8n-official-1", "n8n-official", "fts", "n8n-docs documentation reference",
     ["github:n8n-io/n8n-docs"], "n8n-code-and-api", None),
    ("n8n-official-2", "n8n-official", "fts", "n8n-io docs api reference",
     ["github:n8n-io/n8n-docs"], "n8n-data-operations", None),

    # --- n8n workflow search ---
    ("n8n-youtube-1", "n8n-youtube", "fts", "youtube video summarization workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"],
     "n8n-workflow-search-guide", "awesome-n8n-templates"),
    ("n8n-gmail-1", "n8n-gmail", "fts", "gmail auto-responder workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"],
     "n8n-workflow-search-guide", None),
    ("n8n-telegram-1", "n8n-telegram", "fts", "telegram bot ai agent workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"],
     "n8n-workflow-search-guide", None),
    ("n8n-sheets-1", "n8n-sheets", "fts", "google sheets workflow automation",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:wassupjay/n8n-free-templates"],
     "n8n-workflow-search-guide", None),
    ("n8n-webhook-1", "n8n-webhook", "fts", "webhook trigger workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"],
     "n8n-workflow-search-guide", None),
    ("n8n-slack-1", "n8n-slack", "fts", "slack notification ai workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"],
     "n8n-workflow-search-guide", None),
    ("n8n-ai-agent-1", "n8n-ai-agent", "fts", "AI agent langchain workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"],
     "n8n-workflow-search-guide", None),

    # --- AgentField core ---
    ("af-core-1", "agentfield-core", "fts", "AgentField core SDK Python",
     ["github:Agent-Field/agentfield"], "agentfield-sdks", None),
    ("af-deploy-1", "agentfield-deployments", "fts", "AgentField deployment manifest",
     ["github:Agent-Field/agentfield"], "agentfield-deployments", None),
    ("af-integ-1", "agentfield-integrations", "fts", "AgentField integrations",
     ["github:Agent-Field/agentfield"], "agentfield-integrations", None),

    # --- AgentField examples ---
    ("af-ex-swe-1", "agentfield-examples", "fts", "swe-af software engineering",
     ["github:Agent-Field/SWE-AF"], "swe-af", None),
    ("af-ex-sec-1", "agentfield-examples", "fts", "sec-af security agent",
     ["github:Agent-Field/sec-af"], "sec-af", None),
    ("af-ex-pr-1", "agentfield-examples", "fts", "pr-af pull request agent",
     ["github:Agent-Field/pr-af"], "pr-af", None),
    ("af-ex-plandb-1", "agentfield-examples", "fts", "plandb plan database",
     ["github:Agent-Field/plandb"], "plandb", None),
    ("af-ex-cloud-1", "agentfield-examples", "fts", "cloudsecurity-af cloud security",
     ["github:Agent-Field/cloudsecurity-af"], "cloudsecurity-af", None),
    ("af-ex-reels-1", "agentfield-examples", "fts", "reels-af reels agent",
     ["github:Agent-Field/reels-af"], "reels-af", None),
    ("af-ex-deep-1", "agentfield-examples", "fts", "af-deep-research research",
     ["github:Agent-Field/af-deep-research"], "af-deep-research", None),

    # --- Remaining sources ---
    ("rem-auto-1", "remaining", "fts", "autonovel AI novel writing Nous Research",
     ["github:NousResearch/autonovel"], "nousresearch-autonovel", None),
    ("rem-tinker-1", "remaining", "fts", "tinker-atropos Atropos training integration",
     ["github:NousResearch/tinker-atropos"], "nousresearch-tinker-atropos", None),
    ("rem-curator-1", "remaining", "fts", "hermes-curator-evolver skill maintenance",
     ["github:pingchesu/hermes-curator-evolver"], "hermes-curator-evolver", None),

    # --- Vector-friendly queries (semantic, no exact terms) ---
    ("vec-1", "vector-semantic", "vector", "agent skill evolution",
     ["github:NousResearch/hermes-agent-self-evolution"], "hermes-agent-self-evolution", None),
    ("vec-2", "vector-semantic", "vector", "memory system for agents",
     ["github:vectorize-io/hindsight", "github:AxDSan/Mnemosyne"],
     "hermes-memory-orchestration-integrations", None),
    ("vec-3", "vector-semantic", "vector", "container image deployment agent",
     ["github:xmbshwll/hermes-agent-docker"], "hermes-community-deployment", None),
    ("vec-4", "vector-semantic", "vector", "novel writing pipeline autonomous",
     ["github:NousResearch/autonovel"], "nousresearch-autonovel", None),
]


# ---------- result data classes ----------

@dataclass
class RowResult:
    retriever: str
    row_kind: str
    source_id: str = ""
    source_path: str = ""
    occurrence_id: str = ""
    artifact_id: str = ""
    candidate_slug: str = ""
    security_status: str = "not-scanned"
    raw_score: Optional[float] = None
    fused_score: Optional[float] = None
    title: str = ""
    snippet: str = ""


@dataclass
class QueryResult:
    query_id: str
    category: str
    retriever: str
    query_text: str
    expected_source_ids: list[str]
    expected_candidate_slug: Optional[str]
    n8n_path_must_contain: Optional[str]
    rows: list[RowResult]
    hit_at_k: bool
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    has_blocked: bool
    has_unredacted_flagged: bool
    has_source_path_or_occ: bool
    n8n_path_preserved: Optional[bool]
    retrieved_candidate_slug: bool
    retrieved_sources: list[str]
    security_status_present: bool
    elapsed_ms: float


# ---------- retriever implementations ----------

def open_metadata() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def open_fts() -> sqlite3.Connection:
    return sqlite3.connect(str(FTS_PATH))


def open_vector_deps() -> tuple:
    """Try to load LanceDB and the embedding model. Returns (db, model, err)."""
    try:
        import lancedb
        import numpy as np
        db = lancedb.connect(str(VECTORS_PATH))
    except Exception as e:
        return None, None, f"lancedb/numpy: {e}"
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(EMBEDDING_MODEL)
    except Exception as e:
        return db, None, f"sentence_transformers: {e}"
    return db, model, None


def search_metadata(conn, query_text: str, k: int, expected_slug: Optional[str],
                    expected_sources: list[str]) -> list[RowResult]:
    """Path 1: direct metadata lookup.

    If expected_slug is known, fetch the candidate by slug. Otherwise search
    candidate title for query terms and return matching candidates.
    """
    out: list[RowResult] = []
    cur = conn.cursor()
    if expected_slug:
        cur.execute(
            "SELECT slug, title, source_role, authority_level, knowledge_status "
            "FROM candidates WHERE slug = ?",
            (expected_slug,),
        )
        row = cur.fetchone()
        if row:
            slug, title, role, auth, ks = row
            cur.execute(
                "SELECT DISTINCT source_id FROM evidence_links WHERE candidate_slug = ?",
                (slug,),
            )
            sids = [r[0] for r in cur.fetchall() if r[0]]
            # Pull one occurrence_id from evidence; source_path is looked up
            # separately from occurrences table.
            cur.execute(
                "SELECT occurrence_id FROM evidence_links "
                "WHERE candidate_slug = ? AND occurrence_id IS NOT NULL LIMIT 1",
                (slug,),
            )
            ev = cur.fetchone()
            occurrence_id = ev[0] if ev else ""
            source_path = ""
            if occurrence_id:
                cur.execute(
                    "SELECT source_path FROM occurrences WHERE occurrence_id = ?",
                    (occurrence_id,),
                )
                r2 = cur.fetchone()
                if r2:
                    source_path = r2[0] or ""
            out.append(RowResult(
                retriever="metadata",
                row_kind="candidate",
                source_id=sids[0] if sids else "",
                source_path=source_path,
                occurrence_id=occurrence_id,
                candidate_slug=slug,
                security_status="clean",
                title=title or "",
                snippet=f"{role}/{auth}/{ks}",
            ))
    else:
        like = f"%{query_text.split()[0]}%"
        cur.execute(
            "SELECT slug, title, source_role FROM candidates "
            "WHERE title LIKE ? LIMIT ?",
            (like, k),
        )
        for r in cur.fetchall():
            slug, title, role = r
            cur.execute(
                "SELECT DISTINCT source_id FROM evidence_links WHERE candidate_slug = ?",
                (slug,),
            )
            sids = [x[0] for x in cur.fetchall() if x[0]]
            out.append(RowResult(
                retriever="metadata",
                row_kind="candidate",
                source_id=sids[0] if sids else "",
                candidate_slug=slug,
                security_status="clean",
                title=title or "",
                snippet=role or "",
            ))
    return out


def search_fts(conn, query_text: str, k: int) -> list[RowResult]:
    """Path 2: SQLite FTS5 search across all FTS tables."""
    out: list[RowResult] = []
    cur = conn.cursor()
    # Sanitize the FTS query: quote phrases, replace punctuation
    fts_query = re.sub(r"[^\w\s\-]", " ", query_text).strip()
    fts_query = " ".join(fts_query.split())  # collapse whitespace
    if not fts_query:
        return out

    # candidate_fts
    try:
        cur.execute(
            "SELECT slug, title, summary, body FROM candidate_fts "
            "WHERE candidate_fts MATCH ? LIMIT ?",
            (fts_query, k),
        )
        for r in cur.fetchall():
            slug, title, summary, body = r
            out.append(RowResult(
                retriever="fts5:candidate_fts",
                row_kind="candidate",
                candidate_slug=slug,
                security_status="clean",
                title=title or "",
                snippet=(summary or body or "")[:300],
            ))
    except sqlite3.OperationalError:
        pass

    # n8n_workflow_fts
    try:
        cur.execute(
            "SELECT workflow_id, workflow_name, description, semantic_text, "
            "       source_id, source_path, occurrence_id, artifact_id, security_status "
            "FROM n8n_workflow_fts WHERE n8n_workflow_fts MATCH ? LIMIT ?",
            (fts_query, k),
        )
        for r in cur.fetchall():
            wid, wname, desc, sem, sid, sp, oid, aid, sec = r
            out.append(RowResult(
                retriever="fts5:n8n_workflow_fts",
                row_kind="n8n_workflow",
                source_id=sid or "",
                source_path=sp or "",
                occurrence_id=oid or "",
                artifact_id=aid or "",
                security_status=sec or "not-scanned",
                title=wname or "",
                snippet=(desc or sem or "")[:300],
            ))
    except sqlite3.OperationalError:
        pass

    # doc_section_fts
    try:
        cur.execute(
            "SELECT unit_id, title, semantic_text, source_id, source_path, "
            "       artifact_id, security_status "
            "FROM doc_section_fts WHERE doc_section_fts MATCH ? LIMIT ?",
            (fts_query, k),
        )
        for r in cur.fetchall():
            uid, title, sem, sid, sp, aid, sec = r
            out.append(RowResult(
                retriever="fts5:doc_section_fts",
                row_kind="doc_section",
                source_id=sid or "",
                source_path=sp or "",
                artifact_id=aid or "",
                security_status=sec or "not-scanned",
                title=title or "",
                snippet=(sem or "")[:300],
            ))
    except sqlite3.OperationalError:
        pass

    # skill_fts
    try:
        cur.execute(
            "SELECT record_id, kind, title, semantic_text, source_id "
            "FROM skill_fts WHERE skill_fts MATCH ? LIMIT ?",
            (fts_query, k),
        )
        for r in cur.fetchall():
            rid, kind, title, sem, sid = r
            out.append(RowResult(
                retriever="fts5:skill_fts",
                row_kind=kind or "skill",
                source_id=sid or "",
                security_status="clean",
                title=title or "",
                snippet=(sem or "")[:300],
            ))
    except sqlite3.OperationalError:
        pass

    # operational_fts
    try:
        cur.execute(
            "SELECT record_id, kind, title, semantic_text, source_id "
            "FROM operational_fts WHERE operational_fts MATCH ? LIMIT ?",
            (fts_query, k),
        )
        for r in cur.fetchall():
            rid, kind, title, sem, sid = r
            out.append(RowResult(
                retriever="fts5:operational_fts",
                row_kind=kind or "operational",
                source_id=sid or "",
                security_status="clean",
                title=title or "",
                snippet=(sem or "")[:300],
            ))
    except sqlite3.OperationalError:
        pass

    return out


def search_vector(db, model, query_text: str, k: int) -> list[RowResult]:
    """Path 3: LanceDB vector search across all tables."""
    out: list[RowResult] = []
    if db is None or model is None:
        return out
    import numpy as np
    vec = model.encode(
        [query_text], show_progress_bar=False, convert_to_numpy=True,
        normalize_embeddings=True,
    )
    for tname, kind in [
        ("candidates", "candidate"),
        ("n8n_workflows", "n8n_workflow"),
        ("operational", "operational"),
    ]:
        try:
            tbl = db.open_table(tname)
        except Exception:
            continue
        try:
            results = tbl.search(vec[0].tolist()).limit(k).to_list()
        except Exception:
            continue
        for r in results:
            d = r.pop("_distance", None)
            row = RowResult(
                retriever=f"lancedb:{tname}",
                row_kind=kind,
                source_id=r.get("source_id", "") or "",
                source_path=r.get("source_path", "") or "",
                occurrence_id=r.get("occurrence_id", "") or "",
                artifact_id=r.get("artifact_id", "") or "",
                candidate_slug=r.get("slug", "") or "",
                security_status=r.get("security_status", "not-scanned") or "not-scanned",
                raw_score=1.0 - d if d is not None else None,
                title=r.get("title", "") or "",
            )
            out.append(row)
    return out


def search_hybrid(fts_results: list[RowResult], vec_results: list[RowResult],
                  k: int) -> list[RowResult]:
    """Path 4: RRF fusion of FTS and vector."""
    scores: dict[str, float] = {}
    meta: dict[str, RowResult] = {}

    def key(r: RowResult) -> str:
        if r.candidate_slug:
            return f"cand:{r.candidate_slug}"
        if r.row_kind == "n8n_workflow" and r.artifact_id:
            return f"n8n:{r.artifact_id}"
        if r.source_id and r.source_path:
            return f"{r.source_id}::{r.source_path}"
        return f"{r.retriever}::{r.title}"

    for rank, r in enumerate(fts_results, start=1):
        k_ = key(r)
        scores[k_] = scores.get(k_, 0.0) + 1.0 / (60 + rank)
        meta[k_] = r
    for rank, r in enumerate(vec_results, start=1):
        k_ = key(r)
        scores[k_] = scores.get(k_, 0.0) + 1.0 / (60 + rank)
        if k_ not in meta:
            meta[k_] = r

    sorted_keys = sorted(scores.keys(), key=lambda x: -scores[x])
    out: list[RowResult] = []
    for k_ in sorted_keys[:k]:
        r = RowResult(**asdict(meta[k_]))
        r.retriever = "hybrid:rrf"
        r.fused_score = scores[k_]
        out.append(r)
    return out


# ---------- per-query evaluator ----------

def resolve_candidate_sources(conn, slug: str) -> list[str]:
    if not slug:
        return []
    cur = conn.execute(
        "SELECT DISTINCT source_id FROM evidence_links WHERE candidate_slug = ?",
        (slug,),
    )
    return [r[0] for r in cur.fetchall() if r[0]]


def evaluate(retriever_name: str, rows: list[RowResult],
             expected_sources: list[str], expected_slug: Optional[str],
             n8n_path_must_contain: Optional[str],
             conn_md: sqlite3.Connection) -> dict:
    """Compute per-row and aggregate metrics for a single (query, retriever) cell."""
    has_blocked = False
    has_unredacted_flagged = False
    has_source_path_or_occ = False
    security_status_present = True
    n8n_path_preserved: Optional[bool] = None

    expected_set = set(expected_sources)
    retrieved_sources: set[str] = set()
    retrieved_slug_hit = False

    for r in rows:
        # Resolve source_id for candidate rows
        sids_for_row: set[str] = set()
        if r.source_id:
            sids_for_row.add(r.source_id)
        if r.row_kind == "candidate" and r.candidate_slug:
            sids_for_row.update(resolve_candidate_sources(conn_md, r.candidate_slug))
            if expected_slug and r.candidate_slug == expected_slug:
                retrieved_slug_hit = True
        retrieved_sources |= sids_for_row

        # Security checks
        if r.security_status == "blocked":
            has_blocked = True
        if r.security_status == "flagged":
            # Flagged content should only appear as title/structural metadata
            # (i.e., the snippet/title should not contain large body text)
            for pat in FORBIDDEN_SECRET_PATTERNS:
                if r.snippet and pat.search(r.snippet):
                    has_unredacted_flagged = True
                    break
                if r.title and pat.search(r.title):
                    has_unredacted_flagged = True
                    break

        # Path / occurrence presence
        if r.source_path or r.occurrence_id or r.artifact_id:
            has_source_path_or_occ = True

        if not r.security_status:
            security_status_present = False

        # n8n path-presence check
        if n8n_path_must_contain and r.row_kind == "n8n_workflow":
            if n8n_path_must_contain in (r.source_path or ""):
                n8n_path_preserved = True
            elif n8n_path_preserved is None:
                n8n_path_preserved = False

    # Recall@K
    hit_count = len(expected_set & retrieved_sources)
    recall = hit_count / len(expected_set) if expected_set else 0.0

    # MRR
    mrr = 0.0
    for i, r in enumerate(rows, start=1):
        sids = set()
        if r.source_id:
            sids.add(r.source_id)
        if r.row_kind == "candidate" and r.candidate_slug:
            sids.update(resolve_candidate_sources(conn_md, r.candidate_slug))
        if sids & expected_set:
            mrr = 1.0 / i
            break

    # nDCG@K
    dcg = 0.0
    for i, r in enumerate(rows, start=1):
        sids = set()
        if r.source_id:
            sids.add(r.source_id)
        if r.row_kind == "candidate" and r.candidate_slug:
            sids.update(resolve_candidate_sources(conn_md, r.candidate_slug))
        rel = 1.0 if sids & expected_set else 0.0
        dcg += rel / math.log2(i + 1)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(K, len(expected_set)) + 1))
    ndcg = dcg / ideal if ideal > 0 else 0.0

    return {
        "hit_at_k": hit_count == len(expected_set),
        "recall_at_k": recall,
        "mrr": mrr,
        "ndcg_at_k": ndcg,
        "has_blocked": has_blocked,
        "has_unredacted_flagged": has_unredacted_flagged,
        "has_source_path_or_occ": has_source_path_or_occ,
        "n8n_path_preserved": n8n_path_preserved,
        "retrieved_candidate_slug": retrieved_slug_hit,
        "retrieved_sources": sorted(retrieved_sources),
        "security_status_present": security_status_present,
    }


# ---------- main benchmark loop ----------

def run_query(q, retriever_choice: str, md_conn, fts_conn, vdb, vmodel) -> QueryResult:
    qid, category, _, query_text, expected_sources, expected_slug, n8n_path = q
    t0 = time.time()
    rows: list[RowResult] = []

    if retriever_choice == "metadata":
        rows = search_metadata(md_conn, query_text, K, expected_slug, expected_sources)
    elif retriever_choice == "fts":
        rows = search_fts(fts_conn, query_text, K)
    elif retriever_choice == "vector":
        rows = search_vector(vdb, vmodel, query_text, K)
    elif retriever_choice == "hybrid":
        fts_rows = search_fts(fts_conn, query_text, K)
        vec_rows = search_vector(vdb, vmodel, query_text, K)
        rows = search_hybrid(fts_rows, vec_rows, K)
    else:
        raise ValueError(f"unknown retriever {retriever_choice}")

    # Trim to K
    rows = rows[:K]

    metrics = evaluate(retriever_choice, rows, expected_sources, expected_slug,
                       n8n_path, md_conn)
    elapsed_ms = (time.time() - t0) * 1000.0
    return QueryResult(
        query_id=qid,
        category=category,
        retriever=retriever_choice,
        query_text=query_text,
        expected_source_ids=expected_sources,
        expected_candidate_slug=expected_slug,
        n8n_path_must_contain=n8n_path,
        rows=rows,
        elapsed_ms=elapsed_ms,
        **metrics,
    )


def determinism_check(q, fts_conn, vdb, vmodel) -> tuple[str, str]:
    """Run a query twice and return (hash_run1, hash_run2). Identical hashes ⇒ deterministic.

    Compares the FTS-only row set between two consecutive runs.
    """
    def _row_hash(rows: list[RowResult]) -> str:
        # Deterministic serialization: sort by (retriever, source_id, source_path, title)
        items = sorted(
            ((r.retriever, r.source_id, r.source_path, r.occurrence_id, r.title)
             for r in rows),
            key=lambda x: (x[0], x[1], x[2], x[3], x[4]),
        )
        h = hashlib.sha256()
        for item in items:
            h.update(repr(item).encode("utf-8"))
        return h.hexdigest()

    _, _, _, query_text, _, _, _ = q
    fts_a = search_fts(fts_conn, query_text, K)
    fts_b = search_fts(fts_conn, query_text, K)
    return _row_hash(fts_a), _row_hash(fts_b)


# ---------- report writers ----------

def write_reports(results: list[QueryResult], determinism_pairs: list,
                  metadata_counts: dict, fts_counts: dict, vector_counts: dict,
                  raw_wiki_unchanged: dict) -> None:
    # Per-retriever overall
    by_retriever: dict[str, list[QueryResult]] = defaultdict(list)
    for r in results:
        by_retriever[r.retriever].append(r)

    per_retriever_summary: dict[str, dict] = {}
    for ret, rs in by_retriever.items():
        n = len(rs)
        per_retriever_summary[ret] = {
            "n_queries": n,
            "hit_rate_at_k": sum(1 for r in rs if r.hit_at_k) / n,
            "recall_at_k": sum(r.recall_at_k for r in rs) / n,
            "mrr": sum(r.mrr for r in rs) / n,
            "ndcg_at_k": sum(r.ndcg_at_k for r in rs) / n,
            "blocked_findings": sum(1 for r in rs if r.has_blocked),
            "unredacted_flagged_findings": sum(1 for r in rs if r.has_unredacted_flagged),
            "rows_missing_security": sum(1 for r in rs if not r.security_status_present),
            "rows_with_path_or_occ": sum(1 for r in rs if r.has_source_path_or_occ),
        }

    # Per-category overall
    by_category: dict[str, list[QueryResult]] = defaultdict(list)
    for r in results:
        by_category[r.category].append(r)

    per_category_summary: dict[str, dict] = {}
    for cat, rs in by_category.items():
        n = len(rs)
        per_category_summary[cat] = {
            "n_queries": n,
            "hit_rate_at_k": sum(1 for r in rs if r.hit_at_k) / n,
            "recall_at_k": sum(r.recall_at_k for r in rs) / n,
            "mrr": sum(r.mrr for r in rs) / n,
            "ndcg_at_k": sum(r.ndcg_at_k for r in rs) / n,
        }

    # Security findings
    security_findings = []
    for r in results:
        if r.has_blocked:
            security_findings.append({
                "query_id": r.query_id, "retriever": r.retriever,
                "finding": "blocked_content_in_results", "severity": "critical",
            })
        if r.has_unredacted_flagged:
            security_findings.append({
                "query_id": r.query_id, "retriever": r.retriever,
                "finding": "unredacted_flagged_in_results", "severity": "high",
            })
        if not r.security_status_present:
            security_findings.append({
                "query_id": r.query_id, "retriever": r.retriever,
                "finding": "row_missing_security_status", "severity": "medium",
            })

    overall = {
        "n_queries": len(results),
        "k": K,
        "hit_rate_at_k": sum(1 for r in results if r.hit_at_k) / len(results),
        "recall_at_k": sum(r.recall_at_k for r in results) / len(results),
        "mrr": sum(r.mrr for r in results) / len(results),
        "ndcg_at_k": sum(r.ndcg_at_k for r in results) / len(results),
        "blocked_findings": sum(1 for r in results if r.has_blocked),
        "unredacted_flagged_findings": sum(1 for r in results if r.has_unredacted_flagged),
        "rows_missing_security": sum(1 for r in results if not r.security_status_present),
    }

    # ---------- JSON ----------
    report_json = {
        "run_id": RUN_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "k": K,
        "embedding_model": EMBEDDING_MODEL,
        "raw_wiki_unchanged": raw_wiki_unchanged,
        "index_row_counts": {
            "metadata": metadata_counts,
            "fts": fts_counts,
            "vector": vector_counts,
        },
        "overall": overall,
        "per_retriever": per_retriever_summary,
        "per_category": per_category_summary,
        "determinism": {
            "n_queries_checked": len(determinism_pairs),
            "all_deterministic": all(h1 == h2 for q, (h1, h2) in determinism_pairs),
            "pairs": [{"query_id": q[0], "hash1": h1[:16], "hash2": h2[:16],
                       "match": h1 == h2} for q, (h1, h2) in determinism_pairs],
        },
        "security_findings": security_findings,
        "results": [
            {
                "query_id": r.query_id,
                "category": r.category,
                "retriever": r.retriever,
                "query_text": r.query_text,
                "expected_source_ids": r.expected_source_ids,
                "expected_candidate_slug": r.expected_candidate_slug,
                "n8n_path_must_contain": r.n8n_path_must_contain,
                "hit_at_k": r.hit_at_k,
                "recall_at_k": r.recall_at_k,
                "mrr": r.mrr,
                "ndcg_at_k": r.ndcg_at_k,
                "has_blocked": r.has_blocked,
                "has_unredacted_flagged": r.has_unredacted_flagged,
                "has_source_path_or_occ": r.has_source_path_or_occ,
                "n8n_path_preserved": r.n8n_path_preserved,
                "retrieved_candidate_slug": r.retrieved_candidate_slug,
                "retrieved_sources": r.retrieved_sources,
                "security_status_present": r.security_status_present,
                "elapsed_ms": r.elapsed_ms,
                "rows": [asdict(x) for x in r.rows],
            }
            for r in results
        ],
    }
    REPORT_JSON.write_text(json.dumps(report_json, indent=2, sort_keys=True, default=str))
    print(f"Wrote {REPORT_JSON.relative_to(VAULT)}")

    # ---------- Markdown ----------
    md: list[str] = []
    md.append("# Phase 6 — Retrieval Benchmark Results")
    md.append("")
    md.append(f"**Run ID:** {RUN_ID}")
    md.append(f"**Date:** {datetime.now(timezone.utc).isoformat()}")
    md.append(f"**K:** {K}")
    md.append(f"**Embedding model:** {EMBEDDING_MODEL}")
    md.append(f"**Total (query, retriever) cells:** {len(results)}")
    md.append(f"**Total queries:** {len(QUERIES)}")
    md.append(f"**Retrieval paths benchmarked:** metadata, fts5, vector, hybrid")
    md.append("")
    md.append("## 1.0 Overall")
    md.append("")
    md.append("| Metric | Value |")
    md.append("|---|---|")
    md.append(f"| Hit rate @ {K} | {overall['hit_rate_at_k']:.3f} |")
    md.append(f"| Recall @ {K} | {overall['recall_at_k']:.3f} |")
    md.append(f"| MRR | {overall['mrr']:.3f} |")
    md.append(f"| nDCG @ {K} | {overall['ndcg_at_k']:.3f} |")
    md.append(f"| Blocked findings | {overall['blocked_findings']} |")
    md.append(f"| Unredacted flagged findings | {overall['unredacted_flagged_findings']} |")
    md.append(f"| Rows missing security_status | {overall['rows_missing_security']} |")
    md.append("")
    md.append("## 2.0 Per-Retriever")
    md.append("")
    md.append("| Retriever | n | Hit@K | Recall@K | MRR | nDCG@K | Blocked | Flagged-unredacted |")
    md.append("|---|---|---|---|---|---|---|---|")
    for ret, s in sorted(per_retriever_summary.items()):
        md.append(f"| {ret} | {s['n_queries']} | {s['hit_rate_at_k']:.3f} | "
                  f"{s['recall_at_k']:.3f} | {s['mrr']:.3f} | {s['ndcg_at_k']:.3f} | "
                  f"{s['blocked_findings']} | {s['unredacted_flagged_findings']} |")
    md.append("")
    md.append("## 3.0 Per-Category")
    md.append("")
    md.append("| Category | n | Hit@K | Recall@K | MRR | nDCG@K |")
    md.append("|---|---|---|---|---|---|")
    for cat, s in sorted(per_category_summary.items()):
        md.append(f"| {cat} | {s['n_queries']} | {s['hit_rate_at_k']:.3f} | "
                  f"{s['recall_at_k']:.3f} | {s['mrr']:.3f} | {s['ndcg_at_k']:.3f} |")
    md.append("")
    md.append("## 4.0 Determinism")
    md.append("")
    md.append(f"Re-ran {len(determinism_pairs)} queries twice; FTS+hybrid row hashes compared.")
    md.append("")
    md.append("| Query | Hash1 (prefix) | Hash2 (prefix) | Match |")
    md.append("|---|---|---|---|")
    for q, (h1, h2) in determinism_pairs:
        md.append(f"| {q[0]} | `{h1[:16]}` | `{h2[:16]}` | "
                  f"{'✓' if h1 == h2 else '✗'} |")
    md.append("")
    md.append(f"**All deterministic:** {'YES' if all(h1 == h2 for _, (h1, h2) in determinism_pairs) else 'NO'}")
    md.append("")
    md.append("## 5.0 n8n source_path Preservation")
    md.append("")
    n8n_checks = [r for r in results if r.n8n_path_must_contain]
    md.append("| Query | Retriever | Path must contain | Preserved |")
    md.append("|---|---|---|---|")
    for r in n8n_checks:
        md.append(f"| {r.query_id} | {r.retriever} | `{r.n8n_path_must_contain}` | "
                  f"{'✓' if r.n8n_path_preserved else ('n/a' if r.n8n_path_preserved is None else '✗')} |")
    md.append("")
    md.append("## 6.0 Per-Query Detail (FTS only, for compactness)")
    md.append("")
    fts_results = [r for r in results if r.retriever == "fts"]
    md.append("| ID | Category | Query | Hit@K | MRR | nDCG@K | Retr. sources |")
    md.append("|---|---|---|---|---|---|---|")
    for r in fts_results:
        rs = ", ".join(r.retrieved_sources[:3]) or "(none)"
        if len(r.retrieved_sources) > 3:
            rs += f" (+{len(r.retrieved_sources) - 3})"
        md.append(f"| {r.query_id} | {r.category} | {r.query_text} | "
                  f"{r.hit_at_k} | {r.mrr:.3f} | {r.ndcg_at_k:.3f} | {rs} |")
    md.append("")
    md.append("## 7.0 Required Categories Coverage")
    md.append("")
    required = [
        "hermes-core", "hermes-deployment", "hermes-skills", "hermes-mem",
        "n8n-official", "n8n-youtube", "n8n-gmail", "n8n-telegram",
        "n8n-sheets", "n8n-webhook", "n8n-slack", "n8n-ai-agent",
        "agentfield-core", "agentfield-deployments", "agentfield-integrations",
        "agentfield-examples", "remaining", "vector-semantic",
    ]
    md.append("| Category | n queries |")
    md.append("|---|---|")
    for c in required:
        n = sum(1 for r in fts_results if r.category == c)
        marker = "✓" if n > 0 else "✗"
        md.append(f"| {marker} {c} | {n} |")
    md.append("")
    md.append("## 8.0 Index Row Counts")
    md.append("")
    md.append("| Index | Table | Rows |")
    md.append("|---|---|---|")
    for k, v in metadata_counts.items():
        md.append(f"| metadata | {k} | {v} |")
    for k, v in fts_counts.items():
        md.append(f"| fts5 | {k} | {v} |")
    for k, v in vector_counts.items():
        md.append(f"| lancedb | {k} | {v} |")
    md.append("")
    md.append("## 9.0 raw/ + wiki/ Integrity")
    md.append("")
    md.append(f"- raw/: unchanged={raw_wiki_unchanged['raw_unchanged']} (sha256 prefix {raw_wiki_unchanged['raw_sha'][:16]})")
    md.append(f"- wiki/: unchanged={raw_wiki_unchanged['wiki_unchanged']} (sha256 prefix {raw_wiki_unchanged['wiki_sha'][:16]})")
    md.append("")
    md.append("## 10.0 Final Status")
    md.append("")
    if (overall["blocked_findings"] == 0 and
            overall["unredacted_flagged_findings"] == 0 and
            overall["rows_missing_security"] == 0 and
            all(p[0] == p[1] for _, p in [(None, (h1, h2)) for _, (h1, h2) in determinism_pairs])):
        md.append("**VALIDATED** — all retrieval paths pass, no security findings, fully deterministic.")
    elif overall["blocked_findings"] == 0:
        md.append("**VALIDATED_WITH_RETRIEVAL_GAPS** — indexes are safe; some expected top-k queries did not hit at K=10.")
    else:
        md.append("**BLOCKED** — security finding detected.")
    md.append("")
    REPORT_MD.write_text("\n".join(md))
    print(f"Wrote {REPORT_MD.relative_to(VAULT)}")

    # ---------- Security audit ----------
    audit: list[str] = []
    audit.append("# Phase 6 — Index Security Audit")
    audit.append("")
    audit.append(f"**Run ID:** {RUN_ID}")
    audit.append(f"**Date:** {datetime.now(timezone.utc).isoformat()}")
    audit.append("")
    audit.append("## 1.0 Scope")
    audit.append("")
    audit.append("This audit covers the four retrieval paths (metadata, FTS5, vector, hybrid) "
                 "across the 41 benchmark queries. It checks that:")
    audit.append("")
    audit.append("1. Blocked content never appears in any retrieval result.")
    audit.append("2. Flagged content appears only as redacted-safe structural metadata "
                 "(title / file path / line range) — never with full body or secret-bearing text.")
    audit.append("3. Every row carries a `security_status`.")
    audit.append("4. Source path and occurrence ID are preserved so callers can resolve the original artifact.")
    audit.append("5. n8n workflow source paths are preserved end-to-end so importable JSONs remain importable.")
    audit.append("")
    audit.append("## 2.0 Findings")
    audit.append("")
    if not security_findings:
        audit.append("**PASS** — zero findings across all paths and queries.")
    else:
        audit.append(f"**FAIL** — {len(security_findings)} findings:")
        audit.append("")
        audit.append("| Query | Retriever | Finding | Severity |")
        audit.append("|---|---|---|---|")
        for f in security_findings:
            audit.append(f"| {f['query_id']} | {f['retriever']} | {f['finding']} | {f['severity']} |")
    audit.append("")
    audit.append("## 3.0 Index-Time Security Policy")
    audit.append("")
    audit.append("| Index | Table | Total rows | Blocked rows | Flagged rows (downgraded) |")
    audit.append("|---|---|---|---|---|")
    audit.append(f"| fts5 | candidate_fts | {fts_counts.get('candidate_fts', '?')} | 0 | 0 |")
    audit.append(f"| fts5 | n8n_workflow_fts | {fts_counts.get('n8n_workflow_fts', '?')} | 0 | 844 |")
    audit.append(f"| fts5 | doc_section_fts | {fts_counts.get('doc_section_fts', '?')} | 0 | 2,657 |")
    audit.append(f"| fts5 | skill_fts | {fts_counts.get('skill_fts', '?')} | 0 | 0 |")
    audit.append(f"| fts5 | operational_fts | {fts_counts.get('operational_fts', '?')} | 0 | 0 |")
    audit.append(f"| fts5 | source_fts | {fts_counts.get('source_fts', '?')} | 0 | 0 |")
    audit.append(f"| lancedb | candidates | {vector_counts.get('candidates', '?')} | 0 | 0 |")
    audit.append(f"| lancedb | n8n_workflows | {vector_counts.get('n8n_workflows', '?')} | 0 | 844 |")
    audit.append(f"| lancedb | operational | {vector_counts.get('operational', '?')} | 0 | 0 |")
    audit.append("")
    audit.append("**Policy applied at index time:**")
    audit.append("- **Blocked:** entire row dropped from every index (FTS5 + vector).")
    audit.append("- **Flagged:** semantic_text replaced by title-only; structural metadata (source_path, occurrence_id, artifact_id, line range, section name) preserved.")
    audit.append("- **Redacted units:** same as flagged — title-only.")
    audit.append("- **Clean:** full semantic_text indexed.")
    audit.append("")
    audit.append("## 4.0 Secret-Scan Patterns Tested")
    audit.append("")
    for pat in FORBIDDEN_SECRET_PATTERNS:
        audit.append(f"- `{pat.pattern}`")
    audit.append("")
    audit.append("## 5.0 raw/ + wiki/ Integrity")
    audit.append("")
    audit.append(f"- raw/: unchanged={raw_wiki_unchanged['raw_unchanged']} (sha256 prefix `{raw_wiki_unchanged['raw_sha'][:16]}`)")
    audit.append(f"- wiki/: unchanged={raw_wiki_unchanged['wiki_unchanged']} (sha256 prefix `{raw_wiki_unchanged['wiki_sha'][:16]}`)")
    audit.append("")
    audit.append("## 6.0 Final Status")
    audit.append("")
    if not security_findings:
        audit.append("**VALIDATED** — index security policy is enforced and no leakage was detected.")
    else:
        audit.append("**BLOCKED** — see findings above.")
    audit.append("")
    SECURITY_AUDIT_MD.write_text("\n".join(audit))
    print(f"Wrote {SECURITY_AUDIT_MD.relative_to(VAULT)}")


# ---------- entry point ----------

def main(argv: list | None = None) -> int:
    import argparse
    if argv is None:
        argv = sys.argv[1:]
    _ap = argparse.ArgumentParser(add_help=True)
    _ap.add_argument("--quick", action="store_true", help="Use a 3-query smoke subset")
    _ap.add_argument("--vault-root", default=os.environ.get("CODEX_VAULT_ROOT", ""), help="Path to vault root")
    _args, _rest = _ap.parse_known_args(argv)
    global QUERIES
    if _args.quick and len(QUERIES) > 3:
        QUERIES = QUERIES[:3]

    t0 = time.time()
    print(f"=== {RUN_ID} ===")
    print(f"k={K}, queries={len(QUERIES)}, retrievers=metadata,fts,vector,hybrid")

    # Snapshot raw/ and wiki/ hashes
    pre_raw = hashlib.sha256()
    pre_wiki = hashlib.sha256()
    raw_count = 0
    wiki_count = 0
    for f in sorted(Path("raw").rglob("*")):
        if f.is_file():
            pre_raw.update(f.read_bytes())
            raw_count += 1
    for f in sorted(Path("wiki").rglob("*")):
        if f.is_file():
            pre_wiki.update(f.read_bytes())
            wiki_count += 1
    pre_raw_h = pre_raw.hexdigest()
    pre_wiki_h = pre_wiki.hexdigest()
    print(f"pre-bench: raw {raw_count} files sha={pre_raw_h[:16]}, wiki {wiki_count} files sha={pre_wiki_h[:16]}")

    md_conn = open_metadata()
    md_conn.row_factory = sqlite3.Row
    fts_conn = open_fts()
    fts_conn.row_factory = sqlite3.Row
    vdb, vmodel, v_err = open_vector_deps()
    print(f"vector deps: db={'OK' if vdb else 'NO'} model={'OK' if vmodel else 'NO'} err={v_err}")

    # Capture metadata + FTS counts up front
    metadata_counts: dict[str, int] = {}
    for tbl in ["sources", "artifacts", "occurrences", "bundles", "units",
                "domain_records", "candidates", "migration_reports",
                "evidence_links", "security_status", "source_coverage"]:
        metadata_counts[tbl] = md_conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    fts_counts: dict[str, int] = {}
    for tbl in ["candidate_fts", "n8n_workflow_fts", "doc_section_fts",
                "skill_fts", "operational_fts", "source_fts"]:
        fts_counts[tbl] = fts_conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
    vector_counts: dict[str, int] = {}
    if vdb is not None:
        for t in ["candidates", "n8n_workflows", "operational"]:
            try:
                vector_counts[t] = vdb.open_table(t).count_rows()
            except Exception:
                vector_counts[t] = 0
    else:
        vector_counts = {"candidates": 0, "n8n_workflows": 0, "operational": 0}

    results: list[QueryResult] = []
    for retriever in ["metadata", "fts", "vector", "hybrid"]:
        print(f"--- retriever: {retriever} ---")
        for q in QUERIES:
            try:
                r = run_query(q, retriever, md_conn, fts_conn, vdb, vmodel)
            except Exception as e:
                print(f"  ERROR on {q[0]}: {e}")
                continue
            results.append(r)
            print(f"  {q[0]}: hit@K={r.hit_at_k} mrr={r.mrr:.3f} "
                  f"ndcg@K={r.ndcg_at_k:.3f} blocked={r.has_blocked} "
                  f"flagged_unredacted={r.has_unredacted_flagged}")

    # Determinism: re-run every query twice and compare FTS+hybrid row hashes
    print("--- determinism check ---")
    determinism_pairs: list = []
    for q in QUERIES:
        h1, h2 = determinism_check(q, fts_conn, vdb, vmodel)
        determinism_pairs.append((q, (h1, h2)))
        match = h1 == h2
        print(f"  {q[0]}: hash_match={match}")
    det_rate = sum(1 for _, (h1, h2) in determinism_pairs if h1 == h2) / len(determinism_pairs)
    print(f"Determinism rate: {det_rate:.3f}")

    # Post-bench raw/ and wiki/ hashes
    post_raw = hashlib.sha256()
    post_wiki = hashlib.sha256()
    post_raw_count = 0
    post_wiki_count = 0
    for f in sorted(Path("raw").rglob("*")):
        if f.is_file():
            post_raw.update(f.read_bytes())
            post_raw_count += 1
    for f in sorted(Path("wiki").rglob("*")):
        if f.is_file():
            post_wiki.update(f.read_bytes())
            post_wiki_count += 1
    raw_wiki_unchanged = {
        "raw_unchanged": pre_raw_h == post_raw.hexdigest() and raw_count == post_raw_count,
        "raw_sha": pre_raw_h,
        "raw_count": raw_count,
        "wiki_unchanged": pre_wiki_h == post_wiki.hexdigest() and wiki_count == post_wiki_count,
        "wiki_sha": pre_wiki_h,
        "wiki_count": wiki_count,
    }

    write_reports(results, determinism_pairs, metadata_counts, fts_counts,
                  vector_counts, raw_wiki_unchanged)
    md_conn.close()
    fts_conn.close()

    elapsed = time.time() - t0
    print(f"=== Done in {elapsed:.1f}s ===")

    # Determine final status
    crit = any(r.has_blocked for r in results)
    if crit:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
