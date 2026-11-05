# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""SPICE simulation tools.

The simulation flow is three steps:

1. ``sim_get_readiness()`` audits every component on the
   active schematic and returns three buckets:

     - ``ready``          , has SpicePrefix; nothing to do.
     - ``needs_primitive``, R/L/C/V/I where just setting the prefix +
                             value makes it simulatable (no model file).
     - ``needs_file``     , an IC or active part that requires a
                             vendor SPICE model (.mdl / .ckt / .lib).

   The response also carries ``_spice_guidance`` with the hard rule:
   **vendor datasheets + vendor-published SPICE models only**. LLM-
   generated SPICE models are not trustworthy and must not be used.

2. ``sim_attach_primitives()``, for ``needs_primitive`` entries.
   One bulk call attaches the whole list; no file needed.

3. For ``needs_file`` entries: fetch the vendor model, save it locally,
   call ``sim_attach_model()`` pointing at that file.

Then ``sim_setup_analysis()`` sets the transient, AC and DC values,
``sim_run()`` runs Altium's mixed-signal simulator and returns each
wave's figures, and ``sim_get_results()`` returns the traces.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..bridge import get_bridge
from ..sim.sdf import SdfError, latest_charts, read_sdf, summarise
from ..sim.simcfg import (ANALYSES, DEFAULT_NAME, SimCfgError, analysis_changes,
                          apply, describe, new_simcfg, only_analyses, read_simcfg,
                          write_simcfg)
from .bulk_hints import BulkHintTracker

#: The results document the last sim_run produced, so sim_get_results can
#: be called without repeating its path.
_last_results: dict[str, str] = {}


def _results(charts, waves=None, max_points=0, as_list=False):
    """Charts as plain data, optionally filtered to some waves."""
    keep = {w.strip().lower() for w in (waves or []) if w.strip()}
    out = []
    for chart in charts:
        rows = []
        for wave in chart.waves:
            if keep and wave.name.lower() not in keep:
                continue
            summary = summarise(wave, max_points=max_points)
            if max_points <= 0:
                summary.pop("samples", None)
                summary.pop("sample_columns", None)
            rows.append(summary)
        out.append({"analysis": chart.name, "waves": rows})
    if as_list:
        return out
    return {entry["analysis"]: entry["waves"] for entry in out}


SPICE_MODEL_RULES = [
    "SPICE models for ICs and active parts must come from the "
    "manufacturer's own product page or a trusted aggregator "
    "(SnapEDA, Ultra Librarian, Digi-Key model archive). Do NOT "
    "generate a .mdl or .ckt file from datasheet reasoning, the "
    "parameters that matter for convergence and small-signal "
    "behavior are not in any datasheet summary.",
    "Prefer the manufacturer page first. Most vendors host the model "
    "under '<product page> -> Design Resources -> Models' or a linked "
    "ZIP archive. Typical search: "
    "'<manufacturer> <part_number> SPICE model filetype:lib OR "
    "filetype:mdl OR filetype:cir'.",
    "A model file usually contains a .SUBCKT <name> declaration. The "
    "<name> is what goes into sim_attach_model's model_name "
    "parameter, not the file name.",
    "If a vendor SPICE model cannot be located after a real search, "
    "tell the user. Do not fabricate a substitute; running the sim "
    "with a made-up model gives confidently-wrong results.",
    "For passives (R, L, C) and ideal sources (V, I), no file is "
    "needed, sim_attach_primitives sets SpicePrefix + Value "
    "directly.",
]


def _suggest_vendor_search(entry: dict[str, Any]) -> dict[str, Any]:
    """Build a suggested-search hint from a needs_file entry."""
    mfr = str(entry.get("manufacturer") or "").strip()
    part = str(entry.get("manufacturer_part") or "").strip()
    comment = str(entry.get("comment") or "").strip()
    lib_ref = str(entry.get("lib_ref") or "").strip()

    search_part = part or comment or lib_ref
    search_mfr = mfr

    hints: dict[str, Any] = {
        "designator": entry.get("designator"),
        "search_part": search_part,
        "manufacturer": search_mfr,
    }
    if search_part:
        query_terms = [search_mfr, search_part] if search_mfr else [search_part]
        query = " ".join(query_terms).strip()
        hints["web_search_query"] = (
            f"{query} SPICE model filetype:lib OR filetype:mdl "
            f"OR filetype:cir OR filetype:sub"
        )
        hints["datasheet_query"] = f"{query} datasheet filetype:pdf"
    return hints


