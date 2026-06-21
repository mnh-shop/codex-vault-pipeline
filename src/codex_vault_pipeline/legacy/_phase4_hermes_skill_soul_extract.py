#!/usr/bin/env python3
"""Phase 4 — Domain: hermes-skill + hermes-soul extraction.

Walks all bundle records with artifact_role in {agent-skill, agent-soul}.
For each bundle entrypoint (SKILL.md or SOUL.md):

  - Parse YAML frontmatter (if present)
  - Extract structured fields per AGENTS.md §10

For agent-skill, emit:
  - 1 domain-record/v1 with record_type=hermes-skill
  - 1 unit/v1 with unit_type=hermes-skill

For agent-soul, emit:
  - 1 domain-record/v1 with record_type=hermes-soul
  - 1 unit/v1 with unit_type=hermes-soul

Honors security:
  - blocked: skip entirely
  - flagged: emit with redacted semantic_text (empty)

Dedup: domain records keyed by content_sha256 (per AGENTS.md §4).

Usage:
    python3 _phase4_hermes_skill_soul_extract.py [--runtime-root PATH] [--run-id ID]
"""
import argparse, hashlib, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required", file=sys.stderr)
    sys.exit(2)


def parse_frontmatter(text: str) -> tuple:
    """Parse YAML frontmatter from a markdown file. Returns (frontmatter_dict, body_text)."""
    if not text.startswith("---"):
        return {}, text
    # Find closing ---
    m = re.search(r"\n---\n", text[3:])
    if not m:
        return {}, text
    fm_text = text[3:3 + m.start()]
    body = text[3 + m.end():]
    try:
        fm = yaml.safe_load(fm_text)
        if not isinstance(fm, dict):
            return {}, body
        return fm, body
    except Exception:
        return {}, body


def heading_sections(body: str) -> list:
    """Split markdown body into (heading, content) sections by ## / ### headings."""
    lines = body.splitlines()
    sections = []
    current_h = "ROOT"
    current_buf = []
    for line in lines:
        if line.startswith("## "):
            if current_buf or current_h != "ROOT":
                sections.append((current_h, "\n".join(current_buf).strip()))
            current_h = line[3:].strip()
            current_buf = []
        elif line.startswith("# "):
            if current_buf or current_h != "ROOT":
                sections.append((current_h, "\n".join(current_buf).strip()))
            current_h = line[2:].strip()
            current_buf = []
        else:
            current_buf.append(line)
    if current_buf or current_h != "ROOT":
        sections.append((current_h, "\n".join(current_buf).strip()))
    return sections


def classify_skill_source(source_id: str) -> str:
    """Map source_id to a hermes-agent runtimes scope."""
    sid = source_id.lower()
    if "wondelai" in sid:
        return "wondelai"
    if "n8n-skills" in sid or "czlonkowski" in sid:
        return "n8n-authoring"
    if "hindsight" in sid or "vectorize-io" in sid:
        return "memory-system"
    if "agentfield" in sid or "agent-field" in sid:
        return "agentfield"
    if "hermes-curator" in sid or "pingchesu" in sid:
        return "hermes-extension"
    if "hermes-workspace" in sid or "outsourc-e" in sid:
        return "hermes-extension"
    if "mission-control" in sid or "builderz-labs" in sid:
        return "agent-platform"
    if "swe-af" in sid:
        return "agentfield"
    return "unknown"


def extract_skill(fm: dict, body: str, source_id: str, content_sha256: str) -> dict:
    """Extract skill fields per AGENTS.md §10."""
    # Name (frontmatter name, else from first # heading, else from filename)
    name = str(fm.get("name", "") or "").strip()
    description = str(fm.get("description", "") or "").strip()
    license_ = str(fm.get("license", "") or "").strip()

    # Metadata block
    md_raw = fm.get("metadata")
    md = md_raw if isinstance(md_raw, dict) else {}

    # Sections
    sections = heading_sections(body)
    section_titles = [t for t, _ in sections if t != "ROOT"]

    # Tools: search for tool lists (heuristic)
    tool_lines = []
    for h, content in sections:
        if "tool" in h.lower() or "command" in h.lower() or "function" in h.lower():
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    tool_lines.append(line[2:].strip())
    required_tools = sorted(set(tool_lines))[:50]

    # Scope: wondelai / n8n-authoring / memory-system / agentfield / hermes-extension / agent-platform
    scope = classify_skill_source(source_id)

    return {
        "skill_name": name,
        "purpose": description,
        "supported_agents": ["hermes-agent", "claude-code"],  # skills are mostly framework-agnostic but we list both
        "supported_runtimes": [scope] if scope != "unknown" else ["unknown"],
        "required_tools": required_tools,
        "prerequisites": [],
        "inputs": [],
        "outputs": [],
        "commands": [],
        "scripts": [],
        "dependencies": [k for k in (md.get("author"), license_) if k],
        "supported_platforms": [scope] if scope != "unknown" else [],
        "entrypoint": "SKILL.md",
        "companion_resources": [],
        "license": license_,
        "version": str(md.get("version", "") or "").strip(),
        "author": str(md.get("author", "") or "").strip(),
        "section_count": len(section_titles),
        "section_titles": section_titles[:20],
        "scope": scope,
    }


