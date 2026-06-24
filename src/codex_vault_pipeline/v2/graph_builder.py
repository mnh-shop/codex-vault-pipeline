# Codex Vault Pipeline — graph builder
"""Bounded graph builder from existing Codex Vault records.

Reads curated/structured layers and writes canonical graph runtime outputs.
Does NOT scan full raw/ or generate semantic edges.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .graph_schema import (
    GraphNode, GraphEdge, NodeType, EdgeType, ConfidenceLevel, SecurityState,
    EvidenceRef, make_deterministic_node_id, make_deterministic_edge_id,
    validate_graph_node, validate_graph_edge,
)


# Wikilink pattern: [[path/to/note]]
WIKILINK_PATTERN = re.compile(r'\[\[([^\]]+)\]\]')


def extract_wikilinks(content: str) -> List[str]:
    """Extract wikilink targets from markdown content."""
    return WIKILINK_PATTERN.findall(content)


def make_node_id_from_path(path: str, node_type: NodeType) -> str:
    """Generate deterministic node ID from vault path."""
    # Remove .md extension and normalize
    clean_path = path.replace('.md', '').strip('/')
    return f"vault:{clean_path}#{node_type.value}"


def make_node_id_from_source(source_id: str, node_type: NodeType) -> str:
    """Generate deterministic node ID from source ID."""
    return f"{source_id}#{node_type.value}"


def read_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Extract frontmatter and body from markdown content."""
    if not content.startswith('---\n'):
        return {}, content
    
    parts = content.split('---\n', 2)
    if len(parts) < 3:
        return {}, content
    
    frontmatter_text = parts[1]
    body = parts[2]
    
    # Simple YAML parsing for frontmatter
    frontmatter = {}
    for line in frontmatter_text.split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.strip()
            # Handle lists
            if value.startswith('[') and value.endswith(']'):
                value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(',')]
            elif value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            frontmatter[key] = value
    
    return frontmatter, body


def build_nodes_from_domains(vault_root: Path) -> List[GraphNode]:
    """Build ecosystem nodes from 20-domains/."""
    nodes = []
    domains_dir = vault_root / "20-domains"
    
    if not domains_dir.exists():
        return nodes
    
    for domain_dir in domains_dir.iterdir():
        if not domain_dir.is_dir():
            continue
        
        for md_file in domain_dir.glob("*.md"):
            content = md_file.read_text()
            frontmatter, body = read_frontmatter(content)
            
            # Create ecosystem node
            node_id = make_node_id_from_path(str(md_file.relative_to(vault_root)), NodeType.ECOSYSTEM)
            label = frontmatter.get('title', md_file.stem)
            
            node = GraphNode(
                node_id=node_id,
                node_type=NodeType.ECOSYSTEM,
                label=label,
                path=str(md_file.relative_to(vault_root)),
                metadata={
                    "source_paths": frontmatter.get('source_paths', []),
                    "source_count": frontmatter.get('source_count', 0),
                },
            )
            nodes.append(node)
    
    return nodes


def build_nodes_from_concepts(vault_root: Path) -> List[GraphNode]:
    """Build concept nodes from 30-concepts/."""
    nodes = []
    concepts_dir = vault_root / "30-concepts"
    
    if not concepts_dir.exists():
        return nodes
    
    for md_file in concepts_dir.rglob("*.md"):
        content = md_file.read_text()
        frontmatter, body = read_frontmatter(content)
        
        node_id = make_node_id_from_path(str(md_file.relative_to(vault_root)), NodeType.CONCEPT)
        label = frontmatter.get('title', md_file.stem)
        
        node = GraphNode(
            node_id=node_id,
            node_type=NodeType.CONCEPT,
            label=label,
            path=str(md_file.relative_to(vault_root)),
            ecosystem=frontmatter.get('ecosystem'),
            metadata={
                "topic": frontmatter.get('topic'),
                "tags": frontmatter.get('tags', []),
            },
        )
        nodes.append(node)
    
    return nodes


def build_nodes_from_use_cases(vault_root: Path) -> List[GraphNode]:
    """Build use case nodes from 40-use-cases/."""
    nodes = []
    use_cases_dir = vault_root / "40-use-cases"
    
    if not use_cases_dir.exists():
        return nodes
    
    for md_file in use_cases_dir.rglob("*.md"):
        content = md_file.read_text()
        frontmatter, body = read_frontmatter(content)
        
        node_id = make_node_id_from_path(str(md_file.relative_to(vault_root)), NodeType.USE_CASE)
        label = frontmatter.get('title', md_file.stem)
        
        node = GraphNode(
            node_id=node_id,
            node_type=NodeType.USE_CASE,
            label=label,
            path=str(md_file.relative_to(vault_root)),
            ecosystem=frontmatter.get('ecosystem'),
            metadata={
                "tags": frontmatter.get('tags', []),
            },
        )
        nodes.append(node)
    
    return nodes


