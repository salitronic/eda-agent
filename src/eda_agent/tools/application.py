# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Application-level tools for Altium Designer MCP Server."""

import re
from typing import Any, Optional
from .. import __version__ as _mcp_server_version
from ..bridge import get_bridge, AltiumNotRunningError
from ..cli import get_bundled_scripts_path
from .datasheet_hints import DATASHEET_RULES


_VERSION_RE = re.compile(r"SCRIPT_VERSION\s*=\s*'([^']+)'")

_SESSION_REMINDER = {
    "__DATASHEET_FIRST__": (
        "MANDATORY for this whole session: every device-related "
        "claim (pin function, pin number, rating, package, "
        "polarity, register, timing, threshold, behaviour, "
        "footprint pad map) requires the manufacturer datasheet, "
        "fetched and cited, BEFORE the answer. No exceptions. No "
        "'I'll assume'. No leaning on Comment / Description / "
        "Value, distributor copy, or prior-turn memory. WebSearch "
        "+ WebFetch the manufacturer PDF first, then answer with "
        "a section/page citation."
    ),
    "title": "Datasheet discipline, mandatory before every device-related claim",
    "rule": (
        "For ANY question that touches a real device (IC, sensor, "
        "regulator, MOSFET, connector, passive, anything), the "
        "manufacturer datasheet is the only authoritative source. "
        "Symbol metadata can be wrong, distributor pages can be "
        "wrong, your own prior-turn memory can be wrong. The "
        "datasheet is ground truth. Tool responses that surface "
        "component information carry a `_datasheet_guidance` "
        "block, treat it as a checklist, not an FYI."
    ),
    "datasheet_rules": DATASHEET_RULES,
}


def _bundled_script_version() -> Optional[str]:
    """Read SCRIPT_VERSION from the bundled Main.pas.

    Deliberately NOT cached: install-scripts can replace the bundle while
    this server keeps running, and a cached value then reports a false
    version mismatch (or false match) until the server restarts. The read
    is one small file on demand.

    Returns None if the file can't be found or parsed, in which case we
    skip the stale-cache comparison and just report whatever Altium
    reported.
    """
    try:
        main_pas = get_bundled_scripts_path() / "Main.pas"
        text = main_pas.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = _VERSION_RE.search(text)
    return m.group(1) if m else None


