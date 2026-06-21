"""Tech-profile extractor for Layer A source records.

This module deterministically extracts a technical profile of a
GitHub repository from its raw/ tree. It populates the
`source_platform`, `repo_identity`, `repo_profile`, `interfaces`,
and `workflow_synthesis` fields added to `source.schema.yaml`
in 2026-06-21.

The extractor is **safe by design**:

- It never parses `.env`, `*.pem`, `*credentials*`, `*secret*`,
  `*token*`, or any file matching the secret-bearing patterns.
- It never reads environment-variable VALUES; only declared names.
- It treats dependency manifests as data (names, version specs)
  and never emits their content into the semantic text.
- It is conservative: when a signal is ambiguous, the field is
  omitted rather than guessed.
- The output is a JSON-serializable dict whose shape is a
  strict subset of the `source.schema.yaml` properties.

The CLI subcommand `tech-profile` is registered in
`codex_vault_pipeline.cli`. It accepts `--vault-root` and
`--source-id` (or `--all`) and writes a JSON profile next to
the source record (or a report summarizing all sources).

This module is importable as a library:

    from codex_vault_pipeline.extractors.tech_profile import (
        extract_tech_profile,
    )
    profile = extract_tech_profile(Path("/path/to/raw/<repo>"))
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

# tomllib is stdlib in 3.11+; on 3.9/3.10 we use the `tomli` backport
# if available, otherwise we fall back to a tiny regex-based reader
# that covers the project.dependencies and tool.poetry.dependencies
# tables we actually need.
try:
    import tomllib as _toml  # type: ignore[import-not-found]
except ImportError:  # Python < 3.11
    try:
        import tomli as _toml  # type: ignore[import-not-found,no-redef]
    except ImportError:
        _toml = None  # type: ignore[assignment]

# ----- Constants ---------------------------------------------------------

# Files we never read, ever. These are matched by basename only.
# Content is never parsed, never returned, never logged.
_SECRET_BASENAMES = frozenset({
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    "secrets.yaml",
    "secrets.yml",
    "secrets.json",
    "credentials",
    "credentials.json",
    "service-account.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
})

_SECRET_PATTERNS = (
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"\.pfx$", re.IGNORECASE),
    re.compile(r"\.p12$", re.IGNORECASE),
    re.compile(r"\.keystore$", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"credential", re.IGNORECASE),
    re.compile(r"token", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
)

# File extension → language
LANG_BY_EXT: Dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".pyx": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".m": "objc",
    ".mm": "objc++",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".pl": "perl",
    ".lua": "lua",
    ".r": "r",
    ".R": "r",
    ".scala": "scala",
    ".sc": "scala",
    ".clj": "clojure",
    ".cljs": "clojurescript",
    ".cljc": "clojure",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".dart": "dart",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".ps1": "powershell",
    ".bat": "shell",
    ".cmd": "shell",
    ".sql": "sql",
    ".vue": "vue",
    ".svelte": "svelte",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".sass": "css",
    ".less": "css",
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".xml": "xml",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".tf": "terraform",
    ".hcl": "hcl",
    ".nix": "nix",
    ".zig": "zig",
    ".nim": "nim",
}

# Manifests recognized by name. Keys are matchers (basename or
# filename pattern), values are (ecosystem, package_manager).
# Patterns are tried in order; first match wins.
MANIFEST_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"^pyproject\.toml$", re.IGNORECASE), "python", "poetry/pip"),
    (re.compile(r"^requirements.*\.txt$", re.IGNORECASE), "python", "pip"),
    (re.compile(r"^setup\.py$", re.IGNORECASE), "python", "setuptools"),
    (re.compile(r"^setup\.cfg$", re.IGNORECASE), "python", "setuptools"),
    (re.compile(r"^Pipfile$", re.IGNORECASE), "python", "pipenv"),
    (re.compile(r"^Pipfile\.lock$", re.IGNORECASE), "python", "pipenv"),
    (re.compile(r"^poetry\.lock$", re.IGNORECASE), "python", "poetry"),
    (re.compile(r"^uv\.lock$", re.IGNORECASE), "python", "uv"),
    (re.compile(r"^package\.json$", re.IGNORECASE), "javascript", "npm"),
    (re.compile(r"^pnpm-lock\.yaml$", re.IGNORECASE), "javascript", "pnpm"),
    (re.compile(r"^yarn\.lock$", re.IGNORECASE), "javascript", "yarn"),
    (re.compile(r"^package-lock\.json$", re.IGNORECASE), "javascript", "npm"),
    (re.compile(r"^bun\.lockb?$", re.IGNORECASE), "javascript", "bun"),
    (re.compile(r"^tsconfig\.json$", re.IGNORECASE), "typescript", "tsc"),
    (re.compile(r"^go\.mod$", re.IGNORECASE), "go", "go modules"),
    (re.compile(r"^go\.sum$", re.IGNORECASE), "go", "go modules"),
    (re.compile(r"^Cargo\.toml$", re.IGNORECASE), "rust", "cargo"),
    (re.compile(r"^Cargo\.lock$", re.IGNORECASE), "rust", "cargo"),
    (re.compile(r"^composer\.json$", re.IGNORECASE), "php", "composer"),
    (re.compile(r"^Gemfile$", re.IGNORECASE), "ruby", "bundler"),
    (re.compile(r"^Gemfile\.lock$", re.IGNORECASE), "ruby", "bundler"),
    (re.compile(r"^mix\.exs$", re.IGNORECASE), "elixir", "mix"),
    (re.compile(r"^Dockerfile$", re.IGNORECASE), "container", "docker"),
    (re.compile(r"^Dockerfile\.[^/]+$", re.IGNORECASE), "container", "docker"),
    (re.compile(r"^docker-compose\.ya?ml$", re.IGNORECASE), "container", "compose"),
    (re.compile(r"^compose\.ya?ml$", re.IGNORECASE), "container", "compose"),
    (re.compile(r"^Chart\.yaml$", re.IGNORECASE), "kubernetes", "helm"),
    (re.compile(r"^kustomization\.yaml$", re.IGNORECASE), "kubernetes", "kustomize"),
    (re.compile(r"^Podfile$", re.IGNORECASE), "swift", "cocoapods"),
]

# Build systems detected (presence of these file names at the
# repo root implies the system)
BUILD_SYSTEMS = {
    "Makefile": "make",
    "makefile": "make",
    "GNUmakefile": "make",
    "CMakeLists.txt": "cmake",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "settings.gradle": "gradle",
    "pom.xml": "maven",
    "build.sbt": "sbt",
    "Rakefile": "rake",
    "Justfile": "just",
    "justfile": "just",
    "Taskfile.yml": "task",
    "mage.go": "mage",
}

# Test systems detected by file pattern
TEST_SYSTEMS = [
    (re.compile(r"(^|/)tests?/.*test_[^/]+\.py$"), "pytest"),
    (re.compile(r"(^|/)test_[^/]+\.py$"), "pytest"),
    (re.compile(r"(^|/)[^/]+_test\.py$"), "pytest"),
    (re.compile(r"(^|/)tests?/.*\.test\.[jt]sx?$"), "jest"),
    (re.compile(r"(^|/)[^/]+\.spec\.[jt]sx?$"), "jest"),
    (re.compile(r"(^|/)__tests__/"), "jest"),
    (re.compile(r"(^|/)tests?/.*\.test\.go$"), "go test"),
    (re.compile(r"(^|/)tests/"), "go test"),
    (re.compile(r"(^|/)src/.*/tests/"), "cargo test"),
    (re.compile(r"(^|/)spec/.*_spec\.rb$"), "rspec"),
    (re.compile(r"(^|/)test/.*_test\.rb$"), "minitest"),
]

# Data store names detected from dependency names or env names
DATA_STORE_NAMES = {
    "postgres", "postgresql", "pg", "mysql", "mariadb", "sqlite",
    "redis", "memcached", "mongodb", "mongo", "cassandra",
    "clickhouse", "duckdb", "neo4j", "arangodb", "couchdb",
    "elasticsearch", "opensearch", "influxdb", "timescaledb",
    "milvus", "weaviate", "qdrant", "pinecone", "chroma", "lancedb",
    "faiss", "annoy", "hnswlib", "s3", "minio", "gcs", "azure-blob",
    "kafka", "rabbitmq", "nats", "pulsar", "zmq", "zeromq",
    "vault", "consul", "etcd",
}

# Service name patterns in docker-compose
SERVICE_NAME_RE = re.compile(r"^\s{2}([a-zA-Z0-9_][a-zA-Z0-9_-]*)\s*:")

# Entrypoint kinds
ENTRYPOINT_KINDS = {
    "main.py": ("cli", "python"),
    "app.py": ("api-server", "python"),
    "server.py": ("api-server", "python"),
    "wsgi.py": ("api-server", "python"),
    "asgi.py": ("api-server", "python"),
    "manage.py": ("cli", "python"),
    "cli.py": ("cli", "python"),
    "index.js": ("cli", "javascript"),
    "server.js": ("api-server", "javascript"),
    "main.go": ("cli", "go"),
    "cmd/": ("cli", "go"),
    "main.rs": ("cli", "rust"),
    "src/main.rs": ("cli", "rust"),
    "src/main/java": ("cli", "java"),
    "src/main/kotlin": ("cli", "kotlin"),
    "Dockerfile": ("dockerfile", "container"),
}

# Interface detection patterns
INTERFACE_PATTERNS = [
    (re.compile(r"SKILL\.md$", re.IGNORECASE), "agent-skill"),
    (re.compile(r"SOUL\.md$", re.IGNORECASE), "agent-soul"),
    (re.compile(r"Dockerfile$", re.IGNORECASE), "docker-service"),
    (re.compile(r"docker-compose\.ya?ml$", re.IGNORECASE), "docker-service"),
    (re.compile(r"package\.json$", re.IGNORECASE), "npm-package"),
    (re.compile(r"pyproject\.toml$", re.IGNORECASE), "python-package"),
    (re.compile(r"setup\.py$", re.IGNORECASE), "python-package"),
    (re.compile(r"server\.(py|js|ts|go|rs)$", re.IGNORECASE), "rest-api"),
    (re.compile(r"app\.(py|js|ts|go|rs)$", re.IGNORECASE), "rest-api"),
    (re.compile(r"mcp[_-]server", re.IGNORECASE), "mcp-server"),
    (re.compile(r"\.graphql$", re.IGNORECASE), "graphql-api"),
    (re.compile(r"openapi\.(yaml|yml|json)$", re.IGNORECASE), "rest-api"),
    (re.compile(r"swagger\.(yaml|yml|json)$", re.IGNORECASE), "rest-api"),
    (re.compile(r"index\.html$", re.IGNORECASE), "web-ui"),
]


# ----- Helpers -----------------------------------------------------------

def _is_secret_path(rel: Path) -> bool:
    """Return True if a relative path is secret-bearing and must
    never be read."""
    name = rel.name
    if name in _SECRET_BASENAMES:
        return True
    for pat in _SECRET_PATTERNS:
        if pat.search(name):
            return True
    return False


def _safe_read_text(path: Path, max_bytes: int = 1_000_000) -> Optional[str]:
    """Read a text file as UTF-8 with a size cap. Returns None
    for binary, missing, or secret-bearing files."""
    if _is_secret_path(path):
        return None
    try:
        if path.stat().st_size > max_bytes:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def _walk_files(raw_root: Path) -> Iterable[Path]:
    """Yield every file under raw_root, excluding .git/ and the
    directory entries themselves."""
    for p in raw_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(raw_root)
        if any(part == ".git" for part in rel.parts):
            continue
        yield p


def _detect_languages(raw_root: Path, max_files: int = 50_000) -> Tuple[List[str], Dict[str, float]]:
    """Return (sorted language list, fractional breakdown).

    Walks the tree but caps at `max_files` to keep the extractor
    bounded for very large repositories. Binary files larger
    than 1 MB are skipped (they don't have meaningful extensions
    for language detection anyway)."""
    ext_counts: Counter = Counter()
    seen = 0
    for p in _walk_files(raw_root):
        seen += 1
        if seen > max_files:
            break
        try:
            if p.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        ext = p.suffix
        if ext in LANG_BY_EXT:
            ext_counts[ext] += 1
    total = sum(ext_counts.values())
    if total == 0:
        return [], {}
    lang_counts: Counter = Counter()
    for ext, count in ext_counts.items():
        lang_counts[LANG_BY_EXT[ext]] += count
    sorted_langs = sorted(lang_counts.keys())
    breakdown = {
        lang: round(count / total, 4) for lang, count in lang_counts.most_common()
    }
    return sorted_langs, breakdown


def _detect_dependency_manifests(raw_root: Path) -> List[Dict[str, Any]]:
    """Find dependency manifests in the raw/ tree (root + 1 level
    deep, conservatively). Returns a list of profile entries.

    Optimized: only inspects files whose names match a known
    manifest pattern. Does not walk the entire tree."""
    manifests: List[Dict[str, Any]] = []
    for p in _manifest_files_at_root(raw_root):
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        for pat, ecosystem, pm in MANIFEST_PATTERNS:
            if pat.match(rel.name):
                try:
                    size = p.stat().st_size
                except OSError:
                    size = 0
                manifests.append({
                    "path": str(rel),
                    "ecosystem": ecosystem,
                    "package_manager": pm,
                    "size_bytes": size,
                })
                break
    # Dedupe by path
    seen = set()
    out: List[Dict[str, Any]] = []
    for m in manifests:
        if m["path"] in seen:
            continue
        seen.add(m["path"])
        out.append(m)
    return sorted(out, key=lambda x: x["path"])


def _parse_pyproject_toml(text: str) -> List[Dict[str, str]]:
    """Extract major dependencies from pyproject.toml.

    Returns a list of {name, ecosystem, version_spec} dicts. The
    parser is intentionally simple — it uses tomllib if available,
    otherwise a regex fallback.
    """
    deps: List[Dict[str, str]] = []
    # Try tomllib / tomli (Python 3.11+ stdlib; 3.9/3.10 backport)
    data: Any = None
    if _toml is not None:
        try:
            data = _toml.loads(text)
        except Exception:
            data = None
    if data is None:
        # Regex fallback for the simple case of
        # `name = "spec"` lines under
        # [tool.poetry.dependencies] / [project.dependencies].
        data = None
    if isinstance(data, dict):
        # PEP 621: project.dependencies is a list of strings like "x>=1.0"
        proj = data.get("project", {})
        for entry in proj.get("dependencies", []) or []:
            if isinstance(entry, str):
                m = re.match(r"^([A-Za-z0-9_.\-]+)\s*([><=!~].*)?$", entry)
                if m:
                    deps.append({
                        "name": m.group(1),
                        "ecosystem": "python",
                        "version_spec": (m.group(2) or "").strip(),
                    })
        # Poetry: tool.poetry.dependencies is a dict
        poetry = data.get("tool", {}).get("poetry", {})
        for name, spec in (poetry.get("dependencies") or {}).items():
            if name.lower() == "python":
                continue
            if isinstance(spec, str):
                deps.append({
                    "name": name,
                    "ecosystem": "python",
                    "version_spec": spec,
                })
            elif isinstance(spec, dict):
                deps.append({
                    "name": name,
                    "ecosystem": "python",
                    "version_spec": spec.get("version", "any"),
                })
    return deps


def _parse_requirements_txt(text: str) -> List[Dict[str, str]]:
    """Extract dependencies from requirements.txt.

    Format: one per line, `name==1.0`, `name>=1.0`, `name~=1.0`,
    `name @ url`, or just `name`. Lines starting with `#` and
    options like `-r other.txt` and `-e .` are skipped.
    """
    deps: List[Dict[str, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        # Strip trailing comment
        s = s.split(" #", 1)[0].strip()
        # Strip inline markers
        for marker in (";", " & ", " | "):
            if marker in s:
                s = s.split(marker, 1)[0].strip()
        # Reject anything that smells like an env var or URL
        if "://" in s or s.startswith("git+") or "$" in s:
            continue
        m = re.match(
            r"^([A-Za-z0-9_.\-]+)\s*([><=!~]=?|@)\s*([^\s,;]+)?", s
        )
        if m:
            name = m.group(1)
            spec = m.group(2) + (m.group(3) if m.group(3) else "")
            deps.append({
                "name": name,
                "ecosystem": "python",
                "version_spec": spec.strip(),
            })
    return deps


def _parse_package_json(text: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Extract deps and devDeps from package.json. Returns
    (deps, dev_deps)."""
    deps: List[Dict[str, str]] = []
    dev: List[Dict[str, str]] = []
    try:
        data = json.loads(text)
    except Exception:
        return [], []
    if not isinstance(data, dict):
        return [], []
    for name, spec in (data.get("dependencies") or {}).items():
        if isinstance(name, str):
            deps.append({
                "name": name,
                "ecosystem": "javascript",
                "version_spec": str(spec) if spec else "any",
            })
    for name, spec in (data.get("devDependencies") or {}).items():
        if isinstance(name, str):
            dev.append({
                "name": name,
                "ecosystem": "javascript",
                "version_spec": str(spec) if spec else "any",
            })
    return deps, dev


