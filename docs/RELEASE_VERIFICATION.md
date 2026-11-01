# Release verification: 2026.11.01.1

Unless a section records live verification explicitly, the Pascal below has
been checked by FPC and the linter but **not executed by Altium's DelphiScript engine**. The two are not the
same: each accepts identifiers the other rejects, and an undeclared one
faults at runtime where `Try/Except` cannot catch it, halting the
polling loop.

Work top to bottom. Step 1 needs nothing but Altium and takes seconds,
and is the step most likely to catch a compile-level problem. If it
fails, stop and fix that before running the rest.

If there is only time for some of it, the risk is not evenly spread.
What separates the steps is whether the Altium property being written
is already written somewhere in shipped code, because a property that
works elsewhere cannot be an undeclared identifier:

| Step | Property | Written elsewhere? | Risk |
|---|---|---|---|
| 5, 3D placement | `StandoffHeight` | only by step 15, itself unverified | highest |
| 5, 3D placement | `Rotation` on a body | other object types only | high |
| 2, pin edges | `Symbol_OuterEdge`, `Symbol_InnerEdge` | only by `Library.pas` and, since step 44, `Generic.pas` | high, and can stop the loop; the inner edge has since run live through obj_modify |
| 7, enum words | `StrToPinElectrical` | yes, `Lib_AddPins` | medium |
| 4, DNP paste | `PasteMaskExpansion` | yes, `PCB_MakePasteGrid` | low, but it edits the board |
| 3, filled body | `AreaColor`, `IsSolid` | yes, `Generic.pas` | low |
| 5, 3D placement | `MoveByXY` | yes, `PCB_ReplicateLayout` | low |
| 11, copy and rename | `LibReference` | yes, `Lib_CreateSymbol` | low |
| 12, delete variant | `DM_RemoveProjectVariant` | no, nowhere | medium, ran live; it still destroys the entries |
| 13, sheet symbol filename | `Text` on a sheet symbol's sub-object | yes, on other objects | medium |
| 14, polygon pour options | `RemoveDead`, `RemoveNarrowNecks`, `RemoveIslandsByArea` | new here, and in two places at once | high, and it can stop the loop |
| 15, 3D body on a board | `StandoffHeight` on a free body | only by step 5, itself unverified | highest, shared with step 5 |
| 16, region kind | `Kind` on a region | read by four reference scripts, written by none | high, and it can stop the loop |
| 17, component UniqueId | `UniqueId` on a placed component | no, nowhere | highest, and a wrong id costs the next Update PCB |
| 18, pin owner part | `OwnerPartId` | yes, by two independent reference scripts | medium, behavioural not declarative |
| 6, mirrored text | `MirrorFlag` | yes, `PCB.pas` | lowest |
| 20, bulk vias | `Size`, `HoleSize`, `LowLayer`, `HighLayer` on a via | yes, `PCB_PlaceVia` | low |
| 20, track width | `Width` on a track, now in fractions of a mil | yes, `PCB_PlaceTrack` | low |
| 22, text size | `Size`, `Width` on a board text | yes, `Lib_AddFootprintText` | low |
| 22, silkscreen restore | `MoveToXY` on a designator | yes, `Generic.pas`, on components | low |
| 22, silkscreen anchors | `ChangeNameAutoposition` | yes, `PCB_AutoplaceSilkscreen` before this change | low |
| 23, via template | `CopyTo` on a via's template link | yes, `Library.pas`, on footprints only | highest, and it can stop the loop |
| 23, via mode | `Mode` on a via | yes, on pads, `PCB.pas` | medium |
| 23, via size | `Size`, `HoleSize` on a via | yes, `PCB_PlaceVia` | low |
| 23, Routing Via rule | `PreferedWidth`, `PreferedHoleWidth`, `MinHoleWidth`, `MaxHoleWidth` | no, nowhere | high, and it can stop the loop |
| 25, DbLib connection | `ConnectionString`, `LoginPrompt`, `Connected` on an ADO connection | no, nowhere | highest, and it can stop the loop |
| 25, DbLib query | `Connection` on an ADO query | no, nowhere | highest, and it can stop the loop |
| 25, DbLib record key | `ParamByName`, `Value` on an ADO parameter | no, nowhere | high, and it can stop the loop |
| 25, DbLib placement | `LoadComponentFromDatabaseLibrary` | no, nowhere | highest, and it edits the sheet |
| 25, DbLib placement | `AddSchObject`, `SetState_Orientation` | yes, `Gen_PlaceSchComponentFromLibrary` | low |
| 26, repour | `PolygonRepour` on the system options | no, nowhere | high, and it can stop the loop |
| 26, ECO direction | `AddStringParameter` with `Action=UpdateMe` | yes, `Proj_UpdatePCB` | high, and executing it edits the board |
| 26, exact vias | `Size`, `HoleSize` on a via, in fractions of a mil | yes, `PCB_PlaceVia` | low |
| 26, replicated part | `RegisterSchObjectInContainer` | yes, `Generic.pas` | medium |
| 26, footprint target | `CurrentComponent` | yes, `Lib_SetDesignator` | low |
| 26, copied footprint | `MoveByXY` on a footprint's primitives | yes, `Library.pas`, on a 3D body | medium |
| 27, lengths in mm | `TopXSize`, `TopYSize` on a pad, now from `CoordFromUnits` | yes, `Lib_AddFootprintPads` | low |
| 27, new document | `ShowDocument` on the new document | yes, `App_SetActiveDocument` | low |
| 28, simulation run | `AddStringParameter` with `ObjectKind=Simulator` | yes, `Proj_UpdatePCB` | medium, a process run rather than a write |
| 28, results saved | `SetFileName`, `DoFileSave` on the results document | yes, `App_CreateDocument` | low |
| 28, settings reload | `SupportsReload`, `DoFileLoad`, `UpdateModifiedDate` | no, nowhere | highest, and it can stop the loop |
| 29, slotted hole | `HoleType`, `HoleWidth` on a library pad | only by `PCBGeneric.pas`, on a board pad, itself new | high, and it can stop the loop |
| 29, pad mask expansion | `SolderMaskExpansion`, `SolderMaskExpansionValid` on a pad cache | yes, `PCB_SetViaSoldermaskRelief`, on a via's cache | medium, a library pad is new |
| 29, non-plated hole | `Plated` on a library pad | read in `PCB.pas`, written nowhere else | medium |
| 29, pad paste expansion | `PasteMaskExpansion`, `PasteMaskExpansionValid`, `SetState_Cache` | yes, `PCB_MakePasteGrid` | low, but now on a library pad |
| 29, paste windows | `SetOutlineContour`, `Replicate` on a region contour | yes, `PCB_PlaceRegion` | medium, a region in a footprint is new |
| 29, footprint height | `Height` on a footprint | yes, `Lib_SetFootprintHeight` | low |
| 31, board select | `Selected` on a board primitive, now read back | yes, `SetPCBProperty` | low |
| 31, sheet select | `Selection` on a schematic object, now read back | yes, `SetSchProperty` | low, the read is the one `Gen_ClearHighlights` makes |
| 32, DRC report stamp | `FileAge` on the board's .DRC report | yes, `Proj_ExportSTEP` | low |
| 32, managed loader | `LoadComponent` | yes, `Lib_GetVaultComponent`, also new; the call was recorded working on AD 26.9.1, before this rewrite | high, the whole managed placement depends on it |
| 32, managed placement | `AddSchObject` with a loaded managed component | yes, `PutLoadedComponent`, which file-library placement now shares | low |
| 32, dry run | `DestroySchObject` on a loaded component no sheet owns | yes, `Gen_CreateObject`, on objects never added | medium |
| 32, Workspace name | `LibraryIdentifier`, `LibIdentifierKind`, `VaultGUID` read off placed parts | yes, `VaultNameOnSheet`, also new | low |
| 32, managed lookups | `FindComponentLibraryPath`, `FindComponentDisplayPath`, `GetLifeCycleState`, `GetOrderedRevisionList`, `GetComponentPlacementParameters` | no, nowhere | low, each in its own Try and reported in `diag` when it raises |
| 33, plane net | `InternalPlaneNetName` on the board, written and read | no, nowhere | medium, the old layer-object route still runs when it does not read back |
| 33, net-class members | `IsMember` on a net class | yes, `PCB_AddTestpointsForNetClass` | low, but one call per class and net |
| 33, DbLib part | `CurrentPartID` on a placed DbLib part | yes, `SetSchProperty` | low |
| 33, pad designator | `Name` on a pad, through the generic setter | yes, `PCB_RenumberPads` | low |
| 33, poll timer | `Enabled` on the poll timer, read back after arming | yes, `StartMCPPump` writes it | low |
| 34, 3D body opacity | `BodyOpacity3D` on a component body | yes, `AddExtrudedBody`, on a new extruded body | high, a wrong member name stops the loop; used by several scripts in reference/ |
| 34, slot rotation | `HoleRotation` written on a library pad | only by `PCBGeneric.pas`, on a board pad, itself new | medium, read on the board side already |
| 34, pad copper centre | `BoundingRectangle` on a library pad | yes, `BuildViolationJson`, on any primitive | low |
| 34, pad setter | `SetState_Cache` on a library pad | yes, `Lib_AddFootprintPads` | low |
| 34, arc mirror | `StartAngle`, `EndAngle` written on a placed arc | yes, `Lib_AddSymbolArc` | low |
| 35, footprint switch | `SetState_CurrentComponent` on the library, away from the target and back | yes, `Lib_Link3DModel` | low |
| 35, freeing a removed primitive | `DestroyPCBObject` on a primitive just removed, inside the open iterator | no, nowhere | medium, a freed primitive used again takes the loop down; ran 25 cycles live without a fault |
| 35, temporary holder | `GetUniqueCompName`, `DestroyPCBLibComp` on a footprint made for the delete | no, nowhere | low, ran live in a single-footprint library |
| 36, body layer | `Layer` written on a library 3D body | yes, `PCB_Place3DBody` | low |
| 36, mechanical check | `IsMechanicalLayer` on LayerUtils | no, nowhere | high, a wrong member stops the loop; ran live, and used by scripts in reference/ |
| 36, assembly text | `UnderlyingString`, `Size`, `Width` on a new library text | yes, `Lib_AddFootprintText` | low |
| 36, settle after an add | `SetState_CurrentComponent` on the footprint already current | yes, `Lib_Link3DModel` | low, ran after all eight add kinds and survived save and restart |
| 36, modify bracket | `BeginModify`, `EndModify` round an obj_modify write and a placed body's move | yes, `PCB_SetTrackWidth` | low |
| 36, mask sides | `UseSeparateExpansions`, `SolderMaskBottomExpansion` on the pad cache | yes, `Lib_SetPadProperties` | medium, missing from the API reference; read and written live on pads and vias |
| 36, via hole edge | `SolderMaskExpansionFromHoleEdge` written on a via | only by `PCBGeneric.pas`, through the pad cast, itself new | high, the expansion assigned straight onto a via faults (measured again); this member and the cache route did not |
| 36, mechanical enabled | `MechanicalLayerEnabled` read on a library layer | yes, `Lib_GetMechLayers` | low |
| 36, rule gap | `Gap` read on a typed clearance rule | yes, `PCB_SetRuleProperties` | low |
| 36, designator mirror | `Location` written on a placed designator | yes, `SetSchProperty` | low |
| 32, footprint on a placed part | `ModelName` on a footprint implementation | yes, `Lib_SetModelName` | medium, now on a placed part rather than a library symbol |
| 32, footprint link name | `EntityName` on a model datafile link | yes, `Lib_NormalizeImplementations` | low |
| 37, via tenting | `SetState_IsTenting_Top`, `SetState_IsTenting_Bottom` on a footprint via | yes, `PCB_SetViaTenting` | low, written and read back live both ways; dropped silently unless the mask is manual |
| 37, paste flag | `SetState_IsTopPasteEnabled` on a library pad | no, nowhere | low, written and read back live both ways; dropped silently unless the paste is manual |
| 37, manual first | `SolderMaskExpansionValid`, `PasteMaskExpansionValid` set before the flag | yes, `PCB_SetViaSoldermaskRelief` | low |
| 37, via sizes | `SizeOnLayer` written on a via, `HoleSize` | yes, `PCB.pas` | medium, the size write is new on a via; read back live in mm |
| 37, grid switches | `SnapGridOn`, `VisibleGridOn` on a schematic | no, nowhere | low, switched off and on live and read back |
| 37, pin labels | `ShowName`, `ShowDesignator` on a pin | yes, `Library.pas` | low |
| 37, pin text | `Name_FontMode`, `Name_CustomFontID`, `Name_CustomColor`, `Designator_FontMode`, `Designator_CustomFontID`, `Designator_CustomColor` on a pin | no, nowhere | medium, read live; the writes are not run live |
| 39, polygon pour settings | `Grid`, `TrackSize`, `MinTrack`, `ArcApproximation`, `NeckWidthThreshold`, `IslandAreaThreshold`, `PolyHatchStyle`, `PourOver`, `UseOctagons` on a polygon | only by `PCBGeneric.pas`, itself new | low, read and written back live on AD 26.11 |
| 39, keepout restrictions | `IsKeepoutForCopper`, `IsKeepoutForTrack`, `IsKeepoutForVia`, `IsKeepoutForSMDPad`, `IsKeepoutForTHPad` | only by `PCBGeneric.pas`, itself new | low, written and read back live on keep-out tracks |
| 39, primitive net | `AddPCBObject` on a net | yes, `BindPrimitiveToNet` | low |
| 39, region arcs | `ShapeSegmentCount`, `UpdateContourFromShape` on a board region | yes, `Lib_AddFootprintRegions` | low |
| 39, entry font | `TextFontID` on a sheet entry | no, nowhere | low, read and written back live on AD 26.11 |
| 39, display mode | `DisplayMode` on a placed part | yes, `Library.pas`, on a library part | low, refused out of range and written live |
| 39, pin map | `MapAsString` on a footprint model | no, nowhere | low, written, read back and restored live |
| 39, footprint choice | `IsCurrent` on a placed part's footprint model | yes, `Library.pas` | medium, not yet run on a part with several models |
| 38, stack-up apply | `CopperThickness`, `DielectricHeight`, `DielectricConstant`, `DielectricType` on a board layer | yes, `PCB_ModifyLayer` | low, the same writes pcb_modify_layer makes, each read back |
| 40, body standoff | `StandoffHeight` written on a library 3D body already in place, not a STEP one | yes, `Lib_Link3DModel` and `AddExtrudedBody`, on a body just created | low, run live on an extruded body |
| 40, model Z offset | `SetState` (RotX, RotY, RotZ, Dz) on a STEP body's model | no, nowhere | low, signature measured live from the saved record; the rotations passed are the saved file's |
| 40, model reassigned | `Model` assigned back to a STEP body after SetState | yes, `Lib_Link3DModel`, on a body just created | low, it is what makes the Z offset save, measured live |
| 40, body height | `OverallHeight` written on a library 3D body | yes, `SetPCBProperty`, on a board body | low |
| 40, body opacity | `BodyOpacity3D` written on a library 3D body | yes, `SetPCBProperty`, on a board body | low |
| 40, body layer | `Layer` written on a library 3D body | yes, `Lib_MoveBodiesToLayer` | low |
| 41, symbol parts | `SetState_CurrentSchComponentAddPart`, `SetState_CurrentSchComponentRemovePart` on the library | no, nowhere | medium, each step read back; ran live both ways |
| 41, display modes | `SetState_CurrentSchComponentAddDisplayMode`, `SetState_CurrentSchComponentRemoveDisplayMode` on the library | no, nowhere | medium, each step read back; ran live both ways |
| 41, part to remove | `CurrentPartID`, `DisplayMode` written on a library symbol | yes, `Lib_CreateSymbol` | low |
| 41, model to STEP | `ExportToStep` on a 3D model | no, nowhere | medium, writes a file only; ran live |
| 41, board STEP | `Board3DModelExporter`, `ExportBoard3DModel` | no, nowhere | medium, writes a file only; ran live, and the dialog route stays behind it |
| 41, impedance profiles | `GetImpedanceProfileCount`, `GetImpedanceProfileByIndex`, `GetTraceImpedanceForLayer`, `CalculatedImpedance` | no, nowhere | low since step 50: per-profile reads ran on a board with profiles; the layer is asked for by ID |
| 42, variant API | `DM_AddProjectVariant`, `DM_SetName`, `DM_SetDescription` on a project and its new variant | no, nowhere | medium, ran live; the process route stays behind it |
| 42, variant entries | `DM_AddComponentVariation`, `DM_RemoveComponentVariation`, `DM_SetVariationKind`, `DM_SetAlternatePart` | no, nowhere | medium, ran live and read back |
| 42, net colours | `DM_SetNetColorByName` on a project | no, nowhere | low, ran live, set and cleared |
| 42, rule answers | `FindDominantRuleForObject`, `FindDominantRuleForObjectPair`, `Scope1Includes`, `CheckUnaryScope` | no, nowhere | low, reads only; ran live |
| 42, net clean-up | `CleanNet` on the board | no, nowhere | medium, edits routing; ran live |
| 42, drill pairs | `IsBackdrill`, `PlotDrillGuide`, `PlotDrillDrawing` on a drill pair | no, nowhere | low, ran live and restored |
| 42, part 3D | `SaveModelToFile` on a placed part | no, nowhere | low, writes a file only; ran live |
| 42, fast lookup | `GetPcbComponentByRefDes` on the board | no, nowhere | medium, every designator lookup runs it first; ran live, with the walk behind it |
| 42, generic board properties | `HolePositiveTolerance`, `MitreCorners`, `BorderWidth`, `EnablePinSwapping`, `BarCodeShowText` | no, nowhere | low, ran live and read back |
| 42, multiline text | `Multiline` on a board text | yes, `PCB_PlaceText` (a justified text is framed) | low, ran live and read back |
| 42, pin swapping | `SetState_SwapIdPart`, `SetState_SwapIdPin` on a symbol pin | no, nowhere | low, ran live and read back |
| 42, symbol aliases | `Alias_Add`, `Alias_Remove` on a symbol | no, nowhere | low, ran live and read back |
| 42, sheet options | `SetState_ReferenceZonesOn`, `SetState_CustomXZones`, `SetState_ShowTemplateGraphics` on a sheet | no, nowhere | low, ran live and restored |
| 43, xSignals | `CreateFromPins`, `AssignPinPairClass` on the board's pin pair manager | no, nowhere | medium, ran live; the new xSignal is read back by name |
| 43, text anchors | `ChangeNameAutoposition`, `ChangeCommentAutoposition` on a placed part | yes, `PCB_AutoplaceSilkscreen` | low, ran live; hidden text keeps its place |
| 43, auto position | `AutoPosition_NameComment` on a placed part | no, nowhere | low, ran live |
| 43, part text properties | `LockStrings`, `FootprintDescription`, `DefaultPCB3DModel` on a placed part | only by `PCBGeneric.pas`, itself new | low, the first two written and read back live |
| 43, project setup | `DM_MoveSourceDocument`, `DM_AddSearchPath`, `DM_SetAllowPortNetNames`, `DM_SetAllowSheetEntryNetNames` on a project | no, nowhere | medium, ran live, read back, saved and reopened |
| 43, library check | `Sch_LibraryRuleChecker_Create`, `ShowReport`, `Duplicate_Pins`, `Missing_Footprint` on a library's rule checker | no, nowhere | low, writes a report file only; ran live |
| 44, drill pair | `AddLayerPair` on the board | no, nowhere | medium, ran live; a copy and a non-copper layer are refused first |
| 44, net display | `ShowNetConnects`, `HideNetConnects`, `LoopRemoval` on a net | no, nowhere | low, ran live, read back and restored |
| 44, one footprint | `SaveComponentWithLibrary` on a library | no, nowhere | low, writes a file only; ran live |
| 44, project library | `CreateLibraryFromProject` on a sheet | no, nowhere | low, writes a new library only; ran live |
| 44, scope and output | `DM_SetHierarchyMode`, `DM_SetOutputPath` on a project | no, nowhere | medium, ran live, read back and restored |
| 44, pin functions | `AddDefinedFunction`, `ClearDefinedFunctions` on a pin | no, nowhere | low, ran live and read back |
| 44, pin symbols | `Symbol_InnerEdge` written on a pin | yes, `Lib_AddPins` | low, ran live and read back |
| 44, more pin symbols | `Symbol_Inner`, `Symbol_Outer`, `Symbol_LineWidth`, `HiddenNetName` on a pin | no, nowhere | low, ran live and read back |
| 44, part display | `ShowHiddenPins`, `PinsMoveable`, `OverideColors`, `PinColor` on a symbol | no, nowhere | low, ran live, read back and restored |
| 44, pad ids | `JumperID`, `SwapID_Pad`, `SwapID_Part` on a pad | no, nowhere | low, ran live, read back and cleared |
| 45, board grids | `SnapGridSizeX`, `TrackGridSize`, `ViaGridSize`, `DrawDotGrid` on the board | no, nowhere | low, ran live, read back and restored |
| 45, part STEP | `SaveModelToFileAsPart` on a placed part | no, nowhere | low, writes a file only; ran live |
| 45, auto-save | `DM_SetRecoveryParameters` on the workspace | no, nowhere | medium, changes an Altium setting; ran live and restored |
| 45, net kinds | `DM_IsLocal`, `DM_ElectricalString`, `DM_SuppressERC`, bus members on a net | no, nowhere | low, reads only; ran live |
| 45, project options | `DM_SetAppendSheetNumberToLocalNets`, `DM_SetPinSwapBy_Netlabel`, `DM_SetPinSwapBy_Pin` | no, nowhere | low, ran live, read back and restored |
| 45, pour switches | `DrawDeadCopper`, `DrawRemovedNecks`, `DrawRemovedIslands`, `ExpandOutline` on a pour | no, nowhere | low, ran live and restored |
| 45, pour switches | `AutoGenerateName` on a pour | yes, `PCBSetPoly` clears it when a polygon Name is written (#150) | low, ran live and restored; `AutoGenerateName` renames the pour |
| 45, inverted text | `TTFInvertedTextJustify`, `TTFOffsetFromInvertedRect` on board text | no, nowhere | low, ran live, read back and restored |
| 45, inverted margin | `InvertedTTTextBorder` on board text | yes, `PCB_PlaceText` (the margin on create) | low, ran live, read back and restored |
| 45, text inversion | `Inverted`, `UseInvertedRectangle` on board text | yes, `Lib_ConvertDesignatorsToStroke` | low, ran live, read back and restored |
| 45, parameters and ports | `ReadOnlyState`, `ParamType`, synchronize switches; port `AutoSize`, `OverrideDisplayString`, `HarnessColor` | no, nowhere | low, ran live, read back and restored |
| 45, pin label positions | `Name_PositionMode`, `Name_CustomPosition_Margin`, `Designator_PositionMode`, `Designator_CustomPosition_Margin` and their rotations on a pin | no, nowhere | low, ran live, read back and cleared |
| 46, board view options | `ShowPadNets`, `ShowPadNumbers`, `ShowViaNets`, `SingleLayerMode`, `DisplaySpecialStrings`, `HighlightFull` on the PCB preferences | no, nowhere | medium, changes Altium's preferences; ran live and restored |
| 46, board edit options | `RotationStep`, `ProtectLockedPrimitives`, `OnlySelectVisible`, `DuplicateDesignatorsAllowed` on the PCB preferences | no, nowhere | medium, changes Altium's preferences; ran live and restored |
| 46, schematic options | `SnapToHotSpot`, `ComponentsCutWires`, `PinNameMargin`, `StringIncA`, `DefaultPowerGndName` on the schematic preferences | no, nowhere | medium, changes Altium's preferences; ran live and restored |
| 46, electrical grid | `HotspotGridSize`, `HotspotGridOn` on a sheet | no, nowhere | low, ran live and read back |
| 46, net colour and jumpers | `OverrideColorForDraw` on a net | no, nowhere | low, ran live and restored |
| 46, board part extras | `SourceDescription`, `ChannelOffset`, `GroupNum` on a part | no, nowhere | low, ran live and restored |
| 46, board pad extras | `SwappedPadName`, `OwnerPart_ID` on a pad | no, nowhere | low, ran live and cleared |
| 46, pin and part extras | `DefaultValue`, `FormalType` on a pin; `LibraryPath`, `AliasAsText` on a part | no, nowhere | low, ran live and restored |
| 47, routing options | `InteractiveRouteMode`, `PlowThroughPolygons`, `AutoVia`, `PlaceShoveDepth`, `UseSmartTrackEnds` on the PCB preferences | no, nowhere | medium, changes Altium's preferences; ran live and restored |
| 47, view and edit additions | `PlaneDrawMode`, `DraftTrackThreshold`, `DragMode`, `ComponentMoveKind` on the PCB preferences | no, nowhere | medium, changes Altium's preferences; ran live and restored |
| 47, board options | `PolygonNameTemplate` on the board | no, nowhere | low, ran live and restored |
| 47, schematic display options | `ExpandComponentDesignators`, `WatermarkReadOnlySheet`, `AlwaysDrag`, `DefaultTemplateFileName` on the schematic preferences | no, nowhere | medium, changes Altium's preferences; ran live and restored |
| 47, pair impedance | `ImpedanceDriven`, `MinImpedance`, `MaxImpedance`, `FavoredImpedance` on a differential pair rule | no, nowhere | low, ran live and read back |
| 47, 3D body extras | `Texture`, `TextureRotation` on a 3D body | no, nowhere | low, ran live and restored |
| 48, grow pour | `GrowPolyshape` on a pour | no, nowhere | medium, moves a pour outline; ran live and moved back |
| 48, rule settings | `MaxHeight`, `VerticalGap`, `MinSetBack`, `NetAntennaeTolerance`, `FanoutStyle`, `CheckConnectedCopper` on rules | no, nowhere | low, ran live, read back and restored |
| 48, wire tidy | `OptimizeUseOfPolylines` on a sheet | no, nowhere | medium, rewrites a sheet's wires; ran live, closed unsaved |
| 48, pre-routes | `IsPreRoute`, `UserRouted` on tracks, arcs and vias | no, nowhere | low, ran live and cleared |
| 48, paste openings | `PasteMaskExpansionMode`, `PasteMaskUsePercent`, `PasteMaskPercent`, `PasteMaskEnabled` on pads and vias | no, nowhere | low, ran live, board closed unsaved |
| 49, split lines | `CreateSplitLine`, `DeleteSplitLine`, `RebuildSplitBoardRegions` on the outline | yes, `RefreshBoardOutline` (rebuild) | high, rebuilds regions; refused on a board whose regions are not all from split lines; ran live |
| 49, region settings | `Locked3D` on a board region | no, nowhere | low, ran live and read back |
| 49, layer stacks | `CreateSubstack`, `RemoveSubstack`, `EnableLayer`, `DisableLayer`, `IsFlex` on the layer stack | no, nowhere | medium, changes the stack; ran live and restored |
| 49, bend lines | `CreateBendingLine`, `DeleteBendingLine`, `FoldIndex` on a region's bend lines | no, nowhere | low, ran live and restored |
| 50, dielectrics | `DielectricLossTangent`, `IsStiffener`, `DielectricHeight` on a dielectric or mask layer | no, nowhere | medium, changes the stack; ran live and restored |
| 50, stack style | `LayerStackStyle` on the master stack | no, nowhere | medium, ran live and restored |
| 50, impedance profiles | `CreateImpedanceProfile`, `RemoveImpedanceProfile`, `CreateTraceImpedance`, `TraceMinWidth`, `UseSolderMask` | no, nowhere | low, ran live on a copy |
| 50, layer order | `InsertInStackBelow`, `InsertInStackAbove` on the stack | no, nowhere | medium, ran live on a copy |
| 51, output setup | `GerberOptions`, `PrinterOptions` on the board | no, nowhere | low, saved in the board; ran live, read back and restored |
| 51, testpoint rules | `SetState_MinSize` and the other Testpoint Style setters | no, nowhere | low, ran live and restored |
| 51, preferences | `Paste_InSameNet`, `RepeatX`, `QuestionDelete`, `AutoPanSpeed`, `PadViaFontName`, `BoardAreaColor` in the board editor preferences; `StringIncRemoveLeadingZero`, `CutterFixedLength` in the schematic ones | no, nowhere | low, Altium-wide; ran live and restored |
| 51, grids and pads | `BigVisibleGridMultFactor`, `SnapGridUnit` on the board; `DrillType` on a pad; `BorderSpaceType` on board text | no, nowhere | low, ran live, read back and restored |
| 51, net colours | `Color` on a board net, taken from the project's net colour | yes, `Generic.pas` (schematic objects) | low, ran live and restored |
| 51, sheet zones | `ObjectReferenceZone` on a sheet | no, nowhere | none, read only |
| 52, unpoured pours | `GetDefaultName` on a polygon | no, nowhere | low, names an unnamed pour; ran live |
| 52, library delete | `GetComponentByName` on the library | yes, `Library.pas` (PcbLibTarget) | medium, removes primitives; ran live after a clean restart |
| 53, probes | `AddSchObject` of parameters on a probe | yes, on components and parameter sets | low, adds a probe's own parameters; ran live |
| 53, settings reload | `DoFileLoad` on a settings document reporting `SupportsReload` False | no, nowhere | low, a .simcfg only; ran live |
| 54, variant values | `DM_AddParameterVariation`, `DM_SetParameterName`, `DM_SetVariedValue`, `DM_RemoveParameterVariation` on a part's variant entry | no, nowhere | low, the .PrjPcb only; ran live, saved and reopened on a scratch copy |
| 54, variant names | `DM_SetName`, `DM_SetDescription` on an existing variant | yes, `Proj_CreateVariant` on a new one | low, ran live, saved and reopened |
| 54, current variant | `DM_SetCurrentProjectVariant` on the project | no, nowhere | low, ran live and set back to the base design |
| 54, alternate link | `DM_SetLibIdentifierKind`, `DM_SetLibraryIdentifier`, `DM_SetSourceLibraryName`, `DM_SetSymbolReference`, `DM_SetDesignItemID`, `DM_SetFootprint`, `DM_SetIsSameFootprint` on an alternate's library link | no, nowhere | low, ran live, saved and reopened |
| 54, variant parameters | `DM_AddParameter` on a variant | no, nowhere | low, copies a variant's own parameters; the scratch variants had none |
| 55, PCB library from a board | `SetFileName`, `DoFileSave` on the library Altium's Make PCB Library opens | yes, `PCB_SaveCopyAs` | low, writes a new .PcbLib only; ran live on a scratch copy |
| 55, integrated library from a project | `Focus` on a project document before Make Integrated Library | yes, `Application.pas` | low, writes <project>.IntLib only; ran live |
| 55, PCB library check | `PrimPrimDistance` between a footprint's copper, `CurrentComponent` set per footprint and put back | yes, `Audit.pas` | none, reads only; ran live with a short, a loose track, a doubled track, a bottom-only footprint and an offset origin |
| 55, pin timing | `PinPackageLength`, `PropagationDelay` on a symbol pin | no, nowhere | low, the symbol only; ran live, saved and reopened |
| 55, supplier links | `RemoveSchObject`, `AddSchObject` of numbered parameters on a symbol | yes, `Lib_BatchSetParams` | low, the symbol's link parameters only; ran live, saved and reopened |
| 55, pad/via templates | `AddTemplateByPrimitive`, `AddExternalTemplate` on a pad/via library | no, nowhere | low, the board's own template list and new .PvLib files; ran live and reopened |
| 55, pad/via install | `InstallLibrary` on the pad/via library manager | yes, `Lib_InstallLibrary` | low, installed and uninstalled live |
| 56, port width | `AutoSize` on a port, cleared before its width is set | no, nowhere | low, one port; ran live, 600 mil read back |
| 56, sheet fonts | `TextFontID` on new ports, power ports, text frames and sheet entries | no, nowhere | low, ran live on a scratch sheet |
| 56, loaded part link | `SourceLibraryName`, `LibraryIdentifier` on a part placed from a library | yes, `Library.pas` | low, ran live: `*` became the IntLib's name |
| 57, model rotation | `SetState` on a footprint body's model before it is bound | yes, `Lib_Set3DBody` | low, ran live on a scratch library and restored |
| 57, body position | `MoveByXY` on a footprint body | yes, `PCB.pas` | low, ran live on a scratch library and moved back |
| 57, xSignal class | `AddMember` on an xSignal class | no, nowhere | low, ran live on a scratch board, unsaved |
| 57, part parameters | `SetState_IterationDepth` on a part's parameter iterator | yes, `Project.pas` | low, narrows a write to the part's own parameters |
| 57, layer display | `IsInLayerStack` on a layer | no, nowhere | none, read only; ran live |
| 57, class members | `DM_Members` on a compiled object class | no, nowhere | none, read only |
| 57, net directives | `DM_Directives` on a compiled net | no, nowhere | none, read only; ran live |
| 60, part parameters on the board | `SetOrNewValueByName`, `TryRemoveByName` on a placed part's parameters | no, nowhere | low, one part; ran live on a scratch board, read back from the list, unsaved |
| 60, local grids | `SetProperty_GridStepX`, `SetProperty_AngularStep` on a new grid | no, nowhere | low, the new grid only; ran live and deleted |
| 60, baseline dimension | `SetState_Angle` on a new baseline dimension | no, nowhere | low, the new dimension only; ran live, four text locations read back |
| 60, guides | `DeleteAllGuides` on the board's grid manager | no, nowhere | low, removes guides only; ran live |
| 60, STEP settings | `SetState_ExportFoldedBoard` on the STEP exporter | no, nowhere | low, Altium's kept export settings; ran live, written back unchanged |
| 60, generated documents | `DM_AddGeneratedDocument` on a project | no, nowhere | low, lists a file under Generated; ran live on a scratch project, unsaved |
| 62, label rotation | `RotateBy90` by 90 and back on a part or sheet symbol, to reposition its labels | no, nowhere | low, the object returns to its rotation; ran live, size and entries unchanged, saved and reopened |
| 62, label autoposition | `Autoposition` on a sheet symbol's name and file name | yes, `Library.pas` (a symbol's labels) | low, ran live on a scratch sheet |
| 63, flight time | `MaximumFlightTime` on a Flight Time rising or falling rule | no, nowhere | low, one rule; ran live, read back and in Altium's descriptor |
| 63, impedance limits | `Minimum`, `Maximum` on an Impedance rule | no, nowhere | low, one rule; ran live, read back and in Altium's descriptor |
| 63, length limits | `MinLimit`, `MaxLimit` on a Length rule | yes, `PCB_CreateDesignRule` (hole size) | low, one rule; ran live, read back and in Altium's descriptor |

