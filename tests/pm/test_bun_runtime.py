"""Bun is the JavaScript runtime: pm resolves node, npm and npx to it and downloads nothing."""

import os
import shutil
from pathlib import Path

import pytest

from pm import bun_runtime


@pytest.fixture
def bun(tmp_path, monkeypatch):
    directory = tmp_path / "bun-home"
    directory.mkdir()
    binary = directory / ("bun.exe" if os.name == "nt" else "bun")
    binary.write_bytes(b"")
    binary.chmod(0o755)
    monkeypatch.setenv("HERMES_BUN", str(binary))
    bun_runtime.executable.cache_clear()
    bun_runtime.version.cache_clear()
    yield binary
    bun_runtime.executable.cache_clear()
    bun_runtime.version.cache_clear()


def test_js_names_resolve_to_the_selected_bun(bun):
    from pm.install import installed_package

    for name in ("node", "npm"):
        installed = installed_package(name)
        assert installed is not None
        assert installed.binary == bun
        assert installed.path == bun.parent


def test_ensure_puts_bun_first_on_path_without_downloading(bun, monkeypatch):
    import pm
    from pm import install

    monkeypatch.setattr(install, "_install", lambda *a, **k: pytest.fail("a JavaScript runtime was downloaded"))
    runner = pm.ensure("npm", base_env={"PATH": "/usr/bin"})

    assert runner.env["PATH"].split(os.pathsep)[0] == str(bun.parent)
    assert runner.env["HERMES_BUN"] == str(bun)
    assert shutil.which("bun", path=runner.env["PATH"]) is not None


def test_env_for_composes_bun_for_the_js_names(bun):
    from pm.install import env_for

    assert env_for("node", base_env={"PATH": ""})["PATH"].split(os.pathsep)[0] == str(bun.parent)


def test_node_and_npm_packages_are_never_installable():
    from pm.registry import get_package

    for name in ("node", "npm"):
        assert get_package(name).missing_reason("win32-x64") == bun_runtime.PROVIDED
        assert get_package(name).missing_reason("linux-x64") == bun_runtime.PROVIDED


def test_a_missing_bun_names_the_remedy(tmp_path, monkeypatch):
    import pm
    from pm.package import InstallError
    from pm.install import installed_package

    monkeypatch.setenv("HERMES_BUN", str(tmp_path / "absent" / "bun"))
    bun_runtime.executable.cache_clear()
    try:
        assert installed_package("node") is None
        with pytest.raises(InstallError, match="HERMES_BUN"):
            pm.ensure("node")
    finally:
        bun_runtime.executable.cache_clear()


def test_npx_runs_as_bun_x_after_the_osv_preflight(bun, monkeypatch):
    import asyncio

    from tools import mcp_tool, osv_check

    monkeypatch.setattr(osv_check, "check_package_for_malware", lambda command, args: None)
    command, args = asyncio.run(
        mcp_tool._preflight_stdio_command("server", "npx", ["-y", "@scope/server", "--port", "7"])
    )

    assert Path(command) == bun
    assert args == ["x", "@scope/server", "--port", "7"]
