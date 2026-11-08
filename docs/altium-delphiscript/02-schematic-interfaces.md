# 2. Schematic interfaces (`ISch_*`)

The schematic object model hangs off `SchServer` ([1](01-servers.md)). A document
(`ISch_Document`, or `ISch_Lib` for a `.SchLib`) holds components; a component
holds pins, parameters, and graphics; collections are walked with
`ISch_Iterator`. Geometry is in internal units (`MilsToCoord`); record-typed
properties (`Location`, `Corner`) return copies (see 2.7).

---

## 2.1 `ISch_Document`: a schematic sheet (and `ISch_Lib`)

The document returned by `SchServer.GetCurrentSchDocument`. It owns the placed
objects on a sheet and the iterator factory over them.

```pascal
// Walk every component on the active sheet.
Var Iter : ISch_Iterator; Comp : ISch_Component;
Begin
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        While Comp <> Nil Do
        Begin
            // ... use Comp ...
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
End;
```

### Objects and iteration
**`SchIterator_Create : ISch_Iterator`**: creates an iterator over the sheet's
placed objects. Filter it (2.6), walk it, and destroy it in `Finally`.
**`SchIterator_Destroy(Iter : ISch_Iterator)`**: frees an iterator from
`SchIterator_Create`. Always pair the two; a leaked iterator holds the document.
**`AddSchObject(Obj : ISch_GraphicalObject)`**: adds a factory-created object to
the sheet (a wire, label, power port, placed component).
**`RemoveSchObject(Obj : ISch_GraphicalObject)`**: removes an object from the
sheet (the inverse of `AddSchObject`).
**`RegisterSchObjectInContainer(Obj)`**: registers a freshly added child with the
document so it commits and renders; used alongside the `RobotManager` broadcast.
**`GraphicallyInvalidate`**: forces the editor to repaint. Without it, an added
or modified object can exist in memory but not draw until the sheet is reopened.
**`ClearSelection`**: clears the current selection on the sheet.

### Sheet properties
**`DocumentName : String`**: the document's file name / identifier.
**`SheetStyle : TSheetStyle`**: the standard sheet size (e.g. A4, A). When the
sheet is a custom size, read `CustomX` / `CustomY` instead.
**`SheetSizeX` / `SheetSizeY : TCoord`**: the sheet dimensions.
**`CustomX` / `CustomY : TCoord`**: the custom sheet width/height (used when
`SheetStyle` is the custom style).
**`UnitSystem : TUnitSystem`**: the measurement system, `eImperial` or
`eMetric`. There is no `UseMetricUnit` property; set it with `SetState_Unit`.
**`SetState_Unit(Unit)`**: sets the document's measurement unit.
**`VisibleGridSize` / `SnapGridSize : TCoord`**: the visible grid spacing and the
snap-grid spacing.
**`TitleBlockOn : Boolean`**: whether the title block is shown.
**`WorkspaceOrientation`**: the sheet orientation flag.
**`ObjectId : TObjectId`**: `eSchLib` for a library. Use it to tell which kind
`GetCurrentSchDocument` returned, testing FOR `eSchLib` and treating anything
else as a sheet.

There is no `eSchDoc` constant. Writing `ObjectId = eSchDoc` is an undeclared
identifier, which faults at runtime where `Try/Except` cannot catch it and
stops the polling loop. `scripts/altium/lint.py` rejects it by name
(`KNOWN_WRONG_E_IDENTS`), along with `eSchDocument` and `ePcbDoc`. For a board,
there is no ObjectId constant at all: check `PCBServer.GetCurrentPCBBoard <>
Nil` instead.
**`DM_Components`**: the document-model component list (the compiled side,
[page 4](04-workspace-project-documents.md)).

### 2.1.1 `ISch_Lib`: a schematic library (`.SchLib`)

When `ObjectId = eSchLib`, the document is a library and exposes symbol-level
members. A library does **not** enumerate symbols with `SchIterator` (that walks
placed objects): use `SchServer.CreateLibCompInfoReader` ([1](01-servers.md)) to
list it.

