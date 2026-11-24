{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ RowsPadVia.pas - rows kinds for vias and pads (docs/ROWS_CONTRACT.md):     }
{ pcblib/via, pcblib/pad, pcb/via, pcb/pad. Built on RowsCore.pas, in the    }
{ shape of RowsPcbLib.pas (the track template).                              }
{                                                                            }
{ One getter serves a via or a pad in either document; the documents differ }
{ only in their context fields and their walk. Writes reuse what the bridge }
{ already proves:                                                            }
{   pads - the order and rules of ApplyPadEdit / PadEditProblem (Library.pas, }
{          set_pad_geometry), re-done on the flat table with integers        }
{          (RowsPVPadApply): main's helper parsed decimals out of a ';' row; }
{   vias - the routes of Lib_SetViaProperties (Library.pas): drill and size  }
{          in the order that keeps a ring, stack mode, per-layer sizes,      }
{          tenting on a manual mask, mask from the hole edge, the mask      }
{          through the pad cache.                                            }
{ A middle via size can read 0 after a write that took (issues #65, #140):  }
{ such a read-back is reported as unverified, never as a failed write.      }
{ Testpoint flags and the IPC-4761 structure type are READ only.            }
{                                                                            }
{ pcblib: coordinates from each footprint reference point (Footprint.X / Y).}
{ pcb: the FOCUSED PcbDoc only, ABSOLUTE board coordinates (as rows         }
{ pcb/track, the layout model and the generic X / Y). After a board write   }
{ the object is re-indexed when the write left the spatial index worse      }
{ (EnsureIndexed, bug 41: a via mask write left its box stale).             }
{..............................................................................}

{ ---- field tables ------------------------------------------------------- }
{ Lengths (l) are Altium coordinates, 1/10000 mil; angles (a) 1/10000       }
{ degree; i a plain integer -- every number a canonical integer (RowsCore). }
{ These kinds always run in units 'coord', whatever the call asks.          }

{ A via own fields, the same in a library and on a board. size is the top  }
{ diameter, which is every layer on a simple via.                           }
Function RowsPVViaFields(Dummy : Integer) : String;
Begin
    Result := '|x:l:r|y:l:r|hole:l:w|size:l:w|stack_mode:s:w|size_mid:l:w'
        + '|size_bottom:l:w|low_layer:s:r|high_layer:s:r'
        + '|mask_top:l:w|mask_bottom:l:w|mask_separate:b:w|mask_manual:b:w'
        + '|mask_from_hole_edge:b:w|tenting_top:b:w|tenting_bottom:b:w'
        + '|testpoint_top:b:r|testpoint_bottom:b:r|assy_testpoint_top:b:r'
        + '|assy_testpoint_bottom:b:r|structure_type:i:r|net:s:r|selected:b:r';
End;

{ A pad own fields. Moveable is w for a library pad (name, x, y) and r on a }
{ board, where a pad is renamed or moved through its footprint.             }
Function RowsPVPadFields(Moveable : String) : String;
Begin
    Result := '|name:s:' + Moveable + '|layer:s:r|x:l:' + Moveable + '|y:l:' + Moveable
        + '|rotation:a:w|x_size:l:w|y_size:l:w|bottom_x_size:l:w|bottom_y_size:l:w'
        + '|shape:s:w|corner_radius_pct:i:w|stack_mode:s:w'
        + '|hole:l:w|hole_type:s:w|hole_width:l:w|hole_rotation:a:w|plated:b:w'
        + '|mask_top:l:w|mask_bottom:l:w|mask_separate:b:w|mask_manual:b:w'
        + '|mask_from_hole_edge:b:w|paste:l:w|paste_manual:b:w|paste_top_enabled:b:w'
        + '|testpoint_top:b:r|testpoint_bottom:b:r|assy_testpoint_top:b:r'
        + '|assy_testpoint_bottom:b:r|net:s:r|selected:b:r';
End;

Function RowsPcbLibViaSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|footprint:s:r|description:s:r' + RowsPVViaFields(0);
End;

Function RowsPcbLibPadSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|footprint:s:r|description:s:r' + RowsPVPadFields('w');
End;

Function RowsPcbViaSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|owner:s:r|designator:s:r|pattern:s:r' + RowsPVViaFields(0);
End;

Function RowsPcbPadSpec(Dummy : Integer) : String;
Begin
    Result := 'id:s:r|owner:s:r|designator:s:r|pattern:s:r' + RowsPVPadFields('r');
End;

{ ---- pure helpers (lifted into the tests) ------------------------------- }

{ Whether Value is one of the words of List, written as |a|b|c|, ignoring   }
{ case and surrounding spaces.                                              }
Function RowsPVOneOf(Value : String; List : String) : Boolean;
Begin
    Result := (Trim(Value) <> '') And (Pos('|' + LowerCase(Trim(Value)) + '|', List) > 0);
End;

{ Whether Item is in a comma list, ignoring case: a designator scope.       }
Function RowsPVInCsv(List : String; Item : String) : Boolean;
Var
    Rest, Part : String;
    P : Integer;
Begin
    Result := False;
    If Trim(Item) = '' Then Exit;
    Rest := List;
    While Rest <> '' Do
    Begin
        P := Pos(',', Rest);
        If P = 0 Then
        Begin
            Part := Rest;
            Rest := '';
        End
        Else
        Begin
            Part := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        If UpperCase(Trim(Part)) = UpperCase(Trim(Item)) Then
        Begin
            Result := True;
            Exit;
        End;
    End;
End;

{ bounds = x1,y1,x2,y2 as coordinates (1/10000 mil, canonical integers); any }
{ two opposite corners. False when it is not four of them.                   }
Function RowsPVParseBounds(S : String; Var X1 : Integer; Var Y1 : Integer;
    Var X2 : Integer; Var Y2 : Integer) : Boolean;
Var
    Rest, Part : String;
    P, N : Integer;
    V0, V1, V2, V3, F, T : Integer;
Begin
    Result := False;
    Rest := Trim(S);
    N := 0;
    While (Rest <> '') And (N < 5) Do
    Begin
        P := Pos(',', Rest);
        If P = 0 Then
        Begin
            Part := Rest;
            Rest := '';
        End
        Else
        Begin
            Part := Copy(Rest, 1, P - 1);
            Rest := Copy(Rest, P + 1, Length(Rest));
        End;
        If Not RowsIntStr(Trim(Part)) Then Exit;
        F := StrToInt(Trim(Part));
        If N = 0 Then V0 := F
        Else If N = 1 Then V1 := F
        Else If N = 2 Then V2 := F
        Else If N = 3 Then V3 := F;
        Inc(N);
    End;
    If N <> 4 Then Exit;
    X1 := V0;  Y1 := V1;  X2 := V2;  Y2 := V3;
    If X1 > X2 Then Begin T := X1; X1 := X2; X2 := T; End;
    If Y1 > Y2 Then Begin T := Y1; Y1 := Y2; Y2 := T; End;
    Result := True;
End;

{ ---- reading one object -------------------------------------------------- }

{ The net of a primitive by name, '' for none. }
Function RowsPVNetName(Prim : IPCB_Primitive) : String;
Var
    Net : IPCB_Net;
Begin
    Result := '';
    Try
        Net := Prim.Net;
        If Net <> Nil Then Result := Net.Name;
    Except
        Result := '';
    End;
End;

{ A via mask, through the pad cache (the via interface does not reach it). }
Function RowsPVViaMask(Via : IPCB_Via; Field : String; Units : String) : String;
Var
    AsPad : IPCB_Pad;
    Cache : TPadCache;
Begin
    Result := '';
    Try
        AsPad := Via;
        Cache := AsPad.GetState_Cache;
        If Field = 'mask_top' Then Result := RowsLen(Cache.SolderMaskExpansion, Units)
        Else If Field = 'mask_bottom' Then Result := RowsLen(Cache.SolderMaskBottomExpansion, Units)
        Else If Field = 'mask_separate' Then Result := BoolToJsonStr(Cache.UseSeparateExpansions)
        Else If Field = 'mask_manual' Then
            Result := BoolToJsonStr(Cache.SolderMaskExpansionValid = eCacheManual);
    Except
        Result := '';
    End;
End;

{ A via middle diameter. A simple via stores none and reads 0, the right    }
{ answer; on a top-middle-bottom via a 0 is not a size (a blind read,       }
{ issues #65 / #140) and comes back unreadable.                             }
Function RowsPVViaMid(Via : IPCB_Via; Units : String) : String;
Var
    Mid : Integer;
Begin
    Result := '';
    Try
        Mid := Via.SizeOnLayer(eMidLayers);
        If (Mid = 0) And (Via.Mode = ePadMode_LocalStack) Then Result := ''
        Else Result := RowsLen(Mid, Units);
    Except
        Result := '';
    End;
End;

{ The IPC-4761 structure type, Altium own number as pcb.via_structures     }
{ reports it (PCB.pas ViaStructureJson, the same members); -1 for none.     }
Function RowsPVViaStructure(Via : IPCB_Via) : String;
Var
    VS : IPCB_ViaStructure;
Begin
    Result := '';
    VS := Nil;
    Try VS := Via.ViaStructure; Except VS := Nil; End;
    If VS = Nil Then
    Begin
        Result := '-1';
        Exit;
    End;
    Try Result := IntToStr(VS.StructureType); Except Result := ''; End;
End;

{ One via field (not the context ones) as text in the call unit. }
Function RowsPVViaGet(Via : IPCB_Via; Field : String; XOrg : Integer; YOrg : Integer;
    Units : String) : String;
Var
    V : String;
Begin
    V := '';
    Try
        Case Field Of
            'x':                     V := RowsLen(Via.X - XOrg, Units);
            'y':                     V := RowsLen(Via.Y - YOrg, Units);
            'hole':                  V := RowsLen(Via.HoleSize, Units);
            'size':                  V := RowsLen(Via.SizeOnLayer(eTopLayer), Units);
            'size_mid':              V := RowsPVViaMid(Via, Units);
            'size_bottom':           V := RowsLen(Via.SizeOnLayer(eBottomLayer), Units);
            'stack_mode':            V := PCBStackName(Via.Mode);
            'low_layer':             V := LayerDisplayName(Via.LowLayer);
            'high_layer':            V := LayerDisplayName(Via.HighLayer);
            'mask_top':              V := RowsPVViaMask(Via, Field, Units);
            'mask_bottom':           V := RowsPVViaMask(Via, Field, Units);
            'mask_separate':         V := RowsPVViaMask(Via, Field, Units);
            'mask_manual':           V := RowsPVViaMask(Via, Field, Units);
            'mask_from_hole_edge':   V := BoolToJsonStr(Via.SolderMaskExpansionFromHoleEdge);
            'tenting_top':           V := BoolToJsonStr(Via.IsTenting_Top);
            'tenting_bottom':        V := BoolToJsonStr(Via.IsTenting_Bottom);
            'testpoint_top':         V := BoolToJsonStr(Via.IsTestpoint_Top);
            'testpoint_bottom':      V := BoolToJsonStr(Via.IsTestpoint_Bottom);
            'assy_testpoint_top':    V := BoolToJsonStr(Via.IsAssyTestpoint_Top);
            'assy_testpoint_bottom': V := BoolToJsonStr(Via.IsAssyTestpoint_Bottom);
            'structure_type':        V := RowsPVViaStructure(Via);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ A pad mask or paste cache field. }
Function RowsPVPadCache(Pad : IPCB_Pad; Field : String; Units : String) : String;
Var
    Cache : TPadCache;
Begin
    Result := '';
    Try
        Cache := Pad.GetState_Cache;
        If Field = 'mask_top' Then Result := RowsLen(Cache.SolderMaskExpansion, Units)
        Else If Field = 'mask_bottom' Then Result := RowsLen(Cache.SolderMaskBottomExpansion, Units)
        Else If Field = 'mask_separate' Then Result := BoolToJsonStr(Cache.UseSeparateExpansions)
        Else If Field = 'mask_manual' Then
            Result := BoolToJsonStr(Cache.SolderMaskExpansionValid = eCacheManual)
        Else If Field = 'paste' Then Result := RowsLen(Cache.PasteMaskExpansion, Units)
        Else If Field = 'paste_manual' Then
            Result := BoolToJsonStr(Cache.PasteMaskExpansionValid = eCacheManual);
    Except
        Result := '';
    End;
End;

{ A pad top shape by the word the writer takes (custom read only). }
Function RowsPVPadShape(Pad : IPCB_Pad) : String;
Var
    Sh : Integer;
Begin
    Result := 'round';
    Try
        { read once: each Pad.TopShape is a call into Altium (the stage   }
        { profile of 2026-11-11 had shape among the dearest fields)       }
        Sh := Pad.TopShape;
        If Sh = eRectangular Then Result := 'rectangular'
        Else If Sh = eOctagonal Then Result := 'octagonal'
        Else If Sh = eRoundedRectangular Then Result := 'roundrect'
        Else If ShapeIsCustom(Sh) Then Result := 'custom';
    Except
        Result := '';
    End;
End;

{ A rounded-rectangle pad corner percentage; 0 for any other shape. }
Function RowsPVPadCorner(Pad : IPCB_Pad) : String;
Var
    Pct : Integer;
Begin
    Result := '0';
    Try
        If Pad.TopShape <> eRoundedRectangular Then Exit;
        Pct := Pad.CRPercentage[eTopLayer];
        Result := IntToStr(Pct);
    Except
        Result := '';
    End;
End;

{ The top paste on / off flag; '' where this build cannot read it. }
Function RowsPVPadPasteFlag(Pad : IPCB_Pad) : String;
Begin
    Result := PadPasteTopJson(Pad);
    If Result = 'null' Then Result := '';
End;

{ One pad field (not the context ones) as text in the call unit. }
Function RowsPVPadGet(Pad : IPCB_Pad; Field : String; XOrg : Integer; YOrg : Integer;
    Units : String) : String;
Var
    V : String;
Begin
    V := '';
    Try
        Case Field Of
            'name':                  V := Pad.Name;
            'layer':                 V := LayerDisplayName(Pad.Layer);
            'x':                     V := RowsLen(Pad.X - XOrg, Units);
            'y':                     V := RowsLen(Pad.Y - YOrg, Units);
            'rotation':              V := IntToStr(Round(Pad.Rotation * 10000.0));
            'x_size':                V := RowsLen(Pad.TopXSize, Units);
            'y_size':                V := RowsLen(Pad.TopYSize, Units);
            'bottom_x_size':         V := RowsLen(Pad.BotXSize, Units);
            'bottom_y_size':         V := RowsLen(Pad.BotYSize, Units);
            'shape':                 V := RowsPVPadShape(Pad);
            'corner_radius_pct':     V := RowsPVPadCorner(Pad);
            'stack_mode':            V := PCBStackName(Pad.Mode);
            'hole':                  V := RowsLen(Pad.HoleSize, Units);
            'hole_type':             V := PadHoleKind(Pad);
            'hole_width':            V := RowsLen(Pad.HoleWidth, Units);
            'hole_rotation':         V := IntToStr(Round(Pad.HoleRotation * 10000.0));
            'plated':                V := BoolToJsonStr(Pad.Plated);
            'mask_top':              V := RowsPVPadCache(Pad, Field, Units);
            'mask_bottom':           V := RowsPVPadCache(Pad, Field, Units);
            'mask_separate':         V := RowsPVPadCache(Pad, Field, Units);
            'mask_manual':           V := RowsPVPadCache(Pad, Field, Units);
            'paste':                 V := RowsPVPadCache(Pad, Field, Units);
            'paste_manual':          V := RowsPVPadCache(Pad, Field, Units);
            'mask_from_hole_edge':   V := BoolToJsonStr(Pad.SolderMaskExpansionFromHoleEdge);
            'paste_top_enabled':     V := RowsPVPadPasteFlag(Pad);
            'testpoint_top':         V := BoolToJsonStr(Pad.IsTestpoint_Top);
            'testpoint_bottom':      V := BoolToJsonStr(Pad.IsTestpoint_Bottom);
            'assy_testpoint_top':    V := BoolToJsonStr(Pad.IsAssyTestpoint_Top);
            'assy_testpoint_bottom': V := BoolToJsonStr(Pad.IsAssyTestpoint_Bottom);
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ One field of a via or a pad. CtxA / CtxB / CtxC are the context fields:   }
{ footprint and description in a library, owner, designator and pattern on  }
{ a board. '' when the field cannot be read.                                }
Function RowsPVGet(Prim : IPCB_Primitive; IsVia : Boolean; Field : String;
    CtxA : String; CtxB : String; CtxC : String; XOrg : Integer; YOrg : Integer;
    Units : String) : String;
Var
    V : String;
    Via : IPCB_Via;
    Pad : IPCB_Pad;
Begin
    V := '';
    Try
        Case Field Of
            'id':          V := IntToStr(Prim.I_ObjectAddress);
            'footprint':   V := CtxA;
            'owner':       V := CtxA;
            'description': V := CtxB;
            'designator':  V := CtxB;
            'pattern':     V := CtxC;
            'net':         V := RowsPVNetName(Prim);
            'selected':    V := BoolToJsonStr(Prim.Selected);
        Else
            Begin
                If IsVia Then
                Begin
                    Via := Prim;
                    V := RowsPVViaGet(Via, Field, XOrg, YOrg, Units);
                End
                Else
                Begin
                    Pad := Prim;
                    V := RowsPVPadGet(Pad, Field, XOrg, YOrg, Units);
                End;
            End;
        End;
    Except
        V := '';
    End;
    Result := V;
End;

{ ---- writing one object -------------------------------------------------- }

{ Whether a via row would leave no annular ring on THIS via (the hole or   }
{ the size it does not write is the via own); '' when it keeps one.         }
Function RowsPVViaGeomProblem(Via : IPCB_Via; HoleS : String; SizeS : String;
    Units : String) : String;
Var
    H, S : Integer;
Begin
    Result := '';
    If (HoleS = '') And (SizeS = '') Then Exit;
    Try
        H := Via.HoleSize;
        S := Via.SizeOnLayer(eTopLayer);
    Except
        Result := 'its hole and size cannot be read';
        Exit;
    End;
    If HoleS <> '' Then H := RowsCoord(HoleS, Units);
    If SizeS <> '' Then S := RowsCoord(SizeS, Units);
    If H >= S Then
        Result := 'hole ' + RowsLen(H, Units) + ' would not fit inside size '
            + RowsLen(S, Units) + ' (no annular ring)';
End;

{ Write one via row, in the order Lib_SetViaProperties measured: stack     }
{ mode, then drill and top size (the hole first unless it would not fit    }
{ the present size), per-layer sizes, tenting (the mask made manual first:  }
{ a tenting write on a rule-driven mask is dropped, AD 26.11), mask from   }
{ the hole edge, then the mask through the pad cache. Robots BeginModify / }
{ EndModify round it unless Robots is False (undo=false). Only the       }
{ fields Keep marks (RowsFlatKept). False                                 }
{ when a write raised.                                                    }
Function RowsPVViaApply(Via : IPCB_Via; Keys : TStringList; Vals : TStringList;
    S : Integer; E : Integer; Keep : String; Units : String; Addr : String;
    Var Notes : String; Robots : Boolean) : Boolean;
Var
    HoleS, SizeS, MidS, BotS, StackS, TTop, TBot, Edge, MaskS, MaskBotS, SepS, ManS : String;
    AsPad : IPCB_Pad;
    Cache : TPadCache;
    HoleC, SizeC, F : Integer;
    V : String;
    Ok, HoleFirst : Boolean;
Begin
    { Every field of the row in ONE pass (see RowsPVPadApply). }
    HoleS := '';  SizeS := '';  MidS := '';  BotS := '';  StackS := '';  TTop := '';
    TBot := '';  Edge := '';  MaskS := '';  MaskBotS := '';  SepS := '';  ManS := '';
    For F := S To E - 1 Do
    Begin
        If (Keep <> '') And (Copy(Keep, F - S + 1, 1) <> '1') Then Continue;
        V := Vals.Strings[F];
        Case Keys.Strings[F] Of
            'hole':                HoleS := V;
            'size':                SizeS := V;
            'size_mid':            MidS := V;
            'size_bottom':         BotS := V;
            'stack_mode':          StackS := LowerCase(V);
            'tenting_top':         TTop := LowerCase(V);
            'tenting_bottom':      TBot := LowerCase(V);
            'mask_from_hole_edge': Edge := LowerCase(V);
            'mask_top':            MaskS := V;
            'mask_bottom':         MaskBotS := V;
            'mask_separate':       SepS := LowerCase(V);
            'mask_manual':         ManS := LowerCase(V);
        End;
    End;
    Ok := True;
    If Robots Then
    Try
        PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
            PCBM_BeginModify, c_NoEventData);
    Except End;

    { 1. Stack mode, asked for or implied by a middle or bottom size. }
    If StackS = 'simple' Then
    Begin
        Try Via.Mode := ePadMode_Simple; Except Ok := False; End;
    End
    Else If (StackS = 'top-middle-bottom') Or (MidS <> '') Or (BotS <> '') Then
    Begin
        Try
            If (StackS = 'top-middle-bottom') Or (Via.Mode = ePadMode_Simple) Then
                Via.Mode := ePadMode_LocalStack;
        Except Ok := False; End;
    End;

    { 2. Drill and top size, in the order that never leaves the hole as   }
    { large as the pad on the way (SetViaGeometry, PCBGeneric.pas).       }
    HoleC := 0;
    SizeC := 0;
    If HoleS <> '' Then HoleC := RowsCoord(HoleS, Units);
    If SizeS <> '' Then SizeC := RowsCoord(SizeS, Units);
    HoleFirst := True;
    If HoleS <> '' Then
        Try HoleFirst := HoleC < Via.SizeOnLayer(eTopLayer); Except HoleFirst := True; End;
    If HoleFirst And (HoleS <> '') Then
        Try Via.HoleSize := HoleC; Except Ok := False; End;
    If SizeS <> '' Then
    Begin
        Try
            If Via.Mode = ePadMode_Simple Then Via.Size := SizeC
            Else Via.SizeOnLayer(eTopLayer) := SizeC;
        Except Ok := False; End;
    End;
    If (Not HoleFirst) And (HoleS <> '') Then
        Try Via.HoleSize := HoleC; Except Ok := False; End;

    { 3. Per-layer sizes. A middle size cannot always be read back. }
    If MidS <> '' Then
        Try Via.SizeOnLayer(eMidLayers) := RowsCoord(MidS, Units); Except Ok := False; End;
    If BotS <> '' Then
        Try Via.SizeOnLayer(eBottomLayer) := RowsCoord(BotS, Units); Except Ok := False; End;

    { 4. Tenting, on a manual mask. }
    If (TTop <> '') Or (TBot <> '') Then
    Begin
        Try
            AsPad := Via;
            Cache := AsPad.GetState_Cache;
            If Cache.SolderMaskExpansionValid <> eCacheManual Then
            Begin
                Cache.SolderMaskExpansionValid := eCacheManual;
                AsPad.SetState_Cache := Cache;
                Notes := Notes + 'row ' + Addr + ': a rule-driven mask was made manual at '
                    + 'its present value so the tenting would hold;';
            End;
        Except Ok := False; End;
        If TTop <> '' Then
            Try Via.SetState_IsTenting_Top(TTop = 'true'); Except Ok := False; End;
        If TBot <> '' Then
            Try Via.SetState_IsTenting_Bottom(TBot = 'true'); Except Ok := False; End;
    End;

    { 5. Mask from the hole edge, before the value whose meaning it changes. }
    If Edge <> '' Then
        Try Via.SolderMaskExpansionFromHoleEdge := (Edge = 'true'); Except Ok := False; End;

    { 6. The mask: source, then the values; a top value given alone goes to }
    { both sides (as set_via_properties does, issue #61).                  }
    If (MaskS <> '') Or (MaskBotS <> '') Or (SepS <> '') Or (ManS <> '') Then
    Begin
        Try
            AsPad := Via;
            Cache := AsPad.GetState_Cache;
            If ManS = 'true' Then Cache.SolderMaskExpansionValid := eCacheManual
            Else If ManS = 'false' Then Cache.SolderMaskExpansionValid := eCacheInvalid;
            If MaskS <> '' Then
            Begin
                Cache.SolderMaskExpansionValid := eCacheManual;
                Cache.SolderMaskExpansion := RowsCoord(MaskS, Units);
                If MaskBotS = '' Then
                    Cache.SolderMaskBottomExpansion := Cache.SolderMaskExpansion;
            End;
            If MaskBotS <> '' Then
            Begin
                Cache.SolderMaskExpansionValid := eCacheManual;
                Cache.SolderMaskBottomExpansion := RowsCoord(MaskBotS, Units);
            End;
            If SepS <> '' Then Cache.UseSeparateExpansions := (SepS = 'true');
            AsPad.SetState_Cache := Cache;
        Except Ok := False; End;
    End;

    If Robots Then
    Try
        PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
            PCBM_EndModify, c_NoEventData);
    Except End;
    { No redraw per object: the writer refreshes the view once when it is  }
    { done (ViewManager_FullUpdate).                                         }
    Result := Ok;
End;

{ Write one pad row (the fields Keep marks, RowsFlatKept) in the order and  }
{ by the rules main's set_pad_geometry measured (ApplyPadEdit, Library.pas): }
{ stack, sizes, shape, rotation, place, name, hole, paste flag, mask from   }
{ the hole edge, then mask and paste through the pad cache, manual first.   }
{ Every number goes in as Altium's own integer -- no decimal text, no       }
{ StrToFloat, no unit conversion (main's route cost ~170 us a field in       }
{ StrToFloat alone, and ~50 lookups of a ';' string per pad). WHICH fields  }
{ are written follows Keep; what a write DEPENDS on (the bottom mask        }
{ following the top, the default corner) follows the row as given, so a    }
{ field skipped as already right is still honoured. Robots BeginModify /    }
{ EndModify round it unless Robots is False (undo=false). False when a      }
{ write raised or did not hold.                                             }
Function RowsPVPadApply(Pad : IPCB_Pad; Keys : TStringList; Vals : TStringList;
    S : Integer; E : Integer; Keep : String; XOrg : Integer; YOrg : Integer;
    MayMove : Boolean; Var Notes : String; Robots : Boolean) : Boolean;
Var
    Shape, Corner, Stack, Name, XS, YS, HoleType, Plated, Edge, MaskMan, PasteMan : String;
    XSize, YSize, BXSize, BYSize, Rot, Hole, HoleW, HoleRot, PasteFlag : String;
    MaskT, MaskB, Paste, Sep, AllCorner, AllMaskB, Key, V : String;
    CornerPct, F : Integer;
    Cache : TPadCache;
    Ok : Boolean;
Begin
    { Every field of the row in ONE pass (RowsFlatKept per field walked   }
    { the row 24 times a pad). A field Keep leaves out is not written;     }
    { the corner and the bottom mask count as given whether kept or not.  }
    Shape := '';  Corner := '';  Stack := '';  Name := '';  XS := '';  YS := '';
    XSize := '';  YSize := '';  BXSize := '';  BYSize := '';  Rot := '';
    HoleType := '';  Hole := '';  HoleW := '';  HoleRot := '';  Plated := '';
    PasteFlag := '';  Edge := '';  MaskMan := '';  PasteMan := '';  MaskT := '';
    MaskB := '';  Paste := '';  Sep := '';  AllCorner := '';  AllMaskB := '';
    For F := S To E - 1 Do
    Begin
        Key := Keys.Strings[F];
        V := Vals.Strings[F];
        If Key = 'corner_radius_pct' Then AllCorner := V
        Else If Key = 'mask_bottom' Then AllMaskB := V;
        If (Keep <> '') And (Copy(Keep, F - S + 1, 1) <> '1') Then Continue;
        Case Key Of
            'shape':               Shape := LowerCase(V);
            'corner_radius_pct':   Corner := V;
            'stack_mode':          Stack := LowerCase(V);
            'name':                Name := V;
            'x':                   XS := V;
            'y':                   YS := V;
            'x_size':              XSize := V;
            'y_size':              YSize := V;
            'bottom_x_size':       BXSize := V;
            'bottom_y_size':       BYSize := V;
            'rotation':            Rot := V;
            'hole_type':           HoleType := LowerCase(V);
            'hole':                Hole := V;
            'hole_width':          HoleW := V;
            'hole_rotation':       HoleRot := V;
            'plated':              Plated := LowerCase(V);
            'paste_top_enabled':   PasteFlag := LowerCase(V);
            'mask_from_hole_edge': Edge := LowerCase(V);
            'mask_manual':         MaskMan := LowerCase(V);
            'paste_manual':        PasteMan := LowerCase(V);
            'mask_top':            MaskT := V;
            'mask_bottom':         MaskB := V;
            'paste':               Paste := V;
            'mask_separate':       Sep := LowerCase(V);
        End;
    End;
    { The corner a roundrect write uses: the row's, written or not, else 25. }
    CornerPct := 25;
    If AllCorner <> '' Then CornerPct := RowsIntOf(AllCorner);
    Ok := True;
    If Robots Then
    Try
        PCBServer.SendMessageToRobots(Pad.I_ObjectAddress,
            c_Broadcast, PCBM_BeginModify, c_NoEventData);
    Except End;

    { The stack first: its sizes have nowhere to go before. }
    If Stack = 'simple' Then
    Begin
        Try
            Pad.Mode := ePadMode_Simple;
            If Pad.Mode <> ePadMode_Simple Then Ok := False;
        Except Ok := False; End;
    End
    Else If (Stack = 'top-middle-bottom') Or (BXSize <> '') Or (BYSize <> '') Then
    Begin
        Try
            If Pad.Mode = ePadMode_Simple Then Pad.Mode := ePadMode_LocalStack;
            If Pad.Mode = ePadMode_Simple Then Ok := False;
        Except Ok := False; End;
    End;

    If XSize <> '' Then Try Pad.TopXSize := RowsIntOf(XSize); Except Ok := False; End;
    If YSize <> '' Then Try Pad.TopYSize := RowsIntOf(YSize); Except Ok := False; End;
    If BXSize <> '' Then Try Pad.BotXSize := RowsIntOf(BXSize); Except Ok := False; End;
    If BYSize <> '' Then Try Pad.BotYSize := RowsIntOf(BYSize); Except Ok := False; End;
    If (Shape = 'roundrect') Or ((Shape = '') And (Corner <> '')) Then
    Begin
        { The per-layer route the pad creators use: TopShape alone left a  }
        { through-hole pad round, corner 0.                               }
        Try
            SetPadRoundRect(Pad, CornerPct);
            If (Pad.Layer <> eBottomLayer) And (Pad.TopShape <> eRoundedRectangular) Then
                Ok := False;
        Except Ok := False; End;
    End
    Else If Shape <> '' Then
    Begin
        Try
            If Shape = 'rectangular' Then Pad.TopShape := eRectangular
            Else If Shape = 'octagonal' Then Pad.TopShape := eOctagonal
            Else Pad.TopShape := eRounded;
        Except Ok := False; End;
    End;
    If Rot <> '' Then Try Pad.Rotation := RowsAngleDeg(Rot); Except Ok := False; End;
    { x and y are from the footprint origin. }
    If ((XS <> '') Or (YS <> '')) And MayMove Then
    Begin
        Try
            If XS <> '' Then Pad.X := XOrg + RowsIntOf(XS);
            If YS <> '' Then Pad.Y := YOrg + RowsIntOf(YS);
        Except Ok := False; End;
    End;
    If Name <> '' Then
    Begin
        Try
            Pad.Name := Name;
            If Pad.Name <> Name Then Ok := False;
        Except Ok := False; End;
    End;
    If HoleType <> '' Then
    Begin
        Try
            If HoleType = 'slot' Then Pad.HoleType := eSlotHole
            Else If HoleType = 'square' Then Pad.HoleType := eSquareHole
            Else Pad.HoleType := eRoundHole;
        Except Ok := False; End;
    End;
    If Hole <> '' Then Try Pad.HoleSize := RowsIntOf(Hole); Except Ok := False; End;
    If HoleW <> '' Then
    Begin
        Try
            Pad.HoleType := eSlotHole;
            Pad.HoleWidth := RowsIntOf(HoleW);
        Except Ok := False; End;
    End;
    If HoleRot <> '' Then
        Try Pad.HoleRotation := RowsAngleDeg(HoleRot); Except Ok := False; End;
    If Plated <> '' Then
        Try Pad.Plated := (Plated = 'true'); Except Ok := False; End;

    { The top paste opening's on / off flag (issue #80), on its own. It    }
    { holds only on a manual paste expansion (AD 26.11): a rule-driven one }
    { is made manual first, at the value it holds.                         }
    If (PasteFlag = 'true') Or (PasteFlag = 'false') Then
    Begin
        If Not PadPasteFlagSupported(0) Then
        Begin
            Ok := False;
            If Pos('paste flag', Notes) = 0 Then
                Notes := Notes + 'paste flag: the pad paste on/off flag needs '
                    + 'Altium Designer 25 or later; it was not changed;';
        End
        Else
        Begin
            Try
                Cache := Pad.GetState_Cache;
                If Cache.PasteMaskExpansionValid <> eCacheManual Then
                Begin
                    Cache.PasteMaskExpansionValid := eCacheManual;
                    Pad.SetState_Cache := Cache;
                    If Pos('paste manual', Notes) = 0 Then
                        Notes := Notes + 'paste manual: a rule-driven paste expansion '
                            + 'was made manual at its current value so the paste '
                            + 'flag would hold;';
                End;
            Except Ok := False; End;
            Try
                Pad.SetState_IsTopPasteEnabled(PasteFlag = 'true');
                If Pad.GetState_IsTopPasteEnabled <> (PasteFlag = 'true') Then Ok := False;
            Except Ok := False; End;
        End;
    End;

    { From the hole edge: before the mask value whose meaning it changes. }
    If (Edge = 'true') Or (Edge = 'false') Then
    Begin
        Try
            Pad.SolderMaskExpansionFromHoleEdge := (Edge = 'true');
            If Pad.SolderMaskExpansionFromHoleEdge <> (Edge = 'true') Then Ok := False;
        Except Ok := False; End;
    End;

    If (Paste <> '') Or (MaskT <> '') Or (MaskB <> '') Or (Sep <> '')
        Or (MaskMan <> '') Or (PasteMan <> '') Then
    Begin
        Try
            Cache := Pad.GetState_Cache;
            { The source before any value: a value written while the rule }
            { governs is ignored.                                          }
            If MaskMan = 'true' Then Cache.SolderMaskExpansionValid := eCacheManual
            Else If MaskMan = 'false' Then Cache.SolderMaskExpansionValid := eCacheInvalid;
            If PasteMan = 'true' Then Cache.PasteMaskExpansionValid := eCacheManual
            Else If PasteMan = 'false' Then Cache.PasteMaskExpansionValid := eCacheInvalid;
            If Paste <> '' Then
            Begin
                Cache.PasteMaskExpansionValid := eCacheManual;
                Cache.PasteMaskExpansion := RowsIntOf(Paste);
            End;
            { SolderMaskExpansion is the top value once the sides are       }
            { separate; the bottom follows the top unless the ROW names a   }
            { bottom of its own (written now or already right).             }
            If MaskT <> '' Then
            Begin
                Cache.SolderMaskExpansionValid := eCacheManual;
                Cache.SolderMaskExpansion := RowsIntOf(MaskT);
                If AllMaskB = '' Then
                    Cache.SolderMaskBottomExpansion := Cache.SolderMaskExpansion;
            End;
            If MaskB <> '' Then
            Begin
                Cache.SolderMaskExpansionValid := eCacheManual;
                Cache.SolderMaskBottomExpansion := RowsIntOf(MaskB);
                Cache.UseSeparateExpansions := True;
            End;
            If Sep <> '' Then Cache.UseSeparateExpansions := (Sep = 'true');
            Pad.SetState_Cache := Cache;
            Cache := Pad.GetState_Cache;
            If (MaskMan = 'true') And (Cache.SolderMaskExpansionValid <> eCacheManual) Then Ok := False;
            If (MaskMan = 'false') And (Cache.SolderMaskExpansionValid = eCacheManual) Then Ok := False;
            If (PasteMan = 'true') And (Cache.PasteMaskExpansionValid <> eCacheManual) Then Ok := False;
            If (PasteMan = 'false') And (Cache.PasteMaskExpansionValid = eCacheManual) Then Ok := False;
        Except Ok := False; End;
    End;

    If Robots Then
    Try
        PCBServer.SendMessageToRobots(Pad.I_ObjectAddress,
            c_Broadcast, PCBM_EndModify, c_NoEventData);
    Except End;
    { No redraw per object: the writer refreshes the view once when it is  }
    { done (ViewManager_FullUpdate).                                         }

    If ((XSize <> '') Or (YSize <> '')) And (Pad.Mode <> ePadMode_Simple)
        And (Pos('stack', Notes) = 0) Then
        Notes := Notes + 'a pad with a per-layer stack had only its top size written;';
    Result := Ok;
End;

{ Write one row (the fields Keep marks) onto a via or a pad. MayMove False }
{ keeps a board pad where it is.                                            }
Function RowsPVApply(Prim : IPCB_Primitive; IsVia : Boolean; Keys : TStringList;
    Vals : TStringList; S : Integer; E : Integer; Keep : String; XOrg : Integer;
    YOrg : Integer; Units : String; MayMove : Boolean; Addr : String;
    Var Notes : String; Robots : Boolean) : Boolean;
Var
    Via : IPCB_Via;
    Pad : IPCB_Pad;
Begin
    Result := False;
    Try
        If IsVia Then
        Begin
            Via := Prim;
            Result := RowsPVViaApply(Via, Keys, Vals, S, E, Keep, Units, Addr, Notes, Robots);
        End
        Else
        Begin
            Pad := Prim;
            Result := RowsPVPadApply(Pad, Keys, Vals, S, E, Keep, XOrg, YOrg,
                MayMove, Notes, Robots);
        End;
    Except
        Result := False;
    End;
End;

{ Read every written field back; False when one does not hold. Rec gets   }
{ the fields as JSON pairs with a leading comma. A middle via size that     }
{ reads 0 or unreadable is noted as unverified, not failed.                 }
Function RowsPVCheck(Prim : IPCB_Primitive; IsVia : Boolean; Keys : TStringList;
    Vals : TStringList; Types : TStringList; S : Integer; E : Integer; CtxA : String;
    CtxB : String; CtxC : String; XOrg : Integer; YOrg : Integer; Units : String;
    Addr : String; Var Rec : String; Var Notes : String) : Boolean;
Var
    F : Integer;
    Key, Value, Have, FieldType : String;
    Eq : Boolean;
    Via : IPCB_Via;
    Pad : IPCB_Pad;
Begin
    Result := True;
    If IsVia Then Via := Prim Else Pad := Prim;
    For F := S To E - 1 Do
    Begin
        Key := Keys.Strings[F];
        If (Key = 'id') Or (Copy(Key, 1, 1) = '?') Then Continue;
        Value := Vals.Strings[F];
        FieldType := Types.Strings[F];
        { Only own fields are writable: read one dispatch deep (the generic  }
        { RowsPVGet and RowsCondHolds cost a few hundred us a field).       }
        If IsVia Then Have := RowsPVViaGet(Via, Key, XOrg, YOrg, Units)
        Else Have := RowsPVPadGet(Pad, Key, XOrg, YOrg, Units);
        If (FieldType = 's') Or (FieldType = 'b') Then
            Eq := UpperCase(Have) = UpperCase(Value)
        Else
            Eq := Have = Value;
        If IsVia And (Key = 'size_mid') And ((Have = '') Or (Have = '0')) Then
            Notes := Notes + 'row ' + Addr + ': size_mid was written but reads '
                + 'back 0 / unreadable on this build (issues #65, #140): unverified;'
        Else If (Have = '') Or (Not Eq) Then
        Begin
            Result := False;
            Notes := Notes + 'row ' + Addr + ': ' + Key + ' reads ' + Have
                + ' after writing ' + Value + ';';
        End;
        Rec := Rec + ',"' + Key + '":' + RowsCell(FieldType, Have);
    End;
End;

{ Check every guard of a row against its object; '' when all hold, else    }
{ the notes. A layer guard may name the layer either way: the resolved      }
{ layers are compared.                                                      }
Function RowsPVGuards(Prim : IPCB_Primitive; IsVia : Boolean; Keys : TStringList;
    Vals : TStringList; Types : TStringList; S : Integer; E : Integer; CtxA : String;
    CtxB : String; CtxC : String; XOrg : Integer; YOrg : Integer; Units : String;
    Board : IPCB_Board; Addr : String; Var LayerCache : String) : String;
Var
    F : Integer;
    Key, Name, Value, Have : String;
    L : TLayer;
Begin
    Result := '';
    For F := S To E - 1 Do
    Begin
        Key := Keys.Strings[F];
        If Copy(Key, 1, 1) <> '?' Then Continue;
        Name := Copy(Key, 2, Length(Key));
        Value := Vals.Strings[F];
        Have := RowsPVGet(Prim, IsVia, Name, CtxA, CtxB, CtxC, XOrg, YOrg, Units);
        If (Name = 'layer') Or (Name = 'low_layer') Or (Name = 'high_layer') Then
        Begin
            L := ResolveLayerIdCached(Board, Value, LayerCache);
            If (L = eNoLayer) Or (LayerDisplayName(L) <> Have) Then
                Result := Result + 'row ' + Addr + ': ' + Name + ' is ' + Have
                    + ', not ' + Value + ';';
        End
        Else If Not RowsCondHolds(Types.Strings[F], Have, '=', Value) Then
            Result := Result + 'row ' + Addr + ': ' + Name + ' is ' + Have
                + ', not ' + Value + ';';
    End;
End;

{ RowsPVFirstLook - pass 1 of the library writer for one row, in ONE pass }
{ over its fields: every guard compared (Notes gets the same text           }
{ RowsPVGuards writes) and every written field read once for the change     }
{ check: Keep = one character a field from S, 1 = write it; Rec = the  }
{ written fields as read, JSON pairs. A field is read one                }
{ dispatch deep (RowsPVPadGet / RowsPVViaGet; the context fields from the   }
{ footprint) and compared inline: the two separate loops through RowsPVGet  }
{ and RowsCondHolds cost ~0.6 ms a row (stage profile, 2026-11-23). Text    }
{ and boolean guards ignore case, numbers compare as their canonical text;  }
{ an unreadable value ('') fails a guard and is always written.            }
Function RowsPVFirstLook(Prim : IPCB_Primitive; IsVia : Boolean; Keys : TStringList;
    Vals : TStringList; Types : TStringList; S : Integer; E : Integer; FpName : String;
    FpDesc : String; XOrg : Integer; YOrg : Integer; Units : String;
    Board : IPCB_Board; Addr : String; Var LayerCache : String; Var Rec : String;
    Var Notes : String) : String;
Var
    F : Integer;
    Key, Name, Value, Have, T : String;
    Guard, Eq, Keep : Boolean;
    Via : IPCB_Via;
    Pad : IPCB_Pad;
    L : TLayer;
Begin
    Result := '';
    If IsVia Then Via := Prim Else Pad := Prim;
    For F := S To E - 1 Do
    Begin
        Key := Keys.Strings[F];
        If Key = 'id' Then
        Begin
            Result := Result + '0';
            Continue;
        End;
        Guard := Copy(Key, 1, 1) = '?';
        If Guard Then Name := Copy(Key, 2, Length(Key)) Else Name := Key;
        Value := Vals.Strings[F];
        T := Types.Strings[F];
        { Layers: a guard compares the resolved layer, a written layer is }
        { always written (a guard as RowsPVGuards compares it).             }
        If (Name = 'layer') Or (Name = 'low_layer') Or (Name = 'high_layer') Then
        Begin
            If Guard Then
            Begin
                Have := RowsPVGet(Prim, IsVia, Name, FpName, FpDesc, '', XOrg, YOrg, Units);
                L := ResolveLayerIdCached(Board, Value, LayerCache);
                If (L = eNoLayer) Or (LayerDisplayName(L) <> Have) Then
                    Notes := Notes + 'row ' + Addr + ': ' + Name + ' is ' + Have
                        + ', not ' + Value + ';';
                Result := Result + '0';
            End
            Else
                Result := Result + '1';
            Continue;
        End;
        If Name = 'footprint' Then Have := FpName
        Else If Name = 'description' Then Have := FpDesc
        Else If (Name = 'id') Or (Name = 'net') Or (Name = 'selected') Then
            Have := RowsPVGet(Prim, IsVia, Name, FpName, FpDesc, '', XOrg, YOrg, Units)
        Else If IsVia Then Have := RowsPVViaGet(Via, Name, XOrg, YOrg, Units)
        Else Have := RowsPVPadGet(Pad, Name, XOrg, YOrg, Units);
        If (T = 's') Or (T = 'b') Then Eq := UpperCase(Have) = UpperCase(Value)
        Else Eq := Have = Value;
        If Guard Then
        Begin
            If (Have = '') Or (Not Eq) Then
                Notes := Notes + 'row ' + Addr + ': ' + Name + ' is ' + Have
                    + ', not ' + Value + ';';
            Result := Result + '0';
            Continue;
        End;
        Keep := True;
        If Have <> '' Then
        Begin
            If T = 's' Then Keep := Have <> Value
            Else Keep := Not Eq;
        End;
        Rec := Rec + ',"' + Name + '":' + RowsCell(T, Have);
        If Keep Then Result := Result + '1' Else Result := Result + '0';
    End;
End;

{..............................................................................}
{ pcblib/via and pcblib/pad                                                    }
{..............................................................................}

{ RowsPVPadLine - the whole reply row of a library pad, every field of }
{ table in table order (RowsPcbLibPadSpec), read in straight-line code:    }
{ the generic route (RowsPVGet -> RowsPVPadGet, a Case per cell) cost ~0.1 }
{ ms a cell in the script interpreter, a property read almost nothing      }
{ (rows.probe_cost, 2026-11-10). Values exactly as the generic cells write }
{ them. FpJ / DescJ are the footprint name and description as JSON text.   }
{ PasteOk = the build reads the paste flag (PadPasteFlagSupported, asked   }
{ once a call). LastLayer / LastLayerJ remember the last layer name: pads  }
{ of a footprint share a few. '' when any read raised: the caller then     }
{ takes the generic route for that pad.                                    }
Function RowsPVPadLine(Pad : IPCB_Pad; Addr : String; FpJ : String; DescJ : String;
    XOrg : Integer; YOrg : Integer; PasteOk : Boolean; Var LastLayer : Integer;
    Var LastLayerJ : String) : String;
Var
    Cache : TPadCache;
    Sh, Ly, Ht : Integer;
    ShapeJ, CornerS, StackJ, HoleJ, Pl, Sep, MMan, Edge, PMan, PasteF : String;
    T1, T2, T3, T4, Sel, NetJ, Head, Mid, Tail : String;
    NetO : IPCB_Net;
Begin
    Result := '';
    Try
        Ly := Pad.Layer;
        If Ly <> LastLayer Then
        Begin
            LastLayerJ := '"' + EscapeJsonString(LayerDisplayName(Ly)) + '"';
            LastLayer := Ly;
        End;
        Sh := Pad.TopShape;
        CornerS := '0';
        If Sh = eRectangular Then ShapeJ := '"rectangular"'
        Else If Sh = eOctagonal Then ShapeJ := '"octagonal"'
        Else If Sh = eRoundedRectangular Then
        Begin
            ShapeJ := '"roundrect"';
            CornerS := IntToStr(Pad.CRPercentage[eTopLayer]);
        End
        Else If ShapeIsCustom(Sh) Then ShapeJ := '"custom"'
        Else ShapeJ := '"round"';
        StackJ := '"' + PCBStackName(Pad.Mode) + '"';
        Ht := Pad.HoleType;
        If Ht = eSlotHole Then HoleJ := '"slot"'
        Else If Ht = eSquareHole Then HoleJ := '"square"'
        Else HoleJ := '"round"';
        If Pad.Plated Then Pl := 'true' Else Pl := 'false';
        Cache := Pad.GetState_Cache;
        If Cache.UseSeparateExpansions Then Sep := 'true' Else Sep := 'false';
        If Cache.SolderMaskExpansionValid = eCacheManual Then MMan := 'true'
        Else MMan := 'false';
        If Pad.SolderMaskExpansionFromHoleEdge Then Edge := 'true' Else Edge := 'false';
        If Cache.PasteMaskExpansionValid = eCacheManual Then PMan := 'true'
        Else PMan := 'false';
        PasteF := 'null';
        If PasteOk Then
            If Pad.GetState_IsTopPasteEnabled Then PasteF := 'true' Else PasteF := 'false';
        If Pad.IsTestpoint_Top Then T1 := 'true' Else T1 := 'false';
        If Pad.IsTestpoint_Bottom Then T2 := 'true' Else T2 := 'false';
        If Pad.IsAssyTestpoint_Top Then T3 := 'true' Else T3 := 'false';
        If Pad.IsAssyTestpoint_Bottom Then T4 := 'true' Else T4 := 'false';
        If Pad.Selected Then Sel := 'true' Else Sel := 'false';
        NetJ := '""';
        NetO := Pad.Net;
        If NetO <> Nil Then NetJ := '"' + EscapeJsonString(NetO.Name) + '"';
        Head := '["' + Addr + '",' + FpJ + ',' + DescJ
            + ',"' + EscapeJsonString(Pad.Name) + '",' + LastLayerJ
            + ',' + IntToStr(Pad.X - XOrg) + ',' + IntToStr(Pad.Y - YOrg)
            + ',' + IntToStr(Round(Pad.Rotation * 10000.0))
            + ',' + IntToStr(Pad.TopXSize) + ',' + IntToStr(Pad.TopYSize)
            + ',' + IntToStr(Pad.BotXSize) + ',' + IntToStr(Pad.BotYSize);
        Mid := ',' + ShapeJ + ',' + CornerS + ',' + StackJ
            + ',' + IntToStr(Pad.HoleSize) + ',' + HoleJ + ',' + IntToStr(Pad.HoleWidth)
            + ',' + IntToStr(Round(Pad.HoleRotation * 10000.0)) + ',' + Pl;
        Tail := ',' + IntToStr(Cache.SolderMaskExpansion)
            + ',' + IntToStr(Cache.SolderMaskBottomExpansion) + ',' + Sep + ',' + MMan
            + ',' + Edge + ',' + IntToStr(Cache.PasteMaskExpansion) + ',' + PMan
            + ',' + PasteF + ',' + T1 + ',' + T2 + ',' + T3 + ',' + T4
            + ',' + NetJ + ',' + Sel + ']';
        Result := Head + Mid + Tail;
    Except
        Result := '';
    End;
End;

{ RowsPVAllFields - whether Fields is the whole table in table order: the }
{ rows RowsPVPadLine builds.                                              }
Function RowsPVAllFields(Spec : String; Fields : TStringList) : Boolean;
Var
    All : TStringList;
Begin
    All := RowsSpecNames(Spec);
    Try
        Result := All.Text = Fields.Text;
    Finally
        All.Free;
    End;
End;

{ rows.read_rows doc=pcblib kind=via|pad. Scope footprint (a name or       }
{ pattern, default every footprint); layer for pads (a via spans layers:    }
{ use where on low_layer / high_layer).                                     }
Function RowsPVLibRead(Params : String; RequestId : String; IsVia : Boolean) : String;
Var
    CellCache : TStringList;
    Spec, Units, LibPath, UsedPath, Problem, FpPattern, LayerStr, FpName, FpDesc : String;
    Addr, Line, F, Have, KindName, FpJ, DescJ, LastLayerJ : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Pad : IPCB_Pad;
    Fields, Types, Conds, Lines, Seen, CF, CO, CV, CT : TStringList;
    LayerId : TLayer;
    XOrg, YOrg, Offset, Limit, Matched, Scanned, I, ObjKind, LastLayer : Integer;
    Keep, More, Stop, Whole, PasteOk : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers here (RowsCore) }
    If IsVia Then
    Begin
        Spec := RowsPcbLibViaSpec(0);
        KindName := 'via';
        ObjKind := eViaObject;
    End
    Else
    Begin
        Spec := RowsPcbLibPadSpec(0);
        KindName := 'pad';
        ObjKind := ePadObject;
    End;
    FpPattern := Trim(ExtractJsonValue(Params, 'footprint'));
    If FpPattern = '' Then FpPattern := '*';
    LayerStr := Trim(ExtractJsonValue(Params, 'layer'));
    If IsVia And (LayerStr <> '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_LAYER',
            'A via spans layers: use where on low_layer / high_layer instead of layer');
        Exit;
    End;
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
    Seen := TStringList.Create;
    Seen.Sorted := True;
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
        LibPath := ExtractJsonValue(Params, 'library_path');
        PcbLib := RowsOpenPcbLib(LibPath, UsedPath, Problem);
        If PcbLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', Problem);
            Exit;
        End;
        LayerId := eNoLayer;
        If LayerStr <> '' Then
        Begin
            LayerId := ResolveLayerId(PcbLib.Board, LayerStr);
            If LayerId = eNoLayer Then
            Begin
                Result := BuildErrorResponse(RequestId, 'BAD_LAYER',
                    'No layer named "' + LayerStr + '" in this library');
                Exit;
            End;
        End;

        Matched := 0;
        Scanned := 0;
        More := False;
        Stop := False;
        { Every pad field asked, in table order: whole rows in straight-line }
        { code (RowsPVPadLine), the generic route only for a pad it refuses. }
        Whole := (Not IsVia) And RowsPVAllFields(Spec, Fields);
        PasteOk := Whole And PadPasteFlagSupported(0);
        LastLayer := -1;
        LastLayerJ := '';
        Iter := PcbLib.LibraryIterator_Create;
        Try
            Footprint := Iter.FirstPCBObject;
            While (Footprint <> Nil) And (Not Stop) Do
            Begin
                FpName := '';
                Try FpName := Footprint.Name; Except End;
                If FilterWildMatch(FpName, FpPattern) Then
                Begin
                    FpDesc := '';
                    Try FpDesc := Footprint.Description; Except End;
                    XOrg := 0;  YOrg := 0;
                    Try XOrg := Footprint.X; Except End;
                    Try YOrg := Footprint.Y; Except End;
                    FpJ := '"' + EscapeJsonString(FpName) + '"';
                    DescJ := '"' + EscapeJsonString(FpDesc) + '"';
                    GrpIter := Footprint.GroupIterator_Create;
                    Try
                        GrpIter.AddFilter_ObjectSet(MkSet(ObjKind));
                        Prim := GrpIter.FirstPCBObject;
                        While (Prim <> Nil) And (Not Stop) Do
                        Begin
                            Addr := '';
                            Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
                            If (Addr <> '') And (Seen.IndexOf(Addr) < 0) Then
                            Begin
                                Seen.Add(Addr);
                                Inc(Scanned);
                                Keep := True;
                                If LayerStr <> '' Then Keep := Prim.Layer = LayerId;
                                I := 0;
                                While Keep And (I < Conds.Count) Do
                                Begin
                                    Have := RowsPVGet(Prim, IsVia, RowsCol(CF, I), FpName,
                                        FpDesc, '', XOrg, YOrg, Units);
                                    Keep := RowsCondHolds(RowsCol(CT, I), Have,
                                        RowsCol(CO, I), RowsCol(CV, I));
                                    Inc(I);
                                End;
                                If Keep Then
                                Begin
                                    Inc(Matched);
                                    If Matched > Offset Then
                                    Begin
                                        If (Limit > 0) And (Lines.Count >= Limit) Then
                                        Begin
                                            More := True;
                                            Stop := True;
                                        End
                                        Else
                                        Begin
                                            Line := '';
                                            If Whole Then
                                            Begin
                                                Pad := Prim;
                                                Line := RowsPVPadLine(Pad, Addr, FpJ, DescJ,
                                                    XOrg, YOrg, PasteOk, LastLayer, LastLayerJ);
                                            End;
                                            If Line <> '' Then
                                                Lines.Add(Line)
                                            Else
                                            Begin
                                            For I := 0 To Fields.Count - 1 Do
                                            Begin
                                                F := Fields.Strings[I];
                                                Have := RowsPVGet(Prim, IsVia, F, FpName, FpDesc,
                                                    '', XOrg, YOrg, Units);
                                                If Line <> '' Then Line := Line + ',';
                                                Line := Line + RowsCellCached(Types.Strings[I], Have, I, CellCache);
                                            End;
                                            Lines.Add('[' + Line + ']');
                                            End;
                                        End;
                                    End;
                                End;
                            End;
                            If Not Stop Then Prim := GrpIter.NextPCBObject;
                        End;
                    Finally
                        Footprint.GroupIterator_Destroy(GrpIter);
                    End;
                End;
                If Not Stop Then Footprint := Iter.NextPCBObject;
            End;
        Finally
            PcbLib.LibraryIterator_Destroy(Iter);
        End;

        Result := BuildSuccessResponse(RequestId,
            RowsReadReply('pcblib', KindName, Units, Fields, Lines, Offset, More,
                Scanned, ''));
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
        Seen.Free;
    End;
End;

{ rows.write_rows doc=pcblib kind=via|pad. Every row needs ?footprint=,     }
{ which also limits the walks. Two walks, as the track template: find and  }
{ guard, then write in one edit.                                            }
Function RowsPVLibWrite(Params : String; RequestId : String; IsVia : Boolean) : String;
Var
    Spec, Units, LibPath, UsedPath, Problem, Code, FpName, FpDesc, Addr : String;
    Notes, Rec, LayerCache, KindName, GeomProblem, Keep : String;
    PcbLib : IPCB_Library;
    Iter : IPCB_LibraryIterator;
    Footprint : IPCB_LibComponent;
    GrpIter : IPCB_GroupIterator;
    Prim : IPCB_Primitive;
    Via : IPCB_Via;
    Ids, Texts, Hits, FpNames, Seen, Done, Results : TStringList;
    RS, RE, Keys, Vals, Types : TStringList;
    KeepL, RecL, FpChanged : TStringList;
    XOrg, YOrg, K, S, E, Written, Unchanged, Failed, Repeats, Pass, ObjKind : Integer;
    NChanged : Integer;
    Ok, Changed, Robots : Boolean;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers here (RowsCore) }
    If IsVia Then
    Begin
        Spec := RowsPcbLibViaSpec(0);
        KindName := 'via';
        ObjKind := eViaObject;
    End
    Else
    Begin
        Spec := RowsPcbLibPadSpec(0);
        KindName := 'pad';
        ObjKind := ePadObject;
    End;

    Ids := TStringList.Create;
    Texts := TStringList.Create;
    Hits := TStringList.Create;
    FpNames := TStringList.Create;
    Seen := TStringList.Create;
    Done := TStringList.Create;
    Results := TStringList.Create;
    RS := TStringList.Create;
    RE := TStringList.Create;
    Keys := TStringList.Create;
    Vals := TStringList.Create;
    Types := TStringList.Create;
    KeepL := TStringList.Create;
    RecL := TStringList.Create;
    FpChanged := TStringList.Create;
    Seen.Sorted := True;
    Done.Sorted := True;
    Try
        { Every row is split once, here; the rest reads the flat table. }
        Problem := RowsTakeRows(Spec, Params, 'footprint',
            Ids, Texts, RS, RE, Keys, Vals, Types, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        { The values of a row are checked by rows.py before it is sent        }
        { (rows_rules.py: the only way in); only what needs the document is  }
        { checked here.                                                      }
        For K := 0 To Ids.Count - 1 Do
        Begin
            Hits.Add('0');
            KeepL.Add('-');
            RecL.Add('-');
            FpName := RowsFlatValue(Keys, Vals, StrToInt(RS.Strings[K]),
                StrToInt(RE.Strings[K]), '?footprint');
            If FpNames.IndexOf(FpName) < 0 Then FpNames.Add(FpName);
        End;

        LibPath := ExtractJsonValue(Params, 'library_path');
        PcbLib := RowsOpenPcbLib(LibPath, UsedPath, Problem);
        If PcbLib = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCBLIB', Problem);
            Exit;
        End;

        { undo=false (opt-in, libraries only): no per-object BeginModify /  }
        { EndModify messages. Measured 2026-11-23 on 1000 changed pads:     }
        { write stage 1.69 -> 0.89 s, nothing read back wrong. The cost:    }
        { Altium keeps no undo step for the write, and ONE undo afterwards  }
        { restores the state from before the PREVIOUS undo step, so it       }
        { takes this write and the edit before it. For callers that keep a  }
        { copy of the library first. BeginIgnoreChanges gained               }
        { nothing in a library and is not used.                             }
        Robots := Not RowsUndoOff(Params);
        { Pass 1 finds every row object, checks its guards and works out    }
        { what each row would change (reads only: KeepL / RecL). Pass 2     }
        { writes only the rows that change, walking only the footprints that }
        { hold one and stopping once each is written; nothing to change =   }
        { no edit and no second walk (stage profile, 2026-11-23: the second  }
        { walk was a third of a 1000-pad write that changed nothing).        }
        LayerCache := '';
        Notes := '';
        Written := 0;
        Unchanged := 0;
        Failed := 0;
        Repeats := 0;
        NChanged := 0;
        For Pass := 1 To 2 Do
        Begin
            If Pass = 1 Then RowsProfMark('open') Else RowsProfMark('find');
            If Pass = 2 Then
            Begin
                For K := 0 To Ids.Count - 1 Do
                    If Hits.Strings[K] = '0' Then
                        Notes := Notes + 'row ' + Ids.Strings[K] + ': no ' + KindName
                            + ' with that id in ' + RowsFlatValue(Keys, Vals,
                            StrToInt(RS.Strings[K]), StrToInt(RE.Strings[K]), '?footprint') + ';';
                If Notes <> '' Then
                Begin
                    Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                        Notes + ' nothing was changed; read the ' + KindName + 's again');
                    Exit;
                End;
                For K := 0 To Ids.Count - 1 Do
                    If Pos('1', KeepL.Strings[K]) > 0 Then
                    Begin
                        Inc(NChanged);
                        FpName := RowsFlatValue(Keys, Vals, StrToInt(RS.Strings[K]),
                            StrToInt(RE.Strings[K]), '?footprint');
                        If FpChanged.IndexOf(FpName) < 0 Then FpChanged.Add(FpName);
                    End;
                If NChanged = 0 Then Break;
                PcbBeginEdit(0);
            End;
            Try
                Iter := PcbLib.LibraryIterator_Create;
                Try
                    Footprint := Iter.FirstPCBObject;
                    While (Footprint <> Nil) And ((Pass = 1) Or (Done.Count < NChanged)) Do
                    Begin
                        FpName := '';
                        Try FpName := Footprint.Name; Except End;
                        If ((Pass = 1) And (FpNames.IndexOf(FpName) >= 0))
                            Or ((Pass = 2) And (FpChanged.IndexOf(FpName) >= 0)) Then
                        Begin
                            FpDesc := '';
                            Try FpDesc := Footprint.Description; Except End;
                            XOrg := 0;  YOrg := 0;
                            Try XOrg := Footprint.X; Except End;
                            Try YOrg := Footprint.Y; Except End;
                            GrpIter := Footprint.GroupIterator_Create;
                            Try
                                GrpIter.AddFilter_ObjectSet(MkSet(ObjKind));
                                Prim := GrpIter.FirstPCBObject;
                                While (Prim <> Nil) And ((Pass = 1) Or (Done.Count < NChanged)) Do
                                Begin
                                    Addr := '';
                                    Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
                                    K := -1;
                                    If Addr <> '' Then K := Ids.IndexOf(Addr);
                                    If K >= 0 Then
                                    Begin
                                        S := StrToInt(RS.Strings[K]);
                                        E := StrToInt(RE.Strings[K]);
                                        If Pass = 1 Then
                                        Begin
                                            If Seen.IndexOf(Addr) < 0 Then
                                            Begin
                                                Seen.Add(Addr);
                                                Hits.Strings[K] := '1';
                                                { Guards and what would change, in one pass  }
                                                { over the row; a row that changes nothing    }
                                                { has its read-back in Rec already.           }
                                                Rec := '';
                                                KeepL.Strings[K] := RowsPVFirstLook(Prim, IsVia,
                                                    Keys, Vals, Types, S, E, FpName, FpDesc, XOrg,
                                                    YOrg, Units, PcbLib.Board, Addr, LayerCache,
                                                    Rec, Notes);
                                                If IsVia Then
                                                Begin
                                                    Via := Prim;
                                                    GeomProblem := RowsPVViaGeomProblem(Via,
                                                        RowsFlatValue(Keys, Vals, S, E, 'hole'),
                                                        RowsFlatValue(Keys, Vals, S, E, 'size'), Units);
                                                    If GeomProblem <> '' Then
                                                        Notes := Notes + 'row ' + Addr + ': '
                                                            + GeomProblem + ';';
                                                End;
                                                RecL.Strings[K] := Rec;
                                            End
                                            Else
                                                Inc(Repeats);
                                        End
                                        Else If (Done.IndexOf(Addr) < 0)
                                            And (Pos('1', KeepL.Strings[K]) > 0) Then
                                        Begin
                                            Done.Add(Addr);
                                            Ok := RowsPVApply(Prim, IsVia, Keys, Vals, S, E,
                                                KeepL.Strings[K], XOrg, YOrg, Units, True, Addr,
                                                Notes, Robots);
                                            Inc(Written);
                                            Rec := '';
                                            If Not RowsPVCheck(Prim, IsVia, Keys, Vals, Types,
                                                S, E, FpName, FpDesc, '', XOrg, YOrg, Units,
                                                Addr, Rec, Notes) Then
                                                Ok := False;
                                            If Not Ok Then Inc(Failed);
                                            Results.Add('{"id":"' + Addr + '","ok":'
                                                + BoolToJsonStr(Ok) + ',"changed":true' + Rec + '}');
                                        End;
                                    End;
                                    Prim := GrpIter.NextPCBObject;
                                End;
                            Finally
                                Footprint.GroupIterator_Destroy(GrpIter);
                            End;
                        End;
                        Footprint := Iter.NextPCBObject;
                    End;
                Finally
                    PcbLib.LibraryIterator_Destroy(Iter);
                End;
            Finally
                If Pass = 2 Then PcbEndEdit(0);
            End;
            If (Pass = 1) And (Notes <> '') Then
            Begin
                Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                    Notes + ' nothing was changed; read the ' + KindName + 's again');
                Exit;
            End;
        End;
        { The rows already right: their read-back came from pass 1. A row  }
        { that should have changed but was not reached in pass 2 failed.  }
        For K := 0 To Ids.Count - 1 Do
        Begin
            If Pos('1', KeepL.Strings[K]) = 0 Then
            Begin
                Inc(Unchanged);
                Results.Add('{"id":"' + Ids.Strings[K] + '","ok":true,"changed":false'
                    + RecL.Strings[K] + '}');
            End
            Else If Done.IndexOf(Ids.Strings[K]) < 0 Then
            Begin
                Inc(Failed);
                Notes := Notes + 'row ' + Ids.Strings[K] + ': not found again to write;';
                Results.Add('{"id":"' + Ids.Strings[K] + '","ok":false,"changed":true}');
            End;
        End;
        If NChanged > 0 Then
        Begin
            Try PcbLib.Board.ViewManager_FullUpdate; Except End;
            MarkDocDirtyByPath(UsedPath);
        End;

        Result := BuildSuccessResponse(RequestId,
            RowsWriteReplySkip('pcblib', KindName, Ids.Count, Written, Unchanged, Failed,
                Repeats, Results, Notes));
    Finally
        Ids.Free;  Texts.Free;  Hits.Free;  FpNames.Free;
        Seen.Free;  Done.Free;  Results.Free;
        RS.Free;  RE.Free;  Keys.Free;  Vals.Free;  Types.Free;
        KeepL.Free;  RecL.Free;  FpChanged.Free;
    End;
End;

{..............................................................................}
{ pcb/via and pcb/pad                                                          }
{..............................................................................}

{ Who owns a board primitive: owner free / component, the component        }
{ designator and footprint name.                                            }
Procedure RowsPVOwnerInfo(Prim : IPCB_Primitive; Var Owner : String; Var Des : String;
    Var Pat : String);
Var
    Comp : IPCB_Component;
    NameText : IPCB_Text;
Begin
    Owner := 'free';
    Des := '';
    Pat := '';
    Try
        If Prim.InComponent Then
        Begin
            Owner := 'component';
            Comp := Prim.Component;
            If Comp <> Nil Then
            Begin
                NameText := Comp.Name;
                If NameText <> Nil Then Des := NameText.Text;
                Pat := Comp.Pattern;
            End;
        End;
    Except End;
End;

{ The scope of a board read or write, from its params. }
Function RowsPVScope(Params : String; Board : IPCB_Board; Units : String; IsVia : Boolean;
    Var NetName : String; Var DesList : String; Var Pattern : String; Var OwnerWanted : String;
    Var HasBounds : Boolean; Var BX1 : Integer; Var BY1 : Integer; Var BX2 : Integer;
    Var BY2 : Integer; Var LayerId : TLayer; Var Code : String) : String;
Var
    BoundsStr, LayerStr : String;
Begin
    Result := '';
    Code := '';
    NetName := Trim(ExtractJsonValue(Params, 'net'));
    DesList := Trim(ExtractJsonValue(Params, 'designator'));
    Pattern := Trim(ExtractJsonValue(Params, 'pattern'));
    OwnerWanted := LowerCase(Trim(ExtractJsonValue(Params, 'owner')));
    If OwnerWanted = '' Then OwnerWanted := 'all';
    If Not RowsPVOneOf(OwnerWanted, '|all|free|component|') Then
    Begin
        Code := 'BAD_SCOPE';
        Result := 'owner is free, component or all, not "' + OwnerWanted + '"';
        Exit;
    End;
    If (OwnerWanted = 'free') And ((DesList <> '') Or (Pattern <> '')) Then
    Begin
        Code := 'BAD_SCOPE';
        Result := 'designator and pattern pick the objects of components; owner=free has none';
        Exit;
    End;
    HasBounds := False;
    BX1 := 0;  BY1 := 0;  BX2 := 0;  BY2 := 0;
    BoundsStr := Trim(ExtractJsonValue(Params, 'bounds'));
    If BoundsStr <> '' Then
    Begin
        If Not RowsPVParseBounds(BoundsStr, BX1, BY1, BX2, BY2) Then
        Begin
            Code := 'BAD_SCOPE';
            Result := 'bounds is x1,y1,x2,y2 as coordinates (whole numbers, 1/10000 mil), not "'
                + BoundsStr + '"';
            Exit;
        End;
        HasBounds := True;
    End;
    LayerId := eNoLayer;
    LayerStr := Trim(ExtractJsonValue(Params, 'layer'));
    If LayerStr <> '' Then
    Begin
        If IsVia Then
        Begin
            Code := 'BAD_LAYER';
            Result := 'A via spans layers: use where on low_layer / high_layer instead of layer';
            Exit;
        End;
        LayerId := ResolveLayerId(Board, LayerStr);
        If LayerId = eNoLayer Then
        Begin
            Code := 'BAD_LAYER';
            Result := 'No layer named "' + LayerStr + '" on this board';
            Exit;
        End;
    End;
End;

{ One object a narrowed walk handed out: counted once (Seen), kept when it }
{ is in every part of the scope.                                            }
Procedure RowsPVConsider(Prim : IPCB_Primitive; DesList : String; Pattern : String;
    OwnerWanted : String; HasBounds : Boolean; BX1 : Integer; BY1 : Integer;
    BX2 : Integer; BY2 : Integer; LayerId : TLayer; Matches : TInterfaceList;
    Seen : TStringList; Var Scanned : Integer);
Var
    Addr, Owner, Des, Pat : String;
    X, Y : Integer;
    Found, Keep : Boolean;
Begin
    Addr := '';
    Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
    If (Addr = '') Or (Seen.IndexOf(Addr) >= 0) Then Exit;
    Seen.Add(Addr);
    Inc(Scanned);
    RowsPVOwnerInfo(Prim, Owner, Des, Pat);
    Keep := True;
    If (OwnerWanted <> 'all') And (Owner <> OwnerWanted) Then Keep := False;
    If Keep And (DesList <> '') Then Keep := RowsPVInCsv(DesList, Des);
    If Keep And (Pattern <> '') Then Keep := FilterWildMatch(Pat, Pattern);
    If Keep And (LayerId <> eNoLayer) Then
        Try Keep := Prim.Layer = LayerId; Except Keep := False; End;
    If Keep And HasBounds Then
    Begin
        X := PCBPrimitivePos(Prim, False, Found);
        If Found Then Y := PCBPrimitivePos(Prim, True, Found);
        Keep := Found And (X >= BX1) And (X <= BX2) And (Y >= BY1) And (Y <= BY2);
    End;
    If Keep Then Matches.Add(Prim);
End;

{ Every via or pad of the focused board in a scope, narrowed in Altium:    }
{   net        the net own group (vias and pads of that net only);          }
{   designator / pattern   those components own groups;                      }
{   bounds     a spatial search 100 mil round the box (the index can hold a }
{              via by a box narrower than its printed bounds), the centre   }
{              then checked; a search that finds nothing walks the board;   }
{   else       the board walk, free objects only for owner=free.            }
{ The other scope parts are checked on each object. Returns the objects     }
{ scanned; Note says what could not be narrowed. The list holds the         }
{ objects (never freed: see ProcessPCBBoardObjects).                        }
Function RowsPVCollect(Board : IPCB_Board; IsVia : Boolean; NetName : String;
    DesList : String; Pattern : String; OwnerWanted : String; HasBounds : Boolean;
    BX1 : Integer; BY1 : Integer; BX2 : Integer; BY2 : Integer; LayerId : TLayer;
    Matches : TInterfaceList; Var Note : String) : Integer;
Var
    Iter : IPCB_BoardIterator;
    Group : IPCB_GroupIterator;
    Spatial : IPCB_SpatialIterator;
    Prim : IPCB_Primitive;
    Net : IPCB_Net;
    Comp : IPCB_Component;
    NameText : IPCB_Text;
    Seen : TStringList;
    Des, Pat, NetHave : String;
    ObjKind, Scanned, Margin : Integer;
    NetFound, Walk : Boolean;
Begin
    Note := '';
    Scanned := 0;
    If IsVia Then ObjKind := eViaObject Else ObjKind := ePadObject;
    Seen := TStringList.Create;
    Seen.Sorted := True;
    Try
        Walk := False;
        If NetName <> '' Then
        Begin
            NetFound := False;
            Iter := Board.BoardIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eNetObject));
                Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                Iter.AddFilter_Method(eProcessAll);
                Net := Iter.FirstPCBObject;
                While Net <> Nil Do
                Begin
                    NetHave := '';
                    Try NetHave := Net.Name; Except End;
                    If UpperCase(NetHave) = UpperCase(NetName) Then
                    Begin
                        NetFound := True;
                        Group := Net.GroupIterator_Create;
                        Try
                            Group.AddFilter_ObjectSet(MkSet(ObjKind));
                            Group.AddFilter_AllLayers;
                            Prim := Group.FirstPCBObject;
                            While Prim <> Nil Do
                            Begin
                                RowsPVConsider(Prim, DesList, Pattern, OwnerWanted, HasBounds,
                                    BX1, BY1, BX2, BY2, LayerId, Matches, Seen, Scanned);
                                Prim := Group.NextPCBObject;
                            End;
                        Finally
                            Net.GroupIterator_Destroy(Group);
                        End;
                    End;
                    Net := Iter.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iter);
            End;
            If Not NetFound Then Note := 'no net named "' + NetName + '" on this board';
        End
        Else If (DesList <> '') Or (Pattern <> '') Then
        Begin
            Iter := Board.BoardIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(eComponentObject));
                Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                Iter.AddFilter_Method(eProcessAll);
                Comp := Iter.FirstPCBObject;
                While Comp <> Nil Do
                Begin
                    Des := '';
                    Pat := '';
                    Try
                        NameText := Comp.Name;
                        If NameText <> Nil Then Des := NameText.Text;
                        Pat := Comp.Pattern;
                    Except End;
                    If ((DesList = '') Or RowsPVInCsv(DesList, Des))
                        And ((Pattern = '') Or FilterWildMatch(Pat, Pattern)) Then
                    Begin
                        Group := Comp.GroupIterator_Create;
                        Try
                            Group.AddFilter_ObjectSet(MkSet(ObjKind));
                            Prim := Group.FirstPCBObject;
                            While Prim <> Nil Do
                            Begin
                                RowsPVConsider(Prim, DesList, Pattern, OwnerWanted, HasBounds,
                                    BX1, BY1, BX2, BY2, LayerId, Matches, Seen, Scanned);
                                Prim := Group.NextPCBObject;
                            End;
                        Finally
                            Comp.GroupIterator_Destroy(Group);
                        End;
                    End;
                    Comp := Iter.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iter);
            End;
        End
        Else If HasBounds Then
        Begin
            Margin := 1000000;    { 100 mil in coordinates }
            Spatial := Board.SpatialIterator_Create;
            Try
                Spatial.AddFilter_ObjectSet(MkSet(ObjKind));
                Spatial.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                Spatial.AddFilter_Area(BX1 - Margin, BY1 - Margin, BX2 + Margin, BY2 + Margin);
                Prim := Spatial.FirstPCBObject;
                While Prim <> Nil Do
                Begin
                    RowsPVConsider(Prim, DesList, Pattern, OwnerWanted, HasBounds,
                        BX1, BY1, BX2, BY2, LayerId, Matches, Seen, Scanned);
                    Prim := Spatial.NextPCBObject;
                End;
            Finally
                Board.SpatialIterator_Destroy(Spatial);
            End;
            { The index is a shortcut, not the last word (CollectPCBMatches). }
            If Matches.Count = 0 Then
            Begin
                Walk := True;
                Note := 'the spatial search found nothing in bounds; the board was walked';
            End;
        End
        Else
            Walk := True;

        If Walk Then
        Begin
            Iter := Board.BoardIterator_Create;
            Try
                Iter.AddFilter_ObjectSet(MkSet(ObjKind));
                Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
                If OwnerWanted = 'free' Then Iter.AddFilter_Method(eProcessFree)
                Else Iter.AddFilter_Method(eProcessAll);
                Prim := Iter.FirstPCBObject;
                While Prim <> Nil Do
                Begin
                    RowsPVConsider(Prim, DesList, Pattern, OwnerWanted, HasBounds,
                        BX1, BY1, BX2, BY2, LayerId, Matches, Seen, Scanned);
                    Prim := Iter.NextPCBObject;
                End;
            Finally
                Board.BoardIterator_Destroy(Iter);
            End;
        End;
    Finally
        Seen.Free;
    End;
    Result := Scanned;
