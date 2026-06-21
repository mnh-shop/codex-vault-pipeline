#!/usr/bin/env python3
"""Phase 4 — Domain: deployment-definition extraction.

Walks all artifacts with artifact_role=deployment-definition.
For each:
  - Read content from raw/
  - Extract deployment-relevant fields based on file type (Dockerfile, nix, compose, etc.)
  - Emit 1 domain-record/v1 with record_type=deployment-definition
  - Emit 1 unit/v1 with unit_type=deployment-component
"""
import argparse, hashlib, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR


def extract_dockerfile(text: str) -> dict:
    """Extract Dockerfile structure: FROM, ENV, COPY, RUN, ENTRYPOINT, etc."""
    fields = {
        "kind": "dockerfile",
        "from_images": [],
        "env_vars": [],
        "copy_paths": [],
        "run_commands": [],
        "entrypoint": None,
        "cmd": None,
        "arg_vars": [],
        "expose_ports": [],
        "workdir": None,
        "user": None,
    }
    for line in text.splitlines():
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue
        # Remove inline comments
        line_stripped = re.sub(r"\s+#.*$", "", line_stripped)
        m = re.match(r"FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line_stripped, re.IGNORECASE)
        if m:
            fields["from_images"].append(m.group(1))
            continue
        m = re.match(r"ENV\s+(.+)", line_stripped, re.IGNORECASE)
        if m:
            fields["env_vars"].append(m.group(1).strip())
            continue
        m = re.match(r"ARG\s+(\S+)(?:=.*)?", line_stripped, re.IGNORECASE)
        if m:
            fields["arg_vars"].append(m.group(1))
            continue
        m = re.match(r"COPY\s+(.+)", line_stripped, re.IGNORECASE)
        if m:
            fields["copy_paths"].append(m.group(1).strip())
            continue
        m = re.match(r"RUN\s+(.+)", line_stripped, re.IGNORECASE)
        if m:
            cmd = m.group(1).strip()
            if len(cmd) > 200:
                cmd = cmd[:200] + "..."
            fields["run_commands"].append(cmd)
            continue
        m = re.match(r"ENTRYPOINT\s+(.+)", line_stripped, re.IGNORECASE)
        if m:
            fields["entrypoint"] = m.group(1).strip()
            continue
        m = re.match(r"CMD\s+(.+)", line_stripped, re.IGNORECASE)
        if m:
            fields["cmd"] = m.group(1).strip()
            continue
        m = re.match(r"EXPOSE\s+(.+)", line_stripped, re.IGNORECASE)
        if m:
            fields["expose_ports"].extend(m.group(1).split())
            continue
        m = re.match(r"WORKDIR\s+(\S+)", line_stripped, re.IGNORECASE)
        if m:
            fields["workdir"] = m.group(1)
            continue
        m = re.match(r"USER\s+(\S+)", line_stripped, re.IGNORECASE)
        if m:
            fields["user"] = m.group(1)
            continue
    return fields


def extract_nix_file(text: str, source_path: str) -> dict:
    """Extract nix file structure: description, inputs, pin info."""
    fields = {
        "kind": "nix",
        "nix_kind": "unknown",
        "description": None,
        "inputs": [],
        "outputs": [],
        "pin_version": None,
        "pin_rev": None,
        "pin_hash": None,
        "size_lines": len(text.splitlines()),
    }
    name = source_path.rsplit("/", 1)[-1].lower()
    if "flake" in name:
        fields["nix_kind"] = "flake"
    elif "package" in name:
        fields["nix_kind"] = "package"
    elif "module" in name:
        fields["nix_kind"] = "module"
    elif "checks" in name or "check" in name:
        fields["nix_kind"] = "checks"
    elif "nightly" in name:
        fields["nix_kind"] = "nightly"
    else:
        fields["nix_kind"] = name.replace(".nix", "")

    # Extract description = "..." (top of flake.nix)
    m = re.search(r'description\s*=\s*"([^"]+)"', text)
    if m:
        fields["description"] = m.group(1)

    # Extract inputs.X.url
    for m in re.finditer(r'(\w+)\.url\s*=\s*"([^"]+)"', text):
        fields["inputs"].append({"name": m.group(1), "url": m.group(2)})

    # Extract pinVersion, pinRev, pinHash
    for k in ["pinVersion", "pinRev", "pinHash"]:
        m = re.search(rf'{k}\s*=\s*"([^"]+)"', text)
        if m:
            fields[k.replace("pin", "pin_").lower()] = m.group(1)
    return fields


def extract_generic_deployment(text: str, source_path: str) -> dict:
    """Generic deployment: kind based on extension."""
    sp = source_path.lower()
    if sp.endswith("dockerfile") or "dockerfile" in sp:
        return extract_dockerfile(text)
    if sp.endswith(".nix") or "nix" in sp:
        return extract_nix_file(text, source_path)
    # Fallback
    return {
        "kind": "generic",
        "size_lines": len(text.splitlines()),
        "size_bytes": len(text),
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

    artifacts = {}
    for p in artifacts_dir.glob("*.json"):
        r = json.loads(p.read_text())
        if r.get("artifact_role") == "deployment-definition":
            artifacts[r["content_sha256"]] = r

    occurrences_by_sha = defaultdict(list)
    for p in occurrences_dir.rglob("*.json"):
        o = json.loads(p.read_text())
        occurrences_by_sha[o["content_sha256"]].append(o)

    print(f"Loaded {len(artifacts)} deployment-definition artifacts")

    # Output
    domain_out = runtime / "domain" / "deployment-definition"
    units_out = runtime / "units" / "deployment-component"
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
    kind_counter = Counter()

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

        # Extract
        extracted = extract_generic_deployment(text, source_path)
        kind_counter[extracted.get("kind", "unknown")] += 1

        occ_ids = [o["occurrence_id"] for o in occ_list]
        occ_paths = [o["source_path"] for o in occ_list]
        occ_id = first_occ["occurrence_id"]

        safe_source = source_id.replace(":", "_").replace("/", "_")
        d_dir = domain_out / safe_source
        u_dir = units_out / safe_source
        d_dir.mkdir(parents=True, exist_ok=True)
        u_dir.mkdir(parents=True, exist_ok=True)

        # Domain record
        domain_body = {
            "schema": "domain-record/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-4-deploy-extractor",
            "generator_version": "0.1.0",
            "run_id": args.run_id,
            "content_hash": None,
            "source_record_ids": occ_ids,
            "parser_name": "phase-4-deploy-extractor",
            "parser_version": "0.1.0",
            "record_type": "deployment-definition",
            "deployment": extracted,
            "content_sha256": sha,
            "occurrence_count": len(occ_list),
            "occurrence_ids": occ_ids,
            "source_paths": occ_paths,
            "redacted": is_flagged,
        }
        if is_flagged:
            domain_body["redaction_reason"] = "security_scan.status=flagged"
        d_bytes = json.dumps(domain_body, sort_keys=True, indent=2).encode("utf-8")
        dh = hashlib.sha256(d_bytes).hexdigest()
        domain_body["record_id"] = f"sha256:{dh}"
        domain_body["content_hash"] = f"sha256:{dh}"
        (d_dir / f"{sha}.json").write_text(json.dumps(domain_body, sort_keys=True, indent=2))
        written_d += 1

        # Unit
        if not is_flagged:
            kind = extracted.get("kind", "unknown")
            if kind == "dockerfile":
                summary = (
                    f"Dockerfile: FROM {' '.join(extracted.get('from_images', [])[:3]) or 'unknown'}; "
                    f"ENV {len(extracted.get('env_vars', []))}; "
                    f"COPY {len(extracted.get('copy_paths', []))}; "
                    f"RUN {len(extracted.get('run_commands', []))}; "
                    f"ENTRYPOINT={'yes' if extracted.get('entrypoint') else 'no'}; "
                    f"WORKDIR={extracted.get('workdir', 'unspecified')}"
                )
            elif kind == "nix":
                desc = extracted.get("description") or "none"
                summary = (
                    f"Nix file ({extracted.get('nix_kind', 'unknown')}): "
                    f"description={desc[:50]}; "
                    f"inputs={len(extracted.get('inputs', []))}; "
                    f"pinVersion={extracted.get('pin_version', 'unspecified')}"
                )
            else:
                summary = f"Deployment file ({kind}): {len(text)} bytes"
        else:
            summary = ""
        unit_body = {
            "schema": "unit/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-4-deploy-extractor",
            "generator_version": "0.1.0",
            "run_id": args.run_id,
            "content_hash": None,
            "source_record_ids": [occ_id],
            "parser_name": "phase-4-deploy-extractor",
            "parser_version": "0.1.0",
            "unit_id": f"sha256:{sha}#deployment",
            "artifact_id": f"sha256:{sha}",
            "source_anchor": {
                "section": "deployment",
                "line_start": 1,
                "line_end": 1,
                "json_pointer": "/",
            },
            "unit_type": "deployment-component",
            "title": source_path.rsplit("/", 1)[-1],
            "semantic_text": summary,
            "token_count": len(summary.split()) if summary else 0,
            "fingerprints": {
                "content_sha256": sha,
                "normalized_hash": f"sha256:{hashlib.sha256(extracted.get('kind', '').encode()).hexdigest()}",
                "structural_hash": f"sha256:{hashlib.sha256(json.dumps({k: v for k, v in extracted.items() if k != 'size_lines'}, sort_keys=True).encode()).hexdigest()}",
                "semantic_signature": f"sha256:{hashlib.sha256((extracted.get('kind', '') + '|' + source_path).encode()).hexdigest()}",
            },
            "duplicate_of": None,
            "variant_of": None,
            "derived_from": None,
            "dedup_group": f"sha256:{hashlib.sha256(extracted.get('kind', '').encode()).hexdigest()}",
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

    print()
    print(f"OK: {written_d} domain-record/v1 (deployment-definition) → {domain_out}")
    print(f"OK: {written_u} unit/v1 (deployment-component) → {units_out}")
    print(f"Safe (clean): {safe_count}")
    print(f"Flagged (redacted): {flagged_count}")
    print(f"Excluded (blocked): {excluded_count}")
    print(f"Skipped (invalid): {skipped_invalid}")
    print()
    print("By source:")
    for sid, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {sid}: {n}")
    print()
    print("By kind:")
    for k, n in sorted(kind_counter.items(), key=lambda x: -x[1]):
        print(f"  {k}: {n}")


if __name__ == "__main__":
    main()