**`GetState_SchComponentByLibRef(LibRef : String) : ISch_Component`**: fetches a
symbol by its library reference. It is a **read-only fetch**: it does *not* make
that symbol the editor's current component, so a following edit still targets
whatever was current. Set `CurrentSchComponent` to actually switch.
**`CurrentSchComponent : ISch_Component`**: the editor's active symbol; pin and
graphic adds target this one. Assign to it to switch symbols.
**`AddSchComponent(Comp : ISch_Component)`**: adds a new symbol to the library.
On the 2nd and later add in one session it overwrites `Comp.LibReference` with an
auto-generated `Component_<N>`; re-assign `LibReference` after the call so the
name you chose survives to disk.
**`RemoveSchComponent(Comp : ISch_Component)`**: deletes a symbol from the
library.
**`GraphicallyInvalidate`**: repaints the library editor after a symbol change.

---

## 2.2 `ISch_Component`: a placed part / library symbol

A component is both a placed part on a sheet and a symbol in a library. It holds
its own pins, parameters, and graphics, walked with its own `SchIterator_Create`.

```pascal
// Create a library symbol: set the part scaffold BEFORE adding primitives.
Comp := SchServer.SchObjectFactory(eSchComponent, eCreate_Default);
Comp.CurrentPartID := 1;            // must precede pin/graphic adds
Comp.DisplayMode   := 0;
Comp.LibReference  := 'NE555';
Comp.Designator.Text := 'U?';
SchLib.AddSchComponent(Comp);
Comp.LibReference := 'NE555';       // re-assert after AddSchComponent (2.1.1)
```

### Identity
**`LibReference : String`**: the library symbol name (the lib-ref used to place
and resolve the part).
**`Designator : ISch_Designator`**: the reference-designator sub-object; its text
is `Designator.Text` (e.g. `'U?'`). **`NameOn : Boolean`** toggles its visibility.
**`Name : String`**: the component name.
**`Comment : ISch_Parameter`**: the comment sub-object (`Comment.Text`).
**`CommentOn : Boolean`** toggles its visibility.
**`ComponentDescription : String`**: the human-readable description.

### Multi-part scaffold
**`CurrentPartID : Integer`**: the active part of a multi-part symbol; **set to 1
before adding any primitive** so the primitive's owner-part binding resolves. A
primitive added while this is 0 reports success but lands on an invisible bucket.
**`DisplayMode : Integer`**: the display mode (0 = normal); set alongside
`CurrentPartID`.
**`PartCount : Integer`**: the number of sub-parts (quad op-amp = 4); set
**before** adding pins so each pin can address a real sub-part.

### Children
**`SchIterator_Create : ISch_Iterator`** / **`SchIterator_Destroy(Iter)`**:
iterate the component's own pins / parameters / graphics (filter to `ePin`,
`eParameter`, …).
**`AddSchObject(Obj : ISch_GraphicalObject)`**: adds a pin or graphic primitive to
the symbol.
**`I_ObjectAddress`**: the object handle, passed to
`SchServer.RobotManager.SendMessage` to register the new component or bracket a
modify.

---

## 2.3 `ISch_Pin`

A pin carries its geometry, electrical type, and (on the compiled side) its net.

```pascal
Pin := SchServer.SchObjectFactory(ePin, eCreate_Default);
Pin.Designator := '3';
Pin.Name       := 'OUT';
Pin.Location.X := MilsToCoord(500);     // field-writable for pins
Pin.Location.Y := MilsToCoord(0);
Pin.PinLength  := MilsToCoord(200);
Pin.Orientation := 0;                   // degrees Div 90
Pin.Electrical := eElectricOutput;
Comp.AddSchObject(Pin);
```

