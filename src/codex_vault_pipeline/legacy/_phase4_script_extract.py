#!/usr/bin/env python3
"""Phase 4 — Domain: executable-script + supporting-resource extraction.

For each artifact with role in {executable-script, supporting-resource}:
  - Extract minimal metadata
  - Emit 1 unit/v1 (only)
"""
import argparse, hashlib, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR


def extract_script_metadata(text: str, source_path: str) -> dict:
    """Extract script metadata: shebang, size, basic structure."""
    lines = text.splitlines()
    shebang = None
    if lines and lines[0].startswith("#!"):
        shebang = lines[0]
    return {
        "kind": "executable-script",
        "shebang": shebang,
        "line_count": len(lines),
        "size_bytes": len(text),
    }


def extract_supporting_metadata(text: str, source_path: str) -> dict:
    return {
        "kind": "supporting-resource",
        "line_count": len(text.splitlines()),
        "size_bytes": len(text),
        "is_empty": len(text.strip()) == 0,
    }


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--run-id", default="phase-4-2026-06-20")
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    artifacts_dir = runtime / "artifacts"
    occurrences_dir = runtime / "occurrences"
    raw_root = Path(os.environ.get("CODEX_VAULT_ROOT", "")) / "raw"

    target_roles = {"executable-script", "supporting-resource"}
    artifacts = {}
    for p in artifacts_dir.glob("*.json"):
        r = json.loads(p.read_text())
        if r.get("artifact_role") in target_roles:
            artifacts[r["content_sha256"]] = r

    occurrences_by_sha = defaultdict(list)
    for p in occurrences_dir.rglob("*.json"):
        o = json.loads(p.read_text())
        occurrences_by_sha[o["content_sha256"]].append(o)

    print(f"Loaded {len(artifacts)} script/supporting artifacts")

    units_out = runtime / "units" / "script-and-supporting"
    units_out.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    written_u = 0
    by_source = Counter()
    kind_counter = Counter()

    for sha in sorted(artifacts.keys()):
        art = artifacts[sha]
        role = art["artifact_role"]
        occ_list = occurrences_by_sha.get(sha, [])
        if not occ_list:
            continue
        first_occ = occ_list[0]
        source_id = first_occ["source_id"]
        source_path = first_occ["source_path"]
        by_source[source_id] += 1

        content_path = raw_root / source_path
        try:
            text = content_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            text = ""

        # Extract
        if role == "executable-script":
            extracted = extract_script_metadata(text, source_path)
        else:
            extracted = extract_supporting_metadata(text, source_path)
        kind_counter[extracted["kind"]] += 1

        occ_id = first_occ["occurrence_id"]
        safe_source = source_id.replace(":", "_").replace("/", "_")
        u_dir = units_out / safe_source
        u_dir.mkdir(parents=True, exist_ok=True)

        # Determine unit_type
        unit_type = "code-symbol" if role == "executable-script" else "supporting-resource"

        # Build summary
        if role == "executable-script":
            summary = (
                f"Script {source_path.rsplit('/', 1)[-1]}: "
                f"shebang={extracted.get('shebang', 'none') or 'none'}; "
                f"lines={extracted.get('line_count', 0)}; "
                f"size={extracted.get('size_bytes', 0)}"
            )
        else:
            summary = f"Supporting resource: {source_path} (empty={extracted['is_empty']})"

        unit_body = {
            "schema": "unit/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-4-script-extractor",
            "generator_version": "0.1.0",
            "run_id": args.run_id,
            "content_hash": None,
            "source_record_ids": [occ_id],
            "parser_name": "phase-4-script-extractor",
            "parser_version": "0.1.0",
            "unit_id": f"sha256:{sha}#script",
            "artifact_id": f"sha256:{sha}",
            "source_anchor": {
                "section": "script",
                "line_start": 1,
                "line_end": 1,
                "json_pointer": "/",
            },
            "unit_type": unit_type,
            "title": source_path.rsplit("/", 1)[-1],
            "semantic_text": summary,
            "token_count": len(summary.split()),
            "fingerprints": {
                "content_sha256": sha,
                "normalized_hash": f"sha256:{hashlib.sha256(role.encode()).hexdigest()}",
                "structural_hash": f"sha256:{hashlib.sha256(json.dumps(extracted, sort_keys=True).encode()).hexdigest()}",
                "semantic_signature": f"sha256:{hashlib.sha256((role + '|' + source_path).encode()).hexdigest()}",
            },
            "duplicate_of": None,
            "variant_of": None,
            "derived_from": None,
            "dedup_group": f"sha256:{hashlib.sha256(role.encode()).hexdigest()}",
        }
        u_bytes = json.dumps(unit_body, sort_keys=True, indent=2).encode("utf-8")
        uh = hashlib.sha256(u_bytes).hexdigest()
        unit_body["record_id"] = f"sha256:{uh}"
        unit_body["content_hash"] = f"sha256:{uh}"
        (u_dir / f"{sha}.json").write_text(json.dumps(unit_body, sort_keys=True, indent=2))
        written_u += 1

    print()
    print(f"OK: {written_u} unit/v1 (script+supporting) → {units_out}")
    print("By source:")
    for sid, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {sid}: {n}")
    print()
    print("By kind:")
    for k, n in sorted(kind_counter.items(), key=lambda x: -x[1]):
        print(f"  {k}: {n}")


if __name__ == "__main__":
    main()
