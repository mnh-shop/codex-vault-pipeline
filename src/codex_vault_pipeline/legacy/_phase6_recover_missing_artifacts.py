"""Phase 6 — Recovery script: rebuild missing artifact + occurrence records.

This script re-walks the raw/ tree of every existing Layer A source
and writes back any artifact or occurrence JSON files that are
referenced by an occurrence but missing on disk. It does NOT touch
any source records, knowledge notes, migration reports, or indexes.

Used to recover from an over-zealous artifact cleanup.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Optional, Tuple

import yaml

try:
    from detect_secrets.core.scan import scan_file as _ds_scan_file
    HAVE_DETECT_SECRETS = True
except ImportError:
    HAVE_DETECT_SECRETS = False
    _ds_scan_file = None


VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
RAW_DIR = VAULT / "raw"
SOURCES_DIR = RUNTIME / "sources"
ARTIFACTS_DIR = RUNTIME / "artifacts"
OCCURRENCES_DIR = RUNTIME / "occurrences"

GENERATOR = "codex-vault/phase-6-recovery"
GENERATOR_VERSION = "1.0.0"

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "target", ".idea", "site-packages",
}

BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".webm", ".mov", ".avi",
    ".so", ".dll", ".dylib", ".o", ".a", ".class", ".pyc", ".pyo",
    ".whl", ".egg", ".parquet", ".arrow", ".feather", ".pickle",
    ".pkl", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".safetensors", ".pt", ".pth", ".onnx",
}

MEDIA_TYPES = {
    ".py": "text/python", ".pyi": "text/python", ".pyx": "text/python",
    ".js": "text/javascript", ".ts": "text/typescript", ".tsx": "text/typescript",
    ".go": "text/go", ".rs": "text/rust", ".java": "text/java",
    ".kt": "text/kotlin", ".kts": "text/kotlin",
    ".c": "text/c", ".h": "text/c", ".cpp": "text/cpp",
    ".cs": "text/csharp", ".rb": "text/ruby", ".php": "text/php",
    ".dart": "text/dart", ".sh": "text/shell", ".bash": "text/shell",
    ".sql": "text/sql", ".vue": "text/vue",
    ".html": "text/html", ".htm": "text/html",
    ".css": "text/css", ".scss": "text/css", ".sass": "text/css", ".less": "text/css",
    ".md": "text/markdown", ".mdx": "text/markdown", ".rst": "text/rst",
    ".yaml": "text/yaml", ".yml": "text/yaml",
    ".json": "text/json", ".jsonc": "text/json",
    ".toml": "text/toml", ".ini": "text/ini", ".cfg": "text/cfg",
    ".xml": "text/xml", ".proto": "text/protobuf",
    ".graphql": "text/graphql", ".gql": "text/graphql",
    ".tf": "text/terraform", ".hcl": "text/hcl", ".nix": "text/nix",
    ".ipynb": "text/jupyter",
    ".txt": "text/plain", ".text": "text/plain",
    ".csv": "text/csv", ".tsv": "text/tsv",
    ".lock": "text/plain", ".sum": "text/plain",
    ".env": "text/env", ".envrc": "text/env",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".webp": "image/webp",
    ".ico": "image/x-icon", ".pdf": "application/pdf",
    ".zip": "application/zip", ".tar": "application/tar",
    ".gz": "application/gzip", ".mp4": "video/mp4",
}

SECRET_BASENAMES = {
    ".env", ".env.local", "secrets.yaml", "secrets.json", "credentials.json",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
}
SECRET_PATTERNS = [
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"\.pfx$", re.IGNORECASE),
    re.compile(r"\.p12$", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
]


def raw_path_for_source(source_id: str) -> Path:
    if not source_id.startswith("github:"):
        return Path()
    path = source_id[len("github:"):]
    parts = path.split("/", 1)
    if len(parts) != 2:
        return Path()
    owner, repo = parts
    candidates = [f"{owner}-{repo}", f"{owner.lower()}-{repo.lower()}", repo, repo.lower()]
    for c in candidates:
        p = RAW_DIR / c
        if p.is_dir():
            return p
    repo_low = repo.lower()
    for p in RAW_DIR.iterdir():
        if p.is_dir() and repo_low in p.name.lower():
            return p
    return Path()


def is_secret(rel: Path) -> bool:
    if rel.name in SECRET_BASENAMES:
        return True
    for pat in SECRET_PATTERNS:
        if pat.search(rel.name):
            return True
    return False


def sha256_file(path: Path, max_bytes: int = 10 * 1024 * 1024) -> Optional[str]:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None


def detect_media_type(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def is_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return True
    return not detect_media_type(path).startswith("text/")


def classify_role(rel: Path) -> str:
    name = rel.name
    if name == "SKILL.md":
        return "agent-skill"
    if name == "SOUL.md":
        return "agent-soul"
    if name in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "requirements.txt"):
        return "configuration"
    if name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                "compose.yml", "compose.yaml", "Chart.yaml"):
        return "deployment-definition"
    if name.endswith((".md", ".rst", ".txt")) and len(rel.parts) == 1:
        return "documentation"
    n = rel.parts[0] if rel.parts else ""
    if n in ("docs", "documentation"):
        return "documentation"
    if n in ("test", "tests", "__tests__"):
        return "reference"
    if n in ("scripts", "tools", "bin"):
        return "executable-script"
    return "unknown"


def scan_secrets(path: Path) -> Tuple[str, int]:
    if not HAVE_DETECT_SECRETS:
        return ("not-scanned", 0)
    try:
        findings = list(_ds_scan_file(str(path)))
        if not findings:
            return ("clean", 0)
        high = any(getattr(f, "confidence", "").lower() == "high" for f in findings)
        if high:
            return ("blocked", len(findings))
        return ("flagged", len(findings))
    except Exception:
        return ("not-scanned", 0)


def walk_repo_files(raw_root: Path) -> list:
    out = []
    for p in raw_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(raw_root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        out.append(p)
    return out


def main() -> int:
    created = 0
    skipped = 0
    errors = 0
    sources_processed = 0
    for src_dir in sorted(SOURCES_DIR.iterdir()):
        if not src_dir.is_dir():
            continue
        src_file = src_dir / "source.v1.yaml"
        if not src_file.exists():
            continue
        try:
            rec = yaml.safe_load(src_file.read_text())
        except Exception:
            continue
        source_id = rec.get("source_id", "")
        raw_root = raw_path_for_source(source_id)
        if not raw_root.is_dir():
            continue
        safe_id = source_id.replace(":", "_").replace("/", "_")
        occ_dir = OCCURRENCES_DIR / safe_id
        # Note: we do NOT require occ_dir. Some sources have only units.

        # Collect all referenced artifact IDs from BOTH occurrences
        # and units under this source. (Units reference more artifacts
        # than occurrences because each file can be split into N
        # units.)
        referenced = set()
        # 1. occurrences
        if occ_dir.is_dir():
            for jf in occ_dir.glob("*.json"):
                try:
                    d = json.load(open(jf))
                    aid = d.get("artifact_id", "")
                    if aid.startswith("sha256:"):
                        referenced.add(aid)
                except Exception:
                    pass
        # 2. units under any unit kind directory that mentions this source
        for unit_kind in (RUNTIME / "units").iterdir() if (RUNTIME / "units").exists() else []:
            unit_source_dir = unit_kind / safe_id
            if not unit_source_dir.is_dir():
                continue
            for jf in unit_source_dir.glob("*.json"):
                try:
                    d = json.load(open(jf))
                    sri = d.get("source_record_ids", [])
                    # Only count units belonging to this source.
                    # source_record_ids holds record_ids (sha256:...), not source_ids.
                    record_id = rec.get("record_id", "")
                    if any(isinstance(s, str) and s == record_id for s in sri):
                        aid = d.get("artifact_id", "")
                        if aid.startswith("sha256:"):
                            referenced.add(aid)
                except Exception:
                    pass

        if not referenced:
            continue

        # Determine missing
        missing_artifacts = set()
        for aid in referenced:
            hex_hash = aid.replace("sha256:", "")
            if not (ARTIFACTS_DIR / f"{hex_hash}.json").exists():
                missing_artifacts.add(aid)

        if not missing_artifacts:
            continue
        for p in walk_repo_files(raw_root):
            rel = p.relative_to(raw_root)
            h = sha256_file(p, max_bytes=100 * 1024 * 1024)
            if h is None:
                continue
            artifact_id = f"sha256:{h}"
            if artifact_id not in missing_artifacts:
                continue
            acquisition_start = datetime.now(timezone.utc).isoformat()
            sec_status, sec_count = scan_secrets(p)
            artifact = {
                "schema": "artifact/v1",
                "schema_version": "1.0.0",
                "record_id": artifact_id,
                "artifact_id": artifact_id,
                "content_sha256": h,
                "media_type": detect_media_type(p),
                "size_bytes": p.stat().st_size,
                "artifact_role": classify_role(rel),
                "parse_status": "valid" if not is_binary(p) else "binary",
                "security_status": sec_status,
                "security_finding_count": sec_count,
                "index_policy": "include" if not is_binary(p) else "metadata-only",
                "created_at": acquisition_start,
                "generator": GENERATOR,
                "generator_version": GENERATOR_VERSION,
                "run_id": "phase-6-recovery",
                "content_hash": f"sha256:{h}",
                "source_path": str(rel),
            }
            art_path = ARTIFACTS_DIR / f"{h}.json"
            art_path.write_text(json.dumps(artifact, indent=2, sort_keys=True))
            created += 1

        sources_processed += 1

    print(f"Recovery complete. sources_processed={sources_processed} artifacts_created={created} errors={errors}")
    return 0


from datetime import datetime, timezone


if __name__ == "__main__":
    sys.exit(main())
