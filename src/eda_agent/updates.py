# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Updates from the repository, applied only when the user agrees.

eda-agent is installed from a git clone (``pip install -e .``), so an update
is a fast-forward of that clone plus the steps that make the new code run:
a reinstall when the dependencies changed, the Altium scripts copied again
when they changed, and the Altium loop relaunched to compile them. Nothing
here runs on its own. The server looks every hour, for as long as it runs,
whether the branch has moved (one ls-remote; a fetch only when it has, or
when the last fetch is a day old), and reports new commits on the first tool reply after the
check and on every app_ping and app_context; the assistant tells the user
what is new and calls ``server_update(apply=True)`` only after the user says
yes, and the MCP client's own approval prompt is a second gate. A check
that fails is reported once too, so a missing notice means nothing new.

A copy installed as a package compares its version with the one on GitHub,
and a copy installed from git the commit pip recorded with the branch's
head, and says how to upgrade it; it is never upgraded from here.

A clone with local changes, or with commits of its own, is never touched:
the update is refused and says why. EDA_AGENT_UPDATE_CHECK=0 stops the
daily check from reaching the network.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any, Optional

from .noconsole import no_window

REMOTE = "origin"
BRANCH = "main"
REPO_URL = "https://github.com/salitronic/eda-agent"
#: The version a package install is compared with.
PYPROJECT_URL = ("https://raw.githubusercontent.com/salitronic/eda-agent/"
                 f"{BRANCH}/pyproject.toml")
#: The head a package installed from git is compared with.
COMMIT_URL = f"https://api.github.com/repos/salitronic/eda-agent/commits/{BRANCH}"

#: How long a check is trusted before the next one reaches the network.
CHECK_INTERVAL_S = 24 * 3600
#: How often the running server looks whether a check is due.
RECHECK_POLL_S = 3600
#: Commit titles listed in a notice.
MAX_TITLES = 15

_GIT_TIMEOUT_S = 30
_PIP_TIMEOUT_S = 900
_STEP_TIMEOUT_S = 300

_lock = threading.Lock()
#: The last check's failure in this process, until a check succeeds.
_failure: Optional[str] = None
#: What this process has already put on a tool reply.
_delivered: set[str] = set()

#: Never wait on a credential prompt nobody can see: the repository is
#: public, and a hidden prompt held a fetch open for days.
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}


def repo_root() -> Optional[Path]:
    """The clone this package runs from, or None for an installed wheel."""
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists() and (root / "pyproject.toml").exists():
        return root
    return None


