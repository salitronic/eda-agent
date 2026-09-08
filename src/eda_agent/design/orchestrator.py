# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Top-level orchestrator: plan JSON -> canvas -> Altium emit.

Wraps the three pure-Python stages (symbol extraction, pipeline, emit)
into one entry point the MCP tool layer can call. Returns a result dict
shaped compatibly with the legacy ``ExecutorResult.to_dict()`` so the
``design_execute_plan`` tool can flip between the two paths without
changing its return shape.

What this module is NOT: a placement algorithm. Layout decisions live
in ``pipeline.py``; this is just glue that wires bridge + extractor +
pipeline + emitter together.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import ValidationError

from eda_agent.design.emitter import (
    EmitFailure, EmitResult, emit_canvas, emit_canvas_delta)
from eda_agent.design.pipeline import (
    PipelineResult,
    build_best_canvas_from_plan,
    build_canvas_from_plan,
)
from eda_agent.design.plan import (
    _NET_PATTERN, _REFDES_PATTERN, DesignPlan, PartStatus)
from eda_agent.design.plan_erc import check_plan_erc
from eda_agent.design.render_svg import render_canvas_svg
from eda_agent.design.symbols import SymbolCache, SymbolExtractor

logger = logging.getLogger("eda_agent.design.orchestrator")


def _default_cache_dir() -> Path:
    """Where to keep extracted SymbolModel JSON between runs.

    Lives under the repo (or installed package) so a fresh checkout
    re-extracts; this is the natural place for derived caches that don't
    belong in user data.
    """
    # Repo root is two levels up from this file: src/eda_agent/design/.
    return Path(__file__).resolve().parents[3] / ".symbol_cache"


def execute_plan_via_canvas_from_json(
    plan_json: Union[str, dict],
    project_path: str,
    *,
    bridge: Any = None,
    cache_dir: Optional[Path] = None,
    write_preview_svg: bool = False,
    placement_hints: Optional[dict[str, dict[str, int]]] = None,
    mode: str = "auto",
) -> dict[str, Any]:
    """Parse + validate + run the canvas-based execute pipeline.

    Args:
        plan_json: A DesignPlan as JSON string or dict.
        project_path: Where the resulting .PrjPcb lands.
        bridge: AltiumBridge for symbol extraction + emission. Defaults
            to the global one.
        cache_dir: Where to store the SymbolModel cache. Defaults to
            ``<repo>/.symbol_cache/``.
        write_preview_svg: If True, also dumps the canvas SVG to a
            sibling ``.preview.svg`` next to the project file. Cheap, and
            extremely useful for debugging layout issues without
            squinting at Altium.
        mode: ``"auto"`` (default) emits a DELTA when a prior
            ``<project>.canvas.json`` snapshot exists and the canvas is
            a single sheet, and a full transcription otherwise.
            ``"full"`` always re-places every component, which discards
            any hand edit on the sheet. ``"delta"`` demands a snapshot
            and refuses rather than silently redrawing the sheet.

    Returns:
        Dict with the legacy ExecutorResult shape plus a ``canvas`` key
        carrying the canvas's to_dict() snapshot. Includes:

        - ``ok``: True iff pipeline + emit both reported success.
        - ``project_path``, ``sheets_touched``.
        - ``placed``: list of {refdes, sheet, x_mils, y_mils, rotation}.
        - ``failures``: list of {refdes, code, reason}.
        - ``needs_creation``: refdes list (from pipeline notes).
        - ``notes``: free-text notes from pipeline + emitter.
        - ``nets_labelled``, ``power_ports_placed``: counts.
        - ``net_mismatches``: empty for now; canvas pipeline doesn't
          do post-emit netlist verification yet (the legacy executor
          did, via project.get_nets).
        - ``canvas``: ``canvas.to_dict()`` for caller-side inspection.
        - ``preview_svg_path``: where the SVG was written, when enabled.
    """
    out = _empty_result_dict(project_path)
    payload = _parse_plan(plan_json, out)
    if payload is None:
        return out
    plan = _validate_plan(payload, out)
    if plan is None:
        return out
    # Pydantic validation alone is not enough: it does not catch
    # cross-references (a net naming an unknown refdes, a part on a zone
    # that lives on another sheet) or connectivity faults (a pin on two
    # nets, contradictory power/ground flags, a floating net). The
    # standalone validation tool runs these; the execute path MUST enforce
    # them itself, or an electrically-broken plan reaches emit.
    if _enforce_plan_gates(plan, out):
        return out

    bridge = bridge or _resolve_bridge()
    if bridge is None:
        out["ok"] = False
        reason = f" ({_last_bridge_failure})" if _last_bridge_failure else ""
        out["notes"].append(
            "no Altium bridge available; symbol extraction needs Altium "
            f"to load each referenced SchLib.{reason}"
        )
        return out

    cache_dir = cache_dir or _default_cache_dir()
    cache = SymbolCache(cache_dir)
    extractor = SymbolExtractor(bridge, cache)

    # build_best_canvas_from_plan tries N placement variants (aspect-
    # rescaled from the base) and returns the lowest-scoring one. This
    # closes the iteration loop the SVG renderer was always meant to
    # serve: the pipeline no longer ships the first canvas it produces
    # if a cheaper compress-the-bbox variant scores better.
    pipeline_result = build_best_canvas_from_plan(
        plan, extractor, placement_hints=placement_hints,
    )
    _merge_pipeline_result(out, pipeline_result)
    if not pipeline_result.ok:
        return out

    if write_preview_svg:
        try:
            preview_path = Path(project_path).with_suffix(".preview.svg")
            preview_path.write_text(
                render_canvas_svg(pipeline_result.canvas), encoding="utf-8"
            )
            out["preview_svg_path"] = str(preview_path)
            out["notes"].append(f"preview SVG: {preview_path}")
        except Exception as exc:
            out["notes"].append(f"preview SVG write failed: {exc}")

    # The snapshot is what `design_learn_from_layout` diffs against and
    # what the next edit deltas against, so it must describe the sheet
    # as it actually is. Read now, write after the emit.
    snapshot_path = Path(project_path).with_suffix(".canvas.json")
    prior = _prior_canvas(snapshot_path)

    if mode not in ("auto", "full", "delta"):
        out["ok"] = False
        out["notes"].append(
            f"mode must be auto, full or delta; got {mode!r}")
        return out
    single_sheet = len(pipeline_result.canvas.sheets) <= 1
    use_delta = mode == "delta" or (
        mode == "auto" and prior is not None and single_sheet)
    if use_delta and prior is None:
        out["ok"] = False
        out["notes"].append(
            f"mode=delta needs the prior snapshot at {snapshot_path}, which "
            f"is missing or unreadable; run with mode=full to draw the sheet "
            f"from scratch"
        )
        return out
    if use_delta and not single_sheet:
        out["ok"] = False
        out["notes"].append(
            "mode=delta takes one sheet; this plan lays out "
            f"{len(pipeline_result.canvas.sheets)}"
        )
        return out

    if use_delta:
        emit_result = emit_canvas_delta(
            pipeline_result.canvas,
            prior,
            project_path,
            bridge,
            parameter_stamps=pipeline_result.parameter_stamps,
        )
        out["delta"] = emit_result.delta
        if emit_result.delta:
            out["notes"].append(
                "delta emit: "
                f"{len(emit_result.delta.get('added', []))} added, "
                f"{len(emit_result.delta.get('moved', []))} moved, "
                f"{len(emit_result.delta.get('removed', []))} removed, "
                f"{len(emit_result.delta.get('untouched', []))} untouched"
            )
    else:
        emit_result = emit_canvas(
            pipeline_result.canvas,
            project_path,
            bridge,
            parameter_stamps=pipeline_result.parameter_stamps,
        )
    _merge_emit_result(out, emit_result)
    _write_canvas_snapshot(snapshot_path, payload, pipeline_result,
                           emit_result, out)
    return out


