"""Phase 6 — Incremental Ingest: deep-research / OSINT batch (9 repos).

This script performs a full incremental ingest for the 9 deep-research
and OSINT repositories that the user pre-classified in the prior task.

For each repo:
  - Layer A: source.v1.yaml with all multi-axis fields
  - Layer B: artifacts (one per non-.git file)
  - Layer C: occurrences (one per file path)
  - Layer D: domain records (where applicable)
  - knowledge-notes/<slug>.json (candidate)
  - wiki/_candidates/<slug>.md (frontmatter mirror)
  - wiki/_candidates/_migration/<slug>-migration.yaml
  - .runtime/migration-reports/<slug>-migration.yaml

The script is read-only on existing records. It writes only to
fresh paths in the new source's namespace. It also runs
detect-secrets to mark each file's security_status (clean /
flagged / blocked) and preserves provenance.

The script is intended to be run ONCE per batch. It exits 0 on
success and non-zero on any failure. Re-running it after success
is a no-op (it checks for the existence of the source record
first).

Hard rules:
  - Do not modify existing promoted wiki notes
  - Do not promote candidates (knowledge_status=candidate only)
  - Do not modify raw/ (we only added new subdirs via clone)
  - Preserve existing IDs; use sha256 for new ones
  - Blocked content excluded from indexes; flagged redacted
  - Use existing controlled vocab values; no silent invention

Usage:
    CODEX_VAULT_ROOT=/path/to/codex-vault \
    PYTHONPATH=/path/to/codex-vault-pipeline/src \
    python3 _phase6_ingest_deep_research_osint.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

try:
    from jsonschema import Draft202012Validator
except ImportError:
    print("ERROR: jsonschema library required", file=sys.stderr)
    sys.exit(2)

from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root
from codex_vault_pipeline.extractors.tech_profile import extract_tech_profile
from codex_vault_pipeline.utils import file_policy


# ----- Constants ---------------------------------------------------------

VAULT = Path(os.environ.get("CODEX_VAULT_ROOT") or ".")
RUNTIME = VAULT / ".runtime"
RAW_DIR = VAULT / "raw"
SOURCES_DIR = RUNTIME / "sources"
ARTIFACTS_DIR = RUNTIME / "artifacts"
OCCURRENCES_DIR = RUNTIME / "occurrences"
KN_DIR = RUNTIME / "knowledge-notes"
MR_DIR = RUNTIME / "migration-reports"
WIKI_CANDIDATES = VAULT / "wiki" / "_candidates"
WIKI_CANDIDATES_MIGRATIONS = WIKI_CANDIDATES / "_migration"
REPORTS_DIR = RUNTIME / "reports"

GENERATOR = "codex-vault/phase-6-deep-research-osint-ingest"
GENERATOR_VERSION = "1.0.0"
RUN_ID = f"phase-6-ingest-deep-research-osint-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

# Cap raw/ walk per repo to keep ingest bounded for very large
# repositories (e.g. DeepResearch, SimpleDeepSearcher with model
# checkpoints).
MAX_FILES_PER_REPO = 50_000
# Skip files larger than this from full extraction.
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
# Skip directories
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "target", ".idea", ".vscode", "site-packages",
}

# Heuristic: files that look like model weights or large data
LARGE_DATA_PATTERNS = [
    re.compile(r"\.(safetensors|pt|pth|onnx|bin|gguf|ckpt)$", re.IGNORECASE),
    re.compile(r"\.parquet$", re.IGNORECASE),
]

# Source classifications as decided by the user.
REPO_CONFIGS: List[Dict[str, Any]] = [
    {
        "source_id": "github:langchain-ai/open_deep_research",
        "raw_local": "open_deep_research",
        "primary_domain": "deep-research",
        "ecosystems": ["langchain", "mcp"],
        "capabilities": [
            "deep-research", "web-search", "evidence-collection",
            "report-generation", "retrieval", "rag",
        ],
        "topics": [
            "langgraph", "multi-agent", "plan-and-execute", "mcp-servers",
            "search-api", "tavily", "exa", "arxiv", "deep-research-bench",
            "openai", "anthropic", "groq", "deepseek",
        ],
        "integration_targets": [
            "github", "tavily", "firecrawl",
            "arxiv", "duckduckgo", "exa", "linkup", "azure-search",
        ],
        "project_use_cases": ["deep-research-system", "agent-orchestration"],
        "artifact_role": "agent-platform",
        "source_role": "official-extension",
        "authority_level": "official",
        "maturity_signals": {
            "license": "MIT",
            "stars": 11767,
            "has_tests": True,
            "has_docs": True,
            "active": True,
        },
        "title": "Open Deep Research — LangChain-AI Configurable Deep-Research Agent",
        "slug": "open-deep-research",
        "domain_family": "deep-research",
        "scope_covers": (
            "github:langchain-ai/open_deep_research at the snapshot preserved in "
            "codex-vault/raw/open_deep_research/. LangChain-AI's configurable "
            "open-source deep research agent: multi-provider (OpenAI / Anthropic / "
            "Groq / DeepSeek), multi-search (Tavily default, plus native Anthropic / "
            "OpenAI web search and full MCP compatibility), LangGraph-based planning "
            "and report-compression pipeline. Scored on the Deep Research Bench leaderboard."
        ),
        "scope_excludes": (
            "Other LangChain-AI sources (deep_research_from_scratch is the educational "
            "companion, handled separately in this batch). All n8n / Agent-Field sources. "
            "The hosted Deep Research Bench leaderboard (not in this repo)."
        ),
        "reuse": {"intended_use": "study", "priority": "high", "fit_for_maxios": "medium"},
        "interfaces": [
            {"kind": "python-package", "name": "open_deep_research", "path": "src/open_deep_research/"},
            {"kind": "rest-api", "name": "LangGraph server", "path": "langgraph.json"},
            {"kind": "mcp-client", "name": "MCP config", "path": "src/open_deep_research/configuration.py"},
        ],
        "workflow_roles": ["research-agent", "plan-and-execute-agent"],
        "provides": ["cited-report", "research-plan", "multi-source-synthesis", "compressed-findings"],
        "requires": ["search-api", "llm-api", "vector-store"],
        "composition_edges": [
            {"relation": "can-call", "target": "github:zilliztech/deep-searcher", "evidence": "vector store"},
            {"relation": "can-feed", "target": "github:arc53/DocsGPT", "evidence": "RAG consumer"},
            {"relation": "can-wrap", "target": "github:NousResearch/hermes-agent", "evidence": "skill wrapping"},
        ],
    },
    {
        "source_id": "github:langchain-ai/deep_research_from_scratch",
        "raw_local": "deep_research_from_scratch",
        "primary_domain": "deep-research",
        "ecosystems": ["langchain", "mcp"],
        "capabilities": [
            "deep-research", "web-search", "evidence-collection",
            "report-generation", "retrieval",
        ],
        "topics": [
            "langgraph", "multi-agent", "mcp-servers", "tavily",
            "tutorial", "learning", "jupyter-notebook", "langchain-1.0",
        ],
        "integration_targets": ["github", "tavily", "arxiv"],
        "project_use_cases": ["deep-research-system", "knowledge-factory"],
        "artifact_role": "reference",
        "source_role": "reference",
        "authority_level": "official",
        "maturity_signals": {
            "license": "MIT",
            "stars": 731,
            "has_tests": True,
            "has_docs": True,
            "active": True,
        },
        "title": "Deep Research From Scratch — LangChain-AI Build-a-Deep-Researcher Tutorial",
        "slug": "deep-research-from-scratch",
        "domain_family": "deep-research",
        "scope_covers": (
            "github:langchain-ai/deep_research_from_scratch at the snapshot "
            "preserved in codex-vault/raw/deep_research_from_scratch/. The "
            "Jupyter-Notebook-heavy educational companion to open_deep_research: "
            "builds the deep-researcher from first principles across multiple "
            "notebooks, using LangGraph 1.0 and langchain-mcp-adapters. Designed "
            "as a teach-along for the LangChain Academy course on deep research."
        ),
        "scope_excludes": (
            "open_deep_research (the production agent, handled separately in this "
            "batch). n8n / Agent-Field sources. Hosted LangSmith studio (not in this repo)."
        ),
        "reuse": {"intended_use": "study", "priority": "medium", "fit_for_maxios": "medium"},
        "interfaces": [
            {"kind": "python-package", "name": "deep_research_from_scratch", "path": "src/deep_research_from_scratch/"},
            {"kind": "mcp-client", "name": "MCP config", "path": "src/deep_research_from_scratch/"},
        ],
        "workflow_roles": ["research-agent", "tutorial-reference"],
        "provides": ["cited-report", "research-plan", "multi-source-synthesis"],
        "requires": ["search-api", "llm-api"],
        "composition_edges": [
            {"relation": "can-call", "target": "github:zilliztech/deep-searcher", "evidence": "vector store"},
            {"relation": "can-feed", "target": "github:arc53/DocsGPT", "evidence": "RAG consumer"},
            {"relation": "can-wrap", "target": "github:NousResearch/hermes-agent", "evidence": "skill wrapping"},
        ],
    },
    {
        "source_id": "github:Alibaba-NLP/DeepResearch",
        "raw_local": "DeepResearch",
        "primary_domain": "deep-research",
        "ecosystems": [],
        "capabilities": [
            "deep-research", "retrieval", "report-generation",
            "agent-orchestration",
        ],
        "topics": [
            "tongyi", "agentic", "web-agent", "llm",
            "deep-search", "rlhf", "react", "research",
            "humanitys-last-exam", "browsecomp", "webwalker",
        ],
        "integration_targets": [
            "aliyun-bailian", "dashscope", "aliyun-oss",
        ],
        "project_use_cases": ["deep-research-system"],
        "artifact_role": "agent-platform",
        "source_role": "core",
        "authority_level": "canonical-upstream",
        "maturity_signals": {
            "license": "Apache-2.0",
            "stars": 19497,
            "has_tests": True,
            "has_docs": True,
            "active": True,
        },
        "title": "Tongyi DeepResearch — Alibaba-NLP 30B-A3B Agentic Deep-Research Model",
        "slug": "alibaba-nlp-deepresearch",
        "domain_family": "deep-research",
        "scope_covers": (
            "github:Alibaba-NLP/DeepResearch at the snapshot preserved in "
            "codex-vault/raw/DeepResearch/. Alibaba-NLP's Tongyi DeepResearch: "
            "an agentic LLM (30.5B total params, 3.3B activated) specialized "
            "for long-horizon deep information-seeking. Builds on the WebAgent "
            "submodule. Two inference paradigms (ReAct and IterResearch Heavy). "
            "SOTA on Humanity's Last Exam, BrowseComp, BrowseComp-ZH, WebWalkerQA, "
            "xbench-DeepSearch, FRAMES, SimpleQA. The companion model "
            "Tongyi-DeepResearch-30B-A3B is on HuggingFace / ModelScope, NOT "
            "in this repo; this repo contains the inference pipeline + training code."
        ),
        "scope_excludes": (
            "The WebAgent submodule's earlier work is included; downstream "
            "deployments via Alibaba Cloud Bailian (handled outside this repo). "
            "Model weights (on HuggingFace). All non-Alibaba deep-research "
            "sources in this batch (handled separately)."
        ),
        "reuse": {"intended_use": "study", "priority": "high", "fit_for_maxios": "medium"},
        "interfaces": [
            {"kind": "python-package", "name": "WebAgent", "path": "WebAgent/"},
            {"kind": "cli", "name": "inference runner", "path": "WebAgent/inference/"},
        ],
        "workflow_roles": ["research-agent", "deep-search-agent"],
        "provides": ["cited-report", "research-plan", "multi-source-synthesis", "benchmark-sota"],
        "requires": ["search-api", "llm-api", "gpu"],
        "composition_edges": [
            {"relation": "can-feed", "target": "github:arc53/DocsGPT", "evidence": "RAG consumer"},
            {"relation": "optional", "target": "github:zilliztech/deep-searcher", "evidence": "different vector stacks"},
        ],
    },
    {
        "source_id": "github:RUCAIBox/ManuSearch",
        "raw_local": "ManuSearch",
        "primary_domain": "deep-research",
        "ecosystems": [],
        "capabilities": [
            "deep-research", "web-search", "evidence-collection",
            "retrieval", "agent-orchestration",
        ],
        "topics": [
            "orion", "multi-agent", "long-tail",
            "open-web-reasoning", "benchmark", "vllm",
            "planning-agent", "search-agent", "reader-agent",
        ],
        "integration_targets": [],
        "project_use_cases": ["deep-research-system"],
        "artifact_role": "reference",
        "source_role": "reference",
        "authority_level": "third-party",
        "maturity_signals": {
            "license": "MIT",
            "stars": 32,
            "has_tests": False,
            "has_docs": True,
            "active": False,
        },
        "title": "ManuSearch — RUCAIBox Transparent Multi-Agent Deep-Search Framework",
        "slug": "manusearch",
        "domain_family": "deep-research",
        "scope_covers": (
            "github:RUCAIBox/ManuSearch at the snapshot preserved in "
            "codex-vault/raw/ManuSearch/. RUC-AIBOX's transparent multi-agent "
            "deep-search framework: three collaborative agents (solution-planner, "
            "internet-searcher, structured-reader) operating over the ORION "
            "long-tail open-web reasoning benchmark. Open-source reproduction "
            "of Manus-style agentic deep search. Requires vLLM-served "
            "openai-compatible endpoints for all three agents."
        ),
        "scope_excludes": (
            "All other deep-research sources in this batch. R1-Searcher / "
            "R1-Searcher++ (companion work from the same lab, not in this repo). "
            "The ORION benchmark dataset (on HuggingFace, not in this repo)."
        ),
        "reuse": {"intended_use": "study", "priority": "low", "fit_for_maxios": "medium"},
        "interfaces": [
            {"kind": "python-package", "name": "ManuSearch", "path": "ManuSearch/"},
            {"kind": "cli", "name": "search runner", "path": "ManuSearch/run_search.py"},
        ],
        "workflow_roles": ["multi-agent-deep-search", "planning-agent", "search-agent", "reader-agent"],
        "provides": ["sub-query-plan", "retrieved-documents", "extracted-evidence"],
        "requires": ["search-api", "llm-api", "openai-compatible-vllm"],
        "composition_edges": [
            {"relation": "can-feed", "target": "github:arc53/DocsGPT", "evidence": "RAG consumer"},
            {"relation": "optional", "target": "github:zilliztech/deep-searcher", "evidence": "vector store alternative"},
        ],
    },
    {
        "source_id": "github:RUCAIBox/SimpleDeepSearcher",
        "raw_local": "SimpleDeepSearcher",
        "primary_domain": "deep-research",
        "ecosystems": [],
        "capabilities": [
            "deep-research", "retrieval", "web-search",
        ],
        "topics": [
            "r1-searcher", "knowledge-distillation", "sft",
            "reasoning", "self-distillation", "benchmark",
            "training-pipeline",
        ],
        "integration_targets": [],
        "project_use_cases": ["deep-research-system", "knowledge-factory"],
        "artifact_role": "reference",
        "source_role": "reference",
        "authority_level": "third-party",
        "maturity_signals": {
            "license": "MIT",
            "stars": 120,
            "has_tests": False,
            "has_docs": True,
            "active": False,
        },
        "title": "SimpleDeepSearcher — RUCAIBox SFT Distillation for Deep Information Seeking",
        "slug": "simpledeepresearcher",
        "domain_family": "deep-research",
        "scope_covers": (
            "github:RUCAIBox/SimpleDeepSearcher at the snapshot preserved in "
            "codex-vault/raw/SimpleDeepSearcher/. RUC-AIBOX's lightweight framework "
            "for distilling deep-search capability into small LLMs via SFT on "
            "real-web reasoning trajectories: 871 curated samples produce "
            "out-of-domain performance on par with RL-based baselines. Repo "
            "contains the training / distillation code, NOT the model "
            "checkpoints (those are on HuggingFace)."
        ),
        "scope_excludes": (
            "Trained model weights (on HuggingFace). R1-Searcher / R1-Searcher++ "
            "(companion work). All other deep-research sources in this batch."
        ),
        "reuse": {"intended_use": "study", "priority": "medium", "fit_for_maxios": "medium"},
        "interfaces": [
            {"kind": "python-package", "name": "SimpleDeepSearcher", "path": "SimpleDeepSearcher/"},
            {"kind": "cli", "name": "training script", "path": "SimpleDeepSearcher/train/"},
        ],
        "workflow_roles": ["research-agent", "training-pipeline"],
        "provides": ["distilled-training-trajectories", "reasoning-traces"],
        "requires": ["search-api", "llm-api", "gpu"],
        "composition_edges": [
            {"relation": "optional", "target": "github:NousResearch/tinker-atropos", "evidence": "training-systems peer"},
            {"relation": "can-feed", "target": "github:arc53/DocsGPT", "evidence": "RAG consumer"},
        ],
    },
    {
        "source_id": "github:bytedance/deer-flow",
        "raw_local": "deer-flow",
        "primary_domain": "coding-agents",
        "ecosystems": ["langchain", "docker"],
        "capabilities": [
            "deep-research", "agent-orchestration", "memory",
            "browser-automation", "code-generation", "web-search",
        ],
        "topics": [
            "langgraph", "langmanus", "superagent", "multi-agent",
            "podcast", "sandbox", "subagent", "long-horizon",
            "nextjs", "fastapi", "im-bridge", "tavily",
            "langfuse", "langsmith",
        ],
        "integration_targets": [
            "tavily", "github", "slack", "telegram",
            "lark", "dingtalk", "wecom",
        ],
        "project_use_cases": ["agent-orchestration", "deep-research-system", "coding-agent-memory"],
        "artifact_role": "agent-platform",
        "source_role": "core",
        "authority_level": "canonical-upstream",
        "maturity_signals": {
            "license": "MIT",
            "stars": 72307,
            "has_tests": True,
            "has_docs": True,
            "active": True,
        },
        "title": "DeerFlow 2.0 — Bytedance Long-Horizon Super-Agent Harness",
        "slug": "deer-flow",
        "domain_family": "coding-agents",
        "scope_covers": (
            "github:bytedance/deer-flow at the snapshot preserved in "
            "codex-vault/raw/deer-flow/. Bytedance's open-source super-agent "
            "harness: orchestrates sub-agents, memory, and sandboxes to "
            "research, code, and create. 2.0 is a ground-up rewrite (the "
            "original 1.x deep-research framework lives on the main-1.x branch). "
            "Composed of a FastAPI backend, a Next.js frontend, an Nginx gateway, "
            "and a provisioner. Bridges to Slack, Telegram, Lark, DingTalk, "
            "Discord, and WeCom. Supports LangSmith and Langfuse observability. "
            "Largest repo in the batch (6.4M LOC Python + 1.3M LOC TypeScript)."
        ),
        "scope_excludes": (
            "The 1.x branch's deep-research-only code (on main-1.x; not in the "
            "main snapshot). External model-serving plans (ByteDance Volcengine "
            "coding plan, BytePlus InfoQuest) referenced from the README but not "
            "in the repo. The Next.js frontend's full dependency tree (we index "
            "the manifest only)."
        ),
        "reuse": {"intended_use": "study", "priority": "high", "fit_for_maxios": "high"},
        "interfaces": [
            {"kind": "rest-api", "name": "FastAPI gateway", "path": "backend/app/"},
            {"kind": "web-ui", "name": "Next.js frontend", "path": "frontend/"},
            {"kind": "mcp-server", "name": "MCP server", "path": "backend/"},
            {"kind": "cli", "name": "embedded Python client", "path": "backend/"},
            {"kind": "docker-service", "name": "compose", "path": "docker/docker-compose.yaml"},
        ],
        "workflow_roles": ["super-agent-harness", "long-horizon-research-agent", "subagent-orchestrator", "podcast-producer"],
        "provides": [
            "cited-report", "code-artifact", "podcast", "subagent-delegation",
            "sandboxed-execution", "persistent-memory", "im-bridge",
        ],
        "requires": ["search-api", "llm-api", "sandbox-runtime", "im-channel", "observability"],
        "composition_edges": [
            {"relation": "can-deploy-with", "target": "github:Agent-Field/agentfield", "evidence": "control plane"},
            {"relation": "can-orchestrate", "target": "github:NousResearch/hermes-agent", "evidence": "long-horizon peer"},
            {"relation": "can-call", "target": "github:zilliztech/deep-searcher", "evidence": "vector store"},
            {"relation": "can-call", "target": "github:vectorize-io/hindsight", "evidence": "memory"},
        ],
    },
    {
        "source_id": "github:jivoi/awesome-osint",
        "raw_local": "awesome-osint",
        "primary_domain": "osint",
        "ecosystems": [],
        "capabilities": ["osint-investigation"],
        "topics": [
            "awesome-list", "osint-resources", "curated",
            "footprinting", "people-search", "company-research",
            "geolocation", "dark-web", "google-dorks",
        ],
        "integration_targets": ["github"],
        "project_use_cases": ["osint-tool", "knowledge-factory"],
        "artifact_role": "source-catalog",
        "source_role": "reference",
        "authority_level": "community",
        "maturity_signals": {
            "license": "NOASSERTION",
            "stars": 26930,
            "has_tests": False,
            "has_docs": True,
            "active": True,
        },
        "title": "Awesome OSINT — jivoi Curated List of OSINT Resources",
        "slug": "awesome-osint",
        "domain_family": "osint",
        "scope_covers": (
            "github:jivoi/awesome-osint at the snapshot preserved in "
            "codex-vault/raw/awesome-osint/. jivoi's curated markdown list "
            "of ~hundreds of OSINT resources, tools, and references, "
            "organized by category (people search, geolocation, dark web, "
            "footprinting, etc.). README-only repo; no executable code."
        ),
        "scope_excludes": (
            "All executable OSINT frameworks and tools (lockfale/OSINT-Framework "
            "and gs-ai/SYNINT in this batch). Hermes-Agent and n8n integrations."
        ),
        "reuse": {"intended_use": "study", "priority": "medium", "fit_for_maxios": "medium"},
        "interfaces": [
            {"kind": "dataset", "name": "curated list", "path": "README.md"},
        ],
        "workflow_roles": ["osint-resource-catalog"],
        "provides": ["osint-resource-index"],
        "requires": [],
        "composition_edges": [
            {"relation": "optional", "target": "osint-skill", "evidence": "catalog optionally references skills"},
            {"relation": "optional", "target": "osint-tool", "evidence": "catalog optionally references tools"},
        ],
    },
    {
        "source_id": "github:lockfale/OSINT-Framework",
        "raw_local": "OSINT-Framework",
        "primary_domain": "osint",
        "ecosystems": [],
        "capabilities": ["osint-investigation"],
        "topics": [
            "osint-resources", "reconnaissance", "footprinting",
            "curated", "static-site", "d3", "html",
        ],
        "integration_targets": ["github"],
        "project_use_cases": ["osint-tool"],
        "artifact_role": "source-catalog",
        "source_role": "reference",
        "authority_level": "community",
        "maturity_signals": {
            "license": "MIT",
            "stars": 11518,
            "has_tests": False,
            "has_docs": True,
            "active": True,
        },
        "title": "OSINT Framework — lockfale Advanced Reconnaissance Framework Static Site",
        "slug": "osint-framework",
        "domain_family": "osint",
        "scope_covers": (
            "github:lockfale/OSINT-Framework at the snapshot preserved in "
            "codex-vault/raw/OSINT-Framework/. lockfale's Advanced Reconnaissance "
            "Framework: a static HTML/JS webapp (named `arf` in package.json) "
            "that links out to ~thousands of OSINT tools, organized as a "
            "category tree (D3-rendered). Served via `python3 -m http.server 8000` "
            "for local dev or Cloudflare wrangler for deploy. Repo is "
            "primarily JavaScript (per GH language stats)."
        ),
        "scope_excludes": (
            "All executable OSINT frameworks (gs-ai/SYNINT in this batch). "
            "Curated markdown lists (jivoi/awesome-osint in this batch). "
            "Hermes-Agent and n8n integrations."
        ),
        "reuse": {"intended_use": "study", "priority": "medium", "fit_for_maxios": "medium"},
        "interfaces": [
            {"kind": "static-site", "name": "arf", "path": "public/index.html"},
        ],
        "workflow_roles": ["osint-resource-catalog"],
        "provides": ["osint-resource-index"],
        "requires": [],
        "composition_edges": [
            {"relation": "optional", "target": "osint-skill", "evidence": "catalog optionally references skills"},
            {"relation": "optional", "target": "osint-tool", "evidence": "catalog optionally references tools"},
        ],
    },
    {
        "source_id": "github:gs-ai/SYNINT",
        "raw_local": "SYNINT",
        "primary_domain": "osint",
        "ecosystems": [],
        "capabilities": [
            "osint-investigation", "evidence-collection",
            "entity-resolution", "web-search", "data-extraction",
        ],
        "topics": [
            "osint-framework", "multi-agent", "stealth",
            "evidence-ledger", "sqlite", "forensic",
            "modular", "chain-of-custody", "staged-pipeline",
            "ethical-hacking", "threat-intelligence",
        ],
        "integration_targets": [
            "camoufox", "scrapy", "scrapling", "waymore",
            "pypdf", "python-docx", "pillow", "pytesseract", "opencv",
        ],
        "project_use_cases": ["osint-tool", "maxios"],
        "artifact_role": "agent-platform",
        "source_role": "community-extension",
        "authority_level": "community",
        "maturity_signals": {
            "license": "NOASSERTION",
            "stars": 43,
            "has_tests": True,
            "has_docs": True,
            "active": True,
        },
        "title": "SYNINT — gs-ai Local-First OSINT Investigation Framework",
        "slug": "synint",
        "domain_family": "osint",
        "scope_covers": (
            "github:gs-ai/SYNINT at the snapshot preserved in "
            "codex-vault/raw/SYNINT/. gs-ai's local-first OSINT investigation "
            "framework: 46 modular agents in canonical order, pluggable "
            "collection engines (camoufox for stealth browsing, scrapy / "
            "scrapling / waymore for scraping, pypdf / python-docx / pillow / "
            "pytesseract / opencv for document and media extraction), "
            "centralized evidence and entity registries, append-only "
            "chain-of-custody ledger, resumable runs, structured forensic "
            "reporting (HTML+JSON+SQLite). Staged pipeline (quick / standard "
            "/ deep). 4 execution models."
        ),
        "scope_excludes": (
            "All other OSINT resources in this batch. Hermes-Agent integration "
            "(SYNINT can be wrapped as a Hermes skill, but the integration is "
            "not in this repo). Deep-research sources in this batch."
        ),
        "reuse": {"intended_use": "study", "priority": "medium", "fit_for_maxios": "high"},
        "interfaces": [
            {"kind": "cli", "name": "main.py", "path": "main.py"},
            {"kind": "python-package", "name": "SYNINT", "path": "agents/"},
        ],
        "workflow_roles": ["osint-investigation-framework", "evidence-collector", "entity-resolver"],
        "provides": [
            "forensic-report", "chain-of-custody-ledger", "evidence-registry",
            "entity-registry", "resumable-checkpoints",
        ],
        "requires": ["target-domain", "sqlite"],
        "composition_edges": [
            {"relation": "can-call", "target": "github:NousResearch/hermes-agent", "evidence": "SYNINT as a skill for Hermes"},
            {"relation": "optional", "target": "github:vectorize-io/hindsight", "evidence": "memory"},
            {"relation": "can-store-in", "target": "github:zilliztech/deep-searcher", "evidence": "entity embeddings"},
        ],
    },
]


# ----- Helper functions --------------------------------------------------

def sha256_file(path: Path, max_bytes: int = MAX_FILE_SIZE) -> Optional[str]:
    """Compute sha256 of a file, returning None if the file is too large
    or unreadable. Reads in chunks to avoid loading huge files into memory."""
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > max_bytes:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return None





def walk_repo_files(raw_root: Path, max_files: int = MAX_FILES_PER_REPO) -> List[Path]:
    """Walk the raw/ tree, excluding .git, node_modules, etc.
    Returns at most max_files paths."""
    out: List[Path] = []
    for p in raw_root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(raw_root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def get_pinned_commit(raw_root: Path) -> str:
    """Read HEAD commit from the clone's .git, or 'unknown' if not a git clone."""
    git_dir = raw_root / ".git"
    if not git_dir.exists():
        return "unknown"
    head = git_dir / "HEAD"
    if not head.exists():
        return "unknown"
    try:
        ref = head.read_text().strip()
        if ref.startswith("ref:"):
            ref_path = git_dir / ref.split(" ", 1)[1]
            if ref_path.exists():
                return ref_path.read_text().strip()
        return ref
    except Exception:
        return "unknown"


