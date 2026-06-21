#!/usr/bin/env python3
"""Phase 4 — Domain: configuration extraction.

Walks all artifacts with artifact_role=configuration.
For each:
  - Read content from raw/
  - Parse JSON or YAML
  - Extract structural metadata (top-level keys, type, not values for flagged)
  - Emit 1 domain-record/v1 with record_type=configuration
  - Emit 1 unit/v1 with unit_type=configuration (or similar)

Honors security:
  - blocked: skip entirely
  - flagged: emit with redacted values (structure only, no values)
"""
import argparse, hashlib, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)


def structural_summary(obj, max_depth: int = 3) -> dict:
    """Build a structural summary without exposing values (for flagged content).

    For dicts: list keys with their value-type tags.
    For lists: show count and item-type if uniform.
    """
    if max_depth <= 0:
        return {"type": "unknown", "truncated": True}
    if isinstance(obj, dict):
        keys = {}
        for k, v in obj.items():
            keys[str(k)] = type_tag(v)
        return {"type": "object", "keys": keys, "key_count": len(keys)}
    if isinstance(obj, list):
        if not obj:
            return {"type": "array", "length": 0}
        item_type = type_tag(obj[0])
        return {"type": "array", "length": len(obj), "item_type": item_type}
    return {"type": type_tag(obj)}


