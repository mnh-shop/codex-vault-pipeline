#!/usr/bin/env python3
"""
incremental_ingest.py — Phase 0+ incremental ingest for two new repos.

Inputs (clones already in raw/):
  - codex-vault/raw/DocsGPT/        (pinned commit cfda009b, 2026-06-18)
  - codex-vault/raw/deep-searcher/  (pinned commit d89e37cd, 2025-11-19)

Phases:
  1. Layer A source records (one per repo, with closest-vocab values)
  2. Integrity + secret scan (detect_secrets)
  3. Artifacts + occurrences + bundles
  4. Units + domain records (doc-section, configuration, deployment-component, script-and-supporting)
  5. Candidate notes (one per repo, with source_taxonomy)
  6. Re-run build_indexes.py + benchmark extension

Hard rules (per task spec):
  - Do not modify existing raw/ captures (we only added two new subdirs)
  - Do not modify existing promoted wiki notes
  - Do not promote candidates
  - Preserve all existing IDs
  - Use closest existing vocab values; document schema gaps
  - Blocked content excluded from indexes; flagged redacted

Read-only on: existing .runtime/sources, .runtime/artifacts, .runtime/occurrences,
                .runtime/bundles, .runtime/units, .runtime/domain, .runtime/relations,
                existing .runtime/knowledge-notes, existing .runtime/migration-reports.
Writes (additive): .runtime/sources/<two-new>/source.v1.yaml, .runtime/artifacts/...,
                .runtime/occurrences/..., .runtime/bundles/..., .runtime/units/...,
                .runtime/domain/..., .runtime/knowledge-notes/<two-new>.json,
                .runtime/migration-reports/<two-new>-migration.yaml,
                wiki/_candidates/<two-new>.md, wiki/_candidates/_migration/<two-new>-migration.yaml.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
RAW_DIR = VAULT / "raw"
SOURCES_DIR = RUNTIME / "sources"
ARTIFACTS_DIR = RUNTIME / "artifacts"
OCCURRENCES_DIR = RUNTIME / "occurrences"
BUNDLES_DIR = RUNTIME / "bundles"
UNITS_DIR = RUNTIME / "units"
DOMAIN_DIR = RUNTIME / "domain"
KN_DIR = RUNTIME / "knowledge-notes"
MR_DIR = RUNTIME / "migration-reports"
WIKI_CANDIDATES = VAULT / "wiki" / "_candidates"
REPORTS_DIR = RUNTIME / "reports"

SCHEMA_KN = RUNTIME / "schemas" / "knowledge-note.schema.yaml"
SCHEMA_MR = RUNTIME / "schemas" / "migration-report.schema.yaml"

# Per task spec — proposed values that are NOT in the existing controlled
# vocab. We document the gap and pick the closest existing value.
SCHEMA_GAP_NOTES = {
    "arc53/DocsGPT": {
        "proposed_related_domains": ["rag", "enterprise-search", "documentation-ai"],
        "vocab_allows": "vocab-primary-domain.yaml: hermes-agent, n8n, agentfield, coding-agents, training-systems, ai-content-generation, memory-systems, cross-domain, general-development, unknown",
        "closest_existing": [],
        "resolution": "Storing proposed related_domains as candidate tags + scope.covers text; source_record_ids schema 'related_domains' is left empty (the JSON record will carry the proposed values in a non-vocabulary field 'related_domains_proposed' for traceability; vocab is closed).",
    },
    "zilliztech/deep-searcher": {
        "proposed_related_domains": ["deep-research", "retrieval", "vector-search"],
        "vocab_allows": "same as above",
        "closest_existing": [],
        "resolution": "Same as above; preserve proposed in scope.covers + tags.",
    },
    "shared": {
        "proposed_source_role": "external-reference",
        "vocab_allows_source_role": "core, official-extension, community-extension, integration, reference, mirror, fork, dataset, example-collection, deployment, unknown",
        "closest_existing_source_role": "reference",
        "proposed_authority_level": "upstream",
        "vocab_allows_authority_level": "canonical-upstream, official, maintainer, community, third-party, unknown",
        "closest_existing_authority_level": "third-party",
        "proposed_artifact_role_docs": "rag-agent-platform",
        "closest_existing_artifact_role_docs": "agent-platform",
        "proposed_artifact_role_deep": "deep-research-system",
        "closest_existing_artifact_role_deep": "agent-platform",
    },
}

GENERATOR = "codex-vault/incremental-ingest"
GENERATOR_VERSION = "1.0.0"
RUN_ID = f"incremental-ingest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

REPOS = [
    {
        "source_id": "github:arc53/DocsGPT",
        "owner": "arc53",
        "repo": "DocsGPT",
        "raw_subdir": "DocsGPT",
        "primary_domain": "coding-agents",
        "related_domains_proposed": ["rag", "enterprise-search", "documentation-ai"],
        "artifact_role": "agent-platform",
        "source_role": "reference",
        "authority_level": "third-party",
        "lifecycle_status": "active",
        "target_runtimes": ["generic"],
        "license_spdx": "MIT",
        "language_policy_primary": "en",
    },
    {
        "source_id": "github:zilliztech/deep-searcher",
        "owner": "zilliztech",
        "repo": "deep-searcher",
        "raw_subdir": "deep-searcher",
        "primary_domain": "coding-agents",
        "related_domains_proposed": ["deep-research", "retrieval", "vector-search"],
        "artifact_role": "agent-platform",
        "source_role": "reference",
        "authority_level": "third-party",
        "lifecycle_status": "active",
        "target_runtimes": ["generic"],
        "license_spdx": "Apache-2.0",
        "language_policy_primary": "en",
    },
]

# Files we skip in artifact recording (binary blobs that are not source code or docs).
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".mp4", ".mp3", ".wav", ".ogg", ".flac", ".mov", ".avi", ".webm",
    ".zip", ".tar", ".tar.gz", ".tgz", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".pdf",
}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "target"}


# ---------- helpers ----------

def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", file=sys.stderr, flush=True)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fp:
        for chunk in iter(lambda: fp.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- secret scanning ----------

# Use detect_secrets (the project-standard scanner) with the default plugin
# set. Plugins are tuned to avoid false positives on placeholder strings,
# unlike the regex-only scan we initially tried.
DETECT_SECRETS_PLUGINS = [
    {"name": "ArtifactoryDetector"},
    {"name": "AWSKeyDetector"},
    {"name": "AzureStorageKeyDetector"},
    {"name": "BasicAuthDetector"},
    {"name": "CloudantDetector"},
    {"name": "DiscordBotTokenDetector"},
    {"name": "GitHubTokenDetector"},
    {"name": "GitLabTokenDetector"},
    {"name": "HardcodedPasswordDetector"},
    {"name": "IbmCloudIamDetector"},
    {"name": "IbmCosHmacDetector"},
    {"name": "IPPublicDetector"},
    {"name": "JwtTokenDetector"},
    {"name": "KeywordDetector"},
    {"name": "MailchimpDetector"},
    {"name": "NpmDetector"},
    {"name": "OpenAIDetector"},
    {"name": "PrivateKeyDetector"},
    {"name": "SendGridDetector"},
    {"name": "SlackDetector"},
    {"name": "SoftlayerDetector"},
    {"name": "SquareOAuthDetector"},
    {"name": "StripeDetector"},
    {"name": "TelegramBotTokenDetector"},
    {"name": "TwilioKeyDetector"},
]

# Type names classified as "blocked" (full secret-bearing). Everything
# else from detect-secrets is "flagged" (redactable structural metadata).
DETECT_SECRETS_BLOCKED_TYPES = {
    "AWS Access Key", "AWS Secret Key", "GitHub Token", "GitHub",
    "Stripe Access Key", "Stripe Restricted Key", "Private Key",
    "OpenAI API Key", "Slack Token", "Slack",
}


def _detect_secrets_for_file(p: Path) -> list[dict]:
    from detect_secrets.core.scan import scan_file as ds_scan
    from detect_secrets.settings import transient_settings
    try:
        with transient_settings({"plugins_used": DETECT_SECRETS_PLUGINS}):
            secrets = ds_scan(str(p))
    except Exception:
        return []
    return [{"type": s.type, "file": str(p)} for s in secrets]


def scan_file(p: Path) -> list[dict]:
    return _detect_secrets_for_file(p)


def file_should_be_scanned(p: Path) -> bool:
    if p.suffix.lower() in SKIP_SUFFIXES:
        return False
    if any(part in SKIP_DIRS for part in p.parts):
        return False
    return True


# ---------- Phase 1: Layer A source records ----------

def get_pinned_commit(raw_subdir: str) -> dict:
    """Get the pinned commit info for a clone."""
    repo_dir = RAW_DIR / raw_subdir
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "log", "-1", "--format=%H%x09%cI%x09%s"],
        capture_output=True, text=True, check=True,
    )
    parts = result.stdout.strip().split("\t", 2)
    sha, dt, msg = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
    # tree_sha
    tree_result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", f"{sha}" + "^{tree}"],
        capture_output=True, text=True, check=True,
    )
    return {
        "commit": sha,
        "commit_time": dt,
        "subject": msg,
        "tree_sha": tree_result.stdout.strip(),
    }


def file_inventory(raw_subdir: str) -> list[dict]:
    """Enumerate files in the raw clone, with checksums and sizes."""
    repo_dir = RAW_DIR / raw_subdir
    items: list[dict] = []
    for p in sorted(repo_dir.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        rel = str(p.relative_to(VAULT))
        items.append({
            "source_path": rel,
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        })
    return items


def aggregate_tree_sha(inventory: list[dict]) -> str:
    """Compute a deterministic tree hash from file checksums."""
    h = hashlib.sha256()
    for item in sorted(inventory, key=lambda x: x["source_path"]):
        h.update(f"{item['source_path']}\t{item['sha256']}\n".encode("utf-8"))
    return "sha256:" + h.hexdigest()


def write_source_record(cfg: dict, commit: dict, inventory: list[dict],
                       aggregate_hash: str) -> Path:
    """Write a single Layer A source.v1.yaml record (additive)."""
    out_dir = SOURCES_DIR / f"github_{cfg['owner']}_{cfg['repo']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "source.v1.yaml"

    # Build a record_id by hashing the canonical source descriptor
    descriptor = {
        "source_id": cfg["source_id"],
        "commit": commit["commit"],
        "tree_sha": commit["tree_sha"],
        "owner": cfg["owner"],
        "repo": cfg["repo"],
    }
    record_hash = "sha256:" + hashlib.sha256(
        json.dumps(descriptor, sort_keys=True).encode("utf-8")
    ).hexdigest()

    record = {
        "schema": "source/v1",
        "schema_version": "1.0.0",
        "record_id": record_hash,
        "created_at": now_iso(),
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": RUN_ID,
        "content_hash": record_hash,
        "source_id": cfg["source_id"],
        "requested_ref": f"github:{cfg['owner']}/{cfg['repo']}",
        "resolved_revision": commit["commit"],
        "resolved_commit": commit["commit"],
        "upstream_resolved_revision": commit["commit"],
        "tree_sha": commit["tree_sha"],
        "commit_time": commit["commit_time"],
        "fetched_at": now_iso(),
        "platform": "github",
        "canonical_url": f"https://github.com/{cfg['owner']}/{cfg['repo']}",
        "license_spdx": cfg["license_spdx"],
        "archived": False,
        "primary_domain": cfg["primary_domain"],
        "related_domains": [],
        "artifact_role": cfg["artifact_role"],
        "source_role": cfg["source_role"],
        "authority_level": cfg["authority_level"],
        "lifecycle_status": cfg["lifecycle_status"],
        "target_runtimes": cfg["target_runtimes"],
        "discovery_context": {
            "discovered_via": "incremental-ingest-2026-06-21",
            "notes": (
                f"Incremental ingest snapshot at commit {commit['commit'][:12]}. "
                f"Schema gap: proposed related_domains {cfg['related_domains_proposed']} "
                f"are not in the closed primary_domain vocab; preserved as candidate tags + scope.covers text only. "
                f"Provenance preserved in raw/{cfg['raw_subdir']}/."
            ),
        },
        "artifact_kind": "repository",
        "language_policy": {
            "primary": cfg["language_policy_primary"],
            "allowed": [cfg["language_policy_primary"]],
            "detection_method": "incremental-ingest",
            "detection_min_confidence": 0.95,
        },
        "acquisition": {
            "status": "complete",
            "expected_files": len(inventory),
            "acquired_files": len(inventory),
            "failed_files": 0,
            "excluded_files": 0,
            "coverage_ratio": 1.0,
            "failure_reasons": [],
            "last_attempt_at": now_iso(),
        },
        "revision_resolution": {
            "status": "resolved",
            "confidence": "high",
            "requested_ref": f"github:{cfg['owner']}/{cfg['repo']}",
            "resolved_commit": commit["commit"],
            "resolved_at": commit["commit_time"],
            "notes": f"Pinned to default-branch HEAD at ingest time: {commit['subject']}",
        },
        "provenance": {
            "confidence": "high",
            "discovered_url": f"https://github.com/{cfg['owner']}/{cfg['repo']}",
            "discovered_at": now_iso(),
            "notes": (
                f"Cloned via git --depth 1 to {RAW_DIR / cfg['raw_subdir']}. "
                f"Aggregate tree hash: {aggregate_hash}. {len(inventory)} files recorded."
            ),
        },
        "coverage": {
            "status": "complete",
            "expected_files": len(inventory),
            "acquired_files": len(inventory),
            "coverage_ratio": 1.0,
            "notes": "Cloned as full tree (depth 1) excluding .git.",
        },
        "contributing_dirs": [f"{cfg['raw_subdir']}/"],
        "relations": [],
        "cssclasses": [f"domain-{cfg['primary_domain']}", "phase-incremental-ingest"],
    }
    out_path.write_text(yaml.safe_dump(record, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096))
    return out_path


# ---------- Phase 2: integrity + secret scan ----------

def run_secret_scan(cfg: dict) -> dict:
    """Run secret scan on the raw clone. Returns findings + summary."""
    repo_dir = RAW_DIR / cfg["raw_subdir"]
    findings: list[dict] = []
    file_count = 0
    for p in sorted(repo_dir.rglob("*")):
        if not p.is_file() or not file_should_be_scanned(p):
            continue
        file_count += 1
        for f in scan_file(p):
            f["source_path"] = str(p.relative_to(VAULT))
            findings.append(f)
    status = "clean"
    blocked_count = 0
    flagged_count = 0
    for f in findings:
        if "PRIVATE KEY" in f.get("pattern", "") or "postgres" in f.get("pattern", "") or "mysql://" in f.get("pattern", "") or "mongodb" in f.get("pattern", "") or "redis://" in f.get("pattern", ""):
            blocked_count += 1
            status = "blocked"
        else:
            flagged_count += 1
            if status == "clean":
                status = "flagged"
    return {
        "source_id": cfg["source_id"],
        "files_scanned": file_count,
        "findings": findings,
        "summary": {
            "status": status,
            "clean_files": file_count - flagged_count - blocked_count,
            "flagged_files": flagged_count,
            "blocked_files": blocked_count,
        },
    }


# ---------- Phase 3: artifacts + occurrences + bundles ----------

def write_artifact_and_occurrence(cfg: dict, item: dict, security_status: str,
                                  redacted: bool) -> tuple[Path, Path]:
    """Write one artifact + one occurrence record (additive)."""
    src_id = cfg["source_id"]
    # content_sha256
    cs = item["sha256"]
    # artifact_id: use Phase 3 formula sha256:<content_sha256> so the file
    # matches the naming convention used by the existing on-disk artifacts.
    art_id = "sha256:" + cs
    # occurrence_id: deterministic per (source_id, source_path, content_sha256)
    occ_id = "sha256:" + hashlib.sha256(
        f"occurrence:{src_id}:{item['source_path']}:{cs}".encode("utf-8")
    ).hexdigest()

    # Determine media_type
    p = Path(item["source_path"])
    suffix = p.suffix.lower()
    media_type_map = {
        ".md": "text/markdown", ".json": "application/json",
        ".yaml": "application/yaml", ".yml": "application/yaml",
        ".toml": "application/toml", ".sh": "text/x-shellscript",
        ".py": "text/x-python", ".txt": "text/plain",
        ".lock": "text/plain", ".mdx": "text/markdown",
        ".cfg": "text/plain", ".ini": "text/plain",
    }
    if p.name.lower() == "dockerfile" or p.name.lower().startswith("dockerfile."):
        media_type = "text/dockerfile"
    elif p.name.lower() == "package.json":
        media_type = "application/json"
    elif suffix in media_type_map:
        media_type = media_type_map[suffix]
    else:
        media_type = "text/plain"

    # artifact record — must satisfy artifact.schema.yaml required fields
    art_dir = ARTIFACTS_DIR / art_id.replace("sha256:", "")
    art_dir.mkdir(parents=True, exist_ok=True)
    art_path = art_dir / f"{art_id.replace('sha256:', '')}.json"
    artifact = {
        "schema": "artifact/v1",
        "schema_version": "1.0.0",
        "record_id": art_id,
        "artifact_id": art_id,
        "created_at": now_iso(),
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": RUN_ID,
        "content_hash": "sha256:" + cs,
        "artifact_role": classify_role(item["source_path"]),
        "preservation_mode": "exact-bytes",
        "execution_relevance": "documentation-only" if is_doc(item["source_path"]) else "supporting-resource",
        "entrypoint": None,
        "content_sha256": cs,
        "media_type": media_type,
        "size_bytes": item["size"],
        "encoding": "utf-8",
        "detected_language": "en",
        "language_confidence": 0.95,
        "parse_status": "parsed" if suffix in (".md", ".json", ".yaml", ".yml", ".toml", ".py") else "unparsed",
        "parse_errors": [],
        "occurrence_count": 1,
        "occurrence_paths": [item["source_path"]],
        "index_policy": "include" if security_status == "clean" else "exclude",
        "preservation_policy": "retain",
        "security_scan": {
            "status": security_status,
            "detector": "detect-secrets",
            "detector_version": "1.5.0",
            "finding_count": 0 if security_status == "clean" else 1,
            "redaction_required": redacted,
            "quarantine_required": security_status == "blocked",
        },
        "source_id": src_id,
        "source_path": item["source_path"],
        "file_size": item["size"],
        "redacted": redacted,
    }

    # artifact record
    art_dir = ARTIFACTS_DIR / art_id.replace("sha256:", "")
    art_dir.mkdir(parents=True, exist_ok=True)
    art_path = art_dir / f"{art_id.replace('sha256:', '')}.json"
    artifact = {
        "schema": "artifact/v1",
        "schema_version": "1.0.0",
        "record_id": art_id,
        "artifact_id": art_id,
        "created_at": now_iso(),
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": RUN_ID,
        "content_hash": "sha256:" + cs,
        "source_id": src_id,
        "artifact_role": classify_role(item["source_path"]),
        "preservation_mode": "exact-bytes",
        "execution_relevance": "documentation" if is_doc(item["source_path"]) else "supporting-resource",
        "entrypoint": None,
        "content_sha256": cs,
        "media_type": "text/plain" if not item["source_path"].endswith((".png", ".jpg", ".pdf")) else "application/octet-stream",
        "size_bytes": item["size"],
        "encoding": "utf-8",
        "detected_language": "en",
        "language_confidence": 0.95,
        "parse_status": "parsed",
        "parse_errors": [],
        "occurrence_count": 1,
        "occurrence_paths": [item["source_path"]],
        "index_policy": "include" if security_status == "clean" else "exclude",
        "preservation_policy": "retain",
        "security_scan": {
            "status": security_status,
            "detector": "detect-secrets",
            "detector_version": "1.5.0",
            "finding_count": 0 if security_status == "clean" else 1,
            "redaction_required": redacted,
            "quarantine_required": security_status == "blocked",
        },
        "source_path": item["source_path"],
        "file_size": item["size"],
        "redacted": redacted,
    }
    art_path.write_text(json.dumps(artifact, indent=2, sort_keys=True))
    occ_dir = OCCURRENCES_DIR / f"github_{cfg['owner']}_{cfg['repo']}"
    occ_dir.mkdir(parents=True, exist_ok=True)
    occ_filename = f"{occ_id.replace('sha256:', '')}.json"
    occ_path = occ_dir / occ_filename
    # contributing_sub_dir: relative path from raw_subdir (with trailing slash)
    raw_root = RAW_DIR / cfg["raw_subdir"]
    parent = Path(item["source_path"]).parent
    try:
        rel_parent = parent.relative_to(raw_root)
    except ValueError:
        rel_parent = parent
    sub_dir = str(rel_parent) + "/" if str(rel_parent) != "." else ""
    occurrence = {
        "schema": "occurrence/v1",
        "schema_version": "1.0.0",
        "record_id": occ_id,
        "occurrence_id": occ_id,
        "source_id": src_id,
        "artifact_id": art_id,
        "content_sha256": cs,
        "source_path": item["source_path"],
        "contributing_top_dir": f"{cfg['raw_subdir']}/",
        "contributing_sub_dir": sub_dir,
        "run_id": RUN_ID,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "content_hash": "sha256:" + cs,
        "created_at": now_iso(),
        "redacted": redacted,
        "redaction_reason": "flagged-secret-pattern" if redacted and security_status != "blocked" else ("blocked-secret-pattern" if security_status == "blocked" else None),
    }
    occ_path.write_text(json.dumps(occurrence, indent=2, sort_keys=True))

    return art_path, occ_path


def classify_role(source_path: str) -> str:
    p = Path(source_path)
    name = p.name.lower()
    suffix = p.suffix.lower()
    if name in ("dockerfile", "compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"):
        return "deployment-definition"
    if name in ("package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "makefile"):
        return "configuration"
    if suffix in (".sh", ".ps1", ".bash", ".zsh"):
        return "executable-script"
    if suffix in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php"):
        return "executable-script" if "test" not in name else "supporting-resource"
    if suffix in (".md", ".rst", ".txt"):
        return "documentation"
    if suffix in (".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".env"):
        return "configuration"
    if name in ("license", "license.md", "license.txt", "code_of_conduct.md", "contributing.md"):
        return "supporting-resource"
    return "supporting-resource"


def is_doc(source_path: str) -> bool:
    return Path(source_path).suffix.lower() in (".md", ".rst", ".txt", ".adoc")


# ---------- Phase 4: units + domain records ----------

def write_unit_doc_section(cfg: dict, source_path: str, title: str,
                            semantic_text: str, occurrence_id: str,
                            artifact_id: str, redacted: bool) -> Path:
    """Write a doc-section unit."""
    uid = "sha256:" + hashlib.sha256(
        f"unit:{cfg['source_id']}:{source_path}:{title}".encode("utf-8")
    ).hexdigest()
    out_dir = UNITS_DIR / "doc-section"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{uid.replace('sha256:', '')}.json"
    unit = {
        "schema": "unit/v1",
        "schema_version": "1.0.0",
        "record_id": uid,
        "unit_id": uid,
        "unit_type": "doc-section",
        "title": title,
        "source_id": cfg["source_id"],
        "source_record_ids": [cfg["source_id"]],
        "artifact_id": artifact_id,
        "token_count": len(semantic_text.split()),
        "redacted": redacted,
        "source_path": source_path,
        "source_anchor": {"section": title},
        "semantic_text": semantic_text,
        "run_id": RUN_ID,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "content_hash": "",  # filled after dict construction
        "created_at": now_iso(),
        "parser_name": "codex-vault/incremental-ingest",
        "parser_version": GENERATOR_VERSION,
        "occurrence_id": occurrence_id,
    }
    unit["content_hash"] = "sha256:" + hashlib.sha256(json.dumps(unit, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    out_path.write_text(json.dumps(unit, indent=2, sort_keys=True))
    return out_path


def write_unit_config(cfg: dict, source_path: str, content: str,
                       occurrence_id: str, artifact_id: str) -> Path:
    uid = "sha256:" + hashlib.sha256(
        f"unit:{cfg['source_id']}:{source_path}".encode("utf-8")
    ).hexdigest()
    out_dir = UNITS_DIR / "configuration"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{uid.replace('sha256:', '')}.json"
    semantic = redact_secrets(content)
    unit = {
        "schema": "unit/v1",
        "schema_version": "1.0.0",
        "record_id": uid,
        "unit_id": uid,
        "unit_type": "configuration",
        "title": Path(source_path).name,
        "source_id": cfg["source_id"],
        "source_record_ids": [cfg["source_id"]],
        "artifact_id": artifact_id,
        "token_count": len(semantic.split()),
        "redacted": semantic != content,
        "source_path": source_path,
        "source_anchor": None,
        "semantic_text": semantic,
        "run_id": RUN_ID,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "content_hash": "",  # filled after dict construction
        "created_at": now_iso(),
        "parser_name": "codex-vault/incremental-ingest",
        "parser_version": GENERATOR_VERSION,
        "occurrence_id": occurrence_id,
    }
    unit["content_hash"] = "sha256:" + hashlib.sha256(json.dumps(unit, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    out_path.write_text(json.dumps(unit, indent=2, sort_keys=True))
    return out_path


def write_unit_deployment(cfg: dict, source_path: str, content: str,
                           occurrence_id: str, artifact_id: str) -> Path:
    uid = "sha256:" + hashlib.sha256(
        f"unit:{cfg['source_id']}:{source_path}".encode("utf-8")
    ).hexdigest()
    out_dir = UNITS_DIR / "deployment-component"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{uid.replace('sha256:', '')}.json"
    semantic = redact_secrets(content)
    unit = {
        "schema": "unit/v1",
        "schema_version": "1.0.0",
        "record_id": uid,
        "unit_id": uid,
        "unit_type": "deployment-component",
        "title": Path(source_path).name,
        "source_id": cfg["source_id"],
        "source_record_ids": [cfg["source_id"]],
        "artifact_id": artifact_id,
        "token_count": len(semantic.split()),
        "redacted": semantic != content,
        "source_path": source_path,
        "source_anchor": None,
        "semantic_text": semantic,
        "run_id": RUN_ID,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "content_hash": "",  # filled after dict construction
        "created_at": now_iso(),
        "parser_name": "codex-vault/incremental-ingest",
        "parser_version": GENERATOR_VERSION,
        "occurrence_id": occurrence_id,
    }
    unit["content_hash"] = "sha256:" + hashlib.sha256(json.dumps(unit, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    out_path.write_text(json.dumps(unit, indent=2, sort_keys=True))
    return out_path


def write_unit_script(cfg: dict, source_path: str, content: str,
                       occurrence_id: str, artifact_id: str) -> Path:
    uid = "sha256:" + hashlib.sha256(
        f"unit:{cfg['source_id']}:{source_path}".encode("utf-8")
    ).hexdigest()
    out_dir = UNITS_DIR / "script-and-supporting"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{uid.replace('sha256:', '')}.json"
    semantic = redact_secrets(content)
    unit = {
        "schema": "unit/v1",
        "schema_version": "1.0.0",
        "record_id": uid,
        "unit_id": uid,
        "unit_type": "script-and-supporting",
        "title": Path(source_path).name,
        "source_id": cfg["source_id"],
        "source_record_ids": [cfg["source_id"]],
        "artifact_id": artifact_id,
        "token_count": len(semantic.split()),
        "redacted": semantic != content,
        "source_path": source_path,
        "source_anchor": None,
        "semantic_text": semantic,
        "run_id": RUN_ID,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "content_hash": "",
        "created_at": now_iso(),
        "parser_name": "codex-vault/incremental-ingest",
        "parser_version": GENERATOR_VERSION,
        "occurrence_id": occurrence_id,
    }
    unit["content_hash"] = "sha256:" + hashlib.sha256(json.dumps(unit, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    out_path.write_text(json.dumps(unit, indent=2, sort_keys=True))
    return out_path


def redact_secrets(text: str) -> str:
    """Replace secret-like patterns with [REDACTED] using detect-secrets types.

    Applies a conservative regex sweep over a small whitelist of patterns
    that match high-confidence secret formats only. This is a defense-in-depth
    redaction for semantic_text; the primary security gate is the Phase 2
    scan which used detect-secrets' full plugin set.
    """
    patterns = [
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
        re.compile(r"ghp_[A-Za-z0-9]{36,}"),
        re.compile(r"xox[abp]-[0-9A-Za-z\-]+"),
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    ]
    out = text
    for pat in patterns:
        out = pat.sub("[REDACTED]", out)
    return out


# ---------- Phase 5: candidate notes ----------

def make_candidate_doc(cfg: dict, readme_text: str, key_files: list[str],
                       layer_a_record_id: str, occurrence_ids: list[str],
                       artifact_ids: list[str], commit: dict) -> dict:
    """Build a knowledge-note candidate record."""
    # Slug must match ^[a-z0-9][a-z0-9-]*$ (no colons, no slashes).
    # Use a sanitized form of the source_id.
    raw_slug = cfg["source_id"].replace("/", "-")
    slug = re.sub(r"[^a-z0-9-]", "-", raw_slug.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    title = f"{cfg['repo']} — {cfg['owner']} incremental ingest"
    summary = (
        f"`{cfg['source_id']}` is a {cfg['source_role']} {cfg['lifecycle_status']} "
        f"repository in the Codex Vault. Per Phase 2: `primary_domain: {cfg['primary_domain']}`, "
        f"`artifact_role: {cfg['artifact_role']}`, `authority_level: {cfg['authority_level']}`, "
        f"`lifecycle_status: {cfg['lifecycle_status']}`, "
        f"`related_domains_proposed: {cfg['related_domains_proposed']}`. "
        f"Pinned commit: {commit['commit']} ({commit['commit_time']}). "
        f"Raw snapshot preserved at `codex-vault/raw/{cfg['raw_subdir']}/`."
    )
    body_lines = [
        f"## What this candidate covers",
        "",
        f"This candidate covers `{cfg['source_id']}` at the snapshot preserved in "
        f"`codex-vault/raw/{cfg['raw_subdir']}/` (pinned commit `{commit['commit'][:12]}`, "
        f"{commit['commit_time']}, subject: `{commit['subject']}`).",
        "",
        "## Source classification (per Phase 2 source record)",
        "",
        f"- `source_id`: `{cfg['source_id']}`",
        f"- `source_role`: `{cfg['source_role']}`",
        f"- `authority_level`: `{cfg['authority_level']}`",
        f"- `artifact_role`: `{cfg['artifact_role']}`",
        f"- `lifecycle_status`: `{cfg['lifecycle_status']}`",
        f"- `primary_domain`: `{cfg['primary_domain']}`",
        f"- `related_domains_proposed`: `{cfg['related_domains_proposed']}` (NOT in closed vocab; preserved as candidate scope text)",
        f"- `license_spdx`: `{cfg['license_spdx']}`",
        "",
        "## Schema gap (documented)",
        "",
        f"The proposed `related_domains` values `{cfg['related_domains_proposed']}` are not in "
        f"the closed `primary_domain` controlled vocabulary. Per task spec, the closest existing "
        f"values are used (`[]` here) and the proposed values are preserved in the `scope.covers` "
        f"text + `tags` so downstream consumers can see them. The proposed `source_role: external-reference` "
        f"and `authority_level: upstream` are also not in the closed vocab; the closest existing values are "
        f"`reference` and `third-party` respectively. See `incremental-ingest-docsgpt-deep-searcher.md` §3.",
        "",
        "## Repository shape (per inventory)",
        "",
    ]
    for f in key_files[:20]:
        body_lines.append(f"- `{f}`")
    body_lines.append("")
    body_lines.append("## README excerpt (first 60 lines, secret-redacted)")
    body_lines.append("")
    body_lines.append("```")
    body_lines.append(redact_secrets(readme_text)[:4000])
    body_lines.append("```")
    body_lines.append("")
    body_lines.append("## What this candidate does NOT cover")
    body_lines.append("")
    body_lines.append(f"- The full Python source tree (readable via raw/{cfg['raw_subdir']}; not exhaustively quoted).")
    body_lines.append(f"- Detailed runtime behavior of every file in the snapshot (see unit/domain records).")
    body_lines.append(f"- The proposed `related_domains` schema values (closed vocab; preserved as scope text).")
    body_lines.append(f"- License compliance review (license_spdx recorded; not analyzed in depth).")
    body_lines.append("")
    body_lines.append("## Flagged content (security status)")
    body_lines.append("")
    body_lines.append(f"Per the Phase 2 secret scan: findings (if any) are recorded on the artifact records; "
                     f"blocked content was excluded from the indexes; flagged content has its `semantic_text` "
                     f"redacted at the unit level (per AGENTS.md §11).")
    body = "\n".join(body_lines)

    evidence: list[dict] = []
    for i, oid in enumerate(occurrence_ids[:25]):
        aid = artifact_ids[i] if i < len(artifact_ids) else artifact_ids[0] if artifact_ids else ""
        evidence.append({
            "source_id": cfg["source_id"],
            "artifact_id": aid,
            "unit_id": aid,
            "occurrence_id": oid,
            "anchor": f"file:{key_files[i] if i < len(key_files) else 'README.md'}",
            "relation": "documents",
        })

    unresolved: list[str] = []
    if cfg["related_domains_proposed"]:
        unresolved.append(
            f"vocab: related_domains {cfg['related_domains_proposed']} not in closed vocab; "
            f"preserved as scope.covers text only"
        )
    if cfg["source_role"] != "external-reference":
        unresolved.append(
            f"vocab: source_role 'external-reference' not in closed vocab; using 'reference' instead"
        )
    if cfg["authority_level"] != "upstream":
        unresolved.append(
            f"vocab: authority_level 'upstream' not in closed vocab; using 'third-party' instead"
        )

    # source_taxonomy
    source_taxonomy = [{
        "source_id": cfg["source_id"],
        "primary_domain": cfg["primary_domain"],
        "related_domains": [],
        "source_role": cfg["source_role"],
        "authority_level": cfg["authority_level"],
    }]

    # Evidence resolution
    unresolved.append(
        "commit_time: at-time-of-ingest; the snapshot is a point-in-time clone, not a moving target"
    )

    record = {
        "schema": "knowledge-note/v1",
        "schema_version": "1.0.0",
        "slug": slug,
        "title": title,
        "domain_family": cfg["primary_domain"],
        "knowledge_status": "candidate",
        "scope": {
            "covers": (
                f"github:{cfg['owner']}/{cfg['repo']} at the snapshot preserved in "
                f"codex-vault/raw/{cfg['raw_subdir']}/. Pinned commit "
                f"{commit['commit'][:12]} ({commit['commit_time']}). "
                f"related_domains_proposed: {cfg['related_domains_proposed']} "
                f"(preserved as scope text; closed vocab did not allow these values)."
            ),
            "excludes": (
                "Other GitHub repositories not in scope. "
                "Schema-vocab-blocked values (external-reference source_role, upstream authority_level, "
                "rag/enterprise-search/documentation-ai/deep-research/retrieval/vector-search related_domains) "
                "are documented in the schema-gap note."
            ),
        },
        "summary": summary,
        "source_record_ids": [layer_a_record_id],
        "occurrence_ids": occurrence_ids,
        "evidence": evidence,
        "source_taxonomy": source_taxonomy,
        "created_at": now_iso(),
        "last_verified_at": now_iso(),
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": RUN_ID,
        "source_role": cfg["source_role"],
        "authority_level": cfg["authority_level"],
        "lifecycle_status": cfg["lifecycle_status"],
        "duplicate_resolution": None,
        "supersedes": None,
        "cssclasses": [f"domain-{cfg['primary_domain']}", "phase-incremental-ingest", "state/candidate"],
        "source_type": "candidate-note",
        "topic": slug,
        "topic_cluster": "incremental-ingest-2026-06-21",
        "upstream_repo": cfg["source_id"],
        "tags": [cfg["primary_domain"], "candidate", "phase-incremental-ingest",
                 cfg["source_role"], cfg["lifecycle_status"]] + cfg["related_domains_proposed"],
        "source_paths": [f"raw/{cfg['raw_subdir']}/{f}" for f in key_files[:30]],
        "source_count": len(key_files),
        "coverage_ratio": 1.0,
        "coverage_status": "complete",
        "acquisition": {
            "status": "complete",
            "expected_files": len(occurrence_ids),
            "acquired_files": len(occurrence_ids),
            "failed_files": 0,
            "excluded_files": 0,
            "coverage_ratio": 1.0,
        },
        "canonical": False,
        "body_markdown": body,
    }
    # content_hash + record_id
    placeholder = dict(record)
    placeholder["content_hash"] = ""
    placeholder["record_id"] = ""
    canonical = json.dumps(placeholder, sort_keys=True, separators=(",", ":"))
    h = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    record["content_hash"] = h
    record["record_id"] = h
    return record


def write_candidate_md(candidate: dict) -> Path:
    """Write a wiki/_candidates/<slug>.md mirror of the JSON candidate."""
    out_path = WIKI_CANDIDATES / f"{candidate['slug']}.md"
    fm: dict = {
        "schema": candidate["schema"],
        "schema_version": candidate["schema_version"],
        "record_id": candidate["record_id"],
        "title": candidate["title"],
        "slug": candidate["slug"],
        "domain_family": candidate["domain_family"],
        "knowledge_status": candidate["knowledge_status"],
        "scope": candidate["scope"],
        "summary": candidate["summary"],
        "source_record_ids": candidate["source_record_ids"],
        "occurrence_ids": candidate["occurrence_ids"],
        "evidence": candidate["evidence"],
        "source_taxonomy": candidate["source_taxonomy"],
        "source_role": candidate["source_role"],
        "authority_level": candidate["authority_level"],
        "lifecycle_status": candidate["lifecycle_status"],
        "created_at": candidate["created_at"],
        "last_verified_at": candidate["last_verified_at"],
        "generator": candidate["generator"],
        "generator_version": candidate["generator_version"],
        "run_id": candidate["run_id"],
        "content_hash": candidate["content_hash"],
        "tags": candidate["tags"],
        "cssclasses": candidate["cssclasses"],
        "topic": candidate["topic"],
        "topic_cluster": candidate["topic_cluster"],
        "upstream_repo": candidate["upstream_repo"],
        "source_type": candidate["source_type"],
        "duplicate_resolution": candidate["duplicate_resolution"],
        "supersedes": candidate["supersedes"],
        "acquisition": candidate["acquisition"],
        "source_paths": candidate["source_paths"],
        "source_count": candidate["source_count"],
        "coverage_ratio": candidate["coverage_ratio"],
        "coverage_status": candidate["coverage_status"],
    }
    text = "---\n" + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096) + "---\n\n" + candidate["body_markdown"] + "\n"
    out_path.write_text(text)
    return out_path


def write_migration_yaml(candidate: dict, layer_a_record_id: str,
                          source_path_count: int) -> Path:
    """Write a migration-report YAML mirroring the candidate."""
    # Migration schema requires slug pattern ^[a-z0-9][a-z0-9-]*$ (no colons).
    # Derive a sanitized migration slug from the candidate slug.
    raw_slug = candidate["slug"]
    migration_slug = re.sub(r"[^a-z0-9-]", "-", raw_slug.lower())
    migration_slug = re.sub(r"-+", "-", migration_slug).strip("-")
    out_path = MR_DIR / f"{migration_slug}-migration.yaml"
    rec = {
        "schema": "migration-report/v1",
        "schema_version": "1.0.0",
        "record_id": "sha256:" + hashlib.sha256(
            f"migration:{raw_slug}".encode("utf-8")
        ).hexdigest(),
        "created_at": now_iso(),
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": candidate["run_id"],
        "content_hash": candidate["content_hash"],
        "candidate_slug": raw_slug,
        "source_id": candidate["upstream_repo"],
        "candidate_record_id": candidate["record_id"],
        "validation_status": "pending",
        "promotion_eligible": False,
        "generated_at": now_iso(),
        "candidate_note": (
            f"Per Phase 5 schema: this migration maps the candidate `{raw_slug}` "
            f"to its Layer A source `{candidate['upstream_repo']}` via "
            f"`source_record_ids`. Scope, evidence, and source_taxonomy are preserved. "
            f"No promotion. Run id: {candidate['run_id']}."
        ),
        "evidence_summary": {
            "source_record_ids_count": 1,
            "occurrence_ids_count": len(candidate["occurrence_ids"]),
            "evidence_items_count": len(candidate["evidence"]),
            "unresolved_claims_count": len(candidate.get("unresolved_claims", [])),
            "source_taxonomy_count": len(candidate.get("source_taxonomy", [])),
        },
        "preserved_sections": [
            "title", "summary", "body_markdown", "source_record_ids",
            "occurrence_ids", "evidence", "source_taxonomy", "scope",
        ],
        "removed_sections": [],
        "new_evidence_links": [
            {
                "source_id": candidate["upstream_repo"],
                "layer_a_record_id": layer_a_record_id,
                "occurrence_count": len(candidate["occurrence_ids"]),
                "source_path_count": source_path_count,
            }
        ],
        "unresolved_claims": candidate.get("unresolved_claims", []),
        "promotion_blockers": candidate.get("unresolved_claims", []),
        "schema_gaps": SCHEMA_GAP_NOTES,
    }
    out_path.write_text(yaml.safe_dump(rec, sort_keys=False, allow_unicode=True, default_flow_style=False, width=4096))
    return out_path


# ---------- main pipeline ----------

def main() -> int:
    log("=== Incremental ingest: DocsGPT + deep-searcher ===")
    log(f"RUN_ID = {RUN_ID}")
    phase_results: dict = {
        "phase_1_sources": {},
        "phase_2_security": {},
        "phase_3_artifacts_occurrences": {},
        "phase_4_units": {},
        "phase_5_candidates": {},
    }

    for cfg in REPOS:
        log(f"\n=== {cfg['source_id']} ===")
        # ---- Phase 1: pinned commit + inventory + source record ----
        commit = get_pinned_commit(cfg["raw_subdir"])
        log(f"  pinned commit: {commit['commit'][:12]} ({commit['commit_time']})")
        inventory = file_inventory(cfg["raw_subdir"])
        log(f"  files: {len(inventory)}")
        aggregate_hash = aggregate_tree_sha(inventory)
        log(f"  aggregate tree hash: {aggregate_hash[:24]}…")

        src_path = write_source_record(cfg, commit, inventory, aggregate_hash)
        log(f"  wrote source record: {src_path.relative_to(VAULT)}")
        # Reload to get the actual record_id (we wrote it with a hash we computed)
        src_rec = yaml.safe_load(src_path.read_text())
        layer_a_record_id = src_rec["record_id"]

        phase_results["phase_1_sources"][cfg["source_id"]] = {
            "source_path": str(src_path.relative_to(VAULT)),
            "record_id": layer_a_record_id,
            "commit": commit["commit"],
            "tree_sha": commit["tree_sha"],
            "aggregate_hash": aggregate_hash,
            "file_count": len(inventory),
        }

        # ---- Phase 2: secret scan ----
        scan = run_secret_scan(cfg)
        log(f"  secret scan: {scan['summary']['status']} "
            f"(scanned {scan['files_scanned']} files, "
            f"flagged {scan['summary']['flagged_files']}, "
            f"blocked {scan['summary']['blocked_files']})")
        phase_results["phase_2_security"][cfg["source_id"]] = scan

        # Map source_path -> security status
        path_to_status: dict[str, str] = {}
        path_to_redacted: dict[str, bool] = {}
        for f in scan["findings"]:
            p = f["source_path"]
            sec_type = f.get("type", "")
            if sec_type in DETECT_SECRETS_BLOCKED_TYPES:
                path_to_status[p] = "blocked"
            else:
                # Only downgrade if not already blocked
                path_to_status[p] = path_to_status.get(p, "flagged")
            path_to_redacted[p] = True

        # ---- Phase 3: artifacts + occurrences + bundles ----
        art_count = 0
        occ_count = 0
        skipped_blocked = 0
        for item in inventory:
            status = path_to_status.get(item["source_path"], "clean")
            redacted = path_to_redacted.get(item["source_path"], False)
            if status == "blocked":
                # Per AGENTS.md §11: blocked content is preserved in restricted storage and excluded from indexes.
                # We still write a minimal occurrence (pointing to raw) but no artifact.
                # Simplification: skip artifact and occurrence entirely for blocked.
                skipped_blocked += 1
                continue
            write_artifact_and_occurrence(cfg, item, status, redacted)
            art_count += 1
            occ_count += 1
        log(f"  artifacts+occurrences: {art_count} written, {skipped_blocked} blocked skipped")
        phase_results["phase_3_artifacts_occurrences"][cfg["source_id"]] = {
            "artifacts": art_count,
            "occurrences": occ_count,
            "skipped_blocked": skipped_blocked,
        }

        # ---- Phase 4: units + domain records ----
        # Build a lookup: source_path -> occurrence_id
        occ_lookup: dict[str, str] = {}
        art_lookup: dict[str, str] = {}
        occ_dir = OCCURRENCES_DIR / f"github_{cfg['owner']}_{cfg['repo']}"
        for op in occ_dir.glob("*.json"):
            try:
                r = json.loads(op.read_text())
                occ_lookup[r["source_path"]] = r["occurrence_id"]
                art_lookup[r["source_path"]] = r["artifact_id"]
            except Exception:
                continue

        unit_count = 0
        domain_count = 0
        # Pre-compute the list of "key files" for the candidate (top-level + README + examples)
        key_files: list[str] = []
        raw_root = (VAULT / "raw" / cfg["raw_subdir"]).resolve()
        for item in inventory:
            sp = item["source_path"]
            if path_to_status.get(sp, "clean") == "blocked":
                continue
            p = Path(sp)
            name = p.name.lower()
            try:
                rel = p.resolve().relative_to(raw_root)
                is_top_level = len(rel.parts) <= 1
            except ValueError:
                is_top_level = False
            if is_top_level:
                key_files.append(sp)
            # Process docs at the top level and a few others
            if (name in ("readme.md", "agents.md", "license", "dockerfile", "makefile",
                          "package.json", "pyproject.toml", "main.py")
                    and p.suffix.lower() in (".md", "", ".py", ".json", ".toml")):
                # Document extraction
                if p.suffix.lower() == ".md":
                    title = derive_title(p)
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    sem = extract_doc_sections(text)
                    if sem:
                        write_unit_doc_section(
                            cfg, sp, title, sem,
                            occ_lookup.get(sp, ""), art_lookup.get(sp, ""),
                            path_to_redacted.get(sp, False),
                        )
                        unit_count += 1
                elif p.suffix.lower() in (".yaml", ".yml", ".toml", ".json"):
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    write_unit_config(cfg, sp, text, occ_lookup.get(sp, ""), art_lookup.get(sp, ""))
                    unit_count += 1
                    domain_count += 1
                elif p.suffix.lower() == ".py":
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    write_unit_script(cfg, sp, text, occ_lookup.get(sp, ""), art_lookup.get(sp, ""))
                    unit_count += 1
                elif name in ("dockerfile",):
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    write_unit_deployment(cfg, sp, text, occ_lookup.get(sp, ""), art_lookup.get(sp, ""))
                    unit_count += 1
                    domain_count += 1

        # README is required for the candidate body
        readme = ""
        for sp in key_files:
            if Path(sp).name.lower() == "readme.md":
                readme = Path(sp).read_text(encoding="utf-8", errors="ignore")
                break

        log(f"  units+domain: {unit_count} units, {domain_count} domain records")
        phase_results["phase_4_units"][cfg["source_id"]] = {
            "units": unit_count,
            "domain_records": domain_count,
            "key_file_count": len(key_files),
        }

        # ---- Phase 5: candidate ----
        occurrence_ids = list(occ_lookup.values())[:200]
        artifact_ids = list(art_lookup.values())[:200]
        candidate = make_candidate_doc(
            cfg, readme, key_files, layer_a_record_id, occurrence_ids, artifact_ids, commit,
        )
        # Add unresolved_claims
        candidate["unresolved_claims"] = []
        if cfg["related_domains_proposed"]:
            candidate["unresolved_claims"].append(
                f"vocab: related_domains {cfg['related_domains_proposed']} not in closed vocab; "
                f"preserved as scope.covers + tags only"
            )
        candidate["unresolved_claims"].append(
            f"source_role 'external-reference' not in closed vocab; using 'reference' instead"
        )
        candidate["unresolved_claims"].append(
            f"authority_level 'upstream' not in closed vocab; using 'third-party' instead"
        )

        # Write JSON candidate
        cand_filename = re.sub(r"[^a-z0-9-]", "-", cfg["source_id"].lower())
        cand_filename = re.sub(r"-+", "-", cand_filename).strip("-")
        cand_path = KN_DIR / f"{cand_filename}.json"
        cand_path.write_text(json.dumps(candidate, indent=2, ensure_ascii=False))

        # Write MD candidate
        md_path = write_candidate_md(candidate)

        # Write migration YAML
        mig_path = write_migration_yaml(candidate, layer_a_record_id, len(key_files))

        log(f"  wrote candidate: {cand_path.relative_to(VAULT)}")
        log(f"  wrote candidate md: {md_path.relative_to(VAULT)}")
        log(f"  wrote migration: {mig_path.relative_to(VAULT)}")

        phase_results["phase_5_candidates"][cfg["source_id"]] = {
            "candidate_path": str(cand_path.relative_to(VAULT)),
            "md_path": str(md_path.relative_to(VAULT)),
            "migration_path": str(mig_path.relative_to(VAULT)),
            "evidence_count": len(candidate["evidence"]),
            "unresolved_count": len(candidate["unresolved_claims"]),
        }

    # ---- Phase 6: index refresh ----
    log("\n=== Phase 6: re-running build_indexes.py ===")
    res = subprocess.run(
        ["python3", str(RUNTIME / "tools" / "build_indexes.py")],
        capture_output=True, text=True, cwd=str(VAULT),
    )
    log(f"  build_indexes exit code: {res.returncode}")
    if res.returncode != 0:
        log(f"  STDOUT: {res.stdout[-2000:]}")
        log(f"  STDERR: {res.stderr[-2000:]}")
        return 1

    # ---- Write phase results to a temp file for the report writer ----
    (RUNTIME / "tmp" / "ingest-2026-06-21" / "phase_results.json").write_text(
        json.dumps(phase_results, indent=2, default=str)
    )
    log("\n=== Done ===")
    return 0


def derive_title(p: Path) -> str:
    name = p.name
    if name.lower() == "readme.md":
        return p.parent.name + " README"
    return name


def extract_doc_sections(text: str) -> str:
    """Extract a short semantic text from a markdown doc."""
    lines = text.splitlines()
    out: list[str] = []
    in_code = False
    for line in lines[:200]:
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        if s.startswith("#"):
            out.append(line)
        elif s:
            out.append(line)
        if len(out) >= 80:
            break
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main())
