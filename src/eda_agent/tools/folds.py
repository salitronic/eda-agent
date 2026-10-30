# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Families of tools advertised as one tool each.

A family is a set of tools that differ by a mode: the calculators, the
audits, the dialog and input drivers, the menu runners, the dimension and
body kinds, the checkpoint actions. Each family is advertised as ONE tool
that takes ``kind`` and that kind's ``args``, and dispatches to the
member, which is unchanged. Every member keeps its name and stays
reachable through ``tool_invoke``, and ``tool_catalog`` still returns its
own description and schema, so nothing a member did is lost.

``HIDDEN`` lists single-item tools whose bulk twin takes the same fields
per item, and ``FOLDED_BATCH`` batch tools whose single tool takes their
list as ``items``. Neither is advertised; both stay callable by name.

:class:`FoldingServer` sits between the registrars and the server: it
captures the members and hidden tools as they register, passes everything
else through, and :meth:`FoldingServer.finish` registers the family tools.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .registry import ToolSpec

__all__ = ["FOLDS", "HIDDEN", "FOLDED_BATCH", "Fold", "FoldingServer", "built_members",
           "call_form", "reach_of"]


@dataclass(frozen=True)
class Fold:
    """One advertised family tool."""

    summary: str
    #: Members named outright, kind -> tool name.
    members: dict = field(default_factory=dict)
    #: Members found by prefix; the kind is the name without it.
    prefixes: tuple = ()
    #: The name of the dispatch argument.
    key: str = "kind"


FOLDS: dict[str, Fold] = {
    "pcb_calc": Fold(
        summary=("Engineering calculators: impedance, widths for current or "
                 "impedance, terminations, length matching, thermal vias, "
                 "reactance, resonance, wavelength, PDN target, wire gauge, "
                 "fusing current, via and spiral inductance, padstacks. "
                 "Offline, except polygon_area, which reads the open board."),
        prefixes=("pcb_calc_",),
    ),
    "audit": Fold(
        summary=("Design audits on the open board or project. Each kind "
                 "reports what it examined and every finding."),
        prefixes=("audit_find_", "audit_"),
    ),
    "app_ui": Fold(
        summary=("Drive Altium's interface directly: dialog controls, "
                 "lists, grids, tabs, trees, keystrokes, typing, canvas "
                 "clicks, context menus and toolbars. A LAST RESORT: use an "
                 "API tool when one exists (ask tool_guide first)."),
        key="action",
        members={
            "activate_row": "app_activate_row",
            "dialog_tab": "app_dialog_tab",
            "expand_tree_node": "app_expand_tree_node",
            "invoke_element": "app_invoke_element",
            "scroll_control": "app_scroll_control",
            "set_dialog_control": "app_set_dialog_control",
            "set_dropdown": "app_set_dropdown",
            "set_grid_cell": "app_set_grid_cell",
            "wait_for_dialog": "app_wait_for_dialog",
            "read_window": "app_read_window",
            "type_text": "app_type_text",
            "send_keys": "app_send_keys",
            "canvas": "app_canvas",
            "context_menu": "app_context_menu",
            "toolbar": "app_toolbar",
        },
    ),
    "app_menu": Fold(
        summary=("Run an Altium menu command. find: search the menus by "
                 "text. run: dispatch the command a menu path maps to. "
                 "click: click the real menu. drive: click it and answer "
                 "the dialogs it raises."),
        key="how",
        members={
            "find": "app_find_menu_command",
            "run": "app_run_menu",
            "click": "app_click_menu",
            "drive": "app_run_ui_command",
        },
    ),
    "pcb_dimension": Fold(
        summary="Place a dimension on the open board.",
        members={
            "linear": "pcb_place_dimension",
            "angular": "pcb_place_angular_dimension",
            "radial": "pcb_place_radial_dimension",
            "baseline": "pcb_place_baseline_dimension",
        },
    ),
    "pcb_body": Fold(
        summary=("Place a free 3D body on the open board: a STEP model, an "
                 "extruded outline, or a cylinder or sphere."),
        members={
            "step": "pcb_place_3d_body",
            "extruded": "pcb_place_extruded_body",
            "simple": "pcb_place_simple_body",
        },
    ),
    "app_checkpoints": Fold(
        summary=("Snapshots of the project's files: make one before a risky "
                 "change, list them, restore one."),
        key="action",
        members={
            "make": "app_checkpoint",
            "list": "app_list_checkpoints",
            "restore": "app_restore_checkpoint",
        },
    ),
}

