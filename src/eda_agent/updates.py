# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Updates from the repository, applied only when the user agrees.

eda-agent is installed from a git clone (``pip install -e .``), so an update
is a fast-forward of that clone plus the steps that make the new code run:
a reinstall when the dependencies changed, the Altium scripts copied again
when they changed, and the Altium loop relaunched to compile them. Nothing
here runs on its own. The server checks for new commits at most once a day
and reports them through app_ping and app_context; the assistant tells the
user what is new and calls ``server_update(apply=True)`` only after the user
says yes, and the MCP client's own approval prompt is a second gate.

A clone with local changes, or with commits of its own, is never touched:
the update is refused and says why. EDA_AGENT_UPDATE_CHECK=0 stops the
daily check from reaching the network.

EXCEPT AT STARTUP: ``update_on_start`` applies an update before the server
serves, so a session always starts on the latest code and scripts. Set
EDA_AGENT_AUTO_UPDATE=0 (or EDA_AGENT_UPDATE_CHECK=0) to go back to asking.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .noconsole import no_window

REMOTE = "origin"
BRANCH = "main"
REPO_URL = "https://github.com/salitronic/eda-agent"

#: How long a check is trusted before the next one reaches the network.
CHECK_INTERVAL_S = 24 * 3600
#: Commit titles listed in a notice.
MAX_TITLES = 15

_GIT_TIMEOUT_S = 30
_PIP_TIMEOUT_S = 900
_STEP_TIMEOUT_S = 300

_lock = threading.Lock()


def repo_root() -> Optional[Path]:
    """The clone this package runs from, or None for an installed wheel."""
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists() and (root / "pyproject.toml").exists():
        return root
    return None


def _git(root: Path, *args: str, timeout: float = _GIT_TIMEOUT_S) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, timeout=timeout, **no_window())
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()
                           or f"git {' '.join(args)} failed")
    # rstrip only: a status line starts with a space (" M path").
    return done.stdout.rstrip()


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
    root = root if root is not None else repo_root()
    if root is None:
        return {"kind": "package", "update_available": None,
                "how": (f"this copy was installed as a package, not from a "
                        f"clone; update it with: python -m pip install "
                        f"--upgrade git+{REPO_URL}")}
    with _lock:
        try:
            head = _git(root, "rev-parse", "HEAD")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            return {"kind": "clone", "update_available": None,
                    "error": f"git is not usable here: {exc}"}
        cached = _read_cache()
        if (not force and cached and cached.get("head") == head
                and cached.get("root") == str(root)
                and time.time() - cached.get("checked_at", 0) < CHECK_INTERVAL_S):
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


def notice() -> Optional[dict[str, Any]]:
    """The last check's news, for app_ping and app_context. Never touches
    the network, so it costs nothing on every ping."""
    cached = _read_cache()
    root = repo_root()
    if not cached or not cached.get("update_available") or root is None:
        return None
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


def update_on_start() -> bool:
    """Pull and apply any update before the server starts serving.

    True when new code was pulled, so the caller re-runs itself on it.
    Never fails the startup: a refused or failed update is reported on
    stderr and the server serves the code it has. Local work refuses it
    exactly as ``apply`` does.
    """
    if (os.environ.get("EDA_AGENT_AUTO_UPDATE", "1").strip().lower()
            in ("0", "false", "no", "off")
            or not _checks_enabled() or repo_root() is None):
        return False
    try:
        out = apply()
    except Exception as exc:  # noqa: BLE001  never blocks the server
        print(f"eda-agent: update at startup failed: {exc}", file=sys.stderr)
        return False
    if not out.get("updated"):
        reason = out.get("reason") or out.get("error")
        if reason:
            print(f"eda-agent: not updated at startup: {reason}", file=sys.stderr)
        return False
    print(f"eda-agent: updated {out['from']} -> {out['to']} at startup"
          + (f"; failed: {', '.join(out['failed_steps'])}"
             if out.get("failed_steps") else ""), file=sys.stderr)
    return True


def start_background_check() -> None:
    """Check once in the background when the server starts, so a new
    session learns about an update without waiting on the network."""
    if not _checks_enabled() or repo_root() is None:
        return

    def run() -> None:
        try:
            check()
        except Exception:  # noqa: BLE001  a check never disturbs the server
            pass

    threading.Thread(target=run, name="eda-agent-update-check", daemon=True).start()


def _step(argv: list[str], cwd: Path, timeout: float) -> dict[str, Any]:
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                              text=True, timeout=timeout, **no_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "output": str(exc)}
    tail = (done.stdout + done.stderr).strip().splitlines()[-6:]
    return {"ok": done.returncode == 0, "output": "\n".join(tail)}


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
        steps.append({"step": "reinstall (dependencies changed)",
                      **_step([sys.executable, "-m", "pip", "install", "-e", str(root)],
                              root, _PIP_TIMEOUT_S)})
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