### Geometry & display
**`Location : TLocation`**, for a *placed* pin this is the **electrical end**
(the point a wire connects to), not the body root. It is field-writable here
(`Pin.Location.X := …` works), unlike the rectangle/line copy trap (2.7).
**`PinLength : TCoord`**: the length of the pin stub.
**`Orientation : Integer`**: the ordinal `degrees Div 90` (0/1/2/3), not the
degree value (a pin pointing left is 2).
**`IsHidden : Boolean`**: whether the pin is hidden.
**`ShowName : Boolean`** / **`ShowDesignator : Boolean`**: visibility of the pin
name and number.
**`OwnerPartId` / `OwnerPartDisplayMode : Integer`**: the part / display-mode the
pin belongs to (the binding set by the 2.2 scaffold).

### Identity & electrical
**`Designator : String`**: the pin number (a plain string, unlike the
component's label sub-object).
**`Name : String`**: the pin name.
**`Electrical : TPinElectrical`**: the electrical type: `eElectricInput`,
`eElectricOutput`, `eElectricPassive`, `eElectricPower`,
`eElectricOpenCollector`, `eElectricOpenEmitter`, `eElectricHiZ`, or
`eElectricIO` for bidirectional.
**`SetState_FunctionsFromName`**: derives the pin's functions from its name.

### Document-model (compiled netlist: [page 4](04-workspace-project-documents.md))
**`DM_PinNumber` / `DM_PinName : String`**: the model view of the pin number and
name.
**`DM_FlattenedNetName : String`**: the net this pin connects to in the
flattened design: the canonical connectivity read (requires a compiled project).
**`DM_FlattenedNet`**: the flattened-net object.
**`DM_Part`**: the model component (`IComponent`) the pin belongs to.

---

## 2.4 `ISch_Parameter`

A name/value pair attached to a component or document (value, footprint hint,
custom field).

**`Name : String`**: the parameter name.
**`Text : String`**: its value text.
**`IsHidden : Boolean`**: whether it is shown on the sheet.
**`DM_Name` / `DM_Value : String`**: the document-model view of the same
parameter (compiled side).

**`NameIsReadOnly` / `ValueIsReadOnly : Boolean`** and **`ReadOnlyState`** (0
none, 1 name, 2 value, 3 both): what Altium keeps for itself. The Properties
panel will not edit a name or value marked read-only, and neither does the
generic layer: `obj_modify` lists a `Name` or `Text` write on such a parameter
under `failed` and leaves it alone. The display (font, colour, justification,
`IsHidden`, `Autoposition`, `Location`) stays writable.

**A port's `CrossRef`.** Every port carries one child parameter named
`CrossRef`, read-only in both name and value: the sheet numbers the port's net
reaches, written by the compiler when cross references are on. A query of
`eParameter` returns it like any other parameter (`Name=CrossRef` in the
filter), with its font, colour, justification, `IsHidden` and `Autoposition`.
Nothing on it names its port: with `Autoposition` on it sits 20 mil past the
port's free end (the end with no wire, or the left / bottom end when neither
or both ends are wired), which is how to pair the two.

---

## 2.5 `ISch_Iterator`

Created from a document (sheet objects) or a component (its children); always
destroyed by the owner's `SchIterator_Destroy` in a `Finally`.

**`AddFilter_ObjectSet(ObjectSet)`**: restricts the walk to object kinds, e.g.
`AddFilter_ObjectSet(MkSet(ePin, eParameter))`.
**`AddFilter_Method(Method)`**: sets the traversal method.
**`AddFilter_Area(X1, Y1, X2, Y2)`**: restricts to objects within a rectangle.
**`SetState_FilterAll`**: clears filters (iterate everything).
**`FirstSchObject : ISch_GraphicalObject`**: the first matching object, or `Nil`.
**`NextSchObject : ISch_GraphicalObject`**: the next match, or `Nil` at the end.

> **Deleting during iteration** invalidates a live iterator. To delete, collect
> targets into a `TInterfaceList` during one pass, destroy the iterator, then
> remove them: or re-create the iterator, remove one match, and loop with a
> max-iterations guard.

---

## 2.6 Graphical primitives

Created with `SchServer.SchObjectFactory(eXxx, eCreate_Default)`, sized in
internal units, and added with `AddSchObject`. Schematic line widths are the
small/medium/large enum (`eSmall`/`eMedium`/`eLarge`, `0..3`), not a coordinate.

