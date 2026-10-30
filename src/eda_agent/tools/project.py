# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Project management tools for Altium Designer MCP Server."""

from typing import Any, Optional
from ..bridge import get_bridge
from ..units import MM_PER_MIL
from .option_hints import project_option_collision
from .datasheet_hints import tag_response
from .bulk_hints import BulkHintTracker
from ..export.variant_bom import apply_variant, find_variant


def _rows(result: Any, key: str) -> list:
    if isinstance(result, dict):
        inner = result.get("result", result)
        if isinstance(inner, dict):
            rows = inner.get(key)
            return rows if isinstance(rows, list) else []
    return []


async def _pair_names(bridge) -> set:
    try:
        pairs = await bridge.send_command_async(
            "pcb.get_differential_pairs", {}, timeout=30.0)
    except Exception:                           # noqa: BLE001 - no board yet
        return set()
    return {str(p.get("name")) for p in _rows(pairs, "pairs")
            if isinstance(p, dict) and p.get("name")}


async def _after_sync(bridge, pairs_before: set) -> dict:
    """What a schematic-to-board sync left undone, read from both sides."""
    bom = await bridge.send_command_async("project.get_bom", {"limit": "100000"})
    board = await bridge.send_command_async("pcb.get_components", {})
    sch = {str(c.get("designator")): str(c.get("footprint") or "")
           for c in _rows(bom, "components") if isinstance(c, dict)}
    pcb = {str(c.get("designator")): str(c.get("footprint") or "")
           for c in _rows(board, "components") if isinstance(c, dict)}
    mismatch = [{"designator": d, "schematic": fp, "board": pcb[d]}
                for d, fp in sorted(sch.items())
                if d in pcb and fp and pcb[d] and fp.lower() != pcb[d].lower()]
    missing = sorted(d for d in sch if d not in pcb)
    removed = sorted(pairs_before - await _pair_names(bridge))
    out: dict[str, Any] = {"footprint_mismatch": mismatch,
                           "missing_on_board": missing,
                           "diff_pairs_removed": removed}
    problems = []
    if mismatch:
        problems.append(f"{len(mismatch)} part(s) kept a footprint the "
                        f"schematic no longer uses")
    if missing:
        problems.append(f"{len(missing)} schematic part(s) are not on the board")
    if removed:
        problems.append(f"the sync removed {len(removed)} board-only "
                        f"differential pair(s); define them in the schematic "
                        f"(DifferentialPair directive) to keep them")
    if problems:
        out["warning"] = "; ".join(problems)
    return out


#: Altium's "no colour set" for a net (bit 29), measured on AD 26.11.
_NO_NET_COLOR = 536870912


#: The net identifier scope in Altium's own order (TFlattenMode).
_HIERARCHY_MODES = ("automatic", "flat", "hierarchical", "global",
                    "strict_hierarchical")


#: The STEP exporter settings a script can write, as the bridge names them.
STEP_OPTIONS = {
    "folded": bool, "fold_rate": int, "component_option": int,
    "models_option": int, "holes_option": int, "copper_option": int,
    "mechanical_pad_holes": bool, "electrical_pad_holes": bool,
    "free_pad_holes": bool, "barrels_only": bool, "cover_layer": bool,
    "component_suffix_type": int, "component_suffix": str,
}


def _dir_stamps(folder: str) -> dict[str, float]:
    """Every file under ``folder`` with its modification time."""
    import os
    out: dict[str, float] = {}
    for root, _dirs, files in os.walk(folder):
        for f in files:
            path = os.path.join(root, f)
            try:
                out[path] = os.path.getmtime(path)
            except OSError:
                pass
    return out


def _step_option_params(options: Optional[dict]) -> dict[str, str]:
    """STEP exporter settings as bridge parameters; raises on an unknown one."""
    out: dict[str, str] = {}
    for name, value in (options or {}).items():
        kind = STEP_OPTIONS.get(name)
        if kind is None:
            raise ValueError(f"unknown STEP option {name!r}; known: {sorted(STEP_OPTIONS)}")
        if kind is bool:
            out[name] = "true" if value else "false"
        elif kind is int:
            out[name] = str(int(value))
        else:
            out[name] = str(value)
            out["set_component_suffix"] = "true"
    return out


