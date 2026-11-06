# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Backend adapters: fill a normalized snapshot from a live EDA tool.

Each adapter turns one EDA's live API into the same :class:`DesignSnapshot`.
The neutral tools call :func:`resolve_backend` to pick the adapter matching the
active backend (or an explicit override) and then ``await backend.snapshot()``.
Adding another EDA is a matter of writing one more adapter here.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .snapshot import DesignSnapshot


class BackendUnavailableError(RuntimeError):
    """The requested backend's tool is not reachable (not running, API off)."""


#: The backend whose tools were actually registered, once something has
#: registered them. Set by ``tools.register_backend``.
_REGISTERED: Optional[str] = None


def set_active_backend(name: str) -> None:
    """Record which backend's tools were registered.

    THE REGISTRY AND THE RESOLVER USED TO DISAGREE. Tool registration
    takes the backend as an argument, while this module read it back out
    of the environment, so the two agreed only as long as one caller set
    both. A harness that registered the EasyEDA surface without also
    exporting the variable got EasyEDA tools and an ALTIUM resolver, and
    ``review_design`` reviewed a completely different design while
    reporting success. With Altium open at the time the answer looked
    entirely plausible: real parts, real nets, wrong document.

    Recording it here removes the second source of truth rather than
    asking every caller to remember the first.
    """
    global _REGISTERED
    _REGISTERED = (name or "").strip().lower() or None


def active_backend_name() -> str:
    """The configured backend, or failing that the registered one.

    THE ENVIRONMENT WINS WHEN IT IS SET, because it is the explicit
    configuration and registration is only a record of what happened.
    Preferring the registration instead made `_REGISTERED` sticky for
    the life of the process: once anything had registered a backend it
    overrode every later `EDA_AGENT_BACKEND`, which is how nine tests
    that set the variable started reading the wrong guide text.

    The bug this still fixes is the opposite case, and it is the common
    one: something registers a backend WITHOUT setting the variable, and
    the resolver silently falls back to the default. A harness that did
    that got EasyEDA tools over an Altium resolver, and review_design
    returned a clean, plausible review of a different EDA's document.
    """
    configured = (os.environ.get("EDA_AGENT_BACKEND") or "").strip().lower()
    if configured:
        return configured
    if _REGISTERED:
        return _REGISTERED
    return "altium"


class KiCadBackend:
    name = "kicad"

    async def health(self) -> dict[str, Any]:
        from ..bridge.kicad_bridge import get_kicad_bridge, KiCadNotReachableError
        try:
            return get_kicad_bridge().ping()
        except KiCadNotReachableError as e:
            raise BackendUnavailableError(str(e)) from None

    async def snapshot(self) -> DesignSnapshot:
        from ..bridge.kicad_bridge import get_kicad_bridge, KiCadNotReachableError
        br = get_kicad_bridge()
        try:
            parts, pins, unconnected = br.component_pins()
            stats = br.board_stats()
        except KiCadNotReachableError as e:
            raise BackendUnavailableError(str(e)) from None
        return DesignSnapshot.build(
            "kicad", parts, pins,
            board_name=str(stats.get("name") or ""),
            unconnected_pad_count=unconnected,
            raw_stats={k: v for k, v in stats.items()
                       if k in ("tracks", "vias", "zones", "stackup_layers",
                                "footprints", "pads")},
        )

    async def run_drc(self) -> dict[str, Any]:
        from ..bridge.kicad_bridge import get_kicad_bridge, KiCadNotReachableError
        from .kicad_drc import run_kicad_cli_drc
        br = get_kicad_bridge()
        try:
            cli = br.kicad_cli_path()
            board = br.board_file_path()
        except KiCadNotReachableError as e:
            raise BackendUnavailableError(str(e)) from None
        return await run_kicad_cli_drc(cli, board)

    async def run_erc(self) -> dict[str, Any]:
        from ..bridge.kicad_bridge import get_kicad_bridge, KiCadNotReachableError
        from .kicad_drc import run_kicad_cli_erc
        br = get_kicad_bridge()
        try:
            cli = br.kicad_cli_path()
            sch = br.sch_file_path()
        except KiCadNotReachableError as e:
            raise BackendUnavailableError(str(e)) from None
        return await run_kicad_cli_erc(cli, sch)


