# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Design-lint / audit MCP tools.

Read-only validators that walk the focused Altium project and return
structured violation lists. The corresponding Pascal handlers live in
``scripts/altium/Audit.pas`` and dispatch under the ``audit.*`` command
category.

Each validator is intended to be a single MCP tool call surfacing a
specific class of design bug; the umbrella ``design_lint_report`` tool
(see ``review.py``) calls them in sequence and returns a consolidated
report.
"""

import re
from typing import Any

from ..bridge import get_bridge
from .pcb import _mils


_NC_PIN_NAME = re.compile(
    r"^(NC|N\.C\.|DNC|N/C|NO[ _-]?CONNECT|RSVD|RESERVED)\b",
    re.IGNORECASE,
)


_POWER_PIN_NAME = re.compile(
    r"^(V(CC|DD|BAT|BUS|IN|REF|AA|CORE)|[ADPNT](V(CC|DD)))(_|$|[A-Z]?[0-9])",
    re.IGNORECASE,
)
_GROUND_PIN_NAME = re.compile(
    r"^(GND|VSS|AGND|DGND|PGND|SGND|VSSA?)(_|$|[0-9])",
    re.IGNORECASE,
)
_DECOUPLING_PIN_NAME = re.compile(
    r"^(V(CC|DD|SS|BAT|REF|IO|CORE|BUS|LDO|REG|IN|OUT)|VR?\+|V-"
    r"|[ADP](VCC|VDD|VSS|GND)|GND|VDDA?|VSSA?)",
    re.IGNORECASE,
)


def _net_is_power(name: str) -> bool:
    if not name:
        return False
    n = name.upper()
    if n == "GND" or n.startswith("GND") or n.startswith("VCC") \
       or n.startswith("VDD") or n.startswith("VSS") \
       or (n and n[0] == "+") or n.startswith("AGND") \
       or n.startswith("DGND"):
        return True
    # Other common supply rails whose IC pins still want decoupling: analog
    # supplies, battery/USB/reference/negative rails. Prefix-matched so e.g.
    # AVDD3V3 or VREF_ADC also count. The decoupling audit is intentionally
    # broad here -- missing one of these silently skips its decoupling check.
    if n.startswith(("AVDD", "AVCC", "AVEE", "AVSS", "VBAT", "VBUS", "VEE",
                     "VPP", "VREF", "VSYS", "VIO", "VAA", "VPWR")):
        return True
    if "V3V3" in n or "V5V" in n:
        return True
    if len(n) >= 2 and n[0] == "V" and n[1].isdigit():
        return True
    return False


def _net_is_ground(name: str) -> bool:
    if not name:
        return False
    n = name.upper()
    if n == "GND" or n == "VSS":
        return True
    return (n.startswith("GND") or n.startswith("AGND")
            or n.startswith("DGND") or n.startswith("PGND")
            or n.startswith("SGND") or n.startswith("EARTH"))


def find_pin_net_name_mismatches_from_bom(bom: dict[str, Any]) -> dict[str, Any]:
    """For each IC pin whose NAME looks like a power / ground pin (VCC,
    VDD, GND, etc), verify the connected NET also has a power / ground
    name. A pin named VCC sitting on a non-power net is almost always
    a swapped-wire bug that ERC won't catch."""
    if not isinstance(bom, dict):
        return {"checked": 0, "violations": 0, "items": []}
    items: list[dict[str, Any]] = []
    checked = 0
    for c in bom.get("components") or []:
        des = str(c.get("designator") or "")
        if _component_class_from_designator(des) != "ic":
            continue
        checked += 1
        bad: list[dict[str, str]] = []
        for p in c.get("pins") or []:
            if not isinstance(p, dict):
                continue
            pin_name = str(p.get("name") or "")
            net = str(p.get("net") or "")
            if not pin_name or not net:
                continue
            is_pwr_name = bool(_POWER_PIN_NAME.match(pin_name))
            is_gnd_name = bool(_GROUND_PIN_NAME.match(pin_name))
            if not is_pwr_name and not is_gnd_name:
                continue
            if is_gnd_name and not _net_is_ground(net):
                bad.append({"pin": str(p.get("pin") or ""),
                            "name": pin_name, "net": net,
                            "kind": "ground_pin_on_non_ground_net"})
            elif is_pwr_name and not is_gnd_name and not _net_is_power(net):
                bad.append({"pin": str(p.get("pin") or ""),
                            "name": pin_name, "net": net,
                            "kind": "power_pin_on_non_power_net"})
        if bad:
            items.append({"designator": des, "mismatches": bad})
    return {
        "checked": checked,
        "violations": len(items),
        "items": items,
    }


