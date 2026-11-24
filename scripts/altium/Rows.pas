{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>       }
{ Copyright (c) 2026 Vincent Himpe <vincent.himpe@gmail.com>            }
{..............................................................................}
{ Rows.pas - the rows.* commands (docs/ROWS_CONTRACT.md): which kinds exist,   }
{ and the routing of a read or a write to its kind. Compiles after every      }
{ kind's unit. A new kind adds one line to each Case and to RowsKindsJson.    }
{..............................................................................}

{ One kinds entry: doc, kind, the container guard every written row needs,  }
{ the scope parameters that narrow its walk, the default unit, the fields.  }
Function RowsKindJson(Doc : String; Kind : String; Guard : String; Scope : String;
    Spec : String) : String;
Begin
    Result := '{"doc":"' + Doc + '","kind":"' + Kind + '"'
        + ',"container_guard":"' + Guard + '"'
        + ',"scope":[' + Scope + ']'
        + ',"units":"coord"'
        + ',"fields":' + RowsSpecJson(Spec) + '}';
End;

{ Every (doc, kind) built, with its field table. }
Function RowsKindsJson(Dummy : Integer) : String;
Var
    LibScope, BoardScope, CopperScope : String;
Begin
    LibScope := '"footprint","layer"';
    BoardScope := '"net","layer","designator","pattern","owner","bounds"';
    CopperScope := BoardScope + ',"poured"';
    Result := '{"kinds":['
        + RowsKindJson('pcblib', 'track', 'footprint', LibScope, RowsPcbLibTrackSpec(0))
        + ',' + RowsKindJson('pcblib', 'arc', 'footprint', LibScope, RowsPcbLibArcSpec(0))
        + ',' + RowsKindJson('pcblib', 'text', 'footprint', LibScope, RowsPcbLibTextSpec(0))
        + ',' + RowsKindJson('pcblib', 'via', 'footprint', LibScope, RowsPcbLibViaSpec(0))
        + ',' + RowsKindJson('pcblib', 'pad', 'footprint', LibScope, RowsPcbLibPadSpec(0))
        + ',' + RowsKindJson('pcb', 'track', '', CopperScope, RowsPcbTrackSpec(0))
        + ',' + RowsKindJson('pcb', 'arc', '', CopperScope, RowsPcbArcSpec(0))
        + ',' + RowsKindJson('pcb', 'via', '', BoardScope, RowsPcbViaSpec(0))
        + ',' + RowsKindJson('pcb', 'pad', '', BoardScope, RowsPcbPadSpec(0))
        + ',' + RowsKindJson('schlib', 'pin', 'symbol', '"symbol"', RowsSchLibPinSpec(0))
        + ',' + RowsKindJson('schlib', 'line', 'symbol', '"symbol"', RowsSchLibLineSpec(0))
        + ',' + RowsKindJson('schlib', 'polyline', 'symbol', '"symbol"', RowsSchLibPolylineSpec(0))
        + ',' + RowsKindJson('schlib', 'arc', 'symbol', '"symbol"', RowsSchLibArcSpec(0))
        + ',' + RowsKindJson('sch', 'line', '', '"document_path"', RowsSchLineSpec(0))
        + ',' + RowsKindJson('sch', 'polyline', '', '"document_path"', RowsSchPolylineSpec(0))
        + ',' + RowsKindJson('sch', 'arc', '', '"document_path"', RowsSchArcSpec(0))
        + ']}';
End;

{ The refusal for a doc / kind pair that is not built. }
Function RowsBadKind(RequestId : String; Doc : String; Kind : String) : String;
Begin
    Result := BuildErrorResponse(RequestId, 'BAD_KIND',
        'No rows kind "' + Kind + '" for doc "' + Doc + '". Built: pcblib/track, '
        + 'pcblib/arc, pcblib/text, pcblib/via, pcblib/pad, pcb/track, pcb/arc, '
        + 'pcb/via, pcb/pad, schlib/pin, schlib/line, schlib/polyline, schlib/arc, '
        + 'sch/line, sch/polyline, sch/arc. rows.list_kinds lists them with their fields.');
End;

Function HandleRowsCommand(Action : String; Params : String; RequestId : String) : String;
Var
    Doc, Kind, Key, Undo : String;
Begin
    RowsProfStart(Params);
    If Action = 'list_kinds' Then
    Begin
        Result := BuildSuccessResponse(RequestId, RowsKindsJson(0));
        Exit;
    End;
    Doc := LowerCase(Trim(ExtractJsonValue(Params, 'doc')));
    Kind := LowerCase(Trim(ExtractJsonValue(Params, 'kind')));
    If (Doc = '') Or (Kind = '') Then
    Begin
        Result := BuildErrorResponse(RequestId, 'MISSING_PARAMS',
            'doc (pcblib, pcb, schlib, sch) and kind are required');
        Exit;
    End;
    Key := Doc + '/' + Kind;
    If Action = 'read_rows' Then
    Begin
        Case Key Of
            'pcblib/track': Result := RowsPcbLibTrackRead(Params, RequestId);
            'pcblib/arc':   Result := RowsPcbLibArcRead(Params, RequestId);
            'pcblib/text':  Result := RowsPcbLibTextRead(Params, RequestId);
            'pcblib/via':   Result := RowsPcbLibViaRead(Params, RequestId);
            'pcblib/pad':   Result := RowsPcbLibPadRead(Params, RequestId);
            'pcb/track':    Result := RowsPcbTrackRead(Params, RequestId);
            'pcb/arc':      Result := RowsPcbArcRead(Params, RequestId);
            'pcb/via':      Result := RowsPcbViaRead(Params, RequestId);
            'pcb/pad':      Result := RowsPcbPadRead(Params, RequestId);
            'schlib/pin':   Result := RowsSchLibPinRead(Params, RequestId);
            'schlib/line', 'schlib/polyline', 'schlib/arc', 'sch/line', 'sch/polyline',
            'sch/arc':      Result := RowsSGRead(Doc, Kind, Params, RequestId);
        Else
            Result := RowsBadKind(RequestId, Doc, Kind);
        End;
    End
    Else If Action = 'write_rows' Then
    Begin
        Undo := LowerCase(Trim(ExtractJsonValue(Params, 'undo')));
        If (Undo <> '') And (Undo <> 'true') And (Undo <> 'false') Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
                'undo must be true or false, not ' + Undo + '; nothing was changed');
            Exit;
        End;
        If (Undo = 'false') And (Key <> 'pcblib/pad') And (Key <> 'pcblib/via') Then
        Begin
            Result := BuildErrorResponse(RequestId, 'BAD_PARAM',
                'undo=false is for pcblib pad and via writes only, not ' + Key
                + '; nothing was changed');
            Exit;
        End;
        Case Key Of
            'pcblib/track': Result := RowsPcbLibTrackWrite(Params, RequestId);
            'pcblib/arc':   Result := RowsPcbLibArcWrite(Params, RequestId);
            'pcblib/text':  Result := RowsPcbLibTextWrite(Params, RequestId);
            'pcblib/via':   Result := RowsPcbLibViaWrite(Params, RequestId);
            'pcblib/pad':   Result := RowsPcbLibPadWrite(Params, RequestId);
            'pcb/track':    Result := RowsPcbTrackWrite(Params, RequestId);
            'pcb/arc':      Result := RowsPcbArcWrite(Params, RequestId);
            'pcb/via':      Result := RowsPcbViaWrite(Params, RequestId);
            'pcb/pad':      Result := RowsPcbPadWrite(Params, RequestId);
            'schlib/pin':   Result := RowsSchLibPinWrite(Params, RequestId);
            'schlib/line', 'schlib/polyline', 'schlib/arc', 'sch/line', 'sch/polyline',
            'sch/arc':      Result := RowsSGWrite(Doc, Kind, Params, RequestId);
        Else
            Result := RowsBadKind(RequestId, Doc, Kind);
        End;
    End
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_ACTION',
            'Unknown rows action: ' + Action + '. Use rows.list_kinds, rows.read_rows or rows.write_rows.');
End;

{ UnitStamp_Rows - which build of Rows.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_Rows(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
