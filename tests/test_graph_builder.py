# Tests for graph builder
"""Tests for v2 graph builder module."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from codex_vault_pipeline.v2.graph_builder import (
    build_graph, write_graph_outputs, extract_wikilinks, read_frontmatter,
    make_node_id_from_path, make_node_id_from_source,
)
from codex_vault_pipeline.v2.graph_schema import (
    GraphNode, GraphEdge, NodeType, EdgeType, ConfidenceLevel, SecurityState,
)


class TestWikilinkExtraction:
    """Tests for wikilink extraction."""
    
    def test_extract_wikilinks_basic(self):
        """Test basic wikilink extraction."""
        content = "See [[40-use-cases/40.50-n8n-workflow-automation-substrate]] for details."
        links = extract_wikilinks(content)
        assert "40-use-cases/40.50-n8n-workflow-automation-substrate" in links
    
    def test_extract_wikilinks_multiple(self):
        """Test multiple wikilink extraction."""
        content = "See [[20-domains/n8n/n8n]] and [[50-guides/50.40-reusing-n8n-core-with-agents]]."
        links = extract_wikilinks(content)
        assert len(links) == 2
    
    def test_extract_wikilinks_none(self):
        """Test no wikilinks."""
        content = "No links here."
        links = extract_wikilinks(content)
        assert len(links) == 0


class TestFrontmatterParsing:
    """Tests for frontmatter parsing."""
    
    def test_read_frontmatter_with_frontmatter(self):
        """Test reading frontmatter."""
        content = """---
title: Test Note
slug: test-note
---
# Test Note
Content here."""
        fm, body = read_frontmatter(content)
        assert fm['title'] == 'Test Note'
        assert fm['slug'] == 'test-note'
        assert '# Test Note' in body
    
    def test_read_frontmatter_without_frontmatter(self):
        """Test reading content without frontmatter."""
        content = "# Test Note\nContent here."
        fm, body = read_frontmatter(content)
        assert fm == {}
        assert body == content


class TestNodeIdGeneration:
    """Tests for node ID generation."""
    
    def test_make_node_id_from_path(self):
        """Test node ID from path."""
        node_id = make_node_id_from_path("20-domains/n8n/n8n.md", NodeType.ECOSYSTEM)
        assert node_id == "vault:20-domains/n8n/n8n#ecosystem"
    
    def test_make_node_id_from_source(self):
        """Test node ID from source."""
        node_id = make_node_id_from_source("github:n8n-io/n8n-docs", NodeType.SOURCE)
        assert node_id == "github:n8n-io/n8n-docs#source"


class TestGraphBuilder:
    """Tests for graph builder."""
    
    def test_build_graph_creates_nodes(self, tmp_path):
        """Test graph builder creates nodes from fixture."""
        # Create fixture structure
        domains_dir = tmp_path / "20-domains" / "test-domain"
        domains_dir.mkdir(parents=True)
        (domains_dir / "test-domain.md").write_text("""---
title: Test Domain
slug: test-domain
---
# Test Domain
""")
        
        nodes, edges = build_graph(tmp_path, include_runtime_summaries=False)
        
        assert len(nodes) > 0
        domain_nodes = [n for n in nodes if n.node_type == NodeType.ECOSYSTEM]
        assert len(domain_nodes) == 1
        assert domain_nodes[0].label == "Test Domain"
    
    def test_build_graph_creates_edges_from_wikilinks(self, tmp_path):
        """Test graph builder creates edges from wikilinks."""
        # Create fixture with wikilinks
        domains_dir = tmp_path / "20-domains" / "domain-a"
        domains_dir.mkdir(parents=True)
        (domains_dir / "domain-a.md").write_text("""---
title: Domain A
---
# Domain A
See [[40-use-cases/use-case-b]] for details.
""")
        
        use_cases_dir = tmp_path / "40-use-cases"
        use_cases_dir.mkdir(parents=True)
        (use_cases_dir / "use-case-b.md").write_text("""---
