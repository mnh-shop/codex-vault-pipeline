#!/usr/bin/env python3
"""Phase 4 — Domain: documentation extraction.

Walks all artifacts with artifact_role=documentation.
For each:
  - Read content from raw/
  - Split by markdown headings (## / ### / etc.)
  - Emit one unit/v1 per section with unit_type=doc-section
  - Per AGENTS.md §4, Layer C units are retrieval units (sections, etc.)

Honors security:
  - blocked: skip entirely
  - flagged: emit with empty semantic_text

Dedup: units keyed by sha256:<artifact>#heading:<normalized> (per AGENTS.md §5).
"""
import argparse, hashlib, json, re, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR


def slugify_heading(text: str) -> str:
    """Normalize heading to a stable anchor slug. Lowercase, replace spaces with -, strip non-alphanumeric."""
    s = text.lower().strip()
    s = re.sub(r"[\s/]+", "-", s)
    s = re.sub(r"[^a-z0-9._-]", "", s)
    s = s.strip("-")
    return s[:80] or "section"


def split_markdown_sections(text: str) -> list:
    """Split markdown text into sections by ATX headings (#, ##, ###, etc.).

    Returns list of (heading_level, heading_text, body_lines, line_start, line_end).
    The first section has heading_text="(intro)" if the file doesn't start with a heading.
    """
    lines = text.splitlines()
    sections = []
    current_h_level = None
    current_h_text = None
    current_buf = []
    current_start = 1
    current_end = 0

    def push():
        if current_h_text is not None or current_buf:
            sections.append((current_h_level, current_h_text, "\n".join(current_buf), current_start, current_end))

    first_line = True
    for i, line in enumerate(lines, start=1):
        m = re.match(r"^(#+)\s+(.*)$", line)
        if m:
            # push previous
            push()
            current_h_level = len(m.group(1))
            current_h_text = m.group(2).strip()
            current_buf = []
            current_start = i + 1  # body starts after heading line
            current_end = i
        else:
            current_buf.append(line)
            current_end = i
        first_line = False
    push()
    return sections


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--run-id", default="phase-4-2026-06-20")
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    artifacts_dir = runtime / "artifacts"
    occurrences_dir = runtime / "occurrences"
    raw_root = Path(os.environ.get("CODEX_VAULT_ROOT", "")) / "raw"

    # Load all artifacts and occurrences
    artifacts = {}
    for p in artifacts_dir.glob("*.json"):
        r = json.loads(p.read_text())
        if r.get("artifact_role") == "documentation":
            artifacts[r["content_sha256"]] = r

    occurrences_by_sha = defaultdict(list)
    for p in occurrences_dir.rglob("*.json"):
        o = json.loads(p.read_text())
        occurrences_by_sha[o["content_sha256"]].append(o)

    print(f"Loaded {len(artifacts)} documentation artifacts")
    print(f"Total occurrences for docs: {sum(len(v) for v in occurrences_by_sha.values())}")

    # Output
    units_out = runtime / "units" / "doc-section"
    units_out.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    written_u = 0
    safe_count = 0
    flagged_count = 0
    excluded_count = 0
    skipped_empty = 0
    skipped_invalid = 0
    by_source = Counter()
    section_count_dist = Counter()

    # Sort for deterministic order
    for sha in sorted(artifacts.keys()):
        art = artifacts[sha]
        occ_list = occurrences_by_sha.get(sha, [])
        if not occ_list:
            skipped_invalid += 1
            continue
        first_occ = occ_list[0]
        source_id = first_occ["source_id"]
        source_path = first_occ["source_path"]
        by_source[source_id] += 1

        # Security
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

        # Read content
        content_path = raw_root / source_path
        try:
            text = content_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            skipped_invalid += 1
            continue
        if not text.strip():
            skipped_empty += 1
            continue

        # Split into sections
        sections = split_markdown_sections(text)
        if not sections:
            # Treat the whole file as one section
            sections = [(None, "(root)", text, 1, text.count("\n") + 1)]
        section_count_dist[len(sections)] += 1

        safe_source = source_id.replace(":", "_").replace("/", "_")
        u_dir = units_out / safe_source
        u_dir.mkdir(parents=True, exist_ok=True)

        # Per-section units
        occ_id = first_occ["occurrence_id"]
        for h_level, h_text, body, line_start, line_end in sections:
            # Compute anchor
            if h_text and h_text != "(root)":
                slug = slugify_heading(h_text)
                anchor = f"heading:{slug}"
            else:
                anchor = "root"
            unit_id = f"sha256:{sha}#{anchor}"

            # Build unit
            if not is_flagged:
                body_text = body.strip()
                # Cap section text size for safety
                if len(body_text) > 30000:
                    body_text = body_text[:30000] + "..."
                token_count = len(body_text.split())
            else:
                body_text = ""
                token_count = 0

            # Title: heading text or filename
            if h_text and h_text != "(root)":
                title = h_text
            else:
                title = source_path.rsplit("/", 1)[-1]

            # semantic_text for non-flagged
            if not is_flagged and body_text:
                # Build a semantic summary
                if h_text and h_text != "(root)":
                    summary = f"Section '{h_text}': {body_text[:500]}"
                else:
                    summary = f"Documentation file: {body_text[:500]}"
            else:
                summary = ""

            unit_body = {
                "schema": "unit/v1",
                "schema_version": "1.0.0",
                "record_id": None,
                "created_at": now,
                "generator": "codex-vault/phase-4-doc-extractor",
                "generator_version": "0.1.0",
                "run_id": args.run_id,
                "content_hash": None,
                "source_record_ids": [occ_id],
                "parser_name": "phase-4-doc-extractor",
                "parser_version": "0.1.0",
                "unit_id": unit_id,
                "artifact_id": f"sha256:{sha}",
                "source_anchor": {
                    "section": h_text or "(root)",
                    "line_start": line_start,
                    "line_end": line_end,
                    "json_pointer": None,
                },
                "unit_type": "doc-section",
                "title": title,
                "semantic_text": summary,
                "token_count": token_count,
                "fingerprints": {
                    "content_sha256": sha,
                    "normalized_hash": f"sha256:{hashlib.sha256(unit_id.encode()).hexdigest()}",
                    "structural_hash": f"sha256:{hashlib.sha256(json.dumps({'h_level': h_level, 'h_text': h_text}, sort_keys=True).encode()).hexdigest()}",
                    "semantic_signature": f"sha256:{hashlib.sha256((title + (summary or '')).encode()).hexdigest()}",
                },
                "duplicate_of": None,
                "variant_of": None,
                "derived_from": None,
                "dedup_group": f"sha256:{sha}",
                "redacted": is_flagged,
            }
            if is_flagged:
                unit_body["redaction_reason"] = "security_scan.status=flagged"

            u_bytes = json.dumps(unit_body, sort_keys=True, indent=2).encode("utf-8")
            uh = hashlib.sha256(u_bytes).hexdigest()
            unit_body["record_id"] = f"sha256:{uh}"
            unit_body["content_hash"] = f"sha256:{uh}"

            (u_dir / f"{uh}.json").write_text(json.dumps(unit_body, sort_keys=True, indent=2))
            written_u += 1

        # Progress every 100 docs
        if (safe_count + flagged_count) % 100 == 0 and (safe_count + flagged_count) > 0:
            print(f"PROGRESS: {safe_count + flagged_count} docs processed, {written_u} units written")

    print()
    print(f"OK: {written_u} unit/v1 (doc-section) → {units_out}")
    print(f"Safe (clean): {safe_count}")
    print(f"Flagged (redacted): {flagged_count}")
    print(f"Excluded (blocked): {excluded_count}")
    print(f"Skipped (empty): {skipped_empty}")
    print(f"Skipped (invalid): {skipped_invalid}")
    print()
    print("By source (doc file count):")
    for sid, n in sorted(by_source.items(), key=lambda x: -x[1])[:15]:
        print(f"  {sid}: {n}")
    print()
    print("Sections-per-file distribution:")
    for n, c in sorted(section_count_dist.items(), key=lambda x: x[0])[:15]:
        print(f"  {n} sections: {c} files")


if __name__ == "__main__":
    main()
