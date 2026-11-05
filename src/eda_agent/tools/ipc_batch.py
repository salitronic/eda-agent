# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
# Copyright (c) 2026 StarX-ai
"""Footprints from an IPC batch table, through Altium's own generator.

FootprintWizard:BatchPCBLibCreate takes FileName, OutputFolder and
EnableStepModel. Success is what the output folder and the libraries in
it hold when read back, never the dispatch alone.
"""
from __future__ import annotations

import asyncio
import csv
import re
from html import unescape
from pathlib import Path

from ..bridge import get_bridge
from ..fileio import pcblib_models

PROCESS = "FootprintWizard:BatchPCBLibCreate"


def _path(value: str, label: str) -> Path:
    if not value or any(c in value for c in "|\r\n\0"):
        raise ValueError(f"{label} is empty or contains a process delimiter")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path.resolve()


def _table_rows(path: Path) -> list[list[str]] | None:
    """The table's cells as text: a CSV, or an .xls's Data sheet; None
    for a format read only by Altium (.xlsx)."""
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [[c.strip() for c in row] for row in csv.reader(handle)]
    if path.suffix.lower() == ".xls":
        import xlrd
        try:
            book = xlrd.open_workbook(str(path))
        except Exception as exc:                          # noqa: BLE001
            raise ValueError(f"{path.name} could not be read as .xls: {exc}") from None
        sheet = book.sheet_by_name("Data") if "Data" in book.sheet_names() \
            else book.sheet_by_index(0)
        text = lambda v: (str(int(v)) if v.is_integer() else repr(v)) \
            if isinstance(v, float) else str(v).strip()  # noqa: E731
        return [[text(c.value) for c in sheet.row(r)] for r in range(sheet.nrows)]
    return None


def _check_table(path: Path) -> int | None:
    """The table's footprint count, after checking it against its
    family's installed template; ValueError names every problem."""
    from ..design import ipc_templates
    rows = _table_rows(path)
    if rows is None:
        return None
    folders = ipc_templates.template_folders()
    known: set[str] = set()
    template = None
    if folders:
        known = {f["family"] for f in ipc_templates.families(folders[0]) if f.get("family")}
        if rows and rows[0] and rows[0][0].strip():
            template = ipc_templates.find_template(folders[0], rows[0][0])
            if template and template["family"].lower() != rows[0][0].strip().lower():
                template = None
    problems, count = ipc_templates.table_problems(rows, template, known)
    if problems:
        raise ValueError("; ".join(problems))
    return count


def _report(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8", errors="replace")
    rows = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", raw, re.I | re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)
        rows.append([unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in cells])
    totals = [r for r in rows if len(r) == 4 and all(re.fullmatch(r"\d+", c) for c in r[1:])]
    return {"path": str(path), "fatal_errors": sum(int(r[1]) for r in totals),
            "errors": sum(int(r[2]) for r in totals),
            "warnings": sum(int(r[3]) for r in totals),
            "counts_verified": bool(totals),
            "messages": [r for r in rows if len(r) == 3]}


async def generate_ipc_batch(input_file: str, output_dir: str, *,
                             require_step: bool = True,
                             remove_designator: bool = False,
                             timeout: float = 120.0) -> dict:
    """Run the native importer; only verified generated libraries count."""
    try:
        source = _path(input_file, "input_file")
        out = _path(output_dir, "output_dir")
        if not source.is_file() or source.suffix.lower() not in {".csv", ".xls", ".xlsx"}:
            raise ValueError("input_file must be an existing CSV/XLS/XLSX file")
        if not 10 <= timeout <= 600:
            raise ValueError("timeout must be between 10 and 600 seconds")
        expected = _check_table(source)
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise ValueError("output_dir must be new or empty; existing outputs are never overwritten")
        out.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError) as exc:
        return {"ok": False, "stage": "preflight", "reason": str(exc), "ui_automation": False}

    result = {"ok": False, "process": PROCESS, "input_file": str(source),
              "output_dir": str(out), "ui_automation": False,
              "expected_footprints": expected, "libraries": []}
    bridge = get_bridge()
    # These keys are tested process inputs. Omitting EnableStepModel
    # produces a 2D-only library even when the GUI preference is enabled.
    params = f"FileName={source}|OutputFolder={out}"
    if require_step:
        params += "|EnableStepModel=True"
    try:
        dispatch = await bridge.send_command_async("generic.run_process",
            {"process": PROCESS, "params": params}, timeout=timeout)
        result["dispatch"] = dispatch
        if isinstance(dispatch, dict) and (dispatch.get("error") or dispatch.get("success") is False):
            return {**result, "stage": "dispatch", "reason": "AD refused the process"}
        # The process normally returns after writing. A short bounded
        # filesystem wait handles flush latency without driving dialogs.
        for _ in range(10):
            paths = sorted(out.glob("*.PcbLib"))
            if paths:
                break
            await asyncio.sleep(0.2)
        if not paths:
            return {**result, "stage": "output", "reason": "No PcbLib was generated; process dispatch alone is not success"}
        result["reports"] = [_report(p) for p in sorted(out.glob("*.html"))]
        if any(r["errors"] or r["fatal_errors"] for r in result["reports"]):
            return {**result, "stage": "native_report", "reason": "The native generator reported errors"}
        count = 0
        for path in paths:
            library = {"path": str(path), "footprints": []}
            result["libraries"].append(library)
            listing = await bridge.send_command_async("library.get_footprints", {"library_path": str(path)})
            footprints = listing.get("footprints", [])
            if not footprints:
                return {**result, "stage": "readback", "reason": "Generated library contains no footprints"}
            for fp in footprints:
                scope = {"library_path": str(path), "footprint_name": fp["name"]}
                read = await bridge.send_command_async("library.get_footprint_primitives", scope)
                if read.get("error"):
                    return {**result, "stage": "readback", "reason": str(read["error"])}
                texts = read.get("texts", [])
                unwanted = [t for t in texts if str(t.get("text", "")).strip().casefold() in {".designator", "designator"}]
                if remove_designator and unwanted:
                    for layer in {t["layer"] for t in unwanted}:
                        on_layer = [t for t in texts if t.get("layer") == layer]
                        if any(t not in unwanted for t in on_layer):
                            return {**result, "stage": "designator", "reason": "Designator shares a text layer; selective removal is unavailable, other text preserved"}
                        deletion = await bridge.send_command_async("library.delete_footprint_primitives",
                            {**scope, "object_type": "text", "layer": layer, "confirm": "true"})
                        if deletion.get("leftover") or deletion.get("error"):
                            return {**result, "stage": "designator", "reason": "Designator removal failed"}
                    read = await bridge.send_command_async("library.get_footprint_primitives", scope)
                remains = [t for t in read.get("texts", []) if str(t.get("text", "")).strip().casefold() in {".designator", "designator"}]
                models = [m for m in read.get("body_models", []) if m.get("has_model") and m.get("model_type") == 1]
                pads = await bridge.send_command_async("library.get_pad_geometry", scope)
                if not pads.get("pad_count"):
                    return {**result, "stage": "readback", "reason": "Generated footprint has no pads"}
                library["footprints"].append({"name": fp["name"], "pads": pads["pad_count"],
                    "step_bodies": len(models), "designator_absent": not remains})
                if remove_designator and remains:
                    return {**result, "stage": "designator", "reason": "Designator still present after removal"}
                if require_step and not models:
                    return {**result, "stage": "3d", "reason": "Native batch output has no STEP body despite EnableStepModel=True; no replacement geometry was substituted"}
                count += 1
            saved = await bridge.send_command_async("application.save_document", {"file_path": str(path)}, timeout=60.0)
            if not saved.get("saved"):
                return {**result, "stage": "save", "reason": "AD did not confirm saving the generated library"}
            if require_step:
                extracted = await asyncio.to_thread(pcblib_models.extract_embedded_models,
                    path, out / "STEP" / path.stem, per_record_directory=True)
                library["step_export"] = extracted
                if not extracted.get("ok") or any(not m.get("step_header") for m in extracted.get("written", [])):
                    return {**result, "stage": "3d_export", "reason": "Embedded STEP extraction or checksum verification failed"}
        if expected is not None and count != expected:
            return {**result, "stage": "count", "reason": f"Expected {expected} footprints, read {count}"}
        result.update(ok=True, stage="complete", footprint_count=count,
            native_preferences="AD's saved batch naming/report preferences apply; EnableStepModel=True requests STEP and its presence is verified")
    except Exception as exc:
        result.update(stage="exception", reason=f"{type(exc).__name__}: {exc}")
    return result


