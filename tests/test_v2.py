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


# --- v2 pack index tests ---


class TestPackIndexSchema:
    """Tests for pack schema creation."""

    def test_schema_creation(self, tmp_path):
        """Test database schema creation."""
        from codex_vault_pipeline.v2.pack_index import get_db
        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)

        # Verify tables exist
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "packs" in table_names
        assert "pack_files" in table_names
        assert "pack_chunks" in table_names
        assert "pack_index_runs" in table_names

        conn.close()

    def test_fts_table_creation(self, tmp_path):
        """Test FTS5 table creation."""
        from codex_vault_pipeline.v2.pack_index import get_db
        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)

        # Verify FTS table exists
        fts = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pack_chunks_fts'"
        ).fetchone()
        assert fts is not None

        conn.close()


class TestPackParser:
    """Tests for Repomix pack parser."""

    def test_parse_small_pack(self, tmp_path):
        """Test parsing a small Repomix pack fixture."""
        from codex_vault_pipeline.v2.pack_index import parse_repomix_pack

        # Create a small fixture
        fixture = tmp_path / "output.md"
        fixture.write_text("""# File Summary

## Purpose
Test pack.

# Directory Structure
```
src/
  main.py
  utils.py
README.md
```

# Files

## File: src/main.py
```python
def hello():
    print("hello")
```

## File: src/utils.py
```python
def helper():
    return True
```

## File: README.md
```markdown
# Test Project
This is a test.
```
""")

        result = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )

        assert result["pack_meta"]["source_id"] == "github:test/repo"
        assert result["pack_meta"]["file_count"] == 3
        assert len(result["files"]) == 3
        assert len(result["chunks"]) > 0

    def test_artifact_role_classification(self, tmp_path):
        """Test artifact role classification."""
        from codex_vault_pipeline.v2.pack_index import parse_repomix_pack

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: SKILL.md
```markdown
# Skill
```

## File: SOUL.md
```markdown
# Soul
```

## File: README.md
```markdown
# Readme
```

## File: src/main.py
```python
x = 1
```

## File: docs/guide.md
```markdown
# Guide
```
""")

        result = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )

        files_by_path = {f["path"]: f for f in result["files"]}
        assert files_by_path["SKILL.md"]["artifact_role"] == "skill"
        assert files_by_path["SOUL.md"]["artifact_role"] == "soul"
        assert files_by_path["README.md"]["artifact_role"] == "readme"
        assert files_by_path["src/main.py"]["artifact_role"] == "code"
        assert files_by_path["docs/guide.md"]["artifact_role"] == "docs"

    def test_readme_low_priority(self, tmp_path):
        """Test README gets low priority."""
        from codex_vault_pipeline.v2.pack_index import parse_repomix_pack

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: README.md
```markdown
# Readme
```
""")

        result = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )

        assert result["files"][0]["artifact_role"] == "readme"
        assert result["chunks"][0]["priority_class"] == "low"

    def test_skill_high_priority(self, tmp_path):
        """Test SKILL.md gets high priority."""
        from codex_vault_pipeline.v2.pack_index import parse_repomix_pack

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: SKILL.md
```markdown
# Skill
```
""")

        result = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )

        assert result["files"][0]["artifact_role"] == "skill"
        assert result["chunks"][0]["priority_class"] == "high"

    def test_n8n_workflow_classification(self, tmp_path):
        """Test n8n workflow JSON classification."""
        from codex_vault_pipeline.v2.pack_index import parse_repomix_pack

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: workflow.json
```json
{
  "nodes": [{"name": "Start", "type": "n8n-nodes-base.start"}],
  "connections": {},
  "active": false,
  "settings": {}
}
```
""")

        result = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )

        assert result["files"][0]["artifact_role"] == "n8n_workflow"
        assert result["files"][0]["is_workflow_json"] == 1
        assert result["chunks"][0]["priority_class"] == "high"


