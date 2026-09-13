# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium process detection and management."""

import logging
import sys
import time
import psutil
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger("eda_agent.bridge.process")


# ---------------------------------------------------------------------------
# Native Windows process-name scan.
#
# psutil.process_iter -- even fetching only "name" -- measured ~2.2s on the
# target machine, and is_altium_running() is on the hot path of every bridge
# call. The Toolhelp snapshot API enumerates process names straight from the
# kernel snapshot in tens of milliseconds. Falls back to psutil if the
# native path is unavailable (non-Windows, ctypes failure).
# ---------------------------------------------------------------------------

_TH32CS_SNAPPROCESS = 0x00000002
_th_kernel32 = None
if sys.platform == "win32":
    try:
        import ctypes
        from ctypes import wintypes

        class _PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        _th_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _th_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        _th_kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD, wintypes.DWORD]
        _th_kernel32.Process32FirstW.restype = wintypes.BOOL
        _th_kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        _th_kernel32.Process32NextW.restype = wintypes.BOOL
        _th_kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        _th_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        _th_invalid_handle = ctypes.c_void_p(-1).value
    except Exception as _e:  # pragma: no cover - platform dependent
        _th_kernel32 = None
        logger.debug("Toolhelp process scan unavailable: %s", _e)


def _scan_process_names_native(wanted_upper: set) -> Optional[bool]:
    """Return True/False if any wanted process name is running, via the
    Toolhelp snapshot. Returns None if the native path is unavailable."""
    if _th_kernel32 is None:
        return None
    try:
        snap = _th_kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == _th_invalid_handle:
            return None
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = _th_kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.szExeFile.upper() in wanted_upper:
                    return True
                ok = _th_kernel32.Process32NextW(snap, ctypes.byref(entry))
            return False
        finally:
            _th_kernel32.CloseHandle(snap)
    except Exception as e:
        logger.debug("native process scan failed: %s", e)
        return None


@dataclass
class AltiumProcessInfo:
    """Information about a running Altium process."""

    pid: int
    name: str
    exe_path: str
    version: Optional[str] = None
    cmdline: Optional[list[str]] = None


@dataclass
class AltiumSelection:
    """Which Altium process to act on, and how that was decided.

    ``selected_by`` is one of ``none`` (no Altium running),
    ``only_candidate``, ``status_form``, ``only_windowed`` or
    ``ambiguous``. When ambiguous, ``process`` is None and ``reason``
    names the candidates.
    """

    process: Optional[AltiumProcessInfo]
    candidates: list[AltiumProcessInfo]
    selected_by: str
    reason: str = ""