def _guidance_block(
    needs_file: list[dict[str, Any]],
    needs_primitive: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "spice_model_rules": SPICE_MODEL_RULES,
        "action_required": (
            "Pass all needs_primitive entries to "
            "sim_attach_primitives in one bulk call. For every entry in "
            "needs_file, fetch the vendor SPICE model (WebFetch / "
            "WebSearch using the suggested query below), save it to "
            "a local file, then call sim_attach_model with the "
            ".SUBCKT name from inside the file. Do NOT fabricate a "
            "SPICE model from datasheet reasoning, if the vendor "
            "doesn't publish one, flag it to the user and stop."
        ),
        "search_hints": [
            _suggest_vendor_search(e) for e in needs_file
        ],
        "primitive_hints": [
            {
                "designator": e.get("designator"),
                "suggested_prefix": e.get("suggested_prefix"),
                "suggested_value": e.get("suggested_value"),
            }
            for e in needs_primitive
        ],
    }


#: Chart names in a results file, by the analysis key sim_run takes.
_CHART_KEYS = {"operating point": "op", "dc sweep": "dc",
               "transient analysis": "transient", "ac analysis": "ac"}


async def _chart_count(bridge) -> dict[str, int]:
    """Charts already in the focused project's results file, by its path.

    Altium writes a schematic run's results to Simulation\\<project>.sdf
    beside the project, adding a chart per analysis per run.
    """
    try:
        focused = await bridge.send_command_async("project.get_focused", {})
    except Exception:  # noqa: BLE001 - the count is a nicety, the run decides
        return {}
    project = (focused or {}).get("project_path", "") if isinstance(focused, dict) else ""
    if not project:
        return {}
    sdf = Path(project).parent / "Simulation" / (Path(project).stem + ".sdf")
    try:
        return {str(sdf).lower(): len(read_sdf(sdf))} if sdf.exists() else {}
    except (OSError, SdfError):
        return {}


async def _project_simcfg(bridge, project_path: str) -> dict[str, Any]:
    """The project (focused when not given), its documents and its .simcfg."""
    if not project_path:
        focused = await bridge.send_command_async("project.get_focused", {})
        project_path = (focused or {}).get("project_path", "") \
            if isinstance(focused, dict) else ""
    if not project_path or not project_path.lower().endswith(".prjpcb"):
        return {"success": False, "error": "NO_PROJECT",
                "message": "no PCB project is focused; pass project_path"}
    docs = await bridge.send_command_async(
        "project.get_documents", {"project_path": project_path})
    if isinstance(docs, dict):
        docs = docs.get("result") or docs.get("documents") or []
    simcfg = next((d.get("file_path") for d in docs or []
                   if str(d.get("document_kind", "")).lower() == "simcfg"
                   or str(d.get("file_path", "")).lower().endswith(".simcfg")), "")
    return {"success": True, "project_path": project_path, "docs": docs or [],
            "simcfg": simcfg}


async def _change_simcfg(bridge, project_path: str, changes: dict) -> dict[str, Any]:
    """Write ``changes`` into the project's .simcfg and have Altium reload it."""
    found = await _project_simcfg(bridge, project_path)
    if not found.get("success"):
        return found
    project_path, docs, simcfg = found["project_path"], found["docs"], found["simcfg"]

    created = False
    if simcfg:
        saved = await bridge.send_command_async(
            "generic.simcfg_document", {"action": "save", "file_path": simcfg})
        if isinstance(saved, dict) and saved.get("success") is False:
            return {"success": False, "error": "SIMCFG_NOT_SAVED",
                    "message": "Altium's settings could not be saved first, "
                               "so changing the file would lose them",
                    "detail": saved}
        cfg = read_simcfg(simcfg)
    else:
        simcfg = str(Path(project_path).parent / DEFAULT_NAME)
        cfg = read_simcfg(simcfg) if Path(simcfg).exists() else new_simcfg()
        created = not Path(simcfg).exists()

    from ..saving import refused, saving_off
    if saving_off():
        return refused(f"Changing the simulation settings writes {simcfg}.")
    try:
        write_simcfg(apply(cfg, changes), simcfg)
    except OSError as exc:
        return {"success": False, "error": "NOT_WRITTEN",
                "message": f"cannot write {simcfg}: {exc}"}

    if created or not any(str(d.get("file_path", "")).lower() == simcfg.lower()
                          for d in docs):
        added = await bridge.send_command_async(
            "project.add_document",
            {"document_path": simcfg, "project_path": project_path})
        if isinstance(added, dict) and added.get("success") is False:
            return {"success": False, "error": "NOT_ADDED", "simcfg": simcfg,
                    "message": "the settings file was written but could not "
                               "be added to the project", "detail": added}
    reload = await bridge.send_command_async(
        "generic.simcfg_document", {"action": "reload", "file_path": simcfg})
    reloaded = bool(isinstance(reload, dict) and reload.get("done"))
    if isinstance(reload, dict) and reload.get("success") is False:
        return {"success": False, "error": "NOT_RELOADED", "simcfg": simcfg,
                "message": "the file was changed but Altium did not reload "
                           "it, so the next run would use its old settings",
                "detail": reload}
    return {"success": True, "simcfg": simcfg, "created": created,
            "reloaded": reloaded, "set": changes}