Steps 5 and 2 are the ones that justify a live session. The bottom rows
write properties this codebase already exercises, so they are checking
the new call site rather than the API.

Step 5 appears three times because its properties do not share a risk.
`MoveByXY` is inherited from `IPCB_Primitive` and `PCB_ReplicateLayout`
already calls it, so it cannot be an undeclared identifier and a failure
there would be behavioural: whether moving a group moves its children.
`Rotation` is written on pads, texts, fills and components but never on
a body, and DelphiScript resolves a property against the object in hand,
so another interface accepting it proves nothing here. Only
`StandoffHeight` is entirely unexercised.

Two steps now write `StandoffHeight` and neither has run, so they do not
vouch for each other: steps 5 and 15 share one unproven identifier. Run
whichever is easier and the other drops to a behavioural check.

### What this release adds, and why it is a different kind of risk

The steps above check whether an identifier exists. This release
carries almost none of that risk: every Altium property and method it
touches is already written somewhere in shipped code, so an undeclared
identifier is close to ruled out by construction. The cross-document
hints touch no Altium API at all, being string handling only.

`Proj_UpdatePCB` now reads `DM_FocusedDocument` and `DM_DocumentKind`
before comparing, and refuses while a schematic is focused. Both
identifiers are already used across several units, so neither is new
exposure. Everything else is string handling: the reply fields that
used to overstate what had been checked, and the removal of the
`Client:RunMenu` fallback in `App_ExecuteMenu`, which now refuses an
unmapped path instead of guessing a MenuID.

One thing here cannot be settled by running the steps below. The
refusal is deliberately absent from `Proj_UpdateSchematic`, the
opposite direction, because the focus Altium wants there has never been
measured. If you exercise that direction, record what it does.

Footprint height is the same shape of risk, which is to say almost
none. `Lib_SetFootprintHeight` writes `Footprint.Height`, which the
height sweep beside it has always written, and reads the library
through the same iterator thirty-odd other handlers use. What is new is
a DIRECTION: the sweep can now lower a height to the model, not only
raise it. Check it by setting a footprint absurdly tall by hand, running
the sweep in `match` mode, and confirming it comes back down. Confirm
the reverse too, that `raise` leaves it alone, because a mode that
ignores its argument would pass the first test on its own.

The case worth being careful about is a footprint with NO 3D body. It
must not be written at all. Writing the 0 that an absent model implies
does not relax placement-collision DRC, it disables it for that part,
and doing that across every unmodelled footprint would switch the rule
off wholesale while reporting a clean sweep. Run `match` on a library
where at least one footprint has no model, and check that footprint's
height is untouched and that its name comes back under
`without_model_names`.

What it adds instead is **logic that decides what to touch**, which
fails silently rather than loudly:

| Added | What could go wrong | How you would know |
|---|---|---|
| `GetPCBBoardForMutation` | An edit refuses when it should proceed, or proceeds when two boards are open and none focused | Open two PcbDocs, focus neither, run `pcb_delete_object`. It must refuse and name both |
| Board mechanical layers | A paired kind is written on the layer rather than the pair, which silently does nothing | Set a paired kind, then read it back with `pcb_get_mech_layer_names` |
| `lib_delete_footprint_primitives` | Removes from the wrong footprint, or takes pads with it | Probe the footprint first, delete one layer, probe again and compare pad count |
| Library parameter delete | Matches zero and reports success | Delete a named parameter from a library symbol, then read the symbol's parameters |
| Multi-part scope suffix | Returns part one's pins under another part's name | `lib_get_pin_list` on a multi-part symbol with `@2` and `@3`, and compare the counts |
| Cross-document hints | The hint is appended to an error it does not apply to, or doubles up on a message that already names a tool | Focus a SchDoc and run `pcb_delete_object`. The refusal must name the PCB tool once, and read as one sentence |
| Refusals name the focused document | A wrong-document refusal says what is missing and not what is there, so the caller goes looking for whatever took the focus | Focus a SchLib and run `sch_place_components`. The refusal must name the .SchLib by file name only, no folder, and still carry the `lib_` hint once. Name the library something containing `lib_` and repeat: the hint must still appear |

None of these can halt the polling loop the way an undeclared
identifier does, so they are safe to run in any order and safe to run
last. The cost of getting one wrong is a wrong answer, not a dead
bridge.

### Carried from the previous build: five fixes whose symptom was silence

These came out of one live session and a bug report, and they share a
shape: the tool reported success, the board or sheet did not agree, and
nothing anywhere said so. A guard test covers each one against the
source, which is not the same as having watched it work.

| Fixed | What it did before | How you would know it is fixed |
|---|---|---|
| Component movers use `MoveByXY` | Assigning `Comp.x` moved the record and left every pad where it was, so the pour kept clearing the old footprint and the DRC kept measuring it | Move a placed component with `pcb_move_components`, then `pcb_repour_polygons`. The copper must clear the NEW position and nothing at the old one |
| Placed copper joins its net | `Prim.Net := N` sets a reference, but the net keeps its own collection and connectivity, the ratsnest and the pour all walk that one. Copper had a net, reported that net, and was invisible to everything downstream | `pcb_place_via` on a named net, then `pcb_get_unrouted_nets`. The via must count as connected, and a pour on that net must give it thermal relief |
| Schematic writes mark the document | A write that does not set the modified flag is invisible to `SmartCompile`, which checks `ProjectHasDirtyDocs` and skips. Symptom: ERC and the netlist keep reporting the state before your edit until the sheet is reopened | Place something with `sch_place_no_erc`, then `app_context`. The sheet must appear in `unsaved`, and `proj_run_erc` must see the edit without a reopen |
| Position reads resolve per type | `GetPCBProperty` read `Obj.x` off the declared `IPCB_Primitive`. A type that does not publish it raised "Undeclared identifier: x", which the engine shows as a modal before any `Try/Except` runs, stopping the polling loop | `obj_query` a text object's position, then an arc's. Both must answer, and a track must come back `unreadable` rather than with one of its ends |
| `app_context` can see unsaved work | It filtered the open-document list on a `modified` key the handler never emitted, so the list was always empty and every session opened reporting itself clean | Edit any sheet without saving, then `app_context`. `unsaved` must name it |

`pcb_set_via_soldermask_relief` refuses on this build rather than being
fixed. The write raises an access violation inside
`ScriptingSystem.DLL` on AD 26.10.1.6, measured twice on a scratch
board with three vias and nothing else, once through `BeginModify` and
once through `SendMessageToRobots`. Because the fault lands between
`PreProcess` and `PostProcess` it also leaves an open transaction
behind it.

The refusal is in the PYTHON tool, and that is the half to check.
Reaching the handler means putting the command on the wire, and any
session running a deployed script from before the refusal landed still has the
crashing write in it. Confirm the tool answers `NOT_SCRIPTABLE`
instantly and that `bridge_trace.log` shows no request for it.

### Three new design-rule kinds, and these DO carry identifier risk

Unlike everything else in this release, these write Altium symbols this
codebase has never used. Run them before anything else in a session you
mind losing, because an undeclared identifier here stops the polling
loop rather than returning an error.

| `rule_type` | Symbols | Evidence they exist | How to check |
|---|---|---|---|
| `paste_mask_expansion` | `eRule_PasteMaskExpansion`, `IPCB_PasteMaskExpansionRule.Expansion` | `NofittedNoPaste.pas` in the reference corpus creates one exactly this way | Create one, then read it back with `pcb_get_design_rules` and look at `descriptor`, not `rule_kind`, which is a raw enum ordinal |
| `solder_mask_expansion` | `eRule_SolderMaskExpansion`, `IPCB_SolderMaskExpansionRule.Expansion` | enum used by three reference scripts; interface and property in the SDK reference. The FACTORY call is unproven | Create with `scope="IsVia"` and a negative value, then look at the mask layer over a via |
| `vias_under_smd` | `eRule_ViasUnderSMD`, `IPCB_ViasUnderSMDConstraint.Allowed` | same: enum used by three reference scripts, interface and property documented, factory call unproven | Create with `allowed=false`, place a via inside an SMD pad, run DRC |

`paste_mask_expansion` is the one to run first. It is the only one of
the three whose factory call is demonstrated by working published code,
so if it fails the problem is this handler rather than the symbol, and
if it succeeds the other two are down to their own enum values.

These close the gap the via-relief refusal used to point at: tenting is
a Solder Mask Expansion rule scoped `IsVia`, and via-in-pad is caught
by Vias Under SMD. Both were previously "set it in the dialog".

### Close without saving, and OutJob runs that wrote nothing

Two handler changes. Neither adds an identifier this codebase has not
already called, so the risk is behavioural rather than a halted loop.
Run both on a scratch project, never a client one: the first one is
built to throw edits away.

| Changed | What is unmeasured | How to check |
|---|---|---|
| `Proj_Close` with `save=false` clears each loaded member's modified flag before closing, without reading it | MEASURED on this build, local scratch project: the tab showed the sheet modified, the close raised no prompt, `attempts` was 1, and the file on disk was byte-identical afterwards. On the previous build the clear was gated on a read that returned False, and the close prompted. A managed project is untested | On a managed project, edit a sheet without saving, then `proj_close(project_path=..., save=False)`. Record whether a prompt appears |
| If a prompt appears anyway, `proj_close` answers it: Save None, then OK once the title reads "Confirm Not Saving" | The sequence was MEASURED on the previous build with `app_invoke_element`. The tool running it has not run live, because on this build the prompt did not appear | Only reachable where the step above still prompts. The reply then carries `prompts_answered` |
| It presses nothing when the prompt lists a document outside the project | Unmeasured | Do not arrange it on a project with real work open |
| `App_GetActiveDocument` and `Proj_GetDocuments` put a path in `file_path` | MEASURED: the previous build reported the bare file name from both, and this build reports absolute paths from both | Done |
| A close without saving that stays open is retried once | MEASURED on the previous build, and misread at first: `attempts: 2` on an unmodified scratch project was not the retry doing its job. The first close had closed a different project, the focused one | See the next row |
| `Proj_Close` focuses the named project before closing, refuses when it cannot, and stops without retrying when anything else closes | MEASURED on the previous build: `CloseObject` given a project's full path closed the FOCUSED project instead, and nothing reported it. This build's guard is unmeasured | Open two scratch projects, each with a loaded sheet. Focus a sheet of the first, then `proj_close` the second: only the second may close, and `closed_instead` must be empty. Then `proj_close` a project with no loaded document: it must refuse and close nothing |
| `Proj_RunOutJob` replies `process_issued`, and the Python tools decide `success` from the output folder | Nothing Altium-side | Run a container with its outputs switched off: `proj_run_outjob` must return `success: false` with a `reason`. Run one that writes: `files_written` must be above zero |

---

## Gather these before you start

Each step wants a particular document open or a particular file on
disk, and none of it is interesting to discover halfway through. Step 5
in particular needs a STEP model, which is not something to go looking
for mid-session.

| Step | What must be open, or on disk |
|---|---|
| 0, 1 | Altium running with `Altium_API` loaded and the loop started |
| 2 | a SchLib, with a component selected |
| 3 | a SchLib |
| 4 | a PCB whose active variant has Not-Fitted parts |
| 5 | a PcbLib with a footprint, plus a `.step` file |
| 6 | a PcbLib, plus a KiCad `.kicad_mod` carrying `B.SilkS` text |
| 7 | a SchLib for the pins, and a schematic sheet for the power ports |

Steps 4 and 5 write to the design. Take an `app_checkpoint` first, or
work on a copy. Everything else either reads or builds new library
objects you can delete afterwards.

---

## 0. Confirm what is actually loaded

```
app_ping
```

Expect `altium_script_version` = `2026.11.01.1`, `version_match` =
`true`, and `mcp_server_version` = `0.8.1`.

Those are two different versions and they fail differently.
`altium_script_version` is the Pascal that Altium compiled;
`mcp_server_version` is the Python package answering the call. A wrong
Pascal version means a stale deploy. A wrong package version means the
installed wheel is not this tree, so the tools themselves differ from
what this document describes.

A mismatch means Altium is still running an older compiled copy.
Reload the script project (**File > Run Script**, pick the project,
or close and reopen it) and ping again. Do not interpret any later
result until this matches: a stale script produces failures that look
like defects in the new code.

**No answer at all is a different failure with a different fix.** A
mismatch means the wrong script is running; a timeout usually means no
script is running. Altium's process being alive proves nothing here,
since the polling loop can be stopped while Altium itself is fine. That
was the state of this machine while this document was written.

Read `last_fault.json` in the workspace directory rather than guessing.
The bridge writes a diagnosis and numbered steps there, and tells the
three cases apart:

| `fault` | What it means | Fix |
|---|---|---|
| `dead_loop` | no response and no heartbeat | dismiss any Altium error dialog, Stop, relaunch |
| `stuck_handler` | keep-alives answered, one command never returned | Stop, relaunch |
| `corrupt_response` | Altium crashed mid-write | retry once, the bad file is already removed |

Stop is the red button in the Script IDE (**Run > Stop**, `Ctrl+F3`,
or `Ctrl+Pause` if it is truly hung). Relaunch is **File > Run
Script... > Altium_API > Dispatcher.pas > StartMCPServer**.

Leftover `request_*.json` files in the workspace are a symptom of this,
not a cause: they are calls the loop never picked up. They are harmless
and the next healthy loop consumes them.

---

## 1. Pure logic, no document needed

`RunSelfTest` is no longer listed in the Run Script dialog. It carries a
dummy argument like every other internal routine, so the dialog offers
the one thing a user does: start the bridge. To run the self test,
temporarily drop the argument from `Procedure RunSelfTest(Dummy : Integer)`
in `SelfTest.pas`, reload the project, and pick it from the dialog.

It also ends in `ShowMessage`, so it cannot be reached over the bridge
without splitting the summary out first.

Expect `Failed: 0`. The log is written to the workspace directory.

This runs 27 assertions over `StrToIeeeSymbol`, `IeeeSymbolToStr` and
`StripChar` inside Altium's own engine. The values match what
`tests/test_cross_validate.py` pins against the FPC-compiled originals,
so a failure here means DelphiScript disagrees with Free Pascal. That
is the gap this step exists to close.

**If it fails:** the log names the assertion. Report the text; the
converters are in `scripts/altium/Utils.pas`.

---

## 2. Pin edge decorations (task #20)

Needs a SchLib open with a component selected.

```
lib_add_pins(pins=[
  {"designator": "1", "name": "RESET", "x": -300, "y": 0,
   "symbol_outer_edge": "dot"},
  {"designator": "2", "name": "CLK",   "x": -300, "y": -100,
   "symbol_inner_edge": "clock"},
])
```

Then **look at the symbol**. Pin 1 must carry an inversion bubble at its
outer end; pin 2 a clock wedge at the body end.

The returned `added` count says how many pins were created, not how they
were drawn, so it cannot confirm this. Render or view it.

**What is actually being tested:** whether `Symbol_OuterEdge` /
`Symbol_InnerEdge` are settable on a pin from `SchObjectFactory`
*before* `AddSchObject`, and whether assigning an Integer to those
enum-typed properties behaves the way `Pin.Orientation` does. Step 1
already proved the value mapping.

**The two failure shapes, and why the assignment is deliberately not
wrapped in `Try/Except`:**

* the property exists but rejects the value: the dispatcher catches it
  and answers `INTERNAL_ERROR: Unhandled exception processing:
  library.add_pins`. The loop keeps running.
* the property name is not real: an undeclared identifier faults where
  `Try/Except` cannot reach, and the polling loop stops. Recover with
  Detach and `StartMCPServer`.

Guarding the assignment would turn both into a silent success that adds
undecorated pins, which is the one outcome this step could not tell
apart from working. That is also why this step is early: it is the
first thing here that can halt the loop.

---

## 3. Filled IC bodies (task #28)

```
lib_create_ic_symbol(...)          # any part
```

The body rectangle must be **filled Altium light-yellow**, not a bare
outline. This is discipline rule 17, and it has been silently unmet:
the tool sent `AreaColor 8454143` and the bridge discarded it because
`IsSolid` was pinned False.

Then confirm the no-fill path still works:

```
lib_create_passive_symbol(kind="resistor", ...)
```

That sends `fill_color=-1`, the documented no-fill sentinel, and its
body must stay **unfilled**. `-1` is also the parameter's default, so it
arrives on nearly every call; treating it as a colour would turn every
symbol rectangle solid.

---

## 4. DNP paste exclusion (task #26)

**This edits the board.** Work on a copy, or `app_checkpoint` first.

Needs a PCB with a variant that has Not-Fitted parts.

```
pcb_apply_dnp_paste_exclusion(dry_run=True)
```

Check the designator list matches `audit_variant_not_fitted`. Then:

```
pcb_apply_dnp_paste_exclusion()
pcb_get_pad_properties(designator="<one of them>")
```

`paste_mask_expansion` must be negative.

A negative expansion is necessary but not sufficient. Only the artwork
proves the fab outcome:

```
proj_generate_fab_package(...)
```

Open the paste layer and confirm those apertures are **absent**. Then
restore, passing back the designators the apply reported in `items`:

```
pcb_apply_dnp_paste_exclusion(designators=["<from the apply reply>"],
                              restore=True)
```

Regenerate and confirm the apertures come back.

**Restore will refuse a bare `restore=True`**, and that refusal is the
behaviour to verify, not a defect. Resolving a restore from the current
variant is only correct while the variant has not changed since the
apply; when it has, a component excluded under the old variant keeps
its aperture suppressed and nothing says so. Check that the refusal
names both the way forward and `use_current_variant`, which is the
explicit opt-in to the old resolve-from-the-variant behaviour.

After a restore, `paste_mask_expansion` reads **0**, which is what a
successful restore looks like and not a separate failure. The field is
always present; `PCB_GetPadProperties` initialises it to 0 and only
substitutes the real value when the pad cache is manual. Restore sets
the cache back to invalid, so the design rule drives the aperture and
the reported number falls to 0.

A pad that was never excluded also reads 0, so this distinguishes
applied from not-applied, not restored from never-touched. The artwork
is what settles that.

Check a **bottom-side** DNP part too if the board has one:
`Pad.TopXSize` is the top-layer size and drives the expansion for both
sides.

---

## 5. 3D model placement (task #27)

The highest-risk item. All three properties are documented on
`IPCB_ComponentBody`, but only `MoveByXY` has ever been exercised here,
and on a different object type. See the note under the risk table for
which of the three is actually unproven.

```
lib_link_3d_model(component_name="<fp>", model_path="<...>.step",
                  offset_z=40, rotation_z=90, offset_x=10)
```

Read the `applied` object in the reply first: `standoff_height`,
`rotation_z`, `offset_xy`. Each assignment is individually guarded, so
one property failing does not fail the call.

