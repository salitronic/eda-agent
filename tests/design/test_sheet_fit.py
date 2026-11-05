# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Nothing is drawn outside the sheet; a full sheet grows within its family."""

from __future__ import annotations

from eda_agent.design.sheet_fit import (ALTIUM_FRAMES, Box, altium_frame,
                                        fit_report, next_size)


def test_altium_frames_are_not_paper_sizes():
    assert altium_frame("A4") == (11500, 7600)
    assert altium_frame("A3") == (15500, 11100)       # paper is 16540 x 11690
    assert altium_frame("ANSI B") == (15000, 9500)


def test_families_grow_in_order_and_stop_at_the_top():
    assert next_size("A4") == "A3" and next_size("A1") == "A0"
    assert next_size("A0") is None
    assert next_size("ANSI A") == "B"
    assert next_size("OrCAD B") == "ORCADC"


def test_a_drawing_too_wide_for_a4_enlarges_to_a3():
    boxes = [Box("part", "U1", 300, 2000, 300 + 13000, 4000)]
    rep = fit_report(boxes, "A4")
    assert rep["verdict"] == "enlarge" and rep["to_size"] == "A3"


def test_a_drawing_that_fits_after_a_shift_is_not_enlarged():
    boxes = [Box("part", "U1", -2000, 3000, 2000, 4000)]   # 4000 wide, off-left
    rep = fit_report(boxes, "A4")
    assert rep["verdict"] == "shift" and rep["dx"] > 0


def test_the_title_block_is_kept_clear():
    w, h = ALTIUM_FRAMES["A4"]
    boxes = [Box("part", "U1", w - 1500, 300, w - 400, 900)]   # on the block
    rep = fit_report(boxes, "A4")
    assert rep["outside"] and rep["outside"][0].get("over_title_block")


def test_nothing_in_the_family_holds_it():
    boxes = [Box("part", "U1", 0, 0, 60000, 40000)]
    assert fit_report(boxes, "A4")["verdict"] == "too_big"


def _audit(frame_reply):
    import asyncio

    import eda_agent.tools.generic as g

    class _B:
        async def send_command_async(self, cmd, params=None, **kw):
            assert cmd == "generic.get_sheet_frame"
            return frame_reply

    tools = {}

    class _M:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    orig = g.get_bridge
    g.get_bridge = lambda: _B()
    try:
        g.register_generic_tools(_M())
        return asyncio.run(tools["sch_audit_outside_sheet"]())
    finally:
        g.get_bridge = orig


def _frame(objects, template_boxes=(), style=1):
    return {"file_path": "x.SchDoc", "frame_x": 15500, "frame_y": 11100,
            "sheet_style": style, "use_custom": False, "title_block_on": True,
            "template": "C:/T/Co_A3.SchDot", "objects": objects,
            "template_boxes": list(template_boxes), "count": len(objects),
            "truncated": False}


def test_audit_reads_the_live_frame_and_flags_a_part_past_it():
    out = _audit(_frame([
        {"type": "component", "label": "U1", "bbox": [1000, 3000, 2000, 4000]},
        {"type": "component", "label": "U2", "bbox": [15000, 3000, 15700, 4000]},
    ]))
    assert out["ok"] and out["size"] == "A3" and out["frame"] == [15500, 11100]
    assert [o["ref"] for o in out["outside"]] == ["U2"]
    assert out["verdict"] == "shift"          # 14700 wide fits the 15100 usable
    wide = _audit(_frame([
        {"type": "component", "label": "U1", "bbox": [1000, 3000, 2000, 4000]},
        {"type": "component", "label": "U2", "bbox": [15000, 3000, 16500, 4000]},
    ]))
    assert wide["verdict"] == "enlarge" and wide["to_size"] == "A2"


def test_document_parameters_are_not_drawn_objects():
    # Measured: a blank A4 SchDoc lists its Title/Date fields at 0,0.
    out = _audit(_frame([
        {"type": "parameter", "label": "", "bbox": [0, 0, 5571, 100]},
        {"type": "component", "label": "U1", "bbox": [1000, 3000, 2000, 4000]},
    ]))
    assert out["objects"] == 1 and out["verdict"] == "fits", out


def test_audit_takes_the_title_block_from_the_template_graphics():
    tb = [[11000, 200, 15300, 1700], [12000, 500, 15000, 900]]
    out = _audit(_frame([
        {"type": "text", "label": "", "bbox": [13000, 800, 14000, 1000]},
    ], template_boxes=tb))
    assert out["title_block"] == [4500, 1700]
    assert out["outside"] and out["outside"][0].get("over_title_block")


