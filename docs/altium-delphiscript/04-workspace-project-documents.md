# 4. Workspace, projects & the document model (`DM_*`)

Above the schematic and board editors sits the **document model**: the compiled,
flattened view of a project: its logical documents, components, pins, nets,
parameters and variants. Its members are prefixed **`DM_`** and reached from
`GetWorkspace : IWorkspace` ([page 1](01-servers.md)). This is the layer that
gives a project-wide netlist without walking sheets primitive by primitive, and
it is where the *connectivity* (which pin sits on which net) actually lives.

```pascal
// Canonical model read: focused project -> compile -> walk components & pins.
Var WS : IWorkspace; Prj : IProject; Doc : IDocument;
    Comp : IComponent; Pin : IPin; I, J : Integer;
Begin
    WS := GetWorkspace;                  If WS  = Nil Then Exit;
    Prj := WS.DM_FocusedProject;         If Prj = Nil Then Exit;
    Prj.DM_Compile;                       // refresh the flattened model first
    For I := 0 To Prj.DM_DocumentFlattened.DM_ComponentCount - 1 Do
    Begin
        Comp := Prj.DM_DocumentFlattened.DM_Components(I);
        For J := 0 To Comp.DM_PinCount - 1 Do
        Begin
            Pin := Comp.DM_Pins(J);
            Report(Comp.DM_PhysicalDesignator, Pin.DM_PinNumber,
                   Pin.DM_FlattenedNetName);   // the canonical connectivity
        End;
    End;
End;
```

> **Compile first.** The `DM_*` collections reflect the *compiled* project. Call
> `Project.DM_Compile` after any edit (or whenever a document is dirty) before
> trusting `DM_Components` / `DM_Pins` / `DM_FlattenedNetName`; a stale model
> returns the previous netlist.

> **Collection pattern.** Every collection is a *count* + an *indexed accessor*,
> the accessor written as a method call: `DM_ProjectCount` with
> `DM_Projects(I)`, `DM_ComponentCount` with `DM_Components(I)`, `DM_PinCount`
> with `DM_Pins(I)`, `DM_NetCount` with `DM_Nets(I)`, `DM_ParameterCount` with
> `DM_Parameters(I)`. Loop `For I := 0 To Count - 1`.

---

## 4.1 `IWorkspace`: the workspace (`GetWorkspace`)

The top of the model tree: the open projects and what the user is focused on.

**`DM_FocusedProject : IProject`**
The project the user is currently working in. The usual entry point; guard for
`Nil` (no project open).

**`DM_FocusedDocument : IDocument`**
The focused logical document (the active sheet/board as a model object).

**`DM_ProjectCount : Integer`** / **`DM_Projects(I) : IProject`**
The open projects: iterate to operate across all of them.

**`DM_FreeDocumentsProject : IProject`**
The synthetic project that holds standalone (project-less) documents, so a loose
`.SchDoc` still has a containing `IProject`.

---

## 4.2 `IProject`: a project (`.PrjPcb` / `.PrjScr`)

A logical project and its compiled model. Compile it, then read its documents,
netlist, parameters, variants and violations.

### Identity and documents

**`DM_ProjectFullPath : String`** / **`DM_ProjectFileName : String`**
The project's full path / bare file name.

**`DM_LogicalDocumentCount : Integer`** / **`DM_LogicalDocuments(I) : IDocument`**
The source documents as authored (each sheet / board once).

**`DM_PhysicalDocumentCount : Integer`** / **`DM_PhysicalDocuments(I) : IDocument`**
The physical documents after channel expansion (a sheet used in N channels
appears N times): the basis for per-channel designators.

**`DM_DocumentFlattened : IDocument`**
The single whole-project flattened document. Read its `DM_Components` /
`DM_Nets` for the project-wide netlist (the example at the top uses this).

**`DM_PrimaryImplementationDocument : IDocument`**
The primary PCB implementation of the project.

**`DM_AddSourceDocument(Path : String)`**
Adds a document to the project.

### Compile and netlisting options

**`DM_Compile`**
Compiles the project and rebuilds the flattened model. Call before reading any
`DM_*` connectivity (see the warning above).

**`DM_HierarchyMode`**  *(property)*
Flat vs hierarchical netlisting mode.

**`DM_GetAppendSheetNumberToLocalNets : Boolean`** / **`DM_GetAllowPortNetNames`** / **`DM_GetAllowSheetEntryNetNames`** / **`DM_GetOutputPath : String`**  *(properties)*
The netlisting/output options that shape how net names are formed and where
output is written: read them so a generated netlist matches Altium's.