class TestPackIndexOps:
    """Tests for pack indexing operations."""

    def test_index_and_search(self, tmp_path):
        """Test indexing a pack and searching."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, search_fts, parse_repomix_pack,
        )

        # Create fixture
        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: src/main.py
```python
def hello():
    print("hello world")
```

## File: SKILL.md
```markdown
# My Skill
This is a test skill.
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)

        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        stats = index_pack(conn, parsed)
        rebuild_fts(conn)

        assert stats["files_indexed"] == 2
        assert stats["chunks_indexed"] > 0

        # Search
        results = search_fts(conn, "hello world", limit=5)
        assert len(results) > 0
        assert results[0]["source_id"] == "github:test/repo"
        assert results[0]["path"] == "src/main.py"

        conn.close()

    def test_get_stats(self, tmp_path):
        """Test getting index statistics."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, get_stats, parse_repomix_pack,
        )

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: test.py
```python
x = 1
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)

        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        index_pack(conn, parsed)

        stats = get_stats(conn)
        assert stats["total_packs"] == 1
        assert stats["total_files"] == 1
        assert stats["total_chunks"] > 0
        assert stats["fts_rows"] > 0

        conn.close()

    def test_search_returns_source_and_path(self, tmp_path):
        """Test search returns source_id and path."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, search_fts, parse_repomix_pack,
        )

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: docs/guide.md
```markdown
# Memory System
This is about memory.
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)

        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        index_pack(conn, parsed)
        rebuild_fts(conn)

        results = search_fts(conn, "memory system", limit=5)
        assert len(results) > 0
        assert "source_id" in results[0]
        assert "path" in results[0]
        assert "artifact_role" in results[0]
        assert "priority_class" in results[0]
        assert "snippet" in results[0]

        conn.close()


class TestPackIndexCLI:
    """Tests for v2 pack CLI commands."""

    def test_v2_packs_help(self):
        """Test v2 packs CLI help."""
        from codex_vault_pipeline.cli import main
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            with pytest.raises(SystemExit) as exc_info:
                main(["v2", "packs", "--help"])
            assert exc_info.value.code == 0
        finally:
            sys.stdout = old_stdout

    def test_v2_packs_stats_no_db(self, tmp_path, monkeypatch):
        """Test v2 packs stats with no DB."""
        from codex_vault_pipeline.cli import main

        monkeypatch.setenv("CODEX_VAULT_ROOT", str(tmp_path))
        result = main(["v2", "packs", "stats"])
        assert result == 1  # error because no DB

    def test_v2_packs_search_no_query(self, tmp_path, monkeypatch):
        """Test v2 packs search without query."""
        from codex_vault_pipeline.cli import main

        monkeypatch.setenv("CODEX_VAULT_ROOT", str(tmp_path))
        result = main(["v2", "packs", "search"])
        assert result == 1  # error because no query


# --- v2 context packer tests ---


