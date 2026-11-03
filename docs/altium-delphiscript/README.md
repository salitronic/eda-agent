# Altium DelphiScript API Reference

A reference for the Altium Designer object model as reached from DelphiScript:
the global **servers**, the **interfaces** they hand back (`ISch_*`, `IPCB_*`,
`IWorkspace`, `IProject`, `IDocument`, …), the **enum** vocabulary (`eXxx`), and
the **value types** (`TLocation`, `TCoord`, …).

Every member documented here is one the **eda-agent bridge actually calls** in
working, deployed DelphiScript (`scripts/altium/*.pas`). The reference is
extracted from that implementation, so a signature listed here is one that has
run against a real Altium instance, not a transcription of external
documentation. Members Altium exposes but this project does not use are out of
scope by design.

It was extracted in August 2026 and is not regenerated: the bridge has
reached more of the API since (the Layer Stack Manager, embedded objects, the
primitive counter, the view rectangle and others). For a member not listed
here, the sources under `scripts/altium/` are the authority.

---

## The object model

DelphiScript reaches Altium through a small set of **global server objects**.
From a server you obtain a **document**, from a document its **objects**, and you
walk collections with **iterators**.

```
Client                      the running application (IClient)
  └─ documents              IServerDocument  (any open file: SchDoc, PcbDoc, …)

GetWorkspace -> IWorkspace   the design workspace / focused project tree
  └─ IProject                a .PrjPcb / .PrjScr and its logical documents
       └─ IDocument          a logical document + its flattened (DM_*) netlist

SchServer  (schematic)       ISch_ServerInterface
  ├─ ISch_Document           a .SchDoc sheet  OR  ISch_Lib (.SchLib)
  │    └─ ISch_Component      a placed part / a library symbol
  │         ├─ ISch_Pin       pins
  │         ├─ ISch_Parameter parameters
  │         └─ ISch_*         primitives: Line, Rectangle, Arc, Label, Wire,
  │                           NetLabel, PowerObject, SheetSymbol, …
  └─ ISch_Iterator           walks objects on a sheet / children of a component

PCBServer  (board)           IPCB_ServerInterface
  ├─ IPCB_Board              a .PcbDoc
  │    └─ IPCB_Primitive      base of every board object
  │         └─ IPCB_Pad / Track / Via / Arc / Text / Polygon / Region / Net / …
  ├─ IPCB_Library            a .PcbLib  ->  IPCB_LibComponent (a footprint)
  └─ IPCB_BoardIterator      board / IPCB_SpatialIterator / IPCB_GroupIterator
```

The two editor domains are independent: schematic objects come from `SchServer`
and carry `eSch*` ObjectIds; board objects come from `PCBServer` and carry
`e*Object` ObjectIds. The workspace / project / document-model layer
(`IWorkspace` → `IProject` → `IDocument`) sits above both and is where the
compiled, flattened netlist (`DM_*`) lives.

---

## How to read an entry

Each interface gets an overview and a worked example, then every member is
documented as:

> **`MemberName(args) : ReturnType`**
> A description of what it does: its parameters, what it returns, its behaviour,
> and any caveat: followed by a code example where it clarifies usage.

`args`/`ReturnType` reflect how the member is called; where Altium's full
signature has additional optional parameters not used here, the entry notes
"(as called)". Properties are shown as `Name : Type`.

---

## Conventions

- **Interfaces** are `IXxx` (`ISch_Document`, `IPCB_Pad`). A variable is declared
  of the interface type and tested with `<> Nil`; subtype access uses the
  narrowing pattern (assign a base value into a typed-subtype local after an
  `ObjectId` check: there are no inline casts).
- **Enums** are `eXxx` ordinals (`eSchComponent`, `eTopLayer`, `eRounded`). Sets
  of them are built with `MkSet(...)`.
- **Types** are `TXxx` (`TLocation`, `TCoord`, `TLayer`). Record-typed properties
  (`Location`, `Corner`) return a **copy**: read into a local, mutate, assign
  back.
- **Coordinates** are Altium internal units: `1 mil = 10000 internal units`
  (1 unit ≈ 2.54 nm). Convert with `MilsToCoord` / `CoordToMils`. Angles are in
  degrees (`Double`); pin orientation is the ordinal `degrees Div 90` (0/1/2/3).

---

## Pages

| # | File | Covers |
|---|------|--------|
| 1 | [`01-servers.md`](01-servers.md) | The global servers: `SchServer`, `PCBServer`, `Client`, `GetWorkspace`, `IntegratedLibraryManager`: their methods and what they return. |
| 2 | [`02-schematic-interfaces.md`](02-schematic-interfaces.md) | `ISch_Document` / `ISch_Lib`, `ISch_Component`, `ISch_Pin`, `ISch_Parameter`, the primitive interfaces, and `ISch_Iterator`. |
| 3 | [`03-pcb-interfaces.md`](03-pcb-interfaces.md) | `IPCB_Board`, `IPCB_Primitive` and the board objects (`Pad`/`Track`/`Via`/`Arc`/`Text`/`Polygon`/`Region`/`Net`/`Rule`), `IPCB_Library` / `IPCB_LibComponent`, the iterators, layer stack. |
| 4 | [`04-workspace-project-documents.md`](04-workspace-project-documents.md) | `IWorkspace`, `IProject`, `IProjectVariant`, `IDocument` (the `DM_*` flattened netlist), `IServerDocument`, `IComponent`. |
| 5 | [`05-enums.md`](05-enums.md) | The `eXxx` vocabulary grouped by domain: ObjectIds, layers, electrical pin types, rotations, pad shapes, power-object styles. |
| 6 | [`06-types-and-coordinates.md`](06-types-and-coordinates.md) | `TLocation`, `TCoord`, `TLayer`, the internal-unit system, `MilsToCoord` / `CoordToMils`. |