> **The `TLocation` record-copy trap:** `Location` and `Corner` return a **copy**.
> `Rect.Location.X := v` mutates the copy and is silently discarded: the object
> keeps its factory default and may never register. Read-modify-write:
> `Loc := Rect.Location; Loc.X := v; Rect.Location := Loc;`. (`ISch_Pin.Location`
> is the exception: field-writable, 2.3.)

```pascal
// A body rectangle, read-modify-write on the record properties.
Rect := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
Loc := Rect.Location;  Loc.X := MilsToCoord(-100);  Loc.Y := MilsToCoord(-100);  Rect.Location := Loc;
Loc := Rect.Corner;    Loc.X := MilsToCoord(100);   Loc.Y := MilsToCoord(100);   Rect.Corner := Loc;
Rect.IsSolid := False;
Comp.AddSchObject(Rect);
```

### `ISch_Rectangle`
**`Location` / `Corner : TLocation`**: opposite corners (copy-trap).
**`Left` / `Right` / `Top` / `Bottom : TCoord`**: the edge coordinates.
**`IsSolid : Boolean`**: filled vs outline.
**`LineWidth : TSize`**: the border width (`0..3`).
**`Color` / `AreaColor : Integer`**: border and fill colour.

### `ISch_Line`
**`Location` / `Corner : TLocation`**: the two endpoints (copy-trap).
**`LineWidth : TSize`**: `0..3`. **`Color : Integer`**.

### `ISch_Arc`
**`Location : TLocation`**: the arc centre. **`Radius : TCoord`**.
**`StartAngle` / `EndAngle : Double`**: degrees. **`LineWidth : TSize`**.

### `ISch_Wire` (and bus / polyline)
Built vertex by vertex: setting `Location` plus a single vertex yields an
invisible zero-vertex object.
**`InsertVertex : Integer`**: assign the 1-based index to insert a vertex slot.
**`SetState_Vertex(I, Point : TLocation)`**: set vertex `I`'s position. Call both
for each vertex.
**`GetState_VerticesCount : Integer`**: the vertex count.
**`GetState_Vertex(I) : TLocation`**: read vertex `I`.
**`LineWidth : TSize`**, **`Location : TLocation`**, **`Color : Integer`**.

```pascal
Wire := SchServer.SchObjectFactory(eWire, eCreate_Default);
Wire.InsertVertex := 1;  Wire.SetState_Vertex(1, P1);
Wire.InsertVertex := 2;  Wire.SetState_Vertex(2, P2);
SchDoc.AddSchObject(Wire);
```

### `ISch_NetLabel`
**`Text : String`**: the net name. **`Location : TLocation`**.
**`Orientation : TRotationBy90`**. **`Color : Integer`**.

### `ISch_PowerObject`
A power port / ground symbol.
**`Text : String`**: the net name. **`Style : TPowerObjectStyle`**: the glyph
(`ePowerBar`, `ePowerArrow`, `ePowerWave`, `ePowerCircle`, `ePowerGndPower`,
`ePowerGndSignal`, `ePowerGndEarth`). **`Location : TLocation`** /
**`GetState_Location : TLocation`**: its position. **`Orientation : TRotationBy90`**.
**`ShowNetName : Boolean`**: whether the net name is drawn.

### `ISch_Port`
A port: the net leaves the sheet here, up to the sheet symbol that uses the
sheet (or across to another sheet in a flat design). Measured on AD 25.8.1.