def register_ipc_batch_tools(mcp):
    @mcp.tool()
    async def lib_generate_ipc_batch(input_file: str, output_dir: str,
                                     require_step: bool = True,
                                     remove_designator: bool = False,
                                     timeout: float = 120.0) -> dict:
        """Make PcbLibs from an IPC footprint batch table with Altium's
        own batch generator, for any package family it has a template
        for (lib_ipc_batch_template gives the columns).

        A .csv or .xls table is checked against its family's template
        first: the family in A1, every column name, the required package
        columns filled. Every footprint is then read back: pad count, the
        table's row count, errors in Altium's report, and with
        require_step its STEP body, extracted and checksummed. Naming
        follows Altium's saved batch preferences.

        Args:
            input_file: Absolute path of the .csv, .xls or .xlsx table.
            output_dir: A new or empty folder; nothing is overwritten.
            require_step: Ask for STEP bodies and fail without them.
            remove_designator: Delete the Designator text, refused when
                other text shares its layer.
            timeout: Seconds, 10 to 600.

        Returns:
            ``ok``, ``stage`` (where it stopped), ``libraries`` with each
            footprint, and the ``reports``.
        """
        return await generate_ipc_batch(input_file, output_dir,
            require_step=require_step, remove_designator=remove_designator, timeout=timeout)

    @mcp.tool()
    async def lib_ipc_batch_template(family: str = "") -> dict:
        """Altium's template for an IPC footprint batch table, the input
        of lib_generate_ipc_batch.

        With no family it lists the families installed. With one (its
        code, e.g. "QFN", "QFP", "SOT23", or the start of the template's
        file name) it gives the template's path, the ``columns`` in order
        (row 2 of the table, the family code going in A1), the
        ``required`` package columns and the ``legend``: what each column
        measures, its format and whether it is required. Fill rows from
        the part's datasheet package drawing, in mm.

        Returns:
            ``folder`` and ``families``, or ``family``, ``file``,
            ``columns``, ``required`` and ``legend``.
        """
        from ..design import ipc_templates
        folders = ipc_templates.template_folders()
        if not folders:
            return {"ok": False, "reason": "no IPC Footprint Generator templates are "
                    "installed under ProgramData\Altium; the IPC Footprint Generator "
                    "extension is part of Altium Designer's installation"}
        if not family.strip():
            return {"ok": True, "folder": str(folders[0]),
                    "families": ipc_templates.families(folders[0])}
        template = ipc_templates.find_template(folders[0], family)
        if template is None:
            known = [f["family"] for f in ipc_templates.families(folders[0])]
            return {"ok": False, "reason": f"no template for {family!r}",
                    "families": known}
        return {"ok": True, **template,
                "required": ipc_templates.required_columns(template)}