class AltiumBackend:
    name = "altium"

    async def _bridge(self):
        from ..bridge import get_bridge
        from ..bridge.exceptions import AltiumError
        bridge = get_bridge()
        return bridge, AltiumError

    async def health(self) -> dict[str, Any]:
        bridge, AltiumError = await self._bridge()
        try:
            return await bridge.send_command_async("application.ping", {})
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None

    async def snapshot(self) -> DesignSnapshot:
        bridge, AltiumError = await self._bridge()
        try:
            # One BOM read carries designators, values, footprints, library
            # references and every pin's net -- a complete snapshot.
            bom = await bridge.send_command_async(
                "project.get_bom", {"limit": "5000"})
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None
        parts: list[dict[str, Any]] = []
        pins: list[dict[str, Any]] = []
        unconnected = 0
        for comp in (bom or {}).get("components", []):
            ref = str(comp.get("designator", ""))
            parts.append({
                "refdes": ref,
                "value": comp.get("comment", ""),
                "footprint": comp.get("footprint", ""),
                "lib_ref": comp.get("lib_ref", ""),
            })
            for pin in comp.get("pins", []):
                net = str(pin.get("net", "") or "").strip()
                pins.append({"refdes": ref, "pin": pin.get("pin", ""),
                             "net": net})
                if not net:
                    unconnected += 1
        return DesignSnapshot.build(
            "altium", parts, pins,
            board_name=str((bom or {}).get("project_path", "") or ""),
            unconnected_pad_count=unconnected,
        )

    async def run_drc(self) -> dict[str, Any]:
        bridge, AltiumError = await self._bridge()
        try:
            raw = await bridge.send_command_async("pcb.run_drc", {}, timeout=120.0)
            raw = raw or {}
            from ..export.drc_rules import enrich_drc
            await enrich_drc(bridge, raw)
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None
        violations = raw.get("violations", []) or []
        out = {
            "ok": True,
            "source": "altium",
            "violation_count": raw.get("violation_count", len(violations)),
            "violations": violations[:200],
        }
        for key in ("drc_confirmed", "report_counts", "report_rules",
                    "report_stopped_at_limit", "rules", "verdict", "verdict_reason"):
            if key in raw:
                out[key] = raw[key]
        return out

    async def run_erc(self) -> dict[str, Any]:
        bridge, AltiumError = await self._bridge()
        try:
            raw = await bridge.send_command_async(
                "generic.run_erc", {"limit": "200"})
            # A script from before 2026.10.21 answers the run with a
            # message only; the list is read separately there.
            if not isinstance(raw, dict) or "violation_count" not in raw:
                raw = await bridge.send_command_async(
                    "generic.get_erc_violations", {"limit": "200"})
        except AltiumError as e:
            raise BackendUnavailableError(str(e)) from None
        raw = raw or {}
        violations = raw.get("violations", raw.get("items", [])) or []
        count = raw.get("violation_count", len(violations))
        return {
            "ok": True,
            "source": "altium",
            "violation_count": count,
            "violations": violations[:200],
            "truncated": len(violations[:200]) < count,
        }


