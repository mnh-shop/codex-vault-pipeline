# Codex Vault Pipeline — tests for n8n workflow catalog scanner
"""Tests for v2/n8n_workflow_catalog.py — with credential semantics."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest


# ── Fixtures ────────────────────────────────────────────────────────

WORKFLOW_MINIMAL = {
    "name": "Test Workflow",
    "nodes": [
        {
            "id": "n1",
            "name": "Start",
            "type": "n8n-nodes-base.manualTrigger",
            "position": [250, 300],
        },
        {
            "id": "n2",
            "name": "Set",
            "type": "n8n-nodes-base.set",
            "position": [450, 300],
            "parameters": {"value": "test"},
        },
    ],
    "connections": {},
    "active": True,
    "id": "wf-001",
    "tags": [{"name": "test"}, {"name": "api"}],
}

# Normal n8n credential reference — NOT a secret leak
WORKFLOW_WITH_CREDENTIAL_REFS = {
    "name": "Google Sheets Workflow",
    "nodes": [
        {
            "id": "n1",
            "name": "Start",
            "type": "n8n-nodes-base.manualTrigger",
            "position": [250, 300],
        },
        {
            "id": "n2",
            "name": "Google Sheets",
            "type": "n8n-nodes-base.googleSheets",
            "position": [450, 300],
            "parameters": {"operation": "read"},
            "credentials": {
                "googleSheetsOAuth2Api": {
                    "id": "123",
                    "name": "My Google Sheets Account",
                }
            },
        },
    ],
    "connections": {},
    "id": "wf-002",
}

# Workflow with actual secret value — IS a potential leak
WORKFLOW_WITH_SECRET = {
    "name": "Secret Workflow",
    "nodes": [
        {
            "id": "n1",
            "name": "Start",
            "type": "n8n-nodes-base.manualTrigger",
            "position": [250, 300],
        },
        {
            "id": "n2",
            "name": "HTTP Request",
            "type": "n8n-nodes-base.httpRequest",
            "position": [450, 300],
            "parameters": {
                "url": "https://api.example.com/data",
                "authentication": "genericCredentialType",
                "headers": {
                    "parameters": {
                        "name": "Authorization",
                        "value": "Bearer sk-abc123def456ghi789jkl012mno345pqr678stu901",
                    }
                },
            },
        },
    ],
    "connections": {},
    "id": "wf-003",
}

# Placeholder values — should NOT trigger secret detection
WORKFLOW_WITH_PLACEHOLDER = {
    "name": "Placeholder Workflow",
    "nodes": [
        {
            "id": "n1",
            "name": "Start",
            "type": "n8n-nodes-base.manualTrigger",
            "position": [250, 300],
        },
        {
            "id": "n2",
            "name": "HTTP Request",
            "type": "n8n-nodes-base.httpRequest",
            "position": [450, 300],
            "parameters": {
                "url": "https://api.example.com/data",
                "headers": {
                    "parameters": {
                        "name": "Authorization",
                        "value": "Bearer YOUR_API_KEY_HERE",
                    }
                },
            },
        },
    ],
    "connections": {},
    "id": "wf-004",
}

WORKFLOW_WITH_AI = {
    "name": "AI Agent Workflow",
    "nodes": [
        {
            "id": "n1",
            "name": "Chat Trigger",
            "type": "n8n-nodes-base.chatTrigger",
            "position": [250, 300],
        },
        {
            "id": "n2",
            "name": "OpenAI Agent",
            "type": "@n8n/n8n-nodes-langchain.agent",
            "position": [450, 300],
            "parameters": {"model": "gpt-4"},
        },
    ],
    "connections": {},
    "id": "wf-005",
}

METADATA_FILE = {
    "user_name": "TestUser",
    "user_username": "testuser",
    "user_bio": "A test user",
}

WORKFLOW_STRING_TAGS = {
    "name": "Tagged Workflow",
    "nodes": [
        {
            "id": "n1",
            "name": "Start",
            "type": "n8n-nodes-base.manualTrigger",
            "position": [250, 300],
        },
    ],
    "connections": {},
    "tags": ["automation", "production"],
}


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    """Create a mini raw directory structure for testing."""
    # n8n-workflows source
    n8n_ww = tmp_path / "raw" / "n8n-workflows" / "workflows" / "TestCategory"
    n8n_ww.mkdir(parents=True)
    (n8n_ww / "workflow1.json").write_text(json.dumps(WORKFLOW_MINIMAL))
    (n8n_ww / "workflow2.json").write_text(json.dumps(WORKFLOW_WITH_AI))
    (n8n_ww / "workflow3.json").write_text(json.dumps(WORKFLOW_WITH_SECRET))
    (n8n_ww / "workflow4.json").write_text(json.dumps(WORKFLOW_WITH_PLACEHOLDER))

    # n8nworkflows-xyz source
    xyz = tmp_path / "raw" / "n8nworkflows-xyz" / "workflows" / "Test Workflow-123"
    xyz.mkdir(parents=True)
    (xyz / "test_workflow.json").write_text(json.dumps(WORKFLOW_WITH_CREDENTIAL_REFS))
    (xyz / "metada-123.json").write_text(json.dumps(METADATA_FILE))

    # n8n-free-templates source
    nft = tmp_path / "raw" / "n8n-free-templates" / "Category"
    nft.mkdir(parents=True)
    (nft / "template1.json").write_text(json.dumps(WORKFLOW_STRING_TAGS))

    # awesome-n8n-templates source
    ant = tmp_path / "raw" / "awesome-n8n-templates" / "Category"
    ant.mkdir(parents=True)
    (ant / "awesome1.json").write_text(json.dumps(WORKFLOW_MINIMAL))
    # One invalid JSON
    (ant / "bad.json").write_text("{not valid json {{{")

    return tmp_path


# ── Classification tests ───────────────────────────────────────────

class TestClassifyJson:
    """Test JSON classification logic."""

    def test_workflow_classification(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _classify_json
        assert _classify_json(WORKFLOW_MINIMAL, Path("test.json")) == "workflow"

    def test_metadata_classification(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _classify_json
        assert _classify_json(METADATA_FILE, Path("metada-123.json")) == "metadata"

    def test_metadata_via_name(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _classify_json
        assert _classify_json(METADATA_FILE, Path("anything.json")) == "metadata"

    def test_invalid_non_dict(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _classify_json
        assert _classify_json([1, 2, 3], Path("test.json")) == "invalid"

    def test_unknown_structure(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _classify_json
        assert _classify_json({"foo": "bar"}, Path("test.json")) == "unknown"

    def test_is_metadata_file(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _is_metadata_file
        assert _is_metadata_file(Path("metada-123.json")) is True
        assert _is_metadata_file(Path("workflow.json")) is False
        assert _is_metadata_file(Path("metadata.json")) is False


# ── Extraction tests ───────────────────────────────────────────────

class TestExtraction:
    """Test field extraction helpers."""

    def test_sha256(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _sha256_bytes
        h = _sha256_bytes(b"hello world")
        assert len(h) == 64
        assert h == hashlib.sha256(b"hello world").hexdigest()

    def test_detect_trigger_types(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_trigger_types
        triggers = _detect_trigger_types(WORKFLOW_MINIMAL["nodes"])
        assert "n8n-nodes-base.manualTrigger" in triggers

    def test_detect_trigger_types_empty(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_trigger_types
        assert _detect_trigger_types([]) == []

    def test_detect_ai_components(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_ai_components
        has_ai, models = _detect_ai_components(WORKFLOW_WITH_AI["nodes"])
        assert has_ai is True
        assert "gpt-4" in models

    def test_detect_ai_components_none(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_ai_components
        has_ai, models = _detect_ai_components(WORKFLOW_MINIMAL["nodes"])
        assert has_ai is False
        assert models == []

    def test_detect_credential_nodes(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_credential_nodes
        creds = _detect_credential_nodes(WORKFLOW_WITH_CREDENTIAL_REFS["nodes"])
        assert "n8n-nodes-base.googleSheets" in creds

    def test_detect_credential_nodes_none(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_credential_nodes
        creds = _detect_credential_nodes(WORKFLOW_MINIMAL["nodes"])
        assert creds == []

    def test_detect_external_hosts_with_url(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_external_hosts
        nodes = [{"parameters": {"url": "https://api.example.com/data"}}]
        hosts = _detect_external_hosts(nodes)
        assert "api.example.com" in hosts

    def test_detect_external_hosts_empty(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _detect_external_hosts
        assert _detect_external_hosts([]) == []

    def test_extract_tags_dict(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _extract_tags
        tags = _extract_tags(WORKFLOW_MINIMAL)
        assert tags == ["test", "api"]

    def test_extract_tags_string(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _extract_tags
        tags = _extract_tags(WORKFLOW_STRING_TAGS)
        assert tags == ["automation", "production"]

    def test_extract_tags_missing(self):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import _extract_tags
        assert _extract_tags({}) == []


# ── Credential semantics tests ─────────────────────────────────────

class TestCredentialSemantics:
    """Test the corrected credential/security semantics."""

    def test_credential_ref_not_secret_leak(self):
        """Normal n8n credential reference must NOT be flagged as a secret leak."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault(
            {"workflow.json": WORKFLOW_WITH_CREDENTIAL_REFS}
        ))
        scanner.scan()
        entries = [e for e in scanner.entries if e.classification == "workflow"]
        assert len(entries) == 1
        e = entries[0]
        # Credential references present
        assert e.credential_type_present is True
        assert e.credential_reference_present is True
        # No actual values or secrets
        assert e.credential_value_present is False
        assert e.secret_value_detected is False
        # Security state is refs-only, NOT leak
        assert e.security_state == "credential_refs_only"

    def test_google_sheets_credential_refs(self):
        """Google Sheets credential reference produces correct semantics."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault(
            {"workflow.json": WORKFLOW_WITH_CREDENTIAL_REFS}
        ))
        scanner.scan()
        entries = [e for e in scanner.entries if e.classification == "workflow"]
        e = entries[0]
        assert e.credential_type_present is True
        assert e.credential_reference_present is True
        assert e.credential_value_present is False
        assert e.secret_value_detected is False
        assert e.security_state == "credential_refs_only"

    def test_clean_workflow(self):
        """Workflow with no credentials results in security_state = clean."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault(
            {"workflow.json": WORKFLOW_MINIMAL}
        ))
        scanner.scan()
        entries = [e for e in scanner.entries if e.classification == "workflow"]
        e = entries[0]
        assert e.credential_type_present is False
        assert e.credential_reference_present is False
        assert e.credential_value_present is False
        assert e.secret_value_detected is False
        assert e.security_state == "clean"

    def test_actual_api_key_detected(self):
        """Actual API key/token value triggers potential_secret_leak."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault(
            {"workflow.json": WORKFLOW_WITH_SECRET}
        ))
        scanner.scan()
        entries = [e for e in scanner.entries if e.classification == "workflow"]
        e = entries[0]
        assert e.secret_value_detected is True
        assert e.credential_value_present is True
        assert e.security_state == "potential_secret_leak"

    def test_placeholder_not_secret(self):
        """Placeholder values do NOT trigger potential_secret_leak."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault(
            {"workflow.json": WORKFLOW_WITH_PLACEHOLDER}
        ))
        scanner.scan()
        entries = [e for e in scanner.entries if e.classification == "workflow"]
        e = entries[0]
        assert e.secret_value_detected is False
        assert e.credential_value_present is False
        assert e.security_state in ("clean", "credential_refs_only")

    def test_secret_never_in_output(self):
        """Secret values must never appear in catalog output."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        import tempfile
        out_dir = Path(tempfile.mkdtemp())
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault(
            {"workflow.json": WORKFLOW_WITH_SECRET}
        ), out_dir)
        scanner.scan()
        paths = scanner.write_all()
        # Read catalog and verify no secret value appears
        catalog_text = paths["catalog"].read_text()
        assert "sk-abc123def456" not in catalog_text
        assert "sk-" not in catalog_text or "sk-test" in catalog_text  # only placeholders

    def test_summary_distinguishes_refs_from_leaks(self):
        """Summary counts distinguish credential refs from potential leaks."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault({
            "clean.json": WORKFLOW_MINIMAL,
            "refs.json": WORKFLOW_WITH_CREDENTIAL_REFS,
            "leak.json": WORKFLOW_WITH_SECRET,
        }))
        summary = scanner.scan()
        assert summary.workflows_credential_refs_only >= 1
        assert summary.workflows_potential_secret_leak >= 1
        assert summary.workflows_security_clean >= 1
        assert summary.workflows_with_credential_references >= 1
        assert summary.workflows_with_secret_values >= 1

    def test_security_state_enum_values(self):
        """Security state must be one of the allowed values."""
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(_make_temp_vault({
            "a.json": WORKFLOW_MINIMAL,
            "b.json": WORKFLOW_WITH_CREDENTIAL_REFS,
            "c.json": WORKFLOW_WITH_SECRET,
        }))
        scanner.scan()
        allowed = {"clean", "credential_refs_only", "potential_secret_leak", "blocked", "not_scanned"}
        for e in scanner.entries:
            assert e.security_state in allowed, f"Invalid state: {e.security_state}"


