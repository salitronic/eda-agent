# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""EDA Agent MCP Server - Main entry point + CLI subcommands."""

import argparse
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Optional
from mcp.server.fastmcp import FastMCP

from .tools import (
    BACKENDS, DEFAULT_BACKEND, DEFAULT_TOOLSET, TOOLSETS, register_backend,
)
from .config import get_config

logger = logging.getLogger("eda_agent")


class _ThreadAwareStdout:
    """sys.stdout wrapper that keeps the MAIN thread's writes on the
    real stdout (which the MCP server uses for JSON-RPC) but redirects
    every BACKGROUND-thread write to stderr.

    Lenient MCP clients (Claude Code) tolerate stray stdout bytes from
    the dashboard thread; strict clients (Codex) see a single non-JSON
    byte and close the transport. This wrapper makes the dashboard
    coexist with strict-stdio clients without disabling it -- any
    background-thread print() / write() lands on stderr where it's
    safe, while the main thread's MCP I/O stays untouched.
    """

    __slots__ = ("_real_stdout", "_real_stderr")

    def __init__(self, real_stdout, real_stderr):
        self._real_stdout = real_stdout
        self._real_stderr = real_stderr

    def _target(self):
        if threading.current_thread() is threading.main_thread():
            return self._real_stdout
        return self._real_stderr

    # Required stream methods. Delegating __getattr__ alone isn't safe
    # because some callers do isinstance checks / direct attribute peeks
    # before writing.
    def write(self, s):
        return self._target().write(s)

    def writelines(self, lines):
        return self._target().writelines(lines)

    def flush(self):
        return self._target().flush()

    def isatty(self):
        return self._target().isatty()

    def fileno(self):
        return self._target().fileno()

    @property
    def buffer(self):
        return self._target().buffer

    @property
    def encoding(self):
        return self._target().encoding

    @property
    def errors(self):
        return self._target().errors

    def __getattr__(self, name):
        return getattr(self._target(), name)


def _install_stdio_guard() -> None:
    """Replace sys.stdout with the thread-aware wrapper.

    Idempotent: a second call is a no-op. Captures the original stdout
    so the MCP main thread can keep writing JSON-RPC to it; everything
    on background threads falls through to stderr automatically.
    """
    if isinstance(sys.stdout, _ThreadAwareStdout):
        return
    sys.stdout = _ThreadAwareStdout(sys.stdout, sys.stderr)