# Soul extraction: classify behavior categories from headings
SOUL_CATEGORY_HEADINGS = {
    "principles": ["principle", "philosophy", "value", "core", "belief"],
    "constraints": ["constraint", "rule", "do not", "don't", "must not", "never", "forbidden", "conversation"],
    "priorities": ["priority", "goal", "objective", "first", "preference", "important"],
    "identity": ["identity", "persona", "who you are", "your name", "self", "introduction", "personality", "you are", "soul", "who"],
    "style": ["style", "tone", "format", "voice", "wording", "lowercase", "uppercase", "preamble"],
    "tool-use-rules": ["tool", "command", "use tools", "function", "autonomy", "use of tools", "use your", "what you can do"],
}


def classify_soul_scope(source_id: str, fm: dict, body: str) -> str:
    """Classify SOUL.md scope per AGENTS.md §10."""
    sid = source_id.lower()
    if "crustocean" in sid:
        return "repository-specific"
    if "mission-control" in sid or "builderz-labs" in sid:
        return "agent-specific"
    return "unknown"


def classify_soul_categories(body: str) -> list:
    """Classify sections by behavior_categories per AGENTS.md §10.

    Recognizes both markdown headings (## Foo) and uppercase section names
    like `PERSONALITY` followed by content.
    """
    cats = set()
    # 1. Markdown headings
    for h, _ in heading_sections(body):
        h_low = h.lower()
        for cat, hints in SOUL_CATEGORY_HEADINGS.items():
            if any(hint in h_low for hint in hints):
                cats.add(cat)
                break
    # 2. Uppercase section names: line is mostly UPPERCASE letters and >= 3 chars
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < 3:
            continue
        # Check if it looks like an uppercase heading
        alpha = [c for c in stripped if c.isalpha()]
        if not alpha:
            continue
        uppercase_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
        if uppercase_ratio >= 0.8 and ":" not in stripped and stripped == stripped.upper():
            h_low = stripped.lower()
            for cat, hints in SOUL_CATEGORY_HEADINGS.items():
                if any(hint in h_low for hint in hints):
                    cats.add(cat)
                    break
    return sorted(cats)


def extract_soul(fm: dict, body: str, source_id: str) -> dict:
    """Extract soul fields per AGENTS.md §10."""
    sections = heading_sections(body)
    section_titles = [t for t, _ in sections if t != "ROOT"]

    scope = classify_soul_scope(source_id, fm, body)
    behavior_categories = classify_soul_categories(body)

    return {
        "scope": scope,
        "source_repo": source_id,
        "behavior_categories": behavior_categories,
        "section_count": len(section_titles),
        "section_titles": section_titles[:20],
        "soul_text_length": len(body),
    }


def load_artifact_metadata(artifacts_dir: Path) -> dict:
    """Build content_sha256 -> artifact record."""
    out = {}
    for p in artifacts_dir.glob("*.json"):
        r = json.loads(p.read_text())
        out[r["content_sha256"]] = r
    return out


