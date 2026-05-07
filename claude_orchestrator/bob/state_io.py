"""Atomic JSON writes and append-only JSONL helpers.

Design (see spec §3.1):
- Mutable JSON files use tempfile + fsync + rename for atomicity. Readers
  see either the old or new file, never a half-written one.
- Append-only JSONL files use O_APPEND. POSIX guarantees atomic writes
  per syscall when the record is below PIPE_BUF (~4 KB on Linux/macOS),
  so concurrent appenders interleave records cleanly without corruption.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# POSIX guarantees atomic write per syscall up to PIPE_BUF.
# On Linux/macOS this is at least 512 bytes and usually 4096. We use 4096 as
# the safe upper bound for concurrent appenders.
_PIPE_BUF_SAFE = 4096


def write_json_atomic(path: Path, data: Any) -> None:
    """Write `data` to `path` atomically: tempfile in same dir, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, default=str)

    # Same directory ensures rename is atomic on POSIX (same filesystem).
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from `path`, returning `default` if the file does not exist."""
    if not path.exists():
        return default
    return json.loads(path.read_text())


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record to `path` atomically.

    Raises ValueError if the serialized record exceeds PIPE_BUF_SAFE.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    encoded = line.encode("utf-8")
    if len(encoded) > _PIPE_BUF_SAFE:
        raise ValueError(
            f"record is {len(encoded)} bytes — too large for atomic append "
            f"(PIPE_BUF safe limit is {_PIPE_BUF_SAFE}). Split it or write "
            f"to a non-shared file."
        )
    # O_APPEND ensures the kernel atomically positions and writes.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a JSONL file. Empty lines are skipped."""
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