All three must read `true` for the call above. They mean "this was
applied", not "this was accepted": the handler skips a property whose
value is 0, so a `false` is either a rejection or a value you did not
pass. Keep every argument non-zero while verifying, or the two cases
are indistinguishable. `offset_xy` covers `offset_x` and `offset_y`
together and reads `true` if either is non-zero.

Then open the 3D view: the body should be lifted, turned and nudged.

**Units:** the tool documents mils and applies `MilsToCoord`. A body
that moves 25.4x too far means the property wanted different units.

`rotation_x` / `rotation_y` are accepted and deliberately not applied,
because the API gives the body a planar rotation only. If a live session
finds an X/Y tilt property, revisit the docstring then.

---

## 6. Mirrored bottom-side text (task #21)

Lowest risk of the set. `MirrorFlag` is already written by `PCB.pas` and
read by `Audit.pas`; only the call site is new.

Import a footprint carrying `B.SilkS` text, then:

```
audit_find_mirrored_pcb_text
```

Expect **zero** violations. Before this change the importer produced
`bottom_overlay_text_is_not_mirrored` on every such item, i.e. output
this server's own audit rejected.

---

## 7. Enumerated words map to the right Altium enum (task #33)

This covers what `tests/test_enum_vocabularies.py` cannot. That test
proves every advertised spelling has a branch in the `StrTo*` converter.
It cannot prove the branch assigns the enum member the word names,
because the ordinals only exist inside Altium.

The failure is silent. Each converter ends in an `Else` that picks a
default, so a wrong or missing branch yields Passive, or a supply Bar,
with no error reported.

Place four pins with different electrical types, then read them back:

```
lib_add_pins(pins=[
  {"designator": "1", "name": "VIN",  "x": -300, "y":    0,
   "electrical_type": "power"},
  {"designator": "2", "name": "NRST", "x": -300, "y": -100,
   "electrical_type": "open_collector"},
  {"designator": "3", "name": "SDA",  "x": -300, "y": -200,
   "electrical_type": "io"},
  {"designator": "4", "name": "OUT",  "x": -300, "y": -300,
   "electrical_type": "output"},
])
lib_get_component_details(...)
```

Each pin must report the type it was given. All four reading `passive`
means the string never matched and every one took the default;
`open_collector` alone reading `passive` means the underscore handling
is the part that broke.

Then one power port per glyph:

```
sch_place_power_port(text="GND",  style="gnd_signal", x=1000, y=1000)
sch_place_power_port(text="+3V3", style="bar", x=1400, y=1000,
                     orientation=1)
```

`orientation=1` on the rail is required. The style-based default sends
`bar` and `wave` down with the grounds, so a VCC bar drawn without it
points down and looks like a ground symbol.

Look at the sheet. A signal-ground glyph and a rail bar are visually
distinct; two identical bars mean `gnd_signal` fell through to the
`ePowerBar` default.

---

## 8. ERC violations name the objects they are about

**`proj_run_erc()`, then `proj_get_erc_violations()`**

On a project that reports violations, every entry should now carry
`related_objects`, each with a `kind`, a `document` and a `cross_probe`
string.

This is the step that decides whether the tool is usable at all. A
violation reported as a category and a sheet name cannot be acted on:
the only safe response to "floating input pin, somewhere on this sheet"
is to do nothing, because a NoERC marker placed by guesswork silently
suppresses a real disconnection and is worse than the warning it clears.
`cross_probe` is what Altium itself uses to jump to an object, so it is
what identifies the specific pin or net.

Compare against the Messages panel: the objects listed there for a given
violation should match `related_objects` for the same index.

**Expect `related_object_count` to be non-zero** for floating-pin and
unconnected-object violations. Zero across every violation means
`DM_RelatedObjects` returned nothing in this build, which is a different
outcome from the call faulting, and is why the count is reported
separately from the list.

**If it fails:** the risk here is `DM_PrimaryCrossProbeString`. It is
declared on `IDMObject`, the base every related object implements, so it
should be safe on all of them, but that is reasoned from the reference
rather than measured. An undeclared identifier faults where `Try/Except`
cannot catch it and halts the polling loop, so a dead loop right after
calling this tool points at that call. `Gen_GetErcViolations` is in
`scripts/altium/Generic.pas`.

---

## 9. Reading a symbol's pins no longer faults (task #34)

Needs a SchLib open.

```
lib_get_pin_list(component_name="<a symbol in that library>")
```

Expect a pin list. Then call it again with no `component_name` and a
symbol selected in the editor, which must also work.

**This one is a fix for an observed crash, not a new feature.** The
deployed script answered
`Undeclared identifier: SchIterator_Create` and stopped the polling
loop. The identical call appears ten times in `Library.pas` and works
everywhere else; the difference was where the component came from.
Every working reader fetches it through `GetState_SchComponentByLibRef`
or a SchLib iterator, while this one used the editor's
`CurrentSchComponent` directly. DelphiScript narrows an interface at
iterator-return, and a component obtained any other way does not carry
the methods.

So this step is really asking one question: does resolving through the
library make the iterator available? If it does, the explanation holds.

`component_name` is the other half of the fix. Reading a symbol's pins
used to depend on, and disturb, whatever the editor had selected, which
is why exporting one symbol could change which symbol later calls saw.

**If it fails with the same identifier**, the narrowing explanation is
wrong. Say so rather than trying variations: that reasoning came from
comparing call sites, not from proving the mechanism, and the next step
would be to instrument rather than guess again.

**If it fails with a different identifier**, that is a second undeclared
name in the same function and the message will say which.

---

## Step 9: the multi-part scope suffix actually switches part

Reported in GH #11 against a 4-part TPS23881B, with numbers. The `@N`
suffix parsed and reached the part-switch code, and the switch itself
did nothing: `Component.CurrentPartID := N` takes the value, the
editor's part spinner does not move, and the SchLib iterator follows
the DISPLAYED part. So `obj_query` returned part 1's pins whatever the
scope said, and with the spinner moved by hand the suffix was ignored
outright. Nothing errored in either direction.

The fix drives the editor's own command, `SCH:NextComponentPart`, and
reads `GetState_CurrentSchComponentPartId` back after each step. Both
appear in two independent scripts under `reference/`, so neither is a
guess, but neither has run from this codebase.

Open a multi-part SchLib and, with the editor showing part 1:

    obj_query  scope lib_component:<NAME>@2  kind ePin
    obj_query  scope lib_component:<NAME>@3  kind ePin

Each must return that part's own pin count, and every returned pin must
carry the matching `OwnerPartId`. Then the sharper test: switch the
spinner to part 3 by hand and query `@1`. It must return part 1.

**Two ways this fails quietly.** If
`GetState_CurrentSchComponentPartId` is undeclared, the polling loop
halts, which is loud. If it returns -1 instead, the stepping is skipped
by design and the behaviour is exactly the bug being fixed: the same
wrong answer, no error. So a run that still returns part 1 is not
evidence the command is wrong, it is evidence the part id could not be
read. Report which.

The loop is bounded by `PartCount` because the command wraps at the
last part. A target that can never be reached leaves the editor moved
but not where asked, so check the spinner afterwards.

**Part 1 goes a different way, new in the previous build.** The command only
steps forward and stops at the last part, so from part 3 there is no
step back to part 1, and `@1` read whichever part was on screen.
`@1` now selects some other component in the library and then this
one again, which reopens it on part 1, and checks the pins it can see
afterwards. None of that has run in Altium. Three checks:

* Spinner on part 3 by hand, query `@1`. Part 1's pins, and the
  spinner on part 1 afterwards.
* The same in a library holding only that one component. It must
  refuse and say in `next_step` that there is no other component to
  reselect from, not answer with part 3's pins.
* A scope with no suffix at all, `lib_component:<NAME>`, with the
  spinner on part 2. It now reads part 2, the part on screen, and
  leaves the spinner where it was. It used to be treated as `@1`.

---

## 10. UNC paths survive the trip (task #44)

This release deletes the vestigial second unescape
(`StringReplace(x, '\\', '\', -1)`) from all 94 path-taking handlers.
`ExtractJsonValue` already unescapes the JSON, so the second pass was a
no-op for local paths and stripped one leading backslash from UNC
paths: `\\server\share\lib.SchLib` arrived as
`\server\share\lib.SchLib` and failed as a missing file.

No new identifiers are involved, only deletions, so the compile risk
is nil; what needs proving is the behaviour. From a machine with any
reachable share (an admin share like `\\localhost\C$\...` works):

    lib_get_components  library_path \\localhost\C$\<path-to-any>.SchLib

Before the fix this fails with a file-not-found flavoured error;
after it, the library opens and lists components. Local absolute paths
must keep working unchanged, which step 9's queries already exercise.

`tests/test_no_double_unescape.py` pins the site count at zero from
now on, so this is a one-time verification, not a recurring step.

## 11. Copy and rename actually change the library

Reported against the previous script build: `lib_copy_component` and
`lib_rename_component` both answered `success:true` while the component
count did not move, the copy's `new_name` resolved nowhere, and the
renamed part was still there under its old name.

`AddSchComponent` overrides `LibReference` with an auto-generated
`Component_<N>` on the second and later additions to a SchLib in one
session, so an assignment made before the add does not survive it.
`Lib_CreateSymbol` already re-asserts after the add for this reason;
these two did not. No new identifiers are involved, so the compile risk
is nil, and what needs proving is the behaviour.

On a scratch library, with a symbol that is not the first added this
session:

    lib_copy_component    source_name <existing>  new_name COPY_PROBE
    app_save_all
    lib_get_components    library_path <the same library>

`COPY_PROBE` must appear, and the count must be one higher. Then:

    lib_rename_component  component_name COPY_PROBE  new_name RENAME_PROBE
    app_save_all
    lib_get_components    library_path <the same library>

`RENAME_PROBE` must appear, `COPY_PROBE` must be gone, and the count
must be unchanged.

**Save before reading.** `lib_get_components` reads the FILE, so without
the save these steps can only fail. And `verified:true` is not evidence
of anything on disk: the handlers verify through `LookupLibComponent`,
which returns the component this session last created or renamed by
name, whether or not the library holds it.

**What the previous builds did, live.** Copy and rename both answered
`verified:true`, the library was saved, and the file kept the old name
and never gained the copy. Each first asked whether the new name already
existed, through the lookup that reopens the library on a miss, and a
miss is the normal answer. The reopen saved, closed and reopened the
library, so the handler went on to edit a component in a closed
document, and every later save wrote the reopened library without the
edit. `lib_batch_rename`, which never asks, persisted on the same
scratch library. The existence checks now use a lookup that cannot
reopen. That is the change these steps test.

Note that `part_count` from `lib_get_components` is not evidence of
anything here. It comes from the CompInfoReader, which has been measured
reporting 2 for a symbol created single-part whose every pin carries
`OwnerPartId 1`, while `lib_get_component_details` reported 1 for an
identically created symbol.

## 12. Deleting a project variant

`DM_RemoveProjectVariant` ran live on AD 26.11.1.5 (step 42): a scratch
variant with one entry was deleted, `verified` true, `entries_removed`
1, and the loop kept answering.

Deleting a populated variant still destroys the decisions in it. They
can now be rebuilt from this bridge, one part at a time, with
`proj_set_component_variation` (step 42), but nothing records them
first, so list them with `proj_list_variants` before deleting.

**Work on a copy of a project, or take an `app_checkpoint` first.**

Create a throwaway variant in the Variant Management dialog, then:

    proj_list_variants
    proj_delete_variant   variant_name SCRATCH_VARIANT
    proj_list_variants

The reply must carry `verified: true`, `variant_count_after` one lower
than before, and `entries_removed`. The second listing must not contain
the name. If the loop stops answering instead, the identifier is not
declared in DelphiScript and the reference is wrong about it; say so
and the tool comes back out.

Then check the refusals, which cost nothing: a name that does not exist
must return `VARIANT_NOT_FOUND` and change no count, and an empty
`variant_name` must return `MISSING_PARAMS`.

`proj_set_active_variant` changed in the same release and is cheap to
check alongside. It used to report success on the strength of the name
existing, without asking which variant was actually current afterwards.
Switch to a variant and confirm the reply carries `verified: true`;
the failure reply now names `current_variant` so a switch that did not
take is distinguishable from one that did.

## 13. obj_modify stops reporting writes it did not make

Measured on a live project: `obj_modify` was asked three times to set a
sheet symbol's `FileName`, answered `matched:1, saved:true` each time,
and wrote nothing. The property is readable and had no case in the
writer, so each attempt was recorded as an unknown name in a diagnostic
buffer that only `batch_modify` ever rendered. Nothing in the reply
distinguished it from a real one, and an operator spent a session
working around a rename that had never happened.

`matched` counts what the FILTER selected. It never said anything about
whether a write landed. Every modify reply now carries `properties`
and an explicit `success`.

The cheapest check needs no sheet symbol at all. On any open schematic:

    obj_modify  object_type eNetLabel  filter <anything that matches one>
                set NotAPropertyName=1

That must come back `success:false` with `NotAPropertyName` under
`properties.unknown`, and `matched` may still be 1. Before this release
it returned `matched:1, saved:true` and nothing else.

Then the real one, on a sheet symbol whose child sheet you do NOT mind
re-pointing, or on a scratch copy of a project:

    obj_query   object_type eSheetSymbol  properties Filename,UniqueId
    obj_modify  object_type eSheetSymbol  filter UniqueId=<the id>
                set Filename=SOMETHING_ELSE.SchDoc
    obj_query   object_type eSheetSymbol  properties Filename,UniqueId

The reply must be `success:true` with an empty `properties.unknown`,
and the second query must show the new text. A write that does not
stick now reports under `properties.failed`, because the setter reads
the label back rather than trusting the assignment.

**This re-points a symbol, it does not rename a sheet.** The filename
lives in three places: this label, the file on disk, and the project's
document list. Only Altium's **Sheet Symbol Actions > Rename Child
Sheet** does all three, and it keeps the symbol's `UniqueId`, which is
the project's handle for that sheet instance. Deleting and re-placing a
symbol issues a new id, and the next Update PCB then proposes
delete-and-re-add for every component on the sheet instead of matching
them.

## 14. Polygon pour options, and the PCB writer that never reported

Three properties this codebase has never written, so this carries the
undeclared-identifier risk in full. They are declared on `IPCB_Polygon`
with both accessors:

    Property RemoveDead : Boolean Read GetState_RemoveDead
                                  Write SetState_RemoveDead;

and the same shape for `RemoveNarrowNecks` and `RemoveIslandsByArea`.
Reported from a live board as "not an exposed property on this API",
which was a fair reading of a modify that answered `matched:2` and wrote
nothing.

**Nothing here repours.** These decide what the NEXT pour produces, so
the copper does not change until `pcb_repour_polygons` runs. Check the
flag first and the pour second, or a working change looks like a failed
one.

    pcb_get_polygons
    pcb_modify_polygon  index 0  remove_dead true
    pcb_get_polygons

The reply must carry `changed: ["remove_dead"]`, `modified: true` and
`repour_needed: true`. Then repour and confirm the dead copper goes.

Set it back to `false` afterwards and confirm `changed` names it again:
a handler that ignored the value and always wrote true would pass the
first half on its own.

Two refusals, which cost nothing and are the point of the release:

    pcb_modify_polygon  index 0  hatch_style Horizontal
    pcb_modify_polygon  index 0  net NO_SUCH_NET

Both must come back with `success: false` and the field named under
`not_applied`. `Horizontal` was documented by the tool and handled by no
branch, so it changed nothing and reported success; a net name that
matches nothing did the same.

The PCB property writer also reports now, so the generic route says so
too. On any board:

    obj_modify  object_type ePolyObject  set NotAProperty=1

must come back `success:false` with the name under
`properties.unknown`. Before this it returned a bare `matched` count.

## 15. A STEP model straight onto the board, and a read that stopped moving focus

Two things from one session, and the second is why the first took so
long to diagnose.

**THIS TOOL HAS ALREADY CRASHED ALTIUM ONCE.** The first build set
Layer, x, y and StandoffHeight on the body before adding it to the
board, and the PCB engine went down with "Access violation ... Read of
address 0x20" inside ADVPCB.DLL. A null dereference at a small field
offset is a property setter reaching for state that an owning board
provides. It also assigned x and y directly, where the reference and
`Lib_Link3DModel` both use `MoveByXY` after the add.

Both are corrected: add, register, then Layer, then MoveByXY, then
StandoffHeight. Treat this step as unproven all the same, and take an
`app_checkpoint` before running it.

**`pcb_place_3d_body`** puts a STEP model on the open PcbDoc as a free
3D body, which is Altium's Place > 3D Body > Generic STEP Model. Until
now `lib_link_3d_model` was the only STEP importer in the toolset and it
writes into a `.PcbLib` footprint, so putting a fixture or a
device-under-test on a board meant inventing a library, authoring a
footprint, placing it and deleting all of it again. The call sequence
here is the one `Lib_Link3DModel` already uses, minus the footprint
binding, so every identifier in it is exercised by shipped code.

On a scratch board, with any `.step` to hand:

    pcb_place_3d_body  model_path <path>  x 1000  y 1000  standoff_height 100
    obj_switch_view    3d

The reply's `x` and `y` are READ BACK from the placed body rather than
echoed. Confirm `standoff_applied` is true, then look: the body must be
visible and sitting 100 mils proud. `rotation_applied` is always false
and says why in `note`.

Then the refusals, which cost nothing:

    pcb_place_3d_body  model_path C:\nope.step
    pcb_place_3d_body  model_path <a .txt file>

The first must be `FILE_NOT_FOUND` and the second `MODEL_LOAD_FAILED`.
They are separated on purpose: the path is checked before anything is
created, because `ModelFactory_FromFilename` on a missing file returns
Nil and leaves an orphan body behind.

**The focus fix has no visible output, so check it by its absence.**
Nine read-only library actions used to leave the active document on the
library they read, because focusing it is the only way the PCBServer and
SchServer accessors will answer about it. Measured: `lib_probe_footprint`
silently focused a PcbLib, and the `obj_switch_view 3d` that followed
switched the LIBRARY into 3D. The board looked untouched and the model
looked absent.

With a PcbDoc focused:

    lib_probe_footprint  library_path <some .PcbLib>  footprint_name <one>
    app_get_active_document

The active document must still be the PcbDoc. Try it with
`lib_search`, `lib_get_component_details` and `lib_audit_styles` too;
all nine are listed in `LibActionIsReadOnly`.

Then confirm the opposite, which is the half that could regress
silently: library WRITES must still leave the library focused, because
authoring is a sequence of calls against a current component.

    lib_set_current_component  <a symbol>
    lib_add_pins               <a pin or two>

The pins must land on that symbol. If they land nowhere, the restore has
been applied to writes and the authoring flow is broken.

## 16. A region's Kind, and board cutouts

Reported from a live board as "the Board Cutout flag on a Region isn't
reachable through this API". It is:

    Property Kind : TRegionKind Read GetState_Kind Write SetState_Kind;

and it had simply never been exposed, which is the third instance this
release of a property being called absent because a reply said nothing.

The five identifiers are attested rather than assumed: four independent
scripts in `reference/` COMPARE against `eRegionKind_BoardCutout`,
`_Cutout`, `_Copper`, `_NamedRegion` and `_Cavity`, so they exist in
DelphiScript. None of them ASSIGNS one, so the write is unproven in the
way `StandoffHeight` is. Read first, and take an `app_checkpoint` before
writing.

Words, not numbers, in both directions. The ordinals are undocumented,
and publishing one invites a caller to write it back.

On a board with an existing cutout:

    obj_query   object_type eRegionObject  properties Kind,Layer

At least one region must come back `board_cutout` or `cutout`. If every
region reads `unknown`, the comparison is not matching and the write
below must not be attempted.

Then, on a scratch region you do not mind losing:

    obj_modify  object_type eRegionObject  filter <one region>
                set Kind=board_cutout
    obj_query   object_type eRegionObject  properties Kind

It must read back `board_cutout`, and the board outline must show the
hole. An unknown word is refused rather than ignored:

    obj_modify  object_type eRegionObject  filter <one>  set Kind=nonsense

must come back `success:false` with `Kind` under `properties.unknown`.

---

## 17. Writing a component's UniqueId (highest risk in this release)

`sch_set_component_unique_id` and `sch_replicate_component` both assign
`ISch_Component.UniqueId`. Nothing in this repository wrote it before,
and **no independent script in `reference/` writes it at all**, so the
identifier is unattested. It is guarded with `Try/Except`, which is no
guard: an undeclared identifier is not catchable in DelphiScript, so if
the name is wrong the modal takes the polling loop down.

It also carries a second risk that has nothing to do with whether the
call works. **A UniqueId is the project's handle for a component.** Give
one the wrong value and the next Update PCB stops matching that part and
proposes delete-and-re-add for it instead, taking its placement and
routing with it. That is a worse outcome than the tool failing.

**Work on a copy of a project.** An `app_checkpoint` does not cover the
PCB side of this.

Read one first, so there is something to put back:

    obj_query   object_type eSchComponent  filter Designator=<one>
                properties UniqueId

Then write the same value back to itself, which is the only edit here
that cannot lose anything:

    sch_set_component_unique_id  designator <the same>  unique_id <what it read>

The reply must carry `success: true` and `unique_id_after` equal to what
was asked. If the loop stops answering instead, the identifier is not
declared and the tool comes back out.

`success: false` with `unique_id_after` different is the OTHER outcome
worth knowing: the call worked, Altium declined the assignment and kept
its own id. That is a real answer, not a failure of the bridge, and it
is what the comparison was added to surface.

Only then try a component you do not mind re-annotating, and check with
`proj_compare_sch_pcb` that the PCB still matches before and after.

## 18. Moving a pin to another sub-part

`lib_set_pin_owner_part` writes `OwnerPartId`, which two independent
scripts in `reference/` also write, so unlike step 17 the identifier is
attested and the risk is behavioural rather than declarative.

The contributor stated plainly that this tool has never executed against
a live Altium. Two things are worth checking because they fail quietly:

    lib_get_pin_list        component_name <a multi-part symbol>
    lib_set_pin_owner_part  component_name <the same>  pin_designators "3, 12"
                            owner_part_id 2
    lib_get_pin_list        component_name <the same>

Both pins must come back with `owner_part_id` 2, and the count in the
reply must be 2 rather than 1. The spaced form is deliberate: `"3, 12"`
used to match nothing and report `count: 0` as a successful no-op.

Then the bound, which is the one that corrupts rather than refuses:

    lib_set_pin_owner_part  ...  owner_part_id 99

must be refused. An id above the symbol's `PartCount` is accepted by the
assignment and maps to no displayable part, so the pin vanishes from
every sub-part view while the library still contains it.

`owner_part_id 0` is Part Zero and is always legal: the pin is shared
across all parts. Confirm it reads back as 0 rather than being treated
as an error.

Save with `app_save_all` and reopen the library before trusting any of
it. A library edit is real in memory and absent from disk until then.

## 19. The layout model read

`pcb.get_layout_model` reads a whole board for the new placement and
routing engines, one section per call, in Altium's internal units. It
only reads, so the cost of a mistake is a wrong or missing field, except
for one class of mistake: these identifiers are read here and are NOT
used anywhere else in this codebase. Altium's own example scripts read
them the same way, which is why they are expected to work, but an
undeclared one stops the polling loop:

| Identifier | Where |
|---|---|
| `IsKeepout` on any primitive | copper section |
| `StackShapeOnLayer`, `XStackSizeOnLayer`, `YStackSizeOnLayer`, `StackCRPctOnLayer`, `XPadOffset`, `YPadOffset` | pads section |
| `MidShape`, `MidXSize`, `MidYSize`, `BotShape`, `BotXSize`, `BotYSize` | pads section |
| `Region.HoleCount`, `Region.Holes[i]` | copper and components sections |
| `Fill.Rotation` | copper section |
| `Pad.IsPadRemoved(Layer)` | pads section |
| `Via.IntersectLayer(Layer)`, `Via.SizeOnLayer(Layer)` | copper section |
| `Obj.Polygon`, and that polygon's `Name` and `Net` | copper section |
| `GetState_IsTenting_Top`, `GetState_IsTenting_Bottom`, `SolderMaskExpansionFromHoleEdge` and `GetState_Cache` on a pad (read elsewhere only on vias) | pads section, `LmMaskJson` |

Open any board and run each section in turn, the riskiest first:

    pcb.get_layout_model  section=pads     limit=50
    pcb.get_layout_model  section=copper   limit=50
    pcb.get_layout_model  section=board
    pcb.get_layout_model  section=components
    pcb.get_layout_model  section=rules
    pcb.get_layout_model  section=classes

Each must answer. Then check the values against the board, not just
that they arrived:

* A through-hole pad lists copper on every signal layer, a surface pad
  on its own layer only, and sizes divided by 10000 match the pad's
  properties in mils.
* A rounded-rectangle pad carries its corner percentage.
* On a board with internal planes, `board` lists `split_planes`, each
  with its layer, its net and its regions. A plane's net is read from
  there because `LayerObj.Net` on a stack layer faulted live as an
  undeclared identifier. `pcb_set_plane_net` reads and writes that same
  `LayerObj.Net`, so expect it to stop the polling loop the same way
  until it is changed; do not run it on a board in use.
* A part known to be locked reports `locked: true`.
* On a board with poured polygons, a poured region or track in the
  `copper` section carries `pour` (the polygon's name) and `pour_net`
  (its net), and a region's `copper` flag is false for a polygon
  cutout.
* On a board whose vias have unused inner pads removed, a via's `sizes`
  lists every signal layer it spans, and on a removed layer the size is
  the hole size. A through-hole pad's copper entry for such a layer ends
  in `true`.
* Every pad and via carries `tent` (top, bottom), `from_hole` and
  `mask`. A via shown tented in its properties reads `tent` true on
  that side; a pad with a hand-set solder mask expansion reads it in
  `mask` (internal units, top then bottom), and `mask` is null on one
  that follows the mask rule. The sliver check counts on these: a board
  read by an older script says mask slivers were not checked.
* `pcb.get_layout_model section=copper limit=50 trace=true` answers as
  without `trace`, and `workspace/layout_trace.log` gains a line
  `copper offset=0 limit=50`, then per object its index, `kind=`, and a
  line before each of `owner`, `common`, `via sizes`, `region shape` and
  `polygon points` that the object reaches. Twice the copper read of a
  board crashed the scripting system (an access violation no Try can
  catch) with nothing to say which object it was on; with `trace`, the
  last line names it. Delete the file afterwards.
* The copper read narrows. `section=copper kinds=polygon` lists the
  board's pours and nothing else (compare the count with
  `pcb_get_polygons`), each with its `pts`, arcs as
  `[vx,vy,cx,cy,r,a1,a2]`; on a board of thousands of tracks it answers
  in about a second, where the unnarrowed read took most of a minute.
  `kinds=polygon layer=<a mechanical layer>` lists only the pours on
  that layer. Select two pours by hand: `kinds=polygon selected=true`
  lists exactly those two, and `offset=1 limit=1` the second. `kinds=pad`
  is refused `BAD_KINDS` and `layer=Nowhere` `UNKNOWN_LAYER`, before
  anything is read. Without the three, the read is unchanged: the same
  entries in the same order as before.