def type_tag(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        if len(v) > 200:
            return "string(long)"
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "unknown"


def safe_keys_summary(obj) -> list:
    """Get top-level keys safely."""
    if isinstance(obj, dict):
        return sorted([str(k) for k in obj.keys()])
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        # for arrays of objects, get union of keys
        keys = set()
        for item in obj[:10]:
            if isinstance(item, dict):
                keys.update(str(k) for k in item.keys())
        return sorted(keys)
    return []


def classify_config(content: str, parsed, source_path: str) -> str:
    """Classify the configuration type based on content/path."""
    sp = source_path.lower()
    if sp.endswith(".yaml") or sp.endswith(".yml"):
        return "yaml-config"
    if sp.endswith("package.json"):
        return "package-manifest"
    if sp.endswith("plugin.yaml") or sp.endswith("plugin.yml") or "plugin" in sp:
        return "plugin-definition"
    if sp.endswith("tsconfig.json") or "tsconfig" in sp:
        return "tsconfig"
    if isinstance(parsed, dict):
        if "meta" in parsed and "nodes" not in parsed:
            return "n8n-meta-stub"
        if "name" in parsed and "version" in parsed and ("tools" in parsed or "commands" in parsed or "skills" in parsed):
            return "plugin-manifest"
        if "name" in parsed and "version" in parsed and "dependencies" in parsed:
            return "package-manifest"
    return "configuration"


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

    # Load artifacts and occurrences
    artifacts = {}
    for p in artifacts_dir.glob("*.json"):
        r = json.loads(p.read_text())
        if r.get("artifact_role") == "configuration":
            artifacts[r["content_sha256"]] = r

    occurrences_by_sha = defaultdict(list)
    for p in occurrences_dir.rglob("*.json"):
        o = json.loads(p.read_text())
        occurrences_by_sha[o["content_sha256"]].append(o)

    print(f"Loaded {len(artifacts)} configuration artifacts")

    # Output
    domain_out = runtime / "domain" / "configuration"
    units_out = runtime / "units" / "configuration"
    domain_out.mkdir(parents=True, exist_ok=True)
    units_out.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    written_d = 0
    written_u = 0
    safe_count = 0
    flagged_count = 0
    excluded_count = 0
    skipped_invalid = 0
    by_source = Counter()
    config_type_counter = Counter()
    parse_format_counter = Counter()

    for sha in sorted(artifacts.keys()):
        art = artifacts[sha]
        occ_list = occurrences_by_sha.get(sha, [])
        if not occ_list:
            skipped_invalid += 1
            continue
        first_occ = occ_list[0]
        source_id = first_occ["source_id"]
        source_path = first_occ["source_path"]
        by_source[source_id] += 1

        # Security
        sec_status = art.get("security_scan", {}).get("status", "not-scanned")
        is_flagged = sec_status == "flagged"
        is_blocked = sec_status == "blocked"
        if is_blocked:
            excluded_count += 1
            continue
        if is_flagged:
            flagged_count += 1
        else:
            safe_count += 1

        # Read content
        content_path = raw_root / source_path
        try:
            text = content_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            skipped_invalid += 1
            continue
        if not text.strip():
            skipped_invalid += 1
            continue

        # Parse
        parsed = None
        parse_format = "unknown"
        if source_path.endswith((".yaml", ".yml")):
            try:
                parsed = yaml.safe_load(text)
                parse_format = "yaml"
            except Exception:
                skipped_invalid += 1
                continue
        else:
            try:
                parsed = json.loads(text)
                parse_format = "json"
            except Exception:
                skipped_invalid += 1
                continue
        parse_format_counter[parse_format] += 1

        # Classify
        cfg_type = classify_config(text, parsed, source_path)
        config_type_counter[cfg_type] += 1

        # Build structure summary
        if is_flagged:
            structure = structural_summary(parsed, max_depth=3)
        else:
            # For safe content, build a richer summary but still don't include actual values
            structure = structural_summary(parsed, max_depth=3)

        top_keys = safe_keys_summary(parsed)
        occ_ids = [o["occurrence_id"] for o in occ_list]
        occ_paths = [o["source_path"] for o in occ_list]
        occ_id = first_occ["occurrence_id"]

        safe_source = source_id.replace(":", "_").replace("/", "_")
        d_dir = domain_out / safe_source
        u_dir = units_out / safe_source
        d_dir.mkdir(parents=True, exist_ok=True)
        u_dir.mkdir(parents=True, exist_ok=True)

        # Build domain record
        domain_body = {
            "schema": "domain-record/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-4-config-extractor",
            "generator_version": "0.1.0",
            "run_id": args.run_id,
            "content_hash": None,
            "source_record_ids": occ_ids,
            "parser_name": "phase-4-config-extractor",
            "parser_version": "0.1.0",
            "record_type": "configuration",
            "configuration": {
                "config_type": cfg_type,
                "parse_format": parse_format,
                "top_level_keys": top_keys,
                "key_count": len(top_keys),
                "structure": structure,
                "size_bytes": len(text),
            },
            "content_sha256": sha,
            "occurrence_count": len(occ_list),
            "occurrence_ids": occ_ids,
            "source_paths": occ_paths,
            "redacted": is_flagged,
        }
        if is_flagged:
            domain_body["redaction_reason"] = "security_scan.status=flagged; values excluded"
        d_bytes = json.dumps(domain_body, sort_keys=True, indent=2).encode("utf-8")
        dh = hashlib.sha256(d_bytes).hexdigest()
        domain_body["record_id"] = f"sha256:{dh}"
        domain_body["content_hash"] = f"sha256:{dh}"
        (d_dir / f"{sha}.json").write_text(json.dumps(domain_body, sort_keys=True, indent=2))
        written_d += 1

        # Build unit
        if not is_flagged:
            summary = (
                f"Configuration file ({cfg_type}, {parse_format}, "
                f"{len(top_keys)} top-level keys, {len(text)} bytes): "
                f"{','.join(top_keys[:10]) or 'no-keys'}"
            )
        else:
            summary = ""
        unit_body = {
            "schema": "unit/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-4-config-extractor",
            "generator_version": "0.1.0",
            "run_id": args.run_id,
            "content_hash": None,
            "source_record_ids": [occ_id],
            "parser_name": "phase-4-config-extractor",
            "parser_version": "0.1.0",
            "unit_id": f"sha256:{sha}#config",
            "artifact_id": f"sha256:{sha}",
            "source_anchor": {
                "section": "config",
                "line_start": 1,
                "line_end": 1,
                "json_pointer": "/",
            },
            "unit_type": "configuration",
            "title": source_path.rsplit("/", 1)[-1],
            "semantic_text": summary,
            "token_count": len(summary.split()) if summary else 0,
            "fingerprints": {
                "content_sha256": sha,
                "normalized_hash": f"sha256:{hashlib.sha256((cfg_type + parse_format).encode()).hexdigest()}",
                "structural_hash": f"sha256:{hashlib.sha256(json.dumps(structure, sort_keys=True).encode()).hexdigest()}",
                "semantic_signature": f"sha256:{hashlib.sha256((cfg_type + '|' + ','.join(top_keys)).encode()).hexdigest()}",
            },
            "duplicate_of": None,
            "variant_of": None,
            "derived_from": None,
            "dedup_group": f"sha256:{hashlib.sha256(cfg_type.encode()).hexdigest()}",
            "redacted": is_flagged,
        }
        if is_flagged:
            unit_body["redaction_reason"] = "security_scan.status=flagged"
        u_bytes = json.dumps(unit_body, sort_keys=True, indent=2).encode("utf-8")
        uh = hashlib.sha256(u_bytes).hexdigest()
        unit_body["record_id"] = f"sha256:{uh}"
        unit_body["content_hash"] = f"sha256:{uh}"
        (u_dir / f"{sha}.json").write_text(json.dumps(unit_body, sort_keys=True, indent=2))
        written_u += 1

        if written_d % 100 == 0:
            print(f"PROGRESS: {written_d} configs processed, {written_d} domain, {written_u} unit records")

    print()
    print(f"OK: {written_d} domain-record/v1 (configuration) → {domain_out}")
    print(f"OK: {written_u} unit/v1 (configuration) → {units_out}")
    print(f"Safe (clean): {safe_count}")
    print(f"Flagged (redacted): {flagged_count}")
    print(f"Excluded (blocked): {excluded_count}")
    print(f"Skipped (invalid): {skipped_invalid}")
    print()
    print("By source:")
    for sid, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {sid}: {n}")
    print()
    print("Config type distribution:")
    for ct, n in sorted(config_type_counter.items(), key=lambda x: -x[1]):
        print(f"  {ct}: {n}")
    print()
    print("Parse format:")
    for fmt, n in parse_format_counter.items():
        print(f"  {fmt}: {n}")


if __name__ == "__main__":
    main()
