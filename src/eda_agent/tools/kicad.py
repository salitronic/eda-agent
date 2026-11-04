# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""KiCad-native tools: PCB reads and kicad-cli exports/outputs.

The EDA-agnostic main flows (review, DRC, ERC, board info, component/net
listing) are the neutral tools in ``tools/eda.py``. What lives here is
KiCad-specific: detailed board reads over the IPC API, and file exports produced
by KiCad's own ``kicad-cli`` (Gerbers, drill, STEP, PDF, position files, BOM,
netlist, 3D render). Exports run on the files on disk, so they reflect the last
save; each returns the absolute output location.

Read tools report ``{"ok": True, ...}`` / ``{"ok": False, "reason": ...}``.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from ..bridge.kicad_bridge import KiCadNotReachableError, get_kicad_bridge
from ..core.kicad_export import resolve_output, run_cli, summarize


def _read(fn: Callable[[Any], Any], key: str) -> dict[str, Any]:
    """Run a bridge read and wrap it, or report why KiCad is unreachable."""
    try:
        value = fn(get_kicad_bridge())
    except KiCadNotReachableError as e:
        return {"ok": False, "reason": str(e)}
    except Exception as e:
        return {"ok": False, "reason": f"KiCad read failed: {e}"}
    out: dict[str, Any] = {"ok": True}
    if isinstance(value, list):
        out["count"] = len(value)
    out[key] = value
    return out


def _write(fn: Callable[[Any], Any]) -> dict[str, Any]:
    """Run a bridge mutation and wrap it, or report why it failed."""
    try:
        result = fn(get_kicad_bridge())
    except KiCadNotReachableError as e:
        return {"ok": False, "reason": str(e)}
    except Exception as e:
        return {"ok": False, "reason": f"KiCad write failed: {e}"}
    out: dict[str, Any] = {"ok": True}
    if isinstance(result, dict):
        out.update(result)
    return out