* The copper read reaches Mechanical17 and up (issue #149). Enable
  Mechanical17 and Mechanical21 and place a pour on each:
  `place_polygon_rect` answers with those layer names, not `Unknown`;
  `kinds=polygon` lists both, `layer=Mechanical21` exactly its pour,
  and `layer=BottomLayer` the same pours as before. Remove the pours and
  disable the two layers again.

---

## 20. Router output lands where the router put it

`pcb_place_vias` places a batch of vias in one call (`pcb.place_vias`),
and `pcb_place_tracks` now keeps decimals in coordinates and in width.
The router works on a grid finer than a mil and a rule set in mm is not
whole mils either: a 0.1 mm width is 3.937 mil, and parsed as an integer
it fell back to the 10 mil default.

On a scratch board, not a client's:

    pcb_place_vias  vias=[{"x": 1000.25, "y": 1000.5, "size": 12.5, "hole_size": 6.25},
                          {"x": 1100, "y": 1000, "size": 20, "hole_size": 10,
                           "net": "<a net on the board>"}]
    pcb_place_tracks tracks=[{"x1": 1000.25, "y1": 1100, "x2": 1200.75, "y2": 1100,
                              "width": 3.937, "layer": "TopLayer"}]

Both must report every item placed. Then read them back and compare,
not just the counts:

* The first via sits at 1000.25, 1000.5 with a 12.5 mil pad and a 6.25
  mil hole; the second carries its net.
* The track is 3.937 mil wide, not 10 and not 4.
* A via with an unknown layer name is counted in `failed` and named in
  `unknown_layers`, and the rest of the batch is still placed.

Then the tools that use them, on a public example board opened in Altium
(never a client's):

    pcb_autoroute        expect_file=<the board's full path>
    design_job_status    job_id=<from the reply>        until done
    design_job_result    job_id=<same>
    pcb_autoroute_apply  job_id=<same> expect_file=<same path>

* `pcb_autoroute` with another board focused answers with an error and
  starts no job.
* The job's `summary` reports completion, violations, routing layers, the
  planes it relied on and the pours that join a net. On a two-layer board
  with a ground polygon on each side, `pours` names both and the ground
  net has tracks only where a pour left it apart.
* `pcb_autoroute_apply` places exactly the `tracks` and `vias` counts the
  result lists, names the checkpoint it took, and when the job relied on
  planes or pours answers `repoured: true` with the polygons repoured
  round the new copper (`repour=false` leaves them alone).
* Then `pcb_run_drc`: Altium's own count of clearance violations and
  unrouted nets should match the job's summary. A net joined by a pour
  that Altium leaves unjoined means its repour and the engine's differ
  (a thermal relief or a neck width): write down where.
  A difference is a finding about the engine's DRC or its board read;
  write it down with the rule it came from.

Taking the routing up first, on a saved copy of a public example board:

    obj_count  object_type=eTrackObject  filter=InComponent=false|InPolygon=false|Layer=TopLayer
    obj_count  object_type=eTrackObject  filter=InComponent=true
    pcb_unroute  expect_file=<the copy's full path>

* The two counts differ from each other and from an unfiltered count:
  footprint copper and free copper are told apart. `obj_query` with
  `properties=Component,Locked,Kind` answers them all; `Kind` used to be
  refused as unknown.
* `pcb_unroute` reports tracks, arcs and vias removed. Afterwards
  `pcb_get_board_statistics` shows no free routing left, every footprint
  is intact (its pads and silkscreen drawn as before), polygons and
  keepouts are still there, and a track locked beforehand
  (`pcb_lock_net_routing`) is still there and counted in `kept_locked`.
* `pcb_unroute nets=["NOT_A_NET"]` answers with an error and removes
  nothing; with another board focused it refuses the same way.
* `obj_delete` with `items` of a board type (`eViaObject`,
  `filter=InComponent=false|Net=<a net>`) deletes them and reports a
  `results` row with the count; a board type with `scope=project` is
  reported as `scope_not_supported`, not skipped in silence.
  `obj_modify` with `items` takes the same board types.
* Undo (Ctrl+Z) in Altium after `pcb_unroute` puts the routing back.
* `obj_query object_type=eConnectionObject properties=Net,X1,Y1,X2,Y2,Layer1,Layer2`
  lists one row per ratsnest line, as many as `pcb_get_unrouted_nets`
  counts, each with both ends and their layers filled in.
* On a board whose nets are all joined (Altium's own DRC reports no
  un-routed net), `pcb_get_unrouted_nets` may still list stored lines.
  Read them with `properties=Net,IsRedundant,Mode` and note both values
  per line; then `pcb_get_unrouted_nets reanalyze=true` and note whether
  the count drops to 0. Both calls must answer: a halted polling loop here
  means the script engine does not know IsRedundant, Mode or AnalyzeNet.

Placement next, on the same copy:

    pcb_autoplace        expect_file=<the copy's full path>
    design_job_status    job_id=<from the reply>        until done
    design_job_result    job_id=<same>
    pcb_autoplace_apply  job_id=<same> expect_file=<same path>

* `pcb_autoplace` with another board focused answers with an error and
  starts no job; a designator in `parts` that is not on the board is
  named in the error.
* The job's `summary` reports parts moved and fixed, wirelength before
  and after, and the engine's DRC count on the placed board. Locked
  parts and connectors, mounting holes and test points are not in
  `moves`.
* `pcb_autoplace_apply` answers `moved` equal to `expected` and names
  its checkpoint. Read the parts back: each sits at the whole-mil x, y
  and rotation the job listed, on the side it started on.
* Then `pcb_autoroute` and its apply as above, and `pcb_run_drc`.
  Component clearance violations Altium reports on the placed board
  that the job's summary did not are a finding about the placer's
  body outlines: write down the two parts.

---

## 21. Closing one document by its path

`app_close_document` closes the document it is given, found by its full
path, not whatever has the focus: `WorkspaceManager:CloseObject` acts on
the focused object and once closed a different project than the one
named. It calls `Client.CloseDocument`, which the reference scripts use;
nothing in this codebase called it before, so an undeclared identifier
here would stop the polling loop.

On scratch documents only:

1. Open two scratch boards; focus the FIRST.
2. `app_close_document file_path=<the SECOND's full path>`. It answers
   `closed: true`, the second is gone from `app_list_documents`, and the
   first is still open and still focused.
3. `app_close_document` on a path that is not open answers `NOT_LOADED`
   and closes nothing.
4. Place anything on the remaining scratch board without saving, then
   `app_close_document` on it: it answers `MODIFIED` and the board stays
   open. If the modified read misses the edit and Altium raises its save
   prompt instead, record it: the refusal is a floor, not a guarantee.
5. The same call with `discard_changes=true` closes it with no prompt,
   answers `discarded: true`, and the file on disk is unchanged (compare
   its hash before and after).

---

## 22. Board tools from a full board run

Nine findings from one board driven end to end. On a scratch board and a
scratch schematic only. Every Altium member below is declared in the
API reference or used by a working reference script; none is new to
Altium, so the risk is behaviour, not a halted loop. The two least
exercised are `BoardOutline.SetState_XSizeYSize` and
`Board.UpdateBoardOutline` (step 4).

1. **Filters refuse what they cannot apply.** `obj_delete
   object_type=eTrackObject filter="OnLayer('TopLayer')"` answers
   BAD_FILTER and deletes nothing; `obj_count` before and after agree.
   `obj_query object_type=eTextObject filter="IsDesignator"` answers
   BAD_FILTER with the hint `IsDesignator=true`, and with that filter it
   returns designators only, no pin-number or free texts.
2. **Text size.** `obj_query object_type=eTextObject
   properties="Text,Height,StrokeWidth,IsDesignator"` reads all four.
   `pcb_set_text_style height_mils=31.5 stroke_mils=3.94
   designators=["<one part>"]` answers `changed: 1` and the text reads
   back at those values; the designator is visibly smaller. Repeat with
   `obj_modify ... set="Height=40"` on a free text. `pcb_place_text
   height=31.5 stroke=3.94` reads back the same. A designator not on the
   board comes back in `not_found`.
3. **pcb_delete_object on a track's length.** Place a track
   (1300,1300) to (8250,1300) on a scratch layer and delete at
   (2000,1300): it goes, `distance_mils` near 0, `bbox_mils` is that
   track.
4. **Outline extents after a reshape.** `pcb_set_board_shape` to a
   larger rectangle, then `pcb_get_board_outline`: `bounding_rect`
   matches the vertices. `pcb_render_svg` shows the whole board. Then
   `app_ping` answers.
5. **audit_find_acute_angles returns.** On a board with long tracks it
   answers parseable JSON; each acute join is listed once.
6. **pcb_autoplace_silkscreen keeps clearance.** On a board with no Silk
   To Silk violations, the call answers `placed: 0` and every designator
   `already_clear`; DRC still shows none. Move one designator onto a
   neighbour's outline by hand: the call moves only that one, or lists it
   in `unplaced_designators` and leaves it where you put it. The reply
   names the rules its clearances came from.
7. **app_click_menu checks what it clicked.** `app_click_menu
   menu_path="Tools|Update From PCB Libraries"` (no ellipsis) either
   opens the update wizard or fails naming the item it found; it never
   reports ok with SI Setup Options open. `app_update_from_libraries
   launch="menu"` opens the right wizard.
8. **WPF buttons.** Open Tools > Silkscreen Preparation and
   `app_invoke_element name="Cancel"`: the dialog closes and the reply
   says `dialog_closed: true`. `app_press_dialog_button` on the same
   button answers `method: "uia"`.
9. **route_plan joins what it reports routed.** Place a route_plan
   result verbatim: Altium's DRC shows no Un-Routed Net or Net Antennae
   for the nets reported `routed`, and each `validation.violations`
   record names its two layers.

Verified on AD 26.10.1.6 with the previous script, build 2 of 2026.10.02
(scratch board), through the script side: step 1 (a bare `IsDesignator` filter refused with the
`=true` hint; an `OnLayer(...) Or ...` filter on `obj_delete` refused with
all four tracks intact), step 2 for a free text (Height 31.5 and
StrokeWidth 3.94 written and read back under both names; `Height=abc`
reported failed, text unchanged), step 3 (a point 3800 mil from a track's
midpoint deleted it at distance 0, with its bbox), step 4 (a reshape to
8000 x 5400 read back the same bounding_rect, the render covered it, and
app_ping answered after the outline refresh), step 5 (one acute corner of
24 degrees reported once, a 90 degree corner not). The silkscreen placer
read both clearances from the board's rules (10 mil each). Still to run:
steps 2 and 6 on parts, and steps 7 to 9, which need the MCP server
restarted on this tree.

---

## 23. Vias keep an annular ring, and take a template from another via

`pcb_normalize_vias` read a template-based Routing Via rule's size fields
and set nearly every via on a board to a pad no larger than its hole.
`obj_modify` could not repair them. On a scratch board only:

1. **Template rule refused.** Give the board a Routing Via rule in
   template mode, then `pcb_normalize_vias dry_run=true`: every via counts
   under `refused_template_rule`, `changed` is 0 and `note` points at
   `pcb_apply_via_template`. Without `dry_run` nothing changes either.
2. **Explicit sizes.** `pcb_normalize_vias size_mils=23.622
   hole_mils=11.811 from_size_mils=<one group>`: only that group changes,
   `before` and `after` count it, and `obj_query eViaObject
   properties="Size,HoleSize"` reads 23.622 and 11.811.
   `size_mils=12 hole_mils=12` is refused before anything is sent.
3. **obj_modify on a via.** `set="Size=30|HoleSize=15"` on a 20/12 via
   writes both; `set="HoleSize=40"` on it is reported failed and the via
   is unchanged. `set="TopXSize=50"` on a via is reported unknown, not
   success.
4. **Template copy (highest risk).** Set one via's template in the
   Properties panel, then `pcb_apply_via_template source_x=<x>
   source_y=<y> dry_run=true`, then without `dry_run`. Every other free
   via reads the source's size and hole, and the Properties panel shows
   the same template on a sample of them. `TemplateLink` is used by one
   reference script and nowhere in this codebase; an undeclared one
   would stop the polling loop, so `app_ping` after the call.
5. **Routing Via sizes.** On a rule not in template mode,
   `pcb_set_rule_properties name=<rule> preferred_via_size_mils=24
   preferred_via_hole_mils=12` answers `via_sizes_written: true` and the
   rule editor shows the values. A preferred hole at or above the
   preferred size is refused with nothing written.

Verified on AD 26.10.1.6 with the previous script, build 2 of 2026.10.02
(scratch board): step 3
(`Size=30|HoleSize=15` grew a 20/12 via and read back; `HoleSize=40` on it
reported failed, via unchanged; `TopXSize` on a via reported unknown), and
step 2's rule path (`pcb_normalize_vias` set two vias to the min/max rule's
50/28 with `before` and `after` counts, app_ping answered after). Still to
run: step 1 (needs a rule in template mode), step 2 with explicit sizes,
steps 4 and 5, which need the MCP server restarted on this tree.

---

## 24. lib_search says what it searched

`lib_search` reads open .SchLib files only, and an empty result used to
read as "no such part" even with a database library installed. Python
only: no script change.

1. With a DbLib (or an IntLib) installed, `lib_search query=<anything
   absent>` answers `count: 0`, lists the open .SchLib files under
   `libraries_searched`, and lists the DbLib under `not_searched` with
   `library_type: "database"` and a `coverage_note`.
2. With `library_path` set, `libraries_searched` is that one path and the
   reply carries no `not_searched`.

---

## 25. Database libraries: read, search, one record, place

A DbLib is a .DbLib file naming a database and its tables; each row is a
part. Four new bridge commands in Library.pas read it: `get_dblib_info`,
`query_dblib`, `get_dblib_record` and `place_dblib_component`, behind
`lib_dblib_info`, `lib_dblib_search`, `lib_dblib_get_record` and
`sch_place_dblib_component`. `lib_search` now searches every installed
DbLib too. The table definitions are read from the .DbLib file inside the
handler; the rows are read over ADO, which nothing in this codebase has
used before.

**This is the highest-risk step in the release.** Every ADO member below is
new here and comes from one reference script
(`reference/altium-delphiscripts-brett/common/libADOQuery (clean).pas`):
`TADOConnection`, `TADOQuery`, `ConnectionString`, `LoginPrompt`,
`Connected`, `Connection`, `SQL.Add`, `Open`, `First`, `Eof`, `Next`,
`Close`, `FieldCount`, `Fields[I].DisplayName`, `Fields[I].AsString` and
`Parameters.ParamByName(...).Value`. Placement adds
`LoadComponentFromDatabaseLibrary` (declared in the schematic API, used by
`reference/altium-delphiscripts-brett/Sch/CompPlaceFromLib.pas`) and reads
back `DatabaseTableName`. An undeclared one stops the polling loop with a
dialog no `Try/Except` catches, so run `app_ping` after every item, and
stop at the first item that does not answer.

You need an installed DbLib whose database this machine can open (an
Access .mdb or .accdb is the simplest), one key value from it, and a
scratch schematic sheet. Item 6 writes to that sheet: `app_checkpoint`
first.

1. **File only, no ADO.** `lib_dblib_info library_path=<dblib>
   with_fields=false`. Expect every table the DbLib editor shows, with its
   `enabled` flag; `connection.kind` and `provider` right for the database;
   `has_password` true only if the connection has one, and
   `connection.redacted` showing `***` where it was; `connected: null`.
   Record each table's `key_field_source` and the keys under `settings`:
   the file keys for the key column (`Key`, `UserWhere`) are inferred, not
   documented, and this is where the guess is checked. `app_ping`.
2. **ADO connection (highest risk).** The same call without `with_fields`.
   Expect `connected: true` and each enabled table's `fields` listing the
   columns the DbLib editor's field mappings show, `key_field_found: true`,
   and `symbol_ref_field` / `footprint_fields` matching its [Library Ref] /
   [Footprint Ref] mappings. Repeat with the DbLib open in the Libraries
   panel: Access is then held Share Deny Write by Altium, and the Mode=Read
   connection must still open. `app_ping`. If the loop stops here on
   `TADOConnection`, this Altium does not expose ADO to DelphiScript:
   record the version and skip to item 7.
3. **Search.** `lib_dblib_search query=<part of a known part number>`:
   hits carry `table`, `key`, `symbol_ref`, `footprints` and
   `matched_field`, and `rows_scanned` is above 0. A query that matches
   nothing gives `count: 0` with every enabled table under
   `tables_searched`. `fields=["<a real column>","Nonsense"]` searches the
   one and lists `Nonsense` under `unknown_fields`. `query="10%'"` answers
   without error and matches only that literal text. `app_ping`.
4. **One record.** `lib_dblib_get_record library_path=<dblib>
   table=<table> key=<a key from item 3>`: `found: true`,
   `lookup: "parameter"`, and `fields` holds every column with the values
   the database shows. A key that is not there gives `found: false`, not
   an error. `app_ping`. A `lookup` of `"scan"` means the parameterised
   query failed on this provider: the answer is still right; record the
   provider.
5. **lib_search.** `lib_search query=<the same text>`: the DbLib rows
   are in `results` with `source: "dblib"`, the DbLib is in
   `libraries_searched` and not in `not_searched`, and `dblib_searches`
   reports it searched. With `include_dblibs=false` the reply is step
   24's: the DbLib under `not_searched`, its reason naming the switch.
6. **Placement (highest risk, edits the sheet).** On the scratch sheet,
   `sch_place_dblib_component library_path=<dblib> table=<table> key=<key>
   x=1000 y=1000 rotation=90 designator=U99`: `placed: true`,
   `database_linked: true`, its library reference the symbol from item 4. The part
   is on the sheet at (1000, 1000), turned 90 degrees, with the record's
   parameters, and Update From Libraries sees it as a DbLib part. Nothing
   is left on the cursor. `app_ping`. If the call times out, look for an
   Altium dialog with `app_list_open_dialogs` before anything else.
   Uninstall the DbLib and repeat: `NOT_AVAILABLE`, sheet unchanged. A
   table the DbLib does not declare: `TABLE_UNKNOWN`, sheet unchanged.
7. **Credentials.** With a DbLib that has a password (a SQL Server login
   or a password-protected .accdb), no reply carries it:
   `connection.redacted` reads `Password=***`. Point a copy of that DbLib
   at a server that does not exist: `CONNECT_FAILED`, and neither the
   error nor anything in `libraries` quotes the connection string.
8. **A part's link through obj_query.** `obj_query eSchComponent
   properties="DatabaseTableName,DatabaseLibraryName,DesignItemId"` on the
   sheet from item 6 reads the table, the DbLib's file name and the key.
   On a part from a .SchLib the first two read empty; on any other object
   type they are listed under `properties.unreadable`.

Verified on AD 26.10.1.6 (scratch folder, Access .accdb through ACE OLE DB
12.0, a two-table sample DbLib with a .SchLib and .PcbLib made by this
server's own library tools): items 1 to 6, with `app_ping` answering after
each. ADO works from DelphiScript: the connection opened read-only and
listed every column; a search containing SQL matched nothing and did no
harm; the record was found with `lookup: "parameter"`; lib_search found
a part by its manufacturer part number in the DbLib; and the placed part
carried its database link, key and every mapped column (Value visible,
the rest hidden), at the location and rotation asked. Two findings from
the run: Altium's own DbLib layout keeps the connection in
`[DatabaseLinks]`, keys each table by its `UserWhereText`, and puts every
field mapping in a `[FieldMapN]` section (a first sample with the
connection in a `[DataSource]` section was parsed by this reader but
Altium found no connection and placed nothing); and Altium's component
count for a DbLib is not its row count (0, then 1, for five rows), so
`lib_get_installed_libraries` reports it as unknown. Item 7 is not run.
Item 8 is new in the following build.

---

## 26. Fixes from a second full board run

Results that looked right and were not, from a board taken end to end on
a Spanish Altium. On scratch documents only. New identifiers, all from
Altium's API reference or two independent reference scripts:
`SystemOptions.PolygonRepour` and `eAlwaysRepour` (documented),
`SetState_CopperPourInvalid` (PolygonReFitBO.pas), `PourIndex` and
`Poured` (PolygonBenchmark.pas and PCBObjectInspector.pas), and
`IPCB_Region.Area` (documented). Any of them undeclared stops the loop at
the first repour or polygon read, so run item 2 before relying on the rest.

1. **Exact vias.** `pcb_place_via x=1000 y=1000 size_mm=1.2
   hole_size_mm=0.6`, then `obj_query eViaObject properties="Size,HoleSize"`:
   47.244 and 23.622 mil (1.2 and 0.6 mm), not 47 and 24. `size=20
   hole_size=20` is refused with `BAD_SIZE` and places nothing.
2. **Repour.** A scratch board with a solid pour that runs past the board
   edge and a Board Outline Clearance rule. `pcb_get_polygons`: note
   `copper_area_mm2` and `copper_pieces`. Raise the rule's clearance,
   `pcb_repour_polygons`: `repoured` true, `rebuilt` 1, the item's
   `copper_area_mm2_after` below `_before`, no yes/no prompt, and
   `pcb_get_polygons` reads the new area while `area_mm2` (the outline)
   is unchanged. Shelve the polygon and repour: `shelved` 1, `repoured`
   false, nothing poured. `app_ping` after each.
3. **STEP export.** With a schematic tab focused, `proj_export_step
   output_path=<scratch>\board.step`: the board is focused, and either
   `generated` true with the file there, or `generated` false with
   `dispatched` true and nothing claimed. Record which; false means
   `PCB:ExportSTEP3D` does not take `FileName` on this build.
4. **ECO direction.** A scratch project whose schematic has one changed
   footprint. With the SCHEMATIC focused, `proj_sync_pcb`: the board is
   focused and the ECO dialog lists the PcbDoc, not the SchDoc, as the
   affected document. Press Close without executing, and confirm the
   schematic is unchanged.
5. **Replicated part.** On a sheet with a dual op-amp `U1` part A,
   `sch_replicate_component designator=U1 part_id=2`: `shared` true. Move
   both parts with the mouse: no "An item with the same key has already
   been added". Save, close and reopen the sheet: both parts are there,
   once each.
6. **Pads near a round edge.** A round scratch board with a pad turned
   329.5 degrees 0.5 mm inside the edge. `audit_find_pads_near_board_edge
   clearance_mils=118` lists it at about 19.7 mil, and `unmeasured` is 0.
7. **Outline render.** `pcb_render_svg` on the same board draws a full
   circle, with no straight edge across any quadrant.
8. **Footprint target.** In a scratch PcbLib with footprints A and B, B
   open in the editor: `lib_add_footprint_tracks footprint_name=A` puts
   the tracks in A (`lib_probe_footprint` on both), and a name that does
   not exist is refused with nothing written.
9. **3D body origin.** `lib_link_3d_model component_name=A` with no
   offset: `moved_to_footprint_origin` true, and after a save and reload
   the body sits on A's origin, not 50000 mil away.
10. **Footprint copy.** `lib_copy_footprint source_name=A new_name=A2`
    in the same library with A open in the editor: `primitives_realigned`
    above 0, and after a save and reload A2's pads read the same
    coordinates as A's (`lib_get_pad_geometry`).
11. **Copper less its holes.** On item 2's board, put the pour on a net
    (`pcb_modify_polygon net=GND`) with an unnetted via inside it and
    repour: `copper_area_mm2_after` drops by the cleared ring and
    `copper_area_exact` is true. Reads `Region.MainContour`,
    `Region.HoleCount`, `Region.Holes` and a contour's `X`/`Y`
    (Hyperlynx_Exporter.pas), new here.
12. **Pad name and shape.** `pcb_place_pad shape=rectangular name=P1`
    places a rectangular pad, and `obj_query ePadObject
    properties="Name"` reads `P1` (it read empty). `shape=square` is
    refused with `BAD_SHAPE` and places nothing.

Verified on AD 26.10.1.6 with the script of 2026-10-05, build 2, on a scratch board and
library kept out of every project: item 1 (47.2441 and 23.622 mil read
back; 20/20 refused, nothing placed); item 2 (no prompt, `app_ping`
answered, and after the pour was put on GND the repour cleared the
unnetted via, seen on screen); item 3 (`generated` false with `dispatched`
true, no dialog and no file, so `PCB:ExportSTEP3D` takes no file name on
this build); item 6 on a rectangular board, since no tool draws a round
outline (a 47.2 mil via 500 mil in at 476.38 mil, and an obround
12 x 45 mil pad turned 329.5 degrees at 9.78 mil, which is its end cap);
item 8 (FP_A got its tracks with FP_B current; an unknown name refused);
item 9 (the footprint origin read 50000,50000 mil and the body sat on it);
item 10 (A2's pads at plus and minus 1.27 mm like A's, from a fresh read
but not after a save and reload). Not run: items 4 and 5 need a person at
the editor, item 7 a round outline. Found on the way, and items 11 and 12
in the following build: `copper_area_mm2` stayed at the outline's area
because `IPCB_Region.Area` is the outer contour only; a pad's `Name`
read empty through `obj_query`; and `pcb_place_pad` turned
`shape=rectangular` into a round pad. Also open: a same-library copy
of a footprint with 4 primitives counted 6.

Items 11 and 12 verified on AD 26.10.1.6 with the script of 2026-10-06,
build 1, on the same scratch board: a GND pour over an unnetted 50 mil
via read `copper_area_mm2` 410.405 against an outline of 412.902, the
ring of a 25 mil radius plus 10 mil clearance, with `copper_area_exact`
true and the loop answering afterwards; `shape=rectangular` placed a
rectangular pad whose `Name` read `P1` and matched a `Name=P1` filter,
and `shape=square` was refused with nothing placed.

---

## 27. Lengths in millimetres, and a new document in front

Footprint and pad tools took whole mils only, so a metric land pattern
came out up to 12.7 um off its grid. `units="mm"` now carries lengths as
given. On scratch documents only.

1. **A new document is focused.** With another PcbLib in front,
   `app_create_document kind=PCBLIB` on a scratch path: `focused` true,
   and `app_get_active_document` names the new file. A path in a folder
   that does not exist is refused with `NOT_SAVED`, naming the folder.
2. **Pads in mm.** In the new library, `lib_create_footprint`, then
   `lib_add_footprint_pads units=mm` with pads at x 1.625 and -1.625,
   0.3 x 1.15: `lib_get_pad_geometry` reads 1.625 and 0.3 x 1.15 mm,
   not 1.6256 and 0.3048. A pad with no size reads 1.524 mm (60 mil).
3. **Tracks, arc and text in mm.** `lib_add_footprint_tracks units=mm`
   with a 0.12 mm width, `lib_add_footprint_arc units=mm radius=0.5` and
   `lib_add_footprint_text units=mm size=1`: each lands at the size
   given. `units=inch` is refused on every one with nothing written.
4. **Mils unchanged.** The same calls without units place what they did
   before: 60 mil pads where no size is given, whole mils exact.
5. **Board pad in mm.** On a scratch board, `pcb_place_pad units=mm
   x=12.7 y=25.4 shape=rect x_size=0.3 y_size=1.15`: `pcb_get_pad_properties`
   reads 500 and 1000 mil, 11.811 x 45.276.

Verified on AD 26.10.1.6 with the script of 2026-10-06, build 1, on a
scratch library and board kept out of every project, with a client sheet
in front at the start: item 1 (`focused` true and the new library active;
a missing folder refused, named); item 2 (pads at -1.62500056 and
1.62500056 mm, 0.2999994 x 1.15000024 mm, each within one internal unit;
the unsized pad 1.524 mm); item 3 (track, arc and text created in mm and
`inch` refused with nothing written; their sizes have no readback tool,
and they go through the same conversion as the pads); item 4 (plus and
minus 1.27 mm, 0.762 x 1.016 mm, default 1.524 mm, as before); item 5
(63.5 and 50.8 mm read 2500 and 2000 mil, 0.3 x 1.15 mm read 11.811 x
45.2756 mil, rectangular). The first attempt at item 2 came back rounded
to whole mils because the MCP server was still running the previous
Python; a server restart is part of loading this change.

## 28. A simulation runs, and a dialog press presses what it names

`sim_run` sent `Sim:RunMixedSim`, which AD 26 does not run: nothing
opened, nothing was written, and the tool reported success. It now runs
the project's simulator output, the way the schematic's Simulate menu
does, and succeeds only when a results document appears. Separately,
`app_press_dialog_button` asked for "Don't Save" on the WPF "Unsaved
Changes" prompt pressed Save. Item 3 is Python, so it needs the MCP
server restarted. On scratch documents only.

1. **A simulation runs and reads back.** Copy one sheet of Altium's
   "Simulation Quick Start" example into a scratch folder, then
   `proj_create`, `proj_add_document`, `proj_load_sheets`, activate the
   sheet and call `sim_run`: `ran` true, `results_document` names
   `Simulation\<project>.sdf`, `results_saved` true with the file on
   disk and no Save As dialog, `results` holds an Operating Point with
   v(in) about 2.52 V, and the focused document is the sheet again.
2. **No run, no success.** With a scratch PcbLib focused and no
   schematic project, `sim_run` returns `SIM_NOT_RUN` and nothing opens.
3. **Settings reach the next run.** With the item 1 project still open,
   `sim_setup_analysis transient={"stop":"2u","step":"10n"}
   outputs=["v(out)"]`: `reloaded` true and no "Reload?" prompt. Then
   `sim_run` and `sim_get_results analysis="Transient Analysis"`: the
   time wave ends at 2 us. Change it to 3u and repeat without closing
   the project: it ends at 3 us. This is the step where `DoFileLoad`,
   `SupportsReload` and `UpdateModifiedDate` first run; if the loop stops
   at it, one of them is not available to scripts.
4. **Don't Save presses Don't Save.** Close a scratch project whose
   settings file Altium changed in memory, so that "Unsaved Changes"
   lists it. Answer with `app_press_dialog_button button_caption="Don't
   Save" dialog_title="Unsaved Changes"`: `method` is `uia`, the dialog
   closes, no Save As dialog follows, and nothing is written.

Measured by hand on AD 26.10.1.6 before the change, through
`obj_run_process`: `Sim:RunMixedSim` left the scratch folder unchanged;
`WorkspaceManager:GenerateReport` with `ObjectKind=Simulator|Index=1|Action=Run`
opened `Simulation\SimOne.sdf` with an operating point (V(in) 2.524 V); an
`.nsx` netlist opened as a document and run with `ADVSIM:RunSimulator`
produced a transient and an operating point; and the old press sent Enter,
which opened "Save [project_sim_config.simcfg] As...", cancelled by hand.
Also measured: a hand-written `.simcfg` added to a project is read at
open (its transient stop time and AC and DC values were used), but a run
from a script did an operating point, DC sweep, transient and AC every
time whatever its on/off flags said; and an edit made while the project
was open was not used, raised "has been modified ... Reload?", and was
later overwritten by Altium's own copy. The handlers themselves have not
run yet.

## 29. Footprints from a package drawing

`lib_create_footprint_from_package` builds a whole footprint from a
datasheet package description: IPC-7351B lands, paste windows, silkscreen
with a pin 1 dot, assembly outline and `.Designator`, courtyard and
height, then reads the pads back and fails on any difference. It needs
two new handlers (`add_footprint_regions`, `get_mech_layers`) and four
optional fields on `add_footprint_pads`, so the script must be the new
build and the MCP server restarted. On a scratch PcbLib only, kept out of
every project. The package specs below are the datasheet cases the engine
is tested on; print one as JSON with
`python -c "import json; from tests.design._landpattern_cases import soic8; print(json.dumps(soic8().to_dict()))"`
(and `qfn16` for item 3).

1. **Layers by role, or a refusal.** `app_create_document kind=PCBLIB` on
   a scratch path, then `lib_create_footprint_from_package` with the
   SOIC-8 spec and no layer arguments. Either `layers` names the
   assembly and courtyard layers with the rule that chose them (a kind,
   or a name), or the call refuses, lists the library's mechanical
   layers, and `lib_get_footprints` shows nothing was created. If it
   refused, run `lib_set_mech_layers` with Mechanical13 named "Top
   Assembly", kind "Assembly Top", and Mechanical15 named "Top
   Courtyard", kind "Courtyard Top", and repeat: `layers.assembly.rule`
   now names the kind.
2. **SOIC-8 (TI D0008A).** The repeat of item 1: `ok` true,
   `readback.ok` true with 8 pads read. `lib_get_pad_geometry` reads pad
   1 at -2.45, 1.905 mm, every pad 1.95 x 0.6 mm, `roundrectangle` with
   `corner_pct` 50, within one internal unit, and no manual paste or mask
   expansion. In the editor: silkscreen on Top Overlay clear of every pad
   with a filled dot by pad 1, the body outline with its pin 1 chamfer
   and a `.Designator` on the assembly layer, a rectangle on the
   courtyard layer enclosing everything, and the footprint height 1.75 mm.
3. **QFN-16 with an exposed pad (TI RGT0016C).** `ok` true, 17 pads read
   back. Pad 17 reads `paste_expansion_source` manual and a negative
   value equal to its larger side. With only Top Paste visible: four
   windows inside pad 17 and no opening the size of the pad. Every other
   pad keeps its own rule-driven paste. This is the first region placed
   in a footprint; if the windows are missing after a save and reload,
   the registration did not take.
4. **Slot, non-plated hole and a mask expansion.** A custom spec with a
   slotted pad and a non-plated hole, and `drafting={"mask_expansion":
   0.05}`:
   `{"family": "custom", "name": "SLOT_TEST", "body_x": 6, "body_y": 4,
   "custom_pads": [{"number": "1", "x": -1.5, "y": 0, "width": 2.4,
   "height": 1.4, "shape": "oval", "hole": 0.8, "hole_length": 1.6},
   {"number": "MH", "x": 1.5, "y": 0, "width": 1.0, "height": 1.0,
   "shape": "circle", "hole": 1.0, "plated": false}]}`.
   `lib_get_pad_geometry`: pad 1 `hole_type` slot, `hole_mm` 0.8,
   `hole_w_mm` 1.6; pad MH `plated` false; both `mask_expansion_source`
   manual at 0.05 mm. This is where `HoleType`, `HoleWidth`, `Plated` and
   the solder mask cache fields first run; if the loop stops here, one of
   them is not available to scripts, and the reply's `property_failures`
   names a write Altium refused without stopping.
5. **The mils rounding fix.** `lib_create_standard_footprint name=R_FIX
   family=chip pin_count=2 pitch=59.055 pad_w=31.496 pad_h=39.37
   body_w=78.74 body_h=49.213`. Select a silkscreen track: its ends sit
   at x = 39.37 mil either side, not 39. There is no track readback tool,
   so this one is read in the Properties panel.
6. **Refusals that write nothing.** Repeat item 2: refused, "already in
   the library". `lib_add_footprint_regions` with a region that has no
   layer: refused before sending. With `layer=Mechanical13` and a
   triangle: `added` 1.

Not run yet: none of these handlers or writes has executed in Altium. The
EasyEDA and KiCad twins are not part of this step; the KiCad one writes a
file that its own reply parses back, and the EasyEDA one needs a live
editor to check whether its footprint canvas is Y up.

## 30. Footprint validation

`lib_audit_footprint_vs_package` audits a footprint that already exists
against its package description: pad count, numbering, pin 1 and mirror
images, pitch, span, land sizes, land over terminal, paste, mask, origin,
and from the footprint's other primitives the courtyard, silkscreen,
assembly outline and height. It needs one new handler,
`get_footprint_primitives` in Library.pas, which READS only: the
footprint's tracks, arcs, regions (bounding box and area), fills and texts
with their layers, in mm, and its height. Every property it reads is
already read in shipped code: Track `X1`, `Y1`, `X2`, `Y2`, `Width` and
Arc `XCenter`, `YCenter`, `Radius`, `StartAngle`, `EndAngle`, `LineWidth`
(GetPCBProperty, PCBGeneric.pas), `Region.Area` (RegionCopperSqMils,
PCB.pas), `BoundingRectangle` on a region (Audit.pas) and on a primitive
(PCB.pas), the text reads of Lib_GetLibraryGeometry with `Rotation`
(Lib_ProbeDesignator), and the footprint's `X`, `Y` and `Height`. Reading
does not make it risk-free: an identifier this build rejects still stops
the loop, so run `app_ping` after item 1. The script must be the new build
and the MCP server restarted. `datasheet_fetch`, `footprint_check_package`
and `footprint_render_package` are Python only. On the scratch PcbLib of
step 29 only, with its SOIC-8 and QFN-16 footprints in it.

1. **The read.** `lib_audit_footprint_vs_package footprint_name="SOIC-8
   (D)"` with the same SOIC-8 spec as step 29: `ok` true, no findings,
   `not_examined` empty, and `primitives_read` counting the silkscreen,
   assembly and courtyard tracks and the pin 1 arc, with `height_mm`
   1.75. `app_ping` answers. If the loop stops here, one of the reads
   above is not available on this build: record which footprint was open.
2. **Paste windows and their area.** The same call on QFN-16 with the
   `qfn16` spec: `ok` true, and `notes` says 4 paste windows cover about
   65.8 % of pad 17. A coverage near 0 or near 100 times too large means
   `Region.Area` is not in internal units squared on this build.
3. **Defects are seen.** `lib_copy_footprint` the SOIC-8 to SOIC-8-BAD in
   the same library. On the copy, delete the courtyard tracks with
   `lib_delete_footprint_primitives`, and draw one Top Overlay track from
   (-3, 1.905) to (-1.5, 1.905) mm, 0.2 mm wide, with
   `lib_add_footprint_tracks units=mm`. The audit of SOIC-8-BAD reports a
   `courtyard` error (no courtyard) and a `silk` error on pad 1, and
   nothing else. The original still audits clean.
4. **Height and origin.** `lib_set_footprint_height` the copy to 0: a
   `height` warning, "no height". Move every primitive of the copy 0.5 mm
   right in the editor: an `origin` warning of 0.5 mm, `ok` still true.
5. **A turned footprint.** Rotate a third copy a quarter turn in the
   editor (select all, then rotate): `ok` true and
   `alignment.rotation_deg` 90 for a counter-clockwise turn, 270 for a
   clockwise one.
6. **Python only.** `footprint_check_package` on the SOIC-8 spec: `ok`
   true and `cross_check.ok` true. With the spec's `pitch` set to 4.9: a
   `row_fits_body` error naming the pitch and body_y. `footprint_render_package`
   writes an SVG (and a PNG where Edge or Chrome is installed) showing the
   lands with dashed green terminals over them. `datasheet_fetch
   url=https://www.ti.com/lit/ds/symlink/lm358b.pdf part_number=LM358B`:
   `ok` true, `cache` miss, a `path` under the workspace's `datasheets`
   folder that opens as the datasheet, and `pages` close to its page
   count; the same call again answers `cache` hit at once.

Not run yet: the handler has not executed in Altium. The EasyEDA twin,
`easyeda_audit_footprint_vs_package`, reads pads only and is not part of
this step; it needs the same Y-direction check as step 29's EasyEDA tool.

---

## 31. Issue fixes, phase 1 (#41, #39)

Pascal changes, none of which uses an identifier shipped code does not
already use. Nineteen error paths no longer call `PostProcess` before an
`Exit` whose `Finally` calls it again (fourteen in PCB.pas,
`Lib_SetDesignator` three times, `Gen_SetSheetSize` twice), and
`Proj_Annotate` with `order="none"` no longer closes each sheet twice.
`Gen_SelectObjects` resets the property notes, selects, then reads the
selection back with a second query pass; `GetSchProperty` answers
`Selection` for that (the read `Gen_ClearHighlights` already makes).
`obj_create` and `obj_copy` refuse with `SchOnlyTypeMessage`, which lists
schematic types only. `MatchesFilterPCB` compares a length as a number
(`FilterValueMatchesPCB`: `IsLengthProperty`, `IsFloatStr`,
`StrToFloatDef`, `Abs`). `App_Ping` lists the open `Altium_API.PrjScr`
paths (`GetWorkspace`, `DM_Projects`, `DM_ProjectFullPath`, as
`Proj_GetOpenProjects` does). The script must be the new build and the
MCP server restarted. Scratch documents only.

1. **Ping.** `app_ping`: `script_projects_open` lists the script project
   Altium has open, `bundled_script_path` names the Main.pas compared,
   and on a normal install `script_location_match` is true and
   `warnings` is empty. If `script_projects_open` is empty while the
   script project is open, this build does not list script projects in
   `DM_Projects`: record it; nothing else depends on the field.
2. **Error paths close once.** On a scratch board,
   `pcb_create_design_rule name="t" rule_type="bogus"` answers
   INVALID_PARAM; then save the board. It must save without "A command
   is currently active". On a scratch sheet, `sch_set_sheet_size
   style="Q9"` answers INVALID_STYLE; `sch_set_sheet_size
   orientation="landscape"` alone works; save the sheet. On a scratch
   project, `proj_annotate order="none"` resets designators and the
   sheets save. The create-failed paths need a factory returning Nil
   and cannot be triggered on demand.
3. **Select reports what it selected.** On the scratch board,
   `obj_modify object_type=eTrackObject filter="Layer=TopLayer"
   set="NoSuchProp=1"` (reports `properties.unknown`), then
   `obj_select object_type=eTrackObject filter="Layer=TopLayer"`: the
   reply is `matched` N, `selected` N, `success` true, with no
   `properties`, and the tracks show selected. On the scratch sheet,
   `obj_select object_type=eNetLabel`: `selected` equals `matched`.
   `obj_deselect_all` afterwards. If the loop stops on the sheet select,
   reading `Selection` is not available on that object type: record it.
4. **obj_create refusal.** `obj_create object_type=eComponentBodyObject
   properties=""`: INVALID_TYPE, saying obj_create works on schematic
   objects only and naming `pcb_place_3d_body`, with no PCB type in the
   list that follows.
5. **Decimal filters.** On the scratch board, place a track whose end
   sits on a fraction of a mil, read its `X2` with `obj_query`, then
   query with `filter="X2=<that value>"`, the same with a trailing zero,
   and the same with a comma for the point: one track each time, and no
   script dialog. Repeat on a machine set to a comma decimal separator if
   one is at hand. If a dialog appears, record its exact text: the filter
   is not written into script text anywhere, so it comes from elsewhere.

Python only, same session: `pcb_create_design_rule name="t"
scope="IsPad AND HoleSize > 3"` answers BAD_SCOPE with `suggested_scope`
"IsPad AND (HoleSize > 3)" and creates nothing; with the suggested scope
the rule is created and the Rules editor does not call it incorrectly
defined (delete it after). `app_list_panels` with Properties open answers
without a KeyError. `app_open_panel name="Properties"` with it open
answers `opened` false and clicks nothing; with a panel open that
`app_list_panels` does not list, the panel is still open afterwards and
the reply carries `toggled_back`. `app_click_menu` on a Tools path ten
times: any first read that came back empty or wrong shows `retried`. A
second MCP session started on the same workspace shows up in the first
session's `app_ping` and `app_get_status` as `other_servers`, and nothing
is stopped.

Recorded on AD 26.10.1.6 with the previous script build, scratch documents:
items 1 to 5 as written. The bogus rule type was refused and the board and
sheet then saved; `orientation="landscape"` alone needed the tool to pass
`orientation`, which it now does (it had required a style and never sent
one). The select replies read 3 of 3 tracks and 2 of 2 net labels, and
`X2=250.5`, `250.50` and `250,5` each found the one track with no dialog.
The scope refusal and its suggested scope behaved as written, the rule
was created and deleted again; `app_ping` listed other servers. Not run:
`proj_annotate order="none"`, the Rules editor's own verdict, the panel
and menu checks, and a comma-separator machine.

---

## 32. DRC freshness, managed placement, footprint stamp

The script must be the new build and the MCP server restarted.
Scratch documents only.

1. **DRC confirmed by a fresh report.** `PCB_RunDRC` reads the age of
   the board's `.DRC` report before and after the run. On a scratch
   board that already has a `.DRC` beside it, run
   `pcb_run_drc allow_modal=true` and CANCEL the dialog:
   `drc_confirmed` false, with the reason naming a cancelled check.
   Run it again and answer the dialog: `drc_confirmed` true and the
   report's modified time is now. If the second run reads false, check
   that Create Report File is ticked in the checker before suspecting
   the code.
   Then `pcb_run_drc` with no arguments: no dialog appears
   (`app_list_open_dialogs` reads none), `mode` "batch", `drc_confirmed`
   true, `report_path` in the bridge workspace, the focus unchanged, and
   the report's Rule Violations total equals `violation_count`. Recorded
   2026-10-23 on AD 26.11.1.5, scratch board: 0.45 s, 3 violations
   (Un-Routed Net), report 14 KB HTML.
2. **Managed (Workspace) placement.** Needs a signed-in Workspace and
   an open sheet that already carries one managed part, since the
   Workspace name is read off open sheets. `lib_get_vault_name`:
   `found` true and the name Altium shows. `lib_get_vault_component
   design_item_id=<a real ID>`: `placeable` true and non-empty
   `vault_guid` and `item_guid`. Then `sch_place_vault_components` with
   that ID, three placements, on the scratch sheet with
   `allow_manual_layout=true`: `placed` 3, `linked` 3, and every item
   carries non-empty `vault_guid` and `item_guid`. Save, close and
   reopen the sheet: the parts still show as managed in the Properties
   panel. Four placements onto an empty sheet WITHOUT
   `allow_manual_layout` are refused before anything is sent. With
   every managed sheet closed and no `vault`, both tools answer
   VAULT_NOT_RESOLVED. Recorded on AD 26.9.1 before this rewrite, on
   an installation with no file libraries: `LoadComponent(4,
   <Workspace>, <ID>)` returned a linked component and the link
   survived a save, while `FindComponentSymbol` said no for every kind,
   which is why the dry run asks the loader.
3. **Footprint stamp on a placed part.**
   `sch_set_components_parameters stamps=[{"designator":"R1",
   "Footprint":"<another footprint in the same library>"}]` on a
   scratch sheet: `footprints_applied` 1, and the Properties panel shows
   the new footprint with the same library. Compile: no "Missing
   Component Models" for R1. A part with no footprint model lands in
   `footprints_not_applied`.

Python only: `design_bom_file` on a `.PcbDoc` or a missing path is an
error, and a project never compiled (no `.PrjPcbStructure`) is an error
saying so; a sheet with no parts returns an empty BOM with a `warning`.
`design_review_file` refuses the same three. Under WSL,
`design_plan_from_sheet` on a Windows sheet path names the sheet by its
file, so a re-emit lands on the same sheet.

Not run yet: none of this has executed in Altium on this build.

---

## 33. Paging, read-backs, managed lookup, relaunch

The script must be the new build and the MCP server restarted. Scratch
documents only.

1. **Paged violations.** On a board with more than 200 stored
   violations, `pcb_get_clearance_violations`: `returned` 200,
   `has_more` true, `violation_count` the full number. Again with
   `offset=200`: the next page, no repeats.
2. **Truncation flags.** `proj_get_nets limit=10` on any project:
   `limit_hit` true and `count` 10. With the default limit on a small
   project: `limit_hit` false. `proj_get_bom limit=2` the same.
   `skipped_nil` 0 on a healthy project.
3. **Net-class members.** Make a class of two nets with
   `pcb_create_net_class`, then `pcb_get_net_classes`: that class lists
   both nets, `member_count` 2, `probe_errors` 0. `members=false`
   returns names only, faster. Record the time on a large board.
4. **Plane net.** On a scratch board with an internal plane,
   `pcb_set_plane_net layer=InternalPlane1 net=GND`: `applied` true,
   `route` "InternalPlaneNetName", and the Layer Stack Manager shows GND
   on that plane. If `route` reads "LayerObject.Net", note it: the
   documented property did not read back on this build.
5. **DbLib part.** `sch_place_dblib_component` with a multi-part row
   and `part_id=2`: `part_id` 2, `part_id_applied` true, and the sheet
   shows part B. `part_id=99`: placed, `part_id_applied` false.
6. **Pad designator.** `obj_modify` a pad on a scratch board with
   `Designator=7`: the pad reads 7 under both `Name` and `Designator`.
   `Name=` (empty) is refused and the pad keeps 7.
7. **Managed name lookup.** With a sheet open that carries managed
   parts, `lib_find_vault_components name=<a comment on one of them>`:
   every part with that comment is listed once per Design Item ID, with
   a lifecycle state. A name on no open sheet: `count` 0.
8. **Poll timer.** After StartMCPServer the activity log has
   `_session_start` and then `_tick_first`. No `_arm_failed`.
9. **Ctrl+Z while attached.** With the bridge running and a scratch
   sheet focused, move a part by hand and press Ctrl+Z: the move undoes
   at once. Press it twice more, then Detach: nothing further undoes on
   detach. Repeat with the status window minimised. Never measured on
   this codebase before.
10. **Relaunch.** Stop the bridge from the status window, then run
    `eda-agent relaunch --dry-run`: it prints the X2.EXE command. Run
    `eda-agent relaunch`: the status window comes back and `app_ping`
    answers. Run it again: "already answers", nothing started. The
    ProcName form `Dispatcher.pas>StartMCPServer` is the one Altium's own
    menu files use; if the script does not start, record what Altium
    showed.
11. **Several sessions.** Two sessions, A on a scratch board and B on a
    scratch library. B focuses its library; A then reads its board's
    nets without focusing anything: A's reply carries
    `session_document.restored` naming the board, and the nets are the
    board's. Click a third document by hand, then call a tool from A: the
    board comes back. `app_get_active_document` from B answers what is
    really in front; with `session=true` it brings the library back.
    Close A's board and call A again: `session_document.missing`, and the
    command runs on what is in front. Then, with both sessions sending
    reads, run `eda-agent relaunch --restart`: neither session reports a
    timeout, the trace shows `POLL_QUEUED ... restart`, and a session
    whose server started before the deploy gets `server_outdated` once.

Offline only, already covered by tests: the wheel carries the EasyEDA
extension source and `eda-agent easyeda-extension path|build` finds or
builds it; CI compiles everything on Python 3.11 and installs the built
wheel; `validate_requirement` returns board-intent questions in `notes`
without failing; SECURITY.md and the templates match the repository.

Recorded on AD 26.10.1.6 with the previous script build, scratch board: item 3
(class of two nets: both listed, `member_count` 2, `probe_errors` 0, 0.04 s
either way on a board this small), item 4 (`route` "InternalPlaneNetName",
read back), item 6 (the pad reads 7 under both names; `Name=` is not
written and the pad keeps 7, reported under `properties.failed`), item 8
(`_session_start` then `_tick_first`, no `_arm_failed`) and item 10:
`eda-agent relaunch` started the loop in a running Altium whose loop had
stopped, and with the loop answering it started nothing. It cannot start
one while Altium still counts a halted script as running ("Another script
executing now"); after a script halt only a restart of Altium clears that.
Not run: items 1, 2, 5, 7 and 9.

---

## 34. Issue fixes, phase 2 (#36, #46 to #58)

The script must be the new build, deployed with `install-scripts`, and the
script project closed and reopened. Scratch documents only.

1. **Unit stamps (#47).** `app_ping`: `unit_stamps` lists ten units with
   12-character values, `stale_units` empty. Edit a comment in a deployed
   unit, deploy again, re-run StartMCPServer without closing the project,
   and ping: that unit is named in `stale_units`. Close and reopen the
   project: empty again.
2. **Layer names (#46).** On a scratch PcbLib footprint with silkscreen and
   top copper, `lib_delete_footprint_primitives layer="Top Assembly"` (or
   any name the stack does not have) is refused with the layer list, and
   the copper is untouched. A mechanical layer the stack names works.
   `pcb_place_3d_body layer=<unknown>` is refused before anything is made.
3. **Pad read (#48, #50, #58).** `lib_get_pad_geometry` on a footprint with a
   custom pad: `copper_x_mm` is where the copper is and `x_mm` the origin.
   On a slotted pad `hole_rotation` reads, and `slot_aligned` is false for a
   slot whose hole crosses its copper. `mask_expansion_state` reads manual
   on a pad with a manual value.
4. **Pad setter (#54).** `lib_set_pad_properties` on a scratch pad: `plated`
   false leaves the shape alone; `mask_expansion` 0.1 (mm) reads back with
   state manual; `hole_rotation` 90 on a slot reads back 90. Save, reopen:
   the values held.
5. **Footprint vias (#55).** A footprint with a via: `lib_get_footprint_primitives`
   lists it with its diameter and hole.
6. **Save one document (#56).** Edit a scratch sheet while another open sheet
   sits in a read-only folder. `app_save_document` on the scratch sheet:
   `saved` true, and the read-only one is untouched with no dialog.
7. **Opacity (#57).** `obj_modify` an `eComponentBodyObject` with
   `BodyOpacity3D=0.5`: the Properties panel shows 50%, and it reads back
   0.5. `BodyOpacity3D=50` is refused.
8. **Mirror (#36).** Mirror a part whose body has diagonal lines (an op-amp
   triangle) and one with an arc bubble: the triangle points the other way
   and the bubble moves to the other tip, as mirroring by hand does.
   `primitives_skipped` is 0.

Recorded on AD 26.10.1.6 with the previous script build, scratch documents:
item 1 (ten 12-character stamps; a comment added to the deployed Utils.pas
named Utils in `stale_units` with the reload advice), item 2 (both
refusals, nothing created or removed), items 3 and 4 (a slot across tall
copper read `slot_aligned` false, and true once `hole_rotation` 90 turned
it along the copper; mask 0.1 mm read back manual; plated false left the
shape; all held after save and a restart of Altium), and item 6 in part:
`saved` true for the one document. `was_modified` read false on a board
changed only by script, and the file was written all the same. Not run:
item 5 (no tool adds a via to a footprint), item 7 (needs a STEP model),
item 8, and the read-only half of item 6.

The three left waiting on a live measurement, the pad cache's separate
top and bottom mask fields (#59, #61), the via mask write (#60) and text
that could not be selected after an X/Y move (#39.14), were measured and
fixed in step 36. Item 7 ran there too, with a real STEP model: 0.5 read
back and 50 was refused. Item 8 ran there, and found arcs were not moved
at all.

---

## 35. Footprint delete edits from another footprint (#42, #45, #39.16)

The current footprint's primitives live on the library board, which is
written back into the footprint whenever another footprint becomes
current. Measured on AD 26.10.1.6 on a scratch PcbLib:
`lib_delete_footprint_primitives` on the footprint on screen reported
`removed` 6 and all six were back after switching away and back;
`Board.RemovePCBObject` removed nothing from a library board, whether the
primitive came from a list, a board iterator, or was first removed from
the footprint. A remove while another footprint was current lasted
through switches, save and reopen, with the primitive freed, for 25
cycles each on primitives made in the session and on primitives loaded
from disk.

The delete now makes another footprint current when the target is on
screen (a temporary one in a single-footprint library, removed again),
removes and frees each match while its iterator is still open, and shows
the target again. `was_current` reports the switch.

1. **Delete on screen.** With a footprint on screen holding tracks on a
   mechanical layer, `lib_delete_footprint_primitives layer=<that
   layer> confirm=true`: `removed` N, `was_current` true, the same
   footprint is on screen afterwards. Switch to another footprint and
   back: the tracks stay gone. A second run removes 0.
2. **Single-footprint library.** The same in a library of one footprint:
   no extra footprint is left in the library, before or after a save.
3. **Off screen.** The same on a footprint that is not on screen:
   `was_current` false and the footprint on screen does not change.

Recorded on AD 26.10.1.6 with the previous script build: items 1 to 3, ten
cycles each, then save and a restart of Altium: the tracks stayed gone and
the library held only its own footprints. Board deletes (`obj_delete`,
`pcb_delete_object`) measured the same way on a scratch board, 25 cycles:
every delete held through save and reopen and sentinel tracks were
untouched; they are unchanged and do not free what they remove.


---

## 36. Library cleanups, footprint adds listed once, mask sides, and five field-report fixes

New: `lib_move_bodies_to_layer` and `lib_redraw_assembly` (#43). The rest
are fixes found by live measurement on AD 26.10.1.6:

* a footprint add left the footprint on screen listing the new primitive
  twice until the next footprint switch; every footprint writer now makes
  the same footprint current again afterwards;
* `obj_modify` X/Y on a board text, and `pcb_place_3d_body`, moved the
  object outside its modify bracket, so the spatial index kept the old
  place and the mouse could not pick it (#39.14);
* `pcb_set_via_soldermask_relief` refused; it writes through the via's pad
  cache now, since only the direct `SolderMaskExpansion` assignment faults
  (#60), and pad and via mask reads report the bottom side and the
  separate flag (#59, #61);
* a Board Outline Clearance rule's descriptor says `Gap=0mil` whatever it
  holds; the rule readers add `gap_mm` from the rule itself (#39.7);
* the EasyEDA importer read a region's SVG path as a point list and drew
  per-pad lead outlines as silkscreen (#39.22);
* `sch_mirror_component` never moved an arc, because a local `EArc` hid the
  `eArc` kind (names are case-insensitive), and skipped the designator.
  `lint.py` now flags a local that hides an enum constant.

1. **Bodies to one layer.** In a PcbLib with bodies on different
   mechanical layers, `lib_move_bodies_to_layer layer=Mechanical13`:
   `moved` counts each body not already there, and after save and
   reopen every body is on Mechanical13. `layer=TopLayer` is refused; a
   mechanical layer the library has not enabled is accepted with
   `layer_enabled` false and a `warning`.
2. **Assembly redraw.** `lib_redraw_assembly layer=Mechanical13
   confirm=true` on footprints with a 3D body: a rectangle the size of the
   body, `.Designator` centred in it. Run it twice: `cleared` is 5 per
   footprint the second time and one drawing remains. A body on that
   layer is not cleared. Without `confirm` it is refused.
3. **One footprint in the library.** The same redraw in a single-footprint
   library leaves no extra footprint behind.
4. **Adds listed once.** Add a track to the footprint on screen:
   `lib_get_footprint_primitives` lists it once, and so does Altium's own
   iterator.
5. **Picking a moved text.** `obj_modify eTextObject X=..|Y=..` on a board
   text, then click it at its new place in the editor: it selects.
6. **Via relief.** `pcb_set_via_soldermask_relief expansion_mils=3` on a
   board with vias: `vias_verified` equals `vias_matched`, Altium stays up,
   and each via's mask opening is 3 mil from the hole edge after save and
   reopen.
7. **Mask sides.** `lib_set_pad_properties mask_expansion=0.2
   mask_bottom_expansion=0.05 units=mm`: `lib_get_pad_geometry` reads
   0.2 / 0.05 with `mask_separate` true.
8. **Outline clearance gap.** A Board Outline Clearance rule set to 3 mm:
   `pcb_get_rule_properties` reads `gap_mm` 3 while the descriptor still
   says 0.
9. **Mirror an arc.** Mirror a placed part with an arc: the arc's centre
   reflects, a half arc's opening turns the other way,
   `primitives_skipped` is 0.

Recorded on AD 26.10.1.6 with the previous script build, scratch documents:
items 1 to 4 and 6 to 9. Item 1: two bodies moved and kept their layer
through save and a restart of Altium, the footprint on screen and one off
screen alike. Item 2: a 0603 body gave a 1.6 x 0.8 mm outline, three runs
left one outline and one text each, through save and restart. Item 4:
Altium's iterator read 11 entries for 11 objects after the add (12 for 11
before the fix); pads, pads in bulk, tracks, tracks in bulk, an arc, text,
a region and a 3D body added this way all survived save and restart.
Item 5 was measured with a spatial iterator rather than the mouse: the
text and a placed body were found at their new places and not at the
old. Item 6: two vias, 3 mil both sides from the hole edge, kept through
save and restart. Item 8: 3 mm and then 2.5 mm read back. Item 9: the
arc bubble moved from 210 to -210 mil about the part and back, and a
90..270 degree arc became 270..90. Item 5 by mouse is not run.

## 37. Field-report fixes #74 to #89, and one spelling per file path

New: `lib_add_footprint_via` (#79), `lib_set_via_properties` (#84),
`lib_count_layer_objects` (#81), `lib_get_library_origin` (#83),
`keep_layers` on `lib_move_bodies_to_layer` (#82), `paste_top_enabled` on
`lib_set_pad_properties` (#80), and the grid switches on `obj_set_grid`
(#78). Fixes, measured on AD 26.11.1.5:

* via tenting and the top paste flag are accepted and silently dropped
  while the matching expansion follows the rules, and kept once it is
  manual. Both writers make it manual first, at the value it holds, and
  say so (`mask_made_manual`, `note`);
* Altium keys an open document by the path text it was opened with, so
  `C:/dir\x.PcbLib` and `C:\dir\x.PcbLib` were two open documents. A focus
  by the other spelling loaded a second copy from disk, the edit went to
  the stale copy, and the next one raised "Object accessed after being
  destroyed" in ADVPCB. Every `*path` parameter now arrives with one
  spelling, and focus checks compare the same way;
* `Name` on a type without one stopped the bridge (#74); a pin's
  `Description` wrote the component field and raised (#75); the bare and
  Altium ellipse names did not resolve (#76); pin text settings were
  unreachable (#77); a SchLib component query and the pin toggle in a
  SchLib found nothing (#87, #89);
* a by-name symbol lookup that missed saved, closed and reopened the
  SchLib, every time (#88). It now answers from memory only and a miss is
  `COMPONENT_NOT_FOUND`; `lib_get_components` adds `unsaved_symbols` and
  `saved_names_not_in_open_library` when the library is open; a part
  suffix that cannot be reached puts the editor back on part 1.

1. **Path spelling.** Open a PcbLib as `C:/...\x.PcbLib`, add a pad, then
   `lib_set_pad_properties` with the backslash path: the pad is found,
   and the open document list holds the library once.
2. **Tenting.** On a fresh footprint via, `lib_set_via_properties
   tenting_top=true`: `tenting_top` reads true and `mask_made_manual`
   true. Then false reads false.
3. **Paste flag.** On a fresh SMD pad, `paste_top_enabled=false`, then
   `true`: `lib_get_pad_geometry` reads each, `paste_expansion_source`
   manual.
4. **Via lookup.** `units=mm`, a via 0.5 mm from the given point is not
   found (the window is 1 mil); hole and size written in mm read back.
5. **Wrong library.** Any of the four new library tools with another
   library's path: `WRONG_LIBRARY`, nothing changed.
6. **Pins.** In a SchLib symbol: `obj_modify ePin set=Name=A`,
   `ShowName=false` and `Description=X` read back; `sch_toggle_pin_visibility
   show_name=true` reports every pin of the symbol on screen.
7. **Names that would fault.** `Name` read and written on a net label:
   empty and `unknown`, and `app_ping` still answers.
8. **Grid.** `obj_set_grid snap_grid_on=false visible_grid_on=false`, read
   back by `obj_get_document_info`, then on again.
9. **Keep layers.** `lib_move_bodies_to_layer layer=Mechanical13
   keep_layers=Mechanical15`: the body on Mechanical15 stays, the others
   move; an unknown keep layer refuses and moves nothing.
10. **No save on a miss.** In an open SchLib, `lib_get_pin_list` on a name
    that is not there: `COMPONENT_NOT_FOUND` at once, and the file's
    modified time does not change. A symbol created earlier in the session
    (not the last one) is still found.
11. **Unsaved names.** Create a symbol and do not save: `lib_get_components`
    lists it in `unsaved_symbols`.
12. **Unreachable part.** `obj_query` with `scope=lib_component:<one-part
    symbol>@2`: not found, and the editor shows part 1.

Recorded on AD 26.11.1.5 with the three builds before this one, scratch
documents: items 1 to 9, except the pin `Description` write in item 6
(the build before this one wrote and read it back; earlier ones
reported it `unknown` without a fault). Before item 10's fix, the miss
took 6.6 s and saved the file, while two symbols created earlier in the
session were found in 0.05 s without a save. Item 1 left one open copy; before the fix the same steps left two
and the pad was "not found". Items 2 and 3 were also read with the flag
written back the other way. Nothing here was saved and reopened.

## 38. Fab stack-ups, applied and checked

New: `pcb_check_stackup` and `pcb_apply_stackup_template`, over the
stack-ups in `design_list_stackups` (JLCPCB's 36, parsed from its
impedance page). No script change: both use `pcb.get_layer_stackup` and
`pcb.modify_layer`, which already reads every field back. Use a scratch
4-layer board and make sure it is the only board open, since both act on
the board Altium resolves as current.

1. **Dry run.** `pcb_apply_stackup_template template=JLC04161H-7628A`:
   four calls, the top gap 11.59 mil (0.218 + 0.0764 mm summed) with Dk
   4.27, and the board unchanged (`pcb_get_layer_stackup` before and after).
2. **Apply.** The same with `dry_run=false`: `changed` names all four
   layers, `not_changed` is empty, and `pcb_check_stackup` with the same
   template reports `matches: true`.
3. **A real difference.** `pcb_check_stackup template=JLC04161H-3313`:
   findings for the dielectric heights and Dk, each with the board's and
   the template's value.
4. **Wrong count.** On a 2-layer scratch board, apply refuses naming the
   Layer Stack Manager, and check reports one copper-count finding.

Not yet run live: the documents open on the test machine at the time
included unsaved boards that were not ours to risk.

## 39. Property layer, decimal mils, fonts and footprint models

Run live on AD 26.11 with the script build before step 40 on a scratch copy of a two-layer
round board project, pinging after every step. All passed.

1. **Polygon pour settings (#125).** `obj_query` on `polygon` reads Grid
   20, TrackSize 8, MinTrack 3, ArcApproximation 0.5, NeckWidthThreshold
   5, IslandAreaThreshold 2500, PolyHatchStyle 5, PourOver 1, PourIndex,
   Poured. Grid 5.5, IslandAreaThreshold 1234.5 and a new Name write and
   read back; PourOver 3 is refused and unchanged.
2. **Keepout restrictions (#131).** The five restrictions read true on the
   board-outline keep-out tracks and arcs; IsKeepoutForTrack false, then
   true, on six tracks; asked of 279 vias the read is blank and the loop
   stays up.
3. **Region arcs and net (#119, #126).** `pcb_place_region` with one
   vertex swept 180 degrees reports `arc_edges` 1 and the shape read back;
   Net=GND joins the GND net, Net=NO_SUCH_NET is refused, Net= leaves it
   on no net.
4. **Decimal corners (#124, #130).** `pcb_place_polygon_rect` at
   20000.25, 20000.5 to 20960.6299, 20500.75 reads those bounds back
   exactly.
5. **Footprint selection (#120).** `lib_select_footprint_primitives` on a
   PcbLib footprint selects one pad by name, two pads by type, and clears
   with a set that matches nothing.
6. **FontId gate.** FontId reads on net labels, power objects, parameters
   and designators; on wires, pins and components it is reported
   unreadable instead of raising. On a sheet entry FontId is refused and
   `TextFontId` reads 1, writes 12 and reads 12.
7. **Display mode and pin map.** A one-mode part refuses DisplayMode 1 and
   takes 0. FootprintPinMap reads "(1:1),(2:2)", takes "1:2;2:1" and reads
   "(1:2),(2:1)", takes the original back, and refuses "1-2".
8. **Footprint by name.** A placement naming a model the symbol lacks is
   placed on the library's model and listed in `footprint_issues` with the
   model it carries; naming that model in lower case counts in
   `footprints_chosen`; the Footprint key on the placed part is applied
   through the choice, not a rename.
9. **One path spelling.** WorkspaceManager:OpenObject with a forward-slash
   FileName for an open SchLib leaves one copy open (22 documents before
   and after).

Not run live: a part with several PCB models (the scratch library's parts
carry one each), so the clear-then-set order and the restore after a
failed choice are covered only by the source checks.

---

## 40. Editing a 3D body already in a footprint

New: `lib_set_3d_body`. `lib_link_3d_model` writes a body's standoff only
while it creates the body, so correcting one meant deleting the body and
linking the model again. This reads, and optionally changes, the standoff,
OverallHeight, BodyOpacity3D and Layer of the bodies in one footprint, in
place, inside a modify bracket.

Field report behind it, AD 26.10.1.5: a STEP model whose origin is at its
mid-height was linked with `offset_z=25.59` (0.65 mm). `applied` reported
`standoff_height` true, yet the model was seen sitting with its middle on
the board.

**What the live run found, and changed.** A STEP body ignores its own
`StandoffHeight`. Written by script and saved, it read back 25.5906 mil
from Altium and from the file, and the Properties panel still showed
Standoff Height 0 mm. The panel's value is the model's `MODEL.3D.DZ` in
the saved record, which nothing had written. That explains the field
report. `IPCB_Model.SetState` was then measured on a scratch copy: writing
`(10, 20, 30, 12 mil)` and saving put `ROTX=10`, `ROTY=20`, `ROTZ=30`,
`DZ=12mil` in the record, so the signature is `SetState(RotX, RotY, RotZ :
Double; Dz : TCoord)`, and only `Body.Model := Model` after it makes it
save. `IPCB_Model.Rotation` reads the 2D rotation (0 with 10/20/30 set),
and no getter for the 3D ones is known. So on a STEP body (`ModelType` 1)
`standoff_height` now writes the model's Z offset with the rotations the
SAVED file holds, read by `pcblib_models.saved_body_models`. In the test
library most STEP models sit at a non-zero rotation (90/0/90, -90/180/0),
so writing zeros there would have tipped them over. A body whose saved
record is missing, or names another model file, or a footprint whose
saved body count differs, is listed in `skipped` and left alone. A
library path that is not a file is refused before Altium sees it: asked
to open one, Altium opens a new, empty library under that name.

1. **Read.** `lib_set_3d_body footprint_name=<fp> library_path=<lib>` on a
   footprint with one body: `changed` 0, `before` equals `after`, and
   `saved_bodies` gives the model's Z offset and rotations as saved.
2. **STEP standoff.** `standoff_height=0.5 units=mm` on a STEP body with a
   rotation: `model_z_offset_written` [1]. Save, read again:
   `saved_bodies` reads the new Z offset and the same rotations, and the
   panel's Standoff Height shows 0.5 mm.
3. **Other standoff.** On an extruded body: the body's own
   `standoff_height_mils` changes and nothing is written to a model.
4. **Opacity and layer.** `opacity=0.5 layer=Mechanical15`: the panel
   shows 50% and the body is on Mechanical15. `opacity=50` and
   `layer=TopLayer` are refused.
5. **Unsaved footprint.** On a STEP body of a footprint copied and not
   saved: `skipped` names it, `changed` 0; with `opacity` as well,
   `changed` 1 and the standoff still skipped.
6. **Missing library.** A `library_path` that is not a file: refused, and
   no document opens.
7. **Index.** `body_index=2` on a one-body footprint is refused with
   `NO_BODY`.

Run live on AD 26.11.1.5 with that change's script on a scratch copy of a working
PcbLib, pinging after every step. All passed: 1 on SOT-23-3, 8-SOIC and
C0603; 2 on 6-TSOP (Z offset 41.3386 to 19.685 mil, rotations 90/0/90
kept), and on SOT-23-3 the panel and the 3D view were checked by eye;
3 on QFN16_8TR8210 (0 to 7.874 mil); 4 on SOT-23-3, kept after save,
close and reopen; 5 on a copy of 6-TSOP; 7 on SOT-23-3. Item 6 was run
before the guard and opened an empty library; the guard is unit-tested.
Not run: a footprint with several STEP bodies, which the test library
lacks, so that the saved records list in the editor's order is assumed
and only checked by model file name and body count.

---

## 41. Dialog-free STEP export, symbol parts, model export and impedance profiles

New: `lib_set_symbol_parts`, `lib_export_3d_model`,
`pcb_get_impedance_profiles`, and a dialog-free first route in
`proj_export_step`. Each call was measured live before use. Measured and NOT used: `IPCB_Model.GetState`
runs, but its four var parameters come back unchanged, so a script cannot
read a STEP model's 3D rotations (step 40's file read stays).

1. **Symbol parts.** `lib_create_symbol name=<sym> part_count=3`, pins on
   parts 1 and 3, then `lib_set_symbol_parts part_count=5
   display_mode_count=2`: 5 parts, 2 modes, `pins_removed` 0,
   `complete` true. `part_count=2`: 2 parts, `pins_removed` 2 (part 3's).
   `display_mode_count=1`: 1 mode. An unknown symbol is refused.
2. **Model to STEP.** `lib_export_3d_model` on a footprint with one
   embedded STEP model: `written` true, a valid STEP file. It is
   Altium's Open CASCADE re-encoding (117 KB from a 268 KB original),
   not the vendor's bytes. `body_index=2` is refused with `NO_BODY`.
3. **Board STEP.** `proj_export_step` on a scratch board: `route`
   `exporter`, `dialogs_answered` empty, a valid 7 MB STEP file in 17 s.
   Again with `overwrite=True`: the same, and no dialog is left open.
4. **Impedance profiles.** `pcb_get_impedance_profiles` on three boards:
   `profile_count` 0, `read_errors` 0 on each.

Run live on AD 26.11.1.5 with that change's script on scratch libraries and a
scratch board (closed without saving), pinging after every step. All
passed. Not run: item 4 on a board that HAS a profile (none of the three
did), so the per-profile and per-layer reads are unmeasured and each is
guarded; a symbol's added display mode drawn and placed; a linked rather
than embedded model.

---

## 42. Variant entries, rule answers, drill pairs and more

New: `proj_set_component_variation`, `proj_net_color`,
`proj_get_declared_design_objects`, `pcb_find_dominant_rule`,
`pcb_check_rule_scope`, `pcb_clean_net`, `pcb_get_drill_pairs`,
`pcb_set_drill_pair`, `pcb_export_component_3d`, `lib_set_symbol_aliases`,
`sch_set_sheet_options`, and new `obj_query` / `obj_modify` properties
(hole tolerances, barcode and multiline text, dimension text and arrows,
pour options, part and pin swapping). `proj_create_variant` now creates
through `DM_AddProjectVariant` first, and every designator lookup on a
board asks `GetPcbComponentByRefDes` first. Measured and NOT built:
`Board.DoUndo` runs but takes back none of a script's edits.

1. **Variants.** On a scratch project with none: `proj_create_variant`
   `route` api, 1 variant. `proj_set_component_variation` on one part:
   not_fitted, then alternate with a part name (read back, and listed by
   `proj_list_variants`), then remove (`after` none), then fitted. Then
   `proj_delete_variant`: `verified` true, 0 variants.
2. **Net colour.** Read `none`, set `#FF8000` (read back), set `none`.
3. **Rules.** `pcb_find_dominant_rule rule_name=Width` on three nets
   with their own width rules and one without: each named its own rule
   and the last the catch-all. A pad pair for Clearance: the board rule.
   `pcb_check_rule_scope` on `InNet('VBUS')`: true for VBUS, false for
   GND. An unknown pad is refused.
4. **Net clean-up.** `pcb_clean_net` on a routed net: 21 to 23 pieces
   (tracks split where others meet them).
5. **Drill pairs.** Read the one pair; set backdrill and drill guide,
   read back, restore.
6. **Properties.** Read every new property on pads, vias, pours, parts
   and texts (no dimension on the board). Pad tolerances `unset`, then
   2 and -1.5 (read back 2 and 1.5: Altium keeps the size), then
   `unset`. A part's pin swapping and a pour's mitre corners and border
   written, read back and restored; `MitreCorners=maybe` refused. A
   barcode text's show-text and multiline flags take; its sizes are
   recalculated by Altium.
7. **Part 3D.** `pcb_export_component_3d` on a capacitor: a STEP file;
   an unknown part refused.
8. **Library.** On a scratch symbol: pin swap ids written and read back
   (`SwapIdPart` is the part's, shared by every pin); aliases added and
   one removed.
9. **Sheet.** `sch_set_sheet_options` read; reference zones, the custom
   zones and margin and template graphics written, read back and
   restored. On a standard sheet size `zones_x` and `margin_width` do
   not take, and `not_applied` names them.
10. **Declared objects.** `proj_get_declared_design_objects` on a
    three-sheet project: `sheets_examined` 3, all lists empty.

Run live on AD 26.11.1.5 with that change's script on a scratch project
and library, pinging after every step. All passed as described. Not
run: a dimension, a project that declares classes or pairs, and a
barcode text made in the GUI to read its kind numbers from.

---

## 43. xSignals, shared packages, text positions, net naming, library check and project setup

New: `pcb_create_xsignals`, `pcb_get_xsignals`, `pcb_find_same_package`,
`pcb_auto_position_text`, `proj_get_net_items`, `proj_project_setup`,
`lib_check_schlib`, and the part properties `LockStrings`,
`NameAutoPosition`, `CommentAutoPosition`, `FootprintDescription`,
`DefaultPCB3DModel` and `PackageSpecificHash` (read only) on
`obj_query` / `obj_modify`.

1. **xSignals.** On a board with none: `pcb_create_xsignals` from an
   MCU pin to an LED data pin through a series resistor, with a class
   name: one xSignal, its `path` the two end pads and both resistor
   pads, its length the routed length (821 mil, 9 tracks, 5 vias), and
   `class_assigned` true. `pcb_get_xsignals` lists it.
2. **Same package.** On a 143-part board: five groups (the 0402
   capacitors, the 0805 capacitors, the LEDs, the resistors, two
   SOT-23-5 parts), none without a hash. With `designator=R1`: the
   other three resistors.
3. **Text positions.** On a shown designator, anchors top_left,
   bottom_center and bottom_right each move it, and the reply's
   positions change. On a hidden designator the anchor is recorded and
   the text stays where it is (`name_shown` false). With no anchor, a
   part already at an anchor stays there. An unknown part is listed in
   `not_found`; an unknown anchor name is refused.
4. **Part properties.** `obj_modify` `LockStrings=true`,
   `CommentAutoPosition=3`, `FootprintDescription` text: all read back.
5. **Net naming.** `proj_get_net_items named_only=true` on a
   three-sheet project: the 16 named nets of 91, each label with its
   sheet and position in mils (a label on two sheets lists both). One
   net (GND): its power ports, carrying the net name, and its pins.
6. **Library check.** `lib_check_schlib` on a scratch library: Altium
   writes `<library>.ERR` beside it, read back as "No Footprint" and
   "No Description" on its one symbol. With only `duplicate_pins`
   checked: no problems.
7. **Project setup.** Read: documents in project order, no search
   paths. Moved a sheet from 5 to 2, added a search path with
   subfolders and set ports to name nets: all read back. Saved, closed
   and reopened: all three were kept. Moved it back and restored the
   option; a document not in the project is refused.

Run live on AD 26.11.1.5 with that change's script on a scratch project,
board and library. All passed as described. Not run: an xSignal class
read back by name (the class listing shows net classes only), and a
project with ports or sheet entries.

---

## 44. Drill pairs, xSignal removal, net display, single-footprint and project libraries, pin and pad properties

New: `pcb_add_drill_pair`, `pcb_delete_xsignals`, `pcb_net_display`,
`lib_save_footprint_library`, `proj_make_schematic_library`;
`proj_project_setup` gains `hierarchy_mode` (the net identifier scope)
and `output_path`; `obj_query` / `obj_modify` gain pin `DefinedFunctions`,
`Symbol_Inner`, `Symbol_Outer`, `Symbol_InnerEdge`, `Symbol_OuterEdge`,
`Symbol_LineWidth` and `HiddenNetName`, schematic part `ShowHiddenPins`,
`ShowHiddenFields`, `PinsMoveable`, `PartIdLocked`, `DisplayFieldNames`,
`OverideColors` and `PinColor`, and board pad `JumperID`, `SwapID_Pad`
and `SwapID_Part`. `proj_get_options` now names the scope in Altium's own
order (it had called 0 "Flat" and 1 "GlobalScope"). Measured and NOT
built: splitting a library into one file per part; `SplitIntoComponents`
is an undeclared identifier to a script on both library kinds.

1. **Drill pairs.** On a board with two internal planes: Top Layer to
   Internal Plane 1 added (2 pairs, read back); the same pair the other
   way round refused as `ALREADY_EXISTS`; Mechanical 1 refused; Internal
   Plane 2 to Bottom Layer added (3 pairs).
2. **xSignals.** An unknown name deletes nothing; the one xSignal deleted
   by name in another case; one made again and deleted with `all`.
3. **Net display.** GND's connection lines hidden and loop removal off,
   both read back, then restored; an unknown net listed in `not_found`.
4. **One footprint.** `lib_save_footprint_library` wrote a 138 KB
   library holding only that footprint, with the source's two pads; an
   unknown footprint refused.
5. **Project library.** With the sheets loaded,
   `proj_make_schematic_library` wrote and saved a library of the
   project's 15 placed symbols.
6. **Project setup.** The scope set to each of 0 to 4 and read back;
   `global` read back as 3 and named Global by `proj_get_options`;
   restored to automatic. The output path set and read back, then
   restored; an unknown scope name refused.
7. **Pins.** On a library symbol's pin: inner edge, outer symbol and
   line width, a hidden net name and two functions written and read
   back, then cleared; a symbol value that is not a number refused.
   Pin functions read from 1: read from 0 the list lost its last name.
8. **Parts.** Show hidden pins, pins movable, override colours and a pin
   colour written, read back and restored.
9. **Pads.** A pad's jumper id and both swap ids written and read back,
   a negative jumper id refused, all cleared.

Run live on AD 26.11.1.5 with that change's script on a scratch project,
board and libraries. All passed as described. Not run: a pin function
list on a placed part on a sheet, and the scope's effect on a compiled
hierarchical design.

---

## 45. Board grids, part STEP export, auto-save, net kinds, project options and more properties

New: `pcb_set_grids`, `pcb_export_component_model`, `app_autosave`;
`proj_project_setup` gains `append_sheet_number_to_local_nets`,
`pin_swap_by_net_label` and `pin_swap_by_pin`; `proj_get_net_items` gains
`local_scope`, `electrical`, `erc_suppressed` and bus membership;
`obj_query` / `obj_modify` gain pour `DrawRemovedNecks`,
`DrawRemovedIslands`, `DrawDeadCopper`, `ExpandOutline` and
`AutoGenerateName`, board text `Inverted`, `UseInvertedRectangle`,
`InvertedTTTextBorder`, `TTFInvertedTextJustify`,
`TTFOffsetFromInvertedRect` and `ConvertedString` (read only), schematic
parameter `ReadOnlyState`, `ParamType`, `AllowLibrarySynchronize` and
`AllowDatabaseSynchronize`, port `AutoSize`, `OverrideDisplayString` and
`HarnessColor`, and pin `Name_PositionMode`, `Name_CustomPosition_Margin`,
`Name_CustomPosition_RotationAnchor`, `Name_CustomPosition_RotationRelative`
and the same four for `Designator_`. Measured and NOT built: a rule's
`DataSummaryString` (an undeclared identifier to a script, which stopped
the loop) and a port's `CrossRef` (not known to the script engine).

1. **Grids.** Snap 10, track 2.5, via 12.5, visible 50 and 500 and lines
   instead of dots set and read back, then restored; 0 refused. The grid
   for moving parts follows the snap grid: 25 read back as 10 and was
   listed in `not_applied`, so it is read only.
2. **Part STEP.** U1 written as a 99 KB STEP file (Altium's own STEP
   writer); an unknown designator refused.
3. **Auto-save.** Read off at 30 minutes (Altium keeps 1800000 ms); set
   on at 12 minutes and read back; 0 refused; restored.
4. **Net kinds.** 91 nets: `local_scope` false only on the three nets
   that power ports name (GND, +5V, +3V3); USB_DP, joined across sheets
   by ports, reads true. 16 nets with a No ERC marker read
   `erc_suppressed`. Electrical types Passive, Input, Output and I/O.
   The signal type answered empty on every net and is not reported.
5. **Project options.** Sheet numbers on local nets on and both pin swap
   options off, read back, then restored; the project closed unsaved.
6. **Pours.** All five switches written and read back; writing
   `AutoGenerateName` renamed the pour at once (GND_L01_P000); restored
   with its name; `maybe` refused.
7. **Board text.** Inverted with a 12.5 mil border, justification 4 and
   a 3 mil offset written and read back, then restored; a negative
   border and a write to `ConvertedString` refused.
8. **Parameters.** A sheet parameter made writable, typed 1 and kept from
   library and database updates, read back, then restored; `perhaps`
   refused.
9. **Ports.** On a port placed for the test: auto size off, a display
   override and a harness colour written and read back; a colour that is
   not a number refused; the port deleted.
10. **Pin label positions.** On a library pin: name position mode,
    margin, rotation anchor and relative rotation and the designator's
    mode and margin written and read back, then cleared; a negative
    margin refused. A port property aimed at a pin was refused as not
    belonging to it.

Run live on AD 26.11.1.5 with that change's script on a scratch project,
board and library; every document closed without saving. All passed as
described. Not run: a bus member net, and a STEP export of a part with no
3D body.

---

## 46. Editor preferences, the electrical grid, net colour and jumpers, compile state and more properties

New: `pcb_view_options`, `pcb_edit_options`, `sch_editor_options`;
`obj_set_grid` gains `hotspot_grid` and `hotspot_grid_on`;
`pcb_net_display` gains `use_net_color`, `show_jumpers` and reads
`in_differential_pair`; `proj_get_compile_freshness` gains
`needs_compile` and `violation_count`; `obj_query` / `obj_modify` gain
board part `SourceDescription`, `JumpersVisible`, `ChannelOffset` and
`GroupNum`, board pad `SwappedPadName` and `OwnerPart_ID`, schematic pin
`DefaultValue` and `FormalType`, and schematic part `LibraryPath` and
`AliasAsText`.

1. **Board view options.** All ten read; pad net names and single layer
   mode flipped, read back and restored.
2. **Board edit options.** Rotation step 45 and then 15, and locked-object
   protection off, read back and restored to 90 and on. Undo levels did
   not take (33 read back as 50, listed in `not_applied`), so they are
   reported and not offered for writing.
3. **Schematic editor options.** All 21 read; components cutting wires,
   the pin name margin, the increment step and the power ground name
   written, read back and restored. The pin margins are kept at 10000 to
   the mil (the defaults read 500000 and 800000, 50 and 80 mils), so the
   tool takes and reports mils; 6 mils read back as 6.
4. **Electrical grid.** 40 mils, switched off and on again, read back.
5. **Net colour and jumpers.** GND drawn in its net colour with jumpers
   hidden, read back and restored; USB_DP reads `in_differential_pair`.
6. **Compile state.** `needs_compile` false and 2 violations read, before
   and after a compile.
7. **Board part.** U1's source description, jumpers, channel offset and
   group written, read back and restored; `x` for the group refused.
8. **Board pads.** Swap name and owner part written on the 66 pads named 4,
   read back and cleared.
9. **Schematic pin.** Default value and formal type written, read back
   and restored.
10. **Schematic part.** Aliases and library path written on J1, read back
    and restored (`*`, any library).

Run live on AD 26.11.1.5 with that change's script on a scratch project,
board and library; every document closed without saving. All passed as
described. Not run: the jumper display on a board that has jumpers, and
an electrical grid distance other than 0.

---

## 47. Routing preferences, board options, pours at a point, pair impedance and more

New: `pcb_route_options`, `pcb_board_options`, `pcb_pours_at`;
`pcb_view_options` gains `plane_draw_mode`, `pad_types_display`,
`from_tos_display`, `layer_name_display` and `draft_track_threshold_mils`;
`pcb_edit_options` gains `drag_mode` and `component_move`;
`sch_editor_options` gains the five multi-channel expansions,
`watermark_read_only_sheets`, `always_drag`, `use_printer_fonts` and
`default_template`; `pcb_set_rule_properties` gains
`dp_impedance_driven` and `dp_min_impedance` / `dp_max_impedance` /
`dp_preferred_impedance`; `pcb_get_design_rules` reports
`from_schematic`; `obj_query` / `obj_modify` gain 3D body
`OverrideColor`, `Texture` and `TextureRotation`, and the schematic
parameter flags `NameIsReadOnly`, `ValueIsReadOnly`, `IsRule`,
`IsSystemParameter` and `IsConfigurable` (read only). Measured and NOT
built: the board's undo (a scripted move of R1 by 100 mils stayed after
undo and redo), clearing compile violations (the count stayed at 2), a
dimension's text value (0 on a 500 mil dimension), and writing automatic
split planes (did not take; it is reported only).

1. **Routing options.** All seven read; auto via, shove depth and smart
   track ends written, read back and restored.
2. **View and edit additions.** Plane drawing mode and the draft track
   threshold (7 mils) written, read back and restored; drag mode the same.
3. **Board options.** The pour naming template written, read back and
   restored.
4. **Schematic display options.** All nine read; designator expansion,
   always drag and a default template written, read back and restored.
5. **Pair impedance.** On the board's DiffPairsRouting rule:
   impedance-driven with 85, 95 and 90 ohms written and read back, then
   switched off; 0 ohms refused before Altium.
6. **Rules from the schematic.** The three "Schematic Supply Nets" rules
   read `from_schematic`, the rest do not.
7. **3D body.** On U1's body: colour override, a texture file and a 45
   degree texture rotation written and read back, then restored.
8. **Parameter flags.** Read on a sheet's parameters (system parameters
   with fixed names); a write to one refused.
