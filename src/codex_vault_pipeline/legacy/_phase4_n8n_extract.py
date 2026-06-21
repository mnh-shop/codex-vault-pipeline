#!/usr/bin/env python3
"""Phase 4 — Domain 1: n8n workflow extraction.

Walks all valid n8n JSONs from Phase 3 (occurrence records + content records).
For each: extract structured fields per the user spec:
  - name, node_count, connection_count
  - trigger_types, node_types
  - integrations (derived from node types)
  - credential_types (no values, just type names)
  - external_hosts (URLs in node parameters)
  - ai_components (AI node types)
  - sticky_note_text
  - safe_parameters (parameter keys, not values)
  - topology (connection summary)

For each valid n8n JSON, emits:
  - 1 domain-record/v1 with record_type=n8n-workflow
  - 1 unit/v1 with unit_type=n8n-workflow (semantic_text = workflow summary)

Honors flagged/blocked:
  - blocked: skip entirely (excluded)
  - flagged: emit domain-record with safe fields only (no sticky_note_text, no safe_parameters, no semantic_text)

Dedup: domain records are keyed by content_sha256. Multiple occurrences of the same content share the domain record (occurrence_count > 1).
"""
import argparse, hashlib, json, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from codex_vault_pipeline.paths import resolve_paths, add_vault_root_arg, require_vault_root, ENV_VAR

# n8n node types → integration names
def integration_from_node_type(node_type: str) -> str:
    if not node_type or not node_type.startswith("n8n-nodes-base."):
        return ""
    return node_type[len("n8n-nodes-base."):]


# Trigger node type prefixes
TRIGGER_TYPES = {
    "n8n-nodes-base.webhook": "webhook",
    "n8n-nodes-base.scheduleTrigger": "schedule",
    "n8n-nodes-base.cron": "schedule",
    "n8n-nodes-base.manualTrigger": "manual",
    "n8n-nodes-base.start": "manual",
    "n8n-nodes-base.executeWorkflowTrigger": "workflow",
    "n8n-nodes-base.emailTrigger": "email",
    "n8n-nodes-base.telegramTrigger": "telegram",
    "n8n-nodes-base.slackTrigger": "slack",
    "n8n-nodes-base.githubTrigger": "github",
    "n8n-nodes-base.errorTrigger": "error",
}

AI_NODE_PREFIXES = ("@n8n/n8n-nodes-langchain.", "n8n-nodes-base.openAi", "n8n-nodes-base.anthropic", "n8n-nodes-base.googleGemini")


def extract_workflow(workflow: dict) -> dict:
    """Extract structured fields from an n8n workflow JSON. Returns a dict with all extracted fields.
    No raw text is included — only structural metadata.
    """
    nodes = workflow.get("nodes", []) or []
    connections = workflow.get("connections", {}) or {}

    # node types
    node_types = sorted({n.get("type", "unknown") for n in nodes if isinstance(n, dict)})
    integrations = sorted({integration_from_node_type(t) for t in node_types if integration_from_node_type(t)})

    # triggers
    trigger_types = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = n.get("type", "")
        if t in TRIGGER_TYPES:
            trigger_types.append(TRIGGER_TYPES[t])
        elif "Trigger" in t:
            trigger_types.append(t.split(".")[-1])
    trigger_types = sorted(set(trigger_types))

    # AI components
    ai_components = sorted({n.get("type", "") for n in nodes if isinstance(n, dict) and n.get("type", "").startswith(AI_NODE_PREFIXES)})

    # credentials (TYPES ONLY, no values)
    cred_types = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        creds = n.get("credentials", {}) or {}
        if isinstance(creds, dict):
            for k, v in creds.items():
                if isinstance(v, dict) and "id" in v:
                    cred_types.add(k)
        elif isinstance(creds, list):
            for c in creds:
                if isinstance(c, dict):
                    cred_types.add(c.get("name", "unknown"))
    credential_types = sorted(cred_types)

    # external hosts: extract URLs from node parameters
    import re
    URL_PAT = re.compile(r"https?://([a-zA-Z0-9.\-]+)")
    hosts = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        params = n.get("parameters", {}) or {}
        if isinstance(params, dict):
            for v in params.values():
                if isinstance(v, str):
                    for m in URL_PAT.finditer(v):
                        hosts.add(m.group(1))
        elif isinstance(params, list):
            for item in params:
                if isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str):
                            for m in URL_PAT.finditer(v):
                                hosts.add(m.group(1))
    external_hosts = sorted(hosts)

    # sticky notes (text only)
    sticky_text_parts = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "n8n-nodes-base.stickyNote":
            content = (n.get("parameters", {}) or {}).get("content", "")
            if content:
                sticky_text_parts.append(content)
    sticky_note_text = "\n\n".join(sticky_text_parts)

    # safe parameters: KEYS only, not values, from each node's parameters
    safe_params = set()
    for n in nodes:
        if not isinstance(n, dict):
            continue
        params = n.get("parameters", {}) or {}
        if isinstance(params, dict):
            safe_params.update(params.keys())
        elif isinstance(params, list):
            for item in params:
                if isinstance(item, dict):
                    safe_params.update(item.keys())
    safe_parameters = sorted(safe_params)

    # topology: edge count + unique (source_type, target_type) pairs
    edges = []
    for src_name, conn_list in connections.items():
        if not isinstance(conn_list, list):
            continue
        for conn in conn_list:
            if not isinstance(conn, dict):
                continue
            target = conn.get("node", "")
            edges.append((src_name, target))
    edge_count = len(edges)
    # Find types for each endpoint
    node_types_by_name = {n.get("name"): n.get("type", "unknown") for n in nodes if isinstance(n, dict)}
    typed_edges = sorted({(node_types_by_name.get(s, "?"), node_types_by_name.get(t, "?")) for s, t in edges})
    topology = {
        "edge_count": edge_count,
        "typed_edge_signature": "|".join(f"{s}->{t}" for s, t in typed_edges[:50]),  # cap for size
        "edge_signature_count": len(typed_edges),
    }

    return {
        "name": workflow.get("name", ""),
        "node_count": len(nodes),
        "connection_count": edge_count,
        "trigger_types": trigger_types,
        "node_types": node_types,
        "integrations": integrations,
        "credential_types": credential_types,
        "external_hosts": external_hosts,
        "ai_components": ai_components,
        "sticky_note_text": sticky_note_text,
        "safe_parameters": safe_parameters,
        "topology": topology,
        "active": bool(workflow.get("active", False)),
        "valid_n8n_document": True,
    }


