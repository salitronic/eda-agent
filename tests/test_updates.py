# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Updates are found, reported, and installed only when asked.

Run against real git repositories in a temporary folder: a bare upstream,
a publisher clone that pushes to it, and the user's clone that falls
behind. The reinstall, script copy and relaunch steps are recorded rather
than run.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from eda_agent import cli, updates

pytestmark = pytest.mark.skipif(shutil.which("git") is None,
                                reason="needs git")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, rel: str, text: str, title: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", title)


@pytest.fixture()
def clones(tmp_path, monkeypatch):
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.com"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.com")):
        monkeypatch.setenv(k, v)
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(upstream)], check=True)
    pub = tmp_path / "pub"
    subprocess.run(["git", "clone", "-q", str(upstream), str(pub)], check=True,
                   capture_output=True)
    _git(pub, "checkout", "-q", "-b", "main")
    _commit(pub, "pyproject.toml", "[project]\nname='x'\n", "Start")
    _commit(pub, "scripts/altium/Main.pas", "v1", "Add the scripts")
    _git(pub, "push", "-q", "origin", "main")
    user = tmp_path / "user"
    subprocess.run(["git", "clone", "-q", str(upstream), str(user)], check=True,
                   capture_output=True)
    cache = tmp_path / "update_check.json"
    monkeypatch.setattr(updates, "_cache_path", lambda: cache)
    monkeypatch.setattr(updates, "repo_root", lambda: user)
    steps = []

    def fake_step(argv, cwd, timeout):
        steps.append(argv[3:] if argv[1:3] == ["-m", "eda_agent"] else argv[1:4])
        return {"ok": True, "output": ""}

    monkeypatch.setattr(updates, "_step", fake_step)
    monkeypatch.setattr(updates, "_altium_running", lambda: True)
    return pub, user, steps


def _publish(pub: Path, rel: str, text: str, title: str) -> None:
    _commit(pub, rel, text, title)
    _git(pub, "push", "-q", "origin", "main")


def test_an_up_to_date_clone_reports_nothing(clones):
    _, user, _ = clones
    info = updates.check(force=True)
    assert info["update_available"] is False and info["behind"] == 0
    assert updates.notice() is None


def test_new_commits_are_found_listed_and_reported(clones):
    pub, user, _ = clones
    _publish(pub, "scripts/altium/Main.pas", "v2", "Fix the bridge")
    _publish(pub, "README.md", "hi", "Say hello")
    info = updates.check(force=True)
    assert info["update_available"] is True and info["behind"] == 2
    assert info["new_commits"] == ["Say hello", "Fix the bridge"]
    news = updates.notice()
    assert news["commits_behind"] == 2
    assert "ask" in news["next_step"] and "server_update(apply=True)" in news["next_step"]
    # The check found the news, and nothing in the clone moved.
    assert (user / "scripts/altium/Main.pas").read_text() == "v1"


def test_a_recent_check_is_reused_without_the_network(clones, monkeypatch):
    _, _, _ = clones
    first = updates.check(force=True)
    fetched = []
    real = updates._git
    monkeypatch.setattr(updates, "_git", lambda root, *a, **k: (
        fetched.append(a[0]), real(root, *a, **k))[1])
    again = updates.check()
    assert again["checked_at"] == first["checked_at"]
    assert "fetch" not in fetched


def test_apply_fast_forwards_and_runs_only_the_needed_steps(clones):
    pub, user, steps = clones
    _publish(pub, "scripts/altium/Main.pas", "v2", "Fix the bridge")
    out = updates.apply()
    assert out["updated"] is True and out["commits"] == ["Fix the bridge"]
    assert (user / "scripts/altium/Main.pas").read_text() == "v2"
    # Scripts changed, dependencies did not: copy and relaunch, no pip.
    assert steps == [["install-scripts", "--force"], ["relaunch", "--restart"]]
    assert "Reconnect" in out["next_step"]
    assert updates.notice() is None


def test_changed_dependencies_reinstall(clones):
    pub, _, steps = clones
    _publish(pub, "pyproject.toml", "[project]\nname='x'\nversion='2'\n", "Bump")
    assert updates.apply()["updated"] is True
    assert steps == [["-m", "pip", "install"]]


def test_no_relaunch_leaves_the_loop_and_says_so(clones):
    pub, _, steps = clones
    _publish(pub, "scripts/altium/Main.pas", "v2", "Fix the bridge")
    out = updates.apply(relaunch=False)
    assert steps == [["install-scripts", "--force"]]
    assert "relaunch --restart" in out["next_step"]


