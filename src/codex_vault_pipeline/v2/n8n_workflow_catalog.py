# Codex Vault Pipeline — n8n workflow catalog scanner
"""Deterministic catalog scanner for n8n workflow JSON corpora.

Walks the four raw n8n directories, classifies every JSON file,
extracts schema fields, detects content-hash duplicates, performs
credential-aware security analysis, and writes:

  - catalog.jsonl          one workflow per line
  - summary.json           aggregate stats
  - validation_report.json per-file errors / warnings

Credential semantics:
  - credential_type_present: node declares/references a credential type
  - credential_reference_present: normal n8n credential ref (id/name/type)
  - credential_value_present: actual credential-like value in JSON
  - secret_value_detected: high-confidence secret/token/key
  - security_state: clean | credential_refs_only | potential_secret_leak | blocked | not_scanned

Designed to be invoked via:
    codex-vault v2 n8n catalog --vault-root ... [--output-dir ...]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Known credential-bearing node types ────────────────────────────
_CREDENTIAL_NODE_TYPES: set[str] = {
    "n8n-nodes-base.httpRequest",
    "n8n-nodes-base.slack",
    "n8n-nodes-base.gmail",
    "n8n-nodes-base.googleSheets",
    "n8n-nodes-base.airtable",
    "n8n-nodes-base.notion",
    "n8n-nodes-base.discord",
    "n8n-nodes-base.telegram",
    "n8n-nodes-base.stripe",
    "n8n-nodes-base.salesforce",
    "n8n-nodes-base.hubspot",
    "n8n-nodes-base.postgres",
    "n8n-nodes-base.mysql",
    "n8n-nodes-base.mongoDb",
    "n8n-nodes-base.redis",
    "n8n-nodes-base.awsS3",
    "n8n-nodes-base.microsoftOutlook",
    "n8n-nodes-base.microsoftTeams",
    "n8n-nodes-base.gitHub",
    "n8n-nodes-base.gitLab",
    "n8n-nodes-base.jira",
    "n8n-nodes-base.linear",
    "n8n-nodes-base.supabase",
    "n8n-nodes-base.pinecone",
    "n8n-nodes-base.weaviate",
    "n8n-nodes-base.qdrant",
    "n8n-nodes-base.zapier",
    "n8n-nodes-base.make",
    "n8n-nodes-base.shopify",
    "n8n-nodes-base.woocommerce",
    "n8n-nodes-base.paypal",
}

# n8n credential reference keys that appear in normal workflow JSON
# These are structural keys, not secret values.
_N8N_CREDENTIAL_KEYS = {"credentials", "credential", "id", "name", "type"}

# ── High-confidence secret detection patterns ──────────────────────
# These match actual secret VALUES, not structural keys or type names.
_SECRET_VALUE_PATTERNS: list[re.Pattern] = [
    # Private key blocks
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
    # OpenAI / generic sk- keys
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    # GitHub tokens
    re.compile(r"\bghp_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    # Slack tokens
    re.compile(r"\bxoxb-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bxoxp-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bxoxo-[A-Za-z0-9-]{10,}\b"),
    # AWS access keys (AKIA...)
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Generic long bearer/high-entropy tokens in value positions
    # Matches "Bearer <hex-or-alphanumeric 40+ chars>"
    re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.]{40,}\b"),
    # Generic high-entropy hex strings 64+ chars (likely hashes/tokens)
    # Only match in value-like positions (after colon or equals)
    re.compile(r'[:=]\s*["\']?[A-Fa-f0-9]{64,}["\']?'),
]

# Patterns that are NOT secrets — just structural n8n metadata
_FALSE_POSITIVE_PATTERNS: list[re.Pattern] = [
    # n8n credential reference structure (id + name)
    re.compile(r'"credentials"\s*:\s*\{'),
    # Workflow/template IDs (numeric strings)
    re.compile(r'"id"\s*:\s*"\d{1,10}"'),
    # Node type names
    re.compile(r'"type"\s*:\s*"n8n-nodes-base\.'),
    # UUIDs (node IDs, webhook IDs)
    re.compile(r'"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"'),
    # Version IDs
    re.compile(r'"versionId"\s*:\s*"[a-f0-9]{8,}"'),
    # Instance IDs
    re.compile(r'"instanceId"\s*:\s*"[a-f0-9]{8,}"'),
]

# ── Placeholder / non-secret value patterns ───────────────────────
# Values that look like secrets but are actually placeholders.
_PLACEHOLDER_VALUES: set[str] = {
    "", '""', "''", "null", "undefined", "YOUR_API_KEY", "your-api-key",
    "YOUR_API_KEY_HERE", "your_api_key_here", "REPLACE_ME", "replace_me",
    "token_here", "insert_token", "xxx", "YYYY", "CHANGE_ME", "TODO",
    "fill_in", "example", "test", "demo", "sample", "placeholder",
    "sk-xxx", "sk-test", "ghp_xxx", "xoxb-xxx",
}


# ── Raw source directories (relative to vault root /raw/) ──────────
_RAW_N8N_SOURCES: dict[str, dict[str, str]] = {
    "n8n-workflows": {
        "slug": "n8n-workflows",
        "label": "Zie619/n8n-workflows",
        "glob": "raw/n8n-workflows/**/*.json",
    },
    "n8nworkflows-xyz": {
        "slug": "n8nworkflows-xyz",
        "label": "nusquama/n8nworkflows-xyz",
        "glob": "raw/n8nworkflows-xyz/**/*.json",
    },
    "n8n-free-templates": {
        "slug": "n8n-free-templates",
        "label": "wassupjay/n8n-free-templates",
        "glob": "raw/n8n-free-templates/**/*.json",
    },
    "awesome-n8n-templates": {
        "slug": "awesome-n8n-templates",
        "label": "enescingoz/awesome-n8n-templates",
        "glob": "raw/awesome-n8n-templates/**/*.json",
    },
}


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class WorkflowEntry:
    """Single cataloged workflow — schema fields with credential semantics."""
    source_slug: str
    source_label: str
    source_path: str          # relative to vault root
    content_hash: str         # SHA-256 of file bytes
    classification: str       # workflow | metadata | invalid | unknown
    workflow_id: Optional[str] = None
    name: Optional[str] = None
    node_count: int = 0
    node_types: list[str] = field(default_factory=list)
    trigger_types: list[str] = field(default_factory=list)
    has_ai_components: bool = False
    ai_model_refs: list[str] = field(default_factory=list)

    # Credential semantics (corrected)
    credential_types_used: list[str] = field(default_factory=list)
    credential_type_present: bool = False       # node declares a credential type
    credential_reference_present: bool = False   # normal n8n credential ref structure
    credential_value_present: bool = False       # actual credential-like value found
    secret_value_detected: bool = False          # high-confidence secret/key/token
    security_state: str = "not_scanned"          # clean | credential_refs_only | potential_secret_leak | blocked | not_scanned

    # Deprecated alias (derived from security_state)
    @property
    def credential_security_flag(self) -> bool:
        """Deprecated: use security_state instead."""
        return self.security_state in ("potential_secret_leak", "blocked")

    external_hosts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    active: Optional[bool] = None
    version_id: Optional[str] = None
    error_summary: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    file_size_bytes: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Add deprecated alias for backward compat
        d["credential_security_flag"] = self.credential_security_flag
        return d


@dataclass
class CatalogSummary:
    """Aggregate statistics for the catalog run."""
    total_files_scanned: int = 0
    total_workflows: int = 0
    total_metadata: int = 0
    total_invalid: int = 0
    total_unknown: int = 0
    total_duplicate_hashes: int = 0

    # Credential semantics counts
    workflows_with_credentials: int = 0         # has_credentials (any credential-bearing node)
    workflows_with_credential_types: int = 0    # credential_type_present
    workflows_with_credential_references: int = 0  # credential_reference_present
    workflows_with_credential_values: int = 0   # credential_value_present
    workflows_with_secret_values: int = 0       # secret_value_detected

    # Security state counts
    workflows_security_clean: int = 0
    workflows_credential_refs_only: int = 0
    workflows_potential_secret_leak: int = 0
    workflows_blocked: int = 0
    workflows_not_scanned: int = 0

    workflows_with_ai: int = 0
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)
    node_type_frequency: dict[str, int] = field(default_factory=dict)
    trigger_type_frequency: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    """Per-file validation findings."""
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"errors": self.errors, "warnings": self.warnings}


# ── Classification helpers ──────────────────────────────────────────

def _is_metadata_file(path: Path) -> bool:
    """Detect metada-*.json files from n8nworkflows-xyz."""
    return path.name.startswith("metada-") and path.suffix == ".json"


def _classify_json(data: Any, path: Path) -> str:
    """Classify a parsed JSON blob as workflow | metadata | invalid | unknown."""
    if _is_metadata_file(path):
        return "metadata"
    if not isinstance(data, dict):
        return "invalid"
    # Must have 'nodes' (list) and ideally 'name' to be a workflow
    has_nodes = isinstance(data.get("nodes"), list)
    has_name = isinstance(data.get("name"), str)
    has_connections = isinstance(data.get("connections"), dict)
    if has_nodes and (has_name or has_connections):
        return "workflow"
    # Could be a metadata-only object (user info, etc.)
    if "user_name" in data or "user_username" in data:
        return "metadata"
    return "unknown"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Credential detection ───────────────────────────────────────────

def _detect_credential_nodes(nodes: list[dict]) -> list[str]:
    """Return list of credential-bearing node type names found."""
    found: set[str] = set()
    for node in nodes:
        ntype = node.get("type", "")
        if ntype in _CREDENTIAL_NODE_TYPES:
            found.add(ntype)
    return sorted(found)


def _detect_credential_references(data: dict) -> tuple[bool, bool]:
    """Detect credential type presence and reference structure.

    Returns:
        (credential_type_present, credential_reference_present)

    credential_type_present: True when a node declares a credential type
    required by an n8n integration (detected via node type or credentials key).

    credential_reference_present: True when the workflow JSON contains the
    normal n8n credential reference structure: {"credentials": {"typeName": {"id": "...", "name": "..."}}}
    """
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list):
        return False, False

    type_present = False
    ref_present = False

    for node in nodes:
        # Check if node type is a known credential-bearing type
        ntype = node.get("type", "")
        if ntype in _CREDENTIAL_NODE_TYPES:
            type_present = True

        # Check for credential reference structure in node
        creds = node.get("credentials")
        if isinstance(creds, dict) and creds:
            type_present = True
            # Check if it has the normal ref structure (id/name)
            for cred_val in creds.values():
                if isinstance(cred_val, dict):
                    if "id" in cred_val or "name" in cred_val:
                        ref_present = True

    # Also check top-level credentials (some workflows store refs here)
    top_creds = data.get("credentials")
    if isinstance(top_creds, dict) and top_creds:
        ref_present = True

    return type_present, ref_present


def _is_placeholder_value(val: str) -> bool:
    """Check if a string value is a placeholder, not an actual secret."""
    if not val:
        return True
    stripped = val.strip().strip("\"'")
    if stripped.lower() in {p.lower() for p in _PLACEHOLDER_VALUES}:
        return True
    # Very short values are likely not secrets
    if len(stripped) < 8:
        return True
    return False


def _scan_for_secret_values(raw_text: str) -> tuple[bool, bool]:
    """Scan raw JSON text for actual credential values and secrets.

    Returns:
        (credential_value_present, secret_value_detected)

    credential_value_present: True when an actual credential-like value appears
    (not just a reference or type name).

    secret_value_detected: True only when a high-confidence secret/token/key
    is detected.
    """
    credential_value = False
    secret_detected = False

    # Check for high-confidence secret patterns
    for pat in _SECRET_VALUE_PATTERNS:
        match = pat.search(raw_text)
        if match:
            matched_text = match.group(0)
            # Skip if it's a placeholder
            if not _is_placeholder_value(matched_text):
                secret_detected = True
                credential_value = True
                break

    return credential_value, secret_detected


def _determine_security_state(
    cred_type_present: bool,
    cred_ref_present: bool,
    cred_value_present: bool,
    secret_detected: bool,
) -> str:
    """Determine the security state from credential analysis results."""
    if secret_detected:
        return "potential_secret_leak"
    if cred_value_present:
        return "potential_secret_leak"
    if cred_ref_present or cred_type_present:
        return "credential_refs_only"
    return "clean"


def _detect_trigger_types(nodes: list[dict]) -> list[str]:
    """Extract unique trigger node types."""
    triggers: set[str] = set()
    for node in nodes:
        ntype = node.get("type", "")
        if "trigger" in ntype.lower() or "webhook" in ntype.lower():
            triggers.add(ntype)
    return sorted(triggers)


def _detect_ai_components(nodes: list[dict]) -> tuple[bool, list[str]]:
    """Detect AI/LLM node types and model references."""
    ai_nodes: list[str] = []
    model_refs: list[str] = []
    ai_patterns = [
        "openai", "anthropic", "gemini", "llm", "chat",
        "agent", "langchain", "ai", "gpt", "claude",
    ]
    for node in nodes:
        ntype = node.get("type", "")
        ntype_lower = ntype.lower()
        if any(p in ntype_lower for p in ai_patterns):
            ai_nodes.append(ntype)
        # Check parameters for model references
        params = node.get("parameters", {})
        if isinstance(params, dict):
            model = params.get("model") or params.get("modelName") or params.get("modelId")
            if model and isinstance(model, str):
                model_refs.append(model)
    return bool(ai_nodes), sorted(set(model_refs))


def _detect_external_hosts(nodes: list[dict]) -> list[str]:
    """Extract external hostnames from node parameters."""
    hosts: set[str] = set()
    for node in nodes:
        params = node.get("parameters", {})
        if not isinstance(params, dict):
            continue
        for key in ("url", "endpoint", "host", "baseUrl", "baseURL"):
            val = params.get(key)
            if isinstance(val, str) and val.startswith("http"):
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(val)
                    if parsed.hostname:
                        hosts.add(parsed.hostname)
                except Exception:
                    pass
    return sorted(hosts)


def _extract_tags(data: dict) -> list[str]:
    """Extract tags from workflow JSON."""
    tags_raw = data.get("tags", [])
    if not isinstance(tags_raw, list):
        return []
    result: list[str] = []
    for t in tags_raw:
        if isinstance(t, dict):
            result.append(t.get("name", str(t)))
        elif isinstance(t, str):
            result.append(t)
    return result


# ── Main scanner ───────────────────────────────────────────────────

class N8nWorkflowCatalogScanner:
    """Scan raw n8n directories and build a workflow catalog."""

    def __init__(self, vault_root: Path, output_dir: Optional[Path] = None):
        self.vault_root = vault_root
        self.output_dir = output_dir or vault_root / ".runtime" / "domain" / "n8n-workflows"
        self.entries: list[WorkflowEntry] = []
        self.summary = CatalogSummary()
        self.validation = ValidationReport()
        self._seen_hashes: dict[str, list[str]] = {}  # hash → [paths]

    def scan(self) -> CatalogSummary:
        """Run the full scan. Returns summary."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        for source_key, source_info in _RAW_N8N_SOURCES.items():
            self._scan_source(source_key, source_info)

        self._compute_duplicates()
        self._compute_summary()
        return self.summary

    def _scan_source(self, source_key: str, source_info: dict) -> None:
        """Scan one raw source directory."""
        glob_pattern = source_info["glob"]
        json_files = sorted(self.vault_root.glob(glob_pattern))

        source_stats = {
            "total_files": len(json_files),
            "workflows": 0,
            "metadata": 0,
            "invalid": 0,
            "unknown": 0,
        }

        for jpath in json_files:
            self._scan_file(jpath, source_info, source_stats)

        self.summary.per_source[source_key] = source_stats

    def _scan_file(
        self,
        path: Path,
        source_info: dict,
        source_stats: dict[str, int],
    ) -> None:
        """Classify and extract one JSON file."""
        rel_path = str(path.relative_to(self.vault_root))

        try:
            raw_bytes = path.read_bytes()
        except Exception as exc:
            self.validation.errors.append({
                "path": rel_path,
                "error": f"read_error: {exc}",
            })
            source_stats["invalid"] += 1
            return

        content_hash = _sha256_bytes(raw_bytes)
        file_size = len(raw_bytes)

        # Track hash for duplicate detection
        self._seen_hashes.setdefault(content_hash, []).append(rel_path)

        # Parse JSON
        try:
            data = json.loads(raw_bytes)
        except json.JSONDecodeError as exc:
            self.validation.errors.append({
                "path": rel_path,
                "error": f"json_parse_error: {exc}",
            })
            entry = WorkflowEntry(
                source_slug=source_info["slug"],
                source_label=source_info["label"],
                source_path=rel_path,
                content_hash=content_hash,
                classification="invalid",
                file_size_bytes=file_size,
                error_summary=f"json_parse_error: {exc}",
                security_state="not_scanned",
            )
            self.entries.append(entry)
            source_stats["invalid"] += 1
            return

        # Classify
        classification = _classify_json(data, path)
        source_stats[classification] = source_stats.get(classification, 0) + 1

        # Build entry
        entry = WorkflowEntry(
            source_slug=source_info["slug"],
            source_label=source_info["label"],
            source_path=rel_path,
            content_hash=content_hash,
            classification=classification,
            file_size_bytes=file_size,
        )

        if classification == "workflow":
            self._extract_workflow_fields(entry, data, raw_bytes)
        elif classification == "metadata":
            entry.warnings.append("metadata_file_not_workflow")
            entry.security_state = "not_scanned"
        elif classification == "unknown":
            entry.warnings.append("unrecognized_json_structure")
            entry.security_state = "not_scanned"

        self.entries.append(entry)

    def _extract_workflow_fields(
        self,
        entry: WorkflowEntry,
        data: dict,
        raw_bytes: bytes,
    ) -> None:
        """Extract all workflow-specific fields with credential semantics."""
        entry.workflow_id = data.get("id")
        entry.name = data.get("name")
        entry.active = data.get("active")
        entry.version_id = data.get("versionId")

        nodes = data.get("nodes", [])
        if not isinstance(nodes, list):
            entry.warnings.append("nodes_not_list")
            entry.security_state = "clean"
            return

        entry.node_count = len(nodes)
        entry.node_types = sorted({n.get("type", "") for n in nodes if n.get("type")})
        entry.trigger_types = _detect_trigger_types(nodes)

        has_ai, model_refs = _detect_ai_components(nodes)
        entry.has_ai_components = has_ai
        entry.ai_model_refs = model_refs

        # Credential analysis (corrected semantics)
        cred_nodes = _detect_credential_nodes(nodes)
        entry.credential_types_used = cred_nodes

        type_present, ref_present = _detect_credential_references(data)
        entry.credential_type_present = type_present
        entry.credential_reference_present = ref_present

        # Secret/value detection on raw text
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        cred_value, secret_detected = _scan_for_secret_values(raw_text)
        entry.credential_value_present = cred_value
        entry.secret_value_detected = secret_detected

        # Determine security state
        entry.security_state = _determine_security_state(
            type_present, ref_present, cred_value, secret_detected,
        )

        entry.external_hosts = _detect_external_hosts(nodes)
        entry.tags = _extract_tags(data)

    def _compute_duplicates(self) -> None:
        """Mark duplicate hashes."""
        dup_count = 0
        for h, paths in self._seen_hashes.items():
            if len(paths) > 1:
                dup_count += len(paths) - 1
                for p in paths:
                    for e in self.entries:
                        if e.source_path == p and e.classification == "workflow":
                            e.warnings.append(
                                f"duplicate_of:{paths[0] if paths[0] != p else paths[1]}"
                            )
        self.summary.total_duplicate_hashes = dup_count

    def _compute_summary(self) -> None:
        """Roll up statistics from entries."""
        self.summary.total_files_scanned = len(self.entries)
        for e in self.entries:
            if e.classification == "workflow":
                self.summary.total_workflows += 1
            elif e.classification == "metadata":
                self.summary.total_metadata += 1
            elif e.classification == "invalid":
                self.summary.total_invalid += 1
            elif e.classification == "unknown":
                self.summary.total_unknown += 1

            # Credential counts
            if e.credential_types_used:
                self.summary.workflows_with_credentials += 1
            if e.credential_type_present:
                self.summary.workflows_with_credential_types += 1
            if e.credential_reference_present:
                self.summary.workflows_with_credential_references += 1
            if e.credential_value_present:
                self.summary.workflows_with_credential_values += 1
            if e.secret_value_detected:
                self.summary.workflows_with_secret_values += 1

            # Security state counts
            state = e.security_state
            if state == "clean":
                self.summary.workflows_security_clean += 1
            elif state == "credential_refs_only":
                self.summary.workflows_credential_refs_only += 1
            elif state == "potential_secret_leak":
                self.summary.workflows_potential_secret_leak += 1
            elif state == "blocked":
                self.summary.workflows_blocked += 1
            elif state == "not_scanned":
                self.summary.workflows_not_scanned += 1

            if e.has_ai_components:
                self.summary.workflows_with_ai += 1

            for nt in e.node_types:
                self.summary.node_type_frequency[nt] = (
                    self.summary.node_type_frequency.get(nt, 0) + 1
                )
            for tt in e.trigger_types:
                self.summary.trigger_type_frequency[tt] = (
                    self.summary.trigger_type_frequency.get(tt, 0) + 1
                )

    # ── Output writers ─────────────────────────────────────────────

    def write_catalog_jsonl(self) -> Path:
        """Write catalog.jsonl — one workflow per line."""
        out = self.output_dir / "catalog.jsonl"
        with open(out, "w") as f:
            for entry in self.entries:
                f.write(json.dumps(entry.to_dict()) + "\n")
        return out

    def write_summary_json(self) -> Path:
        """Write summary.json."""
        out = self.output_dir / "summary.json"
        with open(out, "w") as f:
            json.dump(self.summary.to_dict(), f, indent=2)
        return out

    def write_validation_report(self) -> Path:
        """Write validation_report.json."""
        out = self.output_dir / "validation_report.json"
        with open(out, "w") as f:
            json.dump(self.validation.to_dict(), f, indent=2)
        return out

    def write_all(self) -> dict[str, Path]:
        """Write all output files. Returns paths."""
        return {
            "catalog": self.write_catalog_jsonl(),
            "summary": self.write_summary_json(),
            "validation": self.write_validation_report(),
        }