End;

{ rows.read_rows doc=pcb kind=via|pad on the focused board. Scope: net,    }
{ designator (comma list), pattern (footprint name, wildcards), owner       }
{ free / component / all, bounds x1,y1,x2,y2 (absolute), layer             }
{ (pads only).                                                              }
Function RowsPVPcbRead(Params : String; RequestId : String; IsVia : Boolean) : String;
Var
    CellCache : TStringList;
    Spec, Units, Problem, Code, Path, Line, F, Have, KindName, Note : String;
    NetName, DesList, Pattern, OwnerWanted, Owner, Des, Pat : String;
    Board : IPCB_Board;
    Prim : IPCB_Primitive;
    Matches : TInterfaceList;
    Fields, Types, Conds, Lines, CF, CO, CV, CT : TStringList;
    LayerId : TLayer;
    HasBounds, Keep, More : Boolean;
    BX1, BY1, BX2, BY2, XOrg, YOrg, Offset, Limit, Matched, Scanned, I, J : Integer;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers here (RowsCore) }
    If IsVia Then
    Begin
        Spec := RowsPcbViaSpec(0);
        KindName := 'via';
    End
    Else
    Begin
        Spec := RowsPcbPadSpec(0);
        KindName := 'pad';
    End;
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
        Problem := RowsPVScope(Params, Board, Units, IsVia, NetName, DesList, Pattern,
            OwnerWanted, HasBounds, BX1, BY1, BX2, BY2, LayerId, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        { Absolute board coordinates, as rows pcb/track and the generic X / Y. }
        XOrg := 0;  YOrg := 0;

        Matches := TInterfaceList.Create;
        Scanned := RowsPVCollect(Board, IsVia, NetName, DesList, Pattern, OwnerWanted,
            HasBounds, BX1, BY1, BX2, BY2, LayerId, Matches, Note);

        Matched := 0;
        More := False;
        For J := 0 To Matches.Count - 1 Do
        Begin
            Prim := Matches.Items[J];
            If Prim = Nil Then Continue;
            RowsPVOwnerInfo(Prim, Owner, Des, Pat);
            Keep := True;
            I := 0;
            While Keep And (I < Conds.Count) Do
            Begin
                Have := RowsPVGet(Prim, IsVia, RowsCol(CF, I), Owner, Des, Pat,
                    XOrg, YOrg, Units);
                Keep := RowsCondHolds(RowsCol(CT, I), Have, RowsCol(CO, I), RowsCol(CV, I));
                Inc(I);
            End;
            If Keep Then
            Begin
                Inc(Matched);
                If Matched > Offset Then
                Begin
                    If (Limit > 0) And (Lines.Count >= Limit) Then
                    Begin
                        More := True;
                        Break;
                    End;
                    Line := '';
                    For I := 0 To Fields.Count - 1 Do
                    Begin
                        F := Fields.Strings[I];
                        If Line <> '' Then Line := Line + ',';
                        Line := Line + RowsCellCached(Types.Strings[I],
                            RowsPVGet(Prim, IsVia, F, Owner, Des, Pat, XOrg, YOrg, Units), I, CellCache);
                    End;
                    Lines.Add('[' + Line + ']');
                End;
            End;
        End;

        Result := BuildSuccessResponse(RequestId,
            RowsReadReply('pcb', KindName, Units, Fields, Lines, Offset, More,
                Scanned, Note));
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
    End;
End;

{ rows.write_rows doc=pcb kind=via|pad on the focused board. No container  }
{ guard; the read scope params (net, designator, pattern, owner, bounds,    }
{ layer) are taken here too and narrow the walk that finds the ids. Two    }
{ passes over the narrowed objects: find and guard, then write in ONE edit }
{ (one undo step on a board). After each object, EnsureIndexed puts it    }
{ back in the spatial index if the write left it worse (bug 41).           }
Function RowsPVPcbWrite(Params : String; RequestId : String; IsVia : Boolean) : String;
Var
    Spec, Units, Problem, Code, Path, KindName, Note, Addr : String;
    NetName, DesList, Pattern, OwnerWanted, Owner, Des, Pat : String;
    Notes, Rec, LayerCache, GeomProblem : String;
    Board : IPCB_Board;
    Prim : IPCB_Primitive;
    Via : IPCB_Via;
    Matches : TInterfaceList;
    Ids, Texts, Hits, Results : TStringList;
    RS, RE, Keys, Vals, Types : TStringList;
    LayerId : TLayer;
    HasBounds, Ok, Indexed : Boolean;
    BX1, BY1, BX2, BY2, XOrg, YOrg, J, K, F, S, E, Written, Failed, Before, Held : Integer;
Begin
    Units := ExtractJsonValue(Params, 'units');
    If UnitsProblem(Units) <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_UNITS', UnitsProblem(Units));
        Exit;
    End;
    Units := 'coord';    { numbers are scaled integers here (RowsCore) }
    If IsVia Then
    Begin
        Spec := RowsPcbViaSpec(0);
        KindName := 'via';
    End
    Else
    Begin
        Spec := RowsPcbPadSpec(0);
        KindName := 'pad';
    End;

    Ids := TStringList.Create;
    Texts := TStringList.Create;
    Hits := TStringList.Create;
    Results := TStringList.Create;
    RS := TStringList.Create;
    RE := TStringList.Create;
    Keys := TStringList.Create;
    Vals := TStringList.Create;
    Types := TStringList.Create;
    Try
        { Every row is split once, here; the rest reads the flat table. }
        Problem := RowsTakeRows(Spec, Params, '', Ids, Texts, RS, RE,
            Keys, Vals, Types, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        { The values of a row are checked by rows.py before it is sent        }
        { (rows_rules.py: the only way in); only what needs the document is  }
        { checked here.                                                      }
        For K := 0 To Ids.Count - 1 Do Hits.Add('0');

        Board := RowsFocusedBoard(Path, Problem);
        If Board = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'NO_PCB', Problem);
            Exit;
        End;
        Problem := RowsPVScope(Params, Board, Units, IsVia, NetName, DesList, Pattern,
            OwnerWanted, HasBounds, BX1, BY1, BX2, BY2, LayerId, Code);
        If Problem <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, Code, Problem);
            Exit;
        End;
        { Absolute board coordinates, as rows pcb/track and the generic X / Y. }
        XOrg := 0;  YOrg := 0;

        Matches := TInterfaceList.Create;
        RowsPVCollect(Board, IsVia, NetName, DesList, Pattern, OwnerWanted,
            HasBounds, BX1, BY1, BX2, BY2, LayerId, Matches, Note);

        { Pass 1: find every row object, check its guards. Nothing written. }
        LayerCache := '';
        Notes := '';
        For J := 0 To Matches.Count - 1 Do
        Begin
            Prim := Matches.Items[J];
            If Prim = Nil Then Continue;
            Addr := '';
            Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
            K := -1;
            If Addr <> '' Then K := Ids.IndexOf(Addr);
            If K < 0 Then Continue;
            Hits.Strings[K] := '1';
            S := StrToInt(RS.Strings[K]);
            E := StrToInt(RE.Strings[K]);
            RowsPVOwnerInfo(Prim, Owner, Des, Pat);
            Notes := Notes + RowsPVGuards(Prim, IsVia, Keys, Vals, Types, S, E, Owner,
                Des, Pat, XOrg, YOrg, Units, Board, Addr, LayerCache);
            If IsVia Then
            Begin
                Via := Prim;
                GeomProblem := RowsPVViaGeomProblem(Via, RowsFlatValue(Keys, Vals, S, E, 'hole'),
                    RowsFlatValue(Keys, Vals, S, E, 'size'), Units);
                If GeomProblem <> '' Then
                    Notes := Notes + 'row ' + Addr + ': ' + GeomProblem + ';';
            End;
        End;
        For K := 0 To Ids.Count - 1 Do
            If Hits.Strings[K] = '0' Then
                Notes := Notes + 'row ' + Ids.Strings[K] + ': no ' + KindName
                    + ' with that id in the scope given;';
        If Notes <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'ROWS_REFUSED',
                Notes + ' nothing was changed; read the ' + KindName + 's again');
            Exit;
        End;

        { Pass 2: write each object once, all in one edit. }
        Written := 0;
        Failed := 0;
        PcbBeginEdit(0);
        Try
            For J := 0 To Matches.Count - 1 Do
            Begin
                Prim := Matches.Items[J];
                If Prim = Nil Then Continue;
                Addr := '';
                Try Addr := IntToStr(Prim.I_ObjectAddress); Except End;
                K := -1;
                If Addr <> '' Then K := Ids.IndexOf(Addr);
                If K < 0 Then Continue;
                S := StrToInt(RS.Strings[K]);
                E := StrToInt(RE.Strings[K]);
                RowsPVOwnerInfo(Prim, Owner, Des, Pat);
                { Only a VIA's mask can leave the index stale (#208: its box  }
                { includes the mask; a pad's does not, sizes re-index). The   }
                { check costs six spatial searches, so only those rows pay.   }
                Indexed := False;
                If IsVia Then
                    For F := S To E - 1 Do
                        If RowsFlatWrites(Keys, F) And ((Pos('mask', Keys.Strings[F]) > 0)
                            Or (Pos('tenting', Keys.Strings[F]) > 0)) Then
                            Indexed := True;
                If Indexed Then Before := BoardIndexState(Board, Prim);
                Rec := '';
                Ok := RowsPVApply(Prim, IsVia, Keys, Vals, S, E, '', XOrg, YOrg, Units,
                    False, Addr, Notes, True);
                If Indexed Then
                Begin
                    Held := EnsureIndexed(Board, Prim, Before);
                    If Held >= 2 Then
                        Notes := Notes + 'row ' + Addr + ': the spatial index may hold the old '
                            + 'box (a search by area can miss it until the board is reopened);';
                End;
                If Not RowsPVCheck(Prim, IsVia, Keys, Vals, Types, S, E, Owner, Des, Pat,
                    XOrg, YOrg, Units, Addr, Rec, Notes) Then
                    Ok := False;
                If Not Ok Then Inc(Failed);
                Inc(Written);
                Results.Add('{"id":"' + Addr + '","ok":' + BoolToJsonStr(Ok) + Rec + '}');
            End;
        Finally
            PcbEndEdit(0);
        End;
        Try Board.ViewManager_FullUpdate; Except End;
        MarkDocDirtyByPath(Board.FileName);

        Result := BuildSuccessResponse(RequestId,
            RowsWriteReply('pcb', KindName, Ids.Count, Written, Failed, 0, Results, Notes));
    Finally
        Ids.Free;  Texts.Free;  Hits.Free;  Results.Free;
        RS.Free;  RE.Free;  Keys.Free;  Vals.Free;  Types.Free;
    End;
End;

{..............................................................................}
{ The entry points Rows.pas routes to.                                         }
{..............................................................................}

Function RowsPcbLibViaRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVLibRead(Params, RequestId, True);
End;

Function RowsPcbLibViaWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVLibWrite(Params, RequestId, True);
End;

Function RowsPcbLibPadRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVLibRead(Params, RequestId, False);
End;

Function RowsPcbLibPadWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVLibWrite(Params, RequestId, False);
End;

Function RowsPcbViaRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVPcbRead(Params, RequestId, True);
End;

Function RowsPcbViaWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVPcbWrite(Params, RequestId, True);
End;

Function RowsPcbPadRead(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVPcbRead(Params, RequestId, False);
End;

Function RowsPcbPadWrite(Params : String; RequestId : String) : String;
Begin
    Result := RowsPVPcbWrite(Params, RequestId, False);
End;

{ UnitStamp_RowsPadVia - which build of RowsPadVia.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_RowsPadVia(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
