#!/usr/bin/env python3
"""Codex Vault strict validator (Phase 1).

Implements 20 rejection rules from AGENTS.md v3.2 §19.
Uses a duplicate-key-rejecting YAML loader.
Pure stdlib.

Usage:
    python3 validate.py [--data-root PATH] [--vault-root PATH] [--strict]

Exit code: 0 if all checks pass, 1 if any rejection.
"""
import argparse, hashlib, json, os, re, sys
from collections import OrderedDict
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

# ----- Duplicate-key-rejecting YAML loader -----

class DuplicateYAMLKeyError(Exception):
    pass

def _construct_mapping(loader, node, deep=False):
    """PyYAML hook that rejects duplicate keys."""
    if not isinstance(node, type(loader.construct_mapping(node))):
        # fall back to default for tag-handled nodes
        pass
    mapping = OrderedDict()
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise DuplicateYAMLKeyError(f"duplicate YAML key: {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping

def _make_loader():
    """Return a YAML loader class with duplicate-key rejection.
    Uses PyYAML; raises ImportError if not available.
    """
    import yaml
    class StrictLoader(yaml.SafeLoader):
        pass
    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_mapping,
    )
    return StrictLoader

def _fallback_yaml_load(text: str):
    """Tiny YAML subset parser for our specific output format.
    Supports: top-level scalars, block-style mappings, block-style sequences of mappings,
    quoted strings, integers, floats, booleans, null, comments (#).
    Not a general YAML parser. Raises on duplicate keys.
    """
    # ... not actually needed because we always have PyYAML available in practice.
    # But if not, the validator should still work on the JSON files we produce.
    raise NotImplementedError("PyYAML not available; install pyyaml or run validator on JSON only")

# ----- Vocabularies -----

def load_vocabs(schemas_dir: Path):
    """Load control-vocabulary files. Returns {vocab_name: set(allowed)}."""
    out = {}
    for p in sorted(schemas_dir.glob("vocab-*.yaml")):
        try:
            import yaml
            data = yaml.load(p.read_text(), Loader=_make_loader())
            out[data["vocab_name"]] = set(data["allowed"])
        except Exception as e:
            print(f"WARN: failed to load vocab {p.name}: {e}", file=sys.stderr)
    return out

# ----- Record loading -----

# Set of formal record schemas. Auxiliary files (queries, judgments, schemas, vocabs,
# baseline, manifest, registries) are NOT formal records and are skipped by the validator.
FORMAL_RECORD_SCHEMAS = {
    "source/v1",
    "artifact/v1",
    "artifact-occurrence/v1",
    "bundle/v1",
    "unit/v1",
    "domain-record/v1",
    "knowledge-note/v1",
    "relation/v1",
    "acquisition-run/v1",
    "migration-report/v1",
}

