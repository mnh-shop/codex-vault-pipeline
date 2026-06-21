"""Deterministic extractors for Layer A source records.

This subpackage contains the deterministic extractors that
populate the new technical-profile fields (`source_platform`,
`repo_identity`, `repo_profile`, `interfaces`,
`workflow_synthesis`) added to `source.schema.yaml` in
2026-06-21.

Each extractor is **safe by design**:

- Never parses `.env`, `*.pem`, `*credentials*`, `*secret*`,
  `*token*`, or any file matching the secret-bearing patterns.
- Never reads environment-variable VALUES; only declared names.
- Treats dependency manifests as data (names, version specs)
  and never emits their content into the semantic text.
- Is conservative: when a signal is ambiguous, the field is
  omitted rather than guessed.
"""
