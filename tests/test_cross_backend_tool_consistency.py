# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A tool with one name must not mean two things depending on backend.

``pcb_calc_*`` are pure physics and EDA-independent, and both backends
wrap the SAME engine functions (``design.trace_sizing``,
``design.signal_integrity``, ``design.thermal_vias``). Only the thin tool
wrappers differ, so any divergence between them is accidental.

It matters because a user or a model that learns a tool on one backend
carries that knowledge to the other. Today five shared tools diverge, in
three different ways:

* pure renames -- ``z0_ohms``/``z0``, ``dielectric_constant``/``er``
* capability gaps -- Altium's track-current tool takes ``length_mils``
  (voltage drop) while KiCad's takes ``delta_t_c`` (temperature rise);
  neither is a rename of the other
* DIFFERENT DEFAULTS for the same parameter, which is the dangerous one:
  ``pcb_calc_length_match(geometry=...)`` defaults to ``microstrip`` on
  Altium and ``stripline`` on KiCad, so the identical call returns
  different physics with nothing to warn you

This file does NOT silently bless that. It pins the known set so the
problem cannot grow: a SIXTH divergent tool, or a new divergence in a
tool listed here, fails the test. Fixing the existing five is an API
decision (rename, alias, or unify) with consequences for callers, so it
is deliberately left to the owner rather than done implicitly here.
"""

from __future__ import annotations

import inspect

import pytest

from eda_agent.tools import register_backend
from eda_agent.tools.registry import ToolRegistry

#: Shared tools whose wrappers are known to differ today. Shrink this as
#: they are reconciled; never grow it without a reason recorded here.
KNOWN_DIVERGENT = {
    "pcb_calc_termination",
    "pcb_calc_thermal_vias",
    "pcb_calc_trace_width_for_current",
    "pcb_calc_track_current_capacity",
    "pcb_calc_length_match",
}


def _is_unset(value) -> bool:
    """True for the sentinels both sides use to mean "not supplied"."""
    return value is None or value == 0 or value == "" or value is False


def _surface(backend: str) -> dict[str, inspect.Signature]:
    registry = ToolRegistry()
    register_backend(registry, backend, "full")
    # Members of a family tool too: two backends advertising the same
    # family tool can still differ in what each kind takes.
    specs = {name: registry.get(name) for name in registry.names}
    specs.update(registry.hidden)
    # The tools as written: the items wrapper advertises a required
    # argument as optional, which is not a default of the physics.
    from eda_agent.tools.bulk import single
    return {name: inspect.signature(single(spec.fn))
            for name, spec in specs.items()}


@pytest.fixture(scope="module")
def surfaces():
    return _surface("altium"), _surface("kicad")


def test_no_new_cross_backend_divergence(surfaces):
    altium, kicad = surfaces
    shared = set(altium) & set(kicad)
    # 14 today. A bare truthiness check would still pass with ONE shared
    # tool, leaving this guard technically green while comparing almost
    # nothing -- the same way a scanner that stops matching keeps
    # reporting success.
    assert len(shared) >= 10, (
        f"only {len(shared)} tools shared between backends; this guard "
        f"compares too little to be meaningful")

    divergent = {
        name for name in shared
        if list(altium[name].parameters) != list(kicad[name].parameters)
    }
    new = divergent - KNOWN_DIVERGENT
    assert not new, (
        f"new cross-backend parameter divergence in {sorted(new)}. Both "
        f"backends wrap the same engine, so give the wrapper the same "
        f"parameter names rather than adding to KNOWN_DIVERGENT.")

    fixed = KNOWN_DIVERGENT - divergent
    assert not fixed, (
        f"{sorted(fixed)} no longer diverge; remove them from "
        f"KNOWN_DIVERGENT so the guard keeps its teeth.")


def test_shared_defaults_agree_except_where_known(surfaces):
    """Same parameter, same default. A silent physics change is worse
    than a missing argument: it returns a plausible wrong number.
    """
    altium, kicad = surfaces
    offenders: list[str] = []
    for name in sorted(set(altium) & set(kicad)):
        a_params = altium[name].parameters
        k_params = kicad[name].parameters
        for param in set(a_params) & set(k_params):
            a_default = a_params[param].default
            k_default = k_params[param].default
            if a_default is inspect.Parameter.empty or \
                    k_default is inspect.Parameter.empty:
                continue
            # The two sides spell "not supplied" differently: Altium uses
            # 0/0.0/"" sentinels, KiCad uses None. That is a convention
            # difference, not a physics one, and flagging it would bury
            # the case that actually changes an answer.
            if _is_unset(a_default) and _is_unset(k_default):
                continue
            if a_default != k_default:
                offenders.append(f"{name}.{param}: "
                                 f"altium={a_default!r} kicad={k_default!r}")

    # pcb_calc_length_match.geometry is the one already known to differ
    # (microstrip vs stripline). Anything else is a new silent divergence.
    unexpected = [o for o in offenders
                  if not o.startswith("pcb_calc_length_match.geometry")]
    assert not unexpected, (
        "same parameter, different default across backends, so an "
        "identical call returns different physics:\n  "
        + "\n  ".join(unexpected))
