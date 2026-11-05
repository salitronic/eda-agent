# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""sim_get_readiness calls a part ready only when a SIM model is linked.

Retake 07b: sim_attach_primitives answered attached 9/9, readiness listed
all nine as ready, and the netlister said "Missing component models" for
every one. Readiness read the SpicePrefix parameter the attach writes,
not the model link Altium's netlister needs.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_GENERIC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Generic.pas"


def test_ready_requires_the_model_link():
    body = load(_GENERIC)["Gen_GetSimulationReadiness"]
    ready = body.index("ReadyJson := ReadyJson")
    branch = body.rfind("SpicePrefix <> ''", 0, ready)
    assert branch > 0
    line = body[body.rfind("If", 0, branch):body.index("Then", branch)]
    assert re.search(r"SimModel\s*<>\s*''", line), (
        "a part is called ready without a SIM model link")
    assert "CompSimModelName(Comp)" in body
    assert '"no_sim_model":[' in body


def test_the_model_is_read_from_a_sim_implementation():
    body = load(_GENERIC)["CompSimModelName"]
    assert "eImplementation" in body
    assert "UpperCase(ModelType) = 'SIM'" in body
