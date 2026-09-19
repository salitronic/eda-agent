{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ StatusForm.pas - DFM-backed MCP dashboard.                                  }
{                                                                              }
{ Modern dark UI with:                                                        }
{   - Status pill (dot color + label + spinner glyph during in-flight calls)  }
{   - Four KPI cards (uptime, requests, busy time, idle timeout)              }
{   - Inline last-error display                                                }
{   - Prominent "Open Dashboard" button (writes a sentinel the Python bridge  }
{     watches and opens the user's default browser at the web dashboard)      }
{   - Pause / Cancel-current / Renew / Clear / Detach action row              }
{   - Filter box + Hide-pings / Only->100ms / Always-on-top toggles           }
{   - Log tab with newest-on-top entries, request-ID column, inline error    }
{     detail rows, free-text filter                                            }
{   - Perf tab with per-command stats, sorted by max duration desc            }
{                                                                              }
{ State lives in module-level TStringLists, function locals avoid fixed-size  }
{ arrays per [[delphiscript_fixed_string_array_bug]]. Module-level fixed-size }
{ arrays are still safe (no function-return slot to clobber) but we use       }
{ TStringLists for the perf table so the cap can grow past 64 cleanly.        }
{..............................................................................}

Var
    HidePingsFlag    : Boolean;
    OnlySlowFlag     : Boolean;
    AlwaysOnTopFlag  : Boolean;

    { Pause: when True, Dispatcher's poll loop skips ScanForRequestFile.    }
    PausedFlag       : Boolean;

    { Renew: set by the Renew button, consumed once by the Dispatcher to    }
    { reset its real idle deadline (LastActivityMs). The form's             }
    { LastActivityTick alone only moves the visible countdown, not the      }
    { actual auto-shutdown timer that lives local to StartMCPServer.        }
    RenewRequested   : Boolean;

    { Spinner state. SpinnerFrame indexes into a 4-phase moon glyph cycle. }
    SpinnerFrame     : Integer;
    InFlightCommand  : String;
    InFlightStartMs  : Cardinal;
    InFlightActive   : Boolean;

    { Free-text filter applied at WRITE time to mmo_Log.Lines. Empty = no   }
    { text filter. Changing the filter only affects new entries; old ones   }
    { stay until you click Clear log. (We used to keep a TStringList buffer }
    { and re-render on filter change, but DelphiScript's TStringList API is }
    { broken in too many spots, .Insert and .Clear undeclared, typed-param   }
    { dispatch loses methods, empty-literal arguments trip the parser; the  }
    { committed pattern wrote straight to mmo_Log.Lines and we're back to    }
    { that.)                                                                 }
    FilterText       : String;

    { Perf table - parallel TStringLists. Lists grow with the command set. }
    PerfNames        : TStringList;
    PerfCountStrs    : TStringList;
    PerfTotalStrs    : TStringList;
    PerfMaxStrs      : TStringList;

    { Idle-timeout state (mirrored from Dispatcher for the Renew button). }
    LastActivityTick : Cardinal;

    { MCP liveness: GetTickCount at the most-recently-processed command.   }
    { Open Dashboard button only enables when this is within the last 60s. }
    LastPingMs       : Cardinal;
    OpenWebEnabled   : Boolean;


{ Static UI constants. Modern dark palette intended for the StatusForm.   }
{ DelphiScript color literals are BGR-ordered Cardinals (8 hex digits      }
{ after the $); a malformed 7-digit literal bombs the parser without an   }
{ obvious error location, so always double-count.                          }
Const
    COLOR_BG_BASE       = $001A1B1E;
    COLOR_BG_CARD       = $0025262B;
    COLOR_BG_ELEVATED   = $002E2F35;
    COLOR_ACCENT_BLUE   = $00FF9E4A;   { #4A9EFF in BGR }
    COLOR_ACCENT_AMBER  = $00589EE0;   { #E09E58 in BGR }
    COLOR_ACCENT_RED    = $005C5CFF;   { #FF5C5C in BGR }
    COLOR_ACCENT_GREEN  = $006BC464;   { #64C46B in BGR }
    COLOR_TEXT_BODY     = $00E1E2E6;
    COLOR_TEXT_MUTED    = $008B8E96;
    COLOR_TEXT_FAINT    = $00696C73;

    MAX_LOG_LINES       = 2000;
    SPINNER_FRAMES      = 4;


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


Procedure EnsureStatusBuffers(Dummy : Integer);
Begin
    If PerfNames     = Nil Then PerfNames     := TStringList.Create;
    If PerfCountStrs = Nil Then PerfCountStrs := TStringList.Create;
    If PerfTotalStrs = Nil Then PerfTotalStrs := TStringList.Create;
    If PerfMaxStrs   = Nil Then PerfMaxStrs   := TStringList.Create;
End;


{ Spinner glyph cycle. Classic terminal-spinner ASCII for max compatibility   }
{ with DelphiScript's TMemo / TLabel rendering, which doesn't reliably show   }
{ 3-byte UTF-8 sequences without a font fallback.                             }
Function SpinnerGlyph(Frame : Integer) : String;
Begin
    Case (Frame Mod SPINNER_FRAMES) Of
        0: Result := '|';
        1: Result := '/';
        2: Result := '-';
    Else
        Result := '\';
    End;
End;


{ Hidden from the Run Script dialog by its argument; Dummy is never
  read. See KnownPCBPropertyList in PCBGeneric for why. }
Function FilledDot(Dummy : Integer) : String;
Begin
    Result := '[x]';
End;

{ Hidden from the Run Script dialog by its argument; Dummy is never read. }
Function HollowDot(Dummy : Integer) : String;
Begin
    Result := '[ ]';
End;


Function ShouldShowLine(LogLine : String) : Boolean;
Var
    UpperLine, UpperFilt : String;
Begin
    Result := True;
    If HidePingsFlag And (Pos('application.ping', LogLine) > 0) Then
    Begin
        Result := False;
        Exit;
    End;
    If FilterText <> '' Then
    Begin
        UpperLine := UpperCase(LogLine);
        UpperFilt := UpperCase(FilterText);
        If Pos(UpperFilt, UpperLine) = 0 Then
        Begin
            Result := False;
            Exit;
        End;
    End;
End;


{ Used to re-render mmo_Log from a TStringList buffer when filter flags     }
{ changed. The buffer was a DelphiScript minefield, so we dropped it; this   }
{ procedure now just clears the visible memo. Callers that previously       }
{ wanted "apply new filter to old entries" now just see a clean slate after }
{ toggling filter chips, which is honest and side-steps the buggy API.      }
Procedure RebuildVisibleLog(Dummy : Integer);
Begin
    Try
        mmo_Log.Lines.BeginUpdate;
        Try
            mmo_Log.Lines.Clear;
        Finally
            mmo_Log.Lines.EndUpdate;
        End;
    Except End;
End;


{ Perf-table helpers. Lists are parallel: PerfNames[i] <-> the i-th         }
{ count/total/max stringified ints. Names are case-sensitive command IDs.    }
Function FindOrAddPerf(Command : String) : Integer;
Begin
    EnsureStatusBuffers(0);
    Result := PerfNames.IndexOf(Command);
    If Result >= 0 Then Exit;
    PerfNames.Add(Command);
    PerfCountStrs.Add('0');
    PerfTotalStrs.Add('0');
    PerfMaxStrs.Add('0');
    Result := PerfNames.Count - 1;
End;

Procedure ResetPerfStats(Dummy : Integer);
Begin
    { DelphiScript on this build refuses to resolve .Free or .Count on  }
    { module-level TStringList from inside this Procedure - even when    }
    { proxied through a clearly-typed local Tmp. Both methods work fine  }
    { in other .pas files in the same project. The pattern that DOES     }
    { compile here: reassign new TStringLists, let the GC collect the    }
    { old ones (Pascal's reference-counted Interface model OR a one-     }
    { time leak per session of ~4 small TStringLists is acceptable for   }
    { a dashboard reset action).                                          }
    PerfNames     := TStringList.Create;
    PerfCountStrs := TStringList.Create;
    PerfTotalStrs := TStringList.Create;
    PerfMaxStrs   := TStringList.Create;
End;

Procedure EnsurePerfHeader(Dummy : Integer);
Begin
    Try
        If mmo_Perf.Lines.Count < 2 Then
        Begin
            mmo_Perf.Lines.BeginUpdate;
            Try
                mmo_Perf.Lines.Clear;
                mmo_Perf.Lines.Add(PadRight('command', 30) + PadLeft('N', 6)
                    + PadLeft('avg', 8) + PadLeft('max', 8));
                mmo_Perf.Lines.Add(StringOfChar('-', 52));
            Finally
                mmo_Perf.Lines.EndUpdate;
            End;
        End;
    Except End;
End;

Function FormatPerfLine(Idx : Integer) : String;
Var
    CountVal, TotalVal, MaxVal, AvgVal : Integer;
Begin
    CountVal := StrToIntDef(PerfCountStrs[Idx], 0);
    TotalVal := StrToIntDef(PerfTotalStrs[Idx], 0);
    MaxVal   := StrToIntDef(PerfMaxStrs[Idx], 0);
    If CountVal = 0 Then AvgVal := 0
    Else AvgVal := TotalVal Div CountVal;
    Result := PadRight(PerfNames[Idx], 30)
            + PadLeft(IntToStr(CountVal), 6)
            + PadLeft(IntToStr(AvgVal), 8)
            + PadLeft(IntToStr(MaxVal), 8);
End;

Procedure TrackPerf(Command : String; DurationMs : Cardinal);
Var
    Idx, CountVal, TotalVal, MaxVal, Dms, RowLineIdx : Integer;
    Line : String;
    IsNew : Boolean;
Begin
    IsNew := (PerfNames.IndexOf(Command) < 0);
    Idx := FindOrAddPerf(Command);
    If Idx < 0 Then Exit;
    { Promote DurationMs (Cardinal) to a plain Integer local. DelphiScript }
    { rejects both Cardinal() and Integer() typecasts, so we lean on        }
    { implicit conversion via assignment to a typed local instead.          }
    Dms      := DurationMs;
    CountVal := StrToIntDef(PerfCountStrs[Idx], 0) + 1;
    TotalVal := StrToIntDef(PerfTotalStrs[Idx], 0) + Dms;
    MaxVal   := StrToIntDef(PerfMaxStrs[Idx], 0);
    If Dms > MaxVal Then MaxVal := Dms;
    PerfCountStrs[Idx] := IntToStr(CountVal);
    PerfTotalStrs[Idx] := IntToStr(TotalVal);
    PerfMaxStrs[Idx]   := IntToStr(MaxVal);

    { Incremental write into mmo_Perf. The previous implementation did a    }
    { full Clear+repopulate after every command, which DelphiScript's       }
    { TMemo flickers visibly (BeginUpdate doesn't fully suppress the        }
    { paint between the Clear and the re-add). Replacing the single       }
    { changed row in place keeps the panel rock-stable and removes the     }
    { "UI blanks out then reappears" the user reported.                    }
    EnsurePerfHeader(0);
    Line := FormatPerfLine(Idx);
    RowLineIdx := 2 + Idx;   { 2 header lines come first }
    Try
        If IsNew Or (RowLineIdx >= mmo_Perf.Lines.Count) Then
            mmo_Perf.Lines.Add(Line)
        Else
            mmo_Perf.Lines[RowLineIdx] := Line;
    Except End;
End;


{ Full rebuild kept for the "Reset perf" button and the tab-switch case   }
{ where the memo might have been blanked while hidden. Hot path now uses  }
{ TrackPerf's incremental update.                                          }
Procedure RefreshPerfPanel(Dummy : Integer);
Var
    I : Integer;
Begin
    EnsureStatusBuffers(0);
    If PerfNames.Count = 0 Then Exit;
    Try
        mmo_Perf.Lines.BeginUpdate;
        Try
            mmo_Perf.Lines.Clear;
            mmo_Perf.Lines.Add(PadRight('command', 30) + PadLeft('N', 6)
                + PadLeft('avg', 8) + PadLeft('max', 8));
            mmo_Perf.Lines.Add(StringOfChar('-', 52));
            For I := 0 To PerfNames.Count - 1 Do
                mmo_Perf.Lines.Add(FormatPerfLine(I));
        Finally
            mmo_Perf.Lines.EndUpdate;
        End;
    Except End;
End;


{ Visible severity tag for a log row. ERR has the loudest visual weight.   }
{ Public entry point called from Dispatcher.ProcessSingleRequest after every }
{ command. RequestId is the 32-char hex; we show first 8 in the log so the   }
{ user can grep bridge_trace.log for the exact call.                         }
Procedure AppendLogLine(Command : String; DurationMs : Cardinal; IsError : Boolean;
                        RequestId : String; ErrorDetail : String);
Var
    Line, IdShort : String;
Begin
    EnsureStatusBuffers(0);
    TrackPerf(Command, DurationMs);

    { "Only slow" hides fast (<100 ms) non-error calls. Done here against the }
    { raw duration/error rather than by string-searching a tag prefix.        }
    If OnlySlowFlag And (Not IsError) And (DurationMs < 100) Then Exit;

    IdShort := Copy(RequestId, 1, 8);
    If IdShort = '' Then IdShort := '--------';

    Line := PadLeft(IntToStr(DurationMs), 5) + ' ms  '
          + IdShort + '  ' + Command;

    { Apply the filter at write time. The committed pattern wrote straight  }
    { to mmo_Log.Lines via TStrings (TMemo property), which DelphiScript    }
    { routes through fine. Skipping an entry just means the user doesn't    }
    { see it until a future entry matches the new filter.                   }
    If Not ShouldShowLine(Line) Then Exit;
    Try
        mmo_Log.Lines.Insert(0, Line);
        If IsError And (ErrorDetail <> '') Then
            mmo_Log.Lines.Insert(1, '      `-- ' + ErrorDetail);
        While mmo_Log.Lines.Count > MAX_LOG_LINES Do
            mmo_Log.Lines.Delete(mmo_Log.Lines.Count - 1);
    Except End;

    If IsError Then
    Begin
        Try
            If ErrorDetail <> '' Then
                lbl_LastErr.Caption := '! '
                    + Command + ': ' + ErrorDetail
            Else
                lbl_LastErr.Caption := '! last error: ' + Command;
        Except End;
    End;
End;


{ Dispatcher calls this just before invoking ProcessCommand so the spinner   }
{ kicks on. ResetInFlight clears it after the call returns.                  }
Procedure SetInFlight(Command : String);
Begin
    Try
        InFlightCommand := Command;
        InFlightStartMs := GetTickCount;
        InFlightActive := True;
        SpinnerFrame := 0;
        Try tmr_Spinner.Enabled := True; Except End;
        Try
            pnl_StatusDot.Color := COLOR_ACCENT_AMBER;
            lbl_Spinner.Caption := SpinnerGlyph(SpinnerFrame);
            lbl_Status.Caption := Command + '  (0.0 s)';
            lbl_Status.Font.Color := COLOR_TEXT_BODY;
        Except End;
    Except End;
End;

Procedure ResetInFlight(Dummy : Integer);
Begin
    Try
        InFlightActive := False;
        InFlightCommand := '';
        Try tmr_Spinner.Enabled := False; Except End;
        Try
            lbl_Spinner.Caption := '';
            If PausedFlag Then
            Begin
                pnl_StatusDot.Color := COLOR_TEXT_FAINT;
                lbl_Status.Caption := 'paused';
                lbl_Status.Font.Color := COLOR_TEXT_MUTED;
            End
            Else
            Begin
                pnl_StatusDot.Color := COLOR_ACCENT_GREEN;
                lbl_Status.Caption := 'idle';
                lbl_Status.Font.Color := COLOR_TEXT_BODY;
            End;
        Except End;
    Except End;
End;


{ Open-Dashboard button reflects actual dashboard availability via a         }
{ heartbeat file the Python dashboard process writes every ~3s: a Unix      }
{ epoch timestamp in workspace/dashboard.heartbeat. If the timestamp is     }
{ within the last 15s the dashboard is up (could be the in-process one      }
{ MCP spawned OR a standalone `python -m eda_agent.server dashboard` run    }
{ -- both refresh the same file), button is active. Otherwise grey it out  }
{ and tell the user via caption that no dashboard is reachable.            }
Procedure UpdateOpenWebState(Dummy : Integer);
Var
    HeartbeatPath : String;
    HeartbeatStamp, ThresholdStamp : Integer;
    NewEnabled : Boolean;
Begin
    NewEnabled := False;
    Try
        HeartbeatPath := WorkspaceDir + 'dashboard.heartbeat';
        { Read the heartbeat's MODIFICATION TIME, not its content. The      }
        { dashboard rewrites this file every ~3s, so its file timestamp is  }
        { the last-seen time. Opening the file content (ReadFileContent)    }
        { trips a Windows sharing violation whenever the dashboard is       }
        { mid-write, and the script engine surfaces that as a modal that    }
        { stalls the loop. FileAge queries the directory entry instead of   }
        { locking the content, so it never raises here regardless of how    }
        { the dashboard writes. It returns a DOS date-time stamp (-1 if     }
        { missing); DOS stamps are chronologically ordered, so a plain >=   }
        { against the threshold stamp tests "updated within 15s".           }
        HeartbeatStamp := FileAge(HeartbeatPath);
        If HeartbeatStamp >= 0 Then
        Begin
            ThresholdStamp := DateTimeToFileDate(Now - (15.0 / 86400.0));
            If HeartbeatStamp >= ThresholdStamp Then
                NewEnabled := True;
        End;
    Except End;

    { Avoid pointless repaints on every tick. }
    If NewEnabled = OpenWebEnabled Then Exit;
    OpenWebEnabled := NewEnabled;

    Try
        If NewEnabled Then
        Begin
            btn_OpenWeb.Color := COLOR_ACCENT_BLUE;
            btn_OpenWeb.Font.Color := $00FFFFFF;
            btn_OpenWeb.Cursor := crHandPoint;
            btn_OpenWeb.Caption := 'Open Dashboard';
        End
        Else
        Begin
            btn_OpenWeb.Color := COLOR_BG_CARD;
            btn_OpenWeb.Font.Color := COLOR_TEXT_FAINT;
            btn_OpenWeb.Cursor := crDefault;
            btn_OpenWeb.Caption := 'Open Dashboard  (not running)';
        End;
    Except End;
End;


{ Color the IDLE TIMEOUT KPI according to remaining seconds.                }
Procedure ColorCountdown(IdleSecToShutdown : Integer);
Var
    Col : Cardinal;
    Caption : String;
Begin
    If IdleSecToShutdown <= 30 Then Col := COLOR_ACCENT_RED
    Else If IdleSecToShutdown <= 120 Then Col := COLOR_ACCENT_AMBER
    Else Col := COLOR_ACCENT_GREEN;

    If IdleSecToShutdown >= 60 Then
        Caption := IntToStr(IdleSecToShutdown Div 60) + 'm'
            + PadLeft(IntToStr(IdleSecToShutdown Mod 60), 2) + 's'
    Else
        Caption := IntToStr(IdleSecToShutdown) + 's';

    Try
        lbl_ValStop.Font.Color := Col;
        lbl_ValStop.Caption := Caption;
    Except End;
End;


Procedure UpdateStatusHeader(StatusStr : String);
Begin
    Try
        If Not InFlightActive Then
        Begin
            lbl_Status.Caption := StatusStr;
            lbl_Status.Font.Color := COLOR_TEXT_BODY;
        End;
    Except End;
End;


Procedure UpdateStatsLine(UptimeSec, Requests : Integer; AltiumMs : Cardinal;
                          IdleSecToShutdown : Integer);
Var
    UpStr, MsStr : String;
Begin
    { Uptime: humanize past a minute. }
    If UptimeSec >= 3600 Then
        UpStr := IntToStr(UptimeSec Div 3600) + 'h'
            + IntToStr((UptimeSec Mod 3600) Div 60) + 'm'
    Else If UptimeSec >= 60 Then
        UpStr := IntToStr(UptimeSec Div 60) + 'm'
            + PadLeft(IntToStr(UptimeSec Mod 60), 2) + 's'
    Else
        UpStr := IntToStr(UptimeSec) + 's';

    { Busy time: humanize ms past 1s. }
    If AltiumMs >= 60000 Then
        MsStr := IntToStr(AltiumMs Div 60000) + 'm'
            + IntToStr((AltiumMs Mod 60000) Div 1000) + 's'
    Else If AltiumMs >= 1000 Then
        MsStr := IntToStr(AltiumMs Div 1000) + '.'
            + PadLeft(IntToStr((AltiumMs Mod 1000) Div 100), 1) + 's'
    Else
        MsStr := IntToStr(AltiumMs) + 'ms';

    Try lbl_ValUp.Caption  := UpStr; Except End;
    Try lbl_ValReq.Caption := IntToStr(Requests); Except End;
    Try lbl_ValMs.Caption  := MsStr; Except End;
    ColorCountdown(IdleSecToShutdown);
    UpdateOpenWebState(0);
End;


{ Spinner tick: advance the rotating glyph and update the elapsed-ms      }
{ readout on the status line. Runs only while InFlightActive is True.    }
Procedure tmr_SpinnerTimer(Sender : TObject);
Var
    ElapsedMs : Cardinal;
    Tenths : Cardinal;
Begin
    If Not InFlightActive Then Exit;
    SpinnerFrame := (SpinnerFrame + 1) Mod SPINNER_FRAMES;
    Try lbl_Spinner.Caption := SpinnerGlyph(SpinnerFrame); Except End;
    ElapsedMs := GetTickCount - InFlightStartMs;
    Tenths := ElapsedMs Div 100;
    Try
        lbl_Status.Caption := InFlightCommand + '  ('
            + IntToStr(Tenths Div 10) + '.' + IntToStr(Tenths Mod 10) + ' s)';
    Except End;
End;


Procedure ApplyAlwaysOnTop(Dummy : Integer);
Begin
    Try
        If AlwaysOnTopFlag Then StatusForm.FormStyle := fsStayOnTop
        Else StatusForm.FormStyle := fsNormal;
    Except End;
End;


Procedure SetCheckCaption(Pnl : TPanel; Checked : Boolean; LabelText : String);
Begin
    Try
        If Checked Then
            Pnl.Caption := '  ' + FilledDot(0) + '  ' + LabelText
        Else
            Pnl.Caption := '  ' + HollowDot(0) + '  ' + LabelText;
    Except End;
End;


{ Hidden from the Run Script dialog by its argument. StartMCPServer
  calls it at startup, so the form appears without anyone choosing it. }
Procedure ShowStatusForm(Dummy : Integer);
Var
    NewLeft, NewTop : Integer;
    AvailL, AvailT, AvailW, AvailH : Integer;
    Margin : Integer;
Begin
    Try
        EnsureStatusBuffers(0);
        HidePingsFlag   := True;
        OnlySlowFlag    := False;
        AlwaysOnTopFlag := True;
        PausedFlag      := False;
        RenewRequested  := False;
        InFlightActive  := False;
        SpinnerFrame    := 0;
        FilterText      := '';
        LastPingMs      := 0;
        OpenWebEnabled  := False;

        SetCheckCaption(chk_HidePings, HidePingsFlag, 'pings');
        SetCheckCaption(chk_OnlySlow,  OnlySlowFlag,  '>100ms');
        SetCheckCaption(chk_OnTop,     AlwaysOnTopFlag, 'pin');
        ApplyAlwaysOnTop(0);

        ResetPerfStats(0);

        { Position bottom-right of the work area with margin.              }
        Margin := 24;
        AvailL := 0;
        AvailT := 0;
        AvailW := Screen.Width;
        AvailH := Screen.Height - 40;
        Try
            AvailL := Screen.WorkAreaLeft;
            AvailT := Screen.WorkAreaTop;
            AvailW := Screen.WorkAreaWidth;
            AvailH := Screen.WorkAreaHeight;
        Except End;
        NewLeft := AvailL + AvailW - StatusForm.Width  - Margin;
        NewTop  := AvailT + AvailH - StatusForm.Height - Margin;
        If NewLeft < AvailL Then NewLeft := AvailL;
        If NewTop  < AvailT Then NewTop  := AvailT;
        Try StatusForm.Left := NewLeft; Except End;
        Try StatusForm.Top  := NewTop;  Except End;

        If Not StatusForm.Visible Then StatusForm.Show;
        Try StatusForm.Caption := 'EDA Agent MCP'; Except End;
        Try lbl_Version.Caption := 'v' + SCRIPT_VERSION; Except End;
        Try pnl_StatusDot.Color := COLOR_ACCENT_GREEN; Except End;
        Try lbl_Status.Caption := 'idle'; Except End;
        Try lbl_LastErr.Caption := ''; Except End;
        { Button is always enabled: dashboard can run standalone. }
        UpdateOpenWebState(0);
    Except End;
End;

Procedure HideStatusForm(Dummy : Integer);
Begin
    Try
        If StatusForm.Visible Then StatusForm.Hide;
    Except End;
End;


{ StatusFormClose lives at the END of this unit: closing the form has to     }
{ finalise the pump, and the pump is defined down there. DelphiScript has no }
{ forward declarations, and a DFM handler binds by name anywhere within the  }
{ form's own unit, so position does not affect the binding.                   }


{ Action buttons ============================================================ }

Procedure btn_DetachClick(Sender : TObject);
Begin
    Try Running := False; Except End;
End;

Procedure btn_ClearLogClick(Sender : TObject);
Begin
    Try mmo_Log.Lines.Clear; Except End;
    Try lbl_LastErr.Caption := ''; Except End;
End;

Procedure btn_PauseClick(Sender : TObject);
Begin
    Try
        PausedFlag := Not PausedFlag;
        If PausedFlag Then
        Begin
            btn_Pause.Caption := 'Resume';
            btn_Pause.Color := COLOR_ACCENT_AMBER;
            btn_Pause.Font.Color := $00111111;
            pnl_StatusDot.Color := COLOR_TEXT_FAINT;
            lbl_Status.Caption := 'paused';
            lbl_Status.Font.Color := COLOR_TEXT_MUTED;
        End
        Else
        Begin
            btn_Pause.Caption := 'Pause';
            btn_Pause.Color := $002A2C32;
            btn_Pause.Font.Color := COLOR_TEXT_BODY;
            If Not InFlightActive Then
            Begin
                pnl_StatusDot.Color := COLOR_ACCENT_GREEN;
                lbl_Status.Caption := 'idle';
                lbl_Status.Font.Color := COLOR_TEXT_BODY;
            End;
        End;
    Except End;
End;

Procedure btn_RenewClick(Sender : TObject);
Begin
    { Signal the Dispatcher to reset its REAL idle deadline (LastActivityMs,}
    { local to StartMCPServer) on its next poll -- without this the renew    }
    { only moved the visible countdown and the bridge still auto-shut-down.  }
    Try RenewRequested := True; Except End;
    { Also reset the local tick so the visible countdown jumps back to the   }
    { full window immediately, without waiting for the next poll.            }
    Try LastActivityTick := GetTickCount; Except End;
End;


{ Open the web dashboard. Writes a sentinel file the Python dashboard polls }
{ and calls webbrowser.open() on. Sync round-trip would freeze the UI,      }
{ this hand-off is fire-and-forget. If the heartbeat says no dashboard is   }
{ running we surface the hint instead of writing a sentinel nobody reads.   }
Procedure btn_OpenWebClick(Sender : TObject);
Var
    SentinelPath : String;
Begin
    If Not OpenWebEnabled Then
    Begin
        Try lbl_LastErr.Caption := 'no dashboard running - copy the command below'; Except End;
        Exit;
    End;
    Try
        SentinelPath := WorkspaceDir + 'open_dashboard.url';
        WriteFileContent(SentinelPath, 'http://127.0.0.1:8766/');
        Try lbl_LastErr.Caption := ''; Except End;
    Except End;
End;


{ Copy the dashboard standalone-launch command to the Windows clipboard. }
{ The status form's footer shows `eda-agent dashboard --port 8766` and  }
{ a Copy button so the user can paste it into a terminal to run the     }
{ dashboard without needing Claude / any MCP client open.                }
Procedure btn_CopyCmdClick(Sender : TObject);
Begin
    Try
        { Use the `python -m` form so the command works regardless of  }
        { whether the user has added Python's Scripts dir to PATH. The }
        { `eda-agent` console script is installed there by pip but the }
        { directory isn't on PATH by default on Windows.                }
        Clipboard.AsText := 'python -m eda_agent.server dashboard --port 8766';
        Try btn_CopyCmd.Caption := 'Copied'; Except End;
    Except End;
End;


{ Toggles ================================================================== }

Procedure chk_HidePingsClick(Sender : TObject);
Begin
    Try
        HidePingsFlag := Not HidePingsFlag;
        SetCheckCaption(chk_HidePings, HidePingsFlag, 'pings');
        RebuildVisibleLog(0);
    Except End;
End;

Procedure chk_OnlySlowClick(Sender : TObject);
Begin
    Try
        OnlySlowFlag := Not OnlySlowFlag;
        SetCheckCaption(chk_OnlySlow, OnlySlowFlag, '>100ms');
        RebuildVisibleLog(0);
    Except End;
End;

Procedure chk_OnTopClick(Sender : TObject);
Begin
    Try
        AlwaysOnTopFlag := Not AlwaysOnTopFlag;
        SetCheckCaption(chk_OnTop, AlwaysOnTopFlag, 'pin');
        ApplyAlwaysOnTop(0);
    Except End;
End;

Procedure edt_FilterChange(Sender : TObject);
Begin
    Try FilterText := edt_Filter.Text; Except End;
    RebuildVisibleLog(0);
End;


{ Hover handlers ============================================================ }

Procedure btn_DetachEnter(Sender : TObject);
Begin Try btn_Detach.Color := $00D05050; Except End; End;
Procedure btn_DetachLeave(Sender : TObject);
Begin Try btn_Detach.Color := $00B14545; Except End; End;

Procedure btn_ClearLogEnter(Sender : TObject);
Begin Try btn_ClearLog.Color := $003A3C42; Except End; End;
Procedure btn_ClearLogLeave(Sender : TObject);
Begin Try btn_ClearLog.Color := $002A2C32; Except End; End;

Procedure btn_PauseEnter(Sender : TObject);
Begin
    Try
        If PausedFlag Then btn_Pause.Color := $00B388F0
        Else btn_Pause.Color := $003A3C42;
    Except End;
End;
Procedure btn_PauseLeave(Sender : TObject);
Begin
    Try
        If PausedFlag Then btn_Pause.Color := COLOR_ACCENT_AMBER
        Else btn_Pause.Color := $002A2C32;
    Except End;
End;

Procedure btn_RenewEnter(Sender : TObject);
Begin Try btn_Renew.Color := $003A3C42; Except End; End;
Procedure btn_RenewLeave(Sender : TObject);
Begin Try btn_Renew.Color := $002A2C32; Except End; End;

Procedure btn_OpenWebEnter(Sender : TObject);
Begin
    If OpenWebEnabled Then
        Try btn_OpenWeb.Color := $00FFAE6A; Except End;
End;
Procedure btn_OpenWebLeave(Sender : TObject);
Begin
    If OpenWebEnabled Then
        Try btn_OpenWeb.Color := COLOR_ACCENT_BLUE; Except End
    Else
        Try btn_OpenWeb.Color := COLOR_BG_CARD; Except End;
End;

Procedure chk_HidePingsEnter(Sender : TObject);
Begin Try chk_HidePings.Color := $00252630; Except End; End;
Procedure chk_HidePingsLeave(Sender : TObject);
Begin Try chk_HidePings.Color := COLOR_BG_BASE; Except End; End;

Procedure chk_OnlySlowEnter(Sender : TObject);
Begin Try chk_OnlySlow.Color := $00252630; Except End; End;
Procedure chk_OnlySlowLeave(Sender : TObject);
Begin Try chk_OnlySlow.Color := COLOR_BG_BASE; Except End; End;

Procedure chk_OnTopEnter(Sender : TObject);
Begin Try chk_OnTop.Color := $00252630; Except End; End;
Procedure chk_OnTopLeave(Sender : TObject);
Begin Try chk_OnTop.Color := COLOR_BG_BASE; Except End; End;

Procedure tab_LogEnter(Sender : TObject);
Begin
    Try
        If mmo_Log.Visible Then tab_Log.Color := $00252630
        Else tab_Log.Color := $002A2C32;
    Except End;
End;
Procedure tab_LogLeave(Sender : TObject);
Begin
    Try
        If mmo_Log.Visible Then tab_Log.Color := COLOR_BG_BASE
        Else tab_Log.Color := COLOR_BG_CARD;
    Except End;
End;

Procedure tab_PerfEnter(Sender : TObject);
Begin
    Try
        If mmo_Perf.Visible Then tab_Perf.Color := $00252630
        Else tab_Perf.Color := $002A2C32;
    Except End;
End;
Procedure tab_PerfLeave(Sender : TObject);
Begin
    Try
        If mmo_Perf.Visible Then tab_Perf.Color := COLOR_BG_BASE
        Else tab_Perf.Color := COLOR_BG_CARD;
    Except End;
End;


Procedure tab_LogClick(Sender : TObject);
Begin
    Try
        mmo_Log.Visible := True;
        mmo_Perf.Visible := False;
        tab_Log.Color := COLOR_BG_BASE;
        tab_Log.Font.Color := COLOR_ACCENT_BLUE;
        tab_Perf.Color := COLOR_BG_CARD;
        tab_Perf.Font.Color := COLOR_TEXT_MUTED;
    Except End;
End;

Procedure tab_PerfClick(Sender : TObject);
Begin
    Try
        RefreshPerfPanel(0);
        mmo_Log.Visible := False;
        mmo_Perf.Visible := True;
        tab_Perf.Color := COLOR_BG_BASE;
        tab_Perf.Font.Color := COLOR_ACCENT_BLUE;
        tab_Log.Color := COLOR_BG_CARD;
        tab_Log.Font.Color := COLOR_TEXT_MUTED;
    Except End;
End;


{ ============================================================================ }
{ THE PUMP                                                                     }
{                                                                              }
{ Dispatch runs on tmr_Poll, declared in StatusForm.dfm. It lives in THIS unit }
{ and not in Dispatcher.pas for a measured reason: Altium resolves a form's    }
{ event handler only within the unit that owns the form, and a handler defined }
{ elsewhere never fires and never complains. A control timer in the form's own }
{ unit fired 17 times in the same run where the cross-unit one fired zero.     }
{ Everything the tick touches therefore has to be reachable from here, which   }
{ is why ProcessCommand and ProcessSingleRequest moved out of Dispatcher.pas.  }
{                                                                              }
{ The timer also only ticks while its form is VISIBLE: hiding the form         }
{ suspends it (measured, one tick then nothing, on two runs) and re-showing    }
{ resumes it. So the pump is tied to the dashboard being on screen, and        }
{ HideStatusForm is only ever called when the session is ending.               }
{ ============================================================================ }

Const
    { How many queued requests one tick will dispatch before yielding. The old }
    { blocking loop handled one request per 10 ms sleep; a timer's real floor  }
    { is coarser (~16 ms), so a burst would otherwise pay that floor once per  }
    { request. Draining absorbs the burst instead. Bounded rather than         }
    { "until empty" so a client that writes requests faster than they are      }
    { served can still never starve the UI.                                     }
    PUMP_DRAIN_MAX = 32;

Var
    { Dashboard counters, moved here with the dispatch code. }
    StatusStartTick      : Cardinal;
    StatusRequestCount   : Integer;
    StatusLastCommand    : String;
    StatusTotalAltiumMs  : Cardinal;

    { Pump state. All of these were LOCALS of the old blocking StartMCPServer, }
    { which could keep them on its stack because it never returned. A tick     }
    { returns between polls, so they have to outlive it.                       }
    PumpStopPath         : String;
    PumpIdleCount        : Integer;
    PumpLastActivityMs   : Cardinal;
    PumpInterval         : Integer;
    PumpInTick           : Boolean;
    PumpFinalised        : Boolean;
    { True when the session is ending because ALTIUM is closing, as
      opposed to Detach, the stop file, the stop command or auto-shutdown.
      The difference decides how much teardown is safe to attempt. }
    PumpQuitting         : Boolean;


Function ProcessCommand(Command : String; Params : String; RequestId : String) : String;
Var
    Category, Action : String;
    DotPos : Integer;
Begin
    DotPos := Pos('.', Command);
    If DotPos > 0 Then
    Begin
        Category := Copy(Command, 1, DotPos - 1);
        Action := Copy(Command, DotPos + 1, Length(Command));
    End
    Else
    Begin
        Category := Command;
        Action := '';
    End;

    Case Category Of
        'application': Result := HandleApplicationCommand(Action, Params, RequestId);
        'project':     Result := HandleProjectCommand(Action, Params, RequestId);
        'library':     Result := HandleLibraryCommand(Action, Params, RequestId);
        'generic':     Result := HandleGenericCommand(Action, Params, RequestId);
        'pcb':         Result := HandlePCBCommand(Action, Params, RequestId);
        'audit':       Result := HandleAuditCommand(Action, Params, RequestId);
    Else
        Result := BuildErrorResponse(RequestId, 'UNKNOWN_COMMAND',
            'Unknown command category: ' + Category +
            '. Use generic.* for object operations, pcb.* for PCB-specific ' +
            'commands, or audit.* for design-lint checks.');
    End;
End;

{..............................................................................}
{ Process a single request if one exists. Returns True iff a request was found.}
{                                                                                }
{ The dispatcher scans for any request_*.json file in the workspace, extracts  }
{ the ID from the filename, reads and deletes the request, dispatches, and    }
{ writes response_<id>.json. The handler returns the JSON envelope as a       }
{ String; the dispatcher writes the file. Handlers that previously bypassed    }
{ the dispatcher's write via the ResponseAlreadyWritten flag have all been     }
{ migrated to the standard pattern.                                            }
{..............................................................................}

{..............................................................................}
{ CommandIsReadOnly - whether a command leaves the design untouched.           }
{                                                                              }
{ Used for ONE thing: deciding whether the compiled-netlist cache survives a   }
{ command. SmartCompile skips DM_Compile for COMPILE_CACHE_TTL_MS when the     }
{ project reports no dirty documents, and InvalidateCompileCache existed but   }
{ was never called from anywhere, so a write followed within that window by a  }
{ connectivity read handed back the netlist from BEFORE the write. Whether it  }
{ did depended on whether that particular handler happened to dirty the        }
{ document, which is not uniform: many go through ProcessControl, which marks  }
{ the document modified, and others assign through SetState_ and do not.       }
{                                                                              }
{ THE UNKNOWN CASE COUNTS AS A WRITE. Only the prefixes below are treated as   }
{ leaving the design alone, so a command this list has never heard of, and     }
{ every command added later, invalidates. An unnecessary invalidation costs    }
{ one recompile; a missed one returns connectivity that predates the edit.     }
{..............................................................................}

Function ActionHasPrefix(Verb : String; Prefix : String) : Boolean;
Begin
    Result := Copy(Verb, 1, Length(Prefix)) = Prefix;
End;

Function CommandIsReadOnly(Command : String) : Boolean;
Var
    Verb : String;
    DotPos : Integer;
Begin
    Verb := LowerCase(Trim(Command));
    DotPos := Pos('.', Verb);
    If DotPos > 0 Then Verb := Copy(Verb, DotPos + 1, Length(Verb) - DotPos);

    Result := ActionHasPrefix(Verb, 'get_')
           Or ActionHasPrefix(Verb, 'list_')
           Or ActionHasPrefix(Verb, 'query')
           Or ActionHasPrefix(Verb, 'read_')
           Or ActionHasPrefix(Verb, 'find_')
           Or ActionHasPrefix(Verb, 'count')
           Or ActionHasPrefix(Verb, 'audit_')
           Or ActionHasPrefix(Verb, 'check_')
           Or ActionHasPrefix(Verb, 'calc_')
           Or ActionHasPrefix(Verb, 'export_')
           Or ActionHasPrefix(Verb, 'render_')
           Or ActionHasPrefix(Verb, 'probe_')
           Or ActionHasPrefix(Verb, 'inspect_')
           Or ActionHasPrefix(Verb, 'diff_')
           Or ActionHasPrefix(Verb, 'compare_')
           Or (Verb = 'ping');
End;

Function ProcessSingleRequest(Dummy : Integer): Boolean;
Var
    RequestPath, RequestId : String;
    RequestContent, ResponseContent : String;
    Command, Params, ProtoVer, EnvelopeError : String;
    ExceptionMsg : String;
    FocusBefore, FocusAfter : String;
    StartMs, DurationMs : Cardinal;
    ResultTag : String;
    DashIsError : Boolean;
    DashDetail, DashErrPayload, DashCode : String;
Begin
    Result := False;
    EnsureWorkspaceDir(0);

    If Not ScanForRequestFile(RequestPath, RequestId) Then Exit;

    // Read the request file
    RequestContent := ReadFileContent(RequestPath);
    // Remove the request file regardless of read outcome so we never reprocess
    DeleteFile(RequestPath);

    If RequestContent = '' Then
    Begin
        { ReadFileContent already retried 12 times over ~180ms for a       }
        { transient sharing violation, so an empty result here means the   }
        { file was genuinely empty or still locked. Deleting it and        }
        { exiting SILENTLY left the caller to wait out its entire deadline }
        { and report a plain timeout, which reads exactly like a wedged    }
        { polling loop and sends the user hunting the wrong fault.          }
        {                                                                   }
        { The id came from the FILENAME via ScanForRequestFile and has not  }
        { been overwritten by the body's id yet, so the call can still be   }
        { answered with the actual reason.                                  }
        If IsValidRequestId(RequestId) Then
            WriteResponseFile(RequestId,
                BuildErrorResponse(RequestId, 'REQUEST_UNREADABLE',
                    'Request file was empty or unreadable after 12 retries '
                    + 'and has been discarded. The polling loop is healthy; '
                    + 'retry the call.'));
        Exit;
    End;

    // ID arrives in the JSON body. Per-request response files use it for
    // the filename so concurrent callers each get an isolated response file.
    RequestId := ExtractJsonValue(RequestContent, 'id');
    Command := ExtractJsonValue(RequestContent, 'command');
    Params := ExtractJsonValue(RequestContent, 'params');
    ProtoVer := ExtractJsonValue(RequestContent, 'protocol_version');

    EnvelopeError := ValidateRequestEnvelope(RequestId, Command);
    If EnvelopeError <> '' Then
    Begin
        // Without a valid id we can't write a per-request response file;
        // fall back to writing response.json so Python can still pick it up.
        If IsValidRequestId(RequestId) Then
            WriteResponseFile(RequestId,
                BuildErrorResponse(RequestId, 'MALFORMED_REQUEST', EnvelopeError))
        Else
            WriteFileContent(WorkspaceDir + 'response.json',
                BuildErrorResponse('', 'MALFORMED_REQUEST', EnvelopeError));
        Result := True;
        Exit;
    End;

    If (ProtoVer <> '') And (ProtoVer <> IntToStr(PROTOCOL_VERSION)) Then
    Begin
        WriteResponseFile(RequestId,
            BuildErrorResponseDetailed(RequestId, 'PROTOCOL_VERSION_MISMATCH',
                'Client protocol_version=' + ProtoVer +
                ' does not match server PROTOCOL_VERSION=' + IntToStr(PROTOCOL_VERSION) +
                '. Update the eda-agent client or restart the Altium script.',
                '{"client_version":' + ProtoVer +
                ',"server_version":' + IntToStr(PROTOCOL_VERSION) + '}'));
        Result := True;
        Exit;
    End;

    StatusLastCommand := Command;
    Inc(StatusRequestCount);
    StartMs := GetTickCount;
    ResultTag := 'OK';

    { MCP liveness: any inbound command (typically application.ping every }
    { 30 s) keeps the Open Dashboard button enabled.                       }
    LastPingMs := StartMs;

    { Spinner + in-flight readout on the dashboard. Reset on exit so the }
    { status pill drops back to idle/paused/green when we're done.       }
    SetInFlight(Command);

    { WHERE THE CALLER WAS LOOKING, BEFORE THE HANDLER RAN.
      Nearly every tool acts on the focused document, and several change
      it as a side effect of doing their job. Nothing announced that.
      Measured: lib_probe_footprint focused a PcbLib to read it, the
      obj_switch_view that followed switched the LIBRARY into 3D, and the
      session spent a long time looking for a placement bug that was not
      there.
      Captured here rather than per handler because there are hundreds of
      them and this is the one place every command passes through. }
    FocusBefore := CurrentFocusedDocPath(0);
    ResetNextStep(0);

    ExceptionMsg := '';
    { Heartbeat: write progress_<id>.json so Python can distinguish "still      }
    { working" from "polling loop dead" when the 10 s default deadline runs   }
    { out on a legitimately-slow handler. Delete only AFTER writing the       }
    { response, so at no point are both files missing.                        }
    StartProgress(RequestId);
    Try
        Try
            ResponseContent := ProcessCommand(Command, Params, RequestId);
        Except
            ExceptionMsg := 'Unhandled exception processing: ' + Command;
            ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR', ExceptionMsg);
            ResultTag := 'EXCEPTION';
        End;

        { The compiled netlist is stale the moment anything is written, and
          this is the one place every command passes through, so it is done
          here rather than in each of the hundreds of handlers.

          On the exception path too, deliberately: a handler that threw part
          way through may well have written something first, and that is
          exactly when a cached netlist is worth least. }
        If Not CommandIsReadOnly(Command) Then InvalidateCompileCache(0);

        If ResponseContent = '' Then
        Begin
            // Handler returned nothing, degenerate but recoverable. Synthesise
            // an INTERNAL_ERROR rather than leaving the caller polling forever.
            ResponseContent := BuildErrorResponse(RequestId, 'INTERNAL_ERROR',
                'Handler returned empty response for: ' + Command);
            ResultTag := 'EMPTY';
        End;

        { Say so if the active document moved. Appended as a sibling of
          data rather than merged into it, because data is whatever the
          handler chose to return and this must not depend on its shape.
          Silent when nothing moved, which is the overwhelming majority. }
        { The follow-up this reply owes, if the handler named one. }
        If PendingNextStep(0) <> '' Then
            ResponseContent := AppendEnvelopeField(ResponseContent,
                JsonStr('next_step', PendingNextStep(0)));

        FocusAfter := CurrentFocusedDocPath(0);
        If FocusAfter <> FocusBefore Then
            ResponseContent := AppendEnvelopeField(ResponseContent,
                '"active_document_changed":' + JsonObj(
                    JsonStr('from', FocusBefore) + ',' +
                    JsonStr('to', FocusAfter) + ',' +
                    JsonStr('note', 'this command moved the focused '
                        + 'document. Tools that act on the focused '
                        + 'document will now act on the new one.')));

        WriteResponseFile(RequestId, ResponseContent);
    Finally
        EndProgress(RequestId);
    End;

    DurationMs := GetTickCount - StartMs;
    StatusTotalAltiumMs := StatusTotalAltiumMs + DurationMs;

    AppendLog(FormatLogStamp(0) + ',' + IntToStr(DurationMs) + ',' + Command + ',' + ResultTag
              + ',' + IntToStr(Length(ResponseContent)) + ',' + Copy(ResponseContent, 1, 200));

    { Surface the error message to the dashboard (inline detail row + last- }
    { error banner) when the response is success=false. ExtractJsonValue   }
    { handles the nested error/code/message path via two successive calls. }
    DashIsError := (ResultTag = 'EXCEPTION');
    DashErrPayload := ExtractJsonValue(ResponseContent, 'error');
    DashDetail := '';
    DashCode := '';
    If (DashErrPayload <> '') And (DashErrPayload <> 'null') Then
    Begin
        DashIsError := True;
        DashCode    := ExtractJsonValue(DashErrPayload, 'code');
        DashDetail  := ExtractJsonValue(DashErrPayload, 'message');
        { Explicit Begin/End around each branch, DelphiScript parser   }
        { trips on `Else If` without them.                               }
        If (DashCode <> '') And (DashDetail <> '') Then
        Begin
            DashDetail := DashCode + ': ' + DashDetail;
        End
        Else
        Begin
            If (DashCode <> '') Then DashDetail := DashCode;
        End;
    End;
    AppendLogLine(Command, DurationMs, DashIsError, RequestId, DashDetail);

    ResetInFlight(0);

    Result := True;
End;


{..............................................................................}
{ Clean up state left by the MCP server before exiting. Deletes any leftover   }
{ per-request IPC files and flushes the UI.                                    }
{                                                                              }
{ Lives here rather than in Dispatcher.pas because FinalisePump below calls it }
{ and Dispatcher.pas compiles LAST, so a call the other way would point        }
{ forward and DelphiScript has no forward declarations.                        }
{..............................................................................}

Procedure CleanupMCPServer(Dummy : Integer);
Begin
    CleanupOrphanRequests(0);
    CleanupOrphanProgress(0);
    Application.ProcessMessages;
End;


{..............................................................................}
{ End the session: stop the timer, log the session end, put the dashboard away }
{ and clear the IPC files.                                                     }
{                                                                              }
{ IDEMPOTENT, and it has to be, because two different paths reach it. Closing  }
{ the form hides it, and a hidden form's timer stops, so the close handler     }
{ must finalise on the spot rather than leave it to a tick that is never       }
{ coming. Every other stop (Detach, stop file, stop command, auto-shutdown,    }
{ Altium quitting) leaves the form up, so the next tick finalises instead.     }
{..............................................................................}

Procedure FinalisePump(Dummy : Integer);
Var
    QuitTag : String;
Begin
    If PumpFinalised Then Exit;
    PumpFinalised := True;
    Running := False;

    { FIRST, and unconditionally. A tick that fires after this point would }
    { run against an engine Altium may already be unloading.                }
    Try tmr_Poll.Enabled := False; Except End;

    QuitTag := 'no';
    If PumpQuitting Then QuitTag := 'yes';
    Try
        AppendLog(FormatLogStamp(0) + ',0,_session_end,requests='
                  + IntToStr(StatusRequestCount) + ',quitting=' + QuitTag);
    Except End;

    { WHEN ALTIUM IS QUITTING, STOP HERE.                                   }
    {                                                                       }
    { Measured 2026-09-16: with the timer pump, closing Altium while        }
    { attached no longer hangs it. Altium ran its whole shutdown through to }
    { "Application Finalized" and exited, and Windows logged neither a hang }
    { nor a crash. What it DID raise was an Access Violation in             }
    { ScriptingSystem.DLL (read of FFFFFFFFFFFFFFFF) during teardown.       }
    {                                                                       }
    { The two steps below are how you earn that: HideStatusForm touches a   }
    { form Altium is in the middle of destroying, and CleanupMCPServer      }
    { calls Application.ProcessMessages, which pumps the message loop while }
    { the host unloads the scripting engine underneath it. Neither buys     }
    { anything on this path. Altium destroys the form itself, and orphaned  }
    { IPC files are purged at the next _session_start, which is exactly     }
    { where the count in the log comes from.                                }
    {                                                                       }
    { On every other exit, Detach, the stop file, application.stop_server   }
    { and auto-shutdown, the engine is alive and Altium keeps running, so   }
    { the dashboard must actually go away and the workspace must be tidied. }
    If PumpQuitting Then Exit;

    { Detach, the stop file, application.stop_server and auto-shutdown all }
    { land here: Altium keeps running, so the dashboard must go away and   }
    { the workspace must be tidied.                                         }
    {                                                                      }
    { DO NOT CALL CleanupMCPServer HERE. Its Application.ProcessMessages   }
    { pumps the message loop from inside this form's OWN timer handler,    }
    { while that same form is being hidden, which re-enters the form as it }
    { tears down. Measured 2026-09-18: pressing Detach raised "Access      }
    { violation ... in module 'ScriptingSystem.DLL'. Read of address       }
    { FFFFFFFFFFFFFFFF", the same signature the quit path produced before  }
    { it stopped pumping. The old blocking loop ran this from              }
    { StartMCPServer's exit rather than from a timer event on the form,    }
    { which is why it only appeared once dispatch moved onto the timer.    }
    {                                                                      }
    { The orphan sweeps below are the part that actually matters; the      }
    { message pump was only ever there to flush the UI, and the tick       }
    { returning to Altium does that by itself.                             }
    CleanupOrphanRequests(0);
    CleanupOrphanProgress(0);
    HideStatusForm(0);
End;


{..............................................................................}
{ One poll tick. Replaces one iteration of the old `While Running Do` loop,    }
{ with the same stop conditions in the same order.                             }
{..............................................................................}

Procedure tmr_PollTimer(Sender : TObject);
Var
    HadRequest : Boolean;
    Drained    : Integer;
    NowMs      : Cardinal;
Begin
    { RE-ENTRANCY GUARD. A tick is not atomic: a handler can run for seconds  }
    { and calls Application.ProcessMessages while it works, which lets this    }
    { same timer fire again underneath it. Unguarded, the second tick would    }
    { pick up the NEXT request while the first is still in flight and the      }
    { counters and the in-flight pill would interleave.                        }
    If PumpInTick Then Exit;
    PumpInTick := True;
    Try
        Try
            If Not Running Then
            Begin
                FinalisePump(0);
                Exit;
            End;

            { Altium is closing. The old loop could not get here, because the  }
            { close was dispatched inside its own ProcessMessages and never    }
            { came back; the probe measured a DFM timer still ticking for      }
            { three seconds after IsQuitting flipped, which is what makes this }
            { check reachable at all.                                          }
            Try
                If Client.IsQuitting Then
                Begin
                    PumpQuitting := True;
                    FinalisePump(0);
                    Exit;
                End;
            Except
                { The probe itself throwing means the client is already }
                { going away, which is the quitting case just the same. }
                PumpQuitting := True;
                FinalisePump(0);
                Exit;
            End;

            If FileExists(PumpStopPath) Then
            Begin
                DeleteFile(PumpStopPath);
                FinalisePump(0);
                Exit;
            End;

            { Renew button: reset the real idle deadline once per click. }
            If RenewRequested Then
            Begin
                PumpLastActivityMs := GetTickCount;
                RenewRequested := False;
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    AutoShutdownMs Div 1000);
            End;

            { Auto-shutdown after prolonged inactivity. Paused sessions   }
            { never auto-shutdown so the user can step away indefinitely. }
            If PausedFlag Then
                PumpLastActivityMs := GetTickCount;
            If AutoShutdownMs > 0 Then
            Begin
                NowMs := GetTickCount;
                If NowMs >= PumpLastActivityMs Then
                Begin
                    If (NowMs - PumpLastActivityMs) > AutoShutdownMs Then
                    Begin
                        FinalisePump(0);
                        Exit;
                    End;
                End;
            End;

            If PausedFlag Then
            Begin
                { Skip dispatch entirely while paused, but still refresh the }
                { stats so the dashboard countdown stays alive.               }
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    AutoShutdownMs Div 1000);
                Exit;
            End;

            { Drain rather than one-per-tick: see PUMP_DRAIN_MAX. Running is }
            { re-tested each pass so a Detach or a close mid-burst stops it. }
            Drained := 0;
            HadRequest := False;
            While (Drained < PUMP_DRAIN_MAX) And Running Do
            Begin
                If Not ProcessSingleRequest(0) Then Break;
                HadRequest := True;
                Inc(Drained);
                { Yield between requests so a long drain cannot freeze the UI. }
                { Safe against the timer re-entering here: the guard holds.    }
                Application.ProcessMessages;
            End;

            If HadRequest Then
            Begin
                PumpIdleCount := 0;
                PumpLastActivityMs := GetTickCount;
                If PumpInterval <> PollIntervalActiveMs Then
                Begin
                    PumpInterval := PollIntervalActiveMs;
                    Try tmr_Poll.Interval := PumpInterval; Except End;
                End;
                UpdateStatusHeader('MCP: idle');
                UpdateStatsLine(
                    (GetTickCount - StatusStartTick) Div 1000,
                    StatusRequestCount,
                    StatusTotalAltiumMs,
                    (AutoShutdownMs - (GetTickCount - PumpLastActivityMs)) Div 1000);
                { Perf row already updated in-place by TrackPerf (called }
                { from AppendLogLine inside ProcessSingleRequest). Skip  }
                { the full RefreshPerfPanel rebuild that used to flash  }
                { the visible memo on every command.                     }
            End
            Else
            Begin
                Inc(PumpIdleCount);
                { Back off to the idle interval. Assigning Interval restarts }
                { the timer, so only touch it when the value actually        }
                { changes.                                                    }
                If PumpIdleCount > IdleThreshold Then
                Begin
                    If PumpInterval <> PollIntervalIdleMs Then
                    Begin
                        PumpInterval := PollIntervalIdleMs;
                        Try tmr_Poll.Interval := PumpInterval; Except End;
                    End;
                End;
                If (PumpIdleCount Mod 10) = 0 Then
                    UpdateStatsLine(
                        (GetTickCount - StatusStartTick) Div 1000,
                        StatusRequestCount,
                        StatusTotalAltiumMs,
                        (AutoShutdownMs - (GetTickCount - PumpLastActivityMs)) Div 1000);
            End;
        Except
            { Altium tearing down underneath the tick. Stop quietly, exactly }
            { as the old loop's outer Try/Except did. Treated as quitting:    }
            { whatever threw, the engine is not in a state worth poking       }
            { further on the way out.                                          }
            PumpQuitting := True;
            Try FinalisePump(0); Except End;
        End;
    Finally
        PumpInTick := False;
    End;
End;


{..............................................................................}
{ Start MCP server: arm the poll timer and RETURN.                             }
{                                                                              }
{ The return is the whole point. This used to block for the entire session,    }
{ holding Altium's single-threaded scripting engine, which is what made        }
{ closing Altium hang. Now the engine is free between ticks and Altium's       }
{ close path never waits on a script.                                          }
{                                                                              }
{ Stop methods are unchanged: send application.stop_server, drop a 'stop' file }
{ in the workspace, press Detach, close the dashboard, or wait for             }
{ auto-shutdown. All tunables still come from mcp_config.json via              }
{ LoadMCPConfig; PollIntervalActiveMs and PollIntervalIdleMs are now the       }
{ timer's interval rather than a Sleep length. YieldIterations and             }
{ YieldEveryNActive no longer apply: they existed to hand time back to Altium  }
{ from inside a loop that never returned, and a timer yields by construction.  }
{ They stay in the config so an existing mcp_config.json still loads.          }
{                                                                              }
{ Takes a Dummy argument so it stays OUT of the Run Script dialog. The entry   }
{ the user picks is still StartMCPServer in Dispatcher.pas, which calls this;  }
{ the dialog lists every PARAMETERLESS procedure, so without the argument the  }
{ attach step would sprout a second, near-identical entry to choose between.   }
{..............................................................................}

Procedure StartMCPPump(Dummy : Integer);
Begin
    If Running Then Exit;

    InitDefaultConfig(0);
    EnsureWorkspaceDir(0);
    LoadMCPConfig(0);
    { Startup purge: nothing on disk can belong to a live exchange, because no
      pump was running to serve it. Responses are purged here but NOT in
      CleanupMCPServer(0) -- on shutdown a client may still be reading one. }
    CleanupOrphanRequests(0);
    CleanupOrphanResponses(0);
    CleanupOrphanProgress(0);

    Running := True;
    PumpStopPath := WorkspaceDir + 'stop';
    If FileExists(PumpStopPath) Then DeleteFile(PumpStopPath);

    PumpIdleCount := 0;
    PumpLastActivityMs := GetTickCount;
    PumpInTick := False;
    PumpFinalised := False;
    PumpQuitting := False;

    StatusStartTick := GetTickCount;
    StatusRequestCount := 0;
    StatusLastCommand := '';
    StatusTotalAltiumMs := 0;

    { The timer only ticks while the form is visible, so this is not just  }
    { cosmetic: showing the dashboard is what starts the pump running.     }
    ShowStatusForm(0);
    UpdateStatusHeader('MCP: idle');
    UpdateStatsLine(0, 0, 0, AutoShutdownMs Div 1000);
    AppendLog(FormatLogStamp(0) + ',0,_session_start,version=' + SCRIPT_VERSION
              + ',protocol=' + IntToStr(PROTOCOL_VERSION));

    PumpInterval := PollIntervalActiveMs;
    Try
        tmr_Poll.Interval := PumpInterval;
        tmr_Poll.Enabled := True;
    Except End;
End;


{..............................................................................}
{ Closing the dashboard ends the session.                                      }
{                                                                              }
{ Defined here, at the end of the unit, because it finalises the pump and      }
{ DelphiScript has no forward declarations. A DFM handler binds by name        }
{ anywhere inside the form's own unit, so the position costs nothing.          }
{                                                                              }
{ Finalises INLINE rather than just clearing Running: the form hides as it     }
{ closes, a hidden form's timer stops, and the tick that would otherwise have  }
{ written _session_end and cleared the IPC files would never run.              }
{..............................................................................}

Procedure StatusFormClose(Sender : TObject; Var Action : TCloseAction);
Begin
    Try FinalisePump(0); Except End;
End;
