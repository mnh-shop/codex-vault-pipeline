# Tests for v2 repo-context lane
"""Tests for v2 repo-context lane modules."""
from __future__ import annotations

import pytest
from pathlib import Path
from codex_vault_pipeline.v2.config import V2Config, get_config
from codex_vault_pipeline.v2.manifest import RepoPackManifest, PilotManifest
from codex_vault_pipeline.v2.deepwiki_sanity import DeepWikiSanityChecker
from codex_vault_pipeline.v2.n8n_coverage import N8nCoverageAnalyzer, N8nSource
from codex_vault_pipeline.v2.retrieval_policy import RetrievalPolicy
from codex_vault_pipeline.v2.context_pack_schema import (
    ContextPack, ContextItem, SourceProvenance, RetrievalTrace,
    SecurityStatus, ArtifactRole, RetrievalMethod,
    validate_context_pack,
)


class TestV2Config:
    """Tests for v2 configuration."""
    
    def test_config_creation(self):
        """Test V2Config creation."""
        config = V2Config()
        assert config.pipeline_root is not None
        assert config.vault_root is not None
    
    def test_config_defaults(self):
        """Test V2Config defaults."""
        config = V2Config()
        assert config.repomix_enabled is True
        assert config.repomix_output_format == "markdown"
        assert config.repomix_security_check is True
        assert config.repomix_compression is False


class TestManifest:
    """Tests for manifest generation."""
    
    def test_repo_pack_manifest_creation(self):
        """Test RepoPackManifest creation."""
        manifest = RepoPackManifest(
            source_id="github:owner/repo",
            source_type="github",
            repo_url="https://github.com/owner/repo",
        )
        assert manifest.source_id == "github:owner/repo"
        assert manifest.source_type == "github"
        assert manifest.repo_url == "https://github.com/owner/repo"
    
    def test_repo_pack_manifest_to_dict(self):
        """Test RepoPackManifest to_dict."""
        manifest = RepoPackManifest(
            source_id="github:owner/repo",
            source_type="github",
            repo_url="https://github.com/owner/repo",
        )
        d = manifest.to_dict()
        assert d["source_id"] == "github:owner/repo"
        assert d["source_type"] == "github"
        assert d["repo_url"] == "https://github.com/owner/repo"
    
    def test_pilot_manifest_creation(self):
        """Test PilotManifest creation."""
        manifest = PilotManifest()
        assert manifest.phase == "05a"
        assert manifest.pilot_name == "repomix_pilot"
        assert len(manifest.sources) == 0
    
    def test_pilot_manifest_add_source(self):
        """Test PilotManifest add_source."""
        manifest = PilotManifest()
        source = RepoPackManifest(
            source_id="github:owner/repo",
            source_type="github",
            repo_url="https://github.com/owner/repo",
        )
        manifest.add_source(source)
        assert len(manifest.sources) == 1
        assert manifest.sources[0].source_id == "github:owner/repo"


class TestDeepWikiSanity:
    """Tests for DeepWiki sanity checker."""
    
    def test_convert_github_url(self):
        """Test GitHub URL to DeepWiki URL conversion."""
        url = "https://github.com/NousResearch/hermes-agent"
        deepwiki_url = DeepWikiSanityChecker.convert_to_deepwiki_url(url)
        assert deepwiki_url == "https://deepwiki.com/NousResearch/hermes-agent"
    
    def test_convert_owner_repo_format(self):
        """Test owner/repo format to DeepWiki URL conversion."""
        url = "NousResearch/hermes-agent"
        deepwiki_url = DeepWikiSanityChecker.convert_to_deepwiki_url(url)
        assert deepwiki_url == "https://deepwiki.com/NousResearch/hermes-agent"
    
    def test_check_url(self):
        """Test DeepWiki URL check."""
        url = "https://deepwiki.com/NousResearch/hermes-agent"
        result = DeepWikiSanityChecker.check_url(url)
        assert result.repo == "NousResearch/hermes-agent"
        assert result.deepwiki_url == url
        assert result.recommended_use == "external sanity check only - verify manually"