def _write_canvas_snapshot(
    snapshot_path: Path,
    payload: dict,
    pipeline_result: Any,
    emit_result: EmitResult,
    out: dict[str, Any],
) -> None:
    """Record the sheet the emit actually produced, or leave the old one.

    WRITTEN AFTER THE EMIT, and only when the emit worked. Written
    before, it claims the new layout landed whether or not it did: the
    learner would then read a person's edits as deltas from a sheet that
    was never drawn, and the next edit would delta against a state the
    sheet never reached, so a part that failed to move would count as
    untouched from then on.

    On a failure the previous snapshot stays. It under-states what
    landed if the emit got part way, and the next run then re-issues
    moves to coordinates parts are already at, which is harmless. The
    other order loses work silently.
    """
    if not emit_result.ok or emit_result.failures:
        out["notes"].append(
            f"canvas snapshot at {snapshot_path} NOT updated: the emit "
            f"reported {len(emit_result.failures)} failure(s), so the "
            f"sheet does not match this layout")
        return
    try:
        snapshot_path.write_text(
            json.dumps({
                "plan": payload,
                "canvas": pipeline_result.canvas.to_dict(),
                "parameter_stamps": pipeline_result.parameter_stamps,
            }, indent=2),
            encoding="utf-8",
        )
        out["canvas_snapshot_path"] = str(snapshot_path)
    except Exception as exc:                    # noqa: BLE001
        out["notes"].append(f"canvas snapshot write failed: {exc}")


def _prior_canvas(snapshot_path: Path) -> Optional[dict[str, Any]]:
    """The ``canvas`` half of an existing snapshot, or None.

    Returns None for a missing OR unreadable snapshot: a delta against a
    half-parsed file would delete every part it failed to read.
    """
    if not snapshot_path.exists():
        return None
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    canvas = payload.get("canvas")
    if not isinstance(canvas, dict) or not canvas.get("instances"):
        return None
    return canvas


