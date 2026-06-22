# Codex Vault Pipeline — retrieval v2 policy
"""Retrieval policy for v2 repo-context lane."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List
from pathlib import Path
import json


@dataclass
class RetrievalPolicy:
    """Retrieval policy for v2 context packing."""
    
    # Core policies
    repomix_packs_canonical: bool = True
    sqlite_fts_for_exact_search: bool = True
    vector_for_semantic_search: bool = True
    metadata_filters_mandatory: bool = True
    
    # Provenance requirements
    preserve_source_id: bool = True
    preserve_repo_url: bool = True
    preserve_commit: bool = True
    preserve_path: bool = True
    preserve_file_hash: bool = True
    preserve_artifact_role: bool = True
    preserve_acquisition_status: bool = True
    
    # Priority policies
    readme_low_priority: bool = True
    generated_catalog_low_priority: bool = True
    exclude_from_centrality: bool = True
    
    # Type-specific policies
    n8n_workflow_json_typed: bool = True
    skill_soul_typed: bool = True
    
    # External checks
    deepwiki_sanity_only: bool = True
    
    # Candidate adapters
    cocoindex_candidate: bool = True
    llamaindex_candidate: bool = True
    haystack_conceptual: bool = True
    
    # Deferred
    graphrag_deferred: bool = True
    lightrag_deferred: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "core_policies": {
                "repomix_packs_canonical": self.repomix_packs_canonical,
                "sqlite_fts_for_exact_search": self.sqlite_fts_for_exact_search,
                "vector_for_semantic_search": self.vector_for_semantic_search,
                "metadata_filters_mandatory": self.metadata_filters_mandatory,
            },
            "provenance_requirements": {
                "preserve_source_id": self.preserve_source_id,
                "preserve_repo_url": self.preserve_repo_url,
                "preserve_commit": self.preserve_commit,
                "preserve_path": self.preserve_path,
                "preserve_file_hash": self.preserve_file_hash,
                "preserve_artifact_role": self.preserve_artifact_role,
                "preserve_acquisition_status": self.preserve_acquisition_status,
            },
            "priority_policies": {
                "readme_low_priority": self.readme_low_priority,
                "generated_catalog_low_priority": self.generated_catalog_low_priority,
                "exclude_from_centrality": self.exclude_from_centrality,
            },
            "type_specific_policies": {
                "n8n_workflow_json_typed": self.n8n_workflow_json_typed,
                "skill_soul_typed": self.skill_soul_typed,
            },
            "external_checks": {
                "deepwiki_sanity_only": self.deepwiki_sanity_only,
            },
            "candidate_adapters": {
                "cocoindex_candidate": self.cocoindex_candidate,
                "llamaindex_candidate": self.llamaindex_candidate,
                "haystack_conceptual": self.haystack_conceptual,
            },
            "deferred": {
                "graphrag_deferred": self.graphrag_deferred,
                "lightrag_deferred": self.lightrag_deferred,
            },
        }
    
    def write_policy_file(self, path: Path):
        """Write policy to file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        
        content = """# Retrieval v2 Policy

**Date:** 2026-06-22
**Status:** Active

## 1.0 Core Policies

- **Repomix packs are canonical AI-ready repo snapshots, not wiki pages.**
- **SQLite/FTS is used for exact identifier/path/API search.**
- **Vector search is used for semantic/conceptual search.**
- **Metadata filters are mandatory before context packing.**

## 2.0 Provenance Requirements

Context packer must preserve:
- source_id
- repo URL
- commit
- path
- file hash
- artifact_role
- acquisition_status

## 3.0 Priority Policies

- **READMEs and generated catalogs can be indexed, but are low-priority unless query explicitly asks for README/setup/docs overview.**
- **Generated catalog/index pages are excluded from Obsidian centrality and context-packer default priority.**

## 4.0 Type-Specific Policies

- **n8n workflow JSON is preserved and typed as workflow records.**
- **skill/SOUL files are preserved and typed as agent capability records.**

## 5.0 External Checks

- **DeepWiki is external sanity check only.**

## 6.0 Candidate Adapters

- **CocoIndex is candidate for code-aware Tree-sitter chunking after Repomix pilot.**
- **LlamaIndex is candidate for GitHub API ingestion adapter, not canonical source of truth yet.**
- **Haystack pattern is used conceptually for hybrid retrieval + reranking.**

## 7.0 Deferred

- **GraphRAG/LightRAG are deferred until clean typed corpus exists.**
"""
        
        path.write_text(content)
