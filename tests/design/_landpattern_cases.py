# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Package descriptions transcribed from public datasheets, one per case.

Every number below was read off the cited page of the cited revision; the
vendor's recommended land pattern rides along as ``vendor`` where the
datasheet gives one. Where a value is derived from two printed values the
comment says how. These feed the land pattern tests and the render check.
"""

from __future__ import annotations

from eda_agent.design.landpattern import (
    Citation,
    CustomPad,
    Dim,
    PackageSpec,
    VendorLandPattern,
)

# --- sources -----------------------------------------------------------------

KEMET_C1002 = Citation(
    document="KEMET/YAGEO C1002 X7R SMD MLCC datasheet", revision="2026-09-01",
    url="https://content.kemet.com/datasheets/KEM_C1002_X7R_SMD.pdf")
KEMET_T491 = Citation(
    document="KEMET/YAGEO T491 tantalum datasheet (T2005_T491)",
    revision="2026-07-08",
    url="https://content.kemet.com/datasheets/KEM_T2005_T491.pdf")
NX = "https://assets.nexperia.com/documents/package-information/"
TI = "https://www.ti.com/lit/ds/symlink/"
DIODES = "https://www.diodes.com/assets/Package-Files/"
WE = "https://www.we-online.com/components/products/datasheet/"


def _cite(src: Citation, page: str, ref: str) -> Citation:
    return Citation(document=src.document, revision=src.revision, url=src.url,
                    page=page, ref=ref)


def nx(pkg: str, date: str) -> Citation:
    return Citation(document=f"Nexperia {pkg} package information",
                    revision=date, url=f"{NX}{pkg}.pdf")


def ti(part: str, rev: str) -> Citation:
    return Citation(document=f"TI {part.upper()} datasheet", revision=rev,
                    url=f"{TI}{part}.pdf")


def diodes(pkg: str, rev: str, file: str = "") -> Citation:
    return Citation(document=f"Diodes Incorporated {pkg} package information",
                    revision=rev, url=f"{DIODES}{file or pkg}.pdf")


# --- chips (KEMET C1002, p. 3 dimensions, p. 12 Table 3) ------------------------

#: KEMET C1002 Table 3 (p. 12): (C, Y, X, V1, V2) per density level, where
#: C is the land centre from the origin, Y the land length, X its width,
#: V1 and V2 the courtyard. Density level C rows for 0603 and 0805 give C
#: = 0.60 and 0.75, which disagree with the same rows' Y and V1 (V1 = 2C +
#: Y + 2 x 0.1 rounded up fits C = 0.70 and 0.80); see the test notes.
KEMET_TABLE3 = {
    "0402": {"M": (0.50, 0.72, 0.72, 2.20, 1.20), "N": (0.45, 0.62, 0.62, 1.90, 1.00),
             "L": (0.40, 0.52, 0.52, 1.60, 0.80)},
    "0603": {"M": (0.90, 1.15, 1.10, 4.00, 2.10), "N": (0.80, 0.95, 1.00, 3.10, 1.50),
             "L": (0.60, 0.75, 0.90, 2.40, 1.20)},
    "0805": {"M": (1.00, 1.35, 1.55, 4.40, 2.60), "N": (0.90, 1.15, 1.45, 3.50, 2.00),
             "L": (0.75, 0.95, 1.35, 2.80, 1.70)},
    "1206": {"M": (1.60, 1.35, 1.90, 5.60, 2.90), "N": (1.50, 1.15, 1.80, 4.70, 2.30),
             "L": (1.40, 0.95, 1.70, 4.00, 2.00)},
}

#: KEMET C1002 p. 3: L, W, B (band) as nominal and +/- tolerance.
_KEMET_DIMS = {
    "0402": ((1.00, 0.05), (0.50, 0.05), (0.30, 0.10)),
    "0603": ((1.60, 0.15), (0.80, 0.15), (0.35, 0.15)),
    "0805": ((2.00, 0.20), (1.25, 0.20), (0.50, 0.25)),
    "1206": ((3.20, 0.20), (1.60, 0.20), (0.50, 0.25)),
}


def kemet_chip(size: str) -> PackageSpec:
    (l, lt), (w, wt), (b, bt) = _KEMET_DIMS[size]
    dims = _cite(KEMET_C1002, "3", "Dimensions table")
    c, y, x, v1, v2 = KEMET_TABLE3[size]["N"]
    return PackageSpec(
        family="chip", name=f"CAPC-{size}",
        lead_span_x=Dim.tol_(l, lt, dims), lead_width=Dim.tol_(w, wt, dims),
        lead_length=Dim.tol_(b, bt, dims), height=(0.0, 1.0),
        part_number=f"KEMET C{size}C (X7R)",
        source=_cite(KEMET_C1002, "3, 12", "Dimensions; Table 3"),
        vendor=VendorLandPattern(cite=_cite(KEMET_C1002, "12", "Table 3, level B"),
                                 pad_length=y, pad_width=x, span=2 * c))


# --- two-terminal parts ----------------------------------------------------------


def sod123() -> PackageSpec:
    """Nexperia SOD123, package information 27 May 2022: Fig. 1 p. 2 (body
    D 2.55-2.80, E 1.5-1.7, HE 3.45-3.75, Lp 0.15-0.45, bp 0.55-0.70, A
    1.0-1.3), Fig. 2 p. 3 reflow footprint (Cu 0.91 x 1.22, outer 4.18,
    gap 2.36, so centre to centre 4.18 - 0.91 = 3.27)."""
    src = nx("SOD123", "27 May 2022")
    return PackageSpec(
        family="sod", name="SOD123",
        lead_span_x=(3.45, 3.75), lead_length=(0.15, 0.45),
        lead_width=(0.55, 0.70), body_x=(2.55, 2.80), body_y=(1.5, 1.7),
        height=(1.0, 1.3), standoff=(0.0, 0.10),
        source=_cite(src, "2", "Fig. 1 package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "3", "Fig. 2 reflow footprint"),
                                 pad_length=0.91, pad_width=1.22, span=3.27))


def sod80c_melf() -> PackageSpec:
    """Nexperia SOD80C (MiniMELF), 27 May 2022, Fig. 1 p. 2: D 1.45-1.60,
    H 3.3-3.7, L 0.3 (end cap, printed without tolerance). Fig. 2: lands
    0.90 x 1.60, outer 4.30, inner 2.30, so 3.40 centre to centre."""
    src = nx("SOD80C", "27 May 2022")
    return PackageSpec(
        family="melf", name="SOD80C", polarized=True,
        lead_span_x=(3.3, 3.7), lead_length=0.3, lead_width=(1.45, 1.60),
        height=(1.45, 1.60), source=_cite(src, "2", "Fig. 1"),
        vendor=VendorLandPattern(cite=_cite(src, "2", "Fig. 2"),
                                 pad_length=0.90, pad_width=1.60, span=3.40))


def sma_molded() -> PackageSpec:
    """Diodes SMA package information Rev. 2017-03-14 p. 1: E 4.80-5.59
    overall, H 0.76-1.52 terminal, C 1.27-1.63 terminal width, B 4.00-4.60
    body, A 2.29-2.92 body width, J 1.96-2.40 height. Suggested pad layout:
    X 2.50, Y 1.70, C 4.00."""
    src = diodes("SMA", "Rev. 2017-03-14")
    return PackageSpec(
        family="molded", name="SMA (DO-214AC)",
        lead_span_x=(4.80, 5.59), lead_length=(0.76, 1.52),
        lead_width=(1.27, 1.63), body_x=(4.00, 4.60), body_y=(2.29, 2.92),
        height=(1.96, 2.40), source=_cite(src, "1", "Package outline dimensions"),
        vendor=VendorLandPattern(cite=_cite(src, "1", "Suggested pad layout"),
                                 pad_length=2.50, pad_width=1.70, span=4.00))


def t491_case_a() -> PackageSpec:
    """KEMET T491 case A (3216-18), p. 4: L 3.2 +/-0.2, W 1.6 +/-0.2, H 1.6
    +/-0.2, F 1.2 +/-0.1 (terminal width), S 0.80 +0.2/-0.3 (terminal
    length). Table 2 p. 15, level B: W 1.23, L 1.80, S 0.82 (gap)."""
    dims = _cite(KEMET_T491, "4", "Dimensions, case A")
    return PackageSpec(
        family="molded", name="CAPPM3216X160N", polarized=True,
        lead_span_x=Dim.tol_(3.2, 0.2, dims), lead_width=Dim.tol_(1.2, 0.1, dims),
        lead_length=Dim(0.5, 0.8, 1.0, dims), body_x=Dim.tol_(3.2, 0.2, dims),
        body_y=Dim.tol_(1.6, 0.2, dims), height=Dim.tol_(1.6, 0.2, dims),
        source=_cite(KEMET_T491, "4, 15", "Dimensions; Table 2"),
        vendor=VendorLandPattern(cite=_cite(KEMET_T491, "15", "Table 2, level B"),
                                 pad_length=1.80, pad_width=1.23, span=1.80 + 0.82))


def abs07_crystal() -> PackageSpec:
    """Abracon ABS07, revised 08-10-22, p. 4: body 3.2 +/-0.1 x 1.50 +/-0.1,
    H 0.9 max; terminals 0.6 long (2x), 1.3 +/-0.1 wide, 1.8 apart, so they
    span 3.0 (0.6 + 1.8 + 0.6). Recommended land: 1.1 x 1.9, gap 1.4."""
    src = Citation(document="Abracon ABS07 32.768 kHz SMD crystal datasheet",
                   revision="revised 08-10-22", page="4",
                   url="https://abracon.com/Resonators/ABS07.pdf")
    return PackageSpec(
        family="crystal2", name="ABS07",
        body_x=(3.1, 3.3), body_y=(1.4, 1.6), lead_span_x=3.0,
        lead_length=0.6, lead_width=(1.2, 1.4), height=(0.0, 0.9),
        source=src,
        vendor=VendorLandPattern(cite=src, pad_length=1.1, pad_width=1.9,
                                 span=1.4 + 1.1))


# --- SOT ---------------------------------------------------------------------------


def sot23_3() -> PackageSpec:
    """Nexperia SOT23 (TO-236AB), 12 October 2022, Fig. 1 p. 2: A 0.9-1.1,
    bp 0.38-0.48, D 2.8-3.0, E 1.2-1.4, e1 0.95, HE 2.1-2.5, Lp
    0.15-0.45. Fig. 2 p. 3 reflow footprint: lands 0.6 (along the pitch)
    x 0.7, rows 2.0 apart, pitch 1.9 between the two lands of a row."""
    src = nx("SOT23", "12 October 2022")
    return PackageSpec(
        family="sot23", name="SOT23", pins=3, pitch=0.95,
        lead_span_x=(2.1, 2.5), lead_length=(0.15, 0.45),
        lead_width=(0.38, 0.48), body_x=(1.2, 1.4), body_y=(2.8, 3.0),
        height=(0.9, 1.1), source=_cite(src, "2", "Fig. 1"),
        vendor=VendorLandPattern(cite=_cite(src, "3", "Fig. 2"),
                                 pad_length=0.7, pad_width=0.6, span=2.0))


def sot23_5() -> PackageSpec:
    """TI DBV0005A (4214839/K 08/2024) in the LP2985 datasheet Rev. S,
    pp. 40-41: span 2.6-3.0, body 2.75-3.05 x 1.45-1.75, pitch 0.95, b
    0.3-0.5, L 0.3-0.6, height 0.90-1.45. Example land: 1.1 x 0.6, 2.6
    centre to centre."""
    src = ti("lp2985", "Rev. S")
    return PackageSpec(
        family="sot23", name="SOT-23-5 (DBV)", pins=5, pitch=0.95,
        lead_span_x=(2.6, 3.0), lead_length=(0.3, 0.6),
        lead_width=(0.3, 0.5), body_x=(1.45, 1.75), body_y=(2.75, 3.05),
        height=(0.90, 1.45), standoff=(0.0, 0.15),
        source=_cite(src, "40", "DBV0005A package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "41", "DBV0005A example board layout"),
                                 pad_length=1.1, pad_width=0.6, span=2.6,
                                 pitch=0.95))


def sot23_6() -> PackageSpec:
    """Nexperia SOT457 (SC-74), 3 March 2023, Fig. 1 p. 2: A 0.9-1.1, bp
    0.25-0.40, D 2.7-3.1, E 1.3-1.7, e 0.95, HE 2.5-3.0, Lp 0.2-0.6."""
    src = nx("SOT457", "3 March 2023")
    return PackageSpec(
        family="sot23", name="SOT457", pins=6, pitch=0.95,
        lead_span_x=(2.5, 3.0), lead_length=(0.2, 0.6),
        lead_width=(0.25, 0.40), body_x=(1.3, 1.7), body_y=(2.7, 3.1),
        height=(0.9, 1.1), standoff=(0.013, 0.1),
        source=_cite(src, "2", "Fig. 1"))


def sot89() -> PackageSpec:
    """Diodes SOT89 package information Rev. 2026-01-16 p. 1: H 3.95-4.25
    overall, L 0.90-1.20 lead, B1 0.42-0.54 (pins 1, 3), B 0.50-0.62 (pin
    2), D2 1.61-1.81 exposed tab width, H1 2.63-2.93 exposed tab length, D
    4.40-4.60, E 2.40-2.60, A 1.40-1.60, e 1.50. Suggested layout: X 0.58,
    Y 1.63, X2 1.933, Y4 4.53."""
    src = diodes("SOT89", "Rev. 2026-01-16")
    return PackageSpec(
        family="sot89", name="SOT89", pitch=1.50,
        lead_span_x=(3.95, 4.25), lead_length=(0.90, 1.20),
        lead_width=(0.42, 0.54), tab_width=(1.61, 1.81),
        tab_length=(2.63, 2.93), body_x=(2.40, 2.60), body_y=(4.40, 4.60),
        height=(1.40, 1.60), options={"center_lead_width": (0.50, 0.62)},
        source=_cite(src, "1", "Package outline dimensions"),
        vendor=VendorLandPattern(cite=_cite(src, "1", "Suggested pad layout"),
                                 pad_length=1.63, pad_width=0.58, pitch=1.50,
                                 tab_width=1.933))


def sot223() -> PackageSpec:
    """Diodes SOT223 package information Rev. 2017-03-06 p. 1: E1
    6.90-7.10 span, L 0.85-1.05, b 0.60-0.80, b1 2.90-3.10, E 3.45-3.55,
    D 6.45-6.55, A 1.55-1.65, A1 0.010-0.15, e1 2.30. Suggested layout:
    C 2.30, C1 6.40, X 1.20, X1 3.30, Y 1.60, Y1 1.60, Y2 8.00, with the
    note that Y2 "is calculated by adding 0.2 mm to the 'Z' dimension"
    (IPC-7351A)."""
    src = diodes("SOT223", "Rev. 2017-03-06")
    return PackageSpec(
        family="sot223", name="SOT223", pins=3, pitch=2.30,
        lead_span_x=(6.90, 7.10), lead_length=(0.85, 1.05),
        lead_width=(0.60, 0.80), tab_width=(2.90, 3.10),
        body_x=(3.45, 3.55), body_y=(6.45, 6.55), height=(1.55, 1.65),
        standoff=(0.010, 0.15),
        source=_cite(src, "1", "Package outline dimensions"),
        vendor=VendorLandPattern(cite=_cite(src, "1", "Suggested pad layout"),
                                 pad_length=1.60, pad_width=1.20, span=6.40,
                                 pitch=2.30, tab_length=1.60, tab_width=3.30))


def dpak() -> PackageSpec:
    """Diodes TO252 (DPAK) Type TH, 2020-02-05 p. 1: H 9.90-10.30, L
    1.40-1.70 foot, b 0.72-0.85, E1 4.70-4.92 exposed tab width, D1 5.30
    REF exposed tab length, D 6.00-6.20 body, L3 0.90-1.25 tab beyond the
    body (so the body from the tab edge is D + L3 = 6.90-7.45), E
    6.50-6.70, A 2.20-2.38, e 2.286 BSC, lead 2 cropped. Suggested layout:
    C 4.572, X 1.060, Y 2.600, X1 5.632, Y1 5.700, Y2 10.700."""
    src = diodes("TO252 (DPAK) Type TH", "2020-02-05", "TO252-DPAK-Type-TH")
    return PackageSpec(
        family="dpak", name="TO252 (DPAK)", pins=3, pitch=2.286,
        lead_numbers=("1", "", "3"), tab_number="2",
        lead_span_x=(9.90, 10.30), lead_length=(1.40, 1.70),
        lead_width=(0.72, 0.85), tab_width=(4.70, 4.92), tab_length=5.30,
        body_x=(6.90, 7.45), body_y=(6.50, 6.70), height=(2.20, 2.38),
        standoff=(0.0, 0.10),
        source=_cite(src, "1", "Package outline dimensions"),
        vendor=VendorLandPattern(cite=_cite(src, "1", "Suggested pad layout"),
                                 pad_length=2.60, pad_width=1.060,
                                 tab_length=5.70, tab_width=5.632))


# --- gull-wing ICs -----------------------------------------------------------------


def soic8() -> PackageSpec:
    """TI D0008A (4214825/C 02/2019) in the LM358B datasheet Rev. AB,
    pp. 54-55: span 5.80-6.19, body 4.81-5.00 x 3.81-3.98, pitch 1.27, b
    0.31-0.51, L 0.41-1.27, height 1.75 max. Example land: 1.55 x 0.6, 5.4
    centre to centre."""
    src = ti("lm358b", "Rev. AB")
    out = _cite(src, "54", "D0008A package outline")
    return PackageSpec(
        family="soic", name="SOIC-8 (D)", pins=8, pitch=Dim.basic(1.27, out),
        lead_span_x=Dim.range(5.80, 6.19, cite=out),
        lead_length=Dim.range(0.41, 1.27, cite=_cite(src, "54", "D0008A detail A")),
        lead_width=Dim.range(0.31, 0.51, cite=out),
        body_x=Dim.range(3.81, 3.98, cite=out), body_y=Dim.range(4.81, 5.00, cite=out),
        height=(0.0, 1.75), source=out,
        vendor=VendorLandPattern(cite=_cite(src, "55", "D0008A example board layout"),
                                 pad_length=1.55, pad_width=0.6, span=5.4,
                                 pitch=1.27))


def tssop8() -> PackageSpec:
    """TI PW0008A (4221848/A 02/2015) in the LM358B datasheet Rev. AB,
    pp. 60-61: span 6.2-6.6, body 2.9-3.1 x 4.3-4.5, pitch 0.65, b
    0.19-0.30, L 0.50-0.75, height 1.2 max, standoff 0.05-0.15. Example
    land: 1.5 x 0.45, 5.8 centre to centre."""
    src = ti("lm358b", "Rev. AB")
    return PackageSpec(
        family="soic", name="TSSOP-8 (PW)", pins=8, pitch=0.65,
        lead_span_x=(6.2, 6.6), lead_length=(0.50, 0.75),
        lead_width=(0.19, 0.30), body_x=(4.3, 4.5), body_y=(2.9, 3.1),
        height=(0.0, 1.2), standoff=(0.05, 0.15),
        source=_cite(src, "60", "PW0008A package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "61", "PW0008A example board layout"),
                                 pad_length=1.5, pad_width=0.45, span=5.8,
                                 pitch=0.65))


def msop8() -> PackageSpec:
    """TI DGK0008A (4214862/A 04/2023) in the LM358B datasheet Rev. AB,
    p. 65: span 4.75-5.05, body 2.9-3.1 square, pitch 0.65, b 0.25-0.38,
    L 0.4-0.7, height 1.1 max, standoff 0.05-0.15."""
    src = ti("lm358b", "Rev. AB")
    return PackageSpec(
        family="soic", name="VSSOP-8 (DGK)", pins=8, pitch=0.65,
        lead_span_x=(4.75, 5.05), lead_length=(0.4, 0.7),
        lead_width=(0.25, 0.38), body_x=(2.9, 3.1), body_y=(2.9, 3.1),
        height=(0.0, 1.1), standoff=(0.05, 0.15),
        source=_cite(src, "65", "DGK0008A package outline"))


def msop8_ep() -> PackageSpec:
    """TI DGN0008D (4225481/A 11/2019) in the TPA6211A1 datasheet Rev. E,
    p. 37: as DGK, plus an exposed pad 1.28-1.57 x 1.63-1.89."""
    src = ti("tpa6211a1", "Rev. E")
    return PackageSpec(
        family="soic", name="VSSOP-8 PowerPAD (DGN)", pins=8, pitch=0.65,
        lead_span_x=(4.75, 5.05), lead_length=(0.4, 0.7),
        lead_width=(0.25, 0.38), body_x=(2.9, 3.1), body_y=(2.9, 3.1),
        ep_x=(1.28, 1.57), ep_y=(1.63, 1.89),
        height=(0.0, 1.1), standoff=(0.05, 0.15),
        source=_cite(src, "37", "DGN0008D package outline"))


def soic8_ep() -> PackageSpec:
    """TI DDA0008B (4214849/B 09/2025) in the TPS5430 datasheet Rev. L,
    pp. 38-39: span 5.8-6.2, body 4.8-5.0 x 3.8-4.0, pitch 1.27, b
    0.31-0.51, L 0.40-1.27, height 1.7 max, exposed pad 2.11-2.71 x
    2.8-3.4. Example land: 1.55 x 0.6, 5.4 centre to centre."""
    src = ti("tps5430", "Rev. L")
    return PackageSpec(
        family="soic", name="SOIC-8 PowerPAD (DDA)", pins=8, pitch=1.27,
        lead_span_x=(5.8, 6.2), lead_length=(0.40, 1.27),
        lead_width=(0.31, 0.51), body_x=(3.8, 4.0), body_y=(4.8, 5.0),
        ep_x=(2.11, 2.71), ep_y=(2.8, 3.4), height=(0.0, 1.7),
        standoff=(0.0, 0.15), source=_cite(src, "38", "DDA0008B package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "39", "DDA0008B example board layout"),
                                 pad_length=1.55, pad_width=0.6, span=5.4,
                                 pitch=1.27))


def lqfp80() -> PackageSpec:
    """TI PN0080A (4215166/A 08/2022) in the MSP430F5529 datasheet Rev. P,
    pp. 137-138: body 11.8-12.2 square, span 13.8-14.2, pitch 0.5, b
    0.17-0.27, L 0.45-0.75, height 1.6 max, standoff 0.05 min. Example
    land: 1.5 x 0.3, 13.4 centre to centre both ways."""
    src = ti("msp430f5529", "Rev. P")
    return PackageSpec(
        family="qfp", name="LQFP-80 (PN)", pins=80, pitch=0.5,
        lead_span_x=(13.8, 14.2), lead_span_y=(13.8, 14.2),
        lead_length=(0.45, 0.75), lead_width=(0.17, 0.27),
        body_x=(11.8, 12.2), body_y=(11.8, 12.2), height=(0.0, 1.6),
        standoff=(0.05, 0.05),
        source=_cite(src, "137", "PN0080A package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "138", "PN0080A example board layout"),
                                 pad_length=1.5, pad_width=0.3, span=13.4,
                                 span_y=13.4, pitch=0.5))


def plcc44() -> PackageSpec:
    """TI FN0044A (4215154/A 04/2017) in the TL16C550C datasheet Rev. I,
    pp. 48-49: body 16.51-16.66 square, lead span 17.40-17.65, contact
    centreline span 14.79-16.20, lead width 0.33-0.53, pitch 1.27, height
    4.57 max, 11 leads per side, pin 1 top centre. The drawing gives no
    J-lead contact length, so T is derived as span minus contact span:
    17.40 - 16.20 = 1.20 to 17.65 - 14.79 = 2.86. Example land: 2.35 x
    0.75, 16.2 centre to centre."""
    src = ti("tl16c550c", "Rev. I")
    return PackageSpec(
        family="plcc", name="PLCC-44 (FN)", pins=44, pitch=1.27,
        lead_span_x=(17.40, 17.65), lead_length=(1.20, 2.86),
        lead_width=(0.33, 0.53), body_x=(16.51, 16.66),
        body_y=(16.51, 16.66), height=(0.0, 4.57), standoff=(0.51, 0.51),
        source=_cite(src, "48", "FN0044A package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "49", "FN0044A example board layout"),
                                 pad_length=2.35, pad_width=0.75, span=16.2,
                                 pitch=1.27))


# --- no-lead ICs ------------------------------------------------------------------


def qfn16() -> PackageSpec:
    """TI RGT0016C (4222419/E 07/2025) in the TPS62130 datasheet Rev. F,
    pp. 40-42: body 2.9-3.1 square, height 0.8-1.0, pitch 0.5, b
    0.18-0.30, L 0.3-0.5, exposed pad 1.68 +/-0.07. Example land: 0.6 x
    0.24, 2.8 centre to centre, thermal land 1.68; stencil: 1.55 square,
    "85% printed solder coverage by area"."""
    src = ti("tps62130", "Rev. F")
    return PackageSpec(
        family="qfn", name="VQFN-16 (RGT)", pins=16, pitch=0.5,
        body_x=(2.9, 3.1), body_y=(2.9, 3.1), lead_length=(0.3, 0.5),
        lead_width=(0.18, 0.30), ep_x=Dim.tol_(1.68, 0.07),
        ep_y=Dim.tol_(1.68, 0.07), height=(0.8, 1.0), standoff=(0.0, 0.05),
        source=_cite(src, "40", "RGT0016C package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "41", "RGT0016C example board layout"),
                                 pad_length=0.6, pad_width=0.24, span=2.8,
                                 span_y=2.8, pitch=0.5, ep_x=1.68, ep_y=1.68,
                                 paste_coverage=0.85))


def qfn14_rect() -> PackageSpec:
    """TI BQA0014A (4224636/A 11/2018) in the TXB0104 datasheet Rev. K,
    pp. 43-44: body 2.4-2.6 x 2.9-3.1, height 0.7-0.8, pitch 0.5, b
    0.2-0.3, L 0.3-0.5, exposed pad 0.9-1.1 x 1.4-1.6; 5 pins on each long
    side, 2 on each short side, pin 1 the left pin of the top side.
    Example land: 0.6 x 0.25, 2.3 and 2.8 centre to centre, thermal land
    1.0 x 1.5."""
    src = ti("txb0104", "Rev. K")
    return PackageSpec(
        family="qfn", name="WQFN-14 (BQA)", pins=14, pitch=0.5,
        pins_per_side=(5, 2, 5, 2), pin1="top_left",
        body_x=(2.4, 2.6), body_y=(2.9, 3.1), lead_length=(0.3, 0.5),
        lead_width=(0.2, 0.3), ep_x=(0.9, 1.1), ep_y=(1.4, 1.6),
        height=(0.7, 0.8), standoff=(0.0, 0.05),
        source=_cite(src, "43", "BQA0014A package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "44", "BQA0014A example board layout"),
                                 pad_length=0.6, pad_width=0.25, span=2.3,
                                 span_y=2.8, pitch=0.5, ep_x=1.0, ep_y=1.5))


def son8() -> PackageSpec:
    """TI DRB0008A (4218875/A 01/2018) in the TPA6211A1 datasheet Rev. E,
    pp. 33-34: body 2.9-3.1 square, height 1.0 max, pitch 0.65, b
    0.25-0.37, L 0.3-0.5, exposed pad 1.5 +/-0.1 x 1.75 +/-0.1. Example
    land: 0.6 x 0.31, 2.8 centre to centre, thermal land 1.5 x 1.75."""
    src = ti("tpa6211a1", "Rev. E")
    return PackageSpec(
        family="son", name="VSON-8 (DRB)", pins=8, pitch=0.65,
        body_x=(2.9, 3.1), body_y=(2.9, 3.1), lead_length=(0.3, 0.5),
        lead_width=(0.25, 0.37), ep_x=Dim.tol_(1.5, 0.1),
        ep_y=Dim.tol_(1.75, 0.1), height=(0.0, 1.0), standoff=(0.0, 0.05),
        source=_cite(src, "33", "DRB0008A package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "34", "DRB0008A example board layout"),
                                 pad_length=0.6, pad_width=0.31, span=2.8,
                                 pitch=0.65, ep_x=1.5, ep_y=1.75))


def abm8g_crystal() -> PackageSpec:
    """Abracon ABM8G, revised 08.13.15, p. 2: body 3.2 +/-0.1 x 2.5 +/-0.1,
    1.0 max high; terminals 1.2 ref x 0.9 ref, 0.1 ref from the edges; pin
    1 bottom left (top view), counter-clockwise. Turned 90 degrees
    clockwise into zero orientation (pin 1 upper left): body 2.5 x 3.2,
    terminals 0.9 along X and 1.2 along Y, terminal outer edges 2.5 - 2 x
    0.1 = 2.3 apart, terminal centres 3.2 - 2 x 0.1 - 1.2 = 1.8 apart in
    Y. Recommended land (same turn): 1.2 x 1.4, 2.2 and 1.7 centre to
    centre."""
    src = Citation(document="Abracon ABM8G crystal datasheet",
                   revision="revised 08.13.15", page="2",
                   url="https://abracon.com/Resonators/ABM8G.pdf")
    return PackageSpec(
        family="crystal4", name="ABM8G", pitch=1.8,
        body_x=(2.4, 2.6), body_y=(3.1, 3.3), lead_span_x=2.3,
        lead_length=0.9, lead_width=1.2, height=(0.0, 1.0), source=src,
        vendor=VendorLandPattern(cite=src, pad_length=1.2, pad_width=1.4,
                                 span=1.7, pitch=2.2))


# --- grids ------------------------------------------------------------------------


def bga80() -> PackageSpec:
    """TI ZXH0080A (4221325/A 01/2014) in the MSP430F5529 datasheet Rev. P,
    pp. 134-135: body 4.9-5.1 square, 1.0 max high, standoff 0.15-0.25,
    ball 0.25-0.35, pitch 0.5, 9 x 9 grid (rows A-J without I), C3 not
    populated (80 balls). Example land: 0.235-0.265 (0.25) NSMD."""
    src = ti("msp430f5529", "Rev. P")
    return PackageSpec(
        family="bga", name="NFBGA-80 (ZXH)", pins=80, rows=9, cols=9, pitch=0.5,
        ball_diameter=(0.25, 0.35), depopulated=("C3",),
        body_x=(4.9, 5.1), body_y=(4.9, 5.1), height=(0.0, 1.0),
        standoff=(0.15, 0.25),
        source=_cite(src, "134", "ZXH0080A package outline"),
        vendor=VendorLandPattern(cite=_cite(src, "135", "ZXH0080A example board layout"),
                                 land_diameter=0.25, pitch=0.5))


def bme280_lga() -> PackageSpec:
    """Bosch BME280 datasheet BST-BME280-DS001 (front page: revision 1.24,
    February 2024), section 7.5 Figure 20 p. 42: body 2.50 x 2.50
    (general tolerance X.XX +/-0.05), height 0.93 +/-0.07, LGA pitch 0.65,
    land size 0.350 x 0.350 (X.XXX +/-0.030), lands in two columns 1.95
    apart. Figure 21 p. 43 (top view): pins 1-4 down the right column,
    5-8 up the left; recommended lands 0.5 x 0.35."""
    src = Citation(document="Bosch Sensortec BME280 datasheet BST-BME280-DS001",
                   revision="1.24", page="42-43", ref="Figures 20 and 21",
                   url=("https://www.bosch-sensortec.com/media/boschsensortec/"
                        "downloads/datasheets/bst-bme280-ds002.pdf"))
    return PackageSpec(
        family="lga", name="BME280 LGA-8", pins=8, rows=4, cols=2,
        pitch=1.95, pitch_y=0.65, lead_width=(0.32, 0.38),
        lead_length=(0.32, 0.38), body_x=(2.45, 2.55), body_y=(2.45, 2.55),
        height=(0.86, 1.00),
        options={"names": ["8", "1", "7", "2", "6", "3", "5", "4"]},
        source=src)


# --- through-hole -------------------------------------------------------------------


def dip8() -> PackageSpec:
    """Nexperia SOT97-1 (DIP8), 30 January 2017, Fig. 1 p. 2: b1 0.38-0.53
    lead width, c 0.23-0.36 thickness, D 9.2-9.8, E 6.20-6.48, e 2.54, e1
    7.62, A 4.2 max, A1 0.51 min."""
    src = nx("SOT97-1", "30 January 2017")
    return PackageSpec(
        family="dip", name="DIP8", pins=8, pitch=2.54, row_pitch=7.62,
        lead_width=(0.38, 0.53), lead_thickness=(0.23, 0.36),
        body_x=(6.20, 6.48), body_y=(9.2, 9.8), height=(0.0, 4.2),
        source=_cite(src, "2", "Fig. 1"))


def header_1x4() -> PackageSpec:
    """Wurth Elektronik 61300411121 WR-PHD, rev. 003.000 (2023-01-08)
    p. 1: 4 pins, pitch 2.54 +/-0.05, square post 0.64 +/-0.15, length
    10.16 +/-0.35, body 2.54 +/-0.15 wide, 2.54 high; recommended hole
    1.10 +/-0.15."""
    src = Citation(document="Wurth Elektronik 61300411121 WR-PHD datasheet",
                   revision="003.000 (2023-01-08)", page="1",
                   url=f"{WE}61300411121.pdf")
    return PackageSpec(
        family="header", name="PinHeader_1x04_P2.54", pins=4, pitch=2.54,
        rows=1, lead_width=(0.49, 0.79), body_x=(2.39, 2.69),
        body_y=(9.81, 10.51), height=(2.39, 2.69), source=src)


def radial_5x11() -> PackageSpec:
    """Wurth Elektronik WCAP-ATG8 860010672009, rev. 1.0 (2014-11-11) p. 1:
    D 5.0 +/-0.5, P 2.0 +/-0.5 lead spacing, L 11.0 +/-1.5, pin 0.5
    +/-0.02; recommended hole pattern a 2.0, b 0.8."""
    src = Citation(document="Wurth Elektronik WCAP-ATG8 860010672009 datasheet",
                   revision="1.0 (2014-11-11)", page="1",
                   url=f"{WE}860010672009.pdf")
    return PackageSpec(
        family="radial", name="CP_Radial_D5.0mm_P2.00mm", pitch=2.0,
        lead_diameter=(0.48, 0.52), body_x=(4.5, 5.5), height=(9.5, 12.5),
        polarized=True, source=src)


def axial_do34() -> PackageSpec:
    """Nexperia SOD27 (DO-34), 30 January 2017, Fig. 1 p. 2: b 0.55 max
    lead, D 1.85 max body diameter, G1 4.25 max body length. The 7.62 mm
    bend pitch is a board choice, not a package dimension."""
    src = nx("SOD27", "30 January 2017")
    return PackageSpec(
        family="axial", name="D_DO-34_P7.62mm", pitch=7.62,
        lead_diameter=(0.45, 0.55), body_x=(3.85, 4.25), body_y=(1.6, 1.85),
        height=(1.6, 1.85), source=_cite(src, "2", "Fig. 1"))


def to220() -> PackageSpec:
    """Nexperia SOT78 (TO-220AB), 30 January 2017, Fig. 1 p. 2: b 0.6-0.9,
    c 0.4-0.7, E 9.7-10.3, A 4.1-4.7 (tab included), A1 1.25-1.40 tab,
    e 2.54, Q 2.2-2.6 from the lead centreline to the tab's back face,
    D 15.2-16.0 height."""
    src = nx("SOT78", "30 January 2017")
    return PackageSpec(
        family="to220", name="TO-220-3_Vertical", pins=3, pitch=2.54,
        lead_width=(0.6, 0.9), lead_thickness=(0.4, 0.7),
        body_x=(9.7, 10.3), body_y=(4.1, 4.7), height=(15.2, 16.0),
        options={"row_offset": (2.2, 2.6), "tab_thickness": (1.25, 1.40)},
        source=_cite(src, "2", "Fig. 1"))


def to92() -> PackageSpec:
    """Nexperia SOT54 (TO-92), 30 January 2017, Fig. 1 p. 2: b 0.40-0.48,
    c 0.38-0.45, D 4.4-4.8, E 3.6-4.2, d 1.4-1.7 from the flat face to the
    lead row, e1 1.27, A 5.0-5.2."""
    src = nx("SOT54", "30 January 2017")
    return PackageSpec(
        family="to92", name="TO-92_Inline", pins=3, pitch=1.27,
        lead_width=(0.40, 0.48), lead_thickness=(0.38, 0.45),
        body_x=(4.4, 4.8), body_y=(3.6, 4.2), height=(5.0, 5.2),
        options={"row_offset": (1.4, 1.7)}, source=_cite(src, "2", "Fig. 1"))


def usb_b() -> PackageSpec:
    """Wurth Elektronik 61400416121 WR-COM USB 2.0 type B horizontal THT,
    rev. 001.003 (2024-01-30) p. 1: contacts 0.6 +/-0.10, rows 2.0 apart,
    2.5 apart in a row (pin 1 upper right, 2 upper left, 3 lower left, 4
    lower right, as the hole pattern draws them); shield legs in 2.3 holes
    12.04 apart, 2.71 below the lower row; body 12.04 +/-0.20 wide, 16.5
    +/-0.25 deep, 11.05 high, its back face 1.5 behind the upper row (read
    from the hole-pattern outline). Leg pads: the 2.3 hole plus the
    IPC-2221 ring and level B allowance, 2.3 + 0.1 + 0.5 = 2.9."""
    src = Citation(document="Wurth Elektronik 61400416121 WR-COM datasheet",
                   revision="001.003 (2024-01-30)", page="1",
                   url=f"{WE}61400416121.pdf")
    contacts = [CustomPad("1", 1.25, 1.0, 0, 0), CustomPad("2", -1.25, 1.0, 0, 0),
                CustomPad("3", -1.25, -1.0, 0, 0), CustomPad("4", 1.25, -1.0, 0, 0)]
    legs = [CustomPad("MH", -6.02, -3.71, 2.9, 2.9, shape="circle", hole=2.3),
            CustomPad("MH", 6.02, -3.71, 2.9, 2.9, shape="circle", hole=2.3)]
    return PackageSpec(
        family="connector", name="USB_B_61400416121", pins=4, pitch=2.5,
        lead_diameter=(0.5, 0.7), custom_pads=tuple(contacts),
        mounting_pads=tuple(legs), body_x=(11.84, 12.24),
        body_y=(16.25, 16.75), height=(10.85, 11.25),
        options={"body_offset": (0.0, 2.5 - 16.5 / 2.0)}, source=src)


def custom_sot23_vendor() -> PackageSpec:
    """The Nexperia SOT23 reflow footprint itself (Fig. 2 p. 3, 12 October
    2022) as custom lands: 0.6 x 0.7 lands, 1.9 apart in a row, rows 2.0
    apart; body 1.2-1.4 x 2.8-3.0 (Fig. 1 p. 2)."""
    src = nx("SOT23", "12 October 2022")
    pads = (CustomPad("1", -1.0, 0.95, 0.7, 0.6, shape="rect"),
            CustomPad("2", -1.0, -0.95, 0.7, 0.6, shape="rect"),
            CustomPad("3", 1.0, 0.0, 0.7, 0.6, shape="rect"))
    return PackageSpec(family="custom", name="SOT23_vendor", custom_pads=pads,
                       body_x=(1.2, 1.4), body_y=(2.8, 3.0), height=(0.9, 1.1),
                       polarized=True, source=_cite(src, "2-3", "Fig. 1, Fig. 2"))


#: Every family with its datasheet case, for the build and render checks.
ALL_CASES = {
    "chip_0402": lambda: kemet_chip("0402"),
    "chip_0603": lambda: kemet_chip("0603"),
    "melf": sod80c_melf,
    "molded_sma": sma_molded,
    "molded_tantalum": t491_case_a,
    "sod123": sod123,
    "sot23_3": sot23_3,
    "sot23_5": sot23_5,
    "sot23_6": sot23_6,
    "sot89": sot89,
    "sot223": sot223,
    "dpak": dpak,
    "soic8": soic8,
    "soic8_ep": soic8_ep,
    "tssop8": tssop8,
    "msop8": msop8,
    "msop8_ep": msop8_ep,
    "son8": son8,
    "qfn16": qfn16,
    "qfn14_rect": qfn14_rect,
    "lqfp80": lqfp80,
    "plcc44": plcc44,
    "lga8": bme280_lga,
    "bga80": bga80,
    "crystal2": abs07_crystal,
    "crystal4": abm8g_crystal,
    "dip8": dip8,
    "header_1x4": header_1x4,
    "radial": radial_5x11,
    "axial": axial_do34,
    "to220": to220,
    "to92": to92,
    "connector_usb_b": usb_b,
    "custom": custom_sot23_vendor,
}
