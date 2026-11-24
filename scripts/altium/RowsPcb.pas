{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ RowsPcb.pas - rows kinds of a board: track and arc (docs/ROWS_CONTRACT.md).  }
{                                                                              }
{ The FOCUSED PcbDoc only. GetPCBBoardAnywhere is never used: with another    }
{ document in front it answers from whichever board a project walk reaches.   }
{ PCBServer is touched only once the focused document is a PCB: referencing  }
{ it before any board was opened raises an uncatchable modal (Main.pas).      }
{                                                                              }
{ THE USE CASE: every track of ONE net, changed only where it has a given     }
{ width (a BGA breakout keeps its neck-downs). The narrowing is done in        }
{ Altium, by one route per read, chosen from the scope:                       }
{   net        the net own group (as CollectPCBMatches), plus the copper of   }
{              every pour on the net when poured=include or only;           }
{   component  designator / pattern / owner=component: each matching         }
{              component, its own group;                                     }
{   spatial    bounds: a spatial search of that box;                         }
{   board      the board walk of the kind, narrowed to one layer (a V7 set    }
{              past Mechanical16, as NarrowPCBWalk) and to free primitives     }
{              (eProcessFree) when owner=free and pours are left out.         }
{ Every scope condition is ALSO checked on every object a route hands out,   }
{ so a route can only save time, never change the answer.                     }
{                                                                              }
{ POURED COPPER (a hatched pour is made of tracks and arcs) is noise for a    }
{ routing tool: poured=exclude (the default) leaves it out, include keeps it, }
{ only keeps nothing else. owner names it free (it is in no component).       }
{                                                                              }
{ Coordinates are ABSOLUTE board coordinates (not from the board origin), in }
{ the call unit, as pcb.get_layout_model and the generic X / Y give them.    }
{..............................................................................}

{ The track table. layer = the internal name (TopLayer, MidLayer2), the one  }
{ a where can rely on; layer_name = the stack user name.                      }
Function RowsPcbTrackSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|owner:s:r|designator:s:r|pattern:s:r|net:s:r'
        + '|layer:s:w|layer_name:s:r'
        + '|x1:l:w|y1:l:w|x2:l:w|y2:l:w|width:l:w|length:l:r'
        + '|poured:b:r|keepout:b:r|locked:b:r|selected:b:r';
End;

{ The arc table. cx / cy / radius / start_angle / end_angle as Altium keeps   }
{ them (angles in degrees, counter-clockwise from start to end); x1 / y1 and  }
{ x2 / y2 are the two ends, worked out, read only.                           }
Function RowsPcbArcSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|owner:s:r|designator:s:r|pattern:s:r|net:s:r'
        + '|layer:s:w|layer_name:s:r'
        + '|cx:l:w|cy:l:w|radius:l:w|start_angle:a:w|end_angle:a:w|width:l:w'
        + '|x1:l:r|y1:l:r|x2:l:r|y2:l:r|length:l:r'
        + '|poured:b:r|keepout:b:r|locked:b:r|selected:b:r';
End;

Function RowsPcbSpec(Kind : String) : String;
Begin
    If Kind = 'arc' Then Result := RowsPcbArcSpec(0) Else Result := RowsPcbTrackSpec(0);
End;

{..............................................................................}
{ Pure helpers (lifted into the Free Pascal test).                             }
{..............................................................................}

{ RowsArcEnd - an end of an arc: the centre plus the radius at Angle. }
Function RowsArcEnd(Centre : Double; Radius : Double; Angle : Double; WantY : Boolean) : Double;
Begin
    If WantY Then Result := Centre + Radius * Sin(Angle * 3.14159265358979 / 180.0)
    Else Result := Centre + Radius * Cos(Angle * 3.14159265358979 / 180.0);
End;

{ RowsAngleSame - two wire angles (1/10000 degree) the same point on the   }
{ circle: 0 and 3600000 alike, otherwise exact.                            }
Function RowsAngleSame(Have : String; Want : String) : Boolean;
Begin
    Result := False;
    If (Not RowsIntStr(Trim(Have))) Or (Not RowsIntStr(Trim(Want))) Then Exit;
    Result := ((StrToInt(Trim(Have)) - StrToInt(Trim(Want))) Mod 3600000) = 0;
End;

{ RowsBoxesTouch - whether two boxes overlap or touch. }
Function RowsBoxesTouch(L1, B1, R1, T1, L2, B2, R2, T2 : Integer) : Boolean;
Begin
    Result := (L1 <= R2) And (R1 >= L2) And (B1 <= T2) And (T1 >= B2);
End;

{ RowsParseBounds - x1,y1,x2,y2 in the call unit as a box in coordinates,  }
{ corners in any order. '' when fine, else the problem.                     }
Function RowsParseBounds(S : String; Units : String; Var L : Integer; Var B : Integer;
    Var R : Integer; Var T : Integer) : String;
Var
    Rest, Item : String;
    P, N, V, X1, Y1, X2, Y2 : Integer;
Begin
    Result := '';
    L := 0;  B := 0;  R := 0;  T := 0;
    Rest := S;
    N := 0;
    While Rest <> '' Do
    Begin
        P := Pos(',', Rest);
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
        Item := Trim(Item);
        If (Not RowsIntStr(Item)) Or (N >= 4) Then
        Begin
            Result := 'bounds takes four whole numbers x1,y1,x2,y2, not "' + S + '"';
            Exit;
        End;
        V := RowsCoord(Item, Units);
        Case N Of
            0: X1 := V;
            1: Y1 := V;
            2: X2 := V;
            3: Y2 := V;
        End;
        Inc(N);
    End;
    If N <> 4 Then
    Begin
        Result := 'bounds takes four numbers x1,y1,x2,y2, not "' + S + '"';
        Exit;
    End;
    L := X1;  R := X2;
    If L > R Then Begin L := X2; R := X1; End;
    B := Y1;  T := Y2;
    If B > T Then Begin B := Y2; T := Y1; End;