#: Single-item tools, by the bulk tool that takes the same fields per item.
HIDDEN: dict[str, str] = {
    "pcb_place_via": "pcb_place_vias",
    "lib_add_footprint_pad": "lib_add_footprint_pads",
    "lib_add_footprint_track": "lib_add_footprint_tracks",
    "lib_add_footprint_via": "lib_add_footprint_vias",
    "proj_get_component_info": "proj_get_component_info_many",
    "proj_get_connectivity": "proj_get_connectivity_many",
}

#: Batch tools whose single tool now takes their list as ``items`` and
#: runs it through the same handler, by that single tool.
FOLDED_BATCH: dict[str, str] = {
    "obj_batch_modify": "obj_modify",
    "obj_batch_create": "obj_create",
    "obj_batch_delete": "obj_delete",
    "lib_batch_rename": "lib_rename_component",
}

#: Members of every family tool built so far, fold name -> {kind: tool}.
_BUILT: dict[str, dict[str, str]] = {}


def built_members(fold_name: str) -> dict[str, str]:
    """The members a family tool was built with, kind -> tool name."""
    return dict(_BUILT.get(fold_name, {}))


def _fold_of(name: str) -> Optional[tuple[str, str]]:
    """(fold name, kind) for a member tool, or None."""
    for fold_name, fold in FOLDS.items():
        for kind, member in fold.members.items():
            if member == name:
                return fold_name, kind
        for prefix in fold.prefixes:
            if name.startswith(prefix):
                return fold_name, name[len(prefix):]
    return None


def reach_of(name: str) -> Optional[str]:
    """How an unadvertised tool is reached, or None for an advertised one."""
    found = _fold_of(name)
    if found:
        fold_name, kind = found
        return f"{fold_name}({FOLDS[fold_name].key}='{kind}', args={{...}})"
    if name in HIDDEN:
        return f"{HIDDEN[name]} with a list of one, or tool_invoke('{name}')"
    if name in FOLDED_BATCH:
        return f"{FOLDED_BATCH[name]}(items=[...]), or tool_invoke('{name}')"
    return None


def call_form(name: str) -> str:
    """How to name a tool to an agent: the advertised tool that runs it.

    A family member reads ``audit kind=acute_angles``; a single-item tool
    reads as its bulk twin; a folded batch tool reads as its single tool;
    any other name is returned unchanged.
    """
    found = _fold_of(name)
    if found:
        fold_name, kind = found
        return f"{fold_name} {FOLDS[fold_name].key}={kind}"
    return HIDDEN.get(name) or FOLDED_BATCH.get(name, name)


def _signature_line(kind: str, fn: Callable) -> str:
    """``kind(a, b=1, ...)``: the arguments a kind takes, defaults short."""
    parts = []
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        params = []
    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.default is inspect.Parameter.empty:
            parts.append(p.name)
        else:
            shown = repr(p.default)
            parts.append(f"{p.name}={shown if len(shown) <= 14 else '...'}")
    return f"{kind}({', '.join(parts)})"


def _first_line(fn: Callable) -> str:
    doc = (inspect.getdoc(fn) or "").strip()
    line = doc.split("\n\n", 1)[0].replace("\n", " ")
    return line if len(line) <= 140 else line[:137].rstrip() + "..."


def _describe(fold_name: str, fold: Fold, members: dict[str, ToolSpec]) -> str:
    lines = [fold.summary, "",
             f"{fold.key}: one of the names below. args: that {fold.key}'s "
             f"arguments as a dict. Each is also the tool named in "
             f"brackets; tool_catalog(query='<that name>', with_schema=True) "
             f"gives its full description and schema.", ""]
    for kind in sorted(members):
        spec = members[kind]
        lines.append(f"- {_signature_line(kind, spec.fn)} [{spec.name}]: "
                     f"{_first_line(spec.fn)}")
    return "\n".join(lines)


