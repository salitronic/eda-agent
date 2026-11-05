# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Full-preflight checks that talk to a running Altium.

Runs all the health checks first, then a series of canaries that exercise
the IPC bridge, script alive, version match, basic command round-trips,
the Pascal handlers we lean on most.

Library-path canaries are explicit-input only: the doctor accepts an
optional list of SchLib paths from the user, and reports whether each
one is reachable and parseable. It does not crawl directories or assume
any naming scheme.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Sequence

from eda_agent.diag.checks import Check, Severity, Status
from eda_agent.diag.health import run_health_checks


def _altium_process_running() -> Check:
    """Look for the Altium executable in the running process list."""
    try:
        import psutil  # type: ignore
    except ImportError:
        return Check(
            name="altium process running",
            status=Status.SKIP,
            message="psutil not installed, cannot enumerate processes",
        )

    target = os.environ.get("EDA_AGENT_ALTIUM_PROCESS", "X2.exe").lower()
    matches: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name == target or name == target.replace(".exe", ""):
            matches.append(proc.info.get("pid"))

    if not matches:
        return Check(
            name="altium process running",
            status=Status.FAIL,
            message=f"no process named {target} found",
            fix="Start Altium Designer.",
        )
    return Check(
        name="altium process running",
        status=Status.PASS,
        message=f"pid(s): {matches}",
    )


def _ping_altium() -> Optional[dict]:
    """Send a ping. Returns the response dict, or None on any failure."""
    try:
        from eda_agent.bridge import get_bridge
        bridge = get_bridge()
        return bridge.ping_with_version()
    except Exception:  # the check reports no reply; that is what was found
        return None


def _check_script_responsive(ping: Optional[dict]) -> Check:
    if ping is None:
        return Check(
            name="DelphiScript polling loop responsive",
            status=Status.FAIL,
            message="ping_altium did not return",
            fix=(
                "Open Altium_API.PrjScr in Altium and run "
                "Dispatcher.pas > StartMCPServer."
            ),
        )
    return Check(
        name="DelphiScript polling loop responsive",
        status=Status.PASS,
        message=f"version {ping.get('script_version', '?')}",
    )


def _check_script_version_match(ping: Optional[dict]) -> Check:
    if ping is None:
        return Check(
            name="script version match",
            status=Status.SKIP,
            message="cannot check, script not responding",
            severity=Severity.MINOR,
        )

    altium_v = ping.get("script_version", "")
    bundled_v = ""
    try:
        from eda_agent.cli import get_bundled_scripts_path
        main_pas = get_bundled_scripts_path() / "Main.pas"
        if main_pas.exists():
            for line in main_pas.read_text(encoding="utf-8").splitlines():
                if "SCRIPT_VERSION" in line and "=" in line:
                    parts = line.split("=", 1)
                    bundled_v = parts[1].strip().strip(";").strip().strip("'")
                    break
    except (OSError, ImportError, UnicodeDecodeError):  # reported below as not found
        pass

    if not altium_v or not bundled_v:
        return Check(
            name="script version match",
            status=Status.SKIP,
            message=f"altium={altium_v!r} bundled={bundled_v!r}, incomplete",
            severity=Severity.MINOR,
        )

    if altium_v != bundled_v:
        return Check(
            name="script version match",
            status=Status.FAIL,
            message=f"altium={altium_v} but bundled={bundled_v}",
            fix=(
                "Re-run `eda-agent install-scripts --force`, then close "
                "Altium fully (the script cache survives Altium_API.PrjScr "
                "reload) and reopen the script project."
            ),
        )
    return Check(
        name="script version match",
        status=Status.PASS,
        message=altium_v,
    )


def _check_command_canary() -> Check:
    """Round-trip a real command, not just ping, that changes nothing.

    This was application.save_all, described as a no-op: it writes every
    open document, so running the doctor rewrote files in every open
    project. Listing the open documents exercises the same dispatch.
    """
    try:
        from eda_agent.bridge import get_bridge
        bridge = get_bridge()
        bridge.send_command("application.get_open_documents", {}, timeout=15.0)
    except Exception as exc:
        return Check(
            name="command canary",
            status=Status.FAIL,
            message=f"get_open_documents round-trip failed: {exc}",
            fix=(
                "Script may be hung behind a modal Altium dialog, "
                "check Altium for a popup and click OK, then re-run."
            ),
        )
    return Check(
        name="command canary",
        status=Status.PASS,
    )


def _check_lib_paths(library_paths: Sequence[str]) -> list[Check]:
    """Check each user-supplied SchLib path. No hardcoded paths."""
    if not library_paths:
        return [
            Check(
                name="library paths",
                status=Status.SKIP,
                message=(
                    "no --library paths supplied, pass one or more "
                    "--library PATH.SchLib to test lib reachability"
                ),
                severity=Severity.MINOR,
            )
        ]

    checks: list[Check] = []
    for raw in library_paths:
        p = Path(raw).expanduser()
        name = f"library: {p.name}"
        if not p.exists():
            checks.append(
                Check(
                    name=name,
                    status=Status.FAIL,
                    message=f"{p} does not exist",
                    fix="Check the path or correct the spelling.",
                    severity=Severity.MINOR,
                )
            )
            continue
        try:
            from eda_agent.bridge import get_bridge
            bridge = get_bridge()
            raw_resp = bridge.send_command(
                "library.get_components",
                {"library_path": str(p)},
                timeout=30.0,
            )
        except Exception as exc:
            checks.append(
                Check(
                    name=name,
                    status=Status.FAIL,
                    message=f"could not enumerate components: {exc}",
                    fix=(
                        "Confirm Altium is running and the script is "
                        "responsive (run `eda-agent doctor` and look at "
                        "earlier checks)."
                    ),
                    severity=Severity.MINOR,
                )
            )
            continue
        count = 0
        if isinstance(raw_resp, dict):
            count = len(raw_resp.get("components") or raw_resp.get("results") or [])
        elif isinstance(raw_resp, list):
            count = len(raw_resp)
        if count == 0:
            checks.append(
                Check(
                    name=name,
                    status=Status.WARN,
                    message=f"{p}: 0 components found",
                    severity=Severity.MINOR,
                )
            )
        else:
            checks.append(
                Check(
                    name=name,
                    status=Status.PASS,
                    message=f"{count} components",
                )
            )
    return checks


def run_doctor_checks(library_paths: Optional[Sequence[str]] = None) -> list[Check]:
    """Compose health + Altium-side preflight."""
    checks: list[Check] = []

    # Health checks first, early failures often explain later ones.
    checks.extend(run_health_checks())
    if any(c.status == Status.FAIL and c.severity == Severity.CRITICAL for c in checks):
        return checks

    checks.append(_altium_process_running())
    if checks[-1].status == Status.FAIL:
        return checks

    ping = _ping_altium()
    checks.append(_check_script_responsive(ping))
    checks.append(_check_script_version_match(ping))

    if ping is None:
        return checks

    checks.append(_check_command_canary())
    checks.extend(_check_lib_paths(library_paths or []))
    return checks
