# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
r"""CLI subcommands for eda-agent.

Exposed via the `eda-agent` console script. The default subcommand
(invoked when no arguments are given) is `serve`, which starts the
MCP server on stdio -- this is what MCP-compatible clients call.

Other subcommands:
  - scripts-path      : print the path to the bundled DelphiScript sources
  - install-scripts   : copy bundled scripts to a user-chosen directory
                        (default: %USERPROFILE%\EDA Agent\scripts\)
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------
# Package-resource helpers
# --------------------------------------------------------------------------

def get_package_root() -> Path:
    """Return the filesystem path to the installed `eda_agent` package."""
    import eda_agent
    return Path(eda_agent.__file__).resolve().parent


def get_bundled_scripts_path() -> Path:
    """Return the path to the bundled DelphiScript sources.

    In a regular (wheel) install this is `eda_agent/scripts/` inside
    the installed package -- populated by `force-include` at wheel build
    time (see pyproject.toml).

    In an editable install (`pip install -e .`), the `force-include`
    mapping is not honoured; the loader falls back to `<repo>/scripts/altium/`
    by walking up from the package's source location.
    """
    bundled = get_package_root() / "scripts"
    if bundled.exists() and (bundled / "Altium_API.PrjScr").exists():
        return bundled

    # Editable-install fallback.
    pkg_parent = get_package_root().parent
    repo_root = pkg_parent.parent
    dev_scripts = repo_root / "scripts" / "altium"
    if dev_scripts.exists() and (dev_scripts / "Altium_API.PrjScr").exists():
        return dev_scripts

    return bundled


# --------------------------------------------------------------------------
# User-profile paths
# --------------------------------------------------------------------------

#: Where an Altium install keeps its Global Projects list (the Scripting
#: System preference page), under HKCU\Software\Altium\<install>; one
#: value per project. Read on AD 26.11.
_GLOBAL_PROJECTS = r"DesignExplorer\Preferences\ScriptingSystem\Global Projects"


def registered_script_projects() -> list[dict]:
    """Every Altium_API.PrjScr an Altium install loads at startup.

    [{"install": registry key name, "path": project file, "exists"}], in
    registry order; [] off Windows or when no install registers one.
    The folder that matters is the one Altium loads, and it need not be
    the default this package installs to.
    """
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found: list[dict] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Altium")
    except OSError:
        return []
    with root:
        index = 0
        while True:
            try:
                install = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                key = winreg.OpenKey(root, install + "\\" + _GLOBAL_PROJECTS)
            except OSError:
                continue
            with key:
                item = 0
                while True:
                    try:
                        _name, value, _kind = winreg.EnumValue(key, item)
                    except OSError:
                        break
                    item += 1
                    if (isinstance(value, str)
                            and Path(value.strip()).name.lower()
                            == "altium_api.prjscr"):
                        path = Path(value.strip())
                        found.append({"install": install, "path": str(path),
                                      "exists": path.is_file()})
    return found


def get_registered_scripts_dirs() -> list[Path]:
    """The distinct folders Altium loads the script project from."""
    folders: list[Path] = []
    seen: set[str] = set()
    for entry in registered_script_projects():
        folder = Path(entry["path"]).parent
        key = os.path.normcase(str(folder))
        if key not in seen:
            seen.add(key)
            folders.append(folder)
    return folders


def get_fallback_scripts_dest() -> Path:
    """%USERPROFILE%\\EDA Agent\\scripts\\, when Altium names no folder."""
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "EDA Agent" / "scripts"
    return Path.home() / "EDA Agent" / "scripts"


def get_default_scripts_dest() -> Path:
    """Where the scripts belong: the folder Altium loads them from when one
    is registered, else %USERPROFILE%\\EDA Agent\\scripts\\.

    With several installs registering different folders the first is
    returned; `install-scripts` itself deploys to all of them.
    """
    registered = get_registered_scripts_dirs()
    return registered[0] if registered else get_fallback_scripts_dest()


# --------------------------------------------------------------------------
# scripts-path
# --------------------------------------------------------------------------

def cmd_scripts_path() -> int:
    """Print the path to the bundled DelphiScript sources."""
    path = get_bundled_scripts_path()
    if not path.exists():
        print(f"ERROR: bundled scripts directory not found: {path}", file=sys.stderr)
        print("Try reinstalling: pip install --force-reinstall eda-agent", file=sys.stderr)
        return 1
    print(str(path))
    return 0


# --------------------------------------------------------------------------
# easyeda-extension
# --------------------------------------------------------------------------

def cmd_easyeda_extension(action: str, out: Optional[str] = None) -> int:
    """Print the EasyEDA extension source folder, or build its .eext."""
    from . import easyeda_extension

    if action == "path":
        folder = easyeda_extension.source_dir()
        if folder is None:
            print("ERROR: no EasyEDA extension source in this install",
                  file=sys.stderr)
            return 1
        print(str(folder))
        return 0
    code, message = easyeda_extension.build(out)
    print(message, file=sys.stdout if code == 0 else sys.stderr)
    return code


# --------------------------------------------------------------------------
# relaunch
# --------------------------------------------------------------------------

def relaunch_command(exe: str, prjscr: str) -> list[str]:
    """The command that asks a running Altium to start the bridge script.

    A second X2.exe launched with -R hands the process to the instance
    already running, the way Altium's own menu entries run a script.
    Passed as a list, so the | needs no shell escaping.
    """
    return [exe, f"-RScriptingSystem:RunScript(ProjectName={prjscr}"
                 f"|ProcName=Dispatcher.pas>StartMCPServer)"]


#: How long --restart waits for the running loop to finish its command and
#: stop, while a handler is visibly running (a progress file), and while
#: nothing is (a loop that is not running never takes the stop file).
RESTART_STOP_WAIT_S = 300.0
RESTART_IDLE_STOP_WAIT_S = 15.0
#: The same wait when nothing answered a ping and the halted script was
#: stopped: no loop is left to take the stop file.
RESTART_DEAD_STOP_WAIT_S = 2.0
#: How long --restart waits for the new loop to answer.
RESTART_PING_WAIT_S = 90.0
#: How long a dialog that is not a script error box may block the new loop
#: before --restart stops waiting and says which dialog it is.
RESTART_DIALOG_GIVE_UP_S = 10.0


def _wait_for_loop_to_stop(workspace: Path, trust_progress: bool = True,
                           idle_wait: Optional[float] = None) -> str:
    """Drop the stop file and wait for the loop to take it.

    The loop deletes it between commands, so a command in flight always
    finishes first. Returns "stopped", or "not running" when nothing took
    it and no handler was running (the file is then removed, so a later
    start does not stop at once).

    ``trust_progress`` False ignores progress files: a handler that halted
    in the debugger leaves its progress file behind, and read as a command
    still running it held the restart for the whole long wait.
    """
    import time

    stop = workspace / "stop"
    stop.write_text("", encoding="ascii")
    start = time.monotonic()
    while stop.exists():
        waited = time.monotonic() - start
        busy = trust_progress and any(workspace.glob("progress_*.json"))
        if waited > RESTART_STOP_WAIT_S or (
                not busy and waited > (RESTART_IDLE_STOP_WAIT_S
                                       if idle_wait is None else idle_wait)):
            try:
                stop.unlink()
            except OSError:
                pass
            return "not running"
        time.sleep(0.2)
    return "stopped"


def _stop_halted_script(command: list) -> None:
    """Ask Altium to stop a script parked in the debugger.

    A handler that hits an undeclared identifier halts the loop but leaves
    the script running in the engine, and the new loop then fails with
    "Another script executing now". Run > Stop (EditScript:Stop, as the
    install's EditScript.rcs names it) frees the engine; with nothing
    running it does nothing. Measured 2026-10-25: after it, StartMCPServer
    came up at once.
    """
    import subprocess

    try:
        subprocess.Popen([command[0], "-REditScript:Stop"],
                         close_fds=True).wait(timeout=30)
    except Exception:                            # noqa: BLE001
        pass


def _blocked_by_dialog(pid: int, consequence: str) -> bool:
    """Say so and return True when Altium sits behind a blocking dialog.

    Only a window that BLOCKS the loop counts: a hover preview
    (FormPreviewPopup) is listed as a dialog and blocks nothing. A plain
    relaunch used to skip this: with "Unsaved Changes" up, the ping failed,
    so relaunch started the script, and X2.EXE -R went to a NEW Altium
    instead of the busy one.
    """
    from .ui import dialog_report, windows

    if not windows.available():
        return False
    report = dialog_report.report(pid)
    if report.get("blocked"):
        print(f"ERROR: Altium has a dialog open, so {consequence}: "
              f"{report.get('summary')}. Close it first.", file=sys.stderr)
        return True
    return False


def _restart(command: list, frames_pid: int) -> int:
    """Stop the running loop and start a new one, with other sessions'
    calls waiting through it instead of failing.

    maintenance.json tells every eda-agent server that this is a restart
    on purpose: a call that is not taken keeps waiting rather than
    reporting a dead loop, and the loop keeps the requests on disk across
    the stop and the start instead of purging them. It is removed when the
    new loop answers, or when this gives up.
    """
    import json
    import os
    import subprocess
    import time

    from .bridge.altium_bridge import MAINTENANCE_FILE
    from .config import get_config
    from .diag.doctor import _ping_altium
    from .ui import windows

    from .bridge.script_crash import clear_error_boxes

    def clear(stage: str) -> str:
        """Clear script error boxes; what still blocks, "" for nothing.

        A modal holds the stop, the start and the ping alike, and waiting
        one out took 80 s behind a box nobody was pressing (2026-10-30).
        """
        found = clear_error_boxes(frames_pid)
        for box in found["cleared"]:
            said = "; ".join(box["said"][:2]) if box["said"] else "no readable text"
            print(f"{stage}: Altium showed {box['title']!r} ({said}); "
                  f"OK {box['pressed']}.")
        return found["blocking"]

    from .bridge.altium_bridge import _trace_log

    began = time.monotonic()

    def phase(name: str) -> None:
        """Say how far the restart is, with the time, on the output and in
        the bridge trace: a recovery took 145 s (2026-10-30) and nothing
        said where."""
        line = f"[{time.monotonic() - began:5.1f}s] {name}"
        print(line)
        try:
            _trace_log(Path(get_config().workspace_dir), f"RESTART {line}")
        except Exception:                        # noqa: BLE001
            pass

    clear("Before the restart")
    if _blocked_by_dialog(frames_pid, "the loop cannot take the stop file"):
        return 1

    workspace = Path(get_config().workspace_dir)
    # THE LOOP THAT IS RUNNING, not the one the config names: after a demo
    # take the pointer named the normal workspace while the loop still
    # served the demo one, and the stop file went where nobody read it.
    # Capped: with the loop dead the bridge's own wait ran 267 s before
    # the restart even began.
    import concurrent.futures
    pool = concurrent.futures.ThreadPoolExecutor(1)
    try:
        ping = pool.submit(_ping_altium).result(timeout=5.0)
    except concurrent.futures.TimeoutError:
        ping = None
    finally:
        pool.shutdown(wait=False)
    running = Path(str((ping or {}).get("workspace_dir") or "")) if ping else None
    if running and str(running) not in ("", ".") and running.is_dir() \
            and running.resolve() != workspace.resolve():
        print(f"The running loop serves {running}, not {workspace}; "
              f"stopping that one.")
        workspace = running
    marker = workspace / MAINTENANCE_FILE
    marker.write_text(json.dumps({"reason": "relaunch --restart",
                                  "started": time.time(), "pid": os.getpid()}),
                      encoding="utf-8")
    try:
        # Nothing answers: the script may be parked at a breakpoint after a
        # handler crashed. Run > Stop it first, so the stop file waits only
        # the short idle limit, not out a progress file the crash left behind.
        phase("pinged: " + ("the loop answers" if ping else "nothing answers"))
        if ping is None:
            _stop_halted_script(command)
            phase("Run > Stop sent to a halted script")
            clear("After stopping the halted script")
        state = _wait_for_loop_to_stop(workspace, trust_progress=ping is not None,
                                       idle_wait=(RESTART_DEAD_STOP_WAIT_S
                                                  if ping is None else None))
        phase(f"stop file: {state}")
        print(f"The loop {'stopped' if state == 'stopped' else 'was not running'}; "
              f"other sessions' calls are waiting for the new one.")
        if state != "stopped" and ping is None:
            print(f"Nothing answered a ping through {workspace}, so any script "
                  f"left halted in Altium was stopped first. If Altium still "
                  f"says \"Another script executing now\", a loop is serving "
                  f"another workspace: put an empty file named stop in that "
                  f"workspace and run this again.")
        frames = windows.visible_main_frames(frames_pid)
        launcher = subprocess.Popen(command, close_fds=True)
        phase("StartMCPServer handed to Altium")
        _reshow_hidden_frames(launcher, frames)
        phase("hand-off done")
        deadline = time.monotonic() + RESTART_PING_WAIT_S
        blocked_since: Optional[float] = None
        while time.monotonic() < deadline:
            ping = _ping_altium()
            if ping is None:
                # The new loop cannot start behind a modal: clear an error
                # box at once, and give up on any other within seconds
                # rather than waiting out the whole timeout.
                blocking = clear("Starting the loop")
                if not blocking:
                    blocked_since = None
                elif blocked_since is None:
                    blocked_since = time.monotonic()
                elif time.monotonic() - blocked_since > RESTART_DIALOG_GIVE_UP_S:
                    print(f"ERROR: Altium has a dialog open, so the new loop "
                          f"cannot start: {blocking}. Answer it, then run "
                          f"`eda-agent relaunch --restart` again.",
                          file=sys.stderr)
                    return 1
            if ping is not None:
                phase("the new loop answers")
                waiting = len(list(workspace.glob("request_*.json")))
                print(f"The bridge answers again (version "
                      f"{ping.get('script_version', '?')}); {waiting} waiting "
                      f"call{'' if waiting == 1 else 's'} now being served.")
                return 0
            time.sleep(1.0)
        print("ERROR: the new loop did not answer within "
              f"{RESTART_PING_WAIT_S:.0f}s. Check Altium's status window, or "
              "run `eda-agent doctor`.", file=sys.stderr)
        return 1
    finally:
        try:
            marker.unlink()
        except OSError:
            pass


def cmd_update(check_only: bool = False, assume_yes: bool = False,
               relaunch: bool = True) -> int:
    """Show what a newer eda-agent brings, and install it once agreed."""
    from . import updates

    info = updates.check(force=True)
    if info.get("update_available") is None:
        print(info.get("how") or info.get("error") or "Could not check for an update.")
        return 1
    if not info.get("update_available"):
        print("eda-agent is up to date.")
        return 0
    print(f"{info['behind']} new commit(s) on {updates.REMOTE}/{updates.BRANCH}:")
    for title in info.get("new_commits", []):
        print(f"  - {title}")
    if check_only:
        return 0
    if not assume_yes:
        if not sys.stdin.isatty():
            print("Re-run with --yes to update.", file=sys.stderr)
            return 1
        try:
            reply = input("Update now? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Not updated.")
            return 1
    out = updates.apply(relaunch=relaunch)
    if not out.get("updated"):
        print(f"Not updated: {out.get('reason') or out.get('error') or out}",
              file=sys.stderr)
        return 1
    print(f"Updated {out['from']} -> {out['to']}.")
    for step in out.get("steps", []):
        print(f"  {step['step']}: {'ok' if step['ok'] else 'FAILED'}")
        if not step["ok"] and step.get("output"):
            print("    " + step["output"].replace("\n", "\n    "))
    print(out["next_step"])
    return 1 if out.get("failed_steps") else 0


def cmd_relaunch(prjscr: Optional[str] = None, dry_run: bool = False,
                 force: bool = False, restart: bool = False) -> int:
    """Start the bridge script in the running Altium, when you ask for it.

    Nothing runs this on its own: starting a script inside someone's
    Altium is their call. Request files already in the workspace are
    left alone, since another session may be waiting on them, and a
    bridge that already answers a ping is left running unless --force.

    ``restart`` stops a running loop first, after its current command,
    and other sessions' calls wait through the restart (see _restart),
    which is how a deploy reaches a loop several sessions are using.
    """
    if sys.platform != "win32":
        print("ERROR: relaunch runs on Windows, where Altium is", file=sys.stderr)
        return 1
    project = (Path(prjscr).expanduser() if prjscr
               else get_default_scripts_dest() / "Altium_API.PrjScr")
    if not project.is_file():
        print(f"ERROR: no script project at {project}. Run "
              f"`eda-agent install-scripts` first, or pass --prjscr.",
              file=sys.stderr)
        return 1

    from .bridge.process_manager import AltiumProcessManager

    selection = AltiumProcessManager().select_altium_process()
    if selection.process is None:
        print("ERROR: " + (selection.reason or "Altium is not running"),
              file=sys.stderr)
        return 1
    exe = selection.process.exe_path
    if not exe or not Path(exe).is_file():
        print(f"ERROR: could not read the path of Altium's executable "
              f"(pid {selection.process.pid})", file=sys.stderr)
        return 1

    command = relaunch_command(exe, str(project))
    if dry_run:
        print(" ".join(f'"{part}"' if " " in part else part for part in command))
        return 0
    if restart:
        return _restart(command, selection.process.pid)
    if _blocked_by_dialog(selection.process.pid,
                          "a script started now would go to a second Altium"):
        return 1
    if not force:
        from .diag.doctor import _ping_altium

        if _ping_altium() is not None:
            print("The bridge already answers a ping; nothing was started. "
                  "Pass --force to run StartMCPServer anyway.")
            return 0
        _stop_halted_script(command)

    import subprocess

    from .ui import windows

    frames = windows.visible_main_frames(selection.process.pid)
    launcher = subprocess.Popen(command, close_fds=True)
    print(f"Asked Altium (pid {selection.process.pid}) to run "
          f"StartMCPServer from {project}. Check the status window, or run "
          f"`eda-agent doctor`.")
    shown = _reshow_hidden_frames(launcher, frames)
    if shown:
        print(f"Altium's main window was hidden after the hand-off and has "
              f"been shown again ({len(shown)} window"
              f"{'' if len(shown) == 1 else 's'}).")
    return 0


#: How long relaunch waits for the second X2.EXE to hand over and exit,
#: then how long it keeps watching the main window afterwards.
RELAUNCH_HANDOFF_TIMEOUT_S = 30.0
RELAUNCH_WATCH_S = 5.0


def _reshow_hidden_frames(launcher, frames: list) -> list:
    """Show again any main frame the -R hand-off left hidden.

    Altium's main window has been found hidden, not minimized, with no
    taskbar button, after relaunches; only frames that were on screen
    before the hand-off are touched. Returns the handles shown again.
    """
    if not frames:
        return []
    from .ui import windows
    import time

    wait = getattr(launcher, "wait", None)
    if wait is not None:
        try:
            wait(timeout=RELAUNCH_HANDOFF_TIMEOUT_S)
        except Exception:                         # noqa: BLE001
            pass
    shown: list = []
    deadline = time.monotonic() + RELAUNCH_WATCH_S
    while True:
        for hwnd in windows.show_if_hidden(frames):
            if hwnd not in shown:
                shown.append(hwnd)
        if time.monotonic() >= deadline:
            return shown
        time.sleep(0.25)


# --------------------------------------------------------------------------
# install-scripts
# --------------------------------------------------------------------------

_UTF8_BOM = bytes((0xEF, 0xBB, 0xBF))


def _stamp_deployed_unit(path: Path) -> None:
    """Write the unit's content hash into its UnitStamp function, and drop
    any byte-order mark.

    Only the deployed copy is stamped; the repository keeps 'dev'. See
    eda_agent/script_stamps.py for why one version constant is not enough.
    Altium Designer 21 reads a mark as source text and stops on line 1 with
    "Invalid constant", so no deployed unit carries one.
    """
    from .script_stamps import has_stamp, stamp_source

    raw = path.read_bytes()
    bom = raw.startswith(_UTF8_BOM)
    text = raw.decode("utf-8-sig")
    if has_stamp(text):
        path.write_bytes(stamp_source(text).encode("utf-8"))
    elif bom:
        path.write_bytes(raw[len(_UTF8_BOM):])


def cmd_install_scripts(dest: Optional[str] = None, force: bool = False) -> int:
    """Copy the bundled script project where Altium loads it from.

    Default destination: every folder an Altium install registers the
    script project in (Global Projects), so each version on the machine
    compiles the same build; with none registered,
    %USERPROFILE%\\EDA Agent\\scripts\\. Override with --dest PATH.

    After the copy, the user opens `Altium_API.PrjScr` in Altium
    (File > Open, or via the Scripts panel) and runs `StartMCPServer`
    from `Dispatcher.pas`.
    """
    if dest:
        targets = [Path(dest).expanduser().resolve()]
    else:
        targets = get_registered_scripts_dirs() or [get_fallback_scripts_dest()]
    status = 0
    for target in targets:
        status = max(status, _install_scripts_into(target, force))
    if len(targets) > 1:
        print(f"Installed into {len(targets)} folders that Altium installs "
              f"register: {', '.join(map(str, targets))}")
    return status


def _install_scripts_into(dst: Path, force: bool) -> int:
    src = get_bundled_scripts_path()

    if not src.exists():
        print(f"ERROR: bundled scripts directory not found: {src}", file=sys.stderr)
        return 1

    # An auto-accept harness (e.g. Gemini CLI) may allocate a pty -- so
    # sys.stdin.isatty() is True -- yet never feed the prompt, hanging input()
    # forever. Honor an env override so such harnesses can opt out of the
    # prompt without remembering --force on every call.
    if os.environ.get("EDA_AGENT_ASSUME_YES", "").strip().lower() in ("1", "true", "yes"):
        force = True

    if dst.exists() and any(dst.iterdir()) and not force:
        if not sys.stdin.isatty():
            print(
                f"ERROR: target directory already exists and is not empty:\n"
                f"  {dst}\n"
                f"Re-run with --force (or set EDA_AGENT_ASSUME_YES=1) to overwrite.",
                file=sys.stderr,
            )
            return 1
        print(f"Target directory already exists and contains files:")
        print(f"  {dst}")
        print(
            "Overwriting replaces every deployed script -- edits made "
            "directly in that directory (rather than in the repo) are lost. "
            "Back them up first if you have any."
        )
        try:
            reply = input("Overwrite existing files? [y/N] ").strip().lower()
        except EOFError:
            print("\nAborted (no input available -- re-run with --force).", file=sys.stderr)
            return 1
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    dst.mkdir(parents=True, exist_ok=True)

    # Only ship the files Altium needs: .pas sources, DFM form files, and
    # the project file. Without .dfm the DFM-backed StatusForm dashboard
    # fails to compile and StartMCPServer crashes with "unknown identifier"
    # errors referencing the form's controls.
    allowed_suffixes = {".pas", ".dfm", ".PrjScr"}

    copied = 0
    for child in src.iterdir():
        if not child.is_file():
            continue
        if ".tmp." in child.name:
            continue
        if child.suffix not in allowed_suffixes:
            continue
        shutil.copy2(child, dst / child.name)
        if child.suffix == ".pas":
            _stamp_deployed_unit(dst / child.name)
        copied += 1

    # Publish the workspace path for DelphiScript to pick up, even before
    # the MCP server has ever run. It is the SERVER's workspace, wherever
    # the scripts went: the script reads the pointer once at startup, and
    # deriving it from the destination (<dest>\..\workspace) pointed
    # Altium at a folder no request is written to whenever --dest or a
    # registered folder was not the default.
    from .config import get_config, write_workspace_pointer
    workspace_dir = Path(get_config().workspace_dir)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    write_workspace_pointer(workspace_dir)

    prjscr = dst / "Altium_API.PrjScr"
    print(f"Copied {copied} files to: {dst}")
    print(f"Workspace:     {workspace_dir}")
    print()
    registered = {os.path.normcase(str(f)) for f in get_registered_scripts_dirs()}
    if os.path.normcase(str(dst)) in registered:
        print("Altium already loads the script project from this folder.")
    else:
        print("One-time setup in Altium Designer:")
        print("  DXP -> Preferences -> Scripting System -> Global Projects")
        print(f"  Install from file -> select:")
        print(f"  {prjscr}")
        if registered:
            print("  (Altium currently loads it from elsewhere: "
                  f"{', '.join(map(str, get_registered_scripts_dirs()))})")
    print()
    print("Each Altium session, start the polling loop via:")
    print("  File -> Run Script... -> Altium_API -> Dispatcher.pas -> StartMCPServer -> Run")
    return 0


def cmd_calc(name: Optional[str], inputs: list[str], as_json: bool = False) -> int:
    """Run one calculator from design/calculators.py.

    The inputs are read against the function's own signature, so a flag
    can never drift from the parameter it fills: a name it does not take
    is refused with the names it does, and each value is converted to the
    type that parameter is annotated with.
    """
    import inspect
    import json as _json

    from .design.calculators import CALCULATORS

    if not name or name not in CALCULATORS:
        if name:
            print(f"no calculator {name!r}")
        for key, fn in CALCULATORS.items():
            doc = " ".join((fn.__doc__ or "").split())
            print(f"  {key:22s} {doc.split('. ')[0].rstrip('.')}.")
        return 0 if not name else 2
    fn = CALCULATORS[name]
    params = inspect.signature(fn).parameters
    if any(i in ("-h", "--help") for i in inputs):
        print((fn.__doc__ or "").strip())
        print()
        for p in params.values():
            default = "" if p.default is inspect.Parameter.empty else f" = {p.default!r}"
            print(f"  {p.name}{default}")
        return 0
    kwargs = {}
    for item in inputs:
        key, sep, raw = item.partition("=")
        if not sep or key not in params:
            print(f"{item!r}: inputs are KEY=VALUE, KEY one of {', '.join(params)}")
            return 2
        hint = str(params[key].annotation)
        try:
            if "int" in hint and "float" not in hint:
                kwargs[key] = int(raw)
            elif "float" in hint:
                kwargs[key] = float(raw)
            else:
                kwargs[key] = raw
        except ValueError:
            print(f"{key}: {raw!r} is not a number")
            return 2
    try:
        result = fn(**kwargs)
    except TypeError as exc:
        print(f"{name}: {exc}")
        return 2
    if as_json:
        print(_json.dumps(result, indent=1, default=str))
    else:
        for key, value in result.items():
            if isinstance(value, float):
                value = f"{value:.6g}"
            print(f"{key}: {value}")
    return 0 if result.get("ok") else 1