def find_missing_decoupling_from_bom(bom: dict[str, Any]) -> dict[str, Any]:
    """For every IC, verify each of its power pins shares a net with at
    least one capacitor (designator starts with C). Buckets:
        full     -- every power pin has >=1 cap on its net
        partial  -- some covered, some not
        missing  -- no power pin has any cap on its net
    ICs with NO recognised power pins are skipped."""
    if not isinstance(bom, dict):
        return {"checked": 0, "violations": 0, "items": []}
    comps = bom.get("components") or []
    # Build net -> set of cap designators index.
    cap_by_net: dict[str, set[str]] = {}
    for c in comps:
        des = str(c.get("designator") or "")
        if not des or des[0].upper() != "C":
            continue
        for p in c.get("pins") or []:
            if not isinstance(p, dict):
                continue
            n = p.get("net")
            if n:
                cap_by_net.setdefault(str(n), set()).add(des)

    items: list[dict[str, Any]] = []
    checked = 0
    for c in comps:
        des = str(c.get("designator") or "")
        if _component_class_from_designator(des) != "ic":
            continue
        pins = c.get("pins") or []
        power_pins: list[dict[str, Any]] = []
        for p in pins:
            if not isinstance(p, dict):
                continue
            pin_name = str(p.get("name") or "")
            net = str(p.get("net") or "")
            is_power = False
            if net and (_net_is_power(net) or _net_is_ground(net)):
                is_power = True
            elif pin_name and _DECOUPLING_PIN_NAME.match(pin_name):
                is_power = True
            if is_power:
                power_pins.append(p)
        if not power_pins:
            continue
        checked += 1
        covered = []
        uncovered = []
        for p in power_pins:
            net = str(p.get("net") or "")
            if net and net in cap_by_net and cap_by_net[net]:
                covered.append({"pin": str(p.get("pin") or ""),
                                "name": str(p.get("name") or ""),
                                "net": net,
                                "caps": sorted(cap_by_net[net])})
            else:
                uncovered.append({"pin": str(p.get("pin") or ""),
                                  "name": str(p.get("name") or ""),
                                  "net": net})
        if not uncovered:
            status = "full"
        elif not covered:
            status = "missing"
        else:
            status = "partial"
        if status != "full":
            items.append({"designator": des, "status": status,
                          "uncovered_pins": uncovered,
                          "covered_pin_count": len(covered)})
    return {
        "checked": checked,
        "violations": len(items),
        "items": items,
    }


def find_unconnected_ic_pins_from_bom(bom: dict[str, Any]) -> dict[str, Any]:
    """Pure helper: compute the "unconnected IC pins" audit result from
    a ``project.get_bom`` payload. Extracted so the MCP tool, the
    design_lint_report umbrella, and the dashboard's /api/lint endpoint
    can all share one implementation."""
    if not isinstance(bom, dict):
        return {"checked": 0, "violations": 0,
                "unconnected_pin_total": 0, "items": []}
    comps = bom.get("components") or []
    checked = 0
    items: list[dict[str, Any]] = []
    total_unconnected = 0
    for c in comps:
        des = str(c.get("designator") or "")
        if _component_class_from_designator(des) != "ic":
            continue
        checked += 1
        bad: list[dict[str, str]] = []
        for p in c.get("pins") or []:
            if not isinstance(p, dict):
                continue
            pin_name = str(p.get("name") or "")
            if _NC_PIN_NAME.match(pin_name):
                continue
            if p.get("net") in (None, "", "?"):
                bad.append({"pin": str(p.get("pin") or ""),
                            "name": pin_name})
        if bad:
            items.append({"designator": des, "unconnected_pins": bad})
            total_unconnected += len(bad)
    return {
        "checked": checked,
        "violations": len(items),
        "unconnected_pin_total": total_unconnected,
        "items": items,
    }


#: Designator prefix to component class. Kept identical to
#: componentClass() in the dashboard's index.html, and
#: tests/test_designator_classes_agree.py fails if the two drift.
#:
#: Matched on the WHOLE letter prefix, so LED is a semiconductor rather
#: than an inductor and SW is a connector rather than a switch nobody
#: classified.
_DESIGNATOR_CLASSES = {
    "U": "ic", "IC": "ic", "A": "ic",
    "J": "connector", "P": "connector", "X": "connector",
    "CN": "connector", "SW": "connector", "S": "connector",
    "D": "semi", "LED": "semi", "Q": "semi", "T": "semi",
    "VR": "semi", "TVS": "semi",
    "R": "passive", "C": "passive", "L": "passive", "FB": "passive",
    "RN": "passive", "Y": "passive", "XTAL": "passive",
}


def _component_class_from_designator(des: str) -> str:
    """Mirror of the dashboard's componentClass() heuristic so MCP-side
    audits classify the same way the Components tab chips do."""
    if not des:
        return "other"

    # THE WHOLE LETTER PREFIX, matched against one table.
    #
    # This used to try a three-entry multi-letter map and then fall
    # through to des[0], and the claim above that it mirrors the
    # dashboard was simply false: measured across 25 ordinary
    # designators the two disagreed on TEN, twice landing in opposite
    # categories. CN1 was a connector on the dashboard and a passive
    # here; XTAL1 was a passive there and a connector here. Y1, the
    # standard crystal prefix, was "other".
    #
    # A first-letter fallback cannot be made to agree, because the
    # prefixes that matter are multi-letter and their first letters
    # belong to other classes: LED starts with L, TVS with T, SW with S.
    # So the fallback is gone and the table below is the whole rule,
    # kept identical to componentClass() in the dashboard. A guard
    # parses that function and compares the two, because a comment
    # asserting they match is what failed last time.
    import re as _re

    match = _re.match(r"[A-Za-z]+", des)
    prefix = match.group(0).upper() if match else ""
    return _DESIGNATOR_CLASSES.get(prefix, "other")