**`DM_ChannelDesignatorFormat`** / **`DM_ChannelRoomLevelSeperator`**  *(properties)*
The multi-channel designator format and room-level separator (how repeated
channels are named).

### Netlist, parameters, violations

**`DM_NetCount : Integer`** / **`DM_Nets(I) : INet`**
The project's flattened nets (on the flattened document).

**`DM_ParameterCount : Integer`** / **`DM_Parameters(I) : IParameter`**
Project-level parameters (each with `DM_Name` / `DM_Value`).

**`DM_ViolationCount : Integer`** / **`DM_Violations(I)`**
The compile / ERC violations, each carries `DM_ShortDescriptorString` /
`DM_LongDescriptorString` and a location.

**`DM_ComponentMappings`**
The component-to-implementation (symbol→footprint) mappings.

### Variants

**`DM_ProjectVariantCount : Integer`** / **`DM_ProjectVariants(I) : IProjectVariant`**
The assembly variants (§4.3).

**`DM_CurrentProjectVariant : IProjectVariant`**
The active variant: what `DM_VariationKind` is resolved against.

---

## 4.3 `IProjectVariant`: an assembly variant

One assembly variant and its per-component deviations from the base design.

**`DM_Name : String`** / **`DM_Description : String`**  *(properties)*: identity.

**`DM_VariationCount : Integer`** / **`DM_Variations(I)`**
The per-component variations under this variant.

**`DM_FindComponentVariationByUniqueId(Id : String)`**
Looks up one component's variation by its unique id: the direct path when you
already have the component.

A single **variation** exposes **`DM_VariationKind`** (fitted / not-fitted /
alternate), **`DM_AlternatePart`** (the swapped part, when alternate), and
**`DM_VariedValue`** (the overridden value).

---

## 4.4 `IDocument`: a logical document in the model

A sheet or board as a model object (from `IProject.DM_LogicalDocuments(I)`,
`DM_DocumentFlattened`, or `IWorkspace.DM_FocusedDocument`).

**`DM_FullPath : String`** / **`DM_FileName : String`**  *(properties)*: path / name.

**`DM_DocumentKind : String`**  *(property)*: `'SCH'`, `'PCB'`, … .

**`DM_ComponentCount : Integer`** / **`DM_Components(I) : IComponent`**
The document's components (model side, §4.5).

**`DM_NetCount : Integer`** / **`DM_Nets(I) : INet`**
The document's nets.

**`DM_PortCount : Integer`** / **`DM_Ports(I)`**
The sheet ports (the off-sheet connectors).

**`DM_SheetSymbolCount : Integer`** / **`DM_SheetSymbols(I)`**: the sheet symbols
(hierarchy children), each exposing **`DM_SheetEntryCount` / `DM_SheetEntries(I)`**.

**A port's or sheet entry's two net names** (both `INetItem`; read only, the
compiler sets them; measured on AD 25.8.1):
- **`DM_NetName`**: the net's name on the item's OWN sheet: the label, power
  port or port that names the wire it sits on there.
- **`DM_FlattenedNetName`, read on the item of a PHYSICAL document**
  (`IProject.DM_PhysicalDocuments(I)`, after a compile): the name of the net
  it ends up in across the whole project, which Altium's Properties panel
  calls the physical net. A repeated (multi-channel) sheet has one physical
  document per channel, told apart by `DM_PhysicalInstanceName`, so each copy
  of a port has its own physical net.

