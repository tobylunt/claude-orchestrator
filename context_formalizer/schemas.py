"""Canonical data contracts for the Context Formalizer.

This module defines the Pydantic models that describe Claims, SourceReferences,
and the ArtifactManifest. They are provider-oriented from the start: a
SourceReference carries a ``provider`` discriminator plus a ``uri`` rather than
assuming every source is a local file. The MVP only emits ``provider=local_files``
references, but the schema can already represent ``git_history`` (with a commit
sha in ``revision``), ``github``, ``gdrive``, ``notion``, and ``other`` sources
without changes.

A ``schema_version`` field is included on every model so future migrations can
be detected and handled deterministically.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Iterator
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"
"""Semver string stamped on every record. Bump on incompatible field changes."""

SCHEMA_MAJOR = SCHEMA_VERSION.split(".", 1)[0]
"""Major component of :data:`SCHEMA_VERSION`. Used in the ``v<major>`` segment
of golden-file paths and in JSON Schema ``$id`` URNs so version drift is
discoverable from the schema alone."""

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
"""JSON Schema dialect we emit. Pydantic v2 generates Draft 2020-12 output."""


def _schema_id(name: str) -> str:
    """Canonical ``$id`` URN for an exported schema.

    Embeds the major version so a consumer reading the schema alone can tell
    which contract revision it's looking at; sibling URNs (``...:v2:...``) can
    coexist after a breaking change.
    """
    return f"urn:context-formalizer:schemas:v{SCHEMA_MAJOR}:{name}"


class Provider(str, Enum):
    """Where a SourceReference came from.

    The MVP emits only :attr:`LOCAL_FILES`. Other variants exist so the same
    Claim/SourceReference records can later represent git, GitHub, Google Drive,
    Notion, and other org systems without a schema rewrite.
    """

    LOCAL_FILES = "local_files"
    GIT_HISTORY = "git_history"
    GITHUB = "github"
    GDRIVE = "gdrive"
    NOTION = "notion"
    OTHER = "other"


class Status(str, Enum):
    """Lifecycle status of a Claim."""

    PROPOSED = "proposed"
    VERIFIED = "verified"
    CONFLICT = "conflict"
    STALE = "stale"
    RULED_OUT = "ruled_out"
    NEEDS_HUMAN = "needs_human"


class SourceReference(BaseModel):
    """A provider-aware pointer back to the evidence behind a Claim.

    ``uri`` is the canonical locator within the provider's namespace:
      - ``local_files``: a normalized relative path (or ``file://`` URI) under
        the crawl root. No top-level "path" field — keep everything in ``uri``
        so non-file providers don't need a special case.
      - ``git_history``: a path within the repo; ``revision`` carries the
        commit sha.
      - ``github``/``gdrive``/``notion``: provider-native URLs or IDs.

    Span fields are all optional because different providers expose different
    addressing granularity (line spans for code, page for PDFs, block for
    Notion).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    provider: Provider
    uri: str = Field(min_length=1)
    revision: str | None = None
    observed_at: datetime

    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=0)
    page: int | None = Field(default=None, ge=1)
    block: str | None = None


class Claim(BaseModel):
    """A single cited fact about the organization.

    Hard invariants enforced here:
      - ``source_references`` is non-empty (no claim without provenance).
      - ``confidence`` is in ``[0, 1]``.
      - ``status`` must be one of the declared :class:`Status` values.

    Other invariants (e.g. local-file spans resolving to real bytes, ownership
    claims carrying explicit evidence) are enforced by downstream verifiers
    rather than the schema itself.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_references: list[SourceReference] = Field(min_length=1)
    source_type: str = Field(min_length=1)
    observed_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    status: Status
    related_systems: list[str] = Field(default_factory=list)
    related_owners: list[str] = Field(default_factory=list)


class ArtifactFiles(BaseModel):
    """The canonical set of artifact files a render run must produce.

    Each field maps a canonical artifact to its path under the output
    directory. Every field is required — a downstream consumer reading just
    the manifest can therefore tell whether the full set was emitted without
    consulting code. Filenames in the docstrings match the master spec; only
    the paths (values) are caller-controlled.
    """

    model_config = ConfigDict(extra="forbid")

    claims_jsonl: str = Field(min_length=1)
    """Path to ``claims.jsonl``."""
    org_context_md: str = Field(min_length=1)
    """Path to ``ORG_CONTEXT.md``."""
    systems_md: str = Field(min_length=1)
    """Path to ``SYSTEMS.md``."""
    decisions_md: str = Field(min_length=1)
    """Path to ``DECISIONS.md``."""
    owners_md: str = Field(min_length=1)
    """Path to ``OWNERS.md``."""
    glossary_md: str = Field(min_length=1)
    """Path to ``GLOSSARY.md``."""
    context_gaps_md: str = Field(min_length=1)
    """Path to ``CONTEXT_GAPS.md``."""
    ruledout_md: str = Field(min_length=1)
    """Path to ``RULEDOUT.md``."""


class ArtifactManifest(BaseModel):
    """Describes the set of artifact files produced by a render run.

    ``files`` is an :class:`ArtifactFiles` value (not an open string-to-string
    map) so the canonical artifact set is required by the schema itself.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    generated_at: datetime
    files: ArtifactFiles


CLAIM_ID_PREFIX = "clm_"
"""Prefix on every stable claim id. Makes ids self-describing in logs."""