End;

{ RowsSplitNames - a comma list as upper-case names, each once, sorted for  }
{ a binary IndexOf. The caller frees the list.                              }
Function RowsSplitNames(S : String) : TStringList;
Var
    Rest, Item : String;
    P : Integer;
Begin
    Result := TStringList.Create;
    Result.Sorted := True;
    Rest := S;
    While Rest <> '' Do
    Begin
        P := Pos(',', Rest);
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
        Item := UpperCase(Trim(Item));
        If (Item <> '') And (Result.IndexOf(Item) < 0) Then Result.Add(Item);
    End;
End;

{ RowsPcbScopeProblem - owner as free / component / all ('' = all), poured }
{ as exclude / include / only ('' = exclude). '' when both are fine.        }
Function RowsPcbScopeProblem(Var OwnerWant : String; Var PouredWant : String) : String;
Begin
    Result := '';
    OwnerWant := LowerCase(Trim(OwnerWant));
    If OwnerWant = '' Then OwnerWant := 'all';
    PouredWant := LowerCase(Trim(PouredWant));
    If PouredWant = '' Then PouredWant := 'exclude';
    If (OwnerWant <> 'free') And (OwnerWant <> 'component') And (OwnerWant <> 'all') Then
        Result := 'owner takes free, component or all, not "' + OwnerWant + '"'
    Else If (PouredWant <> 'exclude') And (PouredWant <> 'include') And (PouredWant <> 'only') Then
        Result := 'poured takes exclude, include or only, not "' + PouredWant + '"';
End;

{ RowsPcbRoute - which walk a scope takes: the narrowest place that holds   }
{ every possible match.                                                      }
Function RowsPcbRoute(NetWant : String; DesigCount : Integer; PatWant : String;
    OwnerWant : String; HasBounds : Boolean) : String;
Begin
    If Trim(NetWant) <> '' Then Result := 'net'
    Else If (DesigCount > 0) Or (Trim(PatWant) <> '') Or (OwnerWant = 'component') Then
        Result := 'component'
    Else If HasBounds Then Result := 'spatial'
    Else Result := 'board';
End;

{ RowsPcbOwnerKeeps - whether an object of this ownership is in scope. }
Function RowsPcbOwnerKeeps(OwnerWant : String; PouredWant : String;
    InComp : Boolean; InPoly : Boolean) : Boolean;
Begin
    Result := True;
    If (OwnerWant = 'free') And InComp Then Result := False;
    If (OwnerWant = 'component') And (Not InComp) Then Result := False;
    If (PouredWant = 'exclude') And InPoly Then Result := False;
    If (PouredWant = 'only') And (Not InPoly) Then Result := False;
End;

{ RowsPcbFieldSame - a guard or a read-back: angles compared round the     }
{ circle, everything else as the core compares an equal.                    }
Function RowsPcbFieldSame(Spec : String; Field : String; Have : String; Want : String) : Boolean;
Begin
    If (Field = 'start_angle') Or (Field = 'end_angle') Then
        Result := RowsAngleSame(Have, Want)
    Else
        Result := RowsCondHolds(RowsFieldType(Spec, Field), Have, '=', Want);
End;

{..............................................................................}
{ Altium side.                                                                 }
{..............................................................................}

{ The net an object is on: its own, else the net of the pour that made it. }
Function RowsPcbNetName(Obj : IPCB_Primitive) : String;
Var
    Net : IPCB_Net;
    Poly : IPCB_Polygon;
Begin
    Result := '';
    Try
        Net := Obj.Net;
        If Net <> Nil Then
            Result := Net.Name
        Else If Obj.InPolygon Then
        Begin
            Poly := Obj.Polygon;
            If Poly <> Nil Then
            Begin
                Net := Poly.Net;
                If Net <> Nil Then Result := Net.Name;
            End;
        End;
    Except
        Result := '';
    End;
End;

{ The designator and footprint name of the component owning Obj; False for }
{ a free primitive.                                                         }
Function RowsPcbCompOf(Obj : IPCB_Primitive; Var Desig : String; Var Pat : String) : Boolean;
Var
    Comp : IPCB_Component;
    Txt : IPCB_Text;
Begin
    Result := False;
    Desig := '';
    Pat := '';
    Try
        If Not Obj.InComponent Then Exit;
        Comp := Obj.Component;
        If Comp = Nil Then Exit;
        Result := True;
        Txt := Comp.Name;
        If Txt <> Nil Then Desig := Txt.Text;
        Pat := Comp.Pattern;
    Except
    End;
End;

{ One own field of a track, '' when unreadable. }
Function RowsPcbTrackOwnGet(Obj : IPCB_Primitive; Field : String; Units : String) : String;
Var
    Track : IPCB_Track;
    V : String;
Begin
    V := '';
    Try
        Track := Obj;
        Case Field Of
            'x1':     V := RowsLen(Track.X1, Units);
            'y1':     V := RowsLen(Track.Y1, Units);
            'x2':     V := RowsLen(Track.X2, Units);
            'y2':     V := RowsLen(Track.Y2, Units);
            'width':  V := RowsLen(Track.Width, Units);
            'length': V := RowsLen(RowsTrackLength(Track), Units);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ One own field of an arc, '' when unreadable. }
Function RowsPcbArcOwnGet(Obj : IPCB_Primitive; Field : String; Units : String) : String;
Var
    Arc : IPCB_Arc;
    V : String;
    CX, CY, R, A1, A2 : Double;