9. **Pours at a point.** At R1 both GND pours (top and bottom) found; a
   point off the board found none.

Run live on AD 26.11.1.5 with that change's script on a scratch project
and board; every document closed without saving. All passed as
described. Not run: a texture image that exists on disk, and the routing
mode change during an interactive route.

---

## 48. Pour growth, plane connections, wire tidy, rule settings, pre-routes and paste openings

New: `pcb_grow_pour`, `pcb_plane_connections`, `sch_optimize_wires`;
`pcb_set_rule_properties` gains Width impedance limits and
`check_connected_copper`, Height limits, Component Clearance
`vertical_gap_mils`, `show_distances` and `collision_check_mode`, Routing
Corners setbacks, Net Antennae tolerance and the Fanout Control settings,
each refused on a rule of another kind; `obj_query` / `obj_modify` gain
`IsPreRoute` and `UserRouted` on tracks, arcs and vias and the mask modes
and paste percentage on pads and vias. Measured and NOT built: a
schematic object's compile mask (J1 read back unmasked after the write
took), and the per-plane-layer connection style a pad answers (0 for
every layer whatever the rule said).

1. **Grow pour.** Top Layer-GND moved in 20 mils on every side and out
   again to its first bounds; 0 and an unknown pour refused.
2. **Plane connections.** U1's pads on a board with two planes: the rule
   style switched to direct, none and relief read 1, 2 and 0, with the
   relief width, entries, air gap, expansion and clearance of the rule.
