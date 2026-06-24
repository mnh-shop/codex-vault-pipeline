# Tests for graph schema
"""Tests for v2 graph schema module."""
from __future__ import annotations

import pytest
from codex_vault_pipeline.v2.graph_schema import (
    GraphNode, GraphEdge, NodeType, EdgeType, ConfidenceLevel, SecurityState,
    EvidenceRef, make_deterministic_node_id, make_deterministic_edge_id,
    validate_graph_node, validate_graph_edge,
)


class TestNodeType:
    """Tests for NodeType enum."""
    
    def test_node_type_values(self):
        """Test NodeType enum values."""
        assert NodeType.ECOSYSTEM.value == "ecosystem"
        assert NodeType.SOURCE.value == "source"
        assert NodeType.ARTIFACT.value == "artifact"
        assert NodeType.UNIT.value == "unit"
        assert NodeType.SKILL.value == "skill"
        assert NodeType.WORKFLOW.value == "workflow"
    
    def test_node_type_count(self):
        """Test expected number of node types."""
        assert len(NodeType) >= 15


class TestEdgeType:
    """Tests for EdgeType enum."""
    
    def test_edge_type_values(self):
        """Test EdgeType enum values."""
        assert EdgeType.BELONGS_TO_ECOSYSTEM.value == "belongs_to_ecosystem"
        assert EdgeType.DEPENDS_ON.value == "depends_on"
        assert EdgeType.HAS_SKILL.value == "has_skill"
        assert EdgeType.HAS_WORKFLOW.value == "has_workflow"
    
    def test_edge_type_count(self):
        """Test expected number of edge types."""
        assert len(EdgeType) >= 15


class TestGraphNode:
    """Tests for GraphNode dataclass."""
    
    def test_graph_node_creation(self):
        """Test GraphNode creation with required fields."""
        node = GraphNode(
            node_id="github:NousResearch/hermes-agent",
            node_type=NodeType.SOURCE,
            label="Hermes Agent",
        )
        assert node.node_id == "github:NousResearch/hermes-agent"
        assert node.node_type == NodeType.SOURCE
        assert node.label == "Hermes Agent"
        assert node.status == "active"
        assert node.security_state == SecurityState.CLEAN
    
    def test_graph_node_with_provenance(self):
        """Test GraphNode with full provenance."""
        node = GraphNode(
            node_id="sha256:abc123#heading:configuration",
            node_type=NodeType.UNIT,
            label="Configuration",
            source_id="github:NousResearch/hermes-agent",
            artifact_id="sha256:def456",
            path="README.md",
            resolved_commit="abc123def456",
        )
        assert node.source_id == "github:NousResearch/hermes-agent"
        assert node.artifact_id == "sha256:def456"
    
    def test_graph_node_to_dict(self):
        """Test GraphNode serialization."""
        node = GraphNode(
            node_id="test-node",
            node_type=NodeType.SKILL,
            label="Test Skill",
            source_id="github:owner/repo",
        )
        d = node.to_dict()
        assert d["node_id"] == "test-node"
        assert d["node_type"] == "skill"
        assert d["label"] == "Test Skill"
        assert d["source_id"] == "github:owner/repo"
    
    def test_graph_node_to_jsonl_line(self):
        """Test GraphNode JSONL serialization."""
        node = GraphNode(
            node_id="test-node",
            node_type=NodeType.SKILL,
            label="Test Skill",
        )
        line = node.to_jsonl_line()
        assert '"node_id": "test-node"' in line
        assert '"node_type": "skill"' in line


class TestGraphEdge:
    """Tests for GraphEdge dataclass."""
    
    def test_graph_edge_creation(self):
        """Test GraphEdge creation with required fields."""
        edge = GraphEdge(
            edge_id="github:NousResearch/hermes-agent#has_skill#skill-id",
            edge_type=EdgeType.HAS_SKILL,
            from_node_id="github:NousResearch/hermes-agent",
            to_node_id="skill-id",
        )
        assert edge.edge_type == EdgeType.HAS_SKILL
        assert edge.from_node_id == "github:NousResearch/hermes-agent"
        assert edge.to_node_id == "skill-id"
        assert edge.confidence == ConfidenceLevel.EXTRACTED
    
    def test_graph_edge_with_evidence(self):
        """Test GraphEdge with evidence references."""
        edge = GraphEdge(
            edge_id="test-edge",
            edge_type=EdgeType.DEPENDS_ON,
            from_node_id="node-a",
            to_node_id="node-b",
            confidence=ConfidenceLevel.EXTRACTED,
            evidence=[
                EvidenceRef(source_id="github:owner/repo", path="package.json", line=10),
            ],
        )
        assert len(edge.evidence) == 1
        assert edge.evidence[0].source_id == "github:owner/repo"
    
    def test_graph_edge_to_dict(self):
        """Test GraphEdge serialization."""
        edge = GraphEdge(
            edge_id="test-edge",
            edge_type=EdgeType.HAS_WORKFLOW,
            from_node_id="source-id",
            to_node_id="workflow-id",
        )
        d = edge.to_dict()
        assert d["edge_id"] == "test-edge"
        assert d["edge_type"] == "has_workflow"
        assert d["from_node_id"] == "source-id"
        assert d["to_node_id"] == "workflow-id"
    
    def test_graph_edge_to_jsonl_line(self):
        """Test GraphEdge JSONL serialization."""
        edge = GraphEdge(
            edge_id="test-edge",
            edge_type=EdgeType.DEPENDS_ON,
            from_node_id="a",
            to_node_id="b",
        )
        line = edge.to_jsonl_line()
        assert '"edge_type": "depends_on"' in line