Begin
    V := '';
    Try
        Arc := Obj;
        CX := Arc.XCenter;
        CY := Arc.YCenter;
        R := Arc.Radius;
        A1 := Arc.StartAngle;
        A2 := Arc.EndAngle;
        Case Field Of
            'cx':          V := RowsLen(CX, Units);
            'cy':          V := RowsLen(CY, Units);
            'radius':      V := RowsLen(R, Units);
            'start_angle': V := RowsAngle(A1);
            'end_angle':   V := RowsAngle(A2);
            'width':       V := RowsLen(Arc.LineWidth, Units);
            'x1':          V := RowsLen(RowsArcEnd(CX, R, A1, False), Units);
            'y1':          V := RowsLen(RowsArcEnd(CY, R, A1, True), Units);
            'x2':          V := RowsLen(RowsArcEnd(CX, R, A2, False), Units);
            'y2':          V := RowsLen(RowsArcEnd(CY, R, A2, True), Units);
            'length':      V := RowsLen(R * RowsArcSweep(A1, A2) * 3.14159265358979 / 180.0, Units);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ One field of a board track or arc as text in the call unit; '' when it   }
{ cannot be read.                                                           }
Function RowsPcbGet(Obj : IPCB_Primitive; Kind : String; Field : String; Units : String;
    Board : IPCB_Board) : String;
Var
    V, Desig, Pat : String;
Begin
    V := '';
    Try
        Case Field Of
            'id':         V := IntToStr(Obj.I_ObjectAddress);
            'owner':      If Obj.InComponent Then V := 'component' Else V := 'free';
            'designator': If RowsPcbCompOf(Obj, Desig, Pat) Then V := Desig;
            'pattern':    If RowsPcbCompOf(Obj, Desig, Pat) Then V := Pat;
            'net':        V := RowsPcbNetName(Obj);
            'layer':      V := LayerDisplayName(Obj.Layer);
            'layer_name': V := RowsLayerUserName(Board, Obj.Layer);
            'poured':     V := BoolToJsonStr(Obj.InPolygon);
            'keepout':    V := BoolToJsonStr(Obj.IsKeepout);
            'locked':     V := BoolToJsonStr(Not Obj.Moveable);
            'selected':   V := BoolToJsonStr(Obj.Selected);
        Else
            If Kind = 'arc' Then V := RowsPcbArcOwnGet(Obj, Field, Units)
            Else V := RowsPcbTrackOwnGet(Obj, Field, Units);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ Write one field of a board track or arc. Layer is the resolved layer for }
{ a layer write. False when the write raised or the field is not writable. }
Function RowsPcbSet(Obj : IPCB_Primitive; Kind : String; Field : String; Value : String;
    Units : String; Layer : TLayer) : Boolean;
Var
    Track : IPCB_Track;
    Arc : IPCB_Arc;
Begin
    Result := True;
    Try
        If Kind = 'arc' Then
        Begin
            Arc := Obj;
            Case Field Of
                'layer':       Arc.Layer := Layer;
                'cx':          Arc.XCenter := RowsCoord(Value, Units);
                'cy':          Arc.YCenter := RowsCoord(Value, Units);
                'radius':      Arc.Radius := RowsCoord(Value, Units);
                'start_angle': Arc.StartAngle := RowsAngleDeg(Value);
                'end_angle':   Arc.EndAngle := RowsAngleDeg(Value);
                'width':       Arc.LineWidth := RowsCoord(Value, Units);
            Else
                Result := False;
            End;
        End
        Else
        Begin
            Track := Obj;
            Case Field Of
                'layer': Track.Layer := Layer;
                'x1':    Track.X1 := RowsCoord(Value, Units);
                'y1':    Track.Y1 := RowsCoord(Value, Units);
                'x2':    Track.X2 := RowsCoord(Value, Units);
                'y2':    Track.Y2 := RowsCoord(Value, Units);
                'width': Track.Width := RowsCoord(Value, Units);
            Else
                Result := False;
            End;
        End;
    Except
        Result := False;
    End;
End;

{ Whether an object a route handed out is inside the WHOLE scope. Every    }
{ route asks this of every object, so a route only saves time.              }
Function RowsPcbInScope(Obj : IPCB_Primitive; HasLayer : Boolean; LayerId : TLayer;
    OwnerWant : String; PouredWant : String; Desigs : TStringList; PatWant : String;
    HasBounds : Boolean; BL, BB, BR, BT : Integer) : Boolean;
Var
    Rect : TCoordRect;
    L, B, R, T : Integer;
    Desig, Pat : String;
    InComp, InPoly : Boolean;
Begin
    Result := False;
    Try
        If HasLayer Then
            If Obj.Layer <> LayerId Then Exit;
        InComp := Obj.InComponent;
        InPoly := Obj.InPolygon;
        If Not RowsPcbOwnerKeeps(OwnerWant, PouredWant, InComp, InPoly) Then Exit;
        If (Desigs.Count > 0) Or (PatWant <> '') Then
        Begin
            If Not RowsPcbCompOf(Obj, Desig, Pat) Then Exit;
            If Desigs.Count > 0 Then
                If Desigs.IndexOf(UpperCase(Desig)) < 0 Then Exit;
            If PatWant <> '' Then
                If Not FilterWildMatch(Pat, PatWant) Then Exit;
        End;
        If HasBounds Then
        Begin
            Rect := Obj.BoundingRectangle;
            L := Rect.Left;
            B := Rect.Bottom;
            R := Rect.Right;
            T := Rect.Top;
            If Not RowsBoxesTouch(L, B, R, T, BL, BB, BR, BT) Then Exit;
        End;
        Result := True;
    Except
        Result := False;
    End;
End;

