{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Generic.pas - Generic primitives for the Altium integration bridge                        }
{ 5 primitives: run_process, query_objects, modify_objects,                  }
{               create_object, delete_objects                                }
{ These provide a thin, generic layer so Python controls all logic.          }
{..............................................................................}


{..............................................................................}
{ Object Type Mapping                                                         }
{..............................................................................}

{..............................................................................}
{ Where a pin actually connects.                                                }
{                                                                              }
{ ISch_Pin.Location IS THE BODY-SIDE ROOT, not the point a wire attaches to.   }
{ The electrical end is PinLength away along Orientation:                      }
{   0 = right (+x)   1 = up (+y)   2 = left (-x)   3 = down (-y)               }
{                                                                              }
{ MEASURED on a live sheet, with wires as the ground truth because a wire      }
{ endpoint is where the connection physically is. Four pins at Location.X      }
{ 3700, Orientation 2, PinLength 300: every attached wire vertex sat at        }
{ x 3400, and not one touched 3700. A right-facing pin read Location.X 4900    }
{ and connected at 5200, so the sign follows orientation.                      }
{                                                                              }
{ Shared by the obj_query property getter and by the pin dump, so the two      }
{ cannot drift into disagreeing about the same pin. Callers kept deriving      }
{ this by hand and getting the direction wrong, which is silent: the geometry  }
{ looks plausible and simply does not connect.                                 }
{..............................................................................}

Function PinEndX(RootX : Integer; Orient : Integer; PinLen : Integer) : Integer;
Begin
    Result := RootX;
    If Orient = 0 Then Result := RootX + PinLen
    Else If Orient = 2 Then Result := RootX - PinLen;
End;

Function PinEndY(RootY : Integer; Orient : Integer; PinLen : Integer) : Integer;
Begin
    Result := RootY;
    If Orient = 1 Then Result := RootY + PinLen
    Else If Orient = 3 Then Result := RootY - PinLen;
End;

Function ObjectTypeFromString(TypeStr : String) : Integer;
Var
    N, Raw : String;
Begin
    Result := -1;
    { NormalizeTypeName drops a leading 'e', so 'ellipse' arrived as
      'llipse' and matched nothing (issue #76). These two are read from the
      name before that drop; the Altium spellings 'eEllipse' and
      'eEllipticalArc' normalize to the bare names and match below. }
    Raw := LowerCase(TypeStr);
    Raw := StringReplace(Raw, ' ', '', -1);
    Raw := StringReplace(Raw, '_', '', -1);
    Raw := StringReplace(Raw, '-', '', -1);
    If Raw = 'ellipse' Then Result := eEllipse
    Else If Raw = 'ellipticalarc' Then Result := eEllipticalArc;
    If Result <> -1 Then Exit;
    N := NormalizeTypeName(TypeStr);
    If N = 'netlabel'        Then Result := eNetLabel
    Else If N = 'port'            Then Result := ePort
    Else If N = 'powerobject'     Then Result := ePowerObject
    Else If N = 'powerport'       Then Result := ePowerObject
    Else If N = 'schcomponent'    Then Result := eSchComponent
    { A schematic caller who writes "component" means this one. The PCB
      resolver maps the same word to eComponentObject, and obj_query
      tries the schematic first, so the document in front decides. }
    Else If N = 'component'       Then Result := eSchComponent
    Else If N = 'wire'            Then Result := eWire
    Else If N = 'bus'             Then Result := eBus
    Else If N = 'busentry'        Then Result := eBusEntry
    Else If N = 'parameter'       Then Result := eParameter
    Else If N = 'parameterset'    Then Result := eParameterSet
    Else If N = 'pin'             Then Result := ePin
    Else If N = 'label'           Then Result := eLabel
    Else If N = 'line'            Then Result := eLine
    Else If N = 'polyline'        Then Result := ePolyline
    Else If N = 'rectangle'       Then Result := eRectangle
    Else If N = 'sheetsymbol'     Then Result := eSheetSymbol
    Else If N = 'sheetentry'      Then Result := eSheetEntry
    Else If N = 'noerc'           Then Result := eNoERC
    Else If N = 'junction'        Then Result := eJunction
    Else If N = 'image'           Then Result := eImage
    { PLACEABLE BUT PREVIOUSLY UNRESOLVABLE. Every type below can be created
      by a sch_place_* tool, and none of them could be queried or deleted:
      the server could put an object on a sheet that it then could not find
      or remove. Reported 2026-09-21 by a user trying to delete a text frame,
      where obj_delete reported 0 processed and the type simply did not
      resolve. A guard now checks this list against every SchObjectFactory
      call, because six types had drifted out of it rather than one. }
    Else If N = 'textframe'       Then Result := eTextFrame
    Else If N = 'note'            Then Result := eNote
    Else If N = 'probe'           Then Result := eProbe
    Else If N = 'harnessconnector' Then Result := eHarnessConnector
    { A harness connector's entries, and the signal harness drawn from it. }
    Else If N = 'harnessentry'    Then Result := eHarnessEntry
    Else If N = 'signalharness'   Then Result := eSignalHarness
    { The objects of a harness wiring diagram. }
    Else If N = 'harnesscomponent' Then Result := eHarnessComponent
    Else If N = 'harnesspin'      Then Result := eHarnessPin
    Else If N = 'harnesswire'     Then Result := eHarnessWire
    Else If N = 'harnesssplice'   Then Result := eHarnessSplice
    Else If N = 'crosssheetconnector' Then Result := eCrossSheetConnector
    Else If N = 'compilemask'     Then Result := eCompileMask
    { A blanket is a polygon that carries its directive to every net it
      encloses. sch_place_blanket creates one; without this entry it could
      be placed but not listed, reshaped or deleted. }
    Else If N = 'blanket'         Then Result := eBlanket
    { Symbol graphics. The PCB resolver owns "polygon" and "arc" (a pour
      and a copper arc), and this resolver runs first whatever document is
      in front, so the schematic ones carry a sch_ prefix rather than
      taking those words from the board. }
    Else If N = 'schpolygon'      Then Result := ePolygon
    Else If N = 'scharc'          Then Result := eArc
    Else If N = 'schellipticalarc' Then Result := eEllipticalArc
    Else If N = 'schellipse'      Then Result := eEllipse
    Else If N = 'schbezier'       Then Result := eBezier
    { No board object is a bezier, so the bare word is free to take. }
    Else If N = 'bezier'          Then Result := eBezier
    Else If N = 'ellipse'         Then Result := eEllipse
    Else If N = 'ellipticalarc'   Then Result := eEllipticalArc
    Else If N = 'designator'      Then Result := eDesignator
    Else If N = 'roundrectangle'  Then Result := eRoundRectangle;
End;

{ What the refusal should have said. }
Function SchObjectTypeNames(Dummy : Integer): String;
Begin
    Result := 'eNetLabel, ePort, ePowerObject, eSchComponent, eWire, eBus, '
            + 'eBusEntry, eParameter, eParameterSet, ePin, eLabel, eLine, ePolyline, '
            + 'eRectangle, eSheetSymbol, eSheetEntry, eNoERC, eJunction, '
            + 'eImage, eTextFrame, eNote, eProbe, eHarnessConnector, '
            + 'eHarnessEntry, eSignalHarness, '
            + 'eCrossSheetConnector, eCompileMask, eBlanket, sch_polygon, sch_arc, '
            + 'sch_elliptical_arc, sch_ellipse, sch_bezier, bezier, ellipse, elliptical_arc, '
            + 'eDesignator, eRoundRectangle, eHarnessComponent, eHarnessPin, '
            + 'eHarnessWire, eHarnessSplice';
End;

{ True for the objects that carry a vertex array (GetState_VerticesCount,   }
{ GetState_Vertex). Asking any other object for them is an undeclared      }
{ identifier, which no Try catches: it halts the script. Reported on a     }
{ rectangle (2026-11-04), where a query for VerticesCount crashed the      }
{ bridge. eLine is answered from its two ends by SchLineEndProperty.       }
Function SchHasVertexArray(Obj : ISch_GraphicalObject) : Boolean;
Var
    Id : Integer;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Id := Obj.ObjectId;
    Result := (Id = eWire) Or (Id = eBus) Or (Id = ePolyline)
        Or (Id = ePolygon) Or (Id = eBezier) Or (Id = eBlanket)
        Or (Id = eSignalHarness);
End;

{ Parse 'x1,y1,x2,y2,...' (mils) into two lists. False when the count is   }
{ odd or a value is not a number: an outline is refused, never guessed.    }
Function ParseVertexList(Text : String; Xs, Ys : TStringList) : Boolean;
Var
    Remaining, Token : String;
    CommaPos, N : Integer;
Begin
    Result := False;
    Remaining := StringReplace(Trim(Text), ';', ',', MkSet(rfReplaceAll));
    N := 0;
    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            Token := Trim(Copy(Remaining, 1, CommaPos - 1));
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else
        Begin
            Token := Trim(Remaining);
            Remaining := '';
        End;
        If Not IsFloatStr(Token) Then Exit;
        If (N Mod 2) = 0 Then Xs.Add(Token) Else Ys.Add(Token);
        Inc(N);
    End;
    Result := (N > 0) And ((N Mod 2) = 0);
End;

{ Replace the whole vertex array of a polygon-family object. True when the }
{ count read back matches what was written.                                }
Function SetSchVertices(Obj : ISch_GraphicalObject; Text : String) : Boolean;
Var
    Xs, Ys : TStringList;
    I : Integer;
Begin
    Result := False;
    Xs := TStringList.Create;
    Ys := TStringList.Create;
    Try
        If Not ParseVertexList(Text, Xs, Ys) Then Exit;
        If Xs.Count < 3 Then Exit;
        Obj.VerticesCount := Xs.Count;
        For I := 1 To Xs.Count Do
            Obj.Vertex[I] := Point(MilsToCoordF(StrToFloatDef(Xs[I - 1], 0)),
                                   MilsToCoordF(StrToFloatDef(Ys[I - 1], 0)));
        Result := (Obj.VerticesCount = Xs.Count);
    Finally
        Ys.Free;
        Xs.Free;
    End;
End;

{ The refusal itself, in one place.                                          }
{                                                                             }
{ "Unknown object type: Sheet" is true and leaves the caller guessing, and    }
{ the guesses observed were Component, Sheet, Sheet Symbol and SheetSymbol    }
{ in a row. Three of those four now resolve. The fourth does not, because a   }
{ sheet is a DOCUMENT rather than an object on one, and saying so is more     }
{ use than accepting it and returning nothing.                                }
Function UnknownObjectTypeMessage(TypeStr : String) : String;
Var
    N : String;
Begin
    N := NormalizeTypeName(TypeStr);
    Result := 'Unknown object type: ' + TypeStr + '. ';
    If (N = 'sheet') Or (N = 'schdoc') Or (N = 'document') Then
        Result := Result + 'A sheet is a document, not an object on one. '
                + 'For the symbol that REFERENCES a child sheet use '
                + 'eSheetSymbol; to list documents use proj_list_documents. '
    Else If (N = 'net') Or (N = 'netclass') Then
        Result := Result + 'Nets are not schematic objects. Read them with '
                + 'proj_get_nets, or query eNetLabel for the labels. '
    Else If (N = 'polygon') Or (N = 'poly') Or (N = 'track') Or (N = 'via') Then
        Result := Result + 'That is a PCB type and this document is a '
                + 'schematic. The pcb_ tools act on an open .PcbDoc. ';
    Result := Result + 'Schematic types: ' + SchObjectTypeNames(0)
            + '. PCB types: ' + PCBObjectTypeNames(0)
            + '. Spelling is forgiving: case, spaces, underscores and the '
            + 'leading "e" are all optional.';
End;

{ The refusal for a tool that works on schematic objects only (obj_create,   }
{ obj_copy). It lists only the types the tool can take. The shared refusal   }
{ above also lists the PCB types, so obj_create given eComponentBodyObject   }
{ answered "unknown" while naming eComponentBodyObject among the types. A    }
{ PCB type given to obj_create is pointed at the pcb_ tool that places it.   }
Function SchOnlyTypeMessage(TypeStr : String; ToolName : String) : String;
Var
    PcbType : Integer;
    Placer : String;
Begin
    PcbType := ObjectTypeFromStringPCB(TypeStr);
    If PcbType = -1 Then
        Result := 'Unknown object type: ' + TypeStr + '. '
    Else
    Begin
        Result := ToolName + ' works on schematic objects only, and ' + TypeStr
                + ' is a PCB type. ';
        Placer := '';
        If PcbType = eTrackObject Then Placer := 'pcb_place_tracks';
        If PcbType = ePadObject Then Placer := 'pcb_place_pad';
        If PcbType = eViaObject Then Placer := 'pcb_place_vias';
        If PcbType = eComponentObject Then Placer := 'pcb_place_components';
        If PcbType = eArcObject Then Placer := 'pcb_place_arc';
        If PcbType = eFillObject Then Placer := 'pcb_place_fill';
        If PcbType = eTextObject Then Placer := 'pcb_place_text';
        If PcbType = ePolyObject Then Placer := 'pcb_place_polygon_rect';
        If PcbType = eRegionObject Then Placer := 'pcb_place_region';
        If PcbType = eRuleObject Then Placer := 'pcb_create_design_rule';
        If PcbType = eDimensionObject Then Placer := 'pcb_place_dimension';
        If PcbType = eComponentBodyObject Then Placer := 'pcb_place_3d_body';
        If PcbType = eConnectionObject Then
            Result := Result + 'A connection is a ratsnest line drawn from the '
                    + 'nets, and nothing places one. ';
        If (ToolName = 'obj_create') And (Placer <> '') Then
            Result := Result + 'On a board it is placed with ' + Placer + '. '
        Else
            Result := Result + 'The pcb_ tools act on an open .PcbDoc. ';
    End;
    Result := Result + 'Types ' + ToolName + ' can take: ' + SchObjectTypeNames(0)
            + '. Spelling is forgiving: case, spaces, underscores and the '
            + 'leading "e" are all optional.';
End;

{..............................................................................}
{ Generic Property Getter                                                     }
{ Returns string value of a named property from a schematic object.          }
{ Coordinates are returned in mils.                                          }
{..............................................................................}

{..............................................................................}
{ Typed component property helper, extract designator / comment text by    }
{ casting to ISch_Component first. Required because `Obj.Designator` on a    }
{ base ISch_GraphicalObject fails to compile, DelphiScript cannot late-bind }
{ properties that return compound interfaces (ISch_Parameter) the way it can  }
{ for primitive returns.                                                      }
{..............................................................................}

{ Which schematic objects carry Text, IsHidden and Orientation.                }
{                                                                              }
{ Neither lives on the base ISch_GraphicalObject, and reaching for one on a    }
{ type that lacks it raises "Undeclared identifier". That is not catchable:    }
{ the script engine surfaces it as a modal before any Try/Except runs, so the  }
{ polling loop stops. Issue #22, reproduced with                              }
{ obj_query(object_type='ePort', properties='...,Orientation') on AD25.       }
{                                                                              }
{ A DENYLIST, deliberately. The types below are the ones there is evidence     }
{ for: PlaceAPort.pas in the scripting reference builds a Port from Name,      }
{ Style, IOType, Alignment and Width and never touches Text or Orientation,    }
{ and ReplaceSchObjects.pas reads a cross-sheet connector's Orientation in     }
{ order to map it onto Port.Style. Enumerating every type that DOES have       }
{ these would be guesswork and would silently break queries that work today.  }
{ IsHidden is the exception, below.                                            }

Function SchObjectHasText(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := True;
    If Obj = Nil Then Exit;
    { None of these has Text in Altium's interface: ports and sheet entries
      (named by Name), parameter-set directives (measured on AD 21 and
      AD 26), pins, the containers, and wiring and graphics. The round
      rectangle, ellipse, polyline, polygon, arcs and bezier were missing:
      Text asked of a round rectangle raised "Undeclared identifier: Text"
      on AD 25.8.1 and the polling loop stopped. }
    If (Obj.ObjectId = ePort) Or (Obj.ObjectId = eSheetEntry)
        Or (Obj.ObjectId = eParameterSet) Or (Obj.ObjectId = ePin)
        Or (Obj.ObjectId = eSchComponent) Or (Obj.ObjectId = eSheetSymbol)
        Or (Obj.ObjectId = eHarnessConnector) Or (Obj.ObjectId = eProbe)
        Or (Obj.ObjectId = eHarnessEntry) Or (Obj.ObjectId = eSignalHarness)
        Or (Obj.ObjectId = eWire) Or (Obj.ObjectId = eBus)
        Or (Obj.ObjectId = eBusEntry) Or (Obj.ObjectId = eJunction)
        Or (Obj.ObjectId = eNoERC) Or (Obj.ObjectId = eLine)
        Or (Obj.ObjectId = eRectangle) Or (Obj.ObjectId = eImage)
        Or (Obj.ObjectId = eRoundRectangle) Or (Obj.ObjectId = eEllipse)
        Or (Obj.ObjectId = ePolyline) Or (Obj.ObjectId = ePolygon)
        Or (Obj.ObjectId = eArc) Or (Obj.ObjectId = eEllipticalArc)
        Or (Obj.ObjectId = eBezier) Then
        Result := False;
End;

{ Pin text settings (issue #77): the name and designator labels' visibility,
  font and colour. Only a pin has them, so callers check ePin first. The
  typed view is taken here because the generic Obj does not carry them. }
Function IsSchPinProp(PropName : String) : Boolean;
Begin
    Result := (PropName = 'ShowName') Or (PropName = 'ShowDesignator')
        Or (PropName = 'Name_FontMode') Or (PropName = 'Name_CustomFontID')
        Or (PropName = 'Name_CustomColor') Or (PropName = 'Designator_FontMode')
        Or (PropName = 'Designator_CustomFontID') Or (PropName = 'Designator_CustomColor');
End;

{ NO-ERC MARKERS: what they suppress. ISch_NoERC carries IsActive,         }
{ SuppressAll, StrErrorKindSetToSuppress, StrConnectionPairsToSuppress and  }
{ StrSuppressableErrors. Read here under the                              }
{ names ErrorKindSetToSuppress, ConnectionPairsToSuppress and              }
{ SuppressableErrors. Callers check the type first: the names exist on no  }
{ other object, and an undeclared one halts the loop.                      }
Function ReadNoErcProp(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    N : ISch_NoERC;
Begin
    Result := '';
    N := Obj;
    If PropName = 'SuppressAll' Then
    Begin
        Try Result := BoolToJsonStr(N.SuppressAll); Except End;
    End
    Else If PropName = 'IsActive' Then
    Begin
        Try Result := BoolToJsonStr(N.IsActive); Except End;
    End
    Else If PropName = 'ErrorKindSetToSuppress' Then
    Begin
        Try Result := N.StrErrorKindSetToSuppress; Except End;
    End
    Else If PropName = 'ConnectionPairsToSuppress' Then
    Begin
        Try Result := N.StrConnectionPairsToSuppress; Except End;
    End
    Else If PropName = 'SuppressableErrors' Then
    Begin
        Try Result := N.StrSuppressableErrors; Except End;
    End
    Else If PropName = 'Symbol' Then
    Begin
        Try Result := IntToStr(N.Symbol); Except End;
    End
    Else If PropName = 'Orientation' Then
    Begin
        Try Result := IntToStr(N.Orientation); Except End;
    End;
End;

Procedure WriteNoErcProp(Obj : ISch_GraphicalObject; PropName : String; Value : String);
Var
    N : ISch_NoERC;
    B : Boolean;
Begin
    N := Obj;
    B := (LowerCase(Value) = 'true') Or (Value = '1');
    If PropName = 'SuppressAll' Then
    Begin
        Try N.SuppressAll := B; Except NoteWriteFailed('NoERC.SuppressAll'); End;
    End
    Else If PropName = 'IsActive' Then
    Begin
        Try N.IsActive := B; Except NoteWriteFailed('NoERC.IsActive'); End;
    End
    Else If PropName = 'ErrorKindSetToSuppress' Then
    Begin
        Try N.StrErrorKindSetToSuppress := Value; Except NoteWriteFailed('NoERC.ErrorKindSetToSuppress'); End;
    End
    Else If PropName = 'ConnectionPairsToSuppress' Then
    Begin
        Try N.StrConnectionPairsToSuppress := Value; Except NoteWriteFailed('NoERC.ConnectionPairsToSuppress'); End;
    End
    Else If (PropName = 'Symbol') And IsIntStr(Value) Then
    Begin
        Try N.Symbol := StrToIntDef(Value, 0); Except NoteWriteFailed('NoERC.Symbol'); End;
    End
    Else If (PropName = 'Orientation') And IsIntStr(Value) Then
    Begin
        Try N.Orientation := StrToIntDef(Value, 0); Except NoteWriteFailed('NoERC.Orientation'); End;
    End;
End;

{ A No-ERC marker as it comes from the factory suppressed nothing: placed  }
{ on a pin by sch_place_no_erc or obj_batch_create eNoERC, the ERC still   }
{ reported that pin (measured 2026-10-23, an unconnected pin and an output }
{ meeting a power pin). Every creator makes a new marker active and        }
{ suppressing all violations, Altium's own default for a placed No ERC,   }
{ before the caller's properties, which can still narrow it.              }
Procedure ApplyNoErcDefaults(Obj : ISch_GraphicalObject);
Begin
    If Obj = Nil Then Exit;
    If Obj.ObjectId <> eNoERC Then Exit;
    WriteNoErcProp(Obj, 'IsActive', 'true');
    WriteNoErcProp(Obj, 'SuppressAll', 'true');
End;

Function ReadSchPinProp(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    P : ISch_Pin;
Begin
    Result := '';
    P := Obj;
    Try
        If PropName = 'ShowName' Then Result := BoolToJsonStr(P.ShowName)
        Else If PropName = 'ShowDesignator' Then Result := BoolToJsonStr(P.ShowDesignator)
        Else If PropName = 'Name_FontMode' Then Result := IntToStr(P.Name_FontMode)
        Else If PropName = 'Name_CustomFontID' Then Result := IntToStr(P.Name_CustomFontID)
        Else If PropName = 'Name_CustomColor' Then Result := IntToStr(P.Name_CustomColor)
        Else If PropName = 'Designator_FontMode' Then Result := IntToStr(P.Designator_FontMode)
        Else If PropName = 'Designator_CustomFontID' Then Result := IntToStr(P.Designator_CustomFontID)
        Else If PropName = 'Designator_CustomColor' Then Result := IntToStr(P.Designator_CustomColor)
        Else If PropName = 'Description' Then Result := P.Description;
    Except
        Result := '';
    End;
End;

Function WriteSchPinProp(Obj : ISch_GraphicalObject; PropName : String; Value : String) : Boolean;
Var
    P : ISch_Pin;
Begin
    Result := True;
    P := Obj;
    Try
        SchBeginModify(P);
        If PropName = 'ShowName' Then P.ShowName := StrToBool(Value)
        Else If PropName = 'ShowDesignator' Then P.ShowDesignator := StrToBool(Value)
        Else If PropName = 'Name_FontMode' Then P.Name_FontMode := StrToIntDef(Value, 0)
        Else If PropName = 'Name_CustomFontID' Then P.Name_CustomFontID := StrToIntDef(Value, 0)
        Else If PropName = 'Name_CustomColor' Then P.Name_CustomColor := StrToIntDef(Value, 0)
        Else If PropName = 'Designator_FontMode' Then P.Designator_FontMode := StrToIntDef(Value, 0)
        Else If PropName = 'Designator_CustomFontID' Then P.Designator_CustomFontID := StrToIntDef(Value, 0)
        Else If PropName = 'Designator_CustomColor' Then P.Designator_CustomColor := StrToIntDef(Value, 0)
        Else If PropName = 'Description' Then P.Description := Value;
        SchEndModify(P);
    Except
        Result := False;
    End;
End;

{ A parameter's or designator's display settings: ShowName (name shown    }
{ beside the value), Autoposition and GraphicallyLocked (the Locked tick:  }
{ the label cannot be dragged). Only these two types carry them, so the   }
{ callers check SchHasParamDisplay first; asked of another object the     }
{ names are undeclared and halt the loop. ShowName on a pin stays with    }
{ the pin settings above.                                                 }
Function IsSchParamDisplayProp(PropName : String) : Boolean;
Begin
    Result := (PropName = 'Autoposition') Or (PropName = 'GraphicallyLocked');
End;

Function SchHasParamDisplay(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = eParameter) Or (Obj.ObjectId = eDesignator);
End;

Function ReadSchParamDisplayProp(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    P : ISch_Parameter;
Begin
    Result := '';
    P := Obj;
    Try
        If PropName = 'ShowName' Then Result := BoolToJsonStr(P.ShowName)
        Else If PropName = 'Autoposition' Then Result := BoolToJsonStr(P.Autoposition)
        Else If PropName = 'GraphicallyLocked' Then Result := BoolToJsonStr(P.GraphicallyLocked);
    Except
        Result := '';
    End;
End;

Function WriteSchParamDisplayProp(Obj : ISch_GraphicalObject; PropName : String; Value : String) : Boolean;
Var
    P : ISch_Parameter;
    Flag : Boolean;
Begin
    Result := True;
    P := Obj;
    Flag := (LowerCase(Value) = 'true') Or (Value = '1');
    Try
        SchBeginModify(P);
        If PropName = 'ShowName' Then P.ShowName := Flag
        Else If PropName = 'Autoposition' Then P.Autoposition := Flag
        Else If PropName = 'GraphicallyLocked' Then P.GraphicallyLocked := Flag;
        SchEndModify(P);
    Except
        Result := False;
    End;
End;

{ IsSolid belongs to the filled shapes. Asked of a line it raised             }
{ "Undeclared identifier: IsSolid" on AD 25.8.1, a modal Try/Except cannot  }
{ contain, and the polling loop stopped (issue #253). An ALLOWLIST of the    }
{ types this unit already reads or writes IsSolid on (the symbol dump:       }
{ rectangle, round rectangle, polygon, ellipse; the text frame writer), plus }
{ the polyline (read live) and the types built on them (a note is a text     }
{ frame, a blanket a polygon). Anything else answers unreadable. A sheet    }
{ symbol too: the file saves IsSolid on it, and it is the Fill Color tick   }
{ box of its Properties panel (issue #273).                                 }
Function SchObjectHasIsSolid(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = eRectangle) Or (Obj.ObjectId = eRoundRectangle)
        Or (Obj.ObjectId = ePolygon) Or (Obj.ObjectId = ePolyline)
        Or (Obj.ObjectId = eEllipse) Or (Obj.ObjectId = eTextFrame)
        Or (Obj.ObjectId = eNote) Or (Obj.ObjectId = eBlanket)
        Or (Obj.ObjectId = eSheetSymbol);
End;

{ Sheet symbol and sheet entry enums as the file spells them, both ways. A  }
{ name that is none of them answers -1, so the setter can refuse it rather  }
{ than write a default.                                                      }
Function SheetSymbolTypeToStr(T : Integer) : String;
Begin
    If T = eSheetSymbolType_Normal Then Result := 'Normal'
    Else If T = eSheetSymbolType_DeviceSheet Then Result := 'DeviceSheet'
    Else If T = eSheetSymbolType_DesignItem Then Result := 'DesignItem'
    Else Result := IntToStr(T);
End;

{ Normal and DeviceSheet only: a DesignItem (Managed) sheet symbol points   }
{ at a server item, which a type change alone does not supply.              }
Function SheetSymbolTypeOrdinal(S : String) : Integer;
Var
    N : String;
Begin
    N := LowerCase(Trim(S));
    If (N = 'normal') Or (N = 'local') Then Result := eSheetSymbolType_Normal
    Else If (N = 'devicesheet') Or (N = 'device') Then Result := eSheetSymbolType_DeviceSheet
    Else Result := -1;
End;

Function BusTextStyleToStr(T : Integer) : String;
Begin
    If T = eBusTextStyle_Full Then Result := 'Full'
    Else If T = eBusTextStyle_Prefix Then Result := 'Prefix'
    Else Result := IntToStr(T);
End;

Function BusTextStyleOrdinal(S : String) : Integer;
Var
    N : String;
Begin
    N := LowerCase(Trim(S));
    If N = 'full' Then Result := eBusTextStyle_Full
    Else If N = 'prefix' Then Result := eBusTextStyle_Prefix
    Else Result := -1;
End;

Function ArrowKindToStr(T : Integer) : String;
Begin
    If T = eArrowKind_RectAndTri Then Result := 'Block & Triangle'
    Else If T = eArrowKind_Triangle Then Result := 'Triangle'
    Else If T = eArrowKind_Arrow Then Result := 'Arrow'
    Else If T = eArrowKind_ArrowTail Then Result := 'Arrow Tail'
    Else Result := IntToStr(T);
End;

Function ArrowKindOrdinal(S : String) : Integer;
Var
    N : String;
Begin
    N := StringReplace(LowerCase(Trim(S)), ' ', '', -1);
    If (N = 'block&triangle') Or (N = 'blockandtriangle') Or (N = 'rectandtri') Then
        Result := eArrowKind_RectAndTri
    Else If N = 'triangle' Then Result := eArrowKind_Triangle
    Else If N = 'arrow' Then Result := eArrowKind_Arrow
    Else If N = 'arrowtail' Then Result := eArrowKind_ArrowTail
    Else Result := -1;
End;

{ A sheet entry's Side by name or by the ordinal the reader gives. }
Function SheetEntrySideOrdinal(S : String) : Integer;
Var
    N : String;
Begin
    N := LowerCase(Trim(S));
    If N = 'left' Then Result := eLeftSide
    Else If N = 'right' Then Result := eRightSide
    Else If N = 'top' Then Result := eTopSide
    Else If N = 'bottom' Then Result := eBottomSide
    Else If (N = IntToStr(eLeftSide)) Or (N = IntToStr(eRightSide))
         Or (N = IntToStr(eTopSide)) Or (N = IntToStr(eBottomSide)) Then
        Result := StrToInt(N)
    Else Result := -1;
End;

{ Put a sheet entry on its symbol's edge from its Side and DistanceFromTop, }
{ as the placer does. Setting the two alone leaves the entry's Location    }
{ where it was until the sheet is reloaded, so a wire drawn meanwhile goes  }
{ to the old point. The symbol's Location is its top-left corner.           }
Procedure PositionSheetEntry(Sym : ISch_SheetSymbol; Entry : ISch_SheetEntry);
Var
    EdgeX, EdgeY, Dist : Double;
    Side : Integer;
Begin
    Side := Entry.Side;
    Dist := CoordToMilsF(Entry.DistanceFromTop);
    EdgeX := CoordToMilsF(Sym.Location.X);
    EdgeY := CoordToMilsF(Sym.Location.Y);
    If Side = eLeftSide Then EdgeY := EdgeY - Dist;
    If Side = eRightSide Then
    Begin
        EdgeX := EdgeX + CoordToMilsF(Sym.XSize);
        EdgeY := EdgeY - Dist;
    End;
    If Side = eTopSide Then EdgeX := EdgeX + Dist;
    If Side = eBottomSide Then
    Begin
        EdgeX := EdgeX + Dist;
        EdgeY := EdgeY - CoordToMilsF(Sym.YSize);
    End;
    Entry.Location := Point(MilsToCoordF(EdgeX), MilsToCoordF(EdgeY));
End;

{ Transparent and LineStyle, ALLOWLISTS (an absent member halts the       }
{ script), and only where Altium SAVES the value. MEASURED on AD 25.8.1 on }
{ a scratch sheet, written, saved and reopened: Transparent kept on the    }
{ rectangle and ellipse (and the blanket and polygon, read here before);   }
{ the round rectangle, text frame and note take it, read it back and lose  }
{ it on save. LineStyle kept on the line and rectangle (and the blanket);  }
{ the text frame and note lose it, a round rectangle does not save it, an  }
{ ellipse and a polygon have none.                                         }
Function SchObjectHasTransparent(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = eBlanket) Or (Obj.ObjectId = ePolygon)
        Or (Obj.ObjectId = eRectangle) Or (Obj.ObjectId = eEllipse);
End;

Function SchObjectHasLineStyle(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = eBlanket) Or (Obj.ObjectId = eLine)
        Or (Obj.ObjectId = eRectangle);
End;

{ XSize and YSize, an ALLOWLIST. MEASURED on AD 25.8.1: a generic query     }
{ of a schematic image asking for XSize raised "Undeclared identifier:      }
{ XSize" in an error box no Try catches, and the polling loop stopped       }
{ (issue #267). Only the types this unit already sizes that way carry them: }
{ the sheet symbol and the harness connector (their placers set XSize /     }
{ YSize). Anything else answers unreadable / not written.                   }
Function SchObjectHasSize(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = eSheetSymbol) Or (Obj.ObjectId = eHarnessConnector);
End;

{ IsHidden belongs to pins and to the complex text types (parameters,
  designators, sheet names, sheet file names) and to nothing else in
  Altium's interface, so this is an ALLOWLIST: the set is short and fixed.
  On a net label and on a wire it raised an undeclared identifier on AD 21
  and AD 26 alike, a modal that Try/Except cannot contain. }
Function SchObjectHasIsHidden(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = ePin) Or (Obj.ObjectId = eParameter)
        Or (Obj.ObjectId = eDesignator) Or (Obj.ObjectId = eSheetName)
        Or (Obj.ObjectId = eSheetFileName);
End;

{ TextColor belongs to ports, sheet entries and harness entries, not to
  ISch_GraphicalObject or ISch_Label. Reading it on a power object raises
  an undeclared-identifier modal BEFORE Try/Except can recover (AD 21.4).
  https://www.altium.com/documentation/altium-dxp-developer/schematic-api-design-objects-interfaces-reference }
Function SchObjectHasTextColor(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = ePort) Or (Obj.ObjectId = eSheetEntry)
        Or (Obj.ObjectId = eHarnessEntry) Or (Obj.ObjectId = eTextFrame)
        Or (Obj.ObjectId = eNote);
End;

{ FontId belongs to ISch_Label and the types built on it (net labels,
  power objects, cross-sheet connectors, parameters, designators, sheet
  names and file names), to ISch_Port and to ISch_TextFrame (text frames,
  notes): the interfaces that carry Get/SetState_FontId.
  On a sheet entry it raised an undeclared-identifier modal and stopped the
  polling loop (AD 25.8), so this is an allowlist. }
Function SchObjectHasFontId(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = eLabel) Or (Obj.ObjectId = eNetLabel)
        Or (Obj.ObjectId = ePowerObject) Or (Obj.ObjectId = eCrossSheetConnector)
        Or (Obj.ObjectId = eParameter) Or (Obj.ObjectId = eDesignator)
        Or (Obj.ObjectId = eSheetName) Or (Obj.ObjectId = eSheetFileName)
        Or (Obj.ObjectId = ePort) Or (Obj.ObjectId = eTextFrame)
        Or (Obj.ObjectId = eNote);
End;

{ A sheet entry's or harness entry's label font is TextFontID, not FontId. }
Function SchObjectHasTextFontId(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = eSheetEntry) Or (Obj.ObjectId = eHarnessEntry);
End;

{ The font that most objects of one kind on a sheet carry (TextFontID for }
{ a sheet entry, FontId for the other kinds that have one); 0 when the    }
{ sheet has no such object.                                                }
Function SheetFontFor(SchDoc : ISch_Document; Kind : Integer) : Integer;
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Entry : ISch_SheetEntry;
    Fonts, Hits : TStringList;
    At, Most, FontPick : Integer;
Begin
    Result := 0;
    If SchDoc = Nil Then Exit;
    { Fonts holds each font id seen once; Hits, at the same index, how often. }
    Fonts := TStringList.Create;
    Hits := TStringList.Create;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(Kind));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            FontPick := 0;
            If SchObjectHasTextFontId(Obj) Then
            Begin
                Entry := Obj;
                Try FontPick := Entry.TextFontID; Except FontPick := 0; End;
            End
            Else If SchObjectHasFontId(Obj) Then
            Begin
                Try FontPick := Obj.FontId; Except FontPick := 0; End;
            End;
            If FontPick > 0 Then
            Begin
                At := Fonts.IndexOf(IntToStr(FontPick));
                If At >= 0 Then
                    Hits.Strings[At] := IntToStr(StrToInt(Hits.Strings[At]) + 1)
                Else
                Begin
                    Fonts.Add(IntToStr(FontPick));
                    Hits.Add('1');
                End;
            End;
            Obj := Iter.NextSchObject;
        End;
        Most := 0;
        For At := 0 To Fonts.Count - 1 Do
            If StrToInt(Hits.Strings[At]) > Most Then
            Begin
                Most := StrToInt(Hits.Strings[At]);
                Result := StrToInt(Fonts.Strings[At]);
            End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
        Fonts.Free;
        Hits.Free;
    End;
End;

{ Which font a new port, power port, text frame or sheet entry gets: the   }
{ font_id asked for; otherwise what that kind already uses on the sheet;  }
{ otherwise the sheet's net-label font. 0 keeps Altium's default (font 1,  }
{ Times New Roman 10), which every new object used to get. Called before  }
{ the object is made, so it does not count itself.                       }
Function NewObjectFontId(SchDoc : ISch_Document; Kind : Integer; Params : String) : Integer;
Begin
    Result := StrToIntDef(ExtractJsonValue(Params, 'font_id'), 0);
    If Result <= 0 Then Result := SheetFontFor(SchDoc, Kind);
    If (Result <= 0) And (Kind <> eNetLabel) Then Result := SheetFontFor(SchDoc, eNetLabel);
End;

{ Only these types answer to Name. Anything else (a free text, a note,
  a net label) faults with an undeclared-identifier modal that
  Try/Except cannot contain, which stops the bridge (issue #74). This is
  an allowlist: an unknown type is treated as having no Name. }
Function SchObjectHasName(Obj : ISch_GraphicalObject) : Boolean;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Result := (Obj.ObjectId = ePin) Or (Obj.ObjectId = ePort) Or (Obj.ObjectId = eSheetEntry)
        Or (Obj.ObjectId = eHarnessEntry)
        Or (Obj.ObjectId = eParameter) Or (Obj.ObjectId = eDesignator)
        Or (Obj.ObjectId = eSheetName) Or (Obj.ObjectId = eSheetFileName);
End;

{ SCHEMATIC PROPERTIES OF ONE OBJECT TYPE.                                    }
{ Pins: the four pin symbols and their line width (Altium's numbers),        }
{ HiddenNetName, and DefinedFunctions (the pin's functions, ';'-separated;   }
{ writing replaces the list). Parts: ShowHiddenPins, ShowHiddenFields,       }
{ PinsMoveable, PartIdLocked, DisplayFieldNames, OverideColors (Altium's     }
{ spelling) and PinColor. Pins also: where the name and designator sit      }
{ (Name_PositionMode, Name_CustomPosition_Margin, ..._RotationAnchor,        }
{ ..._RotationRelative, and the same for Designator_). Parameters:          }
{ ReadOnlyState, ParamType, AllowLibrarySynchronize,                        }
{ AllowDatabaseSynchronize. Ports: AutoSize, OverrideDisplayString,         }
{ HarnessColor, ConnectedEnd (read only: 0 none, 1 the port's location end, }
{ 2 the far end, 3 both). Parameters also report NameIsReadOnly, ValueIsReadOnly,     }
{ IsRule, IsSystemParameter and IsConfigurable (read only).                 }
{ Pins also DefaultValue and FormalType; parts also                         }
{ LibraryPath (the library the symbol came from) and AliasAsText.           }

{ 1 a pin property, 2 a part property, 3 a parameter property, 4 a port     }
{ property, 0 none of these.                                                }
Function SchExtraOwner(PropName : String) : Integer;
Begin
    Result := 0;
    If (PropName = 'Symbol_Inner') Or (PropName = 'Symbol_Outer') Or
       (PropName = 'Symbol_InnerEdge') Or (PropName = 'Symbol_OuterEdge') Or
       (PropName = 'Symbol_LineWidth') Or (PropName = 'HiddenNetName') Or
       (PropName = 'DefinedFunctions') Or
       (PropName = 'Name_PositionMode') Or (PropName = 'Name_CustomPosition_Margin') Or
       (PropName = 'Name_CustomPosition_RotationAnchor') Or
       (PropName = 'Name_CustomPosition_RotationRelative') Or
       (PropName = 'Designator_PositionMode') Or
       (PropName = 'Designator_CustomPosition_Margin') Or
       (PropName = 'Designator_CustomPosition_RotationAnchor') Or
       (PropName = 'Designator_CustomPosition_RotationRelative') Or
       (PropName = 'DefaultValue') Or (PropName = 'FormalType') Then Result := 1
    Else If (PropName = 'ShowHiddenPins') Or (PropName = 'ShowHiddenFields') Or
       (PropName = 'PinsMoveable') Or (PropName = 'PartIdLocked') Or
       (PropName = 'DisplayFieldNames') Or (PropName = 'OverideColors') Or
       (PropName = 'PinColor') Or (PropName = 'LibraryPath') Or
       (PropName = 'AliasAsText') Then Result := 2
    Else If (PropName = 'ReadOnlyState') Or (PropName = 'ParamType') Or
       (PropName = 'AllowLibrarySynchronize') Or
       (PropName = 'AllowDatabaseSynchronize') Or (PropName = 'NameIsReadOnly') Or
       (PropName = 'ValueIsReadOnly') Or (PropName = 'IsRule') Or
       (PropName = 'IsSystemParameter') Or (PropName = 'IsConfigurable') Then Result := 3
    Else If (PropName = 'AutoSize') Or (PropName = 'OverrideDisplayString') Or
       (PropName = 'HarnessColor') Or (PropName = 'ConnectedEnd') Then Result := 4
    Else If (PropName = 'WordWrap') Or (PropName = 'ClipToRect') Or
       (PropName = 'TextMargin') Then Result := 5
    Else If PropName = 'Author' Then Result := 6
    Else If PropName = 'ShowBorder' Then Result := 7
    Else If PropName = 'ShowNetName' Then Result := 8
    Else If PropName = 'CrossSheetStyle' Then Result := 9
    Else If PropName = 'Height' Then Result := 10;
End;

{ True when Obj is the type that carries PropName. }
Function SchExtraFits(Obj : ISch_GraphicalObject; PropName : String) : Boolean;
Var
    Owner : Integer;
Begin
    Owner := SchExtraOwner(PropName);
    Result := ((Owner = 1) And (Obj.ObjectId = ePin))
        Or ((Owner = 2) And (Obj.ObjectId = eSchComponent))
        Or ((Owner = 3) And (Obj.ObjectId = eParameter))
        Or ((Owner = 4) And (Obj.ObjectId = ePort))
        Or ((Owner = 5) And ((Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote)))
        Or ((Owner = 6) And (Obj.ObjectId = eNote))
        { A note is always drawn with its border: ShowBorder written false  }
        { read back true (AD 25.8.1), so only a text frame takes it.         }
        Or ((Owner = 7) And (Obj.ObjectId = eTextFrame))
        Or ((Owner = 8) And ((Obj.ObjectId = ePowerObject)
            Or (Obj.ObjectId = eCrossSheetConnector) Or (Obj.ObjectId = ePort)))
        Or ((Owner = 9) And (Obj.ObjectId = eCrossSheetConnector))
        Or ((Owner = 10) And (Obj.ObjectId = ePort));
End;

{ A whole-number extra (0 or more), a true / false one, or neither (text). }
Function SchExtraIsInt(PropName : String) : Boolean;
Begin
    Result := (PropName = 'Symbol_Inner') Or (PropName = 'Symbol_Outer') Or
        (PropName = 'Symbol_InnerEdge') Or (PropName = 'Symbol_OuterEdge') Or
        (PropName = 'Symbol_LineWidth') Or (PropName = 'PinColor') Or
        (PropName = 'Name_PositionMode') Or (PropName = 'Name_CustomPosition_Margin') Or
        (PropName = 'Name_CustomPosition_RotationAnchor') Or
        (PropName = 'Name_CustomPosition_RotationRelative') Or
        (PropName = 'Designator_PositionMode') Or
        (PropName = 'Designator_CustomPosition_Margin') Or
        (PropName = 'Designator_CustomPosition_RotationAnchor') Or
        (PropName = 'Designator_CustomPosition_RotationRelative') Or
        (PropName = 'ReadOnlyState') Or (PropName = 'ParamType') Or
        (PropName = 'HarnessColor') Or (PropName = 'FormalType') Or
        (PropName = 'ConnectedEnd') Or (PropName = 'CrossSheetStyle');
End;

{ A length in mils, decimals kept, not below 0. }
Function SchExtraIsMils(PropName : String) : Boolean;
Begin
    Result := (PropName = 'TextMargin') Or (PropName = 'Height');
End;

Function SchExtraIsBool(PropName : String) : Boolean;
Begin
    Result := ((SchExtraOwner(PropName) = 2) And (PropName <> 'PinColor')
            And (PropName <> 'LibraryPath') And (PropName <> 'AliasAsText'))
        Or (PropName = 'AllowLibrarySynchronize') Or
        (PropName = 'AllowDatabaseSynchronize') Or (PropName = 'AutoSize') Or
        ((SchExtraOwner(PropName) = 5) And (PropName <> 'TextMargin')) Or
        (PropName = 'ShowBorder') Or (PropName = 'ShowNetName');
End;

{ PIN FUNCTIONS ON ALTIUM 25. GetDefinedFunctionsCount, GetDefinedFunction, }
{ ClearDefinedFunctions and AddDefinedFunction are Altium 26 names: none is  }
{ in Altium 25's script engine, and calling one there stops the polling     }
{ loop with an "Undeclared identifier" error box that no Try catches        }
{ (measured on AD 25.8.1: the first read of DefinedFunctions did). So the   }
{ version is read BEFORE any of them. Altium 25 reads the functions from    }
{ the pin's own record (ExportToParameters: PINDEFINEDFUNCTIONSCOUNT=n and  }
{ PINDEFINEDFUNCTION1..n) and writes them the way its Pin Properties do it  }
{ from a name: the list as the name, SetState_FunctionsFromName, the name   }
{ put back.                                                                 }
{ It takes the major version wanted: a routine with no parameters is listed }
{ in Altium's Run Script window as one a user can run.                      }
Function AltiumMajorAtLeast(Wanted : Integer) : Boolean;
Var
    Ver : String;
    Major : Integer;
Begin
    Ver := '';
    Try Ver := Client.GetProductVersion; Except Ver := ''; End;
    Major := 0;
    If Pos('.', Ver) > 1 Then Major := StrToIntDef(Copy(Ver, 1, Pos('.', Ver) - 1), 0);
    Result := Major >= Wanted;
End;

{ The value of KEY in a pin record "K1=V1|K2=V2|...", '' when absent. }
Function PinRecordValue(Rec : String; Key : String) : String;
Var
    P : Integer;
    Rest : String;
Begin
    Result := '';
    Rest := '|' + Rec + '|';
    P := Pos('|' + Key + '=', Rest);
    If P = 0 Then Exit;
    Rest := Copy(Rest, P + Length(Key) + 2, Length(Rest));
    Result := Copy(Rest, 1, Pos('|', Rest) - 1);
End;

{ Altium 25: the functions from the pin's record, ';'-separated. }
Function PinFunctionsFromRecord(Pin : ISch_Pin) : String;
Var
    Rec, Item : String;
    I, Count : Integer;
Begin
    Result := '';
    Rec := '';
    Try Rec := Pin.ExportToParameters; Except Rec := ''; End;
    Count := StrToIntDef(PinRecordValue(Rec, 'PINDEFINEDFUNCTIONSCOUNT'), 0);
    For I := 1 To Count Do
    Begin
        Item := PinRecordValue(Rec, 'PINDEFINEDFUNCTION' + IntToStr(I));
        If Item = '' Then Continue;
        If Result <> '' Then Result := Result + ';';
        Result := Result + Item;
    End;
End;

{ A pin's functions, ';'-separated. }
Function PinFunctionsText(Pin : ISch_Pin) : String;
Var
    I, Count : Integer;
    Item : String;
Begin
    Result := '';
    If Not AltiumMajorAtLeast(26) Then
    Begin
        Result := PinFunctionsFromRecord(Pin);
        Exit;
    End;
    { GetDefinedFunction counts from 1 (measured on AD 26.11: index 0      }
    { answers nothing and the last function is at Count).                  }
    Count := Pin.GetDefinedFunctionsCount;
    For I := 1 To Count Do
    Begin
        Item := '';
        Try Item := Pin.GetDefinedFunction(I); Except Item := ''; End;
        If Item = '' Then Continue;
        If Result <> '' Then Result := Result + ';';
        Result := Result + Item;
    End;
End;

{ The value, '' when Obj is not the type that carries it. }
Function SchGetExtra(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    Pin : ISch_Pin;
    Comp : ISch_Component;
    Par : ISch_Parameter;
    Port : ISch_Port;
    TF : ISch_TextFrame;
    NoteObj : ISch_Note;
    PowerObj : ISch_PowerObject;
    CSC : ISch_CrossSheetConnector;
Begin
    Result := '';
    If Not SchExtraFits(Obj, PropName) Then Exit;
    If Obj.ObjectId = ePin Then
    Begin
        Pin := Obj;
        If PropName = 'Symbol_Inner' Then Result := IntToStr(Pin.Symbol_Inner)
        Else If PropName = 'Symbol_Outer' Then Result := IntToStr(Pin.Symbol_Outer)
        Else If PropName = 'Symbol_InnerEdge' Then Result := IntToStr(Pin.Symbol_InnerEdge)
        Else If PropName = 'Symbol_OuterEdge' Then Result := IntToStr(Pin.Symbol_OuterEdge)
        Else If PropName = 'Symbol_LineWidth' Then Result := IntToStr(Pin.Symbol_LineWidth)
        Else If PropName = 'HiddenNetName' Then Result := Pin.HiddenNetName
        Else If PropName = 'DefinedFunctions' Then Result := PinFunctionsText(Pin)
        Else If PropName = 'Name_PositionMode' Then Result := IntToStr(Pin.Name_PositionMode)
        Else If PropName = 'Name_CustomPosition_Margin' Then Result := IntToStr(Pin.Name_CustomPosition_Margin)
        Else If PropName = 'Name_CustomPosition_RotationAnchor' Then Result := IntToStr(Pin.Name_CustomPosition_RotationAnchor)
        Else If PropName = 'Name_CustomPosition_RotationRelative' Then Result := IntToStr(Pin.Name_CustomPosition_RotationRelative)
        Else If PropName = 'Designator_PositionMode' Then Result := IntToStr(Pin.Designator_PositionMode)
        Else If PropName = 'Designator_CustomPosition_Margin' Then Result := IntToStr(Pin.Designator_CustomPosition_Margin)
        Else If PropName = 'Designator_CustomPosition_RotationAnchor' Then Result := IntToStr(Pin.Designator_CustomPosition_RotationAnchor)
        Else If PropName = 'Designator_CustomPosition_RotationRelative' Then Result := IntToStr(Pin.Designator_CustomPosition_RotationRelative)
        Else If PropName = 'DefaultValue' Then Result := Pin.DefaultValue
        Else If PropName = 'FormalType' Then Result := IntToStr(Pin.FormalType);
    End
    Else If Obj.ObjectId = eParameter Then
    Begin
        Par := Obj;
        If PropName = 'ReadOnlyState' Then Result := IntToStr(Par.ReadOnlyState)
        Else If PropName = 'ParamType' Then Result := IntToStr(Par.ParamType)
        Else If PropName = 'AllowLibrarySynchronize' Then Result := BoolToJsonStr(Par.AllowLibrarySynchronize)
        Else If PropName = 'AllowDatabaseSynchronize' Then Result := BoolToJsonStr(Par.AllowDatabaseSynchronize)
        Else If PropName = 'NameIsReadOnly' Then Result := BoolToJsonStr(Par.NameIsReadOnly)
        Else If PropName = 'ValueIsReadOnly' Then Result := BoolToJsonStr(Par.ValueIsReadOnly)
        Else If PropName = 'IsRule' Then Result := BoolToJsonStr(Par.IsRule)
        Else If PropName = 'IsSystemParameter' Then Result := BoolToJsonStr(Par.IsSystemParameter)
        Else If PropName = 'IsConfigurable' Then Result := BoolToJsonStr(Par.IsConfigurable);
    End
    Else If Obj.ObjectId = ePort Then
    Begin
        Port := Obj;
        If PropName = 'AutoSize' Then Result := BoolToJsonStr(Port.AutoSize)
        Else If PropName = 'OverrideDisplayString' Then Result := Port.OverrideDisplayString
        Else If PropName = 'HarnessColor' Then Result := IntToStr(Port.HarnessColor)
        Else If PropName = 'ConnectedEnd' Then Result := IntToStr(Port.ConnectedEnd)
        Else If PropName = 'ShowNetName' Then Result := BoolToJsonStr(Port.ShowNetName)
        Else If PropName = 'Height' Then Result := FloatToJsonStr(CoordToMilsF(Port.Height));
    End
    { Power ports and off-sheet connectors: whether the net name is drawn, }
    { and the connector's left / right (Altium's number).                  }
    Else If Obj.ObjectId = ePowerObject Then
    Begin
        PowerObj := Obj;
        If PropName = 'ShowNetName' Then Result := BoolToJsonStr(PowerObj.ShowNetName);
    End
    Else If Obj.ObjectId = eCrossSheetConnector Then
    Begin
        CSC := Obj;
        If PropName = 'ShowNetName' Then Result := BoolToJsonStr(CSC.ShowNetName)
        Else If PropName = 'CrossSheetStyle' Then Result := IntToStr(CSC.CrossSheetStyle);
    End
    { A note is a text frame: the border switch, word wrap, clip and margin  }
    { are ISch_TextFrame members (issue: notes and frames had no route to   }
    { them). TextMargin in mils, decimals kept.                              }
    Else If (Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote) Then
    Begin
        TF := Obj;
        If PropName = 'ShowBorder' Then Result := BoolToJsonStr(TF.ShowBorder)
        Else If PropName = 'WordWrap' Then Result := BoolToJsonStr(TF.WordWrap)
        Else If PropName = 'ClipToRect' Then Result := BoolToJsonStr(TF.ClipToRect)
        Else If PropName = 'TextMargin' Then Result := FloatToJsonStr(CoordToMilsF(TF.TextMargin))
        Else If PropName = 'Author' Then
        Begin
            NoteObj := Obj;
            Result := NoteObj.Author;
        End;
    End
    Else
    Begin
        Comp := Obj;
        If PropName = 'ShowHiddenPins' Then Result := BoolToJsonStr(Comp.ShowHiddenPins)
        Else If PropName = 'ShowHiddenFields' Then Result := BoolToJsonStr(Comp.ShowHiddenFields)
        Else If PropName = 'PinsMoveable' Then Result := BoolToJsonStr(Comp.PinsMoveable)
        Else If PropName = 'PartIdLocked' Then Result := BoolToJsonStr(Comp.PartIdLocked)
        Else If PropName = 'DisplayFieldNames' Then Result := BoolToJsonStr(Comp.DisplayFieldNames)
        Else If PropName = 'OverideColors' Then Result := BoolToJsonStr(Comp.OverideColors)
        Else If PropName = 'PinColor' Then Result := IntToStr(Comp.PinColor)
        Else If PropName = 'LibraryPath' Then Result := Comp.LibraryPath
        Else If PropName = 'AliasAsText' Then Result := Comp.AliasAsText;
    End;
End;

{ Write, then read back: True only when the value read equals the value     }
{ asked for. A number property refuses a value that is not a whole number,  }
{ a switch one that is not true / false.                                    }
Function SchSetExtra(Obj : ISch_GraphicalObject; PropName : String; Value : String) : Boolean;
Var
    Pin : ISch_Pin;
    Comp : ISch_Component;
    Par : ISch_Parameter;
    Port : ISch_Port;
    TF : ISch_TextFrame;
    NoteObj : ISch_Note;
    PowerObj : ISch_PowerObject;
    CSC : ISch_CrossSheetConnector;
    Rest, Item, Wanted, Slashed, OldName : String;
    SemiPos, N : Integer;
    B, IsBool, IsInt, Native : Boolean;
    Mils : Double;
Begin
    Result := False;
    If Not SchExtraFits(Obj, PropName) Then Exit;
    { Read only: what Altium says about the parameter, not a setting. }
    If (PropName = 'NameIsReadOnly') Or (PropName = 'ValueIsReadOnly') Or
       (PropName = 'IsRule') Or (PropName = 'IsSystemParameter') Or
       (PropName = 'IsConfigurable') Then Exit;
    { Altium works out which end of a port is wired; a write did not hold. }
    If PropName = 'ConnectedEnd' Then Exit;
    IsInt := SchExtraIsInt(PropName);
    IsBool := SchExtraIsBool(PropName);
    N := 0;
    B := False;
    If IsInt Then
    Begin
        If Not IsIntStr(Value) Then Exit;
        N := StrToInt(Value);
        If N < 0 Then Exit;
    End
    Else If IsBool Then
    Begin
        If LowerCase(Value) = 'true' Then B := True
        Else If LowerCase(Value) <> 'false' Then Exit;
    End;
    { A frame's text margin, a port's height: mils, decimals kept, not   }
    { below 0. CrossSheetStyle is left (0) or right (1).                  }
    If (PropName = 'CrossSheetStyle') And (N > 1) Then Exit;
    Mils := 0;
    If SchExtraIsMils(PropName) Then
    Begin
        If Not IsFloatStr(Value) Then Exit;
        Mils := StrToFloatDef(Value, -1);
        If Mils < 0 Then Exit;
    End;
    If Obj.ObjectId = ePin Then
    Begin
        Pin := Obj;
        If PropName = 'Symbol_Inner' Then Pin.Symbol_Inner := N
        Else If PropName = 'Symbol_Outer' Then Pin.Symbol_Outer := N
        Else If PropName = 'Symbol_InnerEdge' Then Pin.Symbol_InnerEdge := N
        Else If PropName = 'Symbol_OuterEdge' Then Pin.Symbol_OuterEdge := N
        Else If PropName = 'Symbol_LineWidth' Then Pin.Symbol_LineWidth := N
        Else If PropName = 'HiddenNetName' Then Pin.HiddenNetName := Value
        Else If PropName = 'DefinedFunctions' Then
        Begin
            Native := AltiumMajorAtLeast(26);
            If Native Then Pin.ClearDefinedFunctions;
            Wanted := '';
            Slashed := '';
            Rest := Value;
            While Rest <> '' Do
            Begin
                SemiPos := Pos(';', Rest);
                If SemiPos = 0 Then Begin Item := Rest; Rest := ''; End
                Else Begin Item := Copy(Rest, 1, SemiPos - 1); Rest := Copy(Rest, SemiPos + 1, Length(Rest)); End;
                If Trim(Item) <> '' Then
                Begin
                    If Native Then Pin.AddDefinedFunction(Trim(Item));
                    If Wanted <> '' Then Begin Wanted := Wanted + ';'; Slashed := Slashed + '/'; End;
                    Wanted := Wanted + Trim(Item);
                    Slashed := Slashed + Trim(Item);
                End;
            End;
            If Not Native Then
            Begin
                { MEASURED on AD 25.8.1: the split leaves the list alone for a  }
                { name with no '/' ("X1"), so a trailing '/' is added ("X1/"  }
                { gives X1); and no name clears the list ("/", " " and ""  }
                { all leave it), so an empty list is refused, as is a '/'     }
                { inside one function, which would split it in two.          }
                If (Wanted = '') Or (Pos('/', Wanted) > 0) Then Exit;
                OldName := Pin.Name;
                Try
                    Pin.Name := Slashed + '/';
                    Pin.SetState_FunctionsFromName;
                Finally
                    Pin.Name := OldName;
                End;
            End;
        End
        Else If PropName = 'Name_PositionMode' Then Pin.Name_PositionMode := N
        Else If PropName = 'Name_CustomPosition_Margin' Then Pin.Name_CustomPosition_Margin := N
        Else If PropName = 'Name_CustomPosition_RotationAnchor' Then Pin.Name_CustomPosition_RotationAnchor := N
        Else If PropName = 'Name_CustomPosition_RotationRelative' Then Pin.Name_CustomPosition_RotationRelative := N
        Else If PropName = 'Designator_PositionMode' Then Pin.Designator_PositionMode := N
        Else If PropName = 'Designator_CustomPosition_Margin' Then Pin.Designator_CustomPosition_Margin := N
        Else If PropName = 'Designator_CustomPosition_RotationAnchor' Then Pin.Designator_CustomPosition_RotationAnchor := N
        Else If PropName = 'Designator_CustomPosition_RotationRelative' Then Pin.Designator_CustomPosition_RotationRelative := N
        Else If PropName = 'DefaultValue' Then Pin.DefaultValue := Value
        Else If PropName = 'FormalType' Then Pin.FormalType := N;
    End
    Else If Obj.ObjectId = eParameter Then
    Begin
        Par := Obj;
        If PropName = 'ReadOnlyState' Then Par.ReadOnlyState := N
        Else If PropName = 'ParamType' Then Par.ParamType := N
        Else If PropName = 'AllowLibrarySynchronize' Then Par.AllowLibrarySynchronize := B
        Else If PropName = 'AllowDatabaseSynchronize' Then Par.AllowDatabaseSynchronize := B;
    End
    Else If Obj.ObjectId = ePort Then
    Begin
        Port := Obj;
        If PropName = 'AutoSize' Then Port.AutoSize := B
        Else If PropName = 'OverrideDisplayString' Then Port.OverrideDisplayString := Value
        Else If PropName = 'HarnessColor' Then Port.HarnessColor := N
        Else If PropName = 'ShowNetName' Then Port.ShowNetName := B
        Else If PropName = 'Height' Then Port.Height := MilsToCoordF(Mils);
    End
    Else If Obj.ObjectId = ePowerObject Then
    Begin
        PowerObj := Obj;
        If PropName = 'ShowNetName' Then PowerObj.ShowNetName := B;
    End
    Else If Obj.ObjectId = eCrossSheetConnector Then
    Begin
        CSC := Obj;
        If PropName = 'ShowNetName' Then CSC.ShowNetName := B
        Else If PropName = 'CrossSheetStyle' Then CSC.CrossSheetStyle := N;
    End
    Else If (Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote) Then
    Begin
        TF := Obj;
        If PropName = 'ShowBorder' Then TF.ShowBorder := B
        Else If PropName = 'WordWrap' Then TF.WordWrap := B
        Else If PropName = 'ClipToRect' Then TF.ClipToRect := B
        Else If PropName = 'TextMargin' Then TF.TextMargin := MilsToCoordF(Mils)
        Else If PropName = 'Author' Then
        Begin
            NoteObj := Obj;
            NoteObj.Author := Value;
        End;
    End
    Else
    Begin
        Comp := Obj;
        If PropName = 'ShowHiddenPins' Then Comp.ShowHiddenPins := B
        Else If PropName = 'ShowHiddenFields' Then Comp.ShowHiddenFields := B
        Else If PropName = 'PinsMoveable' Then Comp.PinsMoveable := B
        Else If PropName = 'PartIdLocked' Then Comp.PartIdLocked := B
        Else If PropName = 'DisplayFieldNames' Then Comp.DisplayFieldNames := B
        Else If PropName = 'OverideColors' Then Comp.OverideColors := B
        Else If PropName = 'PinColor' Then Comp.PinColor := N
        Else If PropName = 'LibraryPath' Then Comp.LibraryPath := Value
        Else If PropName = 'AliasAsText' Then Comp.AliasAsText := Value;
    End;
    If IsInt Then Result := SchGetExtra(Obj, PropName) = IntToStr(N)
    Else If IsBool Then Result := SchGetExtra(Obj, PropName) = BoolToJsonStr(B)
    Else If PropName = 'DefinedFunctions' Then Result := SchGetExtra(Obj, PropName) = Wanted
    Else If SchExtraIsMils(PropName) Then Result := SchGetExtra(Obj, PropName) = FloatToJsonStr(Mils)
    Else Result := SchGetExtra(Obj, PropName) = Value;
End;

{ Preflight only the known unsupported property/type pairs. Preserve the
  existing treatment of other names; do not guess a complete capability map. }
Function UnsupportedSchProperty(Obj : ISch_GraphicalObject; SetStr : String) : String;
Var
    Remaining, Assignment, PropName : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Assignment := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Assignment := Remaining;
            Remaining := '';
        End;
        EqPos := Pos('=', Assignment);
        If EqPos > 0 Then
        Begin
            PropName := Copy(Assignment, 1, EqPos - 1);
            If ((PropName = 'Text') And (Not SchObjectHasText(Obj)))
                Or ((PropName = 'IsHidden') And (Not SchObjectHasIsHidden(Obj)))
                Or ((PropName = 'TextColor') And (Not SchObjectHasTextColor(Obj)))
                Or ((PropName = 'FontId') And (Not SchObjectHasFontId(Obj)))
                Or ((PropName = 'TextFontId') And (Not SchObjectHasTextFontId(Obj)))
                Or (((PropName = 'DisplayMode') Or (PropName = 'FootprintPinMap')
                    Or (PropName = 'ComponentKind'))
                    And (Obj.ObjectId <> eSchComponent))
                Or (((PropName = 'SwapIdPart') Or (PropName = 'SwapIdPin')
                    Or (PropName = 'SwapIdPair') Or (PropName = 'SwapIdPartPin'))
                    And (Obj.ObjectId <> ePin))
                Or ((SchExtraOwner(PropName) > 0) And (Not SchExtraFits(Obj, PropName))) Then
            Begin
                Result := PropName;
                Exit;
            End;
        End;
    End;
End;

{ The types whose interface declares Orientation, by Altium's SDK: a type   }
{ outside this list has none, and reading or writing it halts the script   }
{ ("Undeclared identifier: Orientation", which no Try catches). It was a   }
{ list of the types WITHOUT it, ports and sheet entries, and a sheet symbol }
{ rotated through obj_modify took the bridge down. A port carries its      }
{ direction in Style, a sheet entry in Side.                               }
Function SchObjectHasOrientation(Obj : ISch_GraphicalObject) : Boolean;
Var
    Id : Integer;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    Id := Obj.ObjectId;
    Result := (Id = eSchComponent) Or (Id = eNetLabel) Or (Id = ePowerObject)
        Or (Id = eCrossSheetConnector) Or (Id = ePin) Or (Id = eParameter)
        Or (Id = eDesignator) Or (Id = eLabel) Or (Id = eNoERC)
        Or (Id = eParameterSet) Or (Id = eProbe) Or (Id = eSheetName)
        Or (Id = eSheetFileName);
End;

Function GetSchComponentSubText(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    C : ISch_Component;
Begin
    Result := '';
    If Obj.ObjectId <> eSchComponent Then Exit;
    Try
        C := Obj;
        If PropName = 'Designator' Then Result := C.Designator.Text
        Else If PropName = 'Comment' Then Result := C.Comment.Text;
    Except
        // Cast unexpectedly failed despite ObjectId check, surface via counter.
        RecordCastError('GetSchComponentSubText:' + PropName);
        Result := '';
    End;
End;

Procedure SetSchComponentSubText(Obj : ISch_GraphicalObject; PropName : String; Value : String);
Var
    C : ISch_Component;
Begin
    If Obj.ObjectId <> eSchComponent Then Exit;
    Try
        C := Obj;
        If PropName = 'Designator' Then C.Designator.Text := Value
        Else If PropName = 'Comment' Then C.Comment.Text := Value;
    Except
        RecordCastError('SetSchComponentSubText:' + PropName);
    End;
End;

{ The text label an owner carries: a placed part's Designator or Comment, a  }
{ sheet symbol's name ('Designator', its SheetName) or file name            }
{ ('Filename', its SheetFileName). Nil for any other pairing.               }
Function SchLabelOf(Obj : ISch_GraphicalObject; Which : String) : ISch_GraphicalObject;
Var
    C : ISch_Component;
    SS : ISch_SheetSymbol;
Begin
    Result := Nil;
    If Obj = Nil Then Exit;
    If Obj.ObjectId = eSchComponent Then
    Begin
        C := Obj;
        If Which = 'Designator' Then Result := C.Designator;
        If Which = 'Comment' Then Result := C.Comment;
    End;
    If Obj.ObjectId = eSheetSymbol Then
    Begin
        SS := Obj;
        If Which = 'Designator' Then Result := SS.SheetName;
        If Which = 'Filename' Then Result := SS.SheetFileName;
    End;
End;

{ A label property name split: 'Designator.Location.X' is the Designator's }
{ Location.X. SheetName. and SheetFileName. are a sheet symbol's spellings  }
{ of Designator. and Filename.. '' when PropName is not one.               }
Function SchLabelPropWhich(PropName : String) : String;
Var
    Prefix, Field : String;
    P : Integer;
Begin
    Result := '';
    P := Pos('.', PropName);
    If P = 0 Then Exit;
    Prefix := Copy(PropName, 1, P - 1);
    Field := Copy(PropName, P + 1, 99);
    If (Field <> 'IsHidden') And (Field <> 'Location.X') And (Field <> 'Location.Y')
        And (Field <> 'Autoposition') And (Field <> 'FontId') And (Field <> 'Color') Then Exit;
    If (Prefix = 'Designator') Or (Prefix = 'SheetName') Then Result := 'Designator';
    If Prefix = 'Comment' Then Result := 'Comment';
    If (Prefix = 'Filename') Or (Prefix = 'SheetFileName') Then Result := 'Filename';
End;

Function SchLabelPropField(PropName : String) : String;
Begin
    Result := Copy(PropName, Pos('.', PropName) + 1, 99);
End;

{ IsHidden, position, Autoposition and font of a label (see SchLabelOf).    }
{ Field is 'IsHidden', 'Location.X', 'Location.Y', 'Autoposition' or       }
{ 'FontId' (issue #229: a sheet symbol's name and file name had no font    }
{ route); positions are in mils. '' when the object carries no such label. }
Function GetSchComponentLabelProp(Obj : ISch_GraphicalObject; Which, Field : String) : String;
Var
    Lbl : ISch_GraphicalObject;
Begin
    Result := '';
    Try
        Lbl := SchLabelOf(Obj, Which);
        If Lbl = Nil Then Exit;
        If Field = 'IsHidden' Then Result := BoolToJsonStr(Lbl.IsHidden);
        If Field = 'Location.X' Then Result := FloatToJsonStr(CoordToMilsF(Lbl.Location.X));
        If Field = 'Location.Y' Then Result := FloatToJsonStr(CoordToMilsF(Lbl.Location.Y));
        If Field = 'Autoposition' Then Result := BoolToJsonStr(Lbl.Autoposition);
        If Field = 'FontId' Then Result := IntToStr(Lbl.FontId);
        { The label's own colour (the file saves Color on the name and file- }
        { name records); a colour, like a font, the panel sets per label.     }
        If Field = 'Color' Then Result := IntToStr(Lbl.Color);
    Except
        RecordCastError('GetSchComponentLabelProp:' + Which + '.' + Field);
        Result := '';
    End;
End;

{ The write side. Moving a label turns its auto-position off, or Altium puts }
{ it back; the same reason lib_set_label_formats does it in a library.       }
{ Autoposition=true hands it back to Altium and puts it where Altium would  }
{ (SchAutopositionLabels).                                                  }
Function SetSchComponentLabelProp(Obj : ISch_GraphicalObject; Which, Field, Value : String) : Boolean;
Var
    Lbl : ISch_GraphicalObject;
    Loc : TLocation;
    Flag : Boolean;
Begin
    Result := False;
    Try
        Lbl := SchLabelOf(Obj, Which);
        If Lbl = Nil Then Exit;
        { A font id is a positive whole number from the font manager; anything }
        { else is refused, never written as font 1.                            }
        If (Field = 'FontId') And (StrToIntDef(Value, 0) < 1) Then Exit;
        { A colour is a whole number 0 .. $FFFFFF (BGR); anything else is   }
        { refused, never written as black.                                  }
        If (Field = 'Color') And ((StrToIntDef(Value, -1) < 0)
            Or (StrToIntDef(Value, -1) > 16777215)) Then Exit;
        Flag := (UpperCase(Value) = 'TRUE') Or (Value = '1');
        SchBeginModify(Lbl);
        If Field = 'IsHidden' Then Lbl.IsHidden := Flag;
        If Field = 'Autoposition' Then Lbl.Autoposition := Flag;
        If Field = 'FontId' Then Lbl.FontId := StrToIntDef(Value, 0);
        If Field = 'Color' Then Lbl.Color := StrToIntDef(Value, 0);
        If (Field = 'Location.X') Or (Field = 'Location.Y') Then
        Begin
            Loc := Lbl.Location;
            If Field = 'Location.X' Then Loc.X := MilsToCoordF(StrToFloatDef(Value, 0));
            If Field = 'Location.Y' Then Loc.Y := MilsToCoordF(StrToFloatDef(Value, 0));
            Lbl.Location := Loc;
            { Left on, Altium puts the moved label back where it was. }
            Try Lbl.Autoposition := False; Except NoteWriteFailed('Label.Autoposition off after a move'); End;
        End;
        SchEndModify(Lbl);
        Result := True;
    Except
        RecordCastError('SetSchComponentLabelProp:' + Which + '.' + Field);
    End;
End;

{ Where a label is and whether Altium positions it, as JSON. }
Function SchLabelJson(Lbl : ISch_GraphicalObject) : String;
Var
    Auto : String;
Begin
    Result := 'null';
    If Lbl = Nil Then Exit;
    Auto := 'null';
    Try Auto := BoolToJsonStr(Lbl.Autoposition); Except End;
    Result := '{"x":' + FloatToJsonStr(CoordToMilsF(Lbl.Location.X))
        + ',"y":' + FloatToJsonStr(CoordToMilsF(Lbl.Location.Y))
        + ',"autoposition":' + Auto + '}';
End;

{ Hand a part's Designator and Comment, or a sheet symbol's name and file    }
{ name, back to Altium's auto-position, and make Altium position them.       }
{                                                                            }
{ A label placed by script keeps the place it was created at until Altium    }
{ repositions it, which an editor does on a rotation and not on a move: the  }
{ names of scripted sheet symbols sat at the sheet origin, the bottom-left   }
{ corner, and a person fixed a part's by rotating it and rotating it back.   }
{ Method names the trigger: 'rotate' (90 and back, about the owner's own     }
{ location), 'move' (one grid step and back), 'modify' (only the modify      }
{ bracket). Each inside SCHM_BeginModify / SCHM_EndModify.                  }
{                                                                            }
{ MEASURED 2026-10-30 (AD 26.11.1.5, script sheet symbols at (3000,4000) and }
{ a 1600 x 400 one with a right-side entry): the names sat at (0,100) and   }
{ (0,0) with Autoposition already True. 'modify' and 'move' left them there  }
{ (MoveByXY does not even carry a sheet symbol's labels); 'rotate' put the   }
{ name 100 mil above the top-left corner and the file name on it, and left  }
{ the size and the entry as they were.                                       }
{                                                                            }
{ Force turns every label's Autoposition on first, for a caller asking to    }
{ have them positioned. A placement passes False: a label the library        }
{ positioned by hand keeps its place, since the rotation and its reverse     }
{ bring it back where it was.                                                }
Procedure SchAutopositionLabels(Obj : ISch_GraphicalObject; Method : String;
    Force : Boolean);
Var
    Lbl : ISch_GraphicalObject;
    Center : TLocation;
Begin
    If Obj = Nil Then Exit;
    If Force Then
    Begin
        Lbl := SchLabelOf(Obj, 'Designator');
        If Lbl <> Nil Then
        Begin
            Try Lbl.Autoposition := True; Except NoteWriteFailed('Designator.Autoposition'); End;
        End;
        Lbl := SchLabelOf(Obj, 'Comment');
        If Lbl <> Nil Then
        Begin
            Try Lbl.Autoposition := True; Except NoteWriteFailed('Comment.Autoposition'); End;
        End;
        Lbl := SchLabelOf(Obj, 'Filename');
        If Lbl <> Nil Then
        Begin
            Try Lbl.Autoposition := True; Except NoteWriteFailed('Filename.Autoposition'); End;
        End;
    End;
    SchBeginModify(Obj);
    If Method = 'rotate' Then
    Begin
        Center := Obj.Location;
        Obj.RotateBy90(Center, eRotate90);
        Obj.RotateBy90(Center, eRotate270);
    End;
    If Method = 'move' Then
    Begin
        Obj.MoveByXY(MilsToCoordF(100), 0);
        Obj.MoveByXY(-MilsToCoordF(100), 0);
    End;
    SchEndModify(Obj);
    Try Obj.GraphicallyInvalidate; Except End;
End;

{ sch_autoposition_labels: the parts and sheet symbols on the active sheet,  }
{ or those named (designators and sheet names, '|'-separated), get their     }
{ labels positioned by Altium. The reply gives every label before and after, }
{ so the caller sees what moved.                                             }
Function Gen_AutopositionLabels(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Found : TInterfaceList;
    Names, Method, Name, Kind, Items, Before : String;
    I, Count : Integer;
Begin
    Names := ExtractJsonValue(Params, 'names');
    Method := LowerCase(ExtractJsonValue(Params, 'method'));
    If Method = '' Then Method := 'rotate';
    If (Method <> 'rotate') And (Method <> 'move') And (Method <> 'modify') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAMETER',
            'method is rotate, move or modify');
        Exit;
    End;
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { Collected first: nothing moves while the iterator is alive. The list }
    { is left to the script host; freeing one that holds Altium objects    }
    { faults (see memory of the copper-modify crash).                      }
    Found := TInterfaceList.Create;
    Iterator := SchDoc.SchIterator_Create;
    Try
        Iterator.AddFilter_ObjectSet(MkSet(eSchComponent, eSheetSymbol));
        Obj := Iterator.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Name := '';
            Try Name := SchLabelOf(Obj, 'Designator').Text; Except End;
            If (Names = '') Or (Pos('|' + UpperCase(Name) + '|',
                '|' + UpperCase(Names) + '|') > 0) Then
                Found.Add(Obj);
            Obj := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;

    Items := '';
    Count := Found.Count;
    SchBeginEdit(SchDoc, '');
    Try
        For I := 0 To Count - 1 Do
        Begin
            Obj := Found.Items[I];
            Name := '';
            Try Name := SchLabelOf(Obj, 'Designator').Text; Except End;
            If Obj.ObjectId = eSheetSymbol Then Kind := 'sheet_symbol' Else Kind := 'part';
            Before := '{"designator":' + SchLabelJson(SchLabelOf(Obj, 'Designator'))
                + ',"comment":' + SchLabelJson(SchLabelOf(Obj, 'Comment'))
                + ',"file_name":' + SchLabelJson(SchLabelOf(Obj, 'Filename')) + '}';
            SchAutopositionLabels(Obj, Method, True);
            If Items <> '' Then Items := Items + ',';
            Items := Items + '{"name":"' + EscapeJsonString(Name) + '","kind":"' + Kind
                + '","location":{"x":' + FloatToJsonStr(CoordToMilsF(Obj.Location.X))
                + ',"y":' + FloatToJsonStr(CoordToMilsF(Obj.Location.Y)) + '}'
                + ',"before":' + Before
                + ',"after":{"designator":' + SchLabelJson(SchLabelOf(Obj, 'Designator'))
                + ',"comment":' + SchLabelJson(SchLabelOf(Obj, 'Comment'))
                + ',"file_name":' + SchLabelJson(SchLabelOf(Obj, 'Filename')) + '}}';
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
    End;
    SchDoc.GraphicallyInvalidate;
    If Count > 0 Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId, '{"method":"' + Method + '","count":'
        + IntToStr(Count) + ',"items":[' + Items + ']}');
End;

{ Read the Designator and SheetFileName text labels carried by a sheet symbol. }
{ ISch_SheetSymbol exposes them as compound sub-objects (ISch_SheetName,        }
{ ISch_SheetFileName), so the Obj.SheetName / Obj.SheetFileName access has to  }
{ go through a typed-local cast and then read .Text. The same primitive cast   }
{ pattern as GetSchComponentSubText, just for the sheet-symbol family.          }
Function GetSheetSymbolText(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    SS : ISch_SheetSymbol;
    Got : Boolean;
Begin
    Result := '';
    If Obj.ObjectId <> eSheetSymbol Then Exit;
    Try
        SS := Obj;
        Got := False;
        If PropName = 'Designator' Then
        Begin
            Try
                If SS.SheetName <> Nil Then
                Begin
                    Result := SS.SheetName.Text;
                    Got := True;
                End;
            Except End;
        End
        Else If PropName = 'Filename' Then
        Begin
            Try
                If SS.SheetFileName <> Nil Then
                Begin
                    Result := SS.SheetFileName.Text;
                    Got := True;
                End;
            Except End;
        End
        Else
            Got := True;

        { AN EMPTY STRING WAS TWO DIFFERENT ANSWERS. A sheet symbol whose
          label is genuinely blank and a build that does not expose the
          sub-object at all both read '', and nothing distinguished them.
          Measured: a session read Filename off a sheet symbol, got
          nothing back, and concluded the hierarchy was broken. It was
          intact; the read was.

          The unreadable case is recorded so the reply can say so, in the
          same buffer the writers use. }
        If Not Got Then
            NotePropertyDiag('unreadable', PropName);
    Except
        RecordCastError('GetSheetSymbolText:' + PropName);
        NotePropertyDiag('unreadable', PropName);
        Result := '';
    End;
End;

{ Write the same two labels. The read side existed and the write side did   }
{ not, so obj_modify recognised SheetFileName well enough to return it and  }
{ not well enough to set it.                                                }
{                                                                           }
{ THIS RE-POINTS A SYMBOL, IT DOES NOT RENAME A SHEET. The child sheet's    }
{ filename lives in three places: this label, the file on disk, and the     }
{ project's document list. Writing only the label leaves the other two      }
{ alone, which is right when pointing a symbol at a sheet that already      }
{ exists and wrong as a way to rename one. Altium's Sheet Symbol Actions >  }
{ Rename Child Sheet does all three and keeps the symbol's UniqueId, which  }
{ is the project's handle for that sheet instance: replacing the symbol     }
{ instead would issue a new id, and the next Update PCB would propose       }
{ delete-and-re-add for every component on the sheet rather than matching   }
{ them.                                                                     }
{                                                                           }
{ Written through the sub-object exactly as GetSheetSymbolText reads it,    }
{ with no intermediate typed local. Narrowing ISch_SheetFileName to a       }
{ label interface is not something this codebase has demonstrated, and the  }
{ direct access above is proven, so this stays on the proven shape.         }
{                                                                           }
{ The result is a READ BACK, not the fact that the assignment ran. That is  }
{ the whole point: the caller reported success for this write for as long   }
{ as it has existed, on the strength of having attempted it.                }
Function SetSheetSymbolText(Obj : ISch_GraphicalObject; PropName : String;
    Value : String) : Boolean;
Var
    SS : ISch_SheetSymbol;
Begin
    Result := False;
    If Obj.ObjectId <> eSheetSymbol Then Exit;
    Try
        SS := Obj;
        If PropName = 'Designator' Then
        Begin
            If SS.SheetName <> Nil Then
            Begin
                SS.SheetName.Text := Value;
                Result := (SS.SheetName.Text = Value);
            End;
        End
        Else If PropName = 'Filename' Then
        Begin
            If SS.SheetFileName <> Nil Then
            Begin
                SS.SheetFileName.Text := Value;
                Result := (SS.SheetFileName.Text = Value);
            End;
        End;
    Except
        RecordCastError('SetSheetSymbolText:' + PropName);
        Result := False;
    End;
End;

{ eLine is an ISch_Line, two ends (Location and Corner), not a polyline:  }
{ asking it for GetState_VerticesCount raised an undeclared identifier no }
{ Try catches (AD 26.11). The polyline property names are answered here   }
{ from the two ends: VerticesCount, Vertices ('x,y;x,y'), Vertex1.X,      }
{ Vertex.2.Y, VertexLast.X and so on.                                     }
Function SchLineEndProperty(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    Segment : ISch_Line;
    Name, Which, Coord : String;
    StartEnd, FarEnd, Chosen : TLocation;
    Known : Boolean;
Begin
    Result := '';
    Segment := Obj;
    StartEnd := Segment.Location;
    FarEnd := Segment.Corner;
    Name := StringReplace(UpperCase(Trim(PropName)), 'VERTEX.', 'VERTEX', MkSet(rfReplaceAll));
    If Name = 'VERTICESCOUNT' Then
        Result := '2'
    Else If Name = 'VERTICES' Then
        Result := FloatToJsonStr(CoordToMilsF(StartEnd.X)) + ','
            + FloatToJsonStr(CoordToMilsF(StartEnd.Y)) + ';'
            + FloatToJsonStr(CoordToMilsF(FarEnd.X)) + ','
            + FloatToJsonStr(CoordToMilsF(FarEnd.Y))
    Else If Copy(Name, 1, 6) = 'VERTEX' Then
    Begin
        Coord := Copy(Name, Length(Name) - 1, 2);
        Which := Copy(Name, 7, Length(Name) - 8);
        Known := True;
        If Which = '1' Then Chosen := StartEnd
        Else If (Which = '2') Or (Which = 'LAST') Then Chosen := FarEnd
        Else Known := False;
        If Known And (Coord = '.X') Then Result := FloatToJsonStr(CoordToMilsF(Chosen.X));
        If Known And (Coord = '.Y') Then Result := FloatToJsonStr(CoordToMilsF(Chosen.Y));
    End;
End;

{ Indexed vertex access for the polyline family (eWire, eBus, eLine,          }
{ ePolyline, ...). Recognises `Vertex<N>.X` / `Vertex<N>.Y`, `VertexLast.X` / }
{ `VertexLast.Y` (N = VerticesCount) and a compact all-vertex dump `Vertices` }
{ formatted `x1,y1;x2,y2` with no trailing separator. Names match case-       }
{ insensitively. Altium's vertex array is 1-based, so GetState_Vertex(1) is   }
{ the first vertex and matches what Location.X / Location.Y report. Values    }
{ come back in mils through CoordToMils, exactly like Location.X, so a caller }
{ can compare a vertex against a Location value directly. Objects with no     }
{ vertex array, out-of-range or non-numeric indices and malformed names all   }
{ return '' -- the same thing GetSchProperty yields for an unknown property.  }
{ Nothing here raises.                                                        }
Function GetSchVertexProperty(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    U : String;
    Axis : String;
    IdxStr : String;
    Ch : String;
    Dump : String;
    Cnt : Integer;
    Idx : Integer;
    I : Integer;
    Vtx : TLocation;
    Ok : Boolean;
Begin
    Result := '';
    U := UpperCase(Trim(PropName));

    { Bulk dump. VerticesCount itself stays on the caller's fast path. }
    If U = 'VERTICES' Then
    Begin
        Cnt := 0;
        Try Cnt := Obj.GetState_VerticesCount; Except End;
        If Cnt < 1 Then Exit;
        Dump := '';
        For I := 1 To Cnt Do
        Begin
            Ok := False;
            Try
                Vtx := Obj.GetState_Vertex(I);
                Ok := True;
            Except
                Ok := False;
            End;
            If Not Ok Then Exit;
            If I > 1 Then Dump := Dump + ';';
            Dump := Dump + FloatToJsonStr(CoordToMilsF(Vtx.X)) + ',' + FloatToJsonStr(CoordToMilsF(Vtx.Y));
        End;
        Result := Dump;
        Exit;
    End;

    { 'VERTEX' + index + '.X' / '.Y'. Shortest legal form is 'VERTEX1.X' (9). }
    If Length(U) < 9 Then Exit;
    If Copy(U, 1, 6) <> 'VERTEX' Then Exit;
    Axis := Copy(U, Length(U) - 1, 2);
    If (Axis <> '.X') And (Axis <> '.Y') Then Exit;
    { Everything between the 'VERTEX' prefix and the trailing '.X' / '.Y'. }
    IdxStr := Copy(U, 7, Length(U) - 8);
    If IdxStr = '' Then Exit;

    Cnt := 0;
    Try Cnt := Obj.GetState_VerticesCount; Except End;
    If Cnt < 1 Then Exit;

    If IdxStr = 'LAST' Then
        Idx := Cnt
    Else
    Begin
        { Digits only. Rejects 'Vertex.1.X', 'VertexA.X', 'Vertex 1.X', '-1'. }
        { DelphiScript quirk (see ClassifyPassivePrefix): index a string with }
        { a 1-char Copy rather than S[I], which is not a Char here.           }
        For I := 1 To Length(IdxStr) Do
        Begin
            Ch := Copy(IdxStr, I, 1);
            If (Ch < '0') Or (Ch > '9') Then Exit;
        End;
        Idx := StrToIntDef(IdxStr, 0);
    End;

    If (Idx < 1) Or (Idx > Cnt) Then Exit;

    Ok := False;
    Try
        Vtx := Obj.GetState_Vertex(Idx);
        Ok := True;
    Except
        Ok := False;
    End;
    If Not Ok Then Exit;

    If Axis = '.X' Then
        Result := FloatToJsonStr(CoordToMilsF(Vtx.X))
    Else
        Result := FloatToJsonStr(CoordToMilsF(Vtx.Y));
End;

{..............................................................................}
{ A PLACED PART'S FOOTPRINT MODELS. Defined above GetSchProperty, which reads  }
{ the pin map through them. A part can carry several PCBLIB models; the one   }
{ marked IsCurrent is the footprint the board gets.                          }
{..............................................................................}

{ The current PCBLIB implementation of a placed part, or the first one. }
Function CurrentFootprintImpl(Comp : ISch_Component) : ISch_Implementation;
Var
    ImplIter : ISch_Iterator;
    Impl, FirstFp : ISch_Implementation;
    MT : String;
    IsCur : Boolean;
Begin
    Result := Nil;
    FirstFp := Nil;
    ImplIter := Comp.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            Try MT := Impl.ModelType; Except End;
            If UpperCase(MT) = 'PCBLIB' Then
            Begin
                If FirstFp = Nil Then FirstFp := Impl;
                IsCur := False;
                Try IsCur := Impl.IsCurrent; Except End;
                If IsCur Then
                Begin
                    Result := Impl;
                    Break;
                End;
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(ImplIter);
    End;
    If Result = Nil Then Result := FirstFp;
End;

{ The PCBLIB model names a part carries, comma separated, for a hint. }
Function FootprintModelNames(Comp : ISch_Component) : String;
Var
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    MT, MN : String;
Begin
    Result := '';
    ImplIter := Comp.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            MN := '';
            Try MT := Impl.ModelType; Except End;
            Try MN := Impl.ModelName; Except End;
            If UpperCase(MT) = 'PCBLIB' Then
            Begin
                If Result <> '' Then Result := Result + ', ';
                Result := Result + MN;
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(ImplIter);
    End;
End;

{ ChooseFootprintModel - make the part's PCBLIB model called Name (matched   }
{ ignoring case and outer spaces) its only current one. 1 = chosen, 0 = the  }
{ part has no model of that name (nothing written), -1 = the flags did not   }
{ read back as asked, and each was put back as it was. Before is the model   }
{ that was current, After the one current now. The models are collected      }
{ first and written after the walk: the others cleared, then the chosen one  }
{ set, then every flag read back, so a setter that also clears its siblings  }
{ and one that does not end the same way. Other model types keep their own   }
{ current model. The list is left to the script host (Free on design         }
{ objects crashes Altium).                                                    }
Function ChooseFootprintModel(Comp : ISch_Component; Name : String;
    Var Before : String; Var After : String) : Integer;
Var
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    Impls : TInterfaceList;
    Flags, MT, MN : String;
    IsCur, Held : Boolean;
    I, K : Integer;
Begin
    Result := 0;
    Before := '';
    After := '';
    K := -1;
    Flags := '';
    Impls := TInterfaceList.Create;
    ImplIter := Comp.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            Try MT := Impl.ModelType; Except End;
            If UpperCase(MT) = 'PCBLIB' Then
            Begin
                MN := '';
                IsCur := False;
                Try MN := Impl.ModelName; Except End;
                Try IsCur := Impl.IsCurrent; Except End;
                If (K < 0) And (UpperCase(Trim(MN)) = UpperCase(Trim(Name))) Then
                    K := Impls.Count;
                If IsCur Then
                Begin
                    Flags := Flags + '1';
                    If Before = '' Then Before := MN;
                End
                Else
                    Flags := Flags + '0';
                Impls.Add(Impl);
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(ImplIter);
    End;
    If K < 0 Then Exit;

    For I := 0 To Impls.Count - 1 Do
        If I <> K Then
        Begin
            Impl := Impls.Items[I];
            SchBeginModify(Impl);
            Try Impl.IsCurrent := False; Except { read back below } End;
            SchEndModify(Impl);
        End;
    Impl := Impls.Items[K];
    SchBeginModify(Impl);
    Try Impl.IsCurrent := True; Except { read back below } End;
    SchEndModify(Impl);

    Held := True;
    For I := 0 To Impls.Count - 1 Do
    Begin
        Impl := Impls.Items[I];
        IsCur := Not (I = K);
        Try IsCur := Impl.IsCurrent; Except End;
        If IsCur <> (I = K) Then Held := False;
    End;
    If Held Then
    Begin
        Impl := Impls.Items[K];
        Try After := Impl.ModelName; Except End;
        Result := 1;
        Exit;
    End;

    { Put every flag back: the cleared ones first, then the current ones. }
    For I := 0 To Impls.Count - 1 Do
        If Copy(Flags, I + 1, 1) = '0' Then
        Begin
            Impl := Impls.Items[I];
            SchBeginModify(Impl);
            Try Impl.IsCurrent := False; Except NoteWriteFailed('restore footprint model IsCurrent'); End;
            SchEndModify(Impl);
        End;
    For I := 0 To Impls.Count - 1 Do
        If Copy(Flags, I + 1, 1) = '1' Then
        Begin
            Impl := Impls.Items[I];
            SchBeginModify(Impl);
            Try Impl.IsCurrent := True; Except NoteWriteFailed('restore footprint model IsCurrent'); End;
            SchEndModify(Impl);
        End;
    After := Before;
    Result := -1;
End;

{ A pin map in Altium's own form, "(G:1),(D:2),(S:3)", from that form or     }
{ "G:1;D:2" or "G->1, D->2", in the order given. '' when an entry lacks a    }
{ pin or a pad.                                                               }
Function CanonicalPinMap(Text : String) : String;
Var
    Rest, Entry, Pin, Pad, Ch : String;
    I, P : Integer;
Begin
    Result := '';
    Rest := '';
    For I := 1 To Length(Text) Do
    Begin
        Ch := Copy(Text, I, 1);
        If (Ch = '(') Or (Ch = ')') Then Ch := '';
        If Ch = ';' Then Ch := ',';
        Rest := Rest + Ch;
    End;
    Rest := StringReplace(Rest, '->', ':', -1);
    While Rest <> '' Do
    Begin
        P := Pos(',', Rest);
        If P > 0 Then
        Begin
            Entry := Trim(Copy(Rest, 1, P - 1));
            Rest := Copy(Rest, P + 1, Length(Rest));
        End
        Else
        Begin
            Entry := Trim(Rest);
            Rest := '';
        End;
        If Entry <> '' Then
        Begin
            P := Pos(':', Entry);
            If P = 0 Then
            Begin
                Result := '';
                Exit;
            End;
            Pin := Trim(Copy(Entry, 1, P - 1));
            Pad := Trim(Copy(Entry, P + 1, Length(Entry)));
            If (Pin = '') Or (Pad = '') Then
            Begin
                Result := '';
                Exit;
            End;
            If Result <> '' Then Result := Result + ',';
            Result := Result + '(' + Pin + ':' + Pad + ')';
        End;
    End;
End;

{ True when every "(pin:pad)" entry of Wanted (in CanonicalPinMap form) is   }
{ in Got, whatever the order and spacing. Altium merges a written map by     }
{ schematic pin, so Got may hold entries Wanted does not name.               }
Function PinMapHolds(Wanted : String; Got : String) : Boolean;
Var
    Rest, Entry, Have : String;
    P : Integer;
Begin
    Result := False;
    Have := ',' + StringReplace(CanonicalPinMap(Got), ' ', '', -1) + ',';
    Rest := StringReplace(Wanted, ' ', '', -1);
    If Rest = '' Then Exit;
    While Rest <> '' Do
    Begin
        P := Pos('),', Rest);
        If P > 0 Then
        Begin
            Entry := Copy(Rest, 1, P);
            Rest := Copy(Rest, P + 2, Length(Rest));
        End
        Else
        Begin
            Entry := Rest;
            Rest := '';
        End;
        If Pos(',' + Entry + ',', Have) = 0 Then Exit;
    End;
    Result := True;
End;

{ WriteFootprintPinMap - the current footprint model's pin map, in any form  }
{ CanonicalPinMap takes. Read back; when it does not hold, the old map is    }
{ written back and False returned. Altium merges by schematic pin, so a map  }
{ cannot be cleared this way, and pads are not checked against the footprint.}
Function WriteFootprintPinMap(Comp : ISch_Component; Value : String) : Boolean;
Var
    Impl : ISch_Implementation;
    Wanted, Old, Got : String;
Begin
    Result := False;
    Wanted := CanonicalPinMap(Value);
    If Wanted = '' Then Exit;
    Impl := CurrentFootprintImpl(Comp);
    If Impl = Nil Then Exit;
    Old := '';
    Try Old := Impl.MapAsString; Except End;
    SchBeginModify(Impl);
    Try Impl.MapAsString := Wanted; Except { read back below } End;
    SchEndModify(Impl);
    Got := '';
    Try Got := Impl.MapAsString; Except End;
    If PinMapHolds(Wanted, Got) Then
    Begin
        Result := True;
        Exit;
    End;
    SchBeginModify(Impl);
    Try Impl.MapAsString := Old; Except NoteWriteFailed('restore footprint pin map'); End;
    SchEndModify(Impl);
End;

{ DISPLAY MODE NAMES (issue #254). A symbol's display modes are Normal and   }
{ its alternates; Altium shows a mode's custom name where it would say       }
{ "Alternate 1". GetState_ / SetState_CustomDisplayModeName(mode, name) on   }
{ the component, mode counted from 0 (Normal). MEASURED on AD 25.8.1: both   }
{ work, Normal takes a name too, the names survive save + reopen, and a      }
{ written name shows in Altium's own UI. ';'-separated, one name per mode.   }
Function ReadDisplayModeNames(Comp : ISch_Component) : String;
Var
    I, N : Integer;
    Item : String;
Begin
    Result := '';
    N := 0;
    Try N := Comp.DisplayModeCount; Except N := 0; End;
    For I := 0 To N - 1 Do
    Begin
        Item := '';
        Try Item := Comp.GetState_CustomDisplayModeName(I); Except Item := ''; End;
        If I > 0 Then Result := Result + ';';
        Result := Result + Item;
    End;
End;

{ Writes one name per existing mode and reads them back. A list whose count }
{ differs from DisplayModeCount is refused: modes are added and removed by  }
{ library.set_symbol_parts, not by naming them.                             }
Function WriteDisplayModeNames(Comp : ISch_Component; Value : String) : Boolean;
Var
    I, N, P : Integer;
    Rest, Item : String;
Begin
    Result := False;
    N := 0;
    Try N := Comp.DisplayModeCount; Except N := 0; End;
    If N < 1 Then Exit;
    If (Length(Value) - Length(StringReplace(Value, ';', '', MkSet(rfReplaceAll)))) <> N - 1 Then Exit;
    Rest := Value;
    For I := 0 To N - 1 Do
    Begin
        P := Pos(';', Rest);
        If P = 0 Then
        Begin
            Item := Rest;
            Rest := '';
        End
        Else
        Begin
            Item := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        Try Comp.SetState_CustomDisplayModeName(I, Item); Except NoteWriteFailed('DisplayModeNames'); End;
    End;
    Result := ReadDisplayModeNames(Comp) = Value;
End;

{ The current footprint model's pin map, '' when the part has no PCB model. }
Function ReadFootprintPinMap(Comp : ISch_Component) : String;
Var
    Impl : ISch_Implementation;
Begin
    Result := '';
    Impl := CurrentFootprintImpl(Comp);
    If Impl = Nil Then Exit;
    Try Result := Impl.MapAsString; Except Result := ''; End;
End;

{ A component's Component Type by the name obj_modify takes, '' if unknown. }
Function ComponentKindName(Kind : Integer) : String;
Begin
    Result := '';
    If Kind = eComponentKind_Standard Then Result := 'standard'
    Else If Kind = eComponentKind_Mechanical Then Result := 'mechanical'
    Else If Kind = eComponentKind_Graphical Then Result := 'graphical'
    Else If Kind = eComponentKind_NetTie_BOM Then Result := 'net_tie_bom'
    Else If Kind = eComponentKind_NetTie_NoBOM Then Result := 'net_tie_no_bom'
    Else If Kind = eComponentKind_Standard_NoBOM Then Result := 'standard_no_bom'
    Else If Kind = eComponentKind_Jumper Then Result := 'jumper';
End;

{ The Component Type a name stands for, -1 for a name that is none. }
Function ComponentKindOf(Name : String) : Integer;
Var
    N : String;
Begin
    N := LowerCase(Trim(Name));
    Result := -1;
    If N = 'standard' Then Result := eComponentKind_Standard
    Else If N = 'mechanical' Then Result := eComponentKind_Mechanical
    Else If N = 'graphical' Then Result := eComponentKind_Graphical
    Else If N = 'net_tie_bom' Then Result := eComponentKind_NetTie_BOM
    Else If N = 'net_tie_no_bom' Then Result := eComponentKind_NetTie_NoBOM
    Else If N = 'standard_no_bom' Then Result := eComponentKind_Standard_NoBOM
    Else If N = 'jumper' Then Result := eComponentKind_Jumper;
End;

{ The name a harness object gives in its description: the word after its    }
{ kind ("W1", "P1-1"), or empty when it gives only coordinates. A harness   }
{ wire, splice or cable has no Name property of its own; this is its name.  }
Function HarnessNameFromDescription(Desc : String) : String;
Var
    P : Integer;
Begin
    Result := '';
    If Copy(Desc, 1, 8) <> 'Harness ' Then Exit;
    Desc := Copy(Desc, 9, Length(Desc));
    P := Pos(' ', Desc);
    If P = 0 Then Exit;
    Desc := Trim(Copy(Desc, P + 1, Length(Desc)));
    If (Desc = '') Or (Copy(Desc, 1, 1) = '(') Then Exit;
    P := Pos(' ', Desc);
    If P > 0 Then Desc := Copy(Desc, 1, P - 1);
    Result := Desc;
End;

Function GetSchProperty(Obj : ISch_GraphicalObject; PropName : String) : String;
Var
    R : ISch_Rectangle;
    L : ISch_Line;
    Comp : ISch_Component;
    PortObj : ISch_Port;
    EntryObj : ISch_SheetEntry;
    HarnessEntryObj : ISch_HarnessEntry;
    FrameObj : ISch_TextFrame;
    SwapPin : ISch_Pin;
    RR : ISch_RoundRectangle;
    SymObj : ISch_SheetSymbol;
    Crn : TLocation;
    Have : Boolean;
    POrient, PLen, PCoord : Integer;
Begin
    Result := '';
    Try
        // Identity
        If SchExtraOwner(PropName) > 0 Then Result := SchGetExtra(Obj, PropName)
        Else If PropName = 'ObjectId'    Then Result := IntToStr(Obj.ObjectId)
        // Read only, on any schematic object: Altium's description and
        // identifier of it, and the compile error it carries (empty when
        // none).
        Else If PropName = 'DescriptionString' Then Result := Obj.GetState_DescriptionString
        Else If PropName = 'IdentifierString' Then Result := Obj.GetState_IdentifierString
        Else If PropName = 'ErrorString' Then Result := Obj.ErrorString

        // Coordinates (returned in mils). Corner is declared only on
        // ISch_Rectangle and ISch_Line (ISch_RoundRectangle inherits from
        // ISch_Rectangle), NOT on the base ISch_GraphicalObject. The
        // DelphiScript compiler rejects any textual reference to
        // 'Obj.Corner' when Obj is typed ISch_GraphicalObject, regardless
        // of the assignment target. The only compile-safe path is to
        // dispatch on Obj.ObjectId and narrow to a typed-local interface
        // that actually has Corner (R := Obj is a legal interface
        // narrowing in DelphiScript, same pattern as GetSchComponentSubText).
        Else If PropName = 'Location.X'  Then Result := FloatToJsonStr(CoordToMilsF(Obj.Location.X))
        Else If PropName = 'Location.Y'  Then Result := FloatToJsonStr(CoordToMilsF(Obj.Location.Y))
        Else If (PropName = 'Corner.X') Or (PropName = 'Corner.Y') Then
        Begin
            Have := False;
            If (Obj.ObjectId = eRectangle) Or (Obj.ObjectId = eRoundRectangle) Then
            Begin
                R := Obj;
                Crn := R.Corner;
                Have := True;
            End
            Else If Obj.ObjectId = eLine Then
            Begin
                L := Obj;
                Crn := L.Corner;
                Have := True;
            End;
            If Have Then
            Begin
                If PropName = 'Corner.X' Then
                    Result := FloatToJsonStr(CoordToMilsF(Crn.X))
                Else
                    Result := FloatToJsonStr(CoordToMilsF(Crn.Y));
            End;
        End
        { A round rectangle's corner radii live on ISch_RoundRectangle only,
          so the same narrowing as Corner: never Obj.CornerXRadius on the
          base interface, which halts the script on any other type
          (issue #268). }
        Else If (PropName = 'CornerXRadius') Or (PropName = 'CornerYRadius') Then
        Begin
            If Obj.ObjectId = eRoundRectangle Then
            Begin
                RR := Obj;
                If PropName = 'CornerXRadius' Then
                    Result := FloatToJsonStr(CoordToMilsF(RR.CornerXRadius))
                Else
                    Result := FloatToJsonStr(CoordToMilsF(RR.CornerYRadius));
            End
            Else
                NotePropertyDiag('unreadable', PropName);
        End

        // String properties (late-bound across all types, primitives only)
        Else If PropName = 'Text'        Then
        Begin
            If SchObjectHasText(Obj) Then
                Result := Obj.Text
            Else
            Begin
                { Say it is not on this type rather than faulting. A Port
                  and a sheet entry both answer to Name. }
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End;
        End
        Else If PropName = 'Name'        Then
        Begin
            If SchObjectHasName(Obj) Then
                Result := Obj.Name
            Else
            Begin
                { A harness wire, splice or cable: its name is in its    }
                { description, so a Name filter finds it too.            }
                Result := '';
                Try Result := HarnessNameFromDescription(Obj.GetState_DescriptionString); Except End;
                If Result = '' Then NotePropertyDiag('unreadable', PropName);
            End;
        End
        Else If PropName = 'LibReference'       Then Result := Obj.LibReference
        Else If PropName = 'SourceLibraryName'  Then Result := Obj.SourceLibraryName
        Else If PropName = 'DesignItemId'       Then Result := Obj.DesignItemId

        { A managed (Workspace) part's identity, read only. It has no
          library path; LibIdentifierKind and LibraryIdentifier are what
          Altium loaded it by, and the GUIDs name the Workspace, the item
          and the revision, which is what the file saves. Read off a part
          Altium placed, they give the values the managed tools need.
          Not written: pointing a part at another item is a relink. }
        Else If (PropName = 'LibIdentifierKind') Or (PropName = 'LibraryIdentifier')
             Or (PropName = 'VaultGUID') Or (PropName = 'ItemGUID')
             Or (PropName = 'RevisionGUID') Or (PropName = 'SymbolReference')
             Or (PropName = 'SymbolVaultGUID') Or (PropName = 'SymbolItemGUID')
             Or (PropName = 'SymbolRevisionGUID') Then
        Begin
            If Obj.ObjectId <> eSchComponent Then
            Begin
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End
            Else
            Begin
                Comp := Obj;
                Try
                    If PropName = 'LibIdentifierKind' Then
                        Result := IntToStr(Comp.LibIdentifierKind)
                    Else If PropName = 'LibraryIdentifier'  Then Result := Comp.LibraryIdentifier
                    Else If PropName = 'VaultGUID'          Then Result := Comp.VaultGUID
                    Else If PropName = 'ItemGUID'           Then Result := Comp.ItemGUID
                    Else If PropName = 'RevisionGUID'       Then Result := Comp.RevisionGUID
                    Else If PropName = 'SymbolReference'    Then Result := Comp.SymbolReference
                    Else If PropName = 'SymbolVaultGUID'    Then Result := Comp.SymbolVaultGUID
                    Else If PropName = 'SymbolItemGUID'     Then Result := Comp.SymbolItemGUID
                    Else Result := Comp.SymbolRevisionGUID;
                Except
                    NotePropertyDiag('failed', PropName);
                    Result := '';
                End;
            End;
        End
        // A database part's link: the DbLib table and the DbLib file name it
        // was placed from, both empty on a part from a .SchLib. Declared on
        // ISch_Component only. Without these branches a query for them
        // answered an empty string as if the part had no link, which is how
        // a database-linked part read as unlinked during the DbLib live test.
        Else If (PropName = 'DatabaseTableName') Or (PropName = 'DatabaseLibraryName') Then
        Begin
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                If PropName = 'DatabaseTableName' Then
                    Result := Comp.DatabaseTableName
                Else
                    Result := Comp.DatabaseLibraryName;
            End
            Else
            Begin
                NotePropertyDiag('unreadable', PropName);
            End;
        End
        // Which part of a multi-part symbol owns this primitive (0 = shared
        // across all parts). Without it a caller querying a multi-part
        // library symbol cannot tell which part a returned primitive is on.
        Else If PropName = 'OwnerPartId'        Then Result := IntToStr(Obj.OwnerPartId)
        Else If PropName = 'OwnerPartDisplayMode' Then Result := IntToStr(Obj.OwnerPartDisplayMode)
        { A placed part's display mode (0 = normal, then the alternates)  }
        { and how many it has, and its current footprint model's pin map  }
        { as Altium writes it, "(G:1),(D:2),(S:3)", and its Component Type  }
        { by name (ComponentKindName). Components only.                     }
        Else If (PropName = 'DisplayMode') Or (PropName = 'DisplayModeCount')
             Or (PropName = 'DisplayModeNames')
             Or (PropName = 'FootprintPinMap') Or (PropName = 'ComponentKind') Then
        Begin
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                If PropName = 'DisplayMode' Then Result := IntToStr(Comp.DisplayMode)
                Else If PropName = 'DisplayModeCount' Then Result := IntToStr(Comp.DisplayModeCount)
                Else If PropName = 'DisplayModeNames' Then Result := ReadDisplayModeNames(Comp)
                Else If PropName = 'ComponentKind' Then
                    Result := ComponentKindName(Comp.ComponentKind)
                Else Result := ReadFootprintPinMap(Comp);
            End
            Else
            Begin
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End;
        End
        // A pin's own description (issue #75), not the component's.
        Else If (PropName = 'Description') And (Obj.ObjectId = ePin) Then
            Result := ReadSchPinProp(Obj, 'Description')
        Else If PropName = 'ComponentDescription' Then Result := Obj.ComponentDescription
        Else If PropName = 'UniqueId'    Then Result := Obj.UniqueId
        { What a No-ERC marker suppresses. Guarded on the type: these names  }
        { exist only on ISch_NoERC, and an undeclared one halts the loop.   }
        Else If (Obj.ObjectId = eNoERC) And ((PropName = 'SuppressAll')
            Or (PropName = 'IsActive') Or (PropName = 'ErrorKindSetToSuppress')
            Or (PropName = 'ConnectionPairsToSuppress')
            Or (PropName = 'SuppressableErrors') Or (PropName = 'Symbol')) Then
            Result := ReadNoErcProp(Obj, PropName)
        Else If PropName = 'CurrentPartID' Then
        Begin
            Comp := Obj;
            Try Result := IntToStr(Comp.CurrentPartID); Except Result := ''; End;
        End
        Else If PropName = 'PartCount' Then
        Begin
            Comp := Obj;
            Try Result := IntToStr(Comp.PartCount); Except Result := ''; End;
        End

        // Sub-object string properties (compound interfaces, typed cast required).
        // Designator dispatches by ObjectId, ISch_Component carries the live designator
        // text on its sub-object Designator.Text, ISch_SheetSymbol carries it on
        // SheetName.Text. Filename / SheetFileName are sheet-symbol only and read the
        // SheetFileName.Text label that links the symbol to its child sheet.
        Else If (PropName = 'Designator') Or (PropName = 'Designator.Text')
             Or (PropName = 'SheetName') Or (PropName = 'SheetName.Text') Then
        Begin
            If Obj.ObjectId = ePin Then
                // A pin's Designator IS its pin number (ISch_Pin.Designator);
                // it must not be routed through the component-text helper,
                // which returns empty for a pin and was the cause of blank
                // pin numbers in ePin queries.
                Result := Obj.Designator
            Else If Obj.ObjectId = eSheetSymbol Then
                Result := GetSheetSymbolText(Obj, 'Designator')
            Else
                Result := GetSchComponentSubText(Obj, 'Designator');
        End
        Else If (PropName = 'Filename') Or (PropName = 'FileName')
             Or (PropName = 'SheetFileName') Or (PropName = 'SheetFileName.Text') Then
            Result := GetSheetSymbolText(Obj, 'Filename')
        Else If PropName = 'Comment'         Then Result := GetSchComponentSubText(Obj, 'Comment')
        Else If PropName = 'Comment.Text'    Then Result := GetSchComponentSubText(Obj, 'Comment')
        Else If SchLabelPropWhich(PropName) <> '' Then
            Result := GetSchComponentLabelProp(Obj, SchLabelPropWhich(PropName),
                SchLabelPropField(PropName))

        // Integer properties (returned as string)
        Else If PropName = 'Orientation' Then
        Begin
            If SchObjectHasOrientation(Obj) Then
                Result := IntToStr(Obj.Orientation)
            Else
            Begin
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End;
        End
        Else If PropName = 'FontId' Then
        Begin
            If SchObjectHasFontId(Obj) Then
                Result := IntToStr(Obj.FontId)
            Else
            Begin
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End;
        End
        Else If PropName = 'TextFontId' Then
        Begin
            If Obj.ObjectId = eSheetEntry Then
            Begin
                EntryObj := Obj;
                Result := IntToStr(EntryObj.TextFontID);
            End
            Else If Obj.ObjectId = eHarnessEntry Then
            Begin
                HarnessEntryObj := Obj;
                Result := IntToStr(HarnessEntryObj.TextFontID);
            End
            Else
            Begin
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End;
        End
        Else If PropName = 'LineWidth'   Then Result := IntToStr(Obj.LineWidth)
        Else If PropName = 'Style'       Then Result := IntToStr(Obj.Style)
        Else If PropName = 'IOType'      Then Result := IntToStr(Obj.IOType)
        Else If PropName = 'Alignment'   Then Result := IntToStr(Obj.Alignment)
        { NAME, NOT ORDINAL, so reading and writing use one spelling.
          This returned the raw enum while obj_modify takes a name and
          lib_get_pin_list reports one, so a caller that read a pin and
          wrote it back was handing '0' to a setter it had just been shown
          as 'input'. ElectricalOrdinal accepts both, so no writer breaks;
          only the reader changes, and it now agrees with lib_get_pin_list.
          Reported GH #11, 2026-09-22. }
        Else If PropName = 'Electrical'  Then Result := PinElectricalToStr(Obj.Electrical)
        { Pin swapping groups (GetState_SwapId*), read through a              }
        { pin-typed local: the declared type decides the dispatch.            }
        Else If (PropName = 'SwapIdPart') Or (PropName = 'SwapIdPin') Or
                (PropName = 'SwapIdPair') Or (PropName = 'SwapIdPartPin') Then
        Begin
            If Obj.ObjectId = ePin Then
            Begin
                SwapPin := Obj;
                If PropName = 'SwapIdPart' Then Result := SwapPin.GetState_SwapIdPart
                Else If PropName = 'SwapIdPin' Then Result := SwapPin.GetState_SwapIdPin
                Else If PropName = 'SwapIdPair' Then Result := SwapPin.GetState_SwapIdPair
                Else Result := SwapPin.GetState_SwapIdPartPin;
            End
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        Else If PropName = 'Color'       Then Result := IntToStr(Obj.Color)
        Else If PropName = 'AreaColor'   Then Result := IntToStr(Obj.AreaColor)
        Else If PropName = 'TextColor' Then
        Begin
            If SchObjectHasTextColor(Obj) Then
            Begin
                If Obj.ObjectId = ePort Then
                Begin
                    PortObj := Obj;
                    Result := IntToStr(PortObj.TextColor);
                End
                Else If Obj.ObjectId = eSheetEntry Then
                Begin
                    EntryObj := Obj;
                    Result := IntToStr(EntryObj.TextColor);
                End
                Else If (Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote) Then
                Begin
                    FrameObj := Obj;
                    Result := IntToStr(FrameObj.TextColor);
                End
                Else
                Begin
                    HarnessEntryObj := Obj;
                    Result := IntToStr(HarnessEntryObj.TextColor);
                End;
            End
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        Else If PropName = 'Justification' Then Result := IntToStr(Obj.Justification)
        { A SHEET ENTRY'S POSITION ON THE SYMBOL. Both read empty before,
          because neither had a case here, so a caller checking whether a
          placement took got nothing back and could not tell an unset
          value from an unreadable one. Side is the enum ordinal and
          DistanceFromTop is in mils, matching Location.X / Location.Y. }
        Else If PropName = 'Side' Then
        Begin
            If Obj.ObjectId = eSheetEntry Then Result := IntToStr(Obj.Side)
            Else If Obj.ObjectId = eHarnessEntry Then
            Begin
                HarnessEntryObj := Obj;
                Result := IntToStr(HarnessEntryObj.Side);
            End;
        End
        Else If PropName = 'DistanceFromTop' Then
        Begin
            If Obj.ObjectId = eSheetEntry Then
                Result := FloatToJsonStr(CoordToMilsF(Obj.DistanceFromTop))
            Else If Obj.ObjectId = eHarnessEntry Then
            Begin
                HarnessEntryObj := Obj;
                Result := FloatToJsonStr(CoordToMilsF(HarnessEntryObj.DistanceFromTop));
            End;
        End
        { The sheet symbol an entry is on, by its designator (Sheet Name). It }
        { had no case, so it read empty and the owner had to be guessed from }
        { document order.                                                    }
        Else If PropName = 'OwnerSheetSymbol' Then
        Begin
            If Obj.ObjectId = eSheetEntry Then
            Begin
                EntryObj := Obj;
                SymObj := Nil;
                Try SymObj := EntryObj.OwnerSheetSymbol; Except SymObj := Nil; End;
                If SymObj <> Nil Then
                Begin
                    Try Result := SymObj.SheetName.Text; Except NotePropertyDiag('unreadable', PropName); End;
                End
                Else
                    NotePropertyDiag('unreadable', PropName);
            End
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        { The rest of a sheet symbol's and a sheet entry's Properties panel,
          each read through its own typed interface. SymbolType is the
          panel's Source (Local / Device / Managed); TextStyle is Bus Text
          Style, kept on every entry; ArrowKind is the entry's Kind and
          HarnessType its Harness Type. Names, not ordinals, as the file
          stores them (issue #273). }
        Else If PropName = 'SymbolType' Then
        Begin
            If Obj.ObjectId = eSheetSymbol Then
            Begin
                SymObj := Obj;
                Result := SheetSymbolTypeToStr(SymObj.SymbolType);
            End
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        Else If (PropName = 'TextStyle') Or (PropName = 'ArrowKind')
             Or (PropName = 'HarnessType') Then
        Begin
            If Obj.ObjectId = eSheetEntry Then
            Begin
                EntryObj := Obj;
                If PropName = 'TextStyle' Then
                    Result := BusTextStyleToStr(EntryObj.TextStyle)
                Else If PropName = 'ArrowKind' Then
                    Result := ArrowKindToStr(EntryObj.ArrowKind)
                Else
                    Result := EntryObj.HarnessType;
            End
            Else
                NotePropertyDiag('unreadable', PropName);
        End

        // Coord properties (returned in mils)
        Else If PropName = 'Width'       Then Result := FloatToJsonStr(CoordToMilsF(Obj.Width))
        Else If PropName = 'PinLength'   Then Result := FloatToJsonStr(CoordToMilsF(Obj.PinLength))

        // ConnectionX / ConnectionY: the pin's ELECTRICAL end, i.e. the
        // point a wire or net label must sit on to attach. Pin.Location is
        // the BODY-side root, so callers were deriving this by hand and
        // getting it wrong. Orientation is the direction the electrical end
        // points away from the body: 0=right(+x) 1=up(+y) 2=left(-x)
        // 3=down(-y). Same convention as the pin dump in Proj_GetComponentInfo.
        Else If PropName = 'ConnectionX' Then
        Begin
            { A connection point is a PIN idea, and Orientation is not on
              every type. The Try below cannot save this: an undeclared
              identifier is a modal, not an exception. Same fault as
              issue #22, in code written to fix a different one. }
            If Obj.ObjectId <> ePin Then
            Begin
                NotePropertyDiag('unreadable', PropName);
                Result := '';
                Exit;
            End;
            POrient := 0; PLen := 0; PCoord := 0;
            Try POrient := Obj.Orientation; Except End;
            Try PLen := Obj.PinLength; Except End;
            Try PCoord := Obj.Location.X; Except End;
            PCoord := PinEndX(PCoord, POrient, PLen);
            Result := FloatToJsonStr(CoordToMilsF(PCoord));
        End
        Else If PropName = 'ConnectionY' Then
        Begin
            If Obj.ObjectId <> ePin Then
            Begin
                NotePropertyDiag('unreadable', PropName);
                Result := '';
                Exit;
            End;
            POrient := 0; PLen := 0; PCoord := 0;
            Try POrient := Obj.Orientation; Except End;
            Try PLen := Obj.PinLength; Except End;
            Try PCoord := Obj.Location.Y; Except End;
            PCoord := PinEndY(PCoord, POrient, PLen);
            Result := FloatToJsonStr(CoordToMilsF(PCoord));
        End
        Else If (PropName = 'XSize') Or (PropName = 'YSize') Then
        Begin
            If Not SchObjectHasSize(Obj) Then
                NotePropertyDiag('unreadable', PropName)
            Else If PropName = 'XSize' Then
                Result := FloatToJsonStr(CoordToMilsF(Obj.XSize))
            Else
                Result := FloatToJsonStr(CoordToMilsF(Obj.YSize));
        End

        // BoundingRectangle on ISch_Component returns a TCoordRect.
        // Audit code reads X1/Y1/X2/Y2 to detect overlapping component
        // bodies. The same accessors are valid on other geometric sch
        // objects; we late-bind so the get-side stays type-agnostic.
        Else If PropName = 'BoundingRectangle.X1' Then Result := FloatToJsonStr(CoordToMilsF(Obj.BoundingRectangle.X1))
        Else If PropName = 'BoundingRectangle.Y1' Then Result := FloatToJsonStr(CoordToMilsF(Obj.BoundingRectangle.Y1))
        Else If PropName = 'BoundingRectangle.X2' Then Result := FloatToJsonStr(CoordToMilsF(Obj.BoundingRectangle.X2))
        Else If PropName = 'BoundingRectangle.Y2' Then Result := FloatToJsonStr(CoordToMilsF(Obj.BoundingRectangle.Y2))

        // A line has two ends, not vertices (see SchLineEndProperty).
        Else If (Obj.ObjectId = eLine) And (Pos('VERT', UpperCase(PropName)) = 1) Then
            Result := SchLineEndProperty(Obj, PropName)
        // Polyline vertex access. eWire / eBus / ePolyline are ISch_Polyline
        // children with GetState_Vertex(i : Integer) : TLocation. Audit
        // code reads the first two vertices to test wire-vs-component
        // segment intersection; higher indices are theoretically valid
        // but rarely needed (most wires are 2-vertex).
        // Anything else without a vertex array (a rectangle, a text frame)
        // has no vertex members at all, and asking halts the script.
        Else If ((Pos('VERT', UpperCase(PropName)) = 1)) And Not SchHasVertexArray(Obj) Then
            NotePropertyDiag('unreadable', PropName)
        Else If PropName = 'VerticesCount' Then Result := IntToStr(Obj.GetState_VerticesCount)
        Else If PropName = 'Vertex.1.X'   Then Result := FloatToJsonStr(CoordToMilsF(Obj.GetState_Vertex(1).X))
        Else If PropName = 'Vertex.1.Y'   Then Result := FloatToJsonStr(CoordToMilsF(Obj.GetState_Vertex(1).Y))
        Else If PropName = 'Vertex.2.X'   Then Result := FloatToJsonStr(CoordToMilsF(Obj.GetState_Vertex(2).X))
        Else If PropName = 'Vertex.2.Y'   Then Result := FloatToJsonStr(CoordToMilsF(Obj.GetState_Vertex(2).Y))
        // Indexed / bulk vertex access, delegated so the parsing stays out
        // of this chain: Vertex<N>.X, Vertex<N>.Y, VertexLast.X,
        // VertexLast.Y and `Vertices` (a compact `x1,y1;x2,y2` dump).
        // Matched case-insensitively and placed AFTER the exact-match
        // forms above so Vertex.1.X / Vertex.2.X and VerticesCount keep
        // their current behaviour. GetSchVertexProperty returns '' for
        // anything it cannot resolve, which is what an unknown PropName
        // yields here anyway.
        Else If (UpperCase(Copy(PropName, 1, 6)) = 'VERTEX')
             Or (UpperCase(PropName) = 'VERTICES') Then
            Result := GetSchVertexProperty(Obj, PropName)

        // Parameter / designator display settings
        Else If ((PropName = 'ShowName') Or IsSchParamDisplayProp(PropName))
            And SchHasParamDisplay(Obj) Then
            Result := ReadSchParamDisplayProp(Obj, PropName)
        Else If IsSchParamDisplayProp(PropName) Then
            NotePropertyDiag('unreadable', PropName)

        // Pin text settings (issue #77)
        Else If IsSchPinProp(PropName) Then
        Begin
            If Obj.ObjectId = ePin Then
                Result := ReadSchPinProp(Obj, PropName)
            Else
                NotePropertyDiag('unreadable', PropName);
        End

        // Boolean properties
        Else If PropName = 'IsHidden' Then
        Begin
            If SchObjectHasIsHidden(Obj) Then
                Result := BoolToJsonStr(Obj.IsHidden)
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        Else If PropName = 'IsSolid' Then
        Begin
            If SchObjectHasIsSolid(Obj) Then
                Result := BoolToJsonStr(Obj.IsSolid)
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        { Blanket members (ISch_Blanket on ISch_CollapsiblePolygon on
          ISch_Polygon). Each is asked only of the types that carry it:
          an absent member halts the script. }
        Else If PropName = 'Collapsed' Then
        Begin
            If (Obj.ObjectId = eBlanket) Or (Obj.ObjectId = eNote) Or (Obj.ObjectId = eCompileMask) Then
                Result := BoolToJsonStr(Obj.Collapsed)
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        Else If PropName = 'LineStyle' Then
        Begin
            If SchObjectHasLineStyle(Obj) Then
                Result := IntToStr(Obj.LineStyle)
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        Else If PropName = 'Transparent' Then
        Begin
            If SchObjectHasTransparent(Obj) Then
                Result := BoolToJsonStr(Obj.Transparent)
            Else
                NotePropertyDiag('unreadable', PropName);
        End
        Else If PropName = 'IsMirrored'  Then Result := BoolToJsonStr(Obj.IsMirrored)
        { Readable so obj_select can confirm what it selected. The same
          member Gen_ClearHighlights reads on this interface. }
        Else If PropName = 'Selection'   Then Result := BoolToJsonStr(Obj.Selection)
        { A name no branch above reads came back as a silent blank, the
          same answer as an empty value: a misspelt or unsupported name
          looked like a property that is there and empty. Say so
          (issue #268). }
        Else
            NotePropertyDiag('unknown', PropName);
    Except
        Result := '';
    End;
End;

{ A parameter whose name or value Altium keeps for itself. A port's CrossRef  }
{ (the sheets the port reaches, written by the compiler) reads               }
{ NameIsReadOnly and ValueIsReadOnly true. MEASURED on AD 25.8.1: a Text     }
{ write landed on it anyway, and a Name write did nothing while reporting    }
{ success. Altium's own Properties panel refuses both, so the generic layer  }
{ does too. True when Altium keeps the value (ForValue) or the name.        }
Function SchParamKeptByAltium(Obj : ISch_GraphicalObject; ForValue : Boolean) : Boolean;
Var
    Par : ISch_Parameter;
Begin
    Result := False;
    If Obj = Nil Then Exit;
    If Obj.ObjectId <> eParameter Then Exit;
    Par := Obj;
    Try
        If ForValue Then Result := Par.ValueIsReadOnly
        Else Result := Par.NameIsReadOnly;
    Except
        Result := False;
    End;
End;

{ Says why a parameter write was not made; False, for WroteOK. }
Function NoteKeptByAltium(ForValue : Boolean) : Boolean;
Begin
    Result := False;
    If ForValue Then
        NoteNextStep('Not written: Altium keeps this parameter''s value read-only '
            + '(a port''s CrossRef is written by the compiler). Its font, colour, '
            + 'justification and visibility can be set.')
    Else
        NoteNextStep('Not written: Altium keeps this parameter''s name read-only '
            + '(a port''s CrossRef is written by the compiler).');
End;

{ A port's Style is only its axis: 0 horizontal, 4 vertical. MEASURED on     }
{ AD 25.8.1: 1-3 and 5-7 are taken and read back, then come back as 0 / 4    }
{ after a save and reopen; Altium draws a port's arrows from its IOType and  }
{ the end that is wired (ConnectedEnd), not from Style. Writing 4 turns a    }
{ horizontal port a quarter turn counterclockwise about its Location (the   }
{ left end becomes the bottom end); 0 turns it back.                         }
Function PortStyleKept(Value : String) : Boolean;
Begin
    Result := (Value = '0') Or (Value = '4');
End;

{..............................................................................}
{ Generic Property Setter                                                     }
{ Sets a named property on a schematic object from a string value.           }
{ Coordinates are expected in mils. Caller handles BeginModify/EndModify.    }
{..............................................................................}

{ Move a harness connector to (X, Y). MoveToXY carries its entries, but  }
{ not the harness type label under it, which stayed at the old place; a   }
{ MoveByXY of the label by the same offset threw it off the sheet (both   }
{ measured on AD 26.11). So the label's offset from the connector is read }
{ first and its Location set from the connector's new one.                }
Procedure MoveHarnessConnector(Obj : ISch_GraphicalObject; X : TCoord; Y : TCoord);
Var
    Harness : ISch_HarnessConnector;
    TypeLabel : ISch_GraphicalObject;
    Was, LabelWas : TLocation;
    HasLabel : Boolean;
Begin
    Harness := Obj;
    Was := Harness.Location;
    TypeLabel := Nil;
    Try TypeLabel := Harness.HarnessConnectorType; Except TypeLabel := Nil; End;
    HasLabel := False;
    If TypeLabel <> Nil Then
    Begin
        Try
            LabelWas := TypeLabel.Location;
            HasLabel := True;
        Except
            HasLabel := False;
        End;
    End;
    Harness.MoveToXY(X, Y);
    If HasLabel Then
    Begin
        { Point, not fields of a local: a record local that was never   }
        { read from a property has no fields here ('Undeclared X').      }
        Try TypeLabel.Location := Point(X + (LabelWas.X - Was.X), Y + (LabelWas.Y - Was.Y));
        Except NoteWriteFailed('HarnessConnectorType.Location'); End;
    End;
End;

Function SetSchProperty(Obj : ISch_GraphicalObject; PropName : String; Value : String) : Integer;
{ Returns: 1 = handled, 0 = unknown property name, -1 = write threw.        }
{ Unknown and failed names are appended to the module-level _PropertyDiag   }
{ so Gen_BatchModify can surface them in the response. Existing callers     }
{ that discard the return value still get the old behaviour.                }
Var
    Loc : TLocation;
    Crn : TLocation;
    R : ISch_Rectangle;
    L : ISch_Line;
    Comp : ISch_Component;
    PortObj : ISch_Port;
    EntryObj : ISch_SheetEntry;
    HarnessEntryObj : ISch_HarnessEntry;
    FrameObj : ISch_TextFrame;
    SwapPin : ISch_Pin;
    RR : ISch_RoundRectangle;
    SymObj : ISch_SheetSymbol;
    EnumVal : Integer;
    Matched : Boolean;
    { Separate from Matched on purpose. Matched says the property NAME is
      one this build writes; WroteOK says the value actually landed, read
      back off the object. Collapsing them would report a recognised
      property whose write did not stick as an unknown name, which points
      a reader at a spelling mistake that is not there. }
    WroteOK : Boolean;
Begin
    { Measured on a live document: callers using modify_objects /             }
    { batch_modify                                                            }
    { with a pipe-combined set like `Location.X=200|Orientation=2` on an ePin }
    { saw Location.X take effect but Orientation silently dropped. Writing    }
    { Location on a pin triggers a re-layout that can snapshot the previous   }
    { Orientation. Workaround until the multi-set parser applies properties   }
    { in an Altium-safe order (Orientation first, then Location): split the   }
    { combined set into two separate ops, the second filtering on the NEW    }
    { Location.X so it still matches the moved pin.                          }
    Result := 0;
    Matched := True;
    WroteOK := True;
    Try
        If SchExtraOwner(PropName) > 0 Then
        Begin
            WroteOK := SchSetExtra(Obj, PropName, Value);
        End
        // Coordinates (expected in mils). `Obj.Location` returns a copy of
        // the TLocation record via the GetState_Location reader; writing
        // directly to `.X` / `.Y` on that copy is silently discarded. Read
        // the whole record, patch the target field, write it back.
        Else If PropName = 'Location.X' Then
        Begin
            Loc := Obj.Location;
            Loc.X := MilsToCoordF(StrToFloatDef(Value, 0));
            { A COMPONENT owns child primitives (pins, designator, comment). }
            { Assigning Location moves only the component record and leaves  }
            { every pin at its old coordinate, silently desynchronising the  }
            { symbol from its own pins, and it 16-bit-truncates coords.      }
            { MoveToXY is the documented whole-component move and is already }
            { what the placement path (Gen_PlaceComponents) uses. Any        }
            { exception propagates to the outer handler as Result = -1       }
            { rather than being swallowed, so a failed move is never         }
            { reported as applied.                                           }
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Comp.MoveToXY(Loc.X, Loc.Y);
            End
            { A harness connector carries its entries: Location alone read }
            { back moved while the connector stayed drawn where it was.    }
            Else If Obj.ObjectId = eHarnessConnector Then
                MoveHarnessConnector(Obj, Loc.X, Loc.Y)
            Else
                Obj.Location := Loc;
        End
        Else If PropName = 'Location.Y' Then
        Begin
            Loc := Obj.Location;
            Loc.Y := MilsToCoordF(StrToFloatDef(Value, 0));
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Comp.MoveToXY(Loc.X, Loc.Y);
            End
            { A harness connector carries its entries: Location alone read }
            { back moved while the connector stayed drawn where it was.    }
            Else If Obj.ObjectId = eHarnessConnector Then
                MoveHarnessConnector(Obj, Loc.X, Loc.Y)
            Else
                Obj.Location := Loc;
        End
        // Corner lives on ISch_Rectangle and ISch_Line only (not on the base
        // ISch_GraphicalObject, the compiler rejects Obj.Corner regardless
        // of assignment target). Dispatch on ObjectId and narrow to a typed
        // local before touching Corner. See GetSchProperty for the read side.
        Else If (PropName = 'Corner.X') Or (PropName = 'Corner.Y') Then
        Begin
            If (Obj.ObjectId = eRectangle) Or (Obj.ObjectId = eRoundRectangle) Then
            Begin
                R := Obj;
                Crn := R.Corner;
                If PropName = 'Corner.X' Then
                    Crn.X := MilsToCoordF(StrToFloatDef(Value, 0))
                Else
                    Crn.Y := MilsToCoordF(StrToFloatDef(Value, 0));
                R.Corner := Crn;
            End
            Else If Obj.ObjectId = eLine Then
            Begin
                L := Obj;
                Crn := L.Corner;
                If PropName = 'Corner.X' Then
                    Crn.X := MilsToCoordF(StrToFloatDef(Value, 0))
                Else
                    Crn.Y := MilsToCoordF(StrToFloatDef(Value, 0));
                L.Corner := Crn;
            End;
        End
        // A round rectangle's corner radii, decimal mils, on that type only
        // (typed local, as Corner above). Read back by the caller's query.
        Else If (PropName = 'CornerXRadius') Or (PropName = 'CornerYRadius') Then
        Begin
            If Obj.ObjectId = eRoundRectangle Then
            Begin
                RR := Obj;
                If PropName = 'CornerXRadius' Then
                    RR.CornerXRadius := MilsToCoordF(StrToFloatDef(Value, 0))
                Else
                    RR.CornerYRadius := MilsToCoordF(StrToFloatDef(Value, 0));
            End
            Else
                Matched := False;
        End

        // String properties (late-bound across all types, primitives only)
        Else If PropName = 'Text'        Then
        Begin
            If SchObjectHasText(Obj) Then
            Begin
                If SchParamKeptByAltium(Obj, True) Then
                    WroteOK := NoteKeptByAltium(True)
                Else
                    Obj.Text := Value;
            End
            Else
                Matched := False;
        End
        Else If PropName = 'Name'        Then
        Begin
            If SchObjectHasName(Obj) Then
            Begin
                If SchParamKeptByAltium(Obj, False) Then
                    WroteOK := NoteKeptByAltium(False)
                Else
                    Obj.Name := Value;
            End
            Else
                Matched := False;
        End
        Else If PropName = 'LibReference'       Then Obj.LibReference := Value
        // SourceLibraryName is the design-cache field that records which
        // library a placed component came from. It is read in GetSchProperty
        // but had no write case, so obj_modify / batch_modify silently no-oped
        // (recorded only as an "unknown property"). Clearing it to '' is the
        // canonical way to detach a part from a stale source-library binding.
        Else If PropName = 'SourceLibraryName'  Then Obj.SourceLibraryName := Value
        // DesignItemId is the library ITEM the placed part re-matches
        // against ("Design Item ID" in the UI). It is a component
        // PROPERTY, not a parameter: stamping a parameter named
        // DesignItemId just creates a stray user parameter, and with no
        // write case here obj_modify silently no-oped while reporting
        // matched. A stale DesignItemId after a library re-link is what
        // produces the <Not Found> state in the Properties panel.
        Else If PropName = 'DesignItemId'       Then Obj.DesignItemId := Value
        // `Description` is the natural name (matches get_component_info /
        // BOM column / lib_set_component_description); `ComponentDescription`
        // is what ISch_Component actually exposes -- both accepted.
        { Only a component has a ComponentDescription. On a pin it used to
          write that field and raise (issue #75): a pin's Description is its
          own field, ISch_Pin.Description, which Altium's Import Pins example
          writes. Anything else reports as not matched instead of faulting. }
        Else If (PropName = 'ComponentDescription') Or (PropName = 'Description') Then
        Begin
            If Obj.ObjectId = eSchComponent Then
            Begin
                Obj.ComponentDescription := Value;
            End
            Else
            Begin
                If (PropName = 'Description') And (Obj.ObjectId = ePin) Then
                    WroteOK := WriteSchPinProp(Obj, 'Description', Value)
                Else
                    Matched := False;
            End;
        End
        { UniqueId is how Altium groups multi-part sub-parts into one physical
          component for ECO. The UniqueId property setter remints when the
          value is already in the document; SetState_UniqueId is the SDK
          writer (same property) and is the path to try first. }
        Else If PropName = 'UniqueId' Then
        Begin
            Comp := Obj;
            { Two spellings of one write; the read-back settles whether it took. }
            Try Comp.SetState_UniqueId(Value); Except { read back below } End;
            Try Comp.UniqueId := Value; Except { read back below } End;
            WroteOK := False;
            Try WroteOK := (Comp.UniqueId = Value); Except End;
        End
        Else If PropName = 'CurrentPartID' Then
        Begin
            Comp := Obj;
            Try Comp.SetState_CurrentPartID(StrToIntDef(Value, 1)); Except { read back below } End;
            Try Comp.CurrentPartID := StrToIntDef(Value, 1); Except { read back below } End;
            WroteOK := False;
            Try WroteOK := (Comp.CurrentPartID = StrToIntDef(Value, 1)); Except End;
        End
        Else If (Obj.ObjectId = eNoERC) And ((PropName = 'SuppressAll')
            Or (PropName = 'IsActive') Or (PropName = 'ErrorKindSetToSuppress')
            Or (PropName = 'ConnectionPairsToSuppress') Or (PropName = 'Symbol')) Then
        Begin
            WriteNoErcProp(Obj, PropName, Value);
            WroteOK := (LowerCase(ReadNoErcProp(Obj, PropName)) = LowerCase(Value));
        End
        Else If PropName = 'PartCount' Then
        Begin
            { LoadComponentFromLibrary can stamp a PLACED component with a
              lower PartCount than the SchLib symbol reports. Measured on
              a multi-part symbol: the placed part read 4 against the
              library's 5, and without a write path the last sub-part,
              which was the supply unit, could not be selected at all.
              Do NOT call SetState_PartCount: that identifier is not on
              ISch_Component in this DelphiScript, and undeclared
              identifiers raise a modal that bypasses Try/Except. }
            Comp := Obj;
            Try Comp.PartCount := StrToIntDef(Value, 1); Except { read back below } End;
            WroteOK := False;
            Try WroteOK := (Comp.PartCount = StrToIntDef(Value, 1)); Except End;
        End

        // Sub-object string properties (compound interfaces, typed cast required)
        // A sheet symbol carries its designator on SheetName rather than on the
        // component Designator sub-object, so it is dispatched by ObjectId the
        // same way GetSchProperty dispatches the read.
        Else If (PropName = 'Designator') Or (PropName = 'Designator.Text')
             Or (PropName = 'SheetName') Or (PropName = 'SheetName.Text') Then
        Begin
            { A pin's Designator IS its pin number, as GetSchProperty reads it.  }
            { Routed through the component-text helper it was a no-op that      }
            { still reported matched:1, so renumbering a pin silently failed.   }
            If Obj.ObjectId = ePin Then
                Obj.Designator := Value
            Else If Obj.ObjectId = eSheetSymbol Then
                WroteOK := SetSheetSymbolText(Obj, 'Designator', Value)
            Else
                SetSchComponentSubText(Obj, 'Designator', Value);
        End
        // Sheet-symbol filename. The READ side of this has always existed and
        // the write side never did, so obj_modify knew the name well enough to
        // return it and not well enough to set it, and said matched:1 either
        // way. Re-points the symbol at a sheet; it does not rename one, see
        // SetSheetSymbolText.
        Else If (PropName = 'Filename') Or (PropName = 'FileName')
             Or (PropName = 'SheetFileName') Or (PropName = 'SheetFileName.Text') Then
            WroteOK := SetSheetSymbolText(Obj, 'Filename', Value)
        Else If (PropName = 'Comment') Or (PropName = 'Comment.Text') Then
            SetSchComponentSubText(Obj, 'Comment', Value)
        { A placed part's designator must be visible on the schematic even
          when the library template hid it; these were unknown names. }
        Else If SchLabelPropWhich(PropName) <> '' Then
            WroteOK := SetSchComponentLabelProp(Obj, SchLabelPropWhich(PropName),
                SchLabelPropField(PropName), Value)

        // Integer properties
        Else If PropName = 'Orientation' Then
        Begin
            If SchObjectHasOrientation(Obj) Then
                Obj.Orientation := StrToIntDef(Value, 0)
            Else
                NotePropertyDiag('unknown', PropName);
        End
        { A placed part's display mode, refused outside 0 .. count - 1 and  }
        { read back; its current footprint model's pin map, which Altium    }
        { merges by schematic pin (WriteFootprintPinMap); its Component     }
        { Type by name (ComponentKindOf), read back. Components only.       }
        Else If (PropName = 'DisplayMode') Or (PropName = 'FootprintPinMap')
             Or (PropName = 'ComponentKind') Or (PropName = 'DisplayModeNames') Then
        Begin
            If Obj.ObjectId <> eSchComponent Then
                Matched := False
            Else If PropName = 'DisplayModeNames' Then
            Begin
                Comp := Obj;
                If Not WriteDisplayModeNames(Comp, Value) Then WroteOK := False;
            End
            Else If PropName = 'ComponentKind' Then
            Begin
                Comp := Obj;
                If ComponentKindOf(Value) < 0 Then
                    WroteOK := False
                Else
                Begin
                    Comp.ComponentKind := ComponentKindOf(Value);
                    If Comp.ComponentKind <> ComponentKindOf(Value) Then WroteOK := False;
                End;
            End
            Else If PropName = 'DisplayMode' Then
            Begin
                Comp := Obj;
                If (Not IsIntStr(Value)) Or (StrToIntDef(Value, -1) < 0)
                   Or (StrToIntDef(Value, -1) >= Comp.DisplayModeCount) Then
                    WroteOK := False
                Else
                Begin
                    Comp.DisplayMode := StrToIntDef(Value, 0);
                    If Comp.DisplayMode <> StrToIntDef(Value, 0) Then WroteOK := False;
                End;
            End
            Else
            Begin
                Comp := Obj;
                If Not WriteFootprintPinMap(Comp, Value) Then WroteOK := False;
            End;
        End
        Else If PropName = 'FontId' Then
        Begin
            { A font id is a positive whole number from the font manager. '0' }
            { was written as font 0 and 'abc' as font 1; both are refused now. }
            If Not SchObjectHasFontId(Obj) Then
                NotePropertyDiag('unknown', PropName)
            Else If StrToIntDef(Value, 0) < 1 Then
                WroteOK := False
            Else
                Obj.FontId := StrToIntDef(Value, 0);
        End
        Else If PropName = 'TextFontId' Then
        Begin
            If Obj.ObjectId = eSheetEntry Then
            Begin
                EntryObj := Obj;
                EntryObj.TextFontID := StrToIntDef(Value, 1);
            End
            Else If Obj.ObjectId = eHarnessEntry Then
            Begin
                HarnessEntryObj := Obj;
                HarnessEntryObj.TextFontID := StrToIntDef(Value, 1);
            End
            Else
                NotePropertyDiag('unknown', PropName);
        End
        { A sheet symbol's Source and a sheet entry's Bus Text Style, Kind and
          Harness Type, by the names the reader gives (see GetSchProperty).
          A name that is none of them is refused, never written as a
          default. }
        Else If PropName = 'SymbolType' Then
        Begin
            EnumVal := SheetSymbolTypeOrdinal(Value);
            If (Obj.ObjectId = eSheetSymbol) And (EnumVal >= 0) Then
            Begin
                SymObj := Obj;
                SymObj.SymbolType := EnumVal;
            End
            Else
                Matched := False;
        End
        Else If (PropName = 'TextStyle') Or (PropName = 'ArrowKind')
             Or (PropName = 'HarnessType') Then
        Begin
            If PropName = 'TextStyle' Then EnumVal := BusTextStyleOrdinal(Value)
            Else If PropName = 'ArrowKind' Then EnumVal := ArrowKindOrdinal(Value)
            Else EnumVal := 0;
            If (Obj.ObjectId = eSheetEntry) And (EnumVal >= 0) Then
            Begin
                EntryObj := Obj;
                If PropName = 'TextStyle' Then EntryObj.TextStyle := EnumVal
                Else If PropName = 'ArrowKind' Then EntryObj.ArrowKind := EnumVal
                Else EntryObj.HarnessType := Value;
            End
            Else
                Matched := False;
        End
        { A sheet entry's place on its symbol. Written, then the entry is put
          on the edge from both, as the placer does: Altium leaves its
          Location alone until the sheet is reloaded (issue #273). }
        Else If (PropName = 'Side') Or (PropName = 'DistanceFromTop') Then
        Begin
            If PropName = 'Side' Then EnumVal := SheetEntrySideOrdinal(Value)
            Else EnumVal := 0;
            If (Obj.ObjectId = eSheetEntry) And (EnumVal >= 0) Then
            Begin
                EntryObj := Obj;
                If PropName = 'Side' Then EntryObj.Side := EnumVal
                Else EntryObj.DistanceFromTop := MilsToCoordF(StrToFloatDef(Value, 0));
                SymObj := EntryObj.OwnerSheetSymbol;
                If SymObj <> Nil Then PositionSheetEntry(SymObj, EntryObj);
            End
            { A harness entry sits on its connector by these two alone. }
            Else If (Obj.ObjectId = eHarnessEntry) And (EnumVal >= 0) Then
            Begin
                HarnessEntryObj := Obj;
                If PropName = 'Side' Then HarnessEntryObj.Side := EnumVal
                Else HarnessEntryObj.DistanceFromTop := MilsToCoordF(StrToFloatDef(Value, 0));
            End
            Else
                Matched := False;
        End
        Else If PropName = 'LineWidth'   Then Obj.LineWidth := StrToIntDef(Value, 1)
        Else If PropName = 'Style'       Then
        Begin
            If (Obj.ObjectId = ePort) And (Not PortStyleKept(Value)) Then
            Begin
                WroteOK := False;
                NoteNextStep('Not written: a port''s Style is only its axis, 0 '
                    + 'horizontal or 4 vertical; Altium resets any other value '
                    + 'to one of those when the sheet is reopened.');
            End
            Else
                Obj.Style := StrToIntDef(Value, 0);
        End
        Else If PropName = 'IOType'      Then Obj.IOType := StrToIntDef(Value, 0)
        Else If PropName = 'Alignment'   Then Obj.Alignment := StrToIntDef(Value, 0)
        Else If PropName = 'Electrical'  Then Obj.Electrical := ElectricalOrdinal(Value)
        Else If (PropName = 'SwapIdPart') Or (PropName = 'SwapIdPin') Or
                (PropName = 'SwapIdPair') Or (PropName = 'SwapIdPartPin') Then
        Begin
            SwapPin := Obj;
            If PropName = 'SwapIdPart' Then SwapPin.SetState_SwapIdPart(Value)
            Else If PropName = 'SwapIdPin' Then SwapPin.SetState_SwapIdPin(Value)
            Else If PropName = 'SwapIdPair' Then SwapPin.SetState_SwapIdPair(Value)
            Else SwapPin.SetState_SwapIdPartPin(Value);
        End
        Else If PropName = 'Color'       Then Obj.Color := StrToIntDef(Value, 0)
        Else If PropName = 'AreaColor'   Then Obj.AreaColor := StrToIntDef(Value, 0)
        Else If PropName = 'TextColor' Then
        Begin
            If SchObjectHasTextColor(Obj) Then
            Begin
                If Obj.ObjectId = ePort Then
                Begin
                    PortObj := Obj;
                    PortObj.TextColor := StrToIntDef(Value, 0);
                End
                Else If Obj.ObjectId = eSheetEntry Then
                Begin
                    EntryObj := Obj;
                    EntryObj.TextColor := StrToIntDef(Value, 0);
                End
                Else If (Obj.ObjectId = eTextFrame) Or (Obj.ObjectId = eNote) Then
                Begin
                    FrameObj := Obj;
                    FrameObj.TextColor := StrToIntDef(Value, 0);
                End
                Else
                Begin
                    HarnessEntryObj := Obj;
                    HarnessEntryObj.TextColor := StrToIntDef(Value, 0);
                End;
            End
            Else
                Matched := False;
        End
        Else If PropName = 'Justification' Then Obj.Justification := StrToIntDef(Value, 0)

        // Coord properties (expected in mils)
        Else If PropName = 'Width'       Then Obj.Width := MilsToCoordF(StrToFloatDef(Value, 0))
        Else If PropName = 'PinLength'   Then Obj.PinLength := MilsToCoordF(StrToFloatDef(Value, 0))
        Else If (PropName = 'XSize') Or (PropName = 'YSize') Then
        Begin
            If Not SchObjectHasSize(Obj) Then
                Matched := False
            Else If PropName = 'XSize' Then
                Obj.XSize := MilsToCoordF(StrToFloatDef(Value, 0))
            Else
                Obj.YSize := MilsToCoordF(StrToFloatDef(Value, 0));
        End

        // Parameter / designator display settings
        Else If ((PropName = 'ShowName') Or IsSchParamDisplayProp(PropName))
            And SchHasParamDisplay(Obj) Then
            WroteOK := WriteSchParamDisplayProp(Obj, PropName, Value)
        Else If IsSchParamDisplayProp(PropName) Then
            Matched := False

        // Pin text settings (issue #77)
        Else If IsSchPinProp(PropName) Then
        Begin
            If Obj.ObjectId = ePin Then
                WroteOK := WriteSchPinProp(Obj, PropName, Value)
            Else
                Matched := False;
        End

        // Boolean properties
        Else If PropName = 'IsHidden' Then
        Begin
            If SchObjectHasIsHidden(Obj) Then
                Obj.IsHidden := StrToBool(Value)
            Else
                Matched := False;
        End
        Else If PropName = 'IsSolid' Then
        Begin
            If SchObjectHasIsSolid(Obj) Then
                Obj.IsSolid := StrToBool(Value)
            Else
                Matched := False;
        End
        Else If PropName = 'Collapsed' Then
        Begin
            If (Obj.ObjectId = eBlanket) Or (Obj.ObjectId = eNote) Or (Obj.ObjectId = eCompileMask) Then
                Obj.Collapsed := StrToBool(Value)
            Else
                Matched := False;
        End
        Else If PropName = 'LineStyle' Then
        Begin
            If SchObjectHasLineStyle(Obj) And IsIntStr(Value) Then
                Obj.LineStyle := StrToIntDef(Value, 0)
            Else
                Matched := False;
        End
        Else If PropName = 'Transparent' Then
        Begin
            If SchObjectHasTransparent(Obj) Then
                Obj.Transparent := StrToBool(Value)
            Else
                Matched := False;
        End
        { The whole outline at once, 'x1,y1,x2,y2,...' in mils: how a
          blanket (or any polygon) is reshaped. At least 3 points. }
        Else If PropName = 'Vertices' Then
        Begin
            If SchHasVertexArray(Obj) And (Obj.ObjectId <> eWire) And (Obj.ObjectId <> eBus) Then
                WroteOK := SetSchVertices(Obj, Value)
            Else
                Matched := False;
        End
        { MIRROR IS NOT A PLAIN PROPERTY WRITE.                              }
        {                                                                    }
        { Measured 2026-09-18 on a placed NE555: obj_modify reported         }
        { matched=1, saved=true, failed=0, and obj_query read IsMirrored     }
        { back as "true", while the editor kept drawing the component        }
        { unmirrored. That held across a focus switch to the sheet, which    }
        { forces a full redraw, so it is not a stale viewport. The plain     }
        { assignment lands somewhere the getter reads and the renderer does  }
        { not, which is the worst shape of failure available: the usual way  }
        { to verify a write agrees with you.                                 }
        {                                                                    }
        { SetState_IsMirrored is the SDK writer for the same property, the   }
        { same pattern UniqueId and CurrentPartID already use above.         }
        { GUARDED BY ObjectId: it lives on ISch_Component, and calling it on }
        { an object that lacks it is an undeclared identifier, which raises  }
        { a modal Try/Except cannot catch and wedges the polling loop. See   }
        { the PartCount note above for that failure in the wild.             }
        Else If PropName = 'IsMirrored' Then
        Begin
            If Obj.ObjectId = eSchComponent Then
            Begin
                { SetState_IsMirrored ONLY, never followed by the raw       }
                { property assignment. The SDK writer applies the mirror    }
                { AND the geometry that goes with it; assigning the bare    }
                { flag afterwards stamps the flag back over the top and     }
                { leaves the drawn shape and the flag disagreeing, which is }
                { unrecoverable from script: measured 2026-09-18, the part  }
                { then kept its old picture through GraphicallyInvalidate,  }
                { SetState_xSizeySize, a deselect, and a full Sch:Zoom      }
                { repaint at a new scale, and only a document reload fixed  }
                { it. FormatCopy makes exactly this split, SetState_ for    }
                { components and the plain property only for non-component  }
                { primitives.                                                }
                Comp := Obj;
                Try Comp.SetState_IsMirrored(StrToBool(Value)); Except WroteOK := False; End;
            End
            Else
            Begin
                Try Obj.IsMirrored := StrToBool(Value); Except WroteOK := False; End;
            End;
        End
        Else If PropName = 'Selection'   Then Obj.Selection := StrToBool(Value)
        Else Matched := False;

        { The geometry recompute and the repaint do NOT belong here. They   }
        { must happen after the SCHM_EndModify bracket closes; see          }
        { RefreshSchObjectRender below and its call site in the modify loop.}

        If Not Matched Then Result := 0
        Else If Not WroteOK Then Result := -1
        Else Result := 1;
    Except
        Result := -1;
    End;

    If Result = 0 Then NotePropertyDiag('unknown', PropName)
    Else If Result = -1 Then NotePropertyDiag('failed', PropName);
End;

{..............................................................................}
{ Filter matching                                                             }
{ FilterStr format: "PropName=Value|PropName2=Value2" (AND logic)            }
{ Empty filter matches everything.                                           }
{..............................................................................}

{..............................................................................}
{ RefreshSchObjectRender - rebuild what an edited object draws as.             }
{                                                                              }
{ CALL THIS AFTER SCHM_EndModify, NEVER INSIDE THE BRACKET.                    }
{                                                                              }
{ A component caches its own bounding geometry, and a property that changes    }
{ how it draws leaves that cache describing the OLD shape. Invalidating asks   }
{ Altium to paint again, not to work out what to paint, so the stale cache is  }
{ repainted faithfully. SetState_xSizeySize is what recomputes it, and it is   }
{ what the scripts that mirror successfully all call (FormatCopy,              }
{ CompPlaceFromLib, CompRename2, annotated there as "recalc bounding rect").   }
{                                                                              }
{ MEASURED 2026-09-18, and the ordering is the whole point. With the recompute }
{ and the invalidate issued INSIDE the Begin/EndModify bracket, the canvas     }
{ lagged the model by exactly one change: after writing IsMirrored=True the    }
{ Properties panel showed Mirrored ticked while the sheet drew the part        }
{ unmirrored, and after writing False it showed unticked while the sheet drew  }
{ it mirrored. The value was never wrong; the picture was always one edit      }
{ behind. Neither a focus switch, nor GraphicallyInvalidate, nor a full        }
{ Sch:Zoom Action=All repaint corrected it, because each of those repaints     }
{ from the cache rather than rebuilding it.                                    }
{                                                                              }
{ Component-guarded: SetState_xSizeySize is an ISch_Component method, and      }
{ calling it on an object without it is an undeclared identifier, which raises }
{ a modal Try/Except cannot catch and wedges the polling loop.                 }
{..............................................................................}

Procedure RefreshSchObjectRender(Obj : ISch_GraphicalObject);
Var
    Comp : ISch_Component;
Begin
    If Obj = Nil Then Exit;
    { SetState_xSizeySize USED TO BE CALLED HERE AND IS NOT ANY MORE.        }
    { It was added chasing a redraw theory that measurement later killed:    }
    { the canvas draws from primitive geometry, so a property write that     }
    { moves nothing has nothing to redraw. Worse, it is the most likely      }
    { cause of a component's pins relocating on their own during that        }
    { investigation, which is a silent edit to a design. Mirroring is now    }
    { handled properly by Gen_MirrorSchComponent, which moves the geometry.  }
    Try Obj.GraphicallyInvalidate; Except End;
End;

{..............................................................................}
{ SetSchPartId - show part PartId of a placed component, the way obj_modify   }
{ does it: both setters inside SCHM_BeginModify / SCHM_EndModify, then the     }
{ geometry recompute after the bracket.                                        }
{                                                                              }
{ The bracket is what the compiler listens to. MEASURED 2026-10-23 on a        }
{ 3-part STM32H743: parts written without it read back right (CurrentPartID   }
{ 1, 2, 3) and the sheet showed the right pins, yet the compiled netlist       }
{ listed U2's pins 700 times, hidden pins of the other parts joined nets (GND  }
{ into +3V3) and 208 of 264 nets matched. Writing the same values through     }
{ obj_batch_modify and recompiling gave 100 rows and 264 of 264.               }
{..............................................................................}
Procedure SetSchPartId(Comp : ISch_Component; PartId : Integer);
Begin
    SchBeginModify(Comp);
    { Two spellings of one write; the read-back settles whether it took. }
    Try Comp.SetState_CurrentPartID(PartId); Except { read back below } End;
    Try Comp.CurrentPartID := PartId; Except { read back below } End;
    SchEndModify(Comp);
    Try
        If Comp.CurrentPartID <> PartId Then NoteWriteFailed('CurrentPartID');
    Except
        NoteWriteFailed('CurrentPartID');
    End;
    RefreshSchObjectRender(Comp);
End;

Function MatchesFilter(Obj : ISch_GraphicalObject; FilterStr : String) : Boolean;
Var
    Remaining, Condition, PropName, Expected, Actual, Op : String;
    PipePos, EqPos : Integer;
Begin
    Result := True;
    If FilterStr = '' Then Exit;

    Remaining := FilterStr;
    While Remaining <> '' Do
    Begin
        // Extract next pipe-separated condition
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Condition := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Condition := Remaining;
            Remaining := '';
        End;

        // Parse "PropName=Value". A condition that is not one matches
        // nothing (see FilterProblem): skipped, it matched everything.
        If Trim(Condition) = '' Then Continue;
        EqPos := FilterOpPos(Condition);
        Op := FilterOpText(Condition);
        If (EqPos < 2) Or (Op = '') Then
        Begin
            Result := False;
            Exit;
        End;
        PropName := Copy(Condition, 1, EqPos - 1);
        Expected := Copy(Condition, EqPos + Length(Op), Length(Condition));

        { An unreadable property is not an empty value. In particular,
          TextColor= must never match power objects in a delete/filter.
          The guard reads Name=Value: Text!=x reached it as the name
          'Text!', and Text~x or Width>3 with no name at all, so it was
          skipped and the matcher read a property the object lacks. }
        Condition := PropName + '=' + Expected;
        If UnsupportedSchProperty(Obj, Condition) <> '' Then
        Begin
            NotePropertyDiag('unreadable', PropName);
            Result := False;
            Exit;
        End;

        // Compare
        Actual := GetSchProperty(Obj, PropName);
        If Not FilterOpHolds(Op, Actual, Expected, Actual = Expected) Then
        Begin
            Result := False;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ Parse comma-separated property names into JSON for one object              }
{..............................................................................}

Function BuildObjectJson(Obj : ISch_GraphicalObject; PropsStr : String) : String;
Var
    Remaining, PropName, PropValue : String;
    CommaPos : Integer;
    First : Boolean;
Begin
    Result := '{';
    First := True;
    Remaining := PropsStr;

    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            PropName := Copy(Remaining, 1, CommaPos - 1);
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else
        Begin
            PropName := Remaining;
            Remaining := '';
        End;

        PropValue := GetSchProperty(Obj, PropName);

        If Not First Then Result := Result + ',';
        First := False;
        Result := Result + '"' + EscapeJsonString(PropName) + '":"' + EscapeJsonString(PropValue) + '"';
    End;

    Result := Result + '}';
End;

{..............................................................................}
{ Apply pipe-separated "PropName=Value" assignments to an object             }
{..............................................................................}

Procedure ApplySetProperties(Obj : ISch_GraphicalObject; SetStr : String);
{ Location.X and Location.Y arriving in the SAME pipe-combined set used to be }
{ applied as two independent SetSchProperty writes. Measured on a live        }
{ eSchComponent: `Location.X=1200|Location.Y=7600` moved X and left Y at its  }
{ old value. Each write re-reads Obj.Location, and the copy handed back after }
{ the first move still carried the PREVIOUS coordinates, so writing it back   }
{ reverted the move that had just been made. Same class of bug as the         }
{ Location.X + Orientation interaction documented in SetSchProperty.          }
{                                                                             }
{ Fix: parse the whole set FIRST, coalesce both axes into ONE positional      }
{ write, then apply every remaining property. A component gets MoveToXY so    }
{ its child pins travel with the body (see SetSchProperty); everything else   }
{ owns no child primitives, so a plain Location assignment is correct for it  }
{ (a net label in particular must NOT go through MoveToXY -- it has no        }
{ MoveToXY and is not a component).                                           }
Var
    Remaining, Assignment, PropName, PropValue, UnsupportedProp : String;
    PipePos, EqPos : Integer;
    Loc : TLocation;
    Comp : ISch_Component;
    HasX, HasY : Boolean;
    NewX, NewY : Double;
Begin
    { Reject the whole assignment list before even the coalesced move.
      Otherwise Location/Color can change before a later TextColor fails. }
    UnsupportedProp := UnsupportedSchProperty(Obj, SetStr);
    If UnsupportedProp <> '' Then
    Begin
        NotePropertyDiag('unknown', UnsupportedProp);
        Exit;
    End;

    { Pass 1: collect the positional assignments without applying anything. }
    HasX := False;
    HasY := False;
    NewX := 0;
    NewY := 0;
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Assignment := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Assignment := Remaining;
            Remaining := '';
        End;

        EqPos := Pos('=', Assignment);
        If EqPos = 0 Then Continue;
        PropName := Copy(Assignment, 1, EqPos - 1);
        PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));

        If PropName = 'Location.X' Then
        Begin
            HasX := True;
            NewX := StrToFloatDef(PropValue, 0);
        End
        Else If PropName = 'Location.Y' Then
        Begin
            HasY := True;
            NewY := StrToFloatDef(PropValue, 0);
        End;
    End;

    { One positional write covering whichever axes were supplied. }
    If HasX Or HasY Then
    Begin
        Try
            Loc := Obj.Location;
            If HasX Then Loc.X := MilsToCoordF(NewX);
            If HasY Then Loc.Y := MilsToCoordF(NewY);
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Comp.MoveToXY(Loc.X, Loc.Y);
            End
            { A harness connector carries its entries: Location alone read }
            { back moved while the connector stayed drawn where it was.    }
            Else If Obj.ObjectId = eHarnessConnector Then
                MoveHarnessConnector(Obj, Loc.X, Loc.Y)
            Else
                Obj.Location := Loc;
            NoteSchWriteLanded(0);
        Except
            NotePropertyDiag('failed', 'Location');
        End;
    End;

    { Pass 2: every non-positional property, in the order it was given. }
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Assignment := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Assignment := Remaining;
            Remaining := '';
        End;

        EqPos := Pos('=', Assignment);
        If EqPos = 0 Then Continue;
        PropName := Copy(Assignment, 1, EqPos - 1);
        PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));

        { Already applied above as part of the coalesced positional write. }
        If (PropName = 'Location.X') Or (PropName = 'Location.Y') Then Continue;

        If SetSchProperty(Obj, PropName, PropValue) = 1 Then NoteSchWriteLanded(0);
    End;
End;

{ What a schematic write call says it changed. A modify that matched     }
{ objects but landed no write (every property unknown, refused or kept   }
{ by Altium, or the whole set rejected up front) changed nothing.        }
Function SchCallModified(IsMutating : Boolean; TotalMatched : Integer; Mode : String) : Boolean;
Begin
    Result := IsMutating And ((TotalMatched > 0) Or (Mode = 'create'));
    If Result And (Mode = 'modify') And (SchWritesLanded(0) = 0) Then
        Result := False;
End;

{..............................................................................}
{ Pull an optional owner-designator constraint (OwnerDesignator=X or          }
{ Designator=X) out of a pipe-separated parameter filter so a delete can      }
{ target one component (e.g. U1) instead of every part on the sheet. Returns  }
{ the designator value and rewrites FilterStr to the remaining conditions.    }
{..............................................................................}

Function PullOwnerDesignator(Var FilterStr : String) : String;
Var
    Remaining, Condition, Rest, PropName, PropUpper : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
    Rest := '';
    Remaining := FilterStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Condition := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else
        Begin
            Condition := Remaining;
            Remaining := '';
        End;

        EqPos := Pos('=', Condition);
        If EqPos > 0 Then
        Begin
            PropName := Copy(Condition, 1, EqPos - 1);
            PropUpper := UpperCase(PropName);
            If (PropUpper = 'OWNERDESIGNATOR') Or (PropUpper = 'DESIGNATOR') Then
            Begin
                Result := Copy(Condition, EqPos + 1, Length(Condition));
                Continue;
            End;
        End;

        { Keep every non-owner condition in the reduced filter. }
        If Rest = '' Then Rest := Condition
        Else Rest := Rest + '|' + Condition;
    End;
    FilterStr := Rest;
End;

{..............................................................................}
{ Delete schematic parameters by their REAL owner. A component parameter is   }
{ owned by its ISch_Component (created via Comp.AddSchObject), so             }
{ SchDoc.RemoveSchObject silently no-ops on it - the same wrong-owner trap    }
{ the sheet-entry delete works around. It must be removed via                 }
{ Comp.RemoveSchObject. Sheet-level (title-block) parameters are owned by the }
{ document and removed via SchDoc.RemoveSchObject. An optional owner          }
{ designator in the filter restricts the delete to one component. Caller      }
{ wraps this in PreProcess/PostProcess.                                       }
{..............................................................................}

{ Remove every parameter of ONE component that matches the filter.           }
{                                                                             }
{ Re-scans after each removal rather than deleting inside the walk: removing  }
{ a child invalidates the iterator, and a walk that continues past it skips   }
{ entries, which reads as a filter that matched less than it did.             }
{                                                                             }
{ A VISIBLE PARAMETER OF THE SYMBOL ON SCREEN IS HELD BY THE LIBRARY.         }
{ MEASURED on AD 25.8: Comp.RemoveSchObject on a shown symbol's visible       }
{ parameter does nothing, so every pass found the same parameter again and    }
{ the loop counted it until the guard ran out: matched 10000, and the         }
{ parameter still there after a save and reopen. A hidden one went at once.   }
{ The library document holds the symbol's visible children while it is on    }
{ screen, and hiding one does not move it back until a reload. So when the    }
{ same object comes back, it is removed from Lib (the library document; Nil   }
{ on a sheet), and a remove that took nothing is never counted.               }

Procedure RemoveMatchingParamsFromComponent(Lib : ISch_Document;
    Comp : ISch_Component; ReducedFilter : String; Var TotalMatched : Integer);
Var
    ParamIter : ISch_Iterator;
    Param, FoundParam : ISch_GraphicalObject;
    Guard, ThisAddr, LastAddr, SameTries : Integer;
    Pending : Boolean;
    StuckParam : ISch_Parameter;
    StuckName : String;
Begin
    If Comp = Nil Then Exit;
    Guard := 10000;
    LastAddr := 0;
    SameTries := 0;
    Pending := False;
    While Guard > 0 Do
    Begin
        FoundParam := Nil;
        ParamIter := Comp.SchIterator_Create;
        Try
            ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
            Param := ParamIter.FirstSchObject;
            While Param <> Nil Do
            Begin
                If MatchesFilter(Param, ReducedFilter) Then
                Begin
                    FoundParam := Param;
                    Break;
                End;
                Param := ParamIter.NextSchObject;
            End;
        Finally
            Comp.SchIterator_Destroy(ParamIter);
        End;
        ThisAddr := 0;
        If FoundParam <> Nil Then
            Try ThisAddr := FoundParam.I_ObjectAddress; Except End;
        { The last remove took: the object it acted on is not found again. }
        If Pending And ((FoundParam = Nil) Or (ThisAddr <> LastAddr)) Then
        Begin
            Inc(TotalMatched);
            Pending := False;
        End;
        If FoundParam = Nil Then Break;
        If (ThisAddr <> 0) And (ThisAddr = LastAddr) Then
        Begin
            { Still there after the component's remove. }
            Inc(SameTries);
            If (SameTries > 1) Or (Lib = Nil) Then
            Begin
                { Name the one that stayed. MEASURED on AD 25.8.1: a symbol's
                  Comment is never removed (Altium keeps the symbol's own
                  comment field); the reply only said a parameter stayed. }
                StuckName := '';
                StuckParam := FoundParam;
                Try StuckName := StuckParam.Name; Except StuckName := ''; End;
                If SameText(StuckName, 'Comment') Then
                    NoteDeleteFault('the parameter "Comment" stayed on the symbol: Altium '
                        + 'keeps a symbol''s own Comment; clear its text or hide it instead')
                Else
                    NoteDeleteFault('the parameter "' + StuckName
                        + '" stayed on the symbol after it was removed');
                Break;
            End;
            Try Lib.RemoveSchObject(FoundParam); Except { the next pass finds it again and reports the fault } End;
            Dec(Guard);
            Continue;
        End;
        SameTries := 0;
        LastAddr := ThisAddr;
        Comp.RemoveSchObject(FoundParam);
        Pending := True;
        Dec(Guard);
    End;
End;

Procedure DeleteParametersAnyOwner(SchDoc : ISch_Document; FilterStr : String;
    Var TotalMatched : Integer);
Var
    OwnerDesig, ReducedFilter, CompDesig : String;
    CompIter, ParamIter : ISch_Iterator;
    Comp : ISch_Component;
    Param, FoundParam : ISch_GraphicalObject;
    Guard : Integer;
    IsLib : Boolean;
Begin
    ReducedFilter := FilterStr;
    OwnerDesig := PullOwnerDesignator(ReducedFilter);

    { A SCHLIB HAS NO PLACED COMPONENTS TO ITERATE.                          }
    {                                                                         }
    { The component walk below uses SchIterator with an eSchComponent filter, }
    { which returns nothing at all on a library: a SchLib's symbols are not   }
    { components placed on its canvas, each is its own internal sheet. So     }
    { every parameter delete against a library reported matched 0 while the   }
    { parameters sat there, and the document-level pass that followed saw     }
    { only the library's OWN parameter, which is why a read came back with    }
    { one entry called Value rather than the component's full set.            }
    {                                                                         }
    { The symbol is the one on screen: a lib_component scope makes its symbol }
    { current (SelectLibComponentPart), and with no scope the symbol the user }
    { has open is the one meant. The held reference to the last symbol      }
    { created is only a fallback; preferred, it sent a delete to a symbol    }
    { the user had since left (issue #107).                                  }
    IsLib := False;
    Try IsLib := (SchDoc.ObjectId = eSchLib); Except IsLib := False; End;
    If IsLib Then
    Begin
        { Same guard as the sheet path: without a filter this would strip     }
        { every parameter off the symbol.                                     }
        If ReducedFilter = '' Then Exit;
        Comp := Nil;
        Try Comp := SchDoc.CurrentSchComponent; Except Comp := Nil; End;
        If Comp = Nil Then Comp := RememberedLibComponent(SchDoc);
        RemoveMatchingParamsFromComponent(SchDoc, Comp, ReducedFilter, TotalMatched);
        Exit;
    End;

    { Component-owned parameters. Guard against a catastrophic "delete every   }
    { parameter on every part": require an owner designator or a non-empty     }
    { reduced filter before touching component parameters.                     }
    If (OwnerDesig <> '') Or (ReducedFilter <> '') Then
    Begin
        CompIter := SchDoc.SchIterator_Create;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Comp := CompIter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                CompDesig := '';
                Try CompDesig := Comp.Designator.Text; Except End;
                If (OwnerDesig = '') Or
                   (UpperCase(CompDesig) = UpperCase(OwnerDesig)) Then
                Begin
                    { Removing a child does not disturb the outer         }
                    { component iterator, so the shared helper is safe here. }
                    RemoveMatchingParamsFromComponent(
                        Nil, Comp, ReducedFilter, TotalMatched);
                End;
                Comp := CompIter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(CompIter);
        End;
    End;

    { Sheet-level (document-owned) parameters. Skipped when an owner           }
    { designator was given - that means "this component only".                }
    If OwnerDesig = '' Then
    Begin
        Guard := 10000;
        While Guard > 0 Do
        Begin
            FoundParam := Nil;
            ParamIter := SchDoc.SchIterator_Create;
            Try
                ParamIter.SetState_IterationDepth(eIterateFirstLevel);
                ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                Param := ParamIter.FirstSchObject;
                While Param <> Nil Do
                Begin
                    If MatchesFilter(Param, ReducedFilter) Then
                    Begin
                        FoundParam := Param;
                        Break;
                    End;
                    Param := ParamIter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(ParamIter);
            End;
            If FoundParam = Nil Then Break;
            SchDoc.RemoveSchObject(FoundParam);
            Inc(TotalMatched);
            Dec(Guard);
        End;
    End;
End;

{..............................................................................}
{ Helper: Process objects in a single SchDoc                                  }
{ Mode: 'query', 'modify', 'delete'                                         }
{..............................................................................}

{ Try to take an object out of every component on the sheet: the delete's  }
{ fallback for one that SchDoc.RemoveSchObject left in place. A component   }
{ that does not hold it raises or does nothing; both are absorbed.          }
Procedure RemoveFromComponents(SchDoc : ISch_Document; Obj : ISch_GraphicalObject);
Var
    It : ISch_Iterator;
    Comp : ISch_Component;
Begin
    It := SchDoc.SchIterator_Create;
    Try
        It.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := It.FirstSchObject;
        While Comp <> Nil Do
        Begin
            Try Comp.RemoveSchObject(Obj); Except { not this component's; the caller looks again } End;
            Comp := It.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(It);
    End;
End;

{ A pin told apart from the other copies of it: designator, part, display }
{ mode and place. Two pins share one only where two parts overlap exactly. }
Function SchPinKey(Pin : ISch_Pin) : String;
Var
    Des : String;
    PartNo, ModeNo, PX, PY : Integer;
Begin
    Des := '';
    PartNo := 0;
    ModeNo := 0;
    PX := 0;
    PY := 0;
    Try Des := Pin.Designator; Except End;
    Try PartNo := Pin.OwnerPartId; Except End;
    Try ModeNo := Pin.OwnerPartDisplayMode; Except End;
    Try PX := Pin.Location.X; Except End;
    Try PY := Pin.Location.Y; Except End;
    Result := Des + '|' + IntToStr(PartNo) + '|' + IntToStr(ModeNo) + '|'
        + IntToStr(PX) + '|' + IntToStr(PY);
End;

{ The pins of one placed part that it does not show, as SchPinKey keys.   }
{ Every part of a multi-part symbol holds all the symbol's pins (the      }
{ others' hidden at its own place) and every display mode's pins; a pin   }
{ shows only on its own part, in the mode the part is drawn in. Keys, not }
{ object addresses: a list of addresses once stopped the bridge with a    }
{ Dispatch-to-Boolean conversion (AD 25.8).                               }
Procedure AddUnshownPins(Comp : ISch_Component; Unshown : TStringList);
Var
    Walk : ISch_Iterator;
    Pin : ISch_Pin;
    DrawnMode, OwnMode : Integer;
Begin
    DrawnMode := 0;
    Try DrawnMode := Comp.DisplayMode; Except DrawnMode := 0; End;
    Walk := Comp.SchIterator_Create;
    Try
        Walk.AddFilter_ObjectSet(MkSet(ePin));
        Pin := Walk.FirstSchObject;
        While Pin <> Nil Do
        Begin
            OwnMode := DrawnMode;
            Try OwnMode := Pin.OwnerPartDisplayMode; Except OwnMode := DrawnMode; End;
            If (OwnMode <> DrawnMode) Or (Not PinShownOn(Comp, Pin)) Then
                Unshown.Add(SchPinKey(Pin));
            Pin := Walk.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Walk);
    End;
End;

{ The SchPinKey of every pin on the sheet that no placed part shows. }
Procedure CollectHiddenPinKeys(SchDoc : ISch_Document; Hidden : TStringList);
Var
    Parts : ISch_Iterator;
    Comp : ISch_Component;
Begin
    Parts := SchDoc.SchIterator_Create;
    Try
        Parts.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Parts.FirstSchObject;
        While Comp <> Nil Do
        Begin
            AddUnshownPins(Comp, Hidden);
            Comp := Parts.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Parts);
    End;
End;

Function ProcessSchDocObjects(SchDoc : ISch_Document; ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; DocPath : String;
    Var TotalMatched : Integer; Limit : Integer) : String;
Var
    Iterator, SymIter : ISch_Iterator;
    Obj, FoundObj : ISch_GraphicalObject;
    Sym : ISch_SheetSymbol;
    LibComp : ISch_Component;
    Removed, ParamsOfSymbol, IsLibDoc : Boolean;
    SchLibDoc : ISch_Lib;
    ObjJson, SymbolRef : String;
    First : Boolean;
    MaxIter, LastAddr, ThisAddr, SameTries : Integer;
    UnshownPins : TStringList;
    PinObj : ISch_Pin;
    OnlyShown, Counted, OwnWalk : Boolean;
    ScopeComp : ISch_Component;
    WantPart, ObjPart, ObjMode, CompMode : Integer;
    ObjRef : String;
Begin
    Result := '';
    First := (TotalMatched = 0);

    // Delete mode: one-at-a-time to avoid iterator invalidation.
    If Mode = 'delete' Then
    Begin
        SchBeginEdit(SchDoc, '');

        { Parameters are owned by their component (or by the document for   }
        { sheet-level params), NOT reachable/removable through the plain    }
        { doc-level RemoveSchObject loop below. Dispatch by owner.          }
        If ObjTypeInt = eParameter Then
        Begin
            DeleteParametersAnyOwner(SchDoc, FilterStr, TotalMatched);
            SchEndEdit(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
            Exit;
        End;

        MaxIter := 100000;
        LastAddr := 0;
        SameTries := 0;
        While MaxIter > 0 Do
        Begin
            Iterator := SchDoc.SchIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            FoundObj := Nil;
            Obj := Iterator.FirstSchObject;
            While Obj <> Nil Do
            Begin
                If MatchesFilter(Obj, FilterStr) Then
                Begin
                    FoundObj := Obj;
                    Break;
                End;
                Obj := Iterator.NextSchObject;
            End;
            SchDoc.SchIterator_Destroy(Iterator);
            If FoundObj = Nil Then Break;
            { The same object found again means the last remove did nothing.
              Counting on would report up to 100000 deletions of one object. }
            ThisAddr := 0;
            Try ThisAddr := FoundObj.I_ObjectAddress; Except End;
            If (ThisAddr <> 0) And (ThisAddr = LastAddr) Then
            Begin
                { STILL THERE: try twice more before calling it a fault. A    }
                { leftover No ERC (2026-10-23) moved fine and would not go: an }
                { object registered twice needs a second remove, and one a    }
                { component holds is removed through that component. Counted  }
                { once whatever it takes.                                     }
                Inc(SameTries);
                If SameTries > 2 Then
                Begin
                    Dec(TotalMatched);
                    NoteDeleteFault('an object stayed on the sheet after it was removed');
                    Break;
                End;
                RemoveFromComponents(SchDoc, FoundObj);
                Try SchDoc.RemoveSchObject(FoundObj); Except { the next pass finds it again and counts the fault } End;
                { MEASURED: the stuck No ERC then went from the sheet and the }
                { saved file, yet the compiler kept a copy at its old place  }
                { until the document was closed and reloaded.                }
                NoteNextStep('An object here needed more than one remove. The '
                    + 'sheet and the saved file are right, but the compiled '
                    + 'model can keep a stale copy: if ERC or the netlist still '
                    + 'shows it, save, close the document (app_close_document) '
                    + 'and reload it (proj_load_sheets).');
                Dec(MaxIter);
                Continue;
            End;
            SameTries := 0;
            LastAddr := ThisAddr;
            { Sheet entries belong to their parent sheet symbol's child       }
            { container, not the SchDoc. SchDoc.RemoveSchObject silently      }
            { no-ops on them, leaving the entry placed. Walk sheet symbols    }
            { until one of them accepts the remove. Try/Except absorbs the    }
            { wrong-parent failures.                                          }
            If ObjTypeInt = eSheetEntry Then
            Begin
                Removed := False;
                SymIter := SchDoc.SchIterator_Create;
                SymIter.AddFilter_ObjectSet(MkSet(eSheetSymbol));
                Try
                    Sym := SymIter.FirstSchObject;
                    While Sym <> Nil Do
                    Begin
                        Try
                            Sym.RemoveSchObject(FoundObj);
                            Removed := True;
                            SchRegisterObject(Sym, FoundObj);
                            Break;
                        Except { not this symbol's entry; try the next } End;
                        Sym := SymIter.NextSchObject;
                    End;
                Finally
                    SchDoc.SchIterator_Destroy(SymIter);
                End;
                If Not Removed Then
                    Try SchDoc.RemoveSchObject(FoundObj); Except { the next pass finds it again and counts the fault } End;
            End
            Else
            Begin
                { A remove that raises used to end the whole request as an
                  error, with the objects already removed uncounted: 25 net
                  labels gone and the reply said deleted 0 (issue #94). }
                Removed := True;
                Try
                    SchDoc.RemoveSchObject(FoundObj);
                Except
                    Removed := False;
                End;
                If Not Removed Then
                Begin
                    NoteDeleteFault('removing an object raised an error');
                    Break;
                End;
                { The removal is announced, as Altium's own delete example   }
                { does. Without it the compiled model kept the object: a     }
                { deleted power port still joined its rail to a new label    }
                { until the sheet was closed and reopened.                   }
                SchRegisterObject(SchDoc, FoundObj);
            End;
            Inc(TotalMatched);
            Dec(MaxIter);
        End;
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
        If (SchDoc <> Nil) And (TotalMatched > 0) Then MarkDocDirtyByPath(SchDoc.DocumentName);
        Exit;
    End;

    // Modify mode: wrap in PreProcess/PostProcess for undo support.
    If Mode = 'modify' Then
        SchBeginEdit(SchDoc, '');

    { A SYMBOL'S PARAMETERS AND ITS DESIGNATOR ARE OWNED BY THE SYMBOL. The
      library's own iterator does not reach them, so a parameter query or
      modify in a lib_component scope matched nothing while the parameters
      sat there, and a designator was never found in a library at all
      (issue #107). Walk the symbol on screen instead, which a
      lib_component scope has made current; the last symbol created is only
      the fallback, as in DeleteParametersAnyOwner. }
    IsLibDoc := False;
    Try IsLibDoc := (SchDoc.ObjectId = eSchLib); Except End;
    { THE SYMBOL A lib_component SCOPE NAMES. The scope used to make the     }
    { symbol current and walk the library's sheet, which showed none of its  }
    { hidden pins (a hidden power pin could not be given its HiddenNetName), }
    { and for eSchComponent walked every symbol: ShowHiddenPins=true on one  }
    { named symbol matched all 25 in the library (2026-10-30). The symbol is }
    { the one SelectLibComponentPart fetched through the library and kept,   }
    { checked by name. Calling LookupLibComponent from this unit instead     }
    { left the whole unit undeclared in Altium (HandleGenericCommand), with  }
    { no compile error shown: measured, 2026.10.30.6.                       }
    ScopeComp := Nil;
    If IsLibDoc And (LibScopeName <> '') Then
    Begin
        { A remembered symbol of a closed library is freed: reading its   }
        { LibReference raises an error box no Try catches. Only the one   }
        { remembered for THIS document is read.                           }
        ObjRef := '';
        If RememberedLibComponent(SchDoc) <> Nil Then
        Begin
            Try ObjRef := LastCreatedLibComponent.LibReference; Except End;
        End;
        If ObjRef = LibScopeName Then ScopeComp := RememberedLibComponent(SchDoc);
    End;
    ParamsOfSymbol := False;
    LibComp := Nil;
    If (ObjTypeInt = eParameter) Or (ObjTypeInt = eDesignator) Then
    Begin
        ParamsOfSymbol := IsLibDoc;
        If ParamsOfSymbol Then
        Begin
            LibComp := ScopeComp;
            If LibComp = Nil Then
            Begin
                Try LibComp := SchDoc.CurrentSchComponent; Except End;
            End;
            If LibComp = Nil Then LibComp := RememberedLibComponent(SchDoc);
            If LibComp = Nil Then ParamsOfSymbol := False;
        End;
    End;
    { The symbol's own primitives: every pin, hidden or not, of the part   }
    { asked for and those shared by all parts (part 0).                     }
    OwnWalk := (Not ParamsOfSymbol) And (ScopeComp <> Nil)
        And (ObjTypeInt <> eSchComponent);
    If OwnWalk Then
    Begin
        LibComp := ScopeComp;
        WantPart := LibScopePart;
        If WantPart < 1 Then WantPart := 1;
    End;
    SymbolRef := '';
    If ParamsOfSymbol Or OwnWalk Then
        Try SymbolRef := LibComp.LibReference; Except SymbolRef := ''; End;
    If ParamsOfSymbol Or OwnWalk Then
        Iterator := LibComp.SchIterator_Create
    { The symbols of a SchLib are not on its sheet iterator, so a component
      query walked nothing (issue #87). The library's own iterator reaches
      them, as Library.pas does. }
    Else If IsLibDoc And (ObjTypeInt = eSchComponent) Then
    Begin
        SchLibDoc := SchDoc;
        Iterator := SchLibDoc.SchLibIterator_Create;
    End
    Else
        Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));

    { Only the pins a part shows. A pin query on a sheet answered every    }
    { copy a multi-part or multi-mode symbol carries, and a modify wrote    }
    { them all. A filter that names OwnerPartId or OwnerPartDisplayMode is  }
    { asking about the copies, and gets them.                               }
    OnlyShown := False;
    If (ObjTypeInt = ePin) And (Not IsLibDoc) And (Not ParamsOfSymbol) Then
        If Pos('OwnerPart', FilterStr) = 0 Then OnlyShown := True;
    UnshownPins := TStringList.Create;
    If OnlyShown Then CollectHiddenPinKeys(SchDoc, UnshownPins);

    Obj := Iterator.FirstSchObject;
    While Obj <> Nil Do
    Begin
        If (Limit > 0) And (TotalMatched >= Limit) Then Break;

        Counted := True;
        If OnlyShown Then
        Begin
            PinObj := Obj;
            Counted := UnshownPins.IndexOf(SchPinKey(PinObj)) < 0;
        End;
        { The part the scope asked for (or part 1) and its display mode, as }
        { the library sheet showed them; part 0 belongs to every part.      }
        If OwnWalk And (Pos('OwnerPart', FilterStr) = 0) Then
        Begin
            ObjPart := 0;
            Try ObjPart := Obj.OwnerPartId; Except End;
            If (ObjPart <> 0) And (ObjPart <> WantPart) Then Counted := False;
            ObjMode := 0;
            CompMode := 0;
            Try ObjMode := Obj.OwnerPartDisplayMode; Except End;
            Try CompMode := LibComp.DisplayMode; Except End;
            If ObjMode <> CompMode Then Counted := False;
        End;
        If (ScopeComp <> Nil) And (ObjTypeInt = eSchComponent) Then
        Begin
            ObjRef := '';
            Try ObjRef := Obj.LibReference; Except End;
            If ObjRef <> LibScopeName Then Counted := False;
        End;

        If Counted And MatchesFilter(Obj, FilterStr) Then
        Begin
            If Mode = 'query' Then
            Begin
                ObjJson := BuildObjectJson(Obj, PropsStr);
                If Length(ObjJson) <= 2 Then
                    ObjJson := '{"_doc":"' + EscapeJsonString(DocPath) + '"}'
                Else
                    ObjJson := Copy(ObjJson, 1, 1) + '"_doc":"' + EscapeJsonString(DocPath) + '",' + Copy(ObjJson, 2, Length(ObjJson));
                { Which symbol answered, so a caller can tell. }
                If SymbolRef <> '' Then
                    ObjJson := Copy(ObjJson, 1, 1) + '"_symbol":"' + EscapeJsonString(SymbolRef) + '",' + Copy(ObjJson, 2, Length(ObjJson));
                If Not First Then Result := Result + ',';
                First := False;
                Result := Result + ObjJson;
            End
            Else If Mode = 'modify' Then
            Begin
                // Bracket the property writes in SCHM_BeginModify /
                // SCHM_EndModify so the editor sub-systems and the undo
                // stack observe the edit. Without these the property is
                // updated in memory but the UI never re-renders and
                // SaveAll may skip the doc.
                SchBeginModify(Obj);
                ApplySetProperties(Obj, SetStr);
                SchEndModify(Obj);
                { AFTER the bracket, not inside it: a recompute or an     }
                { invalidate issued mid-modify is consumed by EndModify   }
                { repainting from the pre-change cache, which leaves the  }
                { canvas exactly one edit behind the model.                }
                RefreshSchObjectRender(Obj);
            End;

            Inc(TotalMatched);
        End;

        Obj := Iterator.NextSchObject;
    End;

    If ParamsOfSymbol Or OwnWalk Then
        LibComp.SchIterator_Destroy(Iterator)
    Else
        SchDoc.SchIterator_Destroy(Iterator);
    UnshownPins.Free;

    If Mode = 'modify' Then
        SchEndEdit(SchDoc, 'Edit');
End;

{..............................................................................}
{ Helper: Iterate project schematic documents                                 }
{..............................................................................}

Function IterateProjectDocs(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; ProjectPath : String; Limit : Integer) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    I, TotalMatched, SheetsProcessed, SheetsMarked : Integer;
    FilePath, JsonItems, BadCond : String;
    IsMutating : Boolean;
Begin
    BadCond := FilterProblem(FilterStr);
    If BadCond <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_FILTER', BadFilterMessage(BadCond));
        Exit;
    End;
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    If ProjectPath <> '' Then
        Project := FindProjectByPath(Workspace, ProjectPath)
    Else
        Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found');
        Exit;
    End;

    TotalMatched := 0;
    SheetsProcessed := 0;
    SheetsMarked := 0;
    JsonItems := '';
    IsMutating := (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create');

    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;

        FilePath := Doc.DM_FullPath;

        // Do NOT force-open documents. Calling RunProcess('Client:OpenDocument')
        // loads the file but strips its project association, so it appears
        // as a "free document" with the absolute path as its tab title,
        // clutters the UI and breaks project-member semantics.
        //
        // Instead, only iterate documents that SchServer already has in
        // memory. A project sheet that isn't loaded (DM_Compile didn't wake
        // it up for some reason) is skipped, and ProjectSheet counts it so
        // the reply says so; proj_load_sheets loads them.
        SchDoc := ProjectSheet(FilePath);
        If SchDoc = Nil Then Continue;

        JsonItems := JsonItems + ProcessSchDocObjects(SchDoc, ObjTypeInt,
            FilterStr, PropsStr, SetStr, Mode, FilePath, TotalMatched, Limit);

        If IsMutating Then
        Begin
            Try SchDoc.GraphicallyInvalidate; Except End;
            // MARKS THE DOCUMENT, it does not write. The flush is
            // app_save_all. This comment used to say the procedure did
            // SetModified + DoFileSave and wrote straight to disk, which
            // was never true and is how three tool docstrings came to
            // promise a save that never happened.
            MarkDocDirtyByPath(FilePath);
            Inc(SheetsMarked);
        End;

        Inc(SheetsProcessed);

        If (Limit > 0) And (TotalMatched >= Limit) Then Break;
    End;

    If Mode = 'query' Then
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched) +
            ',"sheets_processed":' + IntToStr(SheetsProcessed)
            + ',"properties":' + RenderPropertyDiagJson(0) + '}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched) +
            ',"sheets_processed":' + IntToStr(SheetsProcessed) +
            { Named for what it counts. It was "sheets_saved", and the
              documents were only marked dirty; app_save_all is what
              writes them. }
            ',"sheets_marked":' + IntToStr(SheetsMarked)
            + ModifyOutcomeJson(0) + '}');
End;

{..............................................................................}
{ Helper: Process active document only                                       }
{..............................................................................}

Function ProcessActiveDoc(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    TotalMatched : Integer;
    JsonItems, DocPath, SavedStr, BadCond : String;
    IsMutating, Saved : Boolean;
Begin
    BadCond := FilterProblem(FilterStr);
    If BadCond <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_FILTER', BadFilterMessage(BadCond));
        Exit;
    End;
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    DocPath := SchDoc.DocumentName;
    TotalMatched := 0;
    JsonItems := ProcessSchDocObjects(SchDoc, ObjTypeInt,
        FilterStr, PropsStr, SetStr, Mode, DocPath, TotalMatched, Limit);

    IsMutating := (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create');
    { The document is marked modified here, never saved: "saved" said true }
    { on every write, even one that matched nothing, while the file on     }
    { disk did not change (measured on AD 26.11). It now says what         }
    { happened: modified when something was written, saved false.          }
    Saved := False;
    If IsMutating Then
    Begin
        Try SchDoc.GraphicallyInvalidate; Except End;
        MarkDocDirtyByPath(DocPath);
    End;

    If Mode = 'query' Then
        { The diagnostic rides on a QUERY as well. A property that could
          not be read comes back as an empty string, and without this the
          caller cannot tell that from a value that is genuinely empty. }
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched)
            + ',"properties":' + RenderPropertyDiagJson(0) + '}')
    Else
    Begin
        If Saved Then SavedStr := 'true' Else SavedStr := 'false';
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched) + ',"saved":' + SavedStr
            + ',"modified":' + BoolToJsonStr(SchCallModified(IsMutating, TotalMatched, Mode))
            + ModifyOutcomeJson(0) + '}');
    End;
End;

{..............................................................................}
{ Helper: Process a SPECIFIC document by file path (no focus change)          }
{..............................................................................}

Function ProcessDocByPath(DocPath : String; ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    SchDoc : ISch_Document;
    ServerDoc : IServerDocument;
    TotalMatched : Integer;
    JsonItems, SavedStr, BadCond : String;
    IsMutating, Saved : Boolean;
Begin
    BadCond := FilterProblem(FilterStr);
    If BadCond <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_FILTER', BadFilterMessage(BadCond));
        Exit;
    End;

    // Do NOT RunProcess Client:OpenDocument, that loads the file but
    // strips any project association, producing a "free document" in the
    // UI with the full path as its tab title. SchDocByPathLoading opens it
    // through the Client API, which keeps the membership.
    SchDoc := SchDocByPathLoading(DocPath);
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'Document not loaded: ' + DocPath +
            '. Open it in Altium first, then retry.');
        Exit;
    End;

    TotalMatched := 0;
    JsonItems := ProcessSchDocObjects(SchDoc, ObjTypeInt,
        FilterStr, PropsStr, SetStr, Mode, DocPath, TotalMatched, Limit);

    IsMutating := (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create');
    { The document is marked modified here, never saved: "saved" said true }
    { on every write, even one that matched nothing, while the file on     }
    { disk did not change (measured on AD 26.11). It now says what         }
    { happened: modified when something was written, saved false.          }
    Saved := False;
    If IsMutating Then
    Begin
        Try SchDoc.GraphicallyInvalidate; Except End;
        MarkDocDirtyByPath(DocPath);
    End;

    If Mode = 'query' Then
        { The diagnostic rides on a QUERY as well. A property that could
          not be read comes back as an empty string, and without this the
          caller cannot tell that from a value that is genuinely empty. }
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched)
            + ',"properties":' + RenderPropertyDiagJson(0) + '}')
    Else
    Begin
        If Saved Then SavedStr := 'true' Else SavedStr := 'false';
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched) + ',"saved":' + SavedStr
            + ',"modified":' + BoolToJsonStr(SchCallModified(IsMutating, TotalMatched, Mode))
            + ModifyOutcomeJson(0) + '}');
    End;
End;

{..............................................................................}
{ Helper: Parse scope value into type + optional file path.                    }
{                                                                              }
{ Wire form (structured, sent by the Python helper) is a JSON object with     }
{ a "type" field (active_doc / project / doc) and an optional "file_path"     }
{ field. Top-level scope values arriving from MCP tools always use this form. }
{                                                                              }
{ For batch-operation strings (compact key=value;...~~ encoding) the scope    }
{ is still a plain string token: active_doc / project / doc:path /            }
{ project:path. ParseScope handles both forms, JSON-object first, then the   }
{ legacy compact form for batch-op fields.                                    }
{..............................................................................}

Procedure ParseScope(Scope : String; Var ScopeType : String; Var ScopePath : String);
Var
    InnerType, InnerPath : String;
Begin
    ScopeType := 'active_doc';
    ScopePath := '';

    If Scope = '' Then Exit;

    // Structured form: {"type":"...","file_path":"..."}
    If Copy(Scope, 1, 1) = '{' Then
    Begin
        InnerType := ExtractJsonValue(Scope, 'type');
        InnerPath := ExtractJsonValue(Scope, 'file_path');
        If InnerType <> '' Then
            ScopeType := InnerType;
        If InnerPath <> '' Then
            ScopePath := InnerPath;
        Exit;
    End;

    // Legacy compact form used inside batch-op strings only.
    If Copy(Scope, 1, 4) = 'doc:' Then
    Begin
        ScopeType := 'doc';
        ScopePath := Copy(Scope, 5, Length(Scope));
    End
    Else If Copy(Scope, 1, 8) = 'project:' Then
    Begin
        ScopeType := 'project';
        ScopePath := Copy(Scope, 9, Length(Scope));
    End
    Else If Copy(Scope, 1, 14) = 'lib_component:' Then
    Begin
        { Target a named symbol inside the active SchLib. ScopePath carries }
        { the lib-ref name (not a file path), optionally suffixed '@N' to   }
        { select part N of a multi-part symbol. The suffix is left on the   }
        { string here and split by ApplyLibComponentScope, so ParseScope's  }
        { signature stays as every other caller expects it.                 }
        ScopeType := 'lib_component';
        ScopePath := Copy(Scope, 15, Length(Scope));
    End
    Else
        ScopeType := Scope;
End;

{..............................................................................}
{ A scope ParseScope does not know is refused, never read as the active      }
{ document: a sheet's path given bare used to be answered from whichever     }
{ sheet was in front, and obj_modify and obj_delete take the same scope.     }
{ Returns '' when the scope is one of the accepted kinds.                    }
{..............................................................................}

Function UnknownScopeError(RequestId, Scope, ScopeType : String) : String;
Begin
    Result := '';
    If (ScopeType = 'active_doc') Or (ScopeType = 'project')
        Or (ScopeType = 'doc') Or (ScopeType = 'lib_component') Then Exit;
    Result := BuildErrorResponse(RequestId, 'INVALID_SCOPE',
        'Not a scope: "' + Scope + '". Use active_doc, project, '
        + 'doc:PATH, project:PATH or lib_component:NAME. Nothing was read or '
        + 'changed.');
End;

{..............................................................................}
{ If ScopeType is 'lib_component', switch the active SchLib to the named      }
{ symbol (ScopePath holds the lib-ref) and rewrite ScopeType to 'active_doc'  }
{ so the normal active-doc path then iterates that symbol's primitives. This  }
{ folds what used to be a separate set_current_component call into the same   }
{ request. Returns False if no such component exists in the active library.  }
{..............................................................................}
Function ApplyLibComponentScope(Var ScopeType : String; ScopePath : String) : Boolean;
Var
    AtPos, PartId, I : Integer;
    CompName, PartStr : String;
Begin
    Result := True;
    { Cleared first: a batch applies one op's scope after another's. }
    LibScopeName := '';
    LibScopePart := 0;
    If ScopeType <> 'lib_component' Then Exit;

    { Optional '@N' suffix selects part N of a multi-part symbol. A SchLib  }
    { iterator only yields the CURRENT part's primitives, so without this   }
    { every query/modify/delete on a multi-part component could only ever   }
    { reach part 1 and correcting parts 2..N meant a full rebuild.          }
    { Scan from the RIGHT: a lib-ref may legitimately contain '@'.          }
    CompName := ScopePath;
    { 0 = NO SUFFIX, which keeps the plain lookup exactly as it was. A
      written @1 used to arrive here as the same 1, so the two could not be
      told apart and @1 was never honoured. A parsed suffix below still
      clamps to 1, so @0 and @-1 are read as an explicit part 1. }
    PartId := 0;
    AtPos := 0;
    For I := Length(ScopePath) DownTo 1 Do
        If ScopePath[I] = '@' Then
        Begin
            AtPos := I;
            Break;
        End;
    If AtPos > 1 Then
    Begin
        PartStr := Copy(ScopePath, AtPos + 1, Length(ScopePath));
        If (PartStr <> '') And IsIntStr(PartStr) Then
        Begin
            PartId := StrToIntDef(PartStr, 1);
            CompName := Copy(ScopePath, 1, AtPos - 1);
            If PartId < 1 Then PartId := 1;
        End;
    End;

    If SelectLibComponentPart(CompName, PartId) = Nil Then
        Result := False
    Else
    Begin
        ScopeType := 'active_doc';
        LibScopeName := CompName;
        LibScopePart := PartId;
    End;
End;

{..............................................................................}
{ PRIMITIVE 1: query_objects                                                  }
{ Params: scope, object_type, filter, properties                             }
{..............................................................................}

Function Gen_QueryObjects(Params : String; RequestId : String) : String;
Var
    Scope, ObjTypeStr, FilterStr, PropsStr, ScopeType, ScopePath : String;
    BadProps : String;
    ObjTypeInt, Limit : Integer;
Begin
    Scope := ExtractJsonValue(Params, 'scope');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    PropsStr := ExtractJsonValue(Params, 'properties');
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 0);

    If PropsStr = '' Then PropsStr := 'Location.X,Location.Y';
    { Start clean, so the reply describes THIS query. }
    ResetPropertyDiag(0);

    ParseScope(Scope, ScopeType, ScopePath);
    Result := UnknownScopeError(RequestId, Scope, ScopeType);
    If Result <> '' Then Exit;

    { A PCB OBJECT TYPE CANNOT HONOUR A SCOPE, so say so before doing         }
    { anything. PCB primitives live on a board and the PCB path below resolves }
    { one by itself; ScopeType never reaches it.                              }
    {                                                                          }
    { MEASURED: obj_query(eArcObject, scope="lib_component:SWEEP_SYM_A")       }
    { returned 19 arcs belonging to an unrelated client BOARD. The scope was   }
    { silently discarded and the answer looked entirely ordinary, which is the }
    { same wrong-document failure the board readers have.                      }
    {                                                                          }
    { Checked BEFORE ApplyLibComponentScope on purpose: that call MOVES the    }
    { active SchLib's current component as a side effect, and doing so for a   }
    { query that is about to be refused would leave the editor somewhere the   }
    { caller never asked for.                                                  }
    If (ObjectTypeFromStringPCB(ObjTypeStr) <> -1)
        And (ScopeType <> 'active_doc') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SCOPE_NOT_SUPPORTED',
            'A PCB object type cannot be scoped with "' + Scope + '". PCB '
            + 'primitives live on a board, and this query always reads the '
            + 'active one, so a document, project or lib_component scope '
            + 'would be silently ignored. Activate the board you mean and '
            + 'query it with the default scope.');
        Exit;
    End;

    If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Library component not found in active library: ' + ScopePath);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        If ScopeType = 'project' Then
            Result := IterateProjectDocs(ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, ScopePath, Limit)
        Else If ScopeType = 'doc' Then
            Result := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, Limit)
        Else
            Result := ProcessActiveDoc(ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, Limit);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        { REFUSE a property name the PCB getter has no branch for. It
          used to return '' for those, which is the same value a real
          but empty property gives, so a misspelling read as "the data
          is not there". Measured three times, each ending in a report
          that the bridge could not do something it could: the worst was
          'Net.Name', where every track came back with no net and the
          conclusion was that copper carries no net attribution at all. }
        BadProps := UnknownPCBProperties(PropsStr);
        If BadProps <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'UNKNOWN_PROPERTY',
                'Not a PCB property: ' + BadProps + '. These primitives do '
                + 'not use the dotted schematic spelling, so Net.Name is '
                + 'Net here. Available: ' + KnownPCBPropertyList(0) + '.');
            Exit;
        End;
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, PropsStr, '', 'query', RequestId, Limit);
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', UnknownObjectTypeMessage(ObjTypeStr));
End;

{..............................................................................}
{ PRIMITIVE 2: modify_objects                                                 }
{ Params: scope, object_type, filter, set                                    }
{..............................................................................}

Function Gen_ModifyObjects(Params : String; RequestId : String) : String;
Var
    Scope, ObjTypeStr, FilterStr, SetStr, ScopeType, ScopePath : String;
    ObjTypeInt : Integer;
Begin
    Scope := ExtractJsonValue(Params, 'scope');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    SetStr := ExtractJsonValue(Params, 'set');

    If SetStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'set parameter is required');
        Exit;
    End;

    { Start clean, so the reply describes THIS call. The buffer is module   }
    { level and the bridge handles one request at a time, but a handler     }
    { that left entries behind would otherwise fail the next caller for a   }
    { property it never sent.                                               }
    ResetPropertyDiag(0);

    ParseScope(Scope, ScopeType, ScopePath);
    Result := UnknownScopeError(RequestId, Scope, ScopeType);
    If Result <> '' Then Exit;
    If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Library component not found in active library: ' + ScopePath);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        If ScopeType = 'project' Then
            Result := IterateProjectDocs(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, ScopePath, 0)
        Else If ScopeType = 'doc' Then
            Result := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0)
        Else
            Result := ProcessActiveDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', UnknownObjectTypeMessage(ObjTypeStr));
End;

{..............................................................................}
{ PRIMITIVE 3: create_object                                                  }
{ Params: object_type, properties, container                                  }
{..............................................................................}

Function Gen_CreateObject(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, PropsStr, Container, UnsupportedProp : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    NewObj : ISch_GraphicalObject;
Begin
    ResetPropertyDiag(0);
    SchDoc := Nil;
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    PropsStr := ExtractJsonValue(Params, 'properties');
    Container := ExtractJsonValue(Params, 'container');
    If Container = '' Then Container := 'document';

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt = -1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', SchOnlyTypeMessage(ObjTypeStr, 'obj_create'));
        Exit;
    End;

    // Create the object
    NewObj := SchServer.SchObjectFactory(ObjTypeInt, eCreate_Default);
    If NewObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create object of type: ' + ObjTypeStr);
        Exit;
    End;
    ApplyNoErcDefaults(NewObj);

    UnsupportedProp := UnsupportedSchProperty(NewObj, PropsStr);
    If UnsupportedProp <> '' Then
    Begin
        SchServer.DestroySchObject(NewObj);
        Result := BuildErrorResponse(RequestId, 'UNSUPPORTED_PROPERTY',
            'Property ' + UnsupportedProp + ' is not supported on ' + ObjTypeStr);
        Exit;
    End;

    // Set properties
    ApplySetProperties(NewObj, PropsStr);

    // Register in container
    If Container = 'component' Then
    Begin
        // Library component container
        SchLib := SchServer.GetCurrentSchDocument;
        If (SchLib = Nil) Or (SchLib.ObjectId <> eSchLib) Then
        Begin
            SchServer.DestroySchObject(NewObj);
            Result := BuildErrorResponse(RequestId, 'NO_SCHLIB', 'No schematic library is active');
            Exit;
        End;
        Component := SchLib.CurrentSchComponent;
        If Component = Nil Then
        Begin
            SchServer.DestroySchObject(NewObj);
            Result := BuildErrorResponse(RequestId, 'NO_COMPONENT', 'No library component is selected');
            Exit;
        End;
        SchBeginEdit(SchLib, '');
        Component.AddSchObject(NewObj);
        SchRegisterObject(Component, NewObj);
        SchEndEdit(SchLib, '');
    End
    Else
    Begin
        // Document container
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            SchServer.DestroySchObject(NewObj);
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
            Exit;
        End;
        SchBeginEdit(SchDoc, '');
        SchDoc.RegisterSchObjectInContainer(NewObj);
        SchRegisterObject(SchDoc, NewObj);
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId, '{"created":true,"object_type":"' + ObjTypeStr + '"}');
End;

{..............................................................................}
{ PRIMITIVE 4: delete_objects                                                 }
{ Params: scope, object_type, filter                                         }
{..............................................................................}

Function Gen_DeleteObjects(Params : String; RequestId : String) : String;
Var
    Scope, ObjTypeStr, FilterStr, ScopeType, ScopePath : String;
    ObjTypeInt : Integer;
Begin
    Scope := ExtractJsonValue(Params, 'scope');
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    ResetPropertyDiag(0);

    ParseScope(Scope, ScopeType, ScopePath);
    Result := UnknownScopeError(RequestId, Scope, ScopeType);
    If Result <> '' Then Exit;
    If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Library component not found in active library: ' + ScopePath);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        If ScopeType = 'project' Then
            Result := IterateProjectDocs(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, ScopePath, 0)
        Else If ScopeType = 'doc' Then
            Result := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0)
        Else
            Result := ProcessActiveDoc(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0);
        Exit;
    End;

    ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
    If ObjTypeInt <> -1 Then
    Begin
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0);
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', UnknownObjectTypeMessage(ObjTypeStr));
End;

{..............................................................................}
{ PRIMITIVE 5: run_process (enhanced)                                         }
{ Params: process, params (pipe-separated key=value)                         }
{..............................................................................}

Function Gen_RunProcess(Params : String; RequestId : String) : String;
Var
    ProcessName, ProcessParams : String;
    Remaining, KVPair, Key, Value : String;
    PipePos, EqPos : Integer;
Begin
    ProcessName := ExtractJsonValue(Params, 'process');
    ProcessParams := ExtractJsonValue(Params, 'params');

    If ProcessName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'process parameter is required');
        Exit;
    End;

    ResetParameters;

    // Parse pipe-separated key=value pairs
    If ProcessParams <> '' Then
    Begin
        Remaining := ProcessParams;
        While Remaining <> '' Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos > 0 Then
            Begin
                KVPair := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End
            Else
            Begin
                KVPair := Remaining;
                Remaining := '';
            End;

            EqPos := Pos('=', KVPair);
            If EqPos > 1 Then
            Begin
                Key := Copy(KVPair, 1, EqPos - 1);
                If Key <> '' Then
                Begin
                    Value := Copy(KVPair, EqPos + 1, Length(KVPair));
                    AddStringParameter(Key, Value);
                End;
            End;
        End;
    End;

    { DISPATCHED, NOT EXECUTED. Altium's RunProcess returns nothing and       }
    { raises nothing for a process that does not exist, so this handler       }
    { cannot tell a command that ran from a name that was silently ignored.   }
    { MEASURED: obj_run_process("Sch:ThisProcessDoesNotExist") returned        }
    { success true. Reporting that as success is the same defect as #83 in    }
    { app_run_menu, which was fixed while this sibling was left alone.        }
    {                                                                          }
    { The key is named for what is actually known. Anything that needs to      }
    { know the command took effect has to read the design back.               }
    RunProcess(ProcessName);
    Result := BuildSuccessResponse(RequestId,
        '{"dispatched":true,"process":"' + EscapeJsonString(ProcessName) + '"'
        + ',"note":"Altium accepts an unknown process name without error, so '
        + 'this reports that the command was SENT, not that it ran. Verify by '
        + 'reading the design."}');
End;

{..............................................................................}
{ PRIMITIVE 6: get_font_spec                                                 }
{ Params: font_id                                                            }
{..............................................................................}

Function Gen_GetFontSpec(Params : String; RequestId : String) : String;
Var
    FontMgr : ISch_FontManager;
    FontId, Size, Rotation : Integer;
    Underline, Italic, Bold, StrikeOut : Boolean;
    FontName : String;
Begin
    FontId := StrToIntDef(ExtractJsonValue(Params, 'font_id'), 1);
    FontMgr := SchServer.FontManager;
    FontMgr.GetFontSpec(FontId, Size, Rotation, Underline, Italic, Bold, StrikeOut, FontName);
    Result := BuildSuccessResponse(RequestId,
        '{"font_id":' + IntToStr(FontId) +
        ',"size":' + IntToStr(Size) +
        ',"rotation":' + IntToStr(Rotation) +
        ',"bold":' + BoolToJsonStr(Bold) +
        ',"italic":' + BoolToJsonStr(Italic) +
        ',"underline":' + BoolToJsonStr(Underline) +
        ',"strikeout":' + BoolToJsonStr(StrikeOut) +
        ',"font_name":"' + EscapeJsonString(FontName) + '"}');
End;

{..............................................................................}
{ PRIMITIVE 7: get_font_id                                                   }
{ Params: size, font_name, bold, italic, rotation, underline, strikeout      }
{..............................................................................}

Function Gen_GetFontId(Params : String; RequestId : String) : String;
Var
    FontMgr : ISch_FontManager;
    FontId, Size, Rotation : Integer;
    Underline, Italic, Bold, StrikeOut : Boolean;
    FontName : String;
Begin
    Size := StrToIntDef(ExtractJsonValue(Params, 'size'), 10);
    FontName := ExtractJsonValue(Params, 'font_name');
    If FontName = '' Then FontName := 'Arial';
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);
    Bold := ExtractJsonValue(Params, 'bold') = 'true';
    Italic := ExtractJsonValue(Params, 'italic') = 'true';
    Underline := ExtractJsonValue(Params, 'underline') = 'true';
    StrikeOut := ExtractJsonValue(Params, 'strikeout') = 'true';

    FontMgr := SchServer.FontManager;
    FontId := FontMgr.GetFontID(Size, Rotation, Underline, Italic, Bold, StrikeOut, FontName);
    Result := BuildSuccessResponse(RequestId, '{"font_id":' + IntToStr(FontId) + '}');
End;

{..............................................................................}
{ Select objects matching filter, sets Selection/Selected on matching objs  }
{..............................................................................}

{ A SELECT REPORTS WHAT IT MATCHED AND WHAT IS NOW SELECTED, nothing else.  }
{ It used to hand back the modify reply, whose tail is the property notes.   }
{ The notes were never reset here, so a select carried whatever the request  }
{ before it left behind and answered success false for properties it never  }
{ touched: after a query of X on 3D bodies, a select on them said X and Y    }
{ were unreadable. The selection is read back by a second pass over the same }
{ filter with the selection flag added.                                      }
Function Gen_SelectObjects(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, FilterStr, SelProp, ModReply, ReadReply, ReadFilter : String;
    ObjTypeInt, Matched, SelCount : Integer;
    IsBoard : Boolean;
Begin
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    ResetPropertyDiag(0);

    IsBoard := False;
    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt = -1 Then
    Begin
        ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
        IsBoard := ObjTypeInt <> -1;
    End;
    If ObjTypeInt = -1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', UnknownObjectTypeMessage(ObjTypeStr));
        Exit;
    End;

    If IsBoard Then SelProp := 'Selected' Else SelProp := 'Selection';

    If IsBoard Then
        ModReply := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', SelProp + '=true', 'modify', RequestId, 0)
    Else
        ModReply := ProcessActiveDoc(ObjTypeInt, FilterStr, '', SelProp + '=true', 'modify', RequestId, 0);

    { A refusal (bad filter, no document, ambiguous board) goes back as is. }
    If ExtractJsonValue(ModReply, 'success') <> 'true' Then
    Begin
        Result := ModReply;
        Exit;
    End;
    Matched := StrToIntDef(JsonValueAnywhere(ModReply, 'matched'), 0);

    If FilterStr = '' Then ReadFilter := SelProp + '=true'
    Else ReadFilter := FilterStr + '|' + SelProp + '=true';
    ResetPropertyDiag(0);
    If IsBoard Then
        ReadReply := ProcessActivePCBDoc(ObjTypeInt, ReadFilter, '', '', 'query', RequestId, 0)
    Else
        ReadReply := ProcessActiveDoc(ObjTypeInt, ReadFilter, '', '', 'query', RequestId, 0);
    ResetPropertyDiag(0);

    SelCount := -1;
    If ExtractJsonValue(ReadReply, 'success') = 'true' Then
        SelCount := StrToIntDef(JsonValueAnywhere(ReadReply, 'count'), -1);

    If SelCount < 0 Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(Matched) + ',"selected":null,'
            + '"success":false,"reason":"the selection could not be read '
            + 'back, so whether it took is unknown"}');
        Exit;
    End;
    If SelCount < Matched Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(Matched) + ',"selected":' + IntToStr(SelCount)
            + ',"success":false,"reason":"' + IntToStr(Matched - SelCount)
            + ' matched object(s) did not read back as selected"}');
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"matched":' + IntToStr(Matched) + ',"selected":' + IntToStr(SelCount)
        + ',"success":true}');
End;

{..............................................................................}
{ Deselect all objects on the active document                                }
{..............................................................................}

{ ISch_Document has NO ClearSelection method (raises "Undeclared identifier:
  ClearSelection"), and iterating objects to set .Selection := False faults on
  sub-objects (parameters, pin labels) that don't expose Selection, in a modal
  Try/Except cannot catch. So a process does it. MEASURED on AD 26.11.1.5:
  Sch:DeSelectAll left all 123 selected parts selected; Sch:DeSelect with
  Action=All cleared them (the same on AD 25.8). }
Procedure SchDeselectAllObjects(SchDoc : ISch_Document);
Begin
    If SchDoc = Nil Then Exit;
    Try
        ResetParameters;
        AddStringParameter('Action', 'All');
        RunProcess('Sch:DeSelect');
    Except End;
End;

Function Gen_DeselectAll(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc <> Nil Then
    Begin
        SchDeselectAllObjects(SchDoc);
        SchDoc.GraphicallyInvalidate;
        Result := BuildSuccessResponse(RequestId, '{"deselected":true}');
        Exit;
    End;

    Board := GetPCBBoardAnywhere(0);
    If Board <> Nil Then
    Begin
        ResetParameters;
        AddStringParameter('Scope', 'All');
        RunProcess('PCB:DeSelect');
        Result := BuildSuccessResponse(RequestId, '{"deselected":true}');
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active document');
End;

{..............................................................................}
{ Zoom viewport: fit, selection, or region                                   }
{..............................................................................}

{ The document kinds the schematic editor draws, so Sch:Zoom serves them:  }
{ sheets, symbol libraries and harness wiring diagrams. A wiring diagram   }
{ left to the board fallback zoomed whichever board was open instead.      }
Function IsSchEditorKind(Kind : String) : Boolean;
Begin
    Kind := UpperCase(Kind);
    Result := (Kind = 'SCH') Or (Kind = 'SCHLIB') Or (Kind = 'HARNESSWIRINGDIAGRAM');
End;

Function Gen_Zoom(Params : String; RequestId : String) : String;
Var
    Action, FocusedKind, ZoomProcess : String;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    Workspace : IWorkspace;
    Doc : IDocument;
Begin
    Action := ExtractJsonValue(Params, 'action');
    If Action = '' Then Action := 'fit';

    { The focused tab decides which editor zooms. Probing SchServer and the  }
    { board first missed a focused PcbLib ("no schematic or PCB document")   }
    { and sent Sch:Zoom whenever any schematic was current, even with a     }
    { footprint in front. Sch:Zoom serves SchDoc and SchLib, PCB:Zoom serves  }
    { PcbDoc and PcbLib.                                                      }
    FocusedKind := '';
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Nil;
        Try Doc := Workspace.DM_FocusedDocument; Except End;
        If Doc <> Nil Then
            Try FocusedKind := UpperCase(Doc.DM_DocumentKind); Except End;
    End;

    ZoomProcess := '';
    If IsSchEditorKind(FocusedKind) Then ZoomProcess := 'Sch:Zoom'
    Else If (FocusedKind = 'PCB') Or (FocusedKind = 'PCBLIB') Then ZoomProcess := 'PCB:Zoom';

    If ZoomProcess <> '' Then
    Begin
        If Pos('|' + Action + '|', '|fit|selection|board|document|') = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_PARAMS', 'action is fit, board, '
                + 'document or selection, not "' + Action + '"');
            Exit;
        End;
        If (ZoomProcess <> 'Sch:Zoom') And (Action = 'document') Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
                'document zooms to a whole schematic sheet; on a board use board or fit');
            Exit;
        End;
        ResetParameters;
        { "board" is View > Fit Board (AdvPcb.rcs PLPCB:ViewFitBoard): the  }
        { board alone, not the parts parked beside it. A schematic has no   }
        { board, so it fits the sheet's objects as "fit" does.              }
        If (Action = 'board') And (FocusedKind = 'PCB') Then
        Begin
            AddStringParameter('Action', 'Document');
            AddStringParameter('Target', 'Board');
        End
        { document = View > Fit Document, the sheet with its frame; fit is }
        { Fit All Objects, which may leave the frame out.                  }
        Else If Action = 'document' Then AddStringParameter('Action', 'Document')
        Else If (Action = 'fit') Or (Action = 'board') Then AddStringParameter('Action', 'All')
        Else AddStringParameter('Action', 'Selected');
        RunProcess(ZoomProcess);
        Result := BuildSuccessResponse(RequestId, '{"action":"' + Action + '",'
            + '"document_kind":"' + FocusedKind + '","dispatched":true}');
        Exit;
    End;

    { No focused design document (a project or text tab): the old probe. }
    SchDoc := SchServer.GetCurrentSchDocument;
    Board := GetPCBBoardAnywhere(0);

    { ACTION VALUES ARE NOT FREE TEXT. Altium accepts an unknown process     }
    { parameter without complaint and falls back to its own dialog, which is }
    { how "zoom" came to open a Zoom dialog instead of zooming (reported     }
    { 2026-09-18). The values below are the ones actually attested: 'All' is }
    { documented for both PCB:Zoom and Sch:Zoom (system-api.html, and five   }
    { examples in the PCB reference), and 'Redraw' for PCB:Zoom. The old     }
    { 'ZoomToFit' / 'ZoomToSelection' appear in no Altium documentation or   }
    { third-party script; the only hits were this repository's own copy      }
    { vendored into reference/, which is not corroboration.                  }
    If (SchDoc = Nil) And (Board = Nil) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT',
            'No schematic or PCB document is open to zoom.');
        Exit;
    End;

    If Action = 'fit' Then
    Begin
        ResetParameters;
        AddStringParameter('Action', 'All');
        If SchDoc <> Nil Then RunProcess('Sch:Zoom')
        Else RunProcess('PCB:Zoom');
    End
    Else If Action = 'selection' Then
    Begin
        { 'Selected' is attested in third-party PCB scripts but not in the   }
        { Altium documentation, and never for Sch:Zoom. Reported rather than }
        { claimed: the caller is told the zoom was dispatched, not that the  }
        { viewport moved.                                                     }
        ResetParameters;
        AddStringParameter('Action', 'Selected');
        If SchDoc <> Nil Then RunProcess('Sch:Zoom')
        Else RunProcess('PCB:Zoom');
    End
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
            'action must be "fit" or "selection", got "' + Action + '"');
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId, '{"action":"' + Action + '",'
        + '"dispatched":true}');
End;

{..............................................................................}
{ BATCH MODIFY: Multiple modify operations in a single IPC call.             }
{                                                                            }
{ Params: operations. Preferred wire format is the '~~' batch format shared  }
{   with batch_delete / place_wires:                                         }
{     scope=active_doc;object_type=eNetLabel;filter=Text=VBUS;set=Location.Y=6400~~... }
{                                                                            }
{ LEGACY format (still accepted when the payload contains no '~~'):          }
{   pipe-separated operations, each semicolon-separated as                   }
{   scope;object_type;filter;set                                             }
{                                                                            }
{ The legacy format was BROKEN for any op whose filter or set contained a    }
{ '|', which is the documented separator INSIDE both of those fields.        }
{ Measured on a live document: an op list of 4 eSchComponent moves followed  }
{ by 8 eNetLabel moves reported operations_processed=4 and silently dropped  }
{ the other 8, and a lone eNetLabel op with a two-condition filter reported  }
{ operations_processed=0 while the identical filter matched through          }
{ obj_query and obj_batch_delete. Cause: the top-level split on '|' tore     }
{ each such op into fragments, and every fragment after the first had fewer  }
{ than three ';' so it hit `Continue` and vanished without a trace. It was   }
{ never an object-type allowlist -- eNetLabel was always accepted.           }
{                                                                            }
{ '~~' cannot appear in an Altium name, filter or property string (see the   }
{ batch helper notes in Main.pas), so the new format is unambiguous.         }
{                                                                            }
{ Every op is now evaluated independently and reports its own matched count  }
{ in "results", so an op that matches nothing is REPORTED rather than being  }
{ silently counted as fine.                                                  }
{..............................................................................}

{ Split one legacy 'scope;object_type;filter;set' operation. Returns False  }
{ when the fragment does not carry all four fields, which is how a torn      }
{ legacy payload is detected.                                                }
Function SplitLegacyModifyOp(OpStr : String; Var Scope : String;
    Var ObjTypeStr : String; Var FilterStr : String; Var SetStr : String) : Boolean;
Var
    Rest : String;
    SemiPos : Integer;
Begin
    Result := False;
    Rest := OpStr;

    SemiPos := Pos(';', Rest);
    If SemiPos = 0 Then Exit;
    Scope := Copy(Rest, 1, SemiPos - 1);
    Rest := Copy(Rest, SemiPos + 1, Length(Rest));

    SemiPos := Pos(';', Rest);
    If SemiPos = 0 Then Exit;
    ObjTypeStr := Copy(Rest, 1, SemiPos - 1);
    Rest := Copy(Rest, SemiPos + 1, Length(Rest));

    SemiPos := Pos(';', Rest);
    If SemiPos = 0 Then Exit;
    FilterStr := Copy(Rest, 1, SemiPos - 1);
    SetStr := Copy(Rest, SemiPos + 1, Length(Rest));
    Result := True;
End;

Function Gen_BatchModify(Params : String; RequestId : String) : String;
Var
    Operations, OpStr, Remaining, OpResult, Note : String;
    Scope, ObjTypeStr, FilterStr, SetStr : String;
    ScopeType, ScopePath : String;
    ObjTypeInt, PipePos : Integer;
    TotalMatched, OpCount, OpSkipped, OpMatched : Integer;
    ResultJson, ResultsJson : String;
    UseTilde, IsPcb : Boolean;
Begin
    Operations := ExtractJsonValue(Params, 'operations');
    If Operations = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'operations parameter is required');
        Exit;
    End;

    TotalMatched := 0;
    OpCount := 0;
    OpSkipped := 0;
    ResultJson := '';
    ResultsJson := '';
    Remaining := Operations;
    { A keyed op starts 'scope=', and that is the framing to use even for  }
    { a SINGLE op, which has no '~~' in it. Choosing by '~~' alone sent a  }
    { one-op batch down the legacy '|' split, which tore its set string    }
    { apart and read object_type as 'object_type=eParameter'.              }
    UseTilde := (Pos('~~', Operations) > 0) Or (Copy(Operations, 1, 6) = 'scope=');

    { Clear the property-write diagnostics buffer so this call only       }
    { surfaces issues raised by THIS batch, not anything left over.       }
    ResetPropertyDiag(0);

    While Length(Remaining) > 0 Do
    Begin
        Scope := '';
        ObjTypeStr := '';
        FilterStr := '';
        SetStr := '';
        Note := '';

        If UseTilde Then
        Begin
            OpStr := NextBatchOp(Remaining);
            If OpStr = '' Then Break;
            Scope := GetBatchField(OpStr, 'scope');
            ObjTypeStr := GetBatchField(OpStr, 'object_type');
            FilterStr := GetBatchField(OpStr, 'filter');
            SetStr := GetBatchTail(OpStr, 'set');
        End
        Else
        Begin
            { Legacy '|' framing. Kept so an older Python client keeps      }
            { working, but a fragment that lost fields to a '|' inside a    }
            { filter or set is now COUNTED and reported instead of being    }
            { dropped in silence.                                           }
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then
            Begin
                OpStr := Remaining;
                Remaining := '';
            End
            Else
            Begin
                OpStr := Copy(Remaining, 1, PipePos - 1);
                Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
            End;
            If OpStr = '' Then Continue;
            If Not SplitLegacyModifyOp(OpStr, Scope, ObjTypeStr, FilterStr, SetStr) Then
                Note := 'malformed_operation';
        End;

        If Scope = '' Then Scope := 'active_doc';

        { Every op is evaluated on its own merits from here down. A reason  }
        { to skip is RECORDED, never silently swallowed: silent-drop is     }
        { what made the original bug invisible.                             }
        OpMatched := 0;
        ObjTypeInt := -1;
        ScopeType := '';
        ScopePath := '';

        If Note = '' Then
        Begin
            If ObjTypeStr = '' Then Note := 'missing_object_type'
            Else If SetStr = '' Then Note := 'missing_set';
        End;

        IsPcb := False;
        If Note = '' Then
        Begin
            ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
            { A board type has a table of its own. obj_modify has always   }
            { looked there as well; this batch form answered              }
            { unknown_object_type for every board type.                   }
            If ObjTypeInt = -1 Then
            Begin
                ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
                IsPcb := (ObjTypeInt <> -1);
            End;
            If ObjTypeInt = -1 Then Note := 'unknown_object_type';
        End;

        If Note = '' Then
        Begin
            ParseScope(Scope, ScopeType, ScopePath);
            If UnknownScopeError(RequestId, Scope, ScopeType) <> '' Then
                Note := 'unknown_scope'
            { A board type edits the active board and nothing else, and the }
            { lib_component step would move a SchLib's current symbol for   }
            { an operation that never reads it.                             }
            Else If IsPcb Then
            Begin
                If ScopeType <> 'active_doc' Then Note := 'scope_not_supported';
            End
            Else
            Begin
                { lib_component scope: select the symbol; report if it's gone. }
                If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
                    Note := 'lib_component_not_found';
            End;
        End;

        If Note = '' Then
        Begin
            OpResult := '';
            If IsPcb Then
                OpResult := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0)
            Else If ScopeType = 'project' Then
                OpResult := IterateProjectDocs(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, ScopePath, 0)
            Else If ScopeType = 'doc' Then
                OpResult := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0)
            Else
                OpResult := ProcessActiveDoc(ObjTypeInt, FilterStr, '', SetStr, 'modify', RequestId, 0);

            OpMatched := StrToIntDef(JsonValueAnywhere(OpResult, 'matched'), 0);
            TotalMatched := TotalMatched + OpMatched;
            If Pos('"success":false', OpResult) > 0 Then
            Begin
                Note := 'failed: ' + JsonValueAnywhere(OpResult, 'code');
            End
            Else
            Begin
                If OpMatched = 0 Then Note := 'no_objects_matched';
            End;
            Inc(OpCount);
        End
        Else
            Inc(OpSkipped);

        { Per-op row so a zero-match or skipped op is visible to the caller. }
        If ResultsJson <> '' Then ResultsJson := ResultsJson + ',';
        ResultsJson := ResultsJson +
            '{"object_type":"' + EscapeJsonString(ObjTypeStr) + '"' +
            ',"filter":"' + EscapeJsonString(FilterStr) + '"' +
            ',"matched":' + IntToStr(OpMatched) +
            ',"note":"' + EscapeJsonString(Note) + '"}';
    End;

    { Surface unknown / failed property writes so they stop being silent. }
    ResultJson :=
        '{"operations_processed":' + IntToStr(OpCount) +
        ',"operations_skipped":' + IntToStr(OpSkipped) +
        ',"total_matched":' + IntToStr(TotalMatched) +
        ',"results":[' + ResultsJson + ']' +
        ',"properties":' + RenderPropertyDiagJson(0) + '}';
    Result := BuildSuccessResponse(RequestId, ResultJson);
End;

{..............................................................................}
{ Highlight a net by name in the active document (schematic or PCB)          }
{..............................................................................}

Function Gen_HighlightNet(Params : String; RequestId : String) : String;
Var
    NetName : String;
    ClearExisting : String;
    Context : String;
    FocusedKind : String;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    Net : IPCB_Net;
    Iterator : IPCB_BoardIterator;
    AllNet : IPCB_Net;
    SchIter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Matched : Integer;
    TargetUpper, ObjNet : String;
    Workspace : IWorkspace;
    Doc : IDocument;
    PreferSch : Boolean;
Begin
    NetName := ExtractJsonValue(Params, 'net_name');
    ClearExisting := ExtractJsonValue(Params, 'clear_existing');
    Context := ExtractJsonValue(Params, 'context');
    TargetUpper := UpperCase(NetName);

    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'net_name parameter is required');
        Exit;
    End;

    Board := GetPCBBoardAnywhere(0);
    SchDoc := SchServer.GetCurrentSchDocument;

    { GetPCBBoardAnywhere returns a board even when a schematic is the    }
    { focused document, so a schematic highlight would silently paint the }
    { PCB instead. Prefer the focused doc, or an explicit context param.  }
    FocusedKind := '';
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Nil;
        Try Doc := Workspace.DM_FocusedDocument; Except End;
        If Doc <> Nil Then
            Try FocusedKind := Doc.DM_DocumentKind; Except End;
    End;
    PreferSch := (UpperCase(Context) = 'SCHEMATIC') Or
        ((UpperCase(Context) <> 'PCB') And (FocusedKind = 'SCH'));
    If PreferSch And (SchDoc <> Nil) Then
        Board := Nil;

    { PCB path, use the documented IPCB_Net.IsHighlighted property set    }
    { directly on the net object. The earlier RunProcess('PCB:NetColor-    }
    { Highlight') was a guess; that process name isn't in the reference   }
    { and silently no-ops, which is why the tool appeared to do nothing.  }
    If Board <> Nil Then
    Begin
        If (ClearExisting = '') Or (ClearExisting = 'true') Then
        Begin
            Iterator := Board.BoardIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
                Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                Iterator.AddFilter_Method(eProcessAll);
                AllNet := Iterator.FirstPCBObject;
                While AllNet <> Nil Do
                Begin
                    Try AllNet.IsHighlighted := False; Except NoteWriteFailed('clear net highlight'); End;
                    AllNet := Iterator.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iterator);
            End;
        End;

        Net := FindNetByName(Board, NetName);
        If Net = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
                'Net not found on PCB: ' + NetName);
            Exit;
        End;

        PcbBeginEdit(0);
        Try Net.IsHighlighted := True; Except NoteWriteFailed('Net.IsHighlighted'); End;
        PcbEndEdit(0);
        Try Board.GraphicallyInvalidate; Except End;

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"net":"' + EscapeJsonString(NetName) + '",'
            + '"context":"pcb","highlighted":1}');
        Exit;
    End;

    { Schematic path, nets aren't first-class objects in Altium's Sch    }
    { API. The base ISch_GraphicalObject has no NetName property          }
    { (compile-time "Undeclared identifier: NetName"; Try/Except can't    }
    { rescue it). Instead, dispatch on ObjectId:                          }
    {   - eNetLabel / ePowerObject          , match against .Text        }
    {   - ePort                              , match against .Name        }
    {   - eSheetEntry                       , match against .Name         }
    {   - eWire                             , wires don't store a net    }
    {     name as a primitive property; the net is derived at compile     }
    {     time from the labels / ports attached to the wire segment.     }
    {     We skip them, selecting the net labels is enough to make the  }
    {     user eyeball-trace the wires.                                  }
    {                                                                    }
    { Do NOT wrap this in ProcessControl.PreProcess. The MCP poller     }
    { already owns the script engine; PreProcess + a debugger break     }
    { (or an undeclared-identifier modal on .Selection) deadlocks       }
    { Altium. Selection-only paint is not an undoable edit. Clear via   }
    { SchDeselectAllObjects, then Selection=True on matches only, never }
    { Selection=False inside the iterator (that path raises a modal     }
    { that bypasses Try/Except on sub-objects).                         }
    If SchDoc <> Nil Then
    Begin
        Matched := 0;
        If (ClearExisting = '') Or (ClearExisting = 'true') Then
            SchDeselectAllObjects(SchDoc);

        SchIter := SchDoc.SchIterator_Create;
        If SchIter <> Nil Then
        Begin
            Try
                SchIter.AddFilter_ObjectSet(MkSet(eNetLabel, ePowerObject,
                    ePort, eSheetEntry));
                Obj := SchIter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    ObjNet := '';
                    { A Port names itself with Name too, and reading
                      Text on one raises an undeclared identifier that no
                      Try/Except can contain. Issue #22. }
                    If Not SchObjectHasText(Obj) Then
                        Try ObjNet := Obj.Name; Except End
                    Else
                        Try ObjNet := Obj.Text; Except End;

                    If (ObjNet <> '') And (UpperCase(ObjNet) = TargetUpper) Then
                    Begin
                        Try Obj.Selection := True; Except NoteWriteFailed('select net object'); End;
                        Matched := Matched + 1;
                    End;
                    Obj := SchIter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(SchIter);
            End;
        End;
        Try SchDoc.GraphicallyInvalidate; Except End;

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"net":"' + EscapeJsonString(NetName) + '",'
            + '"context":"schematic","highlighted":' + IntToStr(Matched) + '}');
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Clear all highlights in the active document (schematic or PCB)             }
{..............................................................................}

Function Gen_ClearHighlights(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Net : IPCB_Net;
    SchIter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Cleared : Integer;
Begin
    Board := GetPCBBoardAnywhere(0);
    SchDoc := SchServer.GetCurrentSchDocument;
    Cleared := 0;

    If Board <> Nil Then
    Begin
        { Walk every net on the board and clear its IsHighlighted flag.   }
        { RunProcess('PCB:ClearAllHighlights') isn't documented and       }
        { appears to no-op, use the typed API path.                      }
        Iterator := Board.BoardIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
            Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Net := Iterator.FirstPCBObject;
            PcbBeginEdit(0);
            While Net <> Nil Do
            Begin
                Try
                    If Net.IsHighlighted Then
                    Begin
                        Net.IsHighlighted := False;
                        Cleared := Cleared + 1;
                    End;
                Except NoteWriteFailed('clear net highlight'); End;
                Net := Iterator.NextPCBObject;
            End;
            PcbEndEdit(0);
        Finally
            Board.BoardIterator_Destroy(Iterator);
        End;
        Try Board.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"context":"pcb","cleared":' + IntToStr(Cleared) + '}');
        Exit;
    End;

    If SchDoc <> Nil Then
    Begin
        { Deselect all connective primitives on the active sheet.          }
        SchBeginEdit(SchDoc, '');
        Try
            SchIter := SchDoc.SchIterator_Create;
            Try
                SchIter.AddFilter_ObjectSet(MkSet(eWire, eNetLabel, ePowerObject,
                    ePort, ePin, eSheetEntry));
                Obj := SchIter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Try
                        If Obj.Selection Then
                        Begin
                            Obj.Selection := False;
                            Cleared := Cleared + 1;
                        End;
                    Except NoteWriteFailed('deselect'); End;
                    Obj := SchIter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(SchIter);
            End;
        Finally
            SchEndEdit(SchDoc, 'Edit');
        End;
        Try SchDoc.GraphicallyInvalidate; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"context":"schematic","cleared":' + IntToStr(Cleared) + '}');
        Exit;
    End;

    Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Add a new schematic sheet to the focused project                           }
{..............................................................................}

Function Gen_AddSheet(Params : String; RequestId : String) : String;
Var
    SheetName, ProjectPath : String;
    Workspace : IWorkspace;
    Project : IProject;
    NewDocPath : String;
    ServerDoc : IServerDocument;
    Saved, Added : Boolean;
Begin
    SheetName := ExtractJsonValue(Params, 'name');
    If SheetName = '' Then SheetName := 'NewSheet';
    ProjectPath := ExtractJsonValue(Params, 'project_path');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    If ProjectPath <> '' Then
    Begin
        Project := FindProjectByPath(Workspace, ProjectPath);
        If Project = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'PROJECT_NOT_OPEN',
                'Not an open project: ' + ProjectPath + '. Open it with '
                + 'proj_open first. Nothing was created.');
            Exit;
        End;
    End
    Else
        Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    { A free document (a library opened on its own, say) is focused under   }
    { a project that has no file. The sheet went in as an unsaved document  }
    { with a bare name, in no project, and the reply said it was added.     }
    If Not LooksAbsolutePath(Project.DM_ProjectFullPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT',
            'The focused document belongs to no project, so there is nowhere '
            + 'to add the sheet. Name the project with project_path, or bring '
            + 'one of its documents forward. Nothing was created.');
        Exit;
    End;

    // Build the new sheet path in the same directory as the project
    NewDocPath := Project.DM_ProjectFullPath;
    // Strip project filename to get directory
    While (Length(NewDocPath) > 0) And (Copy(NewDocPath, Length(NewDocPath), 1) <> '\') Do
        NewDocPath := Copy(NewDocPath, 1, Length(NewDocPath) - 1);
    NewDocPath := NewDocPath + SheetName + '.SchDoc';

    { A FILE ALREADY THERE is not a new sheet: proj_delete_sheet leaves the }
    { file on disk, and a sheet created again under its old name opened    }
    { that file with its old drawing (retake 07b).                          }
    If FileExists(NewDocPath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'FILE_EXISTS',
            NewDocPath + ' already exists (a sheet removed from a project '
            + 'stays on disk), so a sheet of that name would open with the '
            + 'drawing already in it. Choose another name, or add the existing '
            + 'sheet back with proj_add_document. Nothing was created.');
        Exit;
    End;

    { Create the blank schematic via Client.OpenNewDocument, mirroring the
      working App_CreateDocument path. The previous
      RunProcess('WorkspaceManager:CreateNewDocument') WITHOUT a FileName
      raises a modal "Value cannot be null. (Parameter 'key')" and WEDGES
      the polling loop. OpenNewDocument names the doc up front, so the
      null-key never happens. }
    ServerDoc := Client.OpenNewDocument('SCH', NewDocPath, SheetName, False);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED',
            'Client.OpenNewDocument returned Nil for kind=SCH');
        Exit;
    End;

    { Persist to disk: force the path then DoFileSave, falling back to a
      Save-As via WorkspaceManager:SaveObject (same fallback as
      App_CreateDocument). }
    Saved := False;
    Try ServerDoc.SetFileName(NewDocPath); Except { the file check below decides } End;
    Try
        ServerDoc.SetModified(True);
        ServerDoc.DoFileSave('');
        Saved := FileExists(NewDocPath);
    Except Saved := False; End;
    If Not Saved Then
    Begin
        Try
            ServerDoc.Focus;
            ResetParameters;
            AddStringParameter('ObjectKind', 'Document');
            AddStringParameter('FileName', NewDocPath);
            RunProcess('WorkspaceManager:SaveObject');
            Saved := FileExists(NewDocPath);
        Except Saved := False; End;
    End;

    { Add to the focused project via the documented project-side API
      (DM_AddSourceDocument), which works across workspace states where
      WorkspaceManager:AddObjectToProject silently no-ops. }
    Added := False;
    Try
        Project.DM_AddSourceDocument(NewDocPath);
    Except End;
    { Read back: the call raising nothing is not the sheet being listed. }
    Added := ProjectListsDocument(Project, NewDocPath);

    Result := BuildSuccessResponse(RequestId,
        '{"success":' + BoolToJsonStr(Saved And Added) +
        ',"path":"' + EscapeJsonString(NewDocPath) + '"' +
        ',"project_path":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"' +
        ',"saved":' + BoolToJsonStr(Saved) +
        ',"added_to_project":' + BoolToJsonStr(Added) +
        { Written now, or the add is gone after a reopen. }
        ',"project_saved":' + BoolToJsonStr(Added And SaveProjectFile(Project)) + '}');
End;

{..............................................................................}
{ Delete (remove) a schematic sheet from the focused project                 }
{ Safety check: will not remove the last remaining sheet.                    }
{..............................................................................}

Function Gen_DeleteSheet(Params : String; RequestId : String) : String;
Var
    FilePath : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    I, SchCount : Integer;
    Found, WasModified : Boolean;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');
    If FilePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'file_path parameter is required');
        Exit;
    End;


    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    // Count schematic documents and verify the target exists
    SchCount := 0;
    Found := False;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind = 'SCH' Then
        Begin
            Inc(SchCount);
            If SameText(Doc.DM_FullPath, FilePath) Then
                Found := True;
        End;
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Sheet not found in project: ' + FilePath);
        Exit;
    End;

    If SchCount <= 1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SAFETY_CHECK',
            'Cannot remove the last schematic sheet from the project');
        Exit;
    End;

    { THE SAME REMOVAL AS proj_remove_document. This ran                     }
    { WorkspaceManager:CloseObject and RemoveObjectFromProject, which act on  }
    { the FOCUSED object (a close by path once closed another project) and   }
    { left the .PrjPcb listing the sheet, so it was back after a reopen      }
    { (issue #94). Proj_RemoveDocument removes it, writes the project file   }
    { and reads it back.                                                     }
    Result := Proj_RemoveDocument('{"document_path":"' + EscapeJsonString(FilePath)
        + '","project_path":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '"}',
        RequestId);

    { Then close the sheet itself, by its document object, unless it holds }
    { unsaved edits; a modified sheet is left open rather than lost.        }
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(FilePath); Except End;
    If ServerDoc <> Nil Then
    Begin
        WasModified := True;
        Try WasModified := ServerDoc.Modified; Except End;
        If Not WasModified Then
        Begin
            Try Client.CloseDocument(ServerDoc); Except End;
        End;
    End;
End;

{..............................................................................}
{ Zoom to specific X,Y coordinates (in mils for SCH, mils for PCB)          }
{ PCB: the view is fitted to a square of +-radius mils (default 200) round   }
{ x,y through GraphicalView_ZoomOnRect (Altium's ConvertPicture.PAS does    }
{ the same). PCB:Jump JumpToLocation10 jumped to location MARKER 10 and,    }
{ with none set, raised a modal "Location Marker 10 has not been set" that  }
{ stopped the polling loop; X and Y were never its parameters.              }
{..............................................................................}

Function Gen_ZoomToXY(Params : String; RequestId : String) : String;
Var
    XStr, YStr, RadStr, FocusedKind : String;
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    PcbLib : IPCB_Library;
    Cx, Cy, Rad : TCoord;
    Workspace : IWorkspace;
    Doc : IDocument;
Begin
    XStr := ExtractJsonValue(Params, 'x');
    YStr := ExtractJsonValue(Params, 'y');
    RadStr := ExtractJsonValue(Params, 'radius');

    If (XStr = '') Or (YStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'x and y parameters are required');
        Exit;
    End;

    { The focused tab decides, as in Gen_Zoom. Asking for any board first  }
    { zoomed a board in the background while a sheet or a library was in  }
    { front. A PcbLib zooms its own view, in the footprint editor's frame. }
    FocusedKind := '';
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Nil;
        Try Doc := Workspace.DM_FocusedDocument; Except End;
        If Doc <> Nil Then
            Try FocusedKind := UpperCase(Doc.DM_DocumentKind); Except End;
    End;
    Board := Nil;
    SchDoc := Nil;
    If FocusedKind = 'PCB' Then
        Board := GetPCBBoardAnywhere(0)
    Else If FocusedKind = 'PCBLIB' Then
    Begin
        PcbLib := PCBServer.GetCurrentPCBLibrary;
        If PcbLib <> Nil Then Board := PcbLib.Board;
    End
    Else If IsSchEditorKind(FocusedKind) Then
        SchDoc := SchServer.GetCurrentSchDocument
    Else
    Begin
        Board := GetPCBBoardAnywhere(0);
        If Board = Nil Then SchDoc := SchServer.GetCurrentSchDocument;
    End;

    If Board <> Nil Then
    Begin
        Cx := MilsToCoordF(StrToFloatDef(XStr, 0));
        Cy := MilsToCoordF(StrToFloatDef(YStr, 0));
        Rad := MilsToCoordF(StrToFloatDef(RadStr, 200));
        If Rad <= 0 Then Rad := MilsToCoordF(200);
        Board.GraphicalView_ZoomOnRect(Cx - Rad, Cy - Rad, Cx + Rad, Cy + Rad);

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"x":' + XStr + ',"y":' + YStr + ',"context":"pcb"}');
    End
    Else If SchDoc <> Nil Then
    Begin
        ResetParameters;
        AddStringParameter('X', XStr);
        AddStringParameter('Y', YStr);
        RunProcess('Sch:ZoomToLocation');

        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"x":' + XStr + ',"y":' + YStr + ',"context":"schematic"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Switch between 2D and 3D view for a board or a footprint library          }
{..............................................................................}

{ Lets Altium finish a view change before the next one: run back to back, }
{ a camera turn right after the switch to 3D was dropped.                 }
Procedure SettleView(Dummy : Integer);
Var
    I : Integer;
Begin
    For I := 1 To 8 Do
    Begin
        Try Application.ProcessMessages; Except End;
        Sleep(60);
    End;
End;

{..............................................................................}

{ A focused PcbLib is switched too: PCB:SwitchTo3D serves the footprint     }
{ editor (View > 3D Layout Mode there). camera turns the 3D view to one of  }
{ Altium's camera presets (PCB:ResetCamera To=Top, RotateOrtho or Bottom),  }
{ and fit zooms the view to everything afterwards. footprint_name puts that }
{ footprint of the focused PcbLib on screen first: the editor otherwise     }
{ keeps showing whichever footprint was current, often the empty default.  }
{ Altium's 3D camera presets (PCB:ResetCamera To=...), by the names the   }
{ tool takes; '' for a name that is not one of them.                       }
Function CameraPreset(Name : String) : String;
Begin
    Result := '';
    Name := LowerCase(Trim(Name));
    If Name = 'top' Then Result := 'Top'
    Else If Name = 'iso' Then Result := 'RotateOrtho'
    Else If Name = 'bottom' Then Result := 'Bottom'
    Else If Name = 'front' Then Result := 'Front'
    Else If Name = 'back' Then Result := 'Back'
    Else If Name = 'left' Then Result := 'Left'
    Else If Name = 'right' Then Result := 'Right';
End;

Procedure TurnCamera(CameraTo : String);
Begin
    ResetParameters;
    AddStringParameter('To', CameraTo);
    RunProcess('PCB:ResetCamera');
    SettleView(0);
End;

Function Gen_SwitchView(Params : String; RequestId : String) : String;
Var
    Mode, Camera, CameraTo, FocusedKind, FpName, Views, One, Bad : String;
    Hold : Integer;
    Fit : Boolean;
    Board : IPCB_Board;
    Workspace : IWorkspace;
    Doc : IDocument;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
Begin
    Mode := LowerCase(ExtractJsonValue(Params, 'mode'));
    FpName := ExtractJsonValue(Params, 'footprint_name');
    If Mode = '' Then Mode := '3d';
    Camera := LowerCase(ExtractJsonValue(Params, 'camera'));
    Fit := LowerCase(ExtractJsonValue(Params, 'fit')) = 'true';
    If (Mode <> '3d') And (Mode <> '2d') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
            'mode is 3d or 2d, not "' + Mode + '"');
        Exit;
    End;
    CameraTo := CameraPreset(Camera);
    If (Camera <> '') And (CameraTo = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
            'camera is top, iso, bottom, front, back, left or right, not "' + Camera + '"');
        Exit;
    End;
    { views: cameras to turn through, each held a moment, so the model is }
    { seen from several sides. Checked whole before anything moves.      }
    Views := LowerCase(ExtractJsonValue(Params, 'views'));
    Bad := '';
    One := Views;
    While One <> '' Do
    Begin
        If Pos(',', One) > 0 Then
        Begin
            If CameraPreset(Copy(One, 1, Pos(',', One) - 1)) = '' Then Bad := Copy(One, 1, Pos(',', One) - 1);
            One := Copy(One, Pos(',', One) + 1, Length(One));
        End
        Else
        Begin
            If CameraPreset(One) = '' Then Bad := One;
            One := '';
        End;
    End;
    If Bad <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
            'views lists cameras from top, iso, bottom, front, back, left, right; not "' + Bad + '"');
        Exit;
    End;

    FocusedKind := '';
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Doc := Nil;
        Try Doc := Workspace.DM_FocusedDocument; Except End;
        If Doc <> Nil Then
            Try FocusedKind := UpperCase(Doc.DM_DocumentKind); Except End;
    End;

    If (FpName <> '') And (FocusedKind <> 'PCBLIB') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCBLIB',
            'footprint_name needs a focused .PcbLib; the focused document is ' + FocusedKind);
        Exit;
    End;
    If FpName <> '' Then
    Begin
        PcbLib := PCBServer.GetCurrentPCBLibrary;
        Footprint := Nil;
        If PcbLib <> Nil Then
        Begin
            Iter := PcbLib.LibraryIterator_Create;
            Try
                Footprint := Iter.FirstPCBObject;
                While Footprint <> Nil Do
                Begin
                    If Footprint.Name = FpName Then Break;
                    Footprint := Iter.NextPCBObject;
                End;
            Finally
                PcbLib.LibraryIterator_Destroy(Iter);
            End;
        End;
        If Footprint = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'FOOTPRINT_NOT_FOUND',
                'Footprint not found in the focused library: ' + FpName);
            Exit;
        End;
        Try PcbLib.SetState_CurrentComponent(Footprint); Except NoteWriteFailed('make footprint current'); End;
        ResetParameters;
        AddStringParameter('Action', 'Redraw');
        RunProcess('PCB:Zoom');
    End;

    If FocusedKind <> 'PCBLIB' Then
    Begin
        Board := GetPCBBoardAnywhere(0);
        If Board = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCB',
                'No board or footprint library has focus');
            Exit;
        End;
    End;

    ResetParameters;
    If Mode = '3d' Then RunProcess('PCB:SwitchTo3D')
    Else RunProcess('PCB:SwitchTo2D');
    SettleView(0);

    If Fit Then
    Begin
        { In 3D the fit is taken from straight above, then one step out:  }
        { fitted at an angle, or without the step, the part fills the     }
        { view and runs off its edges. The camera turn and both zooms go  }
        { through with no redraw between them: each redraw showed a step  }
        { on screen, and a large module (LTM8060) gave seconds of black   }
        { from Zoom All, which in 3D lands inside the body until the step }
        { out. The 3D view is drawn once after the switch, before the     }
        { fit: fitted before it exists, the zoom used the last part's     }
        { close camera and the module filled the screen.                  }
        If Mode = '3d' Then
        Begin
            ResetParameters;
            AddStringParameter('To', 'Top');
            RunProcess('PCB:ResetCamera');
        End;
        ResetParameters;
        AddStringParameter('Action', 'All');
        RunProcess('PCB:Zoom');
        If Mode = '3d' Then
        Begin
            ResetParameters;
            AddStringParameter('Action', 'Out');
            RunProcess('PCB:Zoom');
        End;
        SettleView(0);
    End;
    If (Mode = '3d') And (Views <> '') Then
    Begin
        One := Views;
        While One <> '' Do
        Begin
            If Pos(',', One) > 0 Then
            Begin
                TurnCamera(CameraPreset(Copy(One, 1, Pos(',', One) - 1)));
                One := Copy(One, Pos(',', One) + 1, Length(One));
            End
            Else
            Begin
                TurnCamera(CameraPreset(One));
                One := '';
            End;
            For Hold := 1 To 12 Do
            Begin
                Try Application.ProcessMessages; Except End;
                Sleep(100);
            End;
        End;
    End;
    If (Mode = '3d') And (CameraTo <> '') Then TurnCamera(CameraTo);

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"mode":"' + EscapeJsonString(Mode) + '","document_kind":"'
        + EscapeJsonString(FocusedKind) + '","footprint_name":"' + EscapeJsonString(FpName)
        + '","camera":"' + EscapeJsonString(Camera) + '","views":"' + EscapeJsonString(Views)
        + '","fit":' + BoolToJsonStr(Fit) + '}');
End;

{..............................................................................}
{ Measure distance between two points (calculated, no Altium interaction)    }
{ Coordinates in mils. Returns Euclidean distance.                           }
{..............................................................................}

Function Gen_MeasureDistance(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Double;
    DX, DY : Double;
    Distance : Double;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);

    DX := X2 - X1;
    DY := Y2 - Y1;
    Distance := Sqrt(DX * DX + DY * DY);

    Result := BuildSuccessResponse(RequestId,
        '{"x1":' + FloatToJsonStr(X1) +
        ',"y1":' + FloatToJsonStr(Y1) +
        ',"x2":' + FloatToJsonStr(X2) +
        ',"y2":' + FloatToJsonStr(Y2) +
        ',"dx":' + FloatToJsonStr(DX) +
        ',"dy":' + FloatToJsonStr(DY) +
        ',"distance_mils":' + FloatToJsonStr(Distance) +
        ',"distance_mm":' + FloatToJsonStr(Distance * 0.0254) + '}');
End;

{..............................................................................}
{ Get ERC violations from the focused project after compilation/ERC          }
{ Returns violation count and messages from the DM API.                      }
{..............................................................................}

{ Reports each violation WITH the objects it is about.                        }
{                                                                             }
{ A category and a sheet name are not actionable: "floating input pin" on a   }
{ sheet with forty parts does not say which pin, and the only safe response   }
{ to that is to do nothing. A NoERC marker placed by guesswork silently       }
{ suppresses a real disconnection, which is strictly worse than the warning   }
{ it clears.                                                                  }
{                                                                             }
{ IViolation.DM_RelatedObjects carries the offending objects. Everything read }
{ from one is declared on IDMObject, the base interface every related object  }
{ implements, so no call here can hit the undeclared-identifier crash that a  }
{ narrower interface would risk. DM_PrimaryCrossProbeString is what Altium    }
{ itself uses to jump to the object, so it identifies the exact pin or net.   }
{ WHAT ALTIUM REPORTS, NOT EVERYTHING IT FOUND. A violation that a No ERC   }
{ marker covers stays in DM_Violations with DM_IsSuppressed set, and one of a }
{ type set to No Report stays with DM_ErrorLevel 0. Both used to be listed,   }
{ so a correctly placed marker looked as if it suppressed nothing (measured   }
{ 2026-10-23: 91 violations before and after 36 markers). They are now left  }
{ out and counted; include_suppressed lists them too, each flagged.          }
Function Gen_GetErcViolations(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Violation : IViolation;
    RelObj : IDMObject;
    I, J, VCount, MaxItems, Offset, Returned, NextOffset, RelCount : Integer;
    Level, NSuppressed, NNoReport, NReported : Integer;
    JsonItems, RelItems, IncStr : String;
    First, FirstRel, Truncated, Suppressed, IncludeAll : Boolean;
    Desc, Detail, Kind, DocName, Probe : String;
Begin
    { limit 0 = every violation; offset pages through a long list. }
    MaxItems := StrToIntDef(ExtractJsonValue(Params, 'limit'), 100);
    Offset := StrToIntDef(ExtractJsonValue(Params, 'offset'), 0);
    If Offset < 0 Then Offset := 0;
    IncStr := LowerCase(ExtractJsonValue(Params, 'include_suppressed'));
    IncludeAll := (IncStr = 'true') Or (IncStr = '1');

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    VCount := Project.DM_ViolationCount;

    { Counted over the whole list, so the totals do not depend on the page. }
    NSuppressed := 0;
    NNoReport := 0;
    For I := 0 To VCount - 1 Do
    Begin
        Violation := Project.DM_Violations(I);
        If Violation = Nil Then Continue;
        Suppressed := False;
        Try Suppressed := Violation.DM_IsSuppressed; Except Suppressed := False; End;
        Level := 2;
        Try Level := Violation.DM_ErrorLevel; Except Level := 2; End;
        If Suppressed Then Inc(NSuppressed)
        Else If Level = 0 Then Inc(NNoReport);
    End;
    NReported := VCount - NSuppressed - NNoReport;

    JsonItems := '';
    First := True;
    Returned := 0;
    NextOffset := 0;
    Truncated := False;

    For I := Offset To VCount - 1 Do
    Begin
        If (MaxItems > 0) And (Returned >= MaxItems) Then
        Begin
            Truncated := True;
            NextOffset := I;
            Break;
        End;

        Violation := Project.DM_Violations(I);
        If Violation = Nil Then Continue;
        Suppressed := False;
        Try Suppressed := Violation.DM_IsSuppressed; Except Suppressed := False; End;
        Level := 2;
        Try Level := Violation.DM_ErrorLevel; Except Level := 2; End;
        If (Not IncludeAll) And (Suppressed Or (Level = 0)) Then Continue;

        Try
            Desc := Violation.DM_LongDescriptorString;
        Except
            Desc := '(description unavailable)';
        End;

        Try
            Detail := Violation.DM_DetailString;
        Except
            Detail := '';
        End;

        { The objects the violation is actually about. Without these the
          caller can see that something is wrong but never what, which
          is the difference between a report and a to-do list. }
        RelItems := '';
        FirstRel := True;
        RelCount := 0;
        Try
            RelCount := Violation.DM_RelatedObjectCount;
        Except
            RelCount := 0;
        End;

        For J := 0 To RelCount - 1 Do
        Begin
            Try
                RelObj := Violation.DM_RelatedObjects(J);
            Except
                RelObj := Nil;
            End;
            If RelObj = Nil Then Continue;

            Kind := '';
            DocName := '';
            Probe := '';
            Try
                Kind := RelObj.DM_ObjectKindString;
            Except
                Kind := '';
            End;
            Try
                DocName := RelObj.DM_OwnerDocumentName;
            Except
                DocName := '';
            End;
            Try
                { What Altium uses to cross-probe to this exact object.
                  This is the field that turns "a floating pin somewhere
                  on this sheet" into a designator and pin number. }
                Probe := RelObj.DM_PrimaryCrossProbeString;
            Except
                Probe := '';
            End;

            If Not FirstRel Then RelItems := RelItems + ',';
            FirstRel := False;
            RelItems := RelItems + '{"kind":"' + EscapeJsonString(Kind) +
                '","document":"' + EscapeJsonString(DocName) +
                '","cross_probe":"' + EscapeJsonString(Probe) + '"}';
        End;

        If Not First Then JsonItems := JsonItems + ',';
        First := False;
        JsonItems := JsonItems + '{"index":' + IntToStr(I) +
            ',"description":"' + EscapeJsonString(Desc) +
            '","detail":"' + EscapeJsonString(Detail) +
            '","level":"' + ErcLevelName(Level) +
            '","suppressed":' + BoolToJsonStr(Suppressed) +
            ',"related_object_count":' + IntToStr(RelCount) +
            ',"related_objects":[' + RelItems + ']}';
        Inc(Returned);
    End;

    If Truncated Then
        Result := BuildSuccessResponse(RequestId,
            '{"violation_count":' + IntToStr(NReported) +
            ',"suppressed_count":' + IntToStr(NSuppressed) +
            ',"not_reported_count":' + IntToStr(NNoReport) +
            ',"raw_violation_count":' + IntToStr(VCount) +
            ',"offset":' + IntToStr(Offset) +
            ',"returned":' + IntToStr(Returned) +
            ',"truncated":true,"next_offset":' + IntToStr(NextOffset) +
            ',"violations":[' + JsonItems + ']}')
    Else
        Result := BuildSuccessResponse(RequestId,
            '{"violation_count":' + IntToStr(NReported) +
            ',"suppressed_count":' + IntToStr(NSuppressed) +
            ',"not_reported_count":' + IntToStr(NNoReport) +
            ',"raw_violation_count":' + IntToStr(VCount) +
            ',"offset":' + IntToStr(Offset) +
            ',"returned":' + IntToStr(Returned) +
            ',"truncated":false,"violations":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Run Electrical Rules Check on the focused project and return what it found }
{ Compiles first (required), runs Sch:ERC, then answers with the violation   }
{ list. It used to answer "ERC completed" and nothing else, so a caller      }
{ reading violation_count off the run got none. Defined after                }
{ Gen_GetErcViolations because DelphiScript has no forward declarations.     }
{..............................................................................}

Function Gen_RunERC(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    SmartCompile(Project);
    ResetParameters;
    RunProcess('Sch:ERC');

    Result := Gen_GetErcViolations(Params, RequestId);
End;

{..............................................................................}
{ Force refresh/redraw of the current document                               }
{..............................................................................}

{..............................................................................}
{ Gen_MirrorSchComponent - mirror a placed component horizontally.             }
{                                                                              }
{ WHAT ALTIUM ITSELF DOES, measured 2026-09-19 by toggling Mirrored in the     }
{ Properties panel and reading the result back: it sets IsMirrored AND         }
{ reflects the component's primitives. On an NE555 at Location.X 5700, pin     }
{ RESET moved from Location.X 6200 to 5200 and its Orientation flipped 0 to 2, }
{ while IsMirrored went False to True. Both halves, one operation.             }
{                                                                              }
{ WRITING THE FLAG ALONE DOES NOTHING VISIBLE, which is the defect reported    }
{ from the field. obj_modify set IsMirrored, the value read back, Altium's own }
{ Properties panel showed Mirrored ticked, and the part did not move. The      }
{ canvas draws from the primitives, so there was nothing new to draw: no       }
{ repaint call can help, and a whole afternoon of GraphicallyInvalidate,       }
{ SetState_xSizeySize and Sch:Zoom variants confirmed it the hard way.         }
{                                                                              }
{ REFLECTING THE PRIMITIVES ALONE IS ALSO WRONG. It draws correctly and is     }
{ then discarded by Update From Libraries, which re-instantiates the symbol    }
{ from the library. The flag is instance data and survives that. Writing both  }
{ is what Altium stores, so a part mirrored here is identical on disk to one   }
{ mirrored by hand, which is also why this cannot double-mirror on reload:     }
{ there is one representation, not two competing ones.                          }
{                                                                              }
{ Params: designator (required), doc_path (optional, defaults to the focused   }
{ sheet), mirrored (optional "true"/"false"; omitted means toggle).            }
{..............................................................................}

{ MirroredAngle - an angle reflected about the vertical axis, in 0..360.    }
Function MirroredAngle(A : Double) : Double;
Begin
    Result := 180 - A;
    While Result < 0 Do Result := Result + 360;
    While Result >= 360 Do Result := Result - 360;
End;

Function Gen_MirrorSchComponent(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Arc : ISch_Arc;
    ElArc : ISch_EllipticalArc;   { not EArc: names are case-insensitive, and EArc hid the eArc kind }
    Ell : ISch_Ellipse;
    StartA, EndA : Double;
    Iter, PIter : ISch_Iterator;
    Comp, Found : ISch_Component;
    Prim : ISch_GraphicalObject;
    Designator, DocPath, StateStr : String;
    Loc : TLocation;
    Crn : TLocation;
    Vtx : TLocation;
    CompX : Integer;
    Ori, Moved, Swap, Skipped, Kind, Cnt, I : Integer;
    Handled : Boolean;
    SkippedIds : String;
    WantMirror, Explicit : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    DocPath    := ExtractJsonValue(Params, 'doc_path');
    StateStr   := ExtractJsonValue(Params, 'mirrored');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'designator is required');
        Exit;
    End;

    If DocPath <> '' Then SchDoc := SchDocByPathLoading(DocPath)
    Else SchDoc := SchServer.GetCurrentSchDocument;

    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document. Pass doc_path to an open .SchDoc, or '
            + 'focus one first.');
        Exit;
    End;

    Found := Nil;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        While Comp <> Nil Do
        Begin
            If Comp.Designator.Text = Designator Then
            Begin
                Found := Comp;
                Break;
            End;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    If Found = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'No component with designator "' + Designator + '" on '
            + SchDoc.DocumentName);
        Exit;
    End;

    Explicit := (StateStr <> '');
    WantMirror := Not Found.IsMirrored;
    If Explicit Then WantMirror := StrToBool(StateStr);

    { An explicit request for the state it is already in must not reflect     }
    { the geometry, or the part silently ends up mirrored the wrong way.      }
    If Explicit And (WantMirror = Found.IsMirrored) Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            '{"designator":"' + EscapeJsonString(Designator) + '",'
            + '"mirrored":' + BoolToJsonStr(WantMirror) + ','
            + '"primitives_moved":0,"changed":false}');
        Exit;
    End;

    CompX := Found.Location.X;
    Moved := 0;
    Skipped := 0;
    SkippedIds := '';

    SchBeginEdit(SchDoc, '');
    SchBeginModify(Found);

    PIter := Found.SchIterator_Create;
    Try
        Prim := PIter.FirstSchObject;
        While Prim <> Nil Do
        Begin
            { NOT EVERY PRIMITIVE HAS Location.                               }
            {                                                                 }
            { A polygon carries a vertex ARRAY instead, and asking it for     }
            { Location raises "Undeclared identifier: Location", which is a   }
            { modal Try/Except CANNOT catch and which halts the script engine }
            { mid-loop. Measured 2026-09-19 on a 1N4007 placed from a real    }
            { library: its triangle is a polygon and the bridge wedged on     }
            { exactly that line, needing the dialog dismissed and             }
            { StartMCPServer relaunched.                                      }
            {                                                                 }
            { So dispatch on ObjectId and touch nothing unrecognised. An      }
            { unhandled type is COUNTED, not silently ignored, because a      }
            { half-mirrored symbol that reports success is worse than one     }
            { that says which parts it could not move.                        }
            Kind := Prim.ObjectId;

            { FLAT, NOT AN Else If CHAIN. As a chain, an arc never reached   }
            { its branch: measured 2026-10-11, a symbol's arc bubble stayed   }
            { put and was reported skipped as kind 8 while the same          }
            { comparison matched elsewhere. DelphiScript mis-binds long       }
            { Else If chains, so each branch is its own If on Handled.       }
            { The designator moves with the other parameters.                }
            Handled := False;
            If (Kind = ePin) Or (Kind = eParameter) Or (Kind = eDesignator)
               Or (Kind = eLabel)
               Or (Kind = eRectangle) Or (Kind = eRoundRectangle)
               Or (Kind = eLine) Or (Kind = eImage) Then
            Begin
                Handled := True;
                { The materialized local is required, a direct write to       }
                { Prim.Location.X does not take.                               }
                Try
                    Loc := Prim.Location;
                    Loc.X := CompX + (CompX - Loc.X);
                    Prim.Location := Loc;
                    Moved := Moved + 1;
                Except NoteWriteFailed('mirror primitive Location'); End;
            End;
            If (Not Handled)
               And ((Kind = ePolygon) Or (Kind = ePolyline) Or (Kind = eBezier)) Then
            Begin
                Handled := True;
                { Vertex arrays are 1-based in Altium. Reflect every point;   }
                { there is no Location to move.                                }
                Cnt := 0;
                Try Cnt := Prim.GetState_VerticesCount; Except End;
                For I := 1 To Cnt Do
                Begin
                    Try
                        Vtx := Prim.GetState_Vertex(I);
                        Vtx.X := CompX + (CompX - Vtx.X);
                        Prim.SetState_Vertex(I, Vtx);
                    Except NoteWriteFailed('mirror primitive vertex'); End;
                End;
                If Cnt > 0 Then Moved := Moved + 1
                Else Skipped := Skipped + 1;
            End;
            { An arc's centre reflects like any point, and its sweep mirrors:  }
            { the new start is 180 minus the old end and the new end 180     }
            { minus the old start, so the arc still runs counter-clockwise.  }
            { A full circle keeps its angles: mirrored, its start and end    }
            { would coincide. Typed locals, as the symbol reader uses, since }
            { these members are not on the base interface.                   }
            If (Not Handled) And ((Kind = eArc) Or (Kind = eEllipticalArc)) Then
            Begin
                Handled := True;
                Try
                    If Kind = eArc Then
                    Begin
                        Arc := Prim;
                        Loc := Arc.Location;
                        Loc.X := CompX + (CompX - Loc.X);
                        Arc.Location := Loc;
                        StartA := Arc.StartAngle;
                        EndA := Arc.EndAngle;
                        If Abs(EndA - StartA) < 360 Then
                        Begin
                            Arc.StartAngle := MirroredAngle(EndA);
                            Arc.EndAngle := MirroredAngle(StartA);
                        End;
                    End
                    Else
                    Begin
                        ElArc := Prim;
                        Loc := ElArc.Location;
                        Loc.X := CompX + (CompX - Loc.X);
                        ElArc.Location := Loc;
                        StartA := ElArc.StartAngle;
                        EndA := ElArc.EndAngle;
                        If Abs(EndA - StartA) < 360 Then
                        Begin
                            ElArc.StartAngle := MirroredAngle(EndA);
                            ElArc.EndAngle := MirroredAngle(StartA);
                        End;
                    End;
                    Moved := Moved + 1;
                Except
                    Skipped := Skipped + 1;
                End;
            End;
            If (Not Handled) And (Kind = eEllipse) Then
            Begin
                Handled := True;
                Try
                    Ell := Prim;
                    Loc := Ell.Location;
                    Loc.X := CompX + (CompX - Loc.X);
                    Ell.Location := Loc;
                    Moved := Moved + 1;
                Except
                    Skipped := Skipped + 1;
                End;
            End;
            If Not Handled Then
            Begin
                { The ObjectId is RECORDED, not just counted. On a minimal     }
                { symbol one unmoved graphic wrecks the part while a busier    }
                { one still looks plausible, so "5 skipped" is not actionable  }
                { and "kind 13 skipped" is.                                     }
                Skipped := Skipped + 1;
                If Pos(IntToStr(Kind), SkippedIds) = 0 Then
                    SkippedIds := SkippedIds + IntToStr(Kind) + ' ';
            End;

            { A TWO-POINT PRIMITIVE NEEDS BOTH POINTS REFLECTED.              }
            {                                                                 }
            { Reflecting only Location TRANSLATES the shape instead of        }
            { mirroring it: measured 2026-09-19, U1's body rectangle ended up }
            { offset to one side of its own pins while the pins themselves    }
            { were correct. A pin has a single point, which is why it looked  }
            { right and the body did not.                                      }
            {                                                                 }
            { Guarded by ObjectId rather than probed with Try/Except: Corner  }
            { does not exist on a pin, and an undeclared identifier raises a  }
            { modal that Try/Except cannot catch and that wedges the loop.    }
            If (Kind = eRectangle) Or (Kind = eRoundRectangle) Or (Kind = eLine) Then
            Begin
                Try
                    Crn := Prim.Corner;
                    Crn.X := CompX + (CompX - Crn.X);
                    Prim.Corner := Crn;

                    { Reflection swaps left and right, so a rectangle whose   }
                    { Location was its lower-left now holds the lower-RIGHT,  }
                    { and the two are put back in order. NOT a line: swapping }
                    { only the X of its two ends reverses its slope, which    }
                    { sheared every diagonal of an op-amp triangle.           }
                    Loc := Prim.Location;
                    If (Kind <> eLine) And (Loc.X > Crn.X) Then
                    Begin
                        Swap := Loc.X;
                        Loc.X := Crn.X;
                        Crn.X := Swap;
                        Prim.Location := Loc;
                        Prim.Corner := Crn;
                    End;
                Except NoteWriteFailed('mirror primitive Corner'); End;
            End;

            { A horizontal mirror flips a pin that points left or right and   }
            { leaves a vertical one pointing the same way; only its X moved.  }
            If Kind = ePin Then
            Begin
                Try
                    Ori := Prim.Orientation;
                    If Ori = 0 Then Prim.Orientation := 2
                    Else If Ori = 2 Then Prim.Orientation := 0;
                Except NoteWriteFailed('mirror pin Orientation'); End;
            End;

            Prim := PIter.NextSchObject;
        End;
    Finally
        Found.SchIterator_Destroy(PIter);
    End;

    { DESIGNATOR AND COMMENT ARE NOT CHILDREN OF THE ITERATOR.                }
    {                                                                         }
    { They hang off the component as sub-objects, so the loop above never     }
    { sees them and they stayed put while the symbol moved out from under     }
    { them. Reported on all three library parts, 2026-09-19. Same             }
    { materialized-local pattern as Generic.pas:8586, which already writes    }
    { these two locations.                                                     }
    Try
        Loc := Found.Designator.Location;
        Loc.X := CompX + (CompX - Loc.X);
        Found.Designator.Location := Loc;
    Except NoteWriteFailed('mirror Designator location'); End;
    Try
        Loc := Found.Comment.Location;
        Loc.X := CompX + (CompX - Loc.X);
        Found.Comment.Location := Loc;
    Except NoteWriteFailed('mirror Comment location'); End;

    { The flag last, so the instance records what the geometry now shows.     }
    Try Found.SetState_IsMirrored(WantMirror); Except NoteWriteFailed('IsMirrored'); End;

    SchEndModify(Found);
    SchEndEdit(SchDoc, 'Edit');

    Try Found.GraphicallyInvalidate; Except End;
    Try SchDoc.GraphicallyInvalidate; Except End;

    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(Designator) + '",'
        + '"mirrored":' + BoolToJsonStr(WantMirror) + ','
        + '"primitives_moved":' + IntToStr(Moved) + ','
        { Non-zero means the mirror is INCOMPLETE: a primitive type this  }
        { handler does not know how to reflect was left where it was, so  }
        { the symbol is now part-mirrored. Reported rather than hidden.   }
        + '"primitives_skipped":' + IntToStr(Skipped) + ','
        + '"skipped_kinds":"' + Trim(SkippedIds) + '",'
        + '"changed":true}');
End;

Function Gen_RefreshDocument(RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    Board := GetPCBBoardAnywhere(0);

    If SchDoc <> Nil Then
    Begin
        SchDoc.GraphicallyInvalidate;
        Result := BuildSuccessResponse(RequestId, '{"success":true,"context":"schematic"}');
    End
    Else If Board <> Nil Then
    Begin
        ResetParameters;
        AddStringParameter('Action', 'Redraw');
        RunProcess('PCB:Zoom');
        Result := BuildSuccessResponse(RequestId, '{"success":true,"context":"pcb"}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{..............................................................................}
{ Get unconnected/floating pins via DM API                                    }
{ Compiles the project first, then iterates DM components to check            }
{ pin connection status. Returns designator + pin pairs with no net.          }
{..............................................................................}

Function Gen_GetUnconnectedPins(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    I, J, K, PinCount, CompCount, Total, DocCount : Integer;
    UsePhysical : Boolean;
    NetName, Designator, PinNumber, PinName, JsonItems : String;
    First : Boolean;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
        Exit;
    End;

    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No focused project');
        Exit;
    End;

    // Compile the project (required for DM pin connectivity data)
    SmartCompile(Project);

    Total := 0;
    JsonItems := '';
    First := True;

    GetCompiledDocs(Project, DocCount, UsePhysical);
    For I := 0 To DocCount - 1 Do
    Begin
        Doc := GetCompiledDoc(Project, I, UsePhysical);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;

        CompCount := Doc.DM_ComponentCount;
        For J := 0 To CompCount - 1 Do
        Begin
            Comp := Doc.DM_Components(J);
            If Comp = Nil Then Continue;
            Designator := Comp.DM_PhysicalDesignator;
            PinCount := Comp.DM_PinCount;

            For K := 0 To PinCount - 1 Do
            Begin
                Pin := Comp.DM_Pins(K);
                If Pin = Nil Then Continue;

                NetName := Pin.DM_FlattenedNetName;
                PinNumber := Pin.DM_PinNumber;
                PinName := Pin.DM_PinName;

                // A pin with no net or with '?' net is unconnected
                If (NetName = '') Or (NetName = '?') Then
                Begin
                    If Not First Then JsonItems := JsonItems + ',';
                    First := False;
                    JsonItems := JsonItems + '{"designator":"' + EscapeJsonString(Designator) +
                        '","pin_number":"' + EscapeJsonString(PinNumber) +
                        '","pin_name":"' + EscapeJsonString(PinName) +
                        '","sheet":"' + EscapeJsonString(Doc.DM_FullPath) + '"}';
                    Inc(Total);
                End;
            End;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"count":' + IntToStr(Total) + ',"unconnected_pins":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Place a wire segment between two XY coordinates on active schematic         }
{ Params: x1, y1, x2, y2 (in mils)                                          }
{..............................................................................}

Function Gen_PlaceWire(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Wire : ISch_Wire;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Wire := SchServer.SchObjectFactory(eWire, eCreate_Default);
    If Wire = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create wire object');
        Exit;
    End;

    { Two-vertex wire. The canonical pattern: each vertex needs its own      }
    { InsertVertex BEFORE the SetState_Vertex assignment. The previous code  }
    { only inserted vertex 1, so the wire was a single point, invisible.     }
    Wire.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    Wire.InsertVertex := 1;
    Wire.SetState_Vertex(1, Point(MilsToCoordF(X1), MilsToCoordF(Y1)));
    Wire.InsertVertex := 2;
    Wire.SetState_Vertex(2, Point(MilsToCoordF(X2), MilsToCoordF(Y2)));
    { Color := 0 renders the wire BLACK so it looks like a graphic
      line, not an electrical wire. Leave Color at factory default so
      Altium's wire colour scheme applies. }
    Wire.LineWidth := eSmall;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Wire);
    SchRegisterObject(SchDoc, Wire);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1) +
        ',"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + '}');
End;

{..............................................................................}
{ Place a bus segment between two points on the active schematic.             }
{ Buses are multi-signal wires (typically used with bus net labels like       }
{ DATA[0..7]). Placement and vertex handling mirror a normal wire.            }
{..............................................................................}

Function Gen_PlaceBus(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Bus : ISch_Bus;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Bus := SchServer.SchObjectFactory(eBus, eCreate_Default);
    If Bus = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create bus object');
        Exit;
    End;

    { Two-vertex bus: insert both vertices explicitly. }
    Bus.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    Bus.InsertVertex := 1;
    Bus.SetState_Vertex(1, Point(MilsToCoordF(X1), MilsToCoordF(Y1)));
    Bus.InsertVertex := 2;
    Bus.SetState_Vertex(2, Point(MilsToCoordF(X2), MilsToCoordF(Y2)));

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Bus);
    SchRegisterObject(SchDoc, Bus);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1) +
        ',"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + '}');
End;

{..............................................................................}
{ Place a rectangle on the schematic, graphic box, not a functional shape.   }
{ Params: x1,y1,x2,y2 in mils, solid=true/false, line_width=0..3              }
{..............................................................................}

Function Gen_PlaceRectangle(Params : String; RequestId : String) : String;
Var
    TmpI, LW : Integer;
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Rect : ISch_Rectangle;
    SolidStr : String;
    Solid : Boolean;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    SolidStr := ExtractJsonValue(Params, 'solid');
    LW := StrToIntDef(ExtractJsonValue(Params, 'line_width'), 1);
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;
    Solid := (LowerCase(SolidStr) = 'true') Or (SolidStr = '1');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Rect := SchServer.SchObjectFactory(eRectangle, eCreate_Default);
    If Rect = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create rectangle');
        Exit;
    End;

    Rect.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    Rect.Corner := Point(MilsToCoordF(X2), MilsToCoordF(Y2));
    Rect.IsSolid := Solid;
    Try
        If LW <= 0 Then Rect.LineWidth := eSmall
        Else If LW = 1 Then Rect.LineWidth := eSmall
        Else If LW = 2 Then Rect.LineWidth := eMedium
        Else Rect.LineWidth := eLarge;
    Except NoteWriteFailed('Rectangle.LineWidth'); End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Rect);
    SchRegisterObject(SchDoc, Rect);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1) + ','
        + '"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + ','
        + '"solid":' + BoolToJsonStr(Solid) + '}');
End;

{..............................................................................}
{ Place a line segment on the schematic.                                      }
{ Params: x1,y1,x2,y2 in mils, line_width=0..3                                }
{..............................................................................}

Function Gen_PlaceLine(Params : String; RequestId : String) : String;
Var
    LW : Integer;
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Line : ISch_Line;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    LW := StrToIntDef(ExtractJsonValue(Params, 'line_width'), 1);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Line := SchServer.SchObjectFactory(eLine, eCreate_Default);
    If Line = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create line');
        Exit;
    End;

    Line.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    Line.Corner := Point(MilsToCoordF(X2), MilsToCoordF(Y2));
    Try
        If LW <= 1 Then Line.LineWidth := eSmall
        Else If LW = 2 Then Line.LineWidth := eMedium
        Else Line.LineWidth := eLarge;
    Except NoteWriteFailed('Line.LineWidth'); End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Line);
    SchRegisterObject(SchDoc, Line);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1) + ','
        + '"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + '}');
End;

{..............................................................................}
{ Place a note (text box) on the schematic. Notes are ISch_Rectangle children }
{ with rich text. Useful for commentary / design notes on sheets.             }
{ Params: x1,y1,x2,y2 in mils, text                                           }
{..............................................................................}

Function Gen_PlaceNote(Params : String; RequestId : String) : String;
Var
    TmpI : Integer;
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Note : ISch_Note;
    TextStr : String;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    TextStr := ExtractJsonValue(Params, 'text');
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Note := SchServer.SchObjectFactory(eNote, eCreate_Default);
    If Note = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create note');
        Exit;
    End;

    Note.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    Note.Corner := Point(MilsToCoordF(X2), MilsToCoordF(Y2));
    Try Note.Text := TextStr; Except NoteWriteFailed('Note.Text'); End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Note);
    SchRegisterObject(SchDoc, Note);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1) + ','
        + '"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + ','
        + '"text":"' + EscapeJsonString(TextStr) + '"}');
End;

{..............................................................................}
{ Place a sheet symbol on the schematic, reference to a child SchDoc.        }
{ Params: x1,y1,x2,y2 in mils, sheet_file_name (e.g. PSU.SchDoc),             }
{         sheet_name (display name)                                           }
{..............................................................................}

Function Gen_PlaceSheetSymbol(Params : String; RequestId : String) : String;
Var
    TmpI : Double;
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Sym : ISch_SheetSymbol;
    FNObj : ISch_SheetFileName;
    NMObj : ISch_SheetName;
    FileNameStr, NameStr : String;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    FileNameStr := ExtractJsonValue(Params, 'sheet_file_name');
    NameStr := ExtractJsonValue(Params, 'sheet_name');
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    If FileNameStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'sheet_file_name required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Sym := SchServer.SchObjectFactory(eSheetSymbol, eCreate_Default);
    If Sym = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create sheet symbol');
        Exit;
    End;

    { ISch_SheetSymbol has no Corner property (unlike ISch_Rectangle) -- using
      it raises "Undeclared identifier: Corner". Size is set via XSize/YSize.
      Location is the TOP-left corner and the body extends right and down
      from it; placed at (x1, y1) the symbol ended up one full height under
      the box that was asked for. }
    Sym.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y2));
    Sym.XSize := MilsToCoordF(X2 - X1);
    Sym.YSize := MilsToCoordF(Y2 - Y1);
    If NameStr = '' Then NameStr := ChangeFileExt(FileNameStr, '');

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Sym);
    SchRegisterObject(SchDoc, Sym);

    { SheetFileName (link to the child .SchDoc) and SheetName (display label)
      are complex-text SUB-OBJECTS, not direct properties -- assigning
      Sym.SheetFileName raises "Property does not exist or is readonly". Set via
      GetState_SchSheetFileName/Name + SetState_Text, after the symbol is
      registered so the sub-objects exist (Altium SDK / UpdateSheetSymbolFN). }
    FNObj := Nil; Try FNObj := Sym.GetState_SchSheetFileName; Except End;
    If FNObj <> Nil Then FNObj.SetState_Text(FileNameStr);
    NMObj := Nil; Try NMObj := Sym.GetState_SchSheetName; Except End;
    If NMObj <> Nil Then NMObj.SetState_Text(NameStr);
    { The name and file name were left at the sheet origin, the bottom-left }
    { corner, since Location set by property does not carry them.           }
    SchAutopositionLabels(Sym, 'rotate', False);

    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1) + ','
        + '"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + ','
        + '"sheet_file_name":"' + EscapeJsonString(FileNameStr) + '",'
        + '"sheet_name":"' + EscapeJsonString(NameStr) + '"}');
End;

{..............................................................................}
{ Place a sheet entry on a sheet symbol.                                      }
{ Params: sheet_name (name of target ISch_SheetSymbol), entry_name,           }
{         io_type=Input|Output|Bidirectional|Unspecified,                     }
{         side=Left|Right|Top|Bottom, distance_from_top (mils),               }
{         style=None|Left|Right|LeftRight                                     }
{..............................................................................}

Function Gen_PlaceSheetEntry(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Sym : ISch_SheetSymbol;
    Entry : ISch_SheetEntry;
    SheetNameStr, EntryName, IOStr, SideStr, ThisName : String;
    WantSide, GotSide : Integer;
    GotX, GotY, EdgeX, EdgeY : Double;
    GotDist : Double;
    DistFromTop : Double;
    Found, Placed : Boolean;
    FontPick, FontRead : Integer;
Begin
    SheetNameStr := ExtractJsonValue(Params, 'sheet_name');
    EntryName := ExtractJsonValue(Params, 'entry_name');
    IOStr := LowerCase(ExtractJsonValue(Params, 'io_type'));
    SideStr := LowerCase(ExtractJsonValue(Params, 'side'));
    DistFromTop := StrToFloatDef(ExtractJsonValue(Params, 'distance_from_top'), 100);

    If (SheetNameStr = '') Or (EntryName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'sheet_name and entry_name are required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { Locate the target sheet symbol by its SheetName.                          }
    { ISch_SheetSymbol.SheetName is a COMPOUND sub-object (ISch_SheetName),     }
    { not a String -- see Generic.pas:83-97. Accessing it directly returns an   }
    { interface reference; comparing that to a String raises "Invalid variant   }
    { operation" at runtime. The actual text is on the .Text property of the   }
    { sub-object. ISch_SheetFileName has the same shape; both must be          }
    { dereferenced through .Text before any string operation.                  }
    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSheetSymbol));
    Try
        Sym := Iterator.FirstSchObject;
        While Sym <> Nil Do
        Begin
            ThisName := '';
            Try
                If Sym.SheetName <> Nil Then
                    ThisName := Sym.SheetName.Text;
            Except End;
            { A repeated sheet is named Repeat(CH,1,4); its plain name finds it. }
            If (ThisName = SheetNameStr)
                Or (Pos(UpperCase('Repeat(' + SheetNameStr + ','),
                        UpperCase(StringReplace(ThisName, ' ', '', -1))) = 1) Then
            Begin
                Found := True;
                Break;
            End;
            Sym := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Sheet symbol with SheetName "' + SheetNameStr + '" not found');
        Exit;
    End;

    FontPick := NewObjectFontId(SchDoc, eSheetEntry, Params);
    Entry := SchServer.SchObjectFactory(eSheetEntry, eCreate_Default);
    If Entry = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create sheet entry');
        Exit;
    End;

    Entry.Name := EntryName;
    Entry.DistanceFromTop := MilsToCoordF(DistFromTop);

    If IOStr = 'input' Then Entry.IOType := ePortInput
    Else If IOStr = 'output' Then Entry.IOType := ePortOutput
    Else If IOStr = 'bidirectional' Then Entry.IOType := ePortBidirectional
    Else Entry.IOType := ePortUnspecified;

    { Kept in a local so the same value can be re-asserted after the add
      and compared against what the entry ended up with. }
    If SideStr = 'right' Then WantSide := eRightSide
    Else If SideStr = 'top' Then WantSide := eTopSide
    Else If SideStr = 'bottom' Then WantSide := eBottomSide
    Else WantSide := eLeftSide;
    Entry.Side := WantSide;

    { AddAndPositionSchObject POSITIONS IT ITSELF, and in doing so discards
      the Side and DistanceFromTop set above.

      MEASURED on a live sheet, across 90 entries: every one ignored
      distance_from_top and side. They stacked at a fixed 50 mil pitch in
      PLACEMENT ORDER, restarting per symbol, at negative Y, which puts
      them below the sheet origin instead of on the symbol body. The
      comment that used to sit here claimed the opposite, that the call
      computes the position from Side and DistanceFromTop against the
      symbol's bounds. It does not.

      Same shape as AddSchComponent overriding LibReference: the add is
      what decides, so anything set before it has to be set again after.
      Re-asserted below, then read back. }
    SchBeginEdit(SchDoc, '');
    Sym.AddAndPositionSchObject(Entry);

    Try Entry.Side := WantSide; Except NoteWriteFailed('SheetEntry.Side'); End;
    Try Entry.DistanceFromTop := MilsToCoordF(DistFromTop); Except NoteWriteFailed('SheetEntry.DistanceFromTop'); End;
    If FontPick > 0 Then
    Begin
        Try Entry.TextFontID := FontPick; Except NoteWriteFailed('SheetEntry.TextFontID'); End;
    End;
    { Added this way the entry keeps a Location off the body until the  }
    { sheet is reloaded, and a wire drawn meanwhile goes to that point. }
    { So its point on the edge is worked out here: the symbol's Location }
    { is its top-left corner, the body runs right and down.              }
    EdgeX := CoordToMilsF(Sym.Location.X);
    EdgeY := CoordToMilsF(Sym.Location.Y);
    If WantSide = eLeftSide Then EdgeY := EdgeY - DistFromTop;
    If WantSide = eRightSide Then
    Begin
        EdgeX := EdgeX + CoordToMilsF(Sym.XSize);
        EdgeY := EdgeY - DistFromTop;
    End;
    If WantSide = eTopSide Then EdgeX := EdgeX + DistFromTop;
    If WantSide = eBottomSide Then
    Begin
        EdgeX := EdgeX + DistFromTop;
        EdgeY := EdgeY - CoordToMilsF(Sym.YSize);
    End;
    Try Entry.Location := Point(MilsToCoordF(EdgeX), MilsToCoordF(EdgeY)); Except NoteWriteFailed('SheetEntry.Location'); End;

    SchRegisterObject(Sym, Entry);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { READ BACK. placed:true and an echo of the requested side was the
      whole reply, so a caller asking for the left edge at 300 mils was
      told it had happened whatever the entry actually did. }
    GotSide := -1;
    GotDist := -1;
    Try GotSide := Entry.Side; Except End;
    Try GotDist := CoordToMilsF(Entry.DistanceFromTop); Except End;
    GotX := 0;
    GotY := 0;
    Try
        GotX := CoordToMilsF(Entry.Location.X);
        GotY := CoordToMilsF(Entry.Location.Y);
    Except End;

    FontRead := 0;
    Try FontRead := Entry.TextFontID; Except End;
    Placed := (GotSide = WantSide) And (Abs(GotDist - DistFromTop) < 0.0001)
        And (Abs(GotX - EdgeX) < 0.01) And (Abs(GotY - EdgeY) < 0.01);

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonBool('placed', True) + ',' +
            JsonBool('positioned_as_asked', Placed) + ',' +
            JsonStr('sheet_name', SheetNameStr) + ',' +
            JsonStr('entry_name', EntryName) + ',' +
            JsonStr('io_type', IOStr) + ',' +
            JsonStr('requested_side', SideStr) + ',' +
            JsonFloat('requested_distance_from_top', DistFromTop) + ',' +
            JsonInt('actual_side', GotSide) + ',' +
            JsonInt('font_id', FontRead) + ',' +
            JsonFloat('actual_distance_from_top', GotDist) + ',' +
            JsonFloat('x', GotX) + ',' +
            JsonFloat('y', GotY) + ',' +
            JsonStr('note', 'x and y are read back from the placed entry. '
                + 'If positioned_as_asked is false the entry exists but sits '
                + 'where Altium put it; obj_modify on eSheetEntry can set '
                + 'Location.X and Location.Y absolutely.')
        ));
End;

{..............................................................................}
{ Place a bus entry (45 deg stub) between a bus line and a wire.                 }
{ ISch_BusEntry inherits ISch_Line, so it accepts Location + Corner.          }
{ Params: x1,y1,x2,y2 in mils                                                 }
{..............................................................................}

{..............................................................................}
{ Harness wiring diagrams. Each harness object names itself in its           }
{ description ("Harness Wire W1", "Harness Pin P1-1 (1) Passive", "Harness   }
{ Shield (x,y)(x,y)"); a wire names what its ends and groupings touch only   }
{ by unique id, so the reader maps every id to a kind and a name first.      }
{..............................................................................}
Function HarnessKindName(Id : Integer) : String;
Begin
    Result := '';
    If Id = eHarnessWire Then Result := 'wire'
    Else If Id = eHarnessPin Then Result := 'pin'
    Else If Id = eHarnessComponent Then Result := 'component'
    Else If Id = eHarnessGenericComponent Then Result := 'generic_component'
    Else If Id = eHarnessSplice Then Result := 'splice'
    Else If Id = eHarnessCable Then Result := 'cable'
    Else If Id = eHarnessTwist Then Result := 'twist'
    Else If Id = eHarnessShield Then Result := 'shield'
    Else If Id = eHarnessWireLabel Then Result := 'wire_label'
    Else If Id = eHarnessNoConnect Then Result := 'no_connect'
    Else If Id = eHarnessCavity Then Result := 'cavity'
    Else If Id = eHarnessWireBreak Then Result := 'wire_break'
    Else If Id = eHarnessCovering Then Result := 'covering'
    Else If Id = eHarnessBundle Then Result := 'bundle';
End;

{ An Altium colour (blue in the high byte) as #RRGGBB. }
Function HarnessColourHex(C : Integer) : String;
Begin
    Result := '#' + IntToHex(C Mod 256, 2) + IntToHex((C Div 256) Mod 256, 2)
        + IntToHex((C Div 65536) Mod 256, 2);
End;

{ "kind:name" for a unique id, from the map built over the document. }
Function HarnessRef(Names : TStringList; Uid : String) : String;
Begin
    Result := '';
    If Uid = '' Then Exit;
    Result := Names.Values[Uid];
    If Result = '' Then Result := 'unknown:' + Uid;
End;

{ The objects of one grouping kind a harness wire belongs to, as a JSON list. }
Function HarnessRefList(Names : TStringList; Wire : ISch_GraphicalObject; Kind : String) : String;
Var
    I, Count : Integer;
    Uid : String;
Begin
    Result := '';
    Count := 0;
    Try
        If Kind = 'splice' Then Count := Wire.GetState_ConnectedInlineSplicesUniqueIdsCount
        Else If Kind = 'label' Then Count := Wire.GetState_ConnectedWireLabelsUniqueIdsCount
        Else If Kind = 'shield' Then Count := Wire.GetState_ConnectedShieldsUniqueIdsCount
        Else If Kind = 'twist' Then Count := Wire.GetState_ConnectedTwistsUniqueIdsCount
        Else If Kind = 'cable' Then Count := Wire.GetState_ConnectedCablesUniqueIdsCount;
    Except Count := 0; End;
    For I := 0 To Count - 1 Do
    Begin
        Uid := '';
        Try
            If Kind = 'splice' Then Uid := Wire.GetState_ConnectedInlineSpliceUniqueId(I)
            Else If Kind = 'label' Then Uid := Wire.GetState_ConnectedWireLabelUniqueId(I)
            Else If Kind = 'shield' Then Uid := Wire.GetState_ConnectedShieldUniqueId(I)
            Else If Kind = 'twist' Then Uid := Wire.GetState_ConnectedTwistUniqueId(I)
            Else If Kind = 'cable' Then Uid := Wire.GetState_ConnectedCableUniqueId(I);
        Except Uid := ''; End;
        If Uid = '' Then Continue;
        If Result <> '' Then Result := Result + ',';
        Result := Result + '"' + EscapeJsonString(HarnessRef(Names, Uid)) + '"';
    End;
    Result := '[' + Result + ']';
End;

{..............................................................................}
{ Gen_GetHarness - the harness of a wiring diagram: its components, pins,    }
{ splices, cables, twists, shields and no-connects by name, and each wire    }
{ with its points, colours, what its two ends meet and the splices, labels,  }
{ shields, twists and cables it belongs to. Params: path (optional; default  }
{ the document in front), limit (wires, default 500).                         }
{..............................................................................}
Function Gen_GetHarness(Params : String; RequestId : String) : String;
Var
    Doc : ISch_Document;
    It : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Path, Kind, Name, Uid, Desc, Groups, Wires, Pts, Colours, End1, End2 : String;
    Names : TStringList;
    I, N, Limit, Listed, WireCount, Unnamed : Integer;
Begin
    Path := Trim(ExtractJsonValue(Params, 'path'));
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 500);
    If Limit < 1 Then Limit := 500;
    Doc := Nil;
    If Path <> '' Then
    Begin
        Try Doc := SchServer.GetSchDocumentByPath(Path); Except Doc := Nil; End;
    End
    Else
    Begin
        Try Doc := SchServer.GetCurrentSchDocument; Except Doc := Nil; End;
    End;
    If Doc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT',
            'No open wiring diagram or schematic ' + Path);
        Exit;
    End;

    { Pass 1: every harness object's unique id to kind:name, and the list. }
    Names := TStringList.Create;
    Groups := '';
    Unnamed := 0;
    WireCount := 0;
    It := Doc.SchIterator_Create;
    Try
        Obj := It.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Kind := HarnessKindName(Obj.ObjectId);
            If Kind <> '' Then
            Begin
                Desc := '';
                Try Desc := Obj.GetState_DescriptionString; Except End;
                Name := HarnessNameFromDescription(Desc);
                If Name = '' Then
                Begin
                    Inc(Unnamed);
                    Name := Kind + IntToStr(Unnamed);
                End;
                Uid := '';
                Try Uid := Obj.UniqueId; Except End;
                If Uid <> '' Then Names.Add(Uid + '=' + Kind + ':' + Name);
                If Kind = 'wire' Then Inc(WireCount)
                Else
                Begin
                    If Groups <> '' Then Groups := Groups + ',';
                    Groups := Groups + JsonObj(JsonStr('kind', Kind) + ',' + JsonStr('name', Name));
                End;
            End;
            Obj := It.NextSchObject;
        End;
    Finally
        Doc.SchIterator_Destroy(It);
    End;

    { Pass 2: the wires. }
    Wires := '';
    Listed := 0;
    It := Doc.SchIterator_Create;
    Try
        It.AddFilter_ObjectSet(MkSet(eHarnessWire));
        Obj := It.FirstSchObject;
        While (Obj <> Nil) And (Listed < Limit) Do
        Begin
            Desc := '';
            Try Desc := Obj.GetState_DescriptionString; Except End;
            Pts := '';
            N := 0;
            Try N := Obj.VerticesCount; Except N := 0; End;
            For I := 1 To N Do
            Begin
                If Pts <> '' Then Pts := Pts + ',';
                Pts := Pts + '[' + FloatToJsonStr(CoordToMilsF(Obj.GetState_Vertex(I).X)) + ','
                    + FloatToJsonStr(CoordToMilsF(Obj.GetState_Vertex(I).Y)) + ']';
            End;
            Colours := JsonStr('primary', '');
            Try Colours := JsonStr('primary', Obj.GetState_PrimaryColorName); Except End;
            Try Colours := Colours + ',' + JsonStr('secondary', Obj.GetState_SecondaryColorName); Except End;
            Try Colours := Colours + ',' + JsonStr('tertiary', Obj.GetState_TertiaryColorName); Except End;
            Try Colours := Colours + ',' + JsonStr('border', Obj.GetState_BorderColorName); Except End;
            Try Colours := Colours + ',' + JsonStr('primary_rgb', HarnessColourHex(Obj.Color)); Except End;
            Try Colours := Colours + ',' + JsonStr('secondary_rgb', HarnessColourHex(Obj.GetState_SecondaryColor)); Except End;
            Try Colours := Colours + ',' + JsonStr('tertiary_rgb', HarnessColourHex(Obj.GetState_TertiaryColor)); Except End;
            Try Colours := Colours + ',' + JsonStr('border_rgb', HarnessColourHex(Obj.GetState_BorderColor)); Except End;
            End1 := '';
            End2 := '';
            Try End1 := HarnessRef(Names, Obj.GetState_EndVertex1ConnectedObjectUniqueId); Except End;
            Try End2 := HarnessRef(Names, Obj.GetState_EndVertex2ConnectedObjectUniqueId); Except End;
            If Wires <> '' Then Wires := Wires + ',';
            Wires := Wires + JsonObj(
                JsonStr('name', HarnessNameFromDescription(Desc)) + ','
                + JsonRaw('points', '[' + Pts + ']') + ','
                + JsonRaw('colours', JsonObj(Colours)) + ','
                + JsonStr('end1', End1) + ','
                + JsonStr('end2', End2) + ','
                + JsonRaw('splices', HarnessRefList(Names, Obj, 'splice')) + ','
                + JsonRaw('labels', HarnessRefList(Names, Obj, 'label')) + ','
                + JsonRaw('shields', HarnessRefList(Names, Obj, 'shield')) + ','
                + JsonRaw('twists', HarnessRefList(Names, Obj, 'twist')) + ','
                + JsonRaw('cables', HarnessRefList(Names, Obj, 'cable')));
            Inc(Listed);
            Obj := It.NextSchObject;
        End;
    Finally
        Doc.SchIterator_Destroy(It);
    End;
    Names.Free;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('document', Doc.DocumentName) + ','
        + JsonInt('wire_count', WireCount) + ','
        + JsonInt('wires_listed', Listed) + ','
        + JsonBool('truncated', Listed < WireCount) + ','
        + JsonRaw('objects', '[' + Groups + ']') + ','
        + JsonRaw('wires', '[' + Wires + ']')));
End;

{ The harness wiring diagram at Path, or the document in front. Nil, with  }
{ Why set, when there is none or it is not a wiring diagram: harness      }
{ objects on an ordinary sheet are refused rather than drawn.             }
Function HarnessDiagramFor(Path : String; Var Why : String) : ISch_Document;
Var
    SrvDoc : IServerDocument;
Begin
    Result := Nil;
    Why := '';
    If Path <> '' Then
    Begin
        Try Result := SchServer.GetSchDocumentByPath(Path); Except Result := Nil; End;
    End
    Else
    Begin
        Try Result := SchServer.GetCurrentSchDocument; Except Result := Nil; End;
    End;
    If Result = Nil Then
    Begin
        Why := 'No open wiring diagram ' + Path;
        Exit;
    End;
    SrvDoc := Nil;
    Try SrvDoc := Client.GetDocumentByPath(Result.DocumentName); Except SrvDoc := Nil; End;
    If (SrvDoc = Nil) Or (UpperCase(SrvDoc.Kind) <> 'HARNESSWIRINGDIAGRAM') Then
    Begin
        Why := Result.DocumentName + ' is not a harness wiring diagram (.WirDoc)';
        Result := Nil;
    End;
End;

{ #RRGGBB as an Altium colour (red in the low byte); -1 when it is not one. }
Function HarnessColourFromHex(S : String) : Integer;
Var
    Digits : String;
    I, V, D : Integer;
Begin
    Result := -1;
    S := UpperCase(Trim(S));
    If Copy(S, 1, 1) = '#' Then S := Copy(S, 2, Length(S));
    If Length(S) <> 6 Then Exit;
    Digits := '0123456789ABCDEF';
    V := 0;
    For I := 1 To 6 Do
    Begin
        D := Pos(Copy(S, I, 1), Digits) - 1;
        If D < 0 Then Exit;
        V := V * 16 + D;
    End;
    { V is RRGGBB; Altium keeps BBGGRR. }
    Result := (V Div 65536) + ((V Div 256) Mod 256) * 256 + (V Mod 256) * 65536;
End;

{..............................................................................}
{ Gen_PlaceHarnessConnectors - put library parts on a harness wiring diagram  }
{ as harness connectors. A part placed the ordinary way lands as a plain      }
{ schematic component, which harness wires cannot meet, so each part is       }
{ loaded from its library and copied into a new harness component: its        }
{ graphics and models as they are, each pin as a harness pin with the same     }
{ position, orientation, length, number, name and type, and its designator    }
{ and comment where the library put them.                                     }
{ Params: path (optional), connectors = 'lib_reference=..;library_path=..;    }
{ designator=..;x=..;y=..;rotation=0|90|180|270~~...'                         }
{..............................................................................}
Function Gen_PlaceHarnessConnectors(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    SrvDoc : IServerDocument;
    Src, Comp : ISch_Component;
    It : ISch_Iterator;
    Child, Dup, Pin : ISch_GraphicalObject;
    Remaining, Op, LibRef, LibPath, Desig, Why, Problem, Rows, Pins : String;
    X, Y : Double;
    Rotation, NPins, Placed, Failed, Total : Integer;
Begin
    SchDoc := HarnessDiagramFor(Trim(ExtractJsonValue(Params, 'path')), Why);
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_A_WIRING_DIAGRAM', Why);
        Exit;
    End;
    Remaining := ExtractJsonValue(Params, 'connectors');
    Rows := '';
    Placed := 0;
    Failed := 0;
    Total := 0;
    While True Do
    Begin
        Op := NextBatchOp(Remaining);
        If Op = '' Then Break;
        Inc(Total);
        LibRef := GetBatchField(Op, 'lib_reference');
        LibPath := SafeSchLibPath(GetBatchField(Op, 'library_path'));
        Desig := GetBatchField(Op, 'designator');
        X := StrToFloatDef(GetBatchField(Op, 'x'), 0);
        Y := StrToFloatDef(GetBatchField(Op, 'y'), 0);
        Rotation := StrToIntDef(GetBatchField(Op, 'rotation'), 0);
        Problem := '';
        Pins := '';
        Src := Nil;
        If LibPath <> '' Then
        Begin
            Try Src := SchServer.LoadComponentFromLibrary(LibRef, LibPath); Except Src := Nil; End;
        End;
        If (Src = Nil) And (LibPath <> '') Then
        Begin
            { A library on disk but not open answers Nil; open it and retry, }
            { then put the diagram back in front.                            }
            If FocusSchLib(LibPath) <> Nil Then
            Begin
                Try Src := SchServer.LoadComponentFromLibrary(LibRef, LibPath); Except Src := Nil; End;
            End;
            Try
                SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
                If SrvDoc <> Nil Then Client.ShowDocument(SrvDoc);
            Except End;
        End;
        Comp := Nil;
        If LibPath = '' Then Problem := 'library_path is not a .SchLib'
        Else If Src = Nil Then Problem := 'no part ' + LibRef + ' in ' + LibPath
        Else
        Begin
            Try Comp := SchServer.SchObjectFactory(eHarnessComponent, eCreate_Default); Except Comp := Nil; End;
            If Comp = Nil Then Problem := 'Altium did not create a harness component';
        End;
        If Comp <> Nil Then
        Begin
            Try Comp.Location := Src.Location; Except NoteWriteFailed('HarnessComponent.Location'); End;
            Try Comp.LibReference := Src.LibReference; Except NoteWriteFailed('HarnessComponent.LibReference'); End;
            Try Comp.SourceLibraryName := Src.SourceLibraryName; Except NoteWriteFailed('HarnessComponent.SourceLibraryName'); End;
            Try Comp.ComponentDescription := Src.ComponentDescription; Except NoteWriteFailed('HarnessComponent.ComponentDescription'); End;
            Try Comp.Designator.Text := Desig; Except NoteWriteFailed('HarnessComponent.Designator'); End;
            Try Comp.Designator.Location := Src.Designator.Location; Except NoteWriteFailed('HarnessComponent.Designator.Location'); End;
            Try Comp.Comment.Text := Src.Comment.Text; Except NoteWriteFailed('HarnessComponent.Comment'); End;
            Try Comp.Comment.Location := Src.Comment.Location; Except NoteWriteFailed('HarnessComponent.Comment.Location'); End;
            NPins := 0;
            It := Src.SchIterator_Create;
            Try
                It.AddFilter_ObjectSet(MkSet(ePin, eRectangle, eLine, ePolyline, eEllipse, eArc, eLabel, eImplementation));
                Child := It.FirstSchObject;
                While Child <> Nil Do
                Begin
                    If Child.ObjectId = ePin Then
                    Begin
                        Pin := Nil;
                        Try Pin := SchServer.SchObjectFactory(eHarnessPin, eCreate_Default); Except Pin := Nil; End;
                        If Pin <> Nil Then
                        Begin
                            Try
                                Pin.Location := Child.Location;
                                Pin.Orientation := Child.Orientation;
                                Pin.PinLength := Child.PinLength;
                                Pin.Designator := Child.Designator;
                                Pin.Name := Child.Name;
                                Pin.Electrical := Child.Electrical;
                                Pin.OwnerPartId := Child.OwnerPartId;
                                Comp.AddSchObject(Pin);
                                Inc(NPins);
                            Except Problem := 'a pin could not be copied'; End;
                        End;
                    End
                    Else
                    Begin
                        Dup := Nil;
                        Try Dup := Child.Replicate; Except Dup := Nil; End;
                        If Dup <> Nil Then
                        Begin
                            Try Comp.AddSchObject(Dup); Except NoteWriteFailed('HarnessComponent graphic'); End;
                        End;
                    End;
                    Child := It.NextSchObject;
                End;
            Finally
                Src.SchIterator_Destroy(It);
            End;
            SchBeginEdit(SchDoc, '');
            Try
                SchDoc.AddSchObject(Comp);
                Comp.MoveToXY(MilsToCoordF(X), MilsToCoordF(Y));
                If Rotation = 90 Then Comp.SetState_Orientation(1)
                Else If Rotation = 180 Then Comp.SetState_Orientation(2)
                Else If Rotation = 270 Then Comp.SetState_Orientation(3);
            Except Problem := 'Altium refused the connector on the diagram'; End;
            SchEndEdit(SchDoc, 'Edit');
            { Where each pin's wire end must sit, read after the move. }
            It := Comp.SchIterator_Create;
            Try
                It.AddFilter_ObjectSet(MkSet(eHarnessPin));
                Child := It.FirstSchObject;
                While Child <> Nil Do
                Begin
                    If Pins <> '' Then Pins := Pins + ',';
                    Pins := Pins + JsonObj(JsonStr('pin', Child.Designator) + ','
                        + JsonStr('name', Child.Name) + ','
                        + JsonFloat('x', CoordToMilsF(PinEndX(Child.Location.X, Child.Orientation, Child.PinLength))) + ','
                        + JsonFloat('y', CoordToMilsF(PinEndY(Child.Location.Y, Child.Orientation, Child.PinLength))));
                    Child := It.NextSchObject;
                End;
            Finally
                Comp.SchIterator_Destroy(It);
            End;
            If NPins = 0 Then Problem := 'the part has no pins';
        End;
        If Problem = '' Then Inc(Placed) Else Inc(Failed);
        If Rows <> '' Then Rows := Rows + ',';
        Rows := Rows + JsonObj(JsonStr('designator', Desig) + ','
            + JsonStr('lib_reference', LibRef) + ','
            + JsonBool('placed', Problem = '') + ','
            + JsonStr('problem', Problem) + ','
            + JsonRaw('pin_ends', '[' + Pins + ']'));
    End;
    Try SchDoc.GraphicallyInvalidate; Except End;
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonInt('placed', Placed) + ','
        + JsonInt('failed', Failed) + ','
        + JsonInt('total', Total) + ','
        + JsonRaw('connectors', '[' + Rows + ']')));
End;

{..............................................................................}
{ Gen_PlaceHarnessWires - harness wires and splices on a wiring diagram.     }
{ A wire runs through its points; a splice sits where wires meet. Altium     }
{ links a wire's ends to the pins and splices they touch when it loads the   }
{ diagram, not when the wire is added, so the caller saves and reopens it.   }
{ Params: path (optional), wires = 'points=x,y,x,y,...;name=W1;colour=#RRGGBB }
{ ;colour_name=RD~~...', splices = 'x=..;y=..;name=SPL1~~...'.               }
{..............................................................................}
Function Gen_PlaceHarnessWires(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Obj : ISch_GraphicalObject;
    Remaining, Op, Name, Why, Problem, Rows, Colour, ColourName : String;
    Xs, Ys : TStringList;
    I, Rgb, Placed, Failed, Total : Integer;
Begin
    SchDoc := HarnessDiagramFor(Trim(ExtractJsonValue(Params, 'path')), Why);
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_A_WIRING_DIAGRAM', Why);
        Exit;
    End;
    Rows := '';
    Placed := 0;
    Failed := 0;
    Total := 0;
    SchBeginEdit(SchDoc, '');
    Try
        Remaining := ExtractJsonValue(Params, 'wires');
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            Inc(Total);
            Name := GetBatchField(Op, 'name');
            Colour := GetBatchField(Op, 'colour');
            ColourName := GetBatchField(Op, 'colour_name');
            Problem := '';
            Obj := Nil;
            Xs := TStringList.Create;
            Ys := TStringList.Create;
            If Not ParseVertexList(GetBatchField(Op, 'points'), Xs, Ys) Then Problem := 'points are x,y pairs'
            Else If Xs.Count < 2 Then Problem := 'a wire needs two points or more';
            Rgb := -1;
            If (Problem = '') And (Colour <> '') Then
            Begin
                Rgb := HarnessColourFromHex(Colour);
                If Rgb < 0 Then Problem := 'colour is #RRGGBB';
            End;
            If Problem = '' Then
            Begin
                Try Obj := SchServer.SchObjectFactory(eHarnessWire, eCreate_Default); Except Obj := Nil; End;
                If Obj = Nil Then Problem := 'Altium did not create a harness wire';
            End;
            If Obj <> Nil Then
            Begin
                Try
                    Obj.Location := Point(MilsToCoordF(StrToFloatDef(Xs[0], 0)), MilsToCoordF(StrToFloatDef(Ys[0], 0)));
                    For I := 0 To Xs.Count - 1 Do
                    Begin
                        Obj.InsertVertex := I + 1;
                        Obj.SetState_Vertex(I + 1, Point(MilsToCoordF(StrToFloatDef(Xs[I], 0)),
                            MilsToCoordF(StrToFloatDef(Ys[I], 0))));
                    End;
                    If Rgb >= 0 Then Obj.Color := Rgb;
                    If ColourName <> '' Then Obj.SetState_PrimaryColorName(ColourName);
                    SchDoc.RegisterSchObjectInContainer(Obj);
                    SchRegisterObject(SchDoc, Obj);
                    If Name <> '' Then Obj.GetState_SchDesignator.Text := Name;
                Except Problem := 'Altium refused the wire'; End;
            End;
            Xs.Free;
            Ys.Free;
            If Problem = '' Then Inc(Placed) Else Inc(Failed);
            If Rows <> '' Then Rows := Rows + ',';
            Rows := Rows + JsonObj(JsonStr('kind', 'wire') + ',' + JsonStr('name', Name) + ','
                + JsonBool('placed', Problem = '') + ',' + JsonStr('problem', Problem));
        End;
        Remaining := ExtractJsonValue(Params, 'splices');
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            Inc(Total);
            Name := GetBatchField(Op, 'name');
            Problem := '';
            Obj := Nil;
            Try Obj := SchServer.SchObjectFactory(eHarnessSplice, eCreate_Default); Except Obj := Nil; End;
            If Obj = Nil Then Problem := 'Altium did not create a splice'
            Else
            Begin
                Try
                    Obj.Location := Point(MilsToCoordF(StrToFloatDef(GetBatchField(Op, 'x'), 0)),
                        MilsToCoordF(StrToFloatDef(GetBatchField(Op, 'y'), 0)));
                    SchDoc.RegisterSchObjectInContainer(Obj);
                    SchRegisterObject(SchDoc, Obj);
                    If Name <> '' Then Obj.GetState_SchDesignator.Text := Name;
                Except Problem := 'Altium refused the splice'; End;
            End;
            If Problem = '' Then Inc(Placed) Else Inc(Failed);
            If Rows <> '' Then Rows := Rows + ',';
            Rows := Rows + JsonObj(JsonStr('kind', 'splice') + ',' + JsonStr('name', Name) + ','
                + JsonBool('placed', Problem = '') + ',' + JsonStr('problem', Problem));
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
    End;
    Try SchDoc.GraphicallyInvalidate; Except End;
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('document', SchDoc.DocumentName) + ','
        + JsonInt('placed', Placed) + ','
        + JsonInt('failed', Failed) + ','
        + JsonInt('total', Total) + ','
        + JsonRaw('items', '[' + Rows + ']')));
End;

Function Gen_PlaceBusEntry(Params : String; RequestId : String) : String;
Var
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Entry : ISch_BusEntry;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Entry := SchServer.SchObjectFactory(eBusEntry, eCreate_Default);
    If Entry = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create bus entry');
        Exit;
    End;

    Entry.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    Entry.Corner := Point(MilsToCoordF(X2), MilsToCoordF(Y2));

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Entry);
    SchRegisterObject(SchDoc, Entry);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1)
        + ',"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + '}');
End;

{..............................................................................}
{ Set the sheet size / template style of the active schematic.                }
{ Params: style (e.g. A, B, C, A0, A1, A2, A3, A4, Letter, Legal, Custom),   }
{         custom_width, custom_height (in mils, only used with Custom)        }
{..............................................................................}

Function Gen_SetSheetSize(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    StyleStr, OrientStr, Data : String;
    CustomW, CustomH : Double;
Begin
    StyleStr := UpperCase(ExtractJsonValue(Params, 'style'));
    CustomW := StrToFloatDef(ExtractJsonValue(Params, 'custom_width'), 0);
    CustomH := StrToFloatDef(ExtractJsonValue(Params, 'custom_height'), 0);
    OrientStr := LowerCase(ExtractJsonValue(Params, 'orientation'));

    If (StyleStr = '') And (OrientStr = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'style or orientation required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    SchBeginEdit(SchDoc, '');
    Try
        If OrientStr = 'landscape' Then
        Begin
            Try SchDoc.WorkspaceOrientation := eLandscape; Except NoteWriteFailed('sheet orientation'); End;
        End
        Else If OrientStr = 'portrait' Then
        Begin
            Try SchDoc.WorkspaceOrientation := ePortrait; Except NoteWriteFailed('sheet orientation'); End;
        End;

        If StyleStr = '' Then
        Begin
            SchDoc.GraphicallyInvalidate;
            MarkDocDirtyByPath(SchDoc.DocumentName);
            Result := BuildSuccessResponse(RequestId,
                '{"success":true,"orientation":"' + EscapeJsonString(OrientStr) + '"}');
            Exit;
        End;

        If StyleStr = 'A' Then SchDoc.SheetStyle := eSheetA
        Else If StyleStr = 'B' Then SchDoc.SheetStyle := eSheetB
        Else If StyleStr = 'C' Then SchDoc.SheetStyle := eSheetC
        Else If StyleStr = 'D' Then SchDoc.SheetStyle := eSheetD
        Else If StyleStr = 'E' Then SchDoc.SheetStyle := eSheetE
        Else If StyleStr = 'A4' Then SchDoc.SheetStyle := eSheetA4
        Else If StyleStr = 'A3' Then SchDoc.SheetStyle := eSheetA3
        Else If StyleStr = 'A2' Then SchDoc.SheetStyle := eSheetA2
        Else If StyleStr = 'A1' Then SchDoc.SheetStyle := eSheetA1
        Else If StyleStr = 'A0' Then SchDoc.SheetStyle := eSheetA0
        Else If StyleStr = 'LETTER' Then SchDoc.SheetStyle := eSheetLetter
        Else If StyleStr = 'LEGAL' Then SchDoc.SheetStyle := eSheetLegal
        Else If StyleStr = 'TABLOID' Then SchDoc.SheetStyle := eSheetTabloid
        Else If StyleStr = 'CUSTOM' Then
        Begin
            { There is no eSheetCustom: naming it halted the script with
              "Undeclared identifier". A custom size is a flag beside the
              style, the way Altium's own template scripts set it. }
            SchDoc.UseCustomSheet := True;
            If CustomW > 0 Then SchDoc.CustomX := MilsToCoordF(CustomW);
            If CustomH > 0 Then SchDoc.CustomY := MilsToCoordF(CustomH);
        End
        Else
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_STYLE',
                'Unknown sheet style: ' + StyleStr);
            Exit;
        End;
        { A named style is ignored while the custom flag is on. }
        If StyleStr <> 'CUSTOM' Then SchDoc.UseCustomSheet := False;
    Finally
        SchEndEdit(SchDoc, 'Edit');
    End;
    { Without it SheetSizeX/Y still read the previous size (measured on AD
      26.11); ExtractTemplate.pas calls it after the same writes. }
    SchDoc.UpdateDocumentProperties;
    SchDoc.GraphicallyInvalidate;
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Data := '{"success":true,"style":"' + EscapeJsonString(StyleStr) + '"';
    Try Data := Data + ',"use_custom":' + BoolToJsonStr(SchDoc.UseCustomSheet); Except End;
    Try Data := Data + ',"custom_x":' + FloatToJsonStr(CoordToMilsF(SchDoc.GetState_CustomX))
        + ',"custom_y":' + FloatToJsonStr(CoordToMilsF(SchDoc.GetState_CustomY)); Except End;
    Try Data := Data + ',"frame_x":' + FloatToJsonStr(CoordToMilsF(SchDoc.SheetSizeX))
        + ',"frame_y":' + FloatToJsonStr(CoordToMilsF(SchDoc.SheetSizeY)); Except End;
    Result := BuildSuccessResponse(RequestId, Data + '}');
End;

{..............................................................................}
{ SheetOptionsJson - the active sheet's zone, margin and border settings.      }
{..............................................................................}
Function SheetOptionsJson(SchDoc : ISch_Document) : String;
Var
    ZX, ZY, CZX, CZY, Border : Integer;
    Margin, CMargin : Double;
    RefZones, Graphics : Boolean;
Begin
    ZX := -1; ZY := -1; CZX := -1; CZY := -1; Border := -1;
    Margin := -1; CMargin := -1; RefZones := False; Graphics := False;
    Try ZX := SchDoc.GetState_SheetZonesX; Except End;
    Try ZY := SchDoc.GetState_SheetZonesY; Except End;
    Try CZX := SchDoc.GetState_CustomXZones; Except End;
    Try CZY := SchDoc.GetState_CustomYZones; Except End;
    Try Border := SchDoc.DocumentBorderStyle; Except End;
    Try Margin := CoordToMilsF(SchDoc.GetState_SheetMarginWidth); Except End;
    Try CMargin := CoordToMilsF(SchDoc.GetState_CustomMarginWidth); Except End;
    Try RefZones := SchDoc.GetState_ReferenceZonesOn; Except End;
    Try Graphics := SchDoc.GetState_ShowTemplateGraphics; Except End;
    Result := JsonObj(
        JsonInt('zones_x', ZX) + ',' + JsonInt('zones_y', ZY) + ','
        + JsonInt('custom_zones_x', CZX) + ',' + JsonInt('custom_zones_y', CZY) + ','
        + JsonFloat('margin_width_mils', Margin) + ','
        + JsonFloat('custom_margin_width_mils', CMargin) + ','
        + JsonInt('border_style', Border) + ','
        + JsonBool('reference_zones', RefZones) + ','
        + JsonBool('show_template_graphics', Graphics));
End;

{..............................................................................}
{ Gen_SheetOptions - read, and optionally change, the active sheet's zones,   }
{ margin, border style and template graphics: the SetState_ / GetState_ pairs }
{ on ISch_Document. Empty parameters are left alone;                          }
{ the answer is read back after the writes.                                    }
{ Params: zones_x, zones_y, custom_zones_x, custom_zones_y, margin_width,     }
{ custom_margin_width (mils), border_style, reference_zones,                   }
{ show_template_graphics ('true' / 'false').                                   }
{..............................................................................}
Function Gen_SheetOptions(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    ZX, ZY, CZX, CZY, Margin, CMargin, Border, RefZones, Graphics : String;
    Wrote : Boolean;
Begin
    ZX := ExtractJsonValue(Params, 'zones_x');
    ZY := ExtractJsonValue(Params, 'zones_y');
    CZX := ExtractJsonValue(Params, 'custom_zones_x');
    CZY := ExtractJsonValue(Params, 'custom_zones_y');
    Margin := ExtractJsonValue(Params, 'margin_width');
    CMargin := ExtractJsonValue(Params, 'custom_margin_width');
    Border := ExtractJsonValue(Params, 'border_style');
    RefZones := LowerCase(ExtractJsonValue(Params, 'reference_zones'));
    Graphics := LowerCase(ExtractJsonValue(Params, 'show_template_graphics'));
    SchDoc := SchServer.GetCurrentSchDocument;
    If (SchDoc = Nil) Or (SchDoc.ObjectId = eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic sheet is active');
        Exit;
    End;
    If ((ZX <> '') And (StrToIntDef(ZX, -1) < 1)) Or ((ZY <> '') And (StrToIntDef(ZY, -1) < 1))
        Or ((CZX <> '') And (StrToIntDef(CZX, -1) < 1)) Or ((CZY <> '') And (StrToIntDef(CZY, -1) < 1))
        Or ((Border <> '') And (StrToIntDef(Border, -1) < 0))
        Or ((Margin <> '') And (StrToFloatDef(Margin, -1) < 0))
        Or ((CMargin <> '') And (StrToFloatDef(CMargin, -1) < 0)) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_VALUE',
            'zones are whole numbers from 1; margins are mils from 0; border_style from 0');
        Exit;
    End;
    Wrote := (ZX <> '') Or (ZY <> '') Or (CZX <> '') Or (CZY <> '') Or (Margin <> '')
        Or (CMargin <> '') Or (Border <> '') Or (RefZones <> '') Or (Graphics <> '');
    If Wrote Then
    Begin
        SchBeginEdit(SchDoc, '');
        Try
            If ZX <> '' Then SchDoc.SetState_SheetZonesX(StrToIntDef(ZX, 4));
            If ZY <> '' Then SchDoc.SetState_SheetZonesY(StrToIntDef(ZY, 4));
            If CZX <> '' Then SchDoc.SetState_CustomXZones(StrToIntDef(CZX, 6));
            If CZY <> '' Then SchDoc.SetState_CustomYZones(StrToIntDef(CZY, 4));
            If Margin <> '' Then SchDoc.SetState_SheetMarginWidth(MilsToCoordF(StrToFloatDef(Margin, 0)));
            If CMargin <> '' Then SchDoc.SetState_CustomMarginWidth(MilsToCoordF(StrToFloatDef(CMargin, 0)));
            If Border <> '' Then SchDoc.SetState_DocumentBorderStyle(StrToIntDef(Border, 0));
            If RefZones <> '' Then SchDoc.SetState_ReferenceZonesOn(RefZones = 'true');
            If Graphics <> '' Then SchDoc.SetState_ShowTemplateGraphics(Graphics = 'true');
        Finally
            SchEndEdit(SchDoc, 'Edit');
        End;
        SchDoc.UpdateDocumentProperties;
        SchDoc.GraphicallyInvalidate;
        MarkDocDirtyByPath(SchDoc.DocumentName);
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('sheet', SchDoc.DocumentName) + ','
        + JsonBool('changed', Wrote) + ','
        + JsonRaw('options', SheetOptionsJson(SchDoc))));
End;


{..............................................................................}
{ Gen_SheetTemplate - the sheet template (.SchDot) of the active sheet: read  }
{ it, set it, update it from its file, or remove it, with no dialog.          }
{                                                                              }
{ Params: action = get (default) / set / update / remove; template_file (set: }
{ the .SchDot to apply; update: optional, else the sheet's own file); vault   }
{ ('true': also read the vault link the sheet carries).                       }
{                                                                              }
{ SET and UPDATE run Altium's own Sch:ChangeCurrentTemplate with              }
{ TemplateSource=Local, FileName and Silent=True. MEASURED (AD 25.8.1): that  }
{ form applies the template with no dialog (sheet size, title block off, the  }
{ template graphics, parameters kept). Its menu forms, Sch:UpdateCurrentTemplate }
{ and Sch:RemoveTemplate, always ask for a scope and then report in a modal,  }
{ which blocks the loop. A process reports nothing, so every action is read   }
{ back: the template file name and the template objects on the sheet.         }
{                                                                              }
{ REMOVE does what Sch:RemoveTemplate does without its dialogs: the template  }
{ object (eTemplate, the container of the border / title block graphics, as   }
{ ExtractTemplate.pas finds it) leaves the sheet and the file name is         }
{ cleared. Sheet size and the standard title block flag stay as they are.     }
{..............................................................................}

Function SheetTemplateObject(SchDoc : ISch_Document) : ISch_GraphicalObject;
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
Begin
    Result := Nil;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.SetState_IterationDepth(eIterateFirstLevel);
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.ObjectId = eTemplate Then
            Begin
                Result := Obj;
                Break;
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
End;

Function SheetTemplateCount(SchDoc : ISch_Document; Var Children : Integer) : Integer;
Var
    Iter, TplIter : ISch_Iterator;
    Obj, Child : ISch_GraphicalObject;
Begin
    Result := 0;
    Children := 0;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.SetState_IterationDepth(eIterateFirstLevel);
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.ObjectId = eTemplate Then
            Begin
                Inc(Result);
                TplIter := Obj.SchIterator_Create;
                Try
                    Child := TplIter.FirstSchObject;
                    While Child <> Nil Do
                    Begin
                        Inc(Children);
                        Child := TplIter.NextSchObject;
                    End;
                Finally
                    Obj.SchIterator_Destroy(TplIter);
                End;
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
End;

{ Each object the sheet's template draws, as its bounding box in mils:       }
{ [[x1,y1,x2,y2],...]. A converter takes out what lies in these and keeps    }
{ what the user drew.                                                        }
Function SheetTemplateBoxes(SchDoc : ISch_Document) : String;
Var
    Iter, TplIter : ISch_Iterator;
    Obj, Child : ISch_GraphicalObject;
    Box : String;
Begin
    Result := '';
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.SetState_IterationDepth(eIterateFirstLevel);
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.ObjectId = eTemplate Then
            Begin
                TplIter := Obj.SchIterator_Create;
                Try
                    Child := TplIter.FirstSchObject;
                    While Child <> Nil Do
                    Begin
                        Box := '';
                        Try
                            Box := '[' + FloatToJsonStr(CoordToMilsF(Child.BoundingRectangle.X1)) + ','
                                + FloatToJsonStr(CoordToMilsF(Child.BoundingRectangle.Y1)) + ','
                                + FloatToJsonStr(CoordToMilsF(Child.BoundingRectangle.X2)) + ','
                                + FloatToJsonStr(CoordToMilsF(Child.BoundingRectangle.Y2)) + ']';
                        Except
                            Box := '';
                        End;
                        If Box <> '' Then
                        Begin
                            If Result <> '' Then Result := Result + ',';
                            Result := Result + Box;
                        End;
                        Child := TplIter.NextSchObject;
                    End;
                Finally
                    Obj.SchIterator_Destroy(TplIter);
                End;
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
    Result := '[' + Result + ']';
End;

Function SheetTemplateFile(SchDoc : ISch_Document) : String;
Begin
    Result := '';
    Try Result := SchDoc.GetState_TemplateFileName; Except End;
End;

{ The vault link a sheet set from the server carries. Read only when asked: }
{ these names are in the script engine's table but not yet proven on        }
{ ISch_Document, and an unknown one stops the script.                        }
Function SheetTemplateVaultJson(SchDoc : ISch_Document) : String;
Var
    VaultGuid, ItemGuid, RevGuid, VaultHrid, RevHrid : String;
Begin
    VaultGuid := '';
    ItemGuid := '';
    RevGuid := '';
    VaultHrid := '';
    RevHrid := '';
    Try VaultGuid := SchDoc.TemplateVaultGUID; Except End;
    Try ItemGuid := SchDoc.TemplateItemGUID; Except End;
    Try RevGuid := SchDoc.TemplateRevisionGUID; Except End;
    Try VaultHrid := SchDoc.TemplateVaultHRID; Except End;
    Try RevHrid := SchDoc.TemplateRevisionHRID; Except End;
    Result := JsonObj(JsonStr('vault_guid', VaultGuid) + ','
        + JsonStr('item_guid', ItemGuid) + ','
        + JsonStr('revision_guid', RevGuid) + ','
        + JsonStr('vault_hrid', VaultHrid) + ','
        + JsonStr('revision_hrid', RevHrid));
End;

Function SheetTemplateJson(SchDoc : ISch_Document; Vault : Boolean) : String;
Var
    Count, Children : Integer;
    TitleBlock, Border : Boolean;
    Style : Integer;
Begin
    Count := SheetTemplateCount(SchDoc, Children);
    TitleBlock := False;
    Border := False;
    Style := -1;
    Try TitleBlock := SchDoc.TitleBlockOn; Except End;
    Try Border := SchDoc.BorderOn; Except End;
    Try Style := SchDoc.SheetStyle; Except End;
    Result := JsonStr('template_file', SheetTemplateFile(SchDoc)) + ','
        + JsonInt('template_objects', Count) + ','
        + JsonInt('template_graphics', Children) + ','
        + JsonInt('sheet_style', Style) + ','
        + JsonBool('title_block_on', TitleBlock) + ','
        + JsonBool('border_on', Border);
    If Vault Then
        Result := Result + ',' + JsonRaw('vault', SheetTemplateVaultJson(SchDoc));
End;

Function Gen_SheetTemplate(Params : String; RequestId : String) : String;
Var
    SchDoc, Front : ISch_Document;
    What, TplFile, Before, After, Data, SheetPath : String;
    Vault, Ok, Shown : Boolean;
    PrevView : IServerDocumentView;
    SheetDoc : IServerDocument;
    Tpl : ISch_GraphicalObject;
    Guard, Removed, Count, Children : Integer;
    LastAddr, ThisAddr : Integer;
Begin
    What := LowerCase(ExtractJsonValue(Params, 'action'));
    If What = '' Then What := 'get';
    TplFile := ExtractJsonValue(Params, 'template_file');
    Vault := LowerCase(ExtractJsonValue(Params, 'vault')) = 'true';
    If (What <> 'get') And (What <> 'set') And (What <> 'update') And (What <> 'remove') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_ACTION',
            'action is get, set, update or remove');
        Exit;
    End;
    { The named sheet, else the one in front. }
    SheetPath := ExtractJsonValue(Params, 'file_path');
    If SheetPath <> '' Then
    Begin
        SchDoc := SchDocByPathLoading(SheetPath);
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_FOUND',
                'No schematic sheet at ' + SheetPath);
            Exit;
        End;
    End
    Else
        SchDoc := SchServer.GetCurrentSchDocument;
    If (SchDoc = Nil) Or (SchDoc.ObjectId = eSchLib) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic sheet is active');
        Exit;
    End;
    Before := SheetTemplateFile(SchDoc);
    Shown := False;
    PrevView := Nil;
    Ok := True;
    Removed := 0;

    If (What = 'set') Or (What = 'update') Then
    Begin
        If (What = 'update') And (TplFile = '') Then TplFile := Before;
        If TplFile = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_TEMPLATE',
                'The sheet has no template to update; give template_file');
            Exit;
        End;
        If LowerCase(ExtractFileExt(TplFile)) <> '.schdot' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NOT_A_TEMPLATE',
                'template_file must be a .SchDot: ' + TplFile);
            Exit;
        End;
        If Not FileExists(TplFile) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'TEMPLATE_NOT_FOUND',
                'No such file: ' + TplFile);
            Exit;
        End;
        { Sch:ChangeCurrentTemplate acts on the sheet in front: a named    }
        { sheet behind another is brought forward for it, and the view the }
        { user had is put back afterwards.                                 }
        Front := SchServer.GetCurrentSchDocument;
        If (Front = Nil) Or (UpperCase(Front.DocumentName) <> UpperCase(SchDoc.DocumentName)) Then
        Begin
            Try PrevView := Client.GetCurrentView; Except PrevView := Nil; End;
            SheetDoc := Nil;
            Try SheetDoc := Client.GetDocumentByPath(SchDoc.DocumentName); Except End;
            If SheetDoc <> Nil Then
            Begin
                Try Client.ShowDocument(SheetDoc); Shown := True; Except End;
            End;
            Front := SchServer.GetCurrentSchDocument;
            If (Front = Nil) Or (UpperCase(Front.DocumentName) <> UpperCase(SchDoc.DocumentName)) Then
            Begin
                If (PrevView <> Nil) Then
                Begin
                    Try Client.ShowDocument(PrevView.OwnerDocument); Except End;
                End;
                Result := BuildErrorResponse(RequestId, 'SHEET_NOT_SHOWN',
                    'Could not bring ' + SchDoc.DocumentName + ' forward to apply the template');
                Exit;
            End;
        End;
        ResetParameters;
        AddStringParameter('TemplateSource', 'Local');
        AddStringParameter('FileName', TplFile);
        AddStringParameter('Silent', 'True');
        RunProcess('Sch:ChangeCurrentTemplate');
        If Shown And (PrevView <> Nil) Then
        Begin
            Try Client.ShowDocument(PrevView.OwnerDocument); Except End;
        End;
        SchDoc.GraphicallyInvalidate;
        If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
        After := SheetTemplateFile(SchDoc);
        Count := SheetTemplateCount(SchDoc, Children);
        Ok := (LowerCase(After) = LowerCase(TplFile)) And (Count = 1);
    End
    Else If What = 'remove' Then
    Begin
        SchBeginEdit(SchDoc, '');
        Try
            Guard := 50;
            LastAddr := 0;
            While Guard > 0 Do
            Begin
                Tpl := SheetTemplateObject(SchDoc);
                If Tpl = Nil Then Break;
                ThisAddr := 0;
                Try ThisAddr := Tpl.I_ObjectAddress; Except End;
                { The same template found again: the remove did nothing. }
                If (ThisAddr <> 0) And (ThisAddr = LastAddr) Then Break;
                LastAddr := ThisAddr;
                SchDoc.RemoveSchObject(Tpl);
                SchRegisterObject(SchDoc, Tpl);
                Inc(Removed);
                Dec(Guard);
            End;
            SchDoc.TemplateFileName := '';
        Finally
            SchEndEdit(SchDoc, 'Edit');
        End;
        SchDoc.UpdateDocumentProperties;
        SchDoc.GraphicallyInvalidate;
        If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
        Count := SheetTemplateCount(SchDoc, Children);
        After := SheetTemplateFile(SchDoc);
        Ok := (Count = 0) And (After = '');
    End;

    Data := JsonStr('sheet', SchDoc.DocumentName)
        + ',' + JsonStr('action', What)
        + ',' + JsonBool('applied', Ok)
        + ',' + JsonStr('template_before', Before);
    If What = 'remove' Then Data := Data + ',' + JsonInt('removed', Removed);
    Data := Data + ',' + SheetTemplateJson(SchDoc, Vault);
    If LowerCase(ExtractJsonValue(Params, 'boxes')) = 'true' Then
        Data := Data + ',' + JsonRaw('template_boxes', SheetTemplateBoxes(SchDoc));
    Result := BuildSuccessResponse(RequestId, JsonObj(Data));
End;


{..............................................................................}
{ Place a schematic component instance from a library onto the active sheet.  }
{ Uses ISch_Document.PlaceSchComponent, the verified direct-placement API.   }
{ Params: library_path (.SchLib full path), lib_reference (component name),   }
{         x, y (mils), designator (optional), rotation (0|90|180|270),        }
{         footprint (optional override)                                        }
{..............................................................................}

{ Validate lib_path + lib_reference resolve via CreateLibCompInfoReader     }
{ BEFORE calling PlaceSchComponent. PlaceSchComponent pops a modal Error    }
{ dialog when its lookup fails, Altium shows the popup at the COM layer    }
{ before our Try/Except can swallow it, which freezes the polling loop.    }
{ Pre-validating off-disk avoids that path entirely.                         }
{ Returns ''  if found, otherwise a comma-separated sample of names that    }
{ ARE in the lib so the caller can see what's available.                    }
Function ResolveLibRef(LibPath, LibRef : String; Var Available : String) : Boolean;
Var
    Reader : ILibCompInfoReader;
    Info : IComponentInfo;
    Count, I, Shown : Integer;
    Resolved, Reason : String;
Begin
    Result := False;
    Available := '';
    If LibPath = '' Then Exit;
    { Same reason as the placement path: an .IntLib reaching                }
    { CreateLibCompInfoReader is what raises the uncatchable modal. Every   }
    { caller of this function benefits from resolving here as well, because }
    { not all of them resolve first.                                         }
    Resolved := ResolveSchLibForLoad(LibPath, Reason);
    If Resolved = '' Then
    Begin
        Available := Reason;
        Exit;
    End;
    Try
        Reader := SchServer.CreateLibCompInfoReader(Resolved);
    Except
        Reader := Nil;
    End;
    If Reader = Nil Then Exit;
    Try Reader.ReadAllComponentInfo; Except End;

    Try Count := Reader.NumComponentInfos; Except Count := 0; End;
    Shown := 0;
    For I := 0 To Count - 1 Do
    Begin
        Info := Reader.ComponentInfos[I];
        If Info = Nil Then Continue;
        If Info.CompName = LibRef Then
        Begin
            Result := True;
            Exit;
        End;
        If Shown < 5 Then
        Begin
            If Available <> '' Then Available := Available + '; ';
            Available := Available + Info.CompName;
            Inc(Shown);
        End;
    End;
End;

{ Find the placed ISch_Component on SchDoc that matches the given lib_ref.   }
{ Used after PlaceSchComponent because the SDK signature returns only an     }
{ integer TSchObjectHandle via a Var parameter, not the component object.    }
{ Returns the most recently placed component matching the lib_ref so a      }
{ caller can position / rename / customise it.                                }
Function FindPlacedComponentByLibRef(SchDoc : ISch_Document; LibRef : String) : ISch_Component;
Var
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Best : ISch_Component;
Begin
    Best := Nil;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.LibReference = LibRef Then Best := Obj;  { keep last match }
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
    Result := Best;
End;

Function Gen_PlaceSchComponentFromLibrary(Params : String; RequestId : String) : String;
Var
    LibPath, LibRef, DesigStr, FootprintStr, AvailHint, SheetPath : String;
    ResolvedLib, LibReason : String;
    Rotation, OrientationVal : Integer;
    X, Y : Double;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    SrvDoc : IServerDocument;
Begin
    LibPath := ExtractJsonValue(Params, 'library_path');
    LibRef := ExtractJsonValue(Params, 'lib_reference');
    DesigStr := ExtractJsonValue(Params, 'designator');
    FootprintStr := ExtractJsonValue(Params, 'footprint');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');

    { Resolve before Altium sees the path: an .IntLib raises the "Open      }
    { Integrated Library" modal, which nothing here can catch and which     }
    { stops the polling loop until a human dismisses it. A placed component }
    { reports its library AS an .IntLib, so a caller copying source_library }
    { off the sheet lands here every time.                                   }
    If LibPath <> '' Then
    Begin
        ResolvedLib := ResolveSchLibForLoad(LibPath, LibReason);
        If ResolvedLib = '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'LIBRARY_NOT_LOADABLE', LibReason);
            Exit;
        End;
        LibPath := ResolvedLib;
    End;
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    Rotation := StrToIntDef(ExtractJsonValue(Params, 'rotation'), 0);

    If LibRef = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'lib_reference required');
        Exit;
    End;

    { Resolve target sheet (focus-independent). }
    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath + '. Open it first.');
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    If SchDoc.ObjectId <> eSheet Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOC_KIND',
            'Target document is not a schematic sheet (ObjectId=' +
            IntToStr(SchDoc.ObjectId) + '). Pass sheet_path to a .SchDoc.');
        Exit;
    End;

    { Pre-validate that lib_reference exists in the SchLib. Cheap on-disk   }
    { check via CreateLibCompInfoReader; avoids any internal-popup path.    }
    If LibPath <> '' Then
    Begin
        If Not ResolveLibRef(LibPath, LibRef, AvailHint) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'PLACE_FAILED',
                'lib_reference "' + LibRef + '" not found in ' + LibPath +
                '. Sample of available names: ' + AvailHint);
            Exit;
        End;
    End;

    { THE WORKING PLACEMENT API (per SamacSys Altium Library Loader and    }
    { the Altium Circad translator reference):                              }
    {   1. SchServer.LoadComponentFromLibrary(LibRef, LibPath), note the }
    {      argument order is (REF, PATH), opposite of PlaceSchComponent.   }
    {   2. SchDoc.AddSchObject(comp), attach to the sheet.                }
    {   3. comp.MoveToXY(MilsToCoordF(X), MilsToCoordF(Y)), proper          }
    {      whole-component positioning. Moves designator / comment / pins }
    {      together.                                                        }
    {   4. comp.SetState_Orientation(N), 0/1/2/3 for 0 deg/90 deg/180 deg/270 deg.    }
    {                                                                        }
    { This replaces the broken PlaceSchComponent + Comp.Location :=         }
    { Point(...) approach which 16-bit-truncates coords and pops modal      }
    { errors.                                                               }
    { Load the symbol BEFORE opening the sheet's transaction. The retry below }
    { can open a library DOCUMENT, and doing that between PreProcess and      }
    { PostProcess would nest a document change inside the sheet's edit.       }
    Comp := Nil;
    Try
        Comp := SchServer.LoadComponentFromLibrary(LibRef, LibPath);
    Except
        Comp := Nil;
    End;

    { AUTO-OPEN, THEN RETRY ONCE.                                             }
    {                                                                         }
    { LoadComponentFromLibrary answers from the library as the EDITOR holds   }
    { it, not from the file, so a library sitting on disk unopened returns    }
    { Nil here even though ResolveLibRef read the very same symbol out of it  }
    { moments ago. That split is the whole defect: validation succeeds off    }
    { disk, placement fails in memory, and the caller is told the symbol      }
    { could not be placed by a library that demonstrably contains it.         }
    {                                                                         }
    { Installing the .IntLib does not help, and neither does opening the      }
    { .LibPkg: a library package holds no schematic documents, so nothing is  }
    { loaded by opening it. The source .SchLib itself has to be resident.     }
    {                                                                         }
    { FocusSchLib is the same WorkspaceManager:OpenObject the lib_ handlers   }
    { already use, and it verifies it landed on the requested library rather  }
    { than leaving a previous one current.                                    }
    If Comp = Nil Then
    Begin
        If FocusSchLib(LibPath) <> Nil Then
        Begin
            Try
                Comp := SchServer.LoadComponentFromLibrary(LibRef, LibPath);
            Except
                Comp := Nil;
            End;
        End;

        { FocusSchLib focuses the library it opened. Put the sheet back,      }
        { whether or not the retry worked, so placing a part never leaves the }
        { user looking at a library instead of their schematic.                }
        Try
            SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
            If SrvDoc <> Nil Then Client.ShowDocument(SrvDoc);
        Except End;
    End;

    If Comp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'PLACE_FAILED',
            'LoadComponentFromLibrary returned nil for ' + LibRef +
            ' from ' + LibPath +
            '. The library was opened and the load retried, so the symbol is '
            + 'unreadable rather than absent.');
        Exit;
    End;

    SchBeginEdit(SchDoc, '');

    Try SchDoc.AddSchObject(Comp); Except NoteWriteFailed('add component to sheet'); End;
    Try Comp.MoveToXY(MilsToCoordF(X), MilsToCoordF(Y)); Except NoteWriteFailed('component MoveToXY'); End;

    { Translate degrees to the orientation enum. }
    OrientationVal := 0;
    If Rotation = 90 Then OrientationVal := 1
    Else If Rotation = 180 Then OrientationVal := 2
    Else If Rotation = 270 Then OrientationVal := 3;
    Try Comp.SetState_Orientation(OrientationVal); Except NoteWriteFailed('component Orientation'); End;

    { Override designator if caller supplied one. }
    If DesigStr <> '' Then
    Begin
        Try Comp.Designator.Text := DesigStr; Except NoteWriteFailed('component Designator'); End;
    End;

    { Footprint override at place time is intentionally skipped:           }
    { Comp.CurrentFootprintModelName is a read-only getter in DelphiScript  }
    { and assigning to it raises Undeclared identifier which Try/Except     }
    { cannot suppress (memory: delphiscript_api_quirks.md). The symbol's    }
    { own linked footprint from the library is used. To override at run    }
    { time, walk Comp.Implementations and edit ISch_Implementation.ModelName }
    { in a dedicated handler.                                                }

    SchRegisterObject(SchDoc, Comp);
    SchAutopositionLabels(Comp, 'rotate', False);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { Explicitly flag the IServerDocument dirty so save_all flushes it.    }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"library_path":"' + EscapeJsonString(LibPath) + '",'
        + '"lib_reference":"' + EscapeJsonString(LibRef) + '",'
        + '"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + ','
        + '"rotation":' + IntToStr(Rotation) + ','
        + '"designator":"' + EscapeJsonString(DesigStr) + '"}');
End;

{ A new parameter set at (X, Y) mils carrying 'Name=Value|Name=Value'.      }
{ Nil when the list holds no Name=Value pair. Used by directives and        }
{ blankets alike.                                                           }
Function NewParamSet(X : Double; Y : Double; ParamList : String) : ISch_ParameterSet;
Var
    PSet : ISch_ParameterSet;
    Remaining, Pair, PName, PValue : String;
    BarPos, EqPos, Added : Integer;
    Param : ISch_Parameter;
Begin
    Result := Nil;
    PSet := SchServer.SchObjectFactory(eParameterSet, eCreate_Default);
    If PSet = Nil Then Exit;
    PSet.Location := Point(MilsToCoordF(X), MilsToCoordF(Y));
    Added := 0;
    Remaining := ParamList;
    While Remaining <> '' Do
    Begin
        BarPos := Pos('|', Remaining);
        If BarPos > 0 Then
        Begin
            Pair := Copy(Remaining, 1, BarPos - 1);
            Remaining := Copy(Remaining, BarPos + 1, Length(Remaining));
        End
        Else
        Begin
            Pair := Remaining;
            Remaining := '';
        End;
        EqPos := Pos('=', Pair);
        If EqPos > 1 Then
        Begin
            PName := Trim(Copy(Pair, 1, EqPos - 1));
            PValue := Trim(Copy(Pair, EqPos + 1, Length(Pair)));
            Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
            If Param <> Nil Then
            Begin
                Param.Name := PName;
                Param.Text := PValue;
                PSet.AddSchObject(Param);
                SchRegisterObject(PSet, Param);
                If Added = 0 Then
                Begin
                    Try PSet.Name := PName; Except NoteWriteFailed('directive Name'); End;
                End;
                Inc(Added);
            End;
        End;
    End;
    If Added > 0 Then Result := PSet;
End;

{ One parameter set as JSON: name, place, the parameters it carries, and   }
{ where it was found ("sheet", or "blanket" for one held inside a blanket). }
Function ParamSetJson(ParamSet : ISch_BasicContainer; Owner : String) : String;
Var
    Iter : ISch_Iterator;
    Param : ISch_BasicContainer;
    ChildJson, PName, PValue, DirName : String;
    FirstChild : Boolean;
    X, Y : Double;
    Style, Orient : Integer;
Begin
    DirName := ''; X := 0; Y := 0; Style := -1; Orient := -1;
    Try DirName := ParamSet.Name; Except End;
    Try Style := ParamSet.Style; Except End;
    Try Orient := ParamSet.Orientation; Except End;
    Try X := CoordToMilsF(ParamSet.Location.X); Except End;
    Try Y := CoordToMilsF(ParamSet.Location.Y); Except End;
    ChildJson := '';
    FirstChild := True;
    Try
        Iter := ParamSet.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        While Param <> Nil Do
        Begin
            PName := ''; PValue := '';
            Try PName := Param.Name; Except End;
            Try PValue := Param.Text; Except End;
            If Not FirstChild Then ChildJson := ChildJson + ',';
            FirstChild := False;
            ChildJson := ChildJson + '{"name":"' + EscapeJsonString(PName)
                + '","value":"' + EscapeJsonString(PValue) + '"}';
            Param := Iter.NextSchObject;
        End;
        ParamSet.SchIterator_Destroy(Iter);
    Except End;
    Result := '{"name":"' + EscapeJsonString(DirName) + '",'
        + '"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + ','
        + '"owner":"' + Owner + '",'
        + '"style":' + IntToStr(Style) + ',"orientation":' + IntToStr(Orient) + ','
        + '"parameters":[' + ChildJson + ']}';
End;

{..............................................................................}
{ Place a parameter-set directive on the schematic at (x, y).                 }
{ A parameter-set directive attaches a named parameter to a wire or net,     }
{ commonly used for differential pairs (DifferentialPair=<pair name>), net   }
{ class membership (NetClass=<class name>), or custom net-level rules.        }
{ Params: x, y, and param_name + param_value or parameters                   }
{         'Name=Value|Name=Value'; optional style and orientation (integers)  }
{..............................................................................}

Function Gen_PlaceDirective(Params : String; RequestId : String) : String;
Var
    X, Y : Double;
    ParamName, ParamValue, ParamList, StyleStr, OrientStr : String;
    SchDoc : ISch_Document;
    ParamSet : ISch_ParameterSet;
Begin
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    ParamName := ExtractJsonValue(Params, 'param_name');
    ParamValue := ExtractJsonValue(Params, 'param_value');
    ParamList := ExtractJsonValue(Params, 'parameters');
    StyleStr := ExtractJsonValue(Params, 'style');
    OrientStr := ExtractJsonValue(Params, 'orientation');
    If (ParamList = '') And (ParamName <> '') Then ParamList := ParamName + '=' + ParamValue;

    If ParamList = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'param_name or parameters required');
        Exit;
    End;
    If ((StyleStr <> '') And (Not IsIntStr(StyleStr)))
        Or ((OrientStr <> '') And (Not IsIntStr(OrientStr))) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS',
            'style and orientation are integers. Nothing was placed.');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { ISch_ParameterSet is the proper directive interface, a group of
      parameters applied to the wire/net at its location; each carries
      one ISch_Parameter. ISch_Parameter alone would render as
      free-standing text and not act as a directive. }
    ParamSet := NewParamSet(X, Y, ParamList);
    If ParamSet = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS',
            'parameters must be Name=Value pairs joined by |; got "'
            + EscapeJsonString(ParamList) + '". Nothing was placed.');
        Exit;
    End;
    If StyleStr <> '' Then
    Begin
        Try ParamSet.Style := StrToIntDef(StyleStr, 0); Except NoteWriteFailed('directive Style'); End;
    End;
    If OrientStr <> '' Then
    Begin
        Try ParamSet.Orientation := StrToIntDef(OrientStr, 0); Except NoteWriteFailed('directive Orientation'); End;
    End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(ParamSet);
    SchRegisterObject(SchDoc, ParamSet);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + ','
        + '"param_name":"' + EscapeJsonString(ParamName) + '",'
        + '"param_value":"' + EscapeJsonString(ParamValue) + '",'
        + '"directive":' + ParamSetJson(ParamSet, 'sheet') + '}');
End;

{..............................................................................}
{ Enumerate parameter-set directives on the active sheet (or project).        }
{ Each directive is a named group of key=value parameters attached at a       }
{ specific (x, y) on a wire or net. Used for net classes, differential pair   }
{ definitions, channel naming, and any other per-net design rule directive.   }
{ Params: scope = active_doc | project (default active_doc)                  }
{..............................................................................}

Function Gen_GetDirectives(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    OuterIter, BlanketIter, InnerIter : ISch_Iterator;
    ParamSet, Blanket : ISch_BasicContainer;
    JsonItems : String;
    First : Boolean;
    Count, InBlankets : Integer;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    JsonItems := '';
    First := True;
    Count := 0;
    InBlankets := 0;

    OuterIter := SchDoc.SchIterator_Create;
    OuterIter.AddFilter_ObjectSet(MkSet(eParameterSet));
    Try
        ParamSet := OuterIter.FirstSchObject;
        While ParamSet <> Nil Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems + ParamSetJson(ParamSet, 'sheet');
            Inc(Count);
            ParamSet := OuterIter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(OuterIter);
    End;

    { A blanket is a container: a directive it holds is not on the sheet's }
    { first level, so the walk above cannot see it.                        }
    BlanketIter := SchDoc.SchIterator_Create;
    BlanketIter.AddFilter_ObjectSet(MkSet(eBlanket));
    Try
        Blanket := BlanketIter.FirstSchObject;
        While Blanket <> Nil Do
        Begin
            Try
                InnerIter := Blanket.SchIterator_Create;
                InnerIter.AddFilter_ObjectSet(MkSet(eParameterSet));
                ParamSet := InnerIter.FirstSchObject;
                While ParamSet <> Nil Do
                Begin
                    If Not First Then JsonItems := JsonItems + ',';
                    First := False;
                    JsonItems := JsonItems + ParamSetJson(ParamSet, 'blanket');
                    Inc(Count);
                    Inc(InBlankets);
                    ParamSet := InnerIter.NextSchObject;
                End;
                Blanket.SchIterator_Destroy(InnerIter);
            Except End;
            Blanket := BlanketIter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(BlanketIter);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"directives":[' + JsonItems + '],"count":' + IntToStr(Count)
        + ',"in_blankets":' + IntToStr(InBlankets) + '}');
End;

{..............................................................................}
{ Place a compile mask (blanket) over a rectangular area on the schematic.    }
{ Compile masks exclude enclosed objects from compilation and ERC.            }
{ Params: x1,y1,x2,y2 in mils                                                 }
{..............................................................................}

Function Gen_PlaceCompileMask(Params : String; RequestId : String) : String;
Var
    TmpF : Double;
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Mask : ISch_CompileMask;
    CollStr : String;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    CollStr := LowerCase(ExtractJsonValue(Params, 'collapsed'));
    If X1 > X2 Then Begin TmpF := X1; X1 := X2; X2 := TmpF; End;
    If Y1 > Y2 Then Begin TmpF := Y1; Y1 := Y2; Y2 := TmpF; End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Mask := SchServer.SchObjectFactory(eCompileMask, eCreate_Default);
    If Mask = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create compile mask');
        Exit;
    End;

    Mask.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    Mask.Corner := Point(MilsToCoordF(X2), MilsToCoordF(Y2));
    If (CollStr = 'true') Or (CollStr = 'false') Then
    Begin
        Try Mask.Collapsed := (CollStr = 'true'); Except NoteWriteFailed('CompileMask.Collapsed'); End;
    End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Mask);
    SchRegisterObject(SchDoc, Mask);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    { Read back, so a mask that did not take its box says so. }
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,'
        + '"x1":' + FloatToJsonStr(CoordToMilsF(Mask.Location.X))
        + ',"y1":' + FloatToJsonStr(CoordToMilsF(Mask.Location.Y)) + ','
        + '"x2":' + FloatToJsonStr(CoordToMilsF(Mask.Corner.X))
        + ',"y2":' + FloatToJsonStr(CoordToMilsF(Mask.Corner.Y)) + ','
        + '"collapsed":' + BoolToJsonStr(Mask.Collapsed) + '}');
End;

{..............................................................................}
{ BLANKETS. A blanket is a polygon (ISch_Blanket on ISch_CollapsiblePolygon   }
{ on ISch_Polygon) that hands the parameter-set directive touching its edge  }
{ to every net it encloses: a net class, a differential pair, a No ERC over }
{ a whole area. The compiled design lists what each one caught               }
{ (IDocument.DM_Blankets, IBlanket.DM_NetItems), which is how a placement    }
{ is checked rather than trusted. ParamSetJson, which they share with      }
{ Gen_GetDirectives, is defined above that function.                         }
{..............................................................................}

{ Place a blanket from an outline, with the directive it carries.           }
{ Params: vertices 'x1,y1,x2,y2,...' (mils, at least 3 points);             }
{         parameters 'Name=Value|Name=Value' (optional: a bare blanket);    }
{         directive_x, directive_y (default: the first vertex, on the edge, }
{         where Altium reads a blanket's directive from);                   }
{         line_style (integer), collapsed (true/false);                    }
{         color, area_color (Altium's BGR integers). Without them the      }
{         blanket takes the user's own blanket defaults (Preferences >     }
{         Schematic > Defaults), as one drawn in the editor does. A new    }
{         object is drawn above what is already on the sheet and no        }
{         scripted call reorders a sheet, so the fill is always written    }
{         transparent; an opaque fill would hide every wire and label it   }
{         encloses. Altium does not keep an opaque blanket fill on save    }
{         anyway (it reads back opaque, but is transparent after reopen).  }
Function Gen_PlaceBlanket(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Blanket : ISch_GraphicalObject;
    ParamSet : ISch_ParameterSet;
    Xs, Ys : TStringList;
    VertStr, ParamList, StyleStr, CollStr, DxStr, DyStr, DirJson : String;
    ColorStr, AreaStr : String;
    DX, DY : Double;
    I : Integer;
Begin
    VertStr := ExtractJsonValue(Params, 'vertices');
    ColorStr := ExtractJsonValue(Params, 'color');
    AreaStr := ExtractJsonValue(Params, 'area_color');
    ParamList := ExtractJsonValue(Params, 'parameters');
    StyleStr := ExtractJsonValue(Params, 'line_style');
    CollStr := LowerCase(ExtractJsonValue(Params, 'collapsed'));
    DxStr := ExtractJsonValue(Params, 'directive_x');
    DyStr := ExtractJsonValue(Params, 'directive_y');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Xs := TStringList.Create;
    Ys := TStringList.Create;
    Try
        If (Not ParseVertexList(VertStr, Xs, Ys)) Or (Xs.Count < 3) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS',
                'vertices must be at least 3 points as x1,y1,x2,y2,... in mils; got "'
                + EscapeJsonString(VertStr) + '". Nothing was placed.');
            Exit;
        End;
        If (StyleStr <> '') And (Not IsIntStr(StyleStr)) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS',
                'line_style must be an integer; got "' + EscapeJsonString(StyleStr)
                + '". Nothing was placed.');
            Exit;
        End;
        If ((DxStr <> '') And (Not IsFloatStr(DxStr))) Or ((DyStr <> '') And (Not IsFloatStr(DyStr))) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS',
                'directive_x and directive_y must be numbers in mils. Nothing was placed.');
            Exit;
        End;
        If ((ColorStr <> '') And (Not IsIntStr(ColorStr))) Or ((AreaStr <> '') And (Not IsIntStr(AreaStr))) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS',
                'color and area_color must be Altium colour integers (BGR, e.g. 255 = red). '
                + 'Nothing was placed.');
            Exit;
        End;

        DX := StrToFloatDef(Xs[0], 0);
        DY := StrToFloatDef(Ys[0], 0);
        If DxStr <> '' Then DX := StrToFloatDef(DxStr, DX);
        If DyStr <> '' Then DY := StrToFloatDef(DyStr, DY);
        ParamSet := Nil;
        If Trim(ParamList) <> '' Then
        Begin
            ParamSet := NewParamSet(DX, DY, ParamList);
            If ParamSet = Nil Then
            Begin
                Result := BuildErrorResponse(RequestId, 'INVALID_PARAMS',
                    'parameters must be Name=Value pairs joined by |, e.g. '
                    + '"NetClass=PWR" or "DifferentialPair=USB"; got "'
                    + EscapeJsonString(ParamList) + '". Nothing was placed.');
                Exit;
            End;
        End;

        { The user's blanket defaults, as the editor's Place > Blanket. }
        Blanket := SchServer.SchObjectFactory(eBlanket, eCreate_GlobalCopy);
        If Blanket = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create blanket');
            Exit;
        End;
        Blanket.VerticesCount := Xs.Count;
        For I := 1 To Xs.Count Do
            Blanket.Vertex[I] := Point(MilsToCoordF(StrToFloatDef(Xs[I - 1], 0)),
                                       MilsToCoordF(StrToFloatDef(Ys[I - 1], 0)));
        If StyleStr <> '' Then
        Begin
            Try Blanket.LineStyle := StrToIntDef(StyleStr, 0); Except NoteWriteFailed('Blanket.LineStyle'); End;
        End;
        If (CollStr = 'true') Or (CollStr = 'false') Then
        Begin
            Try Blanket.Collapsed := (CollStr = 'true'); Except NoteWriteFailed('Blanket.Collapsed'); End;
        End;
        If ColorStr <> '' Then
        Begin
            Try Blanket.Color := StrToIntDef(ColorStr, 0); Except NoteWriteFailed('Blanket.Color'); End;
        End;
        If AreaStr <> '' Then
        Begin
            Try Blanket.AreaColor := StrToIntDef(AreaStr, 0); Except NoteWriteFailed('Blanket.AreaColor'); End;
        End;
        Try Blanket.Transparent := True; Except NoteWriteFailed('Blanket.Transparent'); End;

        SchBeginEdit(SchDoc, '');
        SchDoc.RegisterSchObjectInContainer(Blanket);
        SchRegisterObject(SchDoc, Blanket);
        If ParamSet <> Nil Then
        Begin
            SchDoc.RegisterSchObjectInContainer(ParamSet);
            SchRegisterObject(SchDoc, ParamSet);
        End;
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
        If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

        DirJson := 'null';
        If ParamSet <> Nil Then DirJson := ParamSetJson(ParamSet, 'sheet');
        Result := BuildSuccessResponse(RequestId,
            '{"placed":true,"vertices_count":' + IntToStr(Blanket.VerticesCount)
            + ',"vertices":"' + GetSchVertexProperty(Blanket, 'Vertices') + '"'
            + ',"color":' + IntToStr(Blanket.Color)
            + ',"area_color":' + IntToStr(Blanket.AreaColor)
            + ',"transparent":' + BoolToJsonStr(Blanket.Transparent)
            + ',"line_style":' + IntToStr(Blanket.LineStyle)
            + ',"directive":' + DirJson + '}');
    Finally
        Ys.Free;
        Xs.Free;
    End;
End;

{ The DM document for a sheet path in the focused project, or Nil.          }
Function DMDocumentForPath(Path : String) : IDocument;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I : Integer;
Begin
    Result := Nil;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    Project := Workspace.DM_FocusedProject;
    If Project = Nil Then Exit;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try Doc := Project.DM_LogicalDocuments(I); Except End;
        If Doc <> Nil Then
        Begin
            If SameText(Doc.DM_FullPath, Path) Then
            Begin
                Result := Doc;
                Exit;
            End;
        End;
    End;
End;

{ Every blanket on the active sheet: outline, style, the directives inside  }
{ it or on its edge, and what the compiled design says it covers.           }
Function Gen_GetBlankets(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter, PIter, CIter : ISch_Iterator;
    Blanket, PS : ISch_GraphicalObject;
    DMDoc : IDocument;
    DMB : IBlanket;
    Item : INetItem;
    DMSym : ISheetSymbol;
    Json, DirJson, NetsJson, NetList, NetName, Seen, SymList, SymName, SymFile : String;
    First, FirstDir, FirstNet : Boolean;
    Count, I, J, Style : Integer;
    Coll : Boolean;
    BX1, BY1, BX2, BY2, PX, PY : Double;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Json := '';
    First := True;
    Count := 0;
    Iter := SchDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eBlanket));
    Try
        Blanket := Iter.FirstSchObject;
        While Blanket <> Nil Do
        Begin
            Style := -1; Coll := False;
            Try Style := Blanket.LineStyle; Except End;
            Try Coll := Blanket.Collapsed; Except End;
            BX1 := 0; BY1 := 0; BX2 := 0; BY2 := 0;
            Try
                BX1 := CoordToMilsF(Blanket.BoundingRectangle.X1);
                BY1 := CoordToMilsF(Blanket.BoundingRectangle.Y1);
                BX2 := CoordToMilsF(Blanket.BoundingRectangle.X2);
                BY2 := CoordToMilsF(Blanket.BoundingRectangle.Y2);
            Except End;

            { Directives: inside the blanket's own container, and on the  }
            { sheet within its box (the triangle sits on the edge, and    }
            { its box reaches a little past the outline).                 }
            DirJson := '';
            FirstDir := True;
            Try
                CIter := Blanket.SchIterator_Create;
                CIter.AddFilter_ObjectSet(MkSet(eParameterSet));
                PS := CIter.FirstSchObject;
                While PS <> Nil Do
                Begin
                    If Not FirstDir Then DirJson := DirJson + ',';
                    FirstDir := False;
                    DirJson := DirJson + ParamSetJson(PS, 'blanket');
                    PS := CIter.NextSchObject;
                End;
                Blanket.SchIterator_Destroy(CIter);
            Except End;
            PIter := SchDoc.SchIterator_Create;
            PIter.AddFilter_ObjectSet(MkSet(eParameterSet));
            Try
                PS := PIter.FirstSchObject;
                While PS <> Nil Do
                Begin
                    PX := CoordToMilsF(PS.Location.X);
                    PY := CoordToMilsF(PS.Location.Y);
                    If (PX >= BX1 - 50) And (PX <= BX2 + 50)
                       And (PY >= BY1 - 50) And (PY <= BY2 + 50) Then
                    Begin
                        If Not FirstDir Then DirJson := DirJson + ',';
                        FirstDir := False;
                        DirJson := DirJson + ParamSetJson(PS, 'sheet');
                    End;
                    PS := PIter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(PIter);
            End;

            If Not First Then Json := Json + ',';
            First := False;
            Json := Json + '{"index":' + IntToStr(Count)
                + ',"vertices":"' + GetSchVertexProperty(Blanket, 'Vertices') + '"'
                + ',"x1":' + FloatToJsonStr(BX1) + ',"y1":' + FloatToJsonStr(BY1)
                + ',"x2":' + FloatToJsonStr(BX2) + ',"y2":' + FloatToJsonStr(BY2)
                + ',"line_style":' + IntToStr(Style)
                + ',"collapsed":' + BoolToJsonStr(Coll)
                + ',"directives":[' + DirJson + ']}';
            Inc(Count);
            Blanket := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    { What the compiler made of them: only as fresh as the last compile.   }
    NetsJson := '';
    First := True;
    DMDoc := DMDocumentForPath(SchDoc.DocumentName);
    If DMDoc <> Nil Then
    Begin
        For I := 0 To DMDoc.DM_BlanketCount - 1 Do
        Begin
            DMB := Nil;
            Try DMB := DMDoc.DM_Blankets(I); Except End;
            If DMB <> Nil Then
            Begin
                Seen := '|';
                FirstNet := True;
                NetList := '';
                For J := 0 To DMB.DM_NetItemCount - 1 Do
                Begin
                    NetName := '';
                    Try
                        Item := DMB.DM_NetItems(J);
                        NetName := Item.DM_NetName;
                    Except End;
                    If (NetName <> '') And (Pos('|' + NetName + '|', Seen) = 0) Then
                    Begin
                        Seen := Seen + NetName + '|';
                        If Not FirstNet Then NetList := NetList + ',';
                        FirstNet := False;
                        NetList := NetList + '"' + EscapeJsonString(NetName) + '"';
                    End;
                End;
                { Sheet symbols it covers: its directive reaches every net }
                { through their entries.                                    }
                SymList := '';
                Try
                    For J := 0 To DMB.DM_SheetSymbolCount - 1 Do
                    Begin
                        DMSym := Nil; SymName := ''; SymFile := '';
                        Try DMSym := DMB.DM_SheetSymbols(J); Except DMSym := Nil; End;
                        If DMSym = Nil Then Continue;
                        Try SymName := DMSym.DM_LogicalDesignator; Except End;
                        Try SymFile := DMSym.DM_SheetSymbolFileName; Except End;
                        If SymList <> '' Then SymList := SymList + ',';
                        SymList := SymList + '{"sheet_name":"' + EscapeJsonString(SymName)
                            + '","file":"' + EscapeJsonString(SymFile) + '"}';
                    End;
                Except End;
                If Not First Then NetsJson := NetsJson + ',';
                First := False;
                { No position: a compiled blanket reports 0,0 for its      }
                { location (measured on AD 26.11), so it would only mislead. }
                NetsJson := NetsJson + '{"index":' + IntToStr(I)
                    + ',"net_items":' + IntToStr(DMB.DM_NetItemCount)
                    + ',"nets":[' + NetList + ']'
                    + ',"sheet_symbols":[' + SymList + ']}';
            End;
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"blankets":[' + Json + '],"count":' + IntToStr(Count)
        + ',"compiled":' + BoolToJsonStr(DMDoc <> Nil)
        + ',"coverage":[' + NetsJson + ']}');
End;

{..............................................................................}
{ InferNetLabelStyle - the sheet's own net-label font, by majority.          }
{ Every net label a tool adds takes the FontId (font face AND size in the     }
{ font table) most common among the labels already on the target sheet.      }
{ Returns False when the sheet has no net labels yet; the label then keeps    }
{ the font Altium creates it with. Majority, not first-seen: one off-style    }
{ label from an old edit must not define the convention.                      }
{ The colour is not inferred: every placed label is NET_LABEL_COLOR, the      }
{ native Altium maroon, whatever colour older labels on the sheet carry.      }
{..............................................................................}

Function InferNetLabelStyle(SchDoc : ISch_Document;
    Var OutFontId : Integer) : Boolean;
Var
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Keys, Counts : TStringList;
    Key : String;
    Idx, I, N, BestN, FId : Integer;
Begin
    Result := False;
    OutFontId := 0;
    If SchDoc = Nil Then Exit;

    Keys := TStringList.Create;
    Counts := TStringList.Create;
    Try
        Iterator := SchDoc.SchIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(eNetLabel));
            Obj := Iterator.FirstSchObject;
            While Obj <> Nil Do
            Begin
                FId := 0;
                Try FId := Obj.FontId; Except End;
                If FId > 0 Then
                Begin
                    Key := IntToStr(FId);
                    Idx := Keys.IndexOf(Key);
                    If Idx < 0 Then
                    Begin
                        Keys.Add(Key);
                        Counts.Add('1');
                    End
                    Else
                        Counts[Idx] := IntToStr(StrToIntDef(Counts[Idx], 0) + 1);
                End;
                Obj := Iterator.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iterator);
        End;

        BestN := 0;
        For I := 0 To Keys.Count - 1 Do
        Begin
            N := StrToIntDef(Counts[I], 0);
            If N > BestN Then
            Begin
                BestN := N;
                OutFontId := StrToIntDef(Keys[I], 0);
            End;
        End;
        Result := BestN > 0;
    Finally
        Keys.Free;
        Counts.Free;
    End;
End;

{..............................................................................}
{ Place a net label at coordinates on active schematic                        }
{ Params: text, x, y, orientation (0/1/2/3)                                  }
{..............................................................................}

Function Gen_PlaceNetLabel(Params : String; RequestId : String) : String;
Var
    Text, SheetPath : String;
    Orientation : Integer;
    X, Y : Double;
    SchDoc : ISch_Document;
    NetLabel : ISch_NetLabel;
    Loc : TLocation;
    SrvDoc : IServerDocument;
    InfFont : Integer;
    StyleFound : Boolean;
Begin
    Text := ExtractJsonValue(Params, 'text');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    Orientation := StrToIntDef(ExtractJsonValue(Params, 'orientation'), 0);

    If Text = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text parameter is required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath);
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    NetLabel := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
    If NetLabel = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create net label');
        Exit;
    End;

    Loc := NetLabel.Location;
    Loc.X := MilsToCoordF(X);
    Loc.Y := MilsToCoordF(Y);
    NetLabel.Location := Loc;
    NetLabel.Text := Text;
    NetLabel.Orientation := Orientation;
    { The sheet's own net-label font (face and size via the font table),
      always the native net-label colour. }
    StyleFound := InferNetLabelStyle(SchDoc, InfFont);
    If StyleFound Then
    Begin
        Try NetLabel.FontId := InfFont; Except NoteWriteFailed('NetLabel.FontId'); End;
    End;
    NetLabel.Color := NET_LABEL_COLOR;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(NetLabel);
    SchRegisterObject(SchDoc, NetLabel);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"text":"' + EscapeJsonString(Text) +
        '","x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + '}');
End;

{..............................................................................}
{ Get pin world coordinates for a placed component on the active SchDoc.     }
{ Params: designator                                                          }
{ Returns array of [pin_number, pin_name, x_mils, y_mils, orientation].     }
{                                                                             }
{ Used by the design executor to look up pin positions after place_sch_     }
{ component_from_library so it can drop net labels at the right spot.        }
{ ISch_Pin.Location on a placed component instance returns world coords     }
{ already (Altium has applied component placement + orientation).           }
{..............................................................................}

{..............................................................................}
{ Resolving a designator when several symbols carry it.                        }
{                                                                              }
{ A multi-part device places one ISch_Component per sub-part and every one of  }
{ them carries the SAME designator, so "the component called U13" has no       }
{ single answer. The handlers below used to iterate, take the first match,     }
{ stop, and report success naming only the designator, so a caller could not   }
{ tell which symbol had been written, or that there had been a choice at all.  }
{                                                                              }
{ MEASURED on a live sheet: three symbols designated U13 for a PartCount of 2, }
{ two of them sitting on part 1.                                               }
{                                                                              }
{ LOCATION IS THE DISCRIMINATOR. CurrentPartID does not separate them, because }
{ two symbols can rest on the same part, and UniqueId does not either, because }
{ sub-parts of one physical device are supposed to share it.                   }
{..............................................................................}

Function SchComponentCount(SchDoc : ISch_Document; Designator : String) : Integer;
Var
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
Begin
    Result := 0;
    If SchDoc = Nil Then Exit;
    Iterator := SchDoc.SchIterator_Create;
    Try
        Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iterator.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Comp := Obj;
            If Comp.Designator.Text = Designator Then Result := Result + 1;
            Obj := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;
End;

{ One symbol by designator, narrowed by location when one is given. An empty   }
{ location returns the first match, which is the old behaviour and is correct  }
{ once the caller has established there is only one.                           }
Function SchComponentAt(SchDoc : ISch_Document; Designator : String;
                        LocX : String; LocY : String) : ISch_Component;
Var
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    CompLoc : TLocation;
    WantX, WantY : Double;
    Narrow, Hit : Boolean;
Begin
    Result := Nil;
    If SchDoc = Nil Then Exit;

    Narrow := (LocX <> '') And (LocY <> '') And IsFloatStr(LocX) And IsFloatStr(LocY);
    WantX := 0;
    WantY := 0;
    If Narrow Then
    Begin
        WantX := StrToFloatDef(LocX, 0);
        WantY := StrToFloatDef(LocY, 0);
    End;

    Iterator := SchDoc.SchIterator_Create;
    Try
        Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iterator.FirstSchObject;
        While (Obj <> Nil) And (Result = Nil) Do
        Begin
            Comp := Obj;
            If Comp.Designator.Text = Designator Then
            Begin
                Hit := True;
                If Narrow Then
                Begin
                    { Read through a materialized local. This engine does not }
                    { accept a record field reached straight off a property.  }
                    CompLoc := Comp.Location;
                    Hit := (Abs(CoordToMilsF(CompLoc.X) - WantX) < 0.0001)
                       And (Abs(CoordToMilsF(CompLoc.Y) - WantY) < 0.0001);
                End;
                If Hit Then Result := Comp;
            End;
            Obj := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;
End;

{ Every symbol carrying the designator, as a JSON array. A refusal hands this  }
{ back so the caller can re-issue against one of them, rather than being told  }
{ only that the request was ambiguous.                                         }
Function SchComponentCandidates(SchDoc : ISch_Document; Designator : String) : String;
Var
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    CompLoc : TLocation;
    Entry, PartId, PartCnt, Uid : String;
    First : Boolean;
Begin
    Result := '[]';
    If SchDoc = Nil Then Exit;
    Result := '[';
    First := True;
    Iterator := SchDoc.SchIterator_Create;
    Try
        Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iterator.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Comp := Obj;
            If Comp.Designator.Text = Designator Then
            Begin
                CompLoc := Comp.Location;
                PartId := '0';
                PartCnt := '0';
                Uid := '';
                Try PartId := IntToStr(Comp.CurrentPartID); Except End;
                Try PartCnt := IntToStr(Comp.PartCount); Except End;
                Try Uid := Comp.UniqueId; Except End;
                Entry := '{"location_x":' + FloatToJsonStr(CoordToMilsF(CompLoc.X))
                    + ',"location_y":' + FloatToJsonStr(CoordToMilsF(CompLoc.Y))
                    + ',"current_part_id":' + PartId
                    + ',"part_count":' + PartCnt
                    + ',"unique_id":"' + EscapeJsonString(Uid) + '"}';
                If Not First Then Result := Result + ',';
                Result := Result + Entry;
                First := False;
            End;
            Obj := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;
    Result := Result + ']';
End;

{ The refusal itself, so every handler words it the same way and none of them  }
{ can drift back into choosing one silently.                                   }
Function AmbiguousDesignator(SchDoc : ISch_Document; Designator : String;
                             Total : Integer; RequestId : String) : String;
Begin
    Result := BuildErrorResponseDetailed(RequestId, 'AMBIGUOUS_DESIGNATOR',
        'There are ' + IntToStr(Total) + ' symbols designated ' + Designator
        + ' on this sheet, which is normal for a multi-part device. Pass '
        + 'location_x and location_y to choose one. The candidates, with '
        + 'their locations and current part ids, are in the details.',
        JsonObj(JsonRaw('candidates', SchComponentCandidates(SchDoc, Designator))));
End;

Function Gen_GetSchComponentPins(Params : String; RequestId : String) : String;
Var
    Designator, SheetPath : String;
    SchDoc : ISch_Document;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    Found : Boolean;
    Data, PinList : String;
    First : Boolean;
    PinNum, PinName : String;
    PinX, PinY, PinLenMils : Double;
    PinXC, PinYC, PinLenC : TCoord;
    PinOrient : Integer;
    CompX, CompY : Double;
    CompLoc : TLocation;
    SymbolCount, OwnerPart : Integer;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'designator is required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath);
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    Found := False;
    PinList := '';
    First := True;
    SymbolCount := 0;

    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        { EVERY symbol carrying the designator. A multi-part device places  }
        { one per sub-part and a placed instance exposes only its own       }
        { part's pins, so stopping at the first returned a third of a dual  }
        { device's pins as a plain answer, with nothing to say the rest     }
        { existed. Each pin now names the symbol it came from.              }
        While Comp <> Nil Do
        Begin
            If Comp.Designator.Text = Designator Then
            Begin
                Found := True;
                SymbolCount := SymbolCount + 1;
                OwnerPart := 0;
                Try OwnerPart := Comp.CurrentPartID; Except End;

                { CORRECTION: for an ISch_Pin attached to a placed             }
                { ISch_Component on a SchDoc, Pin.Location is ALREADY the      }
                { absolute world coordinate, not an offset from the component }
                { anchor. Adding CompX/CompY (the previous code) doubled the  }
                { offset and pushed every label / power port placed by the    }
                { design executor far past the actual pin endpoint, producing }
                { the "Floating net labels" / "Floating power objects" ERC    }
                { warnings on a freshly executed plan. Read Pin.Location      }
                { directly. CompLoc is kept for future use but no longer      }
                { added here. (Rotation / mirror still not handled in this    }
                { slice; Pin.Location already reflects the placed orientation }
                { because Altium updates it after RotateAroundXY / mirror.)   }
                CompLoc := Comp.Location;
                CompX := CoordToMilsF(CompLoc.X);
                CompY := CoordToMilsF(CompLoc.Y);

                PinIter := Comp.SchIterator_Create;
                Try
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Pin := PinIter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                        If PinShownOn(Comp, Pin) Then
                        Begin
                        PinNum := '';
                        PinName := '';
                        PinXC := 0;
                        PinYC := 0;
                        PinOrient := 0;
                        PinLenC := 0;
                        Try PinNum := Pin.Designator; Except End;
                        Try PinName := Pin.Name; Except End;
                        Try PinXC := Pin.Location.X; Except End;
                        Try PinYC := Pin.Location.Y; Except End;
                        { Pin.Orientation is the TRotationBy90 enum:           }
                        { 0=right (eRotate0),   1=up (eRotate90),               }
                        { 2=left (eRotate180),  3=down (eRotate270).            }
                        { This is the direction the pin's electrical end       }
                        { points AWAY from the component body, so a stub wire  }
                        { from Pin.Location must extend along this vector by   }
                        { Pin.PinLength to reach the electrical hot end.       }
                        Try PinOrient := Pin.Orientation; Except End;
                        Try PinLenC := Pin.PinLength; Except End;
                        PinX := CoordToMilsF(PinXC);
                        PinY := CoordToMilsF(PinYC);
                        PinLenMils := CoordToMilsF(PinLenC);

                        If Not First Then PinList := PinList + ',';
                        First := False;
                        PinList := PinList +
                            '{"pin_number":"' + EscapeJsonString(PinNum) +
                            '","pin_name":"' + EscapeJsonString(PinName) +
                            '","x_mils":' + FloatToJsonStr(PinX) +
                            ',"y_mils":' + FloatToJsonStr(PinY) +
                            ',"orientation":' + IntToStr(PinOrient) +
                            ',"pin_length_mils":' + FloatToJsonStr(PinLenMils) +
                            { x_mils and y_mils are Pin.Location, which is the
                              BODY-SIDE ROOT. These are the point a wire has
                              to sit on. Handed over rather than left to the
                              caller, because deriving it by hand is where the
                              direction gets reversed and the result is silent
                              geometry that does not connect. }
                            ',"connection_x_mils":'
                                + FloatToJsonStr(CoordToMilsF(PinEndX(PinXC, PinOrient, PinLenC))) +
                            ',"connection_y_mils":'
                                + FloatToJsonStr(CoordToMilsF(PinEndY(PinYC, PinOrient, PinLenC))) +
                            ',"owner_part_id":' + IntToStr(OwnerPart) +
                            ',"owner_x":' + FloatToJsonStr(CompX) +
                            ',"owner_y":' + FloatToJsonStr(CompY) + '}';
                        End;

                        Pin := PinIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PinIter);
                End;
            End;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Placed component not found: ' + Designator);
        Exit;
    End;

    Data := '{"designator":"' + EscapeJsonString(Designator) +
        '","symbols":' + IntToStr(SymbolCount) +
        ',"pins":[' + PinList + ']}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{..............................................................................}
{ Place a port on active schematic                                            }
{ Params: name, x, y, style, io_type                                         }
{..............................................................................}

Function Gen_PlacePort(Params : String; RequestId : String) : String;
Var
    Name, StyleStr, IOTypeStr : String;
    X, Y, WantWidth : Double;
    SchDoc : ISch_Document;
    SchPort : ISch_Port;
    FontPick, FontRead : Integer;
    WidthRead : Double;
    AutoRead : Boolean;
Begin
    Name := ExtractJsonValue(Params, 'name');
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    StyleStr := ExtractJsonValue(Params, 'style');
    IOTypeStr := ExtractJsonValue(Params, 'io_type');
    WantWidth := StrToFloatDef(ExtractJsonValue(Params, 'width'), 0);

    If Name = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'name parameter is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    FontPick := NewObjectFontId(SchDoc, ePort, Params);
    SchPort := SchServer.SchObjectFactory(ePort, eCreate_Default);
    If SchPort = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create port');
        Exit;
    End;

    SchPort.Location := Point(MilsToCoordF(X), MilsToCoordF(Y));
    SchPort.Name := Name;

    { Style is only the axis Altium keeps: 0 horizontal, 4 vertical. The    }
    { arrows come from IOType and the wired end; 1-3 and 5-7 read back but  }
    { are 0 / 4 again after a reopen (measured on AD 25.8 and 26.11). The   }
    { arrow names (none, left, right, left_right) give a horizontal port.   }
    StyleStr := LowerCase(StyleStr);
    If (StyleStr = 'vertical') Or (StyleStr = 'top') Or (StyleStr = 'bottom')
        Or (StyleStr = 'top_bottom') Then
        SchPort.Style := 4
    Else
        SchPort.Style := 0;

    // IO Type: unspecified, output, input, bidirectional
    If IOTypeStr = 'output' Then SchPort.IOType := ePortOutput
    Else If IOTypeStr = 'input' Then SchPort.IOType := ePortInput
    Else If IOTypeStr = 'bidirectional' Then SchPort.IOType := ePortBidirectional
    Else SchPort.IOType := ePortUnspecified;

    If FontPick > 0 Then
    Begin
        Try SchPort.FontId := FontPick; Except NoteWriteFailed('Port.FontId'); End;
    End;
    { AutoSize refits a port to its text at every redraw, which undoes a }
    { width; it is turned off for the width to stay.                     }
    If WantWidth > 0 Then
    Begin
        Try
            SchPort.AutoSize := False;
            SchPort.Width := MilsToCoordF(WantWidth);
        Except NoteWriteFailed('Port.Width'); End;
    End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(SchPort);
    SchRegisterObject(SchDoc, SchPort);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    FontRead := 0;
    WidthRead := 0;
    AutoRead := True;
    Try FontRead := SchPort.FontId; Except End;
    Try WidthRead := CoordToMilsF(SchPort.Width); Except End;
    Try AutoRead := SchPort.AutoSize; Except End;
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"name":"' + EscapeJsonString(Name) +
        '","x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + ','
        + JsonInt('font_id', FontRead) + ',' + JsonFloat('width', WidthRead) + ','
        + JsonBool('auto_size', AutoRead) + '}');
End;

{..............................................................................}
{ Place a power port (VCC, GND, etc.) on active schematic                     }
{ Params: text, x, y, style                                                  }
{..............................................................................}

Function Gen_PlacePowerPort(Params : String; RequestId : String) : String;
Var
    Text, StyleStr, SheetPath : String;
    OrientationVal : Integer;
    X, Y : Double;
    SchDoc : ISch_Document;
    PowerObj : ISch_PowerObject;
    Loc : TLocation;
    SrvDoc : IServerDocument;
    FontPick, FontRead : Integer;
Begin
    Text := ExtractJsonValue(Params, 'text');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    StyleStr := ExtractJsonValue(Params, 'style');
    OrientationVal := StrToIntDef(ExtractJsonValue(Params, 'orientation'), -1);

    If Text = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'text parameter is required');
        Exit;
    End;

    { An unknown style used to fall through to a circle and report success. }
    If StyleStr = '' Then StyleStr := 'circle';
    If Not IsPowerPortStyle(StyleStr) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_STYLE',
            'Unknown power port style "' + StyleStr + '". Use one of: ' + PowerPortStyleNames(0));
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath);
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    FontPick := NewObjectFontId(SchDoc, ePowerObject, Params);
    PowerObj := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
    If PowerObj = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create power port');
        Exit;
    End;

    Loc := PowerObj.Location;
    Loc.X := MilsToCoordF(X);
    Loc.Y := MilsToCoordF(Y);
    PowerObj.Location := Loc;
    PowerObj.Text := Text;
    PowerObj.ShowNetName := True;

    // Style: circle, arrow, bar, wave, gnd_power, gnd_signal, gnd_earth
    If StyleStr = 'arrow' Then PowerObj.Style := ePowerArrow
    Else If StyleStr = 'bar' Then PowerObj.Style := ePowerBar
    Else If StyleStr = 'wave' Then PowerObj.Style := ePowerWave
    Else If StyleStr = 'gnd_power' Then PowerObj.Style := ePowerGndPower
    Else If StyleStr = 'gnd_signal' Then PowerObj.Style := ePowerGndSignal
    Else If StyleStr = 'gnd_earth' Then PowerObj.Style := ePowerGndEarth
    Else PowerObj.Style := ePowerCircle;

    { Default orientation: supply styles (circle, arrow, bar, wave) point  }
    { UP, ground styles point DOWN.                                        }
    { Override via the 'orientation' param (0=right, 1=up, 2=left, 3=down). }
    If OrientationVal < 0 Then
    Begin
        If IsGroundPortStyle(StyleStr) Then
            OrientationVal := 3
        Else
            OrientationVal := 1;
    End;
    Try PowerObj.Orientation := OrientationVal; Except NoteWriteFailed('PowerPort.Orientation'); End;
    If FontPick > 0 Then
    Begin
        Try PowerObj.FontId := FontPick; Except NoteWriteFailed('PowerPort.FontId'); End;
    End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(PowerObj);
    SchRegisterObject(SchDoc, PowerObj);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    FontRead := 0;
    Try FontRead := PowerObj.FontId; Except End;
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"text":"' + EscapeJsonString(Text) +
        '","x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + ','
        + JsonInt('font_id', FontRead) + '}');
End;

{..............................................................................}
{ Get title block / sheet parameters from a schematic sheet                   }
{ Params: file_path (optional, defaults to active document)                   }
{..............................................................................}

Function Gen_GetSheetParameters(Params : String; RequestId : String) : String;
Var
    FilePath : String;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Param : ISch_Parameter;
    JsonItems : String;
    First : Boolean;
    ParamCount : Integer;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');

    If FilePath <> '' Then
        SchDoc := SchDocByPathLoading(FilePath)
    Else
        SchDoc := SchServer.GetCurrentSchDocument;

    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document available');
        Exit;
    End;

    JsonItems := '';
    First := True;
    ParamCount := 0;

    { SchIterator + eParameter at IterationDepth=FirstLevel returns
      sheet-level parameters that the title block reads from. This
      matches what set_document_parameter writes to. }
    Iterator := SchDoc.SchIterator_Create;
    Iterator.SetState_IterationDepth(eIterateFirstLevel);
    Iterator.AddFilter_ObjectSet(MkSet(eParameter));

    Try
        Param := Iterator.FirstSchObject;
        While Param <> Nil Do
        Begin
            If Not First Then JsonItems := JsonItems + ',';
            First := False;
            JsonItems := JsonItems + '{"name":"' + EscapeJsonString(Param.Name) +
                '","value":"' + EscapeJsonString(Param.Text) + '"}';
            Inc(ParamCount);
            Param := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"count":' + IntToStr(ParamCount) +
        ',"parameters":[' + JsonItems + ']}');
End;

{..............................................................................}
{ Copy matching objects to clipboard via Sch:CopyToClipboard                  }
{ Params: object_type, filter                                                 }
{..............................................................................}

Function Gen_CopyObjects(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, FilterStr : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    MatchCount : Integer;
Begin
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt = -1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', SchOnlyTypeMessage(ObjTypeStr, 'obj_copy'));
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    // Clear current selection first
    SchDeselectAllObjects(SchDoc);

    // Select matching objects
    MatchCount := 0;
    SchBeginEdit(SchDoc, '');

    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));

    Obj := Iterator.FirstSchObject;
    While Obj <> Nil Do
    Begin
        If MatchesFilter(Obj, FilterStr) Then
        Begin
            Obj.Selection := True;
            Inc(MatchCount);
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);
    SchEndEdit(SchDoc, 'Edit');

    // Copy selected to clipboard
    If MatchCount > 0 Then
        RunProcess('Sch:CopyToClipboard');

    // Clear selection after copy
    SchDeselectAllObjects(SchDoc);
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"copied":' + IntToStr(MatchCount) + '}');
End;

{ PCB count without a filter, from the board's own primitive counter.      }
{                                                                          }
{ The query path builds the JSON of every matching object only to report   }
{ how many there were: about 6 s for the 8722 tracks of one board. Altium  }
{ keeps a per-kind counter that gives the same number at once (checked     }
{ equal to a full board walk for tracks, pads, vias, polygons, components  }
{ and texts). The board is resolved exactly as the query path resolves it, }
{ so the target and the NO_PCB reply do not change. An empty result means  }
{ the counter could not answer and the caller takes the query path.        }
{ No "objects" list: the query path's list carries no properties for a    }
{ count, and obj_count drops it anyway.                                    }
Function PCBCountFromCounter(ObjTypeInt : Integer; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Counter : IPCB_PrimitiveCounter;
    N : Integer;
Begin
    Result := '';
    Board := GetPCBBoardAnywhere(0);
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;
    N := -1;
    Try
        Counter := Board.GetPrimitiveCounter;
        If Counter <> Nil Then
            N := Counter.GetObjectCount(ObjTypeInt);
    Except
        N := -1;
    End;
    If N < 0 Then Exit;
    Result := BuildSuccessResponse(RequestId,
        '{"count":' + IntToStr(N) + ',"counted_by":"counter"}');
End;

{..............................................................................}
{ Quick count of objects by type on active doc or project                     }
{ Params: object_type, scope (active_doc/project), filter                    }
{..............................................................................}

Function Gen_GetObjectCount(Params : String; RequestId : String) : String;
Var
    ObjTypeStr, FilterStr, Scope, ScopeType, ScopePath : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    I, MatchCount, SheetsProcessed : Integer;
    FilePath : String;
Begin
    ObjTypeStr := ExtractJsonValue(Params, 'object_type');
    FilterStr := ExtractJsonValue(Params, 'filter');
    Scope := ExtractJsonValue(Params, 'scope');
    ParseScope(Scope, ScopeType, ScopePath);
    Result := UnknownScopeError(RequestId, Scope, ScopeType);
    If Result <> '' Then Exit;

    ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
    If ObjTypeInt = -1 Then
    Begin
        ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
        If ObjTypeInt = -1 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'INVALID_TYPE', UnknownObjectTypeMessage(ObjTypeStr));
            Exit;
        End;

        // PCB count, active doc only. Without a filter the board's counter
        // answers at once; a filter still needs every object looked at.
        If FilterStr = '' Then
        Begin
            Result := PCBCountFromCounter(ObjTypeInt, RequestId);
            If Result <> '' Then Exit;
        End;
        Result := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', '', 'query', RequestId, 0);
        // The query result already has count, just return it
        Exit;
    End;

    MatchCount := 0;
    SheetsProcessed := 0;

    If ScopeType = 'project' Then
    Begin
        Workspace := GetWorkspace;
        If Workspace = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace available');
            Exit;
        End;

        If ScopePath <> '' Then
            Project := FindProjectByPath(Workspace, ScopePath)
        Else
            Project := Workspace.DM_FocusedProject;
        If Project = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PROJECT', 'No project found');
            Exit;
        End;

        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            If Doc.DM_DocumentKind <> 'SCH' Then Continue;

            FilePath := Doc.DM_FullPath;
            // Don't force-open, that creates free documents. Skip
            // sheets that aren't currently loaded into SchServer;
            // ProjectSheet counts them for the reply.
            SchDoc := ProjectSheet(FilePath);
            If SchDoc = Nil Then Continue;

            Iterator := SchDoc.SchIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            Obj := Iterator.FirstSchObject;
            While Obj <> Nil Do
            Begin
                If MatchesFilter(Obj, FilterStr) Then
                    Inc(MatchCount);
                Obj := Iterator.NextSchObject;
            End;
            SchDoc.SchIterator_Destroy(Iterator);
            Inc(SheetsProcessed);
        End;

        Result := BuildSuccessResponse(RequestId,
            '{"count":' + IntToStr(MatchCount) +
            ',"sheets_processed":' + IntToStr(SheetsProcessed) + '}');
    End
    Else
    Begin
        // Honor doc:<path> scope (parallel to query_objects). Without
        // this the count silently fell back to the active document,
        // returning a misleading number for an explicit doc: scope.
        If (ScopeType = 'doc') And (ScopePath <> '') Then
            SchDoc := SchServer.GetSchDocumentByPath(ScopePath)
        Else
            SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active or loaded for the given scope');
            Exit;
        End;

        Iterator := SchDoc.SchIterator_Create;
        Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
        Obj := Iterator.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If MatchesFilter(Obj, FilterStr) Then
                Inc(MatchCount);
            Obj := Iterator.NextSchObject;
        End;
        SchDoc.SchIterator_Destroy(Iterator);

        Result := BuildSuccessResponse(RequestId,
            '{"count":' + IntToStr(MatchCount) + '}');
    End;
End;

{..............................................................................}
{ Place a No-ERC marker at coordinates on active schematic                    }
{ Params: x, y; optional symbol, orientation (integers), suppress_all,       }
{ active (true/false), error_kinds, connection_pairs (Altium's strings).     }
{..............................................................................}

{ A number or true/false read off a marker, as JSON; null when unreadable. }
Function NoErcJsonVal(S : String) : String;
Begin
    If S = '' Then Result := 'null' Else Result := S;
End;

Function Gen_PlaceNoERC(Params : String; RequestId : String) : String;
Var
    X, Y : Double;
    SchDoc : ISch_Document;
    NoERC : ISch_GraphicalObject;
    Value, Back : String;
Begin
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    NoERC := SchServer.SchObjectFactory(eNoERC, eCreate_Default);
    If NoERC = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create No-ERC marker');
        Exit;
    End;

    ApplyNoErcDefaults(NoERC);
    NoERC.Location := Point(MilsToCoordF(X), MilsToCoordF(Y));
    { The caller's options, after the defaults so they can narrow them. }
    Value := ExtractJsonValue(Params, 'symbol');
    If Value <> '' Then WriteNoErcProp(NoERC, 'Symbol', Value);
    Value := ExtractJsonValue(Params, 'orientation');
    If Value <> '' Then WriteNoErcProp(NoERC, 'Orientation', Value);
    Value := ExtractJsonValue(Params, 'suppress_all');
    If Value <> '' Then WriteNoErcProp(NoERC, 'SuppressAll', Value);
    Value := ExtractJsonValue(Params, 'active');
    If Value <> '' Then WriteNoErcProp(NoERC, 'IsActive', Value);
    Value := ExtractJsonValue(Params, 'error_kinds');
    If Value <> '' Then WriteNoErcProp(NoERC, 'ErrorKindSetToSuppress', Value);
    Value := ExtractJsonValue(Params, 'connection_pairs');
    If Value <> '' Then WriteNoErcProp(NoERC, 'ConnectionPairsToSuppress', Value);

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(NoERC);
    SchRegisterObject(SchDoc, NoERC);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Back := '"symbol":' + NoErcJsonVal(ReadNoErcProp(NoERC, 'Symbol'))
        + ',"orientation":' + NoErcJsonVal(ReadNoErcProp(NoERC, 'Orientation'))
        + ',"suppress_all":' + NoErcJsonVal(ReadNoErcProp(NoERC, 'SuppressAll'))
        + ',"active":' + NoErcJsonVal(ReadNoErcProp(NoERC, 'IsActive'))
        + ',"error_kinds":"' + EscapeJsonString(ReadNoErcProp(NoERC, 'ErrorKindSetToSuppress')) + '"'
        + ',"connection_pairs":"' + EscapeJsonString(ReadNoErcProp(NoERC, 'ConnectionPairsToSuppress')) + '"';
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + ',' + Back + '}');
End;

{..............................................................................}
{ Place a junction at coordinates on active schematic                         }
{ Params: x, y                                                                }
{..............................................................................}

Function Gen_PlaceJunction(Params : String; RequestId : String) : String;
Var
    X, Y : Double;
    SchDoc : ISch_Document;
    Junction : ISch_GraphicalObject;
Begin
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Junction := SchServer.SchObjectFactory(eJunction, eCreate_Default);
    If Junction = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create junction');
        Exit;
    End;

    Junction.Location := Point(MilsToCoordF(X), MilsToCoordF(Y));

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Junction);
    SchRegisterObject(SchDoc, Junction);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) + '}');
End;

{..............................................................................}
{ Gen_PlaceJunctions - Bulk junction placement on the active schematic.        }
{ Params: junctions = 'x=100;y=200~~x=300;y=400~~...'                          }
{..............................................................................}

Function Gen_PlaceJunctions(Params : String; RequestId : String) : String;
Var
    JuncStr, Remaining, Op : String;
    OpCount, Placed, Failed : Integer;
    X, Y : Double;
    SchDoc : ISch_Document;
    Junction : ISch_GraphicalObject;
Begin
    JuncStr := ExtractJsonValue(Params, 'junctions');
    If JuncStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'junctions is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := JuncStr;

    SchBeginEdit(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X := StrToFloatDef(GetBatchField(Op, 'x'), 0);
            Y := StrToFloatDef(GetBatchField(Op, 'y'), 0);

            Junction := SchServer.SchObjectFactory(eJunction, eCreate_Default);
            If Junction = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Junction.Location := Point(MilsToCoordF(X), MilsToCoordF(Y));
            SchDoc.RegisterSchObjectInContainer(Junction);
            SchRegisterObject(SchDoc, Junction);
            Inc(Placed);
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Get comprehensive info about the active document                            }
{ Returns: file_path, kind, sheet_size, title_block, grid_size, unit_system  }
{..............................................................................}

{..............................................................................}
{ Gen_GetSheetFrame - the sheet's real frame and every object's extent.         }
{                                                                              }
{ For the outside-the-sheet check. Frame from SheetSizeX/Y (what Altium draws, }
{ which is not the paper size: an A3 frame is 15500 x 11100 mils), the custom  }
{ flag and size, orientation, border and title block flags, and the template  }
{ file. The template's own graphics are reported apart from the drawing, so a }
{ title block is a keep-out and not an object "outside" anything.              }
{                                                                              }
{ ONLY MEMBERS ALREADY PROVEN: an undeclared identifier halts the script and  }
{ Try/Except cannot catch it. SheetSizeX/Y, TitleBlockOn and                   }
{ WorkspaceOrientation are read elsewhere in this unit; UseCustomSheet,       }
{ GetState_CustomX/Y, BorderOn, GetState_TemplateFileName and eTemplate come  }
{ from working reference scripts (schematic_utils.pas, ExtractTemplate.pas).  }
{..............................................................................}

{ The name of a schematic object type, the inverse of ObjectTypeFromString }
{ and limited to the same constants, so it introduces no new identifier.  }
Function SchTypeName(Id : Integer) : String;
Begin
    If Id = eNetLabel Then Result := 'net_label'
    Else If Id = ePort Then Result := 'port'
    Else If Id = ePowerObject Then Result := 'power_port'
    Else If Id = eSchComponent Then Result := 'component'
    Else If Id = eWire Then Result := 'wire'
    Else If Id = eBus Then Result := 'bus'
    Else If Id = eBusEntry Then Result := 'bus_entry'
    Else If Id = eParameter Then Result := 'parameter'
    Else If Id = eLabel Then Result := 'text'
    Else If Id = eLine Then Result := 'line'
    Else If Id = eRectangle Then Result := 'rectangle'
    Else If Id = eSheetSymbol Then Result := 'sheet_symbol'
    Else If Id = eSheetEntry Then Result := 'sheet_entry'
    Else If Id = eNoERC Then Result := 'no_erc'
    Else If Id = eJunction Then Result := 'junction'
    Else If Id = eImage Then Result := 'image'
    Else If Id = eTextFrame Then Result := 'text_frame'
    Else If Id = eNote Then Result := 'note'
    Else If Id = eProbe Then Result := 'probe'
    Else If Id = eHarnessConnector Then Result := 'harness_connector'
    Else If Id = eCrossSheetConnector Then Result := 'cross_sheet_connector'
    Else If Id = eCompileMask Then Result := 'compile_mask'
    Else If Id = ePolygon Then Result := 'sch_polygon'
    Else If Id = eArc Then Result := 'sch_arc'
    Else If Id = eEllipticalArc Then Result := 'sch_elliptical_arc'
    Else If Id = eEllipse Then Result := 'sch_ellipse'
    Else If Id = eBezier Then Result := 'sch_bezier'
    Else Result := 'type_' + IntToStr(Id);
End;

Function SheetFrameBox(Obj : ISch_GraphicalObject) : String;
Var
    R : TCoordRect;
Begin
    R := Obj.BoundingRectangle;
    Result := '[' + FloatToJsonStr(CoordToMilsF(R.X1)) + ',' + FloatToJsonStr(CoordToMilsF(R.Y1))
        + ',' + FloatToJsonStr(CoordToMilsF(R.X2)) + ',' + FloatToJsonStr(CoordToMilsF(R.Y2)) + ']';
End;

Function Gen_GetSheetFrame(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter, TplIter : ISch_Iterator;
    Obj, Child : ISch_GraphicalObject;
    Comp : ISch_Component;
    Data, Objects, TplBoxes, ObjLabel, TplName : String;
    Count, Limit, TplCount : Integer;
    Truncated : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC', 'No schematic document is active');
        Exit;
    End;
    Limit := StrToIntDef(ExtractJsonValue(Params, 'limit'), 20000);

    Data := '{"file_path":"' + EscapeJsonString(SchDoc.DocumentName) + '"';
    Try Data := Data + ',"frame_x":' + FloatToJsonStr(CoordToMilsF(SchDoc.SheetSizeX))
        + ',"frame_y":' + FloatToJsonStr(CoordToMilsF(SchDoc.SheetSizeY)); Except End;
    Try Data := Data + ',"sheet_style":' + IntToStr(SchDoc.SheetStyle); Except End;
    Try Data := Data + ',"use_custom":' + BoolToJsonStr(SchDoc.UseCustomSheet); Except End;
    Try Data := Data + ',"custom_x":' + FloatToJsonStr(CoordToMilsF(SchDoc.GetState_CustomX))
        + ',"custom_y":' + FloatToJsonStr(CoordToMilsF(SchDoc.GetState_CustomY)); Except End;
    Try Data := Data + ',"orientation":' + IntToStr(SchDoc.WorkspaceOrientation); Except End;
    Try Data := Data + ',"border_on":' + BoolToJsonStr(SchDoc.BorderOn); Except End;
    Try Data := Data + ',"title_block_on":' + BoolToJsonStr(SchDoc.TitleBlockOn); Except End;
    TplName := '';
    Try TplName := SchDoc.GetState_TemplateFileName; Except End;
    Data := Data + ',"template":"' + EscapeJsonString(TplName) + '"';

    Objects := '';
    TplBoxes := '';
    Count := 0;
    TplCount := 0;
    Truncated := False;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.SetState_IterationDepth(eIterateFirstLevel);
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.ObjectId = eTemplate Then
            Begin
                { The template's own graphics: border, title block, logo. }
                TplIter := Obj.SchIterator_Create;
                Try
                    Child := TplIter.FirstSchObject;
                    While Child <> Nil Do
                    Begin
                        If TplCount < 500 Then
                        Begin
                            If TplBoxes <> '' Then TplBoxes := TplBoxes + ',';
                            TplBoxes := TplBoxes + SheetFrameBox(Child);
                            Inc(TplCount);
                        End;
                        Child := TplIter.NextSchObject;
                    End;
                Finally
                    Obj.SchIterator_Destroy(TplIter);
                End;
            End
            Else If Obj.ObjectId = eParameter Then
            Begin
                { A first-level parameter is a document field (Title, Date),
                  read at 0,0 and never drawn as an object. }
            End
            Else
            Begin
                If Count >= Limit Then
                Begin
                    Truncated := True;
                    Break;
                End;
                ObjLabel := '';
                If Obj.ObjectId = eSchComponent Then
                Begin
                    Comp := Obj;
                    Try ObjLabel := Comp.Designator.Text; Except End;
                End;
                If Objects <> '' Then Objects := Objects + ',';
                Objects := Objects + '{"type":"' + SchTypeName(Obj.ObjectId) + '"'
                    + ',"label":"' + EscapeJsonString(ObjLabel) + '"'
                    + ',"bbox":' + SheetFrameBox(Obj) + '}';
                Inc(Count);
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    Data := Data + ',"template_boxes":[' + TplBoxes + ']'
        + ',"objects":[' + Objects + ']'
        + ',"count":' + IntToStr(Count)
        + ',"truncated":' + BoolToJsonStr(Truncated) + '}';
    Result := BuildSuccessResponse(RequestId, Data);
End;

{ Move every drawn object on the sheet by the same offset: components with
  their pins and labels, wires, ports, labels, graphics. The template and the
  document fields stay put. A uniform shift keeps every wire on its pin, so
  connectivity is unchanged. MoveByXY is the documented relative move of
  ISch_GraphicalObject (Altium's UndoRedo sample uses it on a component). }
Function Gen_ShiftSheet(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Moved, Failed : Integer;
    Dx, Dy : Double;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC', 'No schematic document is active');
        Exit;
    End;
    Dx := StrToFloatDef(ExtractJsonValue(Params, 'dx'), 0);
    Dy := StrToFloatDef(ExtractJsonValue(Params, 'dy'), 0);
    Moved := 0;
    Failed := 0;
    If (Dx <> 0) Or (Dy <> 0) Then
    Begin
        SchBeginEdit(SchDoc, '');
        Try
            Iter := SchDoc.SchIterator_Create;
            Try
                Iter.SetState_IterationDepth(eIterateFirstLevel);
                Obj := Iter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    If (Obj.ObjectId <> eTemplate) And (Obj.ObjectId <> eParameter) Then
                    Begin
                        Try
                            SchBeginModify(Obj);
                            Obj.MoveByXY(MilsToCoordF(Dx), MilsToCoordF(Dy));
                            SchEndModify(Obj);
                            RefreshSchObjectRender(Obj);
                            Inc(Moved);
                        Except
                            Inc(Failed);
                        End;
                    End;
                    Obj := Iter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iter);
            End;
        Finally
            SchEndEdit(SchDoc, '');
        End;
    End;
    Result := BuildSuccessResponse(RequestId, '{"dx":' + FloatToJsonStr(Dx)
        + ',"dy":' + FloatToJsonStr(Dy) + ',"moved":' + IntToStr(Moved)
        + ',"failed":' + IntToStr(Failed) + '}');
End;

Function Gen_GetDocumentInfo(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Board : IPCB_Board;
    Data : String;
    SheetStyle, UnitStr : String;
Begin
    Board := GetPCBBoardAnywhere(0);
    SchDoc := SchServer.GetCurrentSchDocument;

    If SchDoc <> Nil Then
    Begin
        // Schematic document info
        Data := '{"file_path":"' + EscapeJsonString(SchDoc.DocumentName) + '"';
        Data := Data + ',"kind":"SCH"';

        // Sheet size
        Try
            Case SchDoc.SheetStyle Of
                0 : SheetStyle := 'A4';
                1 : SheetStyle := 'A3';
                2 : SheetStyle := 'A2';
                3 : SheetStyle := 'A1';
                4 : SheetStyle := 'A0';
                5 : SheetStyle := 'A';
                6 : SheetStyle := 'B';
                7 : SheetStyle := 'C';
                8 : SheetStyle := 'D';
                9 : SheetStyle := 'E';
                10 : SheetStyle := 'Letter';
                11 : SheetStyle := 'Legal';
                12 : SheetStyle := 'Tabloid';
                13 : SheetStyle := 'OrCAD_A';
                14 : SheetStyle := 'OrCAD_B';
                15 : SheetStyle := 'OrCAD_C';
                16 : SheetStyle := 'OrCAD_D';
                17 : SheetStyle := 'OrCAD_E';
            Else
                SheetStyle := 'Custom';
            End;
        Except
            SheetStyle := 'Unknown';
        End;
        Data := Data + ',"sheet_size":"' + SheetStyle + '"';

        // Custom dimensions in mils
        Try
            Data := Data + ',"custom_width":' + FloatToJsonStr(CoordToMilsF(SchDoc.SheetSizeX));
            Data := Data + ',"custom_height":' + FloatToJsonStr(CoordToMilsF(SchDoc.SheetSizeY));
        Except
        End;

        // Title block visibility
        Try
            Data := Data + ',"title_block_on":' + BoolToJsonStr(SchDoc.TitleBlockOn);
        Except
            Data := Data + ',"title_block_on":true';
        End;

        // Snap grid size in mils
        Try
            Data := Data + ',"snap_grid":' + FloatToJsonStr(CoordToMilsF(SchDoc.SnapGridSize));
        Except
        End;

        // Visible grid size in mils
        Try
            Data := Data + ',"visible_grid":' + FloatToJsonStr(CoordToMilsF(SchDoc.VisibleGridSize));
        Except
        End;

        // Whether each grid is switched on (issue #78)
        Try
            Data := Data + ',"snap_grid_on":' + BoolToJsonStr(SchDoc.SnapGridOn);
        Except
        End;
        Try
            Data := Data + ',"visible_grid_on":' + BoolToJsonStr(SchDoc.VisibleGridOn);
        Except
        End;

        // Unit system, ISch_Document.UnitSystem returns a TUnitSystem enum
        // (eImperial / eMetric). TUnit has finer granularity but UnitSystem is
        // the right read for a simple "metric vs imperial" field.
        Try
            If SchDoc.UnitSystem = eMetric Then
                UnitStr := 'metric'
            Else
                UnitStr := 'imperial';
            Data := Data + ',"unit_system":"' + UnitStr + '"';
        Except End;

        Data := Data + '}';
        Result := BuildSuccessResponse(RequestId, Data);
    End
    Else If Board <> Nil Then
    Begin
        // PCB document info
        Data := '{"file_path":"' + EscapeJsonString(Board.FileName) + '"';
        Data := Data + ',"kind":"PCB"';
        Data := Data + ',"origin_x":' + FloatToJsonStr(CoordToMilsF(Board.XOrigin));
        Data := Data + ',"origin_y":' + FloatToJsonStr(CoordToMilsF(Board.YOrigin));

        Try
            Data := Data + ',"snap_grid":' + FloatToJsonStr(CoordToMilsF(Board.SnapGridSizeX));
        Except
        End;

        Data := Data + '}';
        Result := BuildSuccessResponse(RequestId, Data);
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NO_DOCUMENT', 'No active schematic or PCB document');
End;

{ SCHEMATIC EDITOR PREFERENCES (Preferences > Schematic), Altium's own, kept  }
{ for every sheet. 'b' a switch, 'i' a whole number of 0 or more, 't' text,   }
{ '' not one of these.                                                        }
Function SchOptionKind(Name : String) : String;
Begin
    Result := '';
    If (Name = 'SnapToHotSpot') Or (Name = 'MaintainOrthogonal') Or
       (Name = 'ComponentsCutWires') Or (Name = 'OptimizePolylines') Or
       (Name = 'SingleSlashNegation') Or (Name = 'ConvertSpecialStrings') Or
       (Name = 'MarkManualParameters') Or (Name = 'ShowPinDirection') Or
       (Name = 'AutoZoom') Or (Name = 'ClickClearsSelection') Or
       (Name = 'DoubleClickRunsInspector') Or (Name = 'ExpandComponentDesignators') Or
       (Name = 'ExpandNetLabelsText') Or (Name = 'ExpandPortsName') Or
       (Name = 'ExpandSheetNumber') Or (Name = 'ExpandDocumentNumber') Or
       (Name = 'WatermarkReadOnlySheet') Or (Name = 'AlwaysDrag') Or
       (Name = 'DisplayPrinterFonts') Or (Name = 'StringIncRemoveLeadingZero') Or
       (Name = 'CtrlDbleClickGoesDown') Or (Name = 'ConfirmSelectionMemoryClear') Or
       (Name = 'AllowDeviceSheetEditing') Or (Name = 'WatermarkDeviceSheet') Then Result := 'b'
    Else If (Name = 'HotSpotGridDistance') Or (Name = 'UndoRedoStackSize') Or
       (Name = 'PinNameMargin') Or (Name = 'PinNumberMargin') Or
       (Name = 'MultiPartNamingMethod') Or (Name = 'CutterGridSizeMultiple') Or
       (Name = 'CutterFixedLength') Or (Name = 'PhysicalViewDimLevel') Then Result := 'i'
    Else If (Name = 'StringIncA') Or (Name = 'StringIncB') Or
       (Name = 'DefaultPowerGndName') Or (Name = 'DefaultSignalGndName') Or
       (Name = 'DefaultEarthName') Or (Name = 'DefaultTemplateFileName') Then Result := 't';
End;

{ The value as JSON text. }
Function SchOptionGet(Prefs : ISch_Preferences; Name : String) : String;
Begin
    Result := '';
    If Name = 'SnapToHotSpot' Then Result := BoolToJsonStr(Prefs.SnapToHotSpot)
    Else If Name = 'MaintainOrthogonal' Then Result := BoolToJsonStr(Prefs.MaintainOrthogonal)
    Else If Name = 'ComponentsCutWires' Then Result := BoolToJsonStr(Prefs.ComponentsCutWires)
    Else If Name = 'OptimizePolylines' Then Result := BoolToJsonStr(Prefs.OptimizePolylines)
    Else If Name = 'SingleSlashNegation' Then Result := BoolToJsonStr(Prefs.SingleSlashNegation)
    Else If Name = 'ConvertSpecialStrings' Then Result := BoolToJsonStr(Prefs.ConvertSpecialStrings)
    Else If Name = 'MarkManualParameters' Then Result := BoolToJsonStr(Prefs.MarkManualParameters)
    Else If Name = 'ShowPinDirection' Then Result := BoolToJsonStr(Prefs.ShowPinDirection)
    Else If Name = 'AutoZoom' Then Result := BoolToJsonStr(Prefs.AutoZoom)
    Else If Name = 'ClickClearsSelection' Then Result := BoolToJsonStr(Prefs.ClickClearsSelection)
    Else If Name = 'DoubleClickRunsInspector' Then Result := BoolToJsonStr(Prefs.DoubleClickRunsInspector)
    Else If Name = 'ExpandComponentDesignators' Then Result := BoolToJsonStr(Prefs.ExpandComponentDesignators)
    Else If Name = 'ExpandNetLabelsText' Then Result := BoolToJsonStr(Prefs.ExpandNetLabelsText)
    Else If Name = 'ExpandPortsName' Then Result := BoolToJsonStr(Prefs.ExpandPortsName)
    Else If Name = 'ExpandSheetNumber' Then Result := BoolToJsonStr(Prefs.ExpandSheetNumber)
    Else If Name = 'ExpandDocumentNumber' Then Result := BoolToJsonStr(Prefs.ExpandDocumentNumber)
    Else If Name = 'WatermarkReadOnlySheet' Then Result := BoolToJsonStr(Prefs.WatermarkReadOnlySheet)
    Else If Name = 'AlwaysDrag' Then Result := BoolToJsonStr(Prefs.AlwaysDrag)
    Else If Name = 'DisplayPrinterFonts' Then Result := BoolToJsonStr(Prefs.DisplayPrinterFonts)
    Else If Name = 'StringIncRemoveLeadingZero' Then Result := BoolToJsonStr(Prefs.StringIncRemoveLeadingZero)
    Else If Name = 'CtrlDbleClickGoesDown' Then Result := BoolToJsonStr(Prefs.CtrlDbleClickGoesDown)
    Else If Name = 'ConfirmSelectionMemoryClear' Then Result := BoolToJsonStr(Prefs.ConfirmSelectionMemoryClear)
    Else If Name = 'AllowDeviceSheetEditing' Then Result := BoolToJsonStr(Prefs.AllowDeviceSheetEditing)
    Else If Name = 'WatermarkDeviceSheet' Then Result := BoolToJsonStr(Prefs.WatermarkDeviceSheet)
    Else If Name = 'CutterGridSizeMultiple' Then Result := IntToStr(Prefs.CutterGridSizeMultiple)
    Else If Name = 'CutterFixedLength' Then Result := IntToStr(Prefs.CutterFixedLength)
    Else If Name = 'PhysicalViewDimLevel' Then Result := IntToStr(Prefs.PhysicalViewDimLevel)
    Else If Name = 'HotSpotGridDistance' Then Result := IntToStr(Prefs.HotSpotGridDistance)
    Else If Name = 'UndoRedoStackSize' Then Result := IntToStr(Prefs.UndoRedoStackSize)
    Else If Name = 'PinNameMargin' Then Result := IntToStr(Prefs.PinNameMargin)
    Else If Name = 'PinNumberMargin' Then Result := IntToStr(Prefs.PinNumberMargin)
    Else If Name = 'MultiPartNamingMethod' Then Result := IntToStr(Prefs.MultiPartNamingMethod)
    Else If Name = 'StringIncA' Then Result := '"' + EscapeJsonString(Prefs.StringIncA) + '"'
    Else If Name = 'StringIncB' Then Result := '"' + EscapeJsonString(Prefs.StringIncB) + '"'
    Else If Name = 'DefaultPowerGndName' Then Result := '"' + EscapeJsonString(Prefs.DefaultPowerGndName) + '"'
    Else If Name = 'DefaultSignalGndName' Then Result := '"' + EscapeJsonString(Prefs.DefaultSignalGndName) + '"'
    Else If Name = 'DefaultEarthName' Then Result := '"' + EscapeJsonString(Prefs.DefaultEarthName) + '"'
    Else If Name = 'DefaultTemplateFileName' Then Result := '"' + EscapeJsonString(Prefs.DefaultTemplateFileName) + '"';
End;

{ Write one; False when the value is not of the option's kind. }
Function SchOptionSet(Prefs : ISch_Preferences; Name : String; Value : String) : Boolean;
Var
    Kind : String;
    B : Boolean;
    N : Integer;
Begin
    Result := False;
    Kind := SchOptionKind(Name);
    B := False;
    N := 0;
    If Kind = 'b' Then
    Begin
        If LowerCase(Value) = 'true' Then B := True
        Else If LowerCase(Value) <> 'false' Then Exit;
    End
    Else If Kind = 'i' Then
    Begin
        If Not IsIntStr(Value) Then Exit;
        N := StrToInt(Value);
        If N < 0 Then Exit;
    End
    Else If Kind <> 't' Then Exit;
    If Name = 'SnapToHotSpot' Then Prefs.SnapToHotSpot := B
    Else If Name = 'MaintainOrthogonal' Then Prefs.MaintainOrthogonal := B
    Else If Name = 'ComponentsCutWires' Then Prefs.ComponentsCutWires := B
    Else If Name = 'OptimizePolylines' Then Prefs.OptimizePolylines := B
    Else If Name = 'SingleSlashNegation' Then Prefs.SingleSlashNegation := B
    Else If Name = 'ConvertSpecialStrings' Then Prefs.ConvertSpecialStrings := B
    Else If Name = 'MarkManualParameters' Then Prefs.MarkManualParameters := B
    Else If Name = 'ShowPinDirection' Then Prefs.ShowPinDirection := B
    Else If Name = 'AutoZoom' Then Prefs.AutoZoom := B
    Else If Name = 'ClickClearsSelection' Then Prefs.ClickClearsSelection := B
    Else If Name = 'DoubleClickRunsInspector' Then Prefs.DoubleClickRunsInspector := B
    Else If Name = 'ExpandComponentDesignators' Then Prefs.ExpandComponentDesignators := B
    Else If Name = 'ExpandNetLabelsText' Then Prefs.ExpandNetLabelsText := B
    Else If Name = 'ExpandPortsName' Then Prefs.ExpandPortsName := B
    Else If Name = 'ExpandSheetNumber' Then Prefs.ExpandSheetNumber := B
    Else If Name = 'ExpandDocumentNumber' Then Prefs.ExpandDocumentNumber := B
    Else If Name = 'WatermarkReadOnlySheet' Then Prefs.WatermarkReadOnlySheet := B
    Else If Name = 'AlwaysDrag' Then Prefs.AlwaysDrag := B
    Else If Name = 'DisplayPrinterFonts' Then Prefs.DisplayPrinterFonts := B
    Else If Name = 'StringIncRemoveLeadingZero' Then Prefs.StringIncRemoveLeadingZero := B
    Else If Name = 'CtrlDbleClickGoesDown' Then Prefs.CtrlDbleClickGoesDown := B
    Else If Name = 'ConfirmSelectionMemoryClear' Then Prefs.ConfirmSelectionMemoryClear := B
    Else If Name = 'AllowDeviceSheetEditing' Then Prefs.AllowDeviceSheetEditing := B
    Else If Name = 'WatermarkDeviceSheet' Then Prefs.WatermarkDeviceSheet := B
    Else If Name = 'CutterGridSizeMultiple' Then Prefs.CutterGridSizeMultiple := N
    Else If Name = 'CutterFixedLength' Then Prefs.CutterFixedLength := N
    Else If Name = 'PhysicalViewDimLevel' Then Prefs.PhysicalViewDimLevel := N
    Else If Name = 'HotSpotGridDistance' Then Prefs.HotSpotGridDistance := N
    Else If Name = 'UndoRedoStackSize' Then Prefs.UndoRedoStackSize := N
    Else If Name = 'PinNameMargin' Then Prefs.PinNameMargin := N
    Else If Name = 'PinNumberMargin' Then Prefs.PinNumberMargin := N
    Else If Name = 'MultiPartNamingMethod' Then Prefs.MultiPartNamingMethod := N
    Else If Name = 'StringIncA' Then Prefs.StringIncA := Value
    Else If Name = 'StringIncB' Then Prefs.StringIncB := Value
    Else If Name = 'DefaultPowerGndName' Then Prefs.DefaultPowerGndName := Value
    Else If Name = 'DefaultSignalGndName' Then Prefs.DefaultSignalGndName := Value
    Else If Name = 'DefaultEarthName' Then Prefs.DefaultEarthName := Value
    Else If Name = 'DefaultTemplateFileName' Then Prefs.DefaultTemplateFileName := Value;
    Result := True;
End;

{..............................................................................}
{ Gen_SchEditorOptions - read, or set, schematic editor preferences by name.   }
{ Params: names (comma-separated, to read), set ('Name=Value|Name=Value').    }
{ Every option set is read back: one that reads otherwise is in not_applied, }
{ a value of the wrong kind in refused, a name not offered in unknown.       }
{..............................................................................}
Function Gen_SchEditorOptions(Params : String; RequestId : String) : String;
Var
    Prefs : ISch_Preferences;
    Names, SetStr, Item, Name, Value, Items, NotApplied, Refused, Unknown, Got, Wanted : String;
    CommaPos, PipePos, EqPos : Integer;
    Changed : Boolean;
Begin
    Names := ExtractJsonValue(Params, 'names');
    SetStr := ExtractJsonValue(Params, 'set');
    Prefs := Nil;
    Try Prefs := SchServer.Preferences; Except Prefs := Nil; End;
    If Prefs = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PREFERENCES', 'Could not reach the schematic editor preferences');
        Exit;
    End;
    Items := ''; NotApplied := ''; Refused := ''; Unknown := '';
    Changed := False;
    While SetStr <> '' Do
    Begin
        PipePos := Pos('|', SetStr);
        If PipePos = 0 Then Begin Item := SetStr; SetStr := ''; End
        Else Begin Item := Copy(SetStr, 1, PipePos - 1); SetStr := Copy(SetStr, PipePos + 1, Length(SetStr)); End;
        EqPos := Pos('=', Item);
        If EqPos = 0 Then Continue;
        Name := Trim(Copy(Item, 1, EqPos - 1));
        Value := Copy(Item, EqPos + 1, Length(Item));
        If SchOptionKind(Name) = '' Then
        Begin
            If Unknown <> '' Then Unknown := Unknown + ',';
            Unknown := Unknown + '"' + EscapeJsonString(Name) + '"';
            Continue;
        End;
        If SchOptionKind(Name) <> 't' Then Value := Trim(Value);
        If Not SchOptionSet(Prefs, Name, Value) Then
        Begin
            If Refused <> '' Then Refused := Refused + ',';
            Refused := Refused + '"' + EscapeJsonString(Name) + '"';
            Continue;
        End;
        Changed := True;
        Got := SchOptionGet(Prefs, Name);
        If SchOptionKind(Name) = 't' Then Wanted := '"' + EscapeJsonString(Value) + '"'
        Else Wanted := LowerCase(Value);
        If Got <> Wanted Then
        Begin
            If NotApplied <> '' Then NotApplied := NotApplied + ',';
            NotApplied := NotApplied + '"' + Name + '"';
        End;
        If Pos(',' + Name + ',', ',' + Names + ',') = 0 Then
        Begin
            If Names <> '' Then Names := Names + ',';
            Names := Names + Name;
        End;
    End;
    While Names <> '' Do
    Begin
        CommaPos := Pos(',', Names);
        If CommaPos = 0 Then Begin Name := Names; Names := ''; End
        Else Begin Name := Copy(Names, 1, CommaPos - 1); Names := Copy(Names, CommaPos + 1, Length(Names)); End;
        Name := Trim(Name);
        If Name = '' Then Continue;
        If SchOptionKind(Name) = '' Then
        Begin
            If Pos('"' + Name + '"', Unknown) = 0 Then
            Begin
                If Unknown <> '' Then Unknown := Unknown + ',';
                Unknown := Unknown + '"' + EscapeJsonString(Name) + '"';
            End;
            Continue;
        End;
        If Items <> '' Then Items := Items + ',';
        Items := Items + '"' + Name + '":' + SchOptionGet(Prefs, Name);
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonRaw('options', '{' + Items + '}') + ','
        + JsonBool('changed', Changed) + ','
        + JsonRaw('not_applied', '[' + NotApplied + ']') + ','
        + JsonRaw('refused', '[' + Refused + ']') + ','
        + JsonRaw('unknown', '[' + Unknown + ']')));
End;

{..............................................................................}
{ Gen_OptimizeWires - Altium's own wire tidy on the active sheet: wires that  }
{ continue one another are joined and overlaps trimmed                        }
{ (ISch_Sheet.OptimizeUseOfPolylines). Connectivity is unchanged; the wire   }
{ count and the vertex count are reported before and after.                  }
{..............................................................................}
Procedure CountSheetWires(SchDoc : ISch_Document; Var Wires : Integer; Var Vertices : Integer);
Var
    Iter : ISch_Iterator;
    Wire : ISch_GraphicalObject;
    N : Integer;
Begin
    Wires := 0;
    Vertices := 0;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eWire));
        Wire := Iter.FirstSchObject;
        While Wire <> Nil Do
        Begin
            Inc(Wires);
            N := 0;
            Try N := Wire.GetState_VerticesCount; Except N := 0; End;
            Vertices := Vertices + N;
            Wire := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
End;

Function Gen_OptimizeWires(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Sheet : ISch_Sheet;
    WiresBefore, VertsBefore, WiresAfter, VertsAfter : Integer;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    CountSheetWires(SchDoc, WiresBefore, VertsBefore);
    Sheet := SchDoc;
    SchBeginEdit(SchDoc, '');
    Try
        Sheet.OptimizeUseOfPolylines;
    Finally
        SchEndEdit(SchDoc, 'Edit');
    End;
    SchDoc.GraphicallyInvalidate;
    CountSheetWires(SchDoc, WiresAfter, VertsAfter);
    If (WiresAfter <> WiresBefore) Or (VertsAfter <> VertsBefore) Then
        MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('sheet', SchDoc.DocumentName) + ','
        + JsonInt('wires_before', WiresBefore) + ','
        + JsonInt('wires_after', WiresAfter) + ','
        + JsonInt('vertices_before', VertsBefore) + ','
        + JsonInt('vertices_after', VertsAfter)));
End;

{..............................................................................}
{ Gen_ReferenceZones - the border zone ('B3') each part, net label, port,     }
{ power port, sheet symbol and off-sheet connector sits in on the active    }
{ sheet, the way a printed drawing is read. The zone is Altium's own answer }
{ (ISch_Document.ObjectReferenceZone); a sheet without zones answers ''.    }
{ Params: kinds (comma-separated: component, net_label, port, power_port,  }
{ sheet_symbol, cross_sheet; empty = all).                                 }
{..............................................................................}
Function Gen_ReferenceZones(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Lbl : ISch_NetLabel;
    Port : ISch_Port;
    Pwr : ISch_PowerObject;
    Sym : ISch_SheetSymbol;
    Kinds, Kind, Text, Zone, Items : String;
    Oid, Count : Integer;
Begin
    Kinds := LowerCase(ExtractJsonValue(Params, 'kinds'));
    If Kinds = '' Then Kinds := 'component,net_label,port,power_port,sheet_symbol,cross_sheet';
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    Items := '';
    Count := 0;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent, eNetLabel, ePort, ePowerObject,
            eSheetSymbol, eCrossSheetConnector));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Oid := Obj.ObjectId;
            Kind := '';
            Text := '';
            If Oid = eSchComponent Then
            Begin
                Comp := Obj;
                Kind := 'component';
                Try Text := Comp.Designator.Text; Except End;
            End
            Else If Oid = eNetLabel Then
            Begin
                Lbl := Obj;
                Kind := 'net_label';
                Text := Lbl.Text;
            End
            Else If Oid = ePort Then
            Begin
                Port := Obj;
                Kind := 'port';
                Text := Port.Name;
            End
            Else If Oid = ePowerObject Then
            Begin
                Pwr := Obj;
                Kind := 'power_port';
                Text := Pwr.Text;
            End
            Else If Oid = eCrossSheetConnector Then
            Begin
                Pwr := Obj;
                Kind := 'cross_sheet';
                Text := Pwr.Text;
            End
            Else If Oid = eSheetSymbol Then
            Begin
                Sym := Obj;
                Kind := 'sheet_symbol';
                Try Text := Sym.SheetName.Text; Except End;
            End;
            If (Kind <> '') And (Pos(',' + Kind + ',', ',' + Kinds + ',') > 0) Then
            Begin
                Zone := '';
                Try Zone := SchDoc.ObjectReferenceZone(Obj); Except Zone := ''; End;
                If Items <> '' Then Items := Items + ',';
                Items := Items + JsonObj(JsonStr('kind', Kind) + ','
                    + JsonStr('text', Text) + ','
                    + JsonStr('zone', Zone) + ','
                    + JsonRaw('x', FloatToJsonStr(CoordToMilsF(Obj.Location.X))) + ','
                    + JsonRaw('y', FloatToJsonStr(CoordToMilsF(Obj.Location.Y))));
                Inc(Count);
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;
    Result := BuildSuccessResponse(RequestId, JsonObj(
        JsonStr('sheet', SchDoc.DocumentName) + ','
        + JsonInt('count', Count) + ','
        + JsonRaw('objects', '[' + Items + ']')));
End;

{..............................................................................}
{ Set snap grid and visible grid size for the active schematic                }
{ Params: snap_grid, visible_grid, hotspot_grid (in mils), snap_grid_on,      }
{ visible_grid_on, hotspot_grid_on. The hotspot grid is the electrical grid: }
{ how far a wire end is pulled onto a pin or wire.                           }
{..............................................................................}

Function Gen_SetGrid(Params : String; RequestId : String) : String;
Var
    SnapGrid, VisibleGrid, HotGrid : Double;
    SnapOnStr, VisibleOnStr, HotOnStr : String;
    SetSnapOn, SetVisibleOn, SetHotOn : Boolean;
    SchDoc : ISch_Document;
Begin
    SnapGrid := StrToFloatDef(ExtractJsonValue(Params, 'snap_grid'), 0);
    VisibleGrid := StrToFloatDef(ExtractJsonValue(Params, 'visible_grid'), 0);
    SnapOnStr := LowerCase(ExtractJsonValue(Params, 'snap_grid_on'));
    VisibleOnStr := LowerCase(ExtractJsonValue(Params, 'visible_grid_on'));
    SetSnapOn := (SnapOnStr = 'true') Or (SnapOnStr = 'false');
    SetVisibleOn := (VisibleOnStr = 'true') Or (VisibleOnStr = 'false');
    HotGrid := StrToFloatDef(ExtractJsonValue(Params, 'hotspot_grid'), 0);
    HotOnStr := LowerCase(ExtractJsonValue(Params, 'hotspot_grid_on'));
    SetHotOn := (HotOnStr = 'true') Or (HotOnStr = 'false');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    If (SnapGrid <= 0) And (VisibleGrid <= 0) And (HotGrid <= 0) And (Not SetSnapOn)
        And (Not SetVisibleOn) And (Not SetHotOn) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'Give snap_grid, visible_grid or hotspot_grid (mils) and/or snap_grid_on, visible_grid_on or hotspot_grid_on (true/false)');
        Exit;
    End;

    SchBeginEdit(SchDoc, '');

    If SnapGrid > 0 Then
        SchDoc.SnapGridSize := MilsToCoordF(SnapGrid);
    If VisibleGrid > 0 Then
        SchDoc.VisibleGridSize := MilsToCoordF(VisibleGrid);
    { The on/off switches (issue #78). A write the SDK refuses is reported
      back as the value it still holds, not hidden as success. }
    If SetSnapOn Then
    Begin
        Try SchDoc.SnapGridOn := (SnapOnStr = 'true'); Except { read back below } End;
    End;
    If SetVisibleOn Then
    Begin
        Try SchDoc.VisibleGridOn := (VisibleOnStr = 'true'); Except { read back below } End;
    End;
    If HotGrid > 0 Then
        SchDoc.HotspotGridSize := MilsToCoordF(HotGrid);
    If SetHotOn Then
        SchDoc.HotspotGridOn := (HotOnStr = 'true');

    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    SnapOnStr := 'null';
    Try SnapOnStr := BoolToJsonStr(SchDoc.SnapGridOn); Except End;
    VisibleOnStr := 'null';
    Try VisibleOnStr := BoolToJsonStr(SchDoc.VisibleGridOn); Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true' +
        ',"snap_grid":' + FloatToJsonStr(CoordToMilsF(SchDoc.SnapGridSize)) +
        ',"visible_grid":' + FloatToJsonStr(CoordToMilsF(SchDoc.VisibleGridSize)) +
        ',"snap_grid_on":' + SnapOnStr +
        ',"visible_grid_on":' + VisibleOnStr +
        ',"hotspot_grid":' + FloatToJsonStr(CoordToMilsF(SchDoc.HotspotGridSize)) +
        ',"hotspot_grid_on":' + BoolToJsonStr(SchDoc.HotspotGridOn) + '}');
End;

{..............................................................................}
{ Set the active schematic unit system via ISch_Document.SetState_Unit.       }
{ Accepts 'mil', 'inch', 'dxp', 'auto_imperial', 'mm', 'cm', 'm',             }
{ 'auto_metric'. Returns the resulting unit_system (imperial/metric).         }
{..............................................................................}

Function Gen_SetSchUnits(Params : String; RequestId : String) : String;
Var
    UnitStr : String;
    SchDoc : ISch_Document;
    Target : TUnit;
    SystemStr : String;
Begin
    UnitStr := LowerCase(ExtractJsonValue(Params, 'unit'));
    If UnitStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'unit required (mil, inch, dxp, auto_imperial, mm, cm, m, auto_metric)');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { TUnit = (eMil, eMM, eIN, eCM, eDXP, eM, eAutoImperial, eAutoMetric).      }
    If UnitStr = 'mil' Then Target := eMil
    Else If UnitStr = 'inch' Then Target := eIN
    Else If UnitStr = 'in' Then Target := eIN
    Else If UnitStr = 'dxp' Then Target := eDXP
    Else If UnitStr = 'auto_imperial' Then Target := eAutoImperial
    Else If UnitStr = 'mm' Then Target := eMM
    Else If UnitStr = 'cm' Then Target := eCM
    Else If UnitStr = 'm' Then Target := eM
    Else If UnitStr = 'auto_metric' Then Target := eAutoMetric
    Else
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_UNIT',
            'Unknown unit "' + UnitStr + '"');
        Exit;
    End;

    SchBeginEdit(SchDoc, '');
    Try SchDoc.SetState_Unit(Target); Except NoteWriteFailed('sheet Unit'); End;
    SchEndEdit(SchDoc, 'Set schematic unit');
    SchDoc.GraphicallyInvalidate;

    If SchDoc.UnitSystem = eMetric Then SystemStr := 'metric'
    Else SystemStr := 'imperial';

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"unit":"' + EscapeJsonString(UnitStr) + '"'
        + ',"unit_system":"' + SystemStr + '"}');
End;

{..............................................................................}
{ Place an image on the active schematic via RunProcess                       }
{ Params: image_path, x, y, width, height (in mils)                          }
{..............................................................................}

Function Gen_PlaceImage(Params : String; RequestId : String) : String;
Var
    ImagePath : String;
    X, Y, W, H : Double;
    SchDoc : ISch_Document;
    Img : ISch_GraphicalObject;
Begin
    ImagePath := ExtractJsonValue(Params, 'image_path');
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    W := StrToFloatDef(ExtractJsonValue(Params, 'width'), 500);
    H := StrToFloatDef(ExtractJsonValue(Params, 'height'), 500);

    If ImagePath = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'image_path parameter is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Img := SchServer.SchObjectFactory(eImage, eCreate_Default);
    If Img = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create image object');
        Exit;
    End;

    Img.Location := Point(MilsToCoordF(X), MilsToCoordF(Y));
    Img.Corner := Point(MilsToCoordF(X + W), MilsToCoordF(Y + H));
    Try
        Img.FileName := ImagePath;
    Except
        NoteWriteFailed('Image.FileName');
    End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(Img);
    SchRegisterObject(SchDoc, Img);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"image_path":"' + EscapeJsonString(ImagePath) +
        '","x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y) +
        ',"width":' + FloatToJsonStr(W) + ',"height":' + FloatToJsonStr(H) + '}');
End;

{..............................................................................}
{ Pin map of a placed part: ';D=x,y;' per pin of the part shown, with x,y the }
{ connection point in mils, where a wire has to touch for the pin to connect. }
{..............................................................................}

Function SchPinMap(Comp : ISch_Component) : String;
Var
    PIter : ISch_Iterator;
    Pin : ISch_Pin;
    PartId, Owner, Orient, PLen, PX, PY : Integer;
Begin
    Result := ';';
    PartId := 0;
    Try PartId := Comp.CurrentPartID; Except End;
    PIter := Comp.SchIterator_Create;
    Try
        PIter.AddFilter_ObjectSet(MkSet(ePin));
        Pin := PIter.FirstSchObject;
        While Pin <> Nil Do
        Begin
            Owner := 0;
            Try Owner := Pin.OwnerPartId; Except End;
            If (Owner = 0) Or (Owner = PartId) Then
            Begin
                Orient := 0; PLen := 0; PX := 0; PY := 0;
                Try Orient := Pin.Orientation; Except End;
                Try PLen := Pin.PinLength; Except End;
                Try PX := Pin.Location.X; Except End;
                Try PY := Pin.Location.Y; Except End;
                Result := Result + Pin.Designator + '='
                    + FloatToJsonStr(CoordToMilsF(PinEndX(PX, Orient, PLen))) + ','
                    + FloatToJsonStr(CoordToMilsF(PinEndY(PY, Orient, PLen))) + ';';
            End;
            Pin := PIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(PIter);
    End;
End;

{ The x,y a pin map gives for one pin designator, or '' when it has none. }
Function PinMapValue(Map : String; Desig : String) : String;
Var
    P : Integer;
    Rest : String;
Begin
    Result := '';
    P := Pos(';' + Desig + '=', Map);
    If P = 0 Then Exit;
    Rest := Copy(Map, P + Length(Desig) + 2, Length(Map));
    Result := Copy(Rest, 1, Pos(';', Rest) - 1);
End;

{ Names of the parameters on Comp, as ';Name;Name;'. }
Function SchParamNames(Comp : ISch_Component) : String;
Var
    PIter : ISch_Iterator;
    Param : ISch_Parameter;
Begin
    Result := ';';
    PIter := Comp.SchIterator_Create;
    Try
        PIter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := PIter.FirstSchObject;
        While Param <> Nil Do
        Begin
            Try Result := Result + Param.Name + ';'; Except End;
            Param := PIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(PIter);
    End;
End;

{ ',"D"' for each entry of an ';X;Y;' list, without the leading comma. }
Function NameListJson(List : String) : String;
Var
    Rest, Item : String;
    P : Integer;
Begin
    Result := '';
    Rest := Copy(List, 2, Length(List));
    While Rest <> '' Do
    Begin
        P := Pos(';', Rest);
        If P = 0 Then Break;
        Item := Copy(Rest, 1, P - 1);
        Rest := Copy(Rest, P + 1, Length(Rest));
        If Item = '' Then Continue;
        If Result <> '' Then Result := Result + ',';
        Result := Result + '"' + EscapeJsonString(Item) + '"';
    End;
End;

{..............................................................................}
{ Swap a placed part's symbol for one from a .SchLib, in place.               }
{                                                                             }
{ proj_replace_component used to rewrite LibReference, DesignItemId and the   }
{ library name and report success, while the symbol and its pins on the sheet }
{ stayed exactly as they were (issue #94). This places the new symbol where   }
{ the old one is, compares every pin's connection point, and only then        }
{ removes the old part, so wires still land on pins. A pin that would move or }
{ vanish refuses the swap with nothing changed, unless allow_pin_changes.     }
{ The designator and UniqueId carry over, so the board keeps its link.        }
{..............................................................................}

Function SwapSchSymbol(SchDoc : ISch_Document; OldComp : ISch_Component;
    Designator, LibPath, LibRef : String; AllowPinChanges : Boolean;
    RequestId : String) : String;
Var
    NewComp : ISch_Component;
    SrvDoc : IServerDocument;
    ResolvedLib, Reason, AvailHint : String;
    OldMap, NewMap, Rest, Entry, D, V, NV : String;
    Missing, Moved, Added, OldParams, NewParams, Dropped : String;
    OldId, NewId, OldLibRef : String;
    OldX, OldY, Orient, PartId, P, Kept : Integer;
    Mirrored : Boolean;
Begin
    ResolvedLib := ResolveSchLibForLoad(LibPath, Reason);
    If ResolvedLib = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'LIBRARY_NOT_LOADABLE', Reason);
        Exit;
    End;
    LibPath := ResolvedLib;
    If Not ResolveLibRef(LibPath, LibRef, AvailHint) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'lib_reference "' + LibRef + '" not found in ' + LibPath +
            '. Sample of available names: ' + AvailHint + '. Nothing was changed.');
        Exit;
    End;

    { Load before the sheet's transaction, as placement does: the retry can
      open the library document, which must not nest inside the edit. }
    NewComp := Nil;
    Try NewComp := SchServer.LoadComponentFromLibrary(LibRef, LibPath); Except NewComp := Nil; End;
    If NewComp = Nil Then
    Begin
        If FocusSchLib(LibPath) <> Nil Then
        Begin
            Try NewComp := SchServer.LoadComponentFromLibrary(LibRef, LibPath); Except NewComp := Nil; End;
        End;
        Try
            SrvDoc := Client.GetDocumentByPath(SchDoc.DocumentName);
            If SrvDoc <> Nil Then Client.ShowDocument(SrvDoc);
        Except End;
    End;
    If NewComp = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'LOAD_FAILED',
            'LoadComponentFromLibrary returned nil for ' + LibRef + ' from ' +
            LibPath + '. Nothing was changed.');
        Exit;
    End;

    OldX := 0; OldY := 0; Orient := 0; PartId := 1; Mirrored := False;
    OldId := ''; OldLibRef := '';
    Try OldX := OldComp.Location.X; Except End;
    Try OldY := OldComp.Location.Y; Except End;
    Try Orient := OldComp.Orientation; Except End;
    Try Mirrored := OldComp.IsMirrored; Except End;
    Try PartId := OldComp.CurrentPartID; Except End;
    Try OldId := OldComp.UniqueId; Except End;
    Try OldLibRef := OldComp.LibReference; Except End;
    OldMap := SchPinMap(OldComp);
    OldParams := SchParamNames(OldComp);

    SchBeginEdit(SchDoc, '');
    Try
        Try SchDoc.AddSchObject(NewComp); Except NoteWriteFailed('swap: add new symbol'); End;
        SchRegisterObject(SchDoc, NewComp);
        If PartId > 1 Then
        Begin
            Try NewComp.CurrentPartID := PartId; Except NoteWriteFailed('swap: CurrentPartID'); End;
        End;
        Try NewComp.MoveToXY(OldX, OldY); Except NoteWriteFailed('swap: MoveToXY'); End;
        Try NewComp.SetState_Orientation(Orient); Except NoteWriteFailed('swap: Orientation'); End;
        If Mirrored Then
        Begin
            Try NewComp.SetState_IsMirrored(True); Except NoteWriteFailed('swap: IsMirrored'); End;
        End;
        Try NewComp.Designator.Text := Designator; Except NoteWriteFailed('swap: Designator'); End;

        NewMap := SchPinMap(NewComp);
        Missing := ';'; Moved := ';'; Added := ';'; Kept := 0;
        Rest := Copy(OldMap, 2, Length(OldMap));
        While Rest <> '' Do
        Begin
            P := Pos(';', Rest);
            If P = 0 Then Break;
            Entry := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
            P := Pos('=', Entry);
            If P = 0 Then Continue;
            D := Copy(Entry, 1, P - 1);
            V := Copy(Entry, P + 1, Length(Entry));
            NV := PinMapValue(NewMap, D);
            If NV = '' Then
            Begin
                Missing := Missing + D + ';';
            End
            Else If NV <> V Then
            Begin
                Moved := Moved + D + ';';
            End
            Else
            Begin
                Inc(Kept);
            End;
        End;
        Rest := Copy(NewMap, 2, Length(NewMap));
        While Rest <> '' Do
        Begin
            P := Pos(';', Rest);
            If P = 0 Then Break;
            Entry := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
            P := Pos('=', Entry);
            If P = 0 Then Continue;
            D := Copy(Entry, 1, P - 1);
            If PinMapValue(OldMap, D) = '' Then Added := Added + D + ';';
        End;

        If ((Missing <> ';') Or (Moved <> ';')) And (Not AllowPinChanges) Then
        Begin
            Try SchDoc.RemoveSchObject(NewComp); SchRegisterObject(SchDoc, NewComp); Except NoteWriteFailed('swap: take the new symbol back off'); End;
            Result := BuildErrorResponse(RequestId, 'PIN_MISMATCH',
                'The new symbol''s pins do not land where the old ones did, so '
                + 'wires would come off them. Nothing was changed. Missing: '
                + NameListJson(Missing) + '. Moved: ' + NameListJson(Moved)
                + '. Pass allow_pin_changes to swap anyway.');
            Exit;
        End;

        Try SchDoc.RemoveSchObject(OldComp); SchRegisterObject(SchDoc, OldComp); Except NoteWriteFailed('swap: remove the old symbol'); End;
        { The old part is gone, so its UniqueId is free and the stamp sticks;
          AddSchObject remints an id that is still on the sheet. }
        If OldId <> '' Then
        Begin
            Try NewComp.SetState_UniqueId(OldId); Except { read back below } End;
            Try NewComp.UniqueId := OldId; Except { read back below } End;
        End;
    Finally
        { Committed now, even inside a bulk call, for the check below. }
        SchCommitEdit(SchDoc, 'Edit');
    End;
    { AGAIN AFTER THE COMMIT. Measured: the stamp inside the transaction    }
    { held on one swap and was reminted at PostProcess on another (the new  }
    { part came out with a fresh id), which unlinks it from the board.      }
    NewId := '';
    Try NewId := NewComp.UniqueId; Except End;
    If (OldId <> '') And (NewId <> OldId) Then
    Begin
        Try NewComp.SetState_UniqueId(OldId); Except { read back below } End;
        Try NewComp.UniqueId := OldId; Except { read back below } End;
    End;
    SchDoc.GraphicallyInvalidate;
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    NewId := '';
    Try NewId := NewComp.UniqueId; Except End;
    NewParams := SchParamNames(NewComp);
    Dropped := ';';
    Rest := Copy(OldParams, 2, Length(OldParams));
    While Rest <> '' Do
    Begin
        P := Pos(';', Rest);
        If P = 0 Then Break;
        D := Copy(Rest, 1, P - 1);
        Rest := Copy(Rest, P + 1, Length(Rest));
        If (D <> '') And (Pos(';' + D + ';', NewParams) = 0) Then
            Dropped := Dropped + D + ';';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"symbol_replaced":true,"designator":"' + EscapeJsonString(Designator)
        + '","old_lib_ref":"' + EscapeJsonString(OldLibRef)
        + '","new_lib_ref":"' + EscapeJsonString(LibRef)
        + '","library_path":"' + EscapeJsonString(LibPath)
        + '","pins_kept":' + IntToStr(Kept)
        + ',"pins_missing":[' + NameListJson(Missing)
        + '],"pins_moved":[' + NameListJson(Moved)
        + '],"pins_added":[' + NameListJson(Added)
        + '],"parameters_not_on_new_symbol":[' + NameListJson(Dropped)
        + '],"unique_id_kept":' + BoolToJsonStr((OldId <> '') And (NewId = OldId))
        + ',"unique_id":"' + EscapeJsonString(NewId) + '"}');
End;

{..............................................................................}
{ Replace a component with a different library part                           }
{ With library_path: swaps the symbol on the sheet (SwapSchSymbol).           }
{ Without: relinks the part to another library item, leaving its symbol.      }
{ Params: designator, new_lib_ref, new_library, library_path,                 }
{         allow_pin_changes                                                   }
{..............................................................................}

Function Gen_ReplaceComponent(Params : String; RequestId : String) : String;
Var
    AmbigTotal : Integer;
    Designator, NewLibRef, NewLibrary, LibPath : String;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp, Target : ISch_Component;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    NewLibRef := ExtractJsonValue(Params, 'new_lib_ref');
    NewLibrary := ExtractJsonValue(Params, 'new_library');
    LibPath := ExtractJsonValue(Params, 'library_path');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'designator is required');
        Exit;
    End;
    If NewLibRef = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'new_lib_ref is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    { A multi-part device gives every sub-part the same designator, so a
      first-match here wrote whichever symbol the iterator reached and
      called it success. Refuse instead, and hand back the candidates. }
    AmbigTotal := SchComponentCount(SchDoc, Designator);
    If AmbigTotal > 1 Then
    Begin
        Result := AmbiguousDesignator(SchDoc, Designator, AmbigTotal, RequestId);
        Exit;
    End;


    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));

    Obj := Iterator.FirstSchObject;
    While Obj <> Nil Do
    Begin
        Try
            Comp := Obj;   // cast through the strongly-typed local so
                           // Comp.Designator.Text resolves correctly
            If (Comp.Designator.Text = Designator) And (LibPath <> '') Then
            Begin
                Target := Comp;
                Found := True;
                Break;
            End;
            If Comp.Designator.Text = Designator Then
            Begin
                SchBeginEdit(SchDoc, '');
                Comp.LibReference := NewLibRef;
                { DesignItemId must follow the new library reference or
                  the part keeps re-matching against the OLD library item
                  and shows <Not Found> after a re-link. Measured: a
                  replace that updated only LibReference/SourceLibraryName
                  left every re-linked part in that state. }
                Try Comp.DesignItemId := NewLibRef; Except NoteWriteFailed('replace: DesignItemId'); End;
                If NewLibrary <> '' Then
                    Comp.SourceLibraryName := NewLibrary;
                SchEndEdit(SchDoc, 'Edit');
                Found := True;
                Break;
            End;
        Except
            { An object that is not a component has no Designator: skip it. }
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);

    If Found And (LibPath <> '') Then
    Begin
        { After the iterator is gone: the swap adds and removes parts. }
        Result := SwapSchSymbol(SchDoc, Target, Designator, LibPath, NewLibRef,
            ExtractJsonValue(Params, 'allow_pin_changes') = 'true', RequestId);
        Exit;
    End;

    If Found Then
    Begin
        SchDoc.GraphicallyInvalidate;
        If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
        { symbol_replaced false: this path rewrites the library link only.
          The symbol, pins and graphics on the sheet are the old part's
          until it is swapped (library_path) or updated from the library. }
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"symbol_replaced":false,"designator":"' + EscapeJsonString(Designator) +
            '","new_lib_ref":"' + EscapeJsonString(NewLibRef) +
            '","new_library":"' + EscapeJsonString(NewLibrary) +
            '","note":"Only the library link changed; the symbol and pins on the '
            + 'sheet are still the old part. Pass library_path to swap the symbol."}');
    End
    Else
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
End;

{..............................................................................}
{ Gen_GetConstraintGroups - Enumerate IDocument.DM_ConstraintGroups on the      }
{ active schematic document. Constraint groups are FPGA-style pin/timing        }
{ constraints attached to a document; each group has a target kind/id and a    }
{ list of IConstraint entries with Kind + Data payloads.                       }
{..............................................................................}

Function Gen_GetConstraintGroups(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Doc : IDocument;
    Group : IConstraintGroup;
    Cons : IConstraint;
    I, J, GroupCount, ConsCount : Integer;
    Json, GroupJson, ConsJson : String;
    FirstC : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Doc := SchDoc;
    GroupCount := 0;
    Try GroupCount := Doc.DM_ConstraintGroupCount; Except End;

    Json := '';
    For I := 0 To GroupCount - 1 Do
    Begin
        Group := Nil;
        Try Group := Doc.DM_ConstraintGroups(I); Except End;
        If Group = Nil Then Continue;

        ConsCount := 0;
        Try ConsCount := Group.DM_ConstraintCount; Except End;

        ConsJson := '';
        FirstC := True;
        For J := 0 To ConsCount - 1 Do
        Begin
            Cons := Nil;
            Try Cons := Group.DM_Constraints(J); Except End;
            If Cons = Nil Then Continue;
            If Not FirstC Then ConsJson := ConsJson + ',';
            FirstC := False;
            ConsJson := ConsJson + '{"kind":"';
            Try ConsJson := ConsJson + EscapeJsonString(Cons.DM_Kind); Except End;
            ConsJson := ConsJson + '","data":"';
            Try ConsJson := ConsJson + EscapeJsonString(Cons.DM_Data); Except End;
            ConsJson := ConsJson + '"}';
        End;

        GroupJson := '{"target_kind":"';
        Try GroupJson := GroupJson + EscapeJsonString(Group.DM_TargetKindString); Except End;
        GroupJson := GroupJson + '","target_id":"';
        Try GroupJson := GroupJson + EscapeJsonString(Group.DM_TargetId); Except End;
        GroupJson := GroupJson + '","constraint_count":' + IntToStr(ConsCount);
        GroupJson := GroupJson + ',"constraints":[' + ConsJson + ']}';

        If Json <> '' Then Json := Json + ',';
        Json := Json + GroupJson;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"groups":[' + Json + '],"count":' + IntToStr(GroupCount) + '}');
End;

{..............................................................................}
{ Gen_PlaceHarnessConnector - Place an ISch_HarnessConnector on the active      }
{ sheet. Harness connectors group a set of wires/buses into a named harness    }
{ so cross-sheet signal bundles can be represented as a single connection.     }
{ Params: x, y (top left), width, height (mils), harness_type, entries.      }
{..............................................................................}

{ Entries on a harness connector: Names separated by ';', on Side, the    }
{ first Pitch from the top and the rest Pitch apart. The add positions an }
{ entry itself and drops Side and DistanceFromTop, as it does for a sheet }
{ entry, so both are written again after it and read back. The add also  }
{ leaves the entry's Location stacked below the sheet origin (measured:   }
{ 2400,-50 on a connector at 2000,3000), so it is put on the edge from    }
{ the two, as PositionSheetEntry does for a sheet entry. Returns the      }
{ entries as placed, as JSON objects; Placed counts the ones that read    }
{ back where they were asked.                                             }
Function AddHarnessEntries(Harness : ISch_HarnessConnector; Names : String;
    SideStr : String; Pitch : Double; Var Placed : Integer) : String;
Var
    Rest, EntryName : String;
    P, K, WantSide, GotSide : Integer;
    Entry : ISch_HarnessEntry;
    Dist, GotDist, EdgeX, EdgeY : Double;
    Corner : TLocation;
Begin
    Result := '';
    Placed := 0;
    Corner := Harness.Location;
    If SideStr = 'right' Then WantSide := eRightSide
    Else If SideStr = 'top' Then WantSide := eTopSide
    Else If SideStr = 'bottom' Then WantSide := eBottomSide
    Else WantSide := eLeftSide;
    Rest := Names;
    K := 0;
    While Rest <> '' Do
    Begin
        P := Pos(';', Rest);
        If P > 0 Then
        Begin
            EntryName := Trim(Copy(Rest, 1, P - 1));
            Rest := Copy(Rest, P + 1, Length(Rest));
        End
        Else
        Begin
            EntryName := Trim(Rest);
            Rest := '';
        End;
        If EntryName = '' Then Continue;
        Inc(K);
        Dist := Pitch * K;
        Entry := SchServer.SchObjectFactory(eHarnessEntry, eCreate_Default);
        If Entry = Nil Then
        Begin
            NoteWriteFailed('HarnessEntry.create');
            Continue;
        End;
        Entry.Name := EntryName;
        Entry.Side := WantSide;
        Entry.DistanceFromTop := MilsToCoordF(Dist);
        Harness.AddAndPositionSchObject(Entry);
        Try Entry.Side := WantSide; Except NoteWriteFailed('HarnessEntry.Side'); End;
        Try Entry.DistanceFromTop := MilsToCoordF(Dist); Except NoteWriteFailed('HarnessEntry.DistanceFromTop'); End;
        EdgeX := CoordToMilsF(Corner.X);
        EdgeY := CoordToMilsF(Corner.Y);
        If WantSide = eLeftSide Then EdgeY := EdgeY - Dist
        Else If WantSide = eRightSide Then
        Begin
            EdgeX := EdgeX + CoordToMilsF(Harness.XSize);
            EdgeY := EdgeY - Dist;
        End
        Else If WantSide = eTopSide Then EdgeX := EdgeX + Dist
        Else
        Begin
            EdgeX := EdgeX + Dist;
            EdgeY := EdgeY - CoordToMilsF(Harness.YSize);
        End;
        Try Entry.Location := Point(MilsToCoordF(EdgeX), MilsToCoordF(EdgeY)); Except NoteWriteFailed('HarnessEntry.Location'); End;
        GotSide := -1;
        GotDist := -1;
        Try GotSide := Entry.Side; Except End;
        Try GotDist := CoordToMilsF(Entry.DistanceFromTop); Except End;
        If (GotSide = WantSide) And (Abs(GotDist - Dist) < 0.01) Then Inc(Placed);
        If Result <> '' Then Result := Result + ',';
        Result := Result + '{"name":"' + EscapeJsonString(EntryName) + '"'
            + ',"distance_from_top":' + FloatToJsonStr(GotDist)
            + ',"side_as_asked":' + BoolToJsonStr(GotSide = WantSide) + '}';
    End;
End;

Function Gen_PlaceHarnessConnector(Params : String; RequestId : String) : String;
Var
    X, Y, W, H : Double;
    HarnessType, EntryNames, EntrySide, EntriesJson : String;
    EntryPitch : Double;
    EntriesPlaced : Integer;
    SchDoc : ISch_Document;
    Harness : ISch_HarnessConnector;
    TypeLabel : ISch_GraphicalObject;
Begin
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    W := StrToFloatDef(ExtractJsonValue(Params, 'width'), 500);
    H := StrToFloatDef(ExtractJsonValue(Params, 'height'), 800);
    HarnessType := ExtractJsonValue(Params, 'harness_type');
    EntryNames := ExtractJsonValue(Params, 'entries');
    EntrySide := LowerCase(ExtractJsonValue(Params, 'entry_side'));
    EntryPitch := StrToFloatDef(ExtractJsonValue(Params, 'entry_pitch'), 100);
    If EntryPitch <= 0 Then EntryPitch := 100;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Harness := SchServer.SchObjectFactory(eHarnessConnector, eCreate_Default);
    If Harness = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create harness connector');
        Exit;
    End;

    { ISch_HarnessConnector is an ISch_RectangularGroup -- no Corner property
      (using it raises "Undeclared identifier: Corner", and the local must be
      typed as the derived interface, not ISch_GraphicalObject). Size via
      XSize/YSize down and right from the top-left Location (measured: entries at 100 and 200 from the top read Location.Y 1900 and 1800 on a connector at Y 2000). }
    SchBeginEdit(SchDoc, '');
    Harness.Location := Point(MilsToCoordF(X), MilsToCoordF(Y));
    Harness.XSize := MilsToCoordF(W);
    Harness.YSize := MilsToCoordF(H);
    If HarnessType <> '' Then
    Begin
        Try Harness.HarnessType := HarnessType; Except NoteWriteFailed('Harness.HarnessType'); End;
    End;

    SchDoc.RegisterSchObjectInContainer(Harness);
    SchRegisterObject(SchDoc, Harness);
    EntriesJson := '';
    EntriesPlaced := 0;
    If EntryNames <> '' Then
        EntriesJson := AddHarnessEntries(Harness, EntryNames, EntrySide,
            EntryPitch, EntriesPlaced);
    { The type label is left where Altium happens to put it, off the body }
    { and once below the sheet, so it goes under the bottom-left corner.  }
    TypeLabel := Nil;
    Try TypeLabel := Harness.HarnessConnectorType; Except TypeLabel := Nil; End;
    If TypeLabel <> Nil Then
    Begin
        Try TypeLabel.Location := Point(MilsToCoordF(X), MilsToCoordF(Y - H - 100));
        Except NoteWriteFailed('HarnessConnectorType.Location'); End;
    End;
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y)
        + ',"width":' + FloatToJsonStr(W) + ',"height":' + FloatToJsonStr(H)
        + ',"harness_type":"' + EscapeJsonString(HarnessType) + '"'
        + ',"entries":[' + EntriesJson + ']'
        + ',"entries_placed":' + IntToStr(EntriesPlaced) + '}');
End;

{..............................................................................}
{ Gen_PlaceSignalHarness - draw a signal harness, the thick line that carries  }
{ a harness from a harness connector or a harness port to a sheet entry.      }
{ Params: vertices 'x1,y1,x2,y2,...' (mils, two points or more).              }
{ Built like a wire: InsertVertex then SetState_Vertex for each point.        }
{..............................................................................}

Function Gen_PlaceSignalHarness(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Harness : ISch_GraphicalObject;
    Xs, Ys : TStringList;
    I, Count : Integer;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    Xs := TStringList.Create;
    Ys := TStringList.Create;
    Try
        If (Not ParseVertexList(ExtractJsonValue(Params, 'vertices'), Xs, Ys))
            Or (Xs.Count < 2) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_VERTICES',
                'vertices is x1,y1,x2,y2,... in mils, two points or more. Nothing was drawn.');
            Exit;
        End;
        Harness := SchServer.SchObjectFactory(eSignalHarness, eCreate_Default);
        If Harness = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create signal harness');
            Exit;
        End;
        SchBeginEdit(SchDoc, '');
        For I := 0 To Xs.Count - 1 Do
        Begin
            Harness.InsertVertex := I + 1;
            Harness.SetState_Vertex(I + 1, Point(MilsToCoordF(StrToFloatDef(Xs[I], 0)),
                MilsToCoordF(StrToFloatDef(Ys[I], 0))));
        End;
        SchDoc.RegisterSchObjectInContainer(Harness);
        SchRegisterObject(SchDoc, Harness);
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
        If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
        Count := -1;
        Try Count := Harness.VerticesCount; Except End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":' + BoolToJsonStr(Count = Xs.Count)
            + ',"vertices":' + IntToStr(Count) + ',"asked":' + IntToStr(Xs.Count) + '}');
    Finally
        Xs.Free;
        Ys.Free;
    End;
End;

{..............................................................................}
{ Gen_PlaceCrossSheetConnector - Place an ISch_CrossSheetConnector (the off-    }
{ sheet port variant used for hierarchical signal links).                      }
{ Params: x, y, net (net name to connect), side (left|right)                    }
{..............................................................................}

Function Gen_PlaceCrossSheetConnector(Params : String; RequestId : String) : String;
Var
    X, Y : Double;
    NetName, SideStr : String;
    SchDoc : ISch_Document;
    Conn : ISch_GraphicalObject;
    CSC : ISch_CrossSheetConnector;
Begin
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    NetName := ExtractJsonValue(Params, 'net');
    SideStr := LowerCase(ExtractJsonValue(Params, 'side'));

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Conn := SchServer.SchObjectFactory(eCrossSheetConnector, eCreate_Default);
    If Conn = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create cross-sheet connector');
        Exit;
    End;

    SchBeginEdit(SchDoc, '');
    Try Conn.Location := Point(MilsToCoordF(X), MilsToCoordF(Y)); Except NoteWriteFailed('CrossSheetConnector.Location'); End;
    If NetName <> '' Then
    Begin
        Try Conn.Text := NetName; Except NoteWriteFailed('CrossSheetConnector.Text'); End;
    End;
    { The connector's left / right is CrossSheetStyle. It has no Side (a  }
    { sheet entry's member): writing Side raised "Undeclared identifier:   }
    { Side" on AD 25.8.1 and stopped the polling loop.                     }
    CSC := Conn;
    If SideStr = 'left' Then
    Begin
        Try CSC.CrossSheetStyle := eCrossSheetLeft; Except NoteWriteFailed('CrossSheetConnector.CrossSheetStyle'); End;
    End
    Else If SideStr = 'right' Then
    Begin
        Try CSC.CrossSheetStyle := eCrossSheetRight; Except NoteWriteFailed('CrossSheetConnector.CrossSheetStyle'); End;
    End;

    SchDoc.RegisterSchObjectInContainer(Conn);
    SchRegisterObject(SchDoc, Conn);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y)
        + ',"net":"' + EscapeJsonString(NetName) + '"}');
End;

{..............................................................................}
{ Gen_SetComponentPartId - Switch the active sub-part on a multi-part           }
{ component (e.g. U1A -> U1B on a quad op-amp). CurrentPartID is 1-based.      }
{ Params: designator, part_id                                                  }
{..............................................................................}

Function Gen_SetComponentPartId(Params : String; RequestId : String) : String;
Var
    Designator, LocX, LocY : String;
    PartId, PartIdAfter, Total : Integer;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    CompLoc : TLocation;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    PartId := StrToIntDef(ExtractJsonValue(Params, 'part_id'), 0);
    LocX := ExtractJsonValue(Params, 'location_x');
    LocY := ExtractJsonValue(Params, 'location_y');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    If PartId < 1 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PART_ID', 'part_id must be >= 1');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Total := SchComponentCount(SchDoc, Designator);
    If Total = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
        Exit;
    End;

    { Choosing for the caller is the bug this replaces. Every sub-part of a }
    { multi-part device carries the same designator, so picking the first   }
    { wrote whichever the iterator happened to reach and called it success. }
    If (Total > 1) And ((LocX = '') Or (LocY = '')) Then
    Begin
        Result := AmbiguousDesignator(SchDoc, Designator, Total, RequestId);
        Exit;
    End;

    Comp := SchComponentAt(SchDoc, Designator, LocX, LocY);
    If Comp = Nil Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'NOT_FOUND',
            'No symbol designated ' + Designator + ' at that location.',
            JsonObj(JsonRaw('candidates', SchComponentCandidates(SchDoc, Designator))));
        Exit;
    End;

    SchBeginEdit(SchDoc, 'Set part id');
    SetSchPartId(Comp, PartId);
    SchEndEdit(SchDoc, 'Set part id');
    SchDoc.GraphicallyInvalidate;

    { Read back. A part id past PartCount is accepted by the assignment and }
    { simply does not take, which used to be reported as a success.         }
    PartIdAfter := -1;
    Try PartIdAfter := Comp.CurrentPartID; Except End;
    If PartIdAfter <> PartId Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRITE_REFUSED',
            'Set part_id ' + IntToStr(PartId) + ' on ' + Designator
            + ' and it reads back ' + IntToStr(PartIdAfter)
            + '. A part id above PartCount does not take.');
        Exit;
    End;

    CompLoc := Comp.Location;
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator)
        + '","part_id":' + IntToStr(PartIdAfter)
        + ',"location_x":' + FloatToJsonStr(CoordToMilsF(CompLoc.X))
        + ',"location_y":' + FloatToJsonStr(CoordToMilsF(CompLoc.Y))
        { One copy is changed; the others sharing its designator are counted }
        { apart, so the count is not read as the number written.             }
        + ',"changed":1,"same_designator":' + IntToStr(Total) + '}');
End;

{..............................................................................}
{ Gen_SetComponentUniqueId - Stamp ISch_Component.UniqueId.                     }
{ ECO groups sub-parts of a multi-gate symbol (quad comparator, dual           }
{ op-amp) into one physical footprint only when they share UniqueId. Four      }
{ copies of part 1 with four UniqueIds become four TSSOP packages.             }
{ Params: designator, unique_id                                                }
{..............................................................................}

Function Gen_SetComponentUniqueId(Params : String; RequestId : String) : String;
Var
    Designator, UniqueIdStr, AfterId, Details : String;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    CompLoc : TLocation;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Written, Refused, Total : Integer;
    First : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    UniqueIdStr := ExtractJsonValue(Params, 'unique_id');

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    If UniqueIdStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'unique_id required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    { EVERY SYMBOL WITH THIS DESIGNATOR, not the first one. That is the    }
    { point of the property: ECO treats sub-parts as one physical package  }
    { only when they SHARE a UniqueId, so stamping one of three and        }
    { reporting success left the other two pointing at packages of their   }
    { own, which is the exact condition this tool exists to repair.        }
    { MEASURED on a live sheet: three symbols designated U13, three        }
    { different UniqueIds, one dual device.                                }
    AfterId := '';
    Written := 0;
    Refused := 0;
    Total := 0;
    Details := '[';
    First := True;
    Iterator := SchDoc.SchIterator_Create;
    Try
        Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iterator.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Comp := Obj;
            If Comp.Designator.Text = Designator Then
            Begin
                Total := Total + 1;
                SchBeginEdit(SchDoc, 'Set UniqueId');
                Try Comp.SetState_UniqueId(UniqueIdStr); Except { read back below } End;
                Try Comp.UniqueId := UniqueIdStr; Except { read back below } End;
                Try AfterId := Comp.UniqueId; Except AfterId := ''; End;
                SchEndEdit(SchDoc, 'Set UniqueId');

                If AfterId = UniqueIdStr Then
                    Written := Written + 1
                Else
                    Refused := Refused + 1;

                CompLoc := Comp.Location;
                If Not First Then Details := Details + ',';
                Details := Details
                    + '{"location_x":' + FloatToJsonStr(CoordToMilsF(CompLoc.X))
                    + ',"location_y":' + FloatToJsonStr(CoordToMilsF(CompLoc.Y))
                    + ',"unique_id_after":"' + EscapeJsonString(AfterId) + '"}';
                First := False;
            End;
            Obj := Iterator.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iterator);
    End;
    Details := Details + ']';
    SchDoc.GraphicallyInvalidate;

    If Total = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
        Exit;
    End;

    If Refused > 0 Then
    Begin
        Result := BuildErrorResponseDetailed(RequestId, 'WRITE_REFUSED',
            'Stamped ' + IntToStr(Written) + ' of ' + IntToStr(Total)
            + ' symbols designated ' + Designator + '. Altium mints '
            + 'UniqueIds itself and kept its own on the rest, so those '
            + 'sub-parts still belong to separate packages.',
            JsonObj(JsonRaw('symbols', Details)));
        Exit;
    End;

    { THE READ BACK WAS ALREADY HERE AND NOTHING COMPARED IT. success was
      true whether or not AfterId matched what was asked for, so a caller
      had to notice the discrepancy between two adjacent fields to learn
      the write had not taken. Altium mints UniqueIds itself and this is
      the property it is most likely to overrule, which is the whole
      reason the read back exists. }
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator)
        + '","unique_id":"' + EscapeJsonString(UniqueIdStr)
        + '","symbols_written":' + IntToStr(Written)
        + ',"symbols_found":' + IntToStr(Total)
        + ',"symbols":' + Details + '}');
End;

{..............................................................................}
{ Gen_ReplicateSchComponent - Duplicate a placed schematic component via       }
{ ISch_Component.Replicate. Live 2026-08-21: Replicate + AddSchObject minted   }
{ a new UniqueId (NPZTIRAT vs master UCOMP2MP). This stamps UniqueId with      }
{ SetState_UniqueId AFTER Replicate and BEFORE AddSchObject, then again after  }
{ register if Add reminted. Returns staged UniqueId readbacks.                 }
{ Params: designator, part_id, x, y, new_designator                            }
{..............................................................................}

Function Gen_ReplicateSchComponent(Params : String; RequestId : String) : String;
Var
    AmbigTotal : Integer;
    Designator, NewDesig, MasterId, CopyId : String;
    IdAfterReplicate, IdAfterSetPreAdd, IdAfterAdd, Shared : String;
    PartId : Integer;
    X, Y : Double;
    HaveXY : Boolean;
    SchDoc : ISch_Document;
    Comp, NewComp : ISch_Component;
    NewObj : ISch_GraphicalObject;
    Found : Boolean;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    NewDesig := ExtractJsonValue(Params, 'new_designator');
    PartId := StrToIntDef(ExtractJsonValue(Params, 'part_id'), 0);
    HaveXY := (ExtractJsonValue(Params, 'x') <> '') And (ExtractJsonValue(Params, 'y') <> '');
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);

    If Designator = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    { A multi-part device gives every sub-part the same designator, so a
      first-match here wrote whichever symbol the iterator reached and
      called it success. Refuse instead, and hand back the candidates. }
    AmbigTotal := SchComponentCount(SchDoc, Designator);
    If AmbigTotal > 1 Then
    Begin
        Result := AmbiguousDesignator(SchDoc, Designator, AmbigTotal, RequestId);
        Exit;
    End;


    Found := False;
    MasterId := '';
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj := Iterator.FirstSchObject;
    While (Obj <> Nil) And Not Found Do
    Begin
        Comp := Obj;
        If Comp.Designator.Text = Designator Then
        Begin
            Found := True;
            Try MasterId := Comp.UniqueId; Except MasterId := ''; End;
        End
        Else
            Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Designator);
        Exit;
    End;

    SchBeginEdit(SchDoc, 'Replicate component');
    NewObj := Nil;
    Try NewObj := Comp.Replicate; Except NewObj := Nil; End;
    If NewObj = Nil Then
    Begin
        SchEndEdit(SchDoc, 'Replicate component');
        Result := BuildErrorResponse(RequestId, 'REPLICATE_FAILED',
            'ISch_Component.Replicate returned nil for ' + Designator);
        Exit;
    End;
    NewComp := NewObj;
    Try IdAfterReplicate := NewComp.UniqueId; Except IdAfterReplicate := ''; End;
    If MasterId <> '' Then
    Begin
        Try NewComp.SetState_UniqueId(MasterId); Except { read back below } End;
        Try NewComp.UniqueId := MasterId; Except { read back below } End;
    End;
    Try IdAfterSetPreAdd := NewComp.UniqueId; Except IdAfterSetPreAdd := ''; End;
    If PartId >= 1 Then
    Begin
        Try NewComp.SetState_CurrentPartID(PartId); Except { read back below } End;
        Try NewComp.CurrentPartID := PartId; Except { read back below } End;
        Try
            If NewComp.CurrentPartID <> PartId Then NoteWriteFailed('copy CurrentPartID');
        Except
            NoteWriteFailed('copy CurrentPartID');
        End;
    End;
    If NewDesig <> '' Then
    Begin
        Try NewComp.Designator.Text := NewDesig; Except NoteWriteFailed('copy Designator'); End;
    End;
    If HaveXY Then
    Begin
        Try NewComp.MoveToXY(MilsToCoordF(X), MilsToCoordF(Y)); Except NoteWriteFailed('copy MoveToXY'); End;
    End;
    { AddSchObject remints UniqueId when that id is already on the sheet
      (live 2026-08-26: XVRAPPYA -> ASFAVKKE even after SetState_UniqueId
      pre-add). SCHM_PrimitiveRegistration does the same. Attach via
      RegisterSchObjectInContainer, then re-stamp UniqueId while still
      inside this PreProcess. Do not send SCHM_PrimitiveRegistration.
      ONE registration only: AddSchObject followed by this registered the
      copy twice under one key, and the next move of either part raised
      "An item with the same key has already been added". }
    Try SchDoc.RegisterSchObjectInContainer(NewComp); Except NoteWriteFailed('register copy on the sheet'); End;
    Try IdAfterAdd := NewComp.UniqueId; Except IdAfterAdd := ''; End;
    If (MasterId <> '') And (IdAfterAdd <> MasterId) Then
    Begin
        Try NewComp.SetState_UniqueId(MasterId); Except { read back below } End;
        Try NewComp.UniqueId := MasterId; Except { read back below } End;
    End;
    Try CopyId := NewComp.UniqueId; Except CopyId := ''; End;
    SchEndEdit(SchDoc, 'Replicate component');
    SchDoc.GraphicallyInvalidate;

    If CopyId = MasterId Then Shared := 'true' Else Shared := 'false';

    { success reflects whether the copy KEPT the master's id, which is the
      thing this handler exists to achieve. Replicate plus AddSchObject
      mints a new one, so reporting true regardless would hide exactly
      the failure the staged read backs were added to expose. }
    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":' + BoolToJsonStr(Shared = 'true')
        + ',"source_designator":"' + EscapeJsonString(Designator)
        + '","new_designator":"' + EscapeJsonString(NewDesig)
        + '","part_id":' + IntToStr(PartId)
        + ',"source_unique_id":"' + EscapeJsonString(MasterId)
        + '","copy_unique_id":"' + EscapeJsonString(CopyId)
        + '","unique_id_after_replicate":"' + EscapeJsonString(IdAfterReplicate)
        + '","unique_id_after_set_pre_add":"' + EscapeJsonString(IdAfterSetPreAdd)
        + '","unique_id_after_add":"' + EscapeJsonString(IdAfterAdd)
        + '","shared":' + Shared + '}');
End;

{..............................................................................}
{ AddProbeParam - Add one named parameter to a probe, offset from its spot.     }
{..............................................................................}

Procedure AddProbeParam(Probe : ISch_GraphicalObject; PName, PText : String;
    Hidden : Boolean; DX, DY : Double);
Var
    Param : ISch_Parameter;
Begin
    Param := SchServer.SchObjectFactory(eParameter, eCreate_Default);
    If Param = Nil Then Exit;
    Param.Name := PName;
    Param.Text := PText;
    Try Param.IsHidden := Hidden; Except NoteWriteFailed('probe parameter IsHidden'); End;
    Try Param.Location := Point(Probe.Location.X + MilsToCoordF(DX),
        Probe.Location.Y + MilsToCoordF(DY)); Except End;
    Probe.AddSchObject(Param);
    SchRegisterObject(Probe, Param);
End;

{..............................................................................}
{ Gen_PlaceProbe - Place a simulation voltage probe on a wire.                  }
{ Params: x, y, name (shown instead of the net name), net_name and            }
{         probe_method (echoed back).                                         }
{                                                                              }
{ MEASURED on AD 26.11: Altium's own voltage probe (Sch:PlaceProbe with       }
{ ProbeType=SimulationProbe|MeasurementType=Voltage) is a probe carrying the  }
{ parameters SimulationProbe=V (shown), ProbeVoltage=True (hidden),           }
{ ProbeName, ProbeCustomName (hidden) and ProbeValueDisplay. A bare probe got  }
{ only ProbeValueDisplay, and the simulator, which finds probes by their      }
{ SimulationProbe parameter, passed it over.                                  }
{..............................................................................}

Function Gen_PlaceProbe(Params : String; RequestId : String) : String;
Var
    X, Y : Double;
    NetName, MethodStr, CustomName : String;
    SchDoc : ISch_Document;
    Probe : ISch_GraphicalObject;
Begin
    X := StrToFloatDef(ExtractJsonValue(Params, 'x'), 0);
    Y := StrToFloatDef(ExtractJsonValue(Params, 'y'), 0);
    NetName := ExtractJsonValue(Params, 'net_name');
    MethodStr := LowerCase(ExtractJsonValue(Params, 'probe_method'));
    CustomName := ExtractJsonValue(Params, 'name');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Probe := SchServer.SchObjectFactory(eProbe, eCreate_Default);
    If Probe = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create probe');
        Exit;
    End;

    SchBeginEdit(SchDoc, '');
    Try Probe.Location := Point(MilsToCoordF(X), MilsToCoordF(Y)); Except NoteWriteFailed('Probe.Location'); End;
    { ISch_Probe exposes essentially nothing settable from DelphiScript:
      ``Text``, ``NetName`` and ``ProbeMethod`` all raise "Undeclared
      identifier" at compile (Try/Except cannot catch). The probe
      picks up the net of the wire it lands on. ``net_name`` and
      ``probe_method`` are echoed back without being applied. What makes
      it a simulation probe is its parameters, set as Altium sets them. }
    AddProbeParam(Probe, 'SimulationProbe', 'V', False, 100, 100);
    AddProbeParam(Probe, 'ProbeVoltage', 'True', True, 0, 200);
    AddProbeParam(Probe, 'ProbeName', '', False, 0, 100);
    AddProbeParam(Probe, 'ProbeCustomName', CustomName, True, 0, 100);

    SchDoc.RegisterSchObjectInContainer(Probe);
    SchRegisterObject(SchDoc, Probe);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"x":' + FloatToJsonStr(X) + ',"y":' + FloatToJsonStr(Y)
        + ',"net_name":"' + EscapeJsonString(NetName) + '",'
        + '"name":"' + EscapeJsonString(CustomName) + '","measures":"voltage"}');
End;

{..............................................................................}
{ ProbeJson - One probe: where it is and every parameter it carries.          }
{..............................................................................}

Function ProbeJson(Probe : ISch_GraphicalObject; SheetPath : String) : String;
Var
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
    ParamsJson, Kind : String;
    First : Boolean;
Begin
    ParamsJson := '';
    Kind := '';
    First := True;
    Iter := Probe.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        While Param <> Nil Do
        Begin
            If Not First Then ParamsJson := ParamsJson + ',';
            First := False;
            ParamsJson := ParamsJson + '"' + EscapeJsonString(Param.Name) + '":"'
                + EscapeJsonString(Param.Text) + '"';
            If SameText(Param.Name, 'SimulationProbe') Then Kind := Param.Text;
            Param := Iter.NextSchObject;
        End;
    Finally
        Probe.SchIterator_Destroy(Iter);
    End;
    Result := '{"sheet":"' + EscapeJsonString(SheetPath) + '",'
        + '"x":' + FloatToJsonStr(CoordToMilsF(Probe.Location.X)) + ','
        + '"y":' + FloatToJsonStr(CoordToMilsF(Probe.Location.Y)) + ','
        + JsonBool('simulation', Kind <> '') + ','
        + '"kind":"' + EscapeJsonString(Kind) + '",'
        + '"parameters":{' + ParamsJson + '}}';
End;

{..............................................................................}
{ Gen_GetProbes - Every probe on the focused project's sheets.                 }
{ Params: none.                                                               }
{                                                                              }
{ Each probe with its location and parameters: SimulationProbe (V, I, P, VD+  }
{ or VD-) marks one the simulator uses, ProbeName is the node Altium resolved }
{ ("Empty Probe" off a wire), ProbeCustomName the name shown instead, and     }
{ ProbeValueDisplay the value shown on the sheet. A sheet that is not open is }
{ listed in not_loaded rather than read.                                      }
{..............................................................................}

Function Gen_GetProbes(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Probe : ISch_GraphicalObject;
    I, Count : Integer;
    Items, NotLoaded, SheetPath : String;
Begin
    Project := Nil;
    Workspace := GetWorkspace;
    If Workspace <> Nil Then Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT',
            'No focused project. Activate one of the project''s schematic sheets first.');
        Exit;
    End;

    Items := '';
    NotLoaded := '';
    Count := 0;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(I);
        If Doc = Nil Then Continue;
        If Doc.DM_DocumentKind <> 'SCH' Then Continue;
        SheetPath := Doc.DM_FullPath;
        { Counted, so the reply also says how many sheets were not read. }
        SchDoc := ProjectSheet(SheetPath);
        If SchDoc = Nil Then
        Begin
            If NotLoaded <> '' Then NotLoaded := NotLoaded + ',';
            NotLoaded := NotLoaded + '"' + EscapeJsonString(SheetPath) + '"';
            Continue;
        End;
        Iter := SchDoc.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eProbe));
            Probe := Iter.FirstSchObject;
            While Probe <> Nil Do
            Begin
                If Count > 0 Then Items := Items + ',';
                Items := Items + ProbeJson(Probe, SheetPath);
                Inc(Count);
                Probe := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"project":"' + EscapeJsonString(Project.DM_ProjectFullPath) + '",'
        + '"count":' + IntToStr(Count) + ','
        + '"probes":[' + Items + '],'
        + '"not_loaded":[' + NotLoaded + ']}');
End;

{..............................................................................}
{ Gen_AddDatafileLink - Add an ISch_ModelDatafileLink to a component's active  }
{ implementation. This is how parametric data (IBIS model files, sim models,   }
{ external CSVs) is attached to a schematic part.                              }
{ Params: designator, file_path, kind (optional string, implementation-       }
{         specific, e.g. "SimModel", "IBIS")                                   }
{..............................................................................}

Function Gen_AddDatafileLink(Params : String; RequestId : String) : String;
Var
    AmbigTotal : Integer;
    Designator, FilePath, KindStr, EntityName : String;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    Impl : ISch_Implementation;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    FilePath := ExtractJsonValue(Params, 'file_path');
    KindStr := ExtractJsonValue(Params, 'kind');

    If (Designator = '') Or (FilePath = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator and file_path are required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    { A multi-part device gives every sub-part the same designator, so a
      first-match here wrote whichever symbol the iterator reached and
      called it success. Refuse instead, and hand back the candidates. }
    AmbigTotal := SchComponentCount(SchDoc, Designator);
    If AmbigTotal > 1 Then
    Begin
        Result := AmbiguousDesignator(SchDoc, Designator, AmbigTotal, RequestId);
        Exit;
    End;


    Found := False;
    Iterator := SchDoc.SchIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
    Obj := Iterator.FirstSchObject;
    While (Obj <> Nil) And Not Found Do
    Begin
        Comp := Obj;
        If Comp.Designator.Text = Designator Then
        Begin
            Impl := GetFirstSchImplementation(Comp);
            If Impl <> Nil Then
            Begin
                { ISch_Implementation.AddDataFileLink is a PROCEDURE taking
                  (anEntityName, aLocation, aFileKind : WideString), NOT a
                  function returning a link object. Calling it as a no-arg
                  function (Link := Impl.AddDataFileLink) faults. The link's
                  "file" is its Location; EntityName is a label (use the file
                  name); FileKind is the model kind. (Altium SDK.) }
                EntityName := ExtractFileName(FilePath);
                SchBeginEdit(SchDoc, 'Add datafile link');
                Try Impl.AddDataFileLink(EntityName, FilePath, KindStr); Except End;
                SchEndEdit(SchDoc, 'Add datafile link');
                Found := True;
            End;
        End;
        Obj := Iterator.NextSchObject;
    End;
    SchDoc.SchIterator_Destroy(Iterator);
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component or implementation not found for designator: ' + Designator);
        Exit;
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator)
        + '","file_path":"' + EscapeJsonString(FilePath) + '"}');
End;

{..............................................................................}
{ Helpers for SPICE / simulation handlers                                       }
{..............................................................................}

{ Classify a component's Comment as a passive primitive kind (R / L / C) or   }
{ empty when it doesn't look like a standard passive.                         }
Function ClassifyPassivePrefix(Comment : String) : String;
Var
    S, U, First, Second : String;
Begin
    { DelphiScript quirk: ``UpCase(S[1])`` raises EInvalidCast because
      S[1] is treated as String (not Char), and UpCase here expects a
      Char. Use string ops instead: ``UpperCase(S)`` + 1-char ``Copy``
      slices. Same applies to the second-character class check. }
    Result := '';
    If Comment = '' Then Exit;
    S := Trim(Comment);
    If S = '' Then Exit;
    U := UpperCase(S);
    First := Copy(U, 1, 1);
    If (First = 'R') Or (First = 'L') Or (First = 'C') Then
    Begin
        { Accept "R1", "10k", "Res", "Cap" etc., anything that starts with
          the letter and is short. Longer names (e.g. "Resonator") we
          skip. }
        If Length(S) = 1 Then
        Begin
            Result := First;
        End
        Else If Length(S) <= 20 Then
        Begin
            Second := Copy(U, 2, 1);
            If (Second = ' ') Or
               ((Second >= '0') And (Second <= '9')) Or
               ((Second >= 'A') And (Second <= 'Z')) Then
                Result := First;
        End;
    End;
End;

{ Read a named parameter's text off a sch component. Empty string if absent.   }
Function GetCompParamText(Comp : ISch_Component; ParamName : String) : String;
Var
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
Begin
    Result := '';
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Param := Iter.FirstSchObject;
        While Param <> Nil Do
        Begin
            If UpperCase(Param.Name) = UpperCase(ParamName) Then
            Begin
                Result := Param.Text;
                Break;
            End;
            Param := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;
End;

{ Set (or create) a component parameter. Returns True if created, False if     }
{ modified. Caller is responsible for PreProcess/PostProcess on the document. }
{ Set the part's own parameter of that name; False when it has none.    }
{ Nothing is created.                                                    }
Function SetOwnCompParamText(Comp : ISch_Component; ParamName, ParamValue : String) : Boolean;
Var
    Iter : ISch_Iterator;
    Param : ISch_Parameter;
Begin
    Result := False;
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        Iter.SetState_IterationDepth(eIterateFirstLevel);
        Param := Iter.FirstSchObject;
        While Param <> Nil Do
        Begin
            If UpperCase(Param.Name) = UpperCase(ParamName) Then
            Begin
                SchBeginModify(Param);
                Try Param.Text := ParamValue; Except NoteWriteFailed('parameter Text'); End;
                SchEndModify(Param);
                Result := True;
                Break;
            End;
            Param := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;
End;

Function SetCompParamText(Comp : ISch_Component; ParamName, ParamValue : String) : Boolean;
Var
    Iter : ISch_Iterator;
    Param, NewParam : ISch_Parameter;
    Found : ISch_Parameter;
Begin
    Result := False;
    Found := Nil;
    Iter := Comp.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eParameter));
        { The part's own parameters: a model's (Value of a SPICE model, say) }
        { sit below the first level and must not take a part's value.       }
        Iter.SetState_IterationDepth(eIterateFirstLevel);
        Param := Iter.FirstSchObject;
        While Param <> Nil Do
        Begin
            If UpperCase(Param.Name) = UpperCase(ParamName) Then
            Begin
                Found := Param;
                Break;
            End;
            Param := Iter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(Iter);
    End;

    If Found <> Nil Then
    Begin
        SchBeginModify(Found);
        Found.Text := ParamValue;
        SchEndModify(Found);
    End
    Else
    Begin
        NewParam := SchServer.SchObjectFactory(eParameter, eCreate_Default);
        If NewParam <> Nil Then
        Begin
            NewParam.Name := ParamName;
            NewParam.Text := ParamValue;
            { A new parameter came out shown and at the sheet origin, so    }
            { stamping many parts piled their texts there. Hidden, at the   }
            { part's own place.                                              }
            Try NewParam.IsHidden := True; Except NoteWriteFailed('new parameter IsHidden'); End;
            Try NewParam.Location := Comp.Location; Except NoteWriteFailed('new parameter Location'); End;
            Comp.AddSchObject(NewParam);
            SchRegisterObject(Comp, NewParam);
            Result := True;
        End;
    End;
End;

{..............................................................................}
{ NextJsonObjectField - Iterate top-level "key":"value" pairs out of a JSON      }
{ object body (the substring BETWEEN the outer braces, NOT including them).     }
{                                                                                }
{ Walks the string from StartPos, finds the next quoted key, the colon, the     }
{ quoted string value, and returns them via the Var params. Advances StartPos   }
{ past the comma so a caller loop can keep going. Returns False when no more   }
{ pairs are left.                                                                }
{                                                                                }
{ Limitations on purpose: only string-typed values are extracted; numbers /      }
{ booleans / nested objects are skipped over. This matches the parameters-      }
{ payload contract used by Gen_SetSchComponentParameters where every value is   }
{ a string.                                                                      }
{..............................................................................}

Function NextJsonObjectField(SubObj : String; Var StartPos : Integer;
                              Var Key, Value : String) : Boolean;
Var
    L, P, KStart, VStart : Integer;
    BackslashCount, TempPos : Integer;
Begin
    Result := False;
    Key := '';
    Value := '';
    L := Length(SubObj);
    P := StartPos;

    { Skip whitespace and any leading comma. }
    While (P <= L) And ((Copy(SubObj, P, 1) = ' ') Or (Copy(SubObj, P, 1) = #9)
          Or (Copy(SubObj, P, 1) = #10) Or (Copy(SubObj, P, 1) = #13)
          Or (Copy(SubObj, P, 1) = ',')) Do
        Inc(P);

    If (P > L) Or (Copy(SubObj, P, 1) <> '"') Then
    Begin
        StartPos := P;
        Exit;
    End;

    { Parse quoted key. }
    Inc(P);
    KStart := P;
    While P <= L Do
    Begin
        If Copy(SubObj, P, 1) = '"' Then
        Begin
            BackslashCount := 0;
            TempPos := P - 1;
            While (TempPos >= KStart) And (Copy(SubObj, TempPos, 1) = '\') Do
            Begin
                Inc(BackslashCount);
                Dec(TempPos);
            End;
            If (BackslashCount Mod 2) = 0 Then Break;
        End;
        Inc(P);
    End;
    If P > L Then
    Begin
        StartPos := P;
        Exit;
    End;
    Key := UnescapeJsonString(Copy(SubObj, KStart, P - KStart));
    Inc(P);

    { Skip whitespace + colon. }
    While (P <= L) And IsWhitespaceOrColon(SubObj, P) Do
        Inc(P);

    If P > L Then
    Begin
        StartPos := P;
        Exit;
    End;

    { Only handle string values; skip non-string fields gracefully. }
    If Copy(SubObj, P, 1) <> '"' Then
    Begin
        { Skip to next comma or end. }
        While (P <= L) And (Copy(SubObj, P, 1) <> ',') Do
            Inc(P);
        StartPos := P;
        { Key without value: report as empty value; caller decides. }
        Result := True;
        Exit;
    End;

    Inc(P);
    VStart := P;
    While P <= L Do
    Begin
        If Copy(SubObj, P, 1) = '"' Then
        Begin
            BackslashCount := 0;
            TempPos := P - 1;
            While (TempPos >= VStart) And (Copy(SubObj, TempPos, 1) = '\') Do
            Begin
                Inc(BackslashCount);
                Dec(TempPos);
            End;
            If (BackslashCount Mod 2) = 0 Then Break;
        End;
        Inc(P);
    End;
    If P > L Then
    Begin
        StartPos := P;
        Exit;
    End;
    Value := UnescapeJsonString(Copy(SubObj, VStart, P - VStart));
    Inc(P);

    StartPos := P;
    Result := True;
End;

{..............................................................................}
{ SetPlacedFootprintName - point a placed component's footprint model at        }
{ another footprint, and say what it reads afterwards.                          }
{                                                                               }
{ CurrentFootprintModelName is read-only (assigning it is an undeclared         }
{ identifier the loop cannot survive), so the write goes to the footprint       }
{ ISch_Implementation's ModelName, and each of its datafile links gets the new  }
{ EntityName so the model and its link name the same footprint. The link's      }
{ Location is left alone: the footprint library it points at does not change.   }
{ Only a PCBLIB implementation is touched, the current one first; a part with   }
{ none is reported, not given a new model. The name is read back, so a write    }
{ that did not take is not counted.                                             }
{ A part that already carries a model of that name has that model CHOSEN, not   }
{ the current one renamed: renaming turned one model, its link and its pin map  }
{ into a second copy of a name the part already had. A choice that does not    }
{ read back is undone and reported, and nothing is renamed.                     }
{..............................................................................}

Function SetPlacedFootprintName(Comp : ISch_Component; NewName : String;
    Var Before : String; Var After : String) : Boolean;
Var
    ImplIter : ISch_Iterator;
    Impl, Target, FirstFp : ISch_Implementation;
    Link : ISch_ModelDatafileLink;
    MT : String;
    IsCur : Boolean;
    J, LinkCount, Choice : Integer;
Begin
    Result := False;
    Before := '';
    After := '';
    Choice := ChooseFootprintModel(Comp, NewName, Before, After);
    If Choice <> 0 Then
    Begin
        Result := (Choice = 1);
        Exit;
    End;
    Target := Nil;
    FirstFp := Nil;
    ImplIter := Comp.SchIterator_Create;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            MT := '';
            Try MT := Impl.ModelType; Except End;
            If UpperCase(MT) = 'PCBLIB' Then
            Begin
                If FirstFp = Nil Then FirstFp := Impl;
                IsCur := False;
                Try IsCur := Impl.IsCurrent; Except End;
                If IsCur Then
                Begin
                    Target := Impl;
                    Break;
                End;
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(ImplIter);
    End;
    If Target = Nil Then Target := FirstFp;
    If Target = Nil Then Exit;

    Try Before := Target.ModelName; Except End;
    SchBeginModify(Comp);
    SchBeginModify(Target);
    Try Target.ModelName := NewName; Except { read back below } End;
    LinkCount := 0;
    Try LinkCount := Target.DatafileLinkCount; Except LinkCount := 0; End;
    For J := 0 To LinkCount - 1 Do
    Begin
        Link := Nil;
        Try Link := Target.DatafileLink[J]; Except End;
        If Link <> Nil Then
        Begin
            Try Link.EntityName := NewName; Except NoteWriteFailed('footprint link EntityName'); End;
        End;
    End;
    SchEndModify(Target);
    { The renamed model is taken off current and put back, each in its own }
    { modify: renamed in place, the name read back while the compiled part }
    { and the save kept the old footprint (2026-11-04). Altium's own        }
    { release manager script changes a placed footprint the same way.      }
    SchBeginModify(Target);
    Try Target.IsCurrent := False; Except NoteWriteFailed('footprint model IsCurrent off'); End;
    SchEndModify(Target);
    SchBeginModify(Target);
    Try Target.IsCurrent := True; Except NoteWriteFailed('footprint model IsCurrent on'); End;
    SchEndModify(Target);
    SchEndModify(Comp);
    Try After := Target.ModelName; Except End;
    IsCur := False;
    Try IsCur := Target.IsCurrent; Except End;
    Result := (After = NewName) And IsCur;
End;

{..............................................................................}
{ Gen_SetSchComponentParameters - Stamp BOM/value/footprint metadata onto a      }
{ placed schematic component.                                                    }
{                                                                                }
{ Params: designator, sheet_path, parameters (JSON sub-object).                  }
{                                                                                }
{ For each (name, value) entry in parameters:                                    }
{   - "Value" writes to Comp.Comment.Text (convention: Comment field IS Value). }
{   - "Footprint" repoints the footprint model (SetPlacedFootprintName) and     }
{     counts only when the model reads back the new name.                       }
{   - everything else is a regular ISch_Parameter on the component, modified    }
{     in place if present, created via SchObjectFactory(eParameter) otherwise.  }
{                                                                                }
{ Empty values are skipped, so the caller can send a single payload with        }
{ optional fields. Returns the count of fields applied.                          }
{..............................................................................}

Function Gen_SetSchComponentParameters(Params : String; RequestId : String) : String;
Var
    DesigStr, SheetPath, SubObj, Key, Val : String;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp, TargetComp : ISch_Component;
    Found : Boolean;
    P, Applied, Created : Integer;
    SrvDoc : IServerDocument;
    FpBefore, FpAfter, FpJson : String;
    FpOk : Boolean;
Begin
    DesigStr := ExtractJsonValue(Params, 'designator');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    SubObj := ExtractJsonValue(Params, 'parameters');

    If DesigStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator required');
        Exit;
    End;

    If SubObj = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'parameters sub-object required');
        Exit;
    End;

    { Resolve target sheet (focus-independent). Mirrors                         }
    { Gen_PlaceSchComponentFromLibrary's resolution rules.                       }
    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_LOADED',
                'No SchDoc loaded at ' + SheetPath + '. Open it first.');
            Exit;
        End;
    End
    Else
    Begin
        SchDoc := SchServer.GetCurrentSchDocument;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
                'No schematic document is active');
            Exit;
        End;
    End;

    If SchDoc.ObjectId <> eSheet Then
    Begin
        Result := BuildErrorResponse(RequestId, 'WRONG_DOC_KIND',
            'Target document is not a schematic sheet (ObjectId=' +
            IntToStr(SchDoc.ObjectId) + '). Pass sheet_path to a .SchDoc.');
        Exit;
    End;

    { Find the placed component by designator. }
    TargetComp := Nil;
    Found := False;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iter.FirstSchObject;
        While (Obj <> Nil) And Not Found Do
        Begin
            Comp := Obj;
            If Comp.Designator.Text = DesigStr Then
            Begin
                TargetComp := Comp;
                Found := True;
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'COMPONENT_NOT_FOUND',
            'No component with designator "' + DesigStr + '" on sheet');
        Exit;
    End;

    SchBeginEdit(SchDoc, '');
    Applied := 0;
    Created := 0;
    FpJson := '';
    P := 1;
    Try
        While NextJsonObjectField(SubObj, P, Key, Val) Do
        Begin
            If Val = '' Then Continue;  { skip empty values }
            If Key = 'Value' Then
            Begin
                { Convention: Value -> Comment.Text, NOT a new parameter.  }
                { A symbol that shows its own Value parameter kept showing }
                { the library's placeholder, so that one is written too.   }
                SchBeginModify(TargetComp.Comment);
                Try TargetComp.Comment.Text := Val; Except NoteWriteFailed('Comment.Text'); End;
                SchEndModify(TargetComp.Comment);
                SetOwnCompParamText(TargetComp, 'Value', Val);
                Inc(Applied);
            End
            Else If (Key = 'ComponentDescription') Or (Key = 'Description') Then
            Begin
                { The part's description. As a parameter it was dropped }
                { when the sheet was reloaded.                          }
                SchBeginModify(TargetComp);
                Try TargetComp.ComponentDescription := Val; Except NoteWriteFailed('ComponentDescription'); End;
                SchEndModify(TargetComp);
                Inc(Applied);
            End
            Else If Key = 'Footprint' Then
            Begin
                { Counted only when the model reads back the new name. This
                  used to count as applied and write nothing. }
                { A model chosen by name reads back in its own spelling, so the }
                { helper's verdict decides, not a comparison with Val.          }
                FpOk := SetPlacedFootprintName(TargetComp, Val, FpBefore, FpAfter);
                If FpOk Then Inc(Applied);
                FpJson := ',"footprint":{"requested":"' + EscapeJsonString(Val)
                    + '","before":"' + EscapeJsonString(FpBefore)
                    + '","after":"' + EscapeJsonString(FpAfter)
                    + '","applied":' + BoolToJsonStr(FpOk) + '}';
            End
            Else
            Begin
                If SetCompParamText(TargetComp, Key, Val) Then
                    Inc(Created);
                Inc(Applied);
            End;
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { Flag the server doc dirty so save_all flushes it. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(DesigStr) + '",'
        + '"applied":' + IntToStr(Applied) + ','
        + '"created":' + IntToStr(Created) + FpJson + '}');
End;

{..............................................................................}
{ Gen_GetSimulationReadiness - Audit every component on the active schematic    }
{ and report which are ready for SPICE sim vs which need a primitive vs which   }
{ need a model file fetched from the vendor.                                   }
{                                                                               }
{ Ready means: has a SpicePrefix parameter already set. Passives that only have }
{ R/L/C-shaped Comments but no SpicePrefix land in needs_primitive so the       }
{ client can call sch_attach_spice_primitive. Everything else lands in          }
{ needs_file with a suggested vendor search URL.                               }
{..............................................................................}

{ CompSimModelName - the name of the component's SIM model link, '' when it }
{ has none. SpicePrefix alone is not a model: retake 07b had nine parts     }
{ "ready" by that parameter and every one failed the netlister with        }
{ "Missing component models".                                               }
Function CompSimModelName(Comp : ISch_Component) : String;
Var
    ImplIter : ISch_Iterator;
    Impl : ISch_Implementation;
    ModelType : String;
Begin
    Result := '';
    If Comp = Nil Then Exit;
    ImplIter := Nil;
    Try ImplIter := Comp.SchIterator_Create; Except ImplIter := Nil; End;
    If ImplIter = Nil Then Exit;
    Try
        ImplIter.AddFilter_ObjectSet(MkSet(eImplementation));
        Impl := ImplIter.FirstSchObject;
        While Impl <> Nil Do
        Begin
            ModelType := '';
            Try ModelType := Impl.ModelType; Except End;
            If UpperCase(ModelType) = 'SIM' Then
            Begin
                Try Result := Impl.ModelName; Except End;
                If Result = '' Then Result := '(unnamed)';
                Break;
            End;
            Impl := ImplIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(ImplIter);
    End;
End;

Function Gen_GetSimulationReadiness(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Designator, Comment, LibRef, SpicePrefix, Value : String;
    PassivePrefix, Kind, MfrPart, Mfr, SimModel : String;
    ReadyJson, NeedsPrimJson, NeedsFileJson, NoModelJson : String;
    ReadyCount, NeedsPrimCount, NeedsFileCount, NoModelCount : Integer;
    FirstR, FirstP, FirstF, FirstN : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    NoModelJson := '';
    NoModelCount := 0;
    FirstN := True;
    ReadyJson := '';
    NeedsPrimJson := '';
    NeedsFileJson := '';
    ReadyCount := 0;
    NeedsPrimCount := 0;
    NeedsFileCount := 0;
    FirstR := True;
    FirstP := True;
    FirstF := True;

    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            Comp := Obj;
            Designator := '';
            Try Designator := Comp.Designator.Text; Except End;
            { DM_Comment / DM_LibraryReference are on the DM-API
              component interface (returned by SchDoc.DM_Components),
              NOT on the ISch_Component you get from SchIterator.
              Read via the parameter table and direct LibReference
              property instead. }
            Comment := GetCompParamText(Comp, 'Comment');
            LibRef := '';
            Try LibRef := Comp.LibReference; Except End;

            SpicePrefix := GetCompParamText(Comp, 'SpicePrefix');
            Value := GetCompParamText(Comp, 'Value');
            If Value = '' Then
                Value := Comment;
            MfrPart := GetCompParamText(Comp, 'Manufacturer Part Number');
            If MfrPart = '' Then MfrPart := GetCompParamText(Comp, 'PartNumber');
            Mfr := GetCompParamText(Comp, 'Manufacturer');

            SimModel := CompSimModelName(Comp);
            If (SpicePrefix <> '') And (SimModel <> '') Then
            Begin
                If Not FirstR Then ReadyJson := ReadyJson + ',';
                FirstR := False;
                ReadyJson := ReadyJson
                    + '{"designator":"' + EscapeJsonString(Designator) + '",'
                    + '"comment":"' + EscapeJsonString(Comment) + '",'
                    + '"spice_prefix":"' + EscapeJsonString(SpicePrefix) + '",'
                    + '"sim_model":"' + EscapeJsonString(SimModel) + '",'
                    + '"value":"' + EscapeJsonString(Value) + '"}';
                Inc(ReadyCount);
            End
            Else If SpicePrefix <> '' Then
            Begin
                { Parameters written, no model the netlister can use. }
                If Not FirstN Then NoModelJson := NoModelJson + ',';
                FirstN := False;
                NoModelJson := NoModelJson
                    + '{"designator":"' + EscapeJsonString(Designator) + '",'
                    + '"comment":"' + EscapeJsonString(Comment) + '",'
                    + '"spice_prefix":"' + EscapeJsonString(SpicePrefix) + '",'
                    + '"value":"' + EscapeJsonString(Value) + '"}';
                Inc(NoModelCount);
            End
            Else
            Begin
                PassivePrefix := ClassifyPassivePrefix(Comment);
                If PassivePrefix = '' Then
                    PassivePrefix := ClassifyPassivePrefix(LibRef);
                If PassivePrefix <> '' Then
                Begin
                    Kind := PassivePrefix;
                    If Not FirstP Then NeedsPrimJson := NeedsPrimJson + ',';
                    FirstP := False;
                    NeedsPrimJson := NeedsPrimJson
                        + '{"designator":"' + EscapeJsonString(Designator) + '",'
                        + '"comment":"' + EscapeJsonString(Comment) + '",'
                        + '"suggested_prefix":"' + EscapeJsonString(Kind) + '",'
                        + '"suggested_value":"' + EscapeJsonString(Value) + '"}';
                    Inc(NeedsPrimCount);
                End
                Else
                Begin
                    If Not FirstF Then NeedsFileJson := NeedsFileJson + ',';
                    FirstF := False;
                    NeedsFileJson := NeedsFileJson
                        + '{"designator":"' + EscapeJsonString(Designator) + '",'
                        + '"comment":"' + EscapeJsonString(Comment) + '",'
                        + '"lib_ref":"' + EscapeJsonString(LibRef) + '",'
                        + '"manufacturer":"' + EscapeJsonString(Mfr) + '",'
                        + '"manufacturer_part":"' + EscapeJsonString(MfrPart) + '"}';
                    Inc(NeedsFileCount);
                End;
            End;

            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"ready":[' + ReadyJson + '],"ready_count":' + IntToStr(ReadyCount) + ','
        + '"needs_primitive":[' + NeedsPrimJson + '],"needs_primitive_count":' + IntToStr(NeedsPrimCount) + ','
        + '"needs_file":[' + NeedsFileJson + '],"needs_file_count":' + IntToStr(NeedsFileCount) + ','
        + '"no_sim_model":[' + NoModelJson + '],"no_sim_model_count":' + IntToStr(NoModelCount) + '}');
End;

{..............................................................................}
{ Gen_AttachSpicePrimitive - Attach a built-in SPICE primitive to a component.  }
{ For passives (R/L/C) and sources (V/I), this is just SpicePrefix + Value;     }
{ no model file is needed, Altium's simulator maps these to built-in          }
{ primitives directly.                                                          }
{ Params: designator, primitive (R|L|C|V|I|D|Q), value, spice_model (optional  }
{         subckt / model name for semi devices like D / Q), sim_kind (optional }
{         "General"/"Subcircuit"/"Model").                                     }
{..............................................................................}

Function Gen_AttachSpicePrimitive(Params : String; RequestId : String) : String;
Var
    AmbigTotal : Integer;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Designator, Primitive, Value, ModelName, SimKind : String;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    Primitive := UpperCase(ExtractJsonValue(Params, 'primitive'));
    Value := ExtractJsonValue(Params, 'value');
    ModelName := ExtractJsonValue(Params, 'spice_model');
    SimKind := ExtractJsonValue(Params, 'sim_kind');

    If (Designator = '') Or (Primitive = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'designator and primitive are required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    { A multi-part device gives every sub-part the same designator, so a
      first-match here wrote whichever symbol the iterator reached and
      called it success. Refuse instead, and hand back the candidates. }
    AmbigTotal := SchComponentCount(SchDoc, Designator);
    If AmbigTotal > 1 Then
    Begin
        Result := AmbiguousDesignator(SchDoc, Designator, AmbigTotal, RequestId);
        Exit;
    End;


    Found := False;
    SchBeginEdit(SchDoc, 'Attach SPICE primitive');
    Try
        Iter := SchDoc.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj := Iter.FirstSchObject;
            While (Obj <> Nil) And Not Found Do
            Begin
                Comp := Obj;
                If Comp.Designator.Text = Designator Then
                Begin
                    SetCompParamText(Comp, 'SpicePrefix', Primitive);
                    If Value <> '' Then
                        SetCompParamText(Comp, 'Value', Value);
                    If ModelName <> '' Then
                        SetCompParamText(Comp, 'SpiceModel', ModelName);
                    If SimKind <> '' Then
                        SetCompParamText(Comp, 'SimulationKind', SimKind);
                    Found := True;
                End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchEndEdit(SchDoc, 'Attach SPICE primitive');
    End;
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component not found: ' + Designator);
        Exit;
    End;

    { A parameter write dirties the document too: SmartCompile skips its
      recompile while the project looks clean, so a later netlist or ERC
      read answers from the model as it stood before this call. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator) + '",'
        + '"primitive":"' + EscapeJsonString(Primitive) + '",'
        + '"value":"' + EscapeJsonString(Value) + '"}');
End;

{..............................................................................}
{ Gen_AttachSpiceModel - Attach an external SPICE model file (.mdl / .ckt) to  }
{ a component. Sets SpicePrefix=X (subcircuit), SpiceModel=<model_name>,       }
{ SimulationKind=Subcircuit, and adds a datafile link pointing at the file.   }
{ Params: designator, file_path, model_name (subckt name inside the file),    }
{         primitive (default "X")                                             }
{..............................................................................}

Function Gen_AttachSpiceModel(Params : String; RequestId : String) : String;
Var
    AmbigTotal : Integer;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Impl : ISch_Implementation;
    Designator, FilePath, ModelName, Primitive : String;
    Found : Boolean;
Begin
    Designator := ExtractJsonValue(Params, 'designator');
    FilePath := ExtractJsonValue(Params, 'file_path');
    ModelName := ExtractJsonValue(Params, 'model_name');
    Primitive := UpperCase(ExtractJsonValue(Params, 'primitive'));
    If Primitive = '' Then Primitive := 'X';

    If (Designator = '') Or (FilePath = '') Or (ModelName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'designator, file_path, and model_name are all required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    { A multi-part device gives every sub-part the same designator, so a
      first-match here wrote whichever symbol the iterator reached and
      called it success. Refuse instead, and hand back the candidates. }
    AmbigTotal := SchComponentCount(SchDoc, Designator);
    If AmbigTotal > 1 Then
    Begin
        Result := AmbiguousDesignator(SchDoc, Designator, AmbigTotal, RequestId);
        Exit;
    End;


    Found := False;
    SchBeginEdit(SchDoc, 'Attach SPICE model');
    Try
        Iter := SchDoc.SchIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
            Obj := Iter.FirstSchObject;
            While (Obj <> Nil) And Not Found Do
            Begin
                Comp := Obj;
                If Comp.Designator.Text = Designator Then
                Begin
                    SetCompParamText(Comp, 'SpicePrefix', Primitive);
                    SetCompParamText(Comp, 'SpiceModel', ModelName);
                    SetCompParamText(Comp, 'SimulationKind', 'Subcircuit');
                    SetCompParamText(Comp, 'SimulationFile', FilePath);

                    { Also add a datafile link on the first implementation so   }
                    { the file is tracked as a design asset. AddDataFileLink is }
                    { a 3-arg PROCEDURE (EntityName, Location, FileKind), not a }
                    { function returning a link object.                          }
                    Impl := GetFirstSchImplementation(Comp);
                    If Impl <> Nil Then
                        Try Impl.AddDataFileLink(ModelName, FilePath, 'SimModel'); Except End;

                    Found := True;
                End;
                Obj := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchEndEdit(SchDoc, 'Attach SPICE model');
    End;
    SchDoc.GraphicallyInvalidate;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND',
            'Component not found: ' + Designator);
        Exit;
    End;

    { A parameter write dirties the document too: SmartCompile skips its
      recompile while the project looks clean, so a later netlist or ERC
      read answers from the model as it stood before this call. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"designator":"' + EscapeJsonString(Designator) + '",'
        + '"file_path":"' + EscapeJsonString(FilePath) + '",'
        + '"model_name":"' + EscapeJsonString(ModelName) + '"}');
End;

{..............................................................................}
{ Gen_RunSimulation - Run the focused project's mixed-signal simulation and    }
{ report the results document it produced.                                   }
{ Params: analysis (optional label, echoed back), sheet (the schematic whose  }
{         settings to run; empty uses the sheet in front). The analyses come }
{         from the project's simulation setup; a project without one runs    }
{         Altium's default, an operating point.                              }
{                                                                              }
{ MEASURED on AD 26.10.1.6: Sim:RunMixedSim, sent here before, is not a       }
{ process on this build. It ran nothing, wrote nothing, raised no message,   }
{ and the handler reported success. What the schematic's Simulate menu sends }
{ is WorkspaceManager:GenerateReport with ObjectKind=Simulator, Index=1 and  }
{ Action=Run: the first simulator output of the project. A run opens its     }
{ results as a .sdf document and focuses it, so that is the proof it ran.    }
{                                                                              }
{ MEASURED on AD 26.11: that run did operating point, DC sweep, transient and }
{ AC whatever the setup's on/off switches said. The Simulation Dashboard's   }
{ run adds Kind=AdvSimNetlist and SimulationDashboardConfig=Document=<sheet>, }
{ and with it only the analyses switched on ran (DC sweep alone, on the same }
{ setup). Altium reads that selection at the first run after the project     }
{ opens and keeps it until the project is reopened. So a run for a sheet    }
{ uses the dashboard's form, and the first simulator output is used only    }
{ when no sheet is given or in front.                                       }
{..............................................................................}

Function Gen_RunSimulation(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    ServerDoc : IServerDocument;
    AnalysisStr, Before, After, SavedStr, Sheet, RunMode : String;
    OldAge, NewAge : Integer;
Begin
    AnalysisStr := ExtractJsonValue(Params, 'analysis');
    Sheet := ExtractJsonValue(Params, 'sheet');

    Project := Nil;
    Workspace := GetWorkspace;
    If Workspace <> Nil Then Project := Workspace.DM_FocusedProject;
    If Project = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT',
            'No focused project. Activate one of the project''s schematic sheets first.');
        Exit;
    End;
    SmartCompile(Project);

    Before := CurrentFocusedDocPath(0);
    If (Sheet = '') And SameText(ExtractFileExt(Before), '.SchDoc') Then Sheet := Before;
    ResetParameters;
    AddStringParameter('ObjectKind', 'Simulator');
    If Sheet <> '' Then
    Begin
        RunMode := 'sheet';
        AddStringParameter('Kind', 'AdvSimNetlist');
        AddStringParameter('DoEditProperties', 'False');
        AddStringParameter('DoReportStatus', 'False');
        AddStringParameter('Action', 'Run');
        AddStringParameter('DefaultCaption', 'True');
        AddStringParameter('ReturnGeneratedDocuments', 'False');
        AddStringParameter('SimulationDashboardConfig', 'Document=' + Sheet);
    End
    Else
    Begin
        RunMode := 'project';
        AddStringParameter('Index', '1');
        AddStringParameter('Action', 'Run');
    End;
    RunProcess('WorkspaceManager:GenerateReport');
    After := CurrentFocusedDocPath(0);

    If (LowerCase(ExtractFileExt(After)) <> '.sdf') Or (After = Before) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SIM_NOT_RUN',
            'No simulation results document appeared, so the simulation did not run. '
            + 'Check that a schematic sheet of the project is focused and that every part has a '
            + 'simulation model (sim_get_readiness), then read proj_get_messages.');
        Exit;
    End;

    { Save the results so they can be read back: a run leaves its .sdf    }
    { unsaved. The pattern App_CreateDocument uses for a new document     }
    { (path, modified, DoFileSave), with no Save As fallback, which opens }
    { a modal. Judged by the file's age changing, not by the call.       }
    OldAge := -1;
    Try If FileExists(After) Then OldAge := FileAge(After); Except End;
    ServerDoc := Client.GetDocumentByPath(After);
    If (ServerDoc <> Nil) And (Not SaveHeldBack(After)) Then
    Begin
        Try ServerDoc.SetFileName(After); Except { judged by the file age below } End;
        Try
            ServerDoc.SetModified(True);
            ServerDoc.DoFileSave('');
        Except { judged by the file age below } End;
    End;
    NewAge := -1;
    Try If FileExists(After) Then NewAge := FileAge(After); Except End;
    If (NewAge <> -1) And (NewAge <> OldAge) Then SavedStr := 'true' Else SavedStr := 'false';

    { The results stay open in their own tab; tools that act on the focused }
    { document go back to where they were.                                  }
    RestoreFocusedDoc(Before);

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"ran":true,"analysis":"' + EscapeJsonString(AnalysisStr) + '",'
        + '"results_document":"' + EscapeJsonString(After) + '",'
        + '"results_saved":' + SavedStr + ','
        + '"run_mode":"' + RunMode + '","sheet":"' + EscapeJsonString(Sheet) + '",'
        + '"note":"The analyses come from the project''s simulation setup."}');
End;

{..............................................................................}
{ Gen_RunSpiceDeck - Run a SPICE deck (.nsx) in Altium's simulator.           }
{ Params: file_path (the .nsx, written by the caller).                         }
{                                                                              }
{ MEASURED on AD 26.10.1.6: a .nsx (plain SPICE: elements, .TRAN, .PLOT) opened }
{ with WorkspaceManager:OpenObject and run with ADVSIM:RunSimulator opens     }
{ <name>.sdf with one tab per analysis, the .PLOT waves on its chart. It needs }
{ no symbol with a simulation model: a schematic run (Gen_RunSimulation)       }
{ stops at "Missing component models" when a part has none, as a demo board's }
{ hand-drawn simulation sheet did.                                            }
{..............................................................................}

Function Gen_RunSpiceDeck(Params : String; RequestId : String) : String;
Var
    FilePath, Before, After, SavedStr : String;
    ServerDoc : IServerDocument;
    OldAge, NewAge : Integer;
Begin
    FilePath := ExtractJsonValue(Params, 'file_path');
    If LowerCase(ExtractFileExt(FilePath)) <> '.nsx' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_NSX',
            'file_path must be a .nsx SPICE deck: ' + FilePath);
        Exit;
    End;
    If Not FileExists(FilePath) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'No deck at ' + FilePath);
        Exit;
    End;

    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', FilePath);
    RunProcess('WorkspaceManager:OpenObject');
    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_OPENED', 'Altium did not open ' + FilePath);
        Exit;
    End;
    { A deck already open is read again: the caller may have rewritten it. }
    Try If ServerDoc.SupportsReload Then ServerDoc.DoFileLoad; Except End;
    Try ServerDoc.UpdateModifiedDate; Except End;
    Try Client.ShowDocument(ServerDoc); Except End;

    Before := CurrentFocusedDocPath(0);
    ResetParameters;
    RunProcess('ADVSIM:RunSimulator');
    After := CurrentFocusedDocPath(0);
    If (LowerCase(ExtractFileExt(After)) <> '.sdf') Or (After = Before) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SIM_NOT_RUN',
            'No simulation results document appeared, so the deck did not run. '
            + 'Check it ends with .END and has an analysis (.TRAN, .AC, .DC or .OP), '
            + 'then read proj_get_messages.');
        Exit;
    End;

    OldAge := -1;
    Try If FileExists(After) Then OldAge := FileAge(After); Except End;
    ServerDoc := Client.GetDocumentByPath(After);
    If (ServerDoc <> Nil) And (Not SaveHeldBack(After)) Then
    Begin
        Try ServerDoc.SetFileName(After); Except { judged by the file age below } End;
        Try
            ServerDoc.SetModified(True);
            ServerDoc.DoFileSave('');
        Except { judged by the file age below } End;
    End;
    NewAge := -1;
    Try If FileExists(After) Then NewAge := FileAge(After); Except End;
    If (NewAge <> -1) And (NewAge <> OldAge) Then SavedStr := 'true' Else SavedStr := 'false';

    { The results stay in front: the chart is what the user looks at. }
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"ran":true,"deck":"' + EscapeJsonString(FilePath) + '",'
        + '"results_document":"' + EscapeJsonString(After) + '",'
        + '"results_saved":' + SavedStr + '}');
End;

{..............................................................................}
{ Gen_SimCfgDocument - Save or reload a project's simulation settings file.   }
{ Params: action (save | reload), file_path (a .simcfg).                     }
{                                                                              }
{ MEASURED on AD 26.10.1.6: while its project is open Altium runs from the    }
{ settings it holds in memory. An edit on disk is not used by the next run,  }
{ Altium then raises a modal "has been modified ... Reload?", and it later   }
{ saves its own copy over the edit. So a settings change is: save what       }
{ Altium holds (keeping dashboard edits), change the file, reload it.        }
{ Refused for anything but a .simcfg: a reload discards in-memory edits,     }
{ which on a schematic or a board would be somebody's work.                  }
{..............................................................................}

Function Gen_SimCfgDocument(Params : String; RequestId : String) : String;
Var
    Mode, FilePath : String;
    ServerDoc : IServerDocument;
    Done : Boolean;
Begin
    Mode := LowerCase(ExtractJsonValue(Params, 'action'));
    FilePath := ExtractJsonValue(Params, 'file_path');
    If (FilePath = '') Or ((Mode <> 'save') And (Mode <> 'reload')) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'file_path and action (save or reload) are required');
        Exit;
    End;
    If LowerCase(ExtractFileExt(FilePath)) <> '.simcfg' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_SIMCFG',
            'Only a .simcfg is saved or reloaded here: ' + FilePath);
        Exit;
    End;

    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then
    Begin
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"open":false,"done":false,"action":"' + Mode + '",'
            + '"file_path":"' + EscapeJsonString(FilePath) + '",'
            + '"note":"not open in Altium, so the file on disk is what the next run reads"}');
        Exit;
    End;

    Done := False;
    If Mode = 'save' Then
    Begin
        Try
            ServerDoc.SetModified(True);
            ServerDoc.DoFileSave('');
            Done := FileExists(FilePath);
        Except Done := False; End;
    End
    Else
    Begin
        { AD 26.11 reports SupportsReload False for a settings file, yet }
        { the document loads its file again when asked.                 }
        Try Done := ServerDoc.DoFileLoad; Except Done := False; End;
        { Without this Altium still holds the old file date and asks to }
        { reload a second time.                                         }
        Try ServerDoc.UpdateModifiedDate; Except End;
    End;

    If Not Done Then
    Begin
        Result := BuildErrorResponse(RequestId, 'SIMCFG_' + UpperCase(Mode) + '_FAILED',
            'Altium did not ' + Mode + ' ' + FilePath);
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"open":true,"done":true,"action":"' + Mode + '",'
        + '"file_path":"' + EscapeJsonString(FilePath) + '"}');
End;

{..............................................................................}
{ Gen_BatchCreate - Generic bulk create. Each op specifies its own scope,       }
{ object_type, and properties. One shared PreProcess/PostProcess per            }
{ document touched, so N creates cost ~1x the overhead of one create.          }
{ Params: operations = 'scope=active_doc;object_type=eNetLabel;properties=Text=VCC|Location.X=100|Location.Y=200~~scope=...;object_type=...;properties=...' }
{..............................................................................}

Function Gen_BatchCreate(Params : String; RequestId : String) : String;
Var
    Operations, Remaining : String;
    OpCount, Created, Failed : Integer;
    Op, Scope, ObjTypeStr, PropsStr : String;
    ObjTypeInt : Integer;
    SchDoc : ISch_Document;
    SchLib : ISch_Lib;
    Component : ISch_Component;
    NewObj : ISch_GraphicalObject;
    ActiveDoc : ISch_Document;
    ContainerStr : String;
    FailuresJson, ItemReason, UnsupportedProp : String;
    FirstFailure : Boolean;
Begin
    ResetPropertyDiag(0);
    SchDoc := Nil;
    Operations := ExtractJsonValue(Params, 'operations');
    If Operations = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'operations is required');
        Exit;
    End;

    Created := 0;
    Failed := 0;
    OpCount := 0;
    ActiveDoc := SchServer.GetCurrentSchDocument;
    Remaining := Operations;
    FailuresJson := '';
    FirstFailure := True;

    If ActiveDoc <> Nil Then
        SchBeginEdit(ActiveDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            ItemReason := '';
            UnsupportedProp := '';
            ResetPropertyDiag(0);
            Scope := GetBatchField(Op, 'scope');
            If Scope = '' Then Scope := 'active_doc';
            ObjTypeStr := GetBatchField(Op, 'object_type');
            PropsStr := GetBatchField(Op, 'properties');
            ContainerStr := GetBatchField(Op, 'container');
            If ContainerStr = '' Then ContainerStr := 'document';

            ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
            If ObjTypeInt = -1 Then
            Begin
                Inc(Failed);
                ItemReason := 'INVALID_TYPE';
            End
            Else
            Begin
                NewObj := SchServer.SchObjectFactory(ObjTypeInt, eCreate_Default);
                If NewObj = Nil Then
                Begin
                    Inc(Failed);
                    ItemReason := 'CREATE_FAILED';
                End
                Else
                Begin
                    ApplyNoErcDefaults(NewObj);
                    UnsupportedProp := UnsupportedSchProperty(NewObj, PropsStr);
                    If UnsupportedProp <> '' Then
                    Begin
                        SchServer.DestroySchObject(NewObj);
                        Inc(Failed);
                        ItemReason := 'UNSUPPORTED_PROPERTY';
                    End
                    Else
                    Begin
                        ApplySetProperties(NewObj, PropsStr);

                        If ContainerStr = 'component' Then
                        Begin
                            SchLib := SchServer.GetCurrentSchDocument;
                            If (SchLib <> Nil) And (SchLib.ObjectId = eSchLib) Then
                            Begin
                                Component := SchLib.CurrentSchComponent;
                                If Component <> Nil Then
                                Begin
                                    Component.AddSchObject(NewObj);
                                    SchRegisterObject(Component, NewObj);
                                    Inc(Created);
                                End
                                Else
                                Begin
                                    SchServer.DestroySchObject(NewObj);
                                    Inc(Failed);
                                    ItemReason := 'NO_COMPONENT';
                                End;
                            End
                            Else
                            Begin
                                SchServer.DestroySchObject(NewObj);
                                Inc(Failed);
                                ItemReason := 'NO_SCHLIB';
                            End;
                        End
                        Else
                        Begin
                            SchDoc := ActiveDoc;
                            If SchDoc = Nil Then
                            Begin
                                SchServer.DestroySchObject(NewObj);
                                Inc(Failed);
                                ItemReason := 'NO_SCHEMATIC';
                            End
                            Else
                            Begin
                                SchDoc.RegisterSchObjectInContainer(NewObj);
                                SchRegisterObject(SchDoc, NewObj);
                                Inc(Created);
                            End;
                        End;
                    End;
                End;
            End;

            If ItemReason <> '' Then
            Begin
                If Not FirstFailure Then FailuresJson := FailuresJson + ',';
                FirstFailure := False;
                FailuresJson := FailuresJson +
                    '{"index":' + IntToStr(OpCount - 1) +
                    ',"object_type":"' + EscapeJsonString(ObjTypeStr) +
                    '","reason":"' + ItemReason + '"';
                If UnsupportedProp <> '' Then
                    FailuresJson := FailuresJson + ',"property":"' +
                        EscapeJsonString(UnsupportedProp) + '"';
                FailuresJson := FailuresJson + '}';
            End;
        End;
    Finally
        If ActiveDoc <> Nil Then
        Begin
            SchEndEdit(ActiveDoc, '');
            ActiveDoc.GraphicallyInvalidate;
        End;
    End;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"created":' + IntToStr(Created) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) +
        ',"failures":[' + FailuresJson + ']}');
End;

{..............................................................................}
{ Gen_BatchDelete - Generic bulk delete. Each op is one scope/type/filter       }
{ delete expressed in the same format as delete_objects.                       }
{ Params: operations = 'scope=active_doc;object_type=eWire;filter=Text=old~~scope=...' }
{..............................................................................}

Function Gen_BatchDelete(Params : String; RequestId : String) : String;
Var
    Operations, Remaining : String;
    OpCount, OpsRun, OpsPartial, OpMatched, TotalMatched : Integer;
    Op, Scope, ObjTypeStr, FilterStr, ScopeType, ScopePath : String;
    OpResult, Note, ResultsJson : String;
    ObjTypeInt : Integer;
    IsPcb : Boolean;
Begin
    Operations := ExtractJsonValue(Params, 'operations');
    If Operations = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'operations is required');
        Exit;
    End;

    OpsPartial := 0;
    OpsRun := 0;
    OpCount := 0;
    TotalMatched := 0;
    ResultsJson := '';
    Remaining := Operations;

    While True Do
    Begin
        Op := NextBatchOp(Remaining);
        If Op = '' Then Break;
        OpCount := OpCount + 1;
        Scope := GetBatchField(Op, 'scope');
        If Scope = '' Then Scope := 'active_doc';
        ObjTypeStr := GetBatchField(Op, 'object_type');
        FilterStr := GetBatchField(Op, 'filter');
        Note := '';
        OpMatched := 0;
        ResetPropertyDiag(0);

        { A BOARD TYPE WAS SKIPPED HERE WITHOUT A WORD. Only the schematic  }
        { table was looked up, so eTrackObject fell to the unknown-type      }
        { branch, which moved on: the reply counted one operation fewer than }
        { it was sent and named none. Board types now go where obj_delete    }
        { sends them, to the active board, and every operation reports what  }
        { it deleted or why it did not run.                                  }
        IsPcb := False;
        ObjTypeInt := ObjectTypeFromString(ObjTypeStr);
        If ObjTypeInt = -1 Then
        Begin
            ObjTypeInt := ObjectTypeFromStringPCB(ObjTypeStr);
            IsPcb := (ObjTypeInt <> -1);
        End;
        If ObjTypeInt = -1 Then Note := 'unknown_object_type';

        ParseScope(Scope, ScopeType, ScopePath);
        If (Note = '') And (UnknownScopeError(RequestId, Scope, ScopeType) <> '') Then
            Note := 'unknown_scope';
        If (Note = '') And IsPcb And (ScopeType <> 'active_doc') Then
            Note := 'scope_not_supported';
        { EACH OP'S lib_component SCOPE SELECTS ITS OWN SYMBOL. Without this a  }
        { lib_component scope fell through to the active document, so a batch  }
        { naming two symbols deleted from whichever one was current: ops for   }
        { the first symbol emptied the second, and the second's ops matched    }
        { nothing. Gen_BatchModify already did this.                           }
        If (Note = '') And (Not IsPcb) Then
            If Not ApplyLibComponentScope(ScopeType, ScopePath) Then
                Note := 'lib_component_not_found';

        If Note = '' Then
        Begin
            If IsPcb Then
                OpResult := ProcessActivePCBDoc(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0)
            Else If ScopeType = 'project' Then
                OpResult := IterateProjectDocs(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, ScopePath, 0)
            Else If ScopeType = 'doc' Then
                OpResult := ProcessDocByPath(ScopePath, ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0)
            Else
                OpResult := ProcessActiveDoc(ObjTypeInt, FilterStr, '', '', 'delete', RequestId, 0);
            { A delete that stopped part way still removed what it counted:
              read matched on a failure too, or the reply says nothing was
              deleted and the caller repeats it. }
            OpMatched := StrToIntDef(JsonValueAnywhere(OpResult, 'matched'), 0);
            If Pos('"partial":true', OpResult) > 0 Then
            Begin
                Note := 'partial: ' + JsonValueAnywhere(OpResult, 'delete_error');
                Inc(OpsPartial);
            End
            Else If Pos('"success":false', OpResult) > 0 Then
            Begin
                Note := 'failed: ' + JsonValueAnywhere(OpResult, 'code');
            End
            Else
            Begin
                If OpMatched = 0 Then Note := 'no_objects_matched';
            End;
            TotalMatched := TotalMatched + OpMatched;
            Inc(OpsRun);
        End;

        If ResultsJson <> '' Then ResultsJson := ResultsJson + ',';
        ResultsJson := ResultsJson +
            '{"object_type":"' + EscapeJsonString(ObjTypeStr) + '"' +
            ',"filter":"' + EscapeJsonString(FilterStr) + '"' +
            ',"deleted":' + IntToStr(OpMatched) +
            ',"note":"' + EscapeJsonString(Note) + '"}';
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"operations_processed":' + IntToStr(OpsRun) + ',"total":' + IntToStr(OpCount)
        + ',"total_deleted":' + IntToStr(TotalMatched)
        + ',"operations_partial":' + IntToStr(OpsPartial)
        + ',"results":[' + ResultsJson + ']}');
End;

{..............................................................................}
{ Gen_PlaceWires - Bulk wire placement on the active schematic.                 }
{ Params: wires = 'x1=100;y1=200;x2=300;y2=200~~x1=300;y1=200;x2=300;y2=400~~...' }
{..............................................................................}

Function Gen_PlaceWires(Params : String; RequestId : String) : String;
Var
    WireStr, Remaining : String;
    OpCount, Placed, Failed : Integer;
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    Wire : ISch_Wire;
    Op : String;
Begin
    WireStr := ExtractJsonValue(Params, 'wires');
    If WireStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'wires is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := WireStr;

    SchBeginEdit(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            X1 := StrToFloatDef(GetBatchField(Op, 'x1'), 0);
            Y1 := StrToFloatDef(GetBatchField(Op, 'y1'), 0);
            X2 := StrToFloatDef(GetBatchField(Op, 'x2'), 0);
            Y2 := StrToFloatDef(GetBatchField(Op, 'y2'), 0);

            Wire := SchServer.SchObjectFactory(eWire, eCreate_Default);
            If Wire = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            { Two-vertex wire: insert vertex 1 then vertex 2 explicitly. }
            Wire.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
            Wire.InsertVertex := 1;
            Wire.SetState_Vertex(1, Point(MilsToCoordF(X1), MilsToCoordF(Y1)));
            Wire.InsertVertex := 2;
            Wire.SetState_Vertex(2, Point(MilsToCoordF(X2), MilsToCoordF(Y2)));
            { Color := 0 makes wires render BLACK and look like graphic
              lines; leave Wire.Color at the factory default so the
              schematic editor's wire colour scheme applies (blue by
              default). LineWidth=eSmall is the canonical wire weight. }
            Wire.LineWidth := eSmall;

            SchDoc.RegisterSchObjectInContainer(Wire);
            SchRegisterObject(SchDoc, Wire);
            Inc(Placed);
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ PutLoadedComponent - add a component returned by a loader to the sheet at   }
{ (X, Y) mils, turned by Rotation degrees (0/90/180/270), with Desig as its   }
{ designator when one is given. Shared by the file and managed placements.    }
{..............................................................................}
Procedure PutLoadedComponent(SchDoc : ISch_Document; Comp : ISch_Component;
    X : Double; Y : Double; Rotation : Integer; Desig : String);
Var
    Orientation : Integer;
Begin
    Try SchDoc.AddSchObject(Comp); Except NoteWriteFailed('add component to sheet'); End;
    Try Comp.MoveToXY(MilsToCoordF(X), MilsToCoordF(Y)); Except NoteWriteFailed('component MoveToXY'); End;
    Orientation := 0;
    If Rotation = 90 Then Orientation := 1
    Else If Rotation = 180 Then Orientation := 2
    Else If Rotation = 270 Then Orientation := 3;
    Try Comp.SetState_Orientation(Orientation); Except NoteWriteFailed('component Orientation'); End;
    If Desig <> '' Then
    Begin
        Try Comp.Designator.Text := Desig; Except NoteWriteFailed('component Designator'); End;
    End;
    SchRegisterObject(SchDoc, Comp);
    { Its designator and comment where Altium puts them for this rotation, }
    { not where the library left them (see SchAutopositionLabels).         }
    SchAutopositionLabels(Comp, 'rotate', False);
End;

{..............................................................................}
{ Gen_PlaceSchComponentsFromLibrary - Bulk BOM placement.                       }
{ Each op: library_path, lib_reference, x, y, designator, rotation, footprint, }
{ part_id. library_path and lib_reference are required; others have sane      }
{ defaults. part_id places one part of a multi-part symbol (U2 part 3): the   }
{ same designator can then appear once per part. It is read back, and a part  }
{ id the symbol does not have fails that placement (PART_REFUSED), because    }
{ the assignment is accepted and silently does not take.                      }
{..............................................................................}

{ Make a part just loaded from a library look like those already on the  }
{ sheet (measured on AD 26.11.1.5). Its link: SourceLibraryName read "*",  }
{ and Altium then refuses to change its Design Item ID in Properties; the  }
{ writes are those Gen_RelinkComponents makes for a SchLib or IntLib part. }
{ The parameters its models bring, which arrive shown at the sheet origin, }
{ are hidden; the part's own parameters are left as they are. And the      }
{ designator takes the sheet's designator font when there is one. Answers  }
{ JSON fields saying what it found and changed.                            }
Function TidyLoadedComponent(Part : ISch_Component; LibFileName, LibRef : String;
    SheetDesFont : Integer) : String;
Var
    Walk : ISch_Iterator;
    Param : ISch_Parameter;
    OwnNames, Name, Link : String;
    NowHidden, OldFont : Integer;
Begin
    NowHidden := 0;
    OldFont := 0;
    Link := '';
    SchBeginModify(Part);
    Try
        If LibFileName <> '' Then
        Begin
            Try Part.UseDBTableName := False; Except NoteWriteFailed('library link UseDBTableName'); End;
            Try Part.DatabaseTableName := ''; Except NoteWriteFailed('library link DatabaseTableName'); End;
            Try Part.UseLibraryName := True; Except NoteWriteFailed('library link UseLibraryName'); End;
            Try Part.SourceLibraryName := LibFileName; Except NoteWriteFailed('library link SourceLibraryName'); End;
            Try Part.LibReference := LibRef; Except NoteWriteFailed('library link LibReference'); End;
            Try Part.DesignItemId := LibRef; Except NoteWriteFailed('library link DesignItemId'); End;
        End;
        Try Link := Part.SourceLibraryName; Except Link := ''; End;

        { The part's own parameter names, as '|NAME|NAME|'. }
        OwnNames := '|';
        Walk := Part.SchIterator_Create;
        Try
            Walk.AddFilter_ObjectSet(MkSet(eParameter));
            Walk.SetState_IterationDepth(eIterateFirstLevel);
            Param := Walk.FirstSchObject;
            While Param <> Nil Do
            Begin
                Try OwnNames := OwnNames + UpperCase(Param.Name) + '|'; Except End;
                Param := Walk.NextSchObject;
            End;
        Finally
            Part.SchIterator_Destroy(Walk);
        End;
        { Every other parameter under the part came with a model. }
        Walk := Part.SchIterator_Create;
        Try
            Walk.AddFilter_ObjectSet(MkSet(eParameter));
            Param := Walk.FirstSchObject;
            While Param <> Nil Do
            Begin
                Name := '';
                Try Name := UpperCase(Param.Name); Except Name := ''; End;
                If (Pos('|' + Name + '|', OwnNames) = 0) And (Not Param.IsHidden) Then
                Begin
                    Param.IsHidden := True;
                    Inc(NowHidden);
                End;
                Param := Walk.NextSchObject;
            End;
        Finally
            Part.SchIterator_Destroy(Walk);
        End;

        If SheetDesFont > 0 Then
        Begin
            Try OldFont := Part.Designator.FontId; Except OldFont := 0; End;
            If OldFont <> SheetDesFont Then
            Begin
                Try Part.Designator.FontId := SheetDesFont; Except NoteWriteFailed('Designator.FontId'); End;
            End;
        End;
    Finally
        SchEndModify(Part);
    End;
    Result := JsonStr('source_library', Link) + ',' + JsonInt('model_params_hidden', NowHidden);
    If (SheetDesFont > 0) And (OldFont <> SheetDesFont) Then
        Result := Result + ',' + JsonInt('designator_font_id', SheetDesFont);
End;

Function Gen_PlaceSchComponentsFromLibrary(Params : String; RequestId : String) : String;
Var
    PlaceStr, Op, Remaining, FailedRefdes, ResponseBody : String;
    OpCount, Placed, Failed, Rotation, PartId, PartAfter : Integer;
    LibPath, LibRef, Desig, Footprint, AvailHint : String;
    ResolvedLib, LibReason, FpBefore, FpAfter, FpIssues : String;
    FpChosen, FpChoice, SheetDesFont : Integer;
    LibFileName, MatchedRows : String;
    MatchSheet : Boolean;
    X, Y : Double;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
Begin
    PlaceStr := ExtractJsonValue(Params, 'placements');
    If PlaceStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'placements is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := PlaceStr;
    FailedRefdes := '';
    FpIssues := '';
    FpChosen := 0;
    MatchedRows := '';
    { Taken before the new parts exist, so they do not count towards it. }
    MatchSheet := Not SameText(ExtractJsonValue(Params, 'tidy'), 'false');
    SheetDesFont := 0;
    If MatchSheet Then SheetDesFont := SheetFontFor(SchDoc, eDesignator);

    SchBeginEdit(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            LibPath := GetBatchField(Op, 'library_path');
            LibRef := GetBatchField(Op, 'lib_reference');
            Desig := GetBatchField(Op, 'designator');
            Footprint := GetBatchField(Op, 'footprint');
            X := StrToFloatDef(GetBatchField(Op, 'x'), 0);
            Y := StrToFloatDef(GetBatchField(Op, 'y'), 0);
            Rotation := StrToIntDef(GetBatchField(Op, 'rotation'), 0);
            { Part 1 when not given: a symbol is placed as its first part   }
            { unless asked otherwise, and the part is written either way.  }
            PartId := StrToIntDef(GetBatchField(Op, 'part_id'), 1);
            If PartId < 1 Then PartId := 1;

            If LibRef = '' Then
            Begin
                Inc(Failed);
                If Desig <> '' Then
                Begin
                    If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                    FailedRefdes := FailedRefdes + Desig + ':MISSING_LIB_REF';
                End;
                Continue;
            End;

            { AN ABSENT PATH IS NOT A FAILED LOAD, and conflating the two    }
            { cost a user most of a day. LoadComponentFromLibrary with an    }
            { empty path returns Nil, which used to be reported as           }
            { LOAD_FAILED: an error that points at the library, when the     }
            { library was never named. It happens whenever the caller        }
            { spells the field wrong, because an unread key just vanishes.   }
            { Say which of the two it was.                                    }
            If LibPath = '' Then
            Begin
                Inc(Failed);
                If Desig <> '' Then
                Begin
                    If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                    FailedRefdes := FailedRefdes + Desig + ':NO_LIBRARY_PATH';
                End;
                If AvailHint = '' Then
                    AvailHint := 'library_path was empty for at least one '
                        + 'placement: the key is "library_path", and a '
                        + 'misspelled key is dropped before it reaches here';
                Continue;
            End;

            { RESOLVE BEFORE ALTIUM SEES THE PATH. An .IntLib handed to     }
            { CreateLibCompInfoReader or LoadComponentFromLibrary raises the }
            { "Open Integrated Library" modal, which nothing here can catch  }
            { and which stops the polling loop until a human clicks it. A    }
            { placed component reports its library AS an .IntLib, so callers }
            { copying source_library off the sheet hit this every time.      }
            { The link names the library the caller named (an IntLib stays  }
            { an IntLib), not the SchLib it was loaded through.             }
            LibFileName := ExtractFileName(LibPath);
            If LibPath <> '' Then
            Begin
                ResolvedLib := ResolveSchLibForLoad(LibPath, LibReason);
                If ResolvedLib = '' Then
                Begin
                    Inc(Failed);
                    If Desig <> '' Then
                    Begin
                        If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                        FailedRefdes := FailedRefdes + Desig + ':UNRESOLVABLE_LIBRARY';
                    End;
                    If AvailHint = '' Then AvailHint := LibReason;
                    Continue;
                End;
                LibPath := ResolvedLib;
            End;

            { Pre-validate to short-circuit any internal-popup path. }
            If LibPath <> '' Then
                If Not ResolveLibRef(LibPath, LibRef, AvailHint) Then
                Begin
                    Inc(Failed);
                    If Desig <> '' Then
                    Begin
                        If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                        FailedRefdes := FailedRefdes + Desig + ':RESOLVE_FAILED';
                    End;
                    Continue;
                End;

            { Working SchDoc placement API: LoadComponentFromLibrary +     }
            { AddSchObject + MoveToXY + SetState_Orientation. Note arg     }
            { order (REF, PATH) is opposite of PlaceSchComponent.           }
            Comp := Nil;
            Try
                Comp := SchServer.LoadComponentFromLibrary(LibRef, LibPath);
            Except
                Comp := Nil;
            End;
            If Comp = Nil Then
            Begin
                Inc(Failed);
                If Desig <> '' Then
                Begin
                    If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                    FailedRefdes := FailedRefdes + Desig + ':LOAD_FAILED';
                End;
                Continue;
            End;

            { The part is set BEFORE the component joins the sheet, through   }
            { both setters, the order the replicate path uses. A loaded       }
            { symbol does not reliably arrive as part 1: one placed as part 1 }
            { landed as part 2, and parts written only after the add left     }
            { the compiled netlist listing every pin of the symbol on every   }
            { part, with the hidden pins joined into nets. Part 1 is written  }
            { too when it is asked for, never assumed.                         }
            If PartId >= 1 Then
            Begin
                Try Comp.SetState_CurrentPartID(PartId); Except { written and checked again on the sheet below } End;
                Try Comp.CurrentPartID := PartId; Except { written and checked again on the sheet below } End;
            End;
            PutLoadedComponent(SchDoc, Comp, X, Y, Rotation, Desig);
            If PartId >= 1 Then
            Begin
                { Again on the sheet, inside the modify bracket: the write  }
                { above decides what lands, this one is what compiles.      }
                SetSchPartId(Comp, PartId);
                PartAfter := -1;
                Try PartAfter := Comp.CurrentPartID; Except End;
                If PartAfter <> PartId Then
                Begin
                    Inc(Failed);
                    If FailedRefdes <> '' Then FailedRefdes := FailedRefdes + ',';
                    FailedRefdes := FailedRefdes + Desig + ':PART_REFUSED';
                    Continue;
                End;
            End;
            { The footprint the placement names: one of the symbol's own PCB }
            { models, made its only current one. CurrentFootprintModelName  }
            { is read-only, so the model's IsCurrent flags are written       }
            { instead. A name the symbol lacks, or a choice that does not    }
            { read back, keeps the part on the library's model and is named  }
            { in footprint_issues with the models it carries.                }
            If Footprint <> '' Then
            Begin
                FpChoice := ChooseFootprintModel(Comp, Footprint, FpBefore, FpAfter);
                If FpChoice = 1 Then
                    Inc(FpChosen)
                Else
                Begin
                    If FpIssues <> '' Then FpIssues := FpIssues + ',';
                    If FpChoice = 0 Then
                        FpIssues := FpIssues + '{"designator":"' + EscapeJsonString(Desig)
                            + '","issue":"FOOTPRINT_NOT_ON_SYMBOL","kept":"'
                            + EscapeJsonString(FpBefore) + '","models":"'
                            + EscapeJsonString(FootprintModelNames(Comp)) + '"}'
                    Else
                        FpIssues := FpIssues + '{"designator":"' + EscapeJsonString(Desig)
                            + '","issue":"FOOTPRINT_NOT_APPLIED","kept":"'
                            + EscapeJsonString(FpBefore) + '"}';
                End;
            End;
            If MatchSheet Then
                MatchedRows := MatchedRows + ',' + JsonObj(JsonStr('designator', Desig) + ','
                    + TidyLoadedComponent(Comp, LibFileName, LibRef, SheetDesFont));
            Inc(Placed);
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    ResponseBody := '{"placed":' + IntToStr(Placed)
        + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount)
        + ',"footprints_chosen":' + IntToStr(FpChosen);
    If FpIssues <> '' Then
        ResponseBody := ResponseBody + ',"footprint_issues":[' + FpIssues + ']';
    If MatchedRows <> '' Then
        ResponseBody := ResponseBody + ',' + JsonRaw('tidied', '['
            + Copy(MatchedRows, 2, Length(MatchedRows)) + ']');
    If FailedRefdes <> '' Then
        ResponseBody := ResponseBody + ',"failed_refdes":"'
            + EscapeJsonString(FailedRefdes) + '"';
    ResponseBody := ResponseBody + '}';
    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId, ResponseBody);
End;

{..............................................................................}
{ Gen_PlaceSchVaultComponents - place managed (Workspace) parts in one call.   }
{                                                                              }
{ The managed counterpart of Gen_PlaceSchComponentsFromLibrary: the part is   }
{ loaded with SchServer.LoadComponent from (kind, Workspace name, Design Item }
{ ID) instead of from a library file, and is placed the same way after that.  }
{ A load that raises means this host cannot load managed parts at all, and a  }
{ Nil means that one ID did not resolve; the two are reported apart. A part   }
{ on the sheet is only linked to its Workspace item when it reads back a      }
{ VaultGUID and an ItemGUID, which is what the file saves; a Design Item ID   }
{ alone can stay behind as text on an unlinked copy.                          }
{ Each op: design_item_id, x, y, designator, rotation. vault and             }
{ lib_identifier_kind apply to the whole batch.                               }
{..............................................................................}
Function Gen_PlaceSchVaultComponents(Params : String; RequestId : String) : String;
Var
    PlaceStr, Op, Remaining, ItemsJson, VaultName, Note : String;
    DesignItemId, Desig, Reason, LoadedId, VaultGuid, ItemGuid, RevGuid : String;
    OpCount, Placed, Linked, Failed, Kind, PartId, PartAfter : Integer;
    SchDoc : ISch_Document;
    Comp : ISch_Component;
    Raised, AnyRaised, IsPlaced, IsLinked : Boolean;
Begin
    PlaceStr := ExtractJsonValue(Params, 'placements');
    If PlaceStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'placements is required');
        Exit;
    End;
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    If Not ResolveVaultName(ExtractJsonValue(Params, 'vault'), VaultName, Note) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'VAULT_NOT_RESOLVED',
            VaultNotResolvedMessage(0));
        Exit;
    End;
    Kind := StrToIntDef(ExtractJsonValue(Params, 'lib_identifier_kind'),
        LIB_IDENT_KIND_VAULT_NAME);

    Placed := 0;
    Linked := 0;
    Failed := 0;
    OpCount := 0;
    AnyRaised := False;
    ItemsJson := '';
    Remaining := PlaceStr;
    SchBeginEdit(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            Inc(OpCount);
            DesignItemId := GetBatchField(Op, 'design_item_id');
            Desig := GetBatchField(Op, 'designator');
            { The part of a multi-part component, as the library placement  }
            { takes it: part 1 unless asked, written before the add so the  }
            { loader's own pick (the last part, measured) never lands.      }
            PartId := StrToIntDef(GetBatchField(Op, 'part_id'), 1);
            If PartId < 1 Then PartId := 1;
            PartAfter := -1;
            IsPlaced := False;
            IsLinked := False;
            Reason := '';
            LoadedId := '';
            VaultGuid := '';
            ItemGuid := '';
            RevGuid := '';

            Comp := Nil;
            Raised := False;
            If DesignItemId = '' Then
                Reason := 'MISSING_DESIGN_ITEM_ID'
            Else
            Begin
                Try
                    Comp := SchServer.LoadComponent(Kind, VaultName, DesignItemId);
                Except
                    Raised := True;
                End;
                If Raised Then
                Begin
                    AnyRaised := True;
                    Reason := 'LOAD_RAISED';
                End
                Else If Comp = Nil Then
                    Reason := 'NOT_FOUND_IN_VAULT';
            End;

            If Comp = Nil Then
                Inc(Failed)
            Else
            Begin
                Try Comp.SetState_CurrentPartID(PartId); Except { written and checked again on the sheet below } End;
                Try Comp.CurrentPartID := PartId; Except { written and checked again on the sheet below } End;
                PutLoadedComponent(SchDoc, Comp,
                    StrToFloatDef(GetBatchField(Op, 'x'), 0),
                    StrToFloatDef(GetBatchField(Op, 'y'), 0),
                    StrToIntDef(GetBatchField(Op, 'rotation'), 0), Desig);
                SetSchPartId(Comp, PartId);
                Try PartAfter := Comp.CurrentPartID; Except End;
                IsPlaced := True;
                If PartAfter = PartId Then Inc(Placed)
                Else
                Begin
                    { On the sheet, but not as the part asked for. }
                    Inc(Failed);
                    Reason := 'PART_REFUSED';
                End;
                Try LoadedId := Comp.DesignItemId; Except End;
                Try VaultGuid := Comp.VaultGUID; Except End;
                Try ItemGuid := Comp.ItemGUID; Except End;
                Try RevGuid := Comp.RevisionGUID; Except End;
                IsLinked := (VaultGuid <> '') And (ItemGuid <> '');
                If IsLinked Then Inc(Linked)
                Else If Reason = '' Then Reason := 'PLACED_WITHOUT_LINK';
            End;

            If ItemsJson <> '' Then ItemsJson := ItemsJson + ',';
            ItemsJson := ItemsJson + '{"designator":"' + EscapeJsonString(Desig) + '"'
                + ',"design_item_id":"' + EscapeJsonString(DesignItemId) + '"'
                + ',"placed":' + BoolToJsonStr(IsPlaced)
                + ',"linked":' + BoolToJsonStr(IsLinked)
                + ',"part_id":' + IntToStr(PartAfter)
                + ',"loaded_design_item_id":"' + EscapeJsonString(LoadedId) + '"'
                + ',"vault_guid":"' + EscapeJsonString(VaultGuid) + '"'
                + ',"item_guid":"' + EscapeJsonString(ItemGuid) + '"'
                + ',"revision_guid":"' + EscapeJsonString(RevGuid) + '"'
                + ',"reason":"' + Reason + '"}';
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { Marked dirty so the next compile and a deferred save both see it; see }
    { Gen_PlaceSchComponentsFromLibrary. Nothing placed, nothing marked.    }
    If Placed > 0 Then MarkDocDirtyByPath(SchDoc.DocumentName);

    If AnyRaised Then
        Note := 'SchServer.LoadComponent raised, so this host cannot load '
            + 'managed parts and no ID could be tried.';

    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed)
        + ',"linked":' + IntToStr(Linked)
        + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount)
        + ',"vault":"' + EscapeJsonString(VaultName) + '"'
        + ',"lib_identifier_kind":' + IntToStr(Kind)
        + ',"loader_callable":' + BoolToJsonStr(Not AnyRaised)
        + ',"items":[' + ItemsJson + ']'
        + ',"note":"' + EscapeJsonString(Note) + '"}');
End;

{ Gen_PlaceNetLabels - Bulk net-label placement on the active schematic.        }
{ Params: labels = 'text=VCC;x=100;y=200;orientation=0~~text=GND;x=...'         }
{ One PreProcess/PostProcess wraps the whole batch; cuts ~1s/label of overhead. }
{..............................................................................}

Function Gen_PlaceNetLabels(Params : String; RequestId : String) : String;
Var
    LabelsStr, Op, Remaining : String;
    OpCount, Placed, Failed, Orientation, Justification : Integer;
    Text : String;
    X, Y : Double;
    SchDoc : ISch_Document;
    NetLabel : ISch_NetLabel;
    Loc : TLocation;
    InfFont : Integer;
    StyleFound : Boolean;
Begin
    LabelsStr := ExtractJsonValue(Params, 'labels');
    If LabelsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'labels is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := LabelsStr;

    { Sheet convention once per batch, applied to every label below. }
    StyleFound := InferNetLabelStyle(SchDoc, InfFont);

    SchBeginEdit(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            Text := GetBatchField(Op, 'text');
            X := StrToFloatDef(GetBatchField(Op, 'x'), 0);
            Y := StrToFloatDef(GetBatchField(Op, 'y'), 0);
            Orientation := StrToIntDef(GetBatchField(Op, 'orientation'), 0);
            { Justification 2 = bottom-right: text ENDS at the anchor so a }
            { label on a LEFT-facing pin reads to the left of the pin      }
            { while the anchor (electrical hotspot) stays on the wire.     }
            Justification := StrToIntDef(GetBatchField(Op, 'justification'), 0);

            If Text = '' Then
            Begin
                Inc(Failed);
                Continue;
            End;

            NetLabel := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
            If NetLabel = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Loc := NetLabel.Location;
            Loc.X := MilsToCoordF(X);
            Loc.Y := MilsToCoordF(Y);
            NetLabel.Location := Loc;
            NetLabel.Text := Text;
            NetLabel.Orientation := Orientation;
            Try NetLabel.Justification := Justification; Except NoteWriteFailed('NetLabel.Justification'); End;
            If StyleFound Then
            Begin
                Try NetLabel.FontId := InfFont; Except NoteWriteFailed('NetLabel.FontId'); End;
            End;
            NetLabel.Color := NET_LABEL_COLOR;

            SchDoc.RegisterSchObjectInContainer(NetLabel);
            SchRegisterObject(SchDoc, NetLabel);
            Inc(Placed);
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_PlacePowerPorts - Bulk power-port placement on the active schematic.     }
{ Params: ports = 'text=VCC;x=100;y=200;style=bar;orientation=1~~text=GND;...'  }
{..............................................................................}

Function Gen_PlacePowerPorts(Params : String; RequestId : String) : String;
Var
    PortsStr, Op, Remaining, Text, StyleStr : String;
    OpCount, Placed, Failed, OrientationVal : Integer;
    X, Y : Double;
    SchDoc : ISch_Document;
    PowerObj : ISch_PowerObject;
    Loc : TLocation;
    FontPick : Integer;
Begin
    PortsStr := ExtractJsonValue(Params, 'ports');
    If PortsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'ports is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    FontPick := NewObjectFontId(SchDoc, ePowerObject, Params);
    Placed := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := PortsStr;

    SchBeginEdit(SchDoc, '');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;

            Text := GetBatchField(Op, 'text');
            X := StrToFloatDef(GetBatchField(Op, 'x'), 0);
            Y := StrToFloatDef(GetBatchField(Op, 'y'), 0);
            StyleStr := GetBatchField(Op, 'style');
            OrientationVal := StrToIntDef(GetBatchField(Op, 'orientation'), -1);
            If StyleStr = '' Then StyleStr := 'circle';

            If (Text = '') Or (Not IsPowerPortStyle(StyleStr)) Then
            Begin
                Inc(Failed);
                Continue;
            End;

            PowerObj := SchServer.SchObjectFactory(ePowerObject, eCreate_Default);
            If PowerObj = Nil Then
            Begin
                Inc(Failed);
                Continue;
            End;

            Loc := PowerObj.Location;
            Loc.X := MilsToCoordF(X);
            Loc.Y := MilsToCoordF(Y);
            PowerObj.Location := Loc;
            PowerObj.Text := Text;
            PowerObj.ShowNetName := True;

            If StyleStr = 'arrow' Then PowerObj.Style := ePowerArrow
            Else If StyleStr = 'bar' Then PowerObj.Style := ePowerBar
            Else If StyleStr = 'wave' Then PowerObj.Style := ePowerWave
            Else If StyleStr = 'gnd_power' Then PowerObj.Style := ePowerGndPower
            Else If StyleStr = 'gnd_signal' Then PowerObj.Style := ePowerGndSignal
            Else If StyleStr = 'gnd_earth' Then PowerObj.Style := ePowerGndEarth
            Else PowerObj.Style := ePowerCircle;

            If OrientationVal < 0 Then
            Begin
                If IsGroundPortStyle(StyleStr) Then
                    OrientationVal := 3
                Else
                    OrientationVal := 1;
            End;
            Try PowerObj.Orientation := OrientationVal; Except NoteWriteFailed('PowerPort.Orientation'); End;
            If FontPick > 0 Then
            Begin
                Try PowerObj.FontId := FontPick; Except NoteWriteFailed('PowerPort.FontId'); End;
            End;

            SchDoc.RegisterSchObjectInContainer(PowerObj);
            SchRegisterObject(SchDoc, PowerObj);
            Inc(Placed);
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"placed":' + IntToStr(Placed) + ',"failed":' + IntToStr(Failed)
        + ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_GetSchDocPins - Whole-sheet pin dump in one IPC call.                    }
{ Params: sheet_path (optional, defaults to active doc).                        }
{ Returns object with "pins" array; each entry has refdes, pin_number,       }
{ pin_name, x_mils, y_mils, orientation, pin_length_mils, part_id.           }
{                                                                              }
{ ONLY THE PINS OF THE PART EACH INSTANCE SHOWS. A multi-part symbol placed   }
{ as U2 part 1, 2 and 3 is three components that each carry EVERY pin of the }
{ symbol, the other parts' pins hidden at this instance's positions. Read    }
{ unfiltered, each U2 pin came back three times and the template wiring     }
{ check reported 15 false shorts (GND joined to an I2C clock). Kept: a pin   }
{ whose OwnerPartId is 0 (common to every part) or the instance's           }
{ CurrentPartID, the filter SchPinMap already applies.                        }
{..............................................................................}

Function Gen_GetSchDocPins(Params : String; RequestId : String) : String;
Var
    SheetPath, PinList, RefDes, PinNum, PinName : String;
    SchDoc : ISch_Document;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    PinOrient, PartId, Owner : Integer;
    PinX, PinY, PinLenMils : Double;
    First : Boolean;
    DataBlob : String;
Begin
    SheetPath := ExtractJsonValue(Params, 'sheet_path');

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    PinList := '';
    First := True;

    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        While Comp <> Nil Do
        Begin
            RefDes := '';
            Try RefDes := Comp.Designator.Text; Except End;
            If RefDes <> '' Then
            Begin
                PartId := 0;
                Try PartId := Comp.CurrentPartID; Except End;
                PinIter := Comp.SchIterator_Create;
                Try
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Pin := PinIter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                        Owner := 0;
                        Try Owner := Pin.OwnerPartId; Except End;
                        If (Owner = 0) Or (Owner = PartId) Then
                        Begin
                        PinNum := '';
                        PinName := '';
                        PinX := 0;
                        PinY := 0;
                        PinOrient := 0;
                        PinLenMils := 0;
                        Try PinNum := Pin.Designator; Except End;
                        Try PinName := Pin.Name; Except End;
                        Try PinX := CoordToMilsF(Pin.Location.X); Except End;
                        Try PinY := CoordToMilsF(Pin.Location.Y); Except End;
                        Try PinOrient := Pin.Orientation; Except End;
                        Try PinLenMils := CoordToMilsF(Pin.PinLength); Except End;

                        If Not First Then PinList := PinList + ',';
                        First := False;
                        PinList := PinList +
                            '{"refdes":"' + EscapeJsonString(RefDes) +
                            '","pin_number":"' + EscapeJsonString(PinNum) +
                            '","pin_name":"' + EscapeJsonString(PinName) +
                            '","x_mils":' + FloatToJsonStr(PinX) +
                            ',"y_mils":' + FloatToJsonStr(PinY) +
                            ',"orientation":' + IntToStr(PinOrient) +
                            ',"pin_length_mils":' + FloatToJsonStr(PinLenMils) +
                            ',"part_id":' + IntToStr(PartId) + '}';
                        End;

                        Pin := PinIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PinIter);
                End;
            End;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    DataBlob := '{"pins":[' + PinList + ']}';
    Result := BuildSuccessResponse(RequestId, DataBlob);
End;

{..............................................................................}
{ Gen_SetSchComponentsParameters - Bulk parameter stamping.                    }
{ Params: stamps = 'designator=R1;Value=10k;Manufacturer=Yageo;MPN=...;        }
{                   Footprint=0603~~designator=R2;Value=1k;Manufacturer=...'    }
{ One PreProcess/PostProcess wraps every component update; iterate the doc     }
{ ONCE and apply matching ops on the fly. Avoids 14 singular IPC round-trips. }
{..............................................................................}

Function Gen_SetSchComponentsParameters(Params : String; RequestId : String) : String;
Var
    StampsStr, SheetPath, Op, Remaining : String;
    OpCount, Updated, Failed, OpIdx : Integer;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    DesigList : TStringList;
    OpsList : TStringList;
    OpStr, FieldStr, Key, Val : String;
    P, EqPos, SemiPos : Integer;
    SrvDoc : IServerDocument;
    FpBefore, FpAfter, FpMissed : String;
    FpApplied : Integer;
Begin
    StampsStr := ExtractJsonValue(Params, 'stamps');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');

    If StampsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'stamps required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    { Build parallel arrays: designator -> raw op string.                     }
    DesigList := TStringList.Create;
    OpsList := TStringList.Create;
    Try
        Remaining := StampsStr;
        OpCount := 0;
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            FieldStr := GetBatchField(Op, 'designator');
            If FieldStr <> '' Then
            Begin
                DesigList.Add(FieldStr);
                OpsList.Add(Op);
            End;
        End;

        Updated := 0;
        Failed := 0;
        FpApplied := 0;
        FpMissed := '';

        SchBeginEdit(SchDoc, '');
        Try
            Iter := SchDoc.SchIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Comp := Obj;
                    OpIdx := DesigList.IndexOf(Comp.Designator.Text);
                    If OpIdx >= 0 Then
                    Begin
                        OpStr := OpsList[OpIdx];
                        { Iterate semicolon-separated key=value fields.       }
                        P := 1;
                        While P <= Length(OpStr) Do
                        Begin
                            SemiPos := P;
                            While (SemiPos <= Length(OpStr)) And
                                  (OpStr[SemiPos] <> ';') Do
                                Inc(SemiPos);
                            FieldStr := Copy(OpStr, P, SemiPos - P);
                            P := SemiPos + 1;

                            EqPos := Pos('=', FieldStr);
                            If EqPos > 0 Then
                            Begin
                                Key := Copy(FieldStr, 1, EqPos - 1);
                                Val := Copy(FieldStr, EqPos + 1,
                                    Length(FieldStr) - EqPos);

                                If (Key <> '') And (Key <> 'designator') And
                                   (Val <> '') Then
                                Begin
                                    If Key = 'Value' Then
                                    Begin
                                        SchBeginModify(Comp.Comment);
                                        Try Comp.Comment.Text := Val; Except NoteWriteFailed('Comment.Text'); End;
                                        SchEndModify(Comp.Comment);
                                        SetOwnCompParamText(Comp, 'Value', Val);
                                    End
                                    Else If (Key = 'ComponentDescription') Or
                                        (Key = 'Description') Then
                                    Begin
                                        SchBeginModify(Comp);
                                        Try Comp.ComponentDescription := Val; Except NoteWriteFailed('ComponentDescription'); End;
                                        SchEndModify(Comp);
                                    End
                                    Else If Key = 'Footprint' Then
                                    Begin
                                        { Was skipped silently while the tool
                                          listed Footprint as a key it takes. }
                                        If SetPlacedFootprintName(Comp, Val,
                                            FpBefore, FpAfter) Then
                                            Inc(FpApplied)
                                        Else
                                        Begin
                                            If FpMissed <> '' Then
                                                FpMissed := FpMissed + ',';
                                            FpMissed := FpMissed + '"'
                                                + EscapeJsonString(Comp.Designator.Text)
                                                + '"';
                                        End;
                                    End
                                    Else
                                        SetCompParamText(Comp, Key, Val);
                                End;
                            End;
                        End;
                        Inc(Updated);
                    End;
                    Obj := Iter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iter);
            End;
        Finally
            SchEndEdit(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
        End;

        Failed := OpCount - Updated;
    Finally
        DesigList.Free;
        OpsList.Free;
    End;

    { Through the one helper, so the edit is recorded as well as flagged: }
    { a bare SetModified left the save reporting nothing to write.        }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"updated":' + IntToStr(Updated) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) +
        ',"footprints_applied":' + IntToStr(FpApplied) +
        ',"footprints_not_applied":[' + FpMissed + ']}');
End;

{..............................................................................}
{ Gen_SetSchTextPositions - move Designator (and optionally Comment) text of   }
{ placed components to explicit sheet coordinates. The offline text-placement  }
{ pass picks a collision-free side per part; this mirrors those anchors onto   }
{ the live sheet so it matches the offline render. Coordinates are mils,       }
{ absolute sheet frame (the same frame place_sch_components uses).             }
{ Params: positions = 'designator=R1;dx=..;dy=..;vx=..;vy=..~~...'              }
{         (vx/vy optional; omitted = leave Comment where the library put it),  }
{         sheet_path (optional; falls back to the focused document).           }
{..............................................................................}

Function Gen_SetSchTextPositions(Params : String; RequestId : String) : String;
Var
    PosStr, SheetPath, Op, Remaining, FieldStr : String;
    OpCount, Updated, Failed, OpIdx : Integer;
    DX, DY, VX, VY : Double;
    HasV : Boolean;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    DesigList, OpsList : TStringList;
    Loc : TLocation;
    SrvDoc : IServerDocument;
Begin
    PosStr := ExtractJsonValue(Params, 'positions');
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    If PosStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'positions required');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    DesigList := TStringList.Create;
    OpsList := TStringList.Create;
    Try
        Remaining := PosStr;
        OpCount := 0;
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            FieldStr := GetBatchField(Op, 'designator');
            If FieldStr <> '' Then
            Begin
                DesigList.Add(FieldStr);
                OpsList.Add(Op);
            End;
        End;

        Updated := 0;
        SchBeginEdit(SchDoc, '');
        Try
            Iter := SchDoc.SchIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iter.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Comp := Obj;
                    OpIdx := DesigList.IndexOf(Comp.Designator.Text);
                    If OpIdx >= 0 Then
                    Begin
                        Op := OpsList[OpIdx];
                        DX := StrToFloatDef(GetBatchField(Op, 'dx'), 0);
                        DY := StrToFloatDef(GetBatchField(Op, 'dy'), 0);
                        HasV := (GetBatchField(Op, 'vx') <> '')
                            And (GetBatchField(Op, 'vy') <> '');
                        VX := StrToFloatDef(GetBatchField(Op, 'vx'), 0);
                        VY := StrToFloatDef(GetBatchField(Op, 'vy'), 0);

                        { Record-field write needs a materialized local. }
                        SchBeginModify(Comp.Designator);
                        Try
                            Loc := Comp.Designator.Location;
                            Loc.X := MilsToCoordF(DX);
                            Loc.Y := MilsToCoordF(DY);
                            Comp.Designator.Location := Loc;
                            Comp.Designator.Autoposition := False;
                        Except NoteWriteFailed('Designator position'); End;
                        SchEndModify(Comp.Designator);

                        If HasV Then
                        Begin
                            SchBeginModify(Comp.Comment);
                            Try
                                Loc := Comp.Comment.Location;
                                Loc.X := MilsToCoordF(VX);
                                Loc.Y := MilsToCoordF(VY);
                                Comp.Comment.Location := Loc;
                                Comp.Comment.Autoposition := False;
                            Except NoteWriteFailed('Comment position'); End;
                            SchEndModify(Comp.Comment);
                        End;
                        Inc(Updated);
                    End;
                    Obj := Iter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iter);
            End;
        Finally
            SchEndEdit(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
        End;
        Failed := OpCount - Updated;
    Finally
        DesigList.Free;
        OpsList.Free;
    End;

    { Through the one helper, so the edit is recorded as well as flagged: }
    { a bare SetModified left the save reporting nothing to write.        }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"updated":' + IntToStr(Updated) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) + '}');
End;

{..............................................................................}
{ Gen_AttachSpicePrimitivesBatch - Attach SPICE primitives to many components   }
{ in one go. Each op: designator, primitive, value, spice_model (optional),    }
{ sim_kind (optional).                                                          }
{..............................................................................}

Function Gen_AttachSpicePrimitivesBatch(Params : String; RequestId : String) : String;
Var
    AttachStr, Op, Remaining : String;
    OpCount, Attached, Failed : Integer;
    Designator, Primitive, Value, ModelName, SimKind : String;
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    Found : Boolean;
    FailuresJson, ItemReason : String;
    FirstFailure : Boolean;
Begin
    AttachStr := ExtractJsonValue(Params, 'attachments');
    If AttachStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'attachments is required');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Attached := 0;
    Failed := 0;
    OpCount := 0;
    Remaining := AttachStr;
    FailuresJson := '';
    FirstFailure := True;

    SchBeginEdit(SchDoc, 'Attach SPICE primitives');
    Try
        While True Do
        Begin
            Op := NextBatchOp(Remaining);
            If Op = '' Then Break;
            OpCount := OpCount + 1;
            Designator := GetBatchField(Op, 'designator');
            Primitive := UpperCase(GetBatchField(Op, 'primitive'));
            Value := GetBatchField(Op, 'value');
            ModelName := GetBatchField(Op, 'spice_model');
            SimKind := GetBatchField(Op, 'sim_kind');

            ItemReason := '';

            If (Designator = '') Or (Primitive = '') Then
            Begin
                Inc(Failed);
                ItemReason := 'MISSING_FIELDS';
            End
            Else If SchComponentCount(SchDoc, Designator) > 1 Then
            Begin
                { One ITEM is refused, not the whole batch: the others are
                  unambiguous and there is no reason to lose them. A
                  multi-part device shares its designator across sub-parts,
                  so attaching to the first would pick one arbitrarily. }
                Inc(Failed);
                ItemReason := 'AMBIGUOUS_DESIGNATOR';
            End
            Else
            Begin
                Found := False;
                Iter := SchDoc.SchIterator_Create;
                Try
                    Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                    Obj := Iter.FirstSchObject;
                    While (Obj <> Nil) And Not Found Do
                    Begin
                        Comp := Obj;
                        If Comp.Designator.Text = Designator Then
                        Begin
                            SetCompParamText(Comp, 'SpicePrefix', Primitive);
                            If Value <> '' Then
                                SetCompParamText(Comp, 'Value', Value);
                            If ModelName <> '' Then
                                SetCompParamText(Comp, 'SpiceModel', ModelName);
                            If SimKind <> '' Then
                                SetCompParamText(Comp, 'SimulationKind', SimKind);
                            Found := True;
                            Inc(Attached);
                        End;
                        Obj := Iter.NextSchObject;
                    End;
                Finally
                    SchDoc.SchIterator_Destroy(Iter);
                End;
                If Not Found Then
                Begin
                    Inc(Failed);
                    ItemReason := 'COMPONENT_NOT_FOUND';
                End;
            End;

            If ItemReason <> '' Then
            Begin
                If Not FirstFailure Then FailuresJson := FailuresJson + ',';
                FirstFailure := False;
                FailuresJson := FailuresJson +
                    '{"index":' + IntToStr(OpCount - 1) +
                    ',"designator":"' + EscapeJsonString(Designator) +
                    '","reason":"' + ItemReason + '"}';
            End;
        End;
    Finally
        SchEndEdit(SchDoc, 'Attach SPICE primitives');
        SchDoc.GraphicallyInvalidate;
    End;

    { A parameter write dirties the document too: SmartCompile skips its
      recompile while the project looks clean, so a later netlist or ERC
      read answers from the model as it stood before this call. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"attached":' + IntToStr(Attached) +
        ',"failed":' + IntToStr(Failed) +
        ',"total":' + IntToStr(OpCount) +
        ',"failures":[' + FailuresJson + ']}');
End;

{..............................................................................}
{ Gen_CrossRefNet - Compare the schematic vs PCB membership of a named net.   }
{                                                                               }
{ Reports the pin list the compiled SCHEMATIC assigns to `net_name` alongside  }
{ the pad list the PCB assigns to the same net, plus the diff in each         }
{ direction. An in_sync=false result means either the design hasn't been     }
{ ECO'd (Design -> Update PCB from Schematic) OR the PCB was fabricated from }
{ an earlier schematic revision and a later edit broke the merge.             }
{                                                                               }
{ This is the go-to tool when schematic connectivity surprises the user.      }
{ Params: net_name.                                                            }
{..............................................................................}

Function Gen_CrossRefNet(Params : String; RequestId : String) : String;
Var
    NetName : String;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Comp : IComponent;
    Pin : IPin;
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    { Must be typed as IPCB_Pad, the base IPCB_Primitive doesn't expose   }
    { .Net, .Component, or .Name, and accesses fail silently under the    }
    { surrounding Try/Except, which is why every net returned 0 pads.     }
    Pad : IPCB_Pad;
    I, J, K, N, DocCount : Integer;
    UsePhysical : Boolean;
    { Diagnostic counters, emitted in the response so we can tell "PCB   }
    { is open but iterator returned nothing" from "iterator returned pads }
    { but every one had Pad.Net = nil" from "iterator ran fine and we    }
    { just got no name match".                                            }
    DiagBoardNil, DiagIterVisited, DiagPadNetNil, DiagNameRaise : Integer;
    NetReadOk : Boolean;
    { Heap-allocated lists - fixed-size `Array[0..N] Of String` as a       }
    { function local silently returns Params as the response in            }
    { DelphiScript, see [[delphiscript_fixed_string_array_bug]].           }
    SchList, PCBList : TStringList;
    SchJson, PCBJson, SchOnlyJson, PCBOnlyJson, Key : String;
    FirstS, FirstP, FirstSO, FirstPO, InPCB, InSch, InSync : Boolean;
    SchOnlyCount, PCBOnlyCount, MatchCount : Integer;
    EnvelopeData, ResponseStr : String;
Begin
    NetName := ExtractJsonValue(Params, 'net_name');
    If NetName = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS', 'net_name is required');
        Exit;
    End;

    DiagBoardNil := 0;
    DiagIterVisited := 0;
    DiagPadNetNil := 0;
    DiagNameRaise := 0;

    SchList := TStringList.Create;
    PCBList := TStringList.Create;
    Try

    { --- Schematic side: compile project, walk every component's pins,     }
    { collect "designator.pin_number" for every pin whose flattened net    }
    { name matches.                                                         }
    Workspace := GetWorkspace;
    If Workspace <> Nil Then
    Begin
        Project := Workspace.DM_FocusedProject;
        If Project <> Nil Then
        Begin
            SmartCompile(Project);
            GetCompiledDocs(Project, DocCount, UsePhysical);
            For I := 0 To DocCount - 1 Do
            Begin
                Doc := GetCompiledDoc(Project, I, UsePhysical);
                If Doc = Nil Then Continue;
                For J := 0 To Doc.DM_ComponentCount - 1 Do
                Begin
                    Comp := Doc.DM_Components(J);
                    If Comp = Nil Then Continue;
                    For K := 0 To Comp.DM_PinCount - 1 Do
                    Begin
                        Pin := Comp.DM_Pins(K);
                        If Pin = Nil Then Continue;
                        Try
                            If Pin.DM_FlattenedNetName = NetName Then
                                SchList.Add(Comp.DM_PhysicalDesignator + '.' + Pin.DM_PinNumber);
                        Except End;
                    End;
                End;
            End;
        End;
    End;

    { --- PCB side: iterate every pad on the board, collect the ones whose }
    { .Net matches. Each property access is in its own Try/Except and     }
    { we use nested If (not boolean And) so a nil/raising step never      }
    { drops an otherwise-valid pad, that was the bug that made pcb_pin_ }
    { count come back 0 even though pcb_get_component_pads found them.    }
    { GetCurrentPCBBoard only returns a board when a PCB tab has focus.   }
    { crossref_net is typically called while the USER is looking at a    }
    { schematic, so focus-based lookup silently returns nil, that's the }
    { source of the original pcb_pin_count:0 bug. Fall back to iterating }
    { the project's documents, find the first .PcbDoc, resolve it via   }
    { PCBServer.GetPCBBoardByPath which doesn't care about focus.         }
    { GetPCBBoardAnywhere already walks the project's PCB docs internally,    }
    { its result is the single source of truth. Older code duplicated the     }
    { iteration with a direct PCBServer.GetPCBBoardByPath call - that symbol  }
    { is undeclared on some Altium builds and Try/Except cannot catch         }
    { undeclared identifiers (see [[delphiscript_api_quirks]]), so the inline }
    { fallback would crash the script instead of just returning Nil.          }
    Board := GetPCBBoardAnywhere(0);

    If Board = Nil Then
        DiagBoardNil := 1
    Else
    Begin
        Iter := Board.BoardIterator_Create;
        Try
            Iter.AddFilter_ObjectSet(MkSet(ePadObject));
            Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iter.AddFilter_Method(eProcessAll);
            Pad := Iter.FirstPCBObject;
            While Pad <> Nil Do
            Begin
                DiagIterVisited := DiagIterVisited + 1;
                NetReadOk := False;
                Key := '';
                Try
                    If Pad.Net = Nil Then
                        DiagPadNetNil := DiagPadNetNil + 1
                    Else
                    Begin
                        NetReadOk := True;
                        If Pad.Net.Name = NetName Then
                            Key := '__MATCHED__';
                    End;
                Except
                    DiagNameRaise := DiagNameRaise + 1;
                End;

                If Key = '__MATCHED__' Then
                Begin
                    Key := '';
                    Try
                        If Pad.Component <> Nil Then
                            Key := Pad.Component.Name.Text + '.';
                    Except End;
                    If Key = '' Then Key := '?.';
                    Try Key := Key + Pad.Name; Except End;
                    PCBList.Add(Key);
                End;

                Pad := Iter.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iter);
        End;
    End;

    { Build JSON arrays + diff (sch_only = in sch but not pcb, etc.). O(N*M) }
    { is fine at our scale (nets with 1000+ pins are rare). PCBList.IndexOf  }
    { gives us the set membership check without a manual loop.               }
    SchJson := '';
    FirstS := True;
    For I := 0 To SchList.Count - 1 Do
    Begin
        If Not FirstS Then SchJson := SchJson + ',';
        FirstS := False;
        SchJson := SchJson + '"' + EscapeJsonString(SchList[I]) + '"';
    End;

    PCBJson := '';
    FirstP := True;
    For I := 0 To PCBList.Count - 1 Do
    Begin
        If Not FirstP Then PCBJson := PCBJson + ',';
        FirstP := False;
        PCBJson := PCBJson + '"' + EscapeJsonString(PCBList[I]) + '"';
    End;

    SchOnlyJson := '';
    FirstSO := True;
    SchOnlyCount := 0;
    For I := 0 To SchList.Count - 1 Do
    Begin
        InPCB := PCBList.IndexOf(SchList[I]) >= 0;
        If Not InPCB Then
        Begin
            If Not FirstSO Then SchOnlyJson := SchOnlyJson + ',';
            FirstSO := False;
            SchOnlyJson := SchOnlyJson + '"' + EscapeJsonString(SchList[I]) + '"';
            SchOnlyCount := SchOnlyCount + 1;
        End;
    End;

    PCBOnlyJson := '';
    FirstPO := True;
    PCBOnlyCount := 0;
    For I := 0 To PCBList.Count - 1 Do
    Begin
        InSch := SchList.IndexOf(PCBList[I]) >= 0;
        If Not InSch Then
        Begin
            If Not FirstPO Then PCBOnlyJson := PCBOnlyJson + ',';
            FirstPO := False;
            PCBOnlyJson := PCBOnlyJson + '"' + EscapeJsonString(PCBList[I]) + '"';
            PCBOnlyCount := PCBOnlyCount + 1;
        End;
    End;

    MatchCount := SchList.Count - SchOnlyCount;
    InSync := (SchOnlyCount = 0) And (PCBOnlyCount = 0) And
              ((SchList.Count > 0) Or (PCBList.Count > 0));

    EnvelopeData := '{"net_name":"' + EscapeJsonString(NetName) + '",'
        + '"sch_pin_count":' + IntToStr(SchList.Count) + ','
        + '"pcb_pin_count":' + IntToStr(PCBList.Count) + ','
        + '"matched":' + IntToStr(MatchCount) + ','
        + '"sch_only_count":' + IntToStr(SchOnlyCount) + ','
        + '"pcb_only_count":' + IntToStr(PCBOnlyCount) + ','
        + '"in_sync":' + BoolToJsonStr(InSync) + ','
        + '"sch_pins":[' + SchJson + '],'
        + '"pcb_pins":[' + PCBJson + '],'
        + '"sch_only":[' + SchOnlyJson + '],'
        + '"pcb_only":[' + PCBOnlyJson + '],'
        + '"_diag":{'
        + '"board_nil":' + IntToStr(DiagBoardNil) + ','
        + '"iter_visited":' + IntToStr(DiagIterVisited) + ','
        + '"pad_net_nil":' + IntToStr(DiagPadNetNil) + ','
        + '"name_read_raised":' + IntToStr(DiagNameRaise)
        + '}}';

    ResponseStr := BuildSuccessResponse(RequestId, EnvelopeData);
    Result := ResponseStr;
    Finally
        PCBList.Free;
        SchList.Free;
    End;
End;

{ Gen_GetSchGeometry - Walk the active SchDoc and emit every primitive's     }
{ geometry as JSON, so a Python-side renderer can produce SVG independently  }
{ of any third-party Altium parser. v1 surface:                              }
{   - components: world position, orientation, designator, lib_ref, bbox    }
{   - pins: world position, electrical-end / body direction, length         }
{   - wires: vertex polylines                                                }
{   - junctions, net labels, ports (with IOType + width), power ports       }
{                                                                              }
{ Coordinates are reported in mils. Symbol-internal primitives (rects,      }
{ lines, arcs inside each symbol) are deferred to v2 - this v1 gives the    }
{ renderer enough to draw recognizable boxed components with labelled pin   }
{ stubs, wires, junctions, ports, and labels.                                }
Function Gen_GetSchGeometry(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter, PinIter, PrimIter, ParamIter, EntryIter : ISch_Iterator;
    Obj, Prim : ISch_GraphicalObject;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    Wire : ISch_Wire;
    NetLbl : ISch_NetLabel;
    Port : ISch_Port;
    Power : ISch_PowerObject;
    Junct : ISch_Junction;
    Rect : ISch_Rectangle;
    RoundRect : ISch_RoundRectangle;
    Line : ISch_Line;
    Arc : ISch_Arc;
    EllipArc : ISch_EllipticalArc;
    Poly : ISch_Polyline;
    Ellipse : ISch_Ellipse;
    Bezier : ISch_Bezier;
    ParamObj : ISch_Parameter;
    SheetSym : ISch_SheetSymbol;
    SheetEntry : ISch_SheetEntry;
    Bus : ISch_Bus;
    CompsJson, PinsJson, WiresJson, LabelsJson, PortsJson, PowerJson, JunctsJson : String;
    SheetSymsJson, BusesJson, EntriesJson : String;
    NumComps, NumWires, NumLabels, NumPorts, NumPower, NumJuncts, NumPins : Integer;
    NumSheetSyms, NumBuses, NumEntries : Integer;
    PrimJson, PrimPart, CompHeader, ParamsJson : String;
    NumPrim, NumParams : Integer;
    Loc : TLocation;
    BBox : TCoordRect;
    HasBBox : Boolean;
    VtxN, V : Integer;
    Vert : TLocation;
    DesigText, LibRef, ElecStr, ParamName, LogicalDes : String;
    SheetName, SheetFile, SchDocName : String;
    RespJson : String;
    PhysMap : TStringList;
    Workspace : IWorkspace;
    Project : IProject;
    DmDoc : IDocument;
    DmComp : IComponent;
    DmLogical, DmPhysical : String;
    DI, DJ, MapIdx : Integer;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC',
            'No schematic document is active');
        Exit;
    End;
    SchDocName := '';
    Try SchDocName := SchDoc.DocumentName; Except End;

    { Build a logical -> physical designator map from the DM enumeration. }
    { ISch_Component.Designator.Text returns the LOGICAL designator (the }
    { sub-sheet template name like "R1"). For multi-channel designs the  }
    { board-level annotated name (R101, R201, ...) lives on the DM-side  }
    { IComponent.DM_PhysicalDesignator. Single-channel designs map 1:1   }
    { so the lookup is a no-op for them.                                  }
    PhysMap := TStringList.Create;
    Try
        Try
            Workspace := GetWorkspace;
            If Workspace <> Nil Then Project := Workspace.DM_FocusedProject;
        Except End;
        If Project <> Nil Then
        Begin
            Try
                For DI := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    Try
                        DmDoc := Project.DM_LogicalDocuments(DI);
                        If DmDoc = Nil Then Continue;
                        { Match by file name -- the DM doc and the open sch doc both }
                        { resolve to the same .SchDoc file. Only walk components of  }
                        { the doc that matches the active SchDoc so we don't pull in }
                        { other sheets' designators (multi-channel sheets share a   }
                        { logical doc but have distinct physical designators per     }
                        { channel; we want THIS sheet's mapping).                    }
                        If (DmDoc.DM_FullPath <> SchDocName)
                           And (DmDoc.DM_FileName <> SchDocName) Then Continue;
                        For DJ := 0 To DmDoc.DM_ComponentCount - 1 Do
                        Begin
                            Try
                                DmComp := DmDoc.DM_Components(DJ);
                                If DmComp = Nil Then Continue;
                                DmLogical := '';
                                DmPhysical := '';
                                Try DmLogical := DmComp.DM_LogicalDesignator; Except End;
                                Try DmPhysical := DmComp.DM_PhysicalDesignator; Except End;
                                If (DmLogical <> '') And (DmPhysical <> '')
                                   And (DmLogical <> DmPhysical) Then
                                    PhysMap.Add(DmLogical + '|' + DmPhysical);
                            Except End;
                        End;
                    Except End;
                End;
            Except End;
        End;

    CompsJson := '[';      NumComps := 0;
    PinsJson := '[';       NumPins := 0;
    WiresJson := '[';      NumWires := 0;
    LabelsJson := '[';     NumLabels := 0;
    PortsJson := '[';      NumPorts := 0;
    PowerJson := '[';      NumPower := 0;
    JunctsJson := '[';     NumJuncts := 0;
    SheetSymsJson := '['; NumSheetSyms := 0;
    BusesJson := '[';     NumBuses := 0;

    Iter := SchDoc.SchIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eSchComponent, eWire, eNetLabel,
        ePort, ePowerObject, eJunction, eSheetSymbol, eBus));
    Try
        Obj := Iter.FirstSchObject;
        While Obj <> Nil Do
        Begin
            If Obj.ObjectId = eSchComponent Then
            Begin
                Comp := Obj;
                Loc := Comp.Location;
                { Logical designator (the sub-sheet template name like  }
                { "R1" for a multi-channel sheet) then look up the      }
                { physical / board-level name from the map we built     }
                { earlier. Single-channel designs map 1:1 so the lookup }
                { miss falls back to the logical value -- net result    }
                { matches what the BOM endpoint returns.                 }
                LogicalDes := '';
                Try LogicalDes := Comp.Designator.Text; Except End;
                DesigText := LogicalDes;
                If LogicalDes <> '' Then
                Begin
                    For MapIdx := 0 To PhysMap.Count - 1 Do
                    Begin
                        DmLogical := PhysMap[MapIdx];
                        If Copy(DmLogical, 1, Length(LogicalDes) + 1)
                           = LogicalDes + '|' Then
                        Begin
                            DesigText := Copy(DmLogical,
                                Length(LogicalDes) + 2,
                                Length(DmLogical) - Length(LogicalDes) - 1);
                            Break;
                        End;
                    End;
                End;
                LibRef := '';
                Try LibRef := Comp.LibReference; Except End;
                HasBBox := False;
                Try
                    BBox := Comp.BoundingRectangle;
                    HasBBox := True;
                Except
                End;
                If Not HasBBox Then
                Begin
                    { Fallback: a small box at the component origin so the    }
                    { renderer still has something to draw if the API throws. }
                    BBox.X1 := Loc.X - MilsToCoord(100);
                    BBox.Y1 := Loc.Y - MilsToCoord(100);
                    BBox.X2 := Loc.X + MilsToCoord(100);
                    BBox.Y2 := Loc.Y + MilsToCoord(100);
                End;
                { Component header without primitives. The full record gets   }
                { written after the primitives are collected, since v2 nests  }
                { the symbol art inside the component object.                  }
                CompHeader :=
                    '{"des":"' + EscapeJsonString(DesigText) + '"' +
                    ',"lib_ref":"' + EscapeJsonString(LibRef) + '"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(Loc.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Loc.Y)) +
                    ',"rot":' + IntToStr(Comp.Orientation * 90) +
                    ',"mirror":' + BoolToJsonStr(Comp.IsMirrored) +
                    ',"bbox":{"x1":' + FloatToJsonStr(CoordToMilsF(BBox.X1)) +
                    ',"y1":' + FloatToJsonStr(CoordToMilsF(BBox.Y1)) +
                    ',"x2":' + FloatToJsonStr(CoordToMilsF(BBox.X2)) +
                    ',"y2":' + FloatToJsonStr(CoordToMilsF(BBox.Y2)) + '}';

                { Symbol-internal primitives -- iterate the component's own   }
                { children for rect / line / arc / polyline / polygon /       }
                { ellipse so the renderer draws actual symbol art rather than }
                { a labelled bounding box. Colors come back as Altium's       }
                { BGR-packed integers; the Python side maps them to SVG.      }
                PrimJson := '[';
                NumPrim := 0;
                PrimIter := Comp.SchIterator_Create;
                PrimIter.AddFilter_ObjectSet(MkSet(eRectangle, eRoundRectangle,
                    eLine, eArc, eEllipticalArc, ePolyline, ePolygon, eEllipse,
                    eBezier));
                Try
                    Prim := PrimIter.FirstSchObject;
                    While Prim <> Nil Do
                    Begin
                        PrimPart := '';
                        If Prim.ObjectId = eRectangle Then
                        Begin
                            Rect := Prim;
                            PrimPart := '{"kind":"rect"'
                                + ',"x1":' + FloatToJsonStr(CoordToMilsF(Rect.Location.X))
                                + ',"y1":' + FloatToJsonStr(CoordToMilsF(Rect.Location.Y))
                                + ',"x2":' + FloatToJsonStr(CoordToMilsF(Rect.Corner.X))
                                + ',"y2":' + FloatToJsonStr(CoordToMilsF(Rect.Corner.Y))
                                + ',"color":' + IntToStr(Rect.Color)
                                + ',"line_width":' + IntToStr(Rect.LineWidth)
                                + ',"area_color":' + IntToStr(Rect.AreaColor)
                                + ',"is_solid":' + BoolToJsonStr(Rect.IsSolid)
                                + '}';
                        End
                        Else If Prim.ObjectId = eRoundRectangle Then
                        Begin
                            RoundRect := Prim;
                            PrimPart := '{"kind":"roundrect"'
                                + ',"x1":' + FloatToJsonStr(CoordToMilsF(RoundRect.Location.X))
                                + ',"y1":' + FloatToJsonStr(CoordToMilsF(RoundRect.Location.Y))
                                + ',"x2":' + FloatToJsonStr(CoordToMilsF(RoundRect.Corner.X))
                                + ',"y2":' + FloatToJsonStr(CoordToMilsF(RoundRect.Corner.Y))
                                + ',"rx":' + FloatToJsonStr(CoordToMilsF(RoundRect.CornerXRadius))
                                + ',"ry":' + FloatToJsonStr(CoordToMilsF(RoundRect.CornerYRadius))
                                + ',"color":' + IntToStr(RoundRect.Color)
                                + ',"line_width":' + IntToStr(RoundRect.LineWidth)
                                + ',"area_color":' + IntToStr(RoundRect.AreaColor)
                                + ',"is_solid":' + BoolToJsonStr(RoundRect.IsSolid)
                                + '}';
                        End
                        Else If Prim.ObjectId = eLine Then
                        Begin
                            Line := Prim;
                            PrimPart := '{"kind":"line"'
                                + ',"x1":' + FloatToJsonStr(CoordToMilsF(Line.Location.X))
                                + ',"y1":' + FloatToJsonStr(CoordToMilsF(Line.Location.Y))
                                + ',"x2":' + FloatToJsonStr(CoordToMilsF(Line.Corner.X))
                                + ',"y2":' + FloatToJsonStr(CoordToMilsF(Line.Corner.Y))
                                + ',"color":' + IntToStr(Line.Color)
                                + ',"line_width":' + IntToStr(Line.LineWidth)
                                + '}';
                        End
                        Else If Prim.ObjectId = eArc Then
                        Begin
                            Arc := Prim;
                            PrimPart := '{"kind":"arc"'
                                + ',"cx":' + FloatToJsonStr(CoordToMilsF(Arc.Location.X))
                                + ',"cy":' + FloatToJsonStr(CoordToMilsF(Arc.Location.Y))
                                + ',"r":' + FloatToJsonStr(CoordToMilsF(Arc.Radius))
                                + ',"start":' + FloatToJsonStr(Arc.StartAngle)
                                + ',"end":' + FloatToJsonStr(Arc.EndAngle)
                                + ',"color":' + IntToStr(Arc.Color)
                                + ',"line_width":' + IntToStr(Arc.LineWidth)
                                + '}';
                        End
                        Else If Prim.ObjectId = eEllipticalArc Then
                        Begin
                            EllipArc := Prim;
                            PrimPart := '{"kind":"arc"'
                                + ',"cx":' + FloatToJsonStr(CoordToMilsF(EllipArc.Location.X))
                                + ',"cy":' + FloatToJsonStr(CoordToMilsF(EllipArc.Location.Y))
                                + ',"r":' + FloatToJsonStr(CoordToMilsF(EllipArc.Radius))
                                + ',"r2":' + FloatToJsonStr(CoordToMilsF(EllipArc.SecondaryRadius))
                                + ',"start":' + FloatToJsonStr(EllipArc.StartAngle)
                                + ',"end":' + FloatToJsonStr(EllipArc.EndAngle)
                                + ',"color":' + IntToStr(EllipArc.Color)
                                + ',"line_width":' + IntToStr(EllipArc.LineWidth)
                                + '}';
                        End
                        Else If (Prim.ObjectId = ePolyline) Or (Prim.ObjectId = ePolygon) Then
                        Begin
                            Poly := Prim;
                            VtxN := 0;
                            Try VtxN := Poly.GetState_VerticesCount; Except End;
                            If VtxN >= 2 Then
                            Begin
                                If Prim.ObjectId = ePolyline Then
                                    PrimPart := '{"kind":"polyline","pts":['
                                Else
                                    PrimPart := '{"kind":"polygon","pts":[';
                                For V := 1 To VtxN Do
                                Begin
                                    Vert := Poly.GetState_Vertex(V);
                                    If V > 1 Then PrimPart := PrimPart + ',';
                                    PrimPart := PrimPart + '['
                                        + FloatToJsonStr(CoordToMilsF(Vert.X)) + ','
                                        + FloatToJsonStr(CoordToMilsF(Vert.Y)) + ']';
                                End;
                                PrimPart := PrimPart + ']'
                                    + ',"color":' + IntToStr(Poly.Color)
                                    + ',"line_width":' + IntToStr(Poly.LineWidth)
                                    + ',"area_color":' + IntToStr(Poly.AreaColor)
                                    + ',"is_solid":' + BoolToJsonStr(Poly.IsSolid)
                                    + '}';
                            End;
                        End
                        Else If Prim.ObjectId = eEllipse Then
                        Begin
                            Ellipse := Prim;
                            PrimPart := '{"kind":"ellipse"'
                                + ',"cx":' + FloatToJsonStr(CoordToMilsF(Ellipse.Location.X))
                                + ',"cy":' + FloatToJsonStr(CoordToMilsF(Ellipse.Location.Y))
                                + ',"rx":' + FloatToJsonStr(CoordToMilsF(Ellipse.Radius))
                                + ',"ry":' + FloatToJsonStr(CoordToMilsF(Ellipse.SecondaryRadius))
                                + ',"color":' + IntToStr(Ellipse.Color)
                                + ',"line_width":' + IntToStr(Ellipse.LineWidth)
                                + ',"area_color":' + IntToStr(Ellipse.AreaColor)
                                + ',"is_solid":' + BoolToJsonStr(Ellipse.IsSolid)
                                + '}';
                        End
                        Else If Prim.ObjectId = eBezier Then
                        Begin
                            Bezier := Prim;
                            VtxN := 0;
                            Try VtxN := Bezier.GetState_VerticesCount; Except End;
                            If VtxN >= 4 Then
                            Begin
                                PrimPart := '{"kind":"bezier","pts":[';
                                For V := 1 To VtxN Do
                                Begin
                                    Vert := Bezier.GetState_Vertex(V);
                                    If V > 1 Then PrimPart := PrimPart + ',';
                                    PrimPart := PrimPart + '['
                                        + FloatToJsonStr(CoordToMilsF(Vert.X)) + ','
                                        + FloatToJsonStr(CoordToMilsF(Vert.Y)) + ']';
                                End;
                                PrimPart := PrimPart + ']'
                                    + ',"color":' + IntToStr(Bezier.Color)
                                    + ',"line_width":' + IntToStr(Bezier.LineWidth)
                                    + '}';
                            End;
                        End;
                        If PrimPart <> '' Then
                        Begin
                            If NumPrim > 0 Then PrimJson := PrimJson + ',';
                            PrimJson := PrimJson + PrimPart;
                            Inc(NumPrim);
                        End;
                        Prim := PrimIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PrimIter);
                End;
                PrimJson := PrimJson + ']';

                { Symbol-internal parameter text -- the visible labels   }
                { living inside the symbol (other than the special       }
                { Designator / Comment, which the top-level handles).    }
                { Hidden parameters and the two specials are skipped.    }
                ParamsJson := '[';
                NumParams := 0;
                ParamIter := Comp.SchIterator_Create;
                ParamIter.AddFilter_ObjectSet(MkSet(eParameter));
                Try
                    ParamObj := ParamIter.FirstSchObject;
                    While ParamObj <> Nil Do
                    Begin
                        ParamName := '';
                        Try ParamName := ParamObj.Name; Except End;
                        If (Not ParamObj.IsHidden)
                                And (ParamName <> 'Designator')
                                And (ParamName <> 'Comment') Then
                        Begin
                            If NumParams > 0 Then ParamsJson := ParamsJson + ',';
                            ParamsJson := ParamsJson +
                                '{"name":"' + EscapeJsonString(ParamName) + '"' +
                                ',"text":"' + EscapeJsonString(ParamObj.Text) + '"' +
                                ',"x":' + FloatToJsonStr(CoordToMilsF(ParamObj.Location.X)) +
                                ',"y":' + FloatToJsonStr(CoordToMilsF(ParamObj.Location.Y)) +
                                ',"rot":' + IntToStr(ParamObj.Orientation * 90) +
                                ',"color":' + IntToStr(ParamObj.Color) +
                                ',"font_id":' + IntToStr(ParamObj.FontId) + '}';
                            Inc(NumParams);
                        End;
                        ParamObj := ParamIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(ParamIter);
                End;
                ParamsJson := ParamsJson + ']';

                If NumComps > 0 Then CompsJson := CompsJson + ',';
                CompsJson := CompsJson + CompHeader
                    + ',"primitives":' + PrimJson
                    + ',"params":' + ParamsJson + '}';
                Inc(NumComps);

                PinIter := Comp.SchIterator_Create;
                PinIter.AddFilter_ObjectSet(MkSet(ePin));
                Try
                    Pin := PinIter.FirstSchObject;
                    While Pin <> Nil Do
                    Begin
                      If PinShownOn(Comp, Pin) Then
                      Begin
                        Loc := Pin.Location;
                        { Electrical type as a string so the renderer can map }
                        { straight to a glyph (input arrow / OC bubble / ...). }
                        ElecStr := 'passive';
                        Try ElecStr := PinElectricalToStr(Pin.Electrical); Except End;
                        If NumPins > 0 Then PinsJson := PinsJson + ',';
                        PinsJson := PinsJson +
                            '{"comp":"' + EscapeJsonString(DesigText) + '"' +
                            ',"des":"' + EscapeJsonString(Pin.Designator) + '"' +
                            ',"name":"' + EscapeJsonString(Pin.Name) + '"' +
                            ',"x":' + FloatToJsonStr(CoordToMilsF(Loc.X)) +
                            ',"y":' + FloatToJsonStr(CoordToMilsF(Loc.Y)) +
                            ',"rot":' + IntToStr(Pin.Orientation * 90) +
                            ',"len":' + FloatToJsonStr(CoordToMilsF(Pin.PinLength)) +
                            ',"electrical":"' + EscapeJsonString(ElecStr) + '"}';
                        Inc(NumPins);
                      End;
                        Pin := PinIter.NextSchObject;
                    End;
                Finally
                    Comp.SchIterator_Destroy(PinIter);
                End;
            End
            Else If Obj.ObjectId = eWire Then
            Begin
                Wire := Obj;
                VtxN := 0;
                Try VtxN := Wire.GetState_VerticesCount; Except End;
                If VtxN >= 2 Then
                Begin
                    If NumWires > 0 Then WiresJson := WiresJson + ',';
                    WiresJson := WiresJson + '{"verts":[';
                    For V := 1 To VtxN Do
                    Begin
                        Vert := Wire.GetState_Vertex(V);
                        If V > 1 Then WiresJson := WiresJson + ',';
                        WiresJson := WiresJson + '['
                            + FloatToJsonStr(CoordToMilsF(Vert.X)) + ','
                            + FloatToJsonStr(CoordToMilsF(Vert.Y)) + ']';
                    End;
                    WiresJson := WiresJson + ']}';
                    Inc(NumWires);
                End;
            End
            Else If Obj.ObjectId = eNetLabel Then
            Begin
                NetLbl := Obj;
                Loc := NetLbl.Location;
                If NumLabels > 0 Then LabelsJson := LabelsJson + ',';
                LabelsJson := LabelsJson +
                    '{"text":"' + EscapeJsonString(NetLbl.Text) + '"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(Loc.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Loc.Y)) +
                    ',"rot":' + IntToStr(NetLbl.Orientation * 90) + '}';
                Inc(NumLabels);
            End
            Else If Obj.ObjectId = ePort Then
            Begin
                Port := Obj;
                Loc := Port.Location;
                If NumPorts > 0 Then PortsJson := PortsJson + ',';
                PortsJson := PortsJson +
                    '{"text":"' + EscapeJsonString(Port.Name) + '"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(Loc.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Loc.Y)) +
                    ',"w":' + FloatToJsonStr(CoordToMilsF(Port.Width)) +
                    ',"iotype":' + IntToStr(Port.IOType) + '}';
                Inc(NumPorts);
            End
            Else If Obj.ObjectId = ePowerObject Then
            Begin
                Power := Obj;
                Loc := Power.Location;
                If NumPower > 0 Then PowerJson := PowerJson + ',';
                PowerJson := PowerJson +
                    '{"text":"' + EscapeJsonString(Power.Text) + '"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(Loc.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Loc.Y)) +
                    ',"style":' + IntToStr(Power.Style) +
                    ',"rot":' + IntToStr(Power.Orientation * 90) + '}';
                Inc(NumPower);
            End
            Else If Obj.ObjectId = eJunction Then
            Begin
                Junct := Obj;
                Loc := Junct.Location;
                If NumJuncts > 0 Then JunctsJson := JunctsJson + ',';
                JunctsJson := JunctsJson +
                    '{"x":' + FloatToJsonStr(CoordToMilsF(Loc.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Loc.Y)) + '}';
                Inc(NumJuncts);
            End
            Else If Obj.ObjectId = eSheetSymbol Then
            Begin
                { Hierarchical sheet symbols are the labelled boxes on the   }
                { top sheet that represent sub-sheets. Their interior is     }
                { otherwise empty so without rendering them a top-of-design  }
                { sheet renders as blank space. We also walk their child     }
                { eSheetEntry terminals so the renderer can draw the IO     }
                { stubs at the right edges.                                   }
                SheetSym := Obj;
                Loc := SheetSym.Location;
                SheetName := '';
                Try If SheetSym.SheetName <> Nil Then SheetName := SheetSym.SheetName.Text; Except End;
                SheetFile := '';
                Try If SheetSym.SheetFileName <> Nil Then SheetFile := SheetSym.SheetFileName.Text; Except End;

                EntriesJson := '[';
                NumEntries := 0;
                EntryIter := SheetSym.SchIterator_Create;
                EntryIter.AddFilter_ObjectSet(MkSet(eSheetEntry));
                Try
                    SheetEntry := EntryIter.FirstSchObject;
                    While SheetEntry <> Nil Do
                    Begin
                        If NumEntries > 0 Then EntriesJson := EntriesJson + ',';
                        EntriesJson := EntriesJson +
                            '{"name":"' + EscapeJsonString(SheetEntry.Name) + '"' +
                            ',"x":' + FloatToJsonStr(CoordToMilsF(SheetEntry.Location.X)) +
                            ',"y":' + FloatToJsonStr(CoordToMilsF(SheetEntry.Location.Y)) +
                            ',"iotype":' + IntToStr(SheetEntry.IOType) +
                            ',"side":' + IntToStr(SheetEntry.Side) + '}';
                        Inc(NumEntries);
                        SheetEntry := EntryIter.NextSchObject;
                    End;
                Finally
                    SheetSym.SchIterator_Destroy(EntryIter);
                End;
                EntriesJson := EntriesJson + ']';

                If NumSheetSyms > 0 Then SheetSymsJson := SheetSymsJson + ',';
                SheetSymsJson := SheetSymsJson +
                    '{"name":"' + EscapeJsonString(SheetName) + '"' +
                    ',"filename":"' + EscapeJsonString(SheetFile) + '"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(Loc.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Loc.Y)) +
                    ',"w":' + FloatToJsonStr(CoordToMilsF(SheetSym.XSize)) +
                    ',"h":' + FloatToJsonStr(CoordToMilsF(SheetSym.YSize)) +
                    ',"color":' + IntToStr(SheetSym.Color) +
                    ',"area_color":' + IntToStr(SheetSym.AreaColor) +
                    ',"entries":' + EntriesJson + '}';
                Inc(NumSheetSyms);
            End
            Else If Obj.ObjectId = eBus Then
            Begin
                { Buses share their vertex API with wires (ISch_Polyline      }
                { children). Emitted separately so the renderer can draw     }
                { them thicker / in a distinct colour.                        }
                Bus := Obj;
                VtxN := 0;
                Try VtxN := Bus.GetState_VerticesCount; Except End;
                If VtxN >= 2 Then
                Begin
                    If NumBuses > 0 Then BusesJson := BusesJson + ',';
                    BusesJson := BusesJson + '{"verts":[';
                    For V := 1 To VtxN Do
                    Begin
                        Vert := Bus.GetState_Vertex(V);
                        If V > 1 Then BusesJson := BusesJson + ',';
                        BusesJson := BusesJson + '['
                            + FloatToJsonStr(CoordToMilsF(Vert.X)) + ','
                            + FloatToJsonStr(CoordToMilsF(Vert.Y)) + ']';
                    End;
                    BusesJson := BusesJson + ']}';
                    Inc(NumBuses);
                End;
            End;
            Obj := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    CompsJson    := CompsJson    + ']';
    PinsJson     := PinsJson     + ']';
    WiresJson    := WiresJson    + ']';
    LabelsJson   := LabelsJson   + ']';
    PortsJson    := PortsJson    + ']';
    PowerJson    := PowerJson    + ']';
    JunctsJson   := JunctsJson   + ']';
    SheetSymsJson := SheetSymsJson + ']';
    BusesJson    := BusesJson    + ']';

    RespJson :=
        '{"doc":"' + EscapeJsonString(SchDoc.DocumentName) + '"' +
        ',"counts":{"components":' + IntToStr(NumComps) +
        ',"pins":' + IntToStr(NumPins) +
        ',"wires":' + IntToStr(NumWires) +
        ',"net_labels":' + IntToStr(NumLabels) +
        ',"ports":' + IntToStr(NumPorts) +
        ',"power_ports":' + IntToStr(NumPower) +
        ',"junctions":' + IntToStr(NumJuncts) +
        ',"sheet_symbols":' + IntToStr(NumSheetSyms) +
        ',"buses":' + IntToStr(NumBuses) + '}' +
        ',"components":' + CompsJson +
        ',"pins":' + PinsJson +
        ',"wires":' + WiresJson +
        ',"net_labels":' + LabelsJson +
        ',"ports":' + PortsJson +
        ',"power_ports":' + PowerJson +
        ',"junctions":' + JunctsJson +
        ',"sheet_symbols":' + SheetSymsJson +
        ',"buses":' + BusesJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
    Finally
        Try PhysMap.Free; Except End;
    End;
End;

{ Gen_GetPcbGeometry - Walk the active PcbDoc and emit every primitive's    }
{ geometry as JSON so a Python-side renderer can produce per-layer SVG     }
{ independently of any third-party Altium parser.                          }
{                                                                              }
{ v1 surface:                                                                  }
{   - board outline (segments as a line/arc polyline)                         }
{   - tracks (X1/Y1/X2/Y2, width, layer, net)                                 }
{   - arcs (center, radius, start/end angle, width, layer, net)               }
{   - pads (location, shape, x_size, y_size, rotation, hole_size, layer, net) }
{   - vias (location, size, hole, high/low layer, net)                        }
{   - texts (location, text, size, width, rotation, layer)                    }
{                                                                              }
{ Coordinates in mils. Layer names come back as Altium's GetLayerString      }
{ form so the renderer can z-order, colour, and toggle by name.              }
{ Deferred to v2: regions / polygon fills, component bodies, drill drawing. }
Function Gen_GetPcbGeometry(Params : String; RequestId : String) : String;
Var
    Board : IPCB_Board;
    Iter : IPCB_BoardIterator;
    Obj : IPCB_Primitive;
    Track : IPCB_Track;
    Arc : IPCB_Arc;
    Pad : IPCB_Pad;
    Via : IPCB_Via;
    Text : IPCB_Text;
    Region : IPCB_Region;
    Contour : IPCB_Contour;
    CompObj : IPCB_Component;
    Outline : IPCB_BoardOutline;
    OutlineJson, TracksJson, ArcsJson, PadsJson, ViasJson, TextsJson : String;
    RegionsJson, CompsJson : String;
    NumTracks, NumArcs, NumPads, NumVias, NumTexts, NumOutline : Integer;
    NumRegions, NumComps : Integer;
    LayerName, ShapeStr, NetName, TextStr, PadName, HoleStr, PlatedStr : String;
    BR : TCoordRect;
    I, K, PtCount, EL, EB, ER, ET : Integer;
    Seg : TPolySegment;
    RespJson : String;
    NameOnFlag, CommentOnFlag, IsHiddenFlag : Boolean;
    PCBSysOpts : IPCB_SystemOptions;
    Lyr : TLayer;
    LayersJson, LyrNm : String;
    LyrColor, MechN : Integer;
    LyrVisible, LyrFirst : Boolean;
Begin
    Board := GetPCBBoardAnywhere(0);
    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB',
            'No PCB document is active');
        Exit;
    End;

    { Board outline: walk Segments. Each carries a vertex (vx, vy) and -- }
    { for arc segments -- a center (cx, cy) + radius + angles. v1 keeps   }
    { both kinds; the renderer can draw arc segments as proper arcs or    }
    { fall back to chord lines.                                             }
    OutlineJson := '['; NumOutline := 0;
    Outline := Board.BoardOutline;
    If Outline <> Nil Then
    Begin
        Try Outline.Invalidate; Outline.Rebuild; Outline.Validate; Except End;
        For I := 0 To Outline.PointCount - 1 Do
        Begin
            Seg := Outline.Segments[I];
            If NumOutline > 0 Then OutlineJson := OutlineJson + ',';
            If Seg.Kind = ePolySegmentLine Then
            Begin
                OutlineJson := OutlineJson +
                    '{"kind":"line"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(Seg.vx)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Seg.vy)) + '}';
            End
            Else
            Begin
                OutlineJson := OutlineJson +
                    '{"kind":"arc"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(Seg.vx)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Seg.vy)) +
                    ',"cx":' + FloatToJsonStr(CoordToMilsF(Seg.cx)) +
                    ',"cy":' + FloatToJsonStr(CoordToMilsF(Seg.cy)) +
                    ',"angle1":' + FloatToJsonStr(Seg.Angle1) +
                    ',"angle2":' + FloatToJsonStr(Seg.Angle2) +
                    ',"radius":' + FloatToJsonStr(CoordToMilsF(Seg.Radius)) + '}';
            End;
            Inc(NumOutline);
        End;
    End;
    OutlineJson := OutlineJson + ']';

    { From the outline's segments: its cached rectangle went stale after a }
    { reshape and the renders built on this bbox clipped the board.         }
    BR := Board.BoardOutline.BoundingRectangle;
    EL := BR.X1; EB := BR.Y1; ER := BR.X2; ET := BR.Y2;
    OutlineExtents(Board.BoardOutline, EL, EB, ER, ET);

    TracksJson := '['; NumTracks := 0;
    ArcsJson := '[';   NumArcs := 0;
    PadsJson := '[';   NumPads := 0;
    ViasJson := '[';   NumVias := 0;
    TextsJson := '[';  NumTexts := 0;
    RegionsJson := '['; NumRegions := 0;
    CompsJson := '[';   NumComps := 0;

    Iter := Board.BoardIterator_Create;
    Iter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject, ePadObject,
        eViaObject, eTextObject, eRegionObject, eComponentObject));
    Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
    Iter.AddFilter_Method(eProcessAll);
    Try
        Obj := Iter.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            LayerName := '';
            Try LayerName := LayerDisplayName(Obj.Layer); Except End;

            If Obj.ObjectId = eTrackObject Then
            Begin
                Track := Obj;
                NetName := '';
                Try If Track.Net <> Nil Then NetName := Track.Net.Name; Except End;
                If NumTracks > 0 Then TracksJson := TracksJson + ',';
                TracksJson := TracksJson +
                    '{"x1":' + FloatToJsonStr(CoordToMilsF(Track.X1)) +
                    ',"y1":' + FloatToJsonStr(CoordToMilsF(Track.Y1)) +
                    ',"x2":' + FloatToJsonStr(CoordToMilsF(Track.X2)) +
                    ',"y2":' + FloatToJsonStr(CoordToMilsF(Track.Y2)) +
                    ',"width":' + FloatToJsonStr(CoordToMilsF(Track.Width)) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumTracks);
            End
            Else If Obj.ObjectId = eArcObject Then
            Begin
                Arc := Obj;
                NetName := '';
                Try If Arc.Net <> Nil Then NetName := Arc.Net.Name; Except End;
                If NumArcs > 0 Then ArcsJson := ArcsJson + ',';
                ArcsJson := ArcsJson +
                    '{"cx":' + FloatToJsonStr(CoordToMilsF(Arc.XCenter)) +
                    ',"cy":' + FloatToJsonStr(CoordToMilsF(Arc.YCenter)) +
                    ',"r":' + FloatToJsonStr(CoordToMilsF(Arc.Radius)) +
                    ',"start":' + FloatToJsonStr(Arc.StartAngle) +
                    ',"end":' + FloatToJsonStr(Arc.EndAngle) +
                    ',"width":' + FloatToJsonStr(CoordToMilsF(Arc.LineWidth)) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumArcs);
            End
            Else If Obj.ObjectId = ePadObject Then
            Begin
                Pad := Obj;
                ShapeStr := 'Round';
                Try
                    If Pad.TopShape = eRounded Then ShapeStr := 'Round'
                    Else If Pad.TopShape = eRectangular Then ShapeStr := 'Rectangular'
                    Else If Pad.TopShape = eOctagonal Then ShapeStr := 'Octagonal'
                    Else If Pad.TopShape = eRoundedRectangular Then ShapeStr := 'RoundedRect'
                    Else If ShapeIsCustom(Pad.TopShape) Then ShapeStr := 'Custom';
                Except End;
                { Drill shape: round / square / slot. Slot pads expose a   }
                { separate hole_width and a hole_rotation; round + square }
                { reuse hole_size for the width/diameter.                  }
                HoleStr := 'Round';
                Try
                    If Pad.HoleType = eSquareHole Then HoleStr := 'Square'
                    Else If Pad.HoleType = eSlotHole Then HoleStr := 'Slot';
                Except End;
                NetName := '';
                Try If Pad.Net <> Nil Then NetName := Pad.Net.Name; Except End;
                PadName := '';
                Try PadName := Pad.Name; Except End;
                { Owning component's designator -- empty for free pads     }
                { (fiducials, mounting holes, board-level pads). Lets the  }
                { renderer attach data-designator so a pad click on the    }
                { PCB SVG can open the parent component's drawer.          }
                TextStr := '';
                Try
                    If Pad.Component <> Nil Then TextStr := Pad.Component.Name.Text;
                Except End;
                { Plating: a picture draws a plated barrel or bare laminate. }
                PlatedStr := 'true';
                Try
                    PlatedStr := BoolToJsonStr(Pad.Plated);
                Except End;
                If NumPads > 0 Then PadsJson := PadsJson + ',';
                PadsJson := PadsJson +
                    '{"x":' + FloatToJsonStr(CoordToMilsF(Pad.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Pad.Y)) +
                    ',"x_size":' + FloatToJsonStr(CoordToMilsF(Pad.TopXSize)) +
                    ',"y_size":' + FloatToJsonStr(CoordToMilsF(Pad.TopYSize)) +
                    ',"shape":"' + EscapeJsonString(ShapeStr) + '"' +
                    ',"hole_size":' + FloatToJsonStr(CoordToMilsF(Pad.HoleSize)) +
                    ',"hole_type":"' + EscapeJsonString(HoleStr) + '"' +
                    ',"hole_width":' + FloatToJsonStr(CoordToMilsF(Pad.HoleWidth)) +
                    ',"hole_rotation":' + FloatToJsonStr(Pad.HoleRotation) +
                    ',"plated":' + PlatedStr +
                    ',"rotation":' + FloatToJsonStr(Pad.Rotation) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"name":"' + EscapeJsonString(PadName) + '"' +
                    ',"comp":"' + EscapeJsonString(TextStr) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumPads);
            End
            Else If Obj.ObjectId = eViaObject Then
            Begin
                Via := Obj;
                NetName := '';
                Try If Via.Net <> Nil Then NetName := Via.Net.Name; Except End;
                If NumVias > 0 Then ViasJson := ViasJson + ',';
                ViasJson := ViasJson +
                    '{"x":' + FloatToJsonStr(CoordToMilsF(Via.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Via.Y)) +
                    ',"size":' + FloatToJsonStr(CoordToMilsF(Via.Size)) +
                    ',"hole_size":' + FloatToJsonStr(CoordToMilsF(Via.HoleSize)) +
                    ',"high_layer":"' + EscapeJsonString(LayerDisplayName(Via.HighLayer)) + '"' +
                    ',"low_layer":"' + EscapeJsonString(LayerDisplayName(Via.LowLayer)) + '"' +
                    ',"net":"' + EscapeJsonString(NetName) + '"}';
                Inc(NumVias);
            End
            Else If Obj.ObjectId = eTextObject Then
            Begin
                Text := Obj;
                { Skip hidden text entirely -- footprints from vendor      }
                { libraries typically carry 5-10 hidden text objects each }
                { (.Designator / .Comment / .ChannelDesignator on multiple}
                { mech / solder / paste layers). On a 100-component board }
                { that's 500-1000 IPC-irrelevant primitives. Skipping at  }
                { the Pascal side shrinks the JSON payload + client parse }
                { without losing anything the renderer would render.      }
                IsHiddenFlag := False;
                Try IsHiddenFlag := Text.IsHidden; Except End;
                If IsHiddenFlag Then
                Begin
                    Obj := Iter.NextPCBObject;
                    Continue;
                End;
                TextStr := '';
                Try TextStr := Text.Text; Except End;
                If TextStr = '' Then Try TextStr := Text.UnderlyingString; Except End;
                If TextStr = '' Then
                Begin
                    Obj := Iter.NextPCBObject;
                    Continue;
                End;
                If NumTexts > 0 Then TextsJson := TextsJson + ',';
                TextsJson := TextsJson +
                    '{"x":' + FloatToJsonStr(CoordToMilsF(Text.XLocation)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(Text.YLocation)) +
                    ',"text":"' + EscapeJsonString(TextStr) + '"' +
                    ',"size":' + FloatToJsonStr(CoordToMilsF(Text.Size)) +
                    ',"width":' + FloatToJsonStr(CoordToMilsF(Text.Width)) +
                    ',"rotation":' + FloatToJsonStr(Text.Rotation) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"hidden":false}';
                Inc(NumTexts);
            End
            Else If Obj.ObjectId = eRegionObject Then
            Begin
                { Regions are the actual poured copper on a board -- the   }
                { biggest visual gap a tracks-only render leaves behind.   }
                { MainContour is 1-based and indexes X[i] / Y[i] arrays.   }
                Region := Obj;
                NetName := '';
                Try If Region.Net <> Nil Then NetName := Region.Net.Name; Except End;
                Contour := Nil;
                PtCount := 0;
                Try
                    Contour := Region.MainContour;
                    If Contour <> Nil Then PtCount := Contour.Count;
                Except
                End;
                If (Contour <> Nil) And (PtCount >= 3) Then
                Begin
                    If NumRegions > 0 Then RegionsJson := RegionsJson + ',';
                    RegionsJson := RegionsJson +
                        '{"layer":"' + EscapeJsonString(LayerName) + '"' +
                        ',"net":"' + EscapeJsonString(NetName) + '"' +
                        ',"pts":[';
                    For K := 1 To PtCount Do
                    Begin
                        If K > 1 Then RegionsJson := RegionsJson + ',';
                        RegionsJson := RegionsJson + '['
                            + FloatToJsonStr(CoordToMilsF(Contour.X[K])) + ','
                            + FloatToJsonStr(CoordToMilsF(Contour.Y[K])) + ']';
                    End;
                    RegionsJson := RegionsJson + ']}';
                    Inc(NumRegions);
                End;
            End
            Else If Obj.ObjectId = eComponentObject Then
            Begin
                { Component identity -- mostly so the renderer can place a }
                { designator label next to each footprint on a virtual    }
                { "Designators" pseudo-layer. The actual silkscreen art    }
                { for each footprint is already emitted via its child     }
                { tracks / arcs / texts on the *Overlay layers.            }
                CompObj := Obj;
                TextStr := '';
                Try TextStr := CompObj.Name.Text; Except End;
                If TextStr = '' Then Try TextStr := CompObj.SourceDesignator; Except End;
                { NameOn / CommentOn drive whether the designator + comment   }
                { strings are visible in Altium itself. The renderer respects }
                { these flags so designs that explicitly hide labels render  }
                { the same in the dashboard.                                 }
                NameOnFlag := True;
                Try NameOnFlag := CompObj.NameOn; Except End;
                CommentOnFlag := True;
                Try CommentOnFlag := CompObj.CommentOn; Except End;
                If NumComps > 0 Then CompsJson := CompsJson + ',';
                CompsJson := CompsJson +
                    '{"des":"' + EscapeJsonString(TextStr) + '"' +
                    ',"x":' + FloatToJsonStr(CoordToMilsF(CompObj.X)) +
                    ',"y":' + FloatToJsonStr(CoordToMilsF(CompObj.Y)) +
                    ',"rotation":' + FloatToJsonStr(CompObj.Rotation) +
                    ',"layer":"' + EscapeJsonString(LayerName) + '"' +
                    ',"name_on":' + BoolToJsonStr(NameOnFlag) +
                    ',"comment_on":' + BoolToJsonStr(CommentOnFlag) + '}';
                Inc(NumComps);
            End;
            Obj := Iter.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iter);
    End;

    TracksJson  := TracksJson  + ']';
    ArcsJson    := ArcsJson    + ']';
    PadsJson    := PadsJson    + ']';
    ViasJson    := ViasJson    + ']';
    TextsJson   := TextsJson   + ']';
    RegionsJson := RegionsJson + ']';
    CompsJson   := CompsJson   + ']';

    { Layer colours + visibility, straight from Altium so the renderer can }
    { reproduce exactly what the user sees on the bench instead of guessing }
    { a palette. "color" is the raw TColor integer (BGR-packed: $00BBGGRR); }
    { the Python side converts to #RRGGBB. "visible" mirrors Altium's own   }
    { displayed-layer set so the render can default to showing only what    }
    { Altium shows. Keyed by GetLayerString -- the SAME name the primitives }
    { carry -- so the renderer matches by name with no translation. Range   }
    { eTopLayer..eMultiLayer covers signal, plane, mech, mask, paste, silk, }
    { keepout and multilayer (the official Altium docs iterate this range). }
    LayersJson := '{';
    LyrFirst := True;
    PCBSysOpts := Nil;
    Try PCBSysOpts := PCBServer.SystemOptions; Except End;
    If PCBSysOpts <> Nil Then
    Begin
        For Lyr := eTopLayer To eMultiLayer Do
        Begin
            LyrNm := LayerDisplayName(Lyr);
            If LyrNm <> 'Unknown' Then
            Begin
                LyrColor := 0;
                LyrVisible := True;
                Try LyrColor := PCBSysOpts.LayerColors[Lyr]; Except End;
                Try LyrVisible := Board.LayerIsDisplayed[Lyr]; Except End;
                If Not LyrFirst Then LayersJson := LayersJson + ',';
                LayersJson := LayersJson +
                    '"' + EscapeJsonString(LyrNm) + '":{"color":' +
                    IntToStr(LyrColor) + ',"visible":' +
                    BoolToJsonStr(LyrVisible) + '}';
                LyrFirst := False;
            End;
        End;
        { the legacy range stops at Mechanical16 }
        For MechN := 17 To 1024 Do
        Begin
            Lyr := MechLayerFromNumber(MechN);
            If Lyr = eNoLayer Then Break;
            If Not MechLayerEnabled(Board, Lyr) Then Continue;
            LyrColor := 0;
            LyrVisible := True;
            Try LyrColor := PCBSysOpts.LayerColors[Lyr]; Except End;
            Try LyrVisible := Board.LayerIsDisplayed[Lyr]; Except End;
            If Not LyrFirst Then LayersJson := LayersJson + ',';
            LayersJson := LayersJson +
                '"' + EscapeJsonString(LayerDisplayName(Lyr)) + '":{"color":' +
                IntToStr(LyrColor) + ',"visible":' +
                BoolToJsonStr(LyrVisible) + '}';
            LyrFirst := False;
        End;
    End;
    LayersJson := LayersJson + '}';

    RespJson :=
        '{"counts":{"outline":' + IntToStr(NumOutline) +
        ',"tracks":' + IntToStr(NumTracks) +
        ',"arcs":' + IntToStr(NumArcs) +
        ',"pads":' + IntToStr(NumPads) +
        ',"vias":' + IntToStr(NumVias) +
        ',"texts":' + IntToStr(NumTexts) +
        ',"regions":' + IntToStr(NumRegions) +
        ',"components":' + IntToStr(NumComps) + '}' +
        ',"bbox":{"x1":' + FloatToJsonStr(CoordToMilsF(EL)) +
        ',"y1":' + FloatToJsonStr(CoordToMilsF(EB)) +
        ',"x2":' + FloatToJsonStr(CoordToMilsF(ER)) +
        ',"y2":' + FloatToJsonStr(CoordToMilsF(ET)) + '}' +
        ',"outline":' + OutlineJson +
        ',"tracks":' + TracksJson +
        ',"arcs":' + ArcsJson +
        ',"pads":' + PadsJson +
        ',"vias":' + ViasJson +
        ',"texts":' + TextsJson +
        ',"regions":' + RegionsJson +
        ',"components":' + CompsJson +
        ',"layers":' + LayersJson + '}';
    Result := BuildSuccessResponse(RequestId, RespJson);
End;

{..............................................................................}
{ Gen_IncrementDesignators - Offset the trailing number of every schematic     }
{ component designator by a delta (e.g. +100 turns R5 into R105), optionally   }
{ restricted to a designator prefix. Useful for renumbering a copied block.    }
{ Params: delta (non-zero int), prefix (optional, e.g. "R")                    }
{..............................................................................}

Function Gen_IncrementDesignators(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Comp : ISch_Component;
    Delta, NumVal, Count, i, DigitPos : Integer;
    PrefixFilter, DesigFull, AlphaPart, NumPart, NewDesig : String;
    Ch : Char;
Begin
    Delta := StrToIntDef(ExtractJsonValue(Params, 'delta'), 0);
    PrefixFilter := ExtractJsonValue(Params, 'prefix');

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;
    If Delta = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'delta must be a non-zero integer');
        Exit;
    End;

    Count := 0;
    SchBeginEdit(SchDoc, '');
    Try
        Iter := SchDoc.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Try
            Comp := Iter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                DesigFull := '';
                Try DesigFull := Comp.Designator.Text; Except End;
                DigitPos := 0;
                For i := Length(DesigFull) DownTo 1 Do
                Begin
                    Ch := DesigFull[i];
                    If (Ch >= '0') And (Ch <= '9') Then DigitPos := i
                    Else Break;
                End;
                If DigitPos > 0 Then
                Begin
                    AlphaPart := Copy(DesigFull, 1, DigitPos - 1);
                    NumPart := Copy(DesigFull, DigitPos, Length(DesigFull) - DigitPos + 1);
                    If (PrefixFilter = '') Or (AlphaPart = PrefixFilter) Then
                    Begin
                        NumVal := StrToIntDef(NumPart, -1);
                        If NumVal >= 0 Then
                        Begin
                            NewDesig := AlphaPart + IntToStr(NumVal + Delta);
                            Try
                                SchBeginModify(Comp.Designator);
                                Comp.Designator.Text := NewDesig;
                                SchEndModify(Comp.Designator);
                                Inc(Count);
                            Except
                                NoteWriteFailed('increment Designator');
                            End;
                        End;
                    End;
                End;
                Comp := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
    End;
    SchDoc.GraphicallyInvalidate;

    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"modified":' + IntToStr(Count) + ',"delta":' + IntToStr(Delta) + '}');
End;

{..............................................................................}
{ TogglePinsOnComponent - set the pin name and/or designator flags on every  }
{ pin of one component. Returns the number of pins changed.                  }
Function TogglePinsOnComponent(Comp : ISch_Component; SetName : Boolean;
    NameVal : Boolean; SetDesig : Boolean; DesigVal : Boolean) : Integer;
Var
    PinIter : ISch_Iterator;
    Pin : ISch_Pin;
Begin
    Result := 0;
    PinIter := Comp.SchIterator_Create;
    PinIter.AddFilter_ObjectSet(MkSet(ePin));
    Try
        Pin := PinIter.FirstSchObject;
        While Pin <> Nil Do
        Begin
            Try
                SchBeginModify(Pin);
                If SetName Then Pin.ShowName := NameVal;
                If SetDesig Then Pin.ShowDesignator := DesigVal;
                SchEndModify(Pin);
                Inc(Result);
            Except
                NoteWriteFailed('pin ShowName/ShowDesignator');
            End;
            Pin := PinIter.NextSchObject;
        End;
    Finally
        Comp.SchIterator_Destroy(PinIter);
    End;
End;

{..............................................................................}
{ Gen_TogglePinVisibility - Show/hide pin name and/or designator labels on a   }
{ single component (by designator) or on every component on the sheet.        }
{ Params: designator (optional), show_name ("true"/"false", optional),        }
{         show_designator ("true"/"false", optional)                          }
{..............................................................................}

Function Gen_TogglePinVisibility(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    DesigFilter, ShowNameStr, ShowDesigStr, CompDesig : String;
    SetName, SetDesig, NameVal, DesigVal, Matched, IsLib : Boolean;
    Count : Integer;
    LibComp : ISch_Component;
Begin
    DesigFilter := ExtractJsonValue(Params, 'designator');
    ShowNameStr := LowerCase(ExtractJsonValue(Params, 'show_name'));
    ShowDesigStr := LowerCase(ExtractJsonValue(Params, 'show_designator'));
    SetName := (ShowNameStr = 'true') Or (ShowNameStr = 'false');
    SetDesig := (ShowDesigStr = 'true') Or (ShowDesigStr = 'false');
    NameVal := (ShowNameStr = 'true');
    DesigVal := (ShowDesigStr = 'true');

    If (Not SetName) And (Not SetDesig) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'Provide show_name and/or show_designator as "true"/"false"');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    Count := 0;
    IsLib := False;
    Try IsLib := (SchDoc.ObjectId = eSchLib); Except IsLib := False; End;
    If IsLib Then
    Begin
        { A SchLib has no placed components to walk: the sheet iterator
          returns nothing there, so the old code reported success with 0.
          The symbol open in the editor is the one to change (issue #89).
          The held reference is only a fallback: it can name a symbol
          created earlier that is no longer the one on screen. }
        LibComp := Nil;
        Try LibComp := SchDoc.CurrentSchComponent; Except LibComp := Nil; End;
        If LibComp = Nil Then LibComp := RememberedLibComponent(SchDoc);
        If LibComp = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_COMPONENT',
                'No symbol is open in the SchLib editor. Open one and retry.');
            Exit;
        End;
        SchBeginEdit(SchDoc, '');
        Try
            Count := TogglePinsOnComponent(LibComp, SetName, NameVal, SetDesig, DesigVal);
        Finally
            SchEndEdit(SchDoc, 'Edit');
        End;
        SchDoc.GraphicallyInvalidate;
        If Count = 0 Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PINS',
                'The open symbol has no pins to change');
            Exit;
        End;
        Result := BuildSuccessResponse(RequestId,
            '{"success":true,"pins_modified":' + IntToStr(Count) + '}');
        Exit;
    End;
    SchBeginEdit(SchDoc, '');
    Try
        Iter := SchDoc.SchIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Try
            Comp := Iter.FirstSchObject;
            While Comp <> Nil Do
            Begin
                CompDesig := '';
                Try CompDesig := Comp.Designator.Text; Except End;
                Matched := (DesigFilter = '') Or (CompDesig = DesigFilter);
                If Matched Then
                Begin
                    PinIter := Comp.SchIterator_Create;
                    PinIter.AddFilter_ObjectSet(MkSet(ePin));
                    Try
                        Pin := PinIter.FirstSchObject;
                        While Pin <> Nil Do
                        Begin
                            Try
                                SchBeginModify(Pin);
                                If SetName Then Pin.ShowName := NameVal;
                                If SetDesig Then Pin.ShowDesignator := DesigVal;
                                SchEndModify(Pin);
                                Inc(Count);
                            Except
                                NoteWriteFailed('pin ShowName/ShowDesignator');
                            End;
                            Pin := PinIter.NextSchObject;
                        End;
                    Finally
                        Comp.SchIterator_Destroy(PinIter);
                    End;
                End;
                Comp := Iter.NextSchObject;
            End;
        Finally
            SchDoc.SchIterator_Destroy(Iter);
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
    End;
    SchDoc.GraphicallyInvalidate;

    If Count = 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_MATCH',
            'No component with pins matched; check the designator');
        Exit;
    End;
    Result := BuildSuccessResponse(RequestId,
        '{"success":true,"pins_modified":' + IntToStr(Count) + '}');
End;

{ A colour argument of place_text_frame: -1 when absent, -2 when it is not a }
{ colour (Altium's packed blue-green-red, 0 .. 16777215). The text is       }
{ checked character by character first: StrToIntDef raises on a number past }
{ 32 bits instead of falling back to its default.                           }
Function TextFrameColorArg(Params : String; Key : String) : Integer;
Var
    S : String;
    I : Integer;
Begin
    S := Trim(ExtractJsonValue(Params, Key));
    Result := -1;
    If S = '' Then Exit;
    Result := -2;
    If Length(S) > 8 Then Exit;
    For I := 1 To Length(S) Do
        If (S[I] < '0') Or (S[I] > '9') Then Exit;
    I := StrToIntDef(S, -2);
    If (I < 0) Or (I > 16777215) Then Exit;
    Result := I;
End;

{ A switch argument of place_text_frame: -1 absent, 0 false, 1 true, -2 not }
{ a switch.                                                                 }
Function TextFrameFlagArg(Params : String; Key : String) : Integer;
Var
    S : String;
Begin
    S := LowerCase(Trim(ExtractJsonValue(Params, Key)));
    If S = '' Then Result := -1
    Else If (S = 'true') Or (S = '1') Then Result := 1
    Else If (S = 'false') Or (S = '0') Then Result := 0
    Else Result := -2;
End;

{ The style of a placed text frame as JSON members, read off the frame, not }
{ echoed from the request. A read that fails leaves its member out.         }
Function TextFrameStyleJson(TF : ISch_TextFrame) : String;
Var
    Parts : String;
Begin
    Parts := '';
    Try Parts := Parts + JsonBool('is_solid', TF.IsSolid) + ','; Except End;
    Try Parts := Parts + JsonInt('area_color', TF.AreaColor) + ','; Except End;
    Try Parts := Parts + JsonInt('color', TF.Color) + ','; Except End;
    Try Parts := Parts + JsonInt('text_color', TF.TextColor) + ','; Except End;
    Try Parts := Parts + JsonBool('show_border', TF.ShowBorder) + ','; Except End;
    Try Parts := Parts + JsonBool('word_wrap', TF.WordWrap) + ','; Except End;
    Try Parts := Parts + JsonBool('clip_to_rect', TF.ClipToRect) + ','; Except End;
    Try Parts := Parts + JsonFloat('text_margin', CoordToMilsF(TF.TextMargin)) + ','; Except End;
    Result := '"style":{' + Copy(Parts, 1, Length(Parts) - 1) + '}';
End;

{..............................................................................}
{ Gen_PlaceTextFrame - Place a multi-line schematic text frame (note block).  }
{ Params: x1,y1,x2,y2 (mils, the two rectangle corners), text (use \n for     }
{         line breaks), align ("left"/"center"/"right", optional)             }
{ Style, all optional, applied before the frame is registered (absent = the  }
{ frame's default, so older callers see no change): area_color (fill; makes  }
{ the frame solid), color (border), text_color (packed BGR integers),        }
{ show_border, word_wrap, clip_to_rect (true/false), text_margin (mils).     }
{ The reply reads every one of them back off the placed frame.              }
{..............................................................................}

Function Gen_PlaceTextFrame(Params : String; RequestId : String) : String;
Var
    TmpI : Integer;
    X1, Y1, X2, Y2 : Double;
    SchDoc : ISch_Document;
    TF : ISch_TextFrame;
    TextStr, AlignStr, MarginStr : String;
    FontPick, FontRead : Integer;
    WantFill, WantBorderColor, WantTextColor : Integer;
    WantShowBorder, WantWrap, WantClip : Integer;
    WantMargin : Double;
Begin
    X1 := StrToFloatDef(ExtractJsonValue(Params, 'x1'), 0);
    Y1 := StrToFloatDef(ExtractJsonValue(Params, 'y1'), 0);
    X2 := StrToFloatDef(ExtractJsonValue(Params, 'x2'), 0);
    Y2 := StrToFloatDef(ExtractJsonValue(Params, 'y2'), 0);
    TextStr := ExtractJsonValue(Params, 'text');
    AlignStr := LowerCase(ExtractJsonValue(Params, 'align'));
    { A line break arrives as a newline or as a typed backslash-n, and the  }
    { second was stored and printed as the two characters. Altium's own    }
    { break in a text frame is CR LF; every spelling becomes that.          }
    TextStr := StringReplace(TextStr, '\' + 'n', #10, MkSet(rfReplaceAll));
    TextStr := StringReplace(TextStr, #13 + #10, #10, MkSet(rfReplaceAll));
    TextStr := StringReplace(TextStr, #13, #10, MkSet(rfReplaceAll));
    TextStr := StringReplace(TextStr, #10, #13 + #10, MkSet(rfReplaceAll));
    If X1 > X2 Then Begin TmpI := X1; X1 := X2; X2 := TmpI; End;
    If Y1 > Y2 Then Begin TmpI := Y1; Y1 := Y2; Y2 := TmpI; End;

    { The style is read and checked before anything is made: a bad value  }
    { places nothing.                                                      }
    WantFill := TextFrameColorArg(Params, 'area_color');
    WantBorderColor := TextFrameColorArg(Params, 'color');
    WantTextColor := TextFrameColorArg(Params, 'text_color');
    WantShowBorder := TextFrameFlagArg(Params, 'show_border');
    WantWrap := TextFrameFlagArg(Params, 'word_wrap');
    WantClip := TextFrameFlagArg(Params, 'clip_to_rect');
    MarginStr := Trim(ExtractJsonValue(Params, 'text_margin'));
    WantMargin := -1;
    If MarginStr <> '' Then WantMargin := StrToFloatDef(MarginStr, -2);
    If (WantFill = -2) Or (WantBorderColor = -2) Or (WantTextColor = -2) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_COLOR',
            'area_color, color and text_color are packed BGR integers 0..16777215');
        Exit;
    End;
    If (WantShowBorder = -2) Or (WantWrap = -2) Or (WantClip = -2) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_FLAG',
            'show_border, word_wrap and clip_to_rect are true or false');
        Exit;
    End;
    If (MarginStr <> '') And (WantMargin < 0) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_MARGIN',
            'text_margin is a length in mils, 0 or more');
        Exit;
    End;

    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC', 'No schematic document is active');
        Exit;
    End;

    FontPick := NewObjectFontId(SchDoc, eTextFrame, Params);
    TF := SchServer.SchObjectFactory(eTextFrame, eCreate_Default);
    If TF = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'CREATE_FAILED', 'Failed to create text frame');
        Exit;
    End;

    TF.Location := Point(MilsToCoordF(X1), MilsToCoordF(Y1));
    TF.Corner := Point(MilsToCoordF(X2), MilsToCoordF(Y2));
    Try TF.Text := TextStr; Except NoteWriteFailed('TextFrame.Text'); End;
    Try TF.WordWrap := True; Except NoteWriteFailed('TextFrame.WordWrap'); End;
    Try TF.ClipToRect := True; Except NoteWriteFailed('TextFrame.ClipToRect'); End;
    Try TF.ShowBorder := True; Except NoteWriteFailed('TextFrame.ShowBorder'); End;
    Try TF.IsSolid := False; Except NoteWriteFailed('TextFrame.IsSolid'); End;
    If AlignStr = 'center' Then
    Begin Try TF.Alignment := eHorizontalCentreAlign; Except NoteWriteFailed('TextFrame.Alignment'); End; End
    Else If AlignStr = 'right' Then
    Begin Try TF.Alignment := eRightAlign; Except NoteWriteFailed('TextFrame.Alignment'); End; End
    Else
    Begin Try TF.Alignment := eLeftAlign; Except NoteWriteFailed('TextFrame.Alignment'); End; End;
    If FontPick > 0 Then
    Begin
        Try TF.FontId := FontPick; Except NoteWriteFailed('TextFrame.FontId'); End;
    End;
    { The asked style, over the defaults above. A fill makes the frame     }
    { solid: AreaColor alone draws nothing on an unfilled frame.           }
    If WantFill >= 0 Then
    Begin
        Try TF.IsSolid := True; Except NoteWriteFailed('TextFrame.IsSolid'); End;
        Try TF.AreaColor := WantFill; Except NoteWriteFailed('TextFrame.AreaColor'); End;
    End;
    If WantBorderColor >= 0 Then
    Begin Try TF.Color := WantBorderColor; Except NoteWriteFailed('TextFrame.Color'); End; End;
    If WantTextColor >= 0 Then
    Begin Try TF.TextColor := WantTextColor; Except NoteWriteFailed('TextFrame.TextColor'); End; End;
    If WantShowBorder >= 0 Then
    Begin Try TF.ShowBorder := (WantShowBorder = 1); Except NoteWriteFailed('TextFrame.ShowBorder'); End; End;
    If WantWrap >= 0 Then
    Begin Try TF.WordWrap := (WantWrap = 1); Except NoteWriteFailed('TextFrame.WordWrap'); End; End;
    If WantClip >= 0 Then
    Begin Try TF.ClipToRect := (WantClip = 1); Except NoteWriteFailed('TextFrame.ClipToRect'); End; End;
    If WantMargin >= 0 Then
    Begin Try TF.TextMargin := MilsToCoordF(WantMargin); Except NoteWriteFailed('TextFrame.TextMargin'); End; End;

    SchBeginEdit(SchDoc, '');
    SchDoc.RegisterSchObjectInContainer(TF);
    SchRegisterObject(SchDoc, TF);
    SchEndEdit(SchDoc, 'Edit');
    SchDoc.GraphicallyInvalidate;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    FontRead := 0;
    Try FontRead := TF.FontId; Except End;
    Result := BuildSuccessResponse(RequestId,
        '{"placed":true,"x1":' + FloatToJsonStr(X1) + ',"y1":' + FloatToJsonStr(Y1) + ','
        + '"x2":' + FloatToJsonStr(X2) + ',"y2":' + FloatToJsonStr(Y2) + ','
        + JsonInt('font_id', FontRead) + ',' + TextFrameStyleJson(TF) + '}');
End;

{..............................................................................}
{ Gen_ClearSchSourceLibrary - schematic mirror of the PCB-side                }
{ clear_source_footprint_library: unpin placed components from a stale        }
{ source library so Altium re-matches them from Available Libraries.          }
{ Per matching component: clear SourceLibraryName, and (default on) sync      }
{ DesignItemId to LibReference - the corpus-standard repair for the           }
{ <Not Found> state a re-link leaves behind when DesignItemId still names     }
{ the OLD library item. DesignItemId is a component PROPERTY, not a           }
{ parameter; the parameter-stamping path only creates a stray user            }
{ parameter of that name.                                                      }
{ Params: sheet_path (optional, focused doc default),                          }
{         designators (optional comma list; empty = every component),          }
{         clear_source_library=true, sync_design_item_id=true.                 }
Function Gen_ClearSchSourceLibrary(Params : String; RequestId : String) : String;
Var
    SheetPath, DesigCsv, FlagStr, Desig, LibRef : String;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    DesigList : TStringList;
    ClearSrc, SyncId, WantAll : Boolean;
    Total, ClearedSrc, Synced : Integer;
    SrvDoc : IServerDocument;
Begin
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    DesigCsv := ExtractJsonValue(Params, 'designators');
    FlagStr := ExtractJsonValue(Params, 'clear_source_library');
    ClearSrc := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));
    FlagStr := ExtractJsonValue(Params, 'sync_design_item_id');
    SyncId := Not ((FlagStr = 'false') Or (FlagStr = 'False') Or (FlagStr = '0'));

    SchDoc := Nil;
    If SheetPath <> '' Then
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
    If SchDoc = Nil Then
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    DesigList := TStringList.Create;
    Try
        DesigList.CommaText := DesigCsv;
        WantAll := DesigList.Count = 0;

        Total := 0;
        ClearedSrc := 0;
        Synced := 0;

        SchBeginEdit(SchDoc, '');
        Try
            Iterator := SchDoc.SchIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iterator.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Comp := Obj;
                    Desig := '';
                    Try Desig := Comp.Designator.Text; Except End;
                    If WantAll Or (DesigList.IndexOf(Desig) >= 0) Then
                    Begin
                        Inc(Total);
                        If ClearSrc Then
                        Begin
                            Try
                                If Comp.SourceLibraryName <> '' Then
                                Begin
                                    SchBeginModify(Comp);
                                    Comp.SourceLibraryName := '';
                                    SchEndModify(Comp);
                                    Inc(ClearedSrc);
                                End;
                            Except NoteWriteFailed('clear SourceLibraryName'); End;
                        End;
                        If SyncId Then
                        Begin
                            Try
                                LibRef := Comp.LibReference;
                                If (LibRef <> '') And (Comp.DesignItemId <> LibRef) Then
                                Begin
                                    SchBeginModify(Comp);
                                    Comp.DesignItemId := LibRef;
                                    SchEndModify(Comp);
                                    Inc(Synced);
                                End;
                            Except NoteWriteFailed('sync DesignItemId'); End;
                        End;
                    End;
                    Obj := Iterator.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iterator);
            End;
        Finally
            SchEndEdit(SchDoc, 'Edit');
            SchDoc.GraphicallyInvalidate;
        End;
    Finally
        DesigList.Free;
    End;

    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"total":' + IntToStr(Total) +
        ',"cleared_source_library":' + IntToStr(ClearedSrc) +
        ',"synced_design_item_id":' + IntToStr(Synced) + '}');
End;


{ Give a placed part with no footprint model one, the way                }
{ Lib_LinkFootprint does on a symbol: AddSchImplementation, PCBLIB,      }
{ current, and one datafile link. The link's location is a FILE NAME or }
{ empty, never a full path, which wedged AD26 there.                     }
Function AddPlacedFootprintModel(Comp : ISch_Component; Name, LibName : String) : Boolean;
Var
    Impl : ISch_Implementation;
Begin
    Result := False;
    Impl := Nil;
    Try Impl := Comp.AddSchImplementation; Except End;
    If Impl = Nil Then Exit;
    Try Impl.ClearAllDatafileLinks; Except NoteWriteFailed('footprint model ClearAllDatafileLinks'); End;
    Try Impl.ModelName := Name; Except NoteWriteFailed('footprint model ModelName'); End;
    Try Impl.ModelType := cDocKind_PcbLib; Except NoteWriteFailed('footprint model ModelType'); End;
    Try Impl.IsCurrent := True; Except NoteWriteFailed('footprint model IsCurrent'); End;
    Try Impl.AddDataFileLink(Name, LibName, 'PCBLib'); Except NoteWriteFailed('footprint model library link'); End;
    Result := CurrentFootprintImpl(Comp) <> Nil;
End;

{ Everything that decides where a placed part comes from, as JSON. }
Function CompLinkJson(Comp : ISch_Component) : String;
Var
    Impl : ISch_Implementation;
    Link : ISch_ModelDatafileLink;
    S, FpName, FpLib : String;
    B : Boolean;
Begin
    Result := '';
    S := ''; Try S := Comp.LibReference; Except End;
    Result := Result + '"lib_ref":"' + EscapeJsonString(S) + '"';
    S := ''; Try S := Comp.SourceLibraryName; Except End;
    Result := Result + ',"source_library":"' + EscapeJsonString(S) + '"';
    S := ''; Try S := Comp.DesignItemId; Except End;
    Result := Result + ',"design_item_id":"' + EscapeJsonString(S) + '"';
    S := ''; Try S := Comp.LibraryIdentifier; Except End;
    Result := Result + ',"library_identifier":"' + EscapeJsonString(S) + '"';
    S := ''; Try S := Comp.DatabaseTableName; Except End;
    Result := Result + ',"database_table":"' + EscapeJsonString(S) + '"';
    B := False; Try B := Comp.UseDBTableName; Except End;
    Result := Result + ',"use_db_table_name":' + BoolToJsonStr(B);
    B := False; Try B := Comp.UseLibraryName; Except End;
    Result := Result + ',"use_library_name":' + BoolToJsonStr(B);
    FpName := '';
    FpLib := '';
    Impl := CurrentFootprintImpl(Comp);
    If Impl <> Nil Then
    Begin
        Try FpName := Impl.ModelName; Except End;
        Link := Nil;
        Try
            If Impl.DatafileLinkCount > 0 Then Link := Impl.DatafileLink[0];
        Except End;
        If Link <> Nil Then
        Begin
            Try FpLib := Link.Location; Except End;
        End;
    End;
    Result := Result + ',"footprint":"' + EscapeJsonString(FpName) + '"'
        + ',"footprint_library":"' + EscapeJsonString(FpLib) + '"';
End;

{ Point the current footprint model's datafile links at another PcbLib.  }
{ UseComponentLibrary off, so Altium takes the footprint from that file   }
{ rather than from the part's own integrated library; both members as    }
{ CompSourceLibReLinker.pas writes them.                                  }
Function SetPlacedFootprintLibrary(Comp : ISch_Component; LibName : String) : Boolean;
Var
    Impl : ISch_Implementation;
    Link : ISch_ModelDatafileLink;
    J, LinkCount : Integer;
    Back : String;
Begin
    Result := False;
    Impl := CurrentFootprintImpl(Comp);
    If Impl = Nil Then Exit;
    SchBeginModify(Impl);
    Try Impl.UseComponentLibrary := False; Except NoteWriteFailed('footprint model UseComponentLibrary'); End;
    LinkCount := 0;
    Try LinkCount := Impl.DatafileLinkCount; Except LinkCount := 0; End;
    For J := 0 To LinkCount - 1 Do
    Begin
        Link := Nil;
        Try Link := Impl.DatafileLink[J]; Except End;
        If Link <> Nil Then
        Begin
            Try Link.Location := LibName; Except { read back below } End;
        End;
    End;
    SchEndModify(Impl);
    Back := '';
    Try
        If Impl.DatafileLinkCount > 0 Then Back := Impl.DatafileLink[0].Location;
    Except End;
    Result := (LinkCount > 0) And SameText(Back, LibName);
End;

{..............................................................................}
{ Gen_RelinkComponents - point placed parts at another library, by property,  }
{ without Altium's dialog.                                                     }
{                                                                              }
{ A SchLib or IntLib part: SourceLibraryName is the file, LibReference the    }
{ symbol, DesignItemId the item (the symbol name), no database table. A DbLib }
{ part: SourceLibraryName is the DbLib, DatabaseTableName and UseDBTableName  }
{ name the table, DesignItemId is the row key. The footprint is the current   }
{ PCBLIB model's name and its datafile links' Location. Every member written  }
{ here is written the same way by CompSourceLibReLinker.pas or                }
{ XIA_Update_From_Database.pas; LibraryIdentifier is reported, never set,     }
{ because Altium derives it.                                                   }
{                                                                              }
{ A SchLib/IntLib target given as a full path is checked FIRST: the symbol    }
{ must be in it, or nothing changes (LIB_REF_NOT_FOUND, with names that are). }
{ Each part is read before and after, and a field that did not read back as  }
{ written is named in not_applied, so the reply never claims a link it did   }
{ not make. dry_run reports what would change and writes nothing.            }
{                                                                              }
{ Params: sheet_path (refused when given and not open), designators (comma    }
{ list, required), kind (schlib|intlib|dblib|''), library (path or name),     }
{ lib_ref, design_item_id, table, footprint, footprint_library, verify,       }
{ dry_run.                                                                     }
{..............................................................................}
Function Gen_RelinkComponents(Params : String; RequestId : String) : String;
Var
    SheetPath, DesigCsv, Kind, LibPath, LibName, LibRef, ItemId, Table : String;
    FpName, FpLib, FpLibName, Desig, Avail, Before, After, Missing, Items : String;
    FpBefore, FpAfter, S, WantItem : String;
    SchDoc : ISch_Document;
    Iterator : ISch_Iterator;
    Obj : ISch_GraphicalObject;
    Comp : ISch_Component;
    DesigList, Seen : TStringList;
    DryRun, Verify, Ok, B : Boolean;
    Total, Changed, Failed, I, ModelsAdded : Integer;
Begin
    ModelsAdded := 0;
    SheetPath := ExtractJsonValue(Params, 'sheet_path');
    DesigCsv := ExtractJsonValue(Params, 'designators');
    Kind := LowerCase(ExtractJsonValue(Params, 'kind'));
    LibPath := ExtractJsonValue(Params, 'library');
    LibRef := ExtractJsonValue(Params, 'lib_ref');
    ItemId := ExtractJsonValue(Params, 'design_item_id');
    Table := ExtractJsonValue(Params, 'table');
    FpName := ExtractJsonValue(Params, 'footprint');
    FpLib := ExtractJsonValue(Params, 'footprint_library');
    DryRun := ExtractJsonValue(Params, 'dry_run') = 'true';
    Verify := ExtractJsonValue(Params, 'verify') <> 'false';
    LibName := ExtractFileName(LibPath);
    FpLibName := ExtractFileName(FpLib);

    If DesigCsv = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'designators is required: name the parts to relink');
        Exit;
    End;
    If (Kind = 'dblib') And ((Table = '') Or (ItemId = '')) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'a database link needs table and design_item_id (the row key)');
        Exit;
    End;
    If (Kind = 'dblib') And (LibName = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM',
            'a database link needs library, the .DbLib');
        Exit;
    End;

    SchDoc := Nil;
    If SheetPath <> '' Then
    Begin
        Try SchDoc := SchServer.GetSchDocumentByPath(SheetPath); Except End;
        If SchDoc = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'SHEET_NOT_OPEN',
                'sheet_path is not an open schematic: ' + SheetPath
                + '. Nothing was changed; open it first');
            Exit;
        End;
    End
    Else
        SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHEMATIC',
            'No schematic document is active');
        Exit;
    End;

    If Verify And ((Kind = 'schlib') Or (Kind = 'intlib')) And (LibRef <> '')
        And (LibPath <> LibName) Then
    Begin
        If Not ResolveLibRef(LibPath, LibRef, Avail) Then
        Begin
            Result := BuildErrorResponse(RequestId, 'LIB_REF_NOT_FOUND',
                LibRef + ' is not in ' + LibPath + '. Nothing was changed. In it: '
                + Avail);
            Exit;
        End;
    End;

    DesigList := TStringList.Create;
    Seen := TStringList.Create;
    Items := '';
    Total := 0;
    Changed := 0;
    Failed := 0;
    Try
        DesigList.CommaText := DesigCsv;
        If Not DryRun Then SchBeginEdit(SchDoc, '');
        Try
            Iterator := SchDoc.SchIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
                Obj := Iterator.FirstSchObject;
                While Obj <> Nil Do
                Begin
                    Comp := Obj;
                    Desig := '';
                    Try Desig := Comp.Designator.Text; Except End;
                    If DesigList.IndexOf(Desig) >= 0 Then
                    Begin
                        Inc(Total);
                        Seen.Add(Desig);
                        Before := CompLinkJson(Comp);
                        Missing := '';
                        If Not DryRun Then
                        Begin
                            { The design item a SchLib/IntLib part re-matches by
                              is its symbol unless one is named. }
                            WantItem := ItemId;
                            If (WantItem = '') And (Kind <> 'dblib') And (Kind <> '') Then
                                WantItem := LibRef;
                            SchBeginModify(Comp);
                            Try
                                If LibName <> '' Then
                                Begin
                                    Try Comp.SourceLibraryName := LibName; Except NoteWriteFailed('relink SourceLibraryName'); End;
                                End;
                                If LibRef <> '' Then
                                Begin
                                    Try Comp.LibReference := LibRef; Except NoteWriteFailed('relink LibReference'); End;
                                End;
                                If Kind = 'dblib' Then
                                Begin
                                    Try Comp.DatabaseTableName := Table; Except NoteWriteFailed('relink DatabaseTableName'); End;
                                    Try Comp.UseDBTableName := True; Except NoteWriteFailed('relink UseDBTableName'); End;
                                    Try Comp.UseLibraryName := True; Except NoteWriteFailed('relink UseLibraryName'); End;
                                End;
                                If (Kind = 'schlib') Or (Kind = 'intlib') Then
                                Begin
                                    Try Comp.DatabaseTableName := ''; Except NoteWriteFailed('relink DatabaseTableName'); End;
                                    Try Comp.UseDBTableName := False; Except NoteWriteFailed('relink UseDBTableName'); End;
                                    Try Comp.UseLibraryName := True; Except NoteWriteFailed('relink UseLibraryName'); End;
                                End;
                                If WantItem <> '' Then
                                Begin
                                    Try Comp.DesignItemId := WantItem; Except NoteWriteFailed('relink DesignItemId'); End;
                                End;
                            Finally
                                SchEndModify(Comp);
                            End;
                            If FpName <> '' Then
                            Begin
                                { A part placed without a footprint has no
                                  model to repoint; it gets one. }
                                If CurrentFootprintImpl(Comp) = Nil Then
                                Begin
                                    SchBeginModify(Comp);
                                    AddPlacedFootprintModel(Comp, FpName, FpLibName);
                                    SchEndModify(Comp);
                                    Inc(ModelsAdded);
                                End;
                                If Not SetPlacedFootprintName(Comp, FpName, FpBefore, FpAfter) Then
                                    Missing := Missing + ',"footprint"';
                            End;
                            If FpLibName <> '' Then
                            Begin
                                If Not SetPlacedFootprintLibrary(Comp, FpLibName) Then
                                    Missing := Missing + ',"footprint_library"';
                            End;

                            { Read back each field that was asked for. }
                            S := '';
                            Try S := Comp.SourceLibraryName; Except End;
                            If (LibName <> '') And (Not SameText(S, LibName)) Then
                                Missing := Missing + ',"source_library"';
                            S := '';
                            Try S := Comp.LibReference; Except End;
                            If (LibRef <> '') And (S <> LibRef) Then
                                Missing := Missing + ',"lib_ref"';
                            If Kind = 'dblib' Then
                            Begin
                                S := '';
                                Try S := Comp.DatabaseTableName; Except End;
                                If S <> Table Then Missing := Missing + ',"database_table"';
                                B := False;
                                Try B := Comp.UseDBTableName; Except End;
                                If Not B Then Missing := Missing + ',"use_db_table_name"';
                            End;
                            S := '';
                            Try S := Comp.DesignItemId; Except End;
                            If (WantItem <> '') And (S <> WantItem) Then
                                Missing := Missing + ',"design_item_id"';
                        End;
                        After := CompLinkJson(Comp);
                        Ok := Missing = '';
                        If Ok And (Before <> After) Then Inc(Changed);
                        If Not Ok Then Inc(Failed);
                        If Missing <> '' Then Missing := Copy(Missing, 2, Length(Missing));
                        If Items <> '' Then Items := Items + ',';
                        Items := Items + '{"designator":"' + EscapeJsonString(Desig) + '"'
                            + ',"before":{' + Before + '}'
                            + ',"after":{' + After + '}'
                            + ',"not_applied":[' + Missing + ']}';
                    End;
                    Obj := Iterator.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iterator);
            End;
        Finally
            If Not DryRun Then
            Begin
                SchEndEdit(SchDoc, 'Edit');
                SchDoc.GraphicallyInvalidate;
            End;
        End;

        Missing := '';
        For I := 0 To DesigList.Count - 1 Do
            If Seen.IndexOf(DesigList[I]) < 0 Then
            Begin
                If Missing <> '' Then Missing := Missing + ',';
                Missing := Missing + '"' + EscapeJsonString(DesigList[I]) + '"';
            End;
    Finally
        DesigList.Free;
        Seen.Free;
    End;

    If (Not DryRun) And (Changed > 0) Then MarkDocDirtyByPath(SchDoc.DocumentName);

    Result := BuildSuccessResponse(RequestId,
        '{"success":' + BoolToJsonStr(Failed = 0)
        + ',"sheet_path":"' + EscapeJsonString(SchDoc.DocumentName) + '"'
        + ',"dry_run":' + BoolToJsonStr(DryRun)
        + ',"matched":' + IntToStr(Total)
        + ',"changed":' + IntToStr(Changed)
        + ',"failed":' + IntToStr(Failed)
        + ',"footprint_models_added":' + IntToStr(ModelsAdded)
        + ',"not_on_sheet":[' + Missing + ']'
        + ',"parts":[' + Items + ']}');
End;

{..............................................................................}
{ Command Handler - must be at end                                            }
{..............................................................................}

{..............................................................................}
{ Gen_StubPins - place a short wire stub + net label at each named pin. Pins   }
{ is a pipe-separated list of "designator,pin_number,label" records (label     }
{ optional, defaults to designator_pinnumber). The stub extends from the pin's }
{ electrical end outward along the pin orientation by stub_length_mils.        }
{..............................................................................}
Function Gen_StubPins(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    PinsStr, RecStr, Remaining, Token : String;
    Desig, PinNum, Lbl : String;
    PipePos, CommaPos, FieldIdx, Stubbed, Failed : Integer;
    StubLen : Double;
    Iter, PinIter : ISch_Iterator;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    PX, PY, EX, EY : Double;
    Orient : Integer;
    Found : Boolean;
    Wire : ISch_Wire;
    NetLabel : ISch_NetLabel;
    InfFont : Integer;
    StyleFound : Boolean;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC', 'No schematic document is active');
        Exit;
    End;

    PinsStr := ExtractJsonValue(Params, 'pins');
    If PinsStr = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'pins parameter required');
        Exit;
    End;
    StubLen := StrToFloatDef(ExtractJsonValue(Params, 'stub_length_mils'), 100);

    Stubbed := 0;
    Failed := 0;
    { Sheet convention once, applied to every stub label below. }
    StyleFound := InferNetLabelStyle(SchDoc, InfFont);
    SchBeginEdit(SchDoc, '');
    Try
        Remaining := PinsStr;
        While Length(Remaining) > 0 Do
        Begin
            PipePos := Pos('|', Remaining);
            If PipePos = 0 Then Begin RecStr := Remaining; Remaining := ''; End
            Else Begin RecStr := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End;
            If RecStr = '' Then Continue;

            Desig := ''; PinNum := ''; Lbl := '';
            FieldIdx := 0;
            While (RecStr <> '') And (FieldIdx <= 2) Do
            Begin
                CommaPos := Pos(',', RecStr);
                If CommaPos = 0 Then Begin Token := RecStr; RecStr := ''; End
                Else Begin Token := Copy(RecStr, 1, CommaPos - 1); RecStr := Copy(RecStr, CommaPos + 1, Length(RecStr)); End;
                Case FieldIdx Of
                    0: Desig := Token;
                    1: PinNum := Token;
                    2: Lbl := Token;
                End;
                FieldIdx := FieldIdx + 1;
            End;

            If (Desig = '') Or (PinNum = '') Then Begin Failed := Failed + 1; Continue; End;

            Found := False;
            PX := 0; PY := 0; Orient := 0;
            Iter := SchDoc.SchIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
                Comp := Iter.FirstSchObject;
                While (Comp <> Nil) And (Not Found) Do
                Begin
                    If Comp.Designator.Text = Desig Then
                    Begin
                        PinIter := Comp.SchIterator_Create;
                        Try
                            PinIter.AddFilter_ObjectSet(MkSet(ePin));
                            Pin := PinIter.FirstSchObject;
                            While (Pin <> Nil) And (Not Found) Do
                            Begin
                                If (Pin.Designator = PinNum) And PinShownOn(Comp, Pin) Then
                                Begin
                                    Try PX := CoordToMilsF(Pin.Location.X); Except End;
                                    Try PY := CoordToMilsF(Pin.Location.Y); Except End;
                                    Try Orient := Pin.Orientation; Except End;
                                    Found := True;
                                End;
                                Pin := PinIter.NextSchObject;
                            End;
                        Finally
                            Comp.SchIterator_Destroy(PinIter);
                        End;
                    End;
                    Comp := Iter.NextSchObject;
                End;
            Finally
                SchDoc.SchIterator_Destroy(Iter);
            End;

            If Not Found Then Begin Failed := Failed + 1; Continue; End;

            EX := PX; EY := PY;
            If Orient = 0 Then EX := PX + StubLen
            Else If Orient = 1 Then EY := PY + StubLen
            Else If Orient = 2 Then EX := PX - StubLen
            Else If Orient = 3 Then EY := PY - StubLen;

            Wire := SchServer.SchObjectFactory(eWire, eCreate_Default);
            If Wire <> Nil Then
            Begin
                Wire.Location := Point(MilsToCoordF(PX), MilsToCoordF(PY));
                Wire.InsertVertex := 1;
                Wire.SetState_Vertex(1, Point(MilsToCoordF(PX), MilsToCoordF(PY)));
                Wire.InsertVertex := 2;
                Wire.SetState_Vertex(2, Point(MilsToCoordF(EX), MilsToCoordF(EY)));
                SchDoc.RegisterSchObjectInContainer(Wire);
                SchRegisterObject(SchDoc, Wire);
            End;

            If Lbl = '' Then Lbl := Desig + '_' + PinNum;
            NetLabel := SchServer.SchObjectFactory(eNetLabel, eCreate_Default);
            If NetLabel <> Nil Then
            Begin
                NetLabel.Text := Lbl;
                NetLabel.Location := Point(MilsToCoordF(EX), MilsToCoordF(EY));
                If StyleFound Then
                Begin
                    Try NetLabel.FontId := InfFont; Except NoteWriteFailed('NetLabel.FontId'); End;
                End;
                NetLabel.Color := NET_LABEL_COLOR;
                SchDoc.RegisterSchObjectInContainer(NetLabel);
                SchRegisterObject(SchDoc, NetLabel);
            End;

            Stubbed := Stubbed + 1;
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
        SchDoc.GraphicallyInvalidate;
    End;

    { A WRITE THAT DOES NOT DIRTY THE DOCUMENT DID NOT HAPPEN, as far as
      the rest of Altium is concerned. SmartCompile skips its recompile
      while the project still looks clean, so a later ERC or netlist read
      answers from the model as it stood BEFORE this call, and a deferred
      save has nothing to flush. Reported as NoERC markers that were in
      the file and still listed as violations until the project was
      reopened. }
    If SchDoc <> Nil Then MarkDocDirtyByPath(SchDoc.DocumentName);
    Result := BuildSuccessResponse(RequestId,
        '{"stubbed":' + IntToStr(Stubbed) + ',"failed":' + IntToStr(Failed) + '}');
End;

{..............................................................................}
{ Gen_SetNetTie - mark a placed schematic component as a net tie. A net tie     }
{ component shorts the nets landing on its pins for routing while keeping them   }
{ logically separate. mode 'bom' keeps it in the BOM; 'nobom' (default) hides    }
{ it and lets synchronization maintain it. Sets ISch_Component.ComponentKind.    }
{..............................................................................}
Function Gen_SetNetTie(Params : String; RequestId : String) : String;
Var
    SchDoc : ISch_Document;
    Iter : ISch_Iterator;
    Comp, Target : ISch_Component;
    Desig, ModeStr, KindStr : String;
Begin
    SchDoc := SchServer.GetCurrentSchDocument;
    If SchDoc = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_SCHDOC', 'No schematic document is active');
        Exit;
    End;

    Desig := ExtractJsonValue(Params, 'designator');
    If Desig = '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAM', 'designator parameter required');
        Exit;
    End;
    ModeStr := ExtractJsonValue(Params, 'mode');
    If ModeStr = '' Then ModeStr := 'nobom';

    Target := Nil;
    Iter := SchDoc.SchIterator_Create;
    Try
        Iter.AddFilter_ObjectSet(MkSet(eSchComponent));
        Comp := Iter.FirstSchObject;
        While (Comp <> Nil) And (Target = Nil) Do
        Begin
            If Comp.Designator.Text = Desig Then Target := Comp;
            Comp := Iter.NextSchObject;
        End;
    Finally
        SchDoc.SchIterator_Destroy(Iter);
    End;

    If Target = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NOT_FOUND', 'Component not found: ' + Desig);
        Exit;
    End;

    SchBeginEdit(SchDoc, '');
    Try
        If ModeStr = 'bom' Then
        Begin
            Target.ComponentKind := eComponentKind_NetTie_BOM;
            KindStr := 'NetTie_BOM';
        End
        Else
        Begin
            Target.ComponentKind := eComponentKind_NetTie_NoBOM;
            KindStr := 'NetTie_NoBOM';
        End;
    Finally
        SchEndEdit(SchDoc, 'Edit');
    End;
    Try SchDoc.GraphicallyInvalidate; Except End;

    Result := BuildSuccessResponse(RequestId,
        '{"designator":"' + EscapeJsonString(Desig) + '","component_kind":"'
        + KindStr + '"}');
End;

{..............................................................................}
{ Gen_ExplainPin                                                              }
{                                                                              }
{ Answers "why is this pin on that net?" -- the question proj_get_nets can    }
{ state a verdict on but never justify. Given a designator and pin number,    }
{ reports the pin's ROOT (Pin.Location, body side) and its CONNECTION point   }
{ (Location + PinLength along Orientation), then lists every schematic object }
{ sitting on each of those two points.                                        }
{                                                                              }
{ Reading the result:                                                         }
{  - objects under "at_connection" are what actually drive the pin's net.     }
{    Two net labels with different text there is a short.                     }
{  - objects under "at_root" are electrically INERT. A net label there is the }
{    classic "sheet looks wired but the pin floats" bug.                      }
{  - an empty "at_connection" with a populated "at_root" means the label      }
{    needs to move by PinLength along the pin's orientation.                  }
{                                                                              }
{ Params: designator (required), pin (required, the pin NUMBER not name).     }
{..............................................................................}

Function Gen_ExplainPin(Params : String; RequestId : String) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    DocI : Integer;
    Document : IDocument;
    Sheet : ISch_Document;
    CompIter, PinIter, SpatIter : ISch_Iterator;
    CompObj, PinObj, Hit : ISch_GraphicalObject;
    Comp : ISch_Component;
    Pin : ISch_Pin;
    WantDesig, WantPin, DocKind, SheetName : String;
    FoundSheet, PinName, PinNumStr : String;
    Loc : TLocation;
    RX, RY, CX, CY, PinLen, PinOrient : Integer;
    Tol, ObjId : Integer;
    ConnJson, RootJson, Detail : String;
    FirstC, FirstR, Found, HitOk : Boolean;
    Pass : Integer;
    PX, PY, HitX, HitY, EndX, EndY : Integer;
    OtherLen, OtherOri, VtxI, VtxN : Integer;
    ItemText : String;
    V1, V2 : TLocation;
    SheetPathParam, ProjectPathParam, FoundPath, ProjectFile, CandPath : String;
    Paths : TStringList;
    NExplicit : Integer;
    CurDoc : ISch_Document;
    ActiveInProject : Boolean;
Begin
    ActiveInProject := False;
    WantDesig := ExtractJsonValue(Params, 'designator');
    WantPin := ExtractJsonValue(Params, 'pin');
    SheetPathParam := ExtractJsonValue(Params, 'sheet_path');
    ProjectPathParam := ExtractJsonValue(Params, 'project_path');
    If (WantDesig = '') Or (WantPin = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_PARAMS',
            'designator and pin are both required');
        Exit;
    End;

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_WORKSPACE', 'No workspace');
        Exit;
    End;
    If ProjectPathParam <> '' Then
        Project := FindProjectByPath(Workspace, ProjectPathParam)
    Else
        Project := Workspace.DM_FocusedProject;
    If (Project = Nil) And ((ProjectPathParam <> '') Or (SheetPathParam = '')) Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PROJECT',
            'No project found: ' + ProjectPathParam);
        Exit;
    End;

    { WHERE TO LOOK, IN ORDER. Walking only the focused project answered for
      that project's U1 while the caller was looking at another sheet with
      its own U1 (issue #94). A named sheet is searched alone; otherwise the
      active sheet comes first, then the project's sheets. }
    Paths := TStringList.Create;
    NExplicit := 0;
    If SheetPathParam <> '' Then
    Begin
        Paths.Add(SheetPathParam);
        NExplicit := 1;
    End
    Else
    Begin
        If ProjectPathParam = '' Then
        Begin
            CurDoc := Nil;
            Try CurDoc := SchServer.GetCurrentSchDocument; Except End;
            If CurDoc <> Nil Then
            Begin
                If CurDoc.ObjectId = eSheet Then
                Begin
                    Paths.Add(CurDoc.DocumentName);
                    NExplicit := 1;
                End;
            End;
        End;
        For DocI := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Document := Nil;
            Try Document := Project.DM_LogicalDocuments(DocI); Except End;
            If Document = Nil Then Continue;
            DocKind := '';
            Try DocKind := Document.DM_DocumentKind; Except End;
            If DocKind <> 'SCH' Then Continue;
            CandPath := Document.DM_FullPath;
            If (NExplicit = 1) And (UpperCase(CandPath) = UpperCase(Paths.Strings[0])) Then
            Begin
                ActiveInProject := True;
                Continue;
            End;
            Paths.Add(CandPath);
        End;
    End;

    Found := False;
    FoundSheet := '';
    PinName := '';
    PinNumStr := '';
    RX := 0; RY := 0; CX := 0; CY := 0;
    PinLen := 0; PinOrient := 0;
    Tol := MilsToCoord(1);
    Sheet := Nil;

    For DocI := 0 To Paths.Count - 1 Do
    Begin
        If Found Then Break;
        CandPath := Paths.Strings[DocI];
        Sheet := Nil;
        If DocI < NExplicit Then
        Begin
            Try Sheet := SchServer.GetSchDocumentByPath(CandPath); Except End;
        End
        Else
            Sheet := ProjectSheet(CandPath);
        If Sheet = Nil Then Continue;
        SheetName := ExtractFileName(CandPath);

        CompIter := Sheet.SchIterator_Create;
        If CompIter = Nil Then Continue;
        Try
            CompIter.AddFilter_ObjectSet(MkSet(eSchComponent));
            CompObj := CompIter.FirstSchObject;
            While (CompObj <> Nil) And (Not Found) Do
            Begin
                Try
                    Comp := CompObj;
                    If Comp.Designator.Text = WantDesig Then
                    Begin
                        PinIter := Comp.SchIterator_Create;
                        If PinIter <> Nil Then
                        Begin
                            Try
                                PinIter.AddFilter_ObjectSet(MkSet(ePin));
                                PinObj := PinIter.FirstSchObject;
                                While (PinObj <> Nil) And (Not Found) Do
                                Begin
                                    Try
                                        Pin := PinObj;
                                        PinNumStr := '';
                                        Try PinNumStr := Pin.Designator; Except End;
                                        If (PinNumStr = WantPin) And PinShownOn(Comp, Pin) Then
                                        Begin
                                            Found := True;
                                            FoundSheet := SheetName;
                                            FoundPath := CandPath;
                                            Try PinName := Pin.Name; Except End;
                                            Loc := Pin.GetState_Location;
                                            RX := Loc.X;
                                            RY := Loc.Y;
                                            Try PinLen := Pin.PinLength; Except End;
                                            Try PinOrient := Pin.Orientation; Except End;
                                            CX := RX;
                                            CY := RY;
                                            If PinOrient = 0 Then CX := RX + PinLen
                                            Else If PinOrient = 1 Then CY := RY + PinLen
                                            Else If PinOrient = 2 Then CX := RX - PinLen
                                            Else If PinOrient = 3 Then CY := RY - PinLen;
                                        End;
                                    Except End;
                                    If Not Found Then PinObj := PinIter.NextSchObject;
                                End;
                            Finally
                                Comp.SchIterator_Destroy(PinIter);
                            End;
                        End;
                    End;
                Except End;
                If Not Found Then CompObj := CompIter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(CompIter);
        End;
    End;

    { Where the pin was found, for the reply: a sheet named by the caller,
      the active sheet, or one of the project's sheets. }
    ProjectFile := '';
    If Found Then
    Begin
        If SheetPathParam <> '' Then
        Begin
            ProjectFile := 'named_sheet';
        End
        Else If (NExplicit = 1) And (UpperCase(FoundPath) = UpperCase(Paths.Strings[0])) Then
        Begin
            If ActiveInProject Then
                ProjectFile := 'active_sheet_in_project'
            Else
                ProjectFile := 'active_sheet_outside_project';
        End
        Else
        Begin
            ProjectFile := 'project';
        End;
    End;
    Paths.Free;

    If Not Found Then
    Begin
        Result := BuildErrorResponse(RequestId, 'PIN_NOT_FOUND',
            'No pin ' + WantPin + ' on component ' + WantDesig);
        Exit;
    End;

    ConnJson := '';
    RootJson := '';
    FirstC := True;
    FirstR := True;

    { Pass 0 = connection point (live), Pass 1 = root (inert). }
    For Pass := 0 To 1 Do
    Begin
        If Pass = 0 Then
        Begin
            PX := CX;
            PY := CY;
        End
        Else
        Begin
            PX := RX;
            PY := RY;
        End;

        SpatIter := Sheet.SchIterator_Create;
        If SpatIter = Nil Then Continue;
        Try
            SpatIter.AddFilter_ObjectSet(
                MkSet(eNetLabel, eWire, ePowerObject, eJunction, ePin, ePort));
            { AddFilter_Area matches BOUNDING BOXES. Net-label text extends }
            { hundreds of mils from Location, so a 1-mil square still hits  }
            { labels whose Location is far away. Always re-check Location   }
            { (or pin electrical end, or wire vertices) after the filter.   }
            SpatIter.AddFilter_Area(PX - Tol, PY - Tol, PX + Tol, PY + Tol);
            Hit := SpatIter.FirstSchObject;
            While Hit <> Nil Do
            Begin
                Try
                    ObjId := Hit.ObjectId;
                    ItemText := '';
                    HitOk := False;
                    HitX := 0;
                    HitY := 0;
                    If (ObjId = eNetLabel) Or (ObjId = ePowerObject) Or
                       (ObjId = ePort) Or (ObjId = eJunction) Then
                    Begin
                        Loc := Hit.GetState_Location;
                        HitX := Loc.X;
                        HitY := Loc.Y;
                        HitOk := CoordWithinTol(HitX, PX, Tol) And
                                 CoordWithinTol(HitY, PY, Tol);
                    End
                    Else If ObjId = ePin Then
                    Begin
                        Loc := Hit.GetState_Location;
                        HitX := Loc.X;
                        HitY := Loc.Y;
                        OtherLen := 0;
                        OtherOri := 0;
                        Try OtherLen := Hit.PinLength; Except End;
                        Try OtherOri := Hit.Orientation; Except End;
                        EndX := HitX;
                        EndY := HitY;
                        If OtherOri = 0 Then EndX := HitX + OtherLen
                        Else If OtherOri = 1 Then EndY := HitY + OtherLen
                        Else If OtherOri = 2 Then EndX := HitX - OtherLen
                        Else If OtherOri = 3 Then EndY := HitY - OtherLen;
                        If CoordWithinTol(EndX, PX, Tol) And
                           CoordWithinTol(EndY, PY, Tol) Then
                        Begin
                            HitOk := True;
                            HitX := EndX;
                            HitY := EndY;
                        End
                        Else If CoordWithinTol(HitX, PX, Tol) And
                                CoordWithinTol(HitY, PY, Tol) Then
                            HitOk := True;
                    End
                    Else If ObjId = eWire Then
                    Begin
                        VtxN := 0;
                        Try VtxN := Hit.GetState_VerticesCount; Except End;
                        VtxI := 1;
                        While (VtxI < VtxN) And (Not HitOk) Do
                        Begin
                            Try
                                V1 := Hit.GetState_Vertex(VtxI);
                                V2 := Hit.GetState_Vertex(VtxI + 1);
                                If PointNearSegment(PX, PY, V1.X, V1.Y,
                                    V2.X, V2.Y, Tol) Then
                                Begin
                                    HitOk := True;
                                    HitX := PX;
                                    HitY := PY;
                                End;
                            Except End;
                            VtxI := VtxI + 1;
                        End;
                    End;

                    If HitOk Then
                    Begin
                        If ObjId = eNetLabel Then
                        Begin
                            Try ItemText := Hit.Text; Except End;
                            Detail := JsonStr('kind', 'net_label') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else If ObjId = ePowerObject Then
                        Begin
                            Try ItemText := Hit.Text; Except End;
                            Detail := JsonStr('kind', 'power_object') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else If ObjId = ePort Then
                        Begin
                            Try ItemText := Hit.Name; Except End;
                            Detail := JsonStr('kind', 'port') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else If ObjId = eWire Then
                            Detail := JsonStr('kind', 'wire') + ',' +
                                      JsonStr('text', '')
                        Else If ObjId = eJunction Then
                            Detail := JsonStr('kind', 'junction') + ',' +
                                      JsonStr('text', '')
                        Else If ObjId = ePin Then
                        Begin
                            Try ItemText := Hit.Designator; Except End;
                            Detail := JsonStr('kind', 'pin') + ',' +
                                      JsonStr('text', ItemText);
                        End
                        Else
                            Detail := JsonStr('kind', 'other') + ',' +
                                      JsonStr('text', '');
                        Detail := Detail + ',' +
                                  JsonFloat('x_mils', CoordToMilsF(HitX)) + ',' +
                                  JsonFloat('y_mils', CoordToMilsF(HitY));

                        If Pass = 0 Then
                        Begin
                            If Not FirstC Then ConnJson := ConnJson + ',';
                            FirstC := False;
                            ConnJson := ConnJson + JsonObj(Detail);
                        End
                        Else
                        Begin
                            If Not FirstR Then RootJson := RootJson + ',';
                            FirstR := False;
                            RootJson := RootJson + JsonObj(Detail);
                        End;
                    End;
                Except End;
                Hit := SpatIter.NextSchObject;
            End;
        Finally
            Sheet.SchIterator_Destroy(SpatIter);
        End;
    End;

    Result := BuildSuccessResponse(RequestId,
        JsonObj(
            JsonStr('designator', WantDesig) + ',' +
            JsonStr('pin', WantPin) + ',' +
            JsonStr('pin_name', PinName) + ',' +
            JsonStr('sheet', FoundSheet) + ',' +
            JsonStr('sheet_path', FoundPath) + ',' +
            JsonStr('found_on', ProjectFile) + ',' +
            JsonInt('orientation', PinOrient) + ',' +
            JsonFloat('pin_length_mils', CoordToMilsF(PinLen)) + ',' +
            JsonFloat('root_x_mils', CoordToMilsF(RX)) + ',' +
            JsonFloat('root_y_mils', CoordToMilsF(RY)) + ',' +
            JsonFloat('connect_x_mils', CoordToMilsF(CX)) + ',' +
            JsonFloat('connect_y_mils', CoordToMilsF(CY)) + ',' +
            JsonRaw('at_connection', '[' + ConnJson + ']') + ',' +
            JsonRaw('at_root', '[' + RootJson + ']')
        ));
End;


Function HandleGenericCommand(Action : String; Params : String; RequestId : String) : String;
Begin
    Case Action Of
        'query_objects':    Result := Gen_QueryObjects(Params, RequestId);
        'explain_pin':      Result := Gen_ExplainPin(Params, RequestId);
        'modify_objects':   Result := Gen_ModifyObjects(Params, RequestId);
        'create_object':    Result := Gen_CreateObject(Params, RequestId);
        'delete_objects':   Result := Gen_DeleteObjects(Params, RequestId);
        'batch_modify':     Result := Gen_BatchModify(Params, RequestId);
        'run_process':      Result := Gen_RunProcess(Params, RequestId);
        'get_font_spec':    Result := Gen_GetFontSpec(Params, RequestId);
        'get_font_id':      Result := Gen_GetFontId(Params, RequestId);
        'select_objects':   Result := Gen_SelectObjects(Params, RequestId);
        'deselect_all':     Result := Gen_DeselectAll(RequestId);
        'zoom':             Result := Gen_Zoom(Params, RequestId);
        'run_erc':          Result := Gen_RunERC(Params, RequestId);
        'get_sch_geometry': Result := Gen_GetSchGeometry(Params, RequestId);
        'get_pcb_geometry': Result := Gen_GetPcbGeometry(Params, RequestId);
        'highlight_net':    Result := Gen_HighlightNet(Params, RequestId);
        'clear_highlights': Result := Gen_ClearHighlights(RequestId);
        'crossref_net':     Result := Gen_CrossRefNet(Params, RequestId);
        'add_sheet':        Result := Gen_AddSheet(Params, RequestId);
        'delete_sheet':     Result := Gen_DeleteSheet(Params, RequestId);
        'zoom_to_xy':       Result := Gen_ZoomToXY(Params, RequestId);
        'switch_view':      Result := Gen_SwitchView(Params, RequestId);
        'measure_distance': Result := Gen_MeasureDistance(Params, RequestId);
        'get_erc_violations': Result := Gen_GetErcViolations(Params, RequestId);
        'mirror_component': Result := Gen_MirrorSchComponent(Params, RequestId);
        'refresh_document': Result := Gen_RefreshDocument(RequestId);
        'get_unconnected_pins': Result := Gen_GetUnconnectedPins(Params, RequestId);
        'place_wire':       Result := Gen_PlaceWire(Params, RequestId);
        'place_bus':        Result := Gen_PlaceBus(Params, RequestId);
        'place_directive':  Result := Gen_PlaceDirective(Params, RequestId);
        'get_directives':   Result := Gen_GetDirectives(Params, RequestId);
        'place_compile_mask': Result := Gen_PlaceCompileMask(Params, RequestId);
        'place_blanket':    Result := Gen_PlaceBlanket(Params, RequestId);
        'get_blankets':     Result := Gen_GetBlankets(Params, RequestId);
        'place_rectangle':  Result := Gen_PlaceRectangle(Params, RequestId);
        'place_line':       Result := Gen_PlaceLine(Params, RequestId);
        'place_note':       Result := Gen_PlaceNote(Params, RequestId);
        'place_sheet_symbol': Result := Gen_PlaceSheetSymbol(Params, RequestId);
        'place_sheet_entry': Result := Gen_PlaceSheetEntry(Params, RequestId);
        'place_bus_entry':   Result := Gen_PlaceBusEntry(Params, RequestId);
        'get_harness':       Result := Gen_GetHarness(Params, RequestId);
        'place_harness_connectors': Result := Gen_PlaceHarnessConnectors(Params, RequestId);
        'place_harness_wires': Result := Gen_PlaceHarnessWires(Params, RequestId);
        'set_sheet_size':    Result := Gen_SetSheetSize(Params, RequestId);
        'sheet_options':     Result := Gen_SheetOptions(Params, RequestId);
        'sheet_template':    Result := Gen_SheetTemplate(Params, RequestId);
        'place_sch_component_from_library': Result := Gen_PlaceSchComponentFromLibrary(Params, RequestId);
        'set_sch_component_parameters': Result := Gen_SetSchComponentParameters(Params, RequestId);
        'get_sch_component_pins': Result := Gen_GetSchComponentPins(Params, RequestId);
        'place_net_label':  Result := Gen_PlaceNetLabel(Params, RequestId);
        'autoposition_labels': Result := Gen_AutopositionLabels(Params, RequestId);
        'stub_pins':        Result := Gen_StubPins(Params, RequestId);
        'set_net_tie':      Result := Gen_SetNetTie(Params, RequestId);
        'place_port':       Result := Gen_PlacePort(Params, RequestId);
        'place_power_port': Result := Gen_PlacePowerPort(Params, RequestId);
        'get_sheet_parameters': Result := Gen_GetSheetParameters(Params, RequestId);
        'copy_objects':     Result := Gen_CopyObjects(Params, RequestId);
        'get_object_count': Result := Gen_GetObjectCount(Params, RequestId);
        'place_no_erc':     Result := Gen_PlaceNoERC(Params, RequestId);
        'place_junction':   Result := Gen_PlaceJunction(Params, RequestId);
        'place_junctions':  Result := Gen_PlaceJunctions(Params, RequestId);
        'get_document_info': Result := Gen_GetDocumentInfo(Params, RequestId);
        'get_sheet_frame': Result := Gen_GetSheetFrame(Params, RequestId);
        'shift_sheet':     Result := Gen_ShiftSheet(Params, RequestId);
        'set_grid':         Result := Gen_SetGrid(Params, RequestId);
        'sch_editor_options': Result := Gen_SchEditorOptions(Params, RequestId);
        'optimize_wires':   Result := Gen_OptimizeWires(Params, RequestId);
        'reference_zones':  Result := Gen_ReferenceZones(Params, RequestId);
        'set_sch_units':    Result := Gen_SetSchUnits(Params, RequestId);
        'place_image':      Result := Gen_PlaceImage(Params, RequestId);
        'replace_component': Result := Gen_ReplaceComponent(Params, RequestId);
        'clear_sch_source_library': Result := Gen_ClearSchSourceLibrary(Params, RequestId);
        'relink_components': Result := Gen_RelinkComponents(Params, RequestId);
        'get_constraint_groups':      Result := Gen_GetConstraintGroups(Params, RequestId);
        'place_harness_connector':    Result := Gen_PlaceHarnessConnector(Params, RequestId);
        'place_signal_harness':       Result := Gen_PlaceSignalHarness(Params, RequestId);
        'place_cross_sheet_connector': Result := Gen_PlaceCrossSheetConnector(Params, RequestId);
        'place_text_frame': Result := Gen_PlaceTextFrame(Params, RequestId);
        'increment_designators': Result := Gen_IncrementDesignators(Params, RequestId);
        'toggle_pin_visibility': Result := Gen_TogglePinVisibility(Params, RequestId);
        'set_component_part_id':      Result := Gen_SetComponentPartId(Params, RequestId);
        'set_component_unique_id':    Result := Gen_SetComponentUniqueId(Params, RequestId);
        'replicate_sch_component':    Result := Gen_ReplicateSchComponent(Params, RequestId);
        'place_probe':                Result := Gen_PlaceProbe(Params, RequestId);
        'get_probes':                 Result := Gen_GetProbes(Params, RequestId);
        'add_datafile_link':          Result := Gen_AddDatafileLink(Params, RequestId);
        'get_simulation_readiness':   Result := Gen_GetSimulationReadiness(Params, RequestId);
        'attach_spice_primitive':     Result := Gen_AttachSpicePrimitive(Params, RequestId);
        'attach_spice_model':         Result := Gen_AttachSpiceModel(Params, RequestId);
        'run_simulation':             Result := Gen_RunSimulation(Params, RequestId);
        'run_spice_deck':             Result := Gen_RunSpiceDeck(Params, RequestId);
        'simcfg_document':            Result := Gen_SimCfgDocument(Params, RequestId);
        'batch_create':               Result := Gen_BatchCreate(Params, RequestId);
        'batch_delete':               Result := Gen_BatchDelete(Params, RequestId);
        'place_wires':                Result := Gen_PlaceWires(Params, RequestId);
        'place_net_labels':           Result := Gen_PlaceNetLabels(Params, RequestId);
        'place_power_ports':          Result := Gen_PlacePowerPorts(Params, RequestId);
        'get_sch_doc_pins':           Result := Gen_GetSchDocPins(Params, RequestId);
        'set_sch_components_parameters': Result := Gen_SetSchComponentsParameters(Params, RequestId);
        'set_sch_text_positions':      Result := Gen_SetSchTextPositions(Params, RequestId);
        'place_sch_components_from_library': Result := Gen_PlaceSchComponentsFromLibrary(Params, RequestId);
        'place_sch_vault_components': Result := Gen_PlaceSchVaultComponents(Params, RequestId);
        'attach_spice_primitives':    Result := Gen_AttachSpicePrimitivesBatch(Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION', 'Unknown generic action: ' + Action);
    End;
End;

{ UnitStamp_Generic - which build of Generic.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_Generic(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
