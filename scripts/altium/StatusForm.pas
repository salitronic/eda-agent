{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ StatusForm.pas - DFM-backed MCP dashboard with per-command performance stats. }
{..............................................................................}

Const
    PERF_TABLE_SIZE = 64;

Var
    HidePingsFlag : Boolean;
    OnlySlowFlag  : Boolean;
    PerfNames  : Array[0..63] Of String;
    PerfCounts : Array[0..63] Of Integer;
    PerfTotal  : Array[0..63] Of Cardinal;
    PerfMax    : Array[0..63] Of Cardinal;
    PerfCount  : Integer;

Function ShouldShowCommand(Command : String; DurationMs : Cardinal; IsError : Boolean) : Boolean;
Begin
    Result := True;
    If IsError Then Exit;
    If HidePingsFlag And (Command = 'application.ping') Then
    Begin
        Result := False;
        Exit;
    End;
    If OnlySlowFlag And (DurationMs < 100) Then
    Begin
        Result := False;
        Exit;
    End;
End;

Function PadLeft(S : String; Width : Integer) : String;
Begin
    Result := S;
    While Length(Result) < Width Do Result := ' ' + Result;
End;

Function PadRight(S : String; Width : Integer) : String;
Begin
    Result := S;
    While Length(Result) < Width Do Result := Result + ' ';
End;

Function FindOrAddPerf(Command : String) : Integer;
Var
    I : Integer;
Begin
    For I := 0 To PerfCount - 1 Do
    Begin
        If PerfNames[I] = Command Then
        Begin
            Result := I;
            Exit;
        End;
    End;
    If PerfCount < PERF_TABLE_SIZE Then
    Begin
        PerfNames[PerfCount] := Command;
        PerfCounts[PerfCount] := 0;
        PerfTotal[PerfCount] := 0;
        PerfMax[PerfCount] := 0;
        Result := PerfCount;
        Inc(PerfCount);
    End
    Else
        Result := -1;
End;

Procedure ResetPerfStats;
Var I : Integer;
Begin
    For I := 0 To PERF_TABLE_SIZE - 1 Do
    Begin
        PerfNames[I] := '';
        PerfCounts[I] := 0;
        PerfTotal[I] := 0;
        PerfMax[I] := 0;
    End;
    PerfCount := 0;
End;

Procedure TrackPerf(Command : String; DurationMs : Cardinal);
Var
    Idx : Integer;
Begin
    Idx := FindOrAddPerf(Command);
    If Idx < 0 Then Exit;
    PerfCounts[Idx] := PerfCounts[Idx] + 1;
    PerfTotal[Idx] := PerfTotal[Idx] + DurationMs;
    If DurationMs > PerfMax[Idx] Then PerfMax[Idx] := DurationMs;
End;