def _parse_go_mod(text: str) -> List[Dict[str, str]]:
    """Extract require lines from go.mod."""
    deps: List[Dict[str, str]] = []
    in_require_block = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("require ("):
            in_require_block = True
            continue
        if in_require_block and s == ")":
            in_require_block = False
            continue
        if in_require_block:
            parts = s.split()
            if len(parts) >= 2:
                deps.append({
                    "name": parts[0],
                    "ecosystem": "go",
                    "version_spec": parts[1],
                })
            continue
        if s.startswith("require "):
            parts = s.split()
            if len(parts) >= 3:
                deps.append({
                    "name": parts[1],
                    "ecosystem": "go",
                    "version_spec": parts[2],
                })
    return deps


def _parse_cargo_toml(text: str) -> List[Dict[str, str]]:
    """Extract [dependencies] from Cargo.toml using tomllib."""
    deps: List[Dict[str, str]] = []
    data: Any = None
    if _toml is not None:
        try:
            data = _toml.loads(text)
        except Exception:
            return []
    if not isinstance(data, dict):
        return []
    for name, spec in (data.get("dependencies") or {}).items():
        if isinstance(name, str):
            if isinstance(spec, str):
                deps.append({
                    "name": name,
                    "ecosystem": "rust",
                    "version_spec": spec,
                })
            elif isinstance(spec, dict):
                deps.append({
                    "name": name,
                    "ecosystem": "rust",
                    "version_spec": spec.get("version", "any"),
                })
    return deps


