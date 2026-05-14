# Context Formalizer: Local-First Org Context Crawler and Artifact Generator

## Motivation
Coding agents and humans both suffer when organizational context is implicit: rationales, decisions, ownership, and ruled-out approaches live in scattered files and tribal memory. This project builds a local-first CLI that crawls a bounded corpus of organization files and produces cited, reviewable context artifacts (claims.jsonl plus narrative Markdown). It is also a Bob dogfood case that pressures spec quality, provenance handling, durable state, review gates, and correction loops. The MVP is deliberately scoped to local files only — no Slack/Drive/Notion/GitHub API/browser automation, and no automatic mutation of canonical org docs. Hard invariants (no claim without provenance, no secrets/PII leakage, conflicts preserved, stale sources marked, ownership never inferred without evidence, RULEDOUT entries respected, human approval before canonical promotion) must be enforced by verifiers, not vibes.

## Features

### F1: Artifact Contracts and Schemas
- task_type: library
- verifier: python_pytest
- success_criteria:
  - Pydantic/dataclass models for Claim, SourceReference, and ArtifactManifest load and validate against committed JSON Schema exports.
  - Stable-id generator is deterministic: identical normalized inputs yield identical ids across runs and processes.
  - Round-trip serialize/deserialize of claim fixtures via JSONL preserves all fields and field order is stable.
  - Status enum rejects unknown values and confidence outside [0,1] is rejected with a clear validation error.
  - JSON Schema export matches committed golden file byte-for-byte after canonical formatting.
- description: |
    Define and implement the canonical data contracts for the system: the Claim record schema (stable id, claim text, source references with file path and span, source type, observed timestamp, confidence in [0,1], status enum {proposed, verified, conflict, stale, ruled_out, needs_human}, related_systems, related_owners), the SourceReference schema, and the artifact manifest schema describing the set of output files (claims.jsonl, ORG_CONTEXT.md, SYSTEMS.md, DECISIONS.md, OWNERS.md, GLOSSARY.md, CONTEXT_GAPS.md, RULEDOUT.md). Provide a Python library module `context_formalizer.schemas` exposing pydantic (or dataclass + jsonschema) models, JSON Schema export, stable-id generation (content-addressed hash over normalized claim text + primary source), and round-trip (de)serialization helpers for JSONL. Include golden fixtures for each schema and a documented version field for forward migration.

### F2: Local File Crawler and Source Inventory
- task_type: cli
- verifier: python_pytest
- success_criteria:
  - Running crawl twice over the same fixture tree produces byte-identical sources.jsonl (deterministic ordering by source_id).
  - .contextignore patterns exclude matching files and excluded files do not appear in sources.jsonl.
  - Crawler refuses to follow symlinks pointing outside the root and emits a logged warning.
  - No network calls occur during crawl (verified by socket monkeypatch that raises on connect).
  - Source type classification matches expected labels on a fixture corpus covering markdown, code, config, and ADR files.
  - Binary files above the size threshold are recorded with status=skipped and no content hash mismatch.
- description: |
    Build a deterministic local-first crawler CLI subcommand `context-formalizer crawl --root <path>` that walks a bounded corpus (respecting an explicit allowlist of extensions and a .contextignore file), classifies each file by source type (markdown_doc, source_code, config, adr, issue_export, meeting_notes, other), captures content hash, size, mtime, and computes a stable source_id. It must not follow symlinks outside the root, must skip binary files above a size threshold, and must emit `sources.jsonl` plus a human-readable `INVENTORY.md`. No network access. The crawler is explicitly local-files-only; no Slack/Drive/Notion/GitHub API/browser automation.

### F3: Claim Candidate Extraction with Provenance
- task_type: library
- verifier: python_pytest
- success_criteria:
  - Every emitted claim has a non-empty source_references list pointing to a real file path and a valid line span (provenance invariant).
  - Running extraction twice on identical inputs yields byte-identical claims.jsonl after stable sort by claim id.
  - Fixture corpus with intentionally contradicting decisions produces two claims both with status=conflict and cross-references.
  - Fixture corpus with old mtimes produces claims with status=stale per configured threshold.
  - No ownership claim is emitted for a fixture where the only signal is heuristic inference without explicit source text.
  - [RULEDOUT] markers in fixtures produce status=ruled_out claims and never status=verified.