class AltiumProcessManager:
    """Manages detection and interaction with Altium Designer process."""

    PROCESS_NAMES = ["X2.exe", "DXP.exe"]  # Altium Designer executable names

    # is_altium_running() is on the hot path -- every bridge call hits it
    # (twice: once in _bridge_call, once inside send_command). A full
    # process_iter that fetches exe/cmdline opens every process on Windows
    # and costs seconds; cache the cheap name-only result for this long.
    _RUNNING_TTL = 3.0

    def __init__(self):
        self._running_cache: Optional[tuple[float, bool]] = None

    #: Caption of the bridge's status window, which StartMCPServer opens.
    #: The process owning a window with this caption is, by construction,
    #: the one running the script. Must match the Caption in
    #: scripts/altium/StatusForm.dfm; a test holds the two together.
    STATUS_FORM_TITLE = "EDA Agent MCP"

    def _candidates(self) -> list[AltiumProcessInfo]:
        """Every Altium process, in enumeration order. The slow path:
        fetches exe and cmdline for each."""
        wanted = {n.upper() for n in self.PROCESS_NAMES}
        out: list[AltiumProcessInfo] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                proc_name = proc.info["name"] or ""
                if proc_name.upper() in wanted:
                    out.append(AltiumProcessInfo(
                        pid=proc.info["pid"],
                        name=proc.info["name"],
                        exe_path=proc.info["exe"] or "",
                        cmdline=proc.info["cmdline"],
                    ))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return out

    @staticmethod
    def _window_owner_pids(title_prefix: Optional[str] = None) -> Optional[set]:
        """Pids owning a top-level window, or None if windows cannot be read.

        With ``title_prefix``: windows whose caption starts with it,
        hidden ones included. Without: VISIBLE windows only, which is
        what separates a live instance from the windowless orphan a
        crashed session leaves behind.
        """
        try:
            from ..ui import windows
        except Exception:                        # noqa: BLE001
            return None
        if not windows.available():
            return None
        try:
            found = windows.enumerate_windows(
                visible_only=title_prefix is None)
        except Exception:                        # noqa: BLE001
            return None
        if title_prefix is None:
            return {w.pid for w in found}
        return {w.pid for w in found
                if (w.title or "").startswith(title_prefix)}

    def select_altium_process(self) -> AltiumSelection:
        """Which Altium to act on, and how that was decided. Slow path.

        THE FIRST MATCH IS NOT AN ANSWER. This used to return whichever
        X2.exe the process scan yielded first. A crashed session leaves
        a windowless orphan, and when that enumerated first every UI
        tool read ITS windows: no dialog open, no button to press, while
        a modal sat plainly on screen in the real instance. Bridge calls
        kept working throughout, because they go through the file channel
        to whichever instance runs the script, so half the toolset talked
        to one Altium and half to the other and nothing said so.

        With more than one candidate, in order:

        1. the one owning the bridge's status window, which only the
           instance running StartMCPServer has;
        2. the only one with a visible top-level window;
        3. otherwise none: ``ambiguous``, with every pid named. A tool
           acting on a guess here reads the wrong process's dialogs, and
           an empty read looks exactly like a correct one.

        Windows are read only when there is more than one candidate, so
        the ordinary single-instance case costs what it always did.
        """
        candidates = self._candidates()
        if not candidates:
            return AltiumSelection(None, [], "none")
        if len(candidates) == 1:
            return AltiumSelection(candidates[0], candidates, "only_candidate")

        pids = [c.pid for c in candidates]
        by_pid = {c.pid: c for c in candidates}

        scripted = self._window_owner_pids(self.STATUS_FORM_TITLE)
        scripted_hits = [p for p in pids if scripted and p in scripted]
        if len(scripted_hits) == 1:
            return AltiumSelection(by_pid[scripted_hits[0]], candidates,
                                   "status_form")

        windowed = self._window_owner_pids()
        windowed_hits = [p for p in pids if windowed and p in windowed]
        if len(windowed_hits) == 1 and not scripted_hits:
            return AltiumSelection(by_pid[windowed_hits[0]], candidates,
                                   "only_windowed")

        if scripted is None or windowed is None:
            why = "their windows could not be read on this host"
        elif len(scripted_hits) > 1:
            why = (f"{len(scripted_hits)} of them show the bridge's status "
                   f"window")
        else:
            why = (f"{len(windowed_hits)} of them have visible windows and "
                   f"none shows the bridge's status window")
        reason = (
            f"{len(pids)} Altium processes are running (pids "
            f"{', '.join(str(p) for p in pids)}) and {why}, so there is no "
            f"telling which one to act on. Refusing rather than guessing: a "
            f"dialog read from the wrong process looks exactly like no "
            f"dialog. Close the instance you do not mean, or start the "
            f"bridge in the one you do, so its status window identifies it.")
        return AltiumSelection(None, candidates, "ambiguous", reason)

    def find_altium_process(self) -> Optional[AltiumProcessInfo]:
        """The Altium process to act on, or None.

        None also when several are running and none can be identified;
        ``select_altium_process`` says which of the two it was.
        """
        return self.select_altium_process().process

    def _scan_running(self) -> bool:
        """Is any Altium process running? Native Toolhelp scan first
        (~tens of ms); psutil name-only scan as the fallback.
        """
        wanted = {n.upper() for n in self.PROCESS_NAMES}
        native = _scan_process_names_native(wanted)
        if native is not None:
            return native
        # Fallback: psutil. Slower, but correct on non-Windows / if the
        # native path failed.
        try:
            for proc in psutil.process_iter(["name"]):
                name = (proc.info.get("name") or "").upper()
                if name in wanted:
                    return True
        except Exception as e:
            logger.debug("process scan failed: %s", e)
        return False

    def is_altium_running(self) -> bool:
        """Check if Altium Designer is running.

        Fast path: a name-only process scan, cached for _RUNNING_TTL
        seconds. Altium does not start/stop within a few seconds, so the
        cache is safe and removes seconds of latency from every bridge
        call.
        """
        now = time.monotonic()
        cached = self._running_cache
        if cached is not None and (now - cached[0]) < self._RUNNING_TTL:
            return cached[1]
        val = self._scan_running()
        self._running_cache = (now, val)
        return val

    def get_altium_info(self) -> Optional[AltiumProcessInfo]:
        """Get information about the running Altium process.

        Returns:
            AltiumProcessInfo for the instance to act on. None when
            Altium is not running, and also when several are running and
            none can be identified as the scripted one.
        """
        return self.select_altium_process().process

    def get_altium_pid(self) -> Optional[int]:
        """Get the PID of the running Altium process.

        Returns:
            PID if Altium is running, None otherwise.
        """
        process = self.find_altium_process()
        return process.pid if process else None

    def refresh(self) -> None:
        """Re-scan for the Altium process."""
        self.find_altium_process()