title: Use Case B
---
# Use Case B
""")
        
        nodes, edges = build_graph(tmp_path, include_runtime_summaries=False)
        
        # Should have nodes for both domain and use case
        assert len(nodes) >= 2
        
        # Should have edge from domain to use case
        wikilink_edges = [e for e in edges if e.edge_type == EdgeType.REFERENCED_BY_GUIDE]
        assert len(wikilink_edges) >= 1
    
    def test_build_graph_no_raw_scanning(self, tmp_path):
        """Test graph builder does not scan raw/."""
        # Create raw directory with files
        raw_dir = tmp_path / "raw" / "test-source"
        raw_dir.mkdir(parents=True)
        (raw_dir / "test.md").write_text("# Test")
        
        nodes, edges = build_graph(tmp_path, include_runtime_summaries=False)
        
        # Should not have any nodes from raw/
        raw_nodes = [n for n in nodes if n.path and n.path.startswith('raw/')]
        assert len(raw_nodes) == 0
    
    def test_build_graph_runtime_summary_included(self, tmp_path):
        """Test graph builder includes runtime summary when present."""
        # Create runtime summary
        runtime_dir = tmp_path / ".runtime" / "domain" / "n8n-workflows"
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "summary.json").write_text(json.dumps({
            "total_workflows": 100,
            "total_files_scanned": 50,
        }))
        
        nodes, edges = build_graph(tmp_path, include_runtime_summaries=True)
        
        # Should have workflow aggregate node
        workflow_nodes = [n for n in nodes if n.node_type == NodeType.WORKFLOW]
        assert len(workflow_nodes) >= 1
        assert workflow_nodes[0].label == "n8n Workflows Aggregate"


class TestGraphOutputs:
    """Tests for graph output writing."""
    
    def test_write_graph_outputs_creates_files(self, tmp_path):
        """Test graph outputs are written correctly."""
        nodes = [
            GraphNode(
                node_id="test-node",
                node_type=NodeType.SOURCE,
                label="Test Source",
            )
        ]
        edges = [
            GraphEdge(
                edge_id="test-edge",
                edge_type=EdgeType.BELONGS_TO_ECOSYSTEM,
                from_node_id="test-node",
                to_node_id="ecosystem-node",
            )
        ]
        
        summary = write_graph_outputs(nodes, edges, tmp_path)
        
        assert (tmp_path / "nodes.jsonl").exists()
        assert (tmp_path / "edges.jsonl").exists()
        assert (tmp_path / "graph-summary.json").exists()
        assert (tmp_path / "validation-report.json").exists()
        
        assert summary["total_nodes"] == 1
        assert summary["total_edges"] == 1
    
    def test_write_graph_outputs_jsonl_format(self, tmp_path):
        """Test JSONL format is valid."""
        nodes = [
            GraphNode(
                node_id="test-node",
                node_type=NodeType.CONCEPT,
                label="Test Concept",
            )
        ]
        edges = []
        
        write_graph_outputs(nodes, edges, tmp_path)
        
        # Verify JSONL is valid
        with (tmp_path / "nodes.jsonl").open() as f:
            line = f.readline()
            data = json.loads(line)
            assert data["node_id"] == "test-node"
            assert data["node_type"] == "concept"
    
    def test_write_graph_outputs_validation(self, tmp_path):
        """Test validation report is generated."""
        nodes = [
            GraphNode(
                node_id="test-node",
                node_type=NodeType.SKILL,
                label="Test Skill",
                # Missing source_id - should fail validation for evidence-bearing node
            )
        ]
        edges = []
        
        summary = write_graph_outputs(nodes, edges, tmp_path)
        
        validation_path = tmp_path / "validation-report.json"
        validation = json.loads(validation_path.read_text())
        
        assert validation["is_valid"] is False
        assert len(validation["validation_errors"]) > 0


class TestDeterministicEdges:
    """Tests for deterministic edge generation."""
    
    def test_wikilink_edges_are_deterministic(self, tmp_path):
        """Test wikilink edges have stable IDs."""
        domains_dir = tmp_path / "20-domains" / "domain-a"
        domains_dir.mkdir(parents=True)
        (domains_dir / "domain-a.md").write_text("""---
title: Domain A
---
# Domain A
See [[40-use-cases/use-case-b]].
""")
        
        use_cases_dir = tmp_path / "40-use-cases"
        use_cases_dir.mkdir(parents=True)
        (use_cases_dir / "use-case-b.md").write_text("""---
title: Use Case B
---
# Use Case B
""")
        
        nodes, edges = build_graph(tmp_path, include_runtime_summaries=False)
        
        # Edge IDs should be deterministic
        for edge in edges:
            assert "#" in edge.edge_id
            assert edge.edge_type.value in edge.edge_id