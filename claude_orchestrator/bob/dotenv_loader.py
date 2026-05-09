"""Auto-load .env files into the process environment.

Loading precedence (highest to lowest priority):
  1. Process environment (anything already set is preserved)
  2. <project_root>/.env  (project-specific overrides)
  3. <cwd>/.env           (where bob is invoked)
  4. ~/.bob/.env          (user-level — typically API keys)

python-dotenv's load_dotenv(override=False) treats existing env vars as
authoritative, and the FIRST file to set a given key wins among the files.
So the load order in this function is the precedence order.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def _user_env_path() -> Path:
    """Path to the user-level .env. Honors BOB_USER_ENV override for tests."""
    override = os.environ.get("BOB_USER_ENV")
    if override:
        return Path(override)
    return Path.home() / ".bob" / ".env"


def load_env_files(*, project_root: Path | None = None, cwd: Path | None = None) -> None:
    """Auto-load .env from project_root, cwd, and ~/.bob/.env.

    Existing env vars take priority. Among files, the more-specific source wins:
    project > cwd > user-level.
    """
    # Order matters because load_dotenv(override=False) means the FIRST file
    # to set a key wins among files. Most-specific first.
    if project_root is not None:
        env_path = project_root / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    if cwd is not None:
        env_path = cwd / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

    user_env = _user_env_path()
    if user_env.exists():
        load_dotenv(user_env, override=False)