{ One object a route handed out. Mode read: where, paging, a reply row.    }
{ Mode find (the writer): note the object of each wanted id. True = stop.  }
Function RowsPcbVisit(Obj : IPCB_Primitive; Board : IPCB_Board; CellCache : TStringList;
    Kind : String; Mode : String;
    Units : String; HasLayer : Boolean; LayerId : TLayer; OwnerWant : String;
    PouredWant : String; Desigs : TStringList; PatWant : String; HasBounds : Boolean;
    BL, BB, BR, BT : Integer; Fields : TStringList; Types : TStringList;
    CF : TStringList; CO : TStringList; CV : TStringList; CT : TStringList;
    Lines : TStringList; Offset : Integer; Limit : Integer; Var Matched : Integer;
    Var More : Boolean; Ids : TStringList; Hits : TStringList; Found : TInterfaceList;
    FoundK : TStringList; Var Repeats : Integer; Seen : TStringList;
    Var Scanned : Integer) : Boolean;
Var
    Addr, Line, Have : String;
    I, K : Integer;
    Keep : Boolean;
Begin
    Result := False;
    Addr := '';
    Try Addr := IntToStr(Obj.I_ObjectAddress); Except End;
    If Addr = '' Then Exit;
    If Seen <> Nil Then
    Begin
        If Seen.IndexOf(Addr) >= 0 Then
        Begin
            If Mode = 'find' Then
                If Ids.IndexOf(Addr) >= 0 Then Inc(Repeats);
            Exit;
        End;
        Seen.Add(Addr);
    End;
    Inc(Scanned);
    If Not RowsPcbInScope(Obj, HasLayer, LayerId, OwnerWant, PouredWant, Desigs, PatWant,
        HasBounds, BL, BB, BR, BT) Then Exit;

    If Mode = 'find' Then
    Begin
        K := Ids.IndexOf(Addr);
        If K < 0 Then Exit;
        If Hits.Strings[K] = '1' Then
            Inc(Repeats)
        Else
        Begin
            Hits.Strings[K] := '1';
            Found.Add(Obj);
            FoundK.Add(IntToStr(K));
        End;
        Result := Found.Count >= Ids.Count;
        Exit;
    End;

    Keep := True;
    I := 0;
    While Keep And (I < CF.Count) Do
    Begin
        Have := RowsPcbGet(Obj, Kind, RowsCol(CF, I), Units, Board);
        Keep := RowsCondHolds(RowsCol(CT, I), Have, RowsCol(CO, I), RowsCol(CV, I));
        Inc(I);
    End;
    If Not Keep Then Exit;
    Inc(Matched);
    If Matched <= Offset Then Exit;
    If (Limit > 0) And (Lines.Count >= Limit) Then
    Begin
        More := True;
        Result := True;
        Exit;
    End;
    Line := '';
    For I := 0 To Fields.Count - 1 Do
    Begin
        If Line <> '' Then Line := Line + ',';
        Line := Line + RowsCellCached(Types.Strings[I],
            RowsPcbGet(Obj, Kind, Fields.Strings[I], Units, Board), I, CellCache);
    End;
    Lines.Add('[' + Line + ']');
End;

{ THE WALK: one route, every object it hands out visited. Returns the route. }
Function RowsPcbWalk(Board : IPCB_Board; CellCache : TStringList; Kind : String; Mode : String; Units : String;
    NetWant : String; HasLayer : Boolean; LayerId : TLayer; OwnerWant : String;
    PouredWant : String; Desigs : TStringList; PatWant : String; HasBounds : Boolean;
    BL, BB, BR, BT : Integer; Fields : TStringList; Types : TStringList;
    CF : TStringList; CO : TStringList; CV : TStringList; CT : TStringList;
    Lines : TStringList; Offset : Integer; Limit : Integer; Var Matched : Integer;
    Var More : Boolean; Ids : TStringList; Hits : TStringList; Found : TInterfaceList;
    FoundK : TStringList; Var Repeats : Integer; Var Scanned : Integer) : String;
Var
    Route, Value, Desig : String;
    ObjType : Integer;
    Iterator : IPCB_BoardIterator;
    Spatial : IPCB_SpatialIterator;
    Group : IPCB_GroupIterator;
    Obj : IPCB_Primitive;
    Net : IPCB_Net;
    Owner : IPCB_Polygon;
    Comp : IPCB_Component;
    Txt : IPCB_Text;
    OneLayer : IPCB_LayerSet;
    Seen : TStringList;
    Stop, Take : Boolean;