The same member read on the item of a sheet's LOGICAL document
(`DM_LogicalDocuments`) only repeats `DM_NetName`; `DM_OwnerNetLogical` gives
that name too and `DM_OwnerNetPhysical` gives nothing. In the flattened
document (`DM_DocumentFlattened`) the nets keep their labels and pins (each
label's `DM_FlattenedNetName` is the project-wide name there) but list no
ports or sheet entries.

They differ when a name from another sheet wins. Example: a label
`PARENT_NET` on the wire to a sheet entry, and a label `CHILD_NET`
on the wire to the matching port in the child sheet. The entry's `DM_NetName`
is `PARENT_NET` and the port's is `CHILD_NET`, whatever the
project options say. Which name the whole net takes depends on the project
option "Higher Level Names Take Priority" (Project Options, Options tab): off,
the child's label won (`CHILD_NET`); on, the parent's label wins
(`PARENT_NET`), so names drive down the hierarchy. The compiler reports
every such net as the warning "Nets with multiple names", listing each label,
port and entry that names it (`run_erc` / `get_erc_violations`).
`proj_get_hierarchy` returns them per port and entry as `net` and `physical`
(one `{instance, net}` per physical copy, matched by sheet, kind and
location), with `x` / `y`; **`DM_LocationX` / `DM_LocationY`** are in internal
units (10000 per mil). A sheet entry's `DM_PortName` is empty: the compiler
gives an entry no name of its own, only its net's.

**`DM_ConstraintGroupCount : Integer`** / **`DM_ConstraintGroups(I)`**
The constraint groups on the document; a group exposes
**`DM_ConstraintCount` / `DM_Constraints(I)`**.

---

## 4.5 `IComponent`, `IPin` and `INet`: model components, pins, nets

### `IComponent`

A model component (from `Document.DM_Components(I)` or `Pin.DM_Part`).

**`DM_PhysicalDesignator : String`**  *(property)*
The resolved refdes after channel expansion (`'U1'`, `'U1_2'`): the one to
report. **`DM_LogicalDesignator`** is the pre-expansion designator.

**`DM_Comment : String`** / **`DM_Name : String`**  *(properties)*: comment/value and name.

**`DM_LibraryReference : String`** / **`DM_Footprint : String`**  *(properties)*
The symbol library reference and the assigned footprint name.

**`DM_UniqueId : String`**  *(property)*
The stable unique id that ties a schematic component to its PCB component (the
key behind `DM_FindComponentVariationByUniqueId` and sync).

**`DM_PinCount : Integer`** / **`DM_Pins(I) : IPin`**: its pins.

**`DM_ParameterCount : Integer`** / **`DM_Parameters(I)`**
Its parameters; each exposes **`DM_Name`** (also **`DM_ParameterName`**) and
**`DM_Value`**.

**`DM_SubPartCount : Integer`** / **`DM_SubParts(I)`**
The sub-parts of a multi-part component (e.g. the four gates of a quad package).

**`DM_LocationX` / `DM_LocationY`** / **`DM_LocationString : String`**  *(properties)*
The component's placement coordinates on the sheet / a formatted location string.

**`DM_VariationKind`**  *(property)*
Fitted / not-fitted / alternate under the current variant.

### `IPin`

A model pin (from `IComponent.DM_Pins(I)`, or an `ISch_Pin`'s `DM_*` members).

**`DM_PinNumber : String`** / **`DM_PinName : String`**  *(properties)*: number and name.

**`DM_FlattenedNetName : String`**  *(property)*
The net this pin connects to in the flattened design: **the canonical
connectivity read**. Build a netlist by grouping pins on equal
`DM_FlattenedNetName`. **`DM_FlattenedNet`** returns the `INet` object itself.

**`DM_Part : IComponent`**  *(property)*: the owning component.

**`DM_Electrical`**  *(property)*: the pin's electrical type (input/output/power/…).

**`DM_Value : String`**  *(property)*: the pin's value, where applicable.

### `INet`

A model net (from `Document.DM_Nets(I)` / `Project.DM_Nets(I)` /
`Pin.DM_FlattenedNet`).

**`DM_NetName : String`**  *(property)*: the net name.

**`DM_PinCount : Integer`** / **`DM_Pins(I) : IPin`**: the pins on the net.

**`DM_NetLabelCount`** / **`DM_PortCount`** / **`DM_PowerObjectCount : Integer`**  *(properties)*
How many net labels / ports / power objects name this net: a net named only by
a single label/port is a connectivity smell an audit flags.

---

## 4.6 Schematic↔PCB comparison (`DM_` differences)

The compile/sync comparison surface, used to diff schematic against board.

**`DM_MatchedComponentCount : Integer`**
How many components matched between source and target.

**`DM_UnmatchedSourceComponentCount : Integer`** / **`DM_UnmatchedSourceComponent(I)`**
Components on the schematic with no board counterpart.

**`DM_UnmatchedTargetComponentCount : Integer`** / **`DM_UnmatchedTargetComponent(I)`**
Components on the board with no schematic counterpart. Together these drive a
"what's out of sync" report (the model-level complement of an ECO).

**`DM_TargetId`** / **`DM_TargetKindString : String`**  *(properties)*
Identify a difference's target object and its kind.

---

## 4.7 `IServerDocument`: the open editor document

The raw open file as the application holds it (from `Client.GetDocumentByPath` /
`Client.OpenDocument`), distinct from the model `IDocument`. Use it to save and
focus files.

**`FileName : String`** / **`SetFileName(Path : String)`**
The document's path; rename/retarget before a save-as.

**`Modified : Boolean`** / **`SetModified(Value : Boolean)`**
The dirty flag: read to decide whether a save is needed, set to force/clear it.

**`DoFileSave(Kind : String)`**
Writes the document to disk (`Kind` is the document kind, e.g. `'PCB'`).

**`Focus`**
Brings the document to the active view.