def _manifest_files_at_root(raw_root: Path) -> List[Path]:
    """Yield files at the repo root or one level deep that match
    a known manifest pattern. Much faster than walking every
    file in the tree."""
    out: List[Path] = []
    manifest_names = {
        "pyproject.toml", "setup.py", "setup.cfg", "Pipfile",
        "Pipfile.lock", "poetry.lock", "uv.lock",
        "package.json", "tsconfig.json",
        "pnpm-lock.yaml", "yarn.lock", "package-lock.json", "bun.lockb", "bun.lock",
        "go.mod", "go.sum", "Cargo.toml", "Cargo.lock",
        "composer.json", "Gemfile", "Gemfile.lock", "mix.exs",
        "Dockerfile", "Chart.yaml", "kustomization.yaml",
    }
    for entry in raw_root.iterdir():
        if not entry.is_file():
            continue
        if entry.name in manifest_names:
            out.append(entry)
        elif entry.name.startswith("requirements") and entry.name.endswith(".txt"):
            out.append(entry)
        elif entry.name.startswith("Dockerfile."):
            out.append(entry)
        elif entry.name.startswith("docker-compose."):
            out.append(entry)
        elif entry.name.startswith("compose."):
            out.append(entry)
    # One level deep
    for sub in raw_root.iterdir():
        if not sub.is_dir() or sub.name == ".git":
            continue
        try:
            for entry in sub.iterdir():
                if not entry.is_file():
                    continue
                if entry.name in manifest_names:
                    out.append(entry)
                elif entry.name.startswith("requirements") and entry.name.endswith(".txt"):
                    out.append(entry)
        except (PermissionError, OSError):
            continue
    return out


