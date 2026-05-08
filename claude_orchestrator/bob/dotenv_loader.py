"""Auto-load .env files into the process environment.

Loading precedence (highest to lowest priority):
  1. Process environment (anything already set is preserved)
  2. <project_root>/.env if project_root is provided
  3. <cwd>/.env

python-dotenv's load_dotenv(override=False) treats existing env vars as
authoritative — exactly what we want.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_env_files(*, project_root: Path | None = None, cwd: Path | None = None) -> None:
    """Auto-load .env from project_root and/or cwd. Existing env vars take priority."""
    # Process env always wins via override=False.
    # Order: cwd first (less specific), then project (more specific overrides cwd).
    # But because override=False, the FIRST file to set a key wins among files.
    # So we load the more-specific (project) FIRST so it wins over cwd.
    if project_root is not None:
        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    if cwd is not None:
        env_path = cwd / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)