def register_sim_tools(mcp):
    """Register SPICE / simulation tools with the MCP server."""

    @mcp.tool()
    async def sim_get_readiness() -> dict[str, Any]:
        """Audit every component on the active schematic for SPICE readiness.

        Step 1 of any simulation. Each component lands in one bucket:
        ``ready`` (SpicePrefix set AND a SIM model linked, which is what
        Altium's netlister reads), ``no_sim_model`` (SpicePrefix written
        but no model link: the netlister reports "Missing component
        models" for these; sim_run_deck runs a plain SPICE deck without
        them), ``needs_primitive`` (R/L/C or a
        V/I/D/Q source needing SpicePrefix and Value; pass them all to
        sim_attach_primitives in one call) or ``needs_file`` (an IC or
        active part needing the vendor's .mdl/.ckt/.lib, for
        sim_attach_model).

        VENDOR SPICE MODELS ONLY. Never build a model from a datasheet:
        the parameters that set convergence and bias are not in it, and
        a plausible hand-made model gives confidently wrong results.
        ``_spice_guidance`` repeats this and gives per-part search
        queries.

        Returns:
            Dict with:
              - ready, ready_count (each with sim_model)
              - no_sim_model, no_sim_model_count
              - needs_primitive, needs_primitive_count
                (each entry has designator, comment, suggested_prefix,
                 suggested_value)
              - needs_file, needs_file_count
                (each entry has designator, comment, lib_ref,
                 manufacturer, manufacturer_part)
              - _spice_guidance (rules + structured search_hints +
                primitive_hints)
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "generic.get_simulation_readiness", {}
        )
        if isinstance(result, dict):
            result["_spice_guidance"] = _guidance_block(
                result.get("needs_file") or [],
                result.get("needs_primitive") or [],
            )
        return result

    @mcp.tool()
    async def sim_attach_model(
        designator: str,
        file_path: str,
        model_name: str,
        primitive: str = "X",
    ) -> dict[str, Any]:
        """Attach an external SPICE model file (.mdl / .ckt / .lib) to a component.

        Use this after fetching a vendor SPICE model file from the
        manufacturer and saving it locally. The model_name parameter
        must be the exact ``.SUBCKT <name>`` declaration from inside
        the file, not the file name.

        Args:
            designator: Component reference (e.g. "U1").
            file_path: Full path to the locally-saved .mdl / .ckt / .lib.
            model_name: Subcircuit / model name declared inside the
                file. Open the file and look for ``.SUBCKT <name>``
                or ``.MODEL <name>``.
            primitive: SPICE prefix letter. Default "X" (subcircuit).
                Use "D"/"Q"/"M" when the file declares a .MODEL of
                that kind instead of a subcircuit.

        Returns:
            Dict with success, designator, file_path, model_name.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "generic.attach_spice_model",
            {
                "designator": designator,
                "file_path": file_path,
                "model_name": model_name,
                "primitive": primitive,
            },
        )

    @mcp.tool()
    async def sim_run(
        analysis: str = "",
        analyses: list[str] | None = None,
        sheet: str = "",
    ) -> dict[str, Any]:
        """Run Altium's mixed-signal simulation of the focused project.

        Runs the sheet's setup the way the Simulation Dashboard's Run
        does (set with sim_setup_analysis, or in the dashboard), and
        reports in ``ran_analyses`` which analyses this run produced,
        read back from the results.

        MEASURED on AD 26.11: Altium takes the on/off switches from the
        setup at the first run after the project opens and keeps that
        selection until the project is reopened; each run also switches
        operating point, DC, transient and AC back on in the setup. So
        ``analyses`` writes the switches and is applied by the first run
        after opening; otherwise the run does what Altium kept, and
        ``selection_applied`` is false. sim_get_results(analysis=...)
        reads one analysis either way.

        With no sheet in front and none given, the project's first
        simulator output runs instead (``run_mode`` "project"), which
        always does those four.

        Succeeds only when a results document (.sdf) appears, else
        SIM_NOT_RUN; the reply summarises each wave, sim_get_results
        gives the traces. Order: focus a sheet, sim_get_readiness and
        sim_attach_*, sch_place_probe, sim_setup_analysis, sim_run.

        Args:
            analysis: Free-form label echoed back in the response.
            analyses: switch on only these before the run: "op", "dc",
                "transient", "ac".
            sheet: the schematic whose setup to run; empty uses the
                sheet in front.

        Returns:
            Dict with success, ran, results_document (the .sdf path),
            results_saved, run_mode ("sheet" or "project"), sheet,
            ran_analyses, results (this run's analyses, per wave: unit,
            points, min, max, first, last; magnitude for an AC wave),
            analysis and note; with analyses, ``switched`` and
            ``selection_applied``.
        """
        bridge = get_bridge()
        before = await _chart_count(bridge)
        switched = None
        if analyses:
            try:
                changes = only_analyses(analyses)
            except SimCfgError as exc:
                return {"success": False, "error": "BAD_ANALYSES", "message": str(exc)}
            switched = await _change_simcfg(bridge, "", changes)
            if not switched.get("success"):
                return switched
        params: dict[str, Any] = {}
        if analysis:
            params["analysis"] = analysis
        if sheet:
            params["sheet"] = sheet
        reply = await bridge.send_command_async(
            "generic.run_simulation", params, timeout=600.0
        )
        if isinstance(reply, dict) and switched is not None:
            reply["switched"] = {"simcfg": switched.get("simcfg"),
                                 "on": sorted(a for a in ANALYSES
                                              if changes[ANALYSES[a]] == "True")}
        if not (isinstance(reply, dict) and reply.get("ran")):
            return reply
        path = reply.get("results_document") or ""
        if path:
            _last_results["path"] = path
        if not reply.get("results_saved"):
            reply["results_note"] = (
                "the results document could not be saved, so its values "
                "were not read; they are open in Altium")
            return reply
        try:
            every = read_sdf(path)
        except (OSError, SdfError) as exc:
            reply["results_note"] = f"the results file could not be read: {exc}"
            return reply
        # A results file gains one chart per analysis per run; one that
        # shrank was started again.
        start = before.get(path.lower(), 0)
        fresh = every[start:] if len(every) > start else every
        charts = latest_charts(fresh)
        ran = []
        for chart in fresh:
            key = _CHART_KEYS.get(chart.name.strip().lower(), chart.name)
            if key not in ran:
                ran.append(key)
        reply["ran_analyses"] = ran
        if switched is not None:
            applied = sorted(ran) == reply["switched"]["on"]
            reply["selection_applied"] = applied
            if not applied:
                reply["selection_note"] = (
                    "Altium kept the analysis selection it loaded at this "
                    "project's first run since it was opened; the switches "
                    "are saved in the setup and apply after the project is "
                    "reopened. sim_get_results(analysis=...) reads one.")
        reply["results"] = _results(charts, waves=None, max_points=0)
        return reply

    @mcp.tool()
    async def sim_run_deck(deck: str, file_path: str) -> dict[str, Any]:
        """Run a SPICE deck in Altium's simulator; the waves open as a chart.

        The way to simulate a circuit that is not a schematic of parts with
        simulation models: a hot-plug, an inrush, a supply under a load
        step. Write it as plain SPICE (elements, models, one analysis such
        as .TRAN, a .PLOT line per analysis naming the waves to chart, and
        .END). Altium opens the results as a chart in front, and the reply
        gives each wave's figures. Passive and source values are yours to
        state; an IC's behaviour needs its vendor's model (SPICE_MODEL_RULES).

        A schematic of your own symbols does not run through sim_run: each
        part needs a simulation model link, and the parameters
        sim_attach_primitives writes are not one (Altium answers "Missing
        component models").

        Args:
            deck: the SPICE text, e.g. "* hot-plug\\nV1 in 0 PULSE(0 5 10u
                1u)\\nR1 in out 0.25\\nC1 out 0 22u\\n.TRAN 1u 2m\\n.PLOT TRAN
                V(out)\\n.END".
            file_path: where to write it: a full path ending in .nsx, in
                the project's folder. An existing deck there is replaced.

        Returns:
            ``ran``, ``deck``, ``results_document`` (the .sdf), and
            ``results`` per analysis per wave (unit, points, min, max,
            first, last); sim_get_results gives the traces.
        """
        import os

        if not file_path or not os.path.isabs(file_path) \
                or not file_path.lower().endswith(".nsx"):
            return {"success": False, "error": "file_path must be a full path ending in .nsx"}
        text = (deck or "").replace("\r\n", "\n").strip()
        if not text:
            return {"success": False, "error": "the deck is empty"}
        if not any(line.strip().upper().startswith((".TRAN", ".AC", ".DC", ".OP"))
                   for line in text.splitlines()):
            return {"success": False, "error": "the deck has no analysis (.TRAN, .AC, .DC or .OP)"}
        if text.splitlines()[-1].strip().upper() != ".END":
            text += "\n.END"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(text + "\n")

        bridge = get_bridge()
        reply = await bridge.send_command_async(
            "generic.run_spice_deck", {"file_path": file_path}, timeout=600.0)
        if not (isinstance(reply, dict) and reply.get("ran")):
            return reply
        path = reply.get("results_document") or ""
        if path:
            _last_results["path"] = path
        if not reply.get("results_saved"):
            reply["results_note"] = (
                "the results document could not be saved, so its values "
                "were not read; the chart is open in Altium")
            return reply
        try:
            charts = latest_charts(read_sdf(path))
        except (OSError, SdfError) as exc:
            reply["results_note"] = f"the results file could not be read: {exc}"
            return reply
        reply["results"] = _results(charts, waves=None, max_points=0)
        return reply

    @mcp.tool()
    async def sim_setup_analysis(
        transient: dict | None = None,
        ac: dict | None = None,
        dc: dict | None = None,
        outputs: list[str] | None = None,
        project_path: str = "",
    ) -> dict[str, Any]:
        """Set the analyses sim_run will use, in the project's .simcfg.

        Values are SPICE values: a number in SI units, or a string with a
        SPICE suffix ("5u", "10n", "1k", "100meg").

        Args:
            transient: {"stop", "step", "start" (default 0),
                "max_step" (default step)}.
            ac: {"start", "stop", "points", "sweep": "dec" | "oct" | "lin"}.
                Points are per decade or octave, or in total for lin.
            dc: {"source" (a source designator such as "V1", or "Temp"),
                "start", "stop", "step"}.
            outputs: expressions to plot in each analysis set here, e.g.
                ["v(out)", "i(v1)"].
            project_path: the project; empty uses the focused one.

        Each analysis set here is switched on; one already on stays on
        and runs on its last settings. sim_run(analyses=[...]) runs only
        the ones named, and sim_get_setup shows what is on. Dashboard
        changes are saved first and kept; a project without a .simcfg
        gets one.

        Returns:
            Dict with simcfg (the file), set (the keys written), reloaded,
            and created when the file is new.
        """
        try:
            changes = analysis_changes(transient=transient, ac=ac, dc=dc,
                                       outputs=outputs)
        except SimCfgError as exc:
            return {"success": False, "error": "BAD_SETTINGS", "message": str(exc)}
        return await _change_simcfg(get_bridge(), project_path, changes)

    @mcp.tool()
    async def sim_get_setup(sheet: str = "", project_path: str = "") -> dict[str, Any]:
        """What a simulation run would do: the analyses on and their settings.

        Reads the project's .simcfg as Altium last saved it (its edits in
        the dashboard are saved first). Altium uses a sheet's own settings
        when ``affect`` is Document and the project's when it is Project;
        ``source`` says which were read.

        Args:
            sheet: the schematic to report for; empty uses the sheet in
                front.
            project_path: the project; empty uses the focused one.

        Returns:
            Dict with simcfg, affect, source, enabled (of op, dc,
            transient, ac), other_enabled (any other analysis switched
            on), settings (per enabled analysis: its sweep or times and
            its output expressions), sheets with settings of their own,
            and results (each recorded run: name, type, date, locked).
        """
        bridge = get_bridge()
        found = await _project_simcfg(bridge, project_path)
        if not found.get("success"):
            return found
        simcfg = found["simcfg"]
        if not simcfg:
            return {"success": False, "error": "NO_SETUP",
                    "project_path": found["project_path"],
                    "message": "the project has no simulation settings yet; "
                               "a run uses Altium's default, an operating point "
                               "(sim_setup_analysis creates them)"}
        await bridge.send_command_async(
            "generic.simcfg_document", {"action": "save", "file_path": simcfg})
        if not sheet:
            active = await bridge.send_command_async("application.get_active_document", {})
            path = str((active or {}).get("file_path", "")) if isinstance(active, dict) else ""
            if path.lower().endswith(".schdoc"):
                sheet = path
        try:
            setup = describe(read_simcfg(simcfg), sheet)
        except (OSError, SimCfgError) as exc:
            return {"success": False, "error": "NOT_READ", "simcfg": simcfg,
                    "message": f"cannot read {simcfg}: {exc}"}
        return {"success": True, "simcfg": simcfg, "sheet": sheet, **setup}

    @mcp.tool()
    async def sim_get_probes() -> dict[str, Any]:
        """List the probes on the focused project's sheets.

        Each probe with its sheet, location and parameters. ``simulation``
        is true for a probe the simulator uses (it carries
        SimulationProbe: V, I, P, VD+ or VD-, given as ``kind``);
        ProbeName is the node Altium resolved ("Empty Probe" when it sits
        off a wire), ProbeCustomName the name shown instead, and
        ProbeValueDisplay the value shown on the sheet. Sheets that are
        not open are listed in ``not_loaded`` (proj_load_sheets opens
        them).

        Returns:
            Dict with project, count, probes and not_loaded.
        """
        return await get_bridge().send_command_async("generic.get_probes", {})

    @mcp.tool()
    async def sim_get_results(
        results_document: str = "",
        analysis: str = "",
        waves: list[str] | None = None,
        max_points: int = 50,
        all_runs: bool = False,
    ) -> dict[str, Any]:
        """Read a simulation's results: every wave, with sampled points.

        Reads the .sdf that sim_run saved (the last one by default). Each
        wave comes with its unit, point count, min/max/first/last over
        every point, and up to max_points samples evenly spaced from first
        to last. An AC wave reports magnitude and phase in degrees against
        frequency. The sweep variable (time, frequency, the swept source)
        is marked independent.

        Args:
            results_document: path of a .sdf; empty uses the last sim_run.
            analysis: one analysis by name ("Transient Analysis", "AC
                Analysis", "Operating Point", "DC Sweep", ...); empty
                returns all.
            waves: wave names to keep, e.g. ["v(out)", "i(v1)"]; empty
                keeps all.
            max_points: samples per wave; 0 returns statistics only.
            all_runs: a document run more than once holds every run; by
                default only the latest run of each analysis is returned.

        Returns:
            Dict with results_document and analyses: a list of
            {analysis, waves: [...]}, or an error naming what is missing.
        """
        path = results_document or _last_results.get("path", "")
        if not path:
            return {"success": False, "error": "NO_RESULTS",
                    "message": "no results_document given and no sim_run "
                               "has produced one in this session"}
        try:
            charts = read_sdf(path)
        except OSError as exc:
            return {"success": False, "error": "NOT_FOUND",
                    "message": f"cannot read {path}: {exc}"}
        except SdfError as exc:
            return {"success": False, "error": "NOT_RESULTS", "message": str(exc)}
        if not all_runs:
            charts = latest_charts(charts)
        if analysis:
            want = analysis.strip().lower()
            charts = [c for c in charts if c.name.lower() == want]
            if not charts:
                return {"success": False, "error": "NO_SUCH_ANALYSIS",
                        "message": f"no {analysis!r} in {path}",
                        "analyses": sorted({c.name for c in read_sdf(path)})}
        return {"success": True, "results_document": path,
                "analyses": _results(charts, waves=waves, max_points=max_points,
                                     as_list=True)}
