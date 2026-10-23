"""Bun is Hermes's JavaScript runtime and package manager.

The ``node``, ``npm`` and ``npx`` package names resolve to the Bun executable instead of a
downloaded Node.js and npm: Bun runs the Node programs, installs workspaces from ``bun.lock``
and executes registry packages with ``bun x``. Nothing is downloaded for these names.

``HERMES_BUN`` selects an explicit executable; otherwise ``bun`` is found on ``PATH``.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

JS_PACKAGES = frozenset({"node", "npm", "npx"})
PROVIDED = "Bun provides the JavaScript runtime and package manager; nothing is downloaded for it"
INSTALL_HINT = "Bun runs Hermes's JavaScript: install it from https://bun.sh or set HERMES_BUN"


@functools.cache
def executable() -> Path | None:
    explicit = os.environ.get("HERMES_BUN", "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    found = shutil.which("bun")
    return Path(found) if found else None


@functools.cache
def version() -> str:
    binary = executable()
    if binary is None:
        return ""
    try:
        proc = subprocess.run([str(binary), "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def env_diff() -> dict:
    """The compose_env diff that puts Bun first on PATH for child processes."""
    binary = executable()
    if binary is None:
        return {}
    return {"PATH": [str(binary.parent)], "HERMES_BUN": str(binary)}


def require() -> Path:
    binary = executable()
    if binary is None:
        from pm.package import InstallError

        raise InstallError("bun", INSTALL_HINT)
    return binary