def setup_logging() -> None:
    """Configure logging for the MCP server."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    )
    root = logging.getLogger("eda_agent")
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# Which EDA tool this server drives. Resolved once, at import, from the
# environment so it is settled before the dashboard (which reads the
# registered tool set) or any subcommand touches ``mcp``. MCP clients select
# it in their server config's ``env``; the ``--backend`` CLI flag re-execs
# with this set for terminal use. Altium is the default so existing installs
# are unaffected.
ACTIVE_BACKEND = os.environ.get("EDA_AGENT_BACKEND", DEFAULT_BACKEND)

# Some MCP clients cap tool count or stall serializing hundreds of schemas
# at startup. EDA_AGENT_TOOLSET=minimal advertises only tool_catalog and
# tool_invoke while keeping every other tool reachable through them. The
# default stays "full" so existing installs are unaffected.
ACTIVE_TOOLSET = os.environ.get("EDA_AGENT_TOOLSET", DEFAULT_TOOLSET)

# WHY THERE ARE SERVER INSTRUCTIONS AT ALL.
#
# tool_catalog and tool_guide only help a caller who thinks to call them,
# and the recorded failures are precisely the ones where nobody did: a
# capability was reported ABSENT while the tool existed under another
# namespace, four times over. A tool description cannot fix that, because
# it is read only once the right tool has already been found. Server
# instructions are the one piece of text a client sees before choosing
# anything, so the pointer belongs here.
#
# Deliberately short. This is prepended to a client's context on every
# session, so it earns its place by covering the mistakes that actually
# happened and nothing else.
#: The schematic-engine paragraph, per backend, because the tools it
#: names are not the same on each. The Altium surface carries the whole
#: family; EasyEDA carries the plan-building half and emits through its
#: own tool; KiCad carries neither, so it gets no paragraph rather than
#: a paragraph naming tools that are not there. A preamble that names a
#: missing tool is read first and trusted most, which is the worst place
#: for a dead end.
_ENGINE_ALTIUM = (
    "DO NOT DRAW A SCHEMATIC BY HAND. Placing parts one at a time\n"
    "with sch_place_components and joining them with sch_place_wires\n"
    "gives a netlist-correct sheet that reads like nothing a person\n"
    "would draw, and it is the most common way this server is misused.\n"
    "A schematic comes from a DesignPlan: build the plan, then\n"
    "design_execute_plan places AND routes the whole sheet in one\n"
    "call, through a layout engine measured against hand-drawn boards.\n"
    "design_layout_schematic returns that same layout as data and\n"
    "design_preview_plan renders it, both without touching the EDA.\n"
    "One engine sits behind all three.\n"
    "\n"
    "CHANGING an existing sheet goes the same way. Re-running\n"
    "design_execute_plan on a project it drew before moves only what\n"
    "the plan changed and leaves every other component alone, so an\n"
    "edit is a plan edit. For a sheet it did not draw,\n"
    "design_plan_from_sheet reads a plan back off the schematic and\n"
    "design_hints_from_sheet reads the current positions to pass as\n"
    "placement_hints. The sch_ and obj_ tools are for the few glyphs a\n"
    "plan cannot express, and for repairs too small to re-lay-out.\n"
    "\n"
)

_ENGINE_EASYEDA = (
    "DO NOT DRAW A SCHEMATIC BY HAND. Placing parts one at a time\n"
    "with easyeda_place_schematic_components and joining them with\n"
    "easyeda_add_wires gives a netlist-correct sheet that reads like\n"
    "nothing a person would draw, and it is the most common way this\n"
    "server is misused. A schematic comes from a DesignPlan: build the\n"
    "plan, lay it out with design_layout_schematic, which runs the same\n"
    "engine measured against hand-drawn boards, then apply it with\n"
    "easyeda_emit_plan.\n"
    "\n"
)

_ENGINE_PARAGRAPHS = {
    "altium": _ENGINE_ALTIUM,
    "both": _ENGINE_ALTIUM,
    "easyeda": _ENGINE_EASYEDA,
    "kicad": "",
}


def build_server_instructions(
    toolset: str = DEFAULT_TOOLSET, backend: str = DEFAULT_BACKEND
) -> str:
    """The preamble, worded for the surface the client will actually see.

    Under the minimal toolset only tool_catalog and tool_invoke are
    advertised, so instructing a client to "call tool_guide" names
    something it cannot see. It stays reachable through tool_invoke, and
    saying which applies is the difference between guidance and a dead
    end for the clients that most need the guidance.

    The backend does the same job for the schematic-engine paragraph:
    design_execute_plan is Altium-only, so naming it to a KiCad client
    sent it looking for a tool that backend never registers.
    """
    if (toolset or DEFAULT_TOOLSET).strip().lower() == "minimal":
        reach = ('reach tool_guide through tool_invoke, since this server '
                 'is advertising only the two meta-tools,')
    else:
        reach = "call tool_guide"
    engine = _ENGINE_PARAGRAPHS.get(
        (backend or DEFAULT_BACKEND).strip().lower(), _ENGINE_ALTIUM)
    return (
        "Tools are grouped by the DOCUMENT they act on, and mixing them up\n"
        "is the most common error here: lib_ acts on a .PcbLib or .SchLib,\n"
        "pcb_ on an open .PcbDoc, sch_ and obj_ on an open .SchDoc. A board\n"
        "tool aimed at a library does not always fail. It can resolve some\n"
        "other open board and report success for work you never asked for.\n"
        "\n"
        f"Before concluding that this server cannot do something, {reach}\n"
        "with what you are trying to do. It answers three separate things:\n"
        "the tool and what it needs first, the tool you were probably\n"
        "reaching for and why it acts on a different document, and the\n"
        "short list of things that are genuinely impossible with the\n"
        "reason. Use tool_catalog to search the surface by name or\n"
        "category.\n"
        "\n"
        + engine +
        "Coordinates are in mils throughout, on every backend.\n"
    )


SERVER_INSTRUCTIONS = build_server_instructions(ACTIVE_TOOLSET, ACTIVE_BACKEND)

# Create global FastMCP instance, named for the backend so a client that
# lists several eda-agent servers can tell them apart.
mcp = FastMCP(
    f"eda-agent-{ACTIVE_BACKEND.strip().lower() or DEFAULT_BACKEND}",
    instructions=SERVER_INSTRUCTIONS,
)

# Register only the selected backend's tools. Returns the normalised name
# (an unrecognised value falls back to the default).
ACTIVE_BACKEND = register_backend(mcp, ACTIVE_BACKEND, ACTIVE_TOOLSET)


def _probe_port_owner(host: str, port: int) -> Optional[int]:
    """Return the OS pid that owns ``host:port`` if it's already bound, else None.

    Used at startup to detect the orphan-MCP-server situation: a previous
    eda-agent instance failed to exit on stdio EOF and is still holding
    port 8766, so this new instance's Flask thread will silently fail to
    bind. Surfacing the owning pid + a kill hint turns a confusing
    "dashboard not loading new endpoints" experience into one obvious fix.
    """
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        for conn in psutil.net_connections(kind="tcp"):
            la = conn.laddr
            if la and la.port == port and conn.status == "LISTEN":
                return conn.pid
    except (psutil.AccessDenied, OSError):
        return None
    return None


def _dashboard_disabled_via_env() -> bool:
    """Check the env vars that opt out of the dashboard.

    Accepts any of:
      EDA_AGENT_NO_DASHBOARD=1      (original name)
      EDA_AGENT_DISABLE_DASHBOARD=1 (alias requested in GH issue #4)
      EDA_AGENT_HEADLESS=1          (alias)
    Reading all three keeps existing setups working while matching the
    naming pattern users / docs may have settled on.
    """
    import os
    for key in ("EDA_AGENT_NO_DASHBOARD",
                "EDA_AGENT_DISABLE_DASHBOARD",
                "EDA_AGENT_HEADLESS"):
        if os.environ.get(key, "").strip() in ("1", "true", "yes", "on"):
            return True
    return False


def _spawn_dashboard_background(host: str, port: int) -> "Optional[object]":
    """Start the local web dashboard on a background thread.

    Two ways the dashboard runs:
      1. Auto-spawned in-process when MCP starts (this function) -- the
         common case when the user opens Claude with the MCP server.
         Dies when MCP exits.
      2. Manually via `eda-agent dashboard --port 8766` in a terminal --
         a standalone process the user controls.

    Returns the Werkzeug server handle so the caller can ``.shutdown()``
    it on stdio EOF. Werkzeug's request-handler threads aren't daemonic,
    so without the explicit shutdown the process can't exit and the
    next /mcp reconnect ends up with port 8766 still held by the orphan.
    """
    import os
    import threading

    if _dashboard_disabled_via_env():
        logger.info("dashboard disabled via env var (no/disable/headless)")
        return None

    # If port is already bound -- probably a manually-launched standalone
    # `eda-agent dashboard` -- skip with an info log. The MCP server
    # works fine without an in-process dashboard.
    owner_pid = _probe_port_owner(host, port)
    if owner_pid is not None and owner_pid != os.getpid():
        logger.info(
            "dashboard already running on port %s (pid %s) -- not "
            "spawning another. http://%s:%s/", port, owner_pid, host, port,
        )
        return {"already_running": True, "owner_pid": owner_pid}

    server_holder: dict[str, object] = {}

    def _run():
        try:
            from werkzeug.serving import make_server
            from .web.dashboard import create_app
            app = create_app()
            import logging as _log
            _log.getLogger("werkzeug").setLevel(_log.WARNING)
            srv = make_server(host, port, app, threaded=True)
            server_holder["srv"] = srv
            srv.serve_forever()
        except OSError as e:
            logger.warning("dashboard could not bind %s:%s (%s)",
                           host, port, e)
        except Exception as e:
            logger.warning("dashboard background thread crashed: %s", e)

    t = threading.Thread(target=_run, name="dashboard-server", daemon=True)
    t.start()
    logger.info("dashboard scheduled on http://%s:%s/", host, port)
    server_holder["thread"] = t
    return server_holder


def serve_mcp(no_dashboard: bool = False) -> int:
    """Start the MCP server on stdio. This is the default mode -- it's
    what an MCP-compatible client calls when it invokes `eda-agent` with no args.

    Passing ``no_dashboard=True`` (or setting any of the supported env
    vars listed in ``_dashboard_disabled_via_env``) skips the dashboard
    background thread entirely. Strict MCP clients (Codex, MCP CLI, etc)
    do not tolerate ANY noise on stdio, and even with the dashboard
    running silently a stray print from a transitive import can corrupt
    the JSON-RPC stream. Headless mode is the safe default for those
    clients.
    """
    # CRITICAL ORDER: install the thread-aware stdio guard BEFORE any
    # other module gets a chance to print. Strict MCP stdio clients
    # (Codex etc) close the transport on the first non-JSON byte. The
    # guard sends background-thread writes to stderr while keeping the
    # main thread's stdout intact for the MCP JSON-RPC stream.
    _install_stdio_guard()
    sys.stdout.flush()

    setup_logging()
    logger.info("Starting EDA Agent MCP Server")

    config = get_config()
    config.ensure_workspace()
    logger.info("Workspace directory: %s", config.workspace_dir)

    # Auto-launch the web dashboard in-process. Skip for headless / strict
    # MCP clients that can't tolerate dashboard side-effects.
    if no_dashboard or _dashboard_disabled_via_env():
        logger.info("headless mode -- dashboard not started")
        dash = None
    else:
        dash = _spawn_dashboard_background(host="127.0.0.1", port=8766)

    try:
        mcp.run(transport="stdio")
    finally:
        # Shut down the in-process Werkzeug server so the process can
        # actually exit. Werkzeug's request-handler threads aren't
        # daemonic, so without this they keep the process alive past
        # stdio-EOF and the next /mcp reconnect ends up with port 8766
        # still held by the orphan.
        if dash and isinstance(dash, dict):
            srv = dash.get("srv")
            try:
                if srv is not None and hasattr(srv, "shutdown"):
                    srv.shutdown()  # type: ignore[attr-defined]
            except Exception as e:
                logger.debug("dashboard shutdown raised: %s", e)
        import os as _os
        _os._exit(0)
    return 0


def _run_review(args) -> int:
    """Handle ``eda-agent review <file>`` -- offline fallback review.

    This is the opt-in, no-Altium fallback (component-level checks only); it
    is NOT the preferred review path. It is disabled unless the caller passes
    ``--offline`` or sets ``EDA_AGENT_HEADLESS_REVIEW=1``.

    Exit code: 0 clean, 1 if any finding at/above ``--fail-on`` (so CI fails
    the build), 2 if disabled or the file could not be read.
    """
    from .fileio.review import (
        ERROR,
        HEADLESS_DISABLED_MESSAGE,
        headless_review_enabled,
        review_project_file,
        to_sarif,
    )

    if not (getattr(args, "offline", False) or headless_review_enabled()):
        print(f"ERROR: {HEADLESS_DISABLED_MESSAGE}", file=sys.stderr)
        return 2

    try:
        report = review_project_file(args.file)
    except (ValueError, OSError) as e:
        print(f"ERROR: cannot read {args.file}: {e}", file=sys.stderr)
        return 2

    if getattr(args, "sarif", False):
        import json as _json
        from . import __version__ as _ver
        print(_json.dumps(to_sarif(report, tool_version=_ver), indent=2))
    elif getattr(args, "json", False):
        import json as _json
        print(_json.dumps(report, indent=2))
    else:
        s = report["summary"]
        print(f"Reviewed {report['file']}")
        if "sheet_count" in report:  # project review
            print(f"  {report['sheet_count']} sheet(s), "
                  f"{report['component_count']} components")
        elif "document" in report:  # single schematic sheet
            doc = report["document"]
            print(f"  {doc.get('title') or '(untitled)'}  "
                  f"rev {doc.get('revision') or '-'}  --  "
                  f"{report['component_count']} components, "
                  f"{len(report.get('net_names', []))} named nets")
        else:  # library review
            print(f"  {report['component_count']} library components")
        print(f"  {s.get('error', 0)} error(s), {s.get('warning', 0)} "
              f"warning(s), {s.get('info', 0)} info")
        for f in report["findings"]:
            tag = f["designator"] or "-"
            sheet = f" ({f['sheet']})" if f.get("sheet") else ""
            print(f"  [{f['severity'].upper():7}] {tag:6} {f['check']}: "
                  f"{f['message']}{sheet}")
    # Gating: exit 1 if any finding at or above the --fail-on threshold.
    # Default "error" preserves the prior behavior.
    fail_on = getattr(args, "fail_on", "error")
    order = {"info": 0, "warning": 1, "error": 2}
    if fail_on == "never":
        return 0
    threshold = order.get(fail_on, 2)
    s = report["summary"]
    triggered = sum(
        s.get(sev, 0) for sev, rank in order.items() if rank >= threshold
    )
    return 1 if triggered > 0 else 0


def _offline_gate_ok(args) -> bool:
    """Shared opt-in check for the offline (no-Altium) CLI fallbacks."""
    from .fileio.review import (
        HEADLESS_DISABLED_MESSAGE,
        headless_review_enabled,
    )
    if getattr(args, "offline", False) or headless_review_enabled():
        return True
    print(f"ERROR: {HEADLESS_DISABLED_MESSAGE}", file=sys.stderr)
    return False


def _run_bom(args) -> int:
    """Handle ``eda-agent bom <file>`` -- offline consolidated BOM.

    Opt-in, no-Altium fallback. Exit 0 on success, 2 if disabled/unreadable.
    """
    if not _offline_gate_ok(args):
        return 2
    from .fileio.bom import bom_from_file, bom_to_csv

    try:
        lines = bom_from_file(args.file)
    except (ValueError, OSError) as e:
        print(f"ERROR: cannot read {args.file}: {e}", file=sys.stderr)
        return 2

    if getattr(args, "csv", False):
        print(bom_to_csv(lines), end="")
    elif getattr(args, "json", False):
        import json as _json
        print(_json.dumps(lines, indent=2))
    else:
        parts = sum(ln["quantity"] for ln in lines)
        print(f"BOM for {args.file}: {len(lines)} line(s), {parts} parts")
        for ln in lines:
            print(f"  {ln['quantity']:3}x  {', '.join(ln['designators']):24}  "
                  f"{ln['value'] or '-':10}  {ln['mpn'] or '-'}")
    return 0


def _run_netlist(args) -> int:
    """Handle ``eda-agent netlist <file>`` -- offline netlist + connectivity.

    Opt-in, no-Altium fallback. Reconstructs the netlist geometrically and
    runs connectivity ERC. Exit 0 clean, 1 if any connectivity finding at/
    above ``--fail-on``, 2 if disabled/unreadable.
    """
    if not _offline_gate_ok(args):
        return 2
    from .fileio.netlist_solver import solve_schematic_nets
    from .fileio.review import review_connectivity

    try:
        solved = solve_schematic_nets(args.file)
    except (ValueError, OSError, KeyError) as e:
        print(f"ERROR: cannot solve {args.file}: {e}", file=sys.stderr)
        return 2

    findings = review_connectivity(solved)
    if getattr(args, "sarif", False):
        import json as _json
        from . import __version__ as _ver
        from .fileio.review import to_sarif
        report = {"file": str(args.file), "findings": findings}
        print(_json.dumps(to_sarif(report, tool_version=_ver), indent=2))
    elif getattr(args, "json", False):
        import json as _json
        print(_json.dumps({"nets": {n: [f"{m['component']}.{m['pin']}"
                                        for m in v]
                                    for n, v in solved["nets"].items()},
                           "findings": findings}, indent=2))
    else:
        print(f"Netlist for {args.file}: {len(solved['nets'])} nets, "
              f"{len(solved['pin_nets'])} pins")
        for f in findings:
            tag = f["designator"] or "-"
            print(f"  [{f['severity'].upper():7}] {tag:6} {f['check']}: "
                  f"{f['message']}")

    fail_on = getattr(args, "fail_on", "error")
    if fail_on == "never":
        return 0
    order = {"info": 0, "warning": 1, "error": 2}
    threshold = order.get(fail_on, 2)
    triggered = any(order.get(f["severity"], 2) >= threshold for f in findings)
    return 1 if triggered else 0


def main() -> int:
    """CLI entry point.

    Subcommands:
      serve             -- run the MCP server (default when no args given)
      scripts-path      -- print the path to the bundled DelphiScript files
      install-scripts   -- copy bundled scripts to a chosen directory

    IMPORTANT: when invoked with no arguments, this MUST start the MCP
    server on stdio -- MCP-compatible clients rely on that behaviour.
    """
    parser = argparse.ArgumentParser(
        prog="eda-agent",
        description=(
            "MCP server bridge for Altium Designer. "
            "Run with no arguments to start the MCP server on stdio."
        ),
    )
    # The bug-report template and CONTRIBUTING both ask reporters for
    # `eda-agent --version`. Without this the flag was an argparse error,
    # so the first thing a bug reporter was told to run did not work.
    from . import __version__ as _pkg_version
    parser.add_argument(
        "--version", action="version",
        version=f"eda-agent {_pkg_version}",
    )
    # Top-level flag so `eda-agent --no-dashboard` works without the
    # `serve` subcommand. Important: most MCP clients invoke the binary
    # with NO arguments, so this needs to attach at the top level.
    parser.add_argument(
        "--no-dashboard", action="store_true",
        help=("Skip the in-process web dashboard. Required by strict "
              "MCP stdio clients (Codex, etc) that can't tolerate the "
              "dashboard thread's side-effects. Equivalent to setting "
              "EDA_AGENT_DISABLE_DASHBOARD=1 / EDA_AGENT_HEADLESS=1."),
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Alias for --no-dashboard.",
    )
    # Top-level so `eda-agent --backend kicad` works with no subcommand, the
    # way MCP clients invoke the binary. The real selection happens at import
    # from EDA_AGENT_BACKEND; when this flag names a different backend we
    # re-exec with that env set so registration runs against the right one.
    parser.add_argument(
        # Choices come from BACKENDS rather than a literal list: adding a
        # backend to the registry and forgetting this line rejects the new
        # name here while EDA_AGENT_BACKEND accepts it, so the flag and the
        # variable disagree about what exists. That is how 'easyeda' was
        # unreachable from the CLI after the backend itself worked.
        "--backend", choices=BACKENDS, default=None,
        help=("Which EDA tool to drive (default: altium). Selects the tool "
              "surface: 'altium' is the full Altium suite, 'kicad' the "
              "KiCad-native tools, 'easyeda' the EasyEDA Pro tools, 'both' "
              "the union of Altium and KiCad. Equivalent to setting "
              "EDA_AGENT_BACKEND."),
    )
    parser.add_argument(
        "--toolset", choices=TOOLSETS, default=None,
        help=("How many tools to advertise (default: full). 'minimal' "
              "exposes only tool_catalog and tool_invoke, keeping every "
              "other tool reachable through them, for MCP clients that "
              "limit tool count or are slow to load hundreds of schemas. "
              "Equivalent to setting EDA_AGENT_TOOLSET."),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # serve -- default when no args given
    serve_p = subparsers.add_parser(
        "serve",
        help="Run the MCP server on stdio (default when no args given)",
    )
    serve_p.add_argument(
        "--no-dashboard", action="store_true",
        help="Skip the in-process web dashboard (see top-level flag).",
    )
    serve_p.add_argument(
        "--headless", action="store_true",
        help="Alias for --no-dashboard.",
    )
    serve_p.add_argument(
        "--backend", choices=BACKENDS, default=None,
        help="Which EDA tool to drive (see top-level flag).",
    )

    # scripts-path
    subparsers.add_parser(
        "scripts-path",
        help="Print the path to the bundled DelphiScript files",
    )

    # install-scripts
    install_p = subparsers.add_parser(
        "install-scripts",
        help="Copy bundled scripts to a directory of your choice",
    )
    install_p.add_argument(
        "--dest",
        help=r"Destination directory (default: %%USERPROFILE%%\EDA Agent\scripts)",
    )
    install_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing scripts without prompting",
    )

    # health -- offline, fast
    subparsers.add_parser(
        "health",
        help="Fast offline preconditions (workspace, pointer file, scripts)",
    )

    # doctor -- full preflight, talks to Altium
    doctor_p = subparsers.add_parser(
        "doctor",
        help="Full preflight: workspace + Altium + version + canary IPC calls",
    )
    doctor_p.add_argument(
        "--library",
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Optional .SchLib path to test reachability. Repeat for "
            "multiple libs. The doctor never crawls; it only tests "
            "paths you supply."
        ),
    )
    doctor_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the text report.",
    )

    # dashboard -- local web UI for the MCP bridge
    dash_p = subparsers.add_parser(
        "dashboard",
        help=(
            "Launch the local web dashboard. Open http://127.0.0.1:8766 "
            "to see live MCP activity, performance, and health. The "
            "in-Altium status form has an 'Open Dashboard' button that "
            "auto-launches this server's URL via a workspace sentinel."
        ),
    )
    dash_p.add_argument("--host", default="127.0.0.1")
    dash_p.add_argument("--port", type=int, default=8766)
    dash_p.add_argument("--debug", action="store_true")

    # stop-dashboard -- terminate the dashboard process by PID file
    stop_dash_p = subparsers.add_parser(
        "stop-dashboard",
        help=("Stop the dashboard process (reads workspace/dashboard.pid). "
              "Use this when the dashboard was launched detached by the "
              "Altium script and you want to kill it without rebooting."),
    )

    # vote -- pairwise-preference vote UI in the browser
    vote_p = subparsers.add_parser(
        "vote",
        help=(
            "Launch the pairwise layout-preference vote UI in your "
            "browser. Generates two layouts of the same plan; you click "
            "the better one. Builds training data for the quality model."
        ),
    )
    vote_p.add_argument("--plan", required=True, type=Path,
                        help="Path to the DesignPlan JSON to vote on.")
    vote_p.add_argument("--symbols", type=Path, default=None,
                        help="Symbol fixtures JSON for offline mode. "
                             "Omit to use the live Altium bridge.")
    vote_p.add_argument("--host", default="127.0.0.1")
    vote_p.add_argument("--port", type=int, default=8765)
    vote_p.add_argument("--debug", action="store_true")

    # review -- OFFLINE FALLBACK, opt-in schematic review (no Altium).
    # Not the preferred path: the live-Altium tools see connectivity this
    # reader can't. Disabled unless --offline / EDA_AGENT_HEADLESS_REVIEW=1.
    review_p = subparsers.add_parser(
        "review",
        help=(
            "Offline FALLBACK design review of a .SchDoc/.PrjPcb -- parses "
            "the file directly (no running Altium, no license) for the "
            "component-level subset only (missing MPN / datasheet, "
            "placeholder values, designator collisions). NOT the preferred "
            "path -- prefer the live tools when Altium is available. Opt-in: "
            "requires --offline (or EDA_AGENT_HEADLESS_REVIEW=1)."
        ),
    )
    review_p.add_argument("file", type=Path,
                          help="Path to a .SchDoc sheet or a .PrjPcb project "
                               "(reviews all its sheets).")
    review_p.add_argument("--offline", action="store_true",
                          help="Opt in to the no-Altium file-reader review. "
                               "Required (this review is off by default); the "
                               "live-Altium tools are preferred when a session "
                               "is available.")
    review_p.add_argument("--json", action="store_true",
                          help="Emit the full report as JSON.")
    review_p.add_argument("--sarif", action="store_true",
                          help="Emit SARIF 2.1.0 (for GitHub code scanning / "
                               "PR annotations).")
    review_p.add_argument("--fail-on", dest="fail_on", default="error",
                          choices=["error", "warning", "info", "never"],
                          help="Exit non-zero when a finding at or above this "
                               "severity exists (default: error).")

    # bom -- offline consolidated BOM (opt-in, no Altium).
    bom_p = subparsers.add_parser(
        "bom",
        help=("Offline consolidated BOM from a .SchDoc/.PrjPcb (no running "
              "Altium). Opt-in: requires --offline (or "
              "EDA_AGENT_HEADLESS_REVIEW=1). Prefer live proj_get_bom."))
    bom_p.add_argument("file", type=Path, help="A .SchDoc or .PrjPcb.")
    bom_p.add_argument("--offline", action="store_true",
                       help="Opt in to the no-Altium reader (required).")
    bom_p.add_argument("--csv", action="store_true", help="Emit CSV.")
    bom_p.add_argument("--json", action="store_true", help="Emit JSON.")

    # netlist -- offline geometric netlist + connectivity ERC (opt-in).
    net_p = subparsers.add_parser(
        "netlist",
        help=("Offline netlist reconstruction + connectivity ERC "
              "(single_pin_net, net_short) from a .SchDoc (no running "
              "Altium). Opt-in: requires --offline. Prefer live "
              "proj_get_nets/proj_run_erc."))
    net_p.add_argument("file", type=Path, help="A .SchDoc sheet.")
    net_p.add_argument("--offline", action="store_true",
                       help="Opt in to the no-Altium solver (required).")
    net_p.add_argument("--json", action="store_true", help="Emit JSON.")
    net_p.add_argument("--sarif", action="store_true",
                       help="Emit SARIF 2.1.0 (GitHub code scanning / PR "
                            "annotations).")
    net_p.add_argument("--fail-on", dest="fail_on", default="error",
                       choices=["error", "warning", "info", "never"],
                       help="Exit non-zero on a finding at/above this "
                            "severity (default: error).")

    args = parser.parse_args()

    # Backend was already registered at import from EDA_AGENT_BACKEND. If
    # --backend asks for a different one, re-exec once with the env set so the
    # reload registers the right tool surface. MCP clients pass the backend via
    # env, not this flag, so their startup never re-execs. A sentinel prevents
    # an exec loop if the child somehow disagrees.
    # --toolset needs the same treatment for the same reason: the surface
    # is chosen at import, so changing it after parsing is too late.
    requested = getattr(args, "backend", None)
    requested_toolset = getattr(args, "toolset", None)
    needs_backend = bool(requested) and requested != ACTIVE_BACKEND
    needs_toolset = (bool(requested_toolset)
                     and requested_toolset != ACTIVE_TOOLSET)
    if ((needs_backend or needs_toolset)
            and not os.environ.get("_EDA_AGENT_BACKEND_REEXEC")):
        if needs_backend:
            os.environ["EDA_AGENT_BACKEND"] = requested
        if needs_toolset:
            os.environ["EDA_AGENT_TOOLSET"] = requested_toolset
        os.environ["_EDA_AGENT_BACKEND_REEXEC"] = "1"
        os.execv(sys.executable,
                 [sys.executable, "-m", "eda_agent.server", *sys.argv[1:]])

    if args.command is None or args.command == "serve":
        # Honour the flag whether it was given at the top level
        # (`eda-agent --no-dashboard`) or on the serve subcommand
        # (`eda-agent serve --no-dashboard`). Either form should work.
        no_dash = bool(
            getattr(args, "no_dashboard", False)
            or getattr(args, "headless", False)
        )
        return serve_mcp(no_dashboard=no_dash)

    # Lazy import -- keeps the hot stdio path free of CLI-only deps.
    from . import cli

    if args.command == "scripts-path":
        return cli.cmd_scripts_path()
    if args.command == "install-scripts":
        return cli.cmd_install_scripts(dest=args.dest, force=args.force)
    if args.command == "dashboard":
        from .web.dashboard import main as dashboard_main
        return dashboard_main([
            "--host", args.host,
            "--port", str(args.port),
            *(["--debug"] if args.debug else []),
        ])
    if args.command == "stop-dashboard":
        # Read the PID written by the dashboard process and SIGTERM it.
        # No PID file = no dashboard running -> success (idempotent stop).
        import os as _os, signal as _sig
        pid_path = get_config().workspace_dir / "dashboard.pid"
        if not pid_path.exists():
            print("dashboard.pid not found -- dashboard not running "
                  "(or workspace mismatch).")
            return 0
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError) as e:
            print(f"could not read dashboard.pid: {e}")
            return 1
        try:
            if sys.platform == "win32":
                # On Windows, SIGTERM isn't honoured for native procs;
                # use TerminateProcess via taskkill for reliability.
                import subprocess as _sp
                _sp.run(["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True, check=False)
            else:
                _os.kill(pid, _sig.SIGTERM)
            print(f"stopped dashboard pid {pid}")
            try: pid_path.unlink()
            except OSError: pass
            return 0
        except Exception as e:
            print(f"could not stop dashboard pid {pid}: {e}")
            return 1
    if args.command == "vote":
        from .web.server import main as vote_main
        return vote_main([
            "--plan", str(args.plan),
            *(["--symbols", str(args.symbols)] if args.symbols else []),
            "--host", args.host,
            "--port", str(args.port),
            *(["--debug"] if args.debug else []),
        ])
    if args.command == "review":
        return _run_review(args)
    if args.command == "bom":
        return _run_bom(args)
    if args.command == "netlist":
        return _run_netlist(args)
    if args.command in ("health", "doctor"):
        from .diag.checks import format_report, overall_exit_code
        if args.command == "health":
            from .diag.health import run_health_checks
            checks = run_health_checks()
            title = "eda-agent health"
        else:
            from .diag.doctor import run_doctor_checks
            checks = run_doctor_checks(library_paths=args.library)
            title = "eda-agent doctor"

        emit_json = bool(getattr(args, "json", False))
        if emit_json:
            import json as _json
            payload = {
                "title": title,
                "checks": [
                    {
                        "name": c.name,
                        "status": c.status.value,
                        "message": c.message,
                        "fix": c.fix,
                        "severity": c.severity.value,
                    }
                    for c in checks
                ],
            }
            print(_json.dumps(payload, indent=2))
        else:
            print(format_report(checks, title=title))
        return overall_exit_code(checks)

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