3. **Rule settings.** Height 10/900/400 mils, component vertical gap 12
   mils with distances shown, corner setbacks 50 and 150 mils, antenna
   tolerance 2 mils, a fanout style and via grid, and Width
   impedance-driven 45/55/50 ohms with the connected-copper check, each
   read back and restored; a height setting on a Width rule and a Width
   setting on the Height rule refused, naming the rule's kind.
4. **Wire tidy.** On a scratch sheet 45 wires became 19 (90 vertices 55);
   two wires drawn end to end became one; the sheet closed unsaved and
   reopened with 45.
5. **Pre-routes.** `IsPreRoute` set on the top-layer tracks, read back,
   cleared.
6. **Paste openings.** On pads: paste mode 2, percentage on at 80, read
   back; 150 percent refused.

Run live on AD 26.11.1.5 with that change's script on a scratch project
and board; every document closed without saving. One earlier test call to
`proj_force_recompile` saved the scratch sheet, as that tool does with
any unsaved document; the sheet held only restored values then. All
passed as described. Not run: a pour grown into a keepout, and paste
percentages on a stencil output.

---

## 49. Rigid-flex regions, layer stacks, bend lines, Gloss and Retrace

New: `pcb_get_board_regions`, `pcb_split_board`, `pcb_remove_split_line`,
`pcb_set_board_region`, `pcb_layer_stacks`, `pcb_add_bend_line`,
`pcb_modify_bend_line`, `pcb_delete_bend_line`, `pcb_gloss`.