Begin
    If Kind = 'arc' Then ObjType := eArcObject Else ObjType := eTrackObject;
    Route := RowsPcbRoute(NetWant, Desigs.Count, PatWant, OwnerWant, HasBounds);
    Result := Route;
    Stop := False;
    Seen := Nil;
    { Only the net route with pours can hand out one object twice (a pour   }
    { piece is not in the net group, but keep the guard where it is cheap). }
    If (Route = 'net') And (PouredWant <> 'exclude') Then
    Begin
        Seen := TStringList.Create;
        Seen.Sorted := True;
    End;
    Try
        If Route = 'net' Then
        Begin
            If PouredWant <> 'only' Then
            Begin
                Iterator := Board.BoardIterator_Create;
                Try
                    Iterator.AddFilter_ObjectSet(MkSet(eNetObject));
                    Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                    Iterator.AddFilter_Method(eProcessAll);
                    Net := Iterator.FirstPCBObject;
                    While (Net <> Nil) And (Not Stop) Do
                    Begin
                        Value := '';
                        Try Value := Net.Name; Except End;
                        If UpperCase(Value) = UpperCase(NetWant) Then
                        Begin
                            Group := Net.GroupIterator_Create;
                            Try
                                Group.AddFilter_ObjectSet(MkSet(ObjType));
                                Group.AddFilter_AllLayers;
                                Obj := Group.FirstPCBObject;
                                While (Obj <> Nil) And (Not Stop) Do
                                Begin
                                    Stop := RowsPcbVisit(Obj, Board, CellCache, Kind, Mode, Units, HasLayer,
                                        LayerId, OwnerWant, PouredWant, Desigs, PatWant, HasBounds,
                                        BL, BB, BR, BT, Fields, Types, CF, CO, CV, CT, Lines,
                                        Offset, Limit, Matched, More, Ids, Hits, Found, FoundK,
                                        Repeats, Seen, Scanned);
                                    If Not Stop Then Obj := Group.NextPCBObject;
                                End;
                            Finally
                                Net.GroupIterator_Destroy(Group);
                            End;
                        End;
                        If Not Stop Then Net := Iterator.NextPCBObject;
                    End;
                Finally
                    Board.BoardIterator_Destroy(Iterator);
                End;
            End;
            If (PouredWant <> 'exclude') And (Not Stop) Then
            Begin
                Iterator := Board.BoardIterator_Create;
                Try
                    Iterator.AddFilter_ObjectSet(MkSet(ePolyObject));
                    Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                    Iterator.AddFilter_Method(eProcessAll);
                    Owner := Iterator.FirstPCBObject;
                    While (Owner <> Nil) And (Not Stop) Do
                    Begin
                        Value := '';
                        Try
                            Net := Owner.Net;
                            If Net <> Nil Then Value := Net.Name;
                        Except End;
                        Take := UpperCase(Value) = UpperCase(NetWant);
                        If Take And HasLayer Then
                            Take := Owner.Layer = LayerId;
                        If Take Then
                        Begin
                            Group := Owner.GroupIterator_Create;
                            Try
                                Group.AddFilter_ObjectSet(MkSet(ObjType));
                                Obj := Group.FirstPCBObject;
                                While (Obj <> Nil) And (Not Stop) Do
                                Begin
                                    Stop := RowsPcbVisit(Obj, Board, CellCache, Kind, Mode, Units, HasLayer,
                                        LayerId, OwnerWant, PouredWant, Desigs, PatWant, HasBounds,
                                        BL, BB, BR, BT, Fields, Types, CF, CO, CV, CT, Lines,
                                        Offset, Limit, Matched, More, Ids, Hits, Found, FoundK,
                                        Repeats, Seen, Scanned);
                                    If Not Stop Then Obj := Group.NextPCBObject;
                                End;
                            Finally
                                Owner.GroupIterator_Destroy(Group);
                            End;
                        End;
                        If Not Stop Then Owner := Iterator.NextPCBObject;
                    End;
                Finally
                    Board.BoardIterator_Destroy(Iterator);
                End;
            End;
        End
        Else If Route = 'component' Then
        Begin
            Iterator := Board.BoardIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(eComponentObject));
                Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                Iterator.AddFilter_Method(eProcessAll);
                Comp := Iterator.FirstPCBObject;
                While (Comp <> Nil) And (Not Stop) Do
                Begin
                    Take := True;
                    If Desigs.Count > 0 Then
                    Begin
                        Desig := '';
                        Try
                            Txt := Comp.Name;
                            If Txt <> Nil Then Desig := Txt.Text;
                        Except End;
                        Take := Desigs.IndexOf(UpperCase(Desig)) >= 0;
                    End;
                    If Take And (PatWant <> '') Then
                    Begin
                        Value := '';
                        Try Value := Comp.Pattern; Except End;
                        Take := FilterWildMatch(Value, PatWant);
                    End;
                    If Take Then
                    Begin
                        Group := Comp.GroupIterator_Create;
                        Try
                            Group.AddFilter_ObjectSet(MkSet(ObjType));
                            Obj := Group.FirstPCBObject;
                            While (Obj <> Nil) And (Not Stop) Do
                            Begin
                                Stop := RowsPcbVisit(Obj, Board, CellCache, Kind, Mode, Units, HasLayer,
                                    LayerId, OwnerWant, PouredWant, Desigs, PatWant, HasBounds,
                                    BL, BB, BR, BT, Fields, Types, CF, CO, CV, CT, Lines,
                                    Offset, Limit, Matched, More, Ids, Hits, Found, FoundK,
                                    Repeats, Seen, Scanned);
                                If Not Stop Then Obj := Group.NextPCBObject;
                            End;
                        Finally
                            Comp.GroupIterator_Destroy(Group);
                        End;
                    End;
                    If Not Stop Then Comp := Iterator.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iterator);
            End;
        End
        Else If Route = 'spatial' Then
        Begin
            { Every layer: a spatial search narrowed to one layer left out    }
            { the SMD pads on it (CollectPCBMatches, MEASURED); the scope     }
            { check takes the layer per object.                               }
            Spatial := Board.SpatialIterator_Create;
            Try
                Spatial.AddFilter_ObjectSet(MkSet(ObjType));
                Spatial.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                Spatial.AddFilter_Area(BL, BB, BR, BT);
                Obj := Spatial.FirstPCBObject;
                While (Obj <> Nil) And (Not Stop) Do
                Begin
                    Stop := RowsPcbVisit(Obj, Board, CellCache, Kind, Mode, Units, HasLayer,
                        LayerId, OwnerWant, PouredWant, Desigs, PatWant, HasBounds,
                        BL, BB, BR, BT, Fields, Types, CF, CO, CV, CT, Lines,
                        Offset, Limit, Matched, More, Ids, Hits, Found, FoundK,
                        Repeats, Seen, Scanned);
                    If Not Stop Then Obj := Spatial.NextPCBObject;
                End;
            Finally
                Board.SpatialIterator_Destroy(Spatial);
            End;
        End
        Else
        Begin
            Iterator := Board.BoardIterator_Create;
            Try
                Iterator.AddFilter_ObjectSet(MkSet(ObjType));
                { One layer as NarrowPCBWalk: the legacy set stops at       }
                { Mechanical16, a layer past it gets a V7 set of its own.   }
                If Not HasLayer Then
                    Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers)
                Else If GetLayerString(LayerId) = 'Unknown' Then
                Begin
                    OneLayer := LayerSet.CreateLayerSet;
                    OneLayer.Include(LayerId);
                    Iterator.AddFilter_IPCB_LayerSet(OneLayer);
                End
                Else
                    Iterator.AddFilter_LayerSet(MkSet(LayerId));
                { eProcessFree leaves out what components AND pours own      }
                { (MEASURED for pours, NarrowPCBWalk): only when both are    }
                { out of scope anyway.                                       }
                If (OwnerWant = 'free') And (PouredWant = 'exclude') Then
                    Iterator.AddFilter_Method(eProcessFree)
                Else
                    Iterator.AddFilter_Method(eProcessAll);
                Obj := Iterator.FirstPCBObject;
                While (Obj <> Nil) And (Not Stop) Do
                Begin
                    Stop := RowsPcbVisit(Obj, Board, CellCache, Kind, Mode, Units, HasLayer,
                        LayerId, OwnerWant, PouredWant, Desigs, PatWant, HasBounds,
                        BL, BB, BR, BT, Fields, Types, CF, CO, CV, CT, Lines,
                        Offset, Limit, Matched, More, Ids, Hits, Found, FoundK,
                        Repeats, Seen, Scanned);
                    If Not Stop Then Obj := Iterator.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iterator);
            End;
        End;
    Finally
        If Seen <> Nil Then Seen.Free;
    End;
