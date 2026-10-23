"""Optional dependency refreshes run in PM's repository with its installed tools."""
from argparse import Namespace
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

from pm import cli, paths, registry
from pm.lock import Facts, Lockfile
from pm.package import Package
from pm.packages import Nodejs, Npm, Python, Uv
from pm.store import current_target, tree_digest


class ManualFixture(Package):
    name = 'manual-fixture'


def project(tmp_path, monkeypatch):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'pyproject.toml').write_text(
        '[project]\nname="update-proof"\nversion="1"\nrequires-python=">=3.14"\n'
        '[tool.uv]\npackage=false\n', encoding='utf-8')
    caller = tmp_path / 'unrelated-project'
    caller.mkdir()
    (caller / 'pyproject.toml').write_text('not a project', encoding='utf-8')
    lock = Lockfile(repo / 'pm/lock.json')
    lock.set_pin('manual-fixture', '1', {})
    lock.save()
    monkeypatch.setitem(registry._packages, 'manual-fixture', ManualFixture())
    monkeypatch.setattr(paths, 'repo_root', lambda: repo)
    monkeypatch.setattr(cli, 'repo_root', lambda: repo)
    monkeypatch.setattr(paths, 'lockfile_path', lambda: lock.path)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('HERMES_RUNTIME_DIR', str(tmp_path / 'store'))
    monkeypatch.chdir(caller)

    run_live = cli._run_live

    def run_owned(cmd, *, cwd, env, **kwargs):
        assert Path(cwd).resolve() == repo.resolve(), 'refuse a dependency command outside the temporary project'
        assert Path(cmd[0]).resolve().is_relative_to(tmp_path.resolve()), 'refuse a tool outside the temporary store'
        return run_live(cmd, cwd=cwd, env=env, **kwargs)

    monkeypatch.setattr(cli, '_run_live', run_owned)
    return repo, caller, lock


def _shared_prefix(root: Path, *, limit: int = 50_000) -> str:
    """Why ``root`` cannot be digested as a standalone Python package entry, or ''.

    The fixture records the host interpreter's prefix as PM's Python entry,
    which means reading every file under it. A distro prefix (/usr, /usr/local,
    Homebrew) holds unrelated, sometimes unreadable, trees; a standalone or
    self-contained build holds one interpreter.
    """
    count = 0
    for directory, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(directory) / name
            count += 1
            if count > limit:
                return f'more than {limit} files'
            if not path.is_symlink() and not os.access(path, os.R_OK):
                return f'{path} is unreadable'
    return ''


@pytest.mark.platforms('windows', 'posix')
def test_uv_refresh_uses_real_installed_tool_and_only_the_owned_project(tmp_path, monkeypatch, capsys):
    repo, caller, lock = project(tmp_path, monkeypatch)
    uv = Path(shutil.which('uv') or pytest.fail('canonical test environment requires uv'))
    runtime = tmp_path / 'store'
    managed = runtime / 'uv-fixture' / uv.name
    managed.parent.mkdir(parents=True)
    shutil.copy2(uv, managed)
    python = Path(sys._base_executable).resolve()
    python_root = python.parent if os.name == 'nt' else python.parents[1]
    shared = _shared_prefix(python_root)
    if shared:
        pytest.skip(f"host Python prefix {python_root} is not self-contained: {shared}")
    target = current_target()
    facts = Facts(runtime / 'facts.json')
    for name, package, entry in [('uv', Uv(), managed.parent), ('python', Python(), python_root)]:
        monkeypatch.setitem(registry._packages, name, package)
        lock.set_pin(name, 'fixture', {target: {'url': f'https://example.invalid/{name}', 'sha256': '1' * 64}})
        facts.record(name, 'fixture', str(entry), package.env(entry, target), runtime,
                     target=target, artifacts=['1' * 64], digest=tree_digest(entry))
    lock.save()
    before_lock = lock.path.read_bytes()
    parent_env = dict(os.environ)
    syncs = []
    ensure = importlib.import_module('pm.install')
    monkeypatch.setattr(ensure, 'sync_venv', lambda **kw: syncs.append(kw))
    args = Namespace(names=['manual-fixture'], target=None, skip_target=[], check=True, uv=True, npm=False, termux=False)
    assert cli.cmd_update(args) == 0
    assert not (repo / 'uv.lock').exists()
    assert not syncs
    check_output = capsys.readouterr().out
    args.check = False
    assert cli.cmd_update(args) == 0
    assert 'uv lock --upgrade' in check_output
    assert (repo / 'uv.lock').is_file()
    assert not (caller / 'uv.lock').exists()
    assert syncs == [{'explicit': True}]
    assert lock.path.read_bytes() == before_lock
    assert dict(os.environ) == parent_env

    original = (repo / 'uv.lock').read_bytes()
    syncs.clear()
    (repo / 'pyproject.toml').write_text('invalid project [', encoding='utf-8')
    assert cli.cmd_update(args) == 1
    assert (repo / 'uv.lock').read_bytes() == original and not syncs
    assert 'Python lock refresh failed' in capsys.readouterr().out

    managed.unlink()
    assert cli.cmd_update(args) == 1
    assert (repo / 'uv.lock').read_bytes() == original and not syncs
    assert 'Python lock refresh failed' in capsys.readouterr().out