- description: |
    Implement deterministic, rule-based claim extraction from the source inventory. For each source, extract candidate Claim records with exact source spans (file path, start_line, end_line, char offsets), attach a confidence derived from declarative heuristics (e.g., ADR headings, decision markers, ownership tables, glossary patterns, ruled-out markers like `[RULEDOUT]`), and assign initial status=proposed. Conflict detection compares claims with overlapping subjects but contradictory predicates and marks both with status=conflict. Stale detection flags claims whose source mtime exceeds a configurable threshold. Ownership claims are only emitted when explicit source evidence (e.g., OWNERS file entries, CODEOWNERS, signed-by lines) is present. Output is appended to `claims.jsonl`. Prefer deterministic parsing over LLM summarization; no LLM calls in MVP.

### F4: Markdown Artifact Generation from Claims
- task_type: cli
- verifier: python_pytest
- success_criteria:
  - Render on a fixture claims.jsonl produces byte-identical artifact files across two runs (round-trip determinism).
  - Every non-heading factual sentence in generated Markdown contains at least one citation marker resolvable to a source span in claims.jsonl.
  - Claims with status=conflict appear in CONTEXT_GAPS.md and are NOT silently merged in ORG_CONTEXT.md.
  - Claims with status=stale are rendered with an explicit stale marker and never appear as current in DECISIONS.md.
  - Running render without --approve writes only to the draft directory and leaves canonical paths untouched.
  - Supersession chains in fixture decisions render in chronological order with supersedes/superseded-by links.
- description: |
    Implement `context-formalizer render --claims claims.jsonl --out <dir>` which generates the full artifact set: ORG_CONTEXT.md, SYSTEMS.md, DECISIONS.md, OWNERS.md, GLOSSARY.md, CONTEXT_GAPS.md, and RULEDOUT.md. Each rendered statement must include an inline citation pointing back to its source span. CONTEXT_GAPS.md aggregates claims with status in {conflict, stale, needs_human} and any subject areas with zero claims from a configurable expected-topics list. RULEDOUT.md lists all status=ruled_out claims with the rationale source. DECISIONS.md preserves supersession chains. Rendering is pure: same claims.jsonl input always produces byte-identical Markdown output. A `--draft` flag writes to a draft directory; promotion to canonical paths requires an explicit `--approve` flag and a recorded human approval token (review gate).

### F5: Verifier Suite: Schema, Provenance, Secrets/PII, Conflicts, Determinism
- task_type: library
- verifier: python_pytest
- success_criteria:
  - Validate passes on the golden fixture corpus and exits 0 with an empty failure list.
  - Injecting a claim without source_references causes validate to exit non-zero with a provenance-violation error.
  - Injecting a synthetic AWS access key into a rendered artifact causes the secrets/PII check to fail with the offending file and line reported.
  - Two contradictory claims without status=conflict cause the conflict-preservation check to fail.
  - An owner claim whose source span does not contain explicit ownership evidence causes the ownership-evidence check to fail.
  - Modifying claims.jsonl ordering without changing content does not cause the determinism check to fail (sort is normalized), but mutating a claim body does cause failure.
  - A verified claim that contradicts an existing ruled_out claim causes the RULEDOUT-respect check to fail.
- description: |
    Implement `context-formalizer validate --inputs <dir>` as a composite verifier that enforces the hard invariants and is intended to gate human approval. Checks: (a) schema validity of claims.jsonl and sources.jsonl against committed JSON Schemas; (b) provenance coverage — every claim resolves to an existing source span and every rendered Markdown citation resolves to a claim id; (c) secrets/PII scan over both source excerpts embedded in claims and rendered artifacts using a configurable regex+entropy ruleset (AWS keys, private keys, JWTs, emails flagged by policy, phone numbers) with explicit allowlist; (d) conflict preservation — verifier fails if two claims with conflicting predicates exist but neither carries status=conflict; (e) ownership-evidence check — every owner claim has an explicit-evidence source reference; (f) round-trip determinism — re-running extraction and render produces identical bytes; (g) RULEDOUT respect — no verified claim contradicts an existing ruled_out claim. The verifier returns a non-zero exit code and a structured report on any failure.
