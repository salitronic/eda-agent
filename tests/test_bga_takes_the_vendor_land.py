# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A BGA built where the datasheet gives a land size uses that size.

The DLPC3430 example board layout gives 0.23 mm lands for its 0.4 mm pitch
NFBGA. The spec carried it as vendor {"land": 0.23}, the key was dropped
without a word and the footprint came out with the IPC estimate, 0.20 mm.
"""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.builder import build_footprint
from eda_agent.design.landpattern.spec import PackageSpec, SpecError


def _bga(vendor=None):
    return PackageSpec(family="bga", rows=15, cols=15, pitch=0.4,
                       ball_diameter=[0.2, 0.25, 0.3], body_x=7.0, body_y=7.0,
                       vendor=vendor, source={"document": "test", "page": "1"})


def _sizes(model):
    return {(round(p.width, 3), round(p.height, 3), p.shape) for p in model.pads}


def test_the_vendor_land_diameter_is_built():
    m = build_footprint(_bga({"land_diameter": 0.23}))
    assert _sizes(m) == {(0.23, 0.23, "circle")}


def test_land_is_read_as_the_land_diameter():
    m = build_footprint(_bga({"land": 0.23}))
    assert _sizes(m) == {(0.23, 0.23, "circle")}


def test_without_a_vendor_land_the_ipc_estimate_stays():
    (w, h, shape), = _sizes(build_footprint(_bga()))
    assert shape == "circle" and w == h and w != 0.23


def test_an_unknown_vendor_field_is_refused_by_name():
    with pytest.raises(SpecError, match="no field landing"):
        _bga({"landing": 0.23})