def load_occurrences(occurrences_dir: Path) -> dict:
    """Build content_sha256 -> list of occurrence records."""
    out = defaultdict(list)
    for p in occurrences_dir.rglob("*.json"):
        r = json.loads(p.read_text())
        out[r["content_sha256"]].append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--run-id", default="phase-4-2026-06-20")
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    artifacts_dir = runtime / "artifacts"
    occurrences_dir = runtime / "occurrences"
    bundles_dir = runtime / "bundles"
    raw_root = Path(os.environ.get("CODEX_VAULT_ROOT", "")) / "raw"

    if not all(p.exists() for p in [artifacts_dir, occurrences_dir, bundles_dir, raw_root]):
        print("ERROR: required dirs missing", file=sys.stderr)
        sys.exit(2)

    artifacts = load_artifact_metadata(artifacts_dir)
    occurrences = load_occurrences(occurrences_dir)

    # Load all bundles
    bundle_records = []
    for p in bundles_dir.rglob("bundle.json"):
        b = json.loads(p.read_text())
        if b.get("artifact_role") in ("agent-skill", "agent-soul"):
            bundle_records.append(b)

    print(f"Loaded {len(bundle_records)} skill/soul bundles")

    # Outputs
    domain_out = runtime / "domain"
    units_out = runtime / "units"
    domain_out.mkdir(parents=True, exist_ok=True)
    units_out.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    written_d_skill = 0
    written_u_skill = 0
    written_d_soul = 0
    written_u_soul = 0
    skipped_invalid = 0
    excluded_count = 0
    flagged_count = 0
    safe_count = 0
    by_source_skill = Counter()
    by_source_soul = Counter()
    scope_counter = Counter()
    cat_counter = Counter()

    for bundle in bundle_records:
        role = bundle["artifact_role"]
        bundle_id = bundle["bundle_id"]
        entrypoint = bundle["entrypoint"]
        members = bundle.get("members", [])
        source_id = bundle["source_id"]
        source_path = bundle.get("source_path", "")

        # Get the entrypoint member
        ep_member = next((m for m in members if m.get("bundle_role") == "entrypoint"), None)
        if not ep_member:
            skipped_invalid += 1
            continue
        ep_sha = ep_member["content_sha256"]
        ep_path = ep_member["path"]

        # Read content from raw/
        content_path = raw_root / ep_path
        try:
            text = content_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            skipped_invalid += 1
            continue

        if not text.strip():
            skipped_invalid += 1
            continue

        # Security check
        art = artifacts.get(ep_sha, {})
        sec_status = art.get("security_scan", {}).get("status", "not-scanned")
        is_flagged = sec_status == "flagged"
        is_blocked = sec_status == "blocked"
        if is_blocked:
            excluded_count += 1
            continue
        if is_flagged:
            flagged_count += 1
        else:
            safe_count += 1

        # Parse frontmatter
        fm, body = parse_frontmatter(text)

        # Domain + unit paths
        safe_source = source_id.replace(":", "_").replace("/", "_")
        # Subdir by role
        role_dir = "hermes-skill" if role == "agent-skill" else "hermes-soul"
        d_dir = domain_out / role_dir / safe_source
        u_dir = units_out / role_dir / safe_source
        d_dir.mkdir(parents=True, exist_ok=True)
        u_dir.mkdir(parents=True, exist_ok=True)

        # Get all occurrence IDs
        occ_list = occurrences.get(ep_sha, [])
        occ_ids = [o["occurrence_id"] for o in occ_list]
        occ_paths = [o["source_path"] for o in occ_list]

        if role == "agent-skill":
            by_source_skill[source_id] += 1
            extracted = extract_skill(fm, body, source_id, ep_sha)
            scope_counter[extracted["scope"]] += 1

            # Build domain record
            domain_body = {
                "schema": "domain-record/v1",
                "schema_version": "1.0.0",
                "record_id": None,
                "created_at": now,
                "generator": "codex-vault/phase-4-skill-extractor",
                "generator_version": "0.1.0",
                "run_id": args.run_id,
                "content_hash": None,
                "source_record_ids": occ_ids,
                "parser_name": "phase-4-skill-extractor",
                "parser_version": "0.1.0",
                "record_type": "hermes-skill",
                "hermes_skill": extracted,
                "content_sha256": ep_sha,
                "occurrence_count": len(occ_list),
                "occurrence_ids": occ_ids,
                "source_paths": occ_paths,
                "bundle_id": bundle_id,
                "redacted": is_flagged,
            }
            if is_flagged:
                domain_body["redaction_reason"] = "security_scan.status=flagged; semantic_text excluded"
            body_bytes = json.dumps(domain_body, sort_keys=True, indent=2).encode("utf-8")
            h = hashlib.sha256(body_bytes).hexdigest()
            domain_body["record_id"] = f"sha256:{h}"
            domain_body["content_hash"] = f"sha256:{h}"
            # Disambiguate filename by bundle_id hash to handle duplicate entrypoint content_sha256
            file_key = hashlib.sha256(bundle_id.encode()).hexdigest()[:16]
            (d_dir / f"{ep_sha}__{file_key}.json").write_text(json.dumps(domain_body, sort_keys=True, indent=2))
            written_d_skill += 1

            # Build unit record
            if not is_flagged:
                desc = extracted["purpose"][:300] if extracted["purpose"] else "n/a"
                summary = (
                    f"Hermes skill '{extracted['skill_name']}' "
                    f"(scope={extracted['scope']}, "
                    f"sections={extracted['section_count']}, "
                    f"tools={len(extracted['required_tools'])}). "
                    f"Purpose: {desc}"
                )
            else:
                summary = ""
            unit_body = {
                "schema": "unit/v1",
                "schema_version": "1.0.0",
                "record_id": None,
                "created_at": now,
                "generator": "codex-vault/phase-4-skill-extractor",
                "generator_version": "0.1.0",
                "run_id": args.run_id,
                "content_hash": None,
                "source_record_ids": occ_ids,
                "parser_name": "phase-4-skill-extractor",
                "parser_version": "0.1.0",
                "unit_id": f"sha256:{ep_sha}#skill",
                "artifact_id": f"sha256:{ep_sha}",
                "source_anchor": {
                    "section": "skill",
                    "line_start": 1,
                    "line_end": 1,
                    "json_pointer": "/",
                },
                "unit_type": "hermes-skill",
                "title": extracted["skill_name"] or "<redacted>",
                "semantic_text": summary,
                "token_count": len(summary.split()) if summary else 0,
                "fingerprints": {
                    "content_sha256": ep_sha,
                    "normalized_hash": f"sha256:{hashlib.sha256((extracted['skill_name'] + (extracted['purpose'] or '')).encode()).hexdigest()}",
                    "structural_hash": f"sha256:{hashlib.sha256(json.dumps(sorted(extracted['section_titles'])).encode()).hexdigest()}",
                    "semantic_signature": f"sha256:{hashlib.sha256((extracted['skill_name'] + '|' + extracted['scope']).encode()).hexdigest()}",
                },
                "duplicate_of": None,
                "variant_of": None,
                "derived_from": None,
                "dedup_group": f"sha256:{hashlib.sha256(extracted['scope'].encode()).hexdigest()}",
                "bundle_id": bundle_id,
                "redacted": is_flagged,
            }
            if is_flagged:
                unit_body["redaction_reason"] = "security_scan.status=flagged"
            u_bytes = json.dumps(unit_body, sort_keys=True, indent=2).encode("utf-8")
            uh = hashlib.sha256(u_bytes).hexdigest()
            unit_body["record_id"] = f"sha256:{uh}"
            unit_body["content_hash"] = f"sha256:{uh}"
            file_key = hashlib.sha256(bundle_id.encode()).hexdigest()[:16]
            (u_dir / f"{ep_sha}__{file_key}.json").write_text(json.dumps(unit_body, sort_keys=True, indent=2))
            written_u_skill += 1

        else:  # agent-soul
            by_source_soul[source_id] += 1
            extracted = extract_soul(fm, body, source_id)
            cat_counter[tuple(extracted["behavior_categories"])] += 1

            domain_body = {
                "schema": "domain-record/v1",
                "schema_version": "1.0.0",
                "record_id": None,
                "created_at": now,
                "generator": "codex-vault/phase-4-soul-extractor",
                "generator_version": "0.1.0",
                "run_id": args.run_id,
                "content_hash": None,
                "source_record_ids": occ_ids,
                "parser_name": "phase-4-soul-extractor",
                "parser_version": "0.1.0",
                "record_type": "hermes-soul",
                "hermes_soul": extracted,
                "content_sha256": ep_sha,
                "occurrence_count": len(occ_list),
                "occurrence_ids": occ_ids,
                "source_paths": occ_paths,
                "bundle_id": bundle_id,
                "redacted": is_flagged,
            }
            if is_flagged:
                domain_body["redaction_reason"] = "security_scan.status=flagged"
            body_bytes = json.dumps(domain_body, sort_keys=True, indent=2).encode("utf-8")
            h = hashlib.sha256(body_bytes).hexdigest()
            domain_body["record_id"] = f"sha256:{h}"
            domain_body["content_hash"] = f"sha256:{h}"
            file_key = hashlib.sha256(bundle_id.encode()).hexdigest()[:16]
            (d_dir / f"{ep_sha}__{file_key}.json").write_text(json.dumps(domain_body, sort_keys=True, indent=2))
            written_d_soul += 1

            if not is_flagged:
                summary = (
                    f"Hermes soul (scope={extracted['scope']}, "
                    f"behavior_categories={','.join(extracted['behavior_categories']) or 'none'}, "
                    f"sections={extracted['section_count']}, "
                    f"text_len={extracted['soul_text_length']}). "
                    f"Source: {source_id}."
                )
            else:
                summary = ""
            unit_body = {
                "schema": "unit/v1",
                "schema_version": "1.0.0",
                "record_id": None,
                "created_at": now,
                "generator": "codex-vault/phase-4-soul-extractor",
                "generator_version": "0.1.0",
                "run_id": args.run_id,
                "content_hash": None,
                "source_record_ids": occ_ids,
                "parser_name": "phase-4-soul-extractor",
                "parser_version": "0.1.0",
                "unit_id": f"sha256:{ep_sha}#soul",
                "artifact_id": f"sha256:{ep_sha}",
                "source_anchor": {
                    "section": "soul",
                    "line_start": 1,
                    "line_end": 1,
                    "json_pointer": "/",
                },
                "unit_type": "hermes-soul",
                "title": f"Soul for {extracted['source_repo']}",
                "semantic_text": summary,
                "token_count": len(summary.split()) if summary else 0,
                "fingerprints": {
                    "content_sha256": ep_sha,
                    "normalized_hash": f"sha256:{hashlib.sha256(json.dumps(extracted['behavior_categories']).encode()).hexdigest()}",
                    "structural_hash": f"sha256:{hashlib.sha256(json.dumps(sorted(extracted['section_titles'])).encode()).hexdigest()}",
                    "semantic_signature": f"sha256:{hashlib.sha256((extracted['scope'] + '|' + source_id).encode()).hexdigest()}",
                },
                "duplicate_of": None,
                "variant_of": None,
                "derived_from": None,
                "dedup_group": f"sha256:{hashlib.sha256(extracted['scope'].encode()).hexdigest()}",
                "bundle_id": bundle_id,
                "redacted": is_flagged,
            }
            if is_flagged:
                unit_body["redaction_reason"] = "security_scan.status=flagged"
            u_bytes = json.dumps(unit_body, sort_keys=True, indent=2).encode("utf-8")
            uh = hashlib.sha256(u_bytes).hexdigest()
            unit_body["record_id"] = f"sha256:{uh}"
            unit_body["content_hash"] = f"sha256:{uh}"
            file_key = hashlib.sha256(bundle_id.encode()).hexdigest()[:16]
            (u_dir / f"{ep_sha}__{file_key}.json").write_text(json.dumps(unit_body, sort_keys=True, indent=2))
            written_u_soul += 1

        # Progress every 100
        total_processed = written_d_skill + written_d_soul
        if total_processed % 100 == 0:
            print(f"PROGRESS: {total_processed} records written ({written_d_skill} skills, {written_d_soul} souls)")

    print()
    print("=== hermes-skill ===")
    print(f"OK: {written_d_skill} domain-record/v1 (hermes-skill)")
    print(f"OK: {written_u_skill} unit/v1 (hermes-skill)")
    print("By source:")
    for sid, n in sorted(by_source_skill.items(), key=lambda x: -x[1]):
        print(f"  {sid}: {n}")
    print("By scope:")
    for s, n in sorted(scope_counter.items(), key=lambda x: -x[1]):
        print(f"  {s}: {n}")
    print()
    print("=== hermes-soul ===")
    print(f"OK: {written_d_soul} domain-record/v1 (hermes-soul)")
    print(f"OK: {written_u_soul} unit/v1 (hermes-soul)")
    print("By source:")
    for sid, n in sorted(by_source_soul.items(), key=lambda x: -x[1]):
        print(f"  {sid}: {n}")
    print("Behavior categories:")
    for c, n in sorted(cat_counter.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")
    print()
    print(f"Safe (clean): {safe_count}")
    print(f"Flagged (redacted): {flagged_count}")
    print(f"Excluded (blocked): {excluded_count}")
    print(f"Skipped (invalid): {skipped_invalid}")


if __name__ == "__main__":
    main()
