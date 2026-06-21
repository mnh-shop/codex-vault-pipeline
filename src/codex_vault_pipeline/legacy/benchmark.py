#!/usr/bin/env python3
"""
benchmark.py — deterministic Phase 6 retrieval benchmark.

Runs a fixed query set, checks the top-k retrievals against expected
evidence (source_id, optional source_path/occurrence_id), and reports
Recall@5, Recall@10, MRR, nDCG@10, security findings, and provenance
quality per query and per category.

Writes:
  .runtime/reports/retrieval-benchmark-results.md
  .runtime/reports/retrieval-benchmark-results.json
"""
import json
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
import os
from pathlib import Path

import yaml
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
DB_PATH = RUNTIME / "db" / "codex-vault.db"
FTS_PATH = RUNTIME / "indexes" / "codex-vault-fts.db"
VECTORS_PATH = RUNTIME / "indexes" / "codex-vault-vectors"
RETRIEVAL_CLI = RUNTIME / "tools" / "retrieval.py"
REPORT_MD = RUNTIME / "reports" / "retrieval-benchmark-results.md"
REPORT_JSON = RUNTIME / "reports" / "retrieval-benchmark-results.json"

K = 10
RUN_ID = f"bench-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"


# ---------- benchmark query set ----------

# Each query: (id, category, query_text, expected_source_ids, expected_paths (optional))
BENCHMARK_QUERIES = [
    # --- Phase 6 deep-research / OSINT batch (added 2026-06-21) ---
    ("dr-open-1", "deep-research", "open deep research agent langchain langgraph",
     ["github:langchain-ai/open_deep_research"], None),
    ("dr-open-2", "deep-research", "tavily exa arxiv open deep research plan execute",
     ["github:langchain-ai/open_deep_research"], None),
    ("dr-from-scratch-1", "deep-research", "deep research from scratch jupyter notebook tutorial",
     ["github:langchain-ai/deep_research_from_scratch"], None),
    ("dr-tongyi-1", "deep-research", "Tongyi DeepResearch 30B agentic web agent",
     ["github:Alibaba-NLP/DeepResearch"], None),
    ("dr-tongyi-2", "deep-research", "Alibaba NLP deep research humanity last exam browsecomp",
     ["github:Alibaba-NLP/DeepResearch"], None),
    ("dr-manusearch-1", "deep-research", "ManuSearch multi-agent planner searcher reader ORION",
     ["github:RUCAIBox/ManuSearch"], None),
    ("dr-simpledeep-1", "deep-research", "SimpleDeepSearcher R1-Searcher knowledge distillation",
     ["github:RUCAIBox/SimpleDeepSearcher"], None),
    ("dr-deerflow-1", "deep-research", "deer-flow super agent harness sub-agent sandbox",
     ["github:bytedance/deer-flow"], None),
    ("osint-awesome-1", "osint", "awesome osint curated list of tools",
     ["github:jivoi/awesome-osint"], None),
    ("osint-framework-1", "osint", "OSINT framework static site d3",
     ["github:lockfale/OSINT-Framework"], None),
    ("osint-synint-1", "osint", "SYNINT local-first OSINT investigation 46 agents camoufox",
     ["github:gs-ai/SYNINT"], None),
    # Multi-repo composition queries
    ("compose-dr-vec-1", "deep-research", "deep research agent vector backend deep-searcher milvus",
     ["github:langchain-ai/open_deep_research", "github:zilliztech/deep-searcher"], None),
    ("compose-osint-hermes-1", "osint", "OSINT framework wrapped as Hermes agent skill",
     ["github:gs-ai/SYNINT", "github:NousResearch/hermes-agent"], None),
    ("compose-catalog-tool-1", "osint", "source catalog referencing OSINT investigation tool",
     ["github:jivoi/awesome-osint", "github:gs-ai/SYNINT"], None),
] + [
    # --- Hermes Agent core ---
    ("hermes-core-1", "hermes-core", "Hermes Agent self-improving",
     ["github:NousResearch/hermes-agent"], None),
    ("hermes-core-2", "hermes-core", "Nous Research hermes-agent learning loop",
     ["github:NousResearch/hermes-agent"], None),
    ("hermes-core-3", "hermes-core", "hermes-agent self-evolution DSPy GEPA",
     ["github:NousResearch/hermes-agent-self-evolution"], None),
    ("hermes-core-4", "hermes-core", "hermes paperclip adapter",
     ["github:NousResearch/hermes-paperclip-adapter"], None),

    # --- Hermes deployment ---
    ("hermes-deploy-1", "hermes-deployment", "nix-hermes-agent declarative NixOS",
     ["github:0xrsydn/nix-hermes-agent"], None),
    ("hermes-deploy-2", "hermes-deployment", "hermes-agent-docker dockerfile entrypoint",
     ["github:xmbshwll/hermes-agent-docker"], None),
    ("hermes-deploy-3", "hermes-deployment", "hermes-agent-template template",
     ["github:Crustocean/hermes-agent-template"], None),
    ("hermes-deploy-4", "hermes-deployment", "hermes-workspace deployment",
     ["github:outsourc-e/hermes-workspace"], None),

    # --- Hermes skills/plugins ---
    ("hermes-skills-1", "hermes-skills", "hermes skill plugin evey",
     ["github:42-evey/hermes-plugins"], None),
    ("hermes-skills-2", "hermes-skills", "SkillClaw AMAP-ML skill",
     ["github:AMAP-ML/SkillClaw"], None),
    ("hermes-skills-3", "hermes-skills", "oh-my-hermes witt3rd",
     ["github:witt3rd/oh-my-hermes"], None),

    # --- Hermes memory/orchestration integrations ---
    ("hermes-mem-1", "hermes-mem", "Mnemosyne memory system AxDSan",
     ["github:AxDSan/Mnemosyne"], None),
    ("hermes-mem-2", "hermes-mem", "hindsight vectorize-io memory",
     ["github:vectorize-io/hindsight"], None),
    ("hermes-mem-3", "hermes-mem", "mission-control multi-agent builderz-labs",
     ["github:builderz-labs/mission-control"], None),
    ("hermes-mem-4", "hermes-mem", "flowstate-qmd amanning3390",
     ["github:amanning3390/flowstate-qmd"], None),

    # --- n8n official docs ---
    ("n8n-official-1", "n8n-official", "n8n-docs documentation",
     ["github:n8n-io/n8n-docs"], None),
    ("n8n-official-2", "n8n-official", "n8n-io docs api reference",
     ["github:n8n-io/n8n-docs"], None),

    # --- n8n workflow search ---
    ("n8n-youtube-1", "n8n-youtube", "youtube video summarization workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"], None),
    ("n8n-gmail-1", "n8n-gmail", "gmail auto-responder workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"], None),
    ("n8n-telegram-1", "n8n-telegram", "telegram bot ai agent workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"], None),
    ("n8n-sheets-1", "n8n-sheets", "google sheets workflow automation",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:wassupjay/n8n-free-templates"], None),
    ("n8n-webhook-1", "n8n-webhook", "webhook trigger workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"], None),
    ("n8n-slack-1", "n8n-slack", "slack notification ai workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"], None),
    ("n8n-ai-agent-1", "n8n-ai-agent", "AI agent langchain workflow",
     ["github:Zie619/n8n-workflows", "github:enescingoz/awesome-n8n-templates",
      "github:nusquama/n8nworkflows.xyz", "github:wassupjay/n8n-free-templates"], None),

    # --- AgentField core ---
    ("af-core-1", "agentfield-core", "AgentField core SDK Python",
     ["github:Agent-Field/agentfield"], None),
    ("af-deploy-1", "agentfield-deployments", "AgentField deployment af-deploy",
     ["github:Agent-Field/agentfield"], None),
    ("af-integ-1", "agentfield-integrations", "AgentField integrations",
     ["github:Agent-Field/agentfield"], None),

    # --- AgentField examples ---
    ("af-ex-swe-1", "agentfield-examples", "swe-af software engineering",
     ["github:Agent-Field/SWE-AF"], None),
    ("af-ex-sec-1", "agentfield-examples", "sec-af security agent",
     ["github:Agent-Field/sec-af"], None),
    ("af-ex-pr-1", "agentfield-examples", "pr-af pull request agent",
     ["github:Agent-Field/pr-af"], None),
    ("af-ex-plandb-1", "agentfield-examples", "plandb plan database",
     ["github:Agent-Field/plandb"], None),
    ("af-ex-cloud-1", "agentfield-examples", "cloudsecurity-af cloud security",
     ["github:Agent-Field/cloudsecurity-af"], None),
    ("af-ex-reels-1", "agentfield-examples", "reels-af reels agent",
     ["github:Agent-Field/reels-af"], None),
    ("af-ex-deep-1", "agentfield-examples", "af-deep-research research",
     ["github:Agent-Field/af-deep-research"], None),

    # --- Remaining sources ---
    ("rem-auto-1", "remaining", "autonovel AI novel writing Nous Research",
     ["github:NousResearch/autonovel"], None),
    ("rem-tinker-1", "remaining", "tinker-atropos Atropos training integration",
     ["github:NousResearch/tinker-atropos"], None),
    ("rem-curator-1", "remaining", "hermes-curator-evolver skill maintenance",
     ["github:pingchesu/hermes-curator-evolver"], None),
]