**`Name : String`**: the port's name (a port has no `Text`).
**`Location : TLocation`**: always the LEFT end of a horizontal port and the
BOTTOM end of a vertical one, with the other coordinate on the port's centre
line, whichever way it was turned.
**`Style`**: only the axis, **0 horizontal, 4 vertical**. A port has no
`Orientation`; a turn of 180 degrees is not stored (the port reads the same,
only `Alignment` swaps 1 and 2). The arrow shapes on screen are not in `Style`:
Altium draws them from `IOType` and `ConnectedEnd`. Values 1-3 and 5-7 read
back but come back as 0 / 4 after a save and reopen, so the generic layer
refuses them. Writing 4 turns a horizontal port a quarter turn
counterclockwise about `Location`; 0 turns it back.
**`IOType`**: 0 unspecified, 1 output, 2 input, 3 bidirectional.
**`Width`**, **`Height`** (mils). With **`AutoSize`** on, Altium widens a port
to its name (text width + 197.75 mil at height 100, Times New Roman 10) but
never narrows it; the wired end stays put and the port grows away from it.
**`Alignment`**: where the name sits in a port wider than it, in sheet terms:
horizontal 0 centre, 1 left, 2 right; vertical 1 top, 2 bottom.
**`ConnectedEnd`** (read only, worked out by Altium from the wiring): 0 none,
1 the `Location` end, 2 the far end, 3 both.
**`ShowNetName`**, **`Color`** (border), **`AreaColor`** (fill),
**`TextColor`**, **`FontId`**: written and kept after a save and reopen.
**`OverrideDisplayString`** and **`HarnessColor`** read back after a write but
were gone after a reopen on a port with no harness.
Its child parameter **`CrossRef`** is described under 2.4.

The port's two net names are on the compiled side (`IPort`, an `INetItem`,
[page 4](04-workspace-project-documents.md)): **`DM_NetName`** is the net's
name on the port's own sheet, **`DM_FlattenedNetName`** the name of the net
it ends up in across the whole project.

### `ISch_Label`
A free text label.
**`Text : String`**. **`Location : TLocation`**. **`Orientation : TRotationBy90`**.
**`Justification`**: text anchor. **`FontId`**: the font (via `FontManager`).
**`IsHidden : Boolean`**. **`Color : Integer`**.

### `ISch_SheetSymbol` / `ISch_HarnessConnector`
`ISch_RectangularGroup` descendants. They have no `Corner`; size them from the
bottom-left `Location` plus **`XSize` / `YSize : TCoord`** (`SetState_XSize` /
`SetState_YSize`). Type the local as the derived interface, not `ISch_GraphicalObject`.
```pascal
Sym.Location := Point(MilsToCoord(X), MilsToCoord(Y));
Sym.XSize := MilsToCoord(W);
Sym.YSize := MilsToCoord(H);
```
A sheet symbol's **`SheetFileName`** (link to the child `.SchDoc`) and **`SheetName`**
(display label) are `ISch_ComplexText` sub-objects, written via `SetState_Text` after
the symbol is registered in its container:
```pascal
SchDoc.RegisterSchObjectInContainer(Sym);
FN := Sym.GetState_SchSheetFileName;   FN.SetState_Text(FileNameStr);   { ISch_SheetFileName }
NM := Sym.GetState_SchSheetName;       NM.SetState_Text(NameStr);       { ISch_SheetName }
```

### Selection
SCH objects carry **`Selection : Boolean`**; the PCB equivalent is **`Selected : Boolean`**.
`ISch_Document` has no document-level clear: deselect the active sheet with
`ResetParameters; RunProcess('Sch:DeSelectAll')`.

---

## 2.7 `ISch_Implementation`: model / footprint links

A component's footprint and simulation models are `eImplementation` children.

```pascal
Impl := SchServer.SchObjectFactory(eImplementation, eCreate_Default);
Impl.ModelType := 'PCBLIB';
Impl.ModelName := 'SOIC-8';
Comp.AddSchObject(Impl);
```

**`ModelName : String`**: the model name (e.g. the footprint `'SOIC-8'`).
**`ModelType : String`**: the model kind, e.g. `'PCBLIB'` for a footprint or
`'SIM'` for a SPICE model.
**`AddDataFileLink(...)`**: attaches a model datafile reference (the file that
backs the model).
**`UseComponentLibrary : Boolean`**: whether the model is resolved from the
component's own library.
**`LibraryIdentifier : String`**: the library the model is resolved from.