def _kill_tree(proc: subprocess.Popen) -> None:
    """End ``proc`` and whatever it started (git runs a remote helper)."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, **no_window())
    proc.kill()


def _git(root: Path, *args: str, timeout: float = _GIT_TIMEOUT_S) -> str:
    proc = subprocess.Popen(["git", "-C", str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, text=True,
                            env={**os.environ, **_GIT_ENV}, **no_window())
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    if proc.returncode != 0:
        raise RuntimeError((err or out).strip()
                           or f"git {' '.join(args)} failed")
    # rstrip only: a status line starts with a space (" M path").
    return out.rstrip()


def _cache_path() -> Path:
    from .config import get_config
    return get_config().workspace_dir / "update_check.json"


def _read_cache() -> Optional[dict]:
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(info: dict) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(info, indent=1), encoding="utf-8")
    except OSError:
        pass


def _checks_enabled() -> bool:
    return os.environ.get("EDA_AGENT_UPDATE_CHECK", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def check(force: bool = False, root: Optional[Path] = None) -> dict[str, Any]:
    """What an update would bring. Fetches from the remote unless a check
    of the same checkout is less than a day old (``force`` always fetches).
    """
    global _failure
    root = root if root is not None else repo_root()
    with _lock:
        info = _check_package(force) if root is None else _check_clone(force, root)
        _failure = info.get("error") if info.get("update_available") is None else None
    _sync_flag()
    return info


UPDATE_FLAG = "update_available.flag"


def _sync_flag() -> None:
    """Leave update_available.flag in the workspace while an update is
    out and remove it once this copy is current. The status window in
    Altium shows its update icon while the file exists."""
    try:
        path = _cache_path().with_name(UPDATE_FLAG)
        news = notice()
        if news:
            path.write_text(news.get("next_step", ""), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except Exception:  # noqa: BLE001  the flag never disturbs the server
        pass


def _check_clone(force: bool, root: Path) -> dict[str, Any]:
    try:
        head = _git(root, "rev-parse", "HEAD")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"kind": "clone", "update_available": None,
                "error": f"git is not usable here: {exc}"}
    cached = _read_cache()
    if (not force and cached and cached.get("head") == head
            and cached.get("root") == str(root)
            and time.time() - cached.get("checked_at", 0) < CHECK_INTERVAL_S
            and not _remote_moved(root, cached.get("latest"))):
        return cached
    info: dict[str, Any] = {"kind": "clone", "root": str(root), "head": head,
                            "checked_at": time.time()}
    try:
        _git(root, "fetch", "--quiet", REMOTE, BRANCH, timeout=60)
        target = f"{REMOTE}/{BRANCH}"
        info["latest"] = _git(root, "rev-parse", target)
        info["branch"] = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
        info["behind"] = int(_git(root, "rev-list", "--count", f"HEAD..{target}"))
        info["ahead"] = int(_git(root, "rev-list", "--count", f"{target}..HEAD"))
        titles = _git(root, "log", "--format=%s", f"-n{MAX_TITLES}", f"HEAD..{target}")
        info["new_commits"] = [t for t in titles.splitlines() if t]
        dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
        info["local_changes"] = [ln[3:] for ln in dirty.splitlines() if ln.strip()]
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        info["error"] = f"could not reach {REMOTE}: {exc}"
        info["update_available"] = None
        return info
    info["update_available"] = info["behind"] > 0
    _write_cache(info)
    return info


def _remote_moved(root: Path, latest: Optional[str]) -> bool:
    """The remote branch's head is not the one the last check saw. One
    ls-remote, no objects fetched; unreachable counts as not moved, so the
    last check stands."""
    try:
        out = _git(root, "ls-remote", REMOTE, f"refs/heads/{BRANCH}", timeout=30)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False
    sha = out.split()[0] if out.split() else ""
    return bool(sha) and sha != latest


def _installed_commit() -> str:
    """The commit a package was installed from, as pip recorded it for a
    ``git+`` install; "" for any other install."""
    try:
        text = metadata.distribution("eda-agent").read_text("direct_url.json") or ""
        return str(json.loads(text).get("vcs_info", {}).get("commit_id", ""))
    except (metadata.PackageNotFoundError, ValueError, AttributeError):
        return ""


def _published_commit() -> str:
    """The head of the main branch on GitHub."""
    request = urllib.request.Request(COMMIT_URL,
                                     headers={"Accept": "application/vnd.github.sha"})
    with urllib.request.urlopen(request, timeout=15) as reply:
        return reply.read(100).decode("ascii", "replace").strip()


def _published_moved(cached: dict) -> bool:
    """For a copy installed from git, the branch's head is not the one the
    last check saw; unreachable counts as not moved."""
    if not cached.get("installed_commit"):
        return False
    try:
        return _published_commit() not in ("", cached.get("latest_commit"))
    except OSError:
        return False


def _installed_version() -> str:
    try:
        return metadata.version("eda-agent")
    except metadata.PackageNotFoundError:
        return ""


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int("".join(c for c in part if c.isdigit()) or 0)
                 for part in text.strip().split("."))


def _published_version() -> str:
    """The version in pyproject.toml on GitHub's main branch."""
    with urllib.request.urlopen(PYPROJECT_URL, timeout=15) as reply:
        text = reply.read(256 * 1024).decode("utf-8", "replace")
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "version":
            return value.strip().strip("'\"")
    raise ValueError("no version in the published pyproject.toml")


def _check_package(force: bool) -> dict[str, Any]:
    installed = _installed_version()
    cached = _read_cache()
    if (not force and cached and cached.get("kind") == "package"
            and cached.get("installed") == installed
            and cached.get("installed_commit", "") == _installed_commit()
            and time.time() - cached.get("checked_at", 0) < CHECK_INTERVAL_S
            and not _published_moved(cached)):
        return cached
    info: dict[str, Any] = {
        "kind": "package", "installed": installed, "checked_at": time.time(),
        "how": (f"this copy was installed as a package, not from a clone; "
                f"update it with: python -m pip install --upgrade "
                f"git+{REPO_URL}")}
    try:
        info["latest"] = _published_version()
    except (OSError, ValueError) as exc:
        info["error"] = f"could not read {PYPROJECT_URL}: {exc}"
        info["update_available"] = None
        return info
    newer = bool(installed) and _version_key(info["latest"]) > _version_key(installed)
    # Installed from git: the commit pip recorded against the branch's head,
    # since commits land without a version bump.
    commit = _installed_commit()
    if commit and not newer:
        info["installed_commit"] = commit
        try:
            info["latest_commit"] = _published_commit()
            newer = info["latest_commit"] not in ("", commit)
        except OSError:
            pass
    info["update_available"] = newer
    _write_cache(info)
    return info


