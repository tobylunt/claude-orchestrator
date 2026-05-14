"""Schema validation tests for :mod:`context_formalizer.schemas`.

Covers the validation-only success criteria for F1:
  - Status enum rejects unknown values.
  - Confidence outside [0, 1] is rejected with a clear validation error.
  - SourceReference supports provider, uri, revision, observed_at, and the
    optional line/char/page/block span fields without assuming a local file.
  - A git history fixture can be represented with ``provider=git_history`` and
    a commit-sha revision.
  - Every Claim carries at least one SourceReference (provenance invariant).

Stable-id, JSONL round-trip, and JSON Schema golden-file checks live in
separate test modules added in later iterations.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from context_formalizer.schemas import (
    SCHEMA_VERSION,
    ArtifactManifest,
    Claim,
    Provider,
    SourceReference,
    Status,
)


def _local_source_ref(**overrides: object) -> SourceReference:
    base: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "source_id": "src-1",
        "provider": Provider.LOCAL_FILES,
        "uri": "docs/decisions/0001-use-postgres.md",
        "observed_at": datetime(2026, 1, 15, tzinfo=timezone.utc),
        "start_line": 12,
        "end_line": 20,
    }
    base.update(overrides)
    return SourceReference(**base)


def _claim(**overrides: object) -> Claim:
    base: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "id": "claim-1",
        "text": "Postgres is the system of record for billing.",
        "source_references": [_local_source_ref()],
        "source_type": "adr",
        "observed_at": datetime(2026, 1, 15, tzinfo=timezone.utc),
        "confidence": 0.9,
        "status": Status.PROPOSED,
    }
    base.update(overrides)
    return Claim(**base)


def _canonical_files(**overrides: str) -> dict[str, str]:
    base = {
        "claims_jsonl": "claims.jsonl",
        "org_context_md": "ORG_CONTEXT.md",
        "systems_md": "SYSTEMS.md",
        "decisions_md": "DECISIONS.md",
        "owners_md": "OWNERS.md",
        "glossary_md": "GLOSSARY.md",
        "context_gaps_md": "CONTEXT_GAPS.md",
        "ruledout_md": "RULEDOUT.md",
    }
    base.update(overrides)
    return base


class TestSourceReference:
    def test_local_files_happy_path(self) -> None:
        ref = _local_source_ref()
        assert ref.provider is Provider.LOCAL_FILES
        assert ref.uri == "docs/decisions/0001-use-postgres.md"
        assert ref.schema_version == SCHEMA_VERSION

    def test_git_history_with_commit_sha_revision(self) -> None:
        # Git history ingestion is deferred, but the schema must already
        # represent it: provider=git_history + commit sha in revision.
        ref = SourceReference(
            schema_version=SCHEMA_VERSION,
            source_id="git-abc123",
            provider=Provider.GIT_HISTORY,
            uri="src/billing/charge.py",
            revision="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            observed_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
            start_line=42,
            end_line=58,
        )
        assert ref.provider is Provider.GIT_HISTORY
        assert ref.revision == "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

    def test_pdf_span_uses_page(self) -> None:
        ref = SourceReference(
            schema_version=SCHEMA_VERSION,
            source_id="gd-1",
            provider=Provider.GDRIVE,
            uri="https://drive.google.com/file/d/abc",
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            page=3,
        )
        assert ref.page == 3
        assert ref.start_line is None

    def test_notion_span_uses_block(self) -> None:
        ref = SourceReference(
            schema_version=SCHEMA_VERSION,
            source_id="nt-1",
            provider=Provider.NOTION,
            uri="notion://page/abc",
            observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            block="b-1234",
        )
        assert ref.block == "b-1234"

    def test_unknown_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SourceReference(
                schema_version=SCHEMA_VERSION,
                source_id="s",
                provider="slack",  # type: ignore[arg-type]
                uri="x",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

    def test_empty_uri_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SourceReference(
                schema_version=SCHEMA_VERSION,
                source_id="s",
                provider=Provider.LOCAL_FILES,
                uri="",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

    def test_schema_version_required(self) -> None:
        # schema_version must be present on every record so downstream
        # consumers can dispatch migrations without guessing.
        with pytest.raises(ValidationError) as exc:
            SourceReference(
                source_id="s",
                provider=Provider.LOCAL_FILES,
                uri="x",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        assert "schema_version" in str(exc.value)

    def test_extra_fields_rejected(self) -> None:
        # The MVP forbids extra fields so accidental drift surfaces as a
        # validation error rather than silently lost data.
        with pytest.raises(ValidationError):
            SourceReference(
                schema_version=SCHEMA_VERSION,
                source_id="s",
                provider=Provider.LOCAL_FILES,
                uri="x",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                path="x",  # type: ignore[call-arg]
            )


class TestClaim:
    def test_happy_path(self) -> None:
        c = _claim()
        assert c.status is Status.PROPOSED
        assert c.confidence == 0.9
        assert c.schema_version == SCHEMA_VERSION
        assert len(c.source_references) == 1

    def test_schema_version_required(self) -> None:
        with pytest.raises(ValidationError) as exc:
            Claim(
                id="claim-1",
                text="x",
                source_references=[_local_source_ref()],
                source_type="adr",
                observed_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
                confidence=0.5,
                status=Status.PROPOSED,
            )
        assert "schema_version" in str(exc.value)

    def test_unknown_status_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _claim(status="approved")  # type: ignore[arg-type]
        assert "status" in str(exc.value)

    @pytest.mark.parametrize("bad", [-0.01, 1.01, 2.0, -1.0])
    def test_confidence_out_of_range_rejected(self, bad: float) -> None:
        with pytest.raises(ValidationError) as exc:
            _claim(confidence=bad)
        msg = str(exc.value)
        assert "confidence" in msg

    @pytest.mark.parametrize("ok", [0.0, 0.5, 1.0])
    def test_confidence_endpoints_allowed(self, ok: float) -> None:
        c = _claim(confidence=ok)
        assert c.confidence == ok

    def test_source_references_required_non_empty(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _claim(source_references=[])
        assert "source_references" in str(exc.value)

    def test_related_systems_and_owners_default_empty(self) -> None:
        c = _claim()
        assert c.related_systems == []
        assert c.related_owners == []


class TestArtifactManifest:
    def test_happy_path(self) -> None:
        m = ArtifactManifest(
            schema_version=SCHEMA_VERSION,
            generated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
            files=_canonical_files(),
        )
        assert m.files.claims_jsonl == "claims.jsonl"
        assert m.files.ruledout_md == "RULEDOUT.md"
        assert m.schema_version == SCHEMA_VERSION

    def test_schema_version_required(self) -> None:
        with pytest.raises(ValidationError) as exc:
            ArtifactManifest(
                generated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
                files=_canonical_files(),
            )
        assert "schema_version" in str(exc.value)

    @pytest.mark.parametrize(
        "missing",
        [
            "claims_jsonl",
            "org_context_md",
            "systems_md",
            "decisions_md",
            "owners_md",
            "glossary_md",
            "context_gaps_md",
            "ruledout_md",
        ],
    )
    def test_missing_canonical_artifact_rejected(self, missing: str) -> None:
        # Dropping any one of the canonical artifacts must fail validation
        # so the manifest is a load-bearing checklist, not advisory.
        files = _canonical_files()
        files.pop(missing)
        with pytest.raises(ValidationError) as exc:
            ArtifactManifest(
                schema_version=SCHEMA_VERSION,
                generated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
                files=files,
            )
        assert missing in str(exc.value)

    def test_unknown_file_key_rejected(self) -> None:
        # Extra logical artifacts must not silently slip in (extra="forbid"
        # on ArtifactFiles).
        files = _canonical_files()
        files["not_a_real_artifact"] = "x.md"
        with pytest.raises(ValidationError):
            ArtifactManifest(
                schema_version=SCHEMA_VERSION,
                generated_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
                files=files,
            )