# ── Scanner integration tests ─────────────────────────────────────

class TestScanner:
    """Integration tests using the sample directory."""

    def test_scan_summary(self, sample_dir: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(sample_dir)
        summary = scanner.scan()

        # n8n-workflows=4, n8nworkflows-xyz=2 (1 wf + 1 meta), n8n-free=1, awesome=2 (1 wf + 1 invalid)
        assert summary.total_files_scanned == 9
        assert summary.total_workflows >= 6
        assert summary.total_metadata >= 1
        assert summary.total_invalid >= 1

    def test_scan_writes_output(self, sample_dir: Path, tmp_path: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        out_dir = tmp_path / "output"
        scanner = N8nWorkflowCatalogScanner(sample_dir, out_dir)
        scanner.scan()
        paths = scanner.write_all()

        assert paths["catalog"].exists()
        assert paths["summary"].exists()
        assert paths["validation"].exists()

        lines = paths["catalog"].read_text().strip().split("\n")
        assert len(lines) >= 8

    def test_catalog_entry_fields(self, sample_dir: Path, tmp_path: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(sample_dir, tmp_path / "out")
        scanner.scan()

        workflows = [e for e in scanner.entries if e.classification == "workflow"]
        assert len(workflows) >= 6

        for wf in workflows:
            assert wf.source_slug != ""
            assert wf.content_hash != ""
            assert len(wf.content_hash) == 64
            assert wf.classification == "workflow"
            # New credential fields present
            assert hasattr(wf, "credential_type_present")
            assert hasattr(wf, "credential_reference_present")
            assert hasattr(wf, "credential_value_present")
            assert hasattr(wf, "secret_value_detected")
            assert hasattr(wf, "security_state")

    def test_duplicate_detection(self, sample_dir: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(sample_dir)
        scanner.scan()

        assert summary_has_duplicates(scanner.summary)

    def test_metadata_classification_in_xyz(self, sample_dir: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(sample_dir)
        scanner.scan()

        xyz_entries = [e for e in scanner.entries if e.source_slug == "n8nworkflows-xyz"]
        metada = [e for e in xyz_entries if e.classification == "metadata"]
        assert len(metada) >= 1

    def test_ai_detection(self, sample_dir: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(sample_dir)
        scanner.scan()

        ai_wf = [e for e in scanner.entries if e.has_ai_components]
        assert len(ai_wf) >= 1

    def test_credential_detection(self, sample_dir: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(sample_dir)
        scanner.scan()

        cred_wf = [e for e in scanner.entries if e.credential_type_present]
        assert len(cred_wf) >= 1

    def test_validation_errors(self, sample_dir: Path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import N8nWorkflowCatalogScanner
        scanner = N8nWorkflowCatalogScanner(sample_dir)
        scanner.scan()

        assert len(scanner.validation.errors) >= 1
        err_paths = [e["path"] for e in scanner.validation.errors]
        assert any("bad.json" in p for p in err_paths)


# ── CLI test ───────────────────────────────────────────────────────

class TestCLICatalog:
    """Test the CLI entrypoint."""

    def test_cli_catalog_build(self, sample_dir: Path, tmp_path: Path, monkeypatch):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import main
        out_dir = tmp_path / "catalog_out"
        result = main(["--vault-root", str(sample_dir), "--output-dir", str(out_dir)])
        assert result == 0
        assert (out_dir / "catalog.jsonl").exists()
        assert (out_dir / "summary.json").exists()

    def test_cli_catalog_bad_vault(self, tmp_path):
        from codex_vault_pipeline.v2.n8n_workflow_catalog import main
        result = main(["--vault-root", str(tmp_path / "nonexistent")])
        assert result == 1


# ── Helpers ────────────────────────────────────────────────────────

def _make_temp_vault(files: dict[str, dict]) -> Path:
    """Create a temporary vault with n8n workflow files."""
    import tempfile
    vault = Path(tempfile.mkdtemp())
    n8n_dir = vault / "raw" / "n8n-workflows" / "workflows" / "test"
    n8n_dir.mkdir(parents=True)
    for name, data in files.items():
        (n8n_dir / name).write_text(json.dumps(data))
    return vault


def summary_has_duplicates(summary) -> bool:
    return summary.total_duplicate_hashes > 0
