# Security Policy

This document describes the security guarantees the pipeline
makes, the boundaries it enforces, and the responsibilities
left to the operator.

## 1.0 Threat Model

The pipeline runs **locally**, against a vault the operator
already has on disk. The threat model therefore focuses on:

- **Accidental disclosure.** The pipeline never writes to
  `${VAULT_ROOT}/raw/` and never ships `raw/` to the repo;
  the `.gitignore` excludes `*.db`, `*.sqlite*`, `*.lance/`,
  `.runtime/`, and `raw/`.
- **Secret leakage via indexes.** Binary files (`.pkl`,
  `.faiss`, model weights, pickled indices) are classified
  as `metadata-only` and are **never** ingested into the
  semantic_text, FTS, candidate body, or vector indexes.
  PDFs are scanned for text-layer secrets; the binary objects
  inside a PDF (images, embedded fonts) are not indexed.
- **Path traversal.** Every path is constructed under
  `${VAULT_ROOT}/.runtime/` or `${VAULT_ROOT}/raw/`. No
  script writes to any other filesystem location.
- **Subprocess injection.** The pipeline never executes
  commands from untrusted input. The CLI's argv synthesis is
  deterministic and the only `subprocess` calls in the legacy
  scripts are sandboxed `git` operations under
  `${VAULT_ROOT}/raw/`.

## 2.0 Secret Scanning

The pipeline uses [detect-secrets](https://github.com/Yelp/detect-secrets)
(the same scanner the vault itself uses) for all
text-readable artifacts. The scanner runs:

- During `codex-vault-ingest`, on every text file in the cloned
  source
- During `codex-vault-build-indexes`, on every text-format
  occurrence that is being added to the FTS index

Findings are categorized as:

- **clean** — no secret detected
- **flagged** — possible secret; the occurrence is marked
  `redacted: true` and its `semantic_text` (if any) is
  replaced with a structural summary
- **blocked** — confirmed secret; the occurrence is excluded
  from all indexes
- **not_scanned** — binary file; the occurrence is treated as
  `metadata-only` regardless of contents

A `redacted: true` flag in an occurrence is **never** silently
cleared. A `blocked` occurrence is **never** added to any
index. The strict validator's R02 rule rejects records with
malformed or missing `redaction_reason` when `redacted: true`.

## 3.0 What the Pipeline Does NOT Do

The pipeline explicitly does **not**:

- Read, write, or exfiltrate `raw/` source captures
- Touch the vault's wiki, candidate notes, or migration
  reports
- Promote candidates (move from `candidate` to `canonical`)
- Delete project files anywhere on disk
- Connect to any external network endpoint (except for the
  controlled `git clone` of a user-specified GitHub URL)
- Read, transmit, or store environment variables other than
  `CODEX_VAULT_ROOT` and the standard `PATH` / `HOME` / `USER`
- Touch `~/.ssh`, `~/.aws`, `~/.config/gh`, or any other
  credential store

The pipeline also does **not** ship the raw/ directory or any
of the runtime data to the git repository. The `.gitignore`
in this repo explicitly excludes `.runtime/`, `raw/`, `*.db`,
`*.sqlite*`, `*.lance/`, `.env`, and `.venv/`.

## 4.0 Operator Responsibilities

The operator is responsible for:

- Setting `CODEX_VAULT_ROOT` to a path they trust
- Reviewing the `--dry-run` output before allowing an ingest
  to proceed for real
- Verifying the vault's `AGENTS.md` and `security_status` table
  before any promotion
- Keeping the `codex-vault-pipeline` repo up to date
- Auditing the security audit report at
  `${VAULT_ROOT}/.runtime/reports/incremental-ingest-security-audit.md`
  after every incremental ingest

## 5.0 Reporting a Vulnerability

If you find a security issue in the pipeline, please open a
GitHub issue in the `codex-vault-pipeline` repository marked
`security` and `do-not-merge`. Do not open a public PR with
exploit details.

## 6.0 Compliance

The pipeline:

- Never reads PII (it operates on code, configs, and
  documentation)
- Never stores credentials (it uses the operator's existing
  `~/.gitconfig` and SSH keys for `git clone`)
- Never makes outbound network calls except `git clone` of
  user-specified URLs
- Produces a complete audit trail in
  `${VAULT_ROOT}/.runtime/reports/` for every run
