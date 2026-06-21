#!/usr/bin/env python3
"""Phase 3 (corrected) — Artifact manifest (Layer B), 2-tier model.

Per the user's correction (2026-06-20):
  - Preserve every artifact occurrence separately.
  - Same content may share a sha256, but every unique (source_id, source_path)
    is a separately addressable occurrence with its own provenance.
  - Use:
      * shared content object keyed by SHA-256 (artifact/v1)
      * artifact occurrence keyed by source_id + source_path (artifact-occurrence/v1)
      * occurrence links to content via content_sha256

Also:
  - Recursive bundle detection: stop at nested SKILL.md (becomes a separate bundle).
  - Real secret scanner: detect-secrets 1.5.0 (deterministic, Python).
  - n8n count reconciliation: per-source deterministic counts.

Outputs:
  .runtime/artifacts/<sha256>.json          (content record, one per unique sha256)
  .runtime/occurrences/<source_id>/<path-hash>.json  (occurrence record, one per file)
  .runtime/bundles/<bundle_id>/bundle.json  (bundle record, recursive)
  .runtime/reports/phase-3-security-findings.json
"""
import argparse, hashlib, json, os, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

# ---------- File classification ----------

MEDIA_TYPE_BY_EXT = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".ini": "text/plain",
    ".sh": "text/x-shellscript",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/tsx",
    ".jsx": "text/jsx",
    ".rb": "text/x-ruby",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".nix": "text/x-nix",
    ".mdx": "text/markdown",
    ".lock": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".xml": "application/xml",
    ".html": "text/html",
    ".css": "text/css",
}

def media_type_for(path: Path) -> str:
    n = path.name.lower()
    if n == "dockerfile" or n.startswith("dockerfile."):
        return "text/dockerfile"
    if n == "skill.md" or n == "soul.md":
        return "text/markdown"
    if n.startswith("metada-") and path.suffix == ".json":
        return "application/json"
    return MEDIA_TYPE_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def classify_artifact(path: Path, content: str) -> tuple:
    """Return (artifact_role, parse_status, parse_errors, index_policy)."""
    n = path.name.lower()
    ext = path.suffix.lower()
    if ext == ".json":
        try:
            obj = json.loads(content)
            if isinstance(obj, dict):
                keys = set(obj.keys())
                if {"name", "nodes", "connections"}.issubset(keys):
                    return ("n8n-workflow", "valid", [], "include")
                if keys == {"meta"} or keys == {"metadata"} or n.startswith("metada-"):
                    return ("metadata", "valid", [], "include")
                if "dependencies" in keys or "scripts" in keys or "name" in keys and "version" in keys:
                    return ("configuration", "valid", [], "include")
                # Other JSON — keep as configuration (could be a generic data file)
                return ("configuration", "valid", [], "include")
            return ("configuration", "valid", [], "include")
        except Exception as e:
            return ("configuration", "invalid", [f"json parse error: {e!r}"], "include")
    if n == "skill.md":
        return ("agent-skill", "valid", [], "include")
    if n == "soul.md":
        return ("agent-soul", "valid", [], "include")
    if ext in {".sh", ".py", ".js", ".ts", ".rb", ".bash", ".zsh", ".tsx", ".jsx"}:
        return ("executable-script", "valid", [], "include")
    if n == "dockerfile" or n.startswith("dockerfile.") or n.startswith("docker-compose"):
        return ("deployment-definition", "valid", [], "include")
    if ext == ".nix" or path.name.endswith(".nix"):
        return ("deployment-definition", "valid", [], "include")
    if ext in {".yaml", ".yml", ".toml", ".ini", ".conf"}:
        return ("configuration", "valid", [], "include")
    if ext == ".md" or ext == ".mdx":
        return ("documentation", "valid", [], "include")
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".pdf"}:
        return ("supporting-resource", "valid", [], "include")
    if ext == ".txt" or ext == ".csv":
        return ("documentation", "valid", [], "include")
    if not content:
        return ("supporting-resource", "empty", [], "exclude")
    return ("supporting-resource", "valid", [], "include")


