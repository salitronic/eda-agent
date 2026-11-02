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


def test_a_package_install_is_told_how(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "repo_root", lambda: None)
    monkeypatch.setattr(updates, "_cache_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(updates, "_installed_version", lambda: "0.8.0")
    monkeypatch.setattr(updates, "_published_version", lambda: "0.8.0")
    info = updates.check(force=True)
    assert info["kind"] == "package" and "pip install --upgrade" in info["how"]
    assert info["update_available"] is False and updates.notice() is None
    assert updates.apply()["updated"] is False


def test_a_package_install_hears_of_a_newer_version(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "repo_root", lambda: None)
    monkeypatch.setattr(updates, "_cache_path", lambda: tmp_path / "c.json")
    monkeypatch.setattr(updates, "_installed_version", lambda: "0.8.1")
    monkeypatch.setattr(updates, "_published_version", lambda: "0.10.0")
    assert updates.check(force=True)["update_available"] is True
    news = updates.notice()
    assert news["latest"] == "0.10.0" and "pip install --upgrade" in news["next_step"]
    # Upgraded since the check: nothing to say.
    monkeypatch.setattr(updates, "_installed_version", lambda: "0.10.0")
    assert updates.notice() is None


@pytest.fixture()
def fresh_delivery(monkeypatch):
    monkeypatch.setattr(updates, "_delivered", set())
    monkeypatch.setattr(updates, "_failure", None)


def test_the_news_goes_on_one_reply_per_published_version(clones, fresh_delivery):
    pub, _, _ = clones
    _publish(pub, "README.md", "hi", "Say hello")
    updates.check(force=True)
    first = updates.notice_once()
    assert first["commits_behind"] == 1 and "server_update" in first["next_step"]
    assert updates.notice_once() is None
    # ping and context still say it every time
    assert updates.notice()["commits_behind"] == 1
    _publish(pub, "README.md", "hi again", "Say it again")
    updates.check(force=True)
    assert updates.notice_once()["commits_behind"] == 2


def test_a_failed_check_is_said_once(clones, fresh_delivery, monkeypatch):
    _, user, _ = clones
    _git(user, "remote", "set-url", "origin", str(user.parent / "gone.git"))
    info = updates.check(force=True)
    assert info["update_available"] is None and info["error"]
    news = updates.notice_once()
    assert news["update_check_failed"] == info["error"]
    assert updates.notice_once() is None
    # a check that works again clears it
    _git(user, "remote", "set-url", "origin", str(user.parent / "upstream.git"))
    updates.check(force=True)
    assert updates._failure is None


def test_git_never_waits_on_a_prompt(clones):
    _, user, _ = clones
    seen = updates._git(user, "-c", "alias.p=!echo $GIT_TERMINAL_PROMPT", "p")
    assert seen == "0"


def test_a_hung_git_is_ended_with_what_it_started(clones):
    import time as _time
    _, user, _ = clones
    started = _time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        updates._git(user, "-c", "alias.h=!sleep 30", "h", timeout=1)
    # the shell and its sleep went too: left running, they hold the pipes
    # and the reader waits out its own 5 s grace
    assert _time.monotonic() - started < 5


def test_the_server_checks_again_while_it_runs(monkeypatch):
    import threading
    checks, stop = [], threading.Event()
    monkeypatch.setattr(updates, "_checks_enabled", lambda: True)
    monkeypatch.setattr(updates, "check", lambda: checks.append(1))

    def sleep(_):
        if len(checks) >= 3:
            stop.set()
            threading.Event().wait()         # park the thread for good
    monkeypatch.setattr(updates.time, "sleep", sleep)
    updates.start_background_check()
    assert stop.wait(10) and len(checks) == 3


def test_any_tool_reply_carries_the_news():
    from mcp.types import TextContent
    from eda_agent.server import add_update_notice
    news = {"update_available": True}
    blocks = [TextContent(type="text", text='{"ok": true}')]
    out, structured = add_update_notice((blocks, {"ok": True}), news)
    assert structured == {"ok": True, "update": news}
    assert '"update"' in out[0].text and len(out) == 1
    # a reply that is not an object gets a block of its own
    out = add_update_notice([TextContent(type="text", text="plain")], news)
    assert out[0].text == "plain" and '"update"' in out[1].text
    # a reply that already says update keeps its own
    _, structured = add_update_notice(([], {"update": "mine"}), news)
    assert structured["update"] == "mine"


def test_the_server_puts_the_news_on_a_real_tool_call(monkeypatch):
    from eda_agent.server import mcp
    sent = iter([{"update_available": True}, None])
    monkeypatch.setattr(updates, "notice_once", lambda: next(sent))
    first = asyncio.run(mcp.call_tool("tool_guide", {"task": "place a via"}))
    second = asyncio.run(mcp.call_tool("tool_guide", {"task": "place a via"}))
    assert first[1]["update"] == {"update_available": True}
    assert '"update"' in first[0][0].text
    assert "update" not in second[1]


def test_ping_names_the_python_without_screen_capture(monkeypatch):
    from eda_agent.tools import application
    from eda_agent.ui import ocr
    monkeypatch.setattr(application.os, "name", "nt")
    monkeypatch.setattr(ocr, "available", lambda: False)
    text = application._screen_capture_warning()
    assert application.sys.executable in text and "Pillow" in text
    monkeypatch.setattr(ocr, "available", lambda: True)
    assert application._screen_capture_warning() is None


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