def test_template_family_swaps_the_size_token(tmp_path):
    from eda_agent.design.sheet_fit import template_for_size, template_size_token

    for name in ("Co_Sheet_A4.SchDot", "Co_Sheet_A3.SchDot",
                 "Co_Sheet_A2.SchDot", "Other_A3.SchDot", "ANSI B.SchDot",
                 "ANSI C.SchDot"):
        (tmp_path / name).write_text("")
    a4 = str(tmp_path / "Co_Sheet_A4.SchDot")
    assert template_size_token(a4) == "A4"
    assert template_for_size(a4, "A3").endswith("Co_Sheet_A3.SchDot")
    assert template_for_size(a4, "A1") is None            # not in the set
    assert template_for_size(str(tmp_path / "ANSI B.SchDot"), "C").endswith(
        "ANSI C.SchDot")


def test_a_shift_is_whole_grid_steps():
    # 150 past the left margin moves 200, so pins stay on the 100 mil grid.
    boxes = [Box("part", "U1", 50, 3000, 2050, 4000)]
    rep = fit_report(boxes, "A4")
    assert rep["verdict"] == "shift" and (rep["dx"], rep["dy"]) == (200, 0)
    assert fit_report(boxes, "A4", grid=0)["dx"] == 150


def _apply_audit(replies):
    """Run sch_audit_outside_sheet(apply=True) against scripted replies."""
    import asyncio

    import eda_agent.tools.generic as g

    sent = []

    class _B:
        async def send_command_async(self, cmd, params=None, **kw):
            sent.append((cmd, params))
            return replies[cmd].pop(0)

    tools = {}

    class _M:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

        def __getattr__(self, name):
            return lambda *a, **k: (lambda fn: fn)

    orig = g.get_bridge
    g.get_bridge = lambda: _B()
    try:
        g.register_generic_tools(_M())
        return asyncio.run(tools["sch_audit_outside_sheet"](apply=True)), sent
    finally:
        g.get_bridge = orig


def test_apply_shifts_everything_back_inside():
    off = _frame([{"type": "wire", "label": "", "bbox": [-510, 500, 10800, 5000]}])
    back = _frame([{"type": "wire", "label": "", "bbox": [290, 500, 11600, 5000]}])
    out, sent = _apply_audit({"generic.get_sheet_frame": [off, back],
                              "generic.shift_sheet": [{"moved": 1}]})
    assert ("generic.shift_sheet", {"dx": "800", "dy": "0"}) in sent
    assert out["verdict"] == "fits" and out["verdict_before"] == "shift"


def test_apply_enlarges_an_untemplated_sheet_then_rechecks():
    # The live run: a line, a rectangle past the A4 edge, one off the block.
    a4 = dict(_frame([
        {"type": "line", "label": "", "bbox": [990, 2990, 3010, 3010]},
        {"type": "rectangle", "label": "", "bbox": [10800, 4000, 12300, 5000]},
        {"type": "rectangle", "label": "", "bbox": [10000, 500, 10600, 900]}]),
              frame_x=11500, frame_y=7600, sheet_style=0, template="")
    a3 = dict(a4, frame_x=15500, frame_y=11100, sheet_style=1)
    sheet = r"C:\w\A.SchDoc"
    out, sent = _apply_audit({"generic.get_sheet_frame": [a4, a3],
                              "generic.set_sheet_size": [{"success": True}],
                              "application.get_active_document": [{"file_path": sheet}],
                              "application.save_document": [{"saved": True}],
                              "application.close_document": [{"closed": True}],
                              "application.run_process": [{"success": True}],
                              "application.set_active_document": [{"success": True}]})
    # The sheet is reopened after the size change: Altium's ERC keeps the
    # old frame until it is.
    assert [c for c, _ in sent] == ["generic.get_sheet_frame",
                                    "generic.set_sheet_size",
                                    "application.get_active_document",
                                    "application.save_document",
                                    "application.close_document",
                                    "application.run_process",
                                    "application.set_active_document",
                                    "generic.get_sheet_frame"]
    assert sent[1][1]["style"] == "A3" and out["verdict"] == "fits"


def test_apply_leaves_a_templated_sheet_to_the_caller():
    wide = _frame([{"type": "component", "label": "U2",
                    "bbox": [1000, 3000, 16500, 4000]}])
    out, sent = _apply_audit({"generic.get_sheet_frame": [wide]})
    assert [c for c, _ in sent] == ["generic.get_sheet_frame"]
    assert out["verdict"] == "enlarge" and "template" in out["not_applied"]