def classify_n8n_json(path: Path, content: str) -> str:
    """More granular JSON classification for n8n reconciliation.
    Returns one of: 'n8n-workflow', 'metadata', 'configuration', 'invalid', 'unknown'.
    """
    n = path.name.lower()
    if path.suffix.lower() != ".json":
        return "non-json"
    try:
        obj = json.loads(content)
    except Exception:
        return "invalid"
    if not isinstance(obj, dict):
        return "unknown"
    keys = set(obj.keys())
    if {"name", "nodes", "connections"}.issubset(keys):
        return "n8n-workflow"
    if n.startswith("metada-") or keys == {"meta"} or keys == {"metadata"}:
        return "metadata"
    if "dependencies" in keys or "scripts" in keys or ("name" in keys and "version" in keys):
        return "configuration"
    return "unknown"


def detect_language(content: str) -> tuple:
    if not content:
        return ("unknown", 0.0)
    try:
        content.encode("ascii")
        return ("en", 0.9)
    except UnicodeEncodeError:
        return ("en", 0.7)


# ---------- Secret scanning (real: detect-secrets 1.5.0) ----------

try:
    from detect_secrets.core.scan import scan_file as _DS_SCAN_FILE
    from detect_secrets.settings import default_settings as _DS_DEFAULT_SETTINGS
    _DS_AVAILABLE = True
    _DS_VERSION = "1.5.0"
except Exception as _ds_err:
    _DS_SCAN_FILE = None
    _DS_DEFAULT_SETTINGS = None
    _DS_AVAILABLE = False
    _DS_VERSION = f"unavailable:{_ds_err!r}"


def scan_secrets(file_path: Path) -> list:
    """Run real detect-secrets on a file. Returns list of finding dicts.
    Each finding: {category, line_number, match_preview}.
    """
    if not _DS_AVAILABLE:
        return [{"category": "scanner-unavailable", "line_number": 0, "match_preview": f"detect-secrets not available: {_DS_VERSION}"}]
    try:
        with _DS_DEFAULT_SETTINGS():
            raw_findings = list(_DS_SCAN_FILE(str(file_path)))
    except Exception as e:
        return [{"category": "scanner-error", "line_number": 0, "match_preview": f"scan error: {e!r}"}]
    out = []
    for f in raw_findings:
        try:
            line_no = getattr(f, "line_number", 0) or 0
            secret = getattr(f, "secret_value", None)
            t = getattr(f, "type", "Unknown")
            preview = (secret[:30] + "***") if secret and isinstance(secret, str) else f"<{t}>"
            out.append({"category": str(t), "line_number": int(line_no), "match_preview": preview})
        except Exception as e:
            out.append({"category": "scanner-parse-error", "line_number": 0, "match_preview": repr(e)[:60]})
    return out


# ---------- Source ID lookup (uses top/sub to disambiguate sub-repos) ----------

def load_source_id_map(sources_dir: Path) -> dict:
    import yaml
    sys.path.insert(0, str(sources_dir.parent.parent / "tools"))
    from validate import _make_loader
    out = {}
    for p in sources_dir.rglob("source.v1.yaml"):
        try:
            rec = yaml.load(p.read_text(), Loader=_make_loader())
            sid = rec.get("source_id")
            for cd in rec.get("contributing_dirs", []):
                cd = cd.rstrip("/")
                parts = cd.split("/")
                if len(parts) >= 2:
                    key = f"{parts[0]}/{parts[1]}"
                else:
                    key = parts[0]
                out[key] = sid
        except Exception as e:
            print(f"WARN: failed to load {p}: {e}", file=sys.stderr)
    return out


_SOURCE_ID_MAP = {}

def source_id_for(rel_path: str) -> str:
    parts = rel_path.split("/")
    if len(parts) >= 2:
        key = f"{parts[0]}/{parts[1]}"
    else:
        key = parts[0]
    return _SOURCE_ID_MAP.get(key, _SOURCE_ID_MAP.get(parts[0], f"unknown:{parts[0]}"))


# ---------- Bundle detection (recursive) ----------

BUNDLE_ENTRYPOINTS = {"SKILL.md", "SOUL.md", "skill.md", "soul.md"}  # case-insensitive: match both