def register_meta_tools(mcp):
    """Register the backend-agnostic discovery/dispatch pair.

    These are deliberately NOT part of register_application_tools. They
    describe the tool surface itself rather than Altium, so every backend
    needs them: the KiCad backend previously had no tool_catalog at all,
    which also made the minimal toolset impossible there.
    """

    @mcp.tool()
    async def tool_catalog(
        category: str = "",
        maturity: str = "",
        interaction: str = "",
        query: str = "",
        with_description: bool = False,
        with_schema: bool = False,
    ) -> dict[str, Any]:
        """Discover tools by category / maturity / interaction without
        loading every schema.

        The surface has 350+ tools; loading them all strains a client's
        context. This meta-tool returns a filtered, classified index so a
        client can find the right tool first, then rely on its full schema.

        Filters (all optional, AND-combined):
            category: core, application, project, library, generic,
                schematic, pcb, audit, design, simulation, routing, meta,
                parts (and kicad on the KiCad backend; the EasyEDA tools
                file under the same subject headings as Altium's, so
                "pcb" means the same thing on either). "core" holds the
                EDA-agnostic main flow (review_design, get_board_info,
                list_components, list_nets, run_drc, run_erc) and is the
                usual starting point. Call with NO filter to get the live
                ``categories`` map rather than trusting this list.
            maturity: how far a tool can be exercised without Altium.
                ``offline`` needs no Altium at all; ``simulator`` is
                bridge-backed but every command it sends is implemented
                by the in-repo Altium simulator; ``live_only`` sends at
                least one command only real Altium answers. Filter on
                ``offline`` to find what runs with nothing open.
            interaction: readonly | silent | modal | partial. ``modal``
                tools pop a blocking Altium dialog; ``partial`` ones leave
                the job incomplete: plan around both.
            query: case-insensitive substring over the tool name (and
                description when ``with_description``).
            with_description: include the one-line summary per tool.
            with_schema: include each tool's parameters and required
                list. ESSENTIAL under the minimal toolset: no tool schema
                is loaded up front there, so calling tool_invoke without
                this means guessing argument names. Filter first, schemas
                are dropped past a cap so this cannot flood the very
                context the minimal toolset exists to protect.

        Returns ``{count, tools:[{name, category, maturity, interaction
        [, description][, parameters, required]}], categories:{cat: n}}``.
        """
        from .metadata import tool_metadata

        _SCHEMA_CAP = 40

        tools = await mcp.list_tools()
        q = query.lower().strip()
        out = []
        cat_counts: dict[str, int] = {}
        for t in tools:
            md = tool_metadata(t.name)
            if category and md["category"] != category:
                continue
            if maturity and md["maturity"] != maturity:
                continue
            if interaction and md["interaction"] != interaction:
                continue
            desc = (t.description or "").strip()
            if q and q not in t.name.lower() and q not in desc.lower():
                continue
            cat_counts[md["category"]] = cat_counts.get(md["category"], 0) + 1
            rec = dict(md)
            if with_description:
                rec["description"] = desc.split("\n", 1)[0][:200]
            if with_schema:
                # A FastMCP tool and a captured ToolSpec both expose
                # inputSchema, so this reads the same in either toolset.
                schema = getattr(t, "inputSchema", None) or {}
                rec["parameters"] = schema.get("properties", {})
                rec["required"] = schema.get("required", [])
            out.append(rec)
        out.sort(key=lambda r: (r["category"], r["name"]))
        result: dict[str, Any] = {
            "count": len(out),
            "categories": dict(sorted(cat_counts.items())),
            "tools": out,
        }
        if with_schema and len(out) > _SCHEMA_CAP:
            # Returning hundreds of schemas would defeat the purpose of
            # the minimal toolset, so drop them and say so rather than
            # truncate silently.
            for entry in out:
                entry.pop("parameters", None)
                entry.pop("required", None)
            result["schema_omitted"] = (
                f"{len(out)} tools matched, over the {_SCHEMA_CAP} cap; "
                f"narrow with category/query to get parameters")
        return result

    @mcp.tool()
    async def tool_invoke(name: str, arguments: Optional[dict] = None) -> dict[str, Any]:
        """Invoke any registered tool by name: the companion to
        `tool_catalog`.

        A client with a limited context can expose only the core tools plus
        this pair: discover a tool with `tool_catalog`, then run it here by
        name without ever loading its schema. ``arguments`` is the tool's
        keyword-argument dict.

        GET THE ARGUMENT NAMES FIRST. Under the minimal toolset no schema
        is loaded up front, so call
        ``tool_catalog(query="<name>", with_schema=True)`` and use the
        ``parameters``/``required`` it returns. Guessing is a real failure
        mode: several tools take names that look obvious but are not
        (``current_amps``, not ``current_a``), and the same tool can differ
        between backends.

        Returns the tool's own result (JSON-decoded), or ``{"error": ...}``
        for an unknown/disallowed name. Note: bypassing the schema means
        argument mistakes surface as the target tool's own error, not a
        pre-validation message.
        """
        import json as _json

        tools = await mcp.list_tools()
        known = {t.name for t in tools}
        if name not in known:
            return {"error": f"unknown tool: {name}"}
        # Prevent self-recursion; tool_catalog is fine to invoke.
        if name == "tool_invoke":
            return {"error": "tool_invoke cannot invoke itself"}
        try:
            result = await mcp.call_tool(name, arguments or {})
        except Exception as e:  # noqa: BLE001 - surface as data, not a crash
            return {"error": f"{name} failed: {e}", "tool": name}
        # FastMCP returns a list of content items; unwrap the tool's dict.
        content = result[0] if isinstance(result, tuple) else result
        if isinstance(content, list) and content and hasattr(content[0], "text"):
            try:
                return {"tool": name, "result": _json.loads(content[0].text)}
            except (ValueError, TypeError):
                return {"tool": name, "result": content[0].text}
        return {"tool": name, "result": content}