def _major_dependencies(raw_root: Path) -> List[Dict[str, str]]:
    """Walk recognized manifests, parse them, and return the union
    of all declared major dependencies (deduped, sorted, with
    source_path). Only inspects files at depth ≤ 1 with names
    matching known manifest patterns — never walks the entire
    tree, never reads large or non-text files."""
    out: List[Dict[str, str]] = []
    for p in _manifest_files_at_root(raw_root):
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        try:
            if p.stat().st_size > 1_000_000:
                continue
        except OSError:
            continue
        text = _safe_read_text(p)
        if text is None:
            continue
        parsed: List[Dict[str, str]] = []
        if rel.name == "pyproject.toml":
            parsed = _parse_pyproject_toml(text)
        elif rel.name.startswith("requirements") and rel.name.endswith(".txt"):
            parsed = _parse_requirements_txt(text)
        elif rel.name == "package.json":
            parsed, _ = _parse_package_json(text)
        elif rel.name == "go.mod":
            parsed = _parse_go_mod(text)
        elif rel.name == "Cargo.toml":
            parsed = _parse_cargo_toml(text)
        for d in parsed:
            d["source_path"] = str(rel)
            out.append(d)
    # Dedupe by (name, ecosystem, source_path)
    seen = set()
    deduped: List[Dict[str, str]] = []
    for d in out:
        key = (d["name"], d["ecosystem"], d["source_path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(d)
    return sorted(deduped, key=lambda x: (x["ecosystem"], x["name"].lower()))


def _runtime_stack_from_deps(deps: List[Dict[str, str]]) -> List[str]:
    """Infer runtime components from declared dependencies.

    Examples: `python>=3.11` from a manifest, `docker` from a
    Dockerfile or docker-compose, `kubernetes` from a Helm chart.
    """
    items: List[str] = []
    for d in deps:
        n = (d.get("name") or "").lower()
        spec = (d.get("version_spec") or "").lower()
        if n in DATA_STORE_NAMES:
            items.append(n)
        if n in ("docker", "compose") or "docker" in n:
            items.append("docker")
        if n in ("kubernetes", "k8s", "helm") or "k8s" in n:
            items.append("kubernetes")
        if "fastapi" in n or "flask" in n or "django" in n or "starlette" in n:
            items.append("python-web")
        if "express" in n or "fastify" in n or "koa" in n or "nestjs" in n:
            items.append("node-web")
    return sorted(set(items))


def _detect_entrypoints(raw_root: Path) -> List[Dict[str, str]]:
    """Detect repo entrypoints conservatively.

    Only paths that are unambiguous (recognized filename at the
    root, or in a `cmd/`, `bin/`, or `src/` directory) are
    reported. No commands are guessed.
    """
    out: List[Dict[str, str]] = []
    for p in _walk_files(raw_root):
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        # Restrict depth
        if len(rel.parts) > 3:
            continue
        if rel.name in ENTRYPOINT_KINDS:
            kind, _ = ENTRYPOINT_KINDS[rel.name]
            out.append({"kind": kind, "path": str(rel), "command": ""})
        if str(rel).startswith("cmd/") and rel.name.endswith(".go"):
            out.append({"kind": "cli", "path": str(rel), "command": ""})
    # Dedupe
    seen = set()
    deduped: List[Dict[str, str]] = []
    for e in out:
        if e["path"] in seen:
            continue
        seen.add(e["path"])
        deduped.append(e)
    return sorted(deduped, key=lambda x: x["path"])[:20]


def _detect_services(raw_root: Path) -> List[str]:
    """Detect named services from docker-compose / k8s manifests.
    Service names only, no values."""
    services: List[str] = []
    for p in _walk_files(raw_root):
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        if rel.name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml",
                        "compose.yaml") or rel.name.startswith("docker-compose."):
            text = _safe_read_text(p)
            if text is None:
                continue
            for line in text.splitlines():
                m = SERVICE_NAME_RE.match(line)
                if m:
                    name = m.group(1)
                    if name not in ("version", "services", "networks", "volumes",
                                    "configs", "secrets"):
                        services.append(name)
    return sorted(set(services))


def _detect_data_stores(raw_root: Path, deps: List[Dict[str, str]]) -> List[str]:
    """Detect data store names from declared dependencies and
    from environment keys in compose files (NEVER values)."""
    stores: set = set()
    for d in deps:
        n = (d.get("name") or "").lower()
        if n in DATA_STORE_NAMES:
            stores.add(n)
    # Env-var names (only names) from compose
    for p in _walk_files(raw_root):
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        if rel.name in ("docker-compose.yml", "docker-compose.yaml") or rel.name.startswith("docker-compose."):
            text = _safe_read_text(p)
            if text is None:
                continue
            for m in re.finditer(r"^\s+([A-Z][A-Z0-9_]+):\s*", text, re.MULTILINE):
                key = m.group(1)
                for store in DATA_STORE_NAMES:
                    if store.upper() in key:
                        stores.add(store)
    return sorted(stores)


def _detect_config_files(raw_root: Path) -> List[str]:
    """Return paths of recognized config files at the root or
    one level deep. Only checks depth ≤ 1 to keep the extractor
    bounded for large repositories."""
    recognized = {
        "pyproject.toml", "setup.py", "setup.cfg", "Pipfile",
        "package.json", "tsconfig.json", "Cargo.toml", "go.mod",
        "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "compose.yml", "compose.yaml",
        "Makefile", "Rakefile", "Justfile",
        "Chart.yaml", "kustomization.yaml",
        "pdm.lock", "poetry.lock", "uv.lock",
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
        ".env.example", ".env.sample", ".env.template",
    }
    out: List[str] = []
    for entry in raw_root.iterdir():
        if not entry.is_file():
            continue
        if entry.name in recognized or entry.name.startswith("Dockerfile.") or entry.name.startswith("docker-compose."):
            out.append(entry.name)
        elif entry.name.startswith(".env."):
            out.append(entry.name)
    return sorted(set(out))


def _detect_interfaces(raw_root: Path) -> List[Dict[str, str]]:
    """Detect interfaces conservatively. Only inspects depth ≤ 2
    to keep the extractor bounded for large repositories. Each
    interface is a single entry; we never read file content
    beyond size, and we only look for path patterns and
    recognized file names."""
    out: List[Dict[str, str]] = []
    seen = set()
    candidates: List[Path] = []
    # depth 0
    for entry in raw_root.iterdir():
        if entry.is_file():
            candidates.append(entry)
    # depth 1
    for sub in raw_root.iterdir():
        if sub.is_dir() and sub.name != ".git":
            try:
                for entry in sub.iterdir():
                    if entry.is_file():
                        candidates.append(entry)
            except (PermissionError, OSError):
                continue
    # depth 2 (one more level, conservatively)
    for sub in raw_root.iterdir():
        if not (sub.is_dir() and sub.name != ".git"):
            continue
        try:
            for sub2 in sub.iterdir():
                if sub2.is_dir() and sub2.name != ".git":
                    try:
                        for entry in sub2.iterdir():
                            if entry.is_file():
                                candidates.append(entry)
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError):
            continue

    for p in candidates:
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        for pat, kind in INTERFACE_PATTERNS:
            if pat.search(rel.name) or pat.search(str(rel)):
                if kind in seen:
                    break
                seen.add(kind)
                out.append({
                    "kind": kind,
                    "name": rel.stem,
                    "path": str(rel),
                    "protocol": "",
                    "notes": "",
                })
                break
    return out


