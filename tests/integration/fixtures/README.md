# Integration test fixtures

This directory holds the Altium project that the real-Altium integration
tests under `tests/integration/` run against.

## What needs to live here

```
EDAAgentTest.PrjPcb            # project file
EDAAgentTest.SchDoc            # one schematic sheet with a few known parts
EDAAgentTest.PcbDoc            # one PCB doc with a known component placement
SELibrary_INTEGRATION.SchLib   # tiny library so the test parts are local
```

The fixture project should be small enough that compiles take <2 s and
should contain a known, stable set of components/nets/pads. Tests assert
on names, counts, and netlists from this project.

## Why isn't it checked in?

Altium project files are binary-ish (they survive git diff but mutate on
every open/close) and depend on the Altium version. Rather than ship a
file that drifts under each contributor's installation, we document the
shape and let you build it once locally.

## How to (re)build it

1. In Altium Designer: `File > New > Project > PCB Project`.
2. Save as `EDAAgentTest.PrjPcb` in this directory.
3. Add a new schematic sheet, save as `EDAAgentTest.SchDoc`.
4. Place at minimum:
    - One resistor (designator `R1`, value `10k`)
    - One capacitor (designator `C1`, value `100n`)
    - One IC with at least 4 pins (designator `U1`)
    - VCC and GND power ports connected to the parts
5. Add a new PCB doc, save as `EDAAgentTest.PcbDoc`.
6. Run `Design > Update PCB Document` so the components flow over.
7. Place the components anywhere on the board; route a couple of tracks.
8. Save everything.

## Running the integration tests

With Altium running and the script (`Altium_API.PrjScr`) loaded with
`StartMCPServer` running:

```
pytest tests/integration/ -v
```

Without those preconditions met, the tests skip cleanly. Set
`EDA_AGENT_INTEGRATION=1` in the environment to make missing preconditions
hard-fail instead — appropriate for CI on a Windows runner with Altium.