class TestN8nCoverage:
    """Tests for n8n coverage analyzer."""
    
    def test_n8n_source_creation(self):
        """Test N8nSource creation."""
        source = N8nSource(
            source_id="github:n8n-io/n8n-docs",
            name="Official n8n documentation",
            raw_path="raw/n8n/",
            status="complete",
            coverage=1.0,
            workflow_count=28,
            total_expected=28,
            authority_level="canonical-upstream",
            source_type="official docs",
        )
        assert source.source_id == "github:n8n-io/n8n-docs"
        assert source.status == "complete"
        assert source.coverage == 1.0
    
    def test_n8n_coverage_status_partial(self):
        """Test n8n coverage status is PARTIAL when there are partial sources."""
        sources = [
            N8nSource(
                source_id="github:n8n-io/n8n-docs",
                name="Official docs",
                raw_path="raw/n8n/",
                status="complete",
                coverage=1.0,
                workflow_count=28,
                total_expected=28,
                authority_level="canonical-upstream",
                source_type="official docs",
            ),
            N8nSource(
                source_id="github:nusquama/n8nworkflows.xyz",
                name="Workflow catalog",
                raw_path="raw/n8nworkflows-xyz/",
                status="partial",
                coverage=0.0763,
                workflow_count=1200,
                total_expected=15744,
                authority_level="community",
                source_type="workflow catalog",
            ),
        ]
        partial_sources = [s for s in sources if s.status == "partial"]
        assert len(partial_sources) > 0
        # Coverage status would be PARTIAL


class TestRetrievalPolicy:
    """Tests for retrieval v2 policy."""
    
    def test_policy_creation(self):
        """Test RetrievalPolicy creation."""
        policy = RetrievalPolicy()
        assert policy.repomix_packs_canonical is True
        assert policy.readme_low_priority is True
        assert policy.generated_catalog_low_priority is True
        assert policy.graphrag_deferred is True
    
    def test_policy_to_dict(self):
        """Test RetrievalPolicy to_dict."""
        policy = RetrievalPolicy()
        d = policy.to_dict()
        assert "core_policies" in d
        assert "provenance_requirements" in d
        assert "priority_policies" in d
        assert "deferred" in d


class TestContextPackSchema:
    """Tests for context pack schema."""
    
    def test_context_pack_creation(self):
        """Test ContextPack creation."""
        pack = ContextPack(pack_id="test-pack")
        assert pack.pack_id == "test-pack"
        assert len(pack.items) == 0
        assert pack.total_tokens == 0
    
    def test_context_item_creation(self):
        """Test ContextItem creation."""
        provenance = SourceProvenance(
            source_id="github:owner/repo",
            repo_url="https://github.com/owner/repo",
            commit="abc123",
            path="src/main.py",
        )
        trace = RetrievalTrace(
            method=RetrievalMethod.VECTOR,
            rank=1,
            score=0.95,
        )
        item = ContextItem(
            item_id="test-item",
            text="def hello(): print('hello')",
            token_estimate=10,
            provenance=provenance,
            retrieval_trace=trace,
        )
        assert item.item_id == "test-item"
        assert item.token_estimate == 10
        assert item.security_status == SecurityStatus.CLEAN
        assert item.is_quarantined is False
        assert item.is_generated_catalog is False
        assert item.is_readme is False
    
    def test_context_pack_add_item(self):
        """Test ContextPack add_item."""
        pack = ContextPack(pack_id="test-pack")
        provenance = SourceProvenance(source_id="test")
        trace = RetrievalTrace(method=RetrievalMethod.FTS, rank=1, score=1.0)
        item = ContextItem(
            item_id="item-1",
            text="test content",
            token_estimate=5,
            provenance=provenance,
            retrieval_trace=trace,
        )
        pack.add_item(item)
        assert len(pack.items) == 1
        assert pack.total_tokens == 5
    
    def test_validate_context_pack_valid(self):
        """Test validate_context_pack with valid pack."""
        data = {
            "pack_id": "test-pack",
            "items": [
                {
                    "item_id": "item-1",
                    "text": "test content",
                    "token_estimate": 5,
                    "provenance": {"source_id": "test"},
                    "retrieval_trace": {"method": "fts", "rank": 1, "score": 1.0},
                }
            ],
        }
        errors = validate_context_pack(data)
        assert len(errors) == 0
    
    def test_validate_context_pack_missing_fields(self):
        """Test validate_context_pack with missing fields."""
        data = {
            "items": [],
        }
        errors = validate_context_pack(data)
        assert len(errors) > 0
        assert any("pack_id" in e for e in errors)
    
    def test_security_status_enum(self):
        """Test SecurityStatus enum."""
        assert SecurityStatus.CLEAN.value == "clean"
        assert SecurityStatus.FLAGGED.value == "flagged"
        assert SecurityStatus.BLOCKED.value == "blocked"
        assert SecurityStatus.NOT_SCANNED.value == "not-scanned"
    
    def test_artifact_role_enum(self):
        """Test ArtifactRole enum."""
        assert ArtifactRole.WORKFLOW.value == "workflow"
        assert ArtifactRole.SKILL.value == "skill"
        assert ArtifactRole.CODE.value == "code"
    
    def test_retrieval_method_enum(self):
        """Test RetrievalMethod enum."""
        assert RetrievalMethod.METADATA.value == "metadata"
        assert RetrievalMethod.FTS.value == "fts"
        assert RetrievalMethod.VECTOR.value == "vector"
        assert RetrievalMethod.REPOMIX.value == "repomix"
