{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ PCBGeneric.pas - PCB object primitives for the Altium integration bridge                  }
{ Parallel to Generic.pas but for PCBServer / IPCB_* objects.               }
{..............................................................................}

{ ObjectTypeFromStringPCB moved to Utils.pas: Library.pas builds BEFORE
  this file and needs it, and a call to a function defined later in the
  concatenation resolves to nothing at runtime. }

{..............................................................................}
{ PCB Property Getter, late-bound, returns '' on unsupported properties     }
{..............................................................................}

{ WHERE A PCB PRIMITIVE KEEPS ITS POSITION, WHICH IS NOT ONE MEMBER.        }
{                                                                            }
{ Reading Obj.x off the declared IPCB_Primitive worked for the types that     }
{ happen to publish it and raised "Undeclared identifier: x" for the rest.    }
{ That is not a tool error: the script engine shows it as a modal before any  }
{ Try/Except runs, so it stops the polling loop and the session has to be     }
{ restarted by hand. Reported from a live board by an obj_query for X on an   }
{ eTextObject.                                                                }
{                                                                            }
{ The mapping below uses only members this build already exercises elsewhere: }
{ Pad.x / Via.x / Comp.x in PCB.pas, Text.XLocation in Generic.pas and        }
{ Library.pas, Arc.XCenter and Fill.X1Location in PCB.pas.                    }
{                                                                            }
{ A track, a region and a polygon have no single position and are reported    }
{ as unreadable rather than answered with one end of themselves. Same reason  }
{ the schematic side gained SchObjectHasText: a type that lacks the member is }
{ told so, and the caller gets a reply instead of a stalled loop.             }
Function PCBPrimitivePos(Obj : IPCB_Primitive; WantY : Boolean;
                         Var Found : Boolean) : Integer;
Var
    Oid   : Integer;
    Pad   : IPCB_Pad;
    Via   : IPCB_Via;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Arc   : IPCB_Arc;
    Fill  : IPCB_Fill;
    Body  : IPCB_ComponentBody;
Begin
    Result := 0;
    Found := False;
    Oid := Obj.ObjectId;
    Try
        If Oid = ePadObject Then
        Begin
            Pad := Obj;
            If WantY Then Result := Pad.y Else Result := Pad.x;
            Found := True;
        End
        Else If Oid = eViaObject Then
        Begin
            Via := Obj;
            If WantY Then Result := Via.y Else Result := Via.x;
            Found := True;
        End
        Else If Oid = eComponentObject Then
        Begin
            Comp := Obj;
            If WantY Then Result := Comp.y Else Result := Comp.x;
            Found := True;
        End
        Else If Oid = eComponentBodyObject Then
        Begin
            Body := Obj;
            If WantY Then Result := Body.y Else Result := Body.x;
            Found := True;
        End
        Else If Oid = eTextObject Then
        Begin
            Txt := Obj;
            If WantY Then Result := Txt.YLocation Else Result := Txt.XLocation;
            Found := True;
        End
        Else If Oid = eArcObject Then
        Begin
            Arc := Obj;
            If WantY Then Result := Arc.YCenter Else Result := Arc.XCenter;
            Found := True;
        End
        Else If Oid = eFillObject Then
        Begin
            Fill := Obj;
            If WantY Then Result := Fill.Y1Location Else Result := Fill.X1Location;
            Found := True;
        End;
    Except
        Found := False;
    End;
End;

