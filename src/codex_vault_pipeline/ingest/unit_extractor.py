"""Deterministic unit extraction from source artifacts.

Reads artifact records + occurrences + raw file content and produces
``unit/v1`` dicts (Layer C) for each addressable unit of knowledge.

Artifact types handled:

- **Markdown** → ``doc-section`` units per ATX heading
- **JSON/YAML config** → ``configuration`` unit per file
- **Docker/nix/compose** → ``deployment-component`` unit per file
- **Python** → ``code-symbol`` units per class / function / async-function
- **JavaScript/TypeScript** → ``code-symbol`` units per exported class / function
- **Shell** → ``code-symbol`` unit per script
- **n8n workflow JSON** → ``n8n-workflow`` unit
- **SKILL.md** → ``hermes-skill`` unit
- **SOUL.md** → ``hermes-soul`` unit
- **Other text** → ``doc-section`` paragraph-chunk units
- **Binaries / unsupported** → skipped

Unit IDs are deterministic: ``sha256:<artifact_sha>#<anchor>`` where
*anchor* encodes the unit's position within the file.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

from codex_vault_pipeline.utils.file_policy import is_binary, detect_media_type

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GENERATOR = "codex-vault/deterministic-unit-extractor"
GENERATOR_VERSION = "1.0.0"

# Maximum size for a single unit's semantic_text to avoid bloat.
MAX_SEMANTIC_TEXT_LEN = 30000

# Maximum file size in bytes to attempt unit extraction from text files.
MAX_TEXT_FILE_SIZE = 1_000_000  # 1 MB

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ExtractedUnit = Dict[str, Any]


def extract_units_from_artifact(
    artifact: Dict[str, Any],
    occurrence: Dict[str, Any],
    raw_content: bytes,
    run_id: str,
    now: Optional[str] = None,
) -> List[ExtractedUnit]:
    """Extract units from a single artifact.

    Arguments:
        artifact:    Artifact record (must contain ``content_sha256``,
                     ``artifact_role``, ``source_id``, ``source_path``,
                     ``security_scan``, ``media_type``).
        occurrence:  First occurrence record for this artifact.
        raw_content: Raw file content as bytes.
        run_id:      Ingest run identifier.
        now:         ISO datetime (auto if ``None``).

    Returns a list of ``unit/v1`` dicts (may be empty if the artifact is
    binary, blocked, oversized, or otherwise unsupported).
    """
    if now is None:
        now = datetime.now(timezone.utc).isoformat()

    sha: str = artifact.get("content_sha256", "") or ""
    if not sha:
        return []

    sec = artifact.get("security_scan", {}) or {}
    sec_status = sec.get("status", "not-scanned")
    if sec_status == "blocked":
        return []
    is_flagged = sec_status == "flagged"

    source_id: str = occurrence.get("source_id", artifact.get("source_id", ""))
    source_path: str = occurrence.get("source_path", artifact.get("source_path", ""))
    occ_id: str = occurrence.get("occurrence_id", "")
    role: str = artifact.get("artifact_role", "unknown")
    media_type: str = artifact.get("media_type", detect_media_type(Path(source_path)))

    # Name for per-source subdirectory.
    safe_source = source_id.replace(":", "_").replace("/", "_")

    # Guard: binary and oversized (non-Markdown text)
    if media_type.startswith("image/") or media_type.startswith(
        "application/octet-stream"
    ):
        return []

    if len(raw_content) > MAX_TEXT_FILE_SIZE:
        # Skip files over 1 MB unless they are a recognized text format.
        if not media_type.startswith("text/"):
            return []

    text = raw_content.decode("utf-8", errors="replace")

    # ── Extract by role + media_type ──────────────────────────────────
    # Agent skill / soul files (always checked first by filename)
    name = Path(source_path).name

    if role == "agent-skill" or name == "SKILL.md":
        return _extract_hermes_skill(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact
        )

    if role == "agent-soul" or name == "SOUL.md":
        return _extract_hermes_soul(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact
        )

    # n8n workflow
    if role == "n8n-workflow" or name.endswith(".json") and _looks_like_n8n(text):
        return _extract_n8n_workflow(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact
        )

    # Deployment definition (check before text/plain doc-section)
    if role == "deployment-definition" or media_type in (
        "text/nix", "text/terraform"
    ) or name == "Dockerfile" or name.startswith("docker-compose"):
        return _extract_deployment(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact,
        )

    # Configuration (JSON / YAML)
    if role == "configuration" or media_type in ("text/json", "text/yaml"):
        return _extract_config(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact,
        )

    # Markdown / documentation
    if role == "documentation" or media_type in (
        "text/markdown", "text/plain", "text/rst"
    ):
        return _extract_doc_sections(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact,
            media_type=media_type,
        )

    # Python code
    if media_type in ("text/python",):
        return _extract_python(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact,
        )

    # JavaScript / TypeScript code
    if media_type in (
        "text/javascript", "text/typescript", "text/vue", "text/svelte"
    ):
        return _extract_jsts(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact,
        )

    # Shell scripts
    if media_type in ("text/shell", "text/bash", "text/zsh", "text/powershell", "text/batch"):
        return _extract_shell(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact,
        )

    # Fallback for text-like: paragraph chunks
    if media_type.startswith("text/"):
        return _extract_text_fallback(
            sha, source_id, source_path, safe_source, occ_id, text,
            run_id, now, is_flagged, artifact,
        )

    return []


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _unit_id(sha: str, anchor: str) -> str:
    return f"sha256:{sha}#{anchor}"


def _content_hash(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"[^a-z0-9._-]", "", s)
    s = s.strip("-")
    return s[:80] or "section"


def _make_fingerprints(
    sha: str, unit_id: str, title: str, summary: str, structural: str = ""
) -> Dict[str, str]:
    return {
        "content_sha256": sha,
        "normalized_hash": _content_hash(unit_id.encode()),
        "structural_hash": _content_hash(structural.encode() if structural else unit_id.encode()),
        "semantic_signature": _content_hash((title + (summary or "")[:200]).encode()),
    }


def _build_unit_body(
    unit_id: str,
    artifact_id: str,
    occ_id: str,
    source_id: str,
    source_path: str,
    unit_type: str,
    title: str,
    semantic_text: str,
    token_count: int,
    sha: str,
    fingerprints_structural: str,
    run_id: str,
    now: str,
    is_flagged: bool,
    dedup_group_extra: str = "",
    source_anchor: Optional[Dict[str, Any]] = None,
) -> ExtractedUnit:
    """Build a unit/v1 dict with all required fields."""
    if source_anchor is None:
        source_anchor = {
            "section": unit_type,
            "line_start": 1,
            "line_end": 1,
            "json_pointer": "/",
        }

    body: ExtractedUnit = {
        "schema": "unit/v1",
        "schema_version": "1.0.0",
        "record_id": None,
        "created_at": now,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": run_id,
        "content_hash": None,
        "source_record_ids": [occ_id],
        "parser_name": GENERATOR,
        "parser_version": GENERATOR_VERSION,
        "unit_id": unit_id,
        "artifact_id": artifact_id,
        "source_anchor": source_anchor,
        "unit_type": unit_type,
        "title": title,
        "semantic_text": semantic_text,
        "token_count": token_count,
        "fingerprints": _make_fingerprints(
            sha, unit_id, title, semantic_text, fingerprints_structural
        ),
        "duplicate_of": None,
        "variant_of": None,
        "derived_from": None,
        "dedup_group": "",
        "redacted": is_flagged,
    }
    if is_flagged:
        body["redaction_reason"] = "security_scan.status=flagged"

    # Dedup group from structural
    dg = dedup_group_extra or fingerprints_structural
    body["dedup_group"] = _content_hash(dg.encode() if dg else unit_id.encode())

    # Compute record_id + content_hash from serialized body.
    u_bytes = json.dumps(body, sort_keys=True, indent=2).encode("utf-8")
    uh = hashlib.sha256(u_bytes).hexdigest()
    body["record_id"] = f"sha256:{uh}"
    body["content_hash"] = f"sha256:{uh}"
    return body


# ---------------------------------------------------------------------------
# Markdown / doc-section
# ---------------------------------------------------------------------------

HEADING_RE = re.compile(r"^(#+)\s+(.*)$", re.MULTILINE)


def split_markdown_sections(text: str) -> List[Tuple[Optional[int], Optional[str], str, int, int]]:
    """Split Markdown by ATX headings.

    Returns ``[(level, heading_text, body, line_start, line_end), ...]``.
    The pre-heading area is treated as an intro section.
    """
    lines = text.splitlines()
    sections: List[Tuple[Optional[int], Optional[str], str, int, int]] = []
    current_level: Optional[int] = None
    current_text: Optional[str] = None
    current_buf: List[str] = []
    current_start = 1
    current_end = 0

    def push():
        nonlocal current_start
        if current_text is not None or current_buf:
            sections.append(
                (current_level, current_text, "\n".join(current_buf), current_start, current_end)
            )

    for i, line in enumerate(lines, start=1):
        m = HEADING_RE.match(line)
        if m:
            push()
            current_level = len(m.group(1))
            current_text = m.group(2).strip()
            current_buf = []
            current_start = i + 1
            current_end = i
        else:
            current_buf.append(line)
            current_end = i

    push()
    return sections


def _make_section_summary(h_text: Optional[str], body: str) -> str:
    """Build a semantic summary for a doc section."""
    trimmed = body.strip()[:500]
    if h_text and h_text != "(root)":
        return f"Section '{h_text}': {trimmed}"
    return f"Documentation: {trimmed}"


def _extract_doc_sections(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact, media_type="text/markdown",
) -> List[ExtractedUnit]:
    if is_flagged:
        # Emit a single minimal unit
        title = Path(source_path).name
        body = _build_unit_body(
            unit_id=_unit_id(sha, "doc-root"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="doc-section",
            title=title,
            semantic_text="",
            token_count=0,
            sha=sha,
            fingerprints_structural="doc-flagged",
            run_id=run_id,
            now=now,
            is_flagged=True,
            source_anchor={"section": "(root)", "line_start": 1, "line_end": 1, "json_pointer": None},
        )
        return [body]

    sections = split_markdown_sections(text)
    if not sections:
        sections = [(None, "(root)", text, 1, text.count("\n") + 1)]

    units: List[ExtractedUnit] = []
    for h_level, h_text, body_txt, line_start, line_end in sections:
        if h_text and h_text != "(root)":
            slug = _slugify(h_text)
            anchor = f"heading:{slug}"
            title = h_text
        else:
            anchor = "root"
            title = Path(source_path).name

        body_txt = body_txt.strip()
        if len(body_txt) > MAX_SEMANTIC_TEXT_LEN:
            body_txt = body_txt[:MAX_SEMANTIC_TEXT_LEN] + "..."

        summary = _make_section_summary(h_text, body_txt)
        token_count = len(body_txt.split())

        structural = json.dumps({"h_level": h_level, "h_text": h_text}, sort_keys=True)

        u = _build_unit_body(
            unit_id=_unit_id(sha, anchor),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="doc-section",
            title=title,
            semantic_text=summary if not is_flagged else "",
            token_count=token_count if not is_flagged else 0,
            sha=sha,
            fingerprints_structural=structural,
            run_id=run_id,
            now=now,
            is_flagged=is_flagged,
            dedup_group_extra=f"sha256:{sha}",
            source_anchor={
                "section": h_text or "(root)",
                "line_start": line_start,
                "line_end": line_end,
                "json_pointer": None,
            },
        )
        units.append(u)

    return units


# ---------------------------------------------------------------------------
# Configuration (JSON / YAML)
# ---------------------------------------------------------------------------

def _safe_keys_summary(obj) -> List[str]:
    if isinstance(obj, dict):
        return sorted(str(k) for k in obj)
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        keys: set = set()
        for item in obj[:10]:
            if isinstance(item, dict):
                keys.update(str(k) for k in item)
        return sorted(keys)
    return []


def _structural_summary(obj, max_depth=3) -> Dict[str, Any]:
    """Structural type summary without exposing values."""
    if max_depth <= 0:
        return {"type": "unknown", "truncated": True}
    if isinstance(obj, dict):
        return {
            "type": "object",
            "keys": {str(k): _type_tag(v) for k, v in obj.items()},
            "key_count": len(obj),
        }
    if isinstance(obj, list):
        if not obj:
            return {"type": "array", "length": 0}
        return {"type": "array", "length": len(obj), "item_type": _type_tag(obj[0])}
    return {"type": _type_tag(obj)}


def _type_tag(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, int):
        return "integer"
    if isinstance(v, float):
        return "number"
    if isinstance(v, str):
        return "string(long)" if len(v) > 200 else "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "unknown"


def _extract_config(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    parsed = None
    parse_format = "unknown"
    sp = source_path.lower()

    if sp.endswith((".yaml", ".yml")):
        if yaml is not None:
            try:
                parsed = yaml.safe_load(text)
                parse_format = "yaml"
            except Exception:
                pass
    else:
        try:
            parsed = json.loads(text)
            parse_format = "json"
        except Exception:
            pass

    if parsed is None:
        return []

    top_keys = _safe_keys_summary(parsed)
    structure = _structural_summary(parsed)

    # Classify config type
    cfg_type = _classify_config(parsed, source_path, parse_format)

    if not is_flagged:
        summary = (
            f"Configuration file ({cfg_type}, {parse_format}, "
            f"{len(top_keys)} top-level keys, {len(text)} bytes): "
            f"{','.join(top_keys[:10]) or 'no-keys'}"
        )
    else:
        summary = ""

    title = Path(source_path).name
    u = _build_unit_body(
        unit_id=_unit_id(sha, "config"),
        artifact_id=f"sha256:{sha}",
        occ_id=occ_id,
        source_id=source_id,
        source_path=source_path,
        unit_type="configuration",
        title=title,
        semantic_text=summary,
        token_count=len(summary.split()) if summary else 0,
        sha=sha,
        fingerprints_structural=json.dumps(structure, sort_keys=True),
        run_id=run_id,
        now=now,
        is_flagged=is_flagged,
        dedup_group_extra=f"sha256:{hashlib.sha256(cfg_type.encode()).hexdigest()}",
        source_anchor={
            "section": "config",
            "line_start": 1,
            "line_end": len(text.splitlines()),
            "json_pointer": "/",
        },
    )
    return [u]


def _classify_config(parsed, source_path: str, parse_format: str) -> str:
    sp = source_path.lower()
    if sp.endswith((".yaml", ".yml")):
        return "yaml-config"
    if sp.endswith("package.json"):
        return "package-manifest"
    if isinstance(parsed, dict):
        if "name" in parsed and "version" in parsed and "dependencies" in parsed:
            return "package-manifest"
        if "name" in parsed and "nodes" in parsed:
            return "n8n-workflow-meta"
    return "configuration"


# ---------------------------------------------------------------------------
# Deployment (Docker, nix, terraform, compose)
# ---------------------------------------------------------------------------

def _extract_dockerfile_fields(text: str) -> Dict[str, Any]:
    fields: Dict[str, Any] = {
        "from_images": [],
        "env_vars": [],
        "copy_paths": [],
        "run_commands": [],
        "entrypoint": None,
        "expose_ports": [],
    }
    for line in text.splitlines():
        ls = line.strip()
        if not ls or ls.startswith("#"):
            continue
        ls = re.sub(r"\s+#.*$", "", ls)
        m = re.match(r"FROM\s+(\S+)", ls, re.IGNORECASE)
        if m:
            fields["from_images"].append(m.group(1))
            continue
        m = re.match(r"ENV\s+(.+)", ls, re.IGNORECASE)
        if m:
            fields["env_vars"].append(m.group(1))
            continue
        m = re.match(r"COPY\s+(.+)", ls, re.IGNORECASE)
        if m:
            fields["copy_paths"].append(m.group(1))
            continue
    return fields


def _extract_deployment(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    name_lower = source_path.lower()
    lines = text.splitlines()
    title = Path(source_path).name

    if "dockerfile" in name_lower or title == "Dockerfile":
        docker = _extract_dockerfile_fields(text)
        if not is_flagged:
            summary = (
                f"Dockerfile: FROM {' '.join(docker['from_images'][:3]) or 'unknown'}; "
                f"ENV {len(docker['env_vars'])}; "
                f"COPY {len(docker['copy_paths'])}"
            )
        else:
            summary = ""
        structural = json.dumps(docker, sort_keys=True)
        u = _build_unit_body(
            unit_id=_unit_id(sha, "deployment"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="deployment-component",
            title=title,
            semantic_text=summary,
            token_count=len(summary.split()) if summary else 0,
            sha=sha,
            fingerprints_structural=structural,
            run_id=run_id,
            now=now,
            is_flagged=is_flagged,
            source_anchor={
                "section": "deployment",
                "line_start": 1,
                "line_end": len(lines),
                "json_pointer": "/",
            },
        )
        return [u]

    # Generic deployment
    if not is_flagged:
        summary = f"Deployment file: {title} ({len(lines)} lines, {len(text)} bytes)"
    else:
        summary = ""
    u = _build_unit_body(
        unit_id=_unit_id(sha, "deployment"),
        artifact_id=f"sha256:{sha}",
        occ_id=occ_id,
        source_id=source_id,
        source_path=source_path,
        unit_type="deployment-component",
        title=title,
        semantic_text=summary,
        token_count=len(summary.split()) if summary else 0,
        sha=sha,
        fingerprints_structural=f"deploy-{title}",
        run_id=run_id,
        now=now,
        is_flagged=is_flagged,
        source_anchor={
            "section": "deployment",
            "line_start": 1,
            "line_end": len(lines),
            "json_pointer": "/",
        },
    )
    return [u]


# ---------------------------------------------------------------------------
# Python code symbols
# ---------------------------------------------------------------------------

# Patterns for Python extraction
PY_CLASS_RE = re.compile(
    r"^class\s+(\w+)\s*.*:\s*$", re.MULTILINE
)
PY_FUNC_RE = re.compile(
    r"^(async\s+)?def\s+(\w+)\s*\(.*\)\s*(->.*)?:\s*$", re.MULTILINE
)
PY_MODULE_DOCSTRING_RE = re.compile(
    r'^"""(.+?)"""', re.DOTALL
)


def _extract_python(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    if is_flagged:
        title = Path(source_path).name
        u = _build_unit_body(
            unit_id=_unit_id(sha, "py-root"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=title,
            semantic_text="",
            token_count=0,
            sha=sha,
            fingerprints_structural="py-flagged",
            run_id=run_id,
            now=now,
            is_flagged=True,
        )
        return [u]

    units: List[ExtractedUnit] = []
    lines = text.splitlines()
    title = Path(source_path).name

    # Module-level docstring
    m = PY_MODULE_DOCSTRING_RE.match(text)
    if m:
        doc = m.group(1).strip()[:500]
        u = _build_unit_body(
            unit_id=_unit_id(sha, "module-docstring"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=f"{title} (module)",
            semantic_text=doc,
            token_count=len(doc.split()),
            sha=sha,
            fingerprints_structural="module-docstring",
            run_id=run_id,
            now=now,
            is_flagged=False,
            source_anchor={
                "section": f"{title}@module-docstring",
                "line_start": 1,
                "line_end": text.count('"""', 0, m.end()) > 0 and m.group(0).count("\n") + 1 or 1,
                "json_pointer": None,
            },
        )
        units.append(u)

    # Classes
    for m in PY_CLASS_RE.finditer(text):
        cls_name = m.group(1)
        line_start = text[: m.start()].count("\n") + 1
        body = _extract_symbol_body(text, m.end(), lines)
        body_trunc = body[:500]
        summary = f"Python class '{cls_name}': {body_trunc}"
        u = _build_unit_body(
            unit_id=_unit_id(sha, _slugify(f"class:{cls_name}")),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=f"{title}/{cls_name}",
            semantic_text=summary,
            token_count=len(summary.split()),
            sha=sha,
            fingerprints_structural=json.dumps({"symbol_type": "class", "name": cls_name}, sort_keys=True),
            run_id=run_id,
            now=now,
            is_flagged=False,
            dedup_group_extra=f"py-class-{cls_name}",
            source_anchor={
                "section": f"{title}@{cls_name}",
                "line_start": line_start,
                "line_end": line_start + body.count("\n"),
                "json_pointer": None,
            },
        )
        units.append(u)

    # Functions
    for m in PY_FUNC_RE.finditer(text):
        is_async = bool(m.group(1))
        func_name = m.group(2)
        if func_name.startswith("_"):
            continue  # skip private helpers
        line_start = text[: m.start()].count("\n") + 1
        body = _extract_symbol_body(text, m.end(), lines)
        body_trunc = body[:500]
        kw = "async " if is_async else ""
        summary = f"Python {kw}function '{func_name}': {body_trunc}"
        u = _build_unit_body(
            unit_id=_unit_id(sha, _slugify(f"func:{func_name}")),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=f"{title}/{func_name}",
            semantic_text=summary,
            token_count=len(summary.split()),
            sha=sha,
            fingerprints_structural=json.dumps(
                {"symbol_type": "async-function" if is_async else "function", "name": func_name},
                sort_keys=True,
            ),
            run_id=run_id,
            now=now,
            is_flagged=False,
            dedup_group_extra=f"py-func-{func_name}",
            source_anchor={
                "section": f"{title}@{func_name}",
                "line_start": line_start,
                "line_end": line_start + body.count("\n"),
                "json_pointer": None,
            },
        )
        units.append(u)

    if not units:
        # Fallback: one unit for the whole file
        summary = f"Python file: {title} ({len(lines)} lines)"
        u = _build_unit_body(
            unit_id=_unit_id(sha, "py-root"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=title,
            semantic_text=summary,
            token_count=len(summary.split()),
            sha=sha,
            fingerprints_structural="py-root",
            run_id=run_id,
            now=now,
            is_flagged=False,
        )
        units.append(u)

    return units


def _extract_symbol_body(text: str, start_offset: int, lines: List[str]) -> str:
    """Extract body text of a code symbol by indentation heuristic."""
    body_offset = text[:start_offset].count("\n")
    if body_offset >= len(lines):
        return ""
    indent = len(lines[body_offset]) - len(lines[body_offset].lstrip()) if body_offset < len(lines) else 0
    body_lines: List[str] = []
    for line in lines[body_offset:]:
        if line.strip() == "":
            continue
        if len(line) - len(line.lstrip()) <= indent and line.strip() and not line.strip().startswith(("#", "@", ")")):
            break
        body_lines.append(line)
    return "\n".join(body_lines)


# ---------------------------------------------------------------------------
# JavaScript / TypeScript code symbols
# ---------------------------------------------------------------------------

JS_EXPORT_CLASS_RE = re.compile(
    r"^(export\s+)?(default\s+)?class\s+(\w+)", re.MULTILINE
)
JS_EXPORT_FUNC_RE = re.compile(
    r"^(export\s+)?(async\s+)?function\s+(\w+)", re.MULTILINE
)
JS_EXPORT_ARROW_RE = re.compile(
    r"^(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s*)?\(?.*\)?\s*=>", re.MULTILINE
)


def _extract_jsts(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    if is_flagged:
        title = Path(source_path).name
        u = _build_unit_body(
            unit_id=_unit_id(sha, "js-root"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=title,
            semantic_text="",
            token_count=0,
            sha=sha,
            fingerprints_structural="js-flagged",
            run_id=run_id,
            now=now,
            is_flagged=True,
        )
        return [u]

    units: List[ExtractedUnit] = []
    lines = text.splitlines()
    title = Path(source_path).name

    for m in JS_EXPORT_CLASS_RE.finditer(text):
        cls_name = m.group(3)
        line_start = text[: m.start()].count("\n") + 1
        summary = f"JS/TS class '{cls_name}'"
        u = _build_unit_body(
            unit_id=_unit_id(sha, _slugify(f"class:{cls_name}")),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=f"{title}/{cls_name}",
            semantic_text=summary,
            token_count=len(summary.split()),
            sha=sha,
            fingerprints_structural=json.dumps({"symbol_type": "class", "name": cls_name}, sort_keys=True),
            run_id=run_id,
            now=now,
            is_flagged=False,
            dedup_group_extra=f"js-class-{cls_name}",
            source_anchor={
                "section": f"{title}@{cls_name}",
                "line_start": line_start,
                "line_end": line_start,
                "json_pointer": None,
            },
        )
        units.append(u)

    for m in JS_EXPORT_FUNC_RE.finditer(text):
        func_name = m.group(3)
        line_start = text[: m.start()].count("\n") + 1
        summary = f"JS/TS function '{func_name}'"
        u = _build_unit_body(
            unit_id=_unit_id(sha, _slugify(f"func:{func_name}")),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=f"{title}/{func_name}",
            semantic_text=summary,
            token_count=len(summary.split()),
            sha=sha,
            fingerprints_structural=json.dumps({"symbol_type": "function", "name": func_name}, sort_keys=True),
            run_id=run_id,
            now=now,
            is_flagged=False,
            dedup_group_extra=f"js-func-{func_name}",
            source_anchor={
                "section": f"{title}@{func_name}",
                "line_start": line_start,
                "line_end": line_start,
                "json_pointer": None,
            },
        )
        units.append(u)

    for m in JS_EXPORT_ARROW_RE.finditer(text):
        var_name = m.group(3)
        line_start = text[: m.start()].count("\n") + 1
        summary = f"JS/TS arrow function '{var_name}'"
        u = _build_unit_body(
            unit_id=_unit_id(sha, _slugify(f"arrow:{var_name}")),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=f"{title}/{var_name}",
            semantic_text=summary,
            token_count=len(summary.split()),
            sha=sha,
            fingerprints_structural=json.dumps({"symbol_type": "arrow-function", "name": var_name}, sort_keys=True),
            run_id=run_id,
            now=now,
            is_flagged=False,
            dedup_group_extra=f"js-arrow-{var_name}",
            source_anchor={
                "section": f"{title}@{var_name}",
                "line_start": line_start,
                "line_end": line_start,
                "json_pointer": None,
            },
        )
        units.append(u)

    if not units:
        summary = f"JS/TS file: {title} ({len(lines)} lines)"
        u = _build_unit_body(
            unit_id=_unit_id(sha, "js-root"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="code-symbol",
            title=title,
            semantic_text=summary,
            token_count=len(summary.split()),
            sha=sha,
            fingerprints_structural="js-root",
            run_id=run_id,
            now=now,
            is_flagged=False,
        )
        units.append(u)

    return units


# ---------------------------------------------------------------------------
# Shell scripts
# ---------------------------------------------------------------------------

def _extract_shell(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    title = Path(source_path).name
    lines = text.splitlines()
    shebang = lines[0] if lines and lines[0].startswith("#!") else ""

    if not is_flagged:
        # Extract first comment block as docstring
        doc = ""
        for line in lines[1:]:
            if line.startswith("#"):
                doc += line.lstrip("#").strip() + "\n"
            else:
                break
        doc = doc.strip()[:500]
        summary = f"Script {title}: shebang={shebang}; doc={doc}" if doc else f"Script {title}: shebang={shebang}; {len(lines)} lines"
    else:
        summary = ""

    u = _build_unit_body(
        unit_id=_unit_id(sha, "script"),
        artifact_id=f"sha256:{sha}",
        occ_id=occ_id,
        source_id=source_id,
        source_path=source_path,
        unit_type="code-symbol",
        title=title,
        semantic_text=summary,
        token_count=len(summary.split()) if summary else 0,
        sha=sha,
        fingerprints_structural=json.dumps({"kind": "shell-script", "shebang": shebang}, sort_keys=True),
        run_id=run_id,
        now=now,
        is_flagged=is_flagged,
        source_anchor={
            "section": "script",
            "line_start": 1,
            "line_end": len(lines),
            "json_pointer": "/",
        },
    )
    return [u]


# ---------------------------------------------------------------------------
# n8n workflow
# ---------------------------------------------------------------------------

N8N_TRIGGER_TYPES = {
    "n8n-nodes-base.webhook": "webhook",
    "n8n-nodes-base.scheduleTrigger": "schedule",
    "n8n-nodes-base.cron": "schedule",
    "n8n-nodes-base.manualTrigger": "manual",
    "n8n-nodes-base.start": "manual",
    "n8n-nodes-base.executeWorkflowTrigger": "workflow",
    "n8n-nodes-base.emailTrigger": "email",
    "n8n-nodes-base.errorTrigger": "error",
}

AI_NODE_PREFIXES = (
    "@n8n/n8n-nodes-langchain.",
    "n8n-nodes-base.openAi",
    "n8n-nodes-base.anthropic",
    "n8n-nodes-base.googleGemini",
)

URL_RE = re.compile(r"https?://([a-zA-Z0-9.\-]+)")


def _looks_like_n8n(text: str) -> bool:
    """Cheap heuristic: is this JSON a plausible n8n workflow?"""
    try:
        wf = json.loads(text)
    except Exception:
        return False
    if not isinstance(wf, dict):
        return False
    return bool(wf.get("name")) and "nodes" in wf and "connections" in wf


def _extract_n8n_workflow(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    try:
        wf = json.loads(text)
    except Exception:
        return []

    if not isinstance(wf, dict) or "name" not in wf or "nodes" not in wf:
        return []

    nodes = wf.get("nodes", []) or []
    connections = wf.get("connections", {}) or {}

    # Node types
    node_types = sorted({n.get("type", "unknown") for n in nodes if isinstance(n, dict)})

    # Triggers
    trigger_types = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type", "")
        if t in N8N_TRIGGER_TYPES:
            trigger_types.add(N8N_TRIGGER_TYPES[t])
        elif "Trigger" in t:
            trigger_types.add(t.split(".")[-1])
    trigger_types = sorted(trigger_types)

    # AI components
    ai_components = sorted({
        n.get("type", "")
        for n in nodes
        if isinstance(n, dict) and n.get("type", "").startswith(AI_NODE_PREFIXES)
    })

    # Credentials (types only)
    cred_types = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        creds = n.get("credentials", {}) or {}
        if isinstance(creds, dict):
            cred_types.update(k for k in creds if isinstance(k, str))
        elif isinstance(creds, list):
            for c in creds:
                if isinstance(c, dict):
                    cred_types.add(c.get("name", "unknown"))
    credential_types = sorted(cred_types)

    # External hosts
    hosts = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        params = n.get("parameters", {}) or {}
        if isinstance(params, dict):
            for v in params.values():
                if isinstance(v, str):
                    hosts.update(URL_RE.findall(v))
    external_hosts = sorted(hosts)

    # Count edges
    edge_count = sum(
        len(conn_list)
        for conn_list in connections.values()
        if isinstance(conn_list, list)
    )

    title = wf.get("name", Path(source_path).name)

    if not is_flagged:
        summary = (
            f"n8n workflow '{title}' with {len(nodes)} nodes "
            f"and {edge_count} connections. "
            f"Triggers: {','.join(trigger_types) or 'none'}. "
            f"AI components: {','.join(ai_components) or 'none'}. "
            f"Credential types: {','.join(credential_types) or 'none'}."
        )
    else:
        summary = ""

    topology_key = f"{len(nodes)}n-{edge_count}e"

    u = _build_unit_body(
        unit_id=_unit_id(sha, "workflow"),
        artifact_id=f"sha256:{sha}",
        occ_id=occ_id,
        source_id=source_id,
        source_path=source_path,
        unit_type="n8n-workflow",
        title=title,
        semantic_text=summary,
        token_count=len(summary.split()) if summary else 0,
        sha=sha,
        fingerprints_structural=topology_key,
        run_id=run_id,
        now=now,
        is_flagged=is_flagged,
        dedup_group_extra=f"sha256:{hashlib.sha256(topology_key.encode()).hexdigest()}",
        source_anchor={
            "section": "workflow",
            "line_start": 1,
            "line_end": 1,
            "json_pointer": "/",
        },
    )
    return [u]


# ---------------------------------------------------------------------------
# Hermes skill / soul
# ---------------------------------------------------------------------------

def _extract_hermes_skill(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    """Extract SKILL.md as a hermes-skill doc-section."""
    units = _extract_doc_sections(
        sha, source_id, source_path, safe_source, occ_id, text,
        run_id, now, is_flagged, artifact,
    )
    for u in units:
        u["unit_type"] = "hermes-skill"
    return units


def _extract_hermes_soul(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    """Extract SOUL.md as a hermes-soul doc-section."""
    units = _extract_doc_sections(
        sha, source_id, source_path, safe_source, occ_id, text,
        run_id, now, is_flagged, artifact,
    )
    for u in units:
        u["unit_type"] = "hermes-soul"
    return units


# ---------------------------------------------------------------------------
# Generic text fallback — paragraph chunks
# ---------------------------------------------------------------------------

PARAGRAPH_SEPARATOR_RE = re.compile(r"\n\s*\n")


def _extract_text_fallback(
    sha, source_id, source_path, safe_source, occ_id, text,
    run_id, now, is_flagged, artifact,
) -> List[ExtractedUnit]:
    """Split text by paragraph boundaries, one unit per paragraph."""
    if not text.strip():
        return []

    paragraphs = PARAGRAPH_SEPARATOR_RE.split(text.strip())

    units: List[ExtractedUnit] = []
    for i, para in enumerate(paragraphs):
        para = para.strip()
        if not para:
            continue
        if len(para) > MAX_SEMANTIC_TEXT_LEN:
            para = para[:MAX_SEMANTIC_TEXT_LEN] + "..."

        title = f"{Path(source_path).name} §{i + 1}"
        summary = para[:500]

        u = _build_unit_body(
            unit_id=_unit_id(sha, f"para:{i + 1}"),
            artifact_id=f"sha256:{sha}",
            occ_id=occ_id,
            source_id=source_id,
            source_path=source_path,
            unit_type="doc-section",
            title=title,
            semantic_text=summary if not is_flagged else "",
            token_count=len(para.split()) if not is_flagged else 0,
            sha=sha,
            fingerprints_structural=f"para-{i}",
            run_id=run_id,
            now=now,
            is_flagged=is_flagged,
            source_anchor={
                "section": f"para:{i + 1}",
                "line_start": 1,
                "line_end": 1,
                "json_pointer": None,
            },
        )
        units.append(u)

    return units
