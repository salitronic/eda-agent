# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The KiCad API codec and item edits, without a running KiCad."""
from __future__ import annotations

import math

import pytest

from eda_agent.bridge import kicad_api as api
from eda_agent.bridge.kicad_wire import Message, decode_varint, encode_varint


@pytest.mark.parametrize("n", [0, 1, 127, 128, 300, 2**31, 2**63 - 1, -1, -1_000_000])
def test_varints_round_trip(n):
    data = encode_varint(n)
    value, end = decode_varint(data, 0)
    assert end == len(data)
    assert (value - (1 << 64) if value >= 1 << 63 else value) == n


def test_negative_coordinates_survive():
    v = api.vec(-12.5, 3.25)
    assert api.read_vec(Message.decode(v.encode())) == [-12.5, 3.25]


def test_a_field_nobody_knows_comes_back_unchanged():
    original = Message().set_int(1, 7).set_str(42, "future field").set_double(3, 1.5)
    edited = Message.decode(original.encode()).set_int(1, 8)
    again = Message.decode(edited.encode())
    assert again.str(42) == "future field"
    assert again.double(3) == 1.5 and again.int(1) == 8


def test_an_unchanged_message_encodes_to_the_same_bytes():
    raw = api.make_track((1, 2), (3, 4), 0.25, 3, "GND").message.encode()
    assert Message.decode(raw).encode() == raw


def test_repeated_integers_read_packed_or_not():
    packed = Message().set_ints(2, [3, 34, 40])
    assert packed.ints(2) == [3, 34, 40]
    loose = Message([[2, 0, 3], [2, 0, 34]])
    assert loose.ints(2) == [3, 34]


def test_layers_by_file_display_and_board_name():
    assert api.layer_number("F.SilkS") == api.layer_number("F.Silkscreen") == 40
    assert api.layer_number("in2.cu") == 5
    assert api.layer_number("top_layer", {"top_layer": 3}) == 3
    with pytest.raises(ValueError):
        api.layer_number("No.Such")


def test_items_read_back_as_built():
    track = api.make_track((10, 20), (13, 24), 0.2, 3, "VCC").read()
    assert track["width_mm"] == 0.2 and track["net"] == "VCC"
    assert track["length_mm"] == 5.0 and track["layer"] == "F.Cu"
    via = api.make_via((5, 5), 0.8, 0.4, "GND").read()
    assert via["diameter_mm"] == 0.8 and via["drill_mm"] == 0.4
    zone = api.make_zone([(0, 0), (4, 0), (4, 3)], [34], net_name="GND", priority=2).read()
    assert zone["layers"] == ["B.Cu"] and zone["net"] == "GND" and zone["priority"] == 2
    keepout = api.make_zone([(0, 0), (1, 0), (1, 1)], [3], keepout={"copper": True}).read()
    assert keepout["zone_type"] == "rule_area" and keepout["keepout"]["copper"]


def _footprint(x, y, pads):
    """A footprint instance as KiCad sends one: its pads at absolute positions."""
    children = [Message().set_str(1, "type.googleapis.com/kiapi.board.types.Pad")
                .set_bytes(2, Message().set_str(3, str(n)).set_msg(7, api.vec(px, py))
                           .set_msg(6, Message().set_msg(6, api.angle(0))).encode())
                for n, (px, py) in enumerate(pads, start=1)]
    definition = Message().set_msgs(11, children)
    ref_text = Message().set_msg(2, Message().set_msg(2, api.vec(x, y - 2)).set_str(5, "U1"))
    m = Message().set_msg(1, api.kiid("fp")).set_msg(2, api.vec(x, y)) \
        .set_msg(3, api.angle(0)).set_int(4, 3).set_msg(6, definition) \
        .set_msg(7, Message().set_msg(3, ref_text))
    return api.Item("FootprintInstance", m)


def test_moving_a_footprint_carries_its_pads_and_fields():
    fp = _footprint(10, 10, [(10, 10), (12.54, 10)])
    moved = api.patch(fp, {"position_mm": (15, 7)}).read()
    assert moved["position_mm"] == [15.0, 7.0]
    assert [p["position_mm"] for p in moved["pads"]] == [[15.0, 7.0], [17.54, 7.0]]
    ref = api.patch(fp, {"position_mm": (15, 7)}).message.msg(7).msg(3).msg(2)
    assert api.read_vec(ref.msg(2)) == [15.0, 5.0]


def test_turning_a_footprint_turns_its_pads_counter_clockwise():
    # Y points down: a pad 1 mm to the right ends up 1 mm above after +90.
    fp = _footprint(0, 0, [(1, 0)])
    turned = api.patch(fp, {"rotation_deg": 90}).read()
    x, y = turned["pads"][0]["position_mm"]
    assert math.isclose(x, 0, abs_tol=1e-6) and math.isclose(y, -1, abs_tol=1e-6)
    assert turned["pads"][0]["angle_deg"] == 90.0
    assert turned["rotation_deg"] == 90.0


def test_turning_there_and_back_restores_every_pad():
    fp = _footprint(3, 4, [(3, 4), (5.54, 4), (5.54, 11.62)])
    there = api.patch(fp, {"rotation_deg": 37.5})
    back = api.patch(there, {"rotation_deg": 0}).read()
    for pad, want in zip(back["pads"], [(3, 4), (5.54, 4), (5.54, 11.62)]):
        assert math.dist(pad["position_mm"], want) < 2e-6


def test_turning_over_is_refused_with_the_tool_to_use():
    with pytest.raises(ValueError, match="flip"):
        api.patch(_footprint(0, 0, [(0, 0)]), {"side": "bottom"})


def test_a_field_that_cannot_be_written_is_named():
    with pytest.raises(ValueError, match="drill_mm cannot be set on a track"):
        api.patch(api.make_track((0, 0), (1, 0), 0.2, 3), {"drill_mm": 0.3})