class TestDeterministicIds:
    """Tests for deterministic ID generation."""
    
    def test_make_deterministic_node_id_basic(self):
        """Test basic node ID generation."""
        node_id = make_deterministic_node_id(
            source_id="github:owner/repo",
            node_type=NodeType.SOURCE,
        )
        assert node_id == "github:owner/repo#source"
    
    def test_make_deterministic_node_id_with_artifact(self):
        """Test node ID with artifact."""
        node_id = make_deterministic_node_id(
            source_id="github:owner/repo",
            artifact_id="sha256:abc123",
            node_type=NodeType.ARTIFACT,
        )
        assert node_id == "github:owner/repo#sha256:abc123#artifact"
    
    def test_make_deterministic_node_id_with_unit(self):
        """Test node ID with unit."""
        node_id = make_deterministic_node_id(
            source_id="github:owner/repo",
            artifact_id="sha256:abc123",
            unit_id="sha256:abc123#heading:configuration",
            node_type=NodeType.UNIT,
        )
        assert node_id == "github:owner/repo#sha256:abc123#sha256:abc123#heading:configuration#unit"
    
    def test_make_deterministic_edge_id(self):
        """Test edge ID generation."""
        edge_id = make_deterministic_edge_id(
            from_node_id="node-a",
            edge_type=EdgeType.HAS_SKILL,
            to_node_id="node-b",
        )
        assert edge_id == "node-a#has_skill#node-b"


class TestValidation:
    """Tests for graph validation."""
    
    def test_validate_graph_node_valid(self):
        """Test valid node passes validation."""
        data = {
            "node_id": "test-node",
            "node_type": "source",
            "label": "Test Source",
            "source_id": "github:owner/repo",
        }
        errors = validate_graph_node(data)
        assert len(errors) == 0
    
    def test_validate_graph_node_missing_node_id(self):
        """Test node validation catches missing node_id."""
        data = {
            "node_type": "source",
            "label": "Test Source",
        }
        errors = validate_graph_node(data)
        assert len(errors) > 0
        assert any("node_id" in e for e in errors)
    
    def test_validate_graph_node_invalid_type(self):
        """Test node validation catches invalid node_type."""
        data = {
            "node_id": "test-node",
            "node_type": "invalid_type",
            "label": "Test",
        }
        errors = validate_graph_node(data)
        assert len(errors) > 0
        assert any("Invalid node_type" in e for e in errors)
    
    def test_validate_graph_node_evidence_bearing_requires_provenance(self):
        """Test evidence-bearing node requires provenance."""
        data = {
            "node_id": "test-node",
            "node_type": "skill",
            "label": "Test Skill",
        }
        errors = validate_graph_node(data)
        assert len(errors) > 0
        assert any("requires source_id or artifact_id" in e for e in errors)
    
    def test_validate_graph_edge_valid(self):
        """Test valid edge passes validation."""
        data = {
            "edge_id": "test-edge",
            "edge_type": "has_skill",
            "from_node_id": "node-a",
            "to_node_id": "node-b",
            "source_id": "github:owner/repo",
        }
        errors = validate_graph_edge(data)
        assert len(errors) == 0
    
    def test_validate_graph_edge_missing_edge_id(self):
        """Test edge validation catches missing edge_id."""
        data = {
            "edge_type": "has_skill",
            "from_node_id": "node-a",
            "to_node_id": "node-b",
        }
        errors = validate_graph_edge(data)
        assert len(errors) > 0
        assert any("edge_id" in e for e in errors)
    
    def test_validate_graph_edge_invalid_type(self):
        """Test edge validation catches invalid edge_type."""
        data = {
            "edge_id": "test-edge",
            "edge_type": "invalid_type",
            "from_node_id": "node-a",
            "to_node_id": "node-b",
        }
        errors = validate_graph_edge(data)
        assert len(errors) > 0
        assert any("Invalid edge_type" in e for e in errors)
    
    def test_validate_graph_edge_evidence_bearing_requires_provenance(self):
        """Test evidence-bearing edge requires evidence or source_id."""
        data = {
            "edge_id": "test-edge",
            "edge_type": "depends_on",
            "from_node_id": "node-a",
            "to_node_id": "node-b",
            "confidence": "extracted",
        }
        errors = validate_graph_edge(data)
        assert len(errors) > 0
        assert any("requires evidence or source_id" in e for e in errors)


class TestEvidenceRef:
    """Tests for EvidenceRef dataclass."""
    
    def test_evidence_ref_creation(self):
        """Test EvidenceRef creation."""
        ref = EvidenceRef(
            source_id="github:owner/repo",
            artifact_id="sha256:abc123",
            path="src/main.py",
            line=42,
        )
        assert ref.source_id == "github:owner/repo"
        assert ref.line == 42
    
    def test_evidence_ref_minimal(self):
        """Test EvidenceRef with minimal fields."""
        ref = EvidenceRef(source_id="github:owner/repo")
        assert ref.artifact_id is None
        assert ref.unit_id is None
        assert ref.path is None
        assert ref.line is None