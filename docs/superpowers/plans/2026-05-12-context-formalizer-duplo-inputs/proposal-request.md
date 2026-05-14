# Proposal Request

Use these notes to draft a Bob spec for a context crawler/formalizer bot.

## Objective

Build a local-first CLI that reads a bounded corpus of organization context
from files and produces cited, reviewable context artifacts for humans and
agents.

This is both a useful tool and a Bob dogfood case. It should pressure Bob's
spec quality, verifier quality, provenance handling, durable state, review
gates, and correction loops.

## Required Product Shape

The first implementation must be local-files-only. Do not include Slack, Drive,
Notion, GitHub API, browser automation, or automatic mutation of canonical org
docs in the MVP.

Expected artifacts:

- `claims.jsonl`: machine-readable extracted claims.
- `ORG_CONTEXT.md`: narrative overview assembled from verified claims.
- `SYSTEMS.md`: systems, modules, repos, services, and boundaries.
- `DECISIONS.md`: decisions, rationales, source links, and supersession notes.
- `OWNERS.md`: people, teams, and ownership claims when evidence exists.
- `GLOSSARY.md`: local terminology and acronyms.
- `CONTEXT_GAPS.md`: missing, conflicting, stale, or low-confidence context.
- `RULEDOUT.md`: rejected interpretations and failed approaches.

## Claim Contract

Each extracted claim should include:

- stable id
- claim text
- source references
- source type
- observed timestamp
- confidence
- status: `proposed`, `verified`, `conflict`, `stale`, `ruled_out`, or
  `needs_human`
- related systems
- related owners, when supported by source evidence

## Hard Invariants

- No claim without provenance.
- No secret or high-risk PII in generated artifacts.
- Conflicts are preserved, not silently resolved.
- Stale sources are marked stale rather than promoted as current.
- Ownership is never inferred without source evidence.
- `RULEDOUT` entries prevent repeated bad interpretations.
- Human approval is required before generated context becomes canonical.

## Suggested Feature Boundaries

Draft a Bob spec with small, testable features in this rough order:

1. Define the artifact contracts and schemas.
2. Implement a local file crawler and source inventory.
3. Extract claim candidates with source spans and confidence.
4. Generate Markdown artifacts from claim records.
5. Add verifier checks for schema validity, provenance coverage, secrets/PII,
   conflict preservation, and round-trip determinism.

Prefer deterministic parsing, fixture-based tests, and reviewable JSONL over
opaque summarization.
