# Codex Vault Taxonomy

This document explains the Codex Vault's controlled
vocabulary for `primary_domain` and how it interacts with
`related_domains`, `tags`, and `scope.covers`. It is the
authoritative reference for taxonomy decisions; the
controlled values themselves live in
`schemas/vocab-primary-domain.yaml`.

## 1.0 What `primary_domain` describes

`primary_domain` is a **single** value from the
`vocab-primary-domain.yaml` controlled list. It describes the
**intrinsic purpose** of a repository, not the ecosystem in
which it is used. It is the field that drives the strict
validator's R13 check, the Obsidian core-coloring, and the
candidate note's top-level grouping.

When in doubt, ask: **"If this repo were rewritten for a
different runtime, would the change be trivial?"** If yes, the
repo's intrinsic purpose is a *capability* (use case), and
`primary_domain` should be a CAPABILITY value. If no (the
repo's structure is dominated by the platform's APIs), the
repo is intrinsically a *platform* asset, and `primary_domain`
should be a PLATFORM value.

## 2.0 The four categories

The allowed list is organized into four categories, in
priority order. A repo's `primary_domain` should be drawn from
the highest-priority category that applies.

### 2.1 PLATFORM / ECOSYSTEM

The repo is intrinsically bound to one runtime's APIs and
concepts. A "platform" repo is recognizably about one runtime
even when you read the README cold.

| Value | When to use |
|---|---|
| `hermes-agent` | Nous Research Hermes Agent — its core, official extensions, deployment packaging. A repo is `hermes-agent` primary_domain if and only if it is a piece of Hermes Agent itself, not merely something that USES Hermes Agent. |
| `n8n` | n8n workflow automation — core, integrations, templates, skills. A repo is `n8n` primary_domain if and only if it ships n8n nodes, n8n workflows, or n8n-specific tooling. |
| `agentfield` | Agent-Field control plane and ecosystem. A repo is `agentfield` primary_domain if and only if it is part of Agent-Field itself or its official ecosystem. |

### 2.2 CAPABILITY / USE-CASE

The repo is intrinsically about a capability, not a runtime.
A "capability" repo could in principle be ported to a
different runtime with a thin adapter layer; the core loop
is the same.

| Value | When to use |
|---|---|
| `deep-research` | Recursive / multi-step research agents that search, read, and synthesize. Includes autonomous research backends, deep-search systems, recursive-research frameworks. A repo is `deep-research` primary_domain if and only if its core is a research loop, not a runtime integration. |
| `osint` | Open-source intelligence — collection, correlation, and analysis of public data for intelligence purposes. A repo is `osint` primary_domain if and only if its core is intelligence-gathering or analysis, not just a generic search tool. |

### 2.3 CROSS-RUNTIME UTILITY

Broadly useful tooling that is not tied to a single platform
or a single use case.

| Value | When to use |
|---|---|
| `coding-agents` | A coding-agent tool/skill/integration that is broadly useful across multiple agent runtimes (memory systems, skill packs, observability, deployment). |
| `training-systems` | RL / fine-tuning infrastructure (e.g. Tinker-Atropos). |
| `ai-content-generation` | AI-driven content pipelines (e.g. autonovel). |
| `memory-systems` | Standalone memory/knowledge tools. |

### 2.4 FALLBACK

| Value | When to use |
|---|---|
| `cross-domain` | Real cross-domain integration that does not fit a single platform or capability bucket. |
| `general-development` | Reference material not tied to a specific domain. |
| `unknown` | Provenance is not yet determined. |

## 3.0 How `primary_domain` interacts with `related_domains`, `tags`, and `scope.covers`

| Field | Cardinality | Vocabulary | Purpose |
|---|---|---|---|
| `primary_domain` | exactly 1 | controlled (`vocab-primary-domain.yaml`) | The repo's intrinsic purpose. Drives Obsidian core-coloring and the strict validator's R13 check. |
| `related_domains` | 0+ | controlled (subset of `vocab-primary-domain.yaml`) | Ecosystems the repo is useful in. Drives Obsidian ecosystem-coloring. |
| `tags` | 0+ | free-form | Short labels for fine-grained search and Obsidian-side filtering. Not controlled. |
| `scope.covers` (knowledge notes only) | 0+ lines of prose | free-form English | What the note covers, described in plain English. |

The user's request might land in any of these fields.
**Default to `tags` or `scope.covers` first** — only promote
to `primary_domain` if the value describes the repo's
intrinsic purpose. Only promote to `related_domains` if the
value describes an ecosystem the repo is useful in.

## 4.0 Examples

| Repo | primary_domain | related_domains | tags | Why |
|---|---|---|---|---|
| `NousResearch/hermes-agent` | `hermes-agent` | (none) | (none) | It IS Hermes Agent. |
| An n8n workflow template | `n8n` | (none) | `telegram`, `slack` | It ships an n8n workflow. |
| A deep-research framework | `deep-research` | (none) | `multi-hop`, `rag` | Its core is a research loop. |
| An OSINT tool | `osint` | (none) | `collection` | Its core is intelligence-gathering. |
| A memory system usable in both hermes-agent and n8n | `memory-systems` | `[hermes-agent, n8n]` | (none) | Cross-runtime utility; both runtimes include it. |
| A coding assistant CLI that works in any agent runtime | `coding-agents` | `[hermes-agent, n8n, agentfield]` | (none) | Cross-runtime utility; all three runtimes can use it. |

## 5.0 What is NOT in the controlled vocabulary (by design)

The following values were considered and **deliberately
excluded** from `vocab-primary-domain.yaml`. They remain
usable as `tags` or `scope.covers`:

| Excluded value | Why it is excluded |
|---|---|
| `rag` | A technique (retrieval-augmented generation), not a domain. Most "RAG" repos are about a different primary domain (e.g. `deep-research`, `n8n`, or `memory-systems`) and use RAG as a technique. |
| `vector-search` | A technique, not a domain. Same reasoning. |
| `enterprise-search` | A sub-capability of `deep-research`. Repo classification should land in `deep-research` with `enterprise-search` as a tag. |
| `cyber-intelligence` | Overlaps `osint` and is out of scope for the current vault. |
| `evidence-management` | An internal concern of the vault itself, not a repo domain. |

These can be added later as a major-version bump to
`vocab_version` if the vault's scope changes. The schema
enum is the contract; tags and `scope.covers` are the
escape hatch.

## 6.0 Schema contract

The allowed list in `schemas/vocab-primary-domain.yaml` is
the source of truth. Both `schemas/source.schema.yaml` (for
Layer A source records) and any downstream consumer
(knowledge-note `source_taxonomy[]` items, Obsidian
cssclasses derivation) MUST draw from this list.

The list is intentionally additive: a value can be added
without breaking existing records. A value is **never**
removed or renamed without a major-version migration.

The strict validator's R13 (orphan_domain_record_source)
and the controlled-vocab check fire on every Layer A source
record and every knowledge note's `source_taxonomy[]` entry.
Adding a new value requires updating:

- `schemas/vocab-primary-domain.yaml` (the controlled list)
- `schemas/source.schema.yaml` (the source record's enum)
- This document (the human-readable explanation)
- The vault's `.runtime/schemas/` mirror (the runtime data)

## 7.0 Versioning

The vocab carries an explicit `vocab_version` field.
Additive changes bump the minor version (e.g. `1.1.0` →
`1.2.0`). Breaking changes (removing or renaming a value)
bump the major version and require a migration script
because existing records still reference the old value.