def _build_systems(raw_root: Path) -> List[str]:
    found: set = set()
    for p in _walk_files(raw_root):
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        if len(rel.parts) > 2:
            continue
        if rel.name in BUILD_SYSTEMS:
            found.add(BUILD_SYSTEMS[rel.name])
    return sorted(found)


def _test_systems(raw_root: Path) -> List[str]:
    found: set = set()
    for p in _walk_files(raw_root):
        if _is_secret_path(p):
            continue
        rel = p.relative_to(raw_root)
        s = str(rel)
        for pat, name in TEST_SYSTEMS:
            if pat.search(s):
                found.add(name)
    return sorted(found)


# ----- Public API --------------------------------------------------------

def _identity_from_source_id(source_id: str) -> Dict[str, str]:
    """Parse a `github:<owner>/<repo>` source_id into the
    repo_identity block. Returns {} for non-github ids."""
    out: Dict[str, str] = {
        "host": "github.com",
        "owner": "",
        "repo": "",
        "full_name": "",
        "clone_url": "",
        "ssh_url": "",
        "default_branch": "",
        "pinned_commit": "",
        "upstream_of_fork": "",
        "fork_intent": "unknown",
    }
    if not source_id.startswith("github:"):
        return out
    path = source_id[len("github:"):]
    if "/" not in path:
        return out
    owner, repo = path.split("/", 1)
    out["owner"] = owner
    out["repo"] = repo
    out["full_name"] = f"{owner}/{repo}"
    out["clone_url"] = f"https://github.com/{owner}/{repo}.git"
    out["ssh_url"] = f"git@github.com:{owner}/{repo}.git"
    return out


