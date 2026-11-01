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

{ ---- MORE BOARD PROPERTIES ------------------------------------------------ }
{ Hole tolerances on pads and vias, barcode and multiline text, dimension     }
{ text and arrows, the rest of the pour options, and pin / part swapping on  }
{ a placed part. Each is a GetState_ / SetState_ pair. A      }
{ length is decimal mils both ways; a choice (BarCodeKind, TextPosition ...)  }
{ is Altium's own ordinal, read back so a caller can copy one.                }

{ Altium's "no hole tolerance set": the largest 32-bit integer, measured on }
{ AD 26.11. Worked out here rather than written as a literal, because AD25  }
{ refuses that literal while compiling (see MaxInt in Main.pas).           }
Function UnsetTolerance(Dummy : Integer) : Integer;
Var
    Half : Integer;
Begin
    Half := 1073741823;
    Result := Half + Half + 1;
End;

{ Which object type carries an extra property: 1 pad or via, 2 via only,     }
{ 3 text, 4 dimension, 5 polygon, 6 component, 7 pad only, 8 3D body, 0     }
{ none of these. A 3D body's OverrideColor says whether its own colour is    }
{ drawn instead of the model's; Texture is an image file laid on it.          }
{ 9 a track, arc or via: IsPreRoute (kept by the autorouter) and UserRouted. }
{ Pads and vias also take the mask modes (PasteMaskExpansionMode,           }
{ SolderMaskExpansionMode, Altium's numbers: from the rule or manual) and   }
{ the paste opening as a percentage (PasteMaskUsePercent,                   }
{ PasteMaskPercent) or switched off (PasteMaskEnabled).                     }
{ Text ConvertedString is the text as shown, special strings resolved; it   }
{ is read only. A pad's DrillType is how its hole is made (0 drilled, 1     }
{ punched, 2 laser, 3 plasma); IsVirtualPin and a part's FlippedOnLayer    }
{ (its "flip on layer" option, not its side) are read only. A text's       }
{ BorderSpaceType is Altium's number for how its inverted border is sized. }
{ A text's TextKind is 0 stroke, 1 TrueType, 2 barcode; FontID picks the   }
{ stroke font (1 Default, 2 Sans Serif, 3 Serif); FontName is the TrueType }
{ font and Bold / Italic are TrueType only. InvRectWidth / InvRectHeight   }
{ are read only: Altium sizes the inverted box itself (text + margin) and  }
{ ignores a written size; on a multiline text the box is the frame, so    }
{ the frame (MultilineTextWidth / Height) is what makes a label larger.   }
{ InvertedTTTextBorder is the margin, a minimum the box never goes below. }
Function ExtraPCBPropertyOwner(PropName : String) : Integer;
Begin
    Result := 0;
    If (PropName = 'HolePositiveTolerance') Or (PropName = 'HoleNegativeTolerance') Or
       (PropName = 'PasteMaskExpansionMode') Or (PropName = 'SolderMaskExpansionMode') Or
       (PropName = 'PasteMaskUsePercent') Or (PropName = 'PasteMaskPercent') Or
       (PropName = 'PasteMaskEnabled') Then Result := 1
    Else If PropName = 'IsCounterHole' Then Result := 2
    Else If (PropName = 'BarCodeKind') Or (PropName = 'BarCodeRenderMode') Or
            (PropName = 'BarCodeFullWidth') Or (PropName = 'BarCodeFullHeight') Or
            (PropName = 'BarCodeXMargin') Or (PropName = 'BarCodeYMargin') Or
            (PropName = 'BarCodeMinWidth') Or (PropName = 'BarCodeInverted') Or
            (PropName = 'BarCodeShowText') Or (PropName = 'BarCodeFontName') Or
            (PropName = 'Multiline') Or (PropName = 'MultilineTextWidth') Or
            (PropName = 'MultilineTextHeight') Or
            (PropName = 'MultilineTextAutoPosition') Or
            (PropName = 'Inverted') Or (PropName = 'UseInvertedRectangle') Or
            (PropName = 'InvertedTTTextBorder') Or
            (PropName = 'TTFInvertedTextJustify') Or
            (PropName = 'TTFOffsetFromInvertedRect') Or
            (PropName = 'ConvertedString') Or (PropName = 'BorderSpaceType') Or
            (PropName = 'FontName') Or (PropName = 'TextKind') Or
            (PropName = 'FontID') Or (PropName = 'Bold') Or
            (PropName = 'Italic') Or (PropName = 'InvRectWidth') Or
            (PropName = 'InvRectHeight') Then Result := 3
    Else If (PropName = 'TextHeight') Or (PropName = 'TextWidth') Or
            (PropName = 'TextLineWidth') Or (PropName = 'TextGap') Or
            (PropName = 'TextPosition') Or (PropName = 'TextDimensionUnit') Or
            (PropName = 'TextPrecision') Or (PropName = 'TextFormat') Or
            (PropName = 'TextPrefix') Or (PropName = 'TextSuffix') Or
            (PropName = 'ArrowSize') Or (PropName = 'ArrowLineWidth') Or
            (PropName = 'ArrowLength') Or (PropName = 'ArrowPosition') Or
            (PropName = 'ExtensionOffset') Or (PropName = 'ExtensionLineWidth') Or
            (PropName = 'ExtensionPickGap') Then Result := 4
    Else If (PropName = 'PolygonType') Or (PropName = 'BorderWidth') Or
            (PropName = 'AvoidObsticles') Or (PropName = 'ClipAcuteCorners') Or
            (PropName = 'MitreCorners') Or (PropName = 'IgnoreViolations') Or
            (PropName = 'ArcPourMode') Or (PropName = 'DrawRemovedNecks') Or
            (PropName = 'DrawRemovedIslands') Or (PropName = 'DrawDeadCopper') Or
            (PropName = 'ExpandOutline') Or (PropName = 'AutoGenerateName') Then Result := 5
    Else If (PropName = 'EnablePinSwapping') Or (PropName = 'EnablePartSwapping') Or
            (PropName = 'IsBGA') Or (PropName = 'LockStrings') Or
            (PropName = 'NameAutoPosition') Or (PropName = 'CommentAutoPosition') Or
            (PropName = 'FootprintDescription') Or (PropName = 'DefaultPCB3DModel') Or
            (PropName = 'PackageSpecificHash') Or (PropName = 'SourceDescription') Or
            (PropName = 'JumpersVisible') Or (PropName = 'ChannelOffset') Or
            (PropName = 'GroupNum') Or (PropName = 'FlippedOnLayer') Then Result := 6
    Else If (PropName = 'JumperID') Or (PropName = 'SwapID_Pad') Or
            (PropName = 'SwapID_Part') Or (PropName = 'SwappedPadName') Or
            (PropName = 'OwnerPart_ID') Or (PropName = 'DrillType') Or
            (PropName = 'IsVirtualPin') Then Result := 7
    Else If (PropName = 'OverrideColor') Or (PropName = 'Texture') Or
            (PropName = 'TextureRotation') Then Result := 8
    Else If (PropName = 'IsPreRoute') Or (PropName = 'UserRouted') Then Result := 9;
End;

{ True when Obj is the type that carries PropName. }
Function ExtraPCBPropertyFits(Oid : Integer; PropName : String) : Boolean;
Var
    Owner : Integer;
Begin
    Owner := ExtraPCBPropertyOwner(PropName);
    Result := ((Owner = 1) And ((Oid = ePadObject) Or (Oid = eViaObject)))
        Or ((Owner = 2) And (Oid = eViaObject))
        Or ((Owner = 3) And (Oid = eTextObject))
        Or ((Owner = 4) And (Oid = eDimensionObject))
        Or ((Owner = 5) And (Oid = ePolyObject))
        Or ((Owner = 6) And (Oid = eComponentObject))
        Or ((Owner = 7) And (Oid = ePadObject))
        Or ((Owner = 8) And (Oid = eComponentBodyObject))
        Or ((Owner = 9) And ((Oid = eTrackObject) Or (Oid = eArcObject) Or (Oid = eViaObject)));
End;

{ The value, '' when Obj is not the type that carries it. }
Function PCBGetExtra(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Pad : IPCB_Pad;
    Via : IPCB_Via;
    Txt : IPCB_Text;
    Dim : IPCB_Dimension;
    Poly : IPCB_Polygon;
    Comp : IPCB_Component;
    Body : IPCB_ComponentBody;
    Oid, Tol : Integer;
Begin
    Result := '';
    Oid := Obj.ObjectId;
    If Not ExtraPCBPropertyFits(Oid, PropName) Then Exit;
    If ExtraPCBPropertyOwner(PropName) = 9 Then
    Begin
        If PropName = 'IsPreRoute' Then Result := BoolToJsonStr(Obj.IsPreRoute)
        Else Result := BoolToJsonStr(Obj.UserRouted);
        Exit;
    End;
    If (PropName = 'PasteMaskExpansionMode') Or (PropName = 'SolderMaskExpansionMode') Or
       (PropName = 'PasteMaskUsePercent') Or (PropName = 'PasteMaskPercent') Or
       (PropName = 'PasteMaskEnabled') Then
    Begin
        If Oid = ePadObject Then
        Begin
            Pad := Obj;
            If PropName = 'PasteMaskExpansionMode' Then Result := IntToStr(Pad.PasteMaskExpansionMode)
            Else If PropName = 'SolderMaskExpansionMode' Then Result := IntToStr(Pad.SolderMaskExpansionMode)
            Else If PropName = 'PasteMaskUsePercent' Then Result := BoolToJsonStr(Pad.PasteMaskUsePercent)
            Else If PropName = 'PasteMaskPercent' Then Result := FloatToJsonStr(Pad.PasteMaskPercent)
            Else Result := BoolToJsonStr(Pad.PasteMaskEnabled);
        End
        Else
        Begin
            Via := Obj;
            If PropName = 'PasteMaskExpansionMode' Then Result := IntToStr(Via.PasteMaskExpansionMode)
            Else If PropName = 'SolderMaskExpansionMode' Then Result := IntToStr(Via.SolderMaskExpansionMode)
            Else If PropName = 'PasteMaskUsePercent' Then Result := BoolToJsonStr(Via.PasteMaskUsePercent)
            Else If PropName = 'PasteMaskPercent' Then Result := FloatToJsonStr(Via.PasteMaskPercent)
            Else Result := BoolToJsonStr(Via.PasteMaskEnabled);
        End;
        Exit;
    End;
    If (PropName = 'HolePositiveTolerance') Or (PropName = 'HoleNegativeTolerance') Then
    Begin
        If Oid = ePadObject Then
        Begin
            Pad := Obj;
            If PropName = 'HolePositiveTolerance' Then Tol := Pad.HolePositiveTolerance
            Else Tol := Pad.HoleNegativeTolerance;
        End
        Else
        Begin
            Via := Obj;
            If PropName = 'HolePositiveTolerance' Then Tol := Via.HolePositiveTolerance
            Else Tol := Via.HoleNegativeTolerance;
        End;
        If Tol = UnsetTolerance(0) Then Result := 'unset'
        Else Result := FloatToJsonStr(CoordToMilsF(Tol));
    End
    Else If PropName = 'IsCounterHole' Then
    Begin
        Via := Obj;
        Result := BoolToJsonStr(Via.IsCounterHole);
    End
    Else If Oid = eTextObject Then
    Begin
        Txt := Obj;
        If PropName = 'BarCodeKind' Then Result := IntToStr(Txt.BarCodeKind)
        Else If PropName = 'BarCodeRenderMode' Then Result := IntToStr(Txt.BarCodeRenderMode)
        Else If PropName = 'BarCodeFullWidth' Then Result := FloatToJsonStr(CoordToMilsF(Txt.BarCodeFullWidth))
        Else If PropName = 'BarCodeFullHeight' Then Result := FloatToJsonStr(CoordToMilsF(Txt.BarCodeFullHeight))
        Else If PropName = 'BarCodeXMargin' Then Result := FloatToJsonStr(CoordToMilsF(Txt.BarCodeXMargin))
        Else If PropName = 'BarCodeYMargin' Then Result := FloatToJsonStr(CoordToMilsF(Txt.BarCodeYMargin))
        Else If PropName = 'BarCodeMinWidth' Then Result := FloatToJsonStr(CoordToMilsF(Txt.BarCodeMinWidth))
        Else If PropName = 'BarCodeInverted' Then Result := BoolToJsonStr(Txt.BarCodeInverted)
        Else If PropName = 'BarCodeShowText' Then Result := BoolToJsonStr(Txt.BarCodeShowText)
        Else If PropName = 'BarCodeFontName' Then Result := Txt.BarCodeFontName
        Else If PropName = 'Multiline' Then Result := BoolToJsonStr(Txt.Multiline)
        Else If PropName = 'MultilineTextWidth' Then Result := FloatToJsonStr(CoordToMilsF(Txt.MultilineTextWidth))
        Else If PropName = 'MultilineTextHeight' Then Result := FloatToJsonStr(CoordToMilsF(Txt.MultilineTextHeight))
        Else If PropName = 'MultilineTextAutoPosition' Then Result := IntToStr(Txt.MultilineTextAutoPosition)
        Else If PropName = 'Inverted' Then Result := BoolToJsonStr(Txt.Inverted)
        Else If PropName = 'UseInvertedRectangle' Then Result := BoolToJsonStr(Txt.UseInvertedRectangle)
        Else If PropName = 'InvertedTTTextBorder' Then Result := FloatToJsonStr(CoordToMilsF(Txt.InvertedTTTextBorder))
        Else If PropName = 'TTFInvertedTextJustify' Then Result := IntToStr(Txt.TTFInvertedTextJustify)
        Else If PropName = 'TTFOffsetFromInvertedRect' Then Result := FloatToJsonStr(CoordToMilsF(Txt.TTFOffsetFromInvertedRect))
        Else If PropName = 'ConvertedString' Then Result := Txt.ConvertedString
        Else If PropName = 'BorderSpaceType' Then Result := IntToStr(Txt.BorderSpaceType)
        Else If PropName = 'FontName' Then Result := Txt.FontName
        Else If PropName = 'TextKind' Then Result := IntToStr(Txt.TextKind)
        Else If PropName = 'FontID' Then Result := IntToStr(Txt.FontID)
        Else If PropName = 'Bold' Then Result := BoolToJsonStr(Txt.Bold)
        Else If PropName = 'Italic' Then Result := BoolToJsonStr(Txt.Italic)
        Else If PropName = 'InvRectWidth' Then Result := FloatToJsonStr(CoordToMilsF(Txt.InvRectWidth))
        Else If PropName = 'InvRectHeight' Then Result := FloatToJsonStr(CoordToMilsF(Txt.InvRectHeight));
    End
    Else If Oid = eDimensionObject Then
    Begin
        Dim := Obj;
        If PropName = 'TextHeight' Then Result := FloatToJsonStr(CoordToMilsF(Dim.TextHeight))
        Else If PropName = 'TextWidth' Then Result := FloatToJsonStr(CoordToMilsF(Dim.TextWidth))
        Else If PropName = 'TextLineWidth' Then Result := FloatToJsonStr(CoordToMilsF(Dim.TextLineWidth))
        Else If PropName = 'TextGap' Then Result := FloatToJsonStr(CoordToMilsF(Dim.TextGap))
        Else If PropName = 'TextPosition' Then Result := IntToStr(Dim.TextPosition)
        Else If PropName = 'TextDimensionUnit' Then Result := IntToStr(Dim.TextDimensionUnit)
        Else If PropName = 'TextPrecision' Then Result := IntToStr(Dim.TextPrecision)
        Else If PropName = 'TextFormat' Then Result := Dim.TextFormat
        Else If PropName = 'TextPrefix' Then Result := Dim.TextPrefix
        Else If PropName = 'TextSuffix' Then Result := Dim.TextSuffix
        Else If PropName = 'ArrowSize' Then Result := FloatToJsonStr(CoordToMilsF(Dim.ArrowSize))
        Else If PropName = 'ArrowLineWidth' Then Result := FloatToJsonStr(CoordToMilsF(Dim.ArrowLineWidth))
        Else If PropName = 'ArrowLength' Then Result := FloatToJsonStr(CoordToMilsF(Dim.ArrowLength))
        Else If PropName = 'ArrowPosition' Then Result := IntToStr(Dim.ArrowPosition)
        Else If PropName = 'ExtensionOffset' Then Result := FloatToJsonStr(CoordToMilsF(Dim.ExtensionOffset))
        Else If PropName = 'ExtensionLineWidth' Then Result := FloatToJsonStr(CoordToMilsF(Dim.ExtensionLineWidth))
        Else If PropName = 'ExtensionPickGap' Then Result := FloatToJsonStr(CoordToMilsF(Dim.ExtensionPickGap));
    End
    Else If Oid = ePolyObject Then
    Begin
        Poly := Obj;
        If PropName = 'PolygonType' Then Result := IntToStr(Poly.PolygonType)
        Else If PropName = 'BorderWidth' Then Result := FloatToJsonStr(CoordToMilsF(Poly.BorderWidth))
        Else If PropName = 'AvoidObsticles' Then Result := BoolToJsonStr(Poly.AvoidObsticles)
        Else If PropName = 'ClipAcuteCorners' Then Result := BoolToJsonStr(Poly.ClipAcuteCorners)
        Else If PropName = 'MitreCorners' Then Result := BoolToJsonStr(Poly.MitreCorners)
        Else If PropName = 'IgnoreViolations' Then Result := BoolToJsonStr(Poly.IgnoreViolations)
        Else If PropName = 'ArcPourMode' Then Result := BoolToJsonStr(Poly.ArcPourMode)
        Else If PropName = 'DrawRemovedNecks' Then Result := BoolToJsonStr(Poly.DrawRemovedNecks)
        Else If PropName = 'DrawRemovedIslands' Then Result := BoolToJsonStr(Poly.DrawRemovedIslands)
        Else If PropName = 'DrawDeadCopper' Then Result := BoolToJsonStr(Poly.DrawDeadCopper)
        Else If PropName = 'ExpandOutline' Then Result := BoolToJsonStr(Poly.ExpandOutline)
        Else If PropName = 'AutoGenerateName' Then Result := BoolToJsonStr(Poly.AutoGenerateName);
    End
    Else If Oid = eComponentObject Then
    Begin
        Comp := Obj;
        If PropName = 'EnablePinSwapping' Then Result := BoolToJsonStr(Comp.EnablePinSwapping)
        Else If PropName = 'EnablePartSwapping' Then Result := BoolToJsonStr(Comp.EnablePartSwapping)
        Else If PropName = 'IsBGA' Then Result := BoolToJsonStr(Comp.IsBGA)
        Else If PropName = 'LockStrings' Then Result := BoolToJsonStr(Comp.LockStrings)
        Else If PropName = 'NameAutoPosition' Then Result := IntToStr(Comp.NameAutoPosition)
        Else If PropName = 'CommentAutoPosition' Then Result := IntToStr(Comp.CommentAutoPosition)
        Else If PropName = 'FootprintDescription' Then Result := Comp.FootprintDescription
        Else If PropName = 'DefaultPCB3DModel' Then Result := Comp.DefaultPCB3DModel
        Else If PropName = 'PackageSpecificHash' Then Result := Comp.PackageSpecificHash
        Else If PropName = 'SourceDescription' Then Result := Comp.SourceDescription
        Else If PropName = 'JumpersVisible' Then Result := BoolToJsonStr(Comp.JumpersVisible)
        Else If PropName = 'ChannelOffset' Then Result := IntToStr(Comp.ChannelOffset)
        Else If PropName = 'GroupNum' Then Result := IntToStr(Comp.GroupNum)
        Else If PropName = 'FlippedOnLayer' Then Result := BoolToJsonStr(Comp.FlippedOnLayer);
    End
    Else If ExtraPCBPropertyOwner(PropName) = 7 Then
    Begin
        Pad := Obj;
        If PropName = 'JumperID' Then Result := IntToStr(Pad.JumperID)
        Else If PropName = 'SwapID_Pad' Then Result := Pad.SwapID_Pad
        Else If PropName = 'SwapID_Part' Then Result := Pad.SwapID_Part
        Else If PropName = 'SwappedPadName' Then Result := Pad.SwappedPadName
        Else If PropName = 'OwnerPart_ID' Then Result := IntToStr(Pad.OwnerPart_ID)
        Else If PropName = 'DrillType' Then Result := IntToStr(Pad.DrillType)
        Else If PropName = 'IsVirtualPin' Then Result := BoolToJsonStr(Pad.IsVirtualPin);
    End
    Else If ExtraPCBPropertyOwner(PropName) = 8 Then
    Begin
        Body := Obj;
        If PropName = 'OverrideColor' Then Result := BoolToJsonStr(Body.OverrideColor)
        Else If PropName = 'Texture' Then Result := Body.Texture
        Else If PropName = 'TextureRotation' Then Result := FloatToJsonStr(Body.TextureRotation);
    End;
End;

{ 1 written, 0 Obj is not the type that carries it, -1 refused: a read-only  }
{ property, a value that is not a number (or true / false), or a negative    }
{ length. Hole tolerances may be negative.                                   }
Function PCBSetExtra(Obj : IPCB_Primitive; PropName : String; Value : String) : Integer;
Var
    Pad : IPCB_Pad;
    Via : IPCB_Via;
    Txt : IPCB_Text;
    Dim : IPCB_Dimension;
    Poly : IPCB_Polygon;
    Comp : IPCB_Component;
    Body : IPCB_ComponentBody;
    Oid, Owner, N : Integer;
    F : Double;
    B, IsBool, IsText, IsInt : Boolean;
Begin
    Result := 0;
    Oid := Obj.ObjectId;
    If Not ExtraPCBPropertyFits(Oid, PropName) Then Exit;
    Result := -1;
    If (PropName = 'IsCounterHole') Or (PropName = 'IsBGA') Or
       (PropName = 'PackageSpecificHash') Or (PropName = 'ConvertedString') Or
       (PropName = 'FlippedOnLayer') Or (PropName = 'IsVirtualPin') Or
       (PropName = 'InvRectWidth') Or (PropName = 'InvRectHeight') Then Exit;
    Owner := ExtraPCBPropertyOwner(PropName);
    If ((PropName = 'HolePositiveTolerance') Or (PropName = 'HoleNegativeTolerance'))
        And (LowerCase(Value) = 'unset') Then
    Begin
        If Oid = ePadObject Then
        Begin
            Pad := Obj;
            If PropName = 'HolePositiveTolerance' Then Pad.HolePositiveTolerance := UnsetTolerance(0)
            Else Pad.HoleNegativeTolerance := UnsetTolerance(0);
        End
        Else
        Begin
            Via := Obj;
            If PropName = 'HolePositiveTolerance' Then Via.HolePositiveTolerance := UnsetTolerance(0)
            Else Via.HoleNegativeTolerance := UnsetTolerance(0);
        End;
        Result := 1;
        Exit;
    End;
    IsBool := (PropName = 'BarCodeInverted') Or (PropName = 'BarCodeShowText') Or
        (PropName = 'Multiline') Or (PropName = 'AvoidObsticles') Or
        (PropName = 'ClipAcuteCorners') Or (PropName = 'MitreCorners') Or
        (PropName = 'IgnoreViolations') Or (PropName = 'ArcPourMode') Or
        (PropName = 'EnablePinSwapping') Or (PropName = 'EnablePartSwapping') Or
        (PropName = 'LockStrings') Or (PropName = 'Inverted') Or
        (PropName = 'UseInvertedRectangle') Or (PropName = 'DrawRemovedNecks') Or
        (PropName = 'DrawRemovedIslands') Or (PropName = 'DrawDeadCopper') Or
        (PropName = 'ExpandOutline') Or (PropName = 'AutoGenerateName') Or
        (PropName = 'JumpersVisible') Or (PropName = 'OverrideColor') Or
        (PropName = 'PasteMaskUsePercent') Or (PropName = 'PasteMaskEnabled') Or
        (PropName = 'IsPreRoute') Or (PropName = 'UserRouted') Or
        (PropName = 'Bold') Or (PropName = 'Italic');
    IsText := (PropName = 'BarCodeFontName') Or (PropName = 'FontName') Or (PropName = 'TextFormat') Or
        (PropName = 'TextPrefix') Or (PropName = 'TextSuffix') Or
        (PropName = 'FootprintDescription') Or (PropName = 'DefaultPCB3DModel') Or
        (PropName = 'SwapID_Pad') Or (PropName = 'SwapID_Part') Or
        (PropName = 'SourceDescription') Or (PropName = 'SwappedPadName') Or
        (PropName = 'Texture');
    IsInt := (PropName = 'BarCodeKind') Or (PropName = 'BarCodeRenderMode') Or
        (PropName = 'MultilineTextAutoPosition') Or (PropName = 'TextPosition') Or
        (PropName = 'TextDimensionUnit') Or (PropName = 'TextPrecision') Or
        (PropName = 'ArrowPosition') Or (PropName = 'PolygonType') Or
        (PropName = 'NameAutoPosition') Or (PropName = 'CommentAutoPosition') Or
        (PropName = 'JumperID') Or (PropName = 'TTFInvertedTextJustify') Or
        (PropName = 'ChannelOffset') Or (PropName = 'GroupNum') Or
        (PropName = 'OwnerPart_ID') Or (PropName = 'PasteMaskExpansionMode') Or
        (PropName = 'SolderMaskExpansionMode') Or (PropName = 'DrillType') Or
        (PropName = 'BorderSpaceType') Or (PropName = 'TextKind') Or
        (PropName = 'FontID');
    B := False;
    F := 0;
    N := 0;
    If IsBool Then
    Begin
        If LowerCase(Value) = 'true' Then B := True
        Else If LowerCase(Value) <> 'false' Then Exit;
    End
    Else If Not IsText Then
    Begin
        If Not IsFloatStr(Value) Then Exit;
        F := StrToFloatDef(Value, 0);
        If IsInt Then
        Begin
            If (Round(F) <> F) Or (F < 0) Then Exit;
            N := Round(F);
        End
        Else If (F < 0) And (Owner <> 1) Then Exit;
    End;

    If Owner = 9 Then
    Begin
        If PropName = 'IsPreRoute' Then Obj.IsPreRoute := B
        Else Obj.UserRouted := B;
    End
    Else If (Owner = 1) And ((PropName = 'PasteMaskExpansionMode') Or
        (PropName = 'SolderMaskExpansionMode') Or (PropName = 'PasteMaskUsePercent') Or
        (PropName = 'PasteMaskPercent') Or (PropName = 'PasteMaskEnabled')) Then
    Begin
        If (PropName = 'PasteMaskPercent') And (F > 100) Then
        Begin
            Result := -1;
            Exit;
        End;
        If Oid = ePadObject Then
        Begin
            Pad := Obj;
            If PropName = 'PasteMaskExpansionMode' Then Pad.PasteMaskExpansionMode := N
            Else If PropName = 'SolderMaskExpansionMode' Then Pad.SolderMaskExpansionMode := N
            Else If PropName = 'PasteMaskUsePercent' Then Pad.PasteMaskUsePercent := B
            Else If PropName = 'PasteMaskPercent' Then Pad.PasteMaskPercent := F
            Else Pad.PasteMaskEnabled := B;
        End
        Else
        Begin
            Via := Obj;
            If PropName = 'PasteMaskExpansionMode' Then Via.PasteMaskExpansionMode := N
            Else If PropName = 'SolderMaskExpansionMode' Then Via.SolderMaskExpansionMode := N
            Else If PropName = 'PasteMaskUsePercent' Then Via.PasteMaskUsePercent := B
            Else If PropName = 'PasteMaskPercent' Then Via.PasteMaskPercent := F
            Else Via.PasteMaskEnabled := B;
        End;
    End
    Else If Owner = 1 Then
    Begin
        If Oid = ePadObject Then
        Begin
            Pad := Obj;
            If PropName = 'HolePositiveTolerance' Then Pad.HolePositiveTolerance := MilsToCoordF(F)
            Else Pad.HoleNegativeTolerance := MilsToCoordF(F);
        End
        Else
        Begin
            Via := Obj;
            If PropName = 'HolePositiveTolerance' Then Via.HolePositiveTolerance := MilsToCoordF(F)
            Else Via.HoleNegativeTolerance := MilsToCoordF(F);
        End;
    End
    Else If Owner = 3 Then
    Begin
        Txt := Obj;
        If PropName = 'BarCodeKind' Then Txt.BarCodeKind := N
        Else If PropName = 'BarCodeRenderMode' Then Txt.BarCodeRenderMode := N
        Else If PropName = 'BarCodeFullWidth' Then Txt.BarCodeFullWidth := MilsToCoordF(F)
        Else If PropName = 'BarCodeFullHeight' Then Txt.BarCodeFullHeight := MilsToCoordF(F)
        Else If PropName = 'BarCodeXMargin' Then Txt.BarCodeXMargin := MilsToCoordF(F)
        Else If PropName = 'BarCodeYMargin' Then Txt.BarCodeYMargin := MilsToCoordF(F)
        Else If PropName = 'BarCodeMinWidth' Then Txt.BarCodeMinWidth := MilsToCoordF(F)
        Else If PropName = 'BarCodeInverted' Then Txt.BarCodeInverted := B
        Else If PropName = 'BarCodeShowText' Then Txt.BarCodeShowText := B
        Else If PropName = 'BarCodeFontName' Then Txt.BarCodeFontName := Value
        Else If PropName = 'Multiline' Then Txt.Multiline := B
        Else If PropName = 'MultilineTextWidth' Then Txt.MultilineTextWidth := MilsToCoordF(F)
        Else If PropName = 'MultilineTextHeight' Then Txt.MultilineTextHeight := MilsToCoordF(F)
        Else If PropName = 'MultilineTextAutoPosition' Then Txt.MultilineTextAutoPosition := N
        Else If PropName = 'Inverted' Then Txt.Inverted := B
        Else If PropName = 'UseInvertedRectangle' Then Txt.UseInvertedRectangle := B
        Else If PropName = 'InvertedTTTextBorder' Then Txt.InvertedTTTextBorder := MilsToCoordF(F)
        Else If PropName = 'TTFInvertedTextJustify' Then Txt.TTFInvertedTextJustify := N
        Else If PropName = 'TTFOffsetFromInvertedRect' Then Txt.TTFOffsetFromInvertedRect := MilsToCoordF(F)
        Else If PropName = 'BorderSpaceType' Then Txt.BorderSpaceType := N
        { An empty font name or a kind / stroke font Altium does not have is }
        { refused rather than written. A TrueType kind keeps the legacy       }
        { UseTTFonts flag in step, as Altium's own dialog does.               }
        Else If PropName = 'FontName' Then
        Begin
            If Trim(Value) = '' Then Exit;
            Txt.FontName := Value;
        End
        Else If PropName = 'TextKind' Then
        Begin
            If N > 2 Then Exit;
            Txt.TextKind := N;
            Txt.UseTTFonts := N = 1;
        End
        Else If PropName = 'FontID' Then
        Begin
            If (N < 1) Or (N > 3) Then Exit;
            Txt.FontID := N;
        End
        Else If PropName = 'Bold' Then Txt.Bold := B
        Else If PropName = 'Italic' Then Txt.Italic := B;
    End
    Else If Owner = 4 Then
    Begin
        Dim := Obj;
        If PropName = 'TextHeight' Then Dim.TextHeight := MilsToCoordF(F)
        Else If PropName = 'TextWidth' Then Dim.TextWidth := MilsToCoordF(F)
        Else If PropName = 'TextLineWidth' Then Dim.TextLineWidth := MilsToCoordF(F)
        Else If PropName = 'TextGap' Then Dim.TextGap := MilsToCoordF(F)
        Else If PropName = 'TextPosition' Then Dim.TextPosition := N
        Else If PropName = 'TextDimensionUnit' Then Dim.TextDimensionUnit := N
        Else If PropName = 'TextPrecision' Then Dim.TextPrecision := N
        Else If PropName = 'TextFormat' Then Dim.TextFormat := Value
        Else If PropName = 'TextPrefix' Then Dim.TextPrefix := Value
        Else If PropName = 'TextSuffix' Then Dim.TextSuffix := Value
        Else If PropName = 'ArrowSize' Then Dim.ArrowSize := MilsToCoordF(F)
        Else If PropName = 'ArrowLineWidth' Then Dim.ArrowLineWidth := MilsToCoordF(F)
        Else If PropName = 'ArrowLength' Then Dim.ArrowLength := MilsToCoordF(F)
        Else If PropName = 'ArrowPosition' Then Dim.ArrowPosition := N
        Else If PropName = 'ExtensionOffset' Then Dim.ExtensionOffset := MilsToCoordF(F)
        Else If PropName = 'ExtensionLineWidth' Then Dim.ExtensionLineWidth := MilsToCoordF(F)
        Else If PropName = 'ExtensionPickGap' Then Dim.ExtensionPickGap := MilsToCoordF(F);
    End
    Else If Owner = 5 Then
    Begin
        Poly := Obj;
        If PropName = 'PolygonType' Then Poly.PolygonType := N
        Else If PropName = 'BorderWidth' Then Poly.BorderWidth := MilsToCoordF(F)
        Else If PropName = 'AvoidObsticles' Then Poly.AvoidObsticles := B
        Else If PropName = 'ClipAcuteCorners' Then Poly.ClipAcuteCorners := B
        Else If PropName = 'MitreCorners' Then Poly.MitreCorners := B
        Else If PropName = 'IgnoreViolations' Then Poly.IgnoreViolations := B
        Else If PropName = 'ArcPourMode' Then Poly.ArcPourMode := B
        Else If PropName = 'DrawRemovedNecks' Then Poly.DrawRemovedNecks := B
        Else If PropName = 'DrawRemovedIslands' Then Poly.DrawRemovedIslands := B
        Else If PropName = 'DrawDeadCopper' Then Poly.DrawDeadCopper := B
        Else If PropName = 'ExpandOutline' Then Poly.ExpandOutline := B
        Else If PropName = 'AutoGenerateName' Then Poly.AutoGenerateName := B;
    End
    Else If Owner = 6 Then
    Begin
        Comp := Obj;
        If PropName = 'EnablePinSwapping' Then Comp.EnablePinSwapping := B
        Else If PropName = 'EnablePartSwapping' Then Comp.EnablePartSwapping := B
        Else If PropName = 'LockStrings' Then Comp.LockStrings := B
        { Change*Autoposition moves the text to the anchor as well as       }
        { recording it; writing NameAutoPosition alone leaves it where it is. }
        Else If PropName = 'NameAutoPosition' Then Comp.ChangeNameAutoposition(N)
        Else If PropName = 'CommentAutoPosition' Then Comp.ChangeCommentAutoposition(N)
        Else If PropName = 'FootprintDescription' Then Comp.FootprintDescription := Value
        Else If PropName = 'DefaultPCB3DModel' Then Comp.DefaultPCB3DModel := Value
        Else If PropName = 'SourceDescription' Then Comp.SourceDescription := Value
        Else If PropName = 'JumpersVisible' Then Comp.JumpersVisible := B
        Else If PropName = 'ChannelOffset' Then Comp.ChannelOffset := N
        Else If PropName = 'GroupNum' Then Comp.GroupNum := N;
    End
    Else If Owner = 7 Then
    Begin
        Pad := Obj;
        If PropName = 'JumperID' Then Pad.JumperID := N
        Else If PropName = 'SwapID_Pad' Then Pad.SwapID_Pad := Value
        Else If PropName = 'SwapID_Part' Then Pad.SwapID_Part := Value
        Else If PropName = 'SwappedPadName' Then Pad.SwappedPadName := Value
        Else If PropName = 'OwnerPart_ID' Then Pad.OwnerPart_ID := N
        Else If PropName = 'DrillType' Then
        Begin
            { Drilled, punched, laser or plasma: 0 to 3. }
            If N > 3 Then Exit;
            Pad.DrillType := N;
        End;
    End
    Else If Owner = 8 Then
    Begin
        Body := Obj;
        If PropName = 'OverrideColor' Then Body.OverrideColor := B
        Else If PropName = 'Texture' Then Body.Texture := Value
        Else If PropName = 'TextureRotation' Then Body.TextureRotation := F;
    End;
    Result := 1;
End;

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

{ ---- VIAS AND PADS ON A BOARD: mask, tenting, testpoints, stack ----------- }
{ The library route (library.set_via_properties) reaches much of this on a  }
{ footprint, and the board route none of it: generic.* on a PcbDoc knew a    }
{ via's Size and HoleSize and nothing of                                     }
{ its mask, tenting, testpoint flags or stack. Each helper uses only members  }
{ that the library route already compiles and has exercised live.            }
{                                                                            }
{ THE MASK IS REACHED THROUGH IPCB_Pad. IPCB_Via does not expose the cache;   }
{ casting the via to a pad and that to the primitive does (issue #60).       }
{ Assigning Via.SolderMaskExpansion directly on a board via faults in         }
{ ScriptingSystem.DLL (PCB_SetViaSoldermaskRelief); the cache route is the    }
{ one that works.                                                            }
{ SolderMaskExpansion is the TOP value once SolderMaskSeparate is true (the   }
{ two sides unlinked); with it false that one value serves both sides and    }
{ the bottom field is a shadow (issue #61). An expansion only takes         }
{ effect with SolderMaskManual true -- otherwise the design rule wins.        }
Function PCBGetMask(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Via    : IPCB_Via;
    AsPad  : IPCB_Pad;
    AsPrim : IPCB_Primitive;
    Cache  : TPadCache;
Begin
    Result := '';
    If Obj.ObjectId = eViaObject Then
    Begin
        Via := Obj;
        AsPad := Via;
    End
    Else If Obj.ObjectId = ePadObject Then
        AsPad := Obj
    Else Exit;
    AsPrim := AsPad;
    If PropName = 'SolderMaskFromHoleEdge' Then
    Begin
        Result := BoolToJsonStr(AsPrim.SolderMaskExpansionFromHoleEdge);
        Exit;
    End;
    Cache := AsPrim.GetState_Cache;
    If PropName = 'SolderMaskExpansion' Then
        Result := FloatToJsonStr(CoordToMilsF(Cache.SolderMaskExpansion))
    Else If PropName = 'SolderMaskBottomExpansion' Then
        Result := FloatToJsonStr(CoordToMilsF(Cache.SolderMaskBottomExpansion))
    Else If PropName = 'SolderMaskSeparate' Then
        Result := BoolToJsonStr(Cache.UseSeparateExpansions)
    Else If PropName = 'SolderMaskManual' Then
        Result := BoolToJsonStr(Cache.SolderMaskExpansionValid = eCacheManual);
End;

{ One field per call, each a fetch-edit-write of the cache. Give them in     }
{ the order they must land, in one set string: SolderMaskFromHoleEdge and    }
{ SolderMaskManual BEFORE the expansion values, or the value is measured     }
{ from the wrong edge or ignored.                                            }
Function PCBSetMask(Obj : IPCB_Primitive; PropName : String; Value : String) : Boolean;
Var
    Via    : IPCB_Via;
    AsPad  : IPCB_Pad;
    AsPrim : IPCB_Primitive;
    Cache  : TPadCache;
Begin
    Result := False;
    If Obj.ObjectId = eViaObject Then
    Begin
        Via := Obj;
        AsPad := Via;
    End
    Else If Obj.ObjectId = ePadObject Then
        AsPad := Obj
    Else Exit;
    AsPrim := AsPad;
    If PropName = 'SolderMaskFromHoleEdge' Then
    Begin
        AsPrim.SolderMaskExpansionFromHoleEdge := StrToBool(Value);
        Result := True;
        Exit;
    End;
    Cache := AsPrim.GetState_Cache;
    If PropName = 'SolderMaskExpansion' Then
        Cache.SolderMaskExpansion := MilsToCoordF(StrToFloatDef(Value, 0))
    Else If PropName = 'SolderMaskBottomExpansion' Then
        Cache.SolderMaskBottomExpansion := MilsToCoordF(StrToFloatDef(Value, 0))
    Else If PropName = 'SolderMaskSeparate' Then
        Cache.UseSeparateExpansions := StrToBool(Value)
    Else If PropName = 'SolderMaskManual' Then
    Begin
        { Back to the rule is eCacheInvalid: Altium recomputes from the    }
        { design rules. eCacheValid would assert that a rule value already }
        { sits in the field, which after an override it does not           }
        { (PCB_ApplyDnpPasteExclusion).                                    }
        If StrToBool(Value) Then
            Cache.SolderMaskExpansionValid := eCacheManual
        Else
            Cache.SolderMaskExpansionValid := eCacheInvalid;
    End
    Else Exit;
    AsPrim.SetState_Cache := Cache;
    Result := True;
End;

{ The four testpoint flags (fabrication / assembly x top / bottom) on a via  }
{ or a pad, and a via's tenting. The flags are the designer's statement of   }
{ where a probe may land: READ here, never written.                          }
Function PCBGetViaFlag(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Via : IPCB_Via;
    Pad : IPCB_Pad;
Begin
    Result := '';
    If Obj.ObjectId = eViaObject Then
    Begin
        Via := Obj;
        If PropName = 'IsTenting_Top' Then Result := BoolToJsonStr(Via.IsTenting_Top)
        Else If PropName = 'IsTenting_Bottom' Then Result := BoolToJsonStr(Via.IsTenting_Bottom)
        Else If PropName = 'IsTestpoint_Top' Then Result := BoolToJsonStr(Via.IsTestpoint_Top)
        Else If PropName = 'IsTestpoint_Bottom' Then Result := BoolToJsonStr(Via.IsTestpoint_Bottom)
        Else If PropName = 'IsAssyTestpoint_Top' Then Result := BoolToJsonStr(Via.IsAssyTestpoint_Top)
        Else If PropName = 'IsAssyTestpoint_Bottom' Then Result := BoolToJsonStr(Via.IsAssyTestpoint_Bottom);
    End
    Else If Obj.ObjectId = ePadObject Then
    Begin
        Pad := Obj;
        If PropName = 'IsTestpoint_Top' Then Result := BoolToJsonStr(Pad.IsTestpoint_Top)
        Else If PropName = 'IsTestpoint_Bottom' Then Result := BoolToJsonStr(Pad.IsTestpoint_Bottom)
        Else If PropName = 'IsAssyTestpoint_Top' Then Result := BoolToJsonStr(Pad.IsAssyTestpoint_Top)
        Else If PropName = 'IsAssyTestpoint_Bottom' Then Result := BoolToJsonStr(Pad.IsAssyTestpoint_Bottom);
    End;
End;

{ A via's or a pad's stack, under the Via dialog's names: simple (one size   }
{ for every layer), top-middle-bottom, full-stack.                           }
Function PCBStackName(M : Integer) : String;
Begin
    If M = ePadMode_Simple Then Result := 'simple'
    Else If M = ePadMode_LocalStack Then Result := 'top-middle-bottom'
    Else If M = ePadMode_ExternalStack Then Result := 'full-stack'
    Else Result := 'unknown';
End;

Function PCBGetStack(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Via : IPCB_Via;
    Pad : IPCB_Pad;
Begin
    Result := '';
    If Obj.ObjectId = eViaObject Then
    Begin
        Via := Obj;
        If PropName = 'Mode' Then Result := PCBStackName(Via.Mode)
        { A simple via stores no middle size: SizeMid reads 0 there, and   }
        { that is the right answer (the top size serves every layer).      }
        Else If PropName = 'SizeTop' Then Result := FloatToJsonStr(CoordToMilsF(Via.SizeOnLayer(eTopLayer)))
        Else If PropName = 'SizeMid' Then Result := FloatToJsonStr(CoordToMilsF(Via.SizeOnLayer(eMidLayers)))
        Else If PropName = 'SizeBottom' Then Result := FloatToJsonStr(CoordToMilsF(Via.SizeOnLayer(eBottomLayer)));
    End
    Else If Obj.ObjectId = ePadObject Then
    Begin
        Pad := Obj;
        If PropName = 'Mode' Then Result := PCBStackName(Pad.Mode);
    End;
End;

{ MODE FIRST, in a set string: on a simple via the middle size has nowhere  }
{ to go until the mode change has exposed it. A middle size cannot be read  }
{ back reliably (issue #65): the write is not verifiable.                   }
Function PCBSetStack(Obj : IPCB_Primitive; PropName : String; Value : String) : Integer;
Var
    Via : IPCB_Via;
    Pad : IPCB_Pad;
    M   : Integer;
Begin
    Result := 0;
    If PropName = 'Mode' Then
    Begin
        If Value = 'simple' Then M := ePadMode_Simple
        Else If Value = 'top-middle-bottom' Then M := ePadMode_LocalStack
        Else If Value = 'full-stack' Then M := ePadMode_ExternalStack
        Else Begin Result := -1; Exit; End;
        If Obj.ObjectId = eViaObject Then Begin Via := Obj; Via.Mode := M; Result := 1; End
        Else If Obj.ObjectId = ePadObject Then Begin Pad := Obj; Pad.Mode := M; Result := 1; End;
        Exit;
    End;
    If Obj.ObjectId <> eViaObject Then Exit;
    Via := Obj;
    If PropName = 'SizeTop' Then Via.SizeOnLayer(eTopLayer) := MilsToCoordF(StrToFloatDef(Value, 0))
    Else If PropName = 'SizeMid' Then Via.SizeOnLayer(eMidLayers) := MilsToCoordF(StrToFloatDef(Value, 0))
    Else If PropName = 'SizeBottom' Then Via.SizeOnLayer(eBottomLayer) := MilsToCoordF(StrToFloatDef(Value, 0))
    Else Exit;
    Result := 1;
End;

{ ---- A POLYGON'S POUR SETTINGS (issue #125) ------------------------------ }
{ The Polygon Pour dialog's fields, under the names Altium's own demo       }
{ IteratePolygons.pas reads: Grid, TrackSize (hatched), MinTrack,           }
{ ArcApproximation and NeckWidthThreshold are lengths, decimal mils;        }
{ IslandAreaThreshold is an area, square mils (the API holds coordinates    }
{ squared, and one mil is 10000 coordinates, so 1e8 per square mil);        }
{ PolyHatchStyle and PourOver are the raw ordinals (1 = 45 degrees,         }
{ 5 = solid; 2 = pour over same-net polygons only), so a copy from one      }
{ polygon to another is exact whatever the enum's names; UseOctagons is a   }
{ flag; PourIndex and Poured are read only. The caller has checked it is   }
{ a polygon (an ePolyObject test here would read as a position resolver).  }
Function PCBGetPoly(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Poly : IPCB_Polygon;
    Area : Double;
Begin
    Result := '';
    Poly := Obj;
    If PropName = 'Grid' Then Result := FloatToJsonStr(CoordToMilsF(Poly.Grid))
    Else If PropName = 'TrackSize' Then Result := FloatToJsonStr(CoordToMilsF(Poly.TrackSize))
    Else If PropName = 'MinTrack' Then Result := FloatToJsonStr(CoordToMilsF(Poly.MinTrack))
    Else If PropName = 'ArcApproximation' Then Result := FloatToJsonStr(CoordToMilsF(Poly.ArcApproximation))
    Else If PropName = 'NeckWidthThreshold' Then Result := FloatToJsonStr(CoordToMilsF(Poly.NeckWidthThreshold))
    Else If PropName = 'IslandAreaThreshold' Then
    Begin
        Area := Poly.IslandAreaThreshold;
        Result := FloatToJsonStr(Area / 100000000.0);
    End
    Else If PropName = 'PolyHatchStyle' Then Result := IntToStr(Poly.PolyHatchStyle)
    Else If PropName = 'PourOver' Then Result := IntToStr(Poly.PourOver)
    Else If PropName = 'UseOctagons' Then Result := BoolToJsonStr(Poly.UseOctagons)
    Else If PropName = 'PourIndex' Then Result := IntToStr(Poly.PourIndex)
    Else If PropName = 'Poured' Then Result := BoolToJsonStr(Poly.Poured);
End;

{ True when written. A negative number, a non-number, a fractional or       }
{ out-of-range ordinal, anything but true / false for UseOctagons and an    }
{ empty Name are refused and nothing is written. The caller has checked it is a polygon.   }
{ Nothing here repours: the settings decide what the NEXT pour does, as     }
{ with RemoveDead and pcb_modify_polygon.                                   }
Function PCBSetPoly(Obj : IPCB_Primitive; PropName : String; Value : String) : Boolean;
Var
    Poly : IPCB_Polygon;
    F    : Double;
Begin
    Result := False;
    Poly := Obj;
    If PropName = 'Name' Then
    Begin
        If Trim(Value) = '' Then Exit;
        { A pour with Auto Naming on builds its name from layer, net and    }
        { index again and the assigned one is lost, while the write answered }
        { success (issue #150). Typing a name in the Polygon Pour dialog     }
        { unticks Auto Naming; do the same, then report what the pour holds. }
        Try Poly.AutoGenerateName := False; Except End;
        Poly.Name := Value;
        Result := Poly.Name = Value;
        Exit;
    End;
    If PropName = 'UseOctagons' Then
    Begin
        If LowerCase(Value) = 'true' Then Poly.UseOctagons := True
        Else If LowerCase(Value) = 'false' Then Poly.UseOctagons := False
        Else Exit;
        Result := True;
        Exit;
    End;
    If Not IsFloatStr(Value) Then Exit;
    F := StrToFloatDef(Value, -1);
    If F < 0 Then Exit;
    If (PropName = 'PolyHatchStyle') Or (PropName = 'PourOver') Then
    Begin
        If Round(F) <> F Then Exit;
        If (PropName = 'PolyHatchStyle') And (Round(F) > 5) Then Exit;
        If (PropName = 'PourOver') And (Round(F) > 2) Then Exit;
    End;
    If PropName = 'Grid' Then Poly.Grid := MilsToCoordF(F)
    Else If PropName = 'TrackSize' Then Poly.TrackSize := MilsToCoordF(F)
    Else If PropName = 'MinTrack' Then Poly.MinTrack := MilsToCoordF(F)
    Else If PropName = 'ArcApproximation' Then Poly.ArcApproximation := MilsToCoordF(F)
    Else If PropName = 'NeckWidthThreshold' Then Poly.NeckWidthThreshold := MilsToCoordF(F)
    Else If PropName = 'IslandAreaThreshold' Then Poly.IslandAreaThreshold := F * 100000000.0
    Else If PropName = 'PolyHatchStyle' Then Poly.PolyHatchStyle := Round(F)
    Else If PropName = 'PourOver' Then Poly.PourOver := Round(F)
    Else Exit;
    Result := True;
End;

{ ---- A PRIMITIVE'S NET, WRITTEN (issue #126) ------------------------------ }
{ '' leaves the primitive on no net; a name joins that net, which must       }
{ already exist. False = no such net, so the caller refuses instead of       }
{ silently leaving the old net in place. Membership is kept BOTH ways, as   }
{ BindPrimitiveToNet in PCB.pas does it: Prim.Net alone sets a reference    }
{ while the net's own collection, which connectivity and pours walk, still  }
{ holds (or lacks) the object. PCB.pas builds after this unit, so its net   }
{ helpers cannot be called from here; this is the small copy.               }
Function PCBSetNet(Obj : IPCB_Primitive; Value : String) : Boolean;
Var
    Board  : IPCB_Board;
    Iter   : IPCB_BoardIterator;
    NetObj : IPCB_Net;
    OldNet : IPCB_Net;
Begin
    Result := False;
    NetObj := Nil;
    If Value <> '' Then
    Begin
        Board := GetPCBBoardAnywhere(0);
        If Board = Nil Then Exit;
        Iter := Board.BoardIterator_Create;
        Iter.AddFilter_ObjectSet(MkSet(eNetObject));
        Iter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Iter.AddFilter_Method(eProcessAll);
        NetObj := Iter.FirstPCBObject;
        While NetObj <> Nil Do
        Begin
            If NetObj.Name = Value Then Break;
            NetObj := Iter.NextPCBObject;
        End;
        Board.BoardIterator_Destroy(Iter);
        If NetObj = Nil Then Exit;
    End;
    OldNet := Obj.Net;
    If OldNet <> Nil Then
        Try OldNet.RemovePCBObject(Obj); Except End;
    Obj.Net := NetObj;
    If NetObj <> Nil Then
        Try NetObj.AddPCBObject(Obj); Except End;
    Result := True;
End;

{ ---- A PRIMITIVE'S BOX, A BODY'S IDENTIFIER, AND UNIONS ------------------- }
{ What a board-side 3D envelope needs: the extent of a component's bodies,  }
{ courtyard and pads, the Identifier a generated body carries, and the      }
{ union that ties it to its component. X/Y on a body is the first vertex of }
{ its outline, not its centre, so the box is the only usable extent.         }
{ NOTE: a pad's box includes its solder-mask expansion; use TopXSize /      }
{ TopYSize for the copper.                                                  }
Function PCBGetBounds(Obj : IPCB_Primitive; PropName : String) : String;
Var
    Box : TCoordRect;
Begin
    Box := Obj.BoundingRectangle;
    If PropName = 'BoundsLeft' Then Result := FloatToJsonStr(CoordToMilsF(Box.Left))
    Else If PropName = 'BoundsBottom' Then Result := FloatToJsonStr(CoordToMilsF(Box.Bottom))
    Else If PropName = 'BoundsRight' Then Result := FloatToJsonStr(CoordToMilsF(Box.Right))
    Else If PropName = 'BoundsTop' Then Result := FloatToJsonStr(CoordToMilsF(Box.Top))
    Else Result := '';
End;

{ A COMPONENT'S UNION IS ITS OWN AND ALL ITS PARTS'. Setting it on the       }
{ component alone leaves its pads, tracks and bodies out, and the union      }
{ does not hold. Every layer, Mechanical17 and up included, so the walk    }
{ uses the IPCB layer set. Called inside the component's own modify         }
{ bracket (ProcessPCBBoardObjects), so no second one here.                  }
Procedure PCBSetComponentUnion(Comp : IPCB_Component; UIndex : Integer);
Var
    GIter : IPCB_GroupIterator;
    Prim  : IPCB_Primitive;
Begin
    Try Comp.SetState_UnionIndex := UIndex; Except End;
    Try Comp.Name.SetState_UnionIndex := UIndex; Except End;
    Try Comp.Comment.SetState_UnionIndex := UIndex; Except End;
    GIter := Comp.GroupIterator_Create;
    Try
        { The kinds a component owns that carry a union index, never      }
        { AllObjects: an unfiltered walk can hand back an object the       }
        { scripting engine cannot cast, an access violation no Try        }
        { catches (#44).                                                   }
        GIter.AddFilter_ObjectSet(MkSet(eTrackObject, eArcObject,
            ePadObject, eViaObject, eFillObject, eRegionObject,
            eTextObject, eComponentBodyObject));
        GIter.AddFilter_IPCB_LayerSet(LayerSet.AllLayers);
        Prim := GIter.FirstPCBObject;
        While Prim <> Nil Do
        Begin
            Prim.BeginModify;
            Try Prim.SetState_UnionIndex := UIndex; Except End;
            Prim.EndModify;
            Prim := GIter.NextPCBObject;
        End;
    Finally
        Comp.GroupIterator_Destroy(GIter);
    End;
End;

{ Every primitive carries a union index; a component's is its parts' too. }
Procedure PCBSetUnion(Obj : IPCB_Primitive; UIndex : Integer);
Var
    Comp : IPCB_Component;
Begin
    If Obj.ObjectId = eComponentObject Then
    Begin
        Comp := Obj;
        PCBSetComponentUnion(Comp, UIndex);
    End
    Else Obj.SetState_UnionIndex := UIndex;
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
            { Poured copper carries no net of its own: the pour that made it }
            { does, the same owner PCB_LayoutModel reads. A poured region    }
            { read as netless, and a Net filter could not find it.           }
            If Obj.Net <> Nil Then
            Begin
                Result := Obj.Net.Name;
            End
            Else
            Begin
                If Obj.InPolygon Then
                Begin
                    If Obj.Polygon <> Nil Then
                    Begin
                        If Obj.Polygon.Net <> Nil Then Result := Obj.Polygon.Net.Name;
                    End;
                End;
            End;
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
        { What a keepout keeps out: the Keepout Restrictions in the       }
        { Properties panel. Copper alone is a wall polygons stop at while }
        { tracks, vias and pads may still cross it.                       }
        { Only asked of the four objects that have them: asked of a via,  }
        { the property raised 'Undeclared identifier' as a modal.          }
        Else If (PropName = 'IsKeepoutForCopper') Or (PropName = 'IsKeepoutForTrack') Or
                (PropName = 'IsKeepoutForVia') Or (PropName = 'IsKeepoutForSMDPad') Or
                (PropName = 'IsKeepoutForTHPad') Then
        Begin
            If (Oid = eTrackObject) Or (Oid = eArcObject) Or (Oid = eFillObject) Or
               (Oid = eRegionObject) Then
            Begin
                If PropName = 'IsKeepoutForCopper' Then Result := BoolToJsonStr(Obj.IsKeepoutForCopper);
                If PropName = 'IsKeepoutForTrack' Then Result := BoolToJsonStr(Obj.IsKeepoutForTrack);
                If PropName = 'IsKeepoutForVia' Then Result := BoolToJsonStr(Obj.IsKeepoutForVia);
                If PropName = 'IsKeepoutForSMDPad' Then Result := BoolToJsonStr(Obj.IsKeepoutForSMDPad);
                If PropName = 'IsKeepoutForTHPad' Then Result := BoolToJsonStr(Obj.IsKeepoutForTHPad);
            End;
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
            If Oid = eConnectionObject Then Begin Conn := Obj; Result := PCBConnMode(Conn); End
            Else If (Oid = eViaObject) Or (Oid = ePadObject) Then Result := PCBGetStack(Obj, PropName);
        End
        Else If (PropName = 'SizeTop') Or (PropName = 'SizeMid') Or
                (PropName = 'SizeBottom') Then
            Result := PCBGetStack(Obj, PropName)
        Else If (PropName = 'SolderMaskExpansion') Or
                (PropName = 'SolderMaskBottomExpansion') Or
                (PropName = 'SolderMaskSeparate') Or
                (PropName = 'SolderMaskManual') Or
                (PropName = 'SolderMaskFromHoleEdge') Then
            Result := PCBGetMask(Obj, PropName)
        Else If (PropName = 'IsTenting_Top') Or (PropName = 'IsTenting_Bottom') Or
                (PropName = 'IsTestpoint_Top') Or (PropName = 'IsTestpoint_Bottom') Or
                (PropName = 'IsAssyTestpoint_Top') Or
                (PropName = 'IsAssyTestpoint_Bottom') Then
            Result := PCBGetViaFlag(Obj, PropName)
        Else If (PropName = 'BoundsLeft') Or (PropName = 'BoundsBottom') Or
                (PropName = 'BoundsRight') Or (PropName = 'BoundsTop') Then
            Result := PCBGetBounds(Obj, PropName)
        Else If PropName = 'UnionIndex' Then
            Result := IntToStr(Obj.UnionIndex)
        Else If PropName = 'Identifier' Then
        Begin
            If Oid = eComponentBodyObject Then Begin Body := Obj; Result := Body.Identifier; End;
        End
        Else If PropName = 'Width' Then
        Begin
            If Oid = eTrackObject Then Begin Track := Obj; Result := FloatToJsonStr(CoordToMilsF(Track.Width)); End
            Else If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(CoordToMilsF(Arc.LineWidth)); End
            Else If Oid = eTextObject Then Begin Txt := Obj; Result := FloatToJsonStr(CoordToMilsF(Txt.Width)); End;
        End
        Else If PropName = 'XCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(CoordToMilsF(Arc.XCenter)); End;
        End
        Else If PropName = 'YCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(CoordToMilsF(Arc.YCenter)); End;
        End
        Else If PropName = 'Radius' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Result := FloatToJsonStr(CoordToMilsF(Arc.Radius)); End;
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
        { The bottom copper: a simple pad's top size serves every layer, a  }
        { stacked one keeps its own.                                       }
        { The same shape as a word, with the comparisons the library pad   }
        { reader makes (get_pad_geometry): the ordinal is not documented.  }
        Else If PropName = 'TopShapeName' Then
        Begin
            If Oid = ePadObject Then
            Begin
                Pad := Obj;
                If Pad.TopShape = eRectangular Then Result := 'rectangular'
                Else If Pad.TopShape = eOctagonal Then Result := 'octagonal'
                Else If Pad.TopShape = eRoundedRectangular Then Result := 'roundrectangle'
                Else If Pad.TopShape = eCustomShape Then Result := 'custom'
                Else Result := 'round';
            End;
        End
        Else If PropName = 'BotXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(CoordToMilsF(Pad.BotXSize)); End;
        End
        Else If PropName = 'BotYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(CoordToMilsF(Pad.BotYSize)); End;
        End
        Else If PropName = 'Plated' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := BoolToJsonStr(Pad.Plated); End;
        End
        { A pad's drill: round, square or slot. For a slot HoleSize is its }
        { width and HoleWidth its overall length; HoleRotation turns the   }
        { drill within the pad, on top of the pad's own Rotation.          }
        Else If PropName = 'HoleType' Then
        Begin
            If Oid = ePadObject Then
            Begin
                Pad := Obj;
                If Pad.HoleType = eSlotHole Then Result := 'slot'
                Else If Pad.HoleType = eSquareHole Then Result := 'square'
                Else Result := 'round';
            End;
        End
        Else If PropName = 'HoleWidth' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(CoordToMilsF(Pad.HoleWidth)); End;
        End
        Else If PropName = 'HoleRotation' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Result := FloatToJsonStr(Pad.HoleRotation); End;
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
        { The schematic link of a placed part: the UniqueId of the symbol it }
        { came from and the sheet-symbol path above it, the pair a multi-   }
        { channel design needs to tell its copies apart. A board part has   }
        { no UniqueId of its own that the schematic matches on.             }
        Else If PropName = 'SourceUniqueId' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceUniqueId; End;
        End
        Else If PropName = 'SourceHierarchicalPath' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceHierarchicalPath; End;
        End
        Else If PropName = 'SourceLibReference' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceLibReference; End;
        End
        Else If PropName = 'SourceFootprintLibrary' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceFootprintLibrary; End;
        End
        Else If PropName = 'SourceComponentLibrary' Then
        Begin
            If Oid = eComponentObject Then Begin Comp := Obj; Result := Comp.SourceComponentLibrary; End;
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
            { A polygon's Name, as the Polygon Pour dialog shows it (#125). }
            If Oid = ePolyObject Then Begin Poly := Obj; Result := Poly.Name; End;
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
        { The rest of the Polygon Pour dialog (issue #125), see PCBGetPoly. }
        Else If (PropName = 'Grid') Or (PropName = 'TrackSize') Or
                (PropName = 'MinTrack') Or (PropName = 'ArcApproximation') Or
                (PropName = 'NeckWidthThreshold') Or
                (PropName = 'IslandAreaThreshold') Or
                (PropName = 'PolyHatchStyle') Or (PropName = 'PourOver') Or
                (PropName = 'UseOctagons') Or (PropName = 'PourIndex') Or
                (PropName = 'Poured') Then
        Begin
            If Oid = ePolyObject Then Result := PCBGetPoly(Obj, PropName);
        End
        Else If PropName = 'StandoffHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Result := FloatToJsonStr(CoordToMilsF(Body.StandoffHeight)); End;
        End
        Else If PropName = 'OverallHeight' Then
        Begin
            If Oid = eComponentBodyObject Then
            Begin Body := Obj; Result := FloatToJsonStr(CoordToMilsF(Body.OverallHeight)); End;
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
        Else If ExtraPCBPropertyOwner(PropName) > 0 Then
            Result := PCBGetExtra(Obj, PropName)
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
        (PropName = 'XCenter') Or (PropName = 'YCenter') Or (PropName = 'Radius') Or
        (PropName = 'TopXSize') Or (PropName = 'TopYSize') Or
        (PropName = 'StandoffHeight') Or (PropName = 'OverallHeight') Or
        (PropName = 'Height') Or (PropName = 'StrokeWidth') Or
        (PropName = 'Size') Or (PropName = 'BotXSize') Or
        (PropName = 'BotYSize') Or (PropName = 'HoleWidth') Or
        (PropName = 'SizeTop') Or (PropName = 'SizeMid') Or
        (PropName = 'SizeBottom') Or (PropName = 'SolderMaskExpansion') Or
        (PropName = 'SolderMaskBottomExpansion') Or
        (PropName = 'BoundsLeft') Or (PropName = 'BoundsBottom') Or
        (PropName = 'BoundsRight') Or (PropName = 'BoundsTop') Or
        (PropName = 'Grid') Or (PropName = 'TrackSize') Or
        (PropName = 'MinTrack') Or (PropName = 'ArcApproximation') Or
        (PropName = 'NeckWidthThreshold');
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
    Arc   : IPCB_Arc;
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
            Else If Oid = eArcObject Then Begin Arc := Obj; Arc.LineWidth := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else If Oid = eTextObject Then Begin Txt := Obj; SetTextGeometry(Txt, False, MilsToCoordF(StrToFloatDef(Value, 0))); End
            Else Matched := False;
        End
        { AN ARC'S GEOMETRY. Readable from the start and not writable, so   }
        { a footprint's pin-1 dot could be read wrong and not put right.    }
        { The same members the reader uses, inside the caller's modify      }
        { bracket, on free arcs and on a placed part's arcs alike.          }
        Else If PropName = 'XCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Arc.XCenter := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'YCenter' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Arc.YCenter := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'Radius' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Arc.Radius := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'StartAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Arc.StartAngle := StrToFloatDef(Value, 0); End
            Else Matched := False;
        End
        Else If PropName = 'EndAngle' Then
        Begin
            If Oid = eArcObject Then Begin Arc := Obj; Arc.EndAngle := StrToFloatDef(Value, 0); End
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
        Else If PropName = 'BotXSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.BotXSize := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'BotYSize' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.BotYSize := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'Plated' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.Plated := StrToBool(Value); End
            Else Matched := False;
        End
        { The drill: round, square or slot, by name. Give HoleType BEFORE  }
        { HoleSize and HoleWidth in one set string, so a slot is a slot    }
        { when its length is written.                                       }
        Else If PropName = 'HoleType' Then
        Begin
            If Oid <> ePadObject Then Matched := False
            Else
            Begin
                Pad := Obj;
                If LowerCase(Value) = 'round' Then Pad.HoleType := eRoundHole
                Else If LowerCase(Value) = 'square' Then Pad.HoleType := eSquareHole
                Else If LowerCase(Value) = 'slot' Then Pad.HoleType := eSlotHole
                Else Refused := True;
            End;
        End
        Else If PropName = 'HoleWidth' Then
        Begin
            If Oid = ePadObject Then Begin Pad := Obj; Pad.HoleWidth := MilsToCoordF(StrToFloatDef(Value, 0)); End
            Else Matched := False;
        End
        Else If PropName = 'HoleRotation' Then
        Begin
            If Oid <> ePadObject Then Matched := False
            Else If Not IsFloatStr(Value) Then Refused := True
            Else Begin Pad := Obj; Pad.HoleRotation := StrToFloatDef(Value, 0); End;
        End
        Else If (PropName = 'SolderMaskExpansion') Or
                (PropName = 'SolderMaskBottomExpansion') Or
                (PropName = 'SolderMaskSeparate') Or
                (PropName = 'SolderMaskManual') Or
                (PropName = 'SolderMaskFromHoleEdge') Then
        Begin
            If (Oid <> eViaObject) And (Oid <> ePadObject) Then Matched := False
            Else If Not PCBSetMask(Obj, PropName, Value) Then Refused := True;
        End
        { Tenting is a via property and goes through the via interface, in }
        { the property form (the method form broke a unit, Library.pas).    }
        { Several Altium versions cannot set the bottom while the two mask  }
        { sides are linked: unlink first (SolderMaskSeparate=true), tent,    }
        { then relink -- the order is the caller's, in one set string.       }
        Else If PropName = 'IsTenting_Top' Then
        Begin
            If Oid = eViaObject Then Begin Via := Obj; Via.IsTenting_Top := StrToBool(Value); End
            Else Matched := False;
        End
        Else If PropName = 'IsTenting_Bottom' Then
        Begin
            If Oid = eViaObject Then Begin Via := Obj; Via.IsTenting_Bottom := StrToBool(Value); End
            Else Matched := False;
        End
        Else If (PropName = 'Mode') Or (PropName = 'SizeTop') Or
                (PropName = 'SizeMid') Or (PropName = 'SizeBottom') Then
        Begin
            PosVal := PCBSetStack(Obj, PropName, Value);
            If PosVal = 0 Then Matched := False
            Else If PosVal < 0 Then Refused := True;
        End
        { A union ties objects so they move together: the 3D envelope to its }
        { component. On a component it is set on the component and every one }
        { of its parts; 0 breaks it.                                         }
        Else If PropName = 'UnionIndex' Then
        Begin
            If (Not IsFloatStr(Value)) Or (StrToFloatDef(Value, -1) < 0) Then
                Refused := True
            Else PCBSetUnion(Obj, Round(StrToFloatDef(Value, 0)));
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
        { A polygon's Name, as the Polygon Pour dialog shows it (issue     }
        { #125). An empty one is refused.                                   }
        Else If (PropName = 'Name') And (Oid = ePolyObject) Then
        Begin
            If Trim(Value) = '' Then Refused := True
            Else If Not PCBSetPoly(Obj, PropName, Value) Then Matched := False;
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
        { A primitive's net (issue #126): '' leaves it on no net, a name     }
        { joins that existing net, an unknown name is refused, never         }
        { ignored. Exploding a polygon leaves its net on every region it     }
        { made; this is how a caller takes it off again. Only copper that    }
        { carries a net takes it; every other type is not matched.           }
        Else If (PropName = 'Net') Or (PropName = 'Net.Name') Then
        Begin
            If (Oid = eTrackObject) Or (Oid = eArcObject) Or (Oid = eViaObject) Or
               (Oid = ePadObject) Or (Oid = eFillObject) Or (Oid = eRegionObject) Or
               (Oid = ePolyObject) Then
            Begin
                If Not PCBSetNet(Obj, Value) Then Refused := True;
            End
            Else Matched := False;
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
        { A keepout and what it keeps out, on the four objects Altium makes   }
        { keepouts of. IsKeepout turns a placed track, arc, fill or region    }
        { into one; the five restrictions follow the Properties panel.       }
        Else If (PropName = 'IsKeepout') Or (PropName = 'IsKeepoutForCopper') Or
                (PropName = 'IsKeepoutForTrack') Or (PropName = 'IsKeepoutForVia') Or
                (PropName = 'IsKeepoutForSMDPad') Or (PropName = 'IsKeepoutForTHPad') Then
        Begin
            If (Oid = eTrackObject) Or (Oid = eArcObject) Or (Oid = eFillObject) Or
               (Oid = eRegionObject) Then
            Begin
                If PropName = 'IsKeepout' Then Obj.IsKeepout := StrToBool(Value);
                If PropName = 'IsKeepoutForCopper' Then Obj.IsKeepoutForCopper := StrToBool(Value);
                If PropName = 'IsKeepoutForTrack' Then Obj.IsKeepoutForTrack := StrToBool(Value);
                If PropName = 'IsKeepoutForVia' Then Obj.IsKeepoutForVia := StrToBool(Value);
                If PropName = 'IsKeepoutForSMDPad' Then Obj.IsKeepoutForSMDPad := StrToBool(Value);
                If PropName = 'IsKeepoutForTHPad' Then Obj.IsKeepoutForTHPad := StrToBool(Value);
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
        { The rest of the Polygon Pour dialog (issue #125), see PCBSetPoly. }
        { PourIndex and Poured are read only and deliberately absent.       }
        Else If (PropName = 'Grid') Or (PropName = 'TrackSize') Or
                (PropName = 'MinTrack') Or (PropName = 'ArcApproximation') Or
                (PropName = 'NeckWidthThreshold') Or
                (PropName = 'IslandAreaThreshold') Or
                (PropName = 'PolyHatchStyle') Or (PropName = 'PourOver') Or
                (PropName = 'UseOctagons') Then
        Begin
            If Oid <> ePolyObject Then Matched := False
            Else If Not PCBSetPoly(Obj, PropName, Value) Then Refused := True;
        End
        Else If ExtraPCBPropertyOwner(PropName) > 0 Then
        Begin
            PosVal := PCBSetExtra(Obj, PropName, Value);
            If PosVal = 0 Then Matched := False
            Else If PosVal < 0 Then Refused := True;
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
    Remaining, Condition, PropName, Expected, Actual, Op : String;
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
        EqPos := FilterOpPos(Condition);
        Op := FilterOpText(Condition);
        If (EqPos < 2) Or (Op = '') Then Begin Result := False; Exit; End;
        PropName := Copy(Condition, 1, EqPos - 1);
        Expected := Copy(Condition, EqPos + Length(Op), Length(Condition));
        Actual := GetPCBProperty(Obj, PropName);
        If Not FilterOpHolds(Op, Actual, Expected,
                FilterValueMatchesPCB(PropName, Actual, Expected)) Then
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
        (PropName = 'SourceUniqueId') Or (PropName = 'SourceHierarchicalPath') Or
        (PropName = 'SourceLibReference') Or (PropName = 'SourceFootprintLibrary') Or
        (PropName = 'SourceComponentLibrary') Or
        (PropName = 'Kind') Or (PropName = 'RegionKind') Or
        (PropName = 'RemoveDead') Or (PropName = 'RemoveNarrowNecks') Or
        (PropName = 'RemoveIslandsByArea') Or
        (PropName = 'StandoffHeight') Or (PropName = 'OverallHeight') Or
        (PropName = 'BodyOpacity3D') Or
        (PropName = 'InComponent') Or (PropName = 'Component') Or
        (PropName = 'InPolygon') Or (PropName = 'IsKeepout') Or
        (PropName = 'IsKeepoutForCopper') Or (PropName = 'IsKeepoutForTrack') Or
        (PropName = 'IsKeepoutForVia') Or (PropName = 'IsKeepoutForSMDPad') Or
        (PropName = 'IsKeepoutForTHPad') Or
        (PropName = 'TearDrop') Or
        (PropName = 'Locked') Or (PropName = 'Layer1') Or
        (PropName = 'Layer2') Or (PropName = 'IsRedundant') Or
        (PropName = 'Mode') Or (PropName = 'Height') Or
        (PropName = 'StrokeWidth') Or (PropName = 'IsDesignator') Or
        (PropName = 'IsComment') Or (PropName = 'UseTTFonts') Or
        (PropName = 'MirrorFlag') Or
        (PropName = 'BotXSize') Or (PropName = 'BotYSize') Or
        (PropName = 'TopShapeName') Or
        (PropName = 'Plated') Or (PropName = 'HoleType') Or
        (PropName = 'HoleWidth') Or (PropName = 'HoleRotation') Or
        (PropName = 'SizeTop') Or (PropName = 'SizeMid') Or
        (PropName = 'SizeBottom') Or
        (PropName = 'SolderMaskExpansion') Or
        (PropName = 'SolderMaskBottomExpansion') Or
        (PropName = 'SolderMaskSeparate') Or (PropName = 'SolderMaskManual') Or
        (PropName = 'SolderMaskFromHoleEdge') Or
        (PropName = 'IsTenting_Top') Or (PropName = 'IsTenting_Bottom') Or
        (PropName = 'IsTestpoint_Top') Or (PropName = 'IsTestpoint_Bottom') Or
        (PropName = 'IsAssyTestpoint_Top') Or
        (PropName = 'IsAssyTestpoint_Bottom') Or
        (PropName = 'BoundsLeft') Or (PropName = 'BoundsBottom') Or
        (PropName = 'BoundsRight') Or (PropName = 'BoundsTop') Or
        (PropName = 'UnionIndex') Or (PropName = 'Identifier') Or
        (PropName = 'Grid') Or (PropName = 'TrackSize') Or
        (PropName = 'MinTrack') Or (PropName = 'ArcApproximation') Or
        (PropName = 'NeckWidthThreshold') Or
        (PropName = 'IslandAreaThreshold') Or
        (PropName = 'PolyHatchStyle') Or (PropName = 'PourOver') Or
        (PropName = 'UseOctagons') Or (PropName = 'PourIndex') Or
        (PropName = 'Poured') Or
        (ExtraPCBPropertyOwner(PropName) > 0);
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
        + 'SourceUniqueId, SourceHierarchicalPath, SourceLibReference, '
        + 'SourceFootprintLibrary, SourceComponentLibrary, '
        { Everything the reader above answers. A list that lags the reader
          tells a caller a property does not exist when it does, which is
          how the pour flags were reported as unreachable. }
        + 'Kind, RemoveDead, RemoveNarrowNecks, RemoveIslandsByArea, '
        + 'StandoffHeight, OverallHeight, BodyOpacity3D, InComponent, Component, '
        + 'InPolygon, IsKeepout, IsKeepoutForCopper, IsKeepoutForTrack, '
        + 'IsKeepoutForVia, IsKeepoutForSMDPad, IsKeepoutForTHPad, '
        + 'TearDrop, Locked, Layer1, Layer2, IsRedundant, Mode, '
        + 'Height, StrokeWidth, IsDesignator, IsComment, UseTTFonts, MirrorFlag'
        + ', TopShapeName, BotXSize, BotYSize, Plated, HoleType, HoleWidth, '
        + 'HoleRotation, '
        + 'SizeTop, SizeMid, SizeBottom, SolderMaskExpansion, '
        + 'SolderMaskBottomExpansion, SolderMaskSeparate, SolderMaskManual, '
        + 'SolderMaskFromHoleEdge, IsTenting_Top, IsTenting_Bottom, '
        + 'IsTestpoint_Top, IsTestpoint_Bottom, IsAssyTestpoint_Top, '
        + 'IsAssyTestpoint_Bottom, BoundsLeft, BoundsBottom, BoundsRight, '
        + 'BoundsTop, UnionIndex, Identifier, Grid, TrackSize, MinTrack, '
        + 'ArcApproximation, NeckWidthThreshold, IslandAreaThreshold, '
        + 'PolyHatchStyle, PourOver, UseOctagons, PourIndex, Poured, '
        + 'HolePositiveTolerance, HoleNegativeTolerance, IsCounterHole, '
        + 'BarCodeKind, BarCodeRenderMode, BarCodeFullWidth, BarCodeFullHeight, '
        + 'BarCodeXMargin, BarCodeYMargin, BarCodeMinWidth, BarCodeInverted, '
        + 'BarCodeShowText, BarCodeFontName, Multiline, MultilineTextWidth, '
        + 'MultilineTextHeight, MultilineTextAutoPosition, TextHeight, TextWidth, '
        + 'TextLineWidth, TextGap, TextPosition, TextDimensionUnit, TextPrecision, '
        + 'TextFormat, TextPrefix, TextSuffix, ArrowSize, ArrowLineWidth, '
        + 'ArrowLength, ArrowPosition, ExtensionOffset, ExtensionLineWidth, '
        + 'ExtensionPickGap, PolygonType, BorderWidth, AvoidObsticles, '
        + 'ClipAcuteCorners, MitreCorners, IgnoreViolations, ArcPourMode, '
        + 'EnablePinSwapping, EnablePartSwapping, IsBGA, LockStrings, '
        + 'NameAutoPosition, CommentAutoPosition, FootprintDescription, '
        + 'DefaultPCB3DModel, PackageSpecificHash, JumperID, SwapID_Pad, SwapID_Part, '
        + 'Inverted, UseInvertedRectangle, InvertedTTTextBorder, '
        + 'TTFInvertedTextJustify, TTFOffsetFromInvertedRect, ConvertedString, FontName, '
        + 'TextKind, FontID, Bold, Italic, InvRectWidth, InvRectHeight, '
        + 'DrawRemovedNecks, DrawRemovedIslands, DrawDeadCopper, ExpandOutline, '
        + 'AutoGenerateName, SourceDescription, JumpersVisible, ChannelOffset, '
        + 'GroupNum, SwappedPadName, OwnerPart_ID, OverrideColor, Texture, '
        + 'TextureRotation, IsPreRoute, UserRouted, PasteMaskExpansionMode, '
        + 'SolderMaskExpansionMode, PasteMaskUsePercent, PasteMaskPercent, '
        + 'PasteMaskEnabled, DrillType, IsVirtualPin, FlippedOnLayer, '
        + 'BorderSpaceType';
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
        { A lone SolderMaskExpansion is for both sides (issue #61): it is  }
        { the top value once the sides are separate, so on such a via or   }
        { pad the old bottom would stay behind. As set_pad_properties does, }
        { the bottom follows unless the same set string gives it a value.  }
        If (PropName = 'SolderMaskExpansion') And IsFloatStr(PropValue)
            And (Pos('SolderMaskBottomExpansion=', SetStr) = 0) Then
        Begin
            Try PCBSetMask(Obj, 'SolderMaskBottomExpansion', PropValue); Except End;
        End;
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
        PcbBeginEdit(0);
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
            PcbEndEdit(0);
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
        PcbBeginEdit(0);
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
            PcbEndEdit(0);
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
        EqPos := FilterOpPos(Condition);
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