def notice() -> Optional[dict[str, Any]]:
    """The last check's news, for app_ping and app_context. Never touches
    the network, so it costs nothing on every ping."""
    cached = _read_cache()
    root = repo_root()
    if not cached or not cached.get("update_available"):
        return None
    if root is None:
        if (cached.get("kind") != "package"
                or cached.get("installed") != _installed_version()
                or cached.get("installed_commit", "") != _installed_commit()):
            return None          # upgraded since the check
        if cached.get("latest") == cached.get("installed"):
            news = (f"eda-agent {cached.get('installed')} has newer commits on "
                    f"{BRANCH} than the one this copy was installed from")
        else:
            news = (f"eda-agent {cached.get('latest')} is out and this copy is "
                    f"{cached.get('installed')}")
        return {
            "update_available": True,
            "installed": cached.get("installed"),
            "latest": cached.get("latest"),
            "next_step": f"{news}. Tell the user; {cached.get('how')}.",
        }
    if cached.get("root") != str(root):
        return None
    try:
        if _git(root, "rev-parse", "HEAD", timeout=5) != cached.get("head"):
            return None          # updated since the check
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    n = cached.get("behind", 0)
    return {
        "update_available": True,
        "commits_behind": n,
        "new_commits": cached.get("new_commits", []),
        "next_step": (f"eda-agent is {n} commit{'s' if n != 1 else ''} behind "
                      f"{REMOTE}/{BRANCH}. Tell the user what is new and ask "
                      "whether to update; only after they agree, call "
                      "server_update(apply=True)."),
    }


def notice_once() -> Optional[dict[str, Any]]:
    """News this process has not yet put on a tool reply: an update, once
    per published version, or a failed check, once per failure. Goes on
    the reply of whichever tool runs next, so a session that never pings
    still hears of it."""
    news = notice()
    if news:
        cached = _read_cache() or {}
        key = "update:" + str(cached.get("latest_commit") or cached.get("latest"))
    elif _failure:
        key = "failure:" + _failure
        news = {"update_check_failed": _failure,
                "next_step": ("Checking for a newer eda-agent failed; tell the "
                              "user. server_update() checks again.")}
    else:
        return None
    if key in _delivered:
        return None
    _delivered.add(key)
    return news


def start_background_check() -> None:
    """Check in the background when the server starts, and again whenever
    the last check is a day old for as long as the server runs, so a
    session learns about an update without waiting on the network."""
    if not _checks_enabled():
        return

    def run() -> None:
        try:
            remove_old_launchers()
        except Exception:  # noqa: BLE001  housekeeping never disturbs the server
            pass
        while True:
            try:
                check()
            except Exception:  # noqa: BLE001  a check never disturbs the server
                pass
            time.sleep(RECHECK_POLL_S)

    threading.Thread(target=run, name="eda-agent-update-check", daemon=True).start()


def _step(argv: list[str], cwd: Path, timeout: float) -> dict[str, Any]:
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                              text=True, timeout=timeout, **no_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "output": str(exc)}
    tail = (done.stdout + done.stderr).strip().splitlines()[-6:]
    return {"ok": done.returncode == 0, "output": "\n".join(tail)}


def _launchers() -> list[Path]:
    """This package's command launchers (eda-agent.exe and the like), as
    pip recorded them at install."""
    try:
        dist = metadata.distribution("eda-agent")
    except metadata.PackageNotFoundError:
        return []
    out = []
    for f in dist.files or []:
        if str(f).lower().endswith(".exe"):
            path = Path(str(dist.locate_file(f))).resolve()
            if path.is_file():
                out.append(path)
    return out


def _set_launchers_aside() -> list[tuple[Path, Path]]:
    """Rename the launchers out of pip's way: Windows will not overwrite or
    remove one a running server holds open (WinError 32), but it will
    rename it. (old, new) per launcher moved."""
    if os.name != "nt":
        return []
    stamp = int(time.time())
    moved = []
    for exe in _launchers():
        aside = exe.with_name(f"{exe.name}.old-{stamp}")
        try:
            exe.rename(aside)
            moved.append((exe, aside))
        except OSError:
            pass
    return moved