def _basename(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _report_placeholders(result: dict[str, Any], refs: list[str]) -> None:
    """Say which parts got a generated box instead of their footprint.

    The board generators fall back to an ``eda:BOX`` of 1 mm pads when a
    part's footprint cannot be found in KiCad's libraries. That used to
    happen silently: the board opened, the ratsnest looked right, and the
    reply did not mention that some parts had no real land pattern.
    """
    result["placeholder_footprints"] = list(refs)
    if refs:
        result["warning"] = (
            f"{len(refs)} of {result.get('part_count', len(refs))} parts got "
            f"a generated placeholder (eda:BOX, 1 mm pads in a row) because "
            f"their footprint was not found in KiCad's libraries: "
            f"{', '.join(refs)}. Give each a footprint lib_id (Lib:Name) "
            f"that exists, or create one with "
            f"kicad_create_footprint_from_package, before laying out.")


def _resolve_plan_libs(plan):
    """Resolve a plan's real symbols and footprints from KiCad's libraries.

    Returns ``(symbols, part_symbol, mod_texts, unread)``: symbol blocks
    keyed by lib_id, refdes->lib_id, refdes->.kicad_mod text, and why the
    libraries could not be read (None when they were). Parts that don't
    resolve fall back to generated boxes in the emitters.
    """
    symbols: dict[str, str] = {}
    part_symbol: dict[str, str] = {}
    mod_texts: dict[str, str] = {}
    try:
        from ..core.kicad_symbol import extract_symbol, standard_symbol_dirs
        from ..core.kicad_footprint import (find_footprint_file,
                                            standard_footprint_dirs)
        cli = get_kicad_bridge().kicad_cli_path()
        sdirs = standard_symbol_dirs(cli)
        fdirs = standard_footprint_dirs(cli)
        for p in plan.parts:
            if p.lib_ref and ":" in p.lib_ref:
                part_symbol[p.refdes] = p.lib_ref
                if p.lib_ref not in symbols:
                    block = extract_symbol(p.lib_ref, sdirs)
                    if block:
                        symbols[p.lib_ref] = block
            if p.footprint:
                path = find_footprint_file(p.footprint, fdirs)
                if path:
                    with open(path, "r", encoding="utf-8") as fh:
                        mod_texts[p.refdes] = fh.read()
    except Exception as e:  # no KiCad or no libraries: generated boxes, said so
        return {}, {}, {}, str(e)
    return symbols, part_symbol, mod_texts, None


def _report_unread_libraries(result: dict, unread) -> None:
    if unread:
        result["libraries_unread"] = (
            unread + "; every part was drawn as a generated box")


# kicad-cli pcb export formats that write into a directory (vs a single file),
# take a --layers list, or need --force to overwrite. Used by the generic
# passthrough so the long-tail formats are all reachable from one tool.
_PCB_DIR_FORMATS = {"gerbers", "drill", "dxf", "svg", "odb"}
_PCB_LAYER_FORMATS = {"dxf", "svg", "pdf", "ps"}
_PCB_FORCE_FORMATS = {"step", "glb", "vrml", "stl", "3dpdf", "stpz"}
_PCB_EXT = {
    "step": "step", "glb": "glb", "vrml": "wrl", "stl": "stl", "3dpdf": "pdf",
    "pdf": "pdf", "pos": "pos", "ipc2581": "xml", "ipcd356": "d356",
    "gencad": "cad", "brep": "brep", "ply": "ply", "xao": "xao",
    "u3d": "u3d", "stpz": "stpz", "ps": "ps", "hpgl": "plt", "stats": "txt",
}

# Schematic export: multi-sheet formats write into a directory; the rest a file.
_SCH_DIR_FORMATS = {"svg", "dxf", "ps", "hpgl"}
_SCH_EXT = {"netlist": "net", "bom": "csv", "python-bom": "xml", "pdf": "pdf"}


async def _pcb_export(output_dir: Optional[str], tag: str,
                      make_args: Callable[[str, str], tuple[list[str], str]]
                      ) -> dict[str, Any]:
    """Resolve kicad-cli + board file, run an export, summarize the result."""
    try:
        br = get_kicad_bridge()
        cli = br.kicad_cli_path()
        board = br.board_file_path()
    except KiCadNotReachableError as e:
        return {"ok": False, "reason": str(e)}
    outdir = resolve_output(output_dir, board, tag)
    args, produced = make_args(board, outdir)
    try:
        result = await run_cli(cli, args)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return summarize(result, produced)


async def _lib_export(library_path: str, output_dir: Optional[str], tag: str,
                      make_args: Callable[[str, str], tuple[list[str], str]]
                      ) -> dict[str, Any]:
    """Run a kicad-cli library command on a user-supplied library path."""
    if not library_path or not os.path.exists(library_path):
        return {"ok": False, "reason": f"library not found: {library_path}"}
    try:
        cli = get_kicad_bridge().kicad_cli_path()
    except KiCadNotReachableError as e:
        return {"ok": False, "reason": str(e)}
    outdir = resolve_output(output_dir, library_path, tag)
    args, produced = make_args(library_path, outdir)
    try:
        result = await run_cli(cli, args)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return summarize(result, produced)


async def _sch_export(output_dir: Optional[str], tag: str,
                      make_args: Callable[[str, str], tuple[list[str], str]]
                      ) -> dict[str, Any]:
    try:
        br = get_kicad_bridge()
        cli = br.kicad_cli_path()
        sch = br.sch_file_path()
    except KiCadNotReachableError as e:
        return {"ok": False, "reason": str(e)}
    if not os.path.exists(sch):
        return {"ok": False, "reason": f"no schematic found at {sch}"}
    outdir = resolve_output(output_dir, sch, tag)
    args, produced = make_args(sch, outdir)
    try:
        result = await run_cli(cli, args)
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    return summarize(result, produced)


def register_kicad_tools(mcp) -> None:

    # -- connection ---------------------------------------------------------
    @mcp.tool()
    async def kicad_ping() -> dict[str, Any]:
        """Check the live connection to a running KiCad; returns KiCad and API
        versions, or a reason (enable the API server, open a board)."""
        try:
            return {"ok": True, **get_kicad_bridge().ping()}
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad call failed: {e}"}

    # -- authoring (writes) -------------------------------------------------
    # These mutate the open board. Changes apply in-session (undoable in KiCad);
    # pass save=true to also write the file. Reference is the component
    # designator (e.g. "R4", "U1").
    @mcp.tool()
    async def kicad_move_component(reference: str, x_mm: float, y_mm: float,
                                   save: bool = False) -> dict[str, Any]:
        """Move a component to an absolute board position (mm)."""
        return _write(lambda b: b.move_component(reference, x_mm, y_mm, save))

    @mcp.tool()
    async def kicad_rotate_component(reference: str, degrees: float,
                                     save: bool = False) -> dict[str, Any]:
        """Set a component's orientation (degrees)."""
        return _write(lambda b: b.rotate_component(reference, degrees, save))

    @mcp.tool()
    async def kicad_set_component_side(reference: str, side: str,
                                       save: bool = False) -> dict[str, Any]:
        """Place a component on the top or bottom copper layer ("top"/"bottom")."""
        return _write(lambda b: b.set_component_side(reference, side, save))

    @mcp.tool()
    async def kicad_lock_component(reference: str, locked: bool = True,
                                   save: bool = False) -> dict[str, Any]:
        """Lock or unlock a component's placement."""
        return _write(lambda b: b.set_component_locked(reference, locked, save))

    @mcp.tool()
    async def kicad_set_component_value(reference: str, value: str,
                                        save: bool = False) -> dict[str, Any]:
        """Set a component's value field."""
        return _write(lambda b: b.set_component_value(reference, value, save))

    @mcp.tool()
    async def kicad_delete_component(reference: str,
                                     save: bool = False) -> dict[str, Any]:
        """Delete a component from the board."""
        return _write(lambda b: b.delete_component(reference, save))

    @mcp.tool()
    async def kicad_add_track(net: str, layer: str, x1_mm: float, y1_mm: float,
                              x2_mm: float, y2_mm: float, width_mm: float,
                              save: bool = False) -> dict[str, Any]:
        """Add a copper track segment on a layer ("F.Cu", "B.Cu", "In1.Cu"),
        assigned to a net, between two points (mm) at a given width (mm)."""
        return _write(lambda b: b.create_track(
            net, layer, x1_mm, y1_mm, x2_mm, y2_mm, width_mm, save))

    @mcp.tool()
    async def kicad_add_via(net: str, x_mm: float, y_mm: float,
                            diameter_mm: float, drill_mm: float,
                            save: bool = False) -> dict[str, Any]:
        """Add a through via at a position (mm) with pad diameter and drill
        diameter (mm), assigned to a net."""
        return _write(lambda b: b.create_via(
            net, x_mm, y_mm, diameter_mm, drill_mm, save))

    @mcp.tool()
    async def kicad_add_text(text: str, x_mm: float, y_mm: float,
                             layer: str = "F.Silkscreen",
                             save: bool = False) -> dict[str, Any]:
        """Add a text label at a position (mm) on a layer (default
        "F.Silkscreen")."""
        return _write(lambda b: b.create_text(text, x_mm, y_mm, layer, save))

    @mcp.tool()
    async def kicad_add_line(x1_mm: float, y1_mm: float, x2_mm: float,
                             y2_mm: float, layer: str = "F.Silkscreen",
                             width_mm: float = 0.15,
                             save: bool = False) -> dict[str, Any]:
        """Add a graphic line between two points (mm) on a layer (default
        "F.Silkscreen") with a stroke width (mm)."""
        return _write(lambda b: b.create_line(
            x1_mm, y1_mm, x2_mm, y2_mm, layer, width_mm, save))

    @mcp.tool()
    async def kicad_add_circle(cx_mm: float, cy_mm: float, radius_mm: float,
                               layer: str = "F.Silkscreen",
                               width_mm: float = 0.15,
                               save: bool = False) -> dict[str, Any]:
        """Add a graphic circle at a centre (mm) with a radius (mm) on a
        layer, with a stroke width (mm)."""
        return _write(lambda b: b.create_circle(
            cx_mm, cy_mm, radius_mm, layer, width_mm, save))

    @mcp.tool()
    async def kicad_add_rectangle(x1_mm: float, y1_mm: float, x2_mm: float,
                                  y2_mm: float, layer: str = "F.Silkscreen",
                                  width_mm: float = 0.15,
                                  save: bool = False) -> dict[str, Any]:
        """Add a graphic rectangle from top-left to bottom-right (mm) on a
        layer, with a stroke width (mm)."""
        return _write(lambda b: b.create_rectangle(
            x1_mm, y1_mm, x2_mm, y2_mm, layer, width_mm, save))

    @mcp.tool()
    async def kicad_add_arc(x1_mm: float, y1_mm: float, mid_x_mm: float,
                            mid_y_mm: float, x2_mm: float, y2_mm: float,
                            layer: str = "F.Silkscreen",
                            width_mm: float = 0.15,
                            save: bool = False) -> dict[str, Any]:
        """Add a graphic arc through start, mid and end points (mm) on a layer,
        with a stroke width (mm)."""
        return _write(lambda b: b.create_arc(
            x1_mm, y1_mm, mid_x_mm, mid_y_mm, x2_mm, y2_mm, layer,
            width_mm, save))

    @mcp.tool()
    async def kicad_add_zone(net: str, points: list, layer: str = "F.Cu",
                             name: str = "", priority: int = 0,
                             save: bool = False) -> dict[str, Any]:
        """Add a copper zone (pour) on a layer, assigned to a net, with an
        outline given as a list of [x_mm, y_mm] points (at least 3)."""
        return _write(lambda b: b.create_zone(
            net, points, layer, name, priority, save))

    @mcp.tool()
    async def kicad_set_text_variable(key: str, value: str,
                                      save: bool = False) -> dict[str, Any]:
        """Set a project text (substitution) variable, e.g. key "REV" value
        "B". Other variables are preserved."""
        return _write(lambda b: b.set_text_variable(key, value, save))

    @mcp.tool()
    async def kicad_upgrade_board(force: bool = True) -> dict[str, Any]:
        """Upgrade the open board's file format to the current KiCad version.
        Rewrites the .kicad_pcb on disk in place."""
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            board = br.board_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        args = ["pcb", "upgrade"] + (["--force"] if force else []) + [board]
        try:
            result = await run_cli(cli, args)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        return summarize(result, board)

    @mcp.tool()
    async def kicad_upgrade_schematic(force: bool = True) -> dict[str, Any]:
        """Upgrade the open schematic's file format to the current KiCad
        version. Rewrites the .kicad_sch on disk in place."""
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        if not os.path.exists(sch):
            return {"ok": False, "reason": f"no schematic found at {sch}"}
        args = ["sch", "upgrade"] + (["--force"] if force else []) + [sch]
        try:
            result = await run_cli(cli, args)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        return summarize(result, sch)

    @mcp.tool()
    async def kicad_run_action(action: str) -> dict[str, Any]:
        """Run an arbitrary KiCad tool action by name (e.g. a TOOL_ACTION such
        as "pcbnew.Control.zoomFitScreen"). Escape hatch equivalent to Altium's
        run-process/run-menu.

        NOTE: this is KiCad's unstable, non-guaranteed action API -- it may have
        side effects on the open design. Use deliberately."""
        return _write(lambda b: b.run_action(action))

    @mcp.tool()
    async def kicad_save_board() -> dict[str, Any]:
        """Write the open board to disk."""
        return _write(lambda b: b.save_board())

    # -- PCB reads ----------------------------------------------------------
    @mcp.tool()
    async def kicad_list_tracks() -> dict[str, Any]:
        """Copper tracks and arcs: net, layer, width, endpoints, length (mm)."""
        return _read(lambda b: b.tracks(), "tracks")

    @mcp.tool()
    async def kicad_get_trace_lengths() -> dict[str, Any]:
        """Total routed track length per net (mm), summed from the copper
        tracks. Useful for length-matching and impedance work."""
        try:
            tracks = get_kicad_bridge().tracks()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        totals: dict[str, float] = {}
        for t in tracks:
            net = t.get("net") or ""
            length = t.get("length_mm")
            if net and length:
                totals[net] = totals.get(net, 0.0) + length
        lengths = [{"net": n, "length_mm": round(v, 3)}
                   for n, v in sorted(totals.items(), key=lambda x: -x[1])]
        return {"ok": True, "net_count": len(lengths), "lengths": lengths}

    @mcp.tool()
    async def kicad_list_vias() -> dict[str, Any]:
        """Vias: net, position, diameter and drill (mm)."""
        return _read(lambda b: b.vias(), "vias")

    @mcp.tool()
    async def kicad_get_layer_usage() -> dict[str, Any]:
        """Per-layer routing usage: track count and total track length (mm),
        sorted by length."""
        try:
            tracks = get_kicad_bridge().tracks()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        usage: dict[str, dict[str, Any]] = {}
        for t in tracks:
            layer = t.get("layer") or ""
            u = usage.setdefault(layer, {"layer": layer, "track_count": 0,
                                         "total_length_mm": 0.0})
            u["track_count"] += 1
            if t.get("length_mm"):
                u["total_length_mm"] += t["length_mm"]
        rows = sorted(usage.values(), key=lambda x: -x["total_length_mm"])
        for r in rows:
            r["total_length_mm"] = round(r["total_length_mm"], 3)
        return {"ok": True, "layers": rows}

    @mcp.tool()
    async def kicad_get_via_summary() -> dict[str, Any]:
        """Via count and the distinct via sizes (pad diameter / drill) in use,
        each with a count. Fewer via types is cheaper to fabricate."""
        try:
            vias = get_kicad_bridge().vias()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        counts: dict[tuple, int] = {}
        for v in vias:
            key = (v.get("diameter_mm"), v.get("drill_mm"))
            counts[key] = counts.get(key, 0) + 1
        types = [{"diameter_mm": d, "drill_mm": dr, "count": c}
                 for (d, dr), c in sorted(counts.items(),
                                          key=lambda x: -x[1])]
        return {"ok": True, "via_count": len(vias), "via_types": types}

    @mcp.tool()
    async def kicad_list_zones() -> dict[str, Any]:
        """Copper zones and rule areas: name, net, layers, fill, priority."""
        return _read(lambda b: b.zones(), "zones")

    @mcp.tool()
    async def kicad_get_stackup() -> dict[str, Any]:
        """Board stackup: layer name, type, material, thickness (mm)."""
        return _read(lambda b: b.stackup(), "stackup")

    @mcp.tool()
    async def kicad_get_layers() -> dict[str, Any]:
        """Enabled board layer names."""
        return _read(lambda b: b.layers(), "layers")

    @mcp.tool()
    async def kicad_get_board_outline() -> dict[str, Any]:
        """Board edge bounding box (mm), from Edge.Cuts graphics."""
        return _read(lambda b: b.board_outline(), "outline")

    @mcp.tool()
    async def kicad_get_title_block() -> dict[str, Any]:
        """Title-block fields (title, date, revision, company, comments)."""
        return _read(lambda b: b.title_block(), "title_block")

    @mcp.tool()
    async def kicad_list_text() -> dict[str, Any]:
        """Free board text items: value, layer, position (mm)."""
        return _read(lambda b: b.texts(), "text")

    @mcp.tool()
    async def kicad_get_component_details(reference: str) -> dict[str, Any]:
        """Full detail for one component: value, footprint, position (mm),
        layer, lock state, and its pads with net assignments."""
        try:
            br = get_kicad_bridge()
            fps = br.footprints()
            _parts, pins, _u = br.component_pins()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        ref = (reference or "").strip()
        fp = next((f for f in fps if f.get("reference") == ref), None)
        if fp is None:
            return {"ok": False, "reason": f"no component '{reference}'"}
        comp_pins = [{"pin": p["pin"], "net": p["net"]} for p in pins
                     if p.get("refdes") == ref]
        return {"ok": True, **fp, "pin_count": len(comp_pins),
                "pins": comp_pins}

    @mcp.tool()
    async def kicad_get_fab_stats() -> dict[str, Any]:
        """Board fabrication statistics (component / pad / hole / track counts,
        board area) via kicad-cli. Runs on the board file on disk."""
        import json as _json
        import tempfile
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            board = br.board_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        fd, out = tempfile.mkstemp(suffix=".json", prefix="eda_stats_")
        os.close(fd)
        try:
            result = await run_cli(cli, ["pcb", "export", "stats", "--format",
                                         "json", "--output", out, board])
            if result["returncode"] != 0:
                return {"ok": False, "reason": result.get("stderr")
                        or result.get("stdout") or "stats export failed"}
            with open(out, "r", encoding="utf-8", errors="replace") as fh:
                data = _json.load(fh)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
        return {"ok": True, "stats": data}

    @mcp.tool()
    async def kicad_get_unconnected_pins() -> dict[str, Any]:
        """Pads with no net assigned (potentially unconnected pins)."""
        try:
            _parts, pins, _u = get_kicad_bridge().component_pins()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        unconn = [{"reference": p["refdes"], "pin": p["pin"]} for p in pins
                  if not (p.get("net") or "").strip()]
        return {"ok": True, "count": len(unconn), "pins": unconn}

    @mcp.tool()
    async def kicad_get_diff_pairs() -> dict[str, Any]:
        """Differential pairs inferred from net naming (X_P/X_N, X+/X-),
        naming-agnostic beyond the universal polarity suffix convention."""
        import re
        try:
            nets = [n["name"] for n in get_kicad_bridge().nets()
                    if n.get("name")]
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        bases: dict[str, dict[str, str]] = {}
        for name in nets:
            m = re.match(r"^(.*?)(_?)([PpNn+\-])$", name)
            if not m:
                continue
            key = m.group(1) + m.group(2)
            positive = m.group(3) in ("P", "p", "+")
            bases.setdefault(key, {})["pos" if positive else "neg"] = name
        pairs = [{"base": k.rstrip("_"), "positive": v["pos"],
                  "negative": v["neg"]}
                 for k, v in sorted(bases.items())
                 if "pos" in v and "neg" in v]
        return {"ok": True, "count": len(pairs), "pairs": pairs}

    @mcp.tool()
    async def kicad_get_net(net_name: str) -> dict[str, Any]:
        """All pads on a given net: reference and pin number."""
        try:
            _parts, pins, _u = get_kicad_bridge().component_pins()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        name = (net_name or "").strip()
        nodes = [{"reference": p["refdes"], "pin": p["pin"]} for p in pins
                 if p.get("net") == name]
        if not nodes:
            return {"ok": False,
                    "reason": f"net '{net_name}' not found or has no pads"}
        return {"ok": True, "net": name, "pad_count": len(nodes),
                "pads": nodes}

    @mcp.tool()
    async def kicad_find_component(query: str) -> dict[str, Any]:
        """Find components whose reference or value contains the query
        (case-insensitive)."""
        try:
            fps = get_kicad_bridge().footprints()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        q = (query or "").strip().lower()
        matches = [f for f in fps if q and (
            q in (f.get("reference") or "").lower()
            or q in (f.get("value") or "").lower())]
        return {"ok": True, "query": query, "count": len(matches),
                "components": matches}

    @mcp.tool()
    async def kicad_get_selection() -> dict[str, Any]:
        """Items currently selected in the KiCad PCB editor: type and, for
        footprints, reference."""
        return _read(lambda b: b.selection(), "selection")

    @mcp.tool()
    async def kicad_list_shapes() -> dict[str, Any]:
        """Graphic shapes: type (segment/arc/circle/rect/polygon), layer, net,
        and geometry points (mm)."""
        return _read(lambda b: b.shapes(), "shapes")

    @mcp.tool()
    async def kicad_list_groups() -> dict[str, Any]:
        """Item groups on the board: name and member count."""
        return _read(lambda b: b.groups(), "groups")

    @mcp.tool()
    async def kicad_list_dimensions() -> dict[str, Any]:
        """Dimension annotations: layer, override text, height (mm)."""
        return _read(lambda b: b.dimensions(), "dimensions")

    @mcp.tool()
    async def kicad_list_documents() -> dict[str, Any]:
        """Documents currently open in KiCad (PCB and schematic) with
        filename and project."""
        return _read(lambda b: b.open_documents(), "documents")

    @mcp.tool()
    async def kicad_get_project_info() -> dict[str, Any]:
        """Open project's name, directory, and net-class names."""
        return _read(lambda b: b.project_info(), "project")

    @mcp.tool()
    async def kicad_get_text_variables() -> dict[str, Any]:
        """Project text (substitution) variables."""
        return _read(lambda b: b.text_variables(), "text_variables")

    @mcp.tool()
    async def kicad_get_netlist() -> dict[str, Any]:
        """Structured schematic netlist: components (reference, value,
        footprint) and nets (name, class, pin nodes). This is the schematic's
        intended connectivity, from ``kicad-cli sch export netlist``."""
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        if not os.path.exists(sch):
            return {"ok": False, "reason": f"no schematic found at {sch}"}
        try:
            from ..core.kicad_netlist import get_schematic_netlist
            data = await get_schematic_netlist(cli, sch)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        return {"ok": True, "component_count": len(data["components"]),
                "net_count": len(data["nets"]), **data}

    @mcp.tool()
    async def kicad_full_review(include_drc: bool = True,
                                include_erc: bool = True) -> dict[str, Any]:
        """One-call comprehensive review of the open design: board stats, PCB
        connectivity review, schematic review, schematic-vs-PCB comparison, and
        (optionally) geometric DRC and schematic ERC. Sections that fail are
        reported under sections_failed rather than aborting the whole review."""
        from ..core.review_engine import review_snapshot
        from ..core.backends import KiCadBackend
        from ..core.kicad_drc import run_kicad_cli_drc, run_kicad_cli_erc
        from ..core.kicad_netlist import (get_schematic_netlist,
                                          snapshot_from_netlist,
                                          compare_schematic_to_pcb)
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
            board_file = br.board_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}

        sections: dict[str, Any] = {}
        failed: list[dict[str, str]] = []

        async def _section(name, coro):
            try:
                sections[name] = await coro
            except Exception as e:
                failed.append({"section": name, "error": str(e)})

        # PCB connectivity review (from the live board model).
        try:
            snap = await KiCadBackend().snapshot()
            sections["pcb_review"] = review_snapshot(snap)
        except Exception as e:
            failed.append({"section": "pcb_review", "error": str(e)})

        # Schematic-side sections share one netlist export.
        netlist = None
        try:
            netlist = await get_schematic_netlist(cli, sch)
        except Exception as e:
            failed.append({"section": "netlist", "error": str(e)})
        if netlist is not None:
            try:
                sections["schematic_review"] = review_snapshot(
                    snapshot_from_netlist(netlist, os.path.basename(sch)))
            except Exception as e:
                failed.append({"section": "schematic_review", "error": str(e)})
            try:
                pcb_refs = {p["refdes"] for p in br.component_pins()[0]
                            if p.get("refdes")}
                pcb_nets = {n["name"] for n in br.nets() if n.get("name")}
                sections["sch_pcb_compare"] = compare_schematic_to_pcb(
                    netlist, pcb_refs, pcb_nets)
            except Exception as e:
                failed.append({"section": "sch_pcb_compare", "error": str(e)})

        if include_drc:
            await _section("drc", run_kicad_cli_drc(cli, board_file))
        if include_erc:
            await _section("erc", run_kicad_cli_erc(cli, sch))

        # sections_run is measured here, not derived from a list of
        # what COULD run, so this never claimed a section had run when
        # it had not. What it did claim was success: with every section
        # failing, the reply was ok:True with an empty sections_run,
        # and a client that reads the envelope before the detail is
        # told the review worked. The same shape on the EasyEDA side
        # reported a board as having no violations when nothing had
        # examined it.
        # What each section CARRIED, not just that it ran. The same
        # distinction the EasyEDA review needed twice: a review of a
        # design whose every section came back empty reports the same
        # "5 sections run" as a review of a full board, and only one of
        # those means anything. Bookkeeping keys are excluded and a dict
        # of scalars counts as one record rather than as its key count,
        # which is what made the EasyEDA version report the section that
        # measured nothing as the richest.
        _NOT_DATA = {"ok", "reason", "note", "error", "errors", "command",
                     "unavailable", "sections_failed", "warning"}
        rows: dict = {}
        for name, section in sections.items():
            if not isinstance(section, dict):
                continue
            for key, value in section.items():
                if key in _NOT_DATA:
                    continue
                if isinstance(value, list):
                    rows[name] = rows.get(name, 0) + len(value)
                elif isinstance(value, dict):
                    rows[name] = rows.get(name, 0) + sum(
                        1 for item in value.values()
                        if isinstance(item, (dict, list)) and item)
        rows = {name: count for name, count in rows.items() if count}

        out = {"ok": bool(sections), "sections_run": sorted(sections),
               "sections_with_data": sorted(rows), "rows_by_section": rows,
               "sections_failed": failed, "sections": sections}
        if sections and not rows:
            out["scope_warning"] = (
                "every section came back EMPTY. That is what an empty "
                "design looks like and also what reading the wrong "
                "project looks like, so this is not evidence the design "
                "is clean.")
        if not sections:
            out["reason"] = (
                "not one section of the review could be produced, so this "
                "is not a clean design: nothing was examined. See "
                "'sections_failed' for why.")
        return out

    @mcp.tool()
    async def kicad_review_schematic() -> dict[str, Any]:
        """Design review of the SCHEMATIC's intended connectivity: annotation,
        single-pin nets, shorts, unconnected parts, missing decoupling, net
        classes -- from the netlist, independent of PCB layout. Complements
        review_design (which reviews the PCB copper)."""
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        if not os.path.exists(sch):
            return {"ok": False, "reason": f"no schematic found at {sch}"}
        try:
            from ..core.kicad_netlist import (get_schematic_netlist,
                                              snapshot_from_netlist)
            netlist = await get_schematic_netlist(cli, sch)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        from ..core.review_engine import review_snapshot
        snap = snapshot_from_netlist(netlist, board_name=os.path.basename(sch))
        return review_snapshot(snap)

    @mcp.tool()
    async def kicad_get_bom() -> dict[str, Any]:
        """Consolidated Bill of Materials from the schematic netlist: one line
        per value+footprint with quantity and the reference designators."""
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        if not os.path.exists(sch):
            return {"ok": False, "reason": f"no schematic found at {sch}"}
        try:
            from ..core.kicad_netlist import (get_schematic_netlist,
                                              bom_from_netlist)
            netlist = await get_schematic_netlist(cli, sch)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        lines = bom_from_netlist(netlist)
        return {"ok": True, "line_count": len(lines),
                "total_parts": sum(l["quantity"] for l in lines),
                "bom": lines}

    @mcp.tool()
    async def kicad_compare_sch_pcb() -> dict[str, Any]:
        """Compare the schematic netlist against the open PCB and report
        components and nets present in one but not the other (the schematic/PCB
        sync differences an ECO would resolve)."""
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
            parts, _pins, _u = br.component_pins()
            pcb_nets = {n["name"] for n in br.nets() if n.get("name")}
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        if not os.path.exists(sch):
            return {"ok": False, "reason": f"no schematic found at {sch}"}
        try:
            from ..core.kicad_netlist import (get_schematic_netlist,
                                              compare_schematic_to_pcb)
            netlist = await get_schematic_netlist(cli, sch)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        pcb_refs = {p["refdes"] for p in parts if p.get("refdes")}
        return {"ok": True,
                **compare_schematic_to_pcb(netlist, pcb_refs, pcb_nets)}

    @mcp.tool()
    async def kicad_get_board_summary() -> dict[str, Any]:
        """One-call board overview: object counts, board size (mm), layer
        count, distinct via sizes, and net/component counts."""
        try:
            br = get_kicad_bridge()
            stats = br.board_stats()
            outline = br.board_outline()
            vias = br.vias()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        except Exception as e:
            return {"ok": False, "reason": f"KiCad read failed: {e}"}
        via_sizes = len({(v.get("diameter_mm"), v.get("drill_mm"))
                         for v in vias})
        bbox = outline.get("bbox_mm") or {}
        area = None
        if bbox.get("w") and bbox.get("h"):
            area = round(bbox["w"] * bbox["h"], 2)
        return {"ok": True, "name": stats.get("name"),
                "footprints": stats.get("footprints"),
                "nets": stats.get("nets"), "pads": stats.get("pads"),
                "tracks": stats.get("tracks"), "vias": stats.get("vias"),
                "zones": stats.get("zones"),
                "stackup_layers": stats.get("stackup_layers"),
                "board_size_mm": {"w": bbox.get("w"), "h": bbox.get("h")},
                "board_bbox_area_mm2": area, "distinct_via_sizes": via_sizes}

    @mcp.tool()
    async def kicad_get_net_classes() -> dict[str, Any]:
        """Per-net net-class assignment and each class's routing rules
        (clearance, diff-pair gap/width, bus width) in mm."""
        return _read(lambda b: b.net_classes(), "net_classes")

    @mcp.tool()
    async def kicad_list_pads() -> dict[str, Any]:
        """All pads on the board: number, net, position (mm), pad type."""
        return _read(lambda b: b.pads(), "pads")

    # -- PCB exports (kicad-cli) --------------------------------------------
    @mcp.tool()
    async def kicad_export_gerbers(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Plot Gerber files for every board layer into a directory."""
        return await _pcb_export(
            output_dir, "gerbers",
            lambda b, o: (["pcb", "export", "gerbers", "--output", o, b], o))

    @mcp.tool()
    async def kicad_export_drill(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Generate Excellon drill files into a directory."""
        return await _pcb_export(
            output_dir, "drill",
            lambda b, o: (["pcb", "export", "drill", "--output",
                           o + os.sep, b], o))

    @mcp.tool()
    async def kicad_export_step(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the board as a STEP 3D model."""
        return await _pcb_export(
            output_dir, "step",
            lambda b, o: (["pcb", "export", "step", "--force", "--output",
                           os.path.join(o, _basename(b) + ".step"), b],
                          os.path.join(o, _basename(b) + ".step")))

    @mcp.tool()
    async def kicad_export_glb(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the board as a GLB (binary glTF) 3D model."""
        return await _pcb_export(
            output_dir, "glb",
            lambda b, o: (["pcb", "export", "glb", "--force", "--output",
                           os.path.join(o, _basename(b) + ".glb"), b],
                          os.path.join(o, _basename(b) + ".glb")))

    @mcp.tool()
    async def kicad_export_pdf(
            layers: str = "F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,Edge.Cuts",
            output_dir: Optional[str] = None) -> dict[str, Any]:
        """Plot the given board layers to a PDF (comma-separated layer names)."""
        return await _pcb_export(
            output_dir, "pdf",
            lambda b, o: (["pcb", "export", "pdf", "--layers", layers,
                           "--output", os.path.join(o, _basename(b) + ".pdf"), b],
                          os.path.join(o, _basename(b) + ".pdf")))

    @mcp.tool()
    async def kicad_export_pos(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Generate a component position (pick-and-place) file."""
        return await _pcb_export(
            output_dir, "pos",
            lambda b, o: (["pcb", "export", "pos", "--output",
                           os.path.join(o, _basename(b) + ".pos"), b],
                          os.path.join(o, _basename(b) + ".pos")))

    @mcp.tool()
    async def kicad_export_ipc2581(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the board in IPC-2581 format."""
        return await _pcb_export(
            output_dir, "ipc2581",
            lambda b, o: (["pcb", "export", "ipc2581", "--output",
                           os.path.join(o, _basename(b) + ".xml"), b],
                          os.path.join(o, _basename(b) + ".xml")))

    @mcp.tool()
    async def kicad_export_odb(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the board in ODB++ format."""
        return await _pcb_export(
            output_dir, "odb",
            lambda b, o: (["pcb", "export", "odb", "--output", o, b], o))

    @mcp.tool()
    async def kicad_export_dxf(
            layers: str = "F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,Edge.Cuts",
            output_dir: Optional[str] = None) -> dict[str, Any]:
        """Plot the given board layers to DXF (mechanical CAD) files."""
        return await _pcb_export(
            output_dir, "dxf",
            lambda b, o: (["pcb", "export", "dxf", "--layers", layers,
                           "--output", o, b], o))

    @mcp.tool()
    async def kicad_export_ipcd356(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Generate an IPC-D-356 netlist (bare-board electrical test)."""
        return await _pcb_export(
            output_dir, "ipcd356",
            lambda b, o: (["pcb", "export", "ipcd356", "--output",
                           os.path.join(o, _basename(b) + ".d356"), b],
                          os.path.join(o, _basename(b) + ".d356")))

    @mcp.tool()
    async def kicad_export_vrml(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the board as a VRML 3D model."""
        return await _pcb_export(
            output_dir, "vrml",
            lambda b, o: (["pcb", "export", "vrml", "--force", "--output",
                           os.path.join(o, _basename(b) + ".wrl"), b],
                          os.path.join(o, _basename(b) + ".wrl")))

    @mcp.tool()
    async def kicad_export_stl(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the board as an STL 3D model (3D printing / mechanical)."""
        return await _pcb_export(
            output_dir, "stl",
            lambda b, o: (["pcb", "export", "stl", "--force", "--output",
                           os.path.join(o, _basename(b) + ".stl"), b],
                          os.path.join(o, _basename(b) + ".stl")))

    @mcp.tool()
    async def kicad_export_3dpdf(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export an interactive 3D PDF of the board."""
        return await _pcb_export(
            output_dir, "3dpdf",
            lambda b, o: (["pcb", "export", "3dpdf", "--force", "--output",
                           os.path.join(o, _basename(b) + "_3d.pdf"), b],
                          os.path.join(o, _basename(b) + "_3d.pdf")))

    @mcp.tool()
    async def kicad_render_3d(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Render the board's 3D view to a PNG image."""
        return await _pcb_export(
            output_dir, "render",
            lambda b, o: (["pcb", "render", "--output",
                           os.path.join(o, _basename(b) + ".png"), b],
                          os.path.join(o, _basename(b) + ".png")))

    @mcp.tool()
    async def kicad_cli(args: list) -> dict[str, Any]:
        """Run an arbitrary kicad-cli command -- an escape hatch for anything
        not covered by a dedicated tool (e.g. pcb import, version formats).

        args is the argument list after the executable, e.g.
        ["version", "--format", "json"] or ["pcb", "import", ...]. Only
        kicad-cli's own subcommands run; it cannot invoke other programs."""
        try:
            cli = get_kicad_bridge().kicad_cli_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        try:
            result = await run_cli(cli, [str(a) for a in (args or [])])
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        ok = result["returncode"] == 0
        out: dict[str, Any] = {
            "ok": ok,
            "returncode": result["returncode"],
            "stdout": result.get("stdout", "")[:4000],
            "stderr": result.get("stderr", "")[:2000],
        }
        if not ok:
            # Every refusal carries a reason a caller can act on. The
            # stderr is still there in full; this names the failure
            # rather than leaving ok:False with nothing to read first.
            stderr_head = (result.get("stderr", "") or "").strip()
            stderr_head = stderr_head.splitlines()[0] if stderr_head else ""
            out["reason"] = (
                f"kicad-cli exited with code {result['returncode']}"
                + (f": {stderr_head[:200]}" if stderr_head else
                   "; it printed nothing on stderr"))
        return out

    @mcp.tool()
    async def kicad_run_jobset(jobset_path: str,
                               output: str = "") -> dict[str, Any]:
        """Run a KiCad jobset (a saved batch of output jobs -- KiCad's
        equivalent of an Altium OutJob) against the open project. Leave output
        blank to generate every output the jobset defines."""
        if not os.path.exists(jobset_path):
            return {"ok": False, "reason": f"jobset not found: {jobset_path}"}
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            board = br.board_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        project = os.path.splitext(board)[0] + ".kicad_pro"
        args = ["jobset", "run", "--file", jobset_path]
        if output:
            args += ["--output", output]
        args.append(project)
        try:
            result = await run_cli(cli, args)
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        ok = result["returncode"] == 0
        return {"ok": ok, "project": os.path.basename(project),
                "log": result.get("stdout", "")[:2000],
                "reason": None if ok else (result.get("stderr")
                          or result.get("stdout") or "jobset run failed")}

    @mcp.tool()
    async def kicad_export_pcb(fmt: str, layers: str = "",
                               output_dir: Optional[str] = None) -> dict[str, Any]:
        """Run any kicad-cli PCB export format not covered by a dedicated tool
        (e.g. gencad, brep, ply, xao, u3d, stpz, ps, hpgl, stats).

        fmt is the export format; directory-based formats (gerbers, drill, dxf,
        svg, odb) write into the output directory, others write a single file.
        Pass layers (comma-separated) for the layer-based formats."""
        f = fmt.strip().lower()

        def mk(b: str, o: str):
            args = ["pcb", "export", f]
            if f in _PCB_LAYER_FORMATS and layers:
                args += ["--layers", layers]
            if f in _PCB_FORCE_FORMATS:
                args += ["--force"]
            if f in _PCB_DIR_FORMATS:
                args += ["--output", o]
                produced = o
            else:
                produced = os.path.join(o, _basename(b) + "." +
                                        _PCB_EXT.get(f, f))
                args += ["--output", produced]
            args.append(b)
            return args, produced
        return await _pcb_export(output_dir, "pcb_" + f, mk)

    # -- schematic exports (kicad-cli) --------------------------------------
    @mcp.tool()
    async def kicad_export_bom(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Generate a Bill of Materials (CSV) from the schematic."""
        return await _sch_export(
            output_dir, "bom",
            lambda s, o: (["sch", "export", "bom", "--output",
                           os.path.join(o, _basename(s) + ".csv"), s],
                          os.path.join(o, _basename(s) + ".csv")))

    @mcp.tool()
    async def kicad_export_netlist(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export a netlist from the schematic."""
        return await _sch_export(
            output_dir, "netlist",
            lambda s, o: (["sch", "export", "netlist", "--output",
                           os.path.join(o, _basename(s) + ".net"), s],
                          os.path.join(o, _basename(s) + ".net")))

    @mcp.tool()
    async def kicad_run_spice() -> dict[str, Any]:
        """Run a SPICE simulation on the schematic via a standalone ngspice.

        Exports the schematic's SPICE netlist and runs it in ngspice batch
        mode. Requires ngspice on PATH -- KiCad bundles only ngspice.dll (used
        by its GUI simulator), not a runnable executable -- so this reports a
        clear reason when no standalone ngspice is installed."""
        import shutil
        import tempfile
        ng = shutil.which("ngspice")
        if not ng:
            return {"ok": False, "reason": "ngspice not found on PATH. Install "
                    "standalone ngspice to run simulations (KiCad bundles only "
                    "ngspice.dll, used by its GUI simulator)."}
        try:
            br = get_kicad_bridge()
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
        except KiCadNotReachableError as e:
            return {"ok": False, "reason": str(e)}
        fd, cir = tempfile.mkstemp(suffix=".cir", prefix="eda_spice_")
        os.close(fd)
        try:
            exp = await run_cli(cli, ["sch", "export", "netlist", "--format",
                                     "spice", "--output", cir, sch])
            if exp["returncode"] != 0:
                return {"ok": False, "reason": exp.get("stderr")
                        or "SPICE netlist export failed"}
            result = await run_cli(ng, ["-b", cir])
        except Exception as e:
            return {"ok": False, "reason": str(e)}
        finally:
            try:
                os.remove(cir)
            except OSError:
                pass
        reply = {"ok": result["returncode"] == 0,
                 "output": result.get("stdout", "")[:6000],
                 "errors": result.get("stderr", "")[:2000]}
        if not reply["ok"]:
            first = next((ln.strip() for ln in (result.get("stderr") or "").splitlines()
                          if ln.strip()), "")
            reply["reason"] = (f"ngspice exited with code {result['returncode']}"
                               + (f": {first[:200]}" if first else "")
                               + ". Its full output is in errors.")
        return reply

    @mcp.tool()
    async def kicad_export_spice(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export a SPICE netlist from the schematic (the input to an ngspice
        simulation; requires SPICE models/directives on the schematic)."""
        return await _sch_export(
            output_dir, "spice",
            lambda s, o: (["sch", "export", "netlist", "--format", "spice",
                           "--output", os.path.join(o, _basename(s) + ".cir"),
                           s], os.path.join(o, _basename(s) + ".cir")))

    @mcp.tool()
    async def kicad_export_schematic_pdf(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the schematic sheets to a PDF."""
        return await _sch_export(
            output_dir, "sch_pdf",
            lambda s, o: (["sch", "export", "pdf", "--output",
                           os.path.join(o, _basename(s) + ".pdf"), s],
                          os.path.join(o, _basename(s) + ".pdf")))

    @mcp.tool()
    async def kicad_export_schematic_svg(output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export the schematic sheets to SVG (one per sheet)."""
        return await _sch_export(
            output_dir, "sch_svg",
            lambda s, o: (["sch", "export", "svg", "--output", o, s], o))

    @mcp.tool()
    async def kicad_export_sch(fmt: str,
                               output_dir: Optional[str] = None) -> dict[str, Any]:
        """Run any kicad-cli schematic export format not covered by a dedicated
        tool (e.g. dxf, ps, hpgl, python-bom).

        fmt is the export format; multi-sheet formats (svg, dxf, ps, hpgl) write
        into the output directory, others write a single file."""
        f = fmt.strip().lower()

        def mk(s: str, o: str):
            args = ["sch", "export", f]
            if f in _SCH_DIR_FORMATS:
                args += ["--output", o]
                produced = o
            else:
                produced = os.path.join(o, _basename(s) + "." +
                                        _SCH_EXT.get(f, f))
                args += ["--output", produced]
            args.append(s)
            return args, produced
        return await _sch_export(output_dir, "sch_" + f, mk)

    # -- library utilities (kicad-cli, on a given library path) -------------
    @mcp.tool()
    async def kicad_export_footprint_svg(
            library_path: str, footprint: str = "",
            output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export a footprint (or a whole .pretty library) to SVG.

        library_path is a .pretty directory (or a single .kicad_mod file);
        footprint optionally names one footprint within the library."""
        def mk(lib: str, o: str):
            args = ["fp", "export", "svg", "--output", o]
            if footprint:
                args += ["--footprint", footprint]
            args.append(lib)
            return args, o
        return await _lib_export(library_path, output_dir, "fp_svg", mk)

    @mcp.tool()
    async def kicad_export_symbol_svg(
            library_path: str, symbol: str = "",
            output_dir: Optional[str] = None) -> dict[str, Any]:
        """Export a symbol (or a whole .kicad_sym library) to SVG.

        symbol optionally names one symbol within the library."""
        def mk(lib: str, o: str):
            args = ["sym", "export", "svg", "--output", o]
            if symbol:
                args += ["--symbol", symbol]
            args.append(lib)
            return args, o
        return await _lib_export(library_path, output_dir, "sym_svg", mk)

    @mcp.tool()
    async def kicad_generate_project(plan_json, output_dir: str,
                                     name: str = "design") -> dict[str, Any]:
        """Generate a complete, openable KiCad project from a DesignPlan:
        <name>.kicad_pro + <name>.kicad_sch + <name>.kicad_pcb in output_dir.
        The full design-execution deliverable (real symbols/footprints where
        resolvable, placement, net assignment, board outline). Parts given a
        placeholder footprint box are listed in ``placeholder_footprints``."""
        import json as _json
        import uuid as _uuid
        from pydantic import ValidationError
        from ..design.plan import DesignPlan
        from ..core.kicad_schematic import build_schematic
        from ..core.kicad_pcb import build_pcb
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = _json.loads(plan_json)
            except Exception as e:
                return {"ok": False, "reason": f"invalid JSON: {e}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}

        parts = [{"refdes": p.refdes, "value": p.value or ""}
                 for p in plan.parts]
        nets = [{"name": n.name,
                 "nodes": [{"refdes": pr.refdes, "pin": pr.pin}
                           for pr in n.pins]} for n in plan.nets]
        symbols, part_symbol, mod_texts, unread = _resolve_plan_libs(plan)
        title = (plan.spec or "")[:80]
        uid = lambda: str(_uuid.uuid4())

        os.makedirs(output_dir, exist_ok=True)
        sch_path = os.path.join(output_dir, f"{name}.kicad_sch")
        pcb_path = os.path.join(output_dir, f"{name}.kicad_pcb")
        pro_path = os.path.join(output_dir, f"{name}.kicad_pro")
        pro = {"meta": {"filename": f"{name}.kicad_pro", "version": 1},
               "board": {}, "boards": [], "cvpcb": {"equivalence_files": []},
               "erc": {}, "libraries": {"pinned_footprint_libs": [],
                                        "pinned_symbol_libs": []},
               "net_settings": {}, "pcbnew": {}, "schematic": {},
               "sheets": [], "text_variables": {}}
        placeholders: list[str] = []
        try:
            with open(sch_path, "w", encoding="utf-8") as fh:
                fh.write(build_schematic(parts, nets, uid=uid, title=title,
                                         symbols=symbols,
                                         part_symbol=part_symbol))
            with open(pcb_path, "w", encoding="utf-8") as fh:
                fh.write(build_pcb(parts, nets, uid=uid, title=title,
                                   mod_texts=mod_texts,
                                   placeholders=placeholders))
            with open(pro_path, "w", encoding="utf-8") as fh:
                _json.dump(pro, fh, indent=2)
        except OSError as e:
            return {"ok": False, "reason": str(e)}
        result: dict[str, Any] = {
            "ok": True, "project": pro_path, "schematic": sch_path,
            "board": pcb_path, "part_count": len(parts),
            "net_count": len(nets)}
        _report_placeholders(result, placeholders)
        _report_unread_libraries(result, unread)
        return result

    @mcp.tool()
    async def kicad_generate_schematic(plan_json, output_path: str,
                                       render: bool = False) -> dict[str, Any]:
        """Generate a KiCad schematic (.kicad_sch) from a DesignPlan: embeds a
        box symbol per part, places every part on a grid, and carries net
        connectivity via global labels. The KiCad counterpart of Altium's
        design-execution (schematic side); the output opens in Eeschema.
        Writes a new file. render=true validates it via kicad-cli."""
        import json as _json
        import uuid as _uuid
        from pydantic import ValidationError
        from ..design.plan import DesignPlan
        from ..core.kicad_schematic import build_schematic
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = _json.loads(plan_json)
            except Exception as e:
                return {"ok": False, "reason": f"invalid JSON: {e}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}
        parts = [{"refdes": p.refdes, "value": p.value or "",
                  "lib_ref": p.lib_ref} for p in plan.parts]
        nets = [{"name": n.name,
                 "nodes": [{"refdes": pr.refdes, "pin": pr.pin}
                           for pr in n.pins]} for n in plan.nets]
        # Real library symbols where they resolve, generated boxes elsewhere.
        symbols, part_symbol, _, unread = _resolve_plan_libs(plan)
        sch = build_schematic(parts, nets, uid=lambda: str(_uuid.uuid4()),
                              title=(plan.spec or "")[:80],
                              symbols=symbols, part_symbol=part_symbol)
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write(sch)
        except OSError as e:
            return {"ok": False, "reason": f"could not write {output_path}: {e}"}
        result: dict[str, Any] = {"ok": True, "path": output_path,
                                  "part_count": len(parts),
                                  "net_count": len(nets)}
        _report_unread_libraries(result, unread)
        if render:
            try:
                cli = get_kicad_bridge().kicad_cli_path()
                rr = await run_cli(cli, ["sch", "export", "svg", "--output",
                                        os.path.dirname(output_path) or ".",
                                        output_path])
                result["render_ok"] = rr["returncode"] == 0
                if rr["returncode"] != 0:
                    result["render_error"] = rr.get("stderr") or rr.get("stdout")
            except Exception as e:
                result["render_ok"] = False
                result["render_error"] = str(e)
        return result

    @mcp.tool()
    async def kicad_generate_pcb(plan_json, output_path: str,
                                 render: bool = False) -> dict[str, Any]:
        """Generate a KiCad board (.kicad_pcb) from a DesignPlan: declares the
        nets and places a footprint per part with pads assigned to those nets.
        The PCB side of design-execution; the output opens in pcbnew with the
        ratsnest ready to route. Writes a new file.

        A part whose footprint is not found in KiCad's libraries gets a
        generated placeholder box instead; those parts are listed in
        ``placeholder_footprints`` and named in ``warning``."""
        import json as _json
        import uuid as _uuid
        from pydantic import ValidationError
        from ..design.plan import DesignPlan
        from ..core.kicad_pcb import build_pcb
        if isinstance(plan_json, dict):
            payload = plan_json
        else:
            try:
                payload = _json.loads(plan_json)
            except Exception as e:
                return {"ok": False, "reason": f"invalid JSON: {e}"}
        try:
            plan = DesignPlan.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "errors": [
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors()]}
        parts = [{"refdes": p.refdes, "value": p.value or ""}
                 for p in plan.parts]
        nets = [{"name": n.name,
                 "nodes": [{"refdes": pr.refdes, "pin": pr.pin}
                           for pr in n.pins]} for n in plan.nets]
        # Real library footprints where they resolve, generated boxes elsewhere.
        _, _, mod_texts, unread = _resolve_plan_libs(plan)
        placeholders: list[str] = []
        pcb = build_pcb(parts, nets, uid=lambda: str(_uuid.uuid4()),
                        title=(plan.spec or "")[:80], mod_texts=mod_texts,
                        placeholders=placeholders)
        try:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write(pcb)
        except OSError as e:
            return {"ok": False, "reason": f"could not write {output_path}: {e}"}
        result: dict[str, Any] = {"ok": True, "path": output_path,
                                  "part_count": len(parts),
                                  "net_count": len(nets)}
        _report_placeholders(result, placeholders)
        _report_unread_libraries(result, unread)
        if render:
            try:
                cli = get_kicad_bridge().kicad_cli_path()
                rr = await run_cli(cli, ["pcb", "export", "svg", "--layers",
                                        "F.Cu,F.SilkS", "--output",
                                        output_path + ".svg", output_path])
                result["render_ok"] = rr["returncode"] == 0
                if rr["returncode"] != 0:
                    result["render_error"] = rr.get("stderr") or rr.get("stdout")
            except Exception as e:
                result["render_ok"] = False
                result["render_error"] = str(e)
        return result

    @mcp.tool()
    async def kicad_create_footprint(
            library_path: str, name: str, pads: list,
            descr: str = "", tags: str = "",
            render: bool = False) -> dict[str, Any]:
        """Create a footprint (.kicad_mod) in a .pretty library from a list of
        pads. Each pad: {number, x_mm, y_mm, w_mm, h_mm, shape (rect/roundrect/
        circle/oval), type (smd/thru_hole), drill_mm (thru_hole only)}.

        Writes a new file (does not touch the open board). Set render=true to
        validate it by exporting an SVG via kicad-cli."""
        from ..core.kicad_footprint import build_footprint
        if not name or not pads:
            return {"ok": False, "reason": "name and at least one pad required"}
        os.makedirs(library_path, exist_ok=True)
        content = build_footprint(name, list(pads), descr, tags)
        path = os.path.join(library_path, f"{name}.kicad_mod")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as e:
            return {"ok": False, "reason": f"could not write {path}: {e}"}
        result: dict[str, Any] = {"ok": True, "path": path,
                                  "pad_count": len(pads)}
        if render:
            try:
                cli = get_kicad_bridge().kicad_cli_path()
                rr = await run_cli(cli, ["fp", "export", "svg", "--footprint",
                                        name, "--output", library_path,
                                        library_path])
                result["render_ok"] = rr["returncode"] == 0
                if rr["returncode"] != 0:
                    result["render_error"] = rr.get("stderr") or rr.get("stdout")
            except Exception as e:
                result["render_ok"] = False
                result["render_error"] = str(e)
        return result

    @mcp.tool()
    async def kicad_create_footprint_from_package(
            library_path: str, package: Any, name: str = "",
            density: str = "", description: str = "", tags: str = "",
            drafting: Optional[dict] = None, overwrite: bool = False,
            render: bool = False) -> dict[str, Any]:
        """Create a COMPLETE .kicad_mod footprint from a package drawing.

        Written into a .pretty library. The lands come from IPC-7351B for the package's terminal
        dimensions and tolerances (the engine behind
        lib_create_footprint_from_package). Writes pads (roundrect with
        roundrect_rratio, circle, oval, rect; drills, oval slots,
        np_thru_hole), paste windows over an exposed pad as unnumbered
        F.Paste pads with the pad's own paste left out, silkscreen kept
        clear of copper with a pin 1 dot on F.SilkS, the body outline and
        ${REFERENCE} on F.Fab, and the courtyard on F.CrtYd. Origin at
        the package centroid; KiCad's Y axis points down, so Y is flipped.
        No 3D body: KiCad draws one only from a model file.

        The written file is parsed back and every pad compared with the
        model (count, centre, size, drill, slot); a difference makes ok
        false.

        Args:
            library_path: the .pretty directory (created if missing).
            package: the PackageSpec as an object or JSON text, in mm,
                the same schema as lib_create_footprint_from_package.
            name: footprint name; default the package's name.
            density: L, N or M; default the package's own (N).
            description: appended to the generated description.
            tags: KiCad keyword tags.
            drafting: drafting rule overrides.
            overwrite: replace an existing footprint of the same name.
                Off by default: a footprint in a library may already be
                on boards.
            render: export an SVG with kicad-cli to look at.
        """
        from ..core.kicad_footprint import (
            build_footprint_from_model,
            footprint_pads_mm,
            is_plain_name,
        )
        from ..design.landpattern.emit import model_from_package
        from ..design.landpattern.spec import SpecError

        if not library_path:
            return {"ok": False, "reason": "library_path is required"}
        try:
            model = model_from_package(package, density=density, name=name,
                                       description=description,
                                       drafting=drafting)
        except SpecError as e:
            return {"ok": False, "reason": str(e)}
        fp_name = model.name
        if not is_plain_name(fp_name):
            return {"ok": False, "reason": (
                f"footprint name {fp_name!r} cannot be a file name (it holds "
                "a path separator, a colon or nothing); pass name")}
        path = os.path.join(library_path, f"{fp_name}.kicad_mod")
        if os.path.exists(path) and not overwrite:
            return {"ok": False, "reason": (
                f"{path} already exists; pass overwrite=true to replace it "
                "or another name")}
        try:
            content = build_footprint_from_model(model, tags=tags)
        except ValueError as e:
            return {"ok": False, "reason": str(e)}
        try:
            os.makedirs(library_path, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as e:
            return {"ok": False, "reason": f"could not write {path}: {e}"}

        # Parse the file back and hold it to the model.
        mismatches: list[dict] = []
        read = [p for p in footprint_pads_mm(content) if p["number"] != ""]
        windows = [p for p in footprint_pads_mm(content) if p["number"] == ""]
        if len(read) != len(model.pads):
            mismatches.append({"check": "pad_count",
                               "expected": len(model.pads), "read": len(read)})
        for want, got in zip(model.pads, read):
            if want.number != got["number"]:
                mismatches.append({"check": "number", "expected": want.number,
                                   "read": got["number"]})
            for key, a, b in (("x", want.x, got["x"]), ("y", want.y, got["y"]),
                              ("w", want.width, got["w"]),
                              ("h", want.height, got["h"]),
                              ("drill", want.hole if want.kind != "smd" else 0.0,
                               got["drill"]),
                              ("slot", want.hole_length
                               if want.kind != "smd"
                               and want.hole_length > want.hole else 0.0,
                               got["slot"])):
                if abs(float(a) - float(b)) > 1e-6:
                    mismatches.append({"check": key, "pad": want.number,
                                       "expected": a, "read": b})
        want_windows = sum(len(p.paste_apertures) for p in model.pads)
        if len(windows) != want_windows:
            mismatches.append({"check": "paste_windows",
                               "expected": want_windows, "read": len(windows)})
        result: dict[str, Any] = {
            "ok": not mismatches, "path": path, "name": fp_name,
            "family": model.family, "density": model.density,
            "description": model.description, "pad_count": len(read),
            "paste_windows": len(windows),
            "layers": {"silk": "F.SilkS", "assembly": "F.Fab",
                       "courtyard": "F.CrtYd", "paste": "F.Paste"},
            "readback": {"ok": not mismatches, "mismatches": mismatches},
            "courtyard_mm": list(model.courtyard_bbox()),
            "warnings": model.warnings,
            "notes": ["3D body: not written; KiCad draws one only from a "
                      "model file",
                      "mask and paste margins: written only when non-zero, "
                      "so a 1:1 opening follows the board's own setting"],
        }
        if render:
            try:
                cli = get_kicad_bridge().kicad_cli_path()
                rr = await run_cli(cli, ["fp", "export", "svg", "--footprint",
                                        fp_name, "--output", library_path,
                                        library_path])
                result["render_ok"] = rr["returncode"] == 0
                if rr["returncode"] != 0:
                    result["render_error"] = rr.get("stderr") or rr.get("stdout")
            except Exception as e:
                result["render_ok"] = False
                result["render_error"] = str(e)
        return result

    @mcp.tool()
    async def kicad_upgrade_footprint_library(
            library_path: str, output_dir: Optional[str] = None) -> dict[str, Any]:
        """Upgrade a footprint (.pretty) library to the current KiCad format."""
        return await _lib_export(
            library_path, output_dir, "fp_upgrade",
            lambda lib, o: (["fp", "upgrade", "--force", "--output", o, lib], o))

    @mcp.tool()
    async def kicad_create_symbol(
            library_path: str, name: str, pins: list,
            reference: str = "U", body_w_mm: float = 10.16,
            body_h_mm: float = 7.62, render: bool = False) -> dict[str, Any]:
        """Create a schematic symbol in a .kicad_sym library. Each pin:
        {number, name, x_mm, y_mm, angle (0/90/180/270), type (input/output/
        bidirectional/passive/power_in/...), length}. If the library file
        exists the symbol is inserted into it, otherwise a new library is
        created. Writes a file (does not touch the open design). render=true
        validates it via kicad-cli."""
        from ..core.kicad_symbol import build_symbol_lib, insert_symbol
        if not name or not pins:
            return {"ok": False, "reason": "name and at least one pin required"}
        try:
            if os.path.exists(library_path):
                with open(library_path, "r", encoding="utf-8") as fh:
                    content = insert_symbol(fh.read(), name, list(pins),
                                            reference, body_w_mm, body_h_mm)
            else:
                os.makedirs(os.path.dirname(library_path) or ".", exist_ok=True)
                content = build_symbol_lib(name, list(pins), reference,
                                           body_w_mm, body_h_mm)
            with open(library_path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as e:
            return {"ok": False, "reason": f"could not write "
                    f"{library_path}: {e}"}
        result: dict[str, Any] = {"ok": True, "path": library_path,
                                  "pin_count": len(pins)}
        if render:
            try:
                cli = get_kicad_bridge().kicad_cli_path()
                outdir = os.path.dirname(library_path) or "."
                rr = await run_cli(cli, ["sym", "export", "svg", "--symbol",
                                        name, "--output", outdir, library_path])
                result["render_ok"] = rr["returncode"] == 0
                if rr["returncode"] != 0:
                    result["render_error"] = rr.get("stderr") or rr.get("stdout")
            except Exception as e:
                result["render_ok"] = False
                result["render_error"] = str(e)
        return result

    @mcp.tool()
    async def kicad_upgrade_symbol_library(
            library_path: str, output_dir: Optional[str] = None) -> dict[str, Any]:
        """Upgrade a symbol (.kicad_sym) library to the current KiCad format."""
        return await _lib_export(
            library_path, output_dir, "sym_upgrade",
            lambda lib, o: (["sym", "upgrade", "--force", "--output", o, lib], o))