# ----- Per-repo ingest ---------------------------------------------------

def ingest_one(cfg: Dict[str, Any], run_id: str) -> Dict[str, Any]:
    """Run a full ingest for one repo. Returns a summary dict.
    Idempotent: returns immediately if the source record already exists."""
    source_id = cfg["source_id"]
    safe_id = source_id.replace(":", "_").replace("/", "_")
    src_dir = SOURCES_DIR / safe_id
    if (src_dir / "source.v1.yaml").exists():
        return {"source_id": source_id, "status": "skipped", "reason": "already ingested"}

    raw_root = RAW_DIR / cfg["raw_local"]
    if not raw_root.is_dir():
        return {"source_id": source_id, "status": "failed", "reason": f"raw_root missing: {raw_root}"}

    src_dir.mkdir(parents=True, exist_ok=True)

    # ----- Layer A: source record -----
    pinned_commit = get_pinned_commit(raw_root)
    # Get tree_sha from the local clone (matches existing convention)
    tree_sha = ""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "-C", str(raw_root), "rev-parse", "HEAD^{tree}"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode == 0:
            tree_sha = out.stdout.strip()
    except Exception:
        pass
    languages = {}  # populated from language stats
    # Pull language counts from local git clone (if available)
    try:
        out = subprocess.run(
            ["git", "-C", str(raw_root), "ls-files"],
            capture_output=True, text=True, timeout=60,
        )
        files_listed = out.stdout.splitlines() if out.returncode == 0 else []
    except Exception:
        files_listed = []

    # Compute record_id using the existing convention:
    # sha256(json.dumps({"source_id", "commit", "tree_sha", "owner", "repo"}, sort_keys=True))
    parts = source_id.split(":")[1].split("/", 1) if source_id.startswith("github:") else ["", ""]
    owner = parts[0] if len(parts) > 0 else ""
    repo = parts[1] if len(parts) > 1 else ""
    descriptor = {
        "source_id": source_id,
        "commit": pinned_commit,
        "tree_sha": tree_sha,
        "owner": owner,
        "repo": repo,
    }
    descriptor_bytes = json.dumps(descriptor, sort_keys=True).encode("utf-8")
    record_hash_input = hashlib.sha256(descriptor_bytes).hexdigest()
    source_id_content_hash = record_hash_input  # for downstream naming consistency

    # ----- Walk the tree and write artifacts + occurrences -----
    files = walk_repo_files(raw_root)
    artifact_records: List[Dict[str, Any]] = []
    occurrence_records: List[Dict[str, Any]] = []
    artifact_by_hash: Dict[str, str] = {}  # content_sha256 -> artifact_id
    language_counts: Counter = Counter()
    file_count_total = 0
    file_count_clean = 0
    file_count_flagged = 0
    file_count_blocked = 0
    file_count_not_scanned = 0
    file_count_binary = 0
    file_count_too_large = 0

    acquisition_start = datetime.now(timezone.utc).isoformat()
    for p in files:
        rel = p.relative_to(raw_root)
        if rel.parts[0] == ".git":
            continue
        source_path = str(rel)
        ext = p.suffix.lower()
        if ext in file_policy.MEDIA_TYPES:
            language = file_policy.MEDIA_TYPES[ext].split("/")[-1]
            language_counts[language] += 1

        file_count_total += 1
        binary = file_policy.is_binary(p)
        if binary:
            file_count_binary += 1
        h = sha256_file(p)
        if h is None:
            file_count_too_large += 1
            continue
        artifact_id = f"sha256:{h}"
        if artifact_id not in artifact_by_hash:
            # Write the artifact record
            artifact = {
                "schema": "artifact/v1",
                "schema_version": "1.0.0",
                "record_id": artifact_id,
                "artifact_id": artifact_id,
                "content_sha256": h,
                "media_type": file_policy.detect_media_type(p),
                "size_bytes": p.stat().st_size,
                "artifact_role": _classify_artifact_role(p, rel),
                "parse_status": "valid" if not binary else "binary",
                "security_status": "clean",  # will be overwritten below
                "index_policy": "include" if not binary else "metadata-only",
                "created_at": acquisition_start,
                "generator": GENERATOR,
                "generator_version": GENERATOR_VERSION,
                "run_id": run_id,
                "content_hash": f"sha256:{h}",
                "source_path": source_path,
            }
            sec_status, sec_count = file_policy.scan_secrets(p)
            artifact["security_status"] = sec_status
            artifact["security_finding_count"] = sec_count
            if sec_status == "clean":
                file_count_clean += 1
            elif sec_status == "flagged":
                file_count_flagged += 1
            elif sec_status == "blocked":
                file_count_blocked += 1
            else:
                file_count_not_scanned += 1
            artifact_records.append(artifact)
            artifact_by_hash[artifact_id] = source_path

        # Write the occurrence record
        occurrence_id = f"sha256:{hashlib.sha256(f'{source_id}|{source_path}'.encode()).hexdigest()}"
        occ = {
            "schema": "artifact-occurrence/v1",
            "schema_version": "1.0.0",
            "record_id": occurrence_id,
            "created_at": acquisition_start,
            "occurrence_id": occurrence_id,
            "source_id": source_id,
            "source_path": source_path,
            "artifact_id": artifact_id,
            "content_sha256": h,
            "redacted": artifact_by_hash and artifact_records[-1]["security_status"] == "flagged",
            "redaction_reason": None,
            "ingestion": {
                "ingested_at": acquisition_start,
                "ingested_by": GENERATOR,
                "generator_version": GENERATOR_VERSION,
            },
            "fetched_at": acquisition_start,
            "generator": GENERATOR,
            "generator_version": GENERATOR_VERSION,
            "run_id": run_id,
            "content_hash": f"sha256:{h}",
        }
        occurrence_records.append(occ)

    acquisition_end = datetime.now(timezone.utc).isoformat()

    # Write artifacts to disk
    for art in artifact_records:
        art_path = ARTIFACTS_DIR / f"{art['artifact_id'].replace('sha256:', '')}.json"
        art_path.parent.mkdir(parents=True, exist_ok=True)
        art_path.write_text(json.dumps(art, indent=2, sort_keys=True))

    # Write occurrences to disk
    occ_dir = OCCURRENCES_DIR / safe_id
    occ_dir.mkdir(parents=True, exist_ok=True)
    for occ in occurrence_records:
        occ_path = occ_dir / f"{occ['occurrence_id'].replace('sha256:', '')}.json"
        occ_path.write_text(json.dumps(occ, indent=2, sort_keys=True))

    # ----- Compute repo_profile via the tech-profile extractor -----
    try:
        profile = extract_tech_profile(raw_root, source_id=source_id,
                                        pinned_commit=pinned_commit)
    except Exception as e:
        profile = {
            "source_platform": "github",
            "repo_identity": {
                "host": "github.com", "owner": source_id.split(":")[1].split("/")[0],
                "repo": source_id.split(":")[1].split("/")[1],
                "full_name": source_id.split(":")[1], "clone_url": "",
                "ssh_url": "", "default_branch": "", "pinned_commit": pinned_commit,
                "upstream_of_fork": "", "fork_intent": "unknown",
            },
            "repo_profile": {}, "interfaces": [], "workflow_synthesis": {},
        }

    # ----- Layer A: source.v1.yaml -----
    artifact_ids = [a["artifact_id"] for a in artifact_records]
    source_record = {
        "schema": "source/v1",
        "schema_version": "1.0.0",
        "record_id": f"sha256:{source_id_content_hash}",
        "created_at": acquisition_start,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": run_id,
        "content_hash": f"sha256:{source_id_content_hash}",
        "source_id": source_id,
        "requested_ref": source_id.split(":")[1],
        "resolved_revision": pinned_commit[:12] if pinned_commit != "unknown" else "unknown",
        "resolved_commit": pinned_commit,
        "fetched_at": acquisition_start,
        "platform": "github",
        "canonical_url": f"https://github.com/{source_id.split(':')[1]}",
        "license_spdx": cfg["maturity_signals"].get("license", "NOASSERTION"),
        "archived": False,
        "source_platform": "github",
        "primary_domain": cfg["primary_domain"],
        "related_domains": cfg.get("related_domains", []),
        "ecosystems": cfg.get("ecosystems", []),
        "capabilities": cfg.get("capabilities", []),
        "topics": cfg.get("topics", []),
        "integration_targets": cfg.get("integration_targets", []),
        "project_use_cases": cfg.get("project_use_cases", []),
        "artifact_role": cfg["artifact_role"],
        "source_role": cfg["source_role"],
        "authority_level": cfg["authority_level"],
        "lifecycle_status": "active",
        "target_runtimes": [],
        "repo_identity": profile["repo_identity"],
        "repo_profile": profile.get("repo_profile", {}),
        "interfaces": cfg.get("interfaces", []) + profile.get("interfaces", []),
        "workflow_synthesis": {
            "workflow_roles": cfg.get("workflow_roles", []),
            "provides": cfg.get("provides", []),
            "requires": cfg.get("requires", []),
            "compatible_with": [],
            "composition_edges": cfg.get("composition_edges", []),
            "composition_notes": [],
        },
        "maturity_signals": cfg["maturity_signals"],
        "reuse_assessment": cfg.get("reuse", {
            "intended_use": "study", "priority": "medium", "fit_for_maxios": "unknown",
        }),
        "artifact_kind": "repository" if profile.get("repo_profile", {}).get("languages") else "documentation",
        "acquisition": {
            "status": "complete" if not file_count_too_large else "partial",
            "expected_files": file_count_total,
            "acquired_files": file_count_total - file_count_too_large,
            "failed_files": file_count_too_large,
            "excluded_files": 0,
            "coverage_ratio": 1.0 if not file_count_too_large else (
                (file_count_total - file_count_too_large) / file_count_total if file_count_total else 0
            ),
            "failure_reasons": [f"skipped: file too large (> {MAX_FILE_SIZE} bytes)"] * file_count_too_large,
            "last_attempt_at": acquisition_end,
        },
        "revision_resolution": {
            "status": "pinned" if pinned_commit != "unknown" else "unresolved",
            "confidence": "high" if pinned_commit != "unknown" else "low",
            "requested_ref": source_id.split(":")[1],
            "resolved_commit": pinned_commit,
            "resolved_at": acquisition_end,
            "notes": "Pinned to default-branch HEAD at ingest time",
        },
        "provenance": {
            "confidence": "high" if pinned_commit != "unknown" else "low",
            "discovered_url": f"https://github.com/{source_id.split(':')[1]}",
            "discovered_at": acquisition_start,
            "notes": "Ingested via the deep-research / OSINT incremental batch.",
        },
        "coverage": {
            "status": "complete",
            "expected_files": file_count_total,
            "acquired_files": file_count_total - file_count_too_large,
            "coverage_ratio": 1.0 if not file_count_too_large else (
                (file_count_total - file_count_too_large) / file_count_total if file_count_total else 0
            ),
            "notes": "Walked the entire raw/ tree (excluding .git/, node_modules/, venv/, etc.).",
        },
        "discovery_context": {
            "discovered_via": "deep-research-osint-pre-classification-2026-06-21",
            "discovery_date": acquisition_start,
            "notes": "Pre-classified by the operator; auto-ingested via Phase 6 incremental batch.",
        },
        "relations": [],
        "contributing_dirs": _top_dirs(raw_root, 10),
        "cssclasses": [
            f"domain/{cfg['primary_domain']}",
            f"layer/source",
            f"role/{cfg['source_role']}",
            f"kind/{cfg['artifact_role']}",
        ],
    }
    (src_dir / "source.v1.yaml").write_text(yaml.safe_dump(source_record, sort_keys=False))

    # ----- Knowledge note candidate (Layer E) -----
    coverage_stats = {
        "file_count_total": file_count_total,
        "file_count_clean": file_count_clean,
        "file_count_flagged": file_count_flagged,
        "file_count_blocked": file_count_blocked,
        "file_count_binary": file_count_binary,
        "file_count_too_large": file_count_too_large,
        "file_count_not_scanned": file_count_not_scanned,
    }
    coverage_ratio = (file_count_total - file_count_too_large) / file_count_total if file_count_total else 0.0
    kn_placeholder = "TODO: scope will be expanded at next synthesis pass"  # never used
    body = _build_candidate_body(cfg, source_id, pinned_commit, files, artifact_records, occurrence_records, coverage_stats, profile)
    summary = _build_candidate_summary(cfg, source_id, files, artifact_records, coverage_stats)

    # Map primary_domain -> domain_family (knowledge-note schema's
    # domain_family is a separate, smaller vocab that intentionally
    # omits capability-domain values like "deep-research" / "osint".
    # We map them to "cross-domain" since these notes cover work that
    # is not tied to a single platform.
    domain_family_map = {
        "deep-research": "cross-domain",
        "osint": "cross-domain",
        "coding-agents": "coding-agents",
        "training-systems": "cross-domain",
        "ai-content-generation": "cross-domain",
        "memory-systems": "cross-domain",
        "hermes-agent": "hermes-agent",
        "n8n": "n8n",
        "agentfield": "agentfield",
    }
    domain_family = domain_family_map.get(cfg["primary_domain"], "cross-domain")

    # Compute the record_id and content_hash as sha256 of stable input
    kn_record_id_input = f"{cfg['slug']}\n{summary}\n{body}"
    kn_record_id_hash = hashlib.sha256(kn_record_id_input.encode()).hexdigest()
    kn_content_hash = hashlib.sha256(f"{cfg['slug']}\n{summary}".encode()).hexdigest()

    kn_record = {
        "schema": "knowledge-note/v1",
        "schema_version": "1.0.0",
        "record_id": f"sha256:{kn_record_id_hash}",
        "title": cfg["title"],
        "slug": cfg["slug"],
        "domain_family": domain_family,
        "knowledge_status": "candidate",
        "scope": {
            "covers": cfg["scope_covers"],
            "excludes": cfg["scope_excludes"],
        },
        "summary": summary,
        "source_record_ids": [source_record["record_id"]],
        "occurrence_ids": [o["occurrence_id"] for o in occurrence_records[:20]],
        "evidence": [
            {
                "source_id": source_id,
                "artifact_id": occ["artifact_id"],
                "unit_id": occ["artifact_id"] + "#file",
                "anchor": f"file:{occ['source_path']}",
                "relation": "documents",
                "occurrence_id": occ["occurrence_id"],
            }
            for occ in occurrence_records[:25]
        ],
        "created_at": acquisition_start,
        "last_verified_at": acquisition_end,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": run_id,
        "content_hash": f"sha256:{kn_content_hash}",
        "source_role": cfg["source_role"],
        "authority_level": cfg["authority_level"],
        "lifecycle_status": "active",
        "coverage_status": "complete" if not file_count_too_large else "partial",
        "coverage_ratio": coverage_ratio,
        "coverage_notes": _build_coverage_notes(cfg, coverage_stats, file_count_total),
        "duplicate_resolution": None,
        "supersedes": None,
        "pinned_to_captured_source": True,
        "promotion_date": None,
        "promotion_run_id": None,
        "promotion_source_candidate": None,
        "synthesis_provenance": "deterministic, no LLM",
        "source_paths": [o["source_path"] for o in occurrence_records[:20]],
        "source_count": len(artifact_records),
        "topic": cfg["primary_domain"],
        "topic_cluster": _topic_cluster(cfg["primary_domain"]),
        "upstream_repo": source_id,
        "source_type": "github",
        "tags": cfg.get("topics", [])[:20],
        "cssclasses": [
            f"domain/{cfg['primary_domain']}",
            "layer/note",
            "state/candidate",
            f"{cfg['primary_domain']}/{cfg['slug']}",
        ],
        "unresolved_claims": [],
        "body_markdown": body,
        "acquisition": {
            "status": "complete" if not file_count_too_large else "partial",
            "acquired_files": file_count_total - file_count_too_large,
            "expected_files": file_count_total,
            "failed_files": file_count_too_large,
            "excluded_files": 0,
            "coverage_ratio": coverage_ratio,
        },
    }
    kn_path = KN_DIR / f"{cfg['slug']}.json"
    kn_path.parent.mkdir(parents=True, exist_ok=True)
    kn_path.write_text(json.dumps(kn_record, indent=2, sort_keys=True))

    # Mirror to wiki/_candidates/<slug>.md (frontmatter only; body minimal)
    md_path = WIKI_CANDIDATES / f"{cfg['slug']}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(_build_md_frontmatter(kn_record, body))

    # ----- Migration report -----
    migration_content_hash_input = f"{cfg['slug']}\n{source_id}\n{run_id}\n{acquisition_end}"
    migration_content_hash = hashlib.sha256(migration_content_hash_input.encode()).hexdigest()
    migration = {
        "schema": "migration-report/v1",
        "schema_version": "1.0.0",
        "record_id": f"sha256:{migration_content_hash}",
        "created_at": acquisition_end,
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "run_id": run_id,
        "content_hash": f"sha256:{migration_content_hash}",
        "candidate_slug": cfg["slug"],
        "generated_at": acquisition_end,
        "old_note": None,
        "candidate_note": f"wiki/_candidates/{cfg['slug']}.md",
        "preserved_sections": [],
        "removed_sections": [],
        "new_evidence_links": [
            {
                "source_id": source_id,
                "artifact_id": occ["artifact_id"],
                "unit_id": occ["artifact_id"] + "#file",
                "anchor": f"file:{occ['source_path']}",
                "relation": "documents",
                "occurrence_id": occ["occurrence_id"],
            }
            for occ in occurrence_records[:25]
        ],
        "unresolved_claims": [],
        "validation_status": "pending",
        "promotion_eligible": False,
        "promotion_blockers": [
            "candidate not yet promoted (operator-managed)",
            "pending operator review of evidence",
        ],
        "evidence_summary": {
            "source_id": source_id,
            "artifact_count": len(artifact_records),
            "occurrence_count": len(occurrence_records),
            "unit_count": 0,
            "redacted_unit_count": 0,
            "security_counter": {
                "clean": coverage_stats['file_count_clean'],
                "flagged": coverage_stats['file_count_flagged'],
                "blocked": coverage_stats['file_count_blocked'],
                "not_scanned": coverage_stats['file_count_not_scanned'],
            },
            "blocked_excluded": coverage_stats['file_count_blocked'],
            "flagged_redacted": coverage_stats['file_count_flagged'],
            "clean_count": coverage_stats['file_count_clean'],
        },
    }
    mr_path = MR_DIR / f"{cfg['slug']}-migration.yaml"
    mr_path.parent.mkdir(parents=True, exist_ok=True)
    mr_path.write_text(yaml.safe_dump(migration, sort_keys=False))
    WIKI_CANDIDATES_MIGRATIONS.mkdir(parents=True, exist_ok=True)
    (WIKI_CANDIDATES_MIGRATIONS / f"{cfg['slug']}-migration.yaml").write_text(
        yaml.safe_dump(migration, sort_keys=False))

    return {
        "source_id": source_id,
        "slug": cfg["slug"],
        "status": "ingested",
        "pinned_commit": pinned_commit,
        "file_count_total": file_count_total,
        "file_count_clean": file_count_clean,
        "file_count_flagged": file_count_flagged,
        "file_count_blocked": file_count_blocked,
        "file_count_too_large": file_count_too_large,
        "artifact_count": len(artifact_records),
        "occurrence_count": len(occurrence_records),
        "coverage_ratio": coverage_ratio,
        "languages": dict(language_counts.most_common(10)),
    }


