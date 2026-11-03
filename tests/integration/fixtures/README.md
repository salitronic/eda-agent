# Integration test fixtures

This directory holds the Altium project that the real-Altium integration
tests under `tests/integration/` run against.

## What lives here

`EDAAgentTest.PrjPcb` (with its `.PrjPcbStructure`) is checked in; the
tests open it by that name. Its documents are not: one schematic sheet
with a few known parts, one PCB with those parts placed, and a small
schematic library so the parts are local. Open the project in Altium to
see the names it lists, and build them once locally.

The project should be small enough that a compile takes under 2 s.

## Why the documents aren't checked in

Altium design files are binary-ish (they survive git diff but mutate on
every open/close) and depend on the Altium version. Rather than ship
files that drift under each contributor's installation, we document the
shape and let you build them once locally.

## How to (re)build them

1. Open `EDAAgentTest.PrjPcb` from this directory in Altium Designer.
2. Add a new schematic sheet under the name the project lists.
3. On it:
    - One resistor (designator `R1`, value `10k`)
    - One capacitor (designator `C1`, value `100n`)
    - One IC with at least 4 pins (designator `U1`), from the project's
      library
    - VCC and GND power ports connected to the parts
4. Add the PCB under the name the project lists.
5. Run `Design > Update PCB Document` so the components flow over.
6. Place the components anywhere on the board; route a couple of tracks.
7. Save everything.

## Running the integration tests

They are opt-in. Without `EDA_AGENT_INTEGRATION=1` every test here is
skipped at collection, before anything reaches Altium, so a plain
`pytest tests/` never touches a live session. With it set, Altium must be
running with the script (`Altium_API.PrjScr`) loaded and `StartMCPServer`
running; a missing precondition is then a failure, not a skip.

```
EDA_AGENT_INTEGRATION=1 pytest tests/integration/ -v
```
