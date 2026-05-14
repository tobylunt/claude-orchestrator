"""Context Formalizer: canonical data contracts and artifact generation.

The MVP is local-files-only, but the schemas in :mod:`context_formalizer.schemas`
are provider-oriented so future ingestion of git history, GitHub, Google Drive,
Notion, etc. can use the same Claim and SourceReference records without rewrites.
"""

from context_formalizer.schemas import (
    CLAIM_ID_PREFIX,
    EXPORTED_MODELS,
    JSON_SCHEMA_DIALECT,
    JSON_SCHEMAS_DIR,
    SCHEMA_MAJOR,
    SCHEMA_VERSION,
    ArtifactFiles,
    ArtifactManifest,
    Claim,
    Provider,
    SourceReference,
    Status,
    dump_claims,
    dumps_claim,
    export_json_schema,
    load_claims,
    load_json_schema,
    loads_claim,
    stable_claim_id,
    write_json_schemas,
)

__all__ = [
    "CLAIM_ID_PREFIX",
    "EXPORTED_MODELS",
    "JSON_SCHEMA_DIALECT",
    "JSON_SCHEMAS_DIR",
    "SCHEMA_MAJOR",
    "SCHEMA_VERSION",
    "ArtifactFiles",
    "ArtifactManifest",
    "Claim",
    "Provider",
    "SourceReference",
    "Status",
    "dump_claims",
    "dumps_claim",
    "export_json_schema",
    "load_claims",
    "load_json_schema",
    "loads_claim",
    "stable_claim_id",
    "write_json_schemas",
]