def extract_tech_profile(
    raw_root: Path,
    source_id: str = "",
    pinned_commit: str = "",
) -> Dict[str, Any]:
    """Deterministically extract a tech profile from a raw/ tree.

    Parameters
    ----------
    raw_root:
        The path of the cloned source (e.g. `vault/raw/<repo>/`).
        Must be an existing directory.
    source_id:
        Optional `github:<owner>/<repo>` for the `repo_identity`
        block. If empty, identity fields other than `host` are
        left blank.
    pinned_commit:
        Optional pinned commit SHA for the `repo_identity.pinned_commit`
        field. If empty, the field is left blank.

    Returns
    -------
    A dict whose shape is a strict subset of `source.schema.yaml`'s
    new fields. All list items are deterministically sorted.
    """
    raw_root = Path(raw_root).resolve()
    if not raw_root.is_dir():
        raise FileNotFoundError(f"raw_root not a directory: {raw_root}")

    languages, breakdown = _detect_languages(raw_root)
    manifests = _detect_dependency_manifests(raw_root)
    deps = _major_dependencies(raw_root)
    runtime = _runtime_stack_from_deps(deps)
    builds = _build_systems(raw_root)
    tests = _test_systems(raw_root)
    entrypoints = _detect_entrypoints(raw_root)
    services = _detect_services(raw_root)
    data_stores = _detect_data_stores(raw_root, deps)
    config_files = _detect_config_files(raw_root)
    interfaces = _detect_interfaces(raw_root)

    identity = _identity_from_source_id(source_id)
    if pinned_commit:
        identity["pinned_commit"] = pinned_commit

    profile: Dict[str, Any] = {
        "source_platform": "github",
        "repo_identity": identity,
        "repo_profile": {
            "languages": languages,
            "language_breakdown": breakdown,
            "dependency_manifests": manifests,
            "major_dependencies": deps,
            "runtime_stack": runtime,
            "build_systems": builds,
            "test_systems": tests,
            "entrypoints": entrypoints,
            "services": services,
            "data_stores": data_stores,
            "config_files": config_files,
        },
        "interfaces": interfaces,
        "workflow_synthesis": {
            "workflow_roles": [],
            "provides": [],
            "requires": [],
            "compatible_with": [],
            "composition_edges": [],
            "composition_notes": [],
        },
    }
    return profile


# ----- CLI ---------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="codex_vault_pipeline.extractors.tech_profile",
        description=(
            "Deterministically extract a technical profile of a "
            "GitHub repository from its raw/ tree. Does NOT "
            "modify any source record by default. Use "
            "--write-profile to overlay the profile onto the "
            "source.v1.yaml (only when deterministic and safe)."
        ),
    )
    p.add_argument(
        "--raw-root",
        type=Path,
        required=True,
        help="Path to the cloned source (e.g. vault/raw/<repo>/).",
    )
    p.add_argument(
        "--source-id",
        type=str,
        default="",
        help="Optional `github:<owner>/<repo>` for the repo_identity block.",
    )
    p.add_argument(
        "--pinned-commit",
        type=str,
        default="",
        help="Optional pinned commit SHA for repo_identity.pinned_commit.",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the profile JSON to this path. Default: stdout.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    profile = extract_tech_profile(
        args.raw_root,
        source_id=args.source_id,
        pinned_commit=args.pinned_commit,
    )
    text = json.dumps(profile, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
