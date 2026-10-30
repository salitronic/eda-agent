# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Application-level tools for Altium Designer MCP Server."""

import functools
import os
import re
from typing import Any, Optional
from .. import __version__ as _mcp_server_version
from ..bridge import get_bridge, AltiumNotRunningError
from ..bridge.exceptions import AltiumCommandError
from ..cli import get_bundled_scripts_path
from .datasheet_hints import DATASHEET_RULES
from ..atomicfile import replace_with_retry
from .. import updates


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

# The reminder is about 750 tokens. app_ping and app_attach carry it on
# their first call in this server and then on every Nth call between
# them, so a long session still sees it now and then without paying for
# it on every health check. EDA_AGENT_REMINDER_EVERY sets N; 1 sends it
# every time.
_REMINDER_EVERY_ENV = "EDA_AGENT_REMINDER_EVERY"
_REMINDER_EVERY_DEFAULT = 10
_reminder_calls = 0


def _reminder_every() -> int:
    raw = (os.environ.get(_REMINDER_EVERY_ENV) or "").strip()
    try:
        every = int(raw) if raw else _REMINDER_EVERY_DEFAULT
    except ValueError:
        every = _REMINDER_EVERY_DEFAULT
    return max(every, 1)


def _with_session_reminder(reply: dict[str, Any]) -> dict[str, Any]:
    """Add ``_system_reminder`` to ``reply`` when this call is due one."""
    global _reminder_calls
    _reminder_calls += 1
    if (_reminder_calls - 1) % _reminder_every() == 0:
        reply["_system_reminder"] = _SESSION_REMINDER
    return reply


def _reports_update(fn):
    """Add ``update`` to a reply when the daily check found a newer
    eda-agent, so a session hears of it from the tools it calls first.
    Read from the last check only; never waits on the network."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        reply = await fn(*args, **kwargs)
        try:
            news = updates.notice()
        except Exception:  # noqa: BLE001  news never breaks a ping
            news = None
        if news and isinstance(reply, dict):
            reply["update"] = news
        return reply
    return wrapper


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


def _bundled_script_path() -> Optional[str]:
    """The Main.pas that _bundled_script_version reads, as a string."""
    try:
        return str(get_bundled_scripts_path() / "Main.pas")
    except Exception:
        return None


def _folder_of(file_path: str) -> str:
    """A file's folder, comparable across slash style, case and WSL mounts."""
    text = str(file_path).replace("\\", "/")
    mounted = re.match(r"^/mnt/([A-Za-z])/(.*)$", text)
    if mounted:
        text = f"{mounted.group(1)}:/{mounted.group(2)}"
    return text.rsplit("/", 1)[0].rstrip("/").lower()


def _script_location(loaded: Any) -> tuple[Optional[bool], list[str]]:
    """Whether Altium's open script project is one this server compares with.

    ``loaded`` is the list of open Altium_API.PrjScr paths the running
    script reports, or None when it predates reporting them. The folders
    this server knows are the bundled scripts (what version_match reads)
    and the install-scripts destination. Neither answer changes
    version_match: it still describes the bundled Main.pas.
    """
    if not isinstance(loaded, list) or not loaded:
        return None, []
    from ..cli import get_default_scripts_dest

    bundled = _bundled_script_path()
    known = {_folder_of(str(get_default_scripts_dest() / "Main.pas"))}
    if bundled:
        known.add(_folder_of(bundled))
    match = any(_folder_of(str(p)) in known for p in loaded)
    warnings = []
    if not match:
        warnings.append(
            f"Altium has the script project open from {', '.join(map(str, loaded))}, "
            f"but SCRIPT_VERSION was compared against {bundled}. version_match "
            f"describes that file, not the script Altium loaded.")
    if len(loaded) > 1:
        warnings.append(
            f"{len(loaded)} Altium_API.PrjScr projects are open in Altium "
            f"({', '.join(map(str, loaded))}); only one of them runs the bridge.")
    return match, warnings