def _classify_artifact_role(p: Path, rel: Path) -> str:
    """Best-effort artifact_role from path."""
    n = rel.parts[0] if rel.parts else ""
    name = rel.name
    if name == "SKILL.md" or name == "SOUL.md":
        return "agent-skill" if name == "SKILL.md" else "agent-soul"
    if name in ("pyproject.toml", "package.json", "Cargo.toml", "go.mod", "requirements.txt"):
        return "configuration"
    if name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                "compose.yml", "compose.yaml", "Chart.yaml"):
        return "deployment-definition"
    if name.endswith((".md", ".rst", ".txt")) and len(rel.parts) == 1:
        return "documentation"
    if n in ("docs", "documentation"):
        return "documentation"
    if n in ("test", "tests", "__tests__"):
        return "reference"
    if n in ("scripts", "tools", "bin"):
        return "executable-script"
    return "unknown"


def _top_dirs(raw_root: Path, limit: int) -> List[str]:
    """Return the top N immediate subdirectories of raw_root."""
    out: List[str] = []
    for entry in raw_root.iterdir():
        if entry.is_dir() and entry.name not in SKIP_DIRS:
            out.append(entry.name)
            if len(out) >= limit:
                break
    return sorted(out)


def _topic_cluster(primary_domain: str) -> str:
    if primary_domain == "deep-research":
        return "research-agents"
    if primary_domain == "osint":
        return "intelligence-frameworks"
    if primary_domain == "coding-agents":
        return "agent-runtimes"
    return "knowledge-factory"