_CLAIM_ID_HEX_LEN = 32
"""SHA-256 hex digest is 64 chars; we keep the first 32 (128 bits) for short,
collision-resistant ids while remaining content-addressed."""


def _normalize_claim_text(text: str) -> str:
    """Whitespace-collapse + NFKC-normalize.

    Trivial reformatting (extra spaces, NBSP, fullwidth chars) must not change
    the claim id — otherwise re-extraction would generate spurious new claims.
    """
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _span_key(ref: SourceReference) -> str:
    """Canonical string form of a SourceReference span, used for hashing."""

    def _s(v: int | str | None) -> str:
        return "" if v is None else str(v)

    return "|".join(
        [
            _s(ref.start_line),
            _s(ref.end_line),
            _s(ref.start_char),
            _s(ref.end_char),
            _s(ref.page),
            _s(ref.block),
        ]
    )


def stable_claim_id(claim_text: str, primary_source: SourceReference) -> str:
    """Deterministic content-addressed id for a Claim.

    Hash domain: normalized claim text plus the primary source's
    ``provider`` / ``uri`` / ``revision`` / span. SHA-256 (not the builtin
    ``hash()``) is used so the id is stable across runs, processes, and
    Python versions — ``PYTHONHASHSEED`` cannot perturb it.

    The primary source is the source the caller considers canonical for this
    claim (typically the first entry in ``Claim.source_references``).
    """
    parts = [
        _normalize_claim_text(claim_text),
        primary_source.provider.value,
        primary_source.uri,
        primary_source.revision or "",
        _span_key(primary_source),
    ]
    payload = "\x00".join(parts).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"{CLAIM_ID_PREFIX}{digest[:_CLAIM_ID_HEX_LEN]}"


def dumps_claim(claim: Claim) -> str:
    """Serialize one Claim to a single JSON Lines record (no trailing newline).

    Field order in the output matches Pydantic's field declaration order on
    :class:`Claim` (and recursively on :class:`SourceReference`). That order is
    fixed at class definition time, so two runs produce byte-identical output
    for the same input — required by F1 criterion "field order is stable".
    """
    return claim.model_dump_json()


def loads_claim(line: str) -> Claim:
    """Parse one JSON Lines record back into a Claim. Inverse of :func:`dumps_claim`."""
    return Claim.model_validate_json(line)


def dump_claims(claims: Iterable[Claim], fp: TextIO) -> int:
    """Write claims as JSONL to ``fp``. Returns the number of records written.

    Each record is one line terminated by ``\\n``. The caller controls record
    ordering; this helper does not sort. (Stable sort by claim id is the
    extractor's job — see F3.)
    """
    count = 0
    for claim in claims:
        fp.write(dumps_claim(claim))
        fp.write("\n")
        count += 1
    return count


def load_claims(fp: TextIO) -> Iterator[Claim]:
    """Yield Claims from a JSONL stream. Blank lines are skipped."""
    for raw in fp:
        line = raw.strip()
        if not line:
            continue
        yield loads_claim(line)


JSON_SCHEMAS_DIR = Path(__file__).resolve().parent / "json_schemas" / "v1"
"""Directory holding committed JSON Schema golden files for SCHEMA_VERSION 1.x.

Bump the ``v<major>`` segment alongside the major part of ``SCHEMA_VERSION``.
"""

EXPORTED_MODELS: dict[str, type[BaseModel]] = {
    "claim": Claim,
    "source_reference": SourceReference,
    "artifact_manifest": ArtifactManifest,
}
"""Maps the golden-file stem (``<name>.schema.json``) to the model class it
describes. Used by :func:`export_json_schema` and :func:`write_json_schemas`."""


def export_json_schema(name: str, model: type[BaseModel]) -> str:
    """Canonical JSON Schema export for ``model`` under registry name ``name``.

    Injects the standard ``$schema`` (dialect) and ``$id`` (a versioned URN)
    keys at the root so consumers can identify the dialect and the exact
    contract revision from the schema alone.

    Output is sorted by key and indented 2 spaces with a trailing newline. The
    sort makes the byte sequence stable across Pydantic releases that may
    reorder keys internally; the trailing newline keeps the file POSIX-clean.
    """
    schema = model.model_json_schema()
    schema["$schema"] = JSON_SCHEMA_DIALECT
    schema["$id"] = _schema_id(name)
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_json_schemas(out_dir: Path | None = None) -> dict[str, Path]:
    """(Re)generate all golden JSON Schema files. Returns name → path written.

    The maintenance entry point. Run via ``python -m context_formalizer.schemas``
    (or in a test) when a schema model changes and the goldens need refreshing.
    """
    target = out_dir if out_dir is not None else JSON_SCHEMAS_DIR
    target.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    for name, model in EXPORTED_MODELS.items():
        path = target / f"{name}.schema.json"
        path.write_text(export_json_schema(name, model), encoding="utf-8")
        written[name] = path
    return written


def load_json_schema(name: str) -> dict[str, object]:
    """Load a committed JSON Schema by its registry name (e.g. ``"claim"``).

    Returns the parsed schema as a dict ready to hand to ``jsonschema``.
    """
    path = JSON_SCHEMAS_DIR / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover - maintenance entry point
    for n, p in write_json_schemas().items():
        print(f"wrote {n} -> {p}")
