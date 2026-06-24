# Phase 05G1: Graph CLI Implementation

## Summary
Added `codex-vault v2 graph build` CLI command for rebuilding the runtime graph deterministically from existing vault layers.

## Changes Made

### Files Modified
- `src/codex_vault_pipeline/cli.py`: Added `graph` action to v2 subcommand and `_v2_graph_build()` handler function
- `tests/test_v2.py`: Added `TestGraphCLI` class with 5 tests

### Implementation Details

The CLI command follows the existing v2 pattern:
```
codex-vault v2 graph build [--vault-root PATH] [--dry-run]
```

**Behavior:**
- Reads curated/structured vault layers (20-domains, 30-concepts, 40-use-cases, 50-guides, 60-sources, 70-reports)
- Does NOT scan raw/ directory (preserves source-layer immutability)
- Includes runtime summaries when present
- Writes outputs to `.runtime/graph/`:
  - `nodes.jsonl` - GraphNode records
  - `edges.jsonl` - GraphEdge records  
  - `graph-summary.json` - Statistics by type
  - `validation-report.json` - Schema validation results

## Test Results

All 20 graph-related tests passed:
- 15 tests in `test_graph_builder.py`
- 5 tests in `test_v2.py::TestGraphCLI`

## Smoke Test Results

Running on real vault (`codex-vault`):
```
Total nodes: 180
Total edges: 265

Nodes by type:
  ecosystem: 9
  concept: 13
  use_case: 8
  guide: 7
  report: 8
  source: 122
  skill: 7
  workflow: 6

Edges by type:
  referenced_by_guide: 218
  belongs_to_ecosystem: 47
```

## Validation

- Syntax check: PASSED (compileall)
- Unit tests: PASSED (20/20)
- Real-vault smoke test: PASSED

## Constraints Verified

- Did not ingest new repos ✓
- Did not mutate vault `raw/` ✓
- Did not modify tracked vault Markdown ✓
- Did not rebuild embeddings, LanceDB, SQLite/FTS, context packs, or Repomix ✓
- Did not run clean reingest ✓
- Did not delete or quarantine legacy scripts ✓
- Did not modify `.obsidian/graph.json` ✓
- Did not commit generated `.runtime` graph outputs ✓

## Next Steps

- Phase 05G2: Consider adding `graph info` subcommand for reading existing graph outputs
- Phase 05G3: Consider adding `graph validate` subcommand for schema validation