def hints_from_sheet(
    project_path: str,
    *,
    bridge: Any = None,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Current sheet positions as ``placement_hints`` for a re-run.

    This is what makes the layout engine usable for EDITING rather than
    only for drawing from scratch: hint every part that already exists at
    where it currently sits, change the plan, and re-run. The pipeline
    re-asserts hints after every post-pass, so hinted parts do not move
    and only the new or unconstrained ones flow through the placer. The
    alternative is choosing coordinates for the new part by hand, which
    is the thing the manual-layout gate refuses.

    FRAME CONVERSION, and it is the whole reason this is not three lines
    in the tool layer. Altium reports ``Location`` as the symbol ORIGIN,
    which is what the emitter wrote, while ``placement_hints`` are read
    as ``PlacedPart`` BODY CENTRES. Feeding one straight into the other
    shifts every part by its centre offset, silently and by a different
    amount per symbol. The snapshot names each instance's library symbol,
    the cache has the geometry on disk, so the offset is recomputed here
    against the part's CURRENT rotation.

    Needs the ``<project>.canvas.json`` snapshot that
    ``design_execute_plan`` writes, because that is what says which
    library symbol each refdes is. A sheet the engine never drew has no
    snapshot; see ``plan_from_live_sheet``.
    """
    out: dict[str, Any] = {"ok": True, "hints": {}, "unmatched": [],
                           "notes": []}
    snapshot = _load_canvas_snapshot(project_path, out)
    if snapshot is None:
        return out
    bridge = bridge or _resolve_bridge()
    if bridge is None:
        out["ok"] = False
        reason = f" ({_last_bridge_failure})" if _last_bridge_failure else ""
        out["notes"].append(f"no Altium bridge; cannot read positions.{reason}")
        return out

    from eda_agent.design.learner import _query_post_edit_positions

    live = _query_post_edit_positions(project_path, bridge, out)
    if live is None:
        return out

    from eda_agent.design.pipeline import _center_offset

    cache = SymbolCache(cache_dir or _default_cache_dir())
    for inst in (snapshot.get("canvas") or {}).get("instances", []):
        refdes = inst.get("refdes")
        pos = live.get(refdes)
        if not refdes or pos is None:
            if refdes:
                out["unmatched"].append(refdes)
            continue
        symbol = cache.get(inst.get("lib_path", ""), inst.get("lib_ref", ""))
        if symbol is None:
            out["unmatched"].append(refdes)
            continue
        off_x, off_y = _center_offset(
            symbol, int(pos["rotation"]), bool(inst.get("flipped", False)))
        out["hints"][refdes] = {
            "x": int(pos["x"] + off_x),
            "y": int(pos["y"] + off_y),
            "rotation": int(pos["rotation"]),
        }
    if out["unmatched"]:
        out["notes"].append(
            f"{len(out['unmatched'])} part(s) in the snapshot could not be "
            f"matched on the live sheet or in the symbol cache; they are "
            f"unhinted and the placer will position them"
        )
    return out


def plan_from_live_sheet(
    project_path: str,
    sheet_document: str = "",
    *,
    bridge: Any = None,
) -> dict[str, Any]:
    """Reconstruct a DesignPlan from a schematic the engine never drew.

    ``hints_from_sheet`` needs a ``<project>.canvas.json`` snapshot, so it
    only works on a sheet this engine drew. This reads the sheet itself:
    the components give the parts, the compiled netlist gives the nets,
    and the power-port glyphs say which of those nets are rails. The
    result is a plan you can edit and lay out.

    WHAT IS NOT ON THE SHEET, and so cannot come back:

    - ``role``. Every part's role tag is a planner assertion, not a
      drawn property. The motif matcher, the decoupling-bank law and
      the placement priors all key on it, so a reconstructed plan lays
      out worse than the plan it was reconstructed from. Re-assert the
      roles before laying it out; that is a reading task an LLM can do
      from the part list and the netlist, and it is why this returns a
      plan rather than going straight to a layout.
    - ``zone``. Functional blocks are not drawn either.
    - A SINGLE-PIN NET. ``Net.pins`` needs two, so a pin sitting on its
      own rail glyph or off-sheet label has nowhere to go. Those pins
      are listed in ``dropped_pins`` and the parts stay.
    - Anything on a sheet other than the one read. This is one sheet.

    The plan is validated before it is returned, so what comes back is
    either a plan the rest of the pipeline accepts or a refusal saying
    why.
    """
    out: dict[str, Any] = {
        "ok": True, "plan": None, "notes": [], "dropped_pins": [],
        "parts_read": 0, "nets_read": 0,
    }
    if not project_path or not Path(project_path).name:
        out["ok"] = False
        out["notes"].append(
            f"project_path must name a .PrjPcb file; got {project_path!r}")
        return out
    bridge = bridge or _resolve_bridge()
    if bridge is None:
        out["ok"] = False
        reason = f" ({_last_bridge_failure})" if _last_bridge_failure else ""
        out["notes"].append(f"no Altium bridge; cannot read the sheet.{reason}")
        return out

    scope = f"doc:{sheet_document}" if sheet_document else "active_doc"
    sheet_name = (Path(sheet_document).stem if sheet_document else "main")
    # A sheet this engine drew is named "<project stem>__<sheet>"; keep
    # the plan-side name it would have had so a re-emit lands on the
    # same document instead of a second one beside it.
    stem = Path(project_path).stem
    if sheet_name.startswith(f"{stem}__"):
        sheet_name = sheet_name[len(stem) + 2:]

    try:
        response = bridge.send_command("generic.query_objects", {
            "object_type": "eSchComponent", "scope": scope,
            "properties": ("Designator.Text,LibReference,SourceLibraryName,"
                           "Comment.Text"),
        })
    except Exception as exc:                    # noqa: BLE001
        out["ok"] = False
        out["notes"].append(f"reading the components failed: {exc}")
        return out

    parts: list[dict[str, Any]] = []
    on_sheet: set[str] = set()
    for row in (response or {}).get("objects", []):
        refdes = str(row.get("Designator.Text", "")).strip()
        lib_ref = str(row.get("LibReference", "")).strip()
        if not refdes or not lib_ref:
            # Both are required by the plan schema and neither can be
            # invented: a part with no designator has no identity and a
            # part with no lib_ref names no symbol.
            out["notes"].append(
                f"skipped a component with designator={refdes!r} "
                f"lib_ref={lib_ref!r}: the plan needs both")
            continue
        if not re.match(_REFDES_PATTERN, refdes):
            out["notes"].append(
                f"skipped {refdes!r}: not a designator the plan accepts")
            continue
        on_sheet.add(refdes)
        part: dict[str, Any] = {"refdes": refdes, "lib_ref": lib_ref,
                                "sheet": sheet_name}
        lib_path = str(row.get("SourceLibraryName", "")).strip()
        if lib_path:
            part["lib_path"] = lib_path
        value = str(row.get("Comment.Text", "")).strip()
        if value:
            part["value"] = value
        parts.append(part)
    out["parts_read"] = len(parts)
    if not parts:
        out["ok"] = False
        out["notes"].append(
            f"no components read from {scope}; a plan needs at least one")
        return out

    _enrich_parts(bridge, project_path, parts, out)
    rails = _rail_nets(bridge, scope, out)
    nets = _nets_on_sheet(bridge, project_path, on_sheet, rails, out)
    out["nets_read"] = len(nets)
    if not nets:
        out["ok"] = False
        out["notes"].append(
            "no net with two pins on this sheet; the plan schema has no "
            "way to express a part list with no connections")
        return out

    sheet: dict[str, Any] = {"name": sheet_name}
    size = _sheet_size(bridge, sheet_document, out)
    if size:
        sheet["size"] = size

    payload = {
        "spec": f"reconstructed from {sheet_document or 'the active sheet'}",
        "summary": (f"{len(parts)} parts and {len(nets)} nets read back off "
                    f"the sheet; roles and zones are not recorded on a "
                    f"schematic and are absent"),
        "sheets": [sheet],
        "parts": parts,
        "nets": nets,
    }
    try:
        DesignPlan.model_validate(payload)
    except ValidationError as exc:
        out["ok"] = False
        out["notes"].extend(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors())
        out["notes"].append(
            "the sheet does not reconstruct into a valid plan; the errors "
            "above name the parts or nets that could not be expressed")
        return out
    out["plan"] = payload
    out["notes"].append(
        "role and zone are NOT on a schematic and are absent from this "
        "plan; assert them before laying it out or the placer loses the "
        "motifs, the decoupling law and the placement priors")
    return out


def _sheet_size(
    bridge: Any, sheet_document: str, out: dict[str, Any]
) -> str:
    """The paper the sheet is drawn on, or "" to leave the plan default.

    Worth a round trip because the placer sizes the drawing area from
    it: 19% of real sheets are bigger than A4, and laying one of those
    out as A4 confines every part to a band it cannot be separated in.

    READS THE ACTIVE DOCUMENT, WHATEVER IT IS ASKED. Gen_GetDocumentInfo
    resolves SchServer.GetCurrentSchDocument and honours no scope, so
    the answer is checked against the document that was wanted before it
    is believed. Reporting the size of a sheet nobody asked about is
    exactly the wrong-document failure this server warns about.
    """
    try:
        info = bridge.send_command("generic.get_document_info", {})
    except Exception as exc:                    # noqa: BLE001
        out["notes"].append(
            f"could not read the sheet size ({exc}); the plan carries the "
            f"default and a re-layout would use an A4 drawing area")
        return ""
    if not isinstance(info, dict):
        return ""
    answered = str(info.get("file_path", "") or "")
    if sheet_document and (
            Path(answered).name.lower() != Path(sheet_document).name.lower()):
        out["notes"].append(
            f"sheet size not read: the document info came back for "
            f"{answered!r}, not the sheet asked for; the plan carries the "
            f"default")
        return ""
    size = str(info.get("sheet_size", "") or "").strip()
    if not size:
        return ""
    # MEMBERSHIP, not "does it resolve": sheet_dimensions falls back to
    # A4 for anything it does not know, so comparing dimensions would
    # accept Custom and every OrCAD size as though they were A4.
    from eda_agent.design.canvas import _SHEET_DIMENSIONS

    if size.upper() not in _SHEET_DIMENSIONS:
        out["notes"].append(
            f"sheet size {size!r} has no drawing area the layout knows; "
            f"the plan carries the default and a re-layout would use an "
            f"A4 area")
        return ""
    return size


def _enrich_parts(
    bridge: Any,
    project_path: str,
    parts: list[dict[str, Any]],
    out: dict[str, Any],
) -> None:
    """Fill in value, footprint and MPN from the compiled component data.

    THE VALUE IS NOT WHERE YOU FIRST LOOK. This engine stamps a part's
    value into a PARAMETER called Value, while a hand-drawn sheet
    conventionally carries it in the Comment, and the object query can
    only read the Comment. Reconstructing from the Comment alone gives a
    plan with no values at all for every sheet this engine drew, and
    laying that plan out again would strip them off the parts.

    Best effort. If the project will not answer, the parts keep whatever
    the sheet query gave them and a note says the values may be short.
    """
    if not parts:
        return
    try:
        response = bridge.send_command("project.get_component_info_batch", {
            "designators": "~~".join(p["refdes"] for p in parts),
            "project_path": project_path,
            "with_pin_nets": "false",
        })
    except Exception as exc:                    # noqa: BLE001
        out["notes"].append(
            f"could not read the compiled component data ({exc}); values "
            f"and footprints come from the sheet alone and may be missing")
        return
    by_refdes = {}
    for row in (response or {}).get("components", []):
        if isinstance(row, dict) and row.get("designator"):
            by_refdes[str(row["designator"]).strip()] = row
    for part in parts:
        row = by_refdes.get(part["refdes"])
        if not row:
            continue
        params = row.get("parameters") if isinstance(
            row.get("parameters"), dict) else {}
        for field, source in (
            ("value", params.get("Value") or row.get("comment")),
            ("footprint", row.get("footprint")),
            ("mpn", params.get("Manufacturer Part Number")),
            ("manufacturer", params.get("Manufacturer")),
        ):
            text = str(source or "").strip()
            if text:
                part[field] = text


def _rail_nets(
    bridge: Any, scope: str, out: dict[str, Any]
) -> dict[str, bool]:
    """Net name -> is_ground, from the sheet's power-port glyphs.

    A power port is drawn evidence that its net is a rail, and its style
    says which kind. Read from the sheet rather than from the net's name,
    which is a convention and not a fact.
    """
    from eda_agent.render.sch_svg import _GROUND_STYLES

    try:
        response = bridge.send_command("generic.query_objects", {
            "object_type": "ePowerObject", "scope": scope,
            "properties": "Text,Style",
        })
    except Exception as exc:                    # noqa: BLE001
        out["notes"].append(
            f"reading the power ports failed ({exc}); every net will be "
            f"laid out as a signal")
        return {}
    rails: dict[str, bool] = {}
    for row in (response or {}).get("objects", []):
        text = str(row.get("Text", "")).strip()
        if not text:
            continue
        try:
            style = int(row.get("Style", 0))
        except (TypeError, ValueError):
            continue
        # A rail with both a ground and a supply glyph on one sheet is
        # a drawing fault, not a thing to average: keep the ground,
        # which is the one that changes how it is drawn.
        rails[text] = rails.get(text, False) or style in _GROUND_STYLES
    return rails


def _nets_on_sheet(
    bridge: Any,
    project_path: str,
    on_sheet: set[str],
    rails: dict[str, bool],
    out: dict[str, Any],
) -> list[dict[str, Any]]:
    """The compiled netlist, restricted to the parts read off this sheet."""
    try:
        # FORCED RECOMPILE. This reads a sheet somebody has been working
        # on, which is the one case where the cached netlist is the old
        # one: a wire drawn since the last compile would be missing from
        # the plan, and nothing about the result would look wrong.
        response = bridge.send_command("project.get_nets", {
            "limit": "100000", "project_path": project_path,
            "force_recompile": "true"})
    except Exception as exc:                    # noqa: BLE001
        out["notes"].append(f"reading the netlist failed: {exc}")
        return []
    by_net: dict[str, list[dict[str, str]]] = {}
    for row in (response or {}).get("pins", []):
        comp = str(row.get("component", "")).strip()
        pin = str(row.get("pin", "")).strip()
        net = str(row.get("net", "")).strip()
        if not comp or not pin or not net:
            continue
        if comp not in on_sheet:
            continue
        # PinRef endpoints must be unique within a net or the plan is
        # refused wholesale. A pin listed twice for one net is a read
        # artefact, not a second connection, so it collapses here rather
        # than taking the whole reconstruction down with it.
        endpoint = {"refdes": comp, "pin": pin}
        if endpoint not in by_net.setdefault(net, []):
            by_net[net].append(endpoint)

    nets: list[dict[str, Any]] = []
    for name in sorted(by_net):
        pins = by_net[name]
        if len(pins) < 2:
            # Net.pins has a minimum of two. Say which pin went, because
            # a silently dropped connection reads as a wiring change.
            out["dropped_pins"].append(
                f"{pins[0]['refdes']}.{pins[0]['pin']} on {name}")
            continue
        if not re.match(_NET_PATTERN, name):
            out["dropped_pins"].extend(
                f"{p['refdes']}.{p['pin']} on {name}" for p in pins)
            out["notes"].append(
                f"net {name!r} is not a name the plan accepts; its "
                f"{len(pins)} pins are unconnected in this plan")
            continue
        net: dict[str, Any] = {"name": name, "pins": pins}
        if name in rails:
            net["is_ground" if rails[name] else "is_power"] = True
        nets.append(net)
    if out["dropped_pins"]:
        out["notes"].append(
            f"{len(out['dropped_pins'])} pin(s) sit on a net this plan "
            f"cannot express and are unconnected in it")
    return nets


def _load_canvas_snapshot(
    project_path: str, out: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """The ``<project>.canvas.json`` the canvas execute path writes."""
    # with_suffix raises on a path with no name, so an empty or
    # directory-only project_path would throw out of the tool rather
    # than being reported as the bad argument it is.
    if not project_path or not Path(project_path).name:
        out["ok"] = False
        out["notes"].append(
            "project_path must name a .PrjPcb file; got "
            f"{project_path!r}")
        return None
    path = Path(project_path).with_suffix(".canvas.json")
    if not path.exists():
        out["ok"] = False
        out["notes"].append(
            f"no canvas snapshot at {path}; run design_execute_plan first so "
            f"the engine records which library symbol each refdes is"
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["ok"] = False
        out["notes"].append(f"snapshot at {path} is unreadable: {exc}")
        return None


def layout_plan_from_json(
    plan_json: Union[str, dict],
    *,
    sheet: str = "main",
    placement_hints: Optional[dict[str, dict[str, int]]] = None,
    render_png: Optional[str] = None,
    bridge: Any = None,
    cache_dir: Optional[Path] = None,
) -> dict[str, Any]:
    """Plan -> layout DATA from the canvas pipeline, without emitting.

    THE SAME ENGINE THAT EXECUTES. ``design_layout_schematic`` used to run
    the standalone neat engine in ``schematic_layout.py``, which placed and
    routed differently from what ``design_execute_plan`` emits, so its
    ``placements`` and ``score`` described a layout nobody would ever see.
    Measured over ten held-out corpus sheets, that engine also lost the
    shared objective on seven of them and produced four pairs of
    OVERLAPPING BODIES where this pipeline produced none, so the shorter
    wire it appeared to draw was partly bought by stacking parts.

    Costs the offline property the old implementation had: symbol
    extraction needs a live Altium bridge, exactly as
    ``preview_plan_from_json`` does, because the real symbol geometry is
    what the placer and router reason about.
    """
    out: dict[str, Any] = {"ok": True, "sheet": sheet, "engine": "canvas",
                           "execution_accurate": True, "notes": [],
                           "failures": []}
    payload = _parse_plan(plan_json, out)
    if payload is None:
        out["errors"] = list(out["notes"])
        return out
    plan = _validate_plan(payload, out)
    if plan is None:
        # `errors` is this tool's long-standing shape for a rejected
        # plan; the shared helpers put the same strings in `notes`.
        out["errors"] = list(out["notes"])
        return out
    cross = plan.cross_check()
    if cross:
        out["ok"] = False
        out["errors"] = list(cross)
        return out

    # ONE ENGINE, TWO SYMBOL SOURCES. This tool is in
    # ``OFFLINE_DESIGN_TOOLS`` and is registered on the EasyEDA backend
    # because it never needed Altium, and `_resolve_bridge` resolves the
    # ALTIUM bridge specifically. Requiring it would have removed the
    # tool from that backend entirely. So the pipeline runs either way
    # and only the SYMBOLS differ: real geometry read from the SchLib
    # when a bridge is there, synthesised from the plan's pin lists when
    # it is not. ``symbols`` and ``execution_accurate`` in the result say
    # which happened, because a layout built on synthesised bodies is a
    # good preview and is NOT what the emit will produce.
    # NEVER RESOLVES A BRIDGE OF ITS OWN. This tool is in
    # OFFLINE_DESIGN_TOOLS, is registered on the EasyEDA backend, and
    # `tests/test_offline_design_tools.py` trips on any call that reaches
    # the Altium bridge -- deliberately, because a tool that degrades
    # quietly on a backend with no Altium reports less than it appears
    # to. A caller that HAS a bridge can pass one; nothing here goes
    # looking. So this is the execution engine seen offline: same
    # placement and routing rules as the emit, symbol geometry
    # synthesised from the plan's pin lists. For the exact geometry use
    # design_preview_plan, which is the same pipeline with real symbols.
    result = None
    if bridge is not None:
        cache_dir = cache_dir or _default_cache_dir()
        try:
            result = build_best_canvas_from_plan(
                plan, SymbolExtractor(bridge, SymbolCache(cache_dir)),
                placement_hints=placement_hints)
            out["symbols"] = "altium"
        except Exception as exc:            # noqa: BLE001 - fall back below
            logger.info("symbol extraction failed, synthesising: %s", exc)
            result = None
            out.pop("symbols", None)
    if result is None or not result.canvas.instances:
        # ONE ENGINE, TWO SYMBOL SOURCES. This tool is in
        # OFFLINE_DESIGN_TOOLS and is registered on the EasyEDA backend
        # because it never needed Altium, while `_resolve_bridge`
        # resolves the ALTIUM bridge specifically. Requiring real
        # symbols would have removed the tool from that backend
        # entirely. The placement and routing still come from the engine
        # that EXECUTES; only the geometry it reasons about is
        # approximate, and `symbols` / `execution_accurate` say so.
        from eda_agent.design.benchmark import SyntheticSymbolExtractor

        # A PLACEHOLDER lib_path, because the pipeline refuses a part
        # that says status=existing and names no library, and an
        # unresolved plan is exactly what this tool gets asked to lay
        # out early in a design. The synthetic extractor answers for any
        # library name, so the placeholder only satisfies the
        # precondition; nothing reads it.
        preview_plan = plan.model_copy(deep=True)
        for _part in preview_plan.parts:
            if not _part.lib_path:
                _part.lib_path = "synthetic"
        result = build_best_canvas_from_plan(
            preview_plan, SyntheticSymbolExtractor(preview_plan),
            placement_hints=placement_hints)
        plan = preview_plan
        out["symbols"] = "synthetic"
        out["execution_accurate"] = False
        out["notes"].append(
            "symbols were synthesised from the plan's pin lists, so this "
            "is a preview: same engine and same placement rules as the "
            "emit, approximate symbol geometry. Supply lib_path on the "
            "parts and run with Altium for the exact layout."
        )
    _accurate = out["execution_accurate"]
    out.update(_layout_payload(result, plan, sheet))
    out["execution_accurate"] = _accurate
    # Rendered HERE because this is where the canvas object lives; the
    # tool layer only ever sees the payload dict.
    if render_png and out.get("ok"):
        try:
            from eda_agent.design.illustrate import canvas_png

            target = Path(render_png)
            target.parent.mkdir(parents=True, exist_ok=True)
            canvas_png(result.canvas, str(target), sheet=sheet,
                       title=f"schematic: {sheet}")
            out["preview_png"] = str(target)
        except Exception as exc:            # never break the data path
            out["preview_error"] = str(exc)
    return out


def _layout_payload(
    result: PipelineResult, plan: DesignPlan, sheet: str,
) -> dict[str, Any]:
    """The layout-data response shape, read off a finished canvas."""
    from eda_agent.design.pipeline import _canvas_instance_to_placement
    from eda_agent.design.quality import score_canvas

    canvas = result.canvas
    placements = [
        {"designator": p.refdes, "x": p.x_mils, "y": p.y_mils,
         "rotation": p.rotation}
        for p in (_canvas_instance_to_placement(i)
                  for i in canvas.instances_on(sheet))
    ]
    wires = [{"x1": w.x1, "y1": w.y1, "x2": w.x2, "y2": w.y2, "net": w.net}
             for w in canvas.wires_on(sheet)]
    net_labels = [{"text": l.text, "x": l.x, "y": l.y,
                   "orientation": l.orientation}
                  for l in canvas.labels if l.sheet == sheet]
    power_ports = [{"text": pp.text, "x": pp.x, "y": pp.y, "style": pp.style}
                   for pp in canvas.power_ports if pp.sheet == sheet]
    junctions = [{"x": j.x, "y": j.y}
                 for j in canvas.junctions if j.sheet == sheet]

    # Which glyph each net ended up drawn as, read off the canvas rather
    # than decided in advance: a net the router demoted mid-run is a
    # label here even though the plan asked for a wire.
    wired = {w["net"] for w in wires if w["net"]}
    ported = {pp["text"] for pp in power_ports}
    labelled = {l["text"] for l in net_labels}
    # PORTS FIRST. A rail is drawn as a glyph plus short spokes, so it
    # has wires too; reporting it as "wire" because a spoke exists would
    # describe the spoke rather than the representation a reader sees.
    net_representation: dict[str, str] = {}
    for net in plan.nets:
        if net.name in ported:
            net_representation[net.name] = "power_port"
        elif net.name in wired:
            net_representation[net.name] = "wire"
        elif net.name in labelled:
            net_representation[net.name] = "net_label"
    sc = score_canvas(canvas, plan, sheet=sheet)
    score = {
        "total": round(sc.total, 1),
        "wire_crossings": sc.wire_crossings,
        "wires_through_bodies": sc.wires_through_bodies,
        "body_overlaps": sc.body_overlaps,
        "total_wire_length": sc.total_wire_length,
        "alignment_penalty": round(sc.alignment_penalty, 3),
        "aspect_ratio_penalty": round(sc.aspect_ratio_penalty, 3),
        "port_count": sc.port_count,
    }
    return {
        "ok": bool(result.ok),
        "placements": placements,
        "net_representation": net_representation,
        "wires": wires,
        "net_labels": net_labels,
        "power_ports": power_ports,
        "junctions": junctions,
        "score": score,
        "notes": [n.text for n in result.notes],
        "failures": [f.text for f in result.failures],
    }


def preview_plan_from_json(
    plan_json: Union[str, dict],
    output_svg_path: Optional[str] = None,
    *,
    bridge: Any = None,
    cache_dir: Optional[Path] = None,
    placement_hints: Optional[dict[str, dict[str, int]]] = None,
) -> dict[str, Any]:
    """Run plan -> canvas + render SVG, **without** emitting to Altium.

    Same as ``execute_plan_via_canvas_from_json`` minus the emit step.
    Useful for seeing what the layout will look like before paying the
    IPC cost of placing parts in Altium. Symbol extraction still talks
    to Altium (cache miss only), but the round-trips stop there; there
    is no project create, no place, no save.

    Args:
        plan_json: A DesignPlan as JSON string or dict.
        output_svg_path: Where to write the rendered SVG. If omitted,
            defaults to a temp file alongside the symbol cache so
            repeated previews don't accumulate.
        bridge: AltiumBridge; defaults to the global one.
        cache_dir: SymbolModel cache directory.

    Returns:
        Dict with:
            - ``ok``: True iff the pipeline ran without failures.
            - ``preview_svg_path``: where the SVG was written.
            - ``canvas``: snapshot of the produced canvas.
            - ``counts``: {placements, wires, labels, power_ports, junctions}.
            - ``notes`` / ``failures``: surfaced from the pipeline.
    """
    out: dict[str, Any] = {
        "ok": True,
        "preview_svg_path": None,
        "canvas": None,
        "counts": {},
        "notes": [],
        "failures": [],
    }
    payload = _parse_plan(plan_json, out)
    if payload is None:
        return out
    plan = _validate_plan(payload, out)
    if plan is None:
        return out

    bridge = bridge or _resolve_bridge()
    if bridge is None:
        out["ok"] = False
        reason = f" ({_last_bridge_failure})" if _last_bridge_failure else ""
        out["notes"].append(
            f"no Altium bridge available; symbol extraction needs Altium.{reason}"
        )
        return out

    cache_dir = cache_dir or _default_cache_dir()
    cache = SymbolCache(cache_dir)
    extractor = SymbolExtractor(bridge, cache)

    # Preview always uses the multi-try iteration so the agent sees the
    # SAME score it would emit; otherwise a hint-driven preview could
    # land at a different layout than the eventual execute step.
    pipeline_result = build_best_canvas_from_plan(
        plan, extractor, placement_hints=placement_hints,
    )
    out["canvas"] = pipeline_result.canvas.to_dict()
    out["counts"] = {
        "placements": pipeline_result.placement_count,
        "wires": pipeline_result.wire_count,
        "labels": pipeline_result.label_count,
        "power_ports": pipeline_result.power_port_count,
        "junctions": pipeline_result.junction_count,
    }
    for note in pipeline_result.notes:
        text = note.text if note.severity == "info" else f"[{note.severity}] {note.text}"
        out["notes"].append(text)
    for failure in pipeline_result.failures:
        out["failures"].append(failure.text)
    if not pipeline_result.ok:
        out["ok"] = False
        return out

    target = Path(output_svg_path) if output_svg_path else (
        _default_cache_dir() / "preview.svg"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(
            render_canvas_svg(pipeline_result.canvas), encoding="utf-8"
        )
        out["preview_svg_path"] = str(target)
    except Exception as exc:
        out["ok"] = False
        out["notes"].append(f"SVG write failed: {exc}")
    return out


def _empty_result_dict(project_path: str) -> dict[str, Any]:
    return {
        "ok": True,
        "project_path": project_path,
        "sheets_touched": [],
        "placed": [],
        "failures": [],
        "needs_creation": [],
        "notes": [],
        "nets_labelled": 0,
        "power_ports_placed": 0,
        "net_mismatches": [],
        "canvas": None,
        "preview_svg_path": None,
        "canvas_snapshot_path": None,
    }


def _parse_plan(
    plan_json: Union[str, dict], out: dict[str, Any]
) -> Optional[dict[str, Any]]:
    if isinstance(plan_json, dict):
        return plan_json
    try:
        return json.loads(plan_json)
    except json.JSONDecodeError as exc:
        out["ok"] = False
        # Match the legacy executor's wording so tools/tests that grep
        # for "invalid JSON" keep working across both execution paths.
        out["notes"].append(f"invalid JSON: {exc}")
        return None


def _validate_plan(
    payload: dict[str, Any], out: dict[str, Any]
) -> Optional[DesignPlan]:
    try:
        return DesignPlan.model_validate(payload)
    except ValidationError as exc:
        out["ok"] = False
        out["notes"].extend(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
            for err in exc.errors()
        )
        return None


def _enforce_plan_gates(plan: DesignPlan, out: dict[str, Any]) -> bool:
    """Run the mandatory pre-emit gates the canvas execute path owns.

    Mirrors the legacy executor: structural cross-check, then ERC-lite,
    then the needs_creation halt. Returns True if any gate tripped (the
    caller should return ``out`` immediately) and leaves ``out['ok']``
    False with the reasons in ``out['notes']`` / ``out['needs_creation']``.

    These run regardless of which placement engine follows, because they
    are about the plan being electrically sound and complete, not about
    layout.
    """
    # 1. Structural cross-check (unknown refdes in a net, a part pointing
    #    at a zone on another sheet, a zone on an unknown sheet).
    cross = plan.cross_check()
    if cross:
        out["ok"] = False
        out["notes"].extend(cross)
        return True

    # 2. ERC-lite: shorted pins, contradictory power/ground flags, floating
    #    nets (errors halt); decoupling / value warnings are surfaced but
    #    do not block.
    report = check_plan_erc(plan)
    for issue in report.warnings:
        out["notes"].append(f"[erc warning] {issue.code}: {issue.message}")
    if report.errors:
        out["ok"] = False
        for issue in report.errors:
            out["notes"].append(f"[erc error] {issue.code}: {issue.message}")
        return True

    # 3. needs_creation halt -- read from the structured plan, never from
    #    formatted note text. Emitting a partial design (skipping the
    #    unresolved parts) would mislead a reviewer about completeness.
    needs_creation = [
        p.refdes for p in plan.parts if p.status == PartStatus.NEEDS_CREATION
    ]
    if needs_creation:
        out["ok"] = False
        out["needs_creation"] = needs_creation
        out["notes"].append(
            "Plan contains needs_creation parts; refusing to instantiate a "
            "partial design. Resolve those parts (pick an existing-lib "
            "equivalent or author a new symbol) and re-run."
        )
        return True

    return False


_last_bridge_failure: str = ""


def _resolve_bridge() -> Any:
    """Lazy-import the global bridge so this module doesn't pull bridge
    code at import time (keeps it cheap for unit tests that don't need
    Altium).

    On failure the reason is kept in ``_last_bridge_failure`` so callers
    can put the REAL cause (e.g. "Altium is not running") in the result
    notes instead of a generic "no bridge".
    """
    global _last_bridge_failure
    try:
        from eda_agent.bridge.altium_bridge import get_bridge
    except ImportError as exc:
        _last_bridge_failure = f"bridge module unavailable: {exc}"
        return None
    try:
        bridge = get_bridge()
        _last_bridge_failure = ""
        return bridge
    except Exception as exc:
        _last_bridge_failure = f"{type(exc).__name__}: {exc}"
        logger.warning("get_bridge failed: %s", exc)
        return None


def _merge_pipeline_result(
    out: dict[str, Any], pr: PipelineResult
) -> None:
    out["canvas"] = pr.canvas.to_dict()
    out["nets_labelled"] = pr.label_count
    out["power_ports_placed"] = pr.power_port_count
    for note in pr.notes:
        text = note.text
        if note.severity != "info":
            text = f"[{note.severity}] {text}"
        out["notes"].append(text)
        # Surface needs_creation skips so the MCP caller can act on them.
        # Read the structured refdes off the note, never the formatted text
        # (the old token split returned the literal word "skipping").
        if note.refdes is not None and note.refdes not in out["needs_creation"]:
            out["needs_creation"].append(note.refdes)
    for failure in pr.failures:
        out["failures"].append({
            "refdes": "",
            "code": "PIPELINE_ERROR",
            "reason": failure.text,
        })
    if pr.failures:
        out["ok"] = False


def _merge_emit_result(out: dict[str, Any], er: EmitResult) -> None:
    out["sheets_touched"] = list(er.sheets_emitted)
    # Recover (refdes, sheet, x, y, rotation) placement records from the
    # canvas + the emit's placed_refdes set so the legacy result shape
    # ("placed": [...]) stays intact.
    canvas_dict = out.get("canvas") or {}
    canvas_instances = {
        i["refdes"]: i for i in canvas_dict.get("instances", [])
    }
    for refdes in er.placed_refdes:
        inst = canvas_instances.get(refdes)
        if inst is None:
            continue
        out["placed"].append({
            "refdes": refdes,
            "sheet": inst.get("sheet", "main"),
            "x_mils": inst.get("x", 0),
            "y_mils": inst.get("y", 0),
            "rotation": inst.get("rotation", 0),
        })
    for failure in er.failures:
        out["failures"].append({
            "refdes": failure.refdes,
            "code": failure.code,
            "reason": failure.reason,
        })
    out["notes"].extend(er.notes)
    if er.failures or not er.ok:
        out["ok"] = False