def _put_launchers_back(moved: list[tuple[Path, Path]]) -> None:
    """After a reinstall that failed: each launcher pip did not write again
    goes back where it was."""
    for exe, aside in moved:
        if not exe.exists():
            try:
                aside.rename(exe)
            except OSError:
                pass


def remove_old_launchers() -> int:
    """Delete launchers set aside by an earlier update once nothing holds
    them; one still in use stays for the next try. The number removed."""
    removed = 0
    for folder in {exe.parent for exe in _launchers()}:
        for old in folder.glob("*.exe.old-*"):
            try:
                old.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def _reinstall(root: Path) -> dict[str, Any]:
    """pip install -e of the clone, with the running launchers set aside."""
    moved = _set_launchers_aside()
    result = _step([sys.executable, "-m", "pip", "install", "-e", str(root)],
                   root, _PIP_TIMEOUT_S)
    if not result["ok"]:
        _put_launchers_back(moved)
    result["python"] = sys.executable
    return result


def _altium_running() -> bool:
    try:
        from .bridge.process_manager import AltiumProcessManager
        return bool(AltiumProcessManager().select_altium_process().candidates)
    except Exception:  # noqa: BLE001
        return False


def apply(relaunch: bool = True, root: Optional[Path] = None) -> dict[str, Any]:
    """Fast-forward the clone and make the new code run. Call only after
    the user has agreed; a clone with local work is refused untouched."""
    root = root if root is not None else repo_root()
    info = check(force=True, root=root)
    if info.get("kind") != "clone" or info.get("update_available") is None:
        return {"updated": False, **info}
    if info.get("branch") != BRANCH:
        return {"updated": False, "reason": (
            f"the clone is on branch {info.get('branch')}, not {BRANCH}; "
            f"switch to {BRANCH} first")}
    if info.get("local_changes"):
        return {"updated": False, "local_changes": info["local_changes"],
                "reason": "the clone has uncommitted changes; commit or stash "
                          "them first, nothing was touched"}
    if info.get("ahead"):
        return {"updated": False, "reason": (
            f"the clone has {info['ahead']} commit(s) that {REMOTE}/{BRANCH} "
            "does not; merge by hand, nothing was touched")}
    if not info.get("behind"):
        return {"updated": False, "up_to_date": True, "head": info.get("head")}

    old = info["head"]
    try:
        _git(root, "merge", "--ff-only", f"{REMOTE}/{BRANCH}")
        new = _git(root, "rev-parse", "HEAD")
        changed = _git(root, "diff", "--name-only", old, new).splitlines()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"updated": False, "reason": f"the fast-forward failed: {exc}"}

    steps: list[dict[str, Any]] = []
    if "pyproject.toml" in changed:
        steps.append({"step": "reinstall (dependencies changed)", **_reinstall(root)})
    scripts_changed = any(p.startswith("scripts/altium/") for p in changed)
    if scripts_changed:
        # Run as a new process: it reads the scripts just pulled, and its
        # output stays off this server's stdio.
        steps.append({"step": "install-scripts --force",
                      **_step([sys.executable, "-m", "eda_agent", "install-scripts",
                               "--force"], root, _STEP_TIMEOUT_S)})
        if relaunch and _altium_running() and steps[-1]["ok"]:
            steps.append({"step": "relaunch --restart",
                          **_step([sys.executable, "-m", "eda_agent", "relaunch",
                                   "--restart"], root, _STEP_TIMEOUT_S)})
    _write_cache({**info, "head": new, "behind": 0, "update_available": False,
                  "new_commits": [], "checked_at": time.time()})
    _sync_flag()
    failed = [s["step"] for s in steps if not s["ok"]]
    out: dict[str, Any] = {
        "updated": True, "from": old[:7], "to": new[:7],
        "commits": info.get("new_commits", []), "steps": steps,
        "next_step": ("Reconnect this MCP server (/mcp in Claude Code, or "
                      "restart the client) to run the new Python."
                      + ("" if not scripts_changed or failed else
                         " Altium already runs the new scripts.")),
    }
    if scripts_changed and not relaunch:
        out["next_step"] += " Then run: eda-agent relaunch --restart."
    if failed:
        out["failed_steps"] = failed
    return out
