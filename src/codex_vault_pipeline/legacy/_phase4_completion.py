#!/usr/bin/env python3
"""Phase 4 — Completion report generator.

Reads all Phase 4 outputs from .runtime/ and generates a summary.
Also verifies raw/ and wiki/ are unchanged.

Usage:
    python3 _phase4_completion.py [--runtime-root PATH]
"""
import argparse, hashlib, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    args = ap.parse_args()

    runtime = Path(args.runtime_root)

    # Load artifacts
    artifacts = {}
    for p in (runtime / "artifacts").glob("*.json"):
        r = json.loads(p.read_text())
        artifacts[r["content_sha256"]] = r

    # Count domain records
    domain_total = 0
    domain_by_type = Counter()
    domain_by_source = defaultdict(Counter)
    for d in (runtime / "domain").rglob("*.json"):
        try:
            r = json.loads(d.read_text())
            if r.get("schema") != "domain-record/v1":
                continue
            domain_total += 1
            rt = r.get("record_type", "unknown")
            domain_by_type[rt] += 1
            sps = r.get("source_paths", [])
            if sps:
                top = sps[0].split("/")[0]
                domain_by_source[rt][top] += 1
        except Exception:
            pass

    # Count unit records
    unit_total = 0
    unit_by_type = Counter()
    unit_orphan_artifact_ids = 0
    known_artifact_ids = set(f"sha256:{k}" for k in artifacts.keys())
    for u in (runtime / "units").rglob("*.json"):
        try:
            r = json.loads(u.read_text())
            if r.get("schema") != "unit/v1":
                continue
            unit_total += 1
            ut = r.get("unit_type", "unknown")
            unit_by_type[ut] += 1
            if r.get("artifact_id") not in known_artifact_ids:
                unit_orphan_artifact_ids += 1
        except Exception:
            pass

    # Per-artifact-role coverage
    art_role = Counter(r.get("artifact_role") for r in artifacts.values())
    coverage = {}
    for role, n in art_role.items():
        if role == "n8n-workflow":
            dom = domain_by_type.get("n8n-workflow", 0)
            uni = unit_by_type.get("n8n-workflow", 0)
        elif role == "agent-skill":
            dom = domain_by_type.get("hermes-skill", 0)
            uni = unit_by_type.get("hermes-skill", 0)
        elif role == "agent-soul":
            dom = domain_by_type.get("hermes-soul", 0)
            uni = unit_by_type.get("hermes-soul", 0)
        elif role == "documentation":
            dom = 0
            uni = unit_by_type.get("doc-section", 0)
        elif role == "configuration":
            dom = domain_by_type.get("configuration", 0)
            uni = unit_by_type.get("configuration", 0)
        elif role == "deployment-definition":
            dom = domain_by_type.get("deployment-definition", 0)
            uni = unit_by_type.get("deployment-component", 0)
        elif role == "executable-script":
            dom = 0
            uni = unit_by_type.get("code-symbol", 0)
        elif role == "supporting-resource":
            dom = 0
            uni = unit_by_type.get("supporting-resource", 0)
        else:
            dom = 0
            uni = 0
        coverage[role] = {"artifacts": n, "domain": dom, "unit": uni}

    # Security status per record_type
    art_sha_to_sec = {sha: r.get("security_scan", {}).get("status", "?") for sha, r in artifacts.items()}
    sec_by_domain = defaultdict(Counter)
    for d in (runtime / "domain").rglob("*.json"):
        try:
            r = json.loads(d.read_text())
            if r.get("schema") != "domain-record/v1":
                continue
            sha = r.get("content_sha256", "")
            sec = art_sha_to_sec.get(sha, "?")
            sec_by_domain[r.get("record_type")][sec] += 1
        except Exception:
            pass

    # Verify raw/ unchanged
    raw_root = Path(os.environ.get("CODEX_VAULT_ROOT", "")) / "raw"
    manifest_path = runtime / "reports" / "raw-LEGACY-MANIFEST.json"
    raw_unchanged = True
    raw_checked = 0
    if manifest_path.exists():
        m = json.loads(manifest_path.read_text())
        for f in m.get("files", []):
            p = raw_root / f["path"]
            if not p.exists():
                raw_unchanged = False
                continue
            expected = f.get("sha256", f.get("content_sha256"))
            actual = hashlib.sha256(p.read_bytes()).hexdigest()
            raw_checked += 1
            if actual != expected:
                raw_unchanged = False

    # Verify wiki/ unchanged
    wiki_root = Path(os.environ.get("CODEX_VAULT_ROOT", "")) / "wiki"
    wiki_md_count = len(list(wiki_root.rglob("*.md"))) if wiki_root.exists() else 0

    now = datetime.now(timezone.utc).isoformat()
    summary = {
        "phase": "Phase 4",
        "operation": "Domain record (Layer D) + Unit (Layer C) extraction",
        "run_id": "phase-4-2026-06-20",
        "generated_at": now,
        "artifacts_loaded": len(artifacts),
        "domain_records_total": domain_total,
        "unit_records_total": unit_total,
        "domain_by_record_type": dict(domain_by_type),
        "unit_by_unit_type": dict(unit_by_type),
        "coverage_per_artifact_role": coverage,
        "security_per_domain": {k: dict(v) for k, v in sec_by_domain.items()},
        "unit_orphan_artifact_ids": unit_orphan_artifact_ids,
        "raw_unchanged": raw_unchanged,
        "raw_checked": raw_checked,
        "wiki_md_count": wiki_md_count,
    }

    body = json.dumps(summary, sort_keys=True, indent=2).encode("utf-8")
    h = hashlib.sha256(body).hexdigest()

    print("=" * 60)
    print(f"Phase 4 completion summary")
    print("=" * 60)
    print(f"Artifacts loaded (Phase 3): {len(artifacts)}")
    print(f"Domain records: {domain_total}")
    print(f"Unit records: {unit_total}")
    print(f"Unit orphan artifact_ids: {unit_orphan_artifact_ids}")
    print()
    print("By record_type:")
    for rt, n in sorted(domain_by_type.items(), key=lambda x: -x[1]):
        print(f"  {rt}: {n}")
    print()
    print("By unit_type:")
    for ut, n in sorted(unit_by_type.items(), key=lambda x: -x[1]):
        print(f"  {ut}: {n}")
    print()
    print("Per-artifact-role coverage:")
    for role, c in sorted(coverage.items()):
        delta_d = c["artifacts"] - c["domain"]
        delta_u = c["artifacts"] - c["unit"]
        print(f"  {role}: artifacts={c['artifacts']}, domain={c['domain']} (Δ={delta_d}), unit={c['unit']} (Δ={delta_u})")
    print()
    print("Security per domain:")
    for rt, sec in sorted(sec_by_domain.items()):
        print(f"  {rt}: {dict(sec)}")
    print()
    print(f"raw/ unchanged: {raw_unchanged} ({raw_checked} files verified)")
    print(f"wiki/ md count: {wiki_md_count}")
    print()
    print(f"Summary content_hash: sha256:{h}")

    # Write summary to a JSON file
    out = runtime / "reports" / "phase-4-summary.json"
    summary["summary_content_hash"] = f"sha256:{h}"
    out.write_text(json.dumps(summary, sort_keys=True, indent=2))
    print(f"Wrote: {out}")


if __name__ == "__main__":
    main()
