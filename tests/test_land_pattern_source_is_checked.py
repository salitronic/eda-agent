# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A footprint cited to a page the datasheet's land pattern is not on is
refused when the citation carries the datasheet's path.

The VL53L5CX was built from the package's own pads (p. 27) twice, and once
after the agent said ST gives no land pattern; ST's recommended solder
pattern is Fig. 28 on p. 33.
"""
from __future__ import annotations

import pytest

from eda_agent.design.landpattern.land_source import check_land_source, cited_pages
from eda_agent.design.landpattern.spec import Citation
from tests.test_datasheet_find_pages import _pdf

pytest.importorskip("pypdf")


@pytest.fixture
def datasheet(tmp_path):
    pdf = tmp_path / "part.pdf"
    pdf.write_bytes(_pdf(["Pin descriptions", "Package outline, bottom view",
                          "Figure 28. Recommended solder pattern, solder pad dimensions"]))
    return str(pdf)


def test_pages_cited_are_read_as_numbers_and_ranges():
    assert cited_pages("26-27") == {26, 27}
    assert cited_pages("p. 33, 58") == {33, 58}
    assert cited_pages("") == set()


def test_the_outline_page_is_refused_and_the_pattern_page_named(datasheet):
    out = check_land_source(Citation(page="2", path=datasheet))
    assert out["ok"] is False and out["land_pattern_pages"] == [3]
    assert "p. 3" in out["reason"]


def test_the_land_pattern_page_passes(datasheet):
    assert check_land_source(Citation(page="3", path=datasheet))["ok"]


def test_a_datasheet_with_no_pattern_for_this_package_says_so(datasheet):
    out = check_land_source(Citation(page="2", path=datasheet,
                                     ref="outline; no land pattern for this package"))
    assert out["ok"] and "no land pattern" in out["note"]


def test_no_path_no_check_and_a_missing_file_is_named(tmp_path):
    assert check_land_source(Citation(page="2")) is None
    assert check_land_source(Citation(page="2", path=str(tmp_path / "x.pdf")))["ok"] is False
