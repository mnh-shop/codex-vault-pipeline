# Phase 05G0: Mass GitHub Ingest Readiness Audit

**Date:** 2026-06-23
**Status:** Audit Complete
**Phase:** 05G0

## Executive Summary

The system is **NOT READY** for mass GitHub ingest. Critical blockers exist in the acquisition layer, legacy script risks, and missing CLI commands. The v2 architecture is sound but incomplete.

## Current Repo State

- Vault: Clean (97d3496)
- Pipeline: Clean except untracked instruction files (ba3863a)
- Tests: 598 passed, 3 skipped
- Compile: Passed

## Instruction File Audit

| Path | Tracked | Purpose | Scope | Risk |
|------|---------|---------|-------|------|
| vault/AGENTS.md | ✅ | Vault-wide instructions | All agents | LOW |
| pipeline/AGENTS.md | ✅ | Pipeline instructions | Pipeline code | LOW |
| pipeline/v2/AGENTS.md | ❌ | GitNexus MCP instructions | GitNexus only | MEDIUM |
| pipeline/v2/.claude/ | ❌ | GitNexus skill files | GitNexus only | MEDIUM |
| pipeline/v2/CLAUDE.md | ❌ | Claude instructions | Claude only | LOW |

**Recommendation:** Ignore untracked files; they are tool-specific and don't affect Codex.

## Python/Package/CLI Audit

### CLI Commands Available
- `codex-vault validate` - ✅
- `codex-vault ingest` - ✅ (single source)
- `codex-vault v2` - ✅ (repomix, n8n, context, packs)
- `codex-vault v2 graph` - ❌ MISSING (should be added)

### v2 Module Boundaries
- `v2/config.py` - ✅
- `v2/manifest.py` - ✅
- `v2/pack_index.py` - ✅
- `v2/context_packer.py` - ✅
- `v2/graph_schema.py` - ✅ (new)
- `v2/graph_builder.py` - ✅ (new, no CLI)

## v2 Architecture Overview

| Component | Module | Input | Output | Vault Write | Runtime Write | Raw Mutate | Safe | Tests |
|-----------|--------|-------|--------|-------------|-------------|-----------|------|-------|
| Source records | v2/manifest.py | Source config | RepoPackManifest | No | No | No | ✅ | ✅ |
| Artifact records | v2/pack_index.py | Repomix output | SQLite DB | No | Yes | No | ✅ | ✅ |
| Units/chunks | v2/pack_index.py | Repomix output | SQLite FTS | No | Yes | No | ✅ | ✅ |
| Domain records | v2/n8n_workflow_catalog.py | Workflow JSON | JSONL | No | Yes | No | ✅ | ✅ |
| n8n catalog | v2/n8n_workflow_catalog.py | raw/n8n/ | catalog.jsonl | No | Yes | No | ✅ | ✅ |
| Context packs | v2/context_packer.py | SQLite FTS | ContextPack | No | No | No | ✅ | ✅ |
| Graph schema | v2/graph_schema.py | N/A | GraphNode/GraphEdge | No | No | No | ✅ | ✅ |
| Graph builder | v2/graph_builder.py | Vault layers | JSONL | No | Yes | No | ✅ | ✅ |

## Legacy Danger Inventory

| Script | Risk | Classification |
|--------|------|----------------|
| `_phase3_*.py` | Outdated source layout | QUARANTINE_FIRST |
| `_phase4_*.py` | Old extraction patterns | QUARANTINE_FIRST |
| `_phase5_*.py` | Old candidate generation | QUARANTINE_FIRST |
| `_phase6_*.py` | Large ingest scripts | QUARANTINE_FIRST |
| `incremental_ingest.py` | Writes to tracked files | QUARANTINE_FIRST |
| `build_indexes.py` | Old index paths | QUARANTINE_FIRST |
| `validate.py` | Old validation rules | QUARANTINE_FIRST |

## Mass Ingest Readiness Assessment

**Is mass ingest safe tonight?** ❌ NO

**Blockers:**
1. No `v2 graph` CLI command (graph_builder exists but no CLI)
2. Legacy scripts could interfere with v2
3. No batch ingest safety checks
4. No duplicate source detection in v2

**Maximum safe batch size:** 1 repo (single-source ingest only)

## Vault Architecture Assessment

**Ecosystem hubs:** 9 hubs are well-structured but need capability edges
**Source-layer:** Good coverage but missing source_id in some frontmatter
**Use cases/guides:** Well-linked to hubs via wikilinks
**Concepts:** Thin but adequate

**Recommendation:** Repositories should stay in source layer + runtime graph, not become Obsidian-visible nodes directly.

## Graph Builder Quality Audit

- Top hubs: Agents(40), Hermes Agent(39), AgentField(32)
- "Agents" is a generic hub - should be more specific
- mattpocock-skills: ✅ Represented (7 skill nodes)
- n8n workflow summary: ✅ Represented (aggregate node)
- Too many `referenced_by_guide` edges (218) - weak taxonomy

## Coding Agent Skeptical Opinion

1. **Architecturally fragile:** Legacy scripts in same package as v2 code
2. **20 repos tonight:** Would break - no batch CLI, no duplicate detection
3. **200 repos this week:** Would catastrophically fail - legacy scripts would corrupt state
4. **Most likely wrong:** `_phase6_ingest_deep_research_osint.py` - 65KB of unreviewed code
5. **Unverified assumptions:** Legacy scripts assume old source layout
6. **Code smells:** Bare `except:` in legacy code, hardcoded paths
7. **Refuse to run:** Any `_phase*.py` script until reviewed
8. **Safest next 3-step plan:**
   - Add `v2 graph build` CLI command
   - Quarantine legacy scripts (rename with `.quarantine` suffix)
   - Add duplicate source detection to v2

## Risk Table

| Risk | Level | Description |
|------|-------|-------------|
| Legacy script interference | BLOCKER | `_phase*.py` scripts could corrupt v2 state |
| Missing graph CLI | HIGH | graph_builder exists but no CLI command |
| No batch ingest | HIGH | Only single-source ingest available |
| No duplicate detection | HIGH | Could create duplicate sources |
| Weak edge taxonomy | MEDIUM | Too many generic `referenced_by_guide` edges |
| Source-layer frontmatter | MEDIUM | Some notes missing source_id |
| Untracked instruction files | LOW | GitNexus/Claude instructions not committed |

## Recommended Next Prompts

1. "Add `v2 graph build` CLI command to pipeline"
2. "Quarantine legacy `_phase*.py` scripts by renaming with `.quarantine` suffix"
3. "Add duplicate source detection to v2 ingest pipeline"