{ Rebuild the Perf tab's memo with a table of per-command stats, sorted by   }
{ max duration descending.                                                    }
Procedure RefreshPerfPanel;
Var
    OrderIdx : Array[0..63] Of Integer;
    I, J, Tmp : Integer;
    Line : String;
    AvgMs : Cardinal;
Begin
    If PerfCount = 0 Then Exit;

    For I := 0 To PerfCount - 1 Do OrderIdx[I] := I;

    For I := 0 To PerfCount - 2 Do
        For J := I + 1 To PerfCount - 1 Do
            If PerfMax[OrderIdx[J]] > PerfMax[OrderIdx[I]] Then
            Begin
                Tmp := OrderIdx[I];
                OrderIdx[I] := OrderIdx[J];
                OrderIdx[J] := Tmp;
            End;

    Try
        mmo_Perf.Lines.Clear;
        mmo_Perf.Lines.Add(PadRight('command', 22) + PadLeft('N', 5)
            + PadLeft('avg', 7) + PadLeft('max', 7));
        mmo_Perf.Lines.Add('----------------------------------------');
        For I := 0 To PerfCount - 1 Do
        Begin
            J := OrderIdx[I];
            If PerfCounts[J] = 0 Then AvgMs := 0
            Else AvgMs := PerfTotal[J] Div PerfCounts[J];
            Line := PadRight(PerfNames[J], 22)
                  + PadLeft(IntToStr(PerfCounts[J]), 5)
                  + PadLeft(IntToStr(AvgMs), 7)
                  + PadLeft(IntToStr(PerfMax[J]), 7);
            mmo_Perf.Lines.Add(Line);
        End;
    Except End;
End;

Procedure AppendLogLine(Command : String; DurationMs : Cardinal; IsError : Boolean);
Var
    Tag, Line : String;
Begin
    TrackPerf(Command, DurationMs);
    If Not ShouldShowCommand(Command, DurationMs, IsError) Then Exit;
    If IsError Then Tag := 'ERR '
    Else If DurationMs >= 500 Then Tag := 'SLOW'
    Else If DurationMs >= 100 Then Tag := 'WARN'
    Else Tag := 'OK  ';
    Line := '[' + Tag + '] ' + PadLeft(IntToStr(DurationMs), 5) + ' ms  ' + Command;
    Try
        mmo_Log.Lines.Insert(0, Line);
        While mmo_Log.Lines.Count > 500 Do
            mmo_Log.Lines.Delete(mmo_Log.Lines.Count - 1);
    Except End;
    If IsError Then
        Try lbl_LastErr.Caption := 'last error: ' + Command; Except End;
End;

Procedure UpdateStatusHeader(StatusStr : String);
Begin
    Try lbl_Status.Caption := StatusStr; Except End;
End;

Procedure UpdateStatsLine(UptimeSec, Requests : Integer; AltiumMs : Cardinal;
                          IdleSecToShutdown : Integer);
Begin
    Try lbl_ValUp.Caption    := IntToStr(UptimeSec) + 's'; Except End;
    Try lbl_ValReq.Caption   := IntToStr(Requests); Except End;
    Try lbl_ValMs.Caption    := IntToStr(AltiumMs); Except End;
    Try lbl_ValStop.Caption  := IntToStr(IdleSecToShutdown) + 's'; Except End;
End;

Procedure ShowStatusForm;
Begin
    Try
        { Initial flags derived from initial checkbox captions in the DFM —     }
        { "[x]" means on, "[ ]" means off.                                       }
        HidePingsFlag := Pos('[x]', chk_HidePings.Caption) > 0;
        OnlySlowFlag  := Pos('[x]', chk_OnlySlow.Caption) > 0;
        ResetPerfStats;
        If Not StatusForm.Visible Then StatusForm.Show;
        Try StatusForm.Caption := 'EDA Agent MCP - v' + SCRIPT_VERSION; Except End;
        Try lbl_Version.Caption := 'v' + SCRIPT_VERSION; Except End;
    Except End;
End;

Procedure HideStatusForm;
Begin
    Try
        If StatusForm.Visible Then StatusForm.Hide;
    Except End;
End;

Procedure StatusFormClose(Sender : TObject; Var Action : TCloseAction);
Begin
    Try Running := False; Except End;
End;

Procedure btn_DetachClick(Sender : TObject);
Begin
    Try Running := False; Except End;
End;

Procedure btn_ClearLogClick(Sender : TObject);
Begin
    Try mmo_Log.Lines.Clear; Except End;
    Try lbl_LastErr.Caption := ''; Except End;
End;

Procedure btn_ResetPerfClick(Sender : TObject);
Begin
    ResetPerfStats;
    Try mmo_Perf.Lines.Clear; Except End;
End;

Procedure chk_HidePingsClick(Sender : TObject);
Begin
    Try
        HidePingsFlag := Not HidePingsFlag;
        If HidePingsFlag Then chk_HidePings.Caption := '  [x] Hide pings'
        Else chk_HidePings.Caption := '  [ ] Hide pings';
    Except End;
End;

Procedure chk_OnlySlowClick(Sender : TObject);
Begin
    Try
        OnlySlowFlag := Not OnlySlowFlag;
        If OnlySlowFlag Then chk_OnlySlow.Caption := '  [x] Only >100ms'
        Else chk_OnlySlow.Caption := '  [ ] Only >100ms';
    Except End;
End;

{ Hover handlers — brighten the panel while the mouse is over it, revert    }
{ to the resting colour on leave. Tab handlers preserve the active/inactive }
{ distinction so leaving a tab returns to the correct resting colour.        }

Procedure btn_DetachEnter(Sender : TObject);
Begin Try btn_Detach.Color := $00505050; Except End; End;
Procedure btn_DetachLeave(Sender : TObject);
Begin Try btn_Detach.Color := $003A3A3A; Except End; End;

Procedure btn_ClearLogEnter(Sender : TObject);
Begin Try btn_ClearLog.Color := $00505050; Except End; End;
Procedure btn_ClearLogLeave(Sender : TObject);
Begin Try btn_ClearLog.Color := $003A3A3A; Except End; End;

Procedure btn_ResetPerfEnter(Sender : TObject);
Begin Try btn_ResetPerf.Color := $00505050; Except End; End;
Procedure btn_ResetPerfLeave(Sender : TObject);
Begin Try btn_ResetPerf.Color := $003A3A3A; Except End; End;

Procedure chk_HidePingsEnter(Sender : TObject);
Begin Try chk_HidePings.Color := $002E2E2E; Except End; End;
Procedure chk_HidePingsLeave(Sender : TObject);
Begin Try chk_HidePings.Color := $001E1E1E; Except End; End;

Procedure chk_OnlySlowEnter(Sender : TObject);
Begin Try chk_OnlySlow.Color := $002E2E2E; Except End; End;
Procedure chk_OnlySlowLeave(Sender : TObject);
Begin Try chk_OnlySlow.Color := $001E1E1E; Except End; End;

Procedure tab_LogEnter(Sender : TObject);
Begin
    Try
        If mmo_Log.Visible Then tab_Log.Color := $00252525
        Else tab_Log.Color := $00353535;
    Except End;
End;
Procedure tab_LogLeave(Sender : TObject);
Begin
    Try
        If mmo_Log.Visible Then tab_Log.Color := $001A1A1A
        Else tab_Log.Color := $00252526;
    Except End;
End;

Procedure tab_PerfEnter(Sender : TObject);
Begin
    Try
        If mmo_Perf.Visible Then tab_Perf.Color := $00252525
        Else tab_Perf.Color := $00353535;
    Except End;
End;
Procedure tab_PerfLeave(Sender : TObject);
Begin
    Try
        If mmo_Perf.Visible Then tab_Perf.Color := $001A1A1A
        Else tab_Perf.Color := $00252526;
    Except End;
End;

{ Manual tab handlers: clicking Log or Perf swaps which TMemo is visible and }
{ updates the tab colours to show active state.                              }
Procedure tab_LogClick(Sender : TObject);
Begin
    Try
        mmo_Log.Visible := True;
        mmo_Perf.Visible := False;
        tab_Log.Color := $001A1A1A;
        tab_Log.Font.Color := $00F0D090;
        tab_Perf.Color := $00252526;
        tab_Perf.Font.Color := $00909090;
    Except End;
End;

Procedure tab_PerfClick(Sender : TObject);
Begin
    Try
        mmo_Log.Visible := False;
        mmo_Perf.Visible := True;
        tab_Perf.Color := $001A1A1A;
        tab_Perf.Font.Color := $00F0D090;
        tab_Log.Color := $00252526;
        tab_Log.Font.Color := $00909090;
    Except End;
End;
