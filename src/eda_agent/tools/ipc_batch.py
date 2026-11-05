"""Native IPC batch generation through RunProcess, without desktop input.

AD 24.0.1 FootprintWizard.ins declares BatchPCBLibCreate. The DLL's
FileName/OutputFolder process entry was exercised against a QFN CSV.
Other native batch preferences are intentionally not advertised as
working process parameters: output inspection, not dispatch, proves success.
"""
from __future__ import annotations

import asyncio
import csv
import json
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


def _check_csv(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if len(rows) < 3 or not rows[0] or rows[0][0] not in {"QFN", "QFP", "SOP"}:
        raise ValueError("CSV needs native QFN/QFP/SOP type in A1, headers in row 2 and data")
    columns = len(rows[1])
    if "FootprintName" not in rows[1] or "DensityLevel" not in rows[1]:
        raise ValueError("CSV is not a native IPC batch template")
    data = [row for row in rows[2:] if any(cell.strip() for cell in row)]
    if not data or any(len(row) != columns for row in data):
        raise ValueError("CSV has no data or inconsistent column counts")
    density = rows[1].index("DensityLevel")
    if any(row[density] not in {"L", "N", "M"} for row in data):
        raise ValueError("Each DensityLevel must be L, N or M")
    return len(data)


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
                             remove_designator: bool = True,
                             timeout: float = 120.0) -> dict:
    """Run the native importer; only verified generated libraries count."""
    try:
        source = _path(input_file, "input_file")
        out = _path(output_dir, "output_dir")
        if not source.is_file() or source.suffix.lower() not in {".csv", ".xls", ".xlsx"}:
            raise ValueError("input_file must be an existing CSV/XLS/XLSX file")
        if not 10 <= timeout <= 600:
            raise ValueError("timeout must be between 10 and 600 seconds")
        expected = _check_csv(source) if source.suffix.lower() == ".csv" else None
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
    (out / "mcp-ipc-batch-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def register_ipc_batch_tools(mcp):
    @mcp.tool()
    async def lib_generate_ipc_batch(input_file: str, output_dir: str,
                                     require_step: bool = True,
                                     remove_designator: bool = True,
                                     timeout: float = 120.0) -> dict:
        """Import a native IPC batch table through AD's own generator, without desktop input.

        Runs FootprintWizard:BatchPCBLibCreate with FileName, OutputFolder,
        and EnableStepModel=True when require_step=True.
        Requires an absolute CSV/XLS/XLSX path and a NEW or EMPTY output folder.
        Reads every generated footprint, checks the CSV row count, rejects native
        report errors, removes Designator text (preserving unrelated text), saves,
        and extracts/checksums embedded STEP models when require_step=True.
        AD's saved native batch preferences control naming/report options;
        require_step requests and verifies STEP, never substitutes extruded bodies.
        No mouse, keyboard, menu clicking, UI Automation, or dialog driving.
        Returns ok=False if dispatch produces no files or any required check fails.
        """
        return await generate_ipc_batch(input_file, output_dir,
            require_step=require_step, remove_designator=remove_designator, timeout=timeout)