1. **Read.** A plain board: one region on "Board Layer Stack" with its
   four copper layers. A rigid-flex fixture: nine regions (five rigid,
   four flex arms on "Research Flex" with GND and PWR), one bend line per
   arm with its points, radius 300 mils, angle -90 and fold order.
2. **Split.** A line across the plain board made two regions, both named
   alike; renamed by index ("#0", "#1"); the split removed again gave one
   region. A line off the board added no region and was taken out
   (`SPLIT_DID_NOT_DIVIDE`). On the fixture, whose regions were not made
   by split lines, a split was refused (`REGIONS_NOT_FROM_SPLIT_LINES`)
   and its nine regions stayed: before that guard a split there merged
   them into two.
3. **Layer stacks.** A flex stack created, Top and Bottom Layer enabled
   in it, one disabled again, renamed, assigned to a region and back,
   removed; an unknown stack refused.
4. **Bend lines.** On a fixture arm: radius, angle and fold order
   changed and restored, the line deleted and added again with the same
   points. On the plain board: a line across a region stretched to its
   edges; one outside it refused (`BEND_OUTSIDE_REGION`) and removed.
5. **Lock.** A region locked in 3D and read back.
6. **Gloss and Retrace.** Gloss on two nets: 8 tracks to 11 and 297 to
   293 mils, 9 tracks to 5 and 463 to 453 mils; Retrace then left the
   first net as it was.

Measured and NOT built: reading a split line's points ("Undeclared
identifier: FromPoint"); `TCoordPoint` records (their fields are
undeclared to a script, so points are `TPoint` from `Point()`); split
lines made with the full region rebuild (the line was thrown away).

Run live on AD 26.11.1.5 with that change's script on scratch copies of
the plain board and the fixture; every document closed without saving.
All passed as described. Not run: bend lines folded in the 3D view, and a
split on a board with existing split lines.

---

## 50. Layer stack manager: physical stack, dielectrics, impedance profiles, layer order, vias

New: `pcb_physical_stack`, `pcb_set_dielectric`, `pcb_impedance_profile`,
`pcb_impedance_profile_layer`, `pcb_via_structures`; `pcb_add_layer`
gains `above` / `below`. `pcb_get_impedance_profiles` now reads a
profile's layers by asking for each copper layer; it no longer reports
the reference layers, which a script cannot name.

1. **Physical stack.** A 4-layer board: 13 layers top down (paste,
   overlay, mask, copper and dielectrics) with Z, thickness, Dk,
   material and type, 64.25 mils in all; the rigid-flex fixture: 13
   layers, 23.86 mils. Altium's Z runs down from the top copper.
2. **Dielectric.** Dielectric 1 set to Df 0.021, a stiffener, Dk 4.3,
   one mil thicker and Isola 370HR, read back (the board then 65.25
   mils), restored; Top Solder Dk 3.5 to 3.8 and back; a copper layer
   refused.
3. **Stack style.** 0 to 1 and back, read back.
4. **Profiles.** A 50 ohm single and a 90 ohm differential profile
   created; Top Layer added to the first with width limits 4 to 12
   mils, solder mask modelled 0.8 mils, then width 9 mils locked;
   Bottom Layer added to the second with gap 6 (5 to 8) mils; renamed,
   retargeted and removed; a second profile of the same name refused.
   A newly added layer starts disabled with no width until Altium's
   solver runs.
5. **Layer order.** MidLayer1 added below Top Layer went in second.
6. **Vias.** 279 vias read: no protection structure and no back
   drilling on that board.

Measured and NOT built: a trace record's own layer and its reference
layers (V7 layer handles a script cannot name: "Undeclared identifier:
Name"); the layer object as the profile's layer argument ("Could not
convert variant of type (Dispatch) into type (Integer)"; the ID works).

Run live on AD 26.11.1.5 with that change's script on scratch copies;
every document closed without saving. All passed as described. Not run:
a via with a protection structure or back drilling, and a layer
inserted above another.

---

## 51. Board output and editor settings, testpoint rules, sheet zones, net colours

New: `pcb_gerber_options`, `pcb_print_options`, `pcb_paste_options`,
`sch_reference_zones`. `pcb_set_rule_properties` takes `testpoint`;
`pcb_set_grids` gains `visible_big_factor` and the grid units;
`pcb_view_options`, `pcb_edit_options` and `sch_editor_options` gain more
preferences; `pcb_net_display` gains `color` and `from_project`;
`obj_query` / `obj_modify` gain DrillType, IsVirtualPin, FlippedOnLayer
and BorderSpaceType.

1. **Gerber and print setup.** Read on a 4-layer board; embed apertures,
   film width 25000 mils, decimals 2 and plus tolerance 0.01 mils
   written, read back and restored; print scale 2, pad numbers off,
   holes on and composite type 1 the same.
2. **Testpoint rules.** Fabrication Testpoint (kind 43) and Assembly
   Testpoint (kind 57): sizes, edge clearance, grid and under-part
   settings written, read back and restored; the Clearance rule refused
   (`WRONG_RULE_KIND`).
3. **Grids.** Coarse factor 10 and snap unit mil written and restored.
   The fine factor reads 1 and stayed 1 for 2, 5, 0.5 and 10; it is
   reported only.
4. **Board preferences.** Paste keeps its net, array count 4, increment
   "2", step -150 and angle 30; transparent layers, pad net font
   Consolas, its minimum size, board area colour; confirm delete,
   netlist comment check, auto-pan speed and pour redraw threshold: each
   written, read back and restored. The repeat step reads 10000 to the
   mil (1000 mils read 10000000).
5. **Schematic preferences.** Increment without the leading zero,
   Ctrl + double-click, selection memory confirm, device sheet editing
   and watermark, cutter grid multiple, cutter length (10000 to the mil)
   and physical view dimming: written, read back and restored.
6. **Object properties.** Pad MH1 drill type 2 written and read back,
   7 refused, restored to 0; board text BorderSpaceType 1 and back;
   IsVirtualPin and FlippedOnLayer read (a bottom-side part reads false:
   it is the "flip on layer" option, not the side).
7. **Sheet zones.** 29 objects on a sheet with their zones ("3B"),
   filtered to ports and power ports.
8. **Net colours.** GND given orange and switched on: the pour drew
   orange on screen. +3V3 given a project colour, then `from_project`:
   +3V3 took it and switched on, GND and every net without one listed in
   `no_project_color`. All restored.

Measured and NOT built: hole symbols (the call showed nothing on the
board with the drill drawing layer on, and nothing reads whether they
are shown); special string resolution through the project (every name,
CurrentDate included, answered not resolved); a dimension's TextValue
(read 0 on a 1000 mil dimension). Simulation probes, profiles and
waveform data are not reachable from a script.

Run live on AD 26.11.1.5 with that change's script on scratch copies;
every document closed without saving and the files hashed the same
before and after. All passed as described.

---

## 52. Contributed fixes, the via delete, unpoured pours, bodies by identifier, the via middle size

Taken from pull requests #137 (a lone via mask value sets both sides),
#138 (object walks always filtered) and #139 (decimal mils in the
remaining handlers), and fixes for issues #141, #136, #135 and #140.
`pcb_delete_object` searches every layer when no layer is given and
reports the layer of what it deleted; `pcb_place_polygon_rect` and
`pcb_place_polygon_board` take `pour` and report `pour_skipped`, `poured`,
`repour_mode` and `name`; `lib_select_footprint_primitives` and
`lib_delete_footprint_primitives` pick 3D bodies by `identifier` prefix;
a footprint via's unreadable middle size reads null with
`mid_unreadable`. `lib_delete_footprint_primitives` no longer faults.