def register_application_tools(mcp):
    """Register application tools with the MCP server."""

    @mcp.tool()
    async def app_get_status() -> dict[str, Any]:
        """Check if Altium Designer is running and get status information.

        Returns information about the Altium Designer process including:
        - Whether Altium is running
        - Process ID
        - Executable path
        - Whether the MCP bridge is attached

        Returns:
            Dictionary with status information
        """
        bridge = get_bridge()
        return bridge.get_altium_status()

    @mcp.tool()
    async def app_context() -> dict[str, Any]:
        """Where you are, in one call. Run this before anything else.

        THE FOUR FACTS THAT EXPLAIN MOST "WHY DIDN'T THAT WORK". Each has
        cost a session on its own, and each was individually available
        and individually unasked for:

        * **Which document is focused, and what kind it is.** Nearly
          every tool acts on the focused document. A pcb_ tool aimed
          while a library is focused does not always fail; it can resolve
          some other open document and report success for work you did
          not ask for.
        * **Whether the running script matches the one on disk.**
          DelphiScript caches compiled units, so a deployed fix does
          nothing until the script project is reopened. Symptom: a fix
          that "did not work".
        * **What is unsaved.** An edit that is real in memory and absent
          from disk reads as a tool that silently did nothing.
        * **Whether the bridge is answering at all**, as opposed to
          Altium being up.

        This composes existing reads rather than adding a bridge call, so
        it is cheap and cannot disagree with the tools it summarises.

        WHAT IT DOES NOT TELL YOU: which library component is current.
        That is editor state with no read exposed, and the library
        authoring tools depend on it. If a lib_add_* call lands somewhere
        unexpected, set the target explicitly with
        ``lib_set_current_component`` rather than assuming.

        Returns:
            ``{"backend": ..., "bridge_answering": bool,
            "script_version_match": bool, "active_document": {...},
            "open_document_count": N, "unsaved": [paths],
            "warnings": [...], "next_step": ...}``.

            ``warnings`` is empty when nothing needs attention.
            ``next_step`` is the single thing worth doing first, or empty.
        """
        bridge = get_bridge()
        warnings: list[str] = []
        next_step = ""

        ping = await app_ping()
        answering = bool(ping.get("success"))
        version_match = bool(ping.get("version_match"))
        if not answering:
            warnings.append(ping.get("message", "the bridge is not answering"))
            next_step = (
                "Start the polling loop: File > Run Script > Altium_API > "
                "Dispatcher.pas > StartMCPServer."
            )
        elif not version_match:
            # Ranked above everything else on purpose. Every other answer
            # below is being produced by code that is not the code on
            # disk, so acting on them can be worse than not asking.
            warnings.append(ping.get("message", "stale script cache"))
            next_step = (
                "Close and reopen Altium_API.PrjScr so Altium recompiles. "
                "Until then the running script is not the deployed one."
            )

        active: dict[str, Any] = {}
        open_count = 0
        unsaved: list[str] = []
        if answering:
            try:
                active = await bridge.send_command_async(
                    "application.get_active_document") or {}
            except Exception as exc:            # noqa: BLE001
                warnings.append(f"could not read the active document: {exc}")
            try:
                docs = await bridge.send_command_async(
                    "application.get_open_documents") or []
                if isinstance(docs, dict):
                    docs = docs.get("documents", [])
                open_count = len(docs)
                unsaved = [d.get("file_path", "") for d in docs
                           if isinstance(d, dict) and d.get("modified")]
            except Exception as exc:            # noqa: BLE001
                warnings.append(f"could not list open documents: {exc}")

            if not active.get("file_path"):
                warnings.append(
                    "no document is focused, so any tool that acts on the "
                    "focused document has nothing to act on")
            if unsaved and not next_step:
                next_step = (
                    f"{len(unsaved)} document(s) have unsaved changes. "
                    "app_save_all flushes them, and reports still_dirty.")

        return {
            "backend": "altium",
            "bridge_answering": answering,
            "script_version_match": version_match,
            "running_script_version": ping.get("altium_script_version"),
            "deployed_script_version": ping.get("bundled_script_version"),
            "altium_version": ping.get("altium_version") or "",
            "active_document": active,
            "open_document_count": open_count,
            "unsaved": unsaved,
            "warnings": warnings,
            "next_step": next_step,
        }

    @mcp.tool()
    async def app_attach() -> dict[str, Any]:
        """Connect to a running Altium Designer instance.

        This verifies Altium is running and the polling script is responding.
        The Altium_API.PrjScr script must be running (StartMCPServer) in Altium.

        Returns:
            Dictionary with attachment status
        """
        bridge = get_bridge()
        try:
            bridge.attach()
            script_loaded = bridge.ping()
            return {
                "attached": True,
                "script_loaded": script_loaded,
                "message": "Connected to Altium Designer, script is responding"
                if script_loaded
                else "Altium is running but script is not responding. Run StartMCPServer in Altium_API.PrjScr.",
                "_system_reminder": _SESSION_REMINDER,
            }
        except AltiumNotRunningError as e:
            return {
                "attached": False,
                "script_loaded": False,
                "message": str(e),
                "_system_reminder": _SESSION_REMINDER,
            }

    @mcp.tool()
    async def app_save_all() -> dict[str, Any]:
        """Flush every dirty Altium document to disk.

        Mutation tools (pcb_place_tracks, move_component, modify_objects, ...)
        now mark documents as modified in-memory only. Changes stay fast
        because they skip per-operation disk writes. Call save_all at logical
        checkpoints, after a routing pass, before running DRC, or before
        closing, to persist everything.

        Detach also triggers save_all automatically, so you don't need this
        as the very last step.

        CHECK ``still_dirty``, DO NOT ASSUME. Altium refuses to save while
        a command is active in the editor and asks a human whether to
        write a copy instead; DoFileSave does not raise when it declines.
        MEASURED: this returned saved:true while every single save was
        being refused. It now counts what is still modified afterwards,
        and ``saved`` is false when anything is. If that happens, run
        ``app_exit_active_command`` and retry.

        Returns:
            Dict with ``saved``, ``still_dirty``, and a ``reason`` when
            documents remain unsaved.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.save_all", timeout=60.0
        )
        return result

    @mcp.tool()
    async def app_exit_active_command() -> dict[str, Any]:
        """Close a transaction a crashed handler left open in Altium.

        THE TOOL FOR "A command is currently active and save cannot be
        completed at this time". While that state is set, EVERY save is
        refused and the editor asks whether to write a copy instead.

        IT SURVIVES RESTARTING THE POLLING LOOP, because the state lives
        in Altium's PCB server rather than in the script, and Escape in
        the editor does not clear it either. MEASURED on 2026-08-25:
        saves were being refused on a brand-new library, and the state
        had been left hours earlier by ``lib_link_3d_model`` faulting
        between PreProcess and PostProcess on an undeclared identifier.
        It outlived several restarts.

        Every PreProcess in this bridge is paired, including on its error
        paths, so this is not routine cleanup: it is recovery from a
        handler that DIED mid-transaction. AltiumScriptCentral ships the
        same one-line remedy as ExitActiveCommand.vbs.

        Calling it when nothing is outstanding is harmless.

        It saves nothing by itself. Run ``app_save_all`` afterwards and
        check ``still_dirty``.

        Returns:
            Dict reporting whether each server accepted the unwind, plus
            the count of still-modified documents before and after.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.exit_active_command", timeout=30.0
        )
        return result

    async def _resolve_project_dir() -> tuple[Optional[str], Optional[str], Optional[dict]]:
        """Return (project_dir, project_file, error_payload).

        Fetches the focused project's path from the bridge. On failure the
        third element is a ready-to-return error dict and the first two are
        None.
        """
        bridge = get_bridge()
        info = await bridge.send_command_async("project.get_project_path")
        project_dir = (info or {}).get("project_dir")
        if not project_dir:
            return None, None, {
                "error": "no focused project (open a project in Altium first)",
                "checkpoint": None,
            }
        return project_dir, (info or {}).get("project_name", ""), None

    def _checkpoint_store():
        from ..config import get_config
        from ..checkpoint import CheckpointStore
        return CheckpointStore(get_config().workspace_dir / "checkpoints")

    @mcp.tool()
    async def app_checkpoint(label: str = "", save_first: bool = True) -> dict[str, Any]:
        """Snapshot the focused project so the session is revertible.

        Copies the project's design files into a content-addressed store under
        the workspace (unchanged files are deduplicated, so repeat checkpoints
        are cheap). Take one before any risky autonomous edit; restore with
        `app_restore_checkpoint`. This is the undo the live bridge otherwise
        lacks.

        Args:
            label: optional human note ("before routing pass").
            save_first: flush dirty Altium docs to disk before snapshotting
                so the checkpoint reflects in-editor changes (default True).

        Returns:
            The checkpoint manifest summary (id, created, file_count, ...).
            With ``save_first``, also ``saved``: True when the flush
            worked, False with ``save_error`` and a ``note`` when it did
            not. A checkpoint is still taken either way, but one taken
            after a failed save holds the on-disk state only, which is
            missing precisely the in-editor work you were protecting.
        """
        saved: Optional[bool] = None
        save_error = ""
        if save_first:
            try:
                await get_bridge().send_command_async(
                    "application.save_all", timeout=60.0
                )
                saved = True
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                # Still snapshot the on-disk state: a checkpoint of
                # something beats none. But SAY SO. A checkpoint is a
                # safety net, and one taken after a failed save is
                # missing exactly the in-editor work the caller was
                # protecting.
                saved = False
                save_error = str(exc)
        project_dir, project_file, err = await _resolve_project_dir()
        if err:
            return err
        from pathlib import Path
        store = _checkpoint_store()
        info = store.create(Path(project_dir), project_file=project_file or "", label=label)
        result: dict[str, Any] = {"checkpoint": info.summary()}
        if save_first:
            result["saved"] = saved
            if not saved:
                result["save_error"] = save_error
                result["note"] = (
                    "save_all failed, so this checkpoint holds the "
                    "on-disk state only. Unsaved editor changes are NOT "
                    "in it."
                )
        return result

    @mcp.tool()
    async def app_list_checkpoints() -> dict[str, Any]:
        """List saved checkpoints for the current workspace, newest first."""
        store = _checkpoint_store()
        return {"checkpoints": [c.summary() for c in store.list()]}

    @mcp.tool()
    async def app_restore_checkpoint(
        checkpoint_id: str, prune_added: bool = False
    ) -> dict[str, Any]:
        """Restore the focused project's files from a checkpoint.

        Overwrites design files with the snapshot contents. Close the project
        in Altium (or expect a reload prompt) before restoring, since Altium
        holds documents open. With `prune_added=True` this is a true revert:
        files created after the checkpoint are deleted; the default leaves
        newer files untouched.

        Args:
            checkpoint_id: id from `app_list_checkpoints`.
            prune_added: delete files absent at checkpoint time (default False).

        Returns:
            {restored, removed, missing_blobs}.
        """
        project_dir, _project_file, err = await _resolve_project_dir()
        if err:
            return err
        from pathlib import Path
        store = _checkpoint_store()
        try:
            return store.restore(
                checkpoint_id, Path(project_dir), prune_added=prune_added
            )
        except FileNotFoundError as e:
            return {"error": str(e)}

    @mcp.tool()
    async def app_detach() -> dict[str, Any]:
        """Stop the Altium MCP polling loop. CALL THIS WHEN YOU'RE FINISHED.

        While the eda-agent MCP server is connected, a keep-alive thread pings
        Altium every 30 s, which keeps Altium's scripting engine held by the
        polling loop, Altium's own script-backed UI commands (some ribbon
        buttons, Parameter Manager actions, etc.) may be unresponsive until the
        loop is released.

        Call this tool once you've finished your Altium work for the session.
        It flushes every dirty document via save_all, then sends
        application.stop_server so the DelphiScript loop exits cleanly
        within ~500 ms and stops the Python keep-alive. Altium becomes
        immediately fully responsive.

        NOTE: After detach, the Altium script has fully stopped. To run more
        MCP tools later in the same Altium session the user must re-launch
        StartMCPServer via File -> Run Script. Don't detach until you're
        confident you're done.

        Returns:
            Dictionary confirming detachment
        """
        bridge = get_bridge()
        try:
            await bridge.send_command_async("application.stop_server", timeout=60.0)
        except Exception:
            pass  # Server may already be stopped
        bridge.detach()
        return {
            "attached": False,
            "message": "Detached from Altium Designer and stopped MCP server",
        }

    @mcp.tool()
    async def app_ping() -> dict[str, Any]:
        """Test if the Altium script is responding and report script version.

        Verifies that:
        1. Altium Designer is running
        2. The Altium_API.PrjScr script is running (StartMCPServer)
        3. File-based communication is working

        Also reads SCRIPT_VERSION from the .pas that Altium has compiled
        and compares it to the version in the bundled on-disk Main.pas.
        A mismatch means Altium is running a stale cached script, close
        and reopen Altium_API.PrjScr (or restart Altium) to recompile.

        Returns:
            Dictionary with:
            - success: True if Altium responded
            - mcp_server_version: version of this eda-agent Python package
              (from `eda_agent.__version__`), identifies the MCP server
              process currently handling tool calls
            - altium_script_version: version the running script reports
              (empty string if the script is too old to report it)
            - bundled_script_version: version of the on-disk Main.pas
            - version_match: True if Altium matches bundled script version
            - altium_version: the Altium build itself, as Altium reports
              it. Reported, never acted on: nothing here refuses to run
              on an old build, because most of the toolset works on one.
              It is here because a bug report without it costs a round
              trip, and twice the answer changed the diagnosis.
            - message: human-readable status (flags stale cache if detected)
        """
        bridge = get_bridge()
        if not bridge.is_altium_running():
            return {
                "success": False,
                "mcp_server_version": _mcp_server_version,
                "altium_script_version": None,
                "bundled_script_version": _bundled_script_version(),
                "version_match": False,
                "altium_version": "",
                "message": "Altium Designer is not running",
            }

        result = bridge.ping_with_version()
        bundled = _bundled_script_version()
        if result is None:
            return {
                "success": False,
                "mcp_server_version": _mcp_server_version,
                "altium_script_version": None,
                "bundled_script_version": bundled,
                "version_match": False,
                "altium_version": "",
                "message": "Altium script is not responding. Run StartMCPServer in Altium_API.PrjScr.",
            }

        altium_ver = result.get("script_version") or ""
        if bundled is None:
            match = False
            msg = "Altium script is responding (bundled version unknown)."
        elif altium_ver == "":
            match = False
            msg = (
                "Altium script is responding but predates version reporting. "
                "Close and reopen Altium_API.PrjScr to pick up the new code."
            )
        elif altium_ver == bundled:
            match = True
            msg = f"Altium script is responding (version {altium_ver})."
        else:
            match = False
            msg = (
                f"STALE SCRIPT CACHE: Altium is running version {altium_ver}, "
                f"but the on-disk bundle is {bundled}. Close and reopen "
                f"Altium_API.PrjScr (or restart Altium) to recompile."
            )

        return {
            "success": True,
            "mcp_server_version": _mcp_server_version,
            "altium_script_version": altium_ver,
            "bundled_script_version": bundled,
            "version_match": match,
            "altium_version": result.get("altium_version") or "",
            "message": msg,
            "_system_reminder": _SESSION_REMINDER,
        }

    @mcp.tool()
    async def app_get_report() -> dict[str, Any]:
        """Report the health of the MCP bridge, workspace, and Altium link.

        One call to diagnose the plumbing without touching the design:
        package version, the script version on disk vs. what Altium has
        compiled, whether Altium is up, the live IPC round-trip time, the
        workspace location, and how many request/response/stop files are
        sitting in it (a backlog of stale request_*.json usually means a
        dead or wedged poller).

        Returns:
            Dictionary with mcp_server_version, bundled_script_version,
            altium_running, altium_script_version, version_match,
            round_trip_ms (None if Altium is down), workspace_dir,
            workspace_exists, and a file_counts breakdown.
        """
        import time
        from ..config import get_config

        bridge = get_bridge()
        ws = get_config().workspace_dir
        ws_exists = ws.exists()

        counts = {"request": 0, "response": 0, "progress": 0, "stop": 0, "other": 0}
        if ws_exists:
            try:
                for entry in ws.iterdir():
                    if not entry.is_file():
                        continue
                    n = entry.name
                    if n.startswith("request_"):
                        counts["request"] += 1
                    elif n.startswith("response_"):
                        counts["response"] += 1
                    elif n.startswith("progress_"):
                        counts["progress"] += 1
                    elif n == "stop":
                        counts["stop"] += 1
                    else:
                        counts["other"] += 1
            except OSError:
                pass

        bundled = _bundled_script_version()
        running = bridge.is_altium_running()
        altium_ver: Optional[str] = None
        match = False
        round_trip_ms: Optional[float] = None
        if running:
            start = time.perf_counter()
            ping = bridge.ping_with_version()
            if ping is not None:
                round_trip_ms = round((time.perf_counter() - start) * 1000.0, 1)
                altium_ver = ping.get("script_version") or ""
                match = bundled is not None and altium_ver == bundled

        return {
            "mcp_server_version": _mcp_server_version,
            "bundled_script_version": bundled,
            "altium_running": running,
            "altium_script_version": altium_ver,
            "version_match": match,
            "round_trip_ms": round_trip_ms,
            "workspace_dir": str(ws),
            "workspace_exists": ws_exists,
            "file_counts": counts,
        }

    @mcp.tool()
    async def app_create_document(
        kind: str,
        file_path: str,
        name: Optional[str] = None,
        add_to_project: bool = True,
    ) -> dict[str, Any]:
        """Create a new blank document of a given kind and save it to disk.

        Wraps IClient.OpenNewDocument + DoFileSave. The new document is
        written to `file_path` and, by default, attached to the currently
        focused project. Use this to create a .PcbDoc before running
        update_pcb, to spin up a fresh .SchDoc, library, OutJob, etc.

        Args:
            kind: Document kind, 'PCB', 'SCH', 'PCBLIB', 'SCHLIB',
                'OUTPUTJOB', or any other kind Altium's server module
                registers under.
            file_path: Absolute path where the new document should live.
                Use Windows backslashes.
            name: Optional display name. Defaults to the filename.
            add_to_project: Attach the new file to the focused project.
                Default True. Set False to leave it as a free document.

        Returns:
            Dictionary with kind, file_path, saved, added_to_project.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "kind": kind,
            "file_path": file_path,
            "add_to_project": "true" if add_to_project else "false",
        }
        if name:
            params["name"] = name
        result = await bridge.send_command_async(
            "application.create_document", params
        )
        return result

    @mcp.tool()
    async def app_list_documents() -> list[dict[str, Any]]:
        """List all documents known to the current Altium workspace.

        Returns both project members and any free documents. Each entry
        carries a `loaded` flag that distinguishes "listed as project
        member on disk" from "actually resident in the editor".
        Project-scope queries (query_objects, batch_modify, ...) only
        iterate loaded sheets, if `loaded` is false for sheets you need
        to hit, call load_project_sheets first.

        Returns:
            List of document information dictionaries containing:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document (SCH, PCB, etc.)
            - loaded: True if the doc is resident in the editor server.
              False means it's a project member on disk whose editor
              state hasn't been opened yet.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_open_documents")
        return result

    @mcp.tool()
    async def app_diag_workspace(pattern: str = "request_*.json") -> dict[str, Any]:
        """Diagnostic: enumerate workspace files via Altium's FindFiles helper.

        Reports workspace_dir, the pattern used, match_count and the first
        10 matching filenames. Used to validate the per-request file
        enumeration that the dispatcher relies on.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "application.diag_workspace",
            {"pattern": pattern},
            timeout=10.0,
        )

    @mcp.tool()
    async def app_get_active_document() -> dict[str, Any]:
        """Get information about the currently active (focused) document.

        Returns:
            Dictionary with active document information:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document (SchDoc, PcbDoc, etc.)
            - modified: Whether the document has unsaved changes
            Returns empty dict if no document is active.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_active_document")
        return result

    @mcp.tool()
    async def app_set_active_document(file_path: str) -> dict[str, Any]:
        """Set a specific document as the active (focused) document.

        Args:
            file_path: Full path to the document to activate

        Returns:
            Dictionary with result of the operation
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.set_active_document", {"file_path": file_path}
        )
        if isinstance(result, dict):
            return {"success": True, "file_path": file_path, **result}
        elif result:
            return {"success": True, "file_path": file_path, "data": result}
        else:
            return {"success": True, "file_path": file_path}

    @mcp.tool()
    async def app_get_version() -> dict[str, Any]:
        """Get the version of Altium Designer.

        Uses Client.GetProductVersion internally. If that API is unavailable
        (older builds or restricted script context), the returned dictionary
        will omit "version" and include a "note" field instead.

        Returns:
            Dictionary with product_name and either:
            - version: Full version string (when Client.GetProductVersion works)
            - note: Explanation when the version API is unavailable
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_version")
        return result

    # ------------------------------------------------------------------
    # Preferences, menu execution, clipboard
    # ------------------------------------------------------------------

    @mcp.tool()
    async def app_get_preferences() -> dict[str, Any]:
        """Get key Altium Designer preferences.

        Returns PCB preferences (snap grid, display unit) from the active board
        and schematic preferences (visible/snap grid) from the active schematic.
        Values are null if no PCB or schematic is currently open.

        Returns:
            Dictionary with "pcb" and "schematic" sub-objects containing
            grid and unit settings
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_preferences")
        return result

    @mcp.tool()
    async def app_run_menu(menu_path: str) -> dict[str, Any]:
        """Dispatch a menu command by path. DISPATCH IS NOT EXECUTION.

        This sends the command and cannot tell you it ran. Altium's
        ``RunProcess`` accepts an unknown process id in silence and
        reports nothing back, so a typo, a command unavailable in the
        current editor, and a command that worked are indistinguishable
        from here. MEASURED 2026-08-17: "Tools|Preferences" returned in
        0.11s with ``success: true`` and no dialog ever appeared. That is
        why the reply says ``dispatched``, never ``executed``, and carries
        ``outcome_verified: false``.

        Use ``app_click_menu`` when you need the command to actually
        happen. It walks the real menu bar and clicks the item, so it
        fails loudly on a path that does not exist and reaches the many
        commands that have no process id at all.

        These paths are mapped to internal processes:
        - "File|Save All"
        - "Tools|Design Rule Check"
        - "Tools|Electrical Rules Check"
        - "Project|Compile"
        - "Edit|Select All" / "Edit|Deselect All"
        - "View|Zoom Fit"
        - "Tools|Preferences"
        - "Tools|Extensions and Updates"

        Anything else is REFUSED with UNMAPPED_MENU_PATH. The old
        fallback guessed a MenuID out of the pipe-separated path and was
        measured opening nothing while reporting success, so refusing
        and pointing at ``app_click_menu`` is strictly more useful than
        a guess that cannot work.

        Args:
            menu_path: Menu path using pipe separators (e.g., "File|Save All")

        Returns:
            Dictionary with dispatched, outcome_verified (always false),
            menu_path, and the process used. A reply the bridge could not
            parse is reported as a failure, not smoothed into a success.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.execute_menu", {"menu_path": menu_path}
        )
        # Report what the handler said, and nothing it did not say. The
        # previous ``{"success": True, **result}`` did preserve an
        # explicit ``success: false`` (the spread wins), but it INVENTED
        # a success for any reply that carried no verdict at all, and
        # ``result or {"success": True, ...}`` turned an empty reply
        # into a success outright. Both manufactured the one fact this
        # tool has no way to establish.
        if isinstance(result, dict):
            return result
        return {
            "success": False,
            "dispatched": False,
            "menu_path": menu_path,
            "reason": "the bridge returned no usable reply for "
                      f"{menu_path!r}, so nothing is known about it",
        }

    @mcp.tool()
    async def app_set_intent(intent: str) -> dict[str, Any]:
        """Tell the dashboard what high-level task the agent is working on.

        The text is written to ``workspace/intent.txt`` where the web
        dashboard polls it and shows a banner. Purely informational --
        does not affect tool dispatch in any way. Call this once at the
        start of a long task ("reviewing buck-converter feedback divider",
        "auto-placing the analog front-end on sheet B") so the user
        watching the dashboard knows what's happening between IPC
        events. Pass an empty string to clear the banner.

        Args:
            intent: Short human-readable description (one line, <=240 chars).

        Returns:
            ``{"ok": true, "intent": "<truncated text>"}``.
        """
        from ..config import get_config
        text = (intent or "").strip()
        if len(text) > 240:
            text = text[:240]
        path = get_config().workspace_dir / "intent.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if text:
                # Atomic write (temp + os.replace) so the Altium polling
                # loop only ever opens a complete, CLOSED file. A plain
                # write_text holds intent.txt open for write; if the loop
                # reads it in that window it hits a sharing violation that
                # the script engine surfaces as a modal. Invariant: every
                # workspace file the Pascal side reads MUST be written this
                # way (see request files, dashboard.heartbeat).
                import os as _os
                tmp = path.with_suffix(".txt.tmp")
                tmp.write_text(text, encoding="utf-8")
                _os.replace(tmp, path)
            elif path.exists():
                path.unlink()
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "intent": text}

    @mcp.tool()
    async def app_get_clipboard() -> dict[str, Any]:
        """Get text content from the Windows clipboard.

        Returns whatever text is currently on the clipboard, which can be
        useful for reading data copied from Altium dialogs or reports.

        Returns:
            Dictionary with "text" containing the clipboard content.
            Returns empty string if clipboard is empty or non-text.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("application.get_clipboard_text")
        return result