{ A ratsnest line's redundancy and connection mode. Each is read in a     }
{ function of its own: both come from the API reference and neither had   }
{ been called from a script here, and an identifier the script engine does }
{ not know halts the polling loop where no Try catches it. Apart, a build  }
{ without one fails only a query that asks for it.                         }
Function PCBConnRedundant(Conn : IPCB_Connection) : String;
Begin
    Result := BoolToJsonStr(Conn.IsRedundant);
End;

Function PCBConnMode(Conn : IPCB_Connection) : String;
Var
    M : Integer;
Begin
    M := Conn.Mode;
    Result := IntToStr(M);
End;

Function GetPCBProperty(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Track : IPCB_Track;
    Arc   : IPCB_Arc;
    Pad   : IPCB_Pad;
    Via   : IPCB_Via;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Rgn   : IPCB_Region;
    Poly  : IPCB_Polygon;
    Body  : IPCB_ComponentBody;
    Conn  : IPCB_Connection;
    Oid   : Integer;
    PosVal : Integer;
    PosFound : Boolean;
Begin
    Result := '';
    Try
        Oid := Obj.ObjectId;
        { Base IPCB_Primitive members, valid to read on ANY primitive. }
        If PropName = 'ObjectId'        Then Result := IntToStr(Oid)
        Else If (PropName = 'X') Or (PropName = 'Y') Then
        Begin
            PosVal := PCBPrimitivePos(Obj, PropName = 'Y', PosFound);
            If PosFound Then
                Result := FloatToJsonStr(CoordToMilsF(PosVal))   { sub-mil coordinates: local patch 2026-09-18 }
            Else
            Begin
                { A track has two ends and a region has an outline, so       }
                { answering with either would be a coordinate the caller     }
                { would then act on. Say it is not on this type instead.     }
                NotePropertyDiag('unreadable', PropName);
                Result := '';
            End;
        End
        Else If PropName = 'Layer'      Then Result := LayerDisplayName(Obj.Layer)
        Else If PropName = 'Descriptor' Then Result := Obj.Descriptor
        Else If PropName = 'Selected'   Then Result := BoolToJsonStr(Obj.Selected)
        { 'Net.Name' is accepted as well as 'Net'. Designator.Text and
          Comment.Text are already accepted alongside their bare forms,
          so a caller who used one of those infers a dotted rule that
          held twice and failed here, silently, returning empty as
          though the copper had no net. Measured: a session concluded
          the bridge could not attribute copper to a net at all and
          stopped, when the property was simply spelled differently. }
        Else If (PropName = 'Net') Or (PropName = 'Net.Name') Then
        Begin
            If Obj.Net <> Nil Then Result := Obj.Net.Name;
        End
        { WHAT A PRIMITIVE BELONGS TO. The walk below takes every primitive  }
        { on the board, so a footprint's own tracks and a hatched pour's     }
        { tracks come through with the routing, on the same copper layers,   }
        { and a filter on Layer alone cannot keep to routing. These let it:  }
        { InComponent=false|InPolygon=false is free copper.                  }
        Else If PropName = 'InComponent' Then
        Begin
            Result := BoolToJsonStr(Obj.InComponent);
        End
        Else If PropName = 'InPolygon' Then
        Begin
            Result := BoolToJsonStr(Obj.InPolygon);
        End
        Else If PropName = 'IsKeepout' Then
        Begin
            Result := BoolToJsonStr(Obj.IsKeepout);
        End
        Else If PropName = 'TearDrop' Then
        Begin
            Result := BoolToJsonStr(Obj.TearDrop);
        End
        Else If PropName = 'Locked' Then
        Begin
            Result := BoolToJsonStr(Not Obj.Moveable);
        End
        Else If PropName = 'Component' Then
        Begin
            { The owning footprint's designator; empty for a free primitive. }
            If Obj.InComponent Then
            Begin
                If Obj.Component <> Nil Then Result := Obj.Component.Name.Text;
            End;
        End
        { Subtype members. DelphiScript resolves members against the DECLARED }
        { type, so Obj.X1 on an IPCB_Primitive is "Undeclared identifier".    }
        { Narrow to a typed local via ObjectId (no Forward casts in script).  }
        { A ratsnest line has two ends the same way a track does. }
        Else If PropName = 'X1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.X1)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.X1)); End;
        End
        Else If PropName = 'Y1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.Y1)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.Y1)); End;
        End
        Else If PropName = 'X2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.X2)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.X2)); End;
        End
        Else If PropName = 'Y2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.Y2)); End
            Else If Oid = eConnectionObject Then Begin Conn := Obj; Result := FloatToJsonStr(CoordToMilsF(Conn.Y2)); End;
        End
        Else If PropName = 'Layer1' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := GetLayerString(Conn.Layer1); End;
        End
        Else If PropName = 'Layer2' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := GetLayerString(Conn.Layer2); End;
        End
        Else If PropName = 'IsRedundant' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := PCBConnRedundant(Conn); End;
        End
        Else If PropName = 'Mode' Then
        Begin
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := PCBConnMode(Conn); End;
        End
        Else If PropName = 'Width' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.Width)); End
            Else If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(CoordToMilsF(Arc.LineWidth)); End
            Else If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToJsonStr(CoordToMilsF(Txt.Width)); End;
        End
        Else If PropName = 'XCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.XCenter)); End;
        End
        Else If PropName = 'YCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.YCenter)); End;
        End
        Else If PropName = 'Radius' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := IntToStr(CoordToMils(Arc.Radius)); End;
        End
        Else If PropName = 'StartAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(Arc.StartAngle); End;
        End
        Else If PropName = 'EndAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(Arc.EndAngle); End;
        End
        Else If PropName = 'HoleSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(CoordToMilsF(Pad.HoleSize)); End
            Else If Oid = eViaObject Then Begin Via := Obj; Result := FloatToJsonStr(CoordToMilsF(Via.HoleSize)); End;
        End
        Else If PropName = 'TopXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(CoordToMilsF(Pad.TopXSize)); End;
        End
        Else If PropName = 'TopYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(CoordToMilsF(Pad.TopYSize)); End;
        End
        Else If PropName = 'TopShape' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := IntToStr(Pad.TopShape); End;
        End
        Else If PropName = 'Size' Then
        Begin
            If Oid = eViaObject Then Begin Via := Obj; Result := FloatToJsonStr(CoordToMilsF(Via.Size)); End
            Else If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToJsonStr(CoordToMilsF(Txt.Size)); End;
        End
        Else If PropName = 'Rotation' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := FloatToJsonStr(Comp.Rotation); End
            Else If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(Pad.Rotation); End
            Else If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToJsonStr(Txt.Rotation); End;
        End
        Else If PropName = 'Pattern' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Pattern; End;
        End
        Else If PropName = 'SourceDesignator' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceDesignator; End;
        End
        Else If PropName = 'Name' Then
        Begin
            { Component Name is an IPCB_Text; return its .Text, not the object }
            { (Dispatch->OleStr otherwise crashed EscapeJsonString via modal). }
            { A pad's Name is its designator, a plain string. It read as empty, }
            { so a Name=1 filter matched no pad at all.                        }
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Name.Text; End;
            If Oid = ePadObject Then Begin Pad := Obj; Result := Pad.Name; End;
            { A region's Name: a plain string (IterateRegions.pas reads it). }
            If Oid = eRegionObject Then Begin Rgn := Obj; Result := Rgn.Name; End;
        End
        Else If (PropName = 'Designator') Or (PropName = 'Designator.Text') Then
        Begin
            { A pad's designator is its Name, so Designator reads it too. }
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Name.Text; End;
            If Oid = ePadObject Then Begin Pad := Obj; Result := Pad.Name; End;
        End
        Else If (PropName = 'Comment') Or (PropName = 'Comment.Text') Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.Comment.Text; End;
        End
        Else If PropName = 'Text' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := Txt.Text; End;
        End
        { A text's height and stroke width under the names a caller reaches  }
        { for (Altium's own are Size and Width), and whether it is a        }
        { component's designator or comment, so a filter can pick those out. }
        Else If PropName = 'Height' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToJsonStr(CoordToMilsF(Txt.Size)); End;
        End
        Else If PropName = 'StrokeWidth' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToJsonStr(CoordToMilsF(Txt.Width)); End;
        End
        Else If PropName = 'IsDesignator' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := BoolToJsonStr(Txt.IsDesignator); End;
        End
        Else If PropName = 'IsComment' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := BoolToJsonStr(Txt.IsComment); End;
        End
        Else If PropName = 'UseTTFonts' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := BoolToJsonStr(Txt.UseTTFonts); End;
        End
        Else If PropName = 'MirrorFlag' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Result := BoolToJsonStr(Txt.MirrorFlag); End;
        End
        { WRITABLE AND UNREADABLE IS THE SAME BUG IN THE OTHER DIRECTION.
          These three were added to the writer and not to this reader, so
          a caller could set a pour option and had no way to confirm it,
          which is the exact failure the writer was fixed for. Found by
          asking for them on a live board and being told they are not PCB
          properties. }
        Else If PropName = 'RemoveDead' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Result := BoolToJsonStr(Poly.RemoveDead); End;
        End
        Else If PropName = 'RemoveNarrowNecks' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Result := BoolToJsonStr(Poly.RemoveNarrowNecks); End;
        End
        Else If PropName = 'RemoveIslandsByArea' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Result := BoolToJsonStr(Poly.RemoveIslandsByArea); End;
        End
        Else If PropName = 'StandoffHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Result := IntToStr(CoordToMils(Body.StandoffHeight)); End;
        End
        Else If PropName = 'OverallHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Result := IntToStr(CoordToMils(Body.OverallHeight)); End;
        End
        { A body's opacity, 0 (clear) to 1 (solid), as the Properties panel  }
        { shows it divided by 100. BodyOpacity3D is the member several      }
        { scripts in reference/ use; a bare Opacity is not one.             }
        Else If PropName = 'BodyOpacity3D' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Result := FloatToJsonStr(Body.BodyOpacity3D); End;
        End
        { A REGION'S KIND IS WHAT MAKES IT A BOARD CUTOUT, and nothing
          here could see it. Reported from a live board as the flag not
          being reachable through this API; it is
            Property Kind : TRegionKind Read GetState_Kind
                                        Write SetState_Kind;
          and we had simply never exposed it.

          Returned as a word rather than an ordinal. The numbers are not
          documented anywhere this project can verify, and publishing an
          unverified number invites a caller to write it back. }
        Else If (PropName = 'Kind') Or (PropName = 'RegionKind') Then
        Begin
            If Oid = eRegionObject Then
            Begin
                Rgn := Obj;
                If Rgn.Kind = eRegionKind_BoardCutout Then Result := 'board_cutout'
                Else If Rgn.Kind = eRegionKind_Cutout Then Result := 'cutout'
                Else If Rgn.Kind = eRegionKind_Copper Then Result := 'copper'
                Else If Rgn.Kind = eRegionKind_NamedRegion Then Result := 'named_region'
                Else If Rgn.Kind = eRegionKind_Cavity Then Result := 'cavity'
                Else Result := 'unknown';
            End;
        End;
    Except
        Result := '';
    End;
End;

{..............................................................................}
{ PCB Property Setter                                                        }
{..............................................................................}

{ A caller-supplied layer name reaching a primitive through obj_modify.       }
{ GetLayerFromString answered eTopLayer for every name it did not know, so    }
{ set="Layer=Internal Plane 1" MOVED the primitive to the top copper layer.   }
{ ResolveLayerId asks the board's own stack instead, and an unresolvable      }
{ name is refused rather than silently rounded to the top.                    }
{                                                                             }
{ Reports whether it applied, because the caller now has somewhere to put     }
{ that. ProcessActivePCBDoc still rejects the whole call up front via         }
{ UnresolvedLayerAssignment, before any object has been touched, which is     }
{ the stronger guarantee: a partly-applied batch is worse than a refused one. }
Function SetPrimitiveLayerByName(Obj : IPCB_Primitive; Value : String) : Boolean;
Var
    Lyr : TLayer;
Begin
    Result := False;
    Lyr := ResolveLayerId(GetPCBBoardAnywhere(0), Value);
    If Lyr <> eNoLayer Then
    Begin
        Obj.Layer := Lyr;
        Result := True;
    End;
End;

{ Returns: 1 = handled, 0 = unknown property name, -1 = write threw.        }
{                                                                           }
{ THIS USED TO BE A Procedure, and that is the whole bug. It reported       }
{ nothing, so a caller could not tell a property this build writes from one }
{ it has never heard of, and modify_objects answered with a match count     }
{ either way. Measured on a live board: setting RemoveDead on a polygon     }
{ came back matched:2 having written nothing, and the operator reasonably   }
{ concluded the property was not writable. It is:                           }
{ IPCB_Polygon declares                                                     }
{   Property RemoveDead : Boolean Read GetState_RemoveDead                  }
{                                 Write SetState_RemoveDead;                }
{ There was simply no case for it here.                                     }
{                                                                           }
{ The schematic writer was given this contract and the PCB one was not, so  }
{ the same class of silent failure survived on this side. Both now feed the }
{ one buffer in Utils.                                                       }
{                                                                           }
{ The error channel this adds is the one SetPrimitiveLayerByName above was   }
{ written without: an unresolvable layer name is now reported as a failed    }
{ write rather than left quietly unapplied.                                  }
{ Properties whose value is a length in mils. }
Function IsLengthProperty(PropName : String) : Boolean;
Begin
    Result := (PropName = 'X') Or (PropName = 'Y') Or (PropName = 'X1') Or
        (PropName = 'Y1') Or (PropName = 'X2') Or (PropName = 'Y2') Or
        (PropName = 'Width') Or (PropName = 'HoleSize') Or
        (PropName = 'TopXSize') Or (PropName = 'TopYSize') Or
        (PropName = 'StandoffHeight') Or (PropName = 'OverallHeight') Or
        (PropName = 'Height') Or (PropName = 'StrokeWidth') Or
        (PropName = 'Size');
End;

{ A text's height or stroke width, inside the modify bracket Altium wants: }
{ the text's own, and its component's when it is a designator or comment,  }
{ as the community designator scripts do it (AdjustDesignators2.pas).      }
Procedure SetTextGeometry(Txt : IPCB_Text; Height : Boolean; Coord : TCoord);
Var
    Owner : IPCB_Component;
Begin
    Owner := Nil;
    If Txt.InComponent Then Owner := Txt.Component;
    If Owner <> Nil Then Owner.BeginModify;
    Txt.BeginModify;
    If Height Then Txt.Size := Coord Else Txt.Width := Coord;
    Txt.EndModify;
    Txt.GraphicallyInvalidate;
    If Owner <> Nil Then Owner.EndModify;
End;

{ Mirror or un-mirror a text WHERE IT STANDS. Altium flips a text about its }
{ anchor, so the text would jump by its own width; it is moved back by the  }
{ shift of its bounding box, which holds at any rotation.                   }
Procedure SetTextMirrorInPlace(Txt : IPCB_Text; Mirror : Boolean);
Var
    Owner : IPCB_Component;
    Before, After : TCoordRect;
Begin
    If Txt.MirrorFlag = Mirror Then Exit;
    Owner := Nil;
    If Txt.InComponent Then Owner := Txt.Component;
    If Owner <> Nil Then Owner.BeginModify;
    Before := Txt.BoundingRectangle;
    Txt.BeginModify;
    Txt.MirrorFlag := Mirror;
    Txt.EndModify;
    After := Txt.BoundingRectangle;
    If (After.Left <> Before.Left) Or (After.Bottom <> Before.Bottom) Then
        Txt.MoveByXY(Before.Left - After.Left, Before.Bottom - After.Bottom);
    Txt.GraphicallyInvalidate;
    If Owner <> Nil Then Owner.EndModify;
End;

{ A via's diameter and hole, written together inside the via's modify    }
{ messages, in the order that never leaves the hole as large as the pad   }
{ on the way: the hole first when it fits inside the current pad, else    }
{ the diameter first. Refused, writing nothing, when the result has no    }
{ annular ring: pcb_normalize_vias once set nearly every via on a board  }
{ to a pad no larger than its hole. True only when both read back.       }
Function SetViaGeometry(Via : IPCB_Via; Size, Hole : TCoord) : Boolean;
Begin
    Result := False;
    If (Hole <= 0) Or (Size <= Hole) Then Exit;
    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
        PCBM_BeginModify, c_NoEventData);
    If Hole < Via.Size Then
    Begin
        Via.HoleSize := Hole;
        Via.Size := Size;
    End
    Else
    Begin
        Via.Size := Size;
        Via.HoleSize := Hole;
    End;
    PCBServer.SendMessageToRobots(Via.I_ObjectAddress, c_Broadcast,
        PCBM_EndModify, c_NoEventData);
    Result := (Via.Size = Size) And (Via.HoleSize = Hole);
End;

Function SetPCBProperty(Obj : IPCB_Primitive; PropName : String; Value : String) : Integer;
Var
    Track : IPCB_Track;
    Pad   : IPCB_Pad;
    Via   : IPCB_Via;
    Comp  : IPCB_Component;
    Txt   : IPCB_Text;
    Poly  : IPCB_Polygon;
    Body  : IPCB_ComponentBody;
    Rgn   : IPCB_Region;
    Oid   : Integer;
    Matched, Refused : Boolean;
    PosVal : Integer;
    PosFound : Boolean;
Begin
    Result := 0;
    Matched := True;
    Refused := False;
    Try
        Oid := Obj.ObjectId;
        { A LENGTH GOES IN AS DECIMAL MILS. It was read with StrToIntDef,  }
        { so 3.5 became 0: a fractional width wrote zero and a fractional  }
        { X moved the object to the origin. A value that is not a number   }
        { is refused and reported as failed, never written as zero.        }
        Refused := IsLengthProperty(PropName) And (Not IsFloatStr(Value));
        { Base members, settable on any primitive. }
        { EVERY PRIMITIVE IS MOVED, NOT ASSIGNED.
          Writing x or y directly is not something this codebase has done
          successfully, and the one time it tried on a component body the
          PCB engine went down with an access violation. It was also
          unreachable for half the types, since x is not declared on all
          of them. MoveByXY is inherited from IPCB_Primitive,
          PCB_ReplicateLayout already calls it, and Lib_Link3DModel
          positions bodies with it, so it is the proven route, and taking
          the delta from PCBPrimitivePos makes one path serve every type
          that has a position at all. }
        If Refused Then
        Begin
            Matched := True;
        End
        Else If (PropName = 'X') Or (PropName = 'Y') Then
        Begin
            { Moved as a DELTA off wherever the primitive currently is, so   }
            { one shared path covers a pad, a via, a text and an arc without }
            { each needing its own writable member. A type with no single    }
            { position is refused rather than moved by one corner.           }
            PosVal := PCBPrimitivePos(Obj, PropName = 'Y', PosFound);
            If Not PosFound Then
            Begin
                { Reported as unreadable and NOT as a failure or an unknown
                  name: X is a real property spelled correctly, and the tail
                  of this function turns 0 into "unknown" and -1 into
                  "failed", either of which would send the caller looking
                  for a spelling mistake that is not there. }
                NotePropertyDiag('unreadable', PropName);
                Result := 1;
                Exit;
            End;
            If PropName = 'X' Then
                Obj.MoveByXY(MilsToCoordF(StrToFloatDef(Value, 0)) - PosVal, 0)
            Else
                Obj.MoveByXY(0, MilsToCoordF(StrToFloatDef(Value, 0)) - PosVal);
        End
        Else If PropName = 'Layer'    Then SetPrimitiveLayerByName(Obj, Value)
        Else If PropName = 'Selected' Then Obj.Selected := StrToBool(Value)
        { Subtype members: narrow to a typed local via ObjectId first. }
        Else If PropName = 'X1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.X1 := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'Y1' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Y1 := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'X2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.X2 := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'Y2' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Y2 := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'Width' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Track.Width := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else If Oid = eTextObject Then Begin Txt := Obj; SetTextGeometry(Txt, False, MilsToCoordF(StrToFloatDef(Value, 0))); End
            Else Matched := False;
        End
        Else If PropName = 'Rotation' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Comp.Rotation := StrToFloatDef(Value, 0); End
            Else If Oid = ePadObject Then Begin Pad := Obj; Pad.Rotation := StrToFloatDef(Value, 0); End
            Else Matched := False;
        End
        { A VIA'S HOLE AND DIAMETER. HoleSize had a pad branch only, and a   }
        { branch that matches the name but not the type used to report     }
        { success while writing nothing; Size had no branch at all. A via   }
        { write that would leave the hole as large as the pad is refused.   }
        { To grow a via, give Size before HoleSize; to shrink it, HoleSize  }
        { first: each write is checked against the via as it stands.       }
        Else If PropName = 'HoleSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.HoleSize := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else If Oid = eViaObject Then
            Begin
                Via := Obj;
                If Not SetViaGeometry(Via, Via.Size, MilsToCoordF(StrToFloatDef(Value, 0))) Then
                    Refused := True;
            End
            Else Matched := False;
        End
        Else If PropName = 'Size' Then
        Begin
            If Oid = eViaObject Then
            Begin
                Via := Obj;
                If Not SetViaGeometry(Via, MilsToCoordF(StrToFloatDef(Value, 0)), Via.HoleSize) Then
                    Refused := True;
            End
            Else If Oid = eTextObject Then
            Begin
                If StrToFloatDef(Value, 0) <= 0 Then
                Begin
                    Refused := True;
                End
                Else
                Begin
                    Txt := Obj;
                    SetTextGeometry(Txt, True, MilsToCoordF(StrToFloatDef(Value, 0)));
                End;
            End
            Else Matched := False;
        End
        Else If PropName = 'TopXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.TopXSize := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'TopYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.TopYSize := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'Text' Then
        Begin
            If Oid = eTextObject Then Begin Txt := Obj; Txt.Text := Value; End
            Else Matched := False;
        End
        Else If PropName = 'MirrorFlag' Then
        Begin
            If Oid <> eTextObject Then
                Matched := False
            Else If (LowerCase(Value) <> 'true') And (LowerCase(Value) <> 'false') Then
                Refused := True
            Else
            Begin
                Txt := Obj;
                SetTextMirrorInPlace(Txt, LowerCase(Value) = 'true');
            End;
        End
        { A pad's Name is its designator, written the way the footprint    }
        { tools write it. An empty name would leave a pad no pin can match }
        { and is refused. A component's designator is not settable from a  }
        { script, so Name and Designator stay unknown for components.      }
        { A region's Name is a tag a tool finds its own regions by again   }
        { (GenerateSamples.pas writes it); empty is allowed and clears it.  }
        Else If (PropName = 'Name') Or (PropName = 'Designator') Then
        Begin
            If (Oid = eRegionObject) And (PropName = 'Name') Then
            Begin
                Rgn := Obj;
                Rgn.Name := Value;
            End
            Else If Oid <> ePadObject Then
                Matched := False
            Else If Trim(Value) = '' Then
                Refused := True
            Else
            Begin
                Pad := Obj;
                Pad.Name := Value;
            End;
        End
        { A text's height and stroke width: silkscreen designator size is a }
        { fabrication requirement, and only the create path could set it.   }
        Else If (PropName = 'Height') Or (PropName = 'StrokeWidth') Then
        Begin
            If Oid <> eTextObject Then
            Begin
                Matched := False;
            End
            Else
            Begin
                If StrToFloatDef(Value, 0) <= 0 Then
                Begin
                    Refused := True;
                End
                Else
                Begin
                    Txt := Obj;
                    SetTextGeometry(Txt, PropName = 'Height', MilsToCoordF(StrToFloatDef(Value, 0)));
                End;
            End;
        End

        { Polygon pour options. All three are declared on IPCB_Polygon with
          both a Read and a Write accessor, so they are settable; they were
          simply absent here. Setting one does NOT repour: the flags decide
          what the NEXT pour does, so pcb_repour_polygons has to follow. }
        { The body's own writable members, from its declared interface:
          Property StandoffHeight : TCoord Read GetStandoffHeight
                                           Write SetStandoffHeight;
          and the same shape for OverallHeight. Identifier is read-only
          and is deliberately absent. }
        Else If PropName = 'StandoffHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Body.StandoffHeight := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'OverallHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Body.OverallHeight := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        { A ratio, not a length: kept out of IsLengthProperty so no mils    }
        { conversion touches it. A value outside 0..1 is refused.           }
        Else If PropName = 'BodyOpacity3D' Then
        Begin
            If Oid <> eComponentBodyObject Then
                Matched := False
            Else If (Not IsFloatStr(Value)) Or (StrToFloatDef(Value, -1) < 0)
                Or (StrToFloatDef(Value, -1) > 1) Then
                Refused := True
            Else
            Begin
                Body := Obj;
                Body.BodyOpacity3D := StrToFloatDef(Value, 1);
            End;
        End
        { Turning a region INTO a board cutout, the other half of the
          read above. The five identifiers are attested: four independent
          scripts in reference/ compare against them, so they exist in
          DelphiScript. What none of them does is ASSIGN one, so the
          write is unproven in the way StandoffHeight is, and it is
          ranked accordingly in the release procedure.

          If/Else If rather than Case, because Case on an enum crashes
          the script engine here. }
        Else If (PropName = 'Kind') Or (PropName = 'RegionKind') Then
        Begin
            If Oid = eRegionObject Then
            Begin
                Rgn := Obj;
                If Value = 'board_cutout' Then Rgn.Kind := eRegionKind_BoardCutout
                Else If Value = 'cutout' Then Rgn.Kind := eRegionKind_Cutout
                Else If Value = 'copper' Then Rgn.Kind := eRegionKind_Copper
                Else If Value = 'named_region' Then Rgn.Kind := eRegionKind_NamedRegion
                Else If Value = 'cavity' Then Rgn.Kind := eRegionKind_Cavity
                Else Matched := False;
            End
            Else Matched := False;
        End
        Else If PropName = 'RemoveDead' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Poly.RemoveDead := StrToBool(Value); End
            Else Matched := False;
        End
        Else If PropName = 'RemoveNarrowNecks' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Poly.RemoveNarrowNecks := StrToBool(Value); End
            Else Matched := False;
        End
        Else If PropName = 'RemoveIslandsByArea' Then
        Begin
            If Oid = ePolyObject Then
            Begin Poly := Obj; Poly.RemoveIslandsByArea := StrToBool(Value); End
            Else Matched := False;
        End
        Else Matched := False;

        If Refused Then
        Begin
            Result := -1;
        End
        Else
        Begin
            If Matched Then Result := 1 Else Result := 0;
        End;
    Except
        Result := -1;
    End;

    If Result = 0 Then NotePropertyDiag('unknown', PropName)
    Else If Result = -1 Then NotePropertyDiag('failed', PropName);
End;

{..............................................................................}
{ PCB Filter / JSON / Apply, parallel to schematic versions                 }
{..............................................................................}

{ Whether one filter value matches what the getter read.                     }
{                                                                            }
{ A LENGTH IS COMPARED AS A NUMBER. The getter spells a length one way       }
{ (3172.6356), so an exact string compare missed the same value written      }
{ 3172.63560 or, on a comma-decimal machine, 3172,6356, and matched nothing  }
{ without saying why. A comma is read as the decimal point, both sides are   }
{ vetted by IsFloatStr before the locale-safe StrToFloatDef parses them, so  }
{ nothing here can raise, and they must agree to half a coordinate unit      }
{ (0.00005 mil). Every other property is still matched exactly.             }
Function FilterValueMatchesPCB(PropName, Actual, Expected : String) : Boolean;
Var
    Want, Ch : String;
    I : Integer;
Begin
    Result := (Actual = Expected);
    If Result Then Exit;
    If Not IsLengthProperty(PropName) Then Exit;
    Want := '';
    For I := 1 To Length(Expected) Do
    Begin
        Ch := Copy(Expected, I, 1);
        If Ch = ',' Then Ch := '.';
        Want := Want + Ch;
    End;
    Want := Trim(Want);
    If Not IsFloatStr(Want) Then Exit;
    If Not IsFloatStr(Actual) Then Exit;
    Result := Abs(StrToFloatDef(Actual, 0) - StrToFloatDef(Want, 0)) < 0.00005;
End;

Function MatchesFilterPCB(Obj : IPCB_Primitive; FilterStr : String) : Boolean;
Var
    Remaining, Condition, PropName, Expected, Actual : String;
    PipePos, EqPos : Integer;
Begin
    Result := True;
    If FilterStr = '' Then Exit;
    Remaining := FilterStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin
            Condition := Copy(Remaining, 1, PipePos - 1);
            Remaining := Copy(Remaining, PipePos + 1, Length(Remaining));
        End
        Else Begin Condition := Remaining; Remaining := ''; End;
        { A condition that is not Name=Value matches nothing (see
          FilterProblem): skipped, it matched every object. }
        If Trim(Condition) = '' Then Continue;
        EqPos := Pos('=', Condition);
        If EqPos < 2 Then Begin Result := False; Exit; End;
        PropName := Copy(Condition, 1, EqPos - 1);
        Expected := Copy(Condition, EqPos + 1, Length(Condition));
        Actual := GetPCBProperty(Obj, PropName);
        If Not FilterValueMatchesPCB(PropName, Actual, Expected) Then
        Begin
            Result := False;
            Exit;
        End;
    End;
End;

{..............................................................................}
{ IsKnownPCBProperty                                                           }
{                                                                              }
{ Whether GetPCBProperty has a branch for this name. It exists because that    }
{ getter returns '' for anything it does not recognise, which makes a          }
{ MISSPELLED property indistinguishable from one that is genuinely empty. That }
{ ambiguity has now cost three separate investigations, each concluding the    }
{ bridge could not do something it could: the caller sees blanks, believes the }
{ data is not there, and stops.                                                }
{                                                                              }
{ Kept next to the getter deliberately. A list that lives somewhere else       }
{ drifts the first time a branch is added, and a stale allow-list would reject }
{ a property that works, which is worse than the silence it replaces.          }
{..............................................................................}

Function IsKnownPCBProperty(PropName : String) : Boolean;
Begin
    { Kind, RegionKind, the three pour options and the two body heights  }
    { were answered by the getter and refused here, so a query for them  }
    { was told they do not exist: the lag the list below warns about.    }
    Result :=
        (PropName = 'ObjectId') Or (PropName = 'X') Or (PropName = 'Y') Or
        (PropName = 'Layer') Or (PropName = 'Descriptor') Or
        (PropName = 'Selected') Or (PropName = 'Net') Or
        (PropName = 'Net.Name') Or (PropName = 'X1') Or (PropName = 'Y1') Or
        (PropName = 'X2') Or (PropName = 'Y2') Or (PropName = 'Width') Or
        (PropName = 'Radius') Or (PropName = 'StartAngle') Or
        (PropName = 'EndAngle') Or (PropName = 'XCenter') Or
        (PropName = 'YCenter') Or (PropName = 'HoleSize') Or
        (PropName = 'Size') Or (PropName = 'TopShape') Or
        (PropName = 'TopXSize') Or (PropName = 'TopYSize') Or
        (PropName = 'Rotation') Or (PropName = 'Name') Or
        (PropName = 'Text') Or (PropName = 'Pattern') Or
        (PropName = 'Designator') Or (PropName = 'Designator.Text') Or
        (PropName = 'Comment') Or (PropName = 'Comment.Text') Or
        (PropName = 'SourceDesignator') Or
        (PropName = 'Kind') Or (PropName = 'RegionKind') Or
        (PropName = 'RemoveDead') Or (PropName = 'RemoveNarrowNecks') Or
        (PropName = 'RemoveIslandsByArea') Or
        (PropName = 'StandoffHeight') Or (PropName = 'OverallHeight') Or
        (PropName = 'BodyOpacity3D') Or
        (PropName = 'InComponent') Or (PropName = 'Component') Or
        (PropName = 'InPolygon') Or (PropName = 'IsKeepout') Or
        (PropName = 'TearDrop') Or
        (PropName = 'Locked') Or (PropName = 'Layer1') Or
        (PropName = 'Layer2') Or (PropName = 'IsRedundant') Or
        (PropName = 'Mode') Or (PropName = 'Height') Or
        (PropName = 'StrokeWidth') Or (PropName = 'IsDesignator') Or
        (PropName = 'IsComment') Or (PropName = 'UseTTFonts') Or
        (PropName = 'MirrorFlag');
End;

Function UnknownPCBProperties(PropsStr : String) : String;
Var
    Remaining, PropName : String;
    CommaPos : Integer;
Begin
    Result := '';
    Remaining := PropsStr;
    While Remaining <> '' Do
    Begin
        CommaPos := Pos(',', Remaining);
        If CommaPos > 0 Then
        Begin
            PropName := Trim(Copy(Remaining, 1, CommaPos - 1));
            Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining));
        End
        Else Begin PropName := Trim(Remaining); Remaining := ''; End;
        If (PropName <> '') And (Not IsKnownPCBProperty(PropName)) Then
        Begin
            If Result <> '' Then Result := Result + ', ';
            Result := Result + PropName;
        End;
    End;
End;

{ HIDDEN FROM THE RUN SCRIPT DIALOG BY ITS ARGUMENT.
  Altium lists only parameterless routines there, so this project puts
  fifty-five internal helpers in front of a user whose four real entry
  points are StartMCPServer, StopMCPServer, RunSelfTest and
  ShowStatusForm. A parameter is the only lever DelphiScript offers:
  there are no visibility modifiers and every unit in the project is
  scanned.

  Dummy is never read. It exists to change the arity and nothing else.
  Reported by a user as too many functions listed to find the right one. }
Function KnownPCBPropertyList(Dummy : Integer) : String;
Begin
    Result := 'ObjectId, X, Y, Layer, Descriptor, Selected, Net, X1, Y1, '
        + 'X2, Y2, Width, Radius, StartAngle, EndAngle, XCenter, YCenter, '
        + 'HoleSize, Size, TopShape, TopXSize, TopYSize, Rotation, Name, '
        + 'Text, Pattern, Designator, Comment, SourceDesignator, '
        { Everything the reader above answers. A list that lags the reader
          tells a caller a property does not exist when it does, which is
          how the pour flags were reported as unreachable. }
        + 'Kind, RemoveDead, RemoveNarrowNecks, RemoveIslandsByArea, '
        + 'StandoffHeight, OverallHeight, BodyOpacity3D, InComponent, Component, '
        + 'InPolygon, IsKeepout, TearDrop, Locked, Layer1, Layer2, IsRedundant, Mode, '
        + 'Height, StrokeWidth, IsDesignator, IsComment, UseTTFonts, MirrorFlag';
End;

Function BuildObjectJsonPCB(Obj : IPCB_Primitive; PropsStr : String) : String;
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
        Begin PropName := Copy(Remaining, 1, CommaPos - 1); Remaining := Copy(Remaining, CommaPos + 1, Length(Remaining)); End
        Else Begin PropName := Remaining; Remaining := ''; End;
        PropValue := GetPCBProperty(Obj, PropName);
        If Not First Then Result := Result + ',';
        First := False;
        Result := Result + '"' + EscapeJsonString(PropName) + '":"' + EscapeJsonString(PropValue) + '"';
    End;
    Result := Result + '}';
End;

Procedure ApplySetPropertiesPCB(Obj : IPCB_Primitive; SetStr : String);
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
Begin
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin Assignment := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End
        Else Begin Assignment := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Assignment);
        If EqPos = 0 Then Continue;
        PropName := Copy(Assignment, 1, EqPos - 1);
        PropValue := Copy(Assignment, EqPos + 1, Length(Assignment));
        SetPCBProperty(Obj, PropName, PropValue);
    End;
End;

{..............................................................................}
{ PCB Board iteration, query/modify/delete on active PCB                    }
{..............................................................................}

Function ProcessPCBBoardObjects(Board : IPCB_Board; ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; Var TotalMatched : Integer; Limit : Integer) : String;
Var
    Iterator : IPCB_BoardIterator;
    Obj, FoundObj : IPCB_Primitive;
    ObjJson : String;
    First, Removed : Boolean;
    I : Integer;
    Victims : TInterfaceList;
Begin
    Result := '';
    First := (TotalMatched = 0);

    { EVERY PreProcess BELOW IS IN A Try/Finally, and that is not tidiness.   }
    {                                                                          }
    { An exception anywhere between PreProcess and PostProcess leaves Altium   }
    { believing a command is still running. From then on EVERY save of a PCB   }
    { document is refused with "A command is currently active and save cannot  }
    { be completed at this time", the editor offers to write a copy instead,   }
    { and NOTHING CLEARS IT: not restarting the polling loop, because the      }
    { state lives in the PCB server rather than the script, and not Escape in  }
    { the editor.                                                              }
    {                                                                          }
    { MEASURED on 2026-08-25: a PcbLib and its board went a whole day without  }
    { a successful save while SchLib documents beside them saved normally,     }
    { and the authored footprints existed only in memory.                      }
    {                                                                          }
    { The loop body calls MatchesFilterPCB, BuildObjectJsonPCB and             }
    { ApplySetPropertiesPCB, all of which touch caller-supplied property names }
    { on arbitrary primitives, so raising is an ordinary outcome here rather   }
    { than a remote possibility. AltiumScriptCentral ships a whole recovery    }
    { script for this symptom, which is a fair measure of how often it bites.  }
    { COLLECT, THEN REMOVE, the way Altium's own DeletePCBObjects example  }
    { does. Finding one match and restarting the walk after each removal   }
    { built a new iterator per object and walked again past everything the }
    { filter rejects, so taking the routing off a board walked the board   }
    { once per track. Nothing is removed while the iterator is live, and   }
    { the list is never Freed: releasing board-primitive refs through it   }
    { faults in oleaut32 (see PCB_SetTrackWidth). Items come back as the   }
    { base IPCB_Primitive, the type RemovePCBObject takes.                 }
    If Mode = 'delete' Then
    Begin
        Victims := TInterfaceList.Create;
        Iterator := Board.BoardIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Obj := Iterator.FirstPCBObject;
            While Obj <> Nil Do
            Begin
                If MatchesFilterPCB(Obj, FilterStr) Then Victims.Add(Obj);
                Obj := Iterator.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iterator);
        End;
        PCBServer.PreProcess;
        Try
            For I := 0 To Victims.Count - 1 Do
            Begin
                FoundObj := Victims.Items[I];
                If FoundObj = Nil Then Continue;
                { A raise here used to end the request as an error with the  }
                { objects already removed uncounted. Stop, and report both.  }
                Removed := True;
                Try
                    PCBServer.SendMessageToRobots(Board.I_ObjectAddress, c_Broadcast,
                        PCBM_BoardRegisteration, FoundObj.I_ObjectAddress);
                    Board.RemovePCBObject(FoundObj);
                Except
                    Removed := False;
                End;
                If Not Removed Then
                Begin
                    NoteDeleteFault('removing an object raised an error');
                    Break;
                End;
                Inc(TotalMatched);
            End;
        Finally
            PCBServer.PostProcess;
        End;
        Exit;
    End;

    { MODIFY COLLECTS FIRST AND CHANGES AFTER, like delete above: an object }
    { changed while the board iterator walks can move in its spatial index  }
    { under it.                                                             }
    If Mode = 'modify' Then
    Begin
        Victims := TInterfaceList.Create;
        Iterator := Board.BoardIterator_Create;
        Try
            Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
            Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
            Iterator.AddFilter_Method(eProcessAll);
            Obj := Iterator.FirstPCBObject;
            While Obj <> Nil Do
            Begin
                If (Limit > 0) And (Victims.Count >= Limit) Then Break;
                If MatchesFilterPCB(Obj, FilterStr) Then Victims.Add(Obj);
                Obj := Iterator.NextPCBObject;
            End;
        Finally
            Board.BoardIterator_Destroy(Iterator);
        End;
        PCBServer.PreProcess;
        Try
            For I := 0 To Victims.Count - 1 Do
            Begin
                FoundObj := Victims.Items[I];
                If FoundObj = Nil Then Continue;
                { Inside the object's modify bracket. Measured on AD 26.10:  }
                { a text moved by X/Y without it was drawn at the new place  }
                { while the board's spatial index still held the old one, so }
                { the mouse could not pick it and a spatial search found it  }
                { where it no longer was.                                    }
                FoundObj.BeginModify;
                Try
                    ApplySetPropertiesPCB(FoundObj, SetStr);
                Finally
                    FoundObj.EndModify;
                End;
                FoundObj.GraphicallyInvalidate;
                Inc(TotalMatched);
            End;
        Finally
            PCBServer.PostProcess;
        End;
        Exit;
    End;

    Iterator := Board.BoardIterator_Create;
    Try
        Iterator.AddFilter_ObjectSet(MkSet(ObjTypeInt));
        { The V7 layer set: the legacy AllLayers stops at Mechanical16, and }
        { MEASURED, 6390 outline primitives on Mechanical17 counted 0.      }
        Iterator.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iterator.AddFilter_Method(eProcessAll);

        Obj := Iterator.FirstPCBObject;
        While Obj <> Nil Do
        Begin
            If (Limit > 0) And (TotalMatched >= Limit) Then Break;
            If MatchesFilterPCB(Obj, FilterStr) Then
            Begin
                ObjJson := BuildObjectJsonPCB(Obj, PropsStr);
                If Not First Then Result := Result + ',';
                First := False;
                Result := Result + ObjJson;
                Inc(TotalMatched);
            End;
            Obj := Iterator.NextPCBObject;
        End;
    Finally
        Board.BoardIterator_Destroy(Iterator);
    End;
End;

{ The first Layer= assignment in a set string this board cannot resolve, or    }
{ '' when every one of them resolves. Checked before the iteration starts so   }
{ a bad name costs nothing rather than relocating half the matched objects.    }

Function UnresolvedLayerAssignment(Board : IPCB_Board; SetStr : String) : String;
Var
    Remaining, Assignment, PropName, PropValue : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
    Remaining := SetStr;
    While Remaining <> '' Do
    Begin
        PipePos := Pos('|', Remaining);
        If PipePos > 0 Then
        Begin Assignment := Copy(Remaining, 1, PipePos - 1); Remaining := Copy(Remaining, PipePos + 1, Length(Remaining)); End
        Else Begin Assignment := Remaining; Remaining := ''; End;
        EqPos := Pos('=', Assignment);
        If EqPos > 0 Then
        Begin
            PropName := UpperCase(Trim(Copy(Assignment, 1, EqPos - 1)));
            PropValue := Trim(Copy(Assignment, EqPos + 1, Length(Assignment)));
            If (PropName = 'LAYER') And (PropValue <> '') Then
            Begin
                If ResolveLayerId(Board, PropValue) = eNoLayer Then
                Begin
                    Result := PropValue;
                    Exit;
                End;
            End;
        End;
    End;
End;

{ Whether angle A (degrees) lies on the counter-clockwise sweep A1 -> A2.  }
Function AngleOnSweep(A, A1, A2 : Double) : Boolean;
Var
    Sweep, Off : Double;
Begin
    Sweep := A2 - A1;
    While Sweep < 0 Do Sweep := Sweep + 360.0;
    If Sweep = 0 Then Sweep := 360.0;
    Off := A - A1;
    While Off < 0 Do Off := Off + 360.0;
    While Off >= 360.0 Do Off := Off - 360.0;
    Result := Off <= Sweep;
End;

{ The board outline's extents in internal units, from its own segments:    }
{ every vertex, and for an arc its two ends and any of 0, 90, 180 and 270   }
{ degrees its sweep crosses. The outline's BoundingRectangle is a cached    }
{ size that a reshape did not refresh: a board set larger kept reporting    }
{ its old rectangle, and renders built from it cut off a third of the       }
{ board. The arguments are left as they came when the outline is empty.     }
Procedure OutlineExtents(Outline : IPCB_BoardOutline; Var L, B, R, T : Integer);
Var
    I, K, N : Integer;
    Vx, Vy, Cx, Cy, Rr, A1, A2, Ang, Px, Py, ToRad : Double;
    MinX, MinY, MaxX, MaxY : Double;
Begin
    N := 0;
    Try N := Outline.PointCount; Except N := 0; End;
    If N <= 0 Then Exit;
    ToRad := 3.14159265358979 / 180.0;
    MinX := 1e30;
    MinY := 1e30;
    MaxX := -1e30;
    MaxY := -1e30;
    For I := 0 To N - 1 Do
    Begin
        Vx := Outline.Segments[I].vx * 1.0;
        Vy := Outline.Segments[I].vy * 1.0;
        If Vx < MinX Then MinX := Vx;
        If Vx > MaxX Then MaxX := Vx;
        If Vy < MinY Then MinY := Vy;
        If Vy > MaxY Then MaxY := Vy;
        If Outline.Segments[I].Kind <> ePolySegmentLine Then
        Begin
            Cx := Outline.Segments[I].cx * 1.0;
            Cy := Outline.Segments[I].cy * 1.0;
            A1 := Outline.Segments[I].Angle1;
            A2 := Outline.Segments[I].Angle2;
            Rr := Sqrt((Vx - Cx) * (Vx - Cx) + (Vy - Cy) * (Vy - Cy));
            For K := 0 To 5 Do
            Begin
                Ang := K * 90.0;
                If K = 4 Then Ang := A1;
                If K = 5 Then Ang := A2;
                If (K >= 4) Or AngleOnSweep(Ang, A1, A2) Then
                Begin
                    Px := Cx + Rr * Cos(Ang * ToRad);
                    Py := Cy + Rr * Sin(Ang * ToRad);
                    If Px < MinX Then MinX := Px;
                    If Px > MaxX Then MaxX := Px;
                    If Py < MinY Then MinY := Py;
                    If Py > MaxY Then MaxY := Py;
                End;
            End;
        End;
    End;
    L := Round(MinX);
    B := Round(MinY);
    R := Round(MaxX);
    T := Round(MaxY);
End;

{ After the outline is rewritten: Altium's own refresh of the cached size,   }
{ as the community outline scripts do (PolygonReFitBO.pas). Apart from the  }
{ caller, so these two calls are the only new identifiers in one place.      }
{ The layer stack regions (Board Planning Mode) are separate objects cut   }
{ from the board shape. Without the rebuild they keep the OLD shape: a     }
{ board reshaped from a copy of another kept that board's region.         }
Procedure RefreshBoardOutline(Board : IPCB_Board);
Begin
    Board.BoardOutline.SetState_XSizeYSize;
    Board.BoardOutline.GraphicallyInvalidate;
    Board.UpdateBoardOutline;
    Board.RebuildSplitBoardRegions(True);
End;

{ A polygon or board outline holds one segment past PointCount: the closing }
{ vertex at index PointCount, a line back to the first, which the community }
{ PCBScale.pas rewrites by walking 0 To PointCount. Setting PointCount and   }
{ writing 0..N-1 leaves it where it was. MEASURED: a replaced outline saved }
{ the OLD first vertex there, a spike in the board shape. Call after the    }
{ N segments are written. The record is copied from a segment, as the bevel }
{ does, because a bare TPolySegment local cannot take field writes.        }
Procedure ClosePolySegments(Poly : IPCB_Polygon; N : Integer);
Var
    Seg : TPolySegment;
Begin
    Seg := Poly.Segments[0];
    Seg.Kind := ePolySegmentLine;
    Poly.Segments[N] := Seg;
End;

{ The first property a filter names that the PCB getter does not answer,  }
{ or ''. The getter returns '' for an unknown name, so Foo= matched every   }
{ object and Foo=x matched none, neither of which the caller meant.         }
Function UnknownPCBFilterProperty(FilterStr : String) : String;
Var
    Remaining, Condition, PropName : String;
    PipePos, EqPos : Integer;
Begin
    Result := '';
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
        If EqPos > 1 Then
        Begin
            PropName := Copy(Condition, 1, EqPos - 1);
            If Not IsKnownPCBProperty(PropName) Then
            Begin
                Result := PropName;
                Exit;
            End;
        End;
    End;
End;

Function ProcessActivePCBDoc(ObjTypeInt : Integer;
    FilterStr : String; PropsStr : String; SetStr : String;
    Mode : String; RequestId : String; Limit : Integer) : String;
Var
    Board : IPCB_Board;
    TotalMatched : Integer;
    JsonItems, Why, BadLayer, BadCond : String;
Begin
    BadCond := FilterProblem(FilterStr);
    If BadCond <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'BAD_FILTER', BadFilterMessage(BadCond));
        Exit;
    End;
    BadCond := UnknownPCBFilterProperty(FilterStr);
    If BadCond <> '' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_PROPERTY',
            'Not a PCB property in the filter: ' + BadCond + '. Nothing was '
            + 'matched or changed. Available: ' + KnownPCBPropertyList(0) + '.');
        Exit;
    End;
    { A READ MAY WANDER; AN EDIT MAY NOT.                                   }
    {                                                                        }
    { GetPCBBoardAnywhere opens the first board it can find when none is     }
    { focused, and hides the focus change afterwards. For a query that is    }
    { the focus-independent access this project advertises. For a delete it  }
    { is a misfire: with a library in front and two boards open, primitives  }
    { would be removed from whichever board the project walk reached first,  }
    { and nothing in the reply would say which.                              }
    {                                                                        }
    { There is no library-scoped primitive delete, so a caller working in a  }
    { PcbLib has no correct tool here and the wrong one used to look like    }
    { it worked.                                                             }
    If (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create') Then
    Begin
        Board := GetPCBBoardForMutation(Why);
        If Board = Nil Then
        Begin
            Result := BuildErrorResponse(RequestId, 'AMBIGUOUS_TARGET', Why);
            Exit;
        End;
    End
    Else
        Board := GetPCBBoardAnywhere(0);

    If Board = Nil Then
    Begin
        Result := BuildErrorResponse(RequestId, 'NO_PCB', 'No PCB document is active');
        Exit;
    End;

    If SetStr <> '' Then
    Begin
        BadLayer := UnresolvedLayerAssignment(Board, SetStr);
        If BadLayer <> '' Then
        Begin
            Result := BuildErrorResponse(RequestId, 'UNKNOWN_LAYER',
                'Unknown layer name: ' + BadLayer + '. ' + BoardLayerNamesHint(Board));
            Exit;
        End;
    End;
    TotalMatched := 0;
    JsonItems := ProcessPCBBoardObjects(Board, ObjTypeInt,
        FilterStr, PropsStr, SetStr, Mode, TotalMatched, Limit);

    If (Mode = 'modify') Or (Mode = 'delete') Or (Mode = 'create') Then
    Begin
        Board.GraphicalView_ZoomRedraw;
        MarkDocDirtyByPath(Board.FileName);
    End;

    If Mode = 'query' Then
        Result := BuildSuccessResponse(RequestId,
            '{"objects":[' + JsonItems + '],"count":' + IntToStr(TotalMatched) + '}')
    Else
        { matched counts what the FILTER selected, and said nothing about
          whether a property write landed. The tail reports that, the same
          way the schematic modify replies do. }
        Result := BuildSuccessResponse(RequestId,
            '{"matched":' + IntToStr(TotalMatched)
            + ModifyOutcomeJson(0) + '}');
End;

{ UnitStamp_PCBGeneric - which build of PCBGeneric.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_PCBGeneric(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