def build_nodes_from_guides(vault_root: Path) -> List[GraphNode]:
    """Build guide nodes from 50-guides/."""
    nodes = []
    guides_dir = vault_root / "50-guides"
    
    if not guides_dir.exists():
        return nodes
    
    for md_file in guides_dir.rglob("*.md"):
        content = md_file.read_text()
        frontmatter, body = read_frontmatter(content)
        
        node_id = make_node_id_from_path(str(md_file.relative_to(vault_root)), NodeType.GUIDE)
        label = frontmatter.get('title', md_file.stem)
        
        node = GraphNode(
            node_id=node_id,
            node_type=NodeType.GUIDE,
            label=label,
            path=str(md_file.relative_to(vault_root)),
            ecosystem=frontmatter.get('ecosystem'),
            metadata={
                "tags": frontmatter.get('tags', []),
            },
        )
        nodes.append(node)
    
    return nodes


def build_nodes_from_reports(vault_root: Path) -> List[GraphNode]:
    """Build report nodes from 70-reports/."""
    nodes = []
    reports_dir = vault_root / "70-reports"
    
    if not reports_dir.exists():
        return nodes
    
    for md_file in reports_dir.glob("*.md"):
        content = md_file.read_text()
        frontmatter, body = read_frontmatter(content)
        
        node_id = make_node_id_from_path(str(md_file.relative_to(vault_root)), NodeType.REPORT)
        label = frontmatter.get('title', md_file.stem)
        
        node = GraphNode(
            node_id=node_id,
            node_type=NodeType.REPORT,
            label=label,
            path=str(md_file.relative_to(vault_root)),
            metadata={
                "phase": frontmatter.get('phase'),
                "status": frontmatter.get('status'),
            },
        )
        nodes.append(node)
    
    return nodes


def build_nodes_from_sources(vault_root: Path) -> List[GraphNode]:
    """Build source-layer nodes from 60-sources/."""
    nodes = []
    sources_dir = vault_root / "60-sources"
    
    if not sources_dir.exists():
        return nodes
    
    for md_file in sources_dir.rglob("*.md"):
        content = md_file.read_text()
        frontmatter, body = read_frontmatter(content)
        
        # Determine node type from path
        rel_path = str(md_file.relative_to(vault_root))
        if "agent-skills" in rel_path:
            node_type = NodeType.SKILL
        elif "workflows" in rel_path:
            node_type = NodeType.WORKFLOW
        else:
            node_type = NodeType.SOURCE
        
        node_id = make_node_id_from_path(rel_path, node_type)
        label = frontmatter.get('title', md_file.stem)
        
        node = GraphNode(
            node_id=node_id,
            node_type=node_type,
            label=label,
            path=rel_path,
            source_id=frontmatter.get('source_id'),
            ecosystem=frontmatter.get('ecosystem'),
            metadata={
                "tags": frontmatter.get('tags', []),
            },
        )
        nodes.append(node)
    
    return nodes


def build_nodes_from_runtime_summary(vault_root: Path) -> List[GraphNode]:
    """Build aggregate nodes from runtime domain summaries."""
    nodes = []
    domain_dir = vault_root / ".runtime" / "domain" / "n8n-workflows"
    summary_file = domain_dir / "summary.json"
    
    if not summary_file.exists():
        return nodes
    
    summary = json.loads(summary_file.read_text())
    
    # Create n8n workflow aggregate node
    node = GraphNode(
        node_id="runtime:n8n-workflows#workflow",
        node_type=NodeType.WORKFLOW,
        label="n8n Workflows Aggregate",
        path=".runtime/domain/n8n-workflows/summary.json",
        ecosystem="n8n",
        metadata={
            "total_workflows": summary.get("total_workflows", 0),
            "total_files_scanned": summary.get("total_files_scanned", 0),
            "workflows_with_ai": summary.get("workflows_with_ai", 0),
        },
    )
    nodes.append(node)
    
    return nodes


def build_edges_from_wikilinks(
    nodes: List[GraphNode],
    vault_root: Path,
) -> List[GraphEdge]:
    """Build edges from wikilinks between graph-visible notes."""
    edges = []
    node_paths = {n.path for n in nodes if n.path}
    
    for node in nodes:
        if not node.path:
            continue
        
        md_path = vault_root / node.path
        if not md_path.exists():
            continue
        
        content = md_path.read_text()
        frontmatter, body = read_frontmatter(content)
        
        wikilinks = extract_wikilinks(body)
        
        for target in wikilinks:
            target_path = target.replace('.md', '')
            
            # Find target node
            target_node_id = None
            for n in nodes:
                if n.path and target_path in n.path:
                    target_node_id = n.node_id
                    break
            
            if target_node_id:
                edge_id = make_deterministic_edge_id(node.node_id, EdgeType.REFERENCED_BY_GUIDE, target_node_id)
                edge = GraphEdge(
                    edge_id=edge_id,
                    edge_type=EdgeType.REFERENCED_BY_GUIDE,
                    from_node_id=node.node_id,
                    to_node_id=target_node_id,
                    confidence=ConfidenceLevel.EXTRACTED,
                    evidence=[EvidenceRef(source_id="vault", path=node.path)],
                )
                edges.append(edge)
    
    return edges