End;

{ The scope of a read or a write, from the call. Code and the returned     }
{ problem are set when it cannot be used. Desigs comes in new and empty.    }
Function RowsPcbScope(Params : String; Board : IPCB_Board; Units : String;
    Var NetWant : String; Var OwnerWant : String; Var PouredWant : String;
    Var PatWant : String; Desigs : TStringList; Var HasLayer : Boolean;
    Var LayerId : TLayer; Var HasBounds : Boolean; Var BL : Integer; Var BB : Integer;
    Var BR : Integer; Var BT : Integer; Var Code : String) : String;
Var
    LayerStr, BoundsStr : String;
    Names : TStringList;
    I : Integer;
Begin
    Result := '';
    Code := '';
    NetWant := Trim(ExtractJsonValue(Params, 'net'));
    PatWant := Trim(ExtractJsonValue(Params, 'pattern'));
    OwnerWant := ExtractJsonValue(Params, 'owner');
    PouredWant := ExtractJsonValue(Params, 'poured');
    Result := RowsPcbScopeProblem(OwnerWant, PouredWant);
    If Result <> '' Then
    Begin
        Code := 'BAD_SCOPE';
        Exit;
    End;
    Names := RowsSplitNames(ExtractJsonValue(Params, 'designator'));
    Try
        For I := 0 To Names.Count - 1 Do Desigs.Add(Names.Strings[I]);
    Finally
        Names.Free;
    End;
    BoundsStr := Trim(ExtractJsonValue(Params, 'bounds'));
    HasBounds := BoundsStr <> '';
    BL := 0;  BB := 0;  BR := 0;  BT := 0;
    If HasBounds Then
    Begin
        Result := RowsParseBounds(BoundsStr, Units, BL, BB, BR, BT);
        If Result <> '' Then
        Begin
            Code := 'BAD_SCOPE';
            Exit;
        End;
    End;
    LayerStr := Trim(ExtractJsonValue(Params, 'layer'));
    HasLayer := LayerStr <> '';
    LayerId := eNoLayer;
    If HasLayer Then
    Begin
        LayerId := ResolveLayerId(Board, LayerStr);
        If LayerId = eNoLayer Then
        Begin
            Code := 'BAD_LAYER';
            Result := 'No layer named "' + LayerStr + '" on this board';
        End;
    End;
End;

{..............................................................................}
{ rows.read_rows doc=pcb kind=track / arc                                      }
{..............................................................................}