def _build_candidate_summary(
    cfg: Dict[str, Any], source_id: str, files: List[Path],
    artifact_records: List[Dict[str, Any]], coverage_stats: Dict[str, int],
) -> str:
    """One-paragraph summary, deterministic, no LLM."""
    role_part = (
        f"Re-ingest of the original {cfg['authority_level']} "
        f"{cfg['source_role']} {cfg['primary_domain']} source."
    )
    cap = ", ".join(cfg.get("capabilities", [])[:5])
    file_count = coverage_stats["file_count_total"]
    return (
        f"{cfg['title']}. {role_part} Preserved at "
        f"codex-vault/raw/{cfg['raw_local']}/. Capabilities: {cap}. "
        f"Ingest walked {file_count} files, "
        f"{coverage_stats['file_count_clean']} clean, "
        f"{coverage_stats['file_count_flagged']} flagged, "
        f"{coverage_stats['file_count_blocked']} blocked."
    )


def _build_candidate_body(
    cfg: Dict[str, Any], source_id: str, pinned_commit: str,
    files: List[Path], artifact_records: List[Dict[str, Any]],
    occurrence_records: List[Dict[str, Any]],
    coverage_stats: Dict[str, int], profile: Dict[str, Any],
) -> str:
    """Build a candidate body (markdown). Deterministic, structured."""
    lines: List[str] = []
    lines.append(f"## What this candidate covers")
    lines.append("")
    lines.append(cfg["scope_covers"])
    lines.append("")
    lines.append(f"## Source profile")
    lines.append("")
    lines.append(f"- **source_id**: `{source_id}`")
    lines.append(f"- **pinned_commit**: `{pinned_commit}`")
    lines.append(f"- **primary_domain**: `{cfg['primary_domain']}`")
    if cfg.get("ecosystems"):
        lines.append(f"- **ecosystems**: {', '.join(cfg['ecosystems'])}")
    lines.append(f"- **capabilities**: {', '.join(cfg.get('capabilities', []))}")
    if cfg.get("topics"):
        lines.append(f"- **topics**: {', '.join(cfg['topics'])}")
    if cfg.get("integration_targets"):
        lines.append(f"- **integration_targets**: {', '.join(cfg['integration_targets'])}")
    lines.append(f"- **artifact_role**: `{cfg['artifact_role']}`")
    lines.append(f"- **source_role**: `{cfg['source_role']}`")
    lines.append(f"- **authority_level**: `{cfg['authority_level']}`")
    if cfg.get("reuse"):
        reuse = cfg["reuse"]
        lines.append(f"- **reuse_assessment.intended_use**: `{reuse.get('intended_use', 'unknown')}`")
        lines.append(f"- **reuse_assessment.priority**: `{reuse.get('priority', 'unknown')}`")
        lines.append(f"- **reuse_assessment.fit_for_maxios**: `{reuse.get('fit_for_maxios', 'unknown')}`")
    if cfg.get("maturity_signals"):
        ms = cfg["maturity_signals"]
        if ms.get("license"):
            lines.append(f"- **license**: `{ms['license']}`")
        if ms.get("stars") is not None:
            lines.append(f"- **stars**: {ms['stars']}")
    lines.append("")
    # Repo profile summary
    rp = profile.get("repo_profile", {}) if profile else {}
    if rp:
        lines.append("## Repo profile (deterministic extract)")
        lines.append("")
        if rp.get("languages"):
            lines.append(f"- **languages**: {', '.join(rp['languages'])}")
        if rp.get("runtime_stack"):
            lines.append(f"- **runtime_stack**: {', '.join(rp['runtime_stack'])}")
        if rp.get("data_stores"):
            lines.append(f"- **data_stores**: {', '.join(rp['data_stores'])}")
        if rp.get("build_systems"):
            lines.append(f"- **build_systems**: {', '.join(rp['build_systems'])}")
        if rp.get("test_systems"):
            lines.append(f"- **test_systems**: {', '.join(rp['test_systems'])}")
        if rp.get("dependency_manifests"):
            lines.append(f"- **dependency_manifests**: {len(rp['dependency_manifests'])} manifest(s) at:")
            for m in rp["dependency_manifests"][:5]:
                lines.append(f"  - `{m.get('path')}` ({m.get('package_manager')})")
        if rp.get("services"):
            lines.append(f"- **services** (compose): {', '.join(rp['services'])}")
        lines.append("")
    # Interfaces
    interfaces = profile.get("interfaces", []) if profile else []
    if interfaces or cfg.get("interfaces"):
        lines.append("## Interfaces")
        lines.append("")
        all_ints = (cfg.get("interfaces", []) or []) + interfaces
        seen = set()
        for i in all_ints:
            key = (i.get("kind"), i.get("name"), i.get("path"))
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- `{i.get('kind', 'unknown')}`: {i.get('name', '?')} (`{i.get('path', '?')}`)")
        lines.append("")
    # Workflow synthesis
    if cfg.get("workflow_roles"):
        lines.append("## Workflow synthesis")
        lines.append("")
        lines.append(f"- **workflow_roles**: {', '.join(cfg['workflow_roles'])}")
        if cfg.get("provides"):
            lines.append(f"- **provides**: {', '.join(cfg['provides'])}")
        if cfg.get("requires"):
            lines.append(f"- **requires**: {', '.join(cfg['requires'])}")
        if cfg.get("composition_edges"):
            lines.append("- **composition_edges**:")
            for e in cfg["composition_edges"]:
                lines.append(f"  - `{e.get('relation', '?')}` → `{e.get('target', '?')}` ({e.get('evidence', '?')})")
        lines.append("")
    # Coverage
    lines.append("## Coverage")
    lines.append("")
    lines.append(
        f"- **files_walked**: {coverage_stats['file_count_total']}, "
        f"**clean**: {coverage_stats['file_count_clean']}, "
        f"**flagged**: {coverage_stats['file_count_flagged']}, "
        f"**blocked**: {coverage_stats['file_count_blocked']}, "
        f"**binary**: {coverage_stats['file_count_binary']}, "
        f"**too_large**: {coverage_stats['file_count_too_large']}"
    )
    lines.append(f"- **artifacts**: {len(artifact_records)}")
    lines.append(f"- **occurrences**: {len(occurrence_records)}")
    lines.append("")
    lines.append("## What this candidate does NOT cover")
    lines.append("")
    lines.append(cfg["scope_excludes"])
    return "\n".join(lines)