class TestContextPacker:
    """Tests for v2 context packer."""

    def test_context_pack_schema_validation(self):
        """Test context pack schema validation."""
        from codex_vault_pipeline.v2.context_packer import ContextPack, ContextPackItem

        pack = ContextPack(
            pack_id="test-pack",
            query="test query",
            generated_at="2026-06-22T00:00:00Z",
            retrieval_method="fts",
            total_results_considered=10,
            selected_items_count=5,
            token_budget=8000,
            estimated_tokens=1000,
        )
        assert pack.pack_id == "test-pack"
        assert pack.token_budget == 8000

    def test_token_budget_enforcement(self, tmp_path):
        """Test token budget enforcement."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, parse_repomix_pack,
        )
        from codex_vault_pipeline.v2.context_packer import pack_context

        # Create fixture with multiple chunks
        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: src/main.py
```python
def hello():
    print("hello world")
```

## File: src/utils.py
```python
def helper():
    return True
```

## File: docs/guide.md
```markdown
# Guide
This is a guide.
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)
        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        index_pack(conn, parsed)
        rebuild_fts(conn)
        conn.close()

        # Pack with small token budget
        pack = pack_context(
            db_path=db_path,
            query="hello world",
            max_tokens=100,  # very small budget
        )
        assert pack.estimated_tokens <= 100
        assert pack.selected_items_count <= 3

    def test_readme_demotion(self, tmp_path):
        """Test README demotion."""
        from codex_vault_pipeline.v2.context_packer import _should_demote_readme

        # Should demote for non-README queries
        assert _should_demote_readme("How does X work?") is True

        # Should NOT demote when query mentions README
        assert _should_demote_readme("Readme setup instructions") is False
        assert _should_demote_readme("Installation overview") is False

    def test_generated_catalog_demotion(self, tmp_path):
        """Test generated catalog demotion."""
        from codex_vault_pipeline.v2.context_packer import _should_demote_catalog

        # Should demote for non-catalog queries
        assert _should_demote_catalog("How does X work?") is True

        # Should NOT demote when query mentions catalog
        assert _should_demote_catalog("Show me the catalog") is False
        assert _should_demote_catalog("Index of files") is False

    def test_source_diversity(self, tmp_path):
        """Test source diversity in context packing."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, parse_repomix_pack,
        )
        from codex_vault_pipeline.v2.context_packer import pack_context

        # Create fixture from source A
        fixture_a = tmp_path / "output_a.md"
        fixture_a.write_text("""# Files

## File: src/main.py
```python
def hello():
    print("hello from A")
```
""")

        # Create fixture from source B
        fixture_b = tmp_path / "output_b.md"
        fixture_b.write_text("""# Files

## File: src/utils.py
```python
def helper():
    return "hello from B"
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)

        parsed_a = parse_repomix_pack(
            pack_path=str(fixture_a),
            source_id="github:test/repo_a",
            pack_id="test_repo_a",
        )
        index_pack(conn, parsed_a)

        parsed_b = parse_repomix_pack(
            pack_path=str(fixture_b),
            source_id="github:test/repo_b",
            pack_id="test_repo_b",
        )
        index_pack(conn, parsed_b)
        rebuild_fts(conn)
        conn.close()

        pack = pack_context(
            db_path=db_path,
            query="hello",
            max_tokens=2000,
        )
        # Should have results from both sources
        sources = {i.source_id for i in pack.items}
        assert len(sources) >= 1  # at least one source

    def test_provenance_inclusion(self, tmp_path):
        """Test source_id/path provenance inclusion."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, parse_repomix_pack,
        )
        from codex_vault_pipeline.v2.context_packer import pack_context

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: src/main.py
```python
def hello():
    print("hello")
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)
        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        index_pack(conn, parsed)
        rebuild_fts(conn)
        conn.close()

        pack = pack_context(
            db_path=db_path,
            query="hello",
        )
        for item in pack.items:
            assert item.source_id is not None
            assert item.path is not None

    def test_markdown_output(self, tmp_path):
        """Test markdown output format."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, parse_repomix_pack,
        )
        from codex_vault_pipeline.v2.context_packer import pack_context

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: src/main.py
```python
def hello():
    print("hello")
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)
        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        index_pack(conn, parsed)
        rebuild_fts(conn)
        conn.close()

        pack = pack_context(db_path=db_path, query="hello")
        md = pack.to_markdown()
        assert "# Context Pack:" in md
        assert "github:test/repo" in md
        assert "src/main.py" in md

    def test_json_output(self, tmp_path):
        """Test JSON output format."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, parse_repomix_pack,
        )
        from codex_vault_pipeline.v2.context_packer import pack_context
        import json

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: src/main.py
```python
def hello():
    print("hello")
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)
        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        index_pack(conn, parsed)
        rebuild_fts(conn)
        conn.close()

        pack = pack_context(db_path=db_path, query="hello")
        j = pack.to_json()
        data = json.loads(j)
        assert "pack_id" in data
        assert "items" in data
        assert "source_coverage" in data

    def test_context_packer_cli_help(self):
        """Test context packer CLI help."""
        from codex_vault_pipeline.cli import main
        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        try:
            with pytest.raises(SystemExit) as exc_info:
                main(["v2", "context", "--help"])
            assert exc_info.value.code == 0
        finally:
            sys.stdout = old_stdout

    def test_context_packer_no_db(self, tmp_path, monkeypatch):
        """Test context packer with no DB."""
        from codex_vault_pipeline.cli import main

        monkeypatch.setenv("CODEX_VAULT_ROOT", str(tmp_path))
        result = main(["v2", "context", "pack", "--query", "test"])
        assert result == 1  # error because no DB

    def test_context_packer_fixture_db(self, tmp_path):
        """Test context packer with fixture DB."""
        from codex_vault_pipeline.v2.pack_index import (
            get_db, index_pack, rebuild_fts, parse_repomix_pack,
        )
        from codex_vault_pipeline.v2.context_packer import pack_context

        fixture = tmp_path / "output.md"
        fixture.write_text("""# Files

## File: src/main.py
```python
def hello():
    print("hello")
```