#: What clears Altium's compiled-unit cache. Re-running StartMCPServer does
#: not recompile an edited unit, and restarting Altium was measured not to
#: either, because Altium reopens the script project with its compiled units.
RELOAD_STEP = ("close the script project in Altium's Projects panel (right-click Altium_API.PrjScr, Close Project), reopen it and run StartMCPServer")


def _unit_stamp_check(bridge: Any, loaded: Any) -> tuple[dict, list[str]]:
    """Compare each unit's running stamp with the files Altium loaded.

    Returns the fields for app_ping and any warning. A script too old to
    report stamps, or one run from unstamped repository files, says so
    rather than passing as current.
    """
    from pathlib import Path

    from ..cli import get_default_scripts_dest
    from ..script_stamps import stale_units

    try:
        reported = bridge.send_command("application.get_unit_stamps", timeout=3.0)
    except Exception:                            # noqa: BLE001 - older script
        return {"unit_stamps": None}, []
    if not isinstance(reported, dict):
        return {"unit_stamps": None}, []
    folders = [Path(str(p)).parent for p in (loaded or []) if p]
    folders.append(get_default_scripts_dest())
    folder = next((f for f in folders if (f / "Main.pas").is_file()), None)
    if folder is None:
        return {"unit_stamps": reported, "stale_units": None}, []
    stale, unstamped = stale_units(reported, folder)
    warnings = []
    if stale:
        warnings.append(
            f"Altium is still running an older compile of {', '.join(stale)} "
            f"than the files in {folder}. To recompile, {RELOAD_STEP}. An "
            f"'Undeclared identifier' naming something those units define is "
            f"this, not a code bug.")
    return {"unit_stamps": reported, "stale_units": stale,
            "unstamped_units": unstamped, "stamps_compared_with": str(folder)}, warnings


def _server_workspace_dir() -> str:
    """The folder this server writes its requests to, or '' unknown."""
    try:
        from ..config import get_config
        return str(get_config().workspace_dir)
    except Exception:                              # noqa: BLE001
        return ""


def _other_servers() -> tuple[list, Optional[str]]:
    """Other eda-agent servers on this workspace, and the warning for them."""
    from .. import server_registry
    from ..config import get_config

    try:
        found = server_registry.others(get_config().workspace_dir)
    except Exception:                            # noqa: BLE001 - a warning only
        return [], None
    return found, server_registry.duplicate_warning(found)


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
        """Find tools by category, maturity, interaction or text, without
        loading every schema.

        Filters (all optional, AND-combined):
            category: core, application, project, library, generic,
                schematic, pcb, audit, design, simulation, routing, meta,
                parts, altium365 (and kicad on the KiCad backend). "core" is the
                EDA-agnostic main flow and the usual start. No filter
                returns the live ``categories`` map.
            maturity: ``offline`` (no Altium), ``simulator`` or
                ``live_only``.
            interaction: readonly | silent | modal | partial. ``modal``
                pops a blocking dialog; ``partial`` leaves the job
                incomplete.
            query: case-insensitive substring of the name or description.
            with_description: include the one-line summary per tool.
            with_schema: include parameters and required list; needed
                before tool_invoke under the minimal toolset. Capped, so
                filter first.

        Returns ``{count, tools:[{name, category, maturity, interaction
        [, description][, parameters, required]}], categories:{cat: n}}``.
        """
        from .metadata import tool_metadata

        _SCHEMA_CAP = 40

        from .folds import reach_of

        tools = list(await mcp.list_tools())
        # Members of a family tool and single-item tools are not
        # advertised; they are listed with how to reach them.
        tools += list((getattr(mcp, "hidden", {}) or {}).values())
        q = query.lower().strip()
        out = []
        cat_counts: dict[str, int] = {}
        for t in tools:
            md = tool_metadata(t.name)
            reach = reach_of(t.name)
            if reach:
                md = dict(md, reach=reach)
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
        known = {t.name for t in tools} | set(getattr(mcp, "hidden", {}) or {})
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
                out = {"tool": name, "result": _json.loads(content[0].text)}
            except (ValueError, TypeError):
                out = {"tool": name, "result": content[0].text}
        else:
            out = {"tool": name, "result": content}
        from .folds import FOLDED_BATCH
        if name in FOLDED_BATCH:
            out["note"] = (f"{name} is now {FOLDED_BATCH[name]}(items=[...]), "
                           "which runs the same handler")
        return out