# ── CLI entrypoint ─────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for `codex-vault v2 n8n catalog`."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Scan n8n workflow JSONs and build a catalog"
    )
    ap.add_argument(
        "--vault-root",
        required=True,
        help="Path to codex-vault root",
    )
    ap.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: .runtime/domain/n8n-workflows/)",
    )
    args = ap.parse_args(argv)

    vault_root = Path(args.vault_root)
    if not vault_root.is_dir():
        print(f"ERROR: vault root not found: {vault_root}", flush=True)
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else None

    scanner = N8nWorkflowCatalogScanner(vault_root, output_dir)
    summary = scanner.scan()
    paths = scanner.write_all()

    # Print summary to stdout
    print(f"\n=== n8n Workflow Catalog ===", flush=True)
    print(f"Files scanned:       {summary.total_files_scanned}", flush=True)
    print(f"Workflows:           {summary.total_workflows}", flush=True)
    print(f"Metadata files:      {summary.total_metadata}", flush=True)
    print(f"Invalid:             {summary.total_invalid}", flush=True)
    print(f"Unknown:             {summary.total_unknown}", flush=True)
    print(f"Duplicate hashes:    {summary.total_duplicate_hashes}", flush=True)
    print(f"\n--- Credential Semantics ---", flush=True)
    print(f"Has credentials:     {summary.workflows_with_credentials}", flush=True)
    print(f"Credential types:    {summary.workflows_with_credential_types}", flush=True)
    print(f"Credential refs:     {summary.workflows_with_credential_references}", flush=True)
    print(f"Credential values:   {summary.workflows_with_credential_values}", flush=True)
    print(f"Secret values:       {summary.workflows_with_secret_values}", flush=True)
    print(f"\n--- Security State ---", flush=True)
    print(f"Clean:               {summary.workflows_security_clean}", flush=True)
    print(f"Credential refs only:{summary.workflows_credential_refs_only}", flush=True)
    print(f"Potential leak:      {summary.workflows_potential_secret_leak}", flush=True)
    print(f"Blocked:             {summary.workflows_blocked}", flush=True)
    print(f"Not scanned:         {summary.workflows_not_scanned}", flush=True)
    print(f"\n--- Other ---", flush=True)
    print(f"With AI components:  {summary.workflows_with_ai}", flush=True)
    print(f"\nPer-source breakdown:", flush=True)
    for src, stats in summary.per_source.items():
        print(f"  {src}: {stats}", flush=True)
    print(f"\nTop 10 node types:", flush=True)
    sorted_nt = sorted(
        summary.node_type_frequency.items(), key=lambda x: -x[1]
    )[:10]
    for nt, cnt in sorted_nt:
        print(f"  {nt}: {cnt}", flush=True)
    print(f"\nTop trigger types:", flush=True)
    sorted_tt = sorted(
        summary.trigger_type_frequency.items(), key=lambda x: -x[1]
    )[:5]
    for tt, cnt in sorted_tt:
        print(f"  {tt}: {cnt}", flush=True)
    print(f"\nOutput files:", flush=True)
    for name, p in paths.items():
        print(f"  {name}: {p}", flush=True)

    if scanner.validation.errors:
        print(f"\nValidation errors: {len(scanner.validation.errors)}", flush=True)
        for err in scanner.validation.errors[:10]:
            print(f"  {err['path']}: {err['error']}", flush=True)

    print(f"\nCounts apply only to acquired raw workflow files.", flush=True)
    return 0