Function RowsPcbRead(Kind : String; Params : String; RequestId : String) : String;
Var
    CellCache : TStringList;
    Spec, Units, Problem, Code, Path, Route, NetWant, OwnerWant, PouredWant, PatWant : String;
    Board : IPCB_Board;
    Fields, Types, Conds, Lines, Desigs, CF, CO, CV, CT, Ids, Hits, FoundK : TStringList;
    Found : TInterfaceList;
    LayerId : TLayer;
    HasLayer, HasBounds, More : Boolean;
    BL, BB, BR, BT, Offset, Limit, Matched, Scanned, Repeats : Integer;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    Spec := RowsPcbSpec(Kind);
    Offset := RowsIntOr(ExtractJsonValue(Params, 'offset'), 0);
    If Offset < 0 Then Offset := 0;
    Limit := RowsIntOr(ExtractJsonValue(Params, 'limit'), 5000);
    If Limit < 0 Then Limit := 0;

    Fields := RowsParseFields(Spec, ExtractJsonValue(Params, 'fields'), Problem);
    Types := RowsFieldTypes(Spec, Fields);
    CellCache := RowsCellCache(Fields.Count);
    Conds := Nil;
    CF := Nil;  CO := Nil;  CV := Nil;  CT := Nil;
    Lines := TStringList.Create;
    Desigs := TStringList.Create;
    Desigs.Sorted := True;
    { The writer find lists, empty for a read. }
    Ids := TStringList.Create;
    Hits := TStringList.Create;
    FoundK := TStringList.Create;
    Found := Nil;
    Try
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_FIELD', Problem);
            Exit;
        End;
        Conds := RowsCompileWhere(Spec, ExtractJsonValue(Params, 'where'), Problem);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_WHERE', Problem);
            Exit;
        End;
        CF := RowsColumn(Conds, 0);
        CO := RowsColumn(Conds, 1);
        CV := RowsColumn(Conds, 2);
        CT := RowsColumn(Conds, 3);

        Board := RowsFocusedBoard(Path, Problem);
        If Board = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCB', Problem);
            Exit;
        End;
        Problem := RowsPcbScope(Params, Board, Units, NetWant, OwnerWant, PouredWant, PatWant,
            Desigs, HasLayer, LayerId, HasBounds, BL, BB, BR, BT, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;

        Matched := 0;
        Scanned := 0;
        Repeats := 0;
        More := False;
        Route := RowsPcbWalk(Board, CellCache, Kind, 'read', Units, NetWant, HasLayer, LayerId, OwnerWant,
            PouredWant, Desigs, PatWant, HasBounds, BL, BB, BR, BT, Fields, Types,
            CF, CO, CV, CT, Lines, Offset, Limit, Matched, More, Ids, Hits, Found, FoundK,
            Repeats, Scanned);

        Result := BuildSuccessResponse(RequestId,
            RowsReadReply('pcb', Kind, Units, Fields, Lines, Offset, More, Scanned,
                'route ' + Route));
    Finally
        CellCache.Free;
        Fields.Free;
        Types.Free;
        If Conds <> Nil Then Conds.Free;
        If CF <> Nil Then CF.Free;
        If CO <> Nil Then CO.Free;
        If CV <> Nil Then CV.Free;
        If CT <> Nil Then CT.Free;
        Lines.Free;
        Desigs.Free;
        Ids.Free;
        Hits.Free;
        FoundK.Free;
    End;
End;

Function RowsPcbTrackRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbRead('track', Params, RequestId);
End;

Function RowsPcbArcRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbRead('arc', Params, RequestId);
End;

{..............................................................................}
{ rows.write_rows doc=pcb kind=track / arc                                     }
{ pcb has no container guard, so the write takes the SAME scope parameters as }
{ the read (net, layer, designator, pattern, owner, poured, bounds) to narrow }
{ its find walk; without them it walks the whole kind. Walk 1 COLLECTS the    }
{ object of each id (and stops once all are found); the guards are compared   }
{ and the writes made on that list, never while an iterator is live: a       }
{ written object can move in the spatial index under a walk. The list is     }
{ never freed (releasing board refs through it faults in oleaut32, as in     }
{ ProcessPCBBoardObjects).                                                    }
{..............................................................................}

Function RowsPcbWrite(Kind : String; Params : String; RequestId : String) : String;
Var
    RS, RE, FKeys, FVals, FTypes : TStringList;
    S, E, F : Integer;
    Spec, Units, Problem, Code, Path, Route, NetWant, OwnerWant, PouredWant, PatWant : String;
    Addr, Key, Name, Value, Have, Notes, Rec, LayerCache : String;
    Board : IPCB_Board;
    Obj : IPCB_Primitive;
    Ids, Texts, Hits, FoundK, Results, Desigs, Fields, Types, Lines : TStringList;
    CF, CO, CV, CT : TStringList;
    Found : TInterfaceList;
    LayerId, WriteLayer : TLayer;
    HasLayer, HasBounds, More, Ok : Boolean;
    BL, BB, BR, BT, K, I, J, Written, Failed, Repeats, Matched, Scanned : Integer;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers (RowsCore) }
    Spec := RowsPcbSpec(Kind);

    RS := TStringList.Create;
    RE := TStringList.Create;
    FKeys := TStringList.Create;
    FVals := TStringList.Create;
    FTypes := TStringList.Create;
    Ids := TStringList.Create;
    Texts := TStringList.Create;
    Hits := TStringList.Create;
    FoundK := TStringList.Create;
    Results := TStringList.Create;
    Desigs := TStringList.Create;
    Desigs.Sorted := True;
    { The read lists, empty for a find walk. }
    Fields := TStringList.Create;
    Types := TStringList.Create;
    Lines := TStringList.Create;
    CF := TStringList.Create;
    CO := TStringList.Create;
    CV := TStringList.Create;
    CT := TStringList.Create;
    Found := TInterfaceList.Create;
    Try
        Problem := RowsTakeRows(Spec, Params, '',
            Ids, Texts, RS, RE, FKeys, FVals, FTypes, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        For K := 0 To Ids.Count - 1 Do Hits.Add('0');

        Board := RowsFocusedBoard(Path, Problem);
        If Board = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCB', Problem);
            Exit;
        End;
        Problem := RowsPcbScope(Params, Board, Units, NetWant, OwnerWant, PouredWant, PatWant,
            Desigs, HasLayer, LayerId, HasBounds, BL, BB, BR, BT, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;

        { Layers that exist: the document's own. Every other value of a  }
        { row is checked by rows.py before it is sent (rows_rules.py).    }
        LayerCache := '';
        For K := 0 To Ids.Count - 1 Do
        Begin
            S := StrToInt(RS.Strings[K]);
            E := StrToInt(RE.Strings[K]);
            For F := S To E - 1 Do
            Begin
                Key := FKeys.Strings[F];
                Value := FVals.Strings[F];
                If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;
                Problem := '';
                If (Problem = '') And (Key = 'layer') Then
                    If ResolveLayerIdCached(Board, Value, LayerCache) = eNoLayer Then
                        Problem := 'no layer named "' + Value + '"';
                If Problem <> '' Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'BAD_VALUE',
                        'row ' + Ids.Strings[K] + ': ' + Problem + '; nothing was changed');
                    Exit;
                End;
            End;
        End;

        { Walk 1: find the object of every id inside the scope. }
        Matched := 0;
        Scanned := 0;
        Repeats := 0;
        More := False;
        Route := RowsPcbWalk(Board, Fields, Kind, 'find', Units, NetWant, HasLayer, LayerId, OwnerWant,
            PouredWant, Desigs, PatWant, HasBounds, BL, BB, BR, BT, Fields, Types,
            CF, CO, CV, CT, Lines, 0, 0, Matched, More, Ids, Hits, Found, FoundK,
            Repeats, Scanned);
        Notes := '';
        For K := 0 To Ids.Count - 1 Do
            If Hits.Strings[K] = '0' Then
                Notes := Notes + 'row ' + Ids.Strings[K] + ': no ' + Kind
                    + ' with that id in the scope (route ' + Route + ');';
        If Notes <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                Notes + ' nothing was changed; read the ' + Kind + 's again');
            Exit;
        End;

        { Every guard, before anything is written. }
        For J := 0 To Found.Count - 1 Do
        Begin
            Obj := Found.Items[J];
            K := StrToInt(FoundK.Strings[J]);
            Addr := Ids.Strings[K];
            S := StrToInt(RS.Strings[K]);
            E := StrToInt(RE.Strings[K]);
            For F := S To E - 1 Do
            Begin
                Key := FKeys.Strings[F];
                If Copy(Key, 1, 1) <> '?' Then Continue;
                Name := Copy(Key, 2, Length(Key));
                Value := FVals.Strings[F];
                Have := RowsPcbGet(Obj, Kind, Name, Units, Board);
                { A layer guard may name the layer either way. }
                If Name = 'layer' Then
                Begin
                    If ResolveLayerIdCached(Board, Value, LayerCache) <> Obj.Layer Then
                        Notes := Notes + 'row ' + Addr + ': layer is ' + Have + ', not '
                            + Value + ';';
                End
                Else If Not RowsPcbFieldSame(Spec, Name, Have, Value) Then
                    Notes := Notes + 'row ' + Addr + ': ' + Name + ' is ' + Have
                        + ', not ' + Value + ';';
            End;
        End;
        If Notes <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                Notes + ' nothing was changed; read the ' + Kind + 's again');
            Exit;
        End;

        { Walk 2 is the list: each object once, in ONE edit. }
        Written := 0;
        Failed := 0;
        PcbBeginEdit(0);
        Try
            For J := 0 To Found.Count - 1 Do
            Begin
                Obj := Found.Items[J];
                K := StrToInt(FoundK.Strings[J]);
                Addr := Ids.Strings[K];
                Ok := True;
                Rec := '';
                Try
                    PCBServer.SendMessageToRobots(Obj.I_ObjectAddress, c_Broadcast,
                        PCBM_BeginModify, c_NoEventData);
                Except End;
                S := StrToInt(RS.Strings[K]);
                E := StrToInt(RE.Strings[K]);
                For F := S To E - 1 Do
                Begin
                    Key := FKeys.Strings[F];
                    If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;
                    Value := FVals.Strings[F];
                    WriteLayer := eNoLayer;
                    If Key = 'layer' Then
                        WriteLayer := ResolveLayerIdCached(Board, Value, LayerCache);
                    If Not RowsPcbSet(Obj, Kind, Key, Value, Units, WriteLayer) Then
                    Begin
                        Ok := False;
                        Notes := Notes + 'row ' + Addr + ': writing ' + Key + ' raised;';
                    End;
                End;
                Try
                    PCBServer.SendMessageToRobots(Obj.I_ObjectAddress, c_Broadcast,
                        PCBM_EndModify, c_NoEventData);
                Except End;
                { No spatial-index check here. #208 was a VIA's mask write (its  }
                { box includes the mask); width and end writes on a track or arc }
                { re-index by themselves. Measured on a test board: 210 widths and}
                { a moved end, never once re-indexed, and a bounds search at the }
                { new end found the track. The check cost six spatial searches  }
                { per object, most of a write's time.                           }
                Try Obj.GraphicallyInvalidate; Except End;
                { Read every written field back. }
                S := StrToInt(RS.Strings[K]);
                E := StrToInt(RE.Strings[K]);
                For F := S To E - 1 Do
                Begin
                    Key := FKeys.Strings[F];
                    If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;
                    Value := FVals.Strings[F];
                    Have := RowsPcbGet(Obj, Kind, Key, Units, Board);
                    If Key = 'layer' Then
                    Begin
                        If Obj.Layer <> ResolveLayerIdCached(Board, Value, LayerCache) Then
                        Begin
                            Ok := False;
                            Notes := Notes + 'row ' + Addr + ': layer reads ' + Have
                                + ' after writing ' + Value + ';';
                        End;
                    End
                    Else If Not RowsPcbFieldSame(Spec, Key, Have, Value) Then
                    Begin
                        Ok := False;
                        Notes := Notes + 'row ' + Addr + ': ' + Key + ' reads ' + Have
                            + ' after writing ' + Value + ';';
                    End;
                    Rec := Rec + ',"' + Key + '":' + RowsCell(FTypes.Strings[F], Have);
                End;
                If Not Ok Then Inc(Failed);
                Inc(Written);
                Results.Add('{"id":"' + Addr + '","ok":' + BoolToJsonStr(Ok) + Rec + '}');
            End;
        Finally
            PcbEndEdit(0);
        End;
        If Written > 0 Then
        Begin
            Try Board.GraphicalView_ZoomRedraw; Except End;
            MarkDocDirtyByPath(Board.FileName);
        End;
        Notes := Trim(Notes + ' route ' + Route);

        Result := BuildSuccessResponse(RequestId,
            RowsWriteReply('pcb', Kind, Ids.Count, Written, Failed, Repeats, Results, Notes));
    Finally
        RS.Free;  RE.Free;  FKeys.Free;  FVals.Free;  FTypes.Free;
        Ids.Free;  Texts.Free;  Hits.Free;  FoundK.Free;  Results.Free;
        Desigs.Free;  Fields.Free;  Types.Free;  Lines.Free;
        CF.Free;  CO.Free;  CV.Free;  CT.Free;
    End;
End;

Function RowsPcbTrackWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbWrite('track', Params, RequestId);
End;

Function RowsPcbArcWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPcbWrite('arc', Params, RequestId);
End;

{ UnitStamp_RowsPcb - which build of RowsPcb.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_RowsPcb(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