def build_edges_from_domain_relationships(
    nodes: List[GraphNode],
    vault_root: Path,
) -> List[GraphEdge]:
    """Build edges from domain relationship tables."""
    edges = []
    
    for node in nodes:
        if node.node_type != NodeType.ECOSYSTEM or not node.path:
            continue
        
        md_path = vault_root / node.path
        if not md_path.exists():
            continue
        
        content = md_path.read_text()
        frontmatter, body = read_frontmatter(content)
        
        # Parse relationship table: | Related Ecosystem | Relationship |
        for line in body.split('\n'):
            if '|' in line and '[[20-domains/' in line:
                # Extract target ecosystem
                match = re.search(r'\[\[20-domains/([^\]]+)\]\]', line)
                if match:
                    target_slug = match.group(1).split('/')[0]
                    target_node_id = f"vault:20-domains/{target_slug}/{target_slug}.md#ecosystem"
                    
                    edge_id = make_deterministic_edge_id(node.node_id, EdgeType.BELONGS_TO_ECOSYSTEM, target_node_id)
                    edge = GraphEdge(
                        edge_id=edge_id,
                        edge_type=EdgeType.BELONGS_TO_ECOSYSTEM,
                        from_node_id=node.node_id,
                        to_node_id=target_node_id,
                        confidence=ConfidenceLevel.EXTRACTED,
                        evidence=[EvidenceRef(source_id="vault", path=node.path)],
                    )
                    edges.append(edge)
    
    return edges


def build_graph(vault_root: Path, include_runtime_summaries: bool = True) -> Tuple[List[GraphNode], List[GraphEdge]]:
    """Build graph from existing vault records.
    
    Only reads curated/structured layers, does NOT scan raw/.
    """
    nodes = []
    edges = []
    
    # Build nodes from structured layers
    nodes.extend(build_nodes_from_domains(vault_root))
    nodes.extend(build_nodes_from_concepts(vault_root))
    nodes.extend(build_nodes_from_use_cases(vault_root))
    nodes.extend(build_nodes_from_guides(vault_root))
    nodes.extend(build_nodes_from_reports(vault_root))
    nodes.extend(build_nodes_from_sources(vault_root))
    
    if include_runtime_summaries:
        nodes.extend(build_nodes_from_runtime_summary(vault_root))
    
    # Build edges from wikilinks and relationships
    edges.extend(build_edges_from_wikilinks(nodes, vault_root))
    edges.extend(build_edges_from_domain_relationships(nodes, vault_root))
    
    return nodes, edges


def write_graph_outputs(
    nodes: List[GraphNode],
    edges: List[GraphEdge],
    output_dir: Path,
) -> Dict[str, Any]:
    """Write graph outputs to runtime directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Write nodes.jsonl
    nodes_path = output_dir / "nodes.jsonl"
    with nodes_path.open('w') as f:
        for node in nodes:
            f.write(node.to_jsonl_line() + '\n')
    
    # Write edges.jsonl
    edges_path = output_dir / "edges.jsonl"
    with edges_path.open('w') as f:
        for edge in edges:
            f.write(edge.to_jsonl_line() + '\n')
    
    # Write summary
    summary = {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "nodes_by_type": {},
        "edges_by_type": {},
    }
    
    for node in nodes:
        t = node.node_type.value
        summary["nodes_by_type"][t] = summary["nodes_by_type"].get(t, 0) + 1
    
    for edge in edges:
        t = edge.edge_type.value
        summary["edges_by_type"][t] = summary["edges_by_type"].get(t, 0) + 1
    
    summary_path = output_dir / "graph-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    
    # Write validation report
    validation_errors = []
    for node in nodes:
        errors = validate_graph_node(node.to_dict())
        for e in errors:
            validation_errors.append({"node_id": node.node_id, "error": e})
    
    for edge in edges:
        errors = validate_graph_edge(edge.to_dict())
        for e in errors:
            validation_errors.append({"edge_id": edge.edge_id, "error": e})
    
    validation_path = output_dir / "validation-report.json"
    validation_path.write_text(json.dumps({
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "validation_errors": validation_errors,
        "is_valid": len(validation_errors) == 0,
    }, indent=2))
    
    return summary