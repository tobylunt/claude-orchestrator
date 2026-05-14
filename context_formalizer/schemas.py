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
import unicodedata
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0.0"
"""Semver string stamped on every record. Bump on incompatible field changes."""


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

    schema_version: str = SCHEMA_VERSION
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

    schema_version: str = SCHEMA_VERSION
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source_references: list[SourceReference] = Field(min_length=1)
    source_type: str = Field(min_length=1)
    observed_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    status: Status
    related_systems: list[str] = Field(default_factory=list)
    related_owners: list[str] = Field(default_factory=list)


class ArtifactManifest(BaseModel):
    """Describes the set of artifact files produced by a render run.

    ``files`` maps a logical artifact name (e.g. ``"claims_jsonl"``,
    ``"org_context_md"``) to a relative path under the output directory.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    files: dict[str, str] = Field(min_length=1)


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
