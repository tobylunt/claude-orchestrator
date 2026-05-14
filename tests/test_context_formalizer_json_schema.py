"""JSON Schema export tests for :mod:`context_formalizer.schemas`.

Covers the remaining F1 success criteria:
  - Pydantic models for Claim, SourceReference, and ArtifactManifest load and
    validate against the committed JSON Schema exports.
  - JSON Schema export matches the committed golden file byte-for-byte after
    canonical formatting.

The export-equality test is the canary: if a schema model is edited (a field
renamed, a constraint changed, a docstring tweaked), the byte-for-byte check
fails immediately, forcing the contributor to regenerate goldens deliberately
via ``python -m context_formalizer.schemas`` rather than letting the contract
drift silently.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import jsonschema
import pytest

from context_formalizer.schemas import (
    EXPORTED_MODELS,
    JSON_SCHEMAS_DIR,
    ArtifactManifest,
    Claim,
    Provider,
    SourceReference,
    Status,
    export_json_schema,
    load_json_schema,
    write_json_schemas,
)


def _local_ref() -> SourceReference:
    return SourceReference(
        source_id="src-1",
        provider=Provider.LOCAL_FILES,
        uri="docs/decisions/0001-use-postgres.md",
        observed_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        start_line=12,
        end_line=20,
    )


def _claim() -> Claim:
    return Claim(
        id="claim-1",
        text="Postgres is the system of record for billing.",
        source_references=[_local_ref()],
        source_type="adr",
        observed_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        confidence=0.9,
        status=Status.PROPOSED,
    )


def _manifest() -> ArtifactManifest:
    return ArtifactManifest(
        generated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        files={
            "claims_jsonl": "claims.jsonl",
            "org_context_md": "ORG_CONTEXT.md",
        },
    )


class TestExportCanonicalForm:
    def test_export_is_sorted_indent_two_with_trailing_newline(self) -> None:
        text = export_json_schema(Claim)
        assert text.endswith("\n")
        parsed = json.loads(text)
        # Round-tripping with the same canonical formatting yields the same
        # bytes — proves the formatter is idempotent.
        assert (
            json.dumps(parsed, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            == text
        )
        # Top-level keys appear in lexical order in the raw text.
        first_keys = [
            line.strip().split('"', 2)[1]
            for line in text.splitlines()
            if line.startswith("  \"")
        ]
        assert first_keys == sorted(first_keys)

    def test_export_is_deterministic_across_calls(self) -> None:
        # Same input, same bytes — required for the byte-for-byte golden test
        # to mean anything.
        for model in EXPORTED_MODELS.values():
            assert export_json_schema(model) == export_json_schema(model)


class TestGoldenMatchesLive:
    @pytest.mark.parametrize("name", sorted(EXPORTED_MODELS))
    def test_export_matches_golden_byte_for_byte(self, name: str) -> None:
        model = EXPORTED_MODELS[name]
        live = export_json_schema(model)
        golden_path = JSON_SCHEMAS_DIR / f"{name}.schema.json"
        assert golden_path.exists(), (
            f"missing golden {golden_path}. Regenerate with "
            f"`python -m context_formalizer.schemas`."
        )
        golden = golden_path.read_text(encoding="utf-8")
        assert live == golden, (
            f"JSON Schema export for {name} drifted from committed golden at "
            f"{golden_path}. If this change is intentional, regenerate with "
            f"`python -m context_formalizer.schemas`."
        )


class TestFixturesValidateAgainstCommittedSchema:
    """Pydantic-serialized fixtures must validate against the committed JSON
    Schema — i.e. the live model and the committed contract agree."""

    def test_claim_fixture_validates(self) -> None:
        schema = load_json_schema("claim")
        payload = json.loads(_claim().model_dump_json())
        jsonschema.validate(payload, schema)

    def test_source_reference_fixture_validates(self) -> None:
        schema = load_json_schema("source_reference")
        payload = json.loads(_local_ref().model_dump_json())
        jsonschema.validate(payload, schema)

    def test_artifact_manifest_fixture_validates(self) -> None:
        schema = load_json_schema("artifact_manifest")
        payload = json.loads(_manifest().model_dump_json())
        jsonschema.validate(payload, schema)

    def test_claim_with_git_history_source_validates(self) -> None:
        schema = load_json_schema("claim")
        claim = Claim(
            id="claim-git-1",
            text="Charge logic was rewritten when we moved to idempotency keys.",
            source_references=[
                SourceReference(
                    source_id="git-abc123",
                    provider=Provider.GIT_HISTORY,
                    uri="src/billing/charge.py",
                    revision="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
                    observed_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
                    start_line=42,
                    end_line=58,
                )
            ],
            source_type="git_commit",
            observed_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
            confidence=0.75,
            status=Status.VERIFIED,
        )
        jsonschema.validate(json.loads(claim.model_dump_json()), schema)

    def test_invalid_payload_fails_committed_schema(self) -> None:
        # Confidence > 1 must be rejected by the committed schema too, not
        # just by Pydantic — proves the schema actually carries the constraint.
        schema = load_json_schema("claim")
        bad = json.loads(_claim().model_dump_json())
        bad["confidence"] = 1.5
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)

    def test_unknown_status_fails_committed_schema(self) -> None:
        schema = load_json_schema("claim")
        bad = json.loads(_claim().model_dump_json())
        bad["status"] = "approved"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)

    def test_empty_source_references_fails_committed_schema(self) -> None:
        # Provenance invariant must live in the schema itself, not only in
        # Pydantic field config — otherwise downstream verifiers reading just
        # the schema would miss it.
        schema = load_json_schema("claim")
        bad = json.loads(_claim().model_dump_json())
        bad["source_references"] = []
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


class TestWriteJsonSchemas:
    def test_write_to_tmp_matches_committed(self, tmp_path) -> None:
        # write_json_schemas is the maintenance entry point. Writing to a
        # scratch dir and comparing to committed files proves the helper does
        # what the docstring claims.
        written = write_json_schemas(tmp_path)
        assert set(written) == set(EXPORTED_MODELS)
        for name in EXPORTED_MODELS:
            assert (
                written[name].read_text(encoding="utf-8")
                == (JSON_SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
            )