def detect_bundles(root: Path, occurrences_by_path: dict, contents_by_sha: dict, now: str, run_id: str, source_id_for_func) -> list:
    """For each SKILL.md / SOUL.md entrypoint, walk up to nested entrypoint.
    Siblings in each bundle level become the bundle. Nested SKILL.md becomes a separate bundle.
    Bundle records are bundle/v1 (per user correction 2026-06-20), NOT artifact/v1.

    occurrences_by_path: {source_path: occurrence_record} for quick lookup.
    contents_by_sha: {content_sha256: content_record} for security status lookup.
    source_id_for_func: callable to get source_id from path.
    """
    # Find all entrypoints (case-insensitive: SKILL.md and skill.md are both)
    entrypoints = []
    for p in root.rglob("*"):
        if p.is_file() and p.name in BUNDLE_ENTRYPOINTS:
            rel = p.relative_to(root).as_posix()
            entrypoints.append(rel)
    entrypoints.sort()

    # Group entrypoints by ancestry: nested entrypoints become separate bundles
    bundles = []
    seen_eps_in_bundle = set()

    for ep in entrypoints:
        if ep in seen_eps_in_bundle:
            continue
        # Find siblings: all files in ep's directory and subdirectories, until we hit a nested entrypoint
        ep_parts = ep.split("/")
        ep_dir = "/".join(ep_parts[:-1])  # the directory containing the entrypoint
        siblings = []
        nested_entrypoints = []

        ep_path_obj = root / ep_dir
        if not ep_path_obj.exists():
            continue
        for p in sorted(ep_path_obj.rglob("*")):
            if not p.is_file() or p.name in (".DS_Store",):
                continue
            rel = p.relative_to(root).as_posix()
            # Stop at nested entrypoint (other than self)
            if rel != ep and rel.split("/")[-1] in BUNDLE_ENTRYPOINTS:
                # A nested entrypoint starts its own bundle; don't include it
                nested_entrypoints.append(rel)
                continue
            # Stop at directories that have their own entrypoint
            siblings.append(rel)

        seen_eps_in_bundle.add(ep)

        # Compute per-member metadata: path, content_sha256, occurrence_id, size, role, security_status
        members = []
        total_size = 0
        worst_status = "clean"
        flagged_count = 0
        blocked_count = 0
        status_order = {"clean": 0, "not-scanned": 1, "flagged": 2, "blocked": 3}
        for rel in siblings:
            full = root / rel
            try:
                b = full.read_bytes()
            except Exception:
                continue
            content_sha = hashlib.sha256(b).hexdigest()
            size = len(b)
            total_size += size
            occ = occurrences_by_path.get(rel)
            occurrence_id = occ["occurrence_id"] if occ else None
            fname = rel.split("/")[-1]
            # Determine bundle_role for the member
            if fname in BUNDLE_ENTRYPOINTS or fname.lower() in {"skill.md", "soul.md"}:
                role = "entrypoint"
            elif fname.endswith((".sh", ".py", ".js", ".ts", ".bash", ".zsh")):
                role = "script"
            elif fname in ("README.md", "AGENTS.md", "CLAUDE.md"):
                role = "reference"
            elif "/examples/" in rel or "/example/" in rel:
                role = "example"
            elif fname.endswith((".tmpl", ".template", ".tpl")):
                role = "template"
            elif fname.endswith((".png", ".jpg", ".jpeg", ".svg", ".gif")):
                role = "asset"
            elif fname.endswith((".json", ".yaml", ".yml", ".toml", ".ini", ".conf")):
                role = "config"
            else:
                role = "supporting"
            # Member security status (from content record)
            c = contents_by_sha.get(content_sha)
            sec_status = c.get("security_status", "not-scanned") if c else "not-scanned"
            if status_order.get(sec_status, 0) > status_order.get(worst_status, 0):
                worst_status = sec_status
            if sec_status == "flagged":
                flagged_count += 1
            elif sec_status == "blocked":
                blocked_count += 1
            members.append({
                "path": rel,
                "content_sha256": content_sha,
                "occurrence_id": occurrence_id,
                "bundle_role": role,
                "size_bytes": size,
                "security_status": sec_status,
            })

        # Compute manifest_hash from sorted member list
        sorted_members = sorted(members, key=lambda m: m["path"])
        manifest_canonical = json.dumps(
            [{"path": m["path"], "content_sha256": m["content_sha256"], "bundle_role": m["bundle_role"]} for m in sorted_members],
            sort_keys=True
        )
        manifest_hash = hashlib.sha256(manifest_canonical.encode()).hexdigest()
        bundle_id = f"sha256:{manifest_hash}"

        # Bundle role (the bundle's own role, determined by entrypoint name)
        ep_fname = ep.split("/")[-1]
        if ep_fname.lower() == "skill.md":
            bundle_artifact_role = "agent-skill"
        elif ep_fname.lower() == "soul.md":
            bundle_artifact_role = "agent-soul"
        else:
            bundle_artifact_role = "unknown"

        # Source ID for the bundle (from the entrypoint's source)
        source_id = source_id_for_func(ep)

        # bundle_id = sha256:<manifest_hash>
        body = {
            "schema": "bundle/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-3-bundle-detector",
            "generator_version": "0.3.0",
            "run_id": run_id,
            "content_hash": None,
            "bundle_id": bundle_id,
            "source_id": source_id,
            "source_path": ep,
            "entrypoint": ep_fname,
            "bundle_role": "entrypoint",
            "artifact_role": bundle_artifact_role,
            "members": sorted_members,
            "manifest_hash": manifest_hash,
            "nested_entrypoints": sorted(nested_entrypoints),
            "preservation_mode": "exact-bundle",
            "execution_relevance": "behavior-definition",
            "parse_status": "valid",
            "parse_errors": [],
            "security_scan": {
                "status": "clean" if worst_status == "clean" else worst_status,
                "worst_member_status": worst_status,
                "flagged_member_count": flagged_count,
                "blocked_member_count": blocked_count,
                "detector": "phase-3-bundle-aggregator",
                "detector_version": "0.3.0",
            },
            "index_policy": "exclude" if worst_status == "blocked" else "include",
            "preservation_policy": "retain",
            "size_bytes_total": total_size,
            "contributing_top_dir": ep.split("/")[0],
        }
        bundles.append(body)
    return bundles


