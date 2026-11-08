# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The land-pattern figure is rendered zoomed and set beside the lands.

Take 25 built three footprints wrong (BGT60LTR11AIP rows on an even grid
where the figure prints 0.75, TPSM63603 corner lands without their step,
SIM7080G pad 69 without its cut corner); each was checked against the
builder's own reading. Side by side with the figure each showed at once.
"""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pypdfium2")
pytest.importorskip("PIL")

from eda_agent.design.datasheet_render import render_region
from tests.test_datasheet_find_pages import _pdf


def test_a_region_is_rendered_with_the_numbers_printed_in_it(tmp_path):
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(_pdf(["Pin table", "Land pattern 0.75 between rows, pitch 0.5"]))
    out = render_region(str(pdf), 2, [0.0, 0.0, 1.0, 0.2], 200, str(tmp_path / "p2.png"))
    assert out["ok"] and (tmp_path / "p2.png").is_file()
    assert {n["value"] for n in out["numbers"]} == {"0.75", "0.5"}
    whole = render_region(str(pdf), 1, None, 100, str(tmp_path / "p1.png"))
    assert whole["ok"] and whole["numbers"] == [] and "picture" in whole["note"]


@pytest.mark.parametrize("region", [[0.5, 0, 0.4, 1], [0, 0, 1], [-0.1, 0, 1, 1]])
def test_a_bad_region_is_refused(tmp_path, region):
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(_pdf(["x"]))
    assert not render_region(str(pdf), 1, region)["ok"]


def _tools(monkeypatch, tmp_path):
    from eda_agent.tools import footprint

    class _Cfg:
        workspace_dir = tmp_path

    monkeypatch.setattr(footprint, "get_config", lambda: _Cfg())
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    footprint.register_footprint_tools(_Mcp())
    return tools


def test_the_figure_is_set_beside_the_lands_and_a_missing_number_named(monkeypatch, tmp_path):
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(_pdf(["Outline", "Land pattern: rows 0.5 apart, 0.75 between rows 3 and 4"]))
    rows = [("1", -0.875), ("2", -0.375), ("3", 0.125), ("4", 0.625)]  # 0.5 everywhere
    pkg = {"family": "custom", "name": "ROWS", "body_x": 3, "body_y": 3,
           "source": {"document": "ds", "page": "2", "path": str(pdf)},
           "custom_pads": [{"number": n, "x": 0, "y": y, "width": 0.25,
                            "height": 0.25, "shape": "circle"} for n, y in rows]}
    tools = _tools(monkeypatch, tmp_path)
    out = asyncio.run(tools["footprint_compare_drawing"](pkg, [0, 0, 1, 0.3], "bottom", 150))
    assert out["ok"], out
    assert out["not_built"] == ["0.75"]
    from eda_agent.design.landpattern.compare_figure import ISSUED
    assert ISSUED[out["compared"]] == 4
    assert (tmp_path / "renders" / "ROWS_vs_drawing.png").is_file()
    again = asyncio.run(tools["footprint_compare_drawing"](pkg, [0, 0, 1, 0.3], "bottom", 150))
    assert again["compared"] == out["compared"]
    top = asyncio.run(tools["footprint_compare_drawing"](pkg, [0, 0, 1, 0.3], "top", 150))
    assert top["compared"] != out["compared"]


def test_the_comparison_needs_the_figure_page(monkeypatch, tmp_path):
    tools = _tools(monkeypatch, tmp_path)
    pkg = {"family": "custom", "name": "X", "body_x": 1, "body_y": 1,
           "custom_pads": [{"number": "1", "x": 0, "y": 0, "width": 0.3, "height": 0.3}]}
    out = asyncio.run(tools["footprint_compare_drawing"](pkg, [0, 0, 1, 1]))
    assert not out["ok"] and "source needs path" in out["reason"]


def test_renders_from_many_threads_at_once_all_succeed(tmp_path):
    """A batched call rendered three pages at once and PDFium answered
    "Data format error" for every file from then on."""
    from concurrent.futures import ThreadPoolExecutor
    pdf = tmp_path / "ds.pdf"
    pdf.write_bytes(_pdf(["a 0.5", "b 0.75", "c 1.25", "d 2.5"] * 3))
    with ThreadPoolExecutor(8) as pool:
        outs = list(pool.map(lambda n: render_region(str(pdf), n % 12 + 1, None, 72,
                                                     str(tmp_path / f"p{n}.png")),
                             range(32)))
    assert all(o["ok"] for o in outs)
    assert render_region(str(pdf), 1, None, 72, str(tmp_path / "after.png"))["ok"]