async def _run_declared_menu(bridge, menu_path: str) -> dict:
    """Run a menu path through the command Altium's menu files give it.

    The focused document picks the menu bar, because "Tools|Teardrops"
    exists on a board and not on a schematic.
    """
    import asyncio

    from ..safety import refuse_process
    from ..ui import altium_menus

    active = await bridge.send_command_async(
        "application.get_active_document", {})
    editor = str((active or {}).get("document_kind") or "").upper() \
        if isinstance(active, dict) else ""
    loop = asyncio.get_running_loop()
    item = await loop.run_in_executor(
        None, altium_menus.resolve, menu_path, editor)
    if item is None:
        near = await loop.run_in_executor(
            None, lambda: altium_menus.nearest(menu_path, editor, 8))
        return {"success": False, "dispatched": False,
                "error": "UNMAPPED_MENU_PATH", "menu_path": menu_path,
                "editor": editor or None,
                "reason": (f"{menu_path!r} is not in the "
                           f"{editor or 'focused'} editor's menus as Altium "
                           f"declares them"),
                "nearest": near}
    refusal = refuse_process(item.command)
    if refusal is not None:
        return {"success": False, "dispatched": False,
                "error": "REFUSED_BY_POLICY", "reason": refusal,
                "menu_path": "|".join(item.path), "command": item.command}
    params = "|".join(p.strip() for p in item.params.split("|") if p.strip())
    params = "|".join("=".join(s.strip() for s in p.split("=", 1))
                      for p in params.split("|") if p)
    result = await bridge.send_command_async(
        "generic.run_process", {"process": item.command, "params": params})
    out = {"success": True, "dispatched": True, "outcome_verified": False,
           "menu_path": "|".join(item.path), "editor": editor,
           "command": item.command, "params": params,
           "shortcut": item.shortcut or None,
           "note": ("run through the command behind the menu item, with no "
                    "menu walk; Altium does not report whether a process "
                    "did anything, so read the result back")}
    if isinstance(result, dict):
        out["bridge"] = result
    return out