def register_project_tools(mcp):
    """Register project tools with the MCP server."""

    @mcp.tool()
    async def proj_create(
        project_path: str,
        project_type: str = "PCB",
    ) -> dict[str, Any]:
        """Create a new Altium project.

        Args:
            project_path: Full path for the new project file (.PrjPcb, .PrjLib, etc.)
            project_type: Type of project to create:
                - "PCB": PCB project (.PrjPcb)
                - "IntegratedLibrary": Integrated library project
                - "ScriptProject": Script project

        Returns:
            Dictionary with created project information
        """
        # Ensure the parent directory exists. The Altium side writes the
        # project stub with Rewrite(), which throws EInOutError ("Invalid
        # file name") into a blocking IDE modal when the folder is missing.
        # Creating it here (the server runs on the same host as Altium)
        # keeps that modal from ever firing.
        import os
        parent = os.path.dirname(project_path)
        if parent:
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError:
                pass
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.create",
            {"project_path": project_path, "project_type": project_type},
        )
        return result

    @mcp.tool()
    async def proj_open(project_path: str) -> dict[str, Any]:
        """Open an existing Altium project.

        Args:
            project_path: Full path to the project file

        Returns:
            Dictionary with opened project information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.open", {"project_path": project_path}
        )
        return result

    @mcp.tool()
    async def proj_save(project_path: Optional[str] = None) -> dict[str, Any]:
        """Save the current or specified project.

        Args:
            project_path: Optional path to specific project. If None, saves active project.

        Returns:
            Dictionary confirming save operation
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.save", params)
        return result

    @mcp.tool()
    async def proj_close(
        project_path: Optional[str] = None, save: bool = True
    ) -> dict[str, Any]:
        """Close a project, saving its documents (default) or discarding
        their unsaved edits (save=False).

        Altium closes the FOCUSED project, so this focuses the named one
        first and refuses if it cannot; if any other project closes, it
        stops and reports it as ``closed_instead``. Without saving, it
        clears each member's modified flag so no save prompt appears; if
        one appears anyway it answers "Save None" then "OK", but only
        when every listed document belongs to this project, else it
        presses nothing. A document shared with another
        open project loses its edits too. A prompt left open blocks the
        bridge: answer it with app_press_dialog_button.

        Args:
            project_path: Project to close; omit for the focused one (a
                save=False prompt is then left for a human).
            save: Write modified documents first (True) or discard (False).

        Returns:
            ``success``, ``closed`` (checked by looking again),
            ``project_path``; ``prompts_answered`` and ``discarded`` when a
            prompt was answered, ``prompt`` and ``reason`` when one was
            left; otherwise ``discarded`` (members closed unsaved, not
            proof of edits) and ``attempts``.
        """
        import asyncio as _asyncio

        bridge = get_bridge()
        params = {"save": save}
        if project_path:
            params["project_path"] = project_path
        members = None
        if not save and project_path:
            members = await _close_members(bridge, project_path)
        try:
            result = await bridge.send_command_async("project.close", params)
        except Exception as exc:                    # noqa: BLE001
            prompt = _close_prompt_in(exc)
            if prompt is None or save:
                raise
            left = {"success": False, "closed": False,
                    "project_path": project_path, "prompt": prompt["title"],
                    "prompts_answered": 0, "discarded": []}
            if members is None:
                left["reason"] = (
                    "Altium is asking whether to save, and this does not know "
                    "which documents belong to the project: either no "
                    "project_path was given, or the deployed script reports "
                    "member file names rather than paths. Nothing was pressed, "
                    "and the bridge is blocked until the prompt is answered")
                return left

            loop = _asyncio.get_running_loop()
            answered, discarded, refusal = await loop.run_in_executor(
                None, _answer_close_prompts, bridge, members, prompt)

            closed = False
            if refusal is None:
                try:
                    listing = await bridge.send_command_async(
                        "project.get_open_projects", {})
                    open_paths = {
                        str(p.get("project_path", "")).upper()
                        for p in (listing or {}).get("projects", [])}
                    closed = str(project_path).upper() not in open_paths
                except Exception:                   # noqa: BLE001
                    closed = False
            out = {"success": closed, "closed": closed,
                   "project_path": project_path, "saved": False,
                   "prompts_answered": answered,
                   "discarded": sorted(set(discarded))}
            if refusal is not None:
                out["prompt"] = prompt["title"]
                out["reason"] = refusal
            elif not closed:
                out["reason"] = (
                    "the save prompt was answered and the project is still "
                    "open. Reported on a managed project, where calling "
                    "proj_close again closed it")
            return out
        else:
            return _dedupe_discarded(result)

    # Helpers for proj_close.

    def _dedupe_discarded(reply):
        """One entry per path in ``discarded``, first occurrence kept.

        MEASURED: a two-attempt close listed the project file twice,
        because the handler clears again on the second attempt and appends
        what it cleared.
        """
        if isinstance(reply, dict) and isinstance(reply.get("discarded"), list):
            seen: set = set()
            unique = []
            for path in reply["discarded"]:
                key = str(path).upper()
                if key not in seen:
                    seen.add(key)
                    unique.append(path)
            reply = {**reply, "discarded": unique}
        return reply

    # The helpers here are defined after proj_close in this scope, which is
    # fine: the tool looks them up when it is CALLED, after registration
    # finishes.

    #: Titles of Altium's close-time save prompt, before and after "Save
    #: None" is pressed. MEASURED 2026-09-13 on AD 26.10.1.6.
    _CLOSE_PROMPT_TITLES = ("Confirm Save", "Confirm Not Saving")

    def _is_absolute(path: str) -> bool:
        return len(path) > 2 and (path[1] == ":" or path.startswith("\\\\"))

    async def _close_members(bridge, project_path):
        """Upper-cased absolute paths a close of this project may discard.

        Its members and the project file. None when membership cannot be
        established, which disables answering any prompt: a file NAME
        cannot tell two projects' documents apart.
        """
        try:
            docs = await bridge.send_command_async(
                "project.get_documents", {"project_path": project_path})
        except Exception:                           # noqa: BLE001
            return None
        if isinstance(docs, dict):
            docs = docs.get("documents", [])
        if not isinstance(docs, list):
            return None
        paths = {str(project_path).upper()}
        for doc in docs:
            path = str(doc.get("file_path", "")) if isinstance(doc, dict) else ""
            if not _is_absolute(path):
                return None
            paths.add(path.upper())
        return paths

    def _close_prompt_in(exc):
        """The close-time save prompt a blocked-bridge error names, or None."""
        report = (getattr(exc, "details", None) or {}).get("dialogs") or {}
        if not isinstance(report, dict):
            return None
        for dialog in report.get("dialogs", []):
            title = str(dialog.get("title", ""))
            if title.startswith(_CLOSE_PROMPT_TITLES):
                return {"title": title, "hwnd": dialog.get("hwnd")}
        return None

    def _answer_close_prompts(bridge, members, prompt, budget=30.0):
        """Answer the save prompt: Save None, then OK. Runs in a thread.

        Returns (answered, discarded, refusal). Presses nothing unless
        every document the prompt lists is in ``members``. Loops because
        a close retried by the handler can raise a second prompt.
        """
        import time as _time
        from ..ui import uia, windows

        if not (windows.available() and uia.available()):
            return 0, [], ("UI Automation is not available here, so the "
                           "prompt was left open")
        if not windows.automation_enabled():
            return 0, [], (f"UI automation is disabled by "
                           f"{windows.UI_AUTOMATION_ENV}, so the prompt was "
                           f"left open")
        status = bridge.get_altium_status()
        pid = status.get("pid")
        if not pid:
            return 0, [], (status.get("reason") or "no Altium process to "
                           "answer the prompt in, so it was left open")

        answered, discarded = 0, []
        hwnd = prompt.get("hwnd")
        deadline = _time.monotonic() + budget
        while hwnd and _time.monotonic() < deadline:
            read = uia.describe_window(hwnd, depth=6, limit=400)
            listed = sorted({e.get("name", "") for e in read.get("elements", [])
                             if _is_absolute(e.get("name", ""))})
            if not listed:
                return answered, discarded, (
                    "could not read which documents the prompt would discard, "
                    "so nothing was pressed")
            foreign = [p for p in listed if p.upper() not in members]
            if foreign:
                return answered, discarded, (
                    "the prompt would also discard documents outside this "
                    "project, so nothing was pressed: " + "; ".join(foreign))
            if not uia.invoke(hwnd, "Save None").get("ok"):
                return answered, discarded, (
                    "Save None could not be pressed, so OK was not pressed "
                    "either")
            # OK with the decisions still on Save would SAVE. The retitle
            # is the only visible sign the decisions changed.
            if not windows.wait_until(
                    lambda: windows.window_title(hwnd).startswith(
                        "Confirm Not Saving"), 5.0):
                return answered, discarded, (
                    "Save None did not change the decisions within 5 s, so OK "
                    "was not pressed: pressed now it would save")
            if not uia.invoke(hwnd, "OK").get("ok"):
                return answered, discarded, "OK could not be pressed"
            windows.wait_for_close(hwnd, timeout=10.0)
            answered += 1
            discarded.extend(listed)

            found = {"hwnd": None}

            def another() -> bool:
                for dialog in windows.dialogs(pid):
                    if (dialog.hwnd != hwnd and (dialog.title or "")
                            .startswith(_CLOSE_PROMPT_TITLES)):
                        found["hwnd"] = dialog.hwnd
                        return True
                return False

            windows.wait_until(another, 3.0)
            hwnd = found["hwnd"]
        return answered, discarded, None

    @mcp.tool()
    async def proj_list_documents(
        project_path: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List all documents in a project.

        Args:
            project_path: Optional path to specific project. If None, uses active project.

        Returns:
            List of document information dictionaries containing:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_documents", params)
        return result

    @mcp.tool()
    async def proj_add_document(
        document_path: str, project_path: Optional[str] = None
    ) -> dict[str, Any]:
        """Add an existing document to a project.

        Args:
            document_path: Full path to the document to add
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        params = {"document_path": document_path}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.add_document", params)
        return result

    @mcp.tool()
    async def proj_remove_document(
        document_path: str,
        project_path: Optional[str] = None,
        save_project: bool = True,
    ) -> dict[str, Any]:
        """Remove a document from a project.

        Note: This only removes the document from the project, it doesn't delete the file.

        The project file is saved and read back, and leftover [DocumentN]
        sections past the new count are taken out (after a backup), since
        a removal held only in memory comes back on reopen.

        Args:
            document_path: Full path to the document to remove
            project_path: Optional project path. If None, uses active project.
            save_project: Write the .PrjPcb so the removal lasts (default).
                False leaves it for a later proj_save.

        Returns:
            ``removed`` (gone from the project in memory),
            ``project_saved``, ``in_project_file`` (the file on disk
            still lists it, with ``project_file_entry``) and
            ``stale_sections`` when leftovers were taken out (with the
            backup path). ``success`` is true only when the file no longer
            lists it.
        """
        bridge = get_bridge()
        params = {"document_path": document_path}
        if project_path:
            params["project_path"] = project_path
        if not save_project:
            params["save_project"] = "false"
        result = await bridge.send_command_async("project.remove_document", params)
        if save_project:
            from ..config import get_config
            from ..prjpcb import finish_removal
            result = finish_removal(
                result, str(get_config().workspace_dir / "backups" / "prjpcb"))
        return result

    @mcp.tool()
    async def proj_get_parameters(
        project_path: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get all parameters defined at the project level.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            List of parameter dictionaries with name and value
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_parameters", params)
        return result

    @mcp.tool()
    async def proj_set_parameter(
        name: str, value: str, project_path: Optional[str] = None
    ) -> dict[str, Any]:
        """Set a project-level parameter.

        Args:
            name: Parameter name
            value: Parameter value
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"name": name, "value": value}
        if project_path:
            params["project_path"] = project_path

        result = await bridge.send_command_async("project.set_parameter", params)
        # A PROJECT PARAMETER IS NOT A PROJECT OPTION, and the names
        # collide. Asking for hierarchy_mode here creates a user
        # parameter of that name and verifies it, correctly, while the
        # actual setting is untouched. Nothing said so, and the reply
        # looked like a success because it was one, of a different thing.
        hint = project_option_collision(name)
        if hint and isinstance(result, dict):
            result["_hint_not_a_project_option"] = hint
        return result

    @mcp.tool()
    async def proj_push_parameters() -> dict[str, Any]:
        """Copy all project-level parameters onto each loaded schematic sheet.

        Every project parameter is written as a document (sheet) parameter on
        each LOADED schematic in the focused project, so title blocks can
        reference project-wide values. Unloaded sheets are skipped -- call
        ``proj_load_sheets`` first to include every sheet.

        Returns:
            Dict with param_count, sheets_updated, sheets_skipped_not_loaded,
            and params_pushed.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "project.push_params_to_sheets", {}
        )

    @mcp.tool()
    async def proj_get_nets(
        component: str = "",
        net_name: str = "",
        project_path: Optional[str] = None,
        limit: int = 500,
        force_recompile: bool = False,
    ) -> dict[str, Any]:
        """Get net-to-pin connectivity from the compiled project netlist.

        For more than one component or net, do NOT loop: call once with
        no filters (raise ``limit``) and slice locally. Each call
        compiles the project, about 700 ms.

        Args:
            component: Filter by component designator. Empty = all.
            net_name: Filter by net name. Empty = all.
            project_path: Optional project path. If None, uses active.
            limit: Max pin records (default 500). Raise for big boards.
            force_recompile: Save dirty docs and recompile from scratch
                (5-10 s more), for a netlist that is surely fresh, e.g.
                after the user edited sheets. ``proj_get_compile_freshness``
                tells whether any doc is dirty.

        Returns:
            Dict with "pins", "count", "limit", "limit_hit" (true when
            the list stopped at ``limit`` with pins left over) and
            "skipped_nil" (entries Altium returned empty).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if component:
            params["component"] = component
        if net_name:
            params["net_name"] = net_name
        if project_path:
            params["project_path"] = project_path
        if force_recompile:
            # Project.pas ForceRecompileIfRequested reads
            # "force_recompile". Sent under the tool's own name this
            # matched nothing, so the flag was dead and a caller who
            # asked for fresh data got the cached compile instead.
            params["force_recompile"] = "true"
        result = await bridge.send_command_async("project.get_nets", params)
        hint = BulkHintTracker.record_and_hint("proj_get_nets")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def proj_export_netlist(
        output_path: Optional[str] = None,
        net_format: str = "pads",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Write the compiled connectivity to a flat netlist file.

        "pads" (PADS-PCB ASCII) or "tabular" (component,pin,pin_name,net
        CSV). Single-pin and unnamed nets are included.

        Args:
            output_path: Destination file. Defaults to
                workspace/netlist.<ext> if omitted.
            net_format: "pads" (default) or "tabular".
            project_path: Optional project path; uses active if omitted.

        Returns:
            Dictionary with output_path, net_format, net_count, node_count.
        """
        from pathlib import Path
        from ..config import get_config

        fmt = (net_format or "pads").strip().lower()
        if fmt not in ("pads", "tabular"):
            return {"success": False, "error": f"unknown net_format '{net_format}' (use 'pads' or 'tabular')"}

        bridge = get_bridge()
        params: dict[str, Any] = {"limit": "100000"}
        if project_path:
            params["project_path"] = project_path
        data = await bridge.send_command_async("project.get_nets", params)
        pins = data.get("pins", []) if isinstance(data, dict) else []

        nodes: list[tuple[str, str, str, str]] = []
        for p in pins:
            comp = str(p.get("component", "")).strip()
            pin = str(p.get("pin", "")).strip()
            pin_name = str(p.get("pin_name", "")).strip()
            net = str(p.get("net", "")).strip()
            if not comp or not pin or not net:
                continue
            nodes.append((comp, pin, pin_name, net))

        by_net: dict[str, list[tuple[str, str, str, str]]] = {}
        for n in nodes:
            by_net.setdefault(n[3], []).append(n)

        if fmt == "pads":
            lines = ["*PADS-PCB*", "*NET*"]
            for net in sorted(by_net):
                lines.append(f"*SIGNAL* {net}")
                row = "".join(f"{c}.{p} " for c, p, _pn, _net in sorted(by_net[net])).rstrip()
                lines.append(row)
            lines.append("*END*")
            text = "\r\n".join(lines) + "\r\n"
            default_name = "netlist.net"
        else:
            lines = ["component,pin,pin_name,net"]
            for comp, pin, pin_name, net in sorted(nodes):
                lines.append(f"{comp},{pin},{pin_name},{net}")
            text = "\n".join(lines) + "\n"
            default_name = "netlist.csv"

        if output_path:
            out = Path(output_path)
        else:
            out = get_config().workspace_dir / default_name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")

        return {
            "success": True,
            "output_path": str(out),
            "net_format": fmt,
            "net_count": len(by_net),
            "node_count": len(nodes),
        }

    @mcp.tool()
    async def proj_compile(project_path: Optional[str] = None) -> dict[str, Any]:
        """Compile a project to check for errors.


        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with compilation results including any errors/warnings
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.compile", params)
        return result

    @mcp.tool()
    async def proj_load_sheets(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Load every schematic sheet of a project into the Altium editor.

        Project-scope queries and edits see only loaded sheets
        (``loaded`` in app_list_documents); call this first. Safe to
        repeat.

        Args:
            project_path: Optional project path. If None, uses focused project.

        Returns:
            Dictionary with:
            - total_sheets: Total SCH sheets in the project
            - loaded: Sheets newly loaded by this call
            - already_loaded: Sheets that were already resident
            - failed: Sheets that could not be opened
        """
        bridge = get_bridge()
        params = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.load_project_sheets", params
        )
        return result

    @mcp.tool()
    async def proj_export_jlc_assembly(
        output_dir: str,
        lcsc_parameter: str = "",
        name: str = "",
    ) -> dict[str, Any]:
        """Write the BOM and pick-and-place (CPL) files JLCPCB assembly takes.

        BOM: Comment, Designator, Footprint, LCSC Part #, one row per part
        kind. CPL: Designator, Mid X, Mid Y (mm, from the board origin, as
        Altium's own pick-and-place and the Gerbers), Layer (Top/Bottom),
        Rotation (degrees, as Altium shows it). Read from the board and the
        compiled project, so nothing is copied by hand.

        Mid X/Y is each part's ORIGIN: right for a footprint whose origin
        is its centre (every footprint lib_create_footprint_from_package
        makes); a part with its origin elsewhere is placed off by that much.
        Rotations follow Altium; JLC's preview shows each part on its pads,
        so check polarised and bottom-side parts there before ordering.

        Args:
            output_dir: folder for the two files (made when missing).
            lcsc_parameter: the parameter holding the LCSC number. Empty
                tries "LCSC Part #", "LCSC Part Number", "LCSC", "LCSC Part",
                "JLCPCB Part #".
            name: file name stem; default the board's name.

        Returns:
            ``bom_path``, ``cpl_path``, ``parts``, ``bom_rows``,
            ``without_lcsc`` (designators with no LCSC number) and
            ``origin_mils``.
        """
        import csv
        import os
        from collections import OrderedDict

        bridge = get_bridge()
        coords = await bridge.send_command_async("pcb.export_coordinates", {})
        comps = [c for c in (coords or {}).get("placements", [])
                 if isinstance(c, dict) and c.get("designator")]
        if not comps:
            return {"ok": False, "reason": "no components were read from the board"}
        board = await bridge.send_command_async("project.get_board_info", {})
        ox = float((board or {}).get("origin_x", 0) or 0)
        oy = float((board or {}).get("origin_y", 0) or 0)
        info = await bridge.send_command_async(
            "project.get_component_info_batch",
            {"designators": "~~".join(str(c["designator"]) for c in comps),
             "with_pin_nets": "false"}, timeout=120.0)
        params_of: dict[str, dict] = {}
        for c in (info or {}).get("components", []) if isinstance(info, dict) else []:
            if isinstance(c, dict) and isinstance(c.get("parameters"), dict):
                params_of[str(c.get("designator"))] = c["parameters"]
        keys = ([lcsc_parameter] if lcsc_parameter else
                ["LCSC Part #", "LCSC Part Number", "LCSC", "LCSC Part",
                 "JLCPCB Part #"])

        def lcsc(ref: str) -> str:
            got = {k.lower(): v for k, v in params_of.get(ref, {}).items()}
            for k in keys:
                v = str(got.get(k.lower(), "") or "").strip()
                if v and v != "*":
                    return v
            return ""

        groups: "OrderedDict[tuple, list[str]]" = OrderedDict()
        missing = []
        for c in comps:
            ref = str(c["designator"])
            part = lcsc(ref)
            if not part:
                missing.append(ref)
            key = (str(c.get("comment", "")), str(c.get("footprint", "")), part)
            groups.setdefault(key, []).append(ref)

        stem = name or os.path.splitext(
            str((coords or {}).get("board_name") or "board"))[0] or "board"
        os.makedirs(output_dir, exist_ok=True)
        bom_path = os.path.join(output_dir, f"{stem}_BOM_JLCPCB.csv")
        cpl_path = os.path.join(output_dir, f"{stem}_CPL_JLCPCB.csv")
        with open(bom_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
            for (comment, fp, part), refs in groups.items():
                w.writerow([comment, ",".join(refs), fp, part])
        with open(cpl_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
            for c in comps:
                x = (float(c.get("x", 0)) - ox) * MM_PER_MIL
                y = (float(c.get("y", 0)) - oy) * MM_PER_MIL
                side = "Bottom" if str(c.get("side", "")).lower().startswith("b") else "Top"
                w.writerow([c["designator"], f"{x:.4f}mm", f"{y:.4f}mm", side,
                            f"{float(c.get('rotation', 0)) % 360:g}"])
        out = {"ok": True, "bom_path": bom_path, "cpl_path": cpl_path,
               "parts": len(comps), "bom_rows": len(groups),
               "without_lcsc": missing, "origin_mils": [ox, oy]}
        if missing:
            out["note"] = (f"{len(missing)} parts have no LCSC number in "
                           f"{' / '.join(keys)}; JLC leaves them unplaced")
        return out

    @mcp.tool()
    async def proj_get_bom(
        project_path: Optional[str] = None,
        limit: int = 1000,
        variant: Optional[str] = None,
    ) -> dict[str, Any]:
        """Export a full BOM from the compiled project.

        DATASHEET DISCIPLINE: library metadata is NOT authoritative; read
        a part's datasheet before drawing conclusions about it
        (``_datasheet_guidance`` has search queries).

        Every component with designator, comment, footprint, library
        reference and pin nets.

        With ``variant``, the BOM is the one that variant builds: its
        not-fitted parts leave ``components`` (listed in ``not_fitted``),
        an alternate part takes the alternate's library reference and
        footprint (``alternate`` holds its library link), and varied
        parameter values replace the base ones (``varied_parameters``;
        a varied Comment becomes ``comment``). An alternate keeps the base
        comment unless the variant varies it.

        Args:
            project_path: Optional project path. If None, uses active project.
            limit: Max components to return (default 1000).
            variant: A variant's name or description. Leave empty for the
                base design.

        Returns:
            Dictionary with "components" array and "count", "limit",
            "limit_hit" (true when the list stopped at ``limit`` with
            components left over), "skipped_nil", plus
            `_datasheet_guidance` + `_datasheet_parts`. With ``variant``
            also ``variant``, ``not_fitted``, ``alternates``, ``varied``
            and ``entries_not_in_bom``; each component carries
            ``variation`` (none, fitted or alternate).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_bom", params)
        if variant and variant.strip() and isinstance(result, dict) \
                and isinstance(result.get("components"), list):
            vparams: dict[str, Any] = {}
            if project_path:
                vparams["project_path"] = project_path
            listing = await bridge.send_command_async(
                "project.get_variants", vparams, timeout=120.0)
            variants = (listing or {}).get("variants") \
                if isinstance(listing, dict) else None
            if not isinstance(variants, list):
                return {"ok": False, "reason": "the project's variants could "
                        "not be read", "variants_reply": listing}
            chosen = find_variant(variants, variant)
            if chosen is None:
                return {"ok": False, "reason": f"no variant named {variant!r}",
                        "variants": [v.get("name") for v in variants]}
            result = {**result, **apply_variant(result["components"], chosen)}
        return tag_response(result, bom=result, context="proj_get_bom")

    @mcp.tool()
    async def proj_get_component_info(
        designator: str,
        project_path: Optional[str] = None,
        with_pin_nets: bool = True,
        with_parameters: bool = True,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Get full information about one placed component.

        Pin nets need a project compile (30-60 s on a large project);
        with_pin_nets=False answers in under a second.

        DATASHEET DISCIPLINE: parameters and comment are not
        authoritative; fetch the datasheet before stating pin functions
        or ratings.

        Args:
            designator: e.g. "U1".
            project_path: Project (default: the active one).
            with_pin_nets: Compile and give each pin's net (default True).
            with_parameters: Include parameters (default True).
            timeout: Poll timeout in seconds (raise to ~90 with pin nets
                on large projects).

        Returns:
            designator, comment, footprint, lib_ref, sheet, ``pins``
            [{pin, name, net?}], ``parameters`` when asked, and
            ``_datasheet_guidance``.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if project_path:
            params["project_path"] = project_path
        if not with_pin_nets:
            params["with_pin_nets"] = "false"
        if not with_parameters:
            params["with_parameters"] = "false"
        result = await bridge.send_command_async(
            "project.get_component_info", params, timeout=timeout,
        )
        hint = BulkHintTracker.record_and_hint("proj_get_component_info")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        if isinstance(result, dict):
            mfr = str(
                result.get("parameters", {}).get("Manufacturer")
                or result.get("parameters", {}).get("manufacturer")
                or ""
            )
            part = str(
                result.get("parameters", {}).get("Manufacturer Part Number")
                or result.get("parameters", {}).get("ManufacturerPartNumber")
                or result.get("parameters", {}).get("PartNumber")
                or result.get("comment")
                or ""
            ).strip()
            parts = [{
                "manufacturer": mfr,
                "part_number": part,
                "designators": str(result.get("designator", "")),
            }] if part else []
            return tag_response(
                result,
                explicit_parts=parts,
                context="proj_get_component_info",
            )
        return result

    @mcp.tool()
    async def proj_get_component_info_many(
        designators: list[str],
        project_path: Optional[str] = None,
        with_pin_nets: bool = True,
        with_parameters: bool = True,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Full per-component info for MANY designators in ONE round-trip.

        One compile for the whole list (a list of one for a single part).
        ``with_pin_nets=False`` skips the compile for a sub-second read.

        Parameters and library metadata are not authoritative: read the
        manufacturer datasheet before reasoning about pin functions,
        ratings or behaviour (see `_datasheet_guidance`).

        Args:
            designators: List of component designators.
            project_path: Optional project path. If None, uses active.
            with_pin_nets: When True (default) compile once and emit
                the flattened net for each pin on every component.
                Set False to skip the compile.
            with_parameters: When True (default) include each
                component's parameter dict.
            timeout: Optional bridge poll-timeout override (seconds).
                Raise this when keeping `with_pin_nets=True` on large
                hierarchical projects.

        Returns:
            Dict with components[], matched, requested, not_found[],
            plus `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        cleaned = [str(d).strip() for d in (designators or []) if str(d).strip()]
        if not cleaned:
            return {"error": "No designators provided", "matched": 0}
        params: dict[str, Any] = {"designators": "~~".join(cleaned)}
        if project_path:
            params["project_path"] = project_path
        if not with_pin_nets:
            params["with_pin_nets"] = "false"
        if not with_parameters:
            params["with_parameters"] = "false"
        result = await bridge.send_command_async(
            "project.get_component_info_batch", params, timeout=timeout,
        )
        if isinstance(result, dict):
            comps = result.get("components") or []
            explicit = []
            seen_keys: set[tuple[str, str]] = set()
            for c in comps:
                if not isinstance(c, dict):
                    continue
                desig = str(c.get("designator") or "").strip()
                params_dict = c.get("parameters") or {}
                mfr = ""
                pn = ""
                if isinstance(params_dict, dict):
                    mfr = str(
                        params_dict.get("Manufacturer")
                        or params_dict.get("manufacturer")
                        or ""
                    )
                    pn = str(
                        params_dict.get("Manufacturer Part Number")
                        or params_dict.get("ManufacturerPartNumber")
                        or params_dict.get("PartNumber")
                        or ""
                    ).strip()
                if not pn:
                    pn = str(c.get("comment") or "").strip()
                if not pn:
                    continue
                key = (pn.lower(), desig.lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                explicit.append({
                    "manufacturer": mfr,
                    "part_number": pn,
                    "designators": desig,
                })
            return tag_response(
                result,
                explicit_parts=explicit,
                context="proj_get_component_info_many",
            )
        return result

    @mcp.tool()
    async def proj_export_pdf(output_path: str) -> dict[str, Any]:
        """Open Altium's PRINT dialog for the active document, for a PDF.

        NOT UNATTENDED. This opens the print preview / print setup dialog
        with the user's real printer selected and a live Print button,
        and blocks the bridge until a person finishes or cancels it; a
        wrong press prints paper. ``written`` says whether the PDF exists
        afterwards. For a PDF with nobody at the machine, run an OutJob
        container whose medium is PDF (proj_list_outjob_containers, then
        proj_run_outjob).

        Args:
            output_path: Full path for the output PDF file

        Returns:
            Dictionary confirming export
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.export_pdf", {"output_path": output_path}
        )
        return result

    @mcp.tool()
    async def proj_cross_probe(
        designator: str,
        target: str = "schematic",
    ) -> dict[str, Any]:
        """Jump to and highlight a component in the schematic or PCB.

        Args:
            designator: Component designator to find (e.g., "U1", "R8")
            target: "schematic" or "pcb"

        Returns:
            Dictionary confirming the operation
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.cross_probe",
            {"designator": designator, "target": target},
        )
        return result

    @mcp.tool()
    async def proj_get_stats(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get design statistics from the compiled project.

        Returns counts of sheets, components, pins, and nets.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with sheets, components, pins, nets counts
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_design_stats", params)
        return result

    @mcp.tool()
    async def proj_get_board_info() -> dict[str, Any]:
        """Get PCB board information, outline vertices, layer stack, origin.

        It answers about ANY open board when none is focused. Check
        ``board_path`` (the board that answered), or focus the board you
        mean first.

        Returns:
            Dictionary with origin_x, origin_y, board_path, outline
            (vertex array), and layers (active copper layer names)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_board_info", {})
        return result

    @mcp.tool()
    async def proj_annotate(
        order: str = "down_then_across",
    ) -> dict[str, Any]:
        """Annotate schematic designators programmatically, no dialog, no user interaction.

        Numbers every unlocked component on every sheet in the chosen
        order, per prefix (R1, R2, ... C1, ...), keeping each prefix ("R?"
        and "R13" both stay R). Locked designators are skipped. Undoable.

        Args:
            order: Annotation traversal order:
                   "down_then_across" (default: row-major top-to-bottom, left-to-right)
                   "up_then_across"   (row-major bottom-to-top, left-to-right)
                   "across_then_down" (column-major left-to-right, top-to-bottom)
                   "across_then_up"   (column-major left-to-right, bottom-to-top)
                   "none"             (reset all designators to "<prefix>?")

        Returns:
            Dictionary with:
              - success: True
              - order: the order that was applied
              - renamed: count of components renumbered
              - reset: count reset to "?" (only for order="none")
              - skipped_locked: count of locked components left untouched
              - documents_processed: count of schematic sheets visited
              - programmatic: True (marks this as the non-interactive path)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.annotate", {"order": order}
        )
        return result

    @mcp.tool()
    async def proj_run_output(
        output_type: str,
        output_path: str = "",
    ) -> dict[str, Any]:
        """Generate manufacturing output files from the active PCB.

        Note: These may open Altium's export dialogs for configuration.

        Args:
            output_type: Type of output, "gerber", "drill", "pick_place", "ipc_netlist"
            output_path: Optional output directory/file path

        Returns:
            Dictionary confirming generation
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"output_type": output_type}
        if output_path:
            params["output_path"] = output_path
        result = await bridge.send_command_async(
            "project.generate_output", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def proj_get_focused() -> dict[str, Any]:
        """Get information about the currently focused project.

        Returns:
            Dictionary with project information:
            - project_name: Name of the project
            - project_path: Full path to the project file
            - document_count: Number of documents in the project
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_focused")
        return result

    # ------------------------------------------------------------------
    # Output generation tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_export_step(output_path: str,
                               board_path: str = "",
                               overwrite: bool = False,
                               options: Optional[dict] = None) -> dict[str, Any]:
        """Export a board to a STEP 3D model with Altium's own exporter.

        First with no dialog, on the options last used in File > Export >
        STEP 3D (``route: "exporter"``). If that writes nothing, through
        File > Export > STEP 3D, answering its three dialogs (``route:
        "dialogs"``, with ``exporter_reason``); any other dialog stops it.
        ``generated`` is true only when a new file is at output_path.

        Args:
            output_path: Full path for the .step file.
            board_path: Focus this .PcbDoc first; it must then be the
                board in front, since the export acts on the front view.
                Omit it to export the board already in front.
            overwrite: Replace an existing file. Off by default, so an
                earlier export is never lost by accident.
            options: Exporter settings to apply first, by the names
                ``proj_step_export_options`` reads (``folded``,
                ``fold_rate``, ``copper_option``, ``holes_option`` ...).
                Altium keeps them for later exports, as the dialog does.
                Applied on the exporter route only.

        Returns:
            ``generated``, ``output_path``, ``size_bytes``, the board that
            was exported (``front_document``) and every dialog answered.
        """
        import asyncio
        import os
        import time as _time

        from ..atomicfile import replace_with_retry
        from ..bridge.exceptions import AltiumError
        from ..ui import windows
        from .uiauto import _altium_pid

        if not output_path or not os.path.isabs(output_path):
            return {"ok": False, "reason": "output_path must be a full path"}
        existed = os.path.exists(output_path)
        if existed and not overwrite:
            return {"ok": False, "output_path": output_path, "reason": (
                "a file is already there. Pass overwrite=True to replace it")}
        pid, problem = _altium_pid()
        if problem is not None:
            return problem
        # Altium's exporter cannot write into a folder that is not there,
        # and says so in an "Information" box, as it says "Done!".
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
        except OSError as e:
            return {"ok": False, "output_path": output_path,
                    "reason": f"the output folder could not be made: {e}"}

        bridge = get_bridge()
        if board_path:
            await bridge.send_command_async(
                "pcb.focus_board", {"board_path": board_path})
        front = await bridge.send_command_async(
            "application.get_active_document", {"front": "true"})
        front_path = (front or {}).get("file_path") or ""
        if board_path and (os.path.normcase(os.path.normpath(front_path))
                           != os.path.normcase(os.path.normpath(board_path))):
            return {"ok": False, "front_document": front_path, "reason": (
                f"{board_path} is not the board in front, so the export "
                "would write another board. Nothing was exported")}
        if not front_path.lower().endswith(".pcbdoc"):
            return {"ok": False, "front_document": front_path,
                    "reason": "the document in front is not a board"}
        # An existing file is moved aside rather than overwritten through
        # Altium: its "Confirm Save As" sat on top of the Save dialog and
        # the export never ran. It comes back if no new file is written.
        aside = ""
        if existed:
            aside = output_path + ".previous"
            try:
                replace_with_retry(output_path, aside)
            except OSError as e:
                return {"ok": False, "output_path": output_path, "reason": (
                    f"the existing file could not be moved aside: {e}")}
        started = _time.time()

        # Altium's exporter object first: no dialog to answer. The dialog
        # route below stays for a build where it writes nothing.
        api: dict[str, Any] = {}
        try:
            api = await bridge.send_command_async(
                "project.export_step_api",
                {"output_path": output_path, **_step_option_params(options)},
                timeout=600.0) or {}
        except AltiumError as e:
            api = {"generated": False, "reason": str(e)}
        if api.get("generated") and os.path.exists(output_path):
            if aside:
                try:
                    os.remove(aside)
                except OSError:
                    pass
            return {"ok": True, "generated": True, "output_path": output_path,
                    "size_bytes": os.path.getsize(output_path),
                    "front_document": front_path, "route": "exporter",
                    "dialogs_answered": []}
        api_reason = api.get("reason") or "no new file"

        # The handler blocks in RunProcess until the dialogs are answered,
        # and the bridge gives up on a call as soon as a modal shows, so
        # the call's end says nothing about the export's. The export ends
        # with Altium's "Done!", which is answered too: left open, it
        # blocked the next call.
        call = asyncio.ensure_future(bridge.send_command_async(
            "project.export_step", {"output_path": output_path},
            timeout=600.0))

        def settled() -> bool:
            """A new file whose size has stopped changing."""
            if not os.path.exists(output_path)                     or os.path.getmtime(output_path) < started - 1:
                return False
            size = os.path.getsize(output_path)
            _time.sleep(1.0)
            return size > 0 and size == os.path.getsize(output_path)

        def answer() -> dict:
            steps = {"export file": "Save", "export options": "OK",
                     "information": "OK"}
            answered, deadline = [], _time.monotonic() + 600.0
            quiet_since = None
            while _time.monotonic() < deadline:
                shown = windows.dialogs(pid)
                for dialog in shown:
                    title = (dialog.title or "").strip().lower()
                    caption = steps.get(title)
                    if caption is None:
                        return {"ok": False, "answered": answered,
                                "reason": (f"an unexpected dialog "
                                           f"{dialog.title!r} is open; "
                                           "nothing was pressed in it")}
                    button = dialog.find_button(caption)
                    if button is None:
                        continue
                    windows.click(button)
                    windows.wait_for_close(dialog.hwnd, timeout=5.0)
                    answered.append(dialog.title)
                    if title == "information":
                        return {"ok": True, "answered": answered}
                    break
                # Nothing on screen once the options are answered is the
                # export running. It is done at "Done!", or, should that
                # never come, once a new file has stopped growing and the
                # screen has stayed empty for a few seconds.
                if shown:
                    quiet_since = None
                elif "Export Options" in answered:
                    quiet_since = quiet_since or _time.monotonic()
                    if _time.monotonic() - quiet_since > 5.0 and settled():
                        return {"ok": True, "answered": answered}
                _time.sleep(0.25)
            return {"ok": False, "answered": answered,
                    "reason": "the export did not finish in ten minutes"}

        def late_done(wait_s: float = 30.0) -> list:
            """Answer a "Done!" that comes after the file has settled.

            On a large board it came seconds after the export had been
            judged finished, and blocked the next call (a demo run's BOM
            and pick-and-place both failed on it)."""
            deadline = _time.monotonic() + wait_s
            while _time.monotonic() < deadline:
                for dialog in windows.dialogs(pid):
                    if (dialog.title or "").strip().lower() != "information":
                        return []
                    button = dialog.find_button("OK")
                    if button is not None:
                        windows.click(button)
                        windows.wait_for_close(dialog.hwnd, timeout=5.0)
                        return [dialog.title]
                _time.sleep(0.25)
            return []

        driven = await asyncio.get_running_loop().run_in_executor(None, answer)
        if driven.get("ok") and "Information" not in driven.get("answered", []):
            late = await asyncio.get_running_loop().run_in_executor(None, late_done)
            driven["answered"] = driven.get("answered", []) + late
        try:
            await asyncio.wait_for(call, timeout=30.0)
        except (AltiumError, asyncio.TimeoutError):
            pass

        # The file can land a few seconds after the last dialog closes.
        written = False
        for _ in range(30):
            written = await asyncio.get_running_loop().run_in_executor(
                None, settled)
            if written:
                break
            await asyncio.sleep(0.5)

        if aside:
            try:
                if written:
                    os.remove(aside)
                else:
                    replace_with_retry(aside, output_path)
            except OSError:
                pass
        result: dict[str, Any] = {
            "ok": bool(written and driven.get("ok")),
            "generated": bool(written),
            "output_path": output_path,
            "front_document": front_path,
            "route": "dialogs",
            "exporter_reason": api_reason,
            "dialogs_answered": driven.get("answered", []),
        }
        if written:
            result["size_bytes"] = os.path.getsize(output_path)
        if aside and not written:
            result["previous_file_kept"] = os.path.exists(output_path)
        if not driven.get("ok"):
            result["reason"] = driven.get("reason")
        elif not written:
            result["reason"] = "the dialogs were answered and no new file is there"
        return result

    @mcp.tool()
    async def proj_create_outjob(
        project_path: str,
        source_outjob: str = "",
        outjob_name: str = "",
        output_dir: str = "",
        use_fallback: bool = False,
    ) -> dict[str, Any]:
        """Give a project an OutJob by copying one in the user's own style.

        It invents no outputs: it copies an existing OutJob (the user's
        own, or one Altium ships), points each output at this project's
        documents, writes every container to ``output_dir``, and adds the
        copy to the project. Run it afterwards with proj_run_outjob or
        proj_generate_fab_package.

        ASK THE USER FIRST. With no ``source_outjob`` nothing is written:
        the reply lists the OutJobs found in open projects. Ask the user
        which one to copy, or how they prefer their outputs (Gerber
        format and units, drill files, pick-and-place, BOM, folders) and
        pick the closest. Altium's own templates are a fallback, used
        only with ``use_fallback`` once the user has agreed.

        Args:
            project_path: The target .PrjPcb.
            source_outjob: The .OutJob to copy.
            outjob_name: File name for the copy; defaults to the source's.
            output_dir: Absolute folder for the outputs; defaults to
                "Project Outputs for <project>" beside the project.
            use_fallback: With no source, copy Altium's shipped
                Fabrication template. Only once the user has agreed.

        Returns:
            ``outjob_path``, the ``outputs`` it holds, which documents
            were ``retargeted`` and which were left ``unmapped``, and the
            containers Altium reads back from it.
        """
        import glob
        import os

        from .. import outjob
        from ..atomicfile import replace_with_retry

        bridge = get_bridge()
        if not source_outjob and use_fallback:
            # Altium installs its OutputJobs templates under the shared
            # Public profile, which Windows names in PUBLIC.
            public = os.environ.get("PUBLIC", "")
            shipped = sorted(glob.glob(os.path.join(
                public, "Documents", "Altium", "*", "OutputJobs",
                "Fabrication.OutJob"))) if public else []
            if not shipped:
                return {"ok": False, "reason": (
                    "Altium's shipped OutputJobs templates were not found")}
            source_outjob = shipped[-1]
        if not source_outjob:
            open_docs = await bridge.send_command_async(
                "application.get_open_documents")
            docs = open_docs.get("result", open_docs) \
                if isinstance(open_docs, dict) else open_docs
            found = sorted({d.get("file_path", "") for d in docs or []
                            if str(d.get("file_path", "")).lower()
                            .endswith(".outjob")})
            return {"ok": False, "needs_input": True,
                    "candidates": found,
                    "reason": (
                        "nothing was written. Ask the user which OutJob to "
                        "copy (one of the candidates, or a path), or how "
                        "they prefer their outputs, then call again with "
                        "source_outjob. use_fallback=True copies Altium's "
                        "shipped Fabrication template, only once the user "
                        "has agreed to it.")}

        if not os.path.isfile(source_outjob):
            return {"ok": False, "reason": f"{source_outjob} is not a file"}
        if not project_path.lower().endswith(".prjpcb"):
            return {"ok": False, "reason": "project_path must be a .PrjPcb"}
        project_dir = os.path.dirname(project_path)
        stem = os.path.splitext(os.path.basename(project_path))[0]
        target = os.path.join(project_dir,
                              outjob_name or os.path.basename(source_outjob))
        if not target.lower().endswith(".outjob"):
            target += ".OutJob"
        if os.path.exists(target):
            return {"ok": False, "outjob_path": target, "reason": (
                "a file of that name is already there and is never "
                "overwritten; pass another outjob_name")}
        output_dir = output_dir or os.path.join(
            project_dir, f"Project Outputs for {stem}")

        listing = await bridge.send_command_async(
            "project.get_documents", {"project_path": project_path})
        members = listing.get("result", listing) \
            if isinstance(listing, dict) else listing
        documents = [d.get("file_path", "") for d in members or []
                     if d.get("file_path")]
        if not documents:
            return {"ok": False, "reason": (
                "Altium lists no documents in that project; open it first")}

        # The board's own copper layers, planes included, for the Gerber
        # layer set: Altium's shipped template plots four signal layers and
        # no plane, and a demo board's planes were left out of its Gerbers.
        codes: list[int] = []
        boards = {os.path.basename(d).lower() for d in documents
                  if d.lower().endswith(".pcbdoc")}
        if boards:
            stack = await bridge.send_command_async("pcb.get_layer_stackup", {})
            # Only the project's own board: the stack read is the focused one.
            if isinstance(stack, dict) and str(stack.get("board_name", "")).lower() in boards:
                codes = outjob.copper_layer_codes(
                    int(layer.get("layer_id") or 0) for layer in stack.get("layers") or [])

        with open(source_outjob, "rb") as fh:
            data, report = outjob.clone(fh.read(), project_path, documents,
                                        output_dir, copper_codes=codes)
        if not codes:
            report["copper_layers_note"] = (
                "the board's layer stack could not be read, so the Gerber "
                "layer set is the template's own: check it plots every "
                "copper and plane layer")
        tmp = target + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        replace_with_retry(tmp, target)

        added = await bridge.send_command_async(
            "project.add_document",
            {"document_path": target, "project_path": project_path})
        read_back = await bridge.send_command_async(
            "project.get_outjob_containers", {"outjob_path": target},
            timeout=30.0)
        return {"ok": bool((read_back or {}).get("containers")),
                "outjob_path": target, "source_outjob": source_outjob,
                "output_dir": output_dir,
                "outputs": outjob.outputs(data),
                **report, "added_to_project": added,
                "containers_read_by_altium": (read_back or {}).get("containers")}

    @mcp.tool()
    async def proj_export_dxf(output_path: str = "") -> dict[str, Any]:
        """Export the active PCB to DXF (AutoCAD) format.

        Requires an active PCB document. If output_path is omitted,
        Altium may show a file-save dialog.

        Args:
            output_path: Full path for the output .dxf file (optional)

        Returns:
            Dictionary confirming the export
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if output_path:
            params["output_path"] = output_path
        result = await bridge.send_command_async(
            "project.export_dxf", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def proj_export_image(
        output_path: str,
        format: str = "pdf",
        width: int = 1920,
        height: int = 1080,
    ) -> dict[str, Any]:
        """Export the active schematic / PCB document, silent (no dialog).

        Only PDF is silent; it prints the focused document, as
        ``proj_export_pdf`` does, so make the wanted tab active first.
        png/jpg/bmp return ``IMAGE_FORMAT_UNSUPPORTED``: they need an
        OutJob with an image container, run by ``proj_run_outjob``.
        An old file at ``output_path`` is deleted first, and a run that
        leaves no file is ``EXPORT_FILE_MISSING``.

        Args:
            output_path: Full path for the output file. The directory must
                already exist. The extension should be ``.pdf``.
            format: Output format. Only ``"pdf"`` is silent today
                (default). ``"png"``, ``"jpg"``, ``"bmp"`` return an
                explicit ``IMAGE_FORMAT_UNSUPPORTED`` error.
            width, height: Unused by the PDF path.

        Returns:
            Dictionary confirming the export with the resolved path,
            format, width, height, and the scoped schematic. Sets
            ``file_exists`` based on a Python-side existence check.
        """
        import os

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.export_image",
            {
                "output_path": output_path,
                "format": format,
                "width": str(width),
                "height": str(height),
            },
            timeout=120.0,
        )
        if isinstance(result, dict) and result.get("success"):
            exists = os.path.exists(output_path)
            result["file_exists"] = exists
            if exists:
                try:
                    result["file_size_bytes"] = os.path.getsize(output_path)
                except OSError:
                    pass
                # A file that is there is not yet a PDF: an empty or cut
                # off print job leaves one too (issue #94 asks that the
                # output decode). Every PDF starts with %PDF-.
                head = b""
                try:
                    with open(output_path, "rb") as fh:
                        head = fh.read(5)
                except OSError:
                    pass
                if str(format).lower() == "pdf" and head != b"%PDF-":
                    result["success"] = False
                    result["error"] = "EXPORT_FILE_INVALID"
                    result["details"] = (
                        f"{output_path} exists but is not a PDF (it starts "
                        f"with {head!r}). The print did not complete.")
            else:
                # Pascal claimed success but file is missing - downgrade
                # to an explicit error so callers don't trust a phantom
                # success.
                result["success"] = False
                result["error"] = "EXPORT_FILE_MISSING"
                result["details"] = (
                    f"Pascal reported success but {output_path} does not "
                    f"exist on disk. The synthetic-OutJob silent path may "
                    f"not be supported on this Altium build."
                )
        return result

    @mcp.tool()
    async def proj_list_outjob_containers(outjob_path: str = "") -> dict[str, Any]:
        """List all output containers defined in an OutJob file.

        Run one with `proj_run_outjob`.

        Args:
            outjob_path: Path to the .OutJob file. If omitted, uses the
                         first OutJob found in the focused project.

        Returns:
            Dictionary with "outjob_path" and "containers" array
            (each: name, type, group)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if outjob_path:
            params["outjob_path"] = outjob_path
        result = await bridge.send_command_async(
            "project.get_outjob_containers", params
        )
        return result

    @mcp.tool()
    async def proj_run_outjob(
        container_name: str,
        outjob_path: str = "",
        fresh_window_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Execute a specific output container from an OutJob file.

        Containers are listed by `proj_list_outjob_containers`; both
        generated-file and publish (PDF) containers run.

        SUCCESS MEANS A FILE WAS WRITTEN under ``output_dir`` after the
        run started; a container bound to a managed release or with its
        outputs off runs cleanly and writes nothing. Output written
        elsewhere reads as ``success: false``, and ``reason`` says what
        was examined.

        The OutJob is opened if needed and made the ACTIVE document
        first: Altium writes nothing for a container whose OutJob is
        not active. A dialog Altium raises during the run (an error
        after the files are out, say) comes back as ``dialog`` with the
        files found so far, not as a bare failure; close it before the
        next call.

        Args:
            container_name: Name of the output container to execute
            outjob_path: Path to the .OutJob file. If omitted, uses the
                         first OutJob found in the focused project.
            fresh_window_seconds: files modified up to this many seconds
                before the run started still count as written, for file
                systems with coarse timestamps. Default 5s.

        Returns:
            ``success``, ``container_name``, ``container_type``,
            ``output_dir``, ``output_dir_exists``, ``files_written`` (a
            count), ``written`` (up to 50 paths), and ``reason`` when
            nothing was written.
        """
        import time as _time

        bridge = get_bridge()
        params: dict[str, Any] = {"container_name": container_name}
        if outjob_path:
            params["outjob_path"] = outjob_path
            busy = _locked_outputs(_outjob_output_dir(outjob_path, container_name))
            if busy:
                return {"success": False, "container_name": container_name,
                        "files_in_use": busy, "reason": (
                            "these outputs are open in another program (a PDF "
                            "viewer?), and Altium stops with an Error dialog when "
                            "it cannot overwrite one: close them and run again. "
                            "Nothing was run.")}
        started_at = _time.time()
        result = await _send_run(bridge, params, 120.0)
        return _judge_outjob_run(result, started_at, fresh_window_seconds)

    def _locked_outputs(output_dir):
        """Files in a container's output folder another program holds
        open, so that Altium cannot overwrite them. A second run of a
        take's OutJob stopped on an Altium "Error" modal naming the
        Fabrication.pdf the first run wrote, still open."""
        import os
        from pathlib import Path

        if not output_dir:
            return []
        busy = []
        folder = Path(output_dir)
        if not folder.is_dir():
            return []
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            try:
                fd = os.open(str(path), os.O_RDWR | os.O_APPEND)
                os.close(fd)
            except PermissionError:
                busy.append(str(path))
            except OSError:
                pass
            if len(busy) >= 20:
                break
        return busy

    async def _send_run(bridge, params, timeout):
        """One container run. A dialog Altium raises during or after the
        run comes back as a reply to judge, not an exception: the output is
        often written already (a NullReference error after a full Gerber
        set, 2026-10-21), and only the folder can say so."""
        from ..bridge.exceptions import AltiumTimeoutError

        try:
            return await bridge.send_command_async(
                "project.run_outjob", params, timeout=timeout)
        except AltiumTimeoutError as exc:
            dialogs = (exc.details or {}).get("dialogs")
            if not dialogs:
                raise
            outjob = params.get("outjob_path", "")
            return {
                "process_issued": True,
                "container_name": params["container_name"],
                "output_dir": _outjob_output_dir(
                    outjob, params["container_name"]) if outjob else "",
                "outjob_path": outjob,
                "dialog": dialogs.get("summary", ""),
                "dialog_message": [line for d in dialogs.get("dialogs", [])
                                   for line in d.get("message", [])][:6],
            }

    def _outjob_output_dir(outjob_path, container_name):
        """The container's output folder, read from the OutJob as the
        handler reads it: the medium's index J in its OutputGroup picks
        OutputBasePathJ in PublishSettings, relative to the OutJob."""
        import configparser
        from pathlib import Path

        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.optionxform = str
        try:
            ini.read(outjob_path, encoding="utf-8-sig")
        except (OSError, configparser.Error, UnicodeDecodeError):
            return ""
        for section in ini.sections():
            if not section.startswith("OutputGroup"):
                continue
            j = 1
            while ini.has_option(section, f"OutputMedium{j}"):
                if ini.get(section, f"OutputMedium{j}") == container_name:
                    rel = ini.get("PublishSettings", f"OutputBasePath{j}",
                                  fallback="")
                    if not rel:
                        return ""
                    base = (Path(rel) if len(rel) >= 2 and rel[1] == ":"
                            else Path(outjob_path).parent / rel)
                    return str(base) + "\\"
                j += 1
        return ""

    # Shared by the three OutJob tools, so they cannot disagree about what
    # "produced" means. Defined after proj_run_outjob in this scope, which
    # is fine: the tools look these up when they are CALLED, by which time
    # registration has finished.
    def _scan_output_dir(output_dir, started_at, fresh_window_seconds):
        """(exists, files) for one container's output directory.

        ``files`` is every file under it, each flagged ``newly_produced``
        when its mtime is at or after the run start less the window.
        """
        from pathlib import Path

        if not output_dir:
            return False, []
        root = Path(output_dir)
        if not root.is_dir():
            return False, []
        cutoff = started_at - float(fresh_window_seconds)
        files: list[dict[str, Any]] = []
        for f in root.rglob("*"):
            try:
                if not f.is_file():
                    continue
                st = f.stat()
            except OSError:
                continue
            files.append({"path": str(f), "size": st.st_size,
                          "modified_at": st.st_mtime,
                          "newly_produced": st.st_mtime >= cutoff})
        return True, files

    def _judge_outjob_run(run, started_at, fresh_window_seconds):
        """A run reply with ``success`` decided by what was written.

        The handler reports only that the process was issued. Anything
        that is not a normal reply is passed through untouched.
        """
        if not isinstance(run, dict) or run.get("error"):
            return run
        out = dict(run)
        out_dir = run.get("output_dir") or ""
        exists, files = _scan_output_dir(out_dir, started_at,
                                         fresh_window_seconds)
        fresh = [f["path"] for f in files if f["newly_produced"]]
        out["output_dir_exists"] = exists
        out["files_written"] = len(fresh)
        out["written"] = fresh[:50]
        out["success"] = bool(fresh)
        if run.get("dialog"):
            out["reason"] = (
                f"Altium raised a dialog during the run and the bridge "
                f"stopped waiting: {run['dialog']}. "
                + (f"{len(fresh)} file(s) were written under {out_dir} "
                   f"anyway. " if fresh else
                   "Nothing new was found in the output folder. "
                   if out_dir else
                   "Pass outjob_path so the output folder can be checked. ")
                + "Close the dialog (app_list_open_dialogs) before the "
                  "next call")
            return out
        if not fresh:
            if run.get("outjob_active") is False:
                out["reason"] = (
                    "the container ran, but the OutJob was not the active "
                    "document, and Altium writes nothing then. Make it "
                    "active with app_set_active_document and run again")
            elif not out_dir:
                out["reason"] = (
                    "the container ran, but the OutJob gives it no output "
                    "path, so there was no folder to check and no file is "
                    "known to have been written")
            elif not exists:
                out["reason"] = (
                    f"the container ran, but {out_dir} does not exist, so "
                    f"nothing was written there. A container bound to a "
                    f"managed release, or with its outputs switched off, "
                    f"runs cleanly and writes no local file")
            else:
                out["reason"] = (
                    f"the container ran, but no file under {out_dir} was "
                    f"written or changed after it started. Outputs switched "
                    f"off in the OutJob, or bound to a managed release, "
                    f"produce this")
        return out

    @mcp.tool()
    async def proj_run_outjob_all(
        outjob_path: str = "",
        include_files: bool = True,
        fresh_window_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Run every container in an OutJob and report what each produced.

        Each container runs through ``proj_run_outjob`` in order, and its
        ``output_dir`` files written since it started are flagged
        ``newly_produced``.

        Args:
            outjob_path: Path to the .OutJob. Omit to use the first one
                in the focused project.
            include_files: List each container's files (default True).
            fresh_window_seconds: Files modified up to this long before a
                container started still count as new. Default 5 s.

        Returns:
            Dict with ``ok``, ``outjob_path``, ``containers_run``
            (list of container names), ``results`` (per-container
            dicts each with ``container``, ``container_type``,
            ``output_dir``, ``ok``, ``files``).
        """
        from pathlib import Path
        import time as _time

        bridge = get_bridge()
        list_params: dict[str, Any] = {}
        if outjob_path:
            list_params["outjob_path"] = outjob_path
        listing = await bridge.send_command_async(
            "project.get_outjob_containers", list_params, timeout=30.0,
        )
        if not isinstance(listing, dict):
            return {"ok": False, "reason": "container listing failed"}
        containers = listing.get("containers") or []
        if not containers:
            return {"ok": True, "containers_run": [], "results": [],
                    "reason": "no containers in OutJob"}

        per_container: list[dict[str, Any]] = []
        for c in containers:
            name = c.get("name") if isinstance(c, dict) else str(c)
            if not name:
                continue
            started_at = _time.time()
            params: dict[str, Any] = {"container_name": name}
            if outjob_path:
                params["outjob_path"] = outjob_path
                busy = _locked_outputs(_outjob_output_dir(outjob_path, name))
                if busy:
                    per_container.append({
                        "container": name, "ok": False, "files_in_use": busy,
                        "reason": "outputs open in another program; close them "
                                  "and run again (not run)"})
                    continue
            run = await _send_run(bridge, params, 300.0)
            judged = _judge_outjob_run(run, started_at, fresh_window_seconds)
            judged = judged if isinstance(judged, dict) else {}
            out_dir = judged.get("output_dir")
            exists, files = _scan_output_dir(out_dir, started_at,
                                             fresh_window_seconds)
            entry: dict[str, Any] = {
                "container": name,
                # From what was written, not from the handler, which
                # reports only that the process was issued.
                "ok": bool(judged.get("success")),
                "container_type": judged.get("container_type"),
                "output_dir": out_dir,
                "output_dir_exists": exists,
                "files_written": judged.get("files_written", 0),
                "files": files if include_files else [],
            }
            if not entry["ok"] or judged.get("dialog"):
                entry["reason"] = judged.get("reason", "")
            per_container.append(entry)
            # A dialog blocks every later run; stop and say so.
            if judged.get("dialog"):
                break

        produced = [r for r in per_container if r["ok"]]
        names = [c.get("name") if isinstance(c, dict) else str(c)
                 for c in containers]
        return {
            "ok": bool(produced),
            "not_run": names[len(per_container):],
            "containers_with_output": len(produced),
            "reason": "" if produced else (
                "no container wrote a file under its output_dir; each "
                "result says what was checked"),
            "outjob_path": (
                (per_container[0].get("output_dir") if per_container else "")
                if not outjob_path else outjob_path
            ),
            "containers_run": [r["container"] for r in per_container],
            "results": per_container,
        }

    @mcp.tool()
    async def proj_generate_fab_package(
        outjob_path: str = "",
        include_step: bool = False,
        include_dxf: bool = False,
        fresh_window_seconds: float = 5.0,
    ) -> dict[str, Any]:
        """Generate a fabrication package from an OutJob (Gerbers, NC drill,
        IPC-356, pick-and-place, assembly, BOM) and report every file produced.

        Those formats exist only as OutJob containers, so this runs every
        container in the OutJob and lists the files each wrote; STEP and
        DXF are optional extra exports. The OutJob's outputs must already
        be enabled: a script cannot switch them on.

        Args:
            outjob_path: path to the .OutJob; omit to use the first one in the
                focused project.
            include_step: also export a STEP 3D model of the active PCB.
            include_dxf: also export a DXF of the active PCB.
            fresh_window_seconds: files modified within this window before a
                container ran still count as newly produced.

        Returns:
            {"ok", "outjob_path", "containers_run", "results" (per-container
             with output_dir + files), "extras" (step/dxf results),
             "all_files" (flat list of newly-produced file paths)}.
        """
        from pathlib import Path
        import time as _time

        bridge = get_bridge()
        list_params: dict[str, Any] = {}
        if outjob_path:
            list_params["outjob_path"] = outjob_path
        listing = await bridge.send_command_async(
            "project.get_outjob_containers", list_params, timeout=30.0,
        )
        if not isinstance(listing, dict):
            return {"ok": False, "reason": "container listing failed"}
        containers = listing.get("containers") or []
        if not containers:
            return {
                "ok": False,
                "reason": "no OutJob containers found; create and configure an "
                "OutJob (Gerber, NC drill, etc.) before generating a fab package",
            }

        per_container: list[dict[str, Any]] = []
        all_files: list[str] = []
        for c in containers:
            name = c.get("name") if isinstance(c, dict) else str(c)
            if not name:
                continue
            started_at = _time.time()
            params: dict[str, Any] = {"container_name": name}
            if outjob_path:
                params["outjob_path"] = outjob_path
                busy = _locked_outputs(_outjob_output_dir(outjob_path, name))
                if busy:
                    per_container.append({
                        "container": name, "ok": False, "files_in_use": busy,
                        "reason": "outputs open in another program; close them "
                                  "and run again (not run)"})
                    continue
            run = await _send_run(bridge, params, 300.0)
            judged = _judge_outjob_run(run, started_at, fresh_window_seconds)
            judged = judged if isinstance(judged, dict) else {}
            out_dir = judged.get("output_dir")
            exists, files = _scan_output_dir(out_dir, started_at,
                                             fresh_window_seconds)
            all_files.extend(f["path"] for f in files if f["newly_produced"])
            entry: dict[str, Any] = {
                "container": name,
                "container_type": judged.get("container_type"),
                # From what was written, not from the handler, which
                # reports only that the process was issued.
                "ok": bool(judged.get("success")),
                "output_dir": out_dir,
                "output_dir_exists": exists,
                "files_written": judged.get("files_written", 0),
                "files": files,
            }
            if not entry["ok"] or judged.get("dialog"):
                entry["reason"] = judged.get("reason", "")
            per_container.append(entry)
            # A dialog blocks every later run, and the extras too.
            if judged.get("dialog"):
                return {
                    "ok": False,
                    "reason": ("stopped at " + name + ": "
                               + entry.get("reason", "")),
                    "outjob_path": outjob_path,
                    "containers_run": [r["container"] for r in per_container],
                    "not_run": [c.get("name") if isinstance(c, dict) else str(c)
                                for c in containers][len(per_container):],
                    "results": per_container,
                    "extras": {},
                    "all_files": sorted(set(all_files)),
                }

        extras: dict[str, Any] = {}
        if include_step:
            # Next to the other outputs, named after the board in front,
            # through proj_export_step, which answers the exporter's
            # dialogs; the bare command waits on them forever.
            import os
            front = await bridge.send_command_async(
                "application.get_active_document", {"front": "true"})
            board = (front or {}).get("file_path") or ""
            out_dir = next((r["output_dir"] for r in per_container
                            if r.get("output_dir")), "")
            if board and out_dir:
                stem = os.path.splitext(os.path.basename(board))[0]
                extras["step"] = await proj_export_step(
                    os.path.join(out_dir, stem + ".step"), overwrite=True)
            else:
                extras["step"] = {"ok": False, "reason": (
                    "no board in front or no output folder to put it in")}
        if include_dxf:
            extras["dxf"] = await bridge.send_command_async(
                "project.export_dxf", {}, timeout=120.0
            )

        return {
            "ok": bool(all_files),
            "reason": "" if all_files else (
                "no container wrote a file under its output_dir, so there "
                "is no fabrication package; each result says what was "
                "checked"),
            "outjob_path": outjob_path,
            "containers_run": [r["container"] for r in per_container],
            "results": per_container,
            "extras": extras,
            "all_files": sorted(set(all_files)),
        }

    # ------------------------------------------------------------------
    # Variant management tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_list_variants(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """List all project variants with their component overrides.

        Compiles the project and returns every variant definition including
        component variation details (Fitted / Not Fitted / Alternate) and
        any parameter overrides.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with "variants" array and "count". Each variant has
            name, description, its own ``parameters``, and variations
            array (designator, unique_id, kind, alternate_part (Altium
            reads it from the varied Comment, so a fitted part with a
            varied Comment shows one too),
            ``alternate_link`` for an alternate: the alternate's library,
            symbol reference and footprint, and ``parameters``, the varied
            parameter values).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_variants", params)
        return result

    @mcp.tool()
    async def proj_get_active_variant(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get the currently active project variant.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with variant "name" and "description".
            Returns "[No Variations]" if no variant is active.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.get_active_variant", params
        )
        return result

    @mcp.tool()
    async def proj_set_active_variant(
        variant_name: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Switch the active project variant, or back to the base design.

        Args:
            variant_name: The variant's name or description, or
                "[No Variations]" for the base design.
            project_path: Optional project path. If None, uses active project.

        Returns:
            ``success``, ``variant_name`` (the variant now current) and
            ``verified``, read back from the project; ``success`` false
            with ``current_variant`` and a ``reason`` otherwise.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"variant_name": variant_name}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.set_active_variant", params
        )
        return result

    @mcp.tool()
    async def proj_export_variant_matrix_csv(
        output_path: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Write the variant fitted/not-fitted matrix to a CSV.

        One row per component (every one), one column per variant, each
        cell ``Fitted`` / ``Not Fitted`` / ``Alternate``.

        Args:
            output_path: Destination .csv. Defaults to
                ``workspace/variant_matrix.csv``.
            project_path: Optional project path. If None, uses the focused
                project.

        Returns:
            {"output_path", "component_count", "variant_count"} or an error.
        """
        from pathlib import Path

        from ..config import get_config
        from ..export.variant_matrix_csv import format_variant_matrix_csv

        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        matrix = await bridge.send_command_async(
            "project.get_variant_matrix", params
        )
        if not isinstance(matrix, dict) or "rows" not in matrix:
            return {"success": False,
                    "error": "could not read variant matrix (open a PcbDoc project)"}

        csv_text = format_variant_matrix_csv(matrix)
        if output_path:
            out = Path(output_path)
        else:
            out = get_config().workspace_dir / "variant_matrix.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(csv_text, encoding="utf-8")
        return {
            "success": True,
            "output_path": str(out),
            "component_count": matrix.get("component_count",
                                          len(matrix.get("rows") or [])),
            "variant_count": len(matrix.get("variants") or []),
        }

    @mcp.tool()
    async def proj_print_all_variants(
        output_dir: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Export a PDF for every project variant.

        One ``<variant>.pdf`` per variant, each made active in turn; the
        original variant is restored at the end.

        Args:
            output_dir: Folder for the per-variant PDFs. Defaults to the
                workspace directory.
            project_path: Optional project path. If None, uses the focused
                project.

        Returns:
            {"exported": [{variant, output_path, ok, error}], "count"} plus
            ``restored``: the variant put back, or null if putting it
            back FAILED: the project is then on another variant, and any
            variant-sensitive call (a BOM, an export,
            ``pcb_apply_dnp_paste_exclusion``) acts on the wrong set. A
            failure also carries ``restore_error`` and a ``note``.
        """
        from pathlib import Path

        from ..config import get_config

        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path

        listing = await bridge.send_command_async("project.get_variants", params)
        variants = (listing or {}).get("variants", []) if isinstance(listing, dict) else []
        active = await bridge.send_command_async("project.get_active_variant", params)
        original = (active or {}).get("name") if isinstance(active, dict) else None

        base = Path(output_dir) if output_dir else get_config().workspace_dir
        base.mkdir(parents=True, exist_ok=True)

        def _safe(s: str) -> str:
            return "".join(c if c.isalnum() or c in "-_." else "_" for c in s) or "variant"

        exported: list[dict[str, Any]] = []
        for var in variants:
            vname = var.get("name") if isinstance(var, dict) else str(var)
            if not vname:
                continue
            out_pdf = base / f"{_safe(vname)}.pdf"
            entry: dict[str, Any] = {"variant": vname, "output_path": str(out_pdf),
                                     "ok": False, "error": ""}
            try:
                set_params = dict(params, variant_name=vname)
                await bridge.send_command_async("project.set_active_variant", set_params)
                res = await bridge.send_command_async(
                    "project.export_pdf", {"output_path": str(out_pdf)}
                )
                entry["ok"] = bool(isinstance(res, dict) and res.get("success", True))
                if isinstance(res, dict) and res.get("error"):
                    entry["error"] = str(res["error"])
            except Exception as exc:  # keep going across variants
                entry["error"] = str(exc)
            exported.append(entry)

        # Restore the variant that was active before we started.
        #
        # Report whether that WORKED, not merely that it was attempted.
        # This loop leaves the project on the last variant it touched,
        # so a swallowed failure here hands back a project whose active
        # variant is not the one the caller had, and every
        # variant-sensitive operation after it acts on the wrong set:
        # an export, a BOM, or pcb_apply_dnp_paste_exclusion stripping
        # paste off the components the OTHER variant does not fit.
        restored: Optional[str] = None
        restore_error = ""
        if original:
            try:
                await bridge.send_command_async(
                    "project.set_active_variant", dict(params, variant_name=original)
                )
                restored = original
            except Exception as exc:
                restore_error = str(exc)

        result: dict[str, Any] = {
            "exported": exported,
            "count": sum(1 for e in exported if e["ok"]),
            "restored": restored,
        }
        if restore_error:
            result["restore_error"] = restore_error
            result["active_variant_unknown"] = True
            result["note"] = (
                "the originally-active variant could not be restored, so "
                "the project is left on the last variant exported. Set it "
                "back with proj_set_active_variant before any "
                "variant-sensitive operation."
            )
        return result

    @mcp.tool()
    async def proj_create_variant(
        name: str,
        description: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create a new project variant.

        After creating, use `proj_set_active_variant` to switch to it,
        and `proj_set_component_variation` to mark parts not fitted or
        give them an alternate part.

        Altium saves only the description and shows it as the variant's
        name; leave ``description`` empty and it takes ``name``. A name
        that differs from the description lasts until the project is
        reopened.

        Args:
            name: Name for the new variant
            description: Optional description
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary confirming creation with name and description
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"name": name}
        if description:
            params["description"] = description
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.create_variant", params
        )
        return result

    @mcp.tool()
    async def proj_set_component_variation(
        variant: str,
        designator: str,
        kind: str,
        alternate_part: str = "",
        project_path: Optional[str] = None,
        alternate_library: str = "",
        alternate_lib_ref: str = "",
        alternate_footprint: str = "",
    ) -> dict[str, Any]:
        """Set one part's entry in a project variant: fitted, not fitted,
        an alternate part, or no entry at all.

        Adds the variation when the part has none in that variant, changes
        it when it has one, and with ``kind="remove"`` deletes it, varied
        parameter values included, so the part follows the base design
        again. Parameter values: ``proj_set_parameter_variation``. The part is found by its
        designator in the compiled project; the entry is read back.

        Args:
            variant: The variant's name or description.
            designator: The part, e.g. "R12".
            kind: "fitted", "not_fitted", "alternate" or "remove".
            alternate_part: For ``alternate``: the alternate part's name,
                which Altium keeps as the part's varied Comment.
            project_path: The .PrjPcb. Defaults to the focused project.
            alternate_library: For ``alternate``: the library the
                alternate comes from, e.g. "Miscellaneous Devices.IntLib".
                Without it Altium saves the entry but loads it back as
                fitted when the project is reopened.
            alternate_lib_ref: The alternate's symbol in that library;
                defaults to ``alternate_part``.
            alternate_footprint: The alternate's footprint when it differs
                from the base part's.

        Save the project (proj_save) to keep the change.

        Returns:
            ``before`` and ``after`` (the entry's kind, or "none"),
            ``alternate_part``, ``alternate_link``, ``unique_id`` and
            ``applied``.
        """
        kind = kind.strip().lower()
        if kind not in ("fitted", "not_fitted", "alternate", "remove"):
            return {"ok": False, "reason": (
                "kind is fitted, not_fitted, alternate or remove")}
        if not variant.strip() or not designator.strip():
            return {"ok": False, "reason": "variant and designator are required"}
        if kind == "alternate" and not alternate_part.strip():
            return {"ok": False, "reason": "kind alternate needs alternate_part"}
        params: dict[str, Any] = {"variant": variant.strip(),
                                  "designator": designator.strip(), "kind": kind}
        if alternate_part.strip():
            params["alternate_part"] = alternate_part.strip()
        for key, value in (("alternate_library", alternate_library),
                           ("alternate_lib_ref", alternate_lib_ref),
                           ("alternate_footprint", alternate_footprint)):
            if value.strip():
                if kind != "alternate":
                    return {"ok": False, "reason": f"{key} is for kind alternate"}
                params[key] = value.strip()
        if project_path:
            params["project_path"] = project_path
        return await get_bridge().send_command_async(
            "project.set_component_variation", params, timeout=120.0)

    @mcp.tool()
    async def proj_set_parameter_variation(
        variant: str,
        designator: str,
        parameters: dict[str, Optional[str]],
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Set or clear the parameter values one part takes in a variant,
        e.g. R5's Value 4k7 in variant "Lite" while the base design has 10k.

        A value of None clears that parameter's variation, so the part
        takes the base value again. A part with no entry in the variant
        gets a fitted one to hold the values; a not-fitted part is
        refused. Every value is read back. ``proj_get_bom`` with
        ``variant`` applies them. Altium keeps an alternate part's name
        as its varied Comment, so changing Comment on an alternate
        renames the alternate.

        Args:
            variant: The variant's name or description.
            designator: The part, e.g. "R5".
            parameters: Parameter name to value, e.g. {"Value": "4k7",
                "Comment": "4k7"}; None clears it.
            project_path: The .PrjPcb. Defaults to the focused project.

        Save the project (proj_save) to keep the change.

        Returns:
            ``parameters`` (the part's varied values afterwards),
            ``entry_created``, ``not_on_part`` (names the part does not
            carry, written anyway), ``not_applied`` and ``applied``.
        """
        if not variant.strip() or not designator.strip():
            return {"ok": False, "reason": "variant and designator are required"}
        if not parameters:
            return {"ok": False, "reason": "parameters is empty"}
        sets: list[str] = []
        clears: list[str] = []
        for name, value in parameters.items():
            name = str(name).strip()
            if not name or "=" in name or "~~" in name:
                return {"ok": False, "reason": f"bad parameter name {name!r}"}
            if value is None:
                clears.append(name)
            elif "~~" in str(value):
                return {"ok": False, "reason": f"the value of {name} contains '~~'"}
            else:
                sets.append(f"{name}={value}")
        params: dict[str, Any] = {"variant": variant.strip(),
                                  "designator": designator.strip()}
        if sets:
            params["set"] = "~~".join(sets)
        if clears:
            params["clear"] = "~~".join(clears)
        if project_path:
            params["project_path"] = project_path
        return await get_bridge().send_command_async(
            "project.set_parameter_variation", params, timeout=120.0)

    @mcp.tool()
    async def proj_rename_variant(
        variant: str,
        new_name: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Rename a project variant.

        Altium saves a variant's description and shows it as the name, so
        the new name is written there (and to the in-memory name). A name
        another variant already has is refused. The name is read back.

        Args:
            variant: The variant's current name or description.
            new_name: Its new name.
            project_path: The .PrjPcb. Defaults to the focused project.

        Save the project (proj_save) to keep the change.

        Returns:
            ``name_before``, ``name``, ``was_active`` and ``applied``.
        """
        if not variant.strip() or not new_name.strip():
            return {"ok": False, "reason": "variant and new_name are required"}
        params: dict[str, Any] = {"variant": variant.strip(),
                                  "new_name": new_name.strip()}
        if project_path:
            params["project_path"] = project_path
        return await get_bridge().send_command_async(
            "project.rename_variant", params, timeout=60.0)

    @mcp.tool()
    async def proj_copy_variant(
        source: str,
        name: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Start a new variant from an existing variant's entries.

        Carries over every entry (fitted, not fitted, alternate with its
        library link), every varied parameter value and the variant's own
        parameters, then compares each entry with its source. Also the way
        to keep a copy before ``proj_delete_variant``.

        Args:
            source: The variant to copy, by name or description.
            name: The new variant's name (saved as its description, which
                Altium shows as the name); one already in use is refused.
            project_path: The .PrjPcb. Defaults to the focused project.

        Save the project (proj_save) to keep the change.

        Returns:
            ``entries_copied``, ``parameter_values_copied``,
            ``variant_parameters``, ``alternate_links_not_copied``,
            ``differs`` (designators whose copy does not match) and
            ``applied``.
        """
        if not source.strip() or not name.strip():
            return {"ok": False, "reason": "source and name are required"}
        params: dict[str, Any] = {"source": source.strip(), "name": name.strip()}
        if project_path:
            params["project_path"] = project_path
        return await get_bridge().send_command_async(
            "project.copy_variant", params, timeout=120.0)

    @mcp.tool()
    async def proj_net_color(
        net: str,
        color: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Read, or set, the colour the project gives a net.

        Args:
            net: The net name.
            color: "#RRGGBB", "none" to clear the project's colour for
                the net, or empty to only read.
            project_path: The .PrjPcb. Defaults to the focused project.

        Returns:
            ``color_before`` and ``color`` as "#RRGGBB", or "none" when the
            project sets no colour (and Altium's own integers as
            ``color_before_bgr`` / ``color_bgr``).
        """
        if not net.strip():
            return {"ok": False, "reason": "net is required"}
        params: dict[str, Any] = {"net": net.strip()}
        if color.strip().lower() == "none":
            params["color"] = str(_NO_NET_COLOR)
        elif color.strip():
            hexpart = color.strip().lstrip("#")
            if len(hexpart) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hexpart):
                return {"ok": False, "reason": "color is #RRGGBB"}
            r_, g_, b_ = (int(hexpart[i:i + 2], 16) for i in (0, 2, 4))
            params["color"] = str(r_ | (g_ << 8) | (b_ << 16))
        if project_path:
            params["project_path"] = project_path
        reply = await get_bridge().send_command_async("project.net_color", params)

        def _hex(bgr: Any) -> Any:
            if bgr == _NO_NET_COLOR:
                return "none"
            if not isinstance(bgr, int) or bgr < 0:
                return bgr
            return "#%02X%02X%02X" % (bgr & 0xFF, (bgr >> 8) & 0xFF, (bgr >> 16) & 0xFF)

        if isinstance(reply, dict):
            for key in ("color_before", "color"):
                if key in reply:
                    reply[key + "_bgr"] = reply[key]
                    reply[key] = _hex(reply[key])
        return reply

    @mcp.tool()
    async def proj_get_net_items(
        net: str = "",
        named_only: bool = False,
        limit: int = 500,
        project_path: Optional[str] = None,
        structure: bool = False,
    ) -> dict[str, Any]:
        """Show what gives each compiled net its name, and where.

        For each net: its net labels, power ports, ports, sheet entries,
        off-sheet connectors and ``directives`` (the ones the compiled net
        carries) with sheet and location, whether the
        compiler made the name up (``auto_generated``),
        ``local_scope`` (False when a power port gives the net a global
        name; a net joined across sheets by ports still reads True), its
        ``electrical`` type as Altium words it (Passive, Input, Output,
        I/O ...), ``erc_suppressed`` (a No ERC marker sits on it), and
        for a bus member ``bus_prefix`` and ``bus_index``. With one net,
        its pins too.

        Args:
            net: One net (any case). Empty lists every net.
            named_only: Skip nets that nothing names.
            limit: The most nets to list (default 500); ``truncated``
                says when more were left out.
            project_path: The .PrjPcb. Defaults to the focused project.
            structure: Add how each net is built, from the compiled
                model: ``sub_wires``, ``lines``, ``all_items``,
                ``removed_items``, ``scope``, ``signal_type``,
                ``calculated_name``, ``hidden_name``, ``bus_width`` and
                the count of ``output_pins``, ``io_pins``, ``power_pins``
                and ``open_collector_pins`` it joins (two outputs on one
                net drive against each other).

        Returns:
            ``nets_examined``, ``nets_listed``, ``truncated`` and
            ``nets``.
        """
        if limit < 1:
            return {"ok": False, "reason": "limit is 1 or more"}
        params: dict[str, Any] = {"limit": str(limit)}
        if net.strip():
            params["net"] = net.strip()
        if named_only:
            params["named_only"] = "true"
        if structure:
            params["structure"] = "true"
        if project_path:
            params["project_path"] = project_path
        return await get_bridge().send_command_async(
            "project.get_net_items", params, timeout=120.0)

    @mcp.tool()
    async def proj_step_export_options(options: Optional[dict] = None) -> dict[str, Any]:
        """Read, or change, the settings Altium's STEP exporter keeps.

        These are the settings File > Export > STEP 3D last used, kept
        between exports: export the folded rigid-flex board, copper,
        holes, how 3D models and parts are written, and the part name
        suffix. ``proj_export_step`` uses whatever is set here. Nothing
        is exported by this call.

        Args:
            options: Settings to write, by name: ``folded`` (bool),
                ``fold_rate`` (int), ``component_option``,
                ``models_option``, ``holes_option``, ``copper_option``
                (Altium's codes, read them first), ``mechanical_pad_holes``,
                ``electrical_pad_holes``, ``free_pad_holes``,
                ``barrels_only``, ``cover_layer`` (bool),
                ``component_suffix_type`` (int), ``component_suffix``
                (text). Omit to read only.

        Returns:
            Every setting as it now reads, ``not_taken`` (names that did
            not take) and ``read_errors``.
        """
        try:
            params = _step_option_params(options)
        except (ValueError, TypeError) as e:
            return {"ok": False, "reason": str(e)}
        return await get_bridge().send_command_async("project.step_options", params)

    @mcp.tool()
    async def proj_get_channels(project_path: str = "") -> dict[str, Any]:
        """List the sheet instances of a multi-channel design, as compiled.

        Every compiled sheet instance with its ``instance`` name,
        hierarchical ``path``, ``room``, ``channel_prefix``,
        ``channel_index`` and ``instance_number``, and the project's
        ``room_naming_style``. A design without repeated channels lists
        each sheet once.

        Args:
            project_path: The .PrjPcb. Defaults to the focused project.
        """
        params = {"project_path": project_path} if project_path else {}
        return await get_bridge().send_command_async(
            "project.get_channels", params, timeout=120.0)

    @mcp.tool()
    async def proj_component_models(project_path: str = "", designator: str = "",
                                    limit: int = 300) -> dict[str, Any]:
        """Show where each part's models resolve, as Altium finds them.

        For each compiled part, each model (footprint, simulation, 3D...)
        with its name, type, whether it is the current one and whether it
        comes from an integrated library, and per datafile the library it
        names, the entity, the kind and ``found_at``: the full path Altium
        resolves it to, empty when it finds none. ``datafiles_not_found``
        counts the links that resolve nowhere.

        Args:
            project_path: The .PrjPcb. Defaults to the focused project.
            designator: One part. Empty lists every part.
            limit: The most parts to list (default 300).
        """
        if limit < 1:
            return {"ok": False, "reason": "limit is 1 or more"}
        params: dict[str, Any] = {"limit": str(limit)}
        if project_path:
            params["project_path"] = project_path
        if designator:
            params["designator"] = designator
        return await get_bridge().send_command_async(
            "project.component_models", params, timeout=180.0)

    @mcp.tool()
    async def lib_library_manager(action: str, library: str = "", component: str = "",
                                  destination: str = "", limit: int = 500) -> dict[str, Any]:
        """Ask Altium's own library manager about a library or a part.

        Args:
            action: ``locate`` (where Altium finds ``component``, in
                ``library`` or across the installed ones when it is
                empty), ``contents`` (the parts of the library at
                ``library``, a full path, with model counts, whether each
                has a footprint and its first datafile; Altium reads only
                a library it has installed or an open project uses, and
                ``known_to_manager`` says which), or ``extract``
                (an IntLib at ``library`` back into its source libraries,
                in ``destination``).
            library: A library name for locate, a full path otherwise.
            component: The part, for locate.
            destination: The folder for extract.
            limit: The most parts contents lists (default 500).
        """
        import os
        action = (action or "").strip().lower()
        if action not in ("locate", "contents", "extract"):
            return {"ok": False, "reason": "action is locate, contents or extract"}
        before: dict[str, float] = {}
        if action == "extract":
            if not destination or not os.path.isabs(destination):
                return {"ok": False, "reason": "destination must be a full path"}
            if not library.lower().endswith(".intlib"):
                return {"ok": False, "reason": "extract takes an .IntLib"}
            os.makedirs(destination, exist_ok=True)
            before = _dir_stamps(destination)
        params = {"action": action, "library": library, "component": component,
                  "destination": destination, "limit": str(limit)}
        out = await get_bridge().send_command_async(
            "project.library_lookup", params, timeout=300.0)
        if action == "extract" and isinstance(out, dict):
            after = _dir_stamps(destination)
            out["files"] = sorted(p for p, t in after.items() if before.get(p) != t)
        return out

    @mcp.tool()
    async def proj_output_panel(last: int = 100, clear: bool = False) -> dict[str, Any]:
        """Read the lines in Altium's Output panel, newest last.

        Altium writes progress and results of many processes there
        (compiles, generators, scripts). ``clear`` empties the panel after
        reading, so the next read shows only what came since.
        """
        if last < 1:
            return {"ok": False, "reason": "last is 1 or more"}
        params = {"last": str(last)}
        if clear:
            params["clear"] = "true"
        return await get_bridge().send_command_async("project.output_panel", params)

    @mcp.tool()
    async def proj_add_generated_document(file_path: str,
                                          project_path: str = "") -> dict[str, Any]:
        """List a file the project produced under its Generated documents.

        For a report, PDF, STEP or drawing written outside an OutJob, so it
        shows with the project. The file itself is not changed. ``listed``
        is true once the project holds it.
        """
        import os
        if not file_path or not os.path.isabs(file_path):
            return {"ok": False, "reason": "file_path must be a full path"}
        if not os.path.isfile(file_path):
            return {"ok": False, "reason": f"no file at {file_path}"}
        params = {"file_path": file_path}
        if project_path:
            params["project_path"] = project_path
        return await get_bridge().send_command_async(
            "project.add_generated_document", params)

    @mcp.tool()
    async def proj_project_setup(
        move_document: str = "",
        move_to_index: Optional[int] = None,
        add_search_path: str = "",
        include_subfolders: bool = False,
        allow_port_net_names: Optional[bool] = None,
        allow_sheet_entry_net_names: Optional[bool] = None,
        hierarchy_mode: Optional[str] = None,
        output_path: str = "",
        append_sheet_number_to_local_nets: Optional[bool] = None,
        pin_swap_by_net_label: Optional[bool] = None,
        pin_swap_by_pin: Optional[bool] = None,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Read, or change, a project's document order, library search
        paths, net naming options, net identifier scope, output path and
        pin swap options.

        Call with no changes to read. Every change is read back, and
        any that did not take is listed in ``not_applied``. Save the
        project (proj_save) to keep the changes.

        Args:
            move_document: Full path of a document to move.
            move_to_index: Its new position, from 0 (with move_document).
            add_search_path: A folder to add to the library search paths.
            include_subfolders: Search below that folder too.
            allow_port_net_names: Ports name nets.
            allow_sheet_entry_net_names: Sheet entries name nets.
            hierarchy_mode: The net identifier scope: automatic, flat,
                hierarchical, global or strict_hierarchical (or Altium's
                number 0 to 4, in that order).
            output_path: The folder outputs are written to.
            append_sheet_number_to_local_nets: Add the sheet number to
                the names of nets local to one sheet.
            pin_swap_by_net_label: Pin swaps on the board may change
                net labels on the schematic.
            pin_swap_by_pin: Pin swaps on the board may move pins on
                the schematic.
            project_path: The .PrjPcb. Defaults to the focused project.

        Returns:
            ``documents`` (in project order), ``search_paths``,
            ``generated_documents``, the two naming options,
            ``hierarchy_mode`` (Altium's number) and
            ``hierarchy_mode_name``, ``output_path``,
            ``append_sheet_number_to_local_nets``,
            ``pin_swap_by_net_label``, ``pin_swap_by_pin``,
            ``changed``, ``moved_from`` / ``moved_to`` and
            ``not_applied``.
        """
        params: dict[str, Any] = {}
        if move_document.strip():
            if move_to_index is None or move_to_index < 0:
                return {"ok": False,
                        "reason": "move_to_index (0 or more) goes with move_document"}
            params["move_document"] = move_document.strip()
            params["move_to_index"] = str(move_to_index)
        elif move_to_index is not None:
            return {"ok": False, "reason": "move_to_index needs move_document"}
        if add_search_path.strip():
            params["add_search_path"] = add_search_path.strip()
            params["include_subfolders"] = "true" if include_subfolders else "false"
        for key, value in (("allow_port_net_names", allow_port_net_names),
                           ("allow_sheet_entry_net_names", allow_sheet_entry_net_names),
                           ("append_sheet_number_to_local_nets",
                            append_sheet_number_to_local_nets),
                           ("pin_swap_by_net_label", pin_swap_by_net_label),
                           ("pin_swap_by_pin", pin_swap_by_pin)):
            if value is not None:
                params[key] = "true" if value else "false"
        if hierarchy_mode is not None:
            text = str(hierarchy_mode).strip().lower().replace(" ", "_").replace("-", "_")
            if text in _HIERARCHY_MODES:
                params["hierarchy_mode"] = str(_HIERARCHY_MODES.index(text))
            elif text.isdigit() and int(text) < len(_HIERARCHY_MODES):
                params["hierarchy_mode"] = text
            else:
                return {"ok": False, "reason": "hierarchy_mode is one of "
                        + ", ".join(_HIERARCHY_MODES) + " or 0 to 4"}
        if output_path.strip():
            params["output_path"] = output_path.strip()
        if project_path:
            params["project_path"] = project_path
        reply = await get_bridge().send_command_async(
            "project.project_setup", params, timeout=60.0)
        if isinstance(reply, dict) and isinstance(reply.get("hierarchy_mode"), int):
            mode = reply["hierarchy_mode"]
            if 0 <= mode < len(_HIERARCHY_MODES):
                reply["hierarchy_mode_name"] = _HIERARCHY_MODES[mode]
        return reply

    @mcp.tool()
    async def proj_make_schematic_library(
        out_path: str,
        add_to_project: bool = False,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Make a schematic library of every symbol placed in the project.

        Altium's Make Schematic Library, run without its dialogs, then
        saved. One sheet of the project must be loaded
        (proj_load_sheets). The new library stays open in Altium.

        Args:
            out_path: Full path of the new .SchLib.
            add_to_project: Add the library to the project.
            project_path: The .PrjPcb. Defaults to the focused project.

        Returns:
            ``saved``, ``written`` (a new file is at out_path),
            ``sheet`` (the one it ran from) and ``in_project``.
        """
        import os
        if not out_path or not os.path.isabs(out_path):
            return {"ok": False, "reason": "out_path must be a full path"}
        if not out_path.lower().endswith(".schlib"):
            return {"ok": False, "reason": "out_path is a .SchLib"}
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        before = os.path.getmtime(out_path) if os.path.isfile(out_path) else None
        params: dict[str, Any] = {"out_path": out_path,
                                  "add_to_project": "true" if add_to_project else "false"}
        if project_path:
            params["project_path"] = project_path
        bridge = get_bridge()
        reply = await bridge.send_command_async(
            "project.make_schematic_library", params, timeout=300.0)
        if isinstance(reply, dict) and "sheet" in reply:
            # Altium opens the new library in the editor and leaves it
            # unsaved (measured on AD 26.11), so it is saved here.
            saved = await bridge.send_command_async(
                "application.save_document", {"file_path": out_path}, timeout=60.0)
            reply["saved"] = bool(isinstance(saved, dict) and saved.get("saved"))
            after = os.path.getmtime(out_path) if os.path.isfile(out_path) else None
            reply["written"] = after is not None and after != before
        return reply

    @mcp.tool()
    async def proj_make_integrated_library(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Make an integrated library of every part the project places.

        Altium's Design > Make Integrated Library, which runs with no
        dialog: the symbols and footprints of the placed parts are
        compiled into "<project>.IntLib" in the project folder,
        replacing one already there. One document of the project must
        be open (proj_load_sheets).

        Args:
            project_path: The .PrjPcb. Defaults to the focused project.

        Returns:
            ``intlib``, ``built`` (a new file is there, read from its
            time), ``bytes`` and the ``focused_document`` it ran from.
        """
        import os
        import asyncio
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        bridge = get_bridge()
        path = project_path
        if not path:
            focused = await bridge.send_command_async("project.get_project_path")
            path = focused.get("project_path") if isinstance(focused, dict) else None
        if not path:
            return {"ok": False, "reason": "no project is focused; pass project_path"}
        intlib = os.path.splitext(path)[0] + ".IntLib"
        before = os.path.getmtime(intlib) if os.path.isfile(intlib) else None
        reply = await bridge.send_command_async(
            "project.make_integrated_library", params, timeout=600.0)
        if not isinstance(reply, dict) or "project" not in reply:
            return reply
        intlib = os.path.splitext(reply["project"])[0] + ".IntLib"
        # The compile can finish writing a moment after the call returns.
        for _ in range(20):
            after = os.path.getmtime(intlib) if os.path.isfile(intlib) else None
            if after is not None and (before is None or after > before):
                break
            await asyncio.sleep(0.5)
        built = after is not None and (before is None or after > before)
        reply.update(intlib=intlib, built=built,
                     bytes=os.path.getsize(intlib) if os.path.isfile(intlib) else 0)
        if not built:
            reply["reason"] = ("no new IntLib after the run; read Altium's "
                               "Messages panel for compile errors")
        return reply

    @mcp.tool()
    async def proj_get_declared_design_objects(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """The net classes, component classes, differential pairs and rooms
        the project's schematics declare, from the compiled project.

        What an Update PCB would create on the board; compare with
        pcb_get_net_classes / pcb_get_differential_pairs.

        Returns:
            ``sheets_examined`` (an empty answer from 0 sheets means
            nothing was read), ``net_classes`` and
            ``component_classes`` (``name``, ``sheet``,
            ``members``), ``differential_pairs`` (``name``, ``positive``,
            ``negative``) and ``rooms`` (``name``, ``scope``).
        """
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        return await get_bridge().send_command_async(
            "project.get_declared_design_objects", params, timeout=120.0)

    @mcp.tool()
    async def proj_delete_variant(
        variant_name: str,
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Delete a project variant, with every component variation in it.

        NOT REVERSIBLE: run ``proj_copy_variant`` first to keep a copy.
        Read the variant with ``proj_list_variants`` first if you did not
        create it; ``entries_removed`` reports how many went. Addressed
        by name, since deleting one renumbers the rest.

        Args:
            variant_name: name of the variant to remove.
            project_path: optional project path; defaults to the focused
                project.

        Returns:
            ``{"success": true, "variant_name": ..., "entries_removed": N,
            "variant_count_before": N, "variant_count_after": N,
            "verified": true}``, or ``success: false`` with a ``reason``
            when the variant is still listed afterwards.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"variant_name": variant_name}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.delete_variant", params
        )
        return result

    # ------------------------------------------------------------------
    # Additional project operations
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_list_open() -> dict[str, Any]:
        """List all currently open projects in the Altium workspace.

        Returns:
            Dictionary with "projects" array (each: project_name,
            project_path, document_count) and "count"
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.get_open_projects", {}
        )
        return result

    # ------------------------------------------------------------------
    # Messages, search, connectivity, import, path, document parameters
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_get_messages(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get all messages from the Messages panel (compile errors, ERC violations, etc.).

        Compiles the project first.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with "messages" array (each: message, severity, source) and "count"
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.get_messages", params)
        return result

    @mcp.tool()
    async def proj_find_component(
        search_text: str,
        search_by: str = "designator",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Search for components across all project sheets.

        Case-insensitive partial match. For a technical question about a
        match, read its datasheet; symbol metadata is not ground truth.

        Args:
            search_text: Text to search for (e.g., "U1", "100nF", "LM317")
            search_by: Property to search, "designator", "value", or "comment" (default "designator")
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with "results" array (each: designator, comment, footprint,
            lib_ref, sheet, location_x, location_y) and "count", plus
            `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "search_text": search_text,
            "search_by": search_by,
        }
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async("project.find_component", params)
        if isinstance(result, dict):
            # find_component response puts matches under "results"
            # with comment/value fields, reshape to the components form
            # extract_unique_parts understands.
            synthetic = {"components": result.get("results") or []}
            return tag_response(
                result,
                components=synthetic,
                context="proj_find_component",
            )
        return result

    @mcp.tool()
    async def proj_get_connectivity(
        designator: str,
        project_path: Optional[str] = None,
        force_recompile: bool = False,
    ) -> dict[str, Any]:
        """Get pin-to-net connectivity for a specific component.

        Compiles the project; every pin with number, name, net and type.

        Args:
            designator: Component designator (e.g., "U1", "R8")
            project_path: Optional project path. If None, uses active.
            force_recompile: SaveAll + invalidate cache + recompile
                before reading. Use when you need a guaranteed-fresh
                netlist.

        Pin names come from the symbol and can be wrong: check a pin's
        function against the manufacturer datasheet before relying on it
        (see `_datasheet_guidance`).

        Returns:
            Dict with designator, comment, sheet, pin_count, pins[],
            plus `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"designator": designator}
        if project_path:
            params["project_path"] = project_path
        if force_recompile:
            # Project.pas ForceRecompileIfRequested reads
            # "force_recompile". Sent under the tool's own name this
            # matched nothing, so the flag was dead and a caller who
            # asked for fresh data got the cached compile instead.
            params["force_recompile"] = "true"
        result = await bridge.send_command_async("project.get_connectivity", params)
        hint = BulkHintTracker.record_and_hint("proj_get_connectivity")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        if isinstance(result, dict):
            comment = str(result.get("comment") or "").strip()
            explicit = [{
                "manufacturer": "",
                "part_number": comment,
                "designators": designator,
            }]
            return tag_response(
                result, explicit_parts=explicit, context="proj_get_connectivity"
            )
        return result

    @mcp.tool()
    async def proj_get_connectivity_many(
        designators: list[str],
        project_path: Optional[str] = None,
        force_recompile: bool = False,
    ) -> dict[str, Any]:
        """Pin-net connectivity for MANY components in ONE round-trip.

        One compile for the whole list (a list of one for a single part).
        Pin names come from the symbol and can be wrong: check pin
        functions in the datasheet.

        Args:
            designators: List of component designators.
            project_path: Optional project path.
            force_recompile: SaveAll + invalidate cache + recompile
                before reading. Use when you need a guaranteed-fresh
                netlist.

        Returns:
            Dict with components[], matched, requested, not_found[],
            plus `_datasheet_guidance` + `_datasheet_parts`.
        """
        bridge = get_bridge()
        cleaned = [str(d).strip() for d in (designators or []) if str(d).strip()]
        if not cleaned:
            return {"error": "No designators provided", "matched": 0}
        params: dict[str, Any] = {"designators": "~~".join(cleaned)}
        if project_path:
            params["project_path"] = project_path
        if force_recompile:
            # Project.pas ForceRecompileIfRequested reads
            # "force_recompile". Sent under the tool's own name this
            # matched nothing, so the flag was dead and a caller who
            # asked for fresh data got the cached compile instead.
            params["force_recompile"] = "true"
        result = await bridge.send_command_async(
            "project.get_connectivity_batch", params
        )
        if isinstance(result, dict):
            comps = result.get("components") or []
            explicit = []
            seen_keys: set[tuple[str, str]] = set()
            for c in comps:
                if not isinstance(c, dict):
                    continue
                desig = str(c.get("designator") or "").strip()
                pn = str(c.get("comment") or "").strip()
                key = (pn.lower(), desig.lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                explicit.append({
                    "manufacturer": "",
                    "part_number": pn,
                    "designators": desig,
                })
            return tag_response(
                result,
                explicit_parts=explicit,
                context="proj_get_connectivity_many",
            )
        return result

    @mcp.tool()
    async def proj_get_erc_levels(
        match: str = "",
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """The project's ERC report level for every violation type (Project
        Options > Error Reporting): no_report, warning, error or fatal.

        Args:
            match: only types whose description contains this text, e.g.
                "multiple names" or "driving source".
            project_path: the .PrjPcb; default the focused project.

        Returns:
            ``levels``: [{kind, description, level}], and ``count``.
        """
        params: dict[str, Any] = {"match": match}
        if project_path:
            params["project_path"] = project_path
        bridge = get_bridge()
        return await bridge.send_command_async("project.get_erc_levels", params,
                                               timeout=30.0)

    @mcp.tool()
    async def proj_set_erc_levels(
        levels: dict[str, str],
        project_path: Optional[str] = None,
        save: bool = True,
    ) -> dict[str, Any]:
        """Set ERC report levels (Project Options > Error Reporting).

        A project's policy, not a way to a clean ERC. Before turning a
        check off because it reports something that is not there, rule out
        a stale compile (proj_force_recompile): a resized or redrawn sheet
        can report "Off sheet" or duplicates that are gone.

        Args:
            levels: {violation type: level}. A type is its exact description
                as proj_get_erc_levels lists it ("Nets with multiple names")
                or its kind number; a level is no_report, warning, error or
                fatal. Example: {"Nets with no driving source": "no_report"}.
            project_path: the .PrjPcb; default the focused project.
            save: save the project file afterwards so the setting persists.

        Returns:
            ``set``, ``failed`` and per item ``results`` with the level read
            back (``problem`` when it did not take), and ``saved``.
        """
        if not levels:
            return {"success": False, "error": "levels is empty"}
        for kind, level in levels.items():
            # Altium splits each item at its first '=' and items at '~~'.
            if "~~" in str(kind) or "~~" in str(level) or "=" in str(kind):
                return {"success": False,
                        "error": f"{kind!r}: '~~' and '=' cannot appear in a type"}
        params: dict[str, Any] = {
            "levels": "~~".join(f"{k}={v}" for k, v in levels.items())}
        if project_path:
            params["project_path"] = project_path
        bridge = get_bridge()
        out = await bridge.send_command_async("project.set_erc_levels", params,
                                              timeout=30.0)
        if not isinstance(out, dict):
            return out
        out = dict(out)
        out["saved"] = False
        if save and out.get("set"):
            target = project_path or out.get("project", "")
            try:
                reply = await bridge.send_command_async(
                    "application.save_document", {"file_path": target}, timeout=60.0)
                out["saved"] = bool(isinstance(reply, dict) and reply.get("saved"))
            except Exception as exc:                          # noqa: BLE001
                out["save_problem"] = str(exc)
        return out

    @mcp.tool()
    async def proj_force_recompile() -> dict[str, Any]:
        """Flush all dirty docs, invalidate the compile cache, and recompile.

        For a netlist that is surely fresh. The compile ticks before and
        after show the recompile happened.

        Returns:
            Dict with recompiled, prev_compile_tick, new_compile_tick,
            project path.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "project.force_recompile", {}, timeout=120.0
        )

    @mcp.tool()
    async def proj_get_compile_freshness() -> dict[str, Any]:
        """Report the age of the cached netlist and which docs are dirty.

        Check this before disagreeing with the user about connectivity:
        a dirty document means the netlist does NOT reflect what the user
        is looking at.

        ``needs_compile`` and ``violation_count`` are Altium's own
        answer: whether the project needs compiling now, and how many
        violations its last compile left (-1 when it would not say).

        Returns:
            Dict with compile_age_ms, needs_compile, violation_count,
            compile_cached (bool), ttl_ms, open_doc_count,
            dirty_doc_count, dirty_docs[], project.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "project.get_compile_freshness", {}
        )

    @mcp.tool()
    async def proj_import_document(
        source_path: str,
    ) -> dict[str, Any]:
        """Import a document into the focused project from an external path.

        Copies the file into the project directory (if not already there),
        adds it to the project, and saves the project file.

        Args:
            source_path: Full path to the source document (SchDoc, PcbDoc, etc.)

        Returns:
            Dictionary with success status, source_path, and dest_path
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.import_document", {"source_path": source_path}
        )
        return result

    @mcp.tool()
    async def proj_get_path() -> dict[str, Any]:
        """Get the full path of the currently focused project file.

        Returns:
            Dictionary with project_path, project_dir, and project_name
        """
        bridge = get_bridge()
        result = await bridge.send_command_async("project.get_project_path")
        return result

    @mcp.tool()
    async def proj_set_document_parameter(
        file_path: str,
        name: str,
        value: str,
    ) -> dict[str, Any]:
        """Set a document-level parameter on a specific schematic sheet.

        Useful for per-sheet title block data (e.g., "SheetTitle", "Revision").
        If the parameter already exists it is updated; otherwise a new hidden
        parameter object is created on the sheet.

        The sheet must be loaded (`proj_load_sheets`), else NOT_LOADED.
        The write goes to disk at once; no `app_save_all` is needed.

        Args:
            file_path: Full path to the schematic document (.SchDoc).
                Use Windows-style backslashes (e.g. C:\\path\\Sheet.SchDoc),
                not forward slashes.
            name: Parameter name
            value: Parameter value

        Returns:
            Dictionary with file_path, name, value, and dirty=true
            (the in-memory flag; the disk write already happened, so no
            `app_save_all` is needed for this parameter).
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.set_document_parameter",
            {"file_path": file_path, "name": name, "value": value},
        )
        return result

    # ------------------------------------------------------------------
    # Design verification and cross-probing tools
    # ------------------------------------------------------------------

    @mcp.tool()
    async def proj_compare_sch_pcb(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Compare schematic and PCB component counts.

        Compiles the project. ``proj_get_differences`` gives the
        per-component breakdown. Before proposing a footprint or pin fix,
        read the parts' datasheets.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with sch_components, pcb_components, components_match
            (bool), and pcb_path. (Net-count comparison is not provided here;
            use ``proj_get_nets`` / ``pcb_get_nets``.)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.compare_sch_pcb", params
        )
        return result

    @mcp.tool()
    async def proj_sync_pcb() -> dict[str, Any]:
        """Push schematic changes to the PCB (Design > Update PCB Document).

        NOT SILENT: the ECO dialog BLOCKS the bridge until Execute Changes
        is pressed. Prefer opening it without blocking and pressing it
        yourself: app_menu(how="click", args={"menu_path": "Design|Import
        Changes From <Project>.PrjPcb", "expect_dialog": "Engineering
        Change Order"}), then app_press_dialog_button("Execute Changes",
        allow_irreversible=True) and "Close". It focuses the PCB first so the update runs
        schematic to board (refuses with WRONG_FOCUS if it cannot); check
        the ECO lists the PcbDoc before executing. ``app_list_open_dialogs``
        reads the dialog while the bridge is blocked.

        Returns:
            success, pcb_path, before/after mapping counts,
            components_added_to_pcb, components_removed_from_pcb,
            components_in_sync, and dialog_outcome_verified (always
            false: which button was pressed is unknown). The counts see
            components added or removed only. ``after_sync`` then reads the
            board against the schematic: ``footprint_mismatch`` (the
            board's footprint is not the schematic's), ``missing_on_board``
            and ``diff_pairs_removed`` (pairs defined only on the board,
            which a sync deletes).
        """
        bridge = get_bridge()
        pairs_before = await _pair_names(bridge)
        result = await bridge.send_command_async("project.update_pcb", {})
        if isinstance(result, dict):
            try:
                result["after_sync"] = await _after_sync(bridge, pairs_before)
            except Exception as exc:            # noqa: BLE001 - a report only
                result["after_sync"] = {"error": f"not read: {exc}"}
        return result

    @mcp.tool()
    async def proj_sync_schematic() -> dict[str, Any]:
        """Push PCB changes back to schematic (back-annotate ECO). Attempts silent execution.

        Design > Update Schematic, compiled before and after with the
        change reported. Older Altium versions may still show a dialog
        (``dialog_may_have_opened``).

        Returns:
            Dictionary with success, pcb_path, before/after mapping counts,
            components_added_to_schematic, components_removed_from_schematic,
            in_sync, and dialog_may_have_opened flag.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.update_schematic", {}
        )
        return result

    @mcp.tool()
    async def proj_get_differences(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get detailed differences between schematic and PCB netlist.

        Compiles the project and uses component mappings to find:
        - Matched components (present in both schematic and PCB)
        - Extra components in schematic (not yet in PCB)
        - Extra components in PCB (not in schematic)

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with matched_components, extra_in_schematic (array),
            extra_in_pcb (array), and in_sync (bool)
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.get_design_differences", params
        )
        return result

    @mcp.tool()
    async def proj_lock_designator(
        designator: str,
        lock: bool = True,
    ) -> dict[str, Any]:
        """Lock or unlock component designators to prevent re-annotation.

        When locked, designators are preserved during annotation operations.
        Use designator="all" to lock/unlock all components on the active sheet.

        Args:
            designator: Component designator (e.g., "U1", "R3") or "all" for all components
            lock: True to lock, False to unlock (default True)

        Returns:
            Dictionary with designator, locked status, and count of affected components
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "project.lock_designator",
            {
                "designator": designator,
                "lock": "true" if lock else "false",
            },
        )
        return result

    @mcp.tool()
    async def proj_get_options(
        project_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Get project options: output path, hierarchy mode, compiler settings.

        Returns configuration details including output directory, hierarchy mode,
        document counts, variant count, channel settings, and net naming options.

        Args:
            project_path: Optional project path. If None, uses active project.

        Returns:
            Dictionary with project_name, output_path, hierarchy_mode,
            logical_document_count, physical_document_count, variant_count,
            channel settings, and net naming options
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if project_path:
            params["project_path"] = project_path
        result = await bridge.send_command_async(
            "project.get_project_options", params
        )
        return result
