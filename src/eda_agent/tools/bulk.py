# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every tool takes one item or many.

Each tool gets an ``items`` argument at registration: a list of argument
dicts, one per item. Given, the tool's own code runs once per item, all at
once, and their requests to Altium are gathered into bulk requests
(bridge/bulk.py), which Altium runs inside one edit, so the whole call is
one undo step. Without ``items`` the tool runs exactly as before.

Arguments given beside ``items`` apply to every item; an item's own value
wins. Each item is checked against the tool's schema like a single call.
The reply lists every item's own result, in order, with the failures.
"""

from __future__ import annotations

import functools
import inspect
import typing
from typing import Annotated, Any, Callable, Optional

from pydantic import Field

#: Tools that do one whole-design thing per call, or drive the interface,
#: or describe the tool surface: a list of them is never what is meant.
NO_BULK_PREFIXES = ("app_", "tool_", "server_", "design_session_",
                    "design_job_", "altium365_")
NO_BULK = frozenset({
    "run_drc", "run_erc", "pcb_run_drc", "pcb_smart_drc", "proj_run_erc",
    "proj_run_outjob", "proj_run_outjob_all", "proj_run_output",
    "proj_generate_fab_package", "proj_open", "proj_close", "proj_save",
    "proj_create", "proj_compile", "proj_force_recompile", "proj_sync_pcb",
    "proj_sync_schematic", "proj_annotate", "pcb_autoroute",
    "pcb_autoroute_apply", "pcb_autoplace", "pcb_autoplace_apply",
    "pcb_repour_polygons", "pcb_save_copy_as", "pcb_build_from_project",
    "sim_run", "sim_run_deck", "review_design",
})

#: Tools that take their list as their own argument and run it as one
#: batch in Altium already; a second list on top would only be a second
#: spelling of the same call.
LIST_NATIVE = frozenset({
    "pcb_place_vias", "pcb_place_tracks", "lib_add_footprint_pads",
    "lib_add_footprint_tracks", "lib_add_footprint_vias",
    "proj_get_component_info_many", "proj_get_connectivity_many",
    "lib_batch_set_params",
})

#: Items run one after another, not together, for backends whose transport
#: takes one request at a time and gathers nothing.
SEQUENTIAL_PREFIXES = ("easyeda_", "kicad_")

ITEMS_DESCRIPTION = (
    "Several at once: a list of this tool's arguments, one dict per item. "
    "Altium runs them as one edit and one undo step. Other arguments given "
    "apply to every item.")


def takes_items(name: str, fn: Callable) -> bool:
    """Whether ``with_items`` gives this tool an ``items`` argument."""
    if name in NO_BULK or name in LIST_NATIVE or name.startswith(NO_BULK_PREFIXES):
        return False
    if not inspect.iscoroutinefunction(fn):
        return False
    params = inspect.signature(fn).parameters
    if "items" in params:
        return False
    return not any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
                   for p in params.values())


def _resolved_signature(fn: Callable) -> inspect.Signature:
    """The tool's signature with its annotations evaluated, so the server
    reads the same types through the wrapper as from the tool itself."""
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception:                                  # noqa: BLE001
        hints = {}
    params = [p.replace(annotation=hints.get(p.name, p.annotation))
              for p in sig.parameters.values()]
    return sig.replace(parameters=params,
                       return_annotation=hints.get("return",
                                                   sig.return_annotation))


def with_items(fn: Callable, name: str) -> Callable:
    """``fn`` with an ``items`` argument, or ``fn`` itself when it should
    not take one (see ``takes_items``)."""
    if not takes_items(name, fn):
        return fn
    sig = _resolved_signature(fn)
    defaults = {p.name: p.default for p in sig.parameters.values()
                if p.default is not p.empty}
    # A required argument cannot stay required in the schema, or a call
    # with items would have to give it too. It is checked here instead.
    required = [p.name for p in sig.parameters.values() if p.default is p.empty]
    advertised = [p if p.default is not p.empty else
                  p.replace(default=None,
                            annotation=Optional[p.annotation]
                            if p.annotation is not p.empty else Any)
                  for p in sig.parameters.values()]
    # After keyword-only arguments (a family tool's) it must be one too.
    kind = (inspect.Parameter.KEYWORD_ONLY
            if any(p.kind is p.KEYWORD_ONLY for p in advertised)
            else inspect.Parameter.POSITIONAL_OR_KEYWORD)
    items_param = inspect.Parameter(
        "items", kind, default=None,
        annotation=Annotated[Optional[list[dict[str, Any]]],
                             Field(description=ITEMS_DESCRIPTION)])

    @functools.wraps(fn)
    async def wrapper(*args: Any, items: Optional[list] = None,
                      **kwargs: Any) -> Any:
        if items is None:
            missing = [n for n in required[len(args):]
                       if kwargs.get(n) is None]
            if missing:
                # Raised, as the server's own check raised before the
                # argument became optional.
                raise ValueError(
                    f"{name} needs {', '.join(missing)}; give "
                    f"{'it' if len(missing) == 1 else 'them'}, or give items")
            result = await fn(*args, **kwargs)
            # A loop of single calls is told about items once it repeats.
            # A tool with its own entry in the tracker counts itself.
            from .bulk_hints import BulkHintTracker
            if name not in BulkHintTracker.BULK_EQUIVALENTS:
                hint = BulkHintTracker.record_and_hint(name, takes_items=True)
                if hint and isinstance(result, dict):
                    result["_hint_bulk"] = hint
            return result
        return await run_bulk(fn, name, items,
                              {k: v for k, v in kwargs.items()
                               if not (k in required and v is None)},
                              defaults)

    wrapper.__signature__ = sig.replace(parameters=[*advertised, items_param])
    # inspect.signature follows __wrapped__ and would hide ``items``.
    del wrapper.__wrapped__
    # The tool as written, for anything that reads its real defaults.
    wrapper.single = fn
    return wrapper


def single(fn: Callable) -> Callable:
    """The tool as written, without the ``items`` wrapper."""
    return getattr(fn, "single", fn)


async def run_bulk(fn: Callable, name: str, items: list, shared: dict,
                   defaults: dict) -> dict:
    """Run ``fn`` once per item and say how each went."""
    if not isinstance(items, list) or not items:
        return {"ok": False, "reason": "items must be a non-empty list of "
                                       "argument dicts"}
    common = {k: v for k, v in shared.items()
              if k not in defaults or v != defaults[k]}
    calls = []
    for item in items:
        if not isinstance(item, dict):
            calls.append(_refusal("each item must be a dict of arguments"))
            continue
        calls.append(functools.partial(_call_item, fn, {**common, **item}))

    if name.startswith(SEQUENTIAL_PREFIXES):
        results = []
        for call in calls:
            try:
                results.append(await call())
            except Exception as exc:                   # noqa: BLE001
                results.append(exc)
        requests = None
    else:
        from ..bridge import bulk, get_bridge
        results, requests = await bulk.run_items(get_bridge(), calls)

    out = [_item_result(r) for r in results]
    failed = sum(1 for r in out if _failed(r))
    reply = {"bulk": True, "count": len(items),
             "succeeded": len(items) - failed, "failed": failed,
             "items": out}
    if requests is not None:
        reply["altium_requests"] = requests
    return reply


async def _call_item(fn: Callable, args: dict) -> Any:
    from .folds import _call_validated
    return await _call_validated(fn, args)


def _refusal(reason: str):
    async def refuse():
        return {"ok": False, "reason": reason}
    return refuse


def _item_result(result: Any) -> Any:
    if isinstance(result, BaseException):
        out = {"ok": False, "error": str(result) or type(result).__name__}
        code = getattr(result, "code", None)
        if code:
            out["code"] = code
        return out
    return result


def _failed(result: Any) -> bool:
    return isinstance(result, dict) and (
        result.get("ok") is False or result.get("success") is False)