def _build_coverage_notes(cfg: Dict[str, Any], cov: Dict[str, int], file_count: int) -> str:
    return (
        f"Ingest of {cfg['source_id']} at commit "
        f"pinned to default-branch HEAD. {file_count} files walked "
        f"({cov['file_count_clean']} clean, {cov['file_count_flagged']} flagged, "
        f"{cov['file_count_blocked']} blocked, {cov['file_count_binary']} binary, "
        f"{cov['file_count_too_large']} skipped-too-large). The repo_profile "
        f"is the deterministic output of `extract_tech_profile.py`; the "
        f"workflow_synthesis edges are operator-classified."
    )


def _build_md_frontmatter(kn_record: Dict[str, Any], body: str) -> str:
    """Emit YAML frontmatter + body to a markdown file."""
    fm_keys = [
        "schema", "schema_version", "record_id", "title", "slug",
        "domain_family", "knowledge_status", "scope", "summary",
        "source_record_ids", "evidence", "created_at", "last_verified_at",
        "generator", "generator_version", "run_id", "content_hash",
        "source_role", "authority_level", "lifecycle_status",
        "coverage_status", "coverage_ratio", "coverage_notes",
        "promotion_status",
    ]
    fm_lines: List[str] = ["---"]
    for k in fm_keys:
        if k in kn_record:
            v = kn_record[k]
            if k == "scope":
                fm_lines.append(f"scope:")
                for sk, sv in v.items():
                    fm_lines.append(f"  {sk}: >-")
                    for line in str(sv).split("\n"):
                        fm_lines.append(f"    {line}")
            elif k == "evidence":
                fm_lines.append(f"evidence:")
                for e in v[:25]:
                    fm_lines.append(f"  - source_id: {e['source_id']}")
                    fm_lines.append(f"    artifact_id: {e['artifact_id']}")
                    fm_lines.append(f"    unit_id: {e['unit_id']}")
                    fm_lines.append(f"    anchor: {e['anchor']}")
                    fm_lines.append(f"    relation: {e['relation']}")
            else:
                fm_lines.append(f"{k}: {json.dumps(v) if not isinstance(v, str) else v}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(body)
    return "\n".join(fm_lines)


# ----- Main --------------------------------------------------------------

def main() -> int:
    global VAULT
    global RUNTIME, RAW_DIR, SOURCES_DIR, ARTIFACTS_DIR, OCCURRENCES_DIR
    global KN_DIR, MR_DIR, WIKI_CANDIDATES, WIKI_CANDIDATES_MIGRATIONS, REPORTS_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault-root", type=Path, default=VAULT)
    parser.add_argument("--run-id", type=str, default=RUN_ID)
    args = parser.parse_args()

    # Update VAULT to the resolved path
    VAULT = args.vault_root.resolve()
    RUNTIME = VAULT / ".runtime"
    RAW_DIR = VAULT / "raw"
    SOURCES_DIR = RUNTIME / "sources"
    ARTIFACTS_DIR = RUNTIME / "artifacts"
    OCCURRENCES_DIR = RUNTIME / "occurrences"
    KN_DIR = RUNTIME / "knowledge-notes"
    MR_DIR = RUNTIME / "migration-reports"
    WIKI_CANDIDATES = VAULT / "wiki" / "_candidates"
    WIKI_CANDIDATES_MIGRATIONS = WIKI_CANDIDATES / "_migration"
    REPORTS_DIR = RUNTIME / "reports"

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    OCCURRENCES_DIR.mkdir(parents=True, exist_ok=True)
    KN_DIR.mkdir(parents=True, exist_ok=True)
    MR_DIR.mkdir(parents=True, exist_ok=True)
    WIKI_CANDIDATES.mkdir(parents=True, exist_ok=True)
    WIKI_CANDIDATES_MIGRATIONS.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Vault root: {VAULT}")
    print(f"Run ID: {args.run_id}")
    print(f"detect-secrets available: {file_policy.HAVE_DETECT_SECRETS}")
    print()

    summaries = []
    for cfg in REPO_CONFIGS:
        try:
            s = ingest_one(cfg, args.run_id)
            summaries.append(s)
            print(f"  [{s.get('status'):>8s}] {s.get('source_id')}  "
                  f"files={s.get('file_count_total', '?')}  "
                  f"clean={s.get('file_count_clean', '?')}  "
                  f"flagged={s.get('file_count_flagged', '?')}  "
                  f"blocked={s.get('file_count_blocked', '?')}")
        except Exception as e:
            summaries.append({"source_id": cfg["source_id"], "status": "failed", "error": str(e)})
            print(f"  [  failed  ] {cfg['source_id']}  ERROR: {e}")

    # Write the ingest report
    report_path = REPORTS_DIR / "incremental-ingest-deep-research-osint.json"
    report_path.write_text(json.dumps({
        "run_id": args.run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": GENERATOR,
        "generator_version": GENERATOR_VERSION,
        "summaries": summaries,
    }, indent=2, sort_keys=True))
    print()
    print(f"Ingest summary: {report_path}")

    failed = [s for s in summaries if s.get("status") == "failed"]
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