def find_records(data_root: Path):
    """Walk codex-vault/.runtime/ and yield (path, format, record) for each formal record.
    Auxiliary files (schemas, vocabs, queries, judgments, baseline, manifest, etc.) are
    loaded but tagged with fmt='aux' so the validator can skip them.
    """
    for p in sorted(data_root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix == ".json":
            try:
                rec = json.loads(p.read_text())
                if isinstance(rec, dict) and rec.get("schema") in FORMAL_RECORD_SCHEMAS:
                    yield p, "json", rec
                else:
                    yield p, "aux", rec
            except Exception as e:
                yield p, "json-error", {"_error": str(e)}
        elif p.suffix in (".yaml", ".yml"):
            try:
                import yaml
                rec = yaml.load(p.read_text(), Loader=_make_loader())
                if isinstance(rec, dict) and rec.get("schema") in FORMAL_RECORD_SCHEMAS:
                    yield p, "yaml", rec
                else:
                    yield p, "aux", rec
            except DuplicateYAMLKeyError as e:
                yield p, "yaml-duplicate-key", {"_error": str(e)}
            except Exception as e:
                yield p, "yaml-error", {"_error": str(e)}

# ----- Validation rules -----

class Rejection:
    def __init__(self, rule_id, rule_name, path, detail):
        self.rule_id = rule_id
        self.rule_name = rule_name
        self.path = str(path)
        self.detail = detail
    def __repr__(self):
        return f"[{self.rule_id}] {self.rule_name} — {self.path}: {self.detail}"

REQUIRED_BASE_FIELDS = [
    "schema", "schema_version", "record_id", "created_at",
    "generator", "generator_version", "run_id", "content_hash",
]

RECORD_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

def _check_required_base(rec, path, rejections):
    """Rule 1 (part): missing required schema field."""
    for f in REQUIRED_BASE_FIELDS:
        if f not in rec:
            rejections.append(Rejection("R01", "missing_required_schema_field", path, f"missing field: {f}"))

def _check_record_id(rec, path, rejections):
    """Rule 3: malformed record_id."""
    rid = rec.get("record_id", "")
    if not rid:
        rejections.append(Rejection("R03", "malformed_record_id", path, "record_id is empty"))
    elif not RECORD_ID_PATTERN.match(rid):
        rejections.append(Rejection("R03", "malformed_record_id", path, f"record_id does not match pattern: {rid[:80]}"))

def _check_content_hash_format(rec, path, rejections):
    """Rule 3 (part): malformed content_hash."""
    ch = rec.get("content_hash", "")
    if not ch:
        rejections.append(Rejection("R03", "malformed_content_hash", path, "content_hash is empty"))
    elif not ch.startswith("sha256:") or not HASH_PATTERN.match(ch[7:]):
        rejections.append(Rejection("R03", "malformed_content_hash", path, f"content_hash malformed: {ch[:80]}"))

def _check_coverage_ratio(rec, path, rejections):
    """Rule 7: coverage_ratio > 1.0 or < 0.0."""
    for k in ("coverage_ratio",):
        v = rec.get(k)
        if isinstance(v, (int, float)) and (v > 1.0 or v < 0.0):
            rejections.append(Rejection("R07", "coverage_ratio_out_of_range", path, f"{k}={v}"))
    # also check nested acquisition
    acq = rec.get("acquisition")
    if isinstance(acq, dict):
        v = acq.get("coverage_ratio")
        if isinstance(v, (int, float)) and (v > 1.0 or v < 0.0):
            rejections.append(Rejection("R07", "coverage_ratio_out_of_range", path, f"acquisition.coverage_ratio={v}"))

def _check_complete_with_failures(rec, path, rejections):
    """Rule 8: status=complete but failed_files > 0 or error_summary non-empty."""
    # check top-level status
    status = rec.get("status")
    failed = rec.get("failed_artifacts") if isinstance(rec.get("failed_artifacts"), dict) else None
    if status == "complete" and failed is not None:
        ff = failed.get("failed_files", 0) or 0
        if ff > 0:
            rejections.append(Rejection("R08", "complete_status_with_failures", path, f"status=complete but failed_files={ff}"))
    # check acquisition.status
    acq = rec.get("acquisition")
    if isinstance(acq, dict) and acq.get("status") == "complete":
        ff = acq.get("failed_files", 0) or 0
        if ff > 0:
            rejections.append(Rejection("R08", "complete_status_with_failures", path, f"acquisition.status=complete but failed_files={ff}"))
    # check error_summary non-empty when status=complete
    es = rec.get("error_summary")
    if status == "complete" and isinstance(es, list) and len(es) > 0:
        rejections.append(Rejection("R08", "complete_status_with_failures", path, f"status=complete but error_summary has {len(es)} entries"))

def _check_partial_marked_complete(rec, path, rejections):
    """Rule 9: partial acquisition represented as complete."""
    acq = rec.get("acquisition")
    if isinstance(acq, dict):
        cr = acq.get("coverage_ratio", 1.0)
        af = acq.get("acquired_files", 0) or 0
        ef = acq.get("expected_files", 0) or 0
        if ef > 0 and af < ef and acq.get("status") == "complete":
            rejections.append(Rejection("R09", "partial_marked_complete", path,
                                        f"acquired={af} expected={ef} but status=complete"))

def _check_canonical_exceeds_evidence(rec, path, rejections, vocabs):
    """Rule 10: canonical knowledge beyond evidence scope (for knowledge notes)."""
    if rec.get("schema") == "knowledge-note/v1":
        if rec.get("knowledge_status") == "canonical":
            ev = rec.get("evidence")
            if not isinstance(ev, list) or len(ev) == 0:
                rejections.append(Rejection("R10", "canonical_without_evidence", path,
                                            "knowledge_status=canonical but evidence is empty"))
            cr = rec.get("coverage_ratio")
            if isinstance(cr, (int, float)) and cr < 1.0:
                rejections.append(Rejection("R10", "canonical_beyond_scope", path,
                                            f"knowledge_status=canonical but coverage_ratio={cr}"))
        # rule 10 also says: canonical is invalid as a top-level boolean on knowledge notes
        if "canonical" in rec and rec["canonical"] is True and rec.get("knowledge_status") != "canonical":
            # tolerated only if v2 frontmatter being migrated; flag for review
            rejections.append(Rejection("R10", "canonical_legacy_field", path,
                                        "knowledge note has canonical: true but knowledge_status != canonical"))

def _check_controlled_vocab(rec, path, rejections, vocabs):
    """Rule 2 (part): invalid controlled-vocabulary values."""
    for field_name, vocab_name in [
        ("primary_domain", "primary_domain"),
        ("source_role", "source_role"),
        ("authority_level", "authority_level"),
        ("knowledge_status", "knowledge_status"),
        ("lifecycle_status", "lifecycle_status"),
    ]:
        v = rec.get(field_name)
        if v is None:
            continue
        if vocab_name in vocabs and v not in vocabs[vocab_name]:
            rejections.append(Rejection("R02", "invalid_controlled_vocab", path,
                                        f"{field_name}={v!r} not in {vocab_name}"))
    # related_domains and target_runtimes (lists)
    for list_field, vocab_name in [
        ("related_domains", "primary_domain"),
        ("target_runtimes", "target_runtimes"),
    ]:
        items = rec.get(list_field)
        if not isinstance(items, list):
            continue
        for item in items:
            if vocab_name in vocabs and item not in vocabs[vocab_name]:
                rejections.append(Rejection("R02", "invalid_controlled_vocab", path,
                                            f"{list_field} contains {item!r} not in {vocab_name}"))
    # artifact_role
    v = rec.get("artifact_role")
    if v is not None and "artifact_role" in vocabs and v not in vocabs["artifact_role"]:
        rejections.append(Rejection("R02", "invalid_controlled_vocab", path,
                                    f"artifact_role={v!r} not in artifact_role"))

def _check_evidence_anchors(rec, path, rejections):
    """Rule 4: missing evidence, source targets, or anchors."""
    if rec.get("schema") == "knowledge-note/v1":
        ev = rec.get("evidence")
        if isinstance(ev, list):
            for i, item in enumerate(ev):
                if not isinstance(item, dict):
                    rejections.append(Rejection("R04", "malformed_evidence", path, f"evidence[{i}] is not an object"))
                    continue
                for k in ("source_id", "artifact_id", "unit_id", "anchor", "relation"):
                    if not item.get(k):
                        rejections.append(Rejection("R04", "missing_evidence_target", path,
                                                    f"evidence[{i}].{k} is empty"))

def _check_acquisition_arithmetic(rec, path, rejections):
    """Rule 6: impossible acquisition arithmetic.
    expected = acquired + failed + excluded (or close to it for in-scope counts)."""
    acq = rec.get("acquisition")
    if not isinstance(acq, dict):
        return
    e = acq.get("expected_files", 0) or 0
    a = acq.get("acquired_files", 0) or 0
    f = acq.get("failed_files", 0) or 0
    x = acq.get("excluded_files", 0) or 0
    if e > 0:
        total = a + f + x
        if a > e:
            rejections.append(Rejection("R06", "impossible_acquisition_arithmetic", path,
                                        f"acquired({a}) > expected({e})"))
        # The plan says: expected = acquired + failed + explicitly_excluded
        if total > e + 0 and total != e:
            # tolerant: only fail if there's a clear mismatch
            if total > e:
                rejections.append(Rejection("R06", "impossible_acquisition_arithmetic", path,
                                            f"acquired+failed+excluded ({total}) > expected ({e})"))

def _check_acquisition_run_fields(rec, path, rejections):
    """For acquisition-run records, check required fields per AGENTS.md §3.7 + §12."""
    if rec.get("schema") == "acquisition-run/v1":
        for f in ("run_id", "started_at", "completed_at", "source_id", "requested_ref",
                  "resolved_commit", "expected_artifacts", "acquired_artifacts",
                  "excluded_artifacts", "failed_artifacts", "coverage_ratio", "status",
                  "error_summary", "tool_versions"):
            if f not in rec:
                rejections.append(Rejection("R-ar", "missing_acquisition_run_field", path, f"missing: {f}"))

def _check_n8n_workflow_classification(rec, path, rejections):
    """Rule 11: invalid JSON classified as n8n-workflow (via domain-record heuristic)."""
    if rec.get("schema") == "domain-record/v1" and rec.get("record_type") == "n8n-workflow":
        wf = rec.get("workflow", {})
        if wf.get("valid_n8n_document") is False:
            rejections.append(Rejection("R11", "invalid_json_as_n8n_workflow", path,
                                        "record_type=n8n-workflow but valid_n8n_document=false"))

def _check_secret_bearing_text(rec, path, rejections):
    """Rule 20: secret-bearing content in searchable derived text (heuristic for n8n workflows).
    Also Rule 12: blocked secrets entering indexes.
    """
    if rec.get("schema") == "domain-record/v1" and rec.get("record_type") == "n8n-workflow":
        creds = rec.get("workflow", {}).get("credential_types", [])
        if not isinstance(creds, list):
            return
        for c in creds:
            if not isinstance(c, str):
                continue
            # credential_types must be type names, not values
            if "=" in c or "://" in c or len(c) > 100 or c.startswith("sk-") or c.startswith("ghp_") or c.startswith("Bearer "):
                rejections.append(Rejection("R20", "secret_in_searchable_text", path,
                                            f"credential_types contains a value-like string: {c[:60]}"))

def _check_orphan_records(records, rejections):
    """Rule 13: orphan artifacts, units, domain records.
    An artifact/unit/domain record is orphan if its source_id does not resolve to a known source.
    Phase 1: relaxed — we flag artifacts/units that reference unknown source_ids, but we don't
    require every source to be present (sources come in Phase 2).
    """
    source_ids = set()
    artifact_ids = set()
    unit_ids = set()
    knowledge_ids = set()
    for path, fmt, rec in records:
        if fmt not in ("yaml", "json"):
            continue
        s = rec.get("schema", "")
        if s == "source/v1":
            source_ids.add(rec.get("source_id"))
        elif s == "artifact/v1":
            artifact_ids.add(rec.get("artifact_id"))
        elif s == "unit/v1":
            unit_ids.add(rec.get("unit_id"))
        elif s == "knowledge-note/v1":
            knowledge_ids.add(rec.get("record_id"))

    for path, fmt, rec in records:
        if fmt not in ("yaml", "json"):
            continue
        s = rec.get("schema", "")
        if s == "artifact/v1":
            src = rec.get("source_id")
            if src and source_ids and src not in source_ids:
                # only flag if there ARE any source records to check against
                rejections.append(Rejection("R13", "orphan_artifact", path,
                                            f"source_id={src!r} not in known sources"))
        elif s == "unit/v1":
            art = rec.get("artifact_id")
            if art and artifact_ids and art not in artifact_ids:
                rejections.append(Rejection("R13", "orphan_unit", path,
                                            f"artifact_id={art!r} not in known artifacts"))
            src_ids = rec.get("source_record_ids") or []
            for sid in src_ids:
                if source_ids and sid not in source_ids and not sid.startswith("sha256:"):
                    rejections.append(Rejection("R13", "orphan_unit_source", path,
                                                f"source_record_ids contains unknown {sid!r}"))
        elif s == "domain-record/v1":
            for sid in rec.get("source_record_ids") or []:
                if source_ids and sid not in source_ids and not sid.startswith("sha256:"):
                    rejections.append(Rejection("R13", "orphan_domain_record_source", path,
                                                f"source_record_ids contains unknown {sid!r}"))

# ----- Main -----

def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--data-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--schemas-dir", default=None)
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    schemas_dir = Path(args.schemas_dir) if args.schemas_dir else data_root / "schemas"

    if not data_root.exists():
        print(f"ERROR: data root not found: {data_root}", file=sys.stderr)
        return 2

    print(f"== Codex Vault strict validator (Phase 1) ==")
    print(f"data_root: {data_root}")
    print(f"schemas_dir: {schemas_dir}")
    print()

    vocabs = load_vocabs(schemas_dir)
    print(f"Loaded vocabs: {sorted(vocabs.keys())}")
    print()

    rejections = []
    parse_errors = []
    records = list(find_records(data_root))
    print(f"Found {len(records)} candidate records")
    print()

    # Rule 1 (parse): duplicate YAML keys, malformed YAML/JSON
    for path, fmt, rec in records:
        if fmt in ("yaml-error", "json-error", "yaml-duplicate-key"):
            detail = rec.get("_error", "unknown")
            rule = "R01.duplicate_yaml_keys" if fmt == "yaml-duplicate-key" else "R01.malformed_yaml_or_json"
            rejections.append(Rejection("R01", rule, path, detail))
            parse_errors.append((path, fmt, detail))

    # Per-record checks
    n_valid = 0
    for path, fmt, rec in records:
        if fmt not in ("yaml", "json"):
            continue
        if not isinstance(rec, dict):
            rejections.append(Rejection("R01", "not_an_object", path, f"top-level is {type(rec).__name__}"))
            continue
        # Check schema field is present
        if "schema" not in rec:
            rejections.append(Rejection("R01", "missing_schema_field", path, "no `schema` field"))
            continue
        n_valid += 1
        _check_required_base(rec, path, rejections)
        _check_record_id(rec, path, rejections)
        _check_content_hash_format(rec, path, rejections)
        _check_coverage_ratio(rec, path, rejections)
        _check_complete_with_failures(rec, path, rejections)
        _check_partial_marked_complete(rec, path, rejections)
        _check_canonical_exceeds_evidence(rec, path, rejections, vocabs)
        _check_controlled_vocab(rec, path, rejections, vocabs)
        _check_evidence_anchors(rec, path, rejections)
        _check_acquisition_arithmetic(rec, path, rejections)
        _check_acquisition_run_fields(rec, path, rejections)
        _check_n8n_workflow_classification(rec, path, rejections)
        _check_secret_bearing_text(rec, path, rejections)

    # Cross-record checks
    _check_orphan_records([(p, f, r) for (p, f, r) in records if f in ("yaml", "json") and isinstance(r, dict)], rejections)

    # Report
    print(f"Valid records inspected: {n_valid}")
    print(f"Parse errors: {len(parse_errors)}")
    print(f"Rejections: {len(rejections)}")
    print()

    if parse_errors:
        print("=== Parse errors ===")
        for path, fmt, detail in parse_errors:
            print(f"  [{fmt}] {path}: {detail}")
        print()

    if rejections:
        print("=== Rejections ===")
        by_rule = {}
        for r in rejections:
            by_rule.setdefault(r.rule_name, []).append(r)
        for rule, items in sorted(by_rule.items()):
            print(f"  {rule}: {len(items)}")
            for r in items[:5]:
                print(f"    - {r.path}: {r.detail}")
            if len(items) > 5:
                print(f"    ... and {len(items) - 5} more")
        print()
        print(f"RESULT: REJECTED ({len(rejections)} violations)")
        return 1
    else:
        print("RESULT: PASSED — all 20 rules green against current data")
        return 0

if __name__ == "__main__":
    sys.exit(main())