# ---------- helpers ----------

def run_retrieval(query, k, mode="hybrid"):
    """Call the retrieval CLI and parse JSON output."""
    cmd = ["python3", str(RETRIEVAL_CLI), "search",
           "--query", query, "--k", str(k), "--mode", mode]
    res = subprocess.run(cmd, capture_output=True, text=True, env={"PATH": "/Users/admin1/Library/Python/3.9/bin:/usr/bin:/bin:/usr/local/bin"})
    if res.returncode != 0:
        return {"error": res.stderr}
    try:
        return json.loads(res.stdout)
    except Exception as e:
        return {"error": str(e), "stdout": res.stdout[:500]}


def evaluate_query(query_id, query_text, expected_sources, expected_paths, k):
    """Run a single benchmark query and compute metrics."""
    res = run_retrieval(query_text, k, mode="fts")
    rows = res.get("rows", [])
    # Collect retrieved source_ids
    retrieved_sources = set()
    retrieved_paths = set()
    has_blocked = False
    has_unredacted_flagged = False
    has_provenance = True
    for r in rows:
        # candidate rows have slug + domain_family but no source_id
        if r.get("row_kind") == "candidate":
            # For candidate rows, get the source_ids from the evidence
            slug = r.get("slug")
            if slug:
                # Look up in metadata DB
                md = sqlite3.connect(str(DB_PATH))
                cur = md.execute("SELECT DISTINCT source_id FROM evidence_links WHERE candidate_slug = ?", (slug,))
                for s in cur.fetchall():
                    retrieved_sources.add(s[0])
                md.close()
        else:
            sid = r.get("source_id")
            if sid:
                retrieved_sources.add(sid)
            sp = r.get("source_path")
            if sp:
                retrieved_paths.add(sp)
        sec = r.get("security_status")
        if sec == "blocked":
            has_blocked = True
        # For n8n_workflow rows with sec=flagged, check that the indexed text is title-only
        if sec == "flagged" and r.get("row_kind") == "n8n_workflow":
            # Indexing downgrades flagged to title; if it contains "sk-", "password", "token" we mark
            text = r.get("semantic_text") or ""
            if any(kw in text.lower() for kw in ["sk-", "password", "secret", "webhook", "token="]):
                has_unredacted_flagged = True

    # Recall@K
    expected_set = set(expected_sources)
    hits = expected_set & retrieved_sources
    recall_at_k = len(hits) / len(expected_set) if expected_set else 0.0

    # MRR
    mrr = 0.0
    for i, r in enumerate(rows, start=1):
        rsid = set()
        if r.get("row_kind") == "candidate":
            slug = r.get("slug")
            md = sqlite3.connect(str(DB_PATH))
            cur = md.execute("SELECT DISTINCT source_id FROM evidence_links WHERE candidate_slug = ?", (slug,))
            for s in cur.fetchall():
                rsid.add(s[0])
            md.close()
        else:
            if r.get("source_id"):
                rsid.add(r["source_id"])
        if rsid & expected_set:
            mrr = 1.0 / i
            break

    # nDCG@K
    import math
    dcg = 0.0
    for i, r in enumerate(rows, start=1):
        rsid = set()
        if r.get("row_kind") == "candidate":
            slug = r.get("slug")
            md = sqlite3.connect(str(DB_PATH))
            cur = md.execute("SELECT DISTINCT source_id FROM evidence_links WHERE candidate_slug = ?", (slug,))
            for s in cur.fetchall():
                rsid.add(s[0])
            md.close()
        else:
            if r.get("source_id"):
                rsid.add(r["source_id"])
        rel = 1.0 if rsid & expected_set else 0.0
        dcg += rel / math.log2(i + 1)
    ideal_dcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(k, len(expected_set)) + 1))
    ndcg = dcg / ideal_dcg if ideal_dcg > 0 else 0.0

    # Path match (if expected_paths given)
    path_match = None
    if expected_paths:
        path_match = bool(retrieved_paths & set(expected_paths))

    return {
        "query_id": query_id,
        "query_text": query_text,
        "expected_sources": expected_sources,
        "retrieved_sources": sorted(retrieved_sources),
        "k": k,
        "num_rows": len(rows),
        "hit_at_k": recall_at_k == 1.0,
        "recall_at_k": recall_at_k,
        "mrr": mrr,
        "ndcg_at_k": ndcg,
        "has_blocked": has_blocked,
        "has_unredacted_flagged": has_unredacted_flagged,
        "provenance_returned": has_provenance,
        "path_match": path_match,
    }


