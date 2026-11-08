# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Ball names are checked against the ball map, not only the land figure.

The BGT60UTR11AIP land figure prints no ball names; its lands were
compared and passed with every name transposed (A2 where B1 belongs).
The ball map prints the names, and a footprint may be the map turned a
quarter turn but never mirrored.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("PIL")

from eda_agent.design.landpattern import compare_figure
from eda_agent.design.landpattern.compare_figure import axis_names, check_ball_names
from tests.test_compare_with_drawing import _tools


class _Land:
    def __init__(self, number, x, y):
        self.number, self.x, self.y = number, x, y


#: A 3 x 3 grid seen from above: row A at the top, columns 1 to 3 left to right.
_GRID = [(f"{r}{c}", (c - 2) * 1.0, (1 - i) * 1.0)
         for i, r in enumerate("ABC") for c in (1, 2, 3)]


def _transposed(name):
    return "ABC"[int(name[1]) - 1] + str("ABC".index(name[0]) + 1)


def _map(names):
    """Names printed where the balls are, as fractions of the image."""
    return [{"value": n, "at": [0.2 + 0.3 * (x + 1), 0.2 + 0.3 * (1 - y)]}
            for n, x, y in names]


def test_the_right_names_pass_and_transposed_names_fail():
    lands = [_Land(*g) for g in _GRID]
    good = check_ball_names(lands, _map(_GRID), "top")
    assert good["checked"] == 9 and good["mismatched"] == []
    wrong = [_Land(_transposed(n), x, y) for n, x, y in _GRID]
    bad = check_ball_names(wrong, _map(_GRID), "top")
    assert len(bad["mismatched"]) == 6  # all but the diagonal


def test_a_footprint_turned_against_the_map_passes_but_a_mirror_fails():
    turned = [_Land(n, -y, x) for n, x, y in _GRID]  # a quarter turn
    out = check_ball_names(turned, _map(_GRID), "top")
    assert out["mismatched"] == [] and out["turned"] in (90, 270)
    mirrored = [_Land(n, -x, y) for n, x, y in _GRID]
    assert check_ball_names(mirrored, _map(_GRID), "top")["mismatched"]
    # ... unless the map is drawn from below, which mirrors it back
    assert check_ball_names(mirrored, _map(_GRID), "bottom")["mismatched"] == []


def test_a_map_with_only_row_letters_and_column_numbers_is_placed():
    labels = ([{"value": r, "at": [0.05, 0.2 + 0.3 * i]} for i, r in enumerate("ABC")]
              + [{"value": str(c), "at": [0.2 + 0.3 * (c - 1), 0.95]} for c in (1, 2, 3)]
              + [{"value": "VDD", "at": [0.5, 0.5]}])
    lands = [_Land(*g) for g in _GRID]
    marks = axis_names(labels, lands)
    assert len(marks) == 9
    assert check_ball_names(lands, marks, "top")["mismatched"] == []
    wrong = [_Land(_transposed(n), x, y) for n, x, y in _GRID]
    assert check_ball_names(wrong, axis_names(labels, wrong), "top")["mismatched"]


def test_too_few_printed_names_check_nothing():
    lands = [_Land(*g) for g in _GRID]
    assert check_ball_names(lands, _map(_GRID[:2]), "top") == {"checked": 0}


def _pdf(pages):
    """A PDF whose pages hold text at given points: [[(text, x, y), ...], ...]."""
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for items in pages:
        stream = " ".join(f"BT /F1 10 Tf {x} {y} Td ({t}) Tj ET" for t, x, y in items).encode()
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


def _package(pdf, names):
    return {"family": "custom", "name": "GRID", "body_x": 3.5, "body_y": 3.5,
            "source": {"document": "ds", "page": "1", "path": str(pdf)},
            "custom_pads": [{"number": n, "x": x, "y": y, "width": 0.3, "height": 0.3,
                             "shape": "circle"} for n, x, y in names]}


@pytest.fixture
def pdf(tmp_path):
    # page 1: the land figure, numbers only; page 2: the ball map, names
    # where the balls are (row A at the top, column 1 on the left)
    ball_map = [(n, 250 + 40 * x, 500 + 40 * y) for n, x, y in _GRID]
    path = tmp_path / "ds.pdf"
    path.write_bytes(_pdf([[("pitch 1.00", 100, 700)], ball_map]))
    return path


def test_the_tool_withholds_the_id_until_the_names_are_checked(monkeypatch, tmp_path, pdf):
    monkeypatch.setattr(compare_figure, "ISSUED", {})
    tools = _tools(monkeypatch, tmp_path)
    run = tools["footprint_compare_drawing"]
    region, names_region = [0, 0, 1, 0.2], [0.2, 0.25, 0.7, 0.5]

    unchecked = asyncio.run(run(_package(pdf, _GRID), region, "top", 100))
    assert not unchecked["ok"] and "names_page" in unchecked["next"]
    assert "compared" not in unchecked and compare_figure.ISSUED == {}

    wrong = [(_transposed(n), x, y) for n, x, y in _GRID]
    bad = asyncio.run(run(_package(pdf, wrong), region, "top", 100,
                          names_page=2, names_region=names_region))
    assert not bad["ok"] and "differ from the map" in bad["reason"]
    assert "compared" not in bad and compare_figure.ISSUED == {}

    good = asyncio.run(run(_package(pdf, _GRID), region, "top", 100,
                           names_page=2, names_region=names_region))
    assert good["ok"], good
    assert good["ball_names"]["checked"] == 9 and good["ball_names"]["mismatched"] == []
    assert compare_figure.ISSUED[good["compared"]] == 9


def test_lands_not_named_like_balls_need_no_map(monkeypatch, tmp_path, pdf):
    monkeypatch.setattr(compare_figure, "ISSUED", {})
    tools = _tools(monkeypatch, tmp_path)
    numbered = [(str(i + 1), x, y) for i, (_, x, y) in enumerate(_GRID)]
    out = asyncio.run(tools["footprint_compare_drawing"](
        _package(pdf, numbered), [0, 0, 1, 0.2], "top", 100))
    assert out["ok"] and "ball_names" not in out and out["compared"]