def main():
    ap = argparse.ArgumentParser()
    add_vault_root_arg(ap)
    ap.add_argument("--runtime-root", default=os.path.join(os.environ.get("CODEX_VAULT_ROOT", ""), ".runtime"))
    ap.add_argument("--run-id", default="phase-4-2026-06-20")
    args = ap.parse_args()

    runtime = Path(args.runtime_root)
    artifacts_dir = runtime / "artifacts"
    occurrences_dir = runtime / "occurrences"

    if not artifacts_dir.exists() or not occurrences_dir.exists():
        print("ERROR: artifacts/occurrences dirs missing; run Phase 3 first", file=sys.stderr)
        sys.exit(2)

    # Load all content records
    contents = {}
    for p in artifacts_dir.glob("*.json"):
        rec = json.loads(p.read_text())
        contents[rec["artifact_id"].split(":", 1)[1]] = rec

    # Load all occurrence records (for content_sha256 → list of occurrences)
    occurrences_by_content = defaultdict(list)
    occurrences_by_sha = defaultdict(list)
    for p in occurrences_dir.rglob("*.json"):
        rec = json.loads(p.read_text())
        occurrences_by_content[rec["content_sha256"]].append(rec)
        occurrences_by_sha[rec["content_sha256"]].append(rec)

    # Find all valid n8n JSONs (status: n8n-workflow, security != blocked)
    n8n_contents = []
    for sha, c in contents.items():
        if c.get("artifact_role") != "n8n-workflow":
            continue
        if c.get("security_scan", {}).get("status") == "blocked":
            continue
        n8n_contents.append((sha, c))
    n8n_contents.sort(key=lambda x: x[0])

    # Outputs
    domain_out = runtime / "domain" / "n8n-workflow"
    units_out = runtime / "units" / "n8n-workflow"
    domain_out.mkdir(parents=True, exist_ok=True)
    units_out.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    written_d = 0
    written_u = 0
    safe_count = 0
    redacted_count = 0
    excluded_count = 0
    skipped_invalid = 0
    by_source = Counter()
    trigger_counter = Counter()
    integration_counter = Counter()
    duplicate_count = 0  # occurrences that share a domain record

    # Per-content dedup: build domain record once per unique content, then add occurrence_count
    seen_sha = set()

    for sha, content_rec in n8n_contents:
        occ_list = occurrences_by_sha.get(sha, [])
        if not occ_list:
            continue
        first_occ = occ_list[0]
        source_id = first_occ["source_id"]
        source_path = first_occ["source_path"]
        occurrence_id = first_occ["occurrence_id"]
        by_source[source_id] += 1

        # Read content
        content_path = Path(os.environ.get("CODEX_VAULT_ROOT", "")) / "raw" / source_path
        try:
            workflow = json.loads(content_path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            skipped_invalid += 1
            continue

        if not isinstance(workflow, dict) or not {"name", "nodes", "connections"}.issubset(workflow.keys()):
            skipped_invalid += 1
            continue

        # Extract
        extracted = extract_workflow(workflow)
        sec_status = content_rec.get("security_scan", {}).get("status", "not-scanned")
        is_flagged = sec_status == "flagged"
        is_blocked = sec_status == "blocked"
        # safety
        if is_blocked:
            excluded_count += 1
            continue
        if is_flagged:
            redacted_count += 1
        else:
            safe_count += 1

        # Dedup: emit domain record per unique content_sha256
        # For flagged: only safe fields (no sticky_note_text, no safe_parameters)
        if is_flagged:
            safe_extracted = {k: v for k, v in extracted.items()
                              if k not in ("sticky_note_text", "safe_parameters")}
            safe_extracted["redacted"] = True
            safe_extracted["redaction_reason"] = "security_scan.status=flagged; raw text excluded"
            extracted = safe_extracted

        # Build domain record
        record_id = f"sha256:{sha}"
        domain_body = {
            "schema": "domain-record/v1",
            "schema_version": "1.0.0",
            "record_id": None,
            "created_at": now,
            "generator": "codex-vault/phase-4-n8n-extractor",
            "generator_version": "0.1.0",
            "run_id": args.run_id,
            "content_hash": None,
            "source_record_ids": [occurrence_id],  # primary occurrence; rest counted via occurrence_count
            "parser_name": "phase-4-n8n-extractor",
            "parser_version": "0.1.0",
            "record_type": "n8n-workflow",
            "n8n_workflow": {
                "name": extracted["name"],
                "node_count": extracted["node_count"],
                "connection_count": extracted["connection_count"],
                "trigger_types": extracted["trigger_types"],
                "node_types": extracted["node_types"],
                "integrations": extracted["integrations"],
                "credential_types": extracted["credential_types"],
                "external_hosts": extracted["external_hosts"],
                "ai_components": extracted["ai_components"],
                "sticky_note_text": extracted.get("sticky_note_text", ""),
                "safe_parameters": extracted.get("safe_parameters", []),
                "topology": extracted["topology"],
                "active": extracted["active"],
                "valid_n8n_document": extracted["valid_n8n_document"],
            },
            "content_sha256": sha,
            "occurrence_count": len(occ_list),
            "occurrence_ids": [o["occurrence_id"] for o in occ_list],
            "source_paths": [o["source_path"] for o in occ_list],
            "redacted": is_flagged,
        }
        if is_flagged:
            domain_body["redaction_reason"] = "security_scan.status=flagged; sticky_note_text and safe_parameters excluded"
        body_bytes = json.dumps(domain_body, sort_keys=True, indent=2).encode("utf-8")
        h = hashlib.sha256(body_bytes).hexdigest()
        domain_body["record_id"] = f"sha256:{h}"
        domain_body["content_hash"] = f"sha256:{h}"

        # Write per-source subdir
        safe_source = source_id.replace(":", "_").replace("/", "_")
        d = domain_out / safe_source
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sha}.json").write_text(json.dumps(domain_body, sort_keys=True, indent=2))
        written_d += 1

        # Build unit record (workflow-level)
        # For flagged: no semantic_text (no raw text)
        if not is_flagged:
            summary = (
                f"n8n workflow '{extracted['name']}' with {extracted['node_count']} nodes "
                f"and {extracted['connection_count']} connections. "
                f"Triggers: {','.join(extracted['trigger_types']) or 'none'}. "
                f"Integrations: {','.join(extracted['integrations'][:10]) or 'none'}. "
                f"AI components: {','.join(extracted['ai_components']) or 'none'}. "
                f"External hosts: {','.join(extracted['external_hosts'][:10]) or 'none'}. "
                f"Credential types: {','.join(extracted['credential_types']) or 'none'}."
            )
            unit_body = {
                "schema": "unit/v1",
                "schema_version": "1.0.0",
                "record_id": None,
                "created_at": now,
                "generator": "codex-vault/phase-4-n8n-extractor",
                "generator_version": "0.1.0",
                "run_id": args.run_id,
                "content_hash": None,
                "source_record_ids": [occurrence_id],
                "parser_name": "phase-4-n8n-extractor",
                "parser_version": "0.1.0",
                "unit_id": f"sha256:{sha}#workflow",
                "artifact_id": f"sha256:{sha}",
                "source_anchor": {
                    "json_pointer": "/",
                    "line_start": 1,
                    "line_end": 1,
                    "section": "workflow",
                },
                "unit_type": "n8n-workflow",
                "title": extracted["name"],
                "semantic_text": summary,
                "token_count": len(summary.split()),
                "fingerprints": {
                    "content_sha256": sha,
                    "normalized_hash": f"sha256:{hashlib.sha256(summary.encode()).hexdigest()}",
                    "structural_hash": f"sha256:{hashlib.sha256(json.dumps(extracted['topology'], sort_keys=True).encode()).hexdigest()}",
                    "semantic_signature": f"sha256:{hashlib.sha256((extracted['name'] + ','.join(extracted['node_types'])).encode()).hexdigest()}",
                },
                "duplicate_of": None,
                "variant_of": None,
                "derived_from": None,
                "dedup_group": f"sha256:{hashlib.sha256(json.dumps(extracted['topology'], sort_keys=True).encode()).hexdigest()}",
            }
            u_bytes = json.dumps(unit_body, sort_keys=True, indent=2).encode("utf-8")
            uh = hashlib.sha256(u_bytes).hexdigest()
            unit_body["record_id"] = f"sha256:{uh}"
            unit_body["content_hash"] = f"sha256:{uh}"
            ud = units_out / safe_source
            ud.mkdir(parents=True, exist_ok=True)
            (ud / f"{sha}.json").write_text(json.dumps(unit_body, sort_keys=True, indent=2))
            written_u += 1
        else:
            # Flagged: emit a minimal unit with no semantic_text
            unit_body = {
                "schema": "unit/v1",
                "schema_version": "1.0.0",
                "record_id": None,
                "created_at": now,
                "generator": "codex-vault/phase-4-n8n-extractor",
                "generator_version": "0.1.0",
                "run_id": args.run_id,
                "content_hash": None,
                "source_record_ids": [occurrence_id],
                "parser_name": "phase-4-n8n-extractor",
                "parser_version": "0.1.0",
                "unit_id": f"sha256:{sha}#workflow",
                "artifact_id": f"sha256:{sha}",
                "source_anchor": {
                    "json_pointer": "/",
                    "line_start": 1,
                    "line_end": 1,
                    "section": "workflow",
                },
                "unit_type": "n8n-workflow",
                "title": extracted["name"] or "<redacted>",
                "semantic_text": "",  # redacted
                "token_count": 0,
                "fingerprints": {
                    "content_sha256": sha,
                    "normalized_hash": f"sha256:{sha}",
                    "structural_hash": f"sha256:{hashlib.sha256(json.dumps(extracted['topology'], sort_keys=True).encode()).hexdigest()}",
                    "semantic_signature": f"sha256:{sha}",
                },
                "duplicate_of": None,
                "variant_of": None,
                "derived_from": None,
                "dedup_group": f"sha256:{hashlib.sha256(json.dumps(extracted['topology'], sort_keys=True).encode()).hexdigest()}",
                "redacted": True,
                "redaction_reason": "security_scan.status=flagged",
            }
            u_bytes = json.dumps(unit_body, sort_keys=True, indent=2).encode("utf-8")
            uh = hashlib.sha256(u_bytes).hexdigest()
            unit_body["record_id"] = f"sha256:{uh}"
            unit_body["content_hash"] = f"sha256:{uh}"
            ud = units_out / safe_source
            ud.mkdir(parents=True, exist_ok=True)
            (ud / f"{sha}.json").write_text(json.dumps(unit_body, sort_keys=True, indent=2))
            written_u += 1

        # Count dedup
        if len(occ_list) > 1:
            duplicate_count += len(occ_list) - 1
        # Tally
        for t in extracted["trigger_types"]:
            trigger_counter[t] += 1
        for i in extracted["integrations"]:
            integration_counter[i] += 1

    # Summary
    print(f"OK: {written_d} domain-record/v1 (n8n-workflow) → {domain_out}")
    print(f"OK: {written_u} unit/v1 (n8n-workflow) → {units_out}")
    print(f"OK: skipped {skipped_invalid} (invalid/unknown JSON)")
    print()
    print(f"Safe (clean): {safe_count}")
    print(f"Redacted (flagged): {redacted_count}")
    print(f"Excluded (blocked): {excluded_count}")
    print(f"Duplicates (occurrences sharing content): {duplicate_count}")
    print()
    print("By source (workflow count):")
    for sid, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {sid}: {n}")
    print()
    print("Trigger types:")
    for t, n in sorted(trigger_counter.items(), key=lambda x: -x[1])[:15]:
        print(f"  {t}: {n}")
    print()
    print("Top integrations:")
    for i, n in sorted(integration_counter.items(), key=lambda x: -x[1])[:15]:
        print(f"  {i}: {n}")


if __name__ == "__main__":
    main()