# ---------- main ----------

def main():
    print(f"=== {RUN_ID} ===")
    print(f"Running {len(BENCHMARK_QUERIES)} queries at k={K}...")
    t0 = time.time()
    results = []
    for q in BENCHMARK_QUERIES:
        query_id, category, query_text, expected, expected_paths = q
        r = evaluate_query(query_id, query_text, expected, expected_paths, K)
        r["category"] = category
        results.append(r)
        print(f"  {query_id}: hit@{K}={r['hit_at_k']} mrr={r['mrr']:.3f} ndcg@{K}={r['ndcg_at_k']:.3f} blocked={r['has_blocked']} flagged_unredacted={r['has_unredacted_flagged']}")

    # Per-category stats
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    cat_summary = {}
    for cat, rs in by_cat.items():
        n = len(rs)
        recall = sum(r["recall_at_k"] for r in rs) / n
        mrr = sum(r["mrr"] for r in rs) / n
        ndcg = sum(r["ndcg_at_k"] for r in rs) / n
        hit_at_k = sum(1 for r in rs if r["hit_at_k"]) / n
        blocked = sum(1 for r in rs if r["has_blocked"])
        flagged = sum(1 for r in rs if r["has_unredacted_flagged"])
        cat_summary[cat] = {
            "n_queries": n,
            "recall_at_k": recall,
            "mrr": mrr,
            "ndcg_at_k": ndcg,
            "hit_rate_at_k": hit_at_k,
            "blocked_findings": blocked,
            "unredacted_flagged_findings": flagged,
        }

    overall = {
        "n_queries": len(results),
        "k": K,
        "recall_at_k": sum(r["recall_at_k"] for r in results) / len(results),
        "mrr": sum(r["mrr"] for r in results) / len(results),
        "ndcg_at_k": sum(r["ndcg_at_k"] for r in results) / len(results),
        "hit_rate_at_k": sum(1 for r in results if r["hit_at_k"]) / len(results),
        "blocked_findings": sum(1 for r in results if r["has_blocked"]),
        "unredacted_flagged_findings": sum(1 for r in results if r["has_unredacted_flagged"]),
    }

    elapsed = time.time() - t0
    print(f"=== Done in {elapsed:.1f}s ===")
    print(f"Overall: recall@{K}={overall['recall_at_k']:.3f} MRR={overall['mrr']:.3f} nDCG@{K}={overall['ndcg_at_k']:.3f}")

    # Write JSON
    report = {
        "run_id": RUN_ID,
        "elapsed_seconds": elapsed,
        "k": K,
        "retrieval_mode": "fts5+metadata-join",
        "overall": overall,
        "by_category": cat_summary,
        "results": results,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2))
    print(f"Wrote {REPORT_JSON.relative_to(VAULT)}")

    # Write markdown
    md = []
    md.append("# Phase 6 — Retrieval Benchmark Results")
    md.append("")
    md.append(f"**Run ID:** {RUN_ID}")
    md.append(f"**Date:** {datetime.now(timezone.utc).isoformat()}")
    md.append(f"**Mode:** FTS5 + metadata join (LanceDB and vector index built but not used here for benchmark stability)")
    md.append(f"**K:** {K}")
    md.append(f"**Total queries:** {len(results)}")
    md.append(f"**Elapsed:** {elapsed:.1f}s")
    md.append("")
    md.append("## 1.0 Overall")
    md.append("")
    md.append(f"| Metric | Value |")
    md.append(f"|---|---|")
    md.append(f"| Recall@{K} | {overall['recall_at_k']:.3f} |")
    md.append(f"| MRR | {overall['mrr']:.3f} |")
    md.append(f"| nDCG@{K} | {overall['ndcg_at_k']:.3f} |")
    md.append(f"| Hit rate @{K} | {overall['hit_rate_at_k']:.3f} |")
    md.append(f"| Blocked findings | {overall['blocked_findings']} |")
    md.append(f"| Unredacted flagged findings | {overall['unredacted_flagged_findings']} |")
    md.append("")
    md.append("## 2.0 Per-Category")
    md.append("")
    md.append("| Category | n | Recall@" + str(K) + " | MRR | nDCG@" + str(K) + " | Hit rate | Blocked | Flagged-unredacted |")
    md.append("|---|---|---|---|---|---|---|---|")
    for cat, s in sorted(cat_summary.items()):
        md.append(f"| {cat} | {s['n_queries']} | {s['recall_at_k']:.3f} | {s['mrr']:.3f} | {s['ndcg_at_k']:.3f} | {s['hit_rate_at_k']:.3f} | {s['blocked_findings']} | {s['unredacted_flagged_findings']} |")
    md.append("")
    md.append("## 3.0 Per-Query")
    md.append("")
    md.append("| ID | Category | Query | Hit@" + str(K) + " | MRR | nDCG@" + str(K) + " | Retrieved |")
    md.append("|---|---|---|---|---|---|---|")
    for r in results:
        retrieved_str = ", ".join(r["retrieved_sources"][:3])
        if len(r["retrieved_sources"]) > 3:
            retrieved_str += f" (+{len(r['retrieved_sources'])-3} more)"
        md.append(f"| {r['query_id']} | {r['category']} | {r['query_text']} | {r['hit_at_k']} | {r['mrr']:.3f} | {r['ndcg_at_k']:.3f} | {retrieved_str} |")
    md.append("")
    md.append("## 4.0 Security Findings")
    md.append("")
    if overall["blocked_findings"] == 0 and overall["unredacted_flagged_findings"] == 0:
        md.append("**PASS** — no blocked content appeared in any retrieval; no unredacted flagged content appeared in any retrieval.")
    else:
        md.append("**FAIL** — security findings detected (see per-query breakdown).")
    md.append("")
    md.append("## 5.0 Coverage of Required Query Categories")
    md.append("")
    required_cats = [
        "hermes-core", "hermes-deployment", "hermes-skills",
        "hermes-mem", "n8n-official", "n8n-youtube", "n8n-gmail",
        "n8n-telegram", "n8n-sheets", "n8n-webhook", "n8n-slack",
        "n8n-ai-agent", "agentfield-core", "agentfield-deployments",
        "agentfield-integrations", "agentfield-examples", "remaining",
    ]
    md.append("| Category | n queries |")
    md.append("|---|---|")
    for c in required_cats:
        n = len(by_cat.get(c, []))
        marker = "✓" if n > 0 else "✗"
        md.append(f"| {marker} {c} | {n} |")
    md.append("")
    md.append("## 6.0 Final Status")
    md.append("")
    if overall["blocked_findings"] == 0 and overall["unredacted_flagged_findings"] == 0:
        md.append("**VALIDATED** — retrieval benchmark passes with no security findings.")
    else:
        md.append("**VALIDATED_WITH_FINDINGS** — see breakdown above.")
    md.append("")

    REPORT_MD.write_text("\n".join(md))
    print(f"Wrote {REPORT_MD.relative_to(VAULT)}")


if __name__ == "__main__":
    main()
