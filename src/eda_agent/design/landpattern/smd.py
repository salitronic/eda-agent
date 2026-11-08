# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Surface-mount families: lands from IPC-7351B, placed in zero orientation.

Zero orientation (IPC-7351B, Hausherr IPC paper "IPC Zero Component
Orientations"): two-terminal parts have pin 1 on the left; SOT, SOIC,
QFP and QFN have pin 1 at the upper left; PLCC has pin 1 at the top
centre; BGA has A1 at the upper left. Rows on the left and right number
counter-clockwise from pin 1.
"""

from __future__ import annotations

from typing import Callable, Optional

from eda_agent.design.landpattern import ipc7351 as ipc
from eda_agent.design.landpattern.builder import Builder
from eda_agent.design.landpattern.model import Polygon
from eda_agent.design.landpattern.spec import Dim, SpecError, dim

__all__ = ["FAMILY_BUILDERS", "bga_row_label"]


# --- shared pieces ------------------------------------------------------------


def _gullwing(b: Builder, name: str, L: Dim, T: Dim, W: Dim,
              pitch: Optional[float], body_across: Optional[Dim]) -> ipc.LandCalc:
    """One gull-wing row pair: picks coarse/fine and gull-wing/outward-L."""
    s_min = L.min - 2.0 * T.max
    form = b.spec.lead_form or ipc.gullwing_form(
        pitch=pitch, s_min=s_min,
        body_across_max=body_across.max if body_across is not None else None,
        t_tol=T.tol)
    goals = ipc.goal_table(form).at(b.d)
    calc = ipc.land_calc(L.pair, T.pair, W.pair, goals, F=b.F, P=b.P,
                         lead_form=form, density=b.d)
    b.record(name, calc, form)
    if form.startswith("outward_l"):
        b.notes.append(
            f"{name}: outward L-bend leads (Smin {s_min:.3f} <= body "
            f"{body_across.max if body_across else 0:.3f} and Ttol "
            f"{T.tol:.3f} < 0.5), reduced heel goal")
    return calc


def _plain(b: Builder, name: str, form: str, L: Dim, T: Dim, W: Dim, *,
           small: bool = False) -> ipc.LandCalc:
    goals = ipc.goal_table(form).at(b.d)
    calc = ipc.land_calc(
        L.pair, T.pair, W.pair, goals, F=b.F, P=b.P,
        size_round=ipc.SMALL_LAND_SIZE_ROUND if small else ipc.LAND_SIZE_ROUND,
        span_round=ipc.SMALL_LAND_SPAN_ROUND if small else ipc.LAND_SPAN_ROUND,
        nominal_terminal=small, t_nom=T.nom, lead_form=form, density=b.d)
    b.record(name, calc, form)
    return calc


def _rows(b: Builder, calc: ipc.LandCalc, left: list, right: list,
          w: Optional[float] = None) -> None:
    """Lands for (number, y) lists on the left and right of the span."""
    xl, xr = -calc.span / 2.0, calc.span / 2.0
    width = calc.pad_width if w is None else w
    for num, y in left:
        b.pad(num, xl, y, calc.pad_length, width, group="left")
    for num, y in right:
        b.pad(num, xr, y, calc.pad_length, width, group="right")


def _column(n: int, pitch: float) -> list[float]:
    """Y positions top to bottom for n lands at ``pitch``."""
    top = (n - 1) * pitch / 2.0
    return [round(top - i * pitch, 6) for i in range(n)]


def _need(d: Optional[Dim], what: str) -> Dim:
    if d is None:
        raise SpecError(f"missing {what}")
    return d


def _two_terminal(b: Builder, calc: ipc.LandCalc, polarized: bool) -> None:
    h = calc.span / 2.0
    b.pad("1", -h, 0.0, calc.pad_length, calc.pad_width, group="left")
    b.pad("2", h, 0.0, calc.pad_length, calc.pad_width, group="right")
    b.marker = polarized
    b.chamfer = polarized
    p1 = b.pads[0]
    b.pin1_candidates = b.cands_row_end_then_toe(p1, "two")


# --- two-terminal families ------------------------------------------------


def _chip(b: Builder) -> None:
    """Chip R/C/L. L = lead_span_x (component length), T = lead_length (end
    band), W = lead_width (component width). IPC-7351B Tables 3-5 / 3-6;
    parts with a nominal length under 1.6 mm (smaller than 1608) use the
    small-chip table, rounding and nominal T (Hausherr IPC paper)."""
    s = b.spec
    L, T, W = s.lead_span_x, s.lead_length, s.lead_width
    small = L.nom < 1.6 - 1e-6
    calc = _plain(b, "chip", "chip_small" if small else "chip", L, T, W,
                  small=small)
    b.excess = calc.goals.courtyard
    _two_terminal(b, calc, bool(s.polarized))
    b.set_body((s.body_x or L).max, (s.body_y or W).max)


def _melf(b: Builder) -> None:
    """MELF. L = lead_span_x (body length), T = lead_length (end cap), W =
    lead_width (diameter). IPC-7351:2005 Table 3-7."""
    s = b.spec
    calc = _plain(b, "melf", "melf", s.lead_span_x, s.lead_length, s.lead_width)
    b.excess = calc.goals.courtyard
    _two_terminal(b, calc, bool(s.polarized))
    b.set_body((s.body_x or s.lead_span_x).max, (s.body_y or s.lead_width).max)


def _molded(b: Builder) -> None:
    """Molded body with inward L leads (SMA/SMB/SMC, tantalum, molded
    inductors). L = lead_span_x (overall length), T = lead_length (terminal
    under the body), W = lead_width. IPC-7351:2005 Table 3-11."""
    s = b.spec
    calc = _plain(b, "molded", s.lead_form or "inward_l", s.lead_span_x,
                  s.lead_length, s.lead_width)
    b.excess = calc.goals.courtyard
    _two_terminal(b, calc, True if s.polarized is None else s.polarized)
    b.set_body((s.body_x or s.lead_span_x).max, (s.body_y or s.lead_width).max)


def _sod(b: Builder) -> None:
    """Two-lead gull wing (SOD-123, SOD-323): pin 1 (cathode) on the left.
    body_x is the body length between the leads."""
    s = b.spec
    calc = _gullwing(b, "sod", s.lead_span_x, s.lead_length, s.lead_width,
                     None, s.body_x)
    b.excess = calc.goals.courtyard
    _two_terminal(b, calc, True if s.polarized is None else s.polarized)
    b.set_body(s.body_x.max, (s.body_y or s.lead_width).max)


def _crystal2(b: Builder) -> None:
    """Two-pad crystal with bottom terminations at the ends: no-lead goals
    (IPC-7351:2005 Table 3-14). IPC-7351B Table 3-20 covers underbody L
    crystals and could not be checked publicly; bottom-terminated ceramic
    crystals are closest to the no-lead family."""
    s = b.spec
    L = s.lead_span_x or s.body_x
    calc = _plain(b, "crystal", "son", L, s.lead_length, s.lead_width)
    b.excess = calc.goals.courtyard
    _two_terminal(b, calc, bool(s.polarized))
    b.set_body(s.body_x.max, s.body_y.max)
    b.notes.append("crystal2: no-lead goals used (IPC-7351B Table 3-20 not "
                   "publicly checkable)")


# --- two-row families -------------------------------------------------------


def _soic(b: Builder) -> None:
    """SOIC, SOP, SSOP, TSSOP, MSOP, with or without an exposed pad.

    Pin 1 top of the left row, counter-clockwise. body_x = body between the
    rows (JEDEC E1), body_y = body length (D), lead_span_x = tip to tip (E).
    """
    s = b.spec
    p = s.pitch.nom
    calc = _gullwing(b, "rows", s.lead_span_x, s.lead_length, s.lead_width, p,
                     s.body_x)
    b.excess = calc.goals.courtyard
    per = s.pins // 2
    ys = _column(per, p)
    left = [(str(i + 1), ys[i]) for i in range(per)]
    right = [(str(per + j + 1), ys[per - 1 - j]) for j in range(per)]
    _rows(b, calc, left, right)
    b.add_ep(str(s.pins + 1))
    b.set_body(s.body_x.max, s.body_y.max)
    b.pin1_candidates = b.cands_row_end_then_toe(b.pads[0], "left")


def _sot23(b: Builder) -> None:
    """SOT-23-3, -5 and -6 (pitch e, normally 0.95).

    SOT-23-3: 1 and 2 on the left at +e and -e, 3 on the right at 0.
    SOT-23-5: 1, 2, 3 on the left; 4 on the right opposite 3 and 5 opposite
    1 (TI DBV0005A, Nexperia SOT753): never centred between them.
    SOT-23-6: 1, 2, 3 on the left; 4, 5, 6 on the right, bottom to top.
    """
    s = b.spec
    e = s.pitch.nom
    calc = _gullwing(b, "rows", s.lead_span_x, s.lead_length, s.lead_width, e,
                     s.body_x)
    b.excess = calc.goals.courtyard
    if s.pins == 3:
        left, right = [("1", e), ("2", -e)], [("3", 0.0)]
    elif s.pins == 5:
        left = [("1", e), ("2", 0.0), ("3", -e)]
        right = [("4", -e), ("5", e)]
    else:
        left = [("1", e), ("2", 0.0), ("3", -e)]
        right = [("4", -e), ("5", 0.0), ("6", e)]
    _rows(b, calc, left, right)
    b.set_body(s.body_x.max, s.body_y.max)
    b.pin1_candidates = b.cands_row_end_then_toe(b.pads[0], "left")


def _son(b: Builder) -> None:
    """SON / DFN (two rows, no leads), optional exposed pad.
    lead_span_x defaults to body_x (the terminals end at the body edge)."""
    s = b.spec
    p = s.pitch.nom
    L = s.lead_span_x or s.body_x
    calc = _plain(b, "rows", s.lead_form or "son", L, s.lead_length,
                  s.lead_width)
    b.excess = calc.goals.courtyard
    per = s.pins // 2
    ys = _column(per, p)
    left = [(str(i + 1), ys[i]) for i in range(per)]
    right = [(str(per + j + 1), ys[per - 1 - j]) for j in range(per)]
    _rows(b, calc, left, right)
    b.add_ep(str(s.pins + 1))
    b.set_body(s.body_x.max, s.body_y.max)
    b.pin1_candidates = b.cands_row_end_then_toe(b.pads[0], "left")


def _crystal4(b: Builder) -> None:
    """Four-pad crystal: two lands per side ``pitch`` apart in Y, numbered
    counter-clockwise from the upper left (1 upper left, 2 lower left,
    3 lower right, 4 upper right). No-lead goals, as ``crystal2``."""
    s = b.spec
    p = s.pitch.nom
    L = s.lead_span_x or s.body_x
    calc = _plain(b, "rows", "son", L, s.lead_length, s.lead_width)
    b.excess = calc.goals.courtyard
    _rows(b, calc, [("1", p / 2), ("2", -p / 2)], [("3", -p / 2), ("4", p / 2)])
    b.set_body(s.body_x.max, s.body_y.max)
    b.pin1_candidates = b.cands_row_end_then_toe(b.pads[0], "left")
    b.notes.append("crystal4: no-lead goals used (IPC-7351B Table 3-19 corner "
                   "concave not publicly checkable)")


def _sot223(b: Builder) -> None:
    """SOT-223: leads 1..n on the left (n = pins, default 3), the tab on the
    right numbered ``tab_number`` (default n + 1). The tab is a gull-wing
    lead like the others; with ``tab_length`` it gets its own T."""
    s = b.spec
    n = s.pins or 3
    p = s.pitch.nom
    if s.tab_length is None:
        calc = _gullwing(b, "rows", s.lead_span_x, s.lead_length, s.lead_width,
                         p, s.body_x)
        b.excess = calc.goals.courtyard
        ys = _column(n, p)
        _rows(b, calc, [(str(i + 1), ys[i]) for i in range(n)], [])
        x_raw, x_tab = ipc.width_calc(s.tab_width.pair, calc.goals.side,
                                      F=b.F, P=b.P)
        tab_num = s.tab_number or str(n + 1)
        b.pad(tab_num, calc.span / 2.0, 0.0, calc.pad_length, x_tab,
              role="tab", group="right")
        b.calcs["rows"]["tab_width_raw"] = x_raw
        b.calcs["rows"]["tab_width"] = x_tab
    else:
        _asym_tab(b, n, p, lead_table=None, tab_table="gullwing")
    b.set_body(s.body_x.max, s.body_y.max)
    b.pin1_candidates = b.cands_row_end_then_toe(b.pads[0], "left")


def _asym_tab(b: Builder, n_slots: int, p: float, *,
              lead_table: Optional[str], tab_table: str,
              lead_numbers: Optional[tuple[str, ...]] = None,
              tab_number: Optional[str] = None,
              center_width: Optional[Dim] = None,
              body_from_tab: bool = False) -> ipc.AsymLandCalc:
    """Leads on the left, one tab on the right, different T each side."""
    s = b.spec
    L, T1, T2 = s.lead_span_x, s.lead_length, s.tab_length
    if lead_table is None:
        if body_from_tab:
            # Outward L test on the lead side: is the most inward heel under
            # the body? Here the body runs from the tab edge (+L/2) inward,
            # so compare distances from the span centre on the lead side.
            heel = L.min / 2.0 - T1.max
            body_face = (s.body_x.max if s.body_x else L.nom) - L.nom / 2.0
            s_equiv, body_eq = 2.0 * heel, 2.0 * body_face
        else:
            s_equiv = L.min - 2.0 * T1.max
            body_eq = s.body_x.max if s.body_x else None
        lead_table = s.lead_form or ipc.gullwing_form(
            pitch=p, s_min=s_equiv, body_across_max=body_eq, t_tol=T1.tol)
    g1 = ipc.goal_table(lead_table).at(b.d)
    g2 = ipc.goal_table(tab_table).at(b.d)
    calc = ipc.land_calc_asym(L.pair, T1.pair, T2.pair, g1, g2, F=b.F, P=b.P,
                              t1_nom=T1.nom, t2_nom=T2.nom)
    x1_raw, x1 = ipc.width_calc(s.lead_width.pair, g1.side, F=b.F, P=b.P)
    x2_raw, x2 = ipc.width_calc(s.tab_width.pair, g2.side, F=b.F, P=b.P)
    xc = None
    if center_width is not None:
        xc_raw, xc = ipc.width_calc(center_width.pair, g1.side, F=b.F, P=b.P)
    b.record("leads_and_tab", calc, None, lead_table=lead_table,
             tab_table=tab_table, lead_width_raw=x1_raw, lead_width=x1,
             tab_width_raw=x2_raw, tab_width=x2,
             center_lead_width=xc)
    for k in (lead_table, tab_table):
        t = ipc.goal_table(k)
        b.tables[k] = {"title": t.title, "source": t.source}
    b.excess = max(g1.courtyard, g2.courtyard)
    ys = _column(n_slots, p)
    nums = lead_numbers or tuple(str(i + 1) for i in range(n_slots))
    if len(nums) != n_slots:
        raise SpecError(f"lead_numbers needs {n_slots} entries (use '' for a "
                        f"cropped lead), got {len(nums)}")
    mid = (n_slots - 1) / 2.0
    for i, num in enumerate(nums):
        if not num:
            continue
        w = xc if (xc is not None and abs(i - mid) < 1e-9) else x1
        b.pad(num, calc.centre1, ys[i], calc.length1, w, group="left")
    tnum = tab_number or s.tab_number
    if not tnum:
        empty = [i for i, num in enumerate(nums) if not num]
        tnum = str(empty[0] + 1) if len(empty) == 1 else str(n_slots + 1)
    b.pad(tnum, calc.centre2, 0.0, calc.length2, x2, role="tab", group="right")
    if calc.G <= 0:
        b.notes.append(
            f"tab and lead row meet across the gap (G = {calc.G:.3f} mm); "
            "the tab land shares its number with the lead it is tied to")
    return calc


def _sot89(b: Builder) -> None:
    """SOT-89: leads 1, 2, 3 on the left (pitch e1), the tab on the right
    tied to pin 2. lead_span_x is the overall span from the lead tips to the
    tab edge, lead_length the lead foot, tab_length the exposed tab from
    its outer edge (Diodes H1), tab_width the exposed tab width (Diodes
    D2). Flat lug goals (IPC-7351:2005 Table 3-12) on both sides; an
    optional ``options['center_lead_width']`` sizes the wider pin 2 lead."""
    s = b.spec
    cw = dim(s.options.get("center_lead_width")) if s.options else None
    _asym_tab(b, 3, s.pitch.nom, lead_table="flat_lug", tab_table="flat_lug",
              tab_number=s.tab_number or "2", center_width=cw)
    # The leads leave the body at their foot length from the tips, so the
    # body sits nearer the tab than the span centre; the origin goes to the
    # body centre, as for DPAK.
    L = s.lead_span_x
    cx_body = -L.nom / 2.0 + s.lead_length.nom + s.body_x.nom / 2.0
    b.set_body(s.body_x.max, s.body_y.max, cx=cx_body)
    b.offset = (-cx_body, 0.0)
    b.pin1_candidates = b.cands_row_end_then_toe(b.pads[0], "left")


def _dpak(b: Builder) -> None:
    """DPAK (TO-252) and D2PAK (TO-263): leads on the left in ``pins``
    slots at ``pitch`` (``lead_numbers`` names them, '' for a cropped lead),
    the tab on the right. lead_span_x is the overall span (lead tips to tab
    edge), tab_length the exposed tab from its outer edge, body_x the body
    from the tab edge to the lead-side body face. Leads take the gull-wing
    goals, the tab the flat lug goals. The origin is the body centre, not
    the land pattern centre (Hausherr IPC paper, "Land Pattern Origins")."""
    s = b.spec
    _asym_tab(b, s.pins, s.pitch.nom, lead_table=None, tab_table="flat_lug",
              lead_numbers=s.lead_numbers, body_from_tab=True)
    L = s.lead_span_x
    bx = s.body_x.max
    cx_body = L.nom / 2.0 - s.body_x.nom / 2.0
    b.set_body(bx, s.body_y.max, cx=cx_body)
    b.offset = (-cx_body, 0.0)
    first_lead = next(p for p in b.pads if p.group == "left")
    b.pin1_name = first_lead.number
    b.pin1_candidates = b.cands_row_end_then_toe(first_lead, "left")


# --- quad families ------------------------------------------------------------

#: Pin 1 positions for quad parts.
_PIN1_MODES = ("left_top", "top_left", "top_center")


def _quad(b: Builder, calc_x: ipc.LandCalc, calc_y: ipc.LandCalc,
          counts: tuple[int, int, int, int], pitch: float, pitch_y: float,
          mode: str) -> None:
    nl, nb, nr, nt = counts
    hx, hy = calc_x.span / 2.0, calc_y.span / 2.0
    seq: list[tuple[float, float, str]] = []
    for y in _column(nl, pitch_y):
        seq.append((-hx, y, "left"))
    for x in reversed(_column(nb, pitch)):
        seq.append((x, -hy, "bottom"))
    for y in reversed(_column(nr, pitch_y)):
        seq.append((hx, y, "right"))
    for x in _column(nt, pitch):
        seq.append((x, hy, "top"))
    n = len(seq)
    if mode not in _PIN1_MODES:
        raise SpecError(f"pin1 must be one of {', '.join(_PIN1_MODES)}, got {mode!r}")
    if mode == "left_top":
        start = 0
    elif mode == "top_left":
        if nt == 0:
            raise SpecError("pin1 'top_left' needs lands on the top side")
        start = n - 1
    else:
        if nt == 0:
            raise SpecError("pin1 'top_center' needs lands on the top side")
        start = nl + nb + nr + nt // 2
    pin1_pad = None
    pin1_side = "left"
    for i, (x, y, side) in enumerate(seq):
        num = str((i - start) % n + 1)
        if side in ("left", "right"):
            w, h = calc_x.pad_length, calc_x.pad_width
        else:
            w, h = calc_y.pad_width, calc_y.pad_length
        p = b.pad(num, x, y, w, h, group=side)
        if num == "1":
            pin1_pad, pin1_side = p, side
    b.pin1_candidates = b.cands_row_end_then_toe(pin1_pad, pin1_side)


def _quad_counts(b: Builder) -> tuple[int, int, int, int]:
    s = b.spec
    if s.pins_per_side is not None:
        c = s.pins_per_side
        return (c[0], c[1], c[2], c[3])
    q = s.pins // 4
    return (q, q, q, q)


def _qfn(b: Builder) -> None:
    """QFN, square or rectangular. body_x/body_y are the body (and, unless
    lead_span_x/_y say otherwise, the terminal span). ``pins_per_side`` is
    (left, bottom, right, top); ``pin1`` is ``left_top`` (default),
    ``top_left`` or ``top_center``."""
    s = b.spec
    form = s.lead_form or "nolead"
    Lx = s.lead_span_x or s.body_x
    Ly = s.lead_span_y or s.body_y
    cx = _plain(b, "rows_x", form, Lx, s.lead_length, s.lead_width)
    cy = _plain(b, "rows_y", form, Ly, s.lead_length, s.lead_width)
    b.excess = cx.goals.courtyard
    p = s.pitch.nom
    py = (s.pitch_y or s.pitch).nom
    mode = "left_top" if s.pin1 == "default" else s.pin1
    _quad(b, cx, cy, _quad_counts(b), p, py, mode)
    b.add_ep(str(s.pins + 1))
    b.set_body(s.body_x.max, s.body_y.max)


def _qfp(b: Builder) -> None:
    """QFP / LQFP / TQFP. lead_span_x/_y tip to tip, body_x/_y the body."""
    s = b.spec
    p = s.pitch.nom
    Lx = s.lead_span_x
    Ly = s.lead_span_y or s.lead_span_x
    bx, by = s.body_x, s.body_y
    cx = _gullwing(b, "rows_x", Lx, s.lead_length, s.lead_width, p, bx)
    cy = _gullwing(b, "rows_y", Ly, s.lead_length, s.lead_width, p, by)
    b.excess = cx.goals.courtyard
    py = (s.pitch_y or s.pitch).nom
    mode = "left_top" if s.pin1 == "default" else s.pin1
    _quad(b, cx, cy, _quad_counts(b), p, py, mode)
    b.add_ep(str(s.pins + 1))
    b.set_body(bx.max, by.max)


def _plcc(b: Builder) -> None:
    """PLCC (J leads on four sides), pin 1 at the top centre by default.
    lead_length is the J lead's contact length T. J-lead goals from
    IPC-7351:2005 Table 3-4 (the B table could not be checked)."""
    s = b.spec
    Lx = s.lead_span_x
    Ly = s.lead_span_y or s.lead_span_x
    cx = _plain(b, "rows_x", s.lead_form or "jlead", Lx, s.lead_length,
                s.lead_width)
    cy = _plain(b, "rows_y", s.lead_form or "jlead", Ly, s.lead_length,
                s.lead_width)
    b.excess = cx.goals.courtyard
    p = s.pitch.nom
    mode = "top_center" if s.pin1 == "default" else s.pin1
    _quad(b, cx, cy, _quad_counts(b), p, (s.pitch_y or s.pitch).nom, mode)
    b.set_body(s.body_x.max, s.body_y.max)


# --- grid families ----------------------------------------------------------------

#: JEDEC ball rows A..Y without I, O, Q, S, X, Z, then AA, AB, ...
_BGA_LETTERS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "IOQSXZ"]


def bga_row_label(i: int) -> str:
    """Row label for a 0-based row index (A ... Y, AA, AB, ...)."""
    n = len(_BGA_LETTERS)
    if i < n:
        return _BGA_LETTERS[i]
    return _BGA_LETTERS[i // n - 1] + _BGA_LETTERS[i % n]


#: Where ball A1 sits, seen from the top: (left?, top?).
_A1_CORNERS = {"top_left": (True, True), "top_right": (False, True),
               "bottom_left": (True, False), "bottom_right": (False, False)}


def _grid(b: Builder, land_w: float, land_h: float, shape: str) -> None:
    """Lands on a rows x cols grid (rows along Y, cols along X). By default
    the letters run down the rows and A1 is top left; datasheets differ, so
    ``options['letters']`` puts the letters along "y" (default) or "x", and
    ``options['a1']`` puts A1 at top_left (default), top_right, bottom_left
    or bottom_right, both as the top view shows them. ``depopulated`` drops
    positions by name. ``options['names']`` renames the positions in
    letter-major order (A1, A2, ..., B1, ...), '' dropping one: an LGA whose
    pins are numbered 1..n rather than by grid position."""
    s = b.spec
    px = s.pitch.nom
    py = (s.pitch_y or s.pitch).nom
    opts = s.options or {}
    letters = str(opts.get("letters", "y")).lower()
    corner = str(opts.get("a1", "top_left")).lower()
    if letters not in ("x", "y"):
        raise SpecError("options['letters'] is 'y' (letters down the rows) or 'x' "
                        "(letters along the columns)")
    if corner not in _A1_CORNERS:
        raise SpecError("options['a1'] is one of " + ", ".join(_A1_CORNERS))
    left, top = _A1_CORNERS[corner]
    n_letters = s.rows if letters == "y" else s.cols
    n_numbers = s.cols if letters == "y" else s.rows
    grid_names = [bga_row_label(li) + str(ni + 1)
                  for li in range(n_letters) for ni in range(n_numbers)]
    unknown = [d for d in s.depopulated if d not in grid_names]
    if unknown:
        raise SpecError(f"depopulated positions not in the grid: {', '.join(unknown)}")
    rename = opts.get("names")
    if rename is not None and len(rename) != len(grid_names):
        raise SpecError(f"options['names'] needs {len(grid_names)} entries "
                        f"(rows x cols, letter-major), got {len(rename)}")
    first_name = None
    for li in range(n_letters):
        for ni in range(n_numbers):
            k = li * n_numbers + ni
            name = grid_names[k]
            if name in s.depopulated:
                continue
            if rename is not None:
                name = str(rename[k])
                if not name:
                    continue
            xi, yi = (ni, li) if letters == "y" else (li, ni)
            if not left:
                xi = s.cols - 1 - xi
            if not top:
                yi = s.rows - 1 - yi
            x = -(s.cols - 1) * px / 2.0 + xi * px
            y = (s.rows - 1) * py / 2.0 - yi * py
            b.pad(name, x, y, land_w, land_h, shape=shape, group="grid")
            if first_name is None:
                first_name = name
    b.pin1_name = "1" if rename is not None else "A1"
    if not any(p.number == b.pin1_name for p in b.pads):
        b.pin1_name = first_name or b.pads[0].number
    b.set_body(s.body_x.max, s.body_y.max)
    x0, y0, x1, y1 = b.body
    rr = b.dot_radius()
    c = b.rules.silk_clearance
    # The mark goes outside the body corner nearest pin 1 (A1 top left for
    # a BGA; wherever pin 1 is for a renamed grid).
    p1 = next(p for p in b.pads if p.number == b.pin1_name)
    sx = -1.0 if p1.x <= 0 else 1.0
    sy = 1.0 if p1.y >= 0 else -1.0
    ex = x0 if sx < 0 else x1
    ey = y1 if sy > 0 else y0
    b.pin1_candidates = [(ex + sx * (c + rr), ey - sy * rr),
                         (ex - sx * rr, ey + sy * (c + rr))]


def _bga(b: Builder) -> None:
    """BGA, full or depopulated (``depopulated`` lists ball names). Land
    diameter from the IPC-7351B ball tables (MMC unless
    ``options['bga_land'] == 'nominal'``), courtyard from Table 3-17."""
    s = b.spec
    collapsing = s.ball_type == "collapsing"
    use_mmc = (s.options or {}).get("bga_land", "mmc") != "nominal"
    info = ipc.bga_land(s.ball_diameter.nom, collapsing=collapsing,
                        use_mmc=use_mmc)
    land = info["land"]
    vendor_land = s.vendor.land_diameter if s.vendor else None
    if vendor_land:
        # The manufacturer's land pattern comes first; IPC only fills in
        # where the datasheet gives none.
        info = dict(info, land=vendor_land, ipc_land=land, in_table=True)
        land = vendor_land
    b.record("ball_land", info)
    b.calcs["ball_land"]["source"] = (
        "the manufacturer's recommended land diameter" if vendor_land else
        "IPC-7351B land approximation for "
        + ("collapsible" if collapsing else "non-collapsible")
        + " solder balls (Hausherr CADlib pp. 34-35)")
    if not info["in_table"]:
        b.warnings.append(info["note"])
    b.excess = ipc.BGA_COURTYARD[b.d]
    b.tables["bga"] = {"title": "Ball grid array courtyard",
                       "source": "IPC-7351B Table 3-17 (Hausherr CADlib p. 36)"}
    _grid(b, land, land, "circle")


def _lga(b: Builder) -> None:
    """LGA (grid of flat lands). Lands 1:1 with the maximum terminal
    ("The land (pad) size can be the same as the maximum bump lead
    diameter", Hausherr CADlib p. 29); courtyard from the no-lead table.
    IPC-7351B Table 3-21 could not be checked publicly."""
    s = b.spec
    w = s.lead_width.max
    h = (s.lead_length or s.lead_width).max
    shape = (s.options or {}).get("land_shape", "roundrect")
    b.excess = ipc.goal_table("nolead").at(b.d).courtyard
    b.tables["nolead"] = {"title": ipc.goal_table("nolead").title,
                          "source": ipc.goal_table("nolead").source}
    b.notes.append("lga: lands 1:1 with the maximum terminal; IPC-7351B Table "
                   "3-21 not publicly checkable")
    b.calcs["lga_land"] = {"land": [w, h], "rule": "1:1 with the maximum terminal"}
    _grid(b, w, h, shape)


def _custom(b: Builder) -> None:
    """Explicit lands (``custom_pads``) with a body; nominal courtyard
    excess unless ``options['courtyard_excess']``."""
    s = b.spec
    for cp in s.custom_pads:
        kind = "smd"
        if cp.hole > 0:
            kind = "tht" if cp.plated else "npth"
        b.pad(cp.number, cp.x, cp.y, cp.width, cp.height, shape=cp.shape,
              role=cp.role, group="custom", kind=kind, hole=cp.hole,
              hole_length=cp.hole_length, plated=cp.plated,
              rotation=cp.rotation)
        if cp.outline:
            pts = [tuple(p) for p in cp.outline]
            b.land_outlines += [Polygon("copper", pts, 0.0, filled=True, kind=f"land {cp.number}"),
                                Polygon("mask", pts, 0.0, filled=True, kind=f"land {cp.number}")]
    b.excess = float((s.options or {}).get(
        "courtyard_excess", ipc.goal_table("gullwing").at(b.d).courtyard))
    if s.body_x is not None and s.body_y is not None:
        b.set_body(s.body_x.max, s.body_y.max)
    b.marker = bool(s.polarized)
    # Pin 1 is the pad the spec names, else "1", else "A1"; the first pad
    # listed is only a last resort (a ball map listed from row 9 put the
    # marker at A9).
    names = [str(s.pin1)] if s.pin1 and s.pin1 != "default" else []
    p1 = next((p for n in names + ["1", "A1"] for p in b.pads if p.number == n),
              b.pads[0])
    b.pin1_name = p1.number
    b.pin1_candidates = b.cands_row_end_then_toe(p1, "left")


FAMILY_BUILDERS: dict[str, Callable[[Builder], None]] = {
    "chip": _chip,
    "melf": _melf,
    "molded": _molded,
    "sod": _sod,
    "sot23": _sot23,
    "sot89": _sot89,
    "sot223": _sot223,
    "dpak": _dpak,
    "soic": _soic,
    "son": _son,
    "qfn": _qfn,
    "qfp": _qfp,
    "plcc": _plcc,
    "bga": _bga,
    "lga": _lga,
    "crystal2": _crystal2,
    "crystal4": _crystal4,
    "custom": _custom,
}
