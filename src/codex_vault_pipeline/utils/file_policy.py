"""Shared file policy utilities: media type inference, binary detection,
secret scanning, and source-role classification.

Extracted from duplicated definitions in legacy phase-6 scripts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Set, Tuple

# ---------------------------------------------------------------------------
# Media type inference by file extension (consolidated from both phase-6
# scripts; uses the more complete mapping from _phase6_ingest_deep_research_osint)
# ---------------------------------------------------------------------------

MEDIA_TYPES: Dict[str, str] = {
    ".py": "text/python", ".pyi": "text/python", ".pyx": "text/python",
    ".js": "text/javascript", ".mjs": "text/javascript", ".cjs": "text/javascript",
    ".jsx": "text/javascript", ".ts": "text/typescript", ".tsx": "text/typescript",
    ".go": "text/go", ".rs": "text/rust", ".java": "text/java",
    ".kt": "text/kotlin", ".kts": "text/kotlin", ".swift": "text/swift",
    ".c": "text/c", ".h": "text/c", ".cpp": "text/cpp", ".cc": "text/cpp",
    ".cxx": "text/cpp", ".hpp": "text/cpp",
    ".cs": "text/csharp", ".rb": "text/ruby", ".php": "text/php",
    ".pl": "text/perl", ".lua": "text/lua", ".r": "text/r",
    ".scala": "text/scala", ".ex": "text/elixir", ".exs": "text/elixir",
    ".erl": "text/erlang", ".hs": "text/haskell",
    ".dart": "text/dart", ".sh": "text/shell", ".bash": "text/shell",
    ".zsh": "text/shell", ".fish": "text/shell",
    ".ps1": "text/powershell", ".bat": "text/batch", ".cmd": "text/batch",
    ".sql": "text/sql", ".vue": "text/vue", ".svelte": "text/svelte",
    ".html": "text/html", ".htm": "text/html",
    ".css": "text/css", ".scss": "text/css", ".sass": "text/css",
    ".less": "text/css",
    ".md": "text/markdown", ".mdx": "text/markdown", ".rst": "text/rst",
    ".yaml": "text/yaml", ".yml": "text/yaml",
    ".json": "text/json", ".json5": "text/json", ".jsonc": "text/json",
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
    ".gz": "application/gzip", ".bz2": "application/bzip2",
    ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".mp4": "video/mp4", ".webm": "video/webm",
}

# Binary extensions that are always treated as binary
BINARY_EXTS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".mp3", ".wav", ".ogg", ".flac", ".mp4", ".webm", ".mov", ".avi",
    ".so", ".dll", ".dylib", ".o", ".a", ".class", ".pyc", ".pyo",
    ".whl", ".egg", ".parquet", ".arrow", ".feather", ".pickle",
    ".pkl", ".bin", ".dat", ".db", ".sqlite", ".sqlite3",
    ".safetensors", ".pt", ".pth", ".onnx",
}

# ---------------------------------------------------------------------------
# detect-secrets integration (optional runtime dep)
# ---------------------------------------------------------------------------

HAVE_DETECT_SECRETS: bool = False
_ds_scan_file = None

try:
    from detect_secrets.core.scan import scan_file as _ds_scan_file_impl
    _ds_scan_file = _ds_scan_file_impl
    HAVE_DETECT_SECRETS = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def detect_media_type(path: Path) -> str:
    """Best-effort media type from file extension."""
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def is_binary(path: Path) -> bool:
    """A file is binary if its extension is in BINARY_EXTS or it doesn't
    have a text-based media type."""
    ext = path.suffix.lower()
    if ext in BINARY_EXTS:
        return True
    mt = detect_media_type(path)
    return not mt.startswith("text/")


def scan_secrets(path: Path) -> Tuple[str, int]:
    """Run detect-secrets on a single file.

    Returns (status, finding_count) where status is one of:
    'clean', 'flagged', 'blocked', 'not-scanned'.
    """
    if not HAVE_DETECT_SECRETS or _ds_scan_file is None:
        return ("not-scanned", 0)
    try:
        findings = list(_ds_scan_file(str(path)))
        if not findings:
            return ("clean", 0)
        # Heuristic: any high-confidence finding is blocked; else flagged
        high_conf = any(
            getattr(f, "confidence", "").lower() in ("high",)
            for f in findings
        )
        if high_conf:
            return ("blocked", len(findings))
        return ("flagged", len(findings))
    except Exception:
        return ("not-scanned", 0)


def classify_role(rel: Path) -> str:
    """Classify a source-relative path into a high-level role label.

    Used by ingest scripts to tag artifacts with their functional role
    (documentation, configuration, deployment-definition, etc.).
    """
    name = rel.name
    if name == "SKILL.md":
        return "agent-skill"
    if name == "SOUL.md":
        return "agent-soul"
    if name in (
        "pyproject.toml", "package.json", "Cargo.toml",
        "go.mod", "requirements.txt",
    ):
        return "configuration"
    if name in (
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "compose.yml", "compose.yaml", "Chart.yaml",
    ):
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