class EasyEdaBackend:
    """EasyEDA Pro, reached through its extension API.

    The editor dials out to this process rather than being driven by it,
    so every method here reports the source as unreachable until the
    extension connects. That is a different answer from "the command
    failed", and the two must not be collapsed: one means start the
    extension, the other means the edit was refused.

    DRC and ERC are run by the editor and read back, never reimplemented
    here. This project does not reimplement an EDA tool's own checks,
    for the same reason it does not synthesize Altium's binary formats:
    a second opinion that disagrees with the tool is worse than no
    opinion.
    """

    name = "easyeda"

    def _bridge(self):
        from ..bridge.easyeda_bridge import (
            EasyEdaNotReachableError,
            get_easyeda_bridge,
        )
        try:
            return get_easyeda_bridge(), EasyEdaNotReachableError
        except Exception as exc:  # noqa: BLE001 - surfaced as unavailable
            raise BackendUnavailableError(str(exc)) from None

    @staticmethod
    def _result(reply: dict, what: str) -> dict:
        """The reply's result, refusing to read a REFUSAL as data.

        A REFUSED COMMAND IS NOT AN EMPTY DESIGN. The editor injects its
        API per document type, so design.snapshot on a schematic comes
        back as a considered error saying to open a PCB. Reading
        ``reply["result"] or {}`` turned that into a snapshot of nothing,
        and review_design then reported success with zero parts, zero
        nets and zero findings: a clean bill of health for a board it
        never looked at, on a design the audits could see 111 parts in.

        Raising keeps the two apart. "Nothing was examined" and "nothing
        was wrong" are opposite answers and only one of them is safe to
        act on.
        """
        error = reply.get("error")
        if error:
            raise BackendUnavailableError(f"{what}: {error}")
        result = reply.get("result")
        if result is None:
            raise BackendUnavailableError(
                f"{what}: the editor answered with neither a result nor an "
                f"error, so there is nothing to report and no reason why")
        return result

    async def health(self) -> dict[str, Any]:
        bridge, unreachable = self._bridge()
        try:
            return bridge.ping()
        except unreachable as exc:
            raise BackendUnavailableError(str(exc)) from None

    async def snapshot(self) -> DesignSnapshot:
        bridge, unreachable = self._bridge()
        try:
            reply = bridge.send_editor_command("design.snapshot")
        except unreachable as exc:
            raise BackendUnavailableError(str(exc)) from None

        result = self._result(reply, "design.snapshot")

        # Translate the wire vocabulary into the snapshot's. The
        # extension speaks EasyEDA's language ("designator"), the
        # snapshot speaks its own ("refdes"), and passing the wire form
        # through unchanged builds a snapshot with no identifiable parts
        # at all. That failure is silent: no error, just a review that
        # finds nothing on a board full of problems, which is worse than
        # a crash because it reads as a clean bill of health.
        parts = [
            {
                "refdes": p.get("designator") or p.get("refdes") or "",
                "value": p.get("value", ""),
                "footprint": p.get("footprint", ""),
                "layer": p.get("layer", ""),
            }
            for p in (result.get("parts") or [])
        ]
        pins = [
            {
                "refdes": p.get("designator") or p.get("refdes") or "",
                "pin": p.get("pin", ""),
                "net": p.get("net", ""),
            }
            for p in (result.get("pins") or [])
        ]
        return DesignSnapshot.build(
            "easyeda", parts, pins,
            board_name=str(result.get("board_name") or ""),
            unconnected_pad_count=int(result.get("unconnected_pads") or 0),
            raw_stats={k: v for k, v in (result.get("stats") or {}).items()
                       if k in ("tracks", "vias", "zones", "stackup_layers",
                                "footprints", "pads")},
        )

    @staticmethod
    def _checked(result: dict, what: str) -> dict:
        """A checker's findings, or a refusal saying nothing was checked.

        A CHECK THAT DID NOT RUN IS NOT A CHECK THAT PASSED. EasyEDA's
        checkers sometimes answer with the bare boolean false instead of
        a report. The extension recognises that and replies with
        ``ran: false`` and a reason, but reading ``violations or []``
        past it turns "nothing was enumerated" into "no violations
        found" and reports a clean board with a straight face.

        The extension already refuses to guess here; this stops the
        refusal being discarded one layer up.
        """
        if result.get("ran") is False:
            return {
                "ok": False,
                "source": "easyeda",
                "reason": str(result.get("failed")
                              or f"{what} did not run, and no reason was given"),
                "ran": False,
            }
        violations = result.get("violations") or []
        return {
            "ok": True,
            "source": "easyeda",
            "ran": True,
            "violation_count": result.get("violation_count", len(violations)),
            "violations": violations[:200],
        }

    async def run_drc(self) -> dict[str, Any]:
        bridge, unreachable = self._bridge()
        try:
            reply = bridge.send_editor_command("design.run_drc", timeout=120.0)
        except unreachable as exc:
            raise BackendUnavailableError(str(exc)) from None
        return self._checked(
            self._result(reply, "design.run_drc"), "design.run_drc")

    async def run_erc(self) -> dict[str, Any]:
        bridge, unreachable = self._bridge()
        try:
            reply = bridge.send_editor_command("design.run_erc", timeout=120.0)
        except unreachable as exc:
            raise BackendUnavailableError(str(exc)) from None
        return self._checked(
            self._result(reply, "design.run_erc"), "design.run_erc")


_BACKENDS = {
    "altium": AltiumBackend,
    "easyeda": EasyEdaBackend,
    "kicad": KiCadBackend,
}


def resolve_backend(name: Optional[str] = None):
    """Return the adapter for ``name`` (or the active backend if None).

    Under the ``both`` backend the default (Altium) is used; pass an
    explicit name to target the other.

    A NAME THIS DOES NOT RECOGNISE IS REFUSED. It used to fall through
    to Altium, which turns a misspelled backend into a full review of
    whichever design Altium happens to have open: plausible parts,
    plausible nets, wrong document, and a report that says nothing went
    wrong. Reviewing the wrong design silently is worse than not
    reviewing at all.
    """
    key = (name or active_backend_name()).strip().lower()
    if key in _BACKENDS:
        return _BACKENDS[key]()
    if not name:
        # No explicit request: an unrecognised ambient value, including
        # "both", means take the default.
        return AltiumBackend()
    raise BackendUnavailableError(
        f"unknown backend {name!r}. Valid names are "
        f"{', '.join(sorted(_BACKENDS))}. Refusing rather than falling "
        f"back, because the fallback would review a different design "
        f"and report success")
