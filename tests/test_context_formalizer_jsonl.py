"""JSONL round-trip tests for :mod:`context_formalizer.schemas`.

Covers F1 criterion: "Round-trip serialize/deserialize of claim fixtures via
JSONL preserves all fields and field order is stable."

Field order being stable means two independent serializations of the same
Claim produce byte-identical strings, and the JSON key order matches the
Pydantic field declaration order on :class:`Claim` and
:class:`SourceReference`.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from context_formalizer.schemas import (
    SCHEMA_VERSION,
    Claim,
    Provider,
    SourceReference,
    Status,
    dump_claims,
    dumps_claim,
    load_claims,
    loads_claim,
)


def _ref_local() -> SourceReference:
    return SourceReference(
        source_id="src-local-1",
        provider=Provider.LOCAL_FILES,
        uri="docs/decisions/0001-use-postgres.md",
        observed_at=datetime(2026, 1, 15, 12, 30, 45, tzinfo=timezone.utc),
        start_line=12,
        end_line=20,
        start_char=0,
        end_char=128,
    )


def _ref_git() -> SourceReference:
    return SourceReference(
        source_id="src-git-1",
        provider=Provider.GIT_HISTORY,
        uri="src/billing/charge.py",
        revision="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        observed_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
        start_line=42,
        end_line=58,
    )


def _ref_notion() -> SourceReference:
    return SourceReference(
        source_id="src-notion-1",
        provider=Provider.NOTION,
        uri="notion://page/abc",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        block="b-1234",
    )


def _full_claim() -> Claim:
    return Claim(
        id="clm_full",
        text="Postgres is the system of record for billing.",
        source_references=[_ref_local(), _ref_git(), _ref_notion()],
        source_type="adr",
        observed_at=datetime(2026, 1, 15, 12, 30, 45, tzinfo=timezone.utc),
        confidence=0.87,
        status=Status.VERIFIED,
        related_systems=["billing", "ledger"],
        related_owners=["team-payments"],
    )


def _minimal_claim() -> Claim:
    return Claim(
        id="clm_minimal",
        text="The platform team owns the API gateway.",
        source_references=[
            SourceReference(
                source_id="src-min",
                provider=Provider.LOCAL_FILES,
                uri="OWNERS.md",
                observed_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            )
        ],
        source_type="ownership",
        observed_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        confidence=0.5,
        status=Status.PROPOSED,
    )


class TestRoundTrip:
    def test_full_claim_round_trip_preserves_all_fields(self) -> None:
        c = _full_claim()
        restored = loads_claim(dumps_claim(c))
        # Equality is field-by-field on a Pydantic model — no field can have
        # silently changed value or been dropped.
        assert restored == c

    def test_minimal_claim_round_trip_preserves_all_fields(self) -> None:
        c = _minimal_claim()
        restored = loads_claim(dumps_claim(c))
        assert restored == c

    def test_serialization_is_single_line_with_no_trailing_newline(self) -> None:
        # JSONL contract: one record per line. dumps_claim itself emits no
        # trailing newline; dump_claims is responsible for delimiters.
        line = dumps_claim(_full_claim())
        assert "\n" not in line
        assert line == line.rstrip()

    def test_schema_version_stamped_on_serialized_record(self) -> None:
        line = dumps_claim(_full_claim())
        # schema_version is the first key — forward migrations can read it
        # without parsing the whole record.
        obj = json.loads(line)
        assert obj["schema_version"] == SCHEMA_VERSION
        assert next(iter(obj)) == "schema_version"

    def test_datetime_preserves_utc_offset(self) -> None:
        c = _full_claim()
        restored = loads_claim(dumps_claim(c))
        assert restored.observed_at == c.observed_at
        assert restored.observed_at.utcoffset() == c.observed_at.utcoffset()
        assert restored.source_references[0].observed_at.tzinfo is not None


class TestFieldOrderStability:
    def test_two_serializations_byte_identical(self) -> None:
        # Stable field order means a re-serialization of the same fixture
        # produces byte-identical output — a precondition for byte-identical
        # claims.jsonl across runs (F3) and golden-file checks downstream.
        c = _full_claim()
        assert dumps_claim(c) == dumps_claim(c)

    def test_claim_keys_in_declaration_order(self) -> None:
        obj = json.loads(dumps_claim(_full_claim()))
        expected = [
            "schema_version",
            "id",
            "text",
            "source_references",
            "source_type",
            "observed_at",
            "confidence",
            "status",
            "related_systems",
            "related_owners",
        ]
        assert list(obj.keys()) == expected

    def test_source_reference_keys_in_declaration_order(self) -> None:
        obj = json.loads(dumps_claim(_full_claim()))
        ref0 = obj["source_references"][0]
        expected = [
            "schema_version",
            "source_id",
            "provider",
            "uri",
            "revision",
            "observed_at",
            "start_line",
            "end_line",
            "start_char",
            "end_char",
            "page",
            "block",
        ]
        assert list(ref0.keys()) == expected

    def test_round_trip_then_reserialize_is_byte_identical(self) -> None:
        # The shape we actually depend on: serialize → deserialize →
        # re-serialize must produce the same bytes. Otherwise extractor
        # idempotence (F3) cannot hold.
        c = _full_claim()
        once = dumps_claim(c)
        twice = dumps_claim(loads_claim(once))
        assert once == twice


class TestStream:
    def test_dump_then_load_preserves_record_order(self) -> None:
        claims = [_full_claim(), _minimal_claim()]
        buf = io.StringIO()
        n = dump_claims(claims, buf)
        assert n == 2
        buf.seek(0)
        restored = list(load_claims(buf))
        assert restored == claims

    def test_dump_emits_newline_terminated_records(self) -> None:
        buf = io.StringIO()
        dump_claims([_full_claim(), _minimal_claim()], buf)
        text = buf.getvalue()
        # Two records, each \n-terminated → exactly two newlines, file ends
        # with one.
        assert text.count("\n") == 2
        assert text.endswith("\n")
        # And neither line contains an embedded newline.
        lines = text.rstrip("\n").split("\n")
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # each line is independently parseable

    def test_load_skips_blank_lines(self) -> None:
        c = _minimal_claim()
        text = f"\n{dumps_claim(c)}\n\n   \n"
        restored = list(load_claims(io.StringIO(text)))
        assert restored == [c]

    def test_file_round_trip(self, tmp_path: Path) -> None:
        claims = [_full_claim(), _minimal_claim()]
        path = tmp_path / "claims.jsonl"
        with path.open("w", encoding="utf-8") as fp:
            dump_claims(claims, fp)
        with path.open("r", encoding="utf-8") as fp:
            restored = list(load_claims(fp))
        assert restored == claims

    def test_file_round_trip_byte_identical_on_second_dump(
        self, tmp_path: Path
    ) -> None:
        claims = [_full_claim(), _minimal_claim()]
        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        with a.open("w", encoding="utf-8") as fp:
            dump_claims(claims, fp)
        with a.open("r", encoding="utf-8") as fp:
            restored = list(load_claims(fp))
        with b.open("w", encoding="utf-8") as fp:
            dump_claims(restored, fp)
        assert a.read_bytes() == b.read_bytes()


class TestErrors:
    def test_loads_rejects_invalid_status(self) -> None:
        line = dumps_claim(_minimal_claim())
        obj = json.loads(line)
        obj["status"] = "approved"
        with pytest.raises(Exception):
            loads_claim(json.dumps(obj))

    def test_loads_rejects_extra_field(self) -> None:
        line = dumps_claim(_minimal_claim())
        obj = json.loads(line)
        obj["unexpected"] = "drift"
        with pytest.raises(Exception):
            loads_claim(json.dumps(obj))