def register_audit_tools(mcp):
    """Register design-lint / audit tools with the MCP server."""

    @mcp.tool()
    async def audit_find_inconsistent_track_widths() -> dict[str, Any]:
        """Find nets where the maximum and minimum track widths differ
        by more than 2×.

        Classic bug pattern: a power rail (VCC / VDD / GND) is routed
        at 20 mil to handle the expected current, then later someone
        adds a connection using the editor's default 8 mil width and
        moves on. The result is a thin orphan stub welded to a wide
        bus -- under load that thin section becomes a thermal hotspot
        and eventually a fab rework. Most agents and human
        reviewers miss it because the bulk of the net looks fine.

        Algorithm: per net, collect min/max track widths on signal
        layers. Ratio > 2.0 → flag. Per-violation reports the actual
        widths and the (x, y) of the *thinnest* track on the net so
        the agent can jump straight to the questionable section.

        Pattern: SDK-derived. No community-script reference.

        Returns:
            Dict with ``{checked, violations, items[]}`` where each
            item carries ``{net, min_width_mils, max_width_mils,
            ratio, thin_x_mils, thin_y_mils}``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_inconsistent_track_widths", {})

    @mcp.tool()
    async def audit_find_acute_angles() -> dict[str, Any]:
        """Find pairs of same-net tracks meeting at < 90° interior
        angle.

        An acute inside corner can over-etch. Pure geometry on signal
        layers, independent of Altium's acute-angle rule, which is often
        off; at most 100 are listed.

        Returns:
            Dict with ``{checked, violations, items[]}`` where each
            item carries ``{net, layer, x_mils, y_mils, angle_deg}``.
        """
        bridge = get_bridge()
        # Nested board+spatial iteration over every track endpoint; on a dense
        # board (10k+ tracks) the response can land past the default 10 s
        # window even though the work itself is sub-second once warm.
        return await bridge.send_command_async(
            "audit.find_acute_angles", {}, timeout=45.0)

    @mcp.tool()
    async def audit_find_placeholder_values() -> dict[str, Any]:
        """Find SCH component parameters containing obvious "I'll
        fix this later" placeholder strings that escaped a release.

        Matched values (case-insensitive, whole-string after trim):
          - ``TBD``, ``TBA``
          - ``TODO``, ``FIXME``, ``XXX``
          - ``?``, ``??``, ``???``
          - ``PLACEHOLDER``, ``FILLER``, ``ASK``, ``UNKNOWN``
          - ``N/A``, ``NA``

        Every parameter of every component in the project; empty values
        are left to other audits such as ``audit_find_missing_datasheets``.

        Returns:
            Dict with ``{checked, violations, items[]}`` where each
            item carries ``{designator, parameter, value}`` for a
            component parameter that matched a placeholder pattern.
            ``checked`` counts ALL parameters inspected.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_placeholder_values", {})

    @mcp.tool()
    async def audit_find_orphan_power_objects() -> dict[str, Any]:
        """Find schematic power-port markers (GND, VCC, +3V3 etc.)
        sitting off-wire.

        Symmetric cousin to ``audit_find_orphan_net_labels``. A power
        port carries its net name (from the marker's Style); the
        marker has to sit ON a wire for the wire to actually adopt
        that net. Markers in empty space create phantom power
        connections -- the schematic LOOKS like the rail is hooked
        up, but the wire underneath is unrelated.

        ERC sometimes catches this when the wire ends up with no
        driver, but power rails frequently have multiple drivers
        (the real connection elsewhere, plus this phantom), so ERC
        sees a valid net and stays quiet.

        Spatial-iterates a 1-mil square at each ``ePowerObject``
        location for any ``eWire``; absence → orphan.

        Pattern: SDK-derived, mirrors the orphan-net-label check.

        Returns:
            Dict with ``{checked, violations, items[]}`` where each
            item carries ``{net, sheet, x_mils, y_mils}`` for an
            orphan power object. ``net`` comes from the marker's
            ``Text`` (its visible net name).
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_orphan_power_objects", {})

    @mcp.tool()
    async def audit_find_orphan_net_labels() -> dict[str, Any]:
        """Find schematic net labels whose location is NOT on any
        wire or bus.

        In Altium, a net label labels whichever wire passes through
        its (x, y). If the label sits in empty space (because the
        wire was moved during a layout pass, or was never placed),
        the compile produces a phantom net -- the label looks correct
        on paper but the signal it "names" connects to nothing under
        the label. ERC sometimes catches this (no driver / no load)
        but not always -- the other end of the same-named net often
        provides a load, so the violation never fires.

        Spatial-iterates a 1-mil square at each net label's location
        for any ``eWire`` / ``eBus`` object. Absence → orphan.

        Pattern: SDK-derived. No community-script reference; surfaced
        by reviewing the ``ISch_NetLabel`` interface in the Altium
        Schematic API docs.

        Returns:
            Dict with ``{checked, violations, items[]}`` where each
            item carries ``{label, sheet, x_mils, y_mils}`` for an
            orphan net label.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_orphan_net_labels", {})

    @mcp.tool()
    async def audit_find_net_label_conflicts() -> dict[str, Any]:
        """Find net labels that silently merge, short, or do nothing.

        All three look correct on a printed sheet:

        1. ``conflicting_labels``: different text at the same point.
           Altium merges the nets under one name, a real short; the
           symptom is a net with zero pins.
        2. ``labels_on_pin_root``: a label on a pin's body-side root
           instead of its electrical end, so it connects nothing. Each
           item gives ``connect_x_mils`` / ``connect_y_mils`` to move it
           to.
        3. ``duplicate_labels``: the same text twice at one point;
           harmless, but it can hide a conflict.

        ``audit_find_orphan_net_labels`` checks only for a wire under
        the label, so it calls a label on a pin end an orphan.

        Returns:
            Dict with ``{checked, conflicts, on_pin_root, duplicates,
            conflicting_labels[], labels_on_pin_root[],
            duplicate_labels[]}``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_net_label_conflicts", {})

    @mcp.tool()
    async def audit_find_visible_supplier_pn() -> dict[str, Any]:
        """Find schematic components with a VISIBLE supplier-PN
        parameter.

        BOM hygiene rule: only the manufacturer's MPN should appear
        on the SCH PDF. Supplier part numbers (Digi-Key, Mouser,
        Newark, Arrow, RS, Farnell) change over time -- a Digi-Key
        bin gets retired, the part is re-binned under a new
        Digi-Key SKU, and an SCH PDF saying "Digi-Key 296-1234-1-ND"
        sends the next person picking the BOM five years from now
        on a wild goose chase. Manufacturer + MPN is stable;
        supplier PNs are not.

        Detects any component parameter whose name starts with
        "supplier" (case-insensitive) AND ``IsHidden=False``. Catches
        the legacy "Supplier Part Number 1" along with any free-text
        "Supplier" / "Supplier 1" variants designers create.

        Returns:
            Dict with `{checked, violations, items[]}` where each item
            carries `{designator, parameter, value}` for a visible
            supplier-PN parameter. Pair with `sch_set_components_parameters`
            to hide them in bulk.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_visible_supplier_pn", {})

    @mcp.tool()
    async def audit_find_unlocked_component_primitives() -> dict[str, Any]:
        """Find placed components whose internal primitives are NOT
        locked. Unlocked primitives are a silent fab-bug class --
        a stray click + drag in the PCB editor moves a single pad
        off-center within the footprint without any warning. The
        gerbers look fine, just slightly off, and the part doesn't
        solder properly because the pad no longer aligns with the
        device's lead.

        Library footprints ship with PrimitiveLock=True by default;
        components show up unlocked when someone clicked Component
        Properties and toggled the flag off (sometimes deliberately
        to nudge silkscreen, more often accidentally).

        Returns:
            Dict with `{checked, violations, items[]}` where each item
            carries `{designator}` for an unlocked component. Pair
            with `obj_batch_modify` to re-lock at scale.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_unlocked_component_primitives", {})

    @mcp.tool()
    async def audit_find_mirrored_pcb_text() -> dict[str, Any]:
        """Find free-floating PCB text (eTextObject) that's mirrored
        on the wrong overlay.

        Rule: top-overlay text must read normally; bottom-overlay
        text must be mirrored. When the assembled board is flipped to
        access the bottom side, the bottom text reads correctly. A
        normally-oriented bottom-side text reads backwards on the
        physical board -- the kind of thing that gets caught only at
        the fab-review stage by the customer's QA, then triggers
        a respin.

        Note: this checks free-floating text (revision strings, layer
        labels, copyright notices) -- NOT component designators or
        comments, which Altium auto-flips with the component.

        Returns:
            Dict with `{checked, violations, items[]}` where each item
            carries `{text, layer, reason}`. ``reason`` is one of
            `top_overlay_text_is_mirrored` or
            `bottom_overlay_text_is_not_mirrored`.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_mirrored_pcb_text", {})

    @mcp.tool()
    async def audit_find_mixed_designator_rotation() -> dict[str, Any]:
        """Find PCB silkscreens where designators face BOTH 0 and 180
        (or both 90 and 270) -- the assembly-readability check.

        Assembly inspectors and pick-and-place operators read silkscreen
        from a fixed angle. If half the designators on the top overlay
        read normally and the other half are upside-down (0 vs 180),
        QC has to physically rotate the board between parts -- slow,
        error-prone, and the kind of thing fab houses charge extra to
        deal with. Orthogonal mixes (0 + 90) are fine because the
        inspector turns their head, not the board.

        The check runs per overlay layer (top, bottom) independently --
        a top side with mixed 0/180 doesn't excuse a clean bottom.

        Returns:
            Dict with `{checked, violations, top_mixed_0_180,
            top_mixed_90_270, bottom_mixed_0_180, bottom_mixed_90_270,
            items[]}`. Each item carries `{designator, layer,
            rotation_deg}` for a component contributing to a flagged
            pair, so the agent can rotate those specifically.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_mixed_designator_rotation", {})

    @mcp.tool()
    async def audit_find_non_embedded_images() -> dict[str, Any]:
        """Find schematic images that are NOT embedded -- they hold
        only a path reference to the original file.

        Non-embedded images render fine on the designer's own machine
        but show a red X / placeholder box on every other machine that
        opens the schematic, because the path doesn't resolve. Common
        offenders are company-logo title-blocks dragged in from a
        network share and never re-saved with EmbedImage=True. The
        manufacturer / customer sees broken images on the SCH PDFs.

        Returns:
            Dict with `{checked, violations, items[]}` where each item
            carries `{sheet, x_mils, y_mils}` for a non-embedded image.
            ``checked`` counts ALL images (embedded + not), ``violations``
            counts only the non-embedded ones.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_non_embedded_images", {})

    @mcp.tool()
    async def audit_find_single_pin_nets() -> dict[str, Any]:
        """Find nets that have EXACTLY ONE pin AND at least one
        net-label / port / power-object on them.

        A single-pin net is normal for unused / no-connect pins.
        A single-pin net **with a named label** is a broken connection:
        the designer asserted "this signal goes somewhere" by naming it
        but the other end never made it onto the schematic. Common
        causes are a typo in a net-label that doesn't match its peer,
        a sheet-entry vs port-name mismatch on a hierarchical block,
        or an off-page connector that was deleted while routing was
        being re-arranged.

        The flat hierarchy is queried (`DM_DocumentFlattened`) so nets
        spanning multiple sheets via off-page connectors are counted
        as one net.

        Returns:
            Dict with `{checked, violations, items[]}`. Each item
            carries `{net, designator, pin}` for the lonely pin.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_single_pin_nets", {})

    @mcp.tool()
    async def audit_find_mpn_inconsistencies() -> dict[str, Any]:
        """Find groups of ICs with the same (lib_ref, comment) but
        DIFFERENT Manufacturer Part Numbers.

        Two presumably-identical parts pointing at different MPNs is
        almost always a bug: either a typo / accidental override
        during a sub-circuit clone, or means the design genuinely wants
        two sources but lost the alternates table somewhere. The agent
        reviewing the BOM should see this before purchase.

        Pattern: ports the dashboard's analyzeMpnConsistency JS
        heuristic to an authoritative Pascal-side audit.

        Returns:
            Dict with `{checked, violations, items[]}`. Each item
            carries `{lib_ref, comment, mpns, designators}` where
            ``mpns`` is a comma-joined list of the conflicting part
            numbers and ``designators`` lists the components involved.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_mpn_inconsistencies", {})

    @mcp.tool()
    async def audit_find_missing_datasheets() -> dict[str, Any]:
        """Find ICs (designator U*) with no fetchable datasheet URL in
        any of their parameters.

        Checks the standard Altium slots:
          - HelpURL, Datasheet, DatasheetURL
          - ComponentLink1URL..ComponentLink4URL

        At least one of those needs to carry an ``http(s)://`` value
        for the IC to count as covered. The agent's review discipline
        requires fetching the actual manufacturer datasheet before
        making any device-related claim: ICs without a stored URL
        force the agent to web-search the part name, which is slower
        and more error-prone than a direct link.

        Pattern: ports the dashboard's pickDatasheetLinks heuristic to
        an authoritative Pascal-side audit.

        Returns:
            Dict with `{checked, violations, items[]}` where each item
            carries `{designator, comment, lib_ref}` for an IC missing
            any datasheet link.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_missing_datasheets", {})

    @mcp.tool()
    async def audit_find_pin_net_name_mismatches() -> dict[str, Any]:
        """Find IC pins whose NAME looks like a power / ground pin but
        whose connected NET is not actually a power / ground rail.

        Examples flagged:
          - pin "VCC" wired to a non-power net (swapped rail bug)
          - pin "GND" or "VSS" wired to a non-ground net (broken
            reference plane)
        ERC won't catch these: the wire is connected, the names just
        disagree on intent. A class of bug that ships when the user
        copied a sub-circuit and forgot to re-tag the rail.

        Pattern: ports the dashboard's JS-side analyzePinNetMismatch
        as an authoritative MCP audit.

        Returns:
            Dict with `{checked, violations, items[]}` where each item
            carries `{designator, mismatches[]}` with one entry per
            offending pin: `{pin, name, net, kind}`. `kind` is
            ``power_pin_on_non_power_net`` or
            ``ground_pin_on_non_ground_net``.
        """
        bridge = get_bridge()
        bom = await bridge.send_command_async(
            "project.get_bom", {"limit": "5000"})
        if not isinstance(bom, dict):
            return {"ok": False, "reason": "bom unavailable"}
        return find_pin_net_name_mismatches_from_bom(bom)

    @mcp.tool()
    async def audit_find_missing_decoupling() -> dict[str, Any]:
        """Find ICs whose power pins lack a nearby decoupling cap.

        For every IC, identify its power pins (by net name or by pin
        name pattern). For each power pin, check if any capacitor
        (designator C*) shares the same net. Buckets:

          - ``missing``: no power pin has any cap on its net (worst)
          - ``partial``: some power pins covered, some not

        ICs with full coverage are not surfaced (no problem to fix).
        ICs with NO recognised power pins are skipped (we can't
        distinguish "no power needed" from "couldn't detect").

        Catches the single most-skipped pre-release review check: a
        missing local bypass doesn't fail ERC, doesn't fail DRC, but
        bites at first power-on with brown-outs / random resets.

        Pattern: ports the dashboard's JS-side analyzeDecoupling.

        Returns:
            Dict with `{checked, violations, items[]}` where each item
            is `{designator, status, covered_pin_count, uncovered_pins[]}`.
            ``status`` is ``missing`` or ``partial``. ``uncovered_pins[]``
            lists each `{pin, name, net}` that needs a cap.
        """
        bridge = get_bridge()
        bom = await bridge.send_command_async(
            "project.get_bom", {"limit": "5000"})
        if not isinstance(bom, dict):
            return {"ok": False, "reason": "bom unavailable"}
        return find_missing_decoupling_from_bom(bom)

    @mcp.tool()
    async def audit_find_unconnected_ic_pins() -> dict[str, Any]:
        """Find IC pins with an empty / unset net (excluding pins
        intentionally named NC / DNC / RSVD / RESERVED).

        Walks the project BOM (cheap: uses the already-cached
        ``project.get_bom`` snapshot, no extra Altium round-trip) and,
        for every component whose designator starts with ``U``, counts
        pins whose `net` is empty / null / "?". Pins whose NAME marks
        them as no-connect by convention are skipped because those
        aren't bugs.

        Same heuristic the Components tab's "unconnected IC pins" chip
        runs in JS, but now exposed as a first-class MCP tool so the
        agent can drill in from one call and the result is consistent
        between the dashboard and the lint sweep.

        Returns:
            Dict with:
              - ``checked``: ICs inspected
              - ``violations``: ICs with at least one unconnected pin
                (the COUNT here is per-IC, not per-pin)
              - ``unconnected_pin_total``: sum of unconnected pins
                across all flagged ICs
              - ``items``: per-IC `{designator, unconnected_pins[]}`
                listing pin numbers + names that were flagged.
        """
        bridge = get_bridge()
        bom = await bridge.send_command_async(
            "project.get_bom", {"limit": "5000"})
        if not isinstance(bom, dict):
            return {"ok": False, "reason": "bom unavailable"}
        return find_unconnected_ic_pins_from_bom(bom)

    @mcp.tool()
    async def audit_component_param_visibility() -> dict[str, Any]:
        """Check that every placed component shows the parameters its class
        requires for a legible schematic and a procurable BOM.

        Per class, by designator: C* shows a value ending in ``F`` and a
        voltage ending in ``V``; R* a value ending in ``R``, ``k``, ``M``
        or ``m``; L* an inductance ending in ``H`` and a saturation
        current ending in ``A``; U* a visible parameter whose name starts
        with ``Manufacturer Part Number``.

        A visible parameter or the Comment satisfies a rule; a hidden one
        does not. Runs on the compiled (flattened) tree, so hierarchical
        and multichannel designs are covered.

        Returns:
            Dict with:
              - ``checked``: how many components matched a known class
              - ``violations``: how many of those have at least one
                missing required value
              - ``items``: list of `{designator, class, missing[]}`
                violation entries. ``missing`` enumerates which required
                fields are absent (``capacitance``, ``voltage``,
                ``resistance``, ``inductance``, ``saturation_current``,
                ``mpn``).
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.validate_component_params",
            {},
        )

    @mcp.tool()
    async def audit_power_port_orientation() -> dict[str, Any]:
        """Check that schematic power-symbol orientations follow the standard
        convention so reviewers' eyes can scan the sheet quickly.

        Rules:
          - Ground symbols (``ePowerGndPower``, ``ePowerGndSignal``,
            ``ePowerGndEarth``) MUST face down (Orientation = 270deg).
          - Power bars (``ePowerBar``, used for VCC/VDD/V+ rails)
            MUST face up (Orientation = 90deg).

        Stylistic but high signal: a flipped ground symbol or a sideways
        rail is invariably an editing accident, and one violation can hide
        a real wiring issue under it because the eye keeps wanting to fix
        the orientation first.

        Returns:
            Dict with:
              - ``checked``: total power-symbols inspected (grounds + bars)
              - ``violations``: count with wrong orientation
              - ``items``: list of ``{text, style, expected, actual, sheet}``
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.power_port_orientation",
            {},
        )

    @mcp.tool()
    async def audit_tented_via_ratio() -> dict[str, Any]:
        """Count tented vs untented via SURFACES on the active board and
        report the tented ratio.

        Untented vias expose the via barrel through solder mask; in humid
        or salt-spray environments that risks acid traps and copper
        corrosion. For consumer boards spec is usually "all vias tented
        top + bottom"; for prototyping boards a lower ratio is fine.

        Counts top and bottom surfaces separately (a via reaching only the
        top layer contributes one surface, a through-via two). Buried /
        blind via surfaces that don't reach an outer layer are ignored.

        Returns:
            Dict with:
              - ``total_surfaces``: tented + untented (denominator)
              - ``tented``: surfaces with tenting on
              - ``untented``: surfaces with tenting off
              - ``ratio``: tented / total (0.0 - 1.0; 1.0 = all tented)
              - ``violation_pct``: untented / total * 100
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.tented_via_ratio",
            {},
        )

    @mcp.tool()
    async def audit_find_floating_ports() -> dict[str, Any]:
        """Find schematic ports that aren't actually connected to anything.

        On each schematic sheet a port is "floating" if its net does not
        appear on any component pin OR any sheet-symbol entry on the same
        sheet. A floating port is usually an editing leftover (renamed a
        net but forgot to retag the port) or a never-completed hookup --
        ERC can miss these when the port carries a valid net-label tag
        that just doesn't connect to anything physically.

        Walks the compiled / flattened doc tree so multichannel /
        hierarchical designs are covered.

        Returns:
            Dict with:
              - ``checked``: total port objects inspected
              - ``violations``: floating-port count
              - ``items``: per-violation `{net, sheet, location}`
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_floating_ports",
            {},
        )

    @mcp.tool()
    async def audit_find_bad_connections(
        tolerance_mils: float = 1.0,
    ) -> dict[str, Any]:
        """Find PCB tracks / arcs with endpoints that are close to another
        same-net primitive but don't actually touch.

        An end of a signal-layer track or arc with no same-net track,
        arc, pad or via within ``tolerance_mils`` is dangling: it can look
        connected and still be an open on the fabricated board, and DRC
        does not reliably catch it. Teardrops, component primitives,
        unnetted copper and full circles are skipped.

        Args:
            tolerance_mils: Coord tolerance in mils. 0 = exact match required
                (rare, since router can place tracks on sub-mil grid).
                Defaults to 1 mil; bump to 5 or 10 to surface
                gross misalignments only.

        Returns:
            Dict with:
              - ``checked``: track + arc primitives inspected
              - ``violations``: primitives with at least one dangling endpoint
              - ``tolerance_mils``: echo of the tolerance used
              - ``items``: per-violation `{kind, layer, net, at}` where
                ``at`` is the dangling endpoint's "(x,y)" in mils
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_bad_connections",
            {"tolerance_mils": str(tolerance_mils)},
        )

    @mcp.tool()
    async def audit_find_signal_vias_without_return(
        radius_mils: float = 50.0,
    ) -> dict[str, Any]:
        """Find signal vias that have no nearby ground / power via for
        return-current flow.

        A signal via changing layers needs its return current to cross
        too; this flags signal vias with no ground or power via within
        ``radius_mils``. Ground and power are guessed from the name
        (starts with GND, V, + or -, or contains GND). A proximity check
        only, not aware of the stackup.

        Args:
            radius_mils: How close a return via must be. Default 50.

        Returns:
            Dict with:
              - ``checked``: signal vias inspected (non-power/ground)
              - ``violations``: signal vias with no return via in range
              - ``radius_mils``: echo of radius used
              - ``items``: per-violation `{net, at}` where ``at`` is
                "(x,y)" mils of the offending signal via
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_signal_vias_without_return",
            {"radius_mils": str(radius_mils)},
        )

    @mcp.tool()
    async def audit_find_pads_near_board_edge(
        clearance_mils: float = 25,
    ) -> dict[str, Any]:
        """Find PCB pads / vias closer than ``clearance_mils`` to the
        board outline (depaneling damage hazard).

        Copper near a routed or scored edge can be damaged when the board
        is separated; take the limit from the fab's rules. The gap is
        measured from the outline's lines and arcs to the pad's copper
        at any rotation.

        Args:
            clearance_mils: Minimum gap to flag (default 25).

        Returns:
            Dict with:
              - ``checked``: total pads + vias inspected
              - ``violations``: how many are within the clearance
              - ``unmeasured``: pads that could not be measured, which
                are therefore neither passed nor flagged
              - ``clearance_mils``: echo of the threshold used
              - ``items``: per-violation `{kind, designator,
                distance_mils, at}` where ``at`` is "(x,y)" mils.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_pads_near_board_edge",
            {"clearance_mils": _mils(clearance_mils)},
        )

    @mcp.tool()
    async def audit_find_components_outside_board_outline() -> dict[str, Any]:
        """Find PCB components whose origin sits outside the board outline.

        Editing accident catch: the user grabbed a component and moved
        it off the PCB by mistake. Usually invisible during normal
        review because the component is off-canvas; DRC doesn't catch it
        (DRC is between primitives, not "is this object on the board").

        Uses ``IPCB_BoardOutline.PrimitiveInsidePoly`` for the actual
        polygon-inside test, so non-rectangular boards are handled
        correctly.

        Pattern: brett's SelectCMPInOutSideBOL.pas.

        Returns:
            Dict with:
              - ``checked``: total components inspected
              - ``violations``: components outside the outline
              - ``items``: per-violation `{designator, layer, at}` with
                ``at`` = "(x,y)" mils.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_components_outside_board_outline",
            {},
        )

    @mcp.tool()
    async def audit_find_off_grid_components(
        grid_mils: float = 100,
    ) -> dict[str, Any]:
        """Find schematic components placed off the snap grid.

        Schematic best practice: components sit on a 100-mil (or whatever
        the project standard is) grid so pins land predictably on wire
        endpoints. Off-grid placement is the classic "I edited around it
        and never resnapped" trap: the symbol LOOKS wired but the pin
        doesn't actually touch the wire, ERC silently passes (no visible
        net) and the connection is missing on the netlist.

        Args:
            grid_mils: Grid size to check against (default 100). Use 50
                if your sheets are set to a half-step grid.

        Returns:
            Dict with:
              - ``checked``: total placed components inspected
              - ``violations``: off-grid component count
              - ``grid_mils``: echo of the grid used
              - ``items``: per-violation `{designator, sheet, at, dx,
                dy}` where ``dx``/``dy`` are the off-grid magnitudes in
                mils. Snap to ``-dx`` / ``-dy`` (or whichever direction
                is closer) to restore grid alignment.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_off_grid_components",
            {"grid_mils": _mils(grid_mils)},
        )

    @mcp.tool()
    async def audit_find_removed_pad_shapes() -> dict[str, Any]:
        """Find pads / vias whose copper annular ring has been removed
        on layers where it's still needed.

        Altium's "Tools → Remove Unused Pad Shapes" optimization
        deletes per-layer pad copper where a primitive isn't electrically
        used on that layer. Applied too aggressively, it leaves a via
        that drills through an inner layer with NO annular ring there:
        any later trace edit on that inner layer that tries to land on
        the via will silently fail to connect at fab.

        Detection:
          - Vias: ``Via.SizeOnLayer(L) <= Via.HoleSize`` means no copper
            left, just the drilled hole.
          - Pads: ``Pad.IsPadRemoved(L)`` is True.

        Pattern: brett's PadShapeRemoved.pas (read-only; we surface
        coordinates rather than highlight on canvas).

        Returns:
            Dict with:
              - ``checked``: total pads + vias inspected
              - ``violations``: how many layer-instances are flagged
                (one via removed on 3 layers counts as 3)
              - ``items``: per-violation `{kind, designator, net,
                layer, at}` where ``at`` is "(x,y)" mils.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_removed_pad_shapes",
            {},
        )

    @mcp.tool()
    async def audit_find_designator_collisions() -> dict[str, Any]:
        """Find schematic components that share a designator across sheets.

        Annotation should make every designator unique across the project,
        but paste-from-another-sheet operations and post-annotate edits
        can leave duplicates that the agent / reviewer want to surface
        before they hit ERC.

        Walks source schematic sheets (not compiled docs: compiled
        flattening would auto-disambiguate multichannel instances with
        channel suffixes, hiding the real source-level collision).

        Returns:
            Dict with:
              - ``checked``: total components inspected
              - ``violations``: count of distinct designators that occur
                more than once
              - ``items``: per-collision `{designator, count, sheets}`
                where ``sheets`` is a comma-joined sheet-filename list
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_designator_collisions",
            {},
        )

    @mcp.tool()
    async def audit_find_via_antennas() -> dict[str, Any]:
        """Find vias connected on only one layer (resonating stubs).

        A via connected on one layer is an open stub, often left when the
        trace on its other side was rerouted. Connected means copper
        touching it on a signal layer, or a plane connection. Lists them;
        selects nothing.

        Returns:
            Dict with:
              - ``checked``: total vias inspected
              - ``violations``: antenna-via count
              - ``items``: per-violation `{net, at, connected_layers}`
                where ``at`` is "(x,y)" mils and ``connected_layers``
                will always be 1.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_via_antennas",
            {},
        )

    @mcp.tool()
    async def audit_find_unmatched_ports() -> dict[str, Any]:
        """Find schematic nets with port-direction mismatches across sheets.

        Catches the two cases that matter most:

          - **multi_output**: a net has MORE THAN ONE Output port. Two
            drivers fighting for the same net: almost always a wiring
            error or copy-paste mistake.
          - **no_driver**: a net has Input ports but ZERO Output (or
            Bidirectional) ports anywhere in the project. Orphan signal:
            something is listening but nothing is talking.

        Walks compiled docs so multichannel designs are covered. Ports
        are grouped by their flattened net name, so net-label renames
        keep ports correctly clustered.

        Returns:
            Dict with:
              - ``checked``: total port objects inspected
              - ``violations``: net count with at least one issue
              - ``items``: per-net `{net, issue, output_count,
                input_count}`. ``issue`` is ``multi_output`` or
                ``no_driver``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_unmatched_ports",
            {},
        )

    @mcp.tool()
    async def audit_variant_not_fitted() -> dict[str, Any]:
        """List components marked Not Fitted in the project's CURRENT variant.

        Not-fitted parts should get no stencil paste.
        ``pcb_apply_dnp_paste_exclusion`` removes it, after this list has
        been reviewed.

        Returns:
            Dict with:
              - ``variant``: the current variant's description (empty
                when the project has no variant selected: DNP only
                applies to a variant)
              - ``checked``: total flattened-project components
              - ``violations``: how many are NotFitted in this variant
              - ``items``: per-violation `{designator, comment, unique_id}`
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.variant_not_fitted",
            {},
        )

    @mcp.tool()
    async def audit_find_invalid_regions() -> dict[str, Any]:
        """Find PCB polygon regions whose area is zero or unset.

        These are leftover from cancelled polygon-pour operations or
        corrupted file imports. They don't render visibly but can throw
        DRC violations under "Modified Polygon" rules and bloat board
        size. Read-only: this tool only surfaces them. Auto-delete
        belongs in a separate user-confirmed mutating flow because
        misclassification could remove a real (legitimately small)
        region.

        Returns:
            Dict with:
              - ``checked``: regions inspected (skips primitives owned
                by components or dimensions)
              - ``violations``: invalid-region count
              - ``items``: per-violation `{layer, at}`
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "audit.find_invalid_regions",
            {},
        )