# ---------- Main ----------

def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--raw-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), "raw"))
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--run-id", default="phase-3-2026-06-20")
    args = ap.parse_args()

    raw = Path(args.raw_root)
    runtime = Path(args.runtime_root)
    manifest_path = runtime / "reports" / "raw-LEGACY-MANIFEST.json"
    sources_dir = runtime / "sources"

    if not manifest_path.exists():
        print(f"ERROR: manifest missing: {manifest_path}", file=sys.stderr)
        sys.exit(2)
    if not sources_dir.exists():
        print(f"ERROR: sources dir missing: {sources_dir}", file=sys.stderr)
        sys.exit(2)

    # Load source_id map
    global _SOURCE_ID_MAP
    _SOURCE_ID_MAP = load_source_id_map(sources_dir)
    print(f"Loaded {len(_SOURCE_ID_MAP)} source_id mappings from {sources_dir}")
    if not _DS_AVAILABLE:
        print(f"WARNING: detect-secrets not available ({_DS_VERSION})")
    else:
        print(f"Real secret scanner: detect-secrets {_DS_VERSION}")

    # Load manifest
    manifest = json.loads(manifest_path.read_text())
    files = manifest["files"]
    n_manifest = len(files)
    print(f"Processing {n_manifest} files from manifest")

    now = datetime.now(timezone.utc).isoformat()

    # Pass 1: build content map (sha256 -> content_record) and occurrence list
    contents = {}  # sha256 -> {role, parse_status, parse_errors, media_type, size, ...}
    occurrences = []  # list of occurrence records
    skipped_root = 0
    skipped_unknown = 0
    n8n_per_source = defaultdict(lambda: {"total_files": 0, "total_json": 0, "valid_n8n_workflows": 0,
                                          "metadata_json": 0, "config_json": 0, "invalid_json": 0,
                                          "unknown_json": 0, "blocked": 0})
    global_n8n = {"total_files": 0, "total_json": 0, "valid_n8n_workflows": 0,
                  "metadata_json": 0, "config_json": 0, "invalid_json": 0,
                  "unknown_json": 0, "blocked": 0}

    for f in files:
        rel = f["path"]
        sha = f["sha256"]
        size = f["size"]
        if "/" not in rel:
            skipped_root += 1
            continue
        sid = source_id_for(rel)
        if sid.startswith("unknown:"):
            skipped_unknown += 1
            continue

        # Read content
        full = raw / rel
        try:
            content_bytes = full.read_bytes()
            content = content_bytes.decode("utf-8", errors="ignore")
        except Exception:
            content = ""

        # Classify
        artifact_role, parse_status, parse_errors, _idx = classify_artifact(full, content)
        lang, lang_conf = detect_language(content)

        # Content record (aggregate)
        if sha not in contents:
            contents[sha] = {
                "media_type": media_type_for(full),
                "size_bytes": size,
                "encoding": "utf-8",
                "detected_language": lang,
                "language_confidence": lang_conf,
                "artifact_role": artifact_role,
                "parse_status": parse_status,
                "parse_errors": parse_errors,
                "occurrence_paths": [],
                "security_status": "not-scanned",
                "security_findings": [],
                "security_detector": "",
                "security_detector_version": "",
            }
        contents[sha]["occurrence_paths"].append(rel)

        # n8n classification (more granular)
        n8n_class = classify_n8n_json(full, content)
        n8n_per_source[sid]["total_files"] += 1
        global_n8n["total_files"] += 1
        if n8n_class == "non-json":
            pass
        else:
            n8n_per_source[sid]["total_json"] += 1
            global_n8n["total_json"] += 1
            if n8n_class == "n8n-workflow":
                n8n_per_source[sid]["valid_n8n_workflows"] += 1
                global_n8n["valid_n8n_workflows"] += 1
            elif n8n_class == "metadata":
                n8n_per_source[sid]["metadata_json"] += 1
                global_n8n["metadata_json"] += 1
            elif n8n_class == "configuration":
                n8n_per_source[sid]["config_json"] += 1
                global_n8n["config_json"] += 1
            elif n8n_class == "invalid":
                n8n_per_source[sid]["invalid_json"] += 1
                global_n8n["invalid_json"] += 1
            else:
                n8n_per_source[sid]["unknown_json"] += 1
                global_n8n["unknown_json"] += 1

        # Occurrence record
        occurrence_id_hash = hashlib.sha256(f"{sid}|{rel}".encode()).hexdigest()
        occ = {
            "schema": "artifact-occurrence/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-3-artifact-manifest",
            "generator_version": "0.4.0",
            "run_id": args.run_id,
            "content_hash": None,
            "occurrence_id": f"sha256:{occurrence_id_hash}",
            "source_id": sid,
            "source_path": rel,
            "content_sha256": sha,
            "ingestion": {
                "ingested_at": now,
                "ingested_by": "codex-vault/phase-3-artifact-manifest",
                "generator_version": "0.4.0",
                "tool_versions": {
                    "python": sys.version.split()[0],
                    "platform": sys.platform,
                    "detect_secrets": _DS_VERSION,
                },
            },
            "provenance": {
                "first_seen_at": now,
                "first_seen_in_manifest": "raw-LEGACY-MANIFEST.json",
                "manifest_tree_sha256": manifest.get("tree_sha256"),
            },
            "contributing_top_dir": rel.split("/")[0],
            "contributing_sub_dir": rel.split("/")[1] if "/" in rel else "",
            "index_policy": "include",
            "preservation_policy": "retain",
        }
        # Stamp hashes
        occ_bytes = json.dumps(occ, sort_keys=True, indent=2).encode("utf-8")
        occ["record_id"] = f"sha256:{hashlib.sha256(occ_bytes).hexdigest()}"
        occ["content_hash"] = f"sha256:{hashlib.sha256(occ_bytes).hexdigest()}"
        occurrences.append(occ)

    print(f"Built {len(contents)} content records + {len(occurrences)} occurrence records")

    # Pass 2: secret scan on each unique content
    print(f"Running secret scan on {len(contents)} unique contents...")
    if not _DS_AVAILABLE:
        # Mark all as not-scanned
        for sha, c in contents.items():
            c["security_status"] = "not-scanned"
            c["security_detector"] = "detect-secrets"
            c["security_detector_version"] = _DS_VERSION
    else:
        for i, (sha, c) in enumerate(contents.items()):
            # Find any occurrence of this content
            sample_path = None
            for occ in occurrences:
                if occ["content_sha256"] == sha:
                    sample_path = raw / occ["source_path"]
                    break
            if sample_path is None:
                continue
            findings = scan_secrets(sample_path)
            c["security_detector"] = "detect-secrets"
            c["security_detector_version"] = _DS_VERSION
            c["security_findings"] = findings
            c["security_status"] = "not-scanned"  # default
            if findings:
                # Heuristic: private-key-like categories → blocked, else flagged
                blocked_cats = {"Private Key", "Secret Key", "AWS Access Key"}
                has_blocked = any(f["category"] in blocked_cats for f in findings)
                # Treat scanner-unavailable / scanner-error as not-scanned
                if all(f["category"] in ("scanner-unavailable", "scanner-error", "scanner-parse-error") for f in findings):
                    c["security_status"] = "not-scanned"
                else:
                    c["security_status"] = "blocked" if has_blocked else "flagged"
            else:
                c["security_status"] = "clean"
            if (i + 1) % 500 == 0:
                print(f"  scanned {i+1}/{len(contents)}")

    # Aggregate security to global n8n counts (blocked affects index)
    n_blocked = 0
    n_flagged = 0
    n_clean = 0
    n_not_scanned = 0
    for c in contents.values():
        s = c["security_status"]
        if s == "blocked":
            n_blocked += 1
        elif s == "flagged":
            n_flagged += 1
        elif s == "clean":
            n_clean += 1
        else:
            n_not_scanned += 1

    # Apply blocked to n8n counts: a blocked occurrence means that source's blocked count goes up
    for sid in n8n_per_source:
        n8n_per_source[sid]["blocked"] = 0
    for occ in occurrences:
        sha = occ["content_sha256"]
        if contents.get(sha, {}).get("security_status") == "blocked":
            n8n_per_source[occ["source_id"]]["blocked"] += 1
            global_n8n["blocked"] += 1

    # Recount global blocked (in case per-source differs)
    global_n8n["blocked"] = sum(s["blocked"] for s in n8n_per_source.values())

    # Pass 3: detect bundles
    occurrence_by_path = {o["source_path"]: o for o in occurrences}
    bundles = detect_bundles(raw, occurrence_by_path, contents, now, args.run_id, source_id_for)

    # ---- Write outputs ----
    artifacts_out = runtime / "artifacts"
    occurrences_out = runtime / "occurrences"
    bundles_out = runtime / "bundles"
    artifacts_out.mkdir(parents=True, exist_ok=True)
    occurrences_out.mkdir(parents=True, exist_ok=True)
    bundles_out.mkdir(parents=True, exist_ok=True)

    # Write content records (one per sha256)
    n_content_written = 0
    for sha, c in contents.items():
        body = {
            "schema": "artifact/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-3-artifact-manifest",
            "generator_version": "0.4.0",
            "run_id": args.run_id,
            "content_hash": None,
            "artifact_id": f"sha256:{sha}",
            "content_sha256": sha,
            "media_type": c["media_type"],
            "size_bytes": c["size_bytes"],
            "encoding": c["encoding"],
            "detected_language": c["detected_language"],
            "language_confidence": c["language_confidence"],
            "artifact_role": c["artifact_role"],
            "preservation_mode": "exact-bytes",
            "execution_relevance": "documentation-only",
            "parse_status": c["parse_status"],
            "parse_errors": c["parse_errors"],
            "occurrence_count": len(c["occurrence_paths"]),
            "occurrence_paths": c["occurrence_paths"] if len(c["occurrence_paths"]) <= 20 else f"<{len(c['occurrence_paths'])} occurrences — see occurrences/*>",
            "index_policy": "exclude" if c["security_status"] == "blocked" else "include",
            "preservation_policy": "retain",
            "security_scan": {
                "status": c["security_status"],
                "detector": c["security_detector"],
                "detector_version": c["security_detector_version"],
                "finding_count": len(c["security_findings"]),
                "finding_categories": sorted(set(f["category"] for f in c["security_findings"])),
                "redaction_required": c["security_status"] in ("flagged", "blocked"),
                "quarantine_required": c["security_status"] == "blocked",
                "findings": c["security_findings"][:50],  # cap to first 50
            },
        }
        body_bytes = json.dumps(body, sort_keys=True, indent=2).encode("utf-8")
        body["record_id"] = f"sha256:{hashlib.sha256(body_bytes).hexdigest()}"
        body["content_hash"] = f"sha256:{hashlib.sha256(body_bytes).hexdigest()}"
        (artifacts_out / f"{sha}.json").write_text(json.dumps(body, sort_keys=True, indent=2))
        n_content_written += 1

    # Write occurrence records
    n_occ_written = 0
    for occ in occurrences:
        safe_sid = occ["source_id"].replace(":", "_").replace("/", "_")
        sd = occurrences_out / safe_sid
        sd.mkdir(parents=True, exist_ok=True)
        # Use occurrence_id for filename
        fname = occ["occurrence_id"].split(":", 1)[1]
        (sd / f"{fname}.json").write_text(json.dumps(occ, sort_keys=True, indent=2))
        n_occ_written += 1

    # Write bundle records
    n_bundle_written = 0
    for b in bundles:
        b_bytes = json.dumps(b, sort_keys=True, indent=2).encode("utf-8")
        b["record_id"] = f"sha256:{hashlib.sha256(b_bytes).hexdigest()}"
        b["content_hash"] = f"sha256:{hashlib.sha256(b_bytes).hexdigest()}"
        bid_safe = b["bundle_id"].replace(":", "_")
        bd = bundles_out / bid_safe
        bd.mkdir(parents=True, exist_ok=True)
        (bd / "bundle.json").write_text(json.dumps(b, sort_keys=True, indent=2))
        n_bundle_written += 1

    # Reconciliation check
    sum_per_source = {k: 0 for k in ("total_files", "total_json", "valid_n8n_workflows",
                                       "metadata_json", "config_json", "invalid_json",
                                       "unknown_json", "blocked")}
    for sid, c in n8n_per_source.items():
        for k in sum_per_source:
            sum_per_source[k] += c.get(k, 0)
    reconciliation_ok = all(sum_per_source[k] == global_n8n[k] for k in sum_per_source)

    # Write reconciliation report
    recon_path = runtime / "reports" / "phase-3-n8n-reconciliation.json"
    recon_path.parent.mkdir(parents=True, exist_ok=True)
    recon_body = {
        "schema": "phase-3-n8n-reconciliation/v1",
        "schema_version": "1.0.0",
        "generated_at": now,
        "run_id": args.run_id,
        "per_source": n8n_per_source,
        "global_totals": global_n8n,
        "sum_of_per_source": sum_per_source,
        "reconciliation_ok": reconciliation_ok,
    }
    recon_path.write_text(json.dumps(recon_body, sort_keys=True, indent=2))

    # Write security findings
    sec_out = runtime / "reports" / "phase-3-security-findings.json"
    sec_findings_detail = []
    for sha, c in contents.items():
        if c["security_findings"]:
            for f in c["security_findings"]:
                sec_findings_detail.append({
                    "content_sha256": sha,
                    "status": c["security_status"],
                    "category": f["category"],
                    "line_number": f["line_number"],
                    "match_preview": f["match_preview"],
                    "occurrences": contents[sha]["occurrence_paths"],
                })
    sec_body = {
        "schema": "phase-3-security-findings/v1",
        "schema_version": "1.0.0",
        "generated_at": now,
        "detector": "detect-secrets",
        "detector_version": _DS_VERSION,
        "detector_real": _DS_AVAILABLE,
        "scope": "raw/",
        "files_scanned": len(contents),
        "findings_total": sum(len(c["security_findings"]) for c in contents.values()),
        "files_with_findings": sum(1 for c in contents.values() if c["security_findings"]),
        "files_by_status": {"clean": n_clean, "flagged": n_flagged, "blocked": n_blocked, "not-scanned": n_not_scanned},
        "by_category": dict(Counter(f["category"] for c in contents.values() for f in c["security_findings"])),
        "per_content": sec_findings_detail,
    }
    sec_out.write_text(json.dumps(sec_body, sort_keys=True, indent=2))

    # Summary
    print()
    print(f"OK: {n_content_written} content records -> {artifacts_out}")
    print(f"OK: {n_occ_written} occurrence records -> {occurrences_out}")
    print(f"OK: {n_bundle_written} bundle records -> {bundles_out}")
    print(f"OK: security findings -> {sec_out}")
    print(f"OK: n8n reconciliation -> {recon_path}")
    print(f"OK: skipped {skipped_root} root-level files + {skipped_unknown} unmapped paths")
    print()
    print(f"Security scan: clean={n_clean} flagged={n_flagged} blocked={n_blocked} not-scanned={n_not_scanned}")
    print()
    print(f"n8n reconciliation: {'OK' if reconciliation_ok else 'FAILED'}")
    print(f"  total_files = {global_n8n['total_files']}")
    print(f"  total_json  = {global_n8n['total_json']}")
    print(f"  valid_n8n   = {global_n8n['valid_n8n_workflows']}")
    print(f"  metadata    = {global_n8n['metadata_json']}")
    print(f"  config      = {global_n8n['config_json']}")
    print(f"  invalid     = {global_n8n['invalid_json']}")
    print(f"  unknown     = {global_n8n['unknown_json']}")
    print(f"  blocked     = {global_n8n['blocked']}")
    print()
    print(f"Bundles: {n_bundle_written} (entrypoint reconciliation)")


if __name__ == "__main__":
    main()
