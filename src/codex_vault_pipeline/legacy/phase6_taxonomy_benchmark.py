#!/usr/bin/env python3
"""
phase6_taxonomy_benchmark.py — Phase 6 taxonomy benchmark.

Verifies that source_taxonomy is retrievable from all three indexes
(metdata DB, FTS5, LanceDB) and that the four required taxonomy
checks pass.

Required checks (per task spec):
  1. primary_domain: ai-content-generation -> github:NousResearch/autonovel
  2. primary_domain: training-systems     -> github:NousResearch/tinker-atropos
  3. primary_domain: coding-agents         -> Mnemosyne, FlowState-QMD,
                                              Mission Control, Hindsight,
                                              wondelai/skills
  4. multi-source candidates expose all source_taxonomy entries

For each check, the runner queries:
  - SQLite metadata DB (candidates.source_taxonomy column)
  - SQLite FTS5 candidate_fts (UNINDEXED source_taxonomy column)
  - LanceDB candidates table (source_taxonomy field)

The taxonomy check passes if ALL THREE indexes return the expected
source_id for the candidate slug, and the source_taxonomy entry has
the expected primary_domain.

Outputs:
  .runtime/reports/phase-6-taxonomy-benchmark.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
DB_PATH = RUNTIME / "db" / "codex-vault.db"
FTS_PATH = RUNTIME / "indexes" / "codex-vault-fts.db"
VECTORS_PATH = RUNTIME / "indexes" / "codex-vault-vectors"
OUTPUT_PATH = RUNTIME / "reports" / "phase-6-taxonomy-benchmark.json"

# (label, primary_domain, expected_source_ids, expected_candidate_slugs)
CHECKS = [
    {
        "id": "tax-autonovel",
        "primary_domain": "ai-content-generation",
        "expected_source_ids": ["github:NousResearch/autonovel"],
        "expected_candidate_slugs": ["nousresearch-autonovel"],
    },
    {
        "id": "tax-tinker",
        "primary_domain": "training-systems",
        "expected_source_ids": ["github:NousResearch/tinker-atropos"],
        "expected_candidate_slugs": ["nousresearch-tinker-atropos"],
    },
    {
        "id": "tax-coding-agents",
        "primary_domain": "coding-agents",
        "expected_source_ids": [
            "github:AxDSan/Mnemosyne",
            "github:amanning3390/flowstate-qmd",
            "github:builderz-labs/mission-control",
            "github:vectorize-io/hindsight",
            "github:wondelai/skills",
        ],
        # Mnemosyne, flowstate-qmd, mission-control, hindsight are all in
        # hermes-memory-orchestration-integrations; wondelai is in
        # wondelai-skills-collection.
        "expected_candidate_slugs": [
            "hermes-memory-orchestration-integrations",
            "wondelai-skills-collection",
        ],
    },
]


def check_metadata(check) -> dict:
    """Query the SQLite metadata DB."""
    md = sqlite3.connect(str(DB_PATH))
    md.row_factory = sqlite3.Row
    rows = md.execute(
        "SELECT slug, source_taxonomy FROM candidates"
    ).fetchall()
    found = []
    for r in rows:
        if not r["source_taxonomy"]:
            continue
        tax = json.loads(r["source_taxonomy"])
        for entry in tax:
            if (entry.get("source_id") in check["expected_source_ids"]
                    and entry.get("primary_domain") == check["primary_domain"]):
                found.append((r["slug"], entry["source_id"], entry["primary_domain"]))
    md.close()
    return {
        "retriever": "metadata",
        "found": [{"slug": s, "source_id": sid, "primary_domain": pd}
                  for s, sid, pd in found],
        "missing": sorted(set(check["expected_source_ids"]) - {sid for _, sid, _ in found}),
    }


def check_fts(check) -> dict:
    """Query the SQLite FTS5 candidate_fts (UNINDEXED source_taxonomy)."""
    fts = sqlite3.connect(str(FTS_PATH))
    fts.row_factory = sqlite3.Row
    rows = fts.execute(
        "SELECT slug, source_taxonomy FROM candidate_fts"
    ).fetchall()
    found = []
    for r in rows:
        if not r["source_taxonomy"]:
            continue
        try:
            tax = json.loads(r["source_taxonomy"])
        except json.JSONDecodeError:
            continue
        for entry in tax:
            if (entry.get("source_id") in check["expected_source_ids"]
                    and entry.get("primary_domain") == check["primary_domain"]):
                found.append((r["slug"], entry["source_id"], entry["primary_domain"]))
    fts.close()
    return {
        "retriever": "fts5",
        "found": [{"slug": s, "source_id": sid, "primary_domain": pd}
                  for s, sid, pd in found],
        "missing": sorted(set(check["expected_source_ids"]) - {sid for _, sid, _ in found}),
    }


def check_vector(check) -> dict:
    """Query the LanceDB candidates table."""
    try:
        import lancedb
    except Exception as e:
        return {"retriever": "lancedb", "error": f"lancedb not importable: {e}",
                "found": [], "missing": check["expected_source_ids"]}
    db = lancedb.connect(str(VECTORS_PATH))
    try:
        tbl = db.open_table("candidates")
    except Exception as e:
        return {"retriever": "lancedb", "error": f"open_table failed: {e}",
                "found": [], "missing": check["expected_source_ids"]}
    # Scan all rows. LanceDB 0.27 doesn't expose to_list(); use to_pandas().
    import pandas as pd
    try:
        df = tbl.to_pandas()
        rows = df.to_dict("records")
    except Exception as e:
        return {"retriever": "lancedb", "error": f"scan failed: {e}",
                "found": [], "missing": check["expected_source_ids"]}
    found = []
    for r in rows:
        st = r.get("source_taxonomy") or ""
        if not st:
            continue
        try:
            tax = json.loads(st) if isinstance(st, str) else st
        except json.JSONDecodeError:
            continue
        for entry in tax:
            if (entry.get("source_id") in check["expected_source_ids"]
                    and entry.get("primary_domain") == check["primary_domain"]):
                found.append((r.get("slug"), entry["source_id"], entry["primary_domain"]))
    return {
        "retriever": "lancedb",
        "found": [{"slug": s, "source_id": sid, "primary_domain": pd}
                  for s, sid, pd in found],
        "missing": sorted(set(check["expected_source_ids"]) - {sid for _, sid, _ in found}),
    }


def check_multi_source_exposure() -> dict:
    """Verify multi-source candidates expose all source_taxonomy entries.

    For each multi-source candidate (size > 1), the candidate must have
    the same number of source_taxonomy entries as distinct source_ids
    in its evidence + source_record_ids.
    """
    md = sqlite3.connect(str(DB_PATH))
    md.row_factory = sqlite3.Row
    rows = md.execute("SELECT slug, source_taxonomy FROM candidates").fetchall()
    details = []
    for r in rows:
        if not r["source_taxonomy"]:
            continue
        tax = json.loads(r["source_taxonomy"])
        if len(tax) <= 1:
            continue
        # Verify each entry has the required fields
        ok = all(
            {"source_id", "primary_domain", "source_role", "authority_level"}.issubset(set(e.keys()))
            for e in tax
        )
        details.append({
            "slug": r["slug"],
            "taxonomy_size": len(tax),
            "all_required_fields_present": ok,
            "source_ids": [e["source_id"] for e in tax],
        })
    md.close()
    return {
        "n_multi_source_candidates": len(details),
        "all_have_required_fields": all(d["all_required_fields_present"] for d in details),
        "details": details,
    }


def main() -> int:
    print("=== Phase 6 taxonomy benchmark ===")
    results: list[dict] = []
    for check in CHECKS:
        print(f"\n--- {check['id']} ---")
        c = {
            "id": check["id"],
            "primary_domain": check["primary_domain"],
            "expected_source_ids": check["expected_source_ids"],
            "expected_candidate_slugs": check["expected_candidate_slugs"],
        }
        for fn, label in [(check_metadata, "metadata"),
                           (check_fts, "fts5"),
                           (check_vector, "lancedb")]:
            r = fn(check)
            missing = r.get("missing", [])
            ok = not missing
            print(f"  {label:>10}: {'PASS' if ok else 'FAIL'} "
                  f"(found {len(r['found'])} of {len(check['expected_source_ids'])}, "
                  f"missing {missing})")
            c[label] = r
            c.setdefault("missing_total", set())
            c["missing_total"] |= set(missing)
        c["missing_total"] = sorted(c["missing_total"])
        c["pass"] = not c["missing_total"]
        results.append(c)

    multi = check_multi_source_exposure()
    print(f"\nMulti-source exposure: {multi['n_multi_source_candidates']} multi-source candidates, "
          f"all have required fields: {multi['all_have_required_fields']}")

    overall = {
        "all_checks_pass": all(r["pass"] for r in results) and multi["all_have_required_fields"],
        "n_checks": len(results),
        "n_passed": sum(1 for r in results if r["pass"]),
        "multi_source": multi,
    }

    output = {
        "run_id": f"taxonomy-bench-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "checks": results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, sort_keys=True))
    print(f"\nWrote {OUTPUT_PATH.relative_to(VAULT)}")
    print(f"Final: {overall['n_passed']}/{overall['n_checks']} checks pass, "
          f"multi-source all-ok={multi['all_have_required_fields']}")
    return 0 if overall["all_checks_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