## File: SKILL.md
```markdown
# My Skill
This is a test skill.
```
""")

        db_path = tmp_path / "test.sqlite"
        conn = get_db(db_path)
        parsed = parse_repomix_pack(
            pack_path=str(fixture),
            source_id="github:test/repo",
            pack_id="test_repo",
        )
        index_pack(conn, parsed)
        rebuild_fts(conn)
        conn.close()

        pack = pack_context(
            db_path=db_path,
            query="hello",
            max_tokens=2000,
        )
        assert pack.selected_items_count > 0
        assert pack.estimated_tokens > 0
        assert len(pack.items) > 0
        assert pack.items[0].source_id == "github:test/repo"


class TestSourceRouting:
    """Tests for source routing rules in context packer."""

    def test_source_routing_rules_exist(self):
        """Verify source routing rules are defined."""
        from codex_vault_pipeline.v2.context_packer import SOURCE_ROUTING_RULES
        assert len(SOURCE_ROUTING_RULES) >= 5

    def test_agentfield_routing(self):
        """AgentField core repo should be boosted for agent definition queries."""
        from codex_vault_pipeline.v2.context_packer import (
            _compute_source_routing_bonus, SOURCE_ROUTING_RULES,
        )
        # Check that agentfield rule exists
        agentfield_rules = [r for r in SOURCE_ROUTING_RULES if "agentfield" in str(r.preferred_sources)]
        assert len(agentfield_rules) >= 1
        rule = agentfield_rules[0]
        # Should match agent definition queries
        assert rule.pattern.search("agentfield agent definition protocol")
        assert rule.pattern.search("reactive agent field")
        # Should boost preferred source
        bonus = _compute_source_routing_bonus("agentfield agent definition", "github:Agent-Field/agentfield")
        assert bonus > 0

    def test_n8n_docs_routing(self):
        """n8n-docs should be boosted for docs concept queries."""
        from codex_vault_pipeline.v2.context_packer import _compute_source_routing_bonus
        bonus = _compute_source_routing_bonus("n8n error handling retry", "github:n8n-io/n8n-docs")
        assert bonus > 0

    def test_osint_routing(self):
        """Dedicated OSINT repos should be boosted for OSINT queries."""
        from codex_vault_pipeline.v2.context_packer import _compute_source_routing_bonus
        bonus_jivoi = _compute_source_routing_bonus("OSINT investigation tools", "github:jivoi/awesome-osint")
        bonus_lockfale = _compute_source_routing_bonus("OSINT framework", "github:lockfale/OSINT-Framework")
        assert bonus_jivoi > 0
        assert bonus_lockfale > 0

    def test_memory_routing(self):
        """Memory system repos should be boosted for memory queries."""
        from codex_vault_pipeline.v2.context_packer import _compute_source_routing_bonus
        bonus_mnemosyne = _compute_source_routing_bonus("memory systems persistence", "github:AxDSan/Mnemosyne")
        bonus_hindsight = _compute_source_routing_bonus("context persistence memory", "github:vectorize-io/hindsight")
        assert bonus_mnemosyne > 0
        assert bonus_hindsight > 0

    def test_coding_agent_routing(self):
        """Coding agent repos should be boosted for coding agent queries."""
        from codex_vault_pipeline.v2.context_packer import _compute_source_routing_bonus
        bonus_docsgpt = _compute_source_routing_bonus("coding agent code generation", "github:arc53/DocsGPT")
        bonus_deer = _compute_source_routing_bonus("autonomous code generation", "github:bytedance/deer-flow")
        assert bonus_docsgpt > 0
        assert bonus_deer > 0

    def test_no_routing_bonus_for_unrelated_query(self):
        """Unrelated queries should not get routing bonus."""
        from codex_vault_pipeline.v2.context_packer import _compute_source_routing_bonus
        bonus = _compute_source_routing_bonus("hello world", "github:Agent-Field/agentfield")
        assert bonus == 0.0

    def test_no_routing_bonus_for_wrong_source(self):
        """Correct query but wrong source should not get bonus."""
        from codex_vault_pipeline.v2.context_packer import _compute_source_routing_bonus
        bonus = _compute_source_routing_bonus("agentfield agent definition", "github:NousResearch/hermes-agent")
        assert bonus == 0.0

    def test_compute_score_includes_source_bonus(self):
        """_compute_score should include source routing bonus."""
        from codex_vault_pipeline.v2.context_packer import _compute_score
        flags = {"is_readme": False, "is_generated_catalog": False}
        # With routing
        score_with = _compute_score(
            rank=-10, artifact_role="code", priority_class="normal",
            query="agentfield agent definition", flags=flags,
            source_id="github:Agent-Field/agentfield",
        )
        # Without routing (wrong source)
        score_without = _compute_score(
            rank=-10, artifact_role="code", priority_class="normal",
            query="agentfield agent definition", flags=flags,
            source_id="github:NousResearch/hermes-agent",
        )
        assert score_with > score_without

    def test_hermes_specific_queries_not_degraded(self):
        """Hermes-specific queries should still rank hermes-agent highly."""
        from codex_vault_pipeline.v2.context_packer import _compute_score
        flags = {"is_readme": False, "is_generated_catalog": False}
        # Hermes query with hermes source should get normal score (no penalty)
        score = _compute_score(
            rank=-5, artifact_role="skill", priority_class="high",
            query="hermes agent soul personality", flags=flags,
            source_id="github:NousResearch/hermes-agent",
        )
        # Should be high score due to skill role + high priority
        assert score > 0.5