async def _call_validated(fn: Callable, args: dict) -> Any:
    """Call a member as the server would: arguments checked and coerced
    against its own signature first ("5" becomes 5.0 for a float)."""
    try:
        from mcp.server.fastmcp.utilities.func_metadata import func_metadata
    except ImportError:                      # pragma: no cover - older mcp
        result = fn(**args)
        return await result if inspect.isawaitable(result) else result
    try:
        meta = func_metadata(fn)
    except Exception:                        # noqa: BLE001 - unusual signature
        result = fn(**args)
        return await result if inspect.isawaitable(result) else result
    from pydantic import ValidationError
    try:
        model = meta.arg_model.model_validate(args)
    except ValidationError as e:
        return {"ok": False, "reason": f"bad arguments: {e}"}
    result = fn(**model.model_dump_one_level())
    return await result if inspect.isawaitable(result) else result


def _make_dispatcher(fold_name: str, fold: Fold,
                     members: dict[str, ToolSpec]) -> Callable:
    key = fold.key

    async def dispatch(**call: Any) -> dict[str, Any]:
        kind = str(call.get(key) or "").strip()
        args = call.get("args") or {}
        spec = members.get(kind)
        if spec is None:
            return {"ok": False,
                    "reason": f"unknown {key} {kind!r} for {fold_name}",
                    "known": sorted(members)}
        if not isinstance(args, dict):
            return {"ok": False, "reason": "args must be a dict of arguments"}
        try:
            inspect.signature(spec.fn).bind(**args)
        except TypeError as e:
            return {"ok": False, "reason": f"{kind}: {e}",
                    "takes": _signature_line(kind, spec.fn)}
        return await _call_validated(spec.fn, args)

    # The advertised signature: the key, then the args dict.
    dispatch.__signature__ = inspect.Signature([
        inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY, annotation=str),
        inspect.Parameter("args", inspect.Parameter.KEYWORD_ONLY,
                          annotation=Optional[dict], default=None),
    ], return_annotation=dict[str, Any])
    dispatch.__annotations__ = {key: str, "args": Optional[dict],
                                "return": dict[str, Any]}
    dispatch.__name__ = fold_name
    dispatch.__qualname__ = fold_name
    dispatch.__doc__ = _describe(fold_name, fold, members)
    return dispatch


class FoldingServer:
    """Stands in for the server while the registrars run."""

    def __init__(self, server: Any) -> None:
        self._server = server
        #: Every member and hidden tool, by its own name.
        self.hidden: dict[str, ToolSpec] = {}
        self._finished = False

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn: Callable) -> Callable:
            name = kwargs.get("name") or fn.__name__
            if name in HIDDEN or name in FOLDED_BATCH or _fold_of(name):
                self.hidden[name] = ToolSpec(
                    name=name, description=inspect.getdoc(fn) or "", fn=fn)
                return fn
            # Every advertised tool takes one item or a list of them; see
            # bulk.py. Family members are reached through their family.
            from .bulk import with_items
            return self._server.tool(*args, **kwargs)(with_items(fn, name))
        return deco

    def finish(self) -> None:
        """Register one tool per family that captured any member."""
        if self._finished:
            return
        self._finished = True
        # The server object keeps the unadvertised tools too, so whatever
        # holds it can still find and describe them.
        try:
            merged = dict(getattr(self._server, "hidden", {}) or {})
            merged.update(self.hidden)
            self._server.hidden = merged
        except AttributeError:
            pass
        for fold_name, fold in FOLDS.items():
            members: dict[str, ToolSpec] = {}
            for name, spec in self.hidden.items():
                found = _fold_of(name)
                if found and found[0] == fold_name:
                    members[found[1]] = spec
            if not members:
                continue
            _BUILT[fold_name] = {k: s.name for k, s in members.items()}
            # A family tool takes a list too: one dict of kind and args
            # per item, kinds mixed freely.
            from .bulk import with_items
            self._server.tool()(with_items(
                _make_dispatcher(fold_name, fold, members), fold_name))

    async def call_tool(self, name: str, arguments: Optional[dict] = None):
        spec = self.hidden.get(name)
        if spec is None:
            return await self._server.call_tool(name, arguments or {})
        result = spec.fn(**(arguments or {}))
        if inspect.isawaitable(result):
            result = await result
        return result

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._server, attr)
