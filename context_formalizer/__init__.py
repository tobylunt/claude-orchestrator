"""Context Formalizer: canonical data contracts and artifact generation.

The MVP is local-files-only, but the schemas in :mod:`context_formalizer.schemas`
are provider-oriented so future ingestion of git history, GitHub, Google Drive,
Notion, etc. can use the same Claim and SourceReference records without rewrites.
"""

from context_formalizer.schemas import (
    SCHEMA_VERSION,
    ArtifactManifest,
    Claim,
    Provider,
    SourceReference,
    Status,
)

__all__ = [
    "SCHEMA_VERSION",
    "ArtifactManifest",
    "Claim",
    "Provider",
    "SourceReference",
    "Status",
]