def register_application_tools(mcp):
    """Register application tools with the MCP server."""

    @mcp.tool()
    async def app_find_menu_command(query: str, editor: str = "",
                                    limit: int = 20) -> dict[str, Any]:
        """Search every Altium menu item and the command behind it.

        Read from Altium's own menu definitions in its install, so it
        answers without Altium's window, in any language Altium ships
        (words may be English, 中文, 日本語, Deutsch, ...). Each hit gives
        the menu ``path`` (English), the ``command`` and ``params`` it
        runs, and its ``shortcut``. Run one with ``app_run_menu`` (no
        menu walk) or ``app_click_menu`` (the real menu).

        Args:
            query: words to find, all of which must match.
            editor: PCB, SCH, PCBLIB or SCHLIB; empty searches all four.
            limit: how many hits to return.
        """
        import asyncio

        from ..ui import altium_locale, altium_menus

        loop = asyncio.get_running_loop()
        hits = await loop.run_in_executor(
            None, lambda: altium_menus.search(query, editor, limit))
        install = altium_locale.install_dir()
        return {"success": True, "query": query, "editor": editor or None,
                "count": len(hits), "hits": hits,
                "install": str(install) if install else None}

    @mcp.tool()
    async def app_get_status() -> dict[str, Any]:
        """Check if Altium Designer is running and get status information.

        Returns information about the Altium Designer process including:
        - Whether Altium is running
        - Process ID
        - Executable path
        - Whether the MCP bridge is attached

        MORE THAN ONE ALTIUM. ``candidate_count`` and ``candidate_pids``
        list every instance, and ``selected_by`` says how ``pid`` was
        chosen: the instance owning the bridge's status window, or the
        only one with a visible window. When neither decides it, ``pid``
        is None, ``ambiguous`` is True and ``reason`` explains, and every
        UI tool refuses rather than read the wrong process's dialogs.

        MORE THAN ONE eda-agent SERVER. ``other_servers`` lists the other
        servers recorded on this workspace (pid and start time), and
        ``warnings`` says so when there are any. A warning only: several
        sessions at once are allowed, and nothing is stopped.

        Returns:
            Dictionary with status information
        """
        bridge = get_bridge()
        status = dict(bridge.get_altium_status())
        servers, warning = _other_servers()
        status["other_servers"] = servers
        if warning:
            status.setdefault("warnings", []).append(warning)
        return status

    @mcp.tool()
    @_reports_update
    async def app_context() -> dict[str, Any]:
        """Where you are, in one call. Run this before anything else.

        Reports the facts behind most "why didn't that work": which
        document is focused and its kind (most tools act on it, and a
        pcb_ tool aimed at a focused library can act on another board);
        whether the running script matches the deployed one (a deployed
        fix does nothing until reloaded); what is unsaved; and whether
        the bridge answers at all.

        It cannot tell which library component is current; if a lib_add_*
        call lands somewhere unexpected, set it with
        ``lib_set_current_component``.

        Returns:
            ``{"backend", "bridge_answering", "script_version_match",
            "running_script_version", "deployed_script_version",
            "altium_version", "active_document", "open_document_count",
            "unsaved": [paths], "warnings", "next_step"}``.

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
        # Where the script project is open from, and other servers on this
        # workspace. Not repeated when the message above already says it.
        already = " ".join(warnings)
        warnings.extend(w for w in ping.get("warnings") or []
                        if w not in already)

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
            return _with_session_reminder({
                "attached": True,
                "script_loaded": script_loaded,
                "message": "Connected to Altium Designer, script is responding"
                if script_loaded
                else "Altium is running but script is not responding. Run StartMCPServer in Altium_API.PrjScr.",
            })
        except AltiumNotRunningError as e:
            return _with_session_reminder({
                "attached": False,
                "script_loaded": False,
                "message": str(e),
            })

    @mcp.tool()
    async def app_save_all() -> dict[str, Any]:
        """Flush every dirty Altium document to disk.

        Edit tools change documents in memory only; call this at
        checkpoints (after routing, before DRC, before closing). Detach
        saves too. A library no tool edited is not written (listed in
        ``libraries_left_unedited``); hand edits are not tracked, so save
        those with ``app_save_document``.

        CHECK ``still_dirty``: Altium declines to save, silently, while a
        command is active in the editor, and ``saved`` is false then. Run
        ``app_exit_active_command`` and retry.

        Returns:
            Dict with ``saved``, ``still_dirty``, ``libraries_left_unedited``,
            and a ``reason`` when documents remain unsaved.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.save_all", timeout=60.0
        )
        return result

    @mcp.tool()
    async def app_save_document(file_path: str = "") -> dict[str, Any]:
        """Save ONE open document: ``file_path``, or the focused one.

        ``app_save_all`` writes every open document, so one that cannot
        be written, a sheet in a read-only folder say, stops a save you
        never asked for. This touches only the named document, never
        changes focus and never walks the workspace.

        ``saved`` is measured from the file's age on disk: Altium does
        not report a declined save. ``was_modified`` says whether Altium
        thought there was anything to write, and it misses edits made
        through scripts: a board changed only by this server's tools read
        false and was saved all the same. A save Altium declines while
        a command is active reads ``saved`` false; run
        ``app_exit_active_command`` and retry.

        Args:
            file_path: Absolute path of an open document. Empty saves the
                focused document.

        Returns:
            ``file_path``, ``saved``, ``was_modified`` and a ``reason``
            when it was not saved. NOT_OPEN when the document is not open,
            since only an open document can hold unsaved edits.
        """
        bridge = get_bridge()
        params = {"file_path": file_path} if file_path else {}
        return await bridge.send_command_async(
            "application.save_document", params, timeout=60.0)

    @mcp.tool()
    async def app_exit_active_command() -> dict[str, Any]:
        """Close a transaction a crashed handler left open in Altium.

        THE TOOL FOR "A command is currently active and save cannot be
        completed at this time", when every save is refused. The state
        lives in Altium, so it survives restarting the polling loop and
        Escape does not clear it; it is left by a handler that died
        mid-transaction. Harmless when nothing is open. It saves nothing:
        run ``app_save_all`` afterwards and check ``still_dirty``.

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
    async def app_close_document(file_path: str,
                                 discard_changes: bool = False) -> dict[str, Any]:
        """Close ONE open document, found by its full path, whatever has focus.

        Unlike closing through the workspace manager, which acts on the
        FOCUSED object, this closes the named document itself. It refuses a
        document that reads modified, and says whether it is really gone
        afterwards. The modified read can miss edits held in the editor, so
        save anything you care about first; a close of an edited document
        may still raise Altium's save prompt.

        Args:
            file_path: the document's full path, as app_list_documents
                reports it.
            discard_changes: close it even when it reads modified, and
                LOSE the unsaved edits: the modified flag is cleared first
                so no save prompt appears. For scratch copies and documents
                Altium changed by merely opening them, never for work. A
                schematic an open project lists may keep the edit in the
                project after its tab closes: ``discarded`` is then false
                and ``project_may_hold_edit`` true; proj_close with
                save=False drops it.

        Returns:
            ``{"closed": bool, "discarded": bool, "project_may_hold_edit":
            bool, "file_path": ...}``; an
            error when the path is not open, or when the document reads
            modified and ``discard_changes`` is not set.
        """
        params = {"file_path": file_path}
        if discard_changes:
            params["discard"] = "true"
        return await get_bridge().send_command_async(
            "application.close_document", params, timeout=120.0)

    async def _other_open_projects(besides: str) -> list[str]:
        """Projects open in Altium other than ``besides``; empty when the
        list cannot be read, since it only informs the reply."""
        import os
        try:
            reply = await get_bridge().send_command_async("project.get_open_projects", {})
        except Exception:  # noqa: BLE001
            return []
        rows = reply.get("projects", []) if isinstance(reply, dict) else []
        kinds = (".prjpcb", ".prjscr", ".libpkg", ".prjmbd")
        return [path for path in (str(r.get("project_path", "")) for r in rows)
                if path.lower().endswith(kinds)
                and os.path.normcase(path) != os.path.normcase(besides)]

    @mcp.tool()
    async def app_checkpoint(label: str = "", save_first: bool = True,
                             project_path: str = "") -> dict[str, Any]:
        """Snapshot a project so the session can be rolled back.

        Copies the project's design files into a content-addressed store under
        the workspace (unchanged files are deduplicated, so repeat checkpoints
        are cheap). Take one before any risky autonomous edit; restore with
        `app_restore_checkpoint`. This is the undo the live bridge otherwise
        lacks.

        Args:
            label: optional human note ("before routing pass").
            save_first: flush dirty Altium docs to disk before snapshotting
                so the checkpoint reflects in-editor changes (default True).
            project_path: the .PrjPcb to snapshot. Left empty, the focused
                project is taken; with several open that may not be the one
                you are editing, so the reply names it and lists the rest.

        Returns:
            The checkpoint manifest summary (id, created, file_count, ...),
            ``project`` (the one snapshotted) and ``other_open_projects``.
            With ``save_first``, also ``saved``: True when the flush
            worked, False with ``save_error`` and a ``note`` when it did
            not. A checkpoint is still taken either way, but one taken
            after a failed save holds the on-disk state only, which is
            missing precisely the in-editor work you were protecting.
        """
        import os
        saved: Optional[bool] = None
        save_error = ""
        if not project_path:
            project_dir, project_file, err = await _resolve_project_dir()
            if err:
                return err
        elif os.path.isfile(project_path):
            project_dir, project_file = os.path.split(project_path)
        else:
            return {"error": f"no project file at {project_path}", "checkpoint": None}
        snapshot_of = os.path.join(project_dir or "", project_file or "")
        others = await _other_open_projects(snapshot_of)
        if save_first:
            # THE PROJECT BEING CHECKPOINTED, not the workspace. save_all
            # wrote every open document, other projects' included, to
            # snapshot one (issue #94 reports unrelated sheets rewritten).
            # Both this and the path lookup above take the focused project.
            try:
                await get_bridge().send_command_async(
                    "project.save",
                    {"project_path": project_path} if project_path else {},
                    timeout=60.0,
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
        from pathlib import Path
        store = _checkpoint_store()
        info = store.create(Path(project_dir), project_file=project_file or "", label=label)
        result: dict[str, Any] = {"checkpoint": info.summary(), "project": snapshot_of,
                                  "other_open_projects": others}
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
        from ..saving import refused, saving_off
        if saving_off():
            return refused("A restore overwrites the project's files.")
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

        While the loop runs it holds Altium's scripting engine, so some of
        Altium's own script-backed commands may not respond. This saves
        edited and modified documents, leaves clean ones alone, and stops
        the loop. Afterwards the user must run StartMCPServer again (File
        > Run Script) before any more tools work, so detach only when done.

        Returns:
            attached False always; stopped True only when Altium answered
            that its loop stopped, with edited_saved and clean_left_alone
            counts. Otherwise stopped False and the reason: the loop may
            still be running.
        """
        bridge = get_bridge()
        reply: Any = None
        reason = ""
        try:
            reply = await bridge.send_command_async("application.stop_server",
                                                    timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
        bridge.detach()
        if isinstance(reply, dict) and reply.get("stopped") is True:
            out = {"attached": False, "stopped": True,
                   "message": "Detached from Altium Designer and stopped MCP server"}
            for key in ("edited_saved", "clean_left_alone",
                        "saving_off", "not_saved", "saving_note"):
                if key in reply:
                    out[key] = reply[key]
            return out
        if not reason:
            reason = f"Altium did not confirm the stop (reply: {reply!r})"
        return {
            "attached": False,
            "stopped": False,
            "reason": reason,
            "message": ("Detached on this side, but Altium did not confirm its "
                        "polling loop stopped. If it was already stopped this is "
                        "harmless; otherwise app_ping shows it still answering."),
        }

    @mcp.tool()
    @_reports_update
    async def app_ping() -> dict[str, Any]:
        """Check that Altium and its bridge script answer, and that the
        running script is the version on disk. A mismatch or a non-empty
        ``stale_units`` means Altium runs an old compile: close and reopen
        the script project to recompile.

        Returns:
            ``success``, ``mcp_server_version``, ``altium_script_version``,
            ``bundled_script_version`` and ``bundled_script_path``,
            ``version_match``, ``altium_version``, ``message``,
            ``script_projects_open`` and ``script_location_match`` (False:
            Altium loaded scripts from elsewhere), ``loop_workspace_dir``
            (the folder the answering loop takes requests from) and
            ``server_workspace_dir`` (this server's), ``unit_stamps``,
            ``stale_units``, ``unstamped_units``, ``saving_allowed`` (False
            while the dashboard's Saving switch is off), ``other_servers``
            (other eda-agent servers on this workspace) and ``warnings``.
        """
        from ..saving import saving_off
        bridge = get_bridge()
        script_path = _bundled_script_path()
        servers, duplicate = _other_servers()
        warnings: list[str] = [duplicate] if duplicate else []
        if not bridge.is_altium_running():
            return {
                "success": False,
                "mcp_server_version": _mcp_server_version,
                "altium_script_version": None,
                "bundled_script_version": _bundled_script_version(),
                "bundled_script_path": script_path,
                "version_match": False,
                "altium_version": "",
                "other_servers": servers,
                "warnings": warnings,
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
                "bundled_script_path": script_path,
                "version_match": False,
                "altium_version": "",
                "other_servers": servers,
                "warnings": warnings,
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
                f"but the on-disk bundle is {bundled}. To recompile, "
                f"{RELOAD_STEP}."
            )

        loaded = result.get("script_projects")
        location_match, location_warnings = _script_location(loaded)
        warnings = location_warnings + warnings
        if location_match is False:
            msg = f"{msg} {location_warnings[0]}"
        stamp_fields, stamp_warnings = _unit_stamp_check(bridge, loaded)
        warnings = stamp_warnings + warnings
        if stamp_warnings:
            match = False
            msg = f"{msg} {stamp_warnings[0]}"

        return _with_session_reminder({
            "success": True,
            "mcp_server_version": _mcp_server_version,
            "altium_script_version": altium_ver,
            "bundled_script_version": bundled,
            "bundled_script_path": script_path,
            "version_match": match,
            "altium_version": result.get("altium_version") or "",
            "script_projects_open": loaded if isinstance(loaded, list) else None,
            "script_location_match": location_match,
            # Whose loop answered: the folder it polls, as the script
            # reports it. Empty from a script older than 2026.10.21.
            "loop_workspace_dir": result.get("workspace_dir") or "",
            "server_workspace_dir": _server_workspace_dir(),
            # The dashboard's Saving switch: False means every save is refused.
            "saving_allowed": not saving_off(),
            **stamp_fields,
            "other_servers": servers,
            "warnings": warnings,
            "message": msg,
        })

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
        project_path: str = "",
    ) -> dict[str, Any]:
        """Create a new blank document of a given kind and save it to disk.

        Saved to `file_path` and, by default, added to the focused
        project, or to ``project_path`` when given. With several projects
        open, name the project: the focused one is whichever was clicked
        last, and a scratch sheet added to a production project stays in
        it. ``member_of`` reads back which projects now list the file.

        Args:
            kind: Document kind, 'PCB', 'SCH', 'PCBLIB', 'SCHLIB',
                'OUTPUTJOB', or any other kind Altium's server module
                registers under.
            file_path: Absolute path where the new document should live.
                Use Windows backslashes.
            name: Optional display name. Defaults to the filename.
            add_to_project: Attach the new file to a project. Default
                True. Set False to leave it as a free document.
            project_path: The .PrjPcb to attach it to. An unknown path is
                refused before anything is created.

        The new document is focused, and ``focused`` says whether that
        took, since the next tool acts on the focused one. A save that
        fails is an error naming the cause.

        A path that is already OPEN is refused (``ALREADY_OPEN``): creating
        over an open document crashed Altium's PCB editor. Focus it with
        app_set_active_document instead. A file that exists on disk but is
        not open is replaced by the blank document.

        Returns:
            Dictionary with kind, file_path, saved, focused,
            added_to_project and member_of (project paths).
        """
        # A missing folder made Altium raise a modal save error that holds
        # the bridge until someone answers it; refused here first.
        folder = os.path.dirname(file_path)
        if os.path.isabs(file_path) and folder and not os.path.isdir(folder):
            return {"success": False, "error": "NOT_SAVED",
                    "reason": f"the folder does not exist: {folder}"}
        bridge = get_bridge()
        params: dict[str, Any] = {
            "kind": kind,
            "file_path": file_path,
            "add_to_project": "true" if add_to_project else "false",
        }
        if name:
            params["name"] = name
        if project_path:
            params["project_path"] = project_path
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
            - modified: True if the editor holds unsaved edits. A false
              is a floor rather than a clean bill, since the flag does
              not always propagate from ProcessControl. app_save_all
              measures file timestamps instead of trusting it.
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
    async def app_get_active_document(session: bool = False) -> dict[str, Any]:
        """Get information about the currently active (focused) document.

        Several Claude sessions can share one Altium. Each command runs on
        its own session's document (the last one it focused), which Altium
        puts back in front first if anything else moved the focus. So by
        default this answers what is REALLY in front, which may be another
        session's document or one a person has just opened. To work on that
        document, focus it (app_set_active_document); it then becomes this
        session's.

        Args:
            session: True for this session's own document instead, brought
                back to the front first if something else is there.

        Returns:
            Dictionary with active document information:
            - file_name: Document file name
            - file_path: Full file path
            - document_kind: Type of document (SchDoc, PcbDoc, etc.)
            - modified: True if the editor holds unsaved edits. A false
              is a floor rather than a clean bill, since the flag does
              not always propagate from ProcessControl.
            Returns empty dict if no document is active.
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "application.get_active_document",
            {} if session else {"front": "true"})
        return result

    @mcp.tool()
    async def app_set_active_document(file_path: str) -> dict[str, Any]:
        """Set a specific document as the active (focused) document,
        opening it first when it is a file Altium has not loaded yet (a
        member of a project just opened, say).

        Args:
            file_path: Full path to the document to activate

        Returns:
            Dictionary with result of the operation; ``opened`` true when
            the document had to be opened first.
        """
        from ..bridge.exceptions import AltiumError

        bridge = get_bridge()
        opened = False
        try:
            result = await bridge.send_command_async(
                "application.set_active_document", {"file_path": file_path}
            )
        except AltiumError as exc:
            # Measured: a demo run opened its project and then could not
            # bring the board forward because no editor had loaded it.
            if "not loaded" not in str(exc).lower() or not os.path.isfile(file_path):
                raise
            await bridge.send_command_async("application.run_process", {
                "process_name": "WorkspaceManager:OpenObject",
                "parameters": f"ObjectKind=Document|FileName={file_path}"})
            opened = True
            result = await bridge.send_command_async(
                "application.set_active_document", {"file_path": file_path}
            )
        if opened:
            result = {"opened": True, **(result if isinstance(result, dict) else {})}
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
    async def app_autosave(
        enabled: Optional[bool] = None,
        interval_minutes: Optional[int] = None,
    ) -> dict[str, Any]:
        """Read, or set, Altium's auto-save: whether it saves backup
        copies of open documents, and how often.

        Call with nothing to read. Both values are read back.

        Args:
            enabled: Auto-save on (True) or off (False).
            interval_minutes: Minutes between saves, 1 or more.

        Returns:
            ``enabled``, ``interval_minutes`` and ``changed``.
        """
        params: dict[str, Any] = {}
        if enabled is not None:
            params["enabled"] = "true" if enabled else "false"
        if interval_minutes is not None:
            if interval_minutes < 1:
                return {"ok": False, "reason": "interval_minutes is 1 or more"}
            params["interval"] = str(interval_minutes)
        return await get_bridge().send_command_async("application.autosave", params)

    @mcp.tool()
    async def app_run_menu(menu_path: str) -> dict[str, Any]:
        """Dispatch a menu command by path. DISPATCH IS NOT EXECUTION:
        Altium accepts an unknown or unavailable process in silence, so
        the reply says ``dispatched`` with ``outcome_verified: false``.
        Use ``app_click_menu`` when the command must actually run; it
        clicks the real menu and fails on a path that does not exist.

        Any path in the focused editor's menu bar works, in English or
        in any language Altium ships: it is looked up in Altium's own
        menu definitions (``app_find_menu_command`` searches them) and
        the command behind it is run, with no menu walk and no focus
        taken. The reply names the ``command`` and ``params`` used. A
        path the install does not declare is refused with
        UNMAPPED_MENU_PATH and the nearest matches.

        Args:
            menu_path: Menu path using pipe separators (e.g., "File|Save All")

        Returns:
            Dictionary with dispatched, outcome_verified (always false),
            menu_path, and the process used. A reply the bridge could not
            parse is reported as a failure, not smoothed into a success.
        """
        bridge = get_bridge()
        try:
            result = await bridge.send_command_async(
                "application.execute_menu", {"menu_path": menu_path}
            )
        except AltiumCommandError as exc:
            if getattr(exc, "code", "") != "UNMAPPED_MENU_PATH":
                raise
            return await _run_declared_menu(bridge, menu_path)
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
                tmp = path.with_suffix(".txt.tmp")
                tmp.write_text(text, encoding="utf-8")
                replace_with_retry(tmp, path)
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
