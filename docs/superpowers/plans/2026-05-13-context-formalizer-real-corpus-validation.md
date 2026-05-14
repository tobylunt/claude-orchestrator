# Context Formalizer Real-Corpus Validation Plan

## Purpose

Use a real, local repository to validate the context formalizer after Bob
implements the draft spec. This should reveal contract pressure that synthetic
fixtures will miss, without making CI depend on private local state.

## Candidate Corpus

Local path: `~/ddl/udc-dagster`

Observed on May 13, 2026:

- 149 tracked files.
- 500 git commits.
- Mix of Python, Markdown, SQL, YAML/YML, shell, CSV, TOML, and deployment
  files.
- Includes root instructions (`AGENTS.md`, `CLAUDE.md`), top-level and nested
  docs, Dagster asset code, SQL transforms, configs, tests, and deployment
  workflow files.
- Working tree has untracked `udc_db/docs/plans/`; validation should default to
  tracked files via `git ls-files` and include untracked files only when
  explicitly requested.
- The repository directory also contains many image artifacts and local cache
  directories; these should not be included by default.

## Validation Sequence

After the context formalizer implementation lands:

```bash
context-formalizer crawl --root ~/ddl/udc-dagster --out /tmp/udc-context
context-formalizer extract --sources /tmp/udc-context/sources.jsonl --out /tmp/udc-context/claims.jsonl
context-formalizer render --claims /tmp/udc-context/claims.jsonl --out /tmp/udc-context/artifacts --draft
context-formalizer validate --inputs /tmp/udc-context
```

Then re-run the same sequence and verify deterministic bytes for
`sources.jsonl`, `claims.jsonl`, and rendered Markdown artifacts.

## What To Learn

- Whether `SourceReference` is provider-oriented enough for local files and
  future git-history/GitHub/Drive/Notion sources.
- Whether `git ls-files` gives a useful default corpus and avoids local image
  artifacts, caches, and ignored files.
- Whether the claim schema can represent docs, source code, SQL, configs,
  owner/instruction files, and deployment metadata without special casing.
- Whether conflict, stale, owner-evidence, and `[RULEDOUT]` behavior appears in
  realistic material or needs richer fixtures.
- Whether full git history should become the next provider after
  `LocalFileSourceProvider`, rather than being forced into the file crawler.

## Non-Goals

- Do not commit `~/ddl/udc-dagster` or generated validation outputs into this
  repository.
- Do not make CI depend on local paths outside the Bob repo.
- Do not add Google Drive, Notion, GitHub API, or browser automation until the
  local provider contract has survived this validation pass.
