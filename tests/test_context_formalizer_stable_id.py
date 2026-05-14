"""Tests for ``context_formalizer.schemas.stable_claim_id``.

Covers F1 success criterion 2:
  "Stable-id generator is deterministic: identical normalized inputs yield
  identical ids across runs and processes."

We verify in-process determinism, cross-process determinism (via a subprocess
so any hidden ``PYTHONHASHSEED`` dependency would surface), text normalization
(NFKC + whitespace collapse), and that the hash domain actually includes each
of the documented inputs (claim text, provider, uri, revision, span).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import datetime, timezone

from context_formalizer.schemas import (
    CLAIM_ID_PREFIX,
    SCHEMA_VERSION,
    Provider,
    SourceReference,
    stable_claim_id,
)


def _ref(**overrides: object) -> SourceReference:
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


class TestStableClaimId:
    def test_format(self) -> None:
        cid = stable_claim_id("Postgres is the system of record.", _ref())
        assert cid.startswith(CLAIM_ID_PREFIX)
        hex_part = cid[len(CLAIM_ID_PREFIX) :]
        assert len(hex_part) == 32
        assert all(c in "0123456789abcdef" for c in hex_part)

    def test_deterministic_in_process(self) -> None:
        a = stable_claim_id("Postgres is the system of record.", _ref())
        b = stable_claim_id("Postgres is the system of record.", _ref())
        assert a == b

    def test_deterministic_across_processes(self) -> None:
        # SHA-256 doesn't depend on PYTHONHASHSEED, but the criterion calls
        # out "across runs and processes" — pin it down with a real subprocess
        # that gets a randomized hash seed.
        script = textwrap.dedent(
            """
            from datetime import datetime, timezone
            from context_formalizer.schemas import (
                SCHEMA_VERSION, Provider, SourceReference, stable_claim_id,
            )
            ref = SourceReference(
                schema_version=SCHEMA_VERSION,
                source_id="src-1",
                provider=Provider.LOCAL_FILES,
                uri="docs/decisions/0001-use-postgres.md",
                observed_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
                start_line=12,
                end_line=20,
            )
            print(stable_claim_id("Postgres is the system of record.", ref))
            """
        )
        out = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": "random"},
        )
        expected = stable_claim_id("Postgres is the system of record.", _ref())
        assert out.stdout.strip() == expected

    def test_text_normalization_whitespace(self) -> None:
        # Trivial reformatting must not change the id.
        a = stable_claim_id("Postgres is the system of record.", _ref())
        b = stable_claim_id(
            "  Postgres   is\tthe  system\nof  record.  ", _ref()
        )
        assert a == b

    def test_text_normalization_nfkc(self) -> None:
        # Fullwidth "Postgres" should normalize to the ASCII form.
        a = stable_claim_id("Postgres is X.", _ref())
        b = stable_claim_id("Ｐｏｓｔｇｒｅｓ is X.", _ref())
        assert a == b

    def test_different_text_yields_different_id(self) -> None:
        a = stable_claim_id("Postgres is the system of record.", _ref())
        b = stable_claim_id("MySQL is the system of record.", _ref())
        assert a != b

    def test_different_uri_yields_different_id(self) -> None:
        a = stable_claim_id("X", _ref(uri="a.md"))
        b = stable_claim_id("X", _ref(uri="b.md"))
        assert a != b

    def test_different_provider_yields_different_id(self) -> None:
        local = stable_claim_id("X", _ref(provider=Provider.LOCAL_FILES))
        git = stable_claim_id(
            "X",
            _ref(
                provider=Provider.GIT_HISTORY,
                revision="a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
            ),
        )
        assert local != git

    def test_different_revision_yields_different_id(self) -> None:
        a = stable_claim_id(
            "X",
            _ref(provider=Provider.GIT_HISTORY, revision="aaaa"),
        )
        b = stable_claim_id(
            "X",
            _ref(provider=Provider.GIT_HISTORY, revision="bbbb"),
        )
        assert a != b

    def test_different_span_yields_different_id(self) -> None:
        a = stable_claim_id("X", _ref(start_line=1, end_line=2))
        b = stable_claim_id("X", _ref(start_line=10, end_line=20))
        assert a != b

    def test_missing_revision_treated_consistently(self) -> None:
        # revision=None and revision="" must hash to the same id so callers
        # don't have to remember which they passed.
        a = stable_claim_id("X", _ref(revision=None))
        b = stable_claim_id("X", _ref(revision=""))
        assert a == b

    def test_page_vs_line_span_distinct(self) -> None:
        # A page=1 span and a start_line=1 span must not collide.
        page = stable_claim_id(
            "X",
            SourceReference(
                schema_version=SCHEMA_VERSION,
                source_id="s",
                provider=Provider.GDRIVE,
                uri="x",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                page=1,
            ),
        )
        line = stable_claim_id(
            "X",
            SourceReference(
                schema_version=SCHEMA_VERSION,
                source_id="s",
                provider=Provider.GDRIVE,
                uri="x",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                start_line=1,
            ),
        )
        assert page != line
