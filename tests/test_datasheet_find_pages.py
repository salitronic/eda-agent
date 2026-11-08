# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""datasheet_find_pages lists where a datasheet mentions its land pattern.

The agent read the ADXL372 pin table and outline (pp. 9, 59-61), said the
manufacturer gives no land pattern and built IPC lands; ADI's Figure 111 is
on p. 58 under "Layout and design recommendations".
"""
from __future__ import annotations

import pytest

from eda_agent.design.datasheet_search import find_pages

pytest.importorskip("pypdf")


def _pdf(pages: list[str]) -> bytes:
    """A minimal PDF, one line of Helvetica text per page."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode()}\nendstream")
        content = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {content} 0 R /Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(f"{len(objs)} 0 R")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_the_land_pattern_page_is_found_far_from_the_outline(tmp_path):
    pdf = tmp_path / "part.pdf"
    pdf.write_bytes(_pdf(["Pin configuration and function descriptions",
                          "Figure 111. Recommended Printed Wiring Board Land Pattern",
                          "Outline dimensions, 16-terminal land grid array"]))
    out = find_pages(str(pdf))
    assert out["ok"] and out["pages_searched"] == 3
    assert [p["page"] for p in out["pages"]] == [2]
    assert "Land Pattern" in out["pages"][0]["lines"][0]


def test_a_landing_pattern_is_a_land_pattern(tmp_path):
    """Bosch titles it "Landing pattern recommendation" (BME688 p. 54)."""
    pdf = tmp_path / "part.pdf"
    pdf.write_bytes(_pdf(["Pin-out", "7.4 Landing pattern recommendation"]))
    assert [p["page"] for p in find_pages(str(pdf))["pages"]] == [2]


def test_own_words_and_nothing_found(tmp_path):
    pdf = tmp_path / "part.pdf"
    pdf.write_bytes(_pdf(["Absolute maximum ratings", "Thermal resistance"]))
    out = find_pages(str(pdf), ["thermal"])
    assert [p["page"] for p in out["pages"]] == [2]
    none = find_pages(str(pdf))
    assert none["ok"] and none["pages"] == [] and "scanned" in none["note"]


def test_a_missing_file_or_no_words_is_refused(tmp_path):
    assert not find_pages(str(tmp_path / "absent.pdf"))["ok"]
    pdf = tmp_path / "part.pdf"
    pdf.write_bytes(_pdf(["x"]))
    assert not find_pages(str(pdf), ["  "])["ok"]