@pytest.mark.parametrize("local", ["edit", "commit", "branch"])
def test_local_work_is_never_touched(clones, local):
    pub, user, steps = clones
    _publish(pub, "scripts/altium/Main.pas", "v2", "Fix the bridge")
    if local == "edit":
        (user / "scripts/altium/Main.pas").write_text("mine")
    elif local == "commit":
        _commit(user, "notes.txt", "mine", "My change")
    else:
        _git(user, "checkout", "-q", "-b", "experiment")
    head = _git(user, "rev-parse", "HEAD")
    out = updates.apply()
    assert out["updated"] is False and out["reason"]
    assert _git(user, "rev-parse", "HEAD") == head and steps == []
    if local == "edit":
        assert (user / "scripts/altium/Main.pas").read_text() == "mine"
        assert out["local_changes"] == ["scripts/altium/Main.pas"]


def test_a_package_install_is_told_how(monkeypatch):
    monkeypatch.setattr(updates, "repo_root", lambda: None)
    info = updates.check(force=True)
    assert info["kind"] == "package" and "pip install --upgrade" in info["how"]
    assert updates.apply()["updated"] is False


def test_ping_and_context_carry_the_news(monkeypatch):
    from eda_agent.tools import application

    async def reply():
        return {"success": True}

    monkeypatch.setattr(updates, "notice", lambda: {"update_available": True})
    assert asyncio.run(application._reports_update(reply)()) == {
        "success": True, "update": {"update_available": True}}
    monkeypatch.setattr(updates, "notice", lambda: None)
    assert asyncio.run(application._reports_update(reply)()) == {"success": True}


def test_the_tool_checks_unless_told_to_apply(monkeypatch):
    from eda_agent.tools.update import register_update_tools
    tools = {}

    class _Mcp:
        def tool(self):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    register_update_tools(_Mcp())
    calls = []
    monkeypatch.setattr(updates, "check", lambda force: calls.append(("check", force)) or {})
    monkeypatch.setattr(updates, "apply", lambda relaunch: calls.append(("apply", relaunch)) or {})
    asyncio.run(tools["server_update"]())
    asyncio.run(tools["server_update"](apply=True, relaunch=False))
    assert calls == [("check", True), ("apply", False)]


def test_the_instructions_say_to_ask_first():
    from eda_agent.server import build_server_instructions
    text = build_server_instructions()
    assert "ask; call server_update(apply=True) only" in text.replace("\n", " ")


def test_the_cli_shows_and_asks(clones, capsys, monkeypatch):
    pub, user, steps = clones
    _publish(pub, "README.md", "hi", "Say hello")
    assert cli.cmd_update(check_only=True) == 0
    assert "Say hello" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.cmd_update() == 1           # no terminal and no --yes
    assert (user / "README.md").exists() is False
    assert cli.cmd_update(assume_yes=True) == 0
    assert (user / "README.md").read_text() == "hi"


def test_startup_pulls_and_deploys_the_scripts(clones, monkeypatch):
    pub, user, steps = clones
    monkeypatch.setenv("EDA_AGENT_AUTO_UPDATE", "1")
    _publish(pub, "scripts/altium/Main.pas", "v2", "Fix the bridge")
    assert updates.update_on_start() is True
    assert (user / "scripts/altium/Main.pas").read_text() == "v2"
    assert steps == [["install-scripts", "--force"], ["relaunch", "--restart"]]
    assert updates.update_on_start() is False          # nothing new now


def test_startup_leaves_local_work_and_can_be_turned_off(clones, monkeypatch):
    pub, user, steps = clones
    _publish(pub, "README.md", "hi", "Say hello")
    monkeypatch.setenv("EDA_AGENT_AUTO_UPDATE", "0")
    assert updates.update_on_start() is False
    monkeypatch.setenv("EDA_AGENT_AUTO_UPDATE", "1")
    (user / "pyproject.toml").write_text("mine", encoding="utf-8")
    assert updates.update_on_start() is False
    assert not (user / "README.md").exists() and steps == []


def test_main_serves_from_a_child_after_an_update(monkeypatch):
    import subprocess
    import eda_agent.server as server
    monkeypatch.delenv("_EDA_AGENT_UPDATED", raising=False)
    monkeypatch.delenv("EDA_AGENT_RELOAD", raising=False)
    monkeypatch.setattr("sys.argv", ["eda-agent"])
    monkeypatch.setattr(updates, "update_on_start", lambda: True)
    monkeypatch.setattr(subprocess, "call", lambda argv, **k: 5)
    monkeypatch.setattr(server, "serve_mcp", lambda **k: 7)
    assert server.main() == 5
    # The child sees the marker, so it serves rather than pulling again.
    assert server.main() == 7