1. **Decimal mils (#139).** A via array from -2000.25, -2000.75 at a
   33.3 mil pitch: four vias at X -2000.25, -1966.95, -1933.65,
   -1900.35, no drift.
2. **Via delete (#141).** Each of those four deleted with no layer
   given, at distance 0, reported on Multi Layer; the via count back to
   279.
3. **Mask (#137).** A board via with a manual mask: 4 / 2 written, then
   6 alone gave 6 / 6, then 8 with a bottom of 3 kept 8 / 3. A library
   via: 0.1 / 0.05 mm separate, then 0.2 alone gave 0.2 / 0.2. On a via
   whose mask follows the rule, the same board write did not take.
4. **Walks (#138).** Library select by type (2 pads) and by layer (2
   objects) still selects.
5. **Bodies by identifier (#135).** Two bodies added with identifiers
   Envelope_Test_Body and Envelope_Test_Courtyard beside the footprint's
   STEP body; select by prefix took the two; delete by prefix removed
   the two and left the STEP body. A pad set with an identifier refused.
   Delete by object_type body and by layer also ran.
6. **Library delete.** It faulted with an access violation in the
   script engine: reading I_ObjectAddress off the footprint after its
   library iterator was gone. The footprint is now fetched by name,
   compared by name, and sent no modify messages. A crash leaves later
   footprint writes faulting in the same Altium session, so every
   result above was taken again after a clean restart.
7. **Unpoured pours (#136).** Placed with pour false: the reply says
   pour_skipped and names the polygon (Top Layer-GND 1), which
   pcb_repour_polygons then found by name. On AD 26.11 Altium poured
   the new polygon anyway, also with its repour preference at never,
   and `poured` reports that.
8. **Via middle size (#140).** A footprint via set top-middle-bottom
   1.0 / 0.7 / 0.9 mm: the middle reads 0 on AD 26.11 too, and is
   reported null with mid_unreadable.

Run live on AD 26.11.1.5 with that change's script on scratch copies;
every document closed without saving and the files hashed the same
before and after. All passed as described.

---

## 53. Simulation probes, the setup a run uses, a run for a sheet

`sch_place_probe` places the probe Altium's Place > Probes > Voltage
makes and takes `name`; `sim_get_probes` lists the probes on the
project's sheets; `sim_get_setup` reports the analyses a run would do and
their settings; `sim_run` runs a sheet's setup the way the Simulation
Dashboard does, takes `analyses` and `sheet`, and reports `run_mode` and
`ran_analyses`. The settings file reloads on AD 26.11, which reports it
cannot.

1. **Probe.** A probe placed by Altium's own command carried
   SimulationProbe=V (shown), ProbeVoltage=True (hidden), ProbeName,
   ProbeCustomName (hidden) and ProbeValueDisplay; the probe the script
   placed before had only ProbeValueDisplay and the simulator passed it
   over. Placed now on a wire of the Simulation Quick Start example's
   first sheet with name NODE_A: `sim_get_probes` lists it as a
   simulation probe of kind V, Altium resolved ProbeName to V_NetQ11_3,
   and every analysis of the next run carried v(netq11_3). The old bare
   probe is listed with `simulation` false.
2. **Setup.** `sim_get_setup` on that sheet read `affect` Document, the
   sheet's own settings, the analyses on, the DC sweep (R7, 100 to 1k,
   step 1, output v(out)), transient and AC settings, and the run history.
3. **Run for a sheet.** With only DC switched on in the file, the
   project's first simulator output (the old form) ran operating point,
   DC, transient and AC; the dashboard's form for the sheet ran DC alone.
   After the project was reopened, OP only in the file gave OP alone.
4. **Selection kept.** A later change of the switches, with the settings
   document reloaded or closed, did not change what ran: Altium keeps the
   selection it read at the first run after the project opened, and each
   run turns OP, DC, transient and AC back on in its copy. `sim_run`
   reports what ran from the results and, with `analyses`, says
   `selection_applied` false and why.
5. **Reload.** On AD 26.11 the settings document reports SupportsReload
   False; asked to load anyway, it did: OP and AC written to the file,
   reloaded, saved by Altium, read back OP and AC. Before, the refusal
   stopped `sim_setup_analysis`.

Run live on AD 26.11.1.5 with that change's script on a scratch copy of
Altium's Simulation Quick Start example; every document closed without
saving. All passed as described.

---

## 54. Variant values, renaming and copying a variant, the BOM a variant builds

`proj_set_parameter_variation` sets or clears the parameter values one part
takes in a variant; `proj_rename_variant` renames a variant;
`proj_copy_variant` starts a variant from another; `proj_get_bom` takes
`variant` and gives the BOM that variant builds;
`proj_set_component_variation` takes the alternate's library, symbol and
footprint. Variants are found by name or description and reported by
description, `proj_set_active_variant` sets the current variant directly,
and `proj_list_variants` gives each entry's unique id, an alternate's
library link and the variant's own parameters.

1. **Values.** On R1 in variant Lite, Value 4k7 and Comment 4k7 were
   written and read back, a name the part does not carry (Bogus) was
   written and named in `not_on_part`, then cleared. A not-fitted part
   (R6) was refused. Altium keeps an alternate's name as its varied
   Comment: setting Q6's alternate added Comment 2N2222, and R1's varied
   Comment reads back as its alternate part.
2. **Names.** The project file keeps only a variant's description; after a
   reopen every variant's name read `Variant`. Variants are now found by
   name or description and reported by description. Renamed (also the
   active one), copied and deleted by description; a name in use was
   refused for both rename and copy.
3. **Current variant.** `proj_set_active_variant` had failed (the process
   left the base design current); set directly it switched to the
   variant and back to `[No Variations]`, read back by unique id.
4. **Copy.** A copy of Lite held its three entries and three values
   (Comment not doubled), and removing an entry from the copy left Lite
   unchanged.
5. **Alternates.** An alternate with no library link was saved as one but
   loaded back as fitted. With `alternate_library` set to Miscellaneous
   Devices.IntLib, Q6's alternate 2N3906 came back as an alternate with its
   library, and so did its copy.
6. **BOM.** For a variant: 55 of 56 parts (R6 not fitted), R1 with comment
   4k7, Q6 as 2N3906, before and after the project was saved and reopened.

Run live on AD 26.11.1.5 with that change's script on a scratch copy of
Altium's Simulation Quick Start example, saved and reopened there. All
passed as described.

## 55. Libraries made from a design, the PCB library check, pad/via libraries, supplier links, pin timing, the library report

`pcb_make_library` makes a PCB library of a board's footprints;
`proj_make_integrated_library` makes an IntLib of a project's parts;
`lib_check_pcblib` runs the checks of Altium's Component Rule Check on a
PCB library; `pcb_get_pad_via_library`, `pcb_add_pad_via_template` and
`pcb_make_pad_via_library` read a board's pad and via templates, add one
and write them to a .PvLib, and `lib_install_library` /
`lib_uninstall_library` take a .PvLib; `lib_set_supplier_links` and
`lib_get_supplier_links` write and read a symbol's manufacturer and
supplier links; `lib_set_pin_timing` sets pin package length and delay,
and `lib_get_pin_list` reads them; `lib_library_report` writes an HTML
report of a library.

1. **PCB library from a board.** All 11 footprints of the board, saved
   under the name asked for and added to the project when asked. Altium
   leaves an empty file under its own name, which is removed. Until it is
   reopened, the new library reads its pads over a metre from their
   origin, so it is closed after saving; reopened, pads read at
   +/-0.48 mm and the check below gives the same answer as on the
   original library.
2. **Integrated library.** No dialog; `<project>.IntLib` rewritten beside
   the project (445 KB), read from its file time.
3. **PCB library check.** On the scratch library: two pads named MP, two
   named S1, and two unnamed holes on the USB-C footprint. A track joining
   the two pads of an 0402 was reported as shorted copper, a loose track
   as unconnected copper, and that track drawn twice as a duplicate
   primitive; a footprint with pads on the bottom layer only, 500 mil
   from its origin, as mirrored and offset.
4. **Pad/via library.** Templates made from pads C1.1 and U1.1 (named by
   Altium r55_60r14 and r120_60r15) were written to a new .PvLib with
   Altium's default template, read back after a reopen, installed (an
   install sent at once is not listed; repeated a second or two later it
   is) and uninstalled. `AddTemplate` with a template added nothing;
   `AddExternalTemplate` copies.
5. **Supplier links.** Two links written as "Manufacturer 1",
   "Manufacturer Part Number 1", "Supplier 1", "Supplier Part Number 1"
   and link 2, replaced by one link (6 removed, 4 written), unchanged
   after save and reopen.
6. **Pin timing.** Pin 1 at 120.5 mil and 35 ps, pin 2 at 12.25 ps, an
   unknown pin named in `not_found`; unchanged after save and reopen.
   `ISch_Pin.PinPropagationDelay` is undeclared to the script engine and
   halted the loop; the member is `PropagationDelay`.
7. **Library report.** 15 symbols and 11 footprints written to HTML, none
   unread.

Not checked: whether Altium shows the delay written as 35 ps (the value
is written as seconds and read back the same way), and whether Altium's Supplier Links panel lists the links.

Run live on AD 26.11.1.5 with that change's script on a scratch copy of
the RoundLED project, saved and reopened there. All passed as described.

---

## 56. Schematic placement fixes, port cross-references, designators sized to parts, layer stacks

New ports, power ports, text
frames and sheet entries take the sheet's font; `sch_place_port` takes a
width and fractional coordinates; a text frame's line breaks become
Altium's; `sch_place_components` matches a part placed from a library to
the sheet (`tidy`); `sch_tidy_port_references` moves port cross-references
to the port's free end; `pcb_resize_designators` sizes designators to
their parts, on a mechanical layer if asked; `pcb_make_layer_stack` makes
the board's stack match a wanted one through `copper_layers`,
`drop_stack_layer` and `tune_copper_layer`.

1. **Placement.** On a scratch sheet: a port 600 mil wide with AutoSize
   off, read back; the new objects carry the sheet's font id; a text
   frame with a typed `\n` shows two lines; the hidden-pin filter gives 4
   pins for a part whose copies read 8; deselect-all leaves nothing
   selected (the old process left 123).
2. **Placed part tidy.** A part placed from RoundLED.IntLib reads that
   library as its source, not `*`.
3. **Designators.** 146 parts planned, 5 written on Mechanical10 and 11,
   read back; a second run plans the same.
4. **Layer stack.** A 5-copper stack made as planned, unchanged after save
   and reopen, and shown so in the Layer Stack Manager.

Not checked live: `sch_tidy_port_references` moving a reference (the
scratch project had cross-references off).

Run live on AD 26.11.1.5 on scratch copies. Run again with the 28 October
script after the commands were rewritten: on StackTest.PcbDoc the
5-copper stack went to a 4-copper one (an empty layer dropped, a
dielectric trimmed, every layer renamed) and back (a signal layer added
between two planes, which kept both), each read back with no mismatch
and unchanged after save and reopen; on RoundLED 146 parts planned, 5
designators written and held, the same plan on a second run; on a new
sheet a 600 mil port held its width, a typed `\n` became a line break, a
part placed from RoundLED.IntLib read that library as its source, and 4
pins were listed for it.

---

## 57. Fixes across the tools, a pour's outline, project-wide designator renames, DbLib batches, body positions

`pcb_get_pour_outline` reads one pour's outline, arcs included;
`sch_rename_designators` renames designators across a project, swaps
included; `sch_place_dblib_rows` places many DbLib rows in one call;
`lib_set_3d_body` takes `center_x` / `center_y`. Fixes:
`lib_link_3d_model` applies its rotation; a sheet symbol sits on the box
asked for and its entries on its edge; a schematic line's ends read as
vertices; `pcb_create_xsignals` reads the class membership back;
`pcb_get_layer_display` says which layers the stack uses;
`app_close_document` counts the bridge's own edit record and says when
the project may still hold a discarded edit; `app_create_document`
refuses a missing folder; class members and pair nets read as names;
lengths and coordinates take fractions of a mil.

1. **Pour outline.** Pour 0 of the scratch board: 12 vertices, 6 arcs
   with centre and radius, closed. A wrong name and an index past the end
   are refused.
2. **Designator renames.** On the Live1023 scratch project U9 (two parts,
   each with its own UniqueId) and U7 swapped through temporary names and
   read back, then swapped back. U5, a three-part part, to U6, which
   another part keeps, was refused before any write.
3. **3D bodies.** CAPC1608X90N linked at 0 degrees reads 1.6 x 0.8 mm,
   at 90 degrees 0.8 x 1.6 mm. A body moved to (10, -5.5) mil reads
   there, and back at (0, 0).
4. **Sheet symbols.** A symbol asked for at (1000,1000)-(2000,2500) has
   that box; an entry 300 mil from the top on the left edge sits at
   (1000, 2200), one 500 mil down the right edge at (2000, 2000).
5. **Closing a document.** A schematic edited and closed with discard came
   back with its edits on reopen while the project stayed open: the reply
   says `discarded` false and `project_may_hold_edit` true.
6. **Ports.** OverrideDisplayString and HarnessColor read back at once
   but were gone after save and project reopen; AutoSize kept. `obj_modify`
   says so in a note.
7. **Errors.** An ambiguous designator's candidates arrive as parsed
   details. A new document in a missing folder raised Altium's save error
   modal; it is now refused before Altium makes the document.

Not checked live: `sch_place_dblib_rows` (no DbLib was installed),
the ECO focus check (it raises Altium's ECO dialog), declared class
members (the test directive did not join a net).

Run live on AD 26.11.1.5 on scratch copies.
Run again with the 28 October script after the commands were rewritten:
the RoundLED GND pour read 12 corners and 6 arcs, closed, with a wrong
name and a bad index refused; a line read 2 vertices, both ends and
`VertexLast`; the sheet symbol and its two entries sat where item 4
above says; a discarded close of a project sheet said
`project_may_hold_edit`; a document in a missing folder was refused by
the script; `sch_place_dblib_rows` refused a missing .DbLib and a .txt
row by row; an alias another symbol has, and one asked twice, were
refused with nothing changed; 107 nets read with their directives.

---

## 58. Stages to production, SDK reach, LTspice runs, company conventions, Altium 365

No Pascal changed.
`design_phase_gate`, `design_phase_answer` and `design_phase_standing`
hold a product to feasibility, prototype, EVT, DVT and PVT;
`eda-agent sdk-reach` counts the properties Altium's SDK declares that
the generic tools reach; `design_simulate_deck` runs a deck in ngspice or
LTspice; `footprint_read_ipc_name`, `lib_verify_ipc_names`,
`lib_check_naming`, `lib_make_name`, `lib_read_pin_table`,
`design_check_company_rules`, `design_read_rules_export` and
`design_compare_plans` cover company conventions; the `altium365_` tools
read and write Altium 365.

1. **Stages.** Every tool a check names exists here (a test fails if one
   does not). A product with feasibility answered for revision A is in
   prototype, and back in feasibility when asked about revision B.
2. **SDK reach.** 802 of 2,221 declared properties reached (36.1 %)
   across 46 object types: 464 of 1,040 on the schematic, 338 of 1,181 on
   the board.
3. **Simulators.** One RC deck in ngspice-47 and LTspice 24.0.12:
   both 4.96629 V at 5 ms and a 2.1972 ms rise (RC ln 9). A deck calling
   an unknown subcircuit fails in both with the engine's error. A deck
   including a file outside the workspace, or using .ferret, is refused
   before it runs. LTspice writes a failed run's log as UTF-16 with no
   byte-order mark.
4. **IPC names.** On the scratch RoundLED.PcbLib, 7 footprints agree with
   their IPC names and 5 are not IPC names.
5. **Company rules.** A trial rules file on the scratch RoundLED project
   read the BOM, parameters, nets and board rules: the 6 mil clearance
   rule passed a 4 mil minimum, 26 single-pin nets were flagged, the note
   came back as text.

Not checked live: Altium 365 (needs a workspace token or a Nexar app),
and the naming policy on a real company policy.

Run on AD 26.11.1.5 on scratch copies.

---

## 59. The Saving switch on the bridge dashboard

The Saving button in the dashboard's button grid, beside Detach, turns
every save the bridge makes on or off. It starts as "Saving on"; off, it reads
"Saving OFF" and turns amber. The
state is kept in `saving.json` in the workspace, so a restart of the
bridge or of Altium keeps it, and `app_ping` reports `saving_allowed`.

Off, the commands whose job is writing a file are refused with
`SAVING_OFF` (save, save all, project save, create document, add sheet,
make library, save copy as, simulation settings save, project close with
save, a Save menu item or process). A command that saves on the side runs,
writes nothing, and its reply lists `not_saved`. A library reload that
would drop unsaved edits is skipped. The Python side refuses its own writes
(project file cleanup, simulation settings, checkpoint restore) and the
interface routes to File > Save, a Save button and Ctrl+S.

Run live on AD 26.11.1.5 with the 28 October script on the scratch
Live1023 project: the button wrote `{"saving":"off"}` and turned amber;
`app_save_document`, `app_save_all`, `proj_save` and a save-first
`proj_close` were refused, `app_send_keys("ctrl+s")` and
`app_click_menu("File|Save")` were refused before sending anything;
`proj_add_document` and `proj_remove_document` ran and listed
`Live1023.PrjPcb` as not saved; a detach after a net label wrote nothing;
no file in the folder changed. After a bridge restart the button still
read off. Switched back on, `app_save_document` wrote the scratch sheet.

## 60. Further SDK reach: board pictures, net figures, grids, part parameters, library manager

New board tools: `pcb_render_png`, `pcb_save_board_text`,
`pcb_net_figures`, `pcb_clear_guides`, `pcb_local_grid`,
`pcb_plane_state`, `pcb_hole_symbols`, `pcb_component_parameters`,
`pcb_place_simple_body`, `pcb_place_baseline_dimension`,
`pcb_file_info`. New project tools: `proj_step_export_options` (and an
`options` argument on `proj_export_step`), `proj_get_channels`,
`proj_component_models`, `lib_library_manager`, `proj_output_panel`,
`proj_add_generated_document`, and a `structure` argument on
`proj_get_net_items`. The lint now refuses a routine defined twice in the
script, since the later one silently takes every call.

Run live on AD 26.11.1.5 with the 29 October script on a scratch copy of
RoundLED: the PNG matched the board; the JSON (9.8 MB) and ASCII dumps
were written beside it; net figures gave routed and signal lengths and
delays for VBUS and USB_DP; polar and cartesian grids were added, found at
a point and deleted; drill symbols shown and hidden; a cylinder and a
sphere body placed; a baseline dimension read back four text locations;
the file version read 5.01; both plane layers read unpoured. On C1 a
parameter was set (read back 43), removed (gone from the list), and a
second remove answered "no parameter of that name". STEP settings read
and wrote back unchanged. Channels, part models, net structure, the
Output panel and adding a generated document answered. The library
manager located a part, extracted an IntLib, listed 218 parts of an
installed library, and said `known_to_manager: false` with a reason for a
library it does not hold. `proj_run_output` still answers through its own
handler.

Not reachable from scripts, so not offered: adding guides or snap points
(only deletion is given), net resistance and current, the DRC checker's
options, variant groups, the file feature list, and running one output
generator without an OutJob (`DM_Generate_OutputFilesTo` wrote nothing for
Gerber or NC Drill, with or without the OutJob's own settings string; use
`proj_run_outjob`).

## 61. One call for many: `items` on every tool

Nearly every tool takes `items`, a list of its own arguments. The items'
requests are gathered into one bulk request per command, which the script
runs item by item inside one edit: one board edit for the whole call, and
on a schematic or SchLib one edit per document, joined as the items reach
it. `obj_modify`, `obj_create`, `obj_delete` and `lib_rename_component`
send their list to the batch handlers that `obj_batch_modify`,
`obj_batch_create`, `obj_batch_delete` and `lib_batch_rename` used; those
names are no longer advertised and still answer through `tool_invoke`,
with a note naming the new form. The family tools (`pcb_calc`, `audit`,
`pcb_dimension`, `pcb_body`) take a list of `{kind, args}`.

On a scratch board and a scratch sheet:

* `pcb_place_arc` with 20 items: one request, the reply lists 20 placed
  arcs in order, and one Undo (Ctrl+Z) removes all 20. A single
  `pcb_place_arc` call still takes one Undo.
* Three items, the second on a layer that does not exist: the first is
  placed, the second refused with the layer error, the third reported
  skipped.
* `sch_place_net_label` with 10 items on one sheet: one Undo removes all
  10. Ten items spread over two sheets: one Undo on each sheet.
* `proj_replace_component` with two items: each new part keeps its old
  UniqueId (read it back with `obj_query`); the board still links both.
* `obj_modify` with items of different filters on one symbol's pins: the
  same reply shape as `obj_batch_modify` gave, and one Undo.
* `tool_invoke('obj_batch_modify', ...)` still works and its reply notes
  `obj_modify(items=[...])`.
* Call one tool singly three times within ten seconds: the third reply
  carries `_hint_bulk` naming `items`.

## 62. Labels where Altium puts them, library scope, and a crash told at once

Measured 2026-10-30 on AD 26.11.1.5 with the 30 October scripts, on scratch
documents:

* `sch_place_sheet_symbol` at (1000,1000)-(2200,1600): the name reads back
  at (1000,1700) and the file name at (1000,1600), not at the sheet origin.
  Before, six symbols on a real sheet had all twelve texts at (0,0)/(0,100).
* `sch_autoposition_labels` on symbols already placed: 'rotate' (90 and
  back) positioned the labels and left a 1600 x 400 symbol's size and its
  right-side entry as they were; 'modify' and 'move' changed nothing.
  `obj_query` / `obj_modify` read and write `SheetName.Location.X/Y`,
  `SheetFileName.Location.X/Y`, `.Autoposition` and `SheetName.Text`.
* A part placed by `sch_place_components` or `sch_draw_template`, rotated:
  its designator and comment sit where Altium puts them for that rotation.
  Not yet measured live.
* `obj_modify eSheetSymbol set=Orientation=1`: refused as unknown, the
  loop keeps running (it halted the script with "Undeclared identifier:
  Orientation").
* A scratch SchLib, two symbols, one with a hidden power pin 11:
  `obj_query ePin scope=lib_component:HID_SYM` lists pin 11;
  `obj_modify ... filter=Designator=11 set=HiddenNetName=GND` reads back
  GND; `obj_modify eSchComponent scope=lib_component:HID_SYM
  set=ShowHiddenPins=true` matched 1 and the other symbols stayed false
  (it matched all 25 of a real library).
* A handler that halts the script: the call reports the crash as soon as
  the halt is confirmed, with the restart running behind it; other calls
  wait for the new loop. `relaunch --restart` prints, and traces as
  `RESTART`, how long each of its steps took. Not yet measured on a real
  halt.

Found while deploying: calling `LookupLibComponent` from Generic.pas left
the whole unit undeclared in Altium ("Undeclared identifier:
HandleGenericCommand" on every generic command) with no compile error
shown. A test refuses that call.

## 63. Rule limits, the copper read by kind and layer, pour names, the foreground guard

Measured 2026-10-31 on AD 26.11.1.5 with the 31 October scripts, on the
scratch board Tent.PcbDoc, closed without saving:

* A pour with Auto Naming on, `obj_modify ePolyObject set=Name=SCRATCH_POUR_A`:
  the pour reads the new name and `AutoGenerateName` false. Before, the
  reply said matched 1 and the old name stayed (#150).
* Mechanical17 enabled, `pcb_place_polygon_rect layer=Mechanical17`: the
  reply names `Mechanical17` (it said `Unknown`). `pcb_get_copper
  kinds=polygon layer=Mechanical17` finds that pour (#149);
  `kinds=via,polygon` lists the 2 vias then both pours; with the
  Mechanical17 pour selected, `selected=true` lists only it;
  `kinds=pad` is refused (BAD_KINDS) and `layer=Nowhere` (UNKNOWN_LAYER).
* `pcb_create_design_rule` made a Length rule (100 / 2500.5 mil), an
  Impedance rule (45 to 55 ohm) and Flight Time rules (rising 1.5 ns,
  falling 2 ns), each read back in `limits`. `pcb_set_rule_properties`
  in one call set the length maximum to 3000 mil, the impedance to 90 to
  110 ohm and the falling flight time to 0.75 ns; a flight time sent to
  the impedance rule came back in `limits_note`, unwritten. Altium's own
  descriptors read "Max=3000mil", "Maximum=750.0pS" and
  "Minimum=90.00Ohms Maximum=110.0Ohms".
* `app_click_menu` while Altium is busy with a bridge command waits for
  the window activation instead of refusing ("belongs to process
  None"); with the session locked or the remote desktop disconnected it
  refuses at once. Covered by simulated tests; the two real-window tests
  skip on this desktop, where the activation never stays pending.
* `pcb_get_vias offset=1 limit=1` on the 2-via scratch board: the second
  via, `count` 2, `returned` 1, `more` false; `net=GND` returns 0 of 0.
  Not yet checked on a board whose vias carry nets.
* A via placed with `pcb_place_vias`, then `app_save_document`:
  `was_modified` true (it said false after an edit made through the API).
  The via was deleted and the board saved again as it was.
* `app_get_active_document` with nothing focused now names the document
  in the editor's current view before the schematic server's last
  sheet. Not provoked live: the state needs no focused document.

## Still open, and not blocking

* **Redo doubles a placed schematic object.** Measured 2026-10-30 on a
  scratch sheet: one `sch_place_net_label` call, Undo (0 labels), Redo, 2
  labels at the same spot; ten in one list call came back as 20. The
  handlers register each new object twice, `RegisterSchObjectInContainer`
  and then the `SCHM_PrimitiveRegistration` robot message, and Altium's
  Redo replays both (44 sites). Undo itself removes the object once.
  Which of the two to drop needs a live check that Undo still takes it.

* **#23** font size: the importer places symbol text but not its height.
  Altium's font size is not in mils and the conversion is undocumented,
  so the source range is reported rather than guessed. Calibrating it
  needs a live measurement.
* **#39**, three items that need a live session before any change:
  a `>` or `<` in a `pcb_create_design_rule` scope raised a parse modal
  and left a rule Altium later called incorrectly defined; Board Outline
  Clearance rules read `Gap` 0 while the real value sits in a per-object
  matrix no documented property reaches; and 3D bodies deleted with
  `lib_delete_footprint_primitives` came back on the next save. The
  first is now refused before sending when a comparison sits unbracketed
  next to And, Or, Xor or Not (step 31), and the last is fixed by step 35.
  The Gap reading came from the descriptor, which says 0 whatever the rule
  holds; the readers now add `gap_mm` from the rule (step 36). The
  per-object table itself is still not exposed to scripts.


## Generic property rejection on Altium 21 (live verified)

The same reads stopped polling on Altium 26.10.1.6 before this change:
`Text` on a parameter set, `IsHidden` on a net label, and `IsHidden` on a
wire. `IsHidden` is now accepted only on pins, parameters, designators, sheet
names and sheet file names, the types whose interface declares it; `Text` is
refused on the types whose interface has none, wires among them. Repeat
steps 3 to 7 on AD 26 with the wire included.

Verified on AD 26.10.1.6 with the script from commit 57760ac: the three reads return
empty fields listed under `properties.unreadable`, `IsHidden=true` on a wire
is refused under `properties.unknown`, `IsHidden` reads and writes on
parameters, and the bridge answered `app_ping` after each call.

Recorded on Altium 21.4.1.30: `IsHidden` on `eNetLabel` and `Text` on
`eParameterSet` opened an undeclared-identifier dialog and stopped polling.
The fix guards reads and writes before accessing those members. It does not
claim a complete property capability table for every Altium version.

Unsupported query fields remain empty and are listed in
`properties.unreadable`; other requested fields remain available. Unsupported
writes are listed in `properties.unknown` and are not reported as applied.
Single creation returns `UNSUPPORTED_PROPERTY` with the property and object
type in the error message. Batch creation rejects only that item, without
registering it, and includes `reason: "UNSUPPORTED_PROPERTY"`, `property`,
`object_type`, and the zero-based `index` in `failures`. Valid items continue.
This preflight covers the guarded Text, IsHidden and TextColor combinations, not every
unknown property. Existing inputs and successful response fields are unchanged.

Use visible net labels with `Text`; do not hide them with `IsHidden`. Query a
parameter-set directive's coordinates without `Text`. The bridge does not
substitute a different field for the rejected property.

Live verification of commit `89c017f` on Altium 21.4.1.30 passed on
2026-09-29 after a full Altium restart. The deployed Generic.pas SHA-256 was
`36f9c0dde95de366a1b0ea60bc653793ed6f915c1c7714d08ee20bb791aef57e`.

- Unsupported reads returned empty fields plus unreadable diagnostics, while
  coordinates and the directive Name remained readable.
- Single writes returned success=false with unknown-property diagnostics;
  batch writes reported both rejected properties in their diagnostic list.
- Both single creates failed without changing object counts. The mixed batch
  returned created=2, failed=2, total=4, with failures at indexes 0 and 2 and
  the expected property/type. Queries confirmed only the valid objects existed.
- Ordinary parameter IsHidden read back false, true, then false after restore.
- Pings and valid queries succeeded after every rejecting call. No error modal
  or polling restart was needed. The recorded response assertions passed.
- Free Pascal execution remains separately pending: local FPC is absent and
  the upstream GitHub run is action_required with no jobs executed.

For future acceptance runs, close unrelated design documents first or retain
byte-for-byte backups. The bridge's save-all operation rewrites open clean
schematics too; it must not be treated as a no-op just because app_context
reports no unsaved documents.

Repeatable acceptance, on a disposable schematic only:

1. Save normal work, install the candidate scripts, restart Altium (scripts
   are cached), start the bridge, and confirm `app_ping` succeeds.
2. Create an ordinary visible net label and parameter-set directive. Record
   their count and positions. Query valid label Text and directive coordinates.
3. Query `IsHidden` on the label and `Text` on the directive, each alongside
   coordinates. Expect empty unsupported fields, unreadable diagnostics, and
   unchanged valid coordinates. Neither call may open a modal or stop polling.
   Do the same for `IsHidden` and `Text` on a wire.
4. Attempt those writes with `obj_modify`, single and with `items`. Expect
   failure diagnostics, unchanged objects, and no modal. These operations
   preflight the complete assignment list for known unsupported Text,
   IsHidden and TextColor pairs before writing any other field. Unknown
   names outside that preflight retain the existing partial-write semantics.
5. Attempt each unsupported pair through `obj_create`, single and with `items`.
   Verify failed objects are absent. In a batch containing invalid, valid,
   invalid, and valid items, expect two creations, two indexed failures and
   no property diagnostic leaking from one item into another.
6. Exercise IsHidden on an ordinary parameter supporting visibility and
   confirm read/write behaviour is unchanged. Restore its original value.
7. After each rejection, run `app_ping` and a valid query. Both must complete
   without dismissing an error or restarting the polling loop.

Record exact Altium and bridge versions, responses, and object counts. Offline
source guards and Free Pascal tests do not establish live Altium acceptance.

## TextColor property rejection

Recorded on AD 21.4.1.30 with script 2026-10-01 revision 1 on 2026-10-03:
`obj_query` on an existing `ePowerObject` with `Text,Color,TextColor` raised
`Undeclared identifier: TextColor` at the generic getter and stopped polling.
The setter had the same unguarded access. See issue #37.

The script from this change allows TextColor only on ports, sheet entries
and harness entries and uses their typed interfaces. Altium's [schematic API
reference](https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference)
documents those owners; power objects use Color. The bridge never substitutes
Color for a rejected TextColor request.

Queries retain other fields and report unsupported TextColor under
`properties.unreadable`, including project scope. Filters using a known
unsupported property return no match before reading it; an empty expected
value must not turn an unreadable property into a match. Modifications reject
the complete assignment list before applying a positional or other write.
Creation retains the existing single error and indexed batch-failure formats.

Repeatable live acceptance on disposable documents:

1. Reload the script project and verify ping reports the version at the top
   of this document. Create a
   power object, a net label, a port, and a sheet symbol with a sheet entry.
2. Query TextColor alongside valid fields on the power object and net label.
   Expect empty TextColor fields with unreadable diagnostics. Repeat with
   document and disposable-project scopes.
3. Filter the power object with `TextColor=` and `TextColor=128`. Expect no
   matches, including a delete using that filter; verify the object remains.
4. Attempt `Color=123|Location.X=100|TextColor=128` through single and batch
   modification. Expect rejection diagnostics and unchanged Color/position.
   Include a valid independent batch item and verify it completes.
5. Attempt single creation and a mixed batch containing two unsupported
   power-object items and two valid items. Expect no invalid objects, two
   indexed failures, and exactly two valid creations.
6. Read/write/read TextColor on a port and sheet entry. Save ONLY the scratch
   document, close/reopen it, and verify persistence. Harness-entry support
   must be checked on a fixture that actually contains one; it is not exposed
   by the current generic object-type mapping.
7. Ping and perform a valid query after each rejection. No error dialog,
   debugger stop, or polling restart may be needed. Restore editor focus and
   close the disposable documents at the end.

Live results for this candidate are recorded below once performed.
