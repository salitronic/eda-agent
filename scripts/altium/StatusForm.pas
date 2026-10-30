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
{   - Pause / Cancel-current / Renew / Clear / Detach action row, and the   }
{     Saving switch, which stops every save the bridge makes (kept across   }
{     restarts in saving.json in the workspace)                             }
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


{ Static UI constants: Altium's dark theme, read off AD 26.11's own      }
{ panels, status bar and buttons, so the form looks like one of them.    }
{ DelphiScript color literals are BGR-ordered Cardinals (8 hex digits      }
{ after the $); a malformed 7-digit literal bombs the parser without an   }
{ obvious error location, so always double-count.                          }
Const
    COLOR_BG_BASE       = $00353535;   { panel body }
    COLOR_BG_CARD       = $00404040;   { title strip, tab strip, status bar }
    COLOR_BG_ELEVATED   = $00545454;   { button }
    COLOR_BG_HOVER      = $00646464;   { button under the mouse }
    COLOR_BG_LOG        = $002B2B2B;   { log and input fields }
    COLOR_ACCENT_BLUE   = $00FFB363;   { #63B3FF in BGR }
    COLOR_ACCENT_AMBER  = $00589EE0;   { #E09E58 in BGR }
    COLOR_ACCENT_RED    = $005C5CFF;   { #FF5C5C in BGR }
    COLOR_ACCENT_GREEN  = $006BC464;   { #64C46B in BGR }
    COLOR_TEXT_BODY     = $00E5E5E5;
    COLOR_TEXT_MUTED    = $00A0A0A0;
    COLOR_TEXT_FAINT    = $00808080;

    MAX_LOG_LINES       = 2000;
    SPINNER_FRAMES      = 4;


{ The notice line under the figures takes no room until there is something }
{ to say: the last error, or why Dashboard did nothing.                     }
Procedure ShowNotice(Text : String);
Begin
    Try lbl_LastErr.Caption := Text; Except End;
    Try
        If Text = '' Then pnl_ErrBar.Height := 0
        Else pnl_ErrBar.Height := 16;
    Except End;
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
                ShowNotice('! ' + Command + ': ' + ErrorDetail)
            Else
                ShowNotice('! last error: ' + Command);
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


{ Tooltips. Each button's icon is a label lying on top of it, so the label }
{ carries the same hint, or hovering the icon would show nothing. The      }
{ delay before a hint appears is Altium's own, the same as its toolbars.  }
{ Altium's hint window does not wrap, so a long hint is broken into lines }
{ here at the first space after 50 characters.                            }
Function WrapTip(Text : String) : String;
Var
    I, LineLen : Integer;
    C, Wrapped : String;
Begin
    Wrapped := '';
    LineLen := 0;
    For I := 1 To Length(Text) Do
    Begin
        C := Copy(Text, I, 1);
        If (C = ' ') And (LineLen >= 50) Then
        Begin
            Wrapped := Wrapped + #13#10;
            LineLen := 0;
        End
        Else
        Begin
            Wrapped := Wrapped + C;
            LineLen := LineLen + 1;
        End;
    End;
    Result := Wrapped;
End;

Procedure SetTip(Ctl : TControl; Text : String);
Begin
    Try
        Ctl.Hint := WrapTip(Text);
        Ctl.ShowHint := True;
    Except End;
End;

Const
    TIP_OPENWEB_ON = 'Open the web dashboard in your browser: design review, '
        + 'components, nets, libraries and the plan for the open project.';
    TIP_OPENWEB_OFF = 'The web dashboard is not running. Start it with the '
        + 'command at the bottom of this window, then this button opens it.';
    TIP_PAUSE = 'Stop handling commands from the AI. New requests wait until '
        + 'you resume (the AI may give up on them if the pause is long), and '
        + 'the bridge does not switch itself off while paused.';
    TIP_RESUME = 'Start handling commands from the AI again.';
    TIP_SAVING_ON = 'Saving is allowed: the bridge saves documents when a '
        + 'command asks it to. Click to turn saving off, so edits stay '
        + 'unsaved in Altium until you save them yourself. Kept across restarts.';
    TIP_SAVING_OFF = 'Saving is off: the bridge refuses every save, and its '
        + 'edits stay unsaved in Altium until you save them yourself. Click '
        + 'to allow saving again. Kept across restarts.';

Procedure ApplyTips(Dummy : Integer);
Var
    Tip : String;
Begin
    Tip := 'What the bridge is doing now. Green: idle and ready. Amber: '
        + 'running a command, shown with its name and elapsed time. '
        + 'Grey: paused.';
    SetTip(pnl_StatusDot, Tip);  SetTip(lbl_Status, Tip);
    SetTip(lbl_Spinner, Tip);
    SetTip(lbl_Version, 'Version of the eda-agent scripts loaded in Altium.');

    Tip := 'How long this bridge session has been running in Altium.';
    SetTip(pnl_K1, Tip);  SetTip(lbl_LblUp, Tip);  SetTip(lbl_ValUp, Tip);
    Tip := 'Commands handled in this session, keep-alive pings included.';
    SetTip(pnl_K2, Tip);  SetTip(lbl_LblReq, Tip);  SetTip(lbl_ValReq, Tip);
    Tip := 'Total time Altium has spent running commands in this session. '
        + 'The rest of the time it is free for you to work.';
    SetTip(pnl_K3, Tip);  SetTip(lbl_LblMs, Tip);  SetTip(lbl_ValMs, Tip);
    Tip := 'Time left before the bridge stops on its own when no commands '
        + 'arrive. Every command starts the countdown again, and so does '
        + 'Renew. Amber under two minutes, red under 30 seconds; it does not '
        + 'run while paused.';
    SetTip(pnl_K4, Tip);  SetTip(lbl_LblStop, Tip);  SetTip(lbl_ValStop, Tip);

    Tip := 'The latest error or notice from the bridge.';
    SetTip(pnl_ErrBar, Tip);  SetTip(lbl_LastErr, Tip);

    SetTip(btn_OpenWeb, TIP_OPENWEB_OFF);  SetTip(ico_OpenWeb, TIP_OPENWEB_OFF);
    SetTip(btn_Pause, TIP_PAUSE);  SetTip(ico_Pause, TIP_PAUSE);
    SetTip(ico_Resume, TIP_RESUME);
    Tip := 'Start the auto-off countdown again, so the bridge stays '
        + 'connected for another full period.';
    SetTip(btn_Renew, Tip);  SetTip(ico_Renew, Tip);
    Tip := 'Empty the command log below. Altium, the Perf tab and the '
        + 'session totals are not affected.';
    SetTip(btn_ClearLog, Tip);  SetTip(ico_ClearLog, Tip);
    SetTip(btn_Saving, TIP_SAVING_ON);  SetTip(ico_Saving, TIP_SAVING_ON);
    Tip := 'Disconnect the AI from Altium: the bridge stops and this window '
        + 'closes. Nothing is saved, so unsaved edits stay open in Altium.';
    SetTip(btn_Detach, Tip);  SetTip(ico_Detach, Tip);

    Tip := 'Show only new log lines that contain this text (any case). '
        + 'Changing it clears the log.';
    SetTip(ico_Filter, Tip);  SetTip(edt_Filter, Tip);
    SetTip(chk_HidePings, 'Leave out the keep-alive pings the AI client '
        + 'sends every 30 seconds, so the log shows real work. Changing it '
        + 'clears the log.');
    SetTip(chk_OnlySlow, 'Show only commands that took 100 ms or more, '
        + 'and every error. Changing it clears the log.');
    SetTip(chk_OnTop, 'Keep this window in front of Altium''s other windows.');

    SetTip(tab_Log, 'Commands as they run, newest at the top: time taken '
        + 'in ms, request id and command.');
    SetTip(tab_Perf, 'Totals per command for this session: how many times '
        + 'it ran, and its average and longest time in ms.');
    Tip := 'Starts the web dashboard on its own, without an AI client. '
        + 'Copy it with the button on the right and run it in a terminal.';
    SetTip(lbl_FooterHint, Tip);  SetTip(lbl_LaunchCmd, Tip);
    SetTip(btn_CopyCmd, 'Copy the command that starts the web dashboard '
        + 'to the clipboard.');
    SetTip(btn_CopyLog, 'Copy the open tab to the clipboard: the command '
        + 'log on Log, the table of totals on Perf.');
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
            btn_OpenWeb.Color := COLOR_BG_ELEVATED;
            btn_OpenWeb.Font.Color := COLOR_TEXT_BODY;
            Try ico_OpenWeb.Font.Color := COLOR_TEXT_BODY; Except End;
            btn_OpenWeb.Cursor := crHandPoint;
            SetTip(btn_OpenWeb, TIP_OPENWEB_ON);
            SetTip(ico_OpenWeb, TIP_OPENWEB_ON);
        End
        Else
        Begin
            btn_OpenWeb.Color := COLOR_BG_CARD;
            btn_OpenWeb.Font.Color := COLOR_TEXT_FAINT;
            Try ico_OpenWeb.Font.Color := COLOR_TEXT_FAINT; Except End;
            btn_OpenWeb.Cursor := crDefault;
            SetTip(btn_OpenWeb, TIP_OPENWEB_OFF);
            SetTip(ico_OpenWeb, TIP_OPENWEB_OFF);
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
        Pnl.Caption := LabelText;
        If Checked Then
        Begin
            Pnl.Color := COLOR_BG_ELEVATED;
            Pnl.Font.Color := COLOR_TEXT_BODY;
        End
        Else
        Begin
            Pnl.Color := COLOR_BG_BASE;
            Pnl.Font.Color := COLOR_TEXT_MUTED;
        End;
    Except End;
End;

Procedure LeaveToggle(Pnl : TPanel; Checked : Boolean);
Begin
    Try
        If Checked Then Pnl.Color := COLOR_BG_ELEVATED
        Else Pnl.Color := COLOR_BG_BASE;
    Except End;
End;


{ A themed TPanel left on ParentBackground paints its parent's colour and
  ignores its own Color. Started through -RScriptingSystem:RunScript, every
  panel here drew in the base colour: no blue Open Dashboard, no red
  Detach, no status dot. Each panel owns its background from here on. }
Procedure OwnPanelColours(Pnl : TPanel);
Begin
    Try Pnl.ParentBackground := False; Except End;
End;

Procedure OwnAllPanelColours(Dummy : Integer);
Begin
    OwnPanelColours(pnl_Header);    OwnPanelColours(pnl_StatusDot);
    OwnPanelColours(pnl_KPIs);      OwnPanelColours(pnl_K1);
    OwnPanelColours(pnl_K2);        OwnPanelColours(pnl_K3);
    OwnPanelColours(pnl_K4);        OwnPanelColours(pnl_ErrBar);
    OwnPanelColours(btn_OpenWeb);
    OwnPanelColours(pnl_Controls);  OwnPanelColours(btn_Pause);
    OwnPanelColours(btn_Renew);     OwnPanelColours(btn_ClearLog);
    OwnPanelColours(btn_Detach);    OwnPanelColours(chk_HidePings);
    OwnPanelColours(chk_OnlySlow);  OwnPanelColours(chk_OnTop);
    OwnPanelColours(pnl_TabBar);    OwnPanelColours(tab_Log);
    OwnPanelColours(tab_Perf);      OwnPanelColours(pnl_Footer);
    OwnPanelColours(btn_CopyCmd);   OwnPanelColours(btn_Saving);
    OwnPanelColours(btn_CopyLog);
End;


{ Saving allowed: an ordinary button. Off: amber, so it cannot be missed }
{ while edits are piling up unsaved in Altium.                           }
Procedure PaintSavingSwitch(Dummy : Integer);
Begin
    Try
        If SavingOff Then
        Begin
            btn_Saving.Caption := 'Saving OFF';
            btn_Saving.Color := COLOR_ACCENT_AMBER;
            btn_Saving.Font.Color := $00111111;
            ico_Saving.Font.Color := $00111111;
            SetTip(btn_Saving, TIP_SAVING_OFF);
            SetTip(ico_Saving, TIP_SAVING_OFF);
        End
        Else
        Begin
            btn_Saving.Caption := 'Saving on';
            btn_Saving.Color := COLOR_BG_ELEVATED;
            btn_Saving.Font.Color := COLOR_TEXT_BODY;
            ico_Saving.Font.Color := COLOR_TEXT_BODY;
            SetTip(btn_Saving, TIP_SAVING_ON);
            SetTip(ico_Saving, TIP_SAVING_ON);
        End;
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

        OwnAllPanelColours(0);
        SetCheckCaption(chk_HidePings, HidePingsFlag, 'Hide pings');
        SetCheckCaption(chk_OnlySlow,  OnlySlowFlag,  'Slow only');
        SetCheckCaption(chk_OnTop,     AlwaysOnTopFlag, 'On top');
        ApplyTips(0);
        PaintSavingSwitch(0);
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
        ShowNotice('');
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
    ShowNotice('');
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
            Try ico_Pause.Visible := False; Except End;
            Try ico_Resume.Visible := True; Except End;
            SetTip(btn_Pause, TIP_RESUME);
            pnl_StatusDot.Color := COLOR_TEXT_FAINT;
            lbl_Status.Caption := 'paused';
            lbl_Status.Font.Color := COLOR_TEXT_MUTED;
        End
        Else
        Begin
            btn_Pause.Caption := 'Pause';
            btn_Pause.Color := COLOR_BG_ELEVATED;
            btn_Pause.Font.Color := COLOR_TEXT_BODY;
            Try ico_Resume.Visible := False; Except End;
            Try ico_Pause.Visible := True; Except End;
            SetTip(btn_Pause, TIP_PAUSE);
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
        ShowNotice('no dashboard running - copy the command below');
        Exit;
    End;
    Try
        SentinelPath := WorkspaceDir + 'open_dashboard.url';
        WriteFileContent(SentinelPath, 'http://127.0.0.1:8766/');
        ShowNotice('');
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


{ Copy whichever tab is showing, so a log or the timings can be pasted }
{ into a bug report.                                                   }
Procedure btn_CopyLogClick(Sender : TObject);
Begin
    Try
        If mmo_Perf.Visible Then
            Clipboard.AsText := mmo_Perf.Lines.Text
        Else
            Clipboard.AsText := mmo_Log.Lines.Text;
        btn_CopyLog.Caption := #59643;
    Except End;
End;

{ The tick shown after a copy turns back into the copy icon on leaving. }
Procedure btn_CopyLogLeave(Sender : TObject);
Begin Try btn_CopyLog.Caption := #59592; Except End; End;


{ Toggles ================================================================== }

Procedure chk_HidePingsClick(Sender : TObject);
Begin
    Try
        HidePingsFlag := Not HidePingsFlag;
        SetCheckCaption(chk_HidePings, HidePingsFlag, 'Hide pings');
        RebuildVisibleLog(0);
    Except End;
End;

Procedure chk_OnlySlowClick(Sender : TObject);
Begin
    Try
        OnlySlowFlag := Not OnlySlowFlag;
        SetCheckCaption(chk_OnlySlow, OnlySlowFlag, 'Slow only');
        RebuildVisibleLog(0);
    Except End;
End;

Procedure chk_OnTopClick(Sender : TObject);
Begin
    Try
        AlwaysOnTopFlag := Not AlwaysOnTopFlag;
        SetCheckCaption(chk_OnTop, AlwaysOnTopFlag, 'On top');
        ApplyAlwaysOnTop(0);
    Except End;
End;

{ The Saving switch. Turning it off stops every save the bridge makes, and }
{ the choice is written to the workspace so a restart keeps it.           }
Procedure btn_SavingClick(Sender : TObject);
Begin
    Try
        SavingOff := Not SavingOff;
        StoreSavingSwitch(0);
        PaintSavingSwitch(0);
        If SavingOff Then
            mmo_Log.Lines.Insert(0, '      saving switched OFF: the bridge writes no documents')
        Else
            mmo_Log.Lines.Insert(0, '      saving switched on');
    Except End;
End;

Procedure edt_FilterChange(Sender : TObject);
Begin
    Try FilterText := edt_Filter.Text; Except End;
    RebuildVisibleLog(0);
End;


{ Hover handlers ============================================================ }

Procedure btn_DetachEnter(Sender : TObject);
Begin Try btn_Detach.Color := $004040E0; Except End; End;
Procedure btn_DetachLeave(Sender : TObject);
Begin Try btn_Detach.Color := $003030C8; Except End; End;

Procedure btn_ClearLogEnter(Sender : TObject);
Begin Try btn_ClearLog.Color := COLOR_BG_HOVER; Except End; End;
Procedure btn_ClearLogLeave(Sender : TObject);
Begin Try btn_ClearLog.Color := COLOR_BG_ELEVATED; Except End; End;

Procedure btn_PauseEnter(Sender : TObject);
Begin
    Try
        If PausedFlag Then btn_Pause.Color := $0070B4F0
        Else btn_Pause.Color := COLOR_BG_HOVER;
    Except End;
End;
Procedure btn_PauseLeave(Sender : TObject);
Begin
    Try
        If PausedFlag Then btn_Pause.Color := COLOR_ACCENT_AMBER
        Else btn_Pause.Color := COLOR_BG_ELEVATED;
    Except End;
End;

Procedure btn_SavingEnter(Sender : TObject);
Begin
    Try
        If SavingOff Then btn_Saving.Color := $0070B4F0
        Else btn_Saving.Color := COLOR_BG_HOVER;
    Except End;
End;
Procedure btn_SavingLeave(Sender : TObject);
Begin
    PaintSavingSwitch(0);
End;

Procedure btn_RenewEnter(Sender : TObject);
Begin Try btn_Renew.Color := COLOR_BG_HOVER; Except End; End;
Procedure btn_RenewLeave(Sender : TObject);
Begin Try btn_Renew.Color := COLOR_BG_ELEVATED; Except End; End;

Procedure btn_OpenWebEnter(Sender : TObject);
Begin
    If OpenWebEnabled Then
        Try btn_OpenWeb.Color := COLOR_BG_HOVER; Except End;
End;
Procedure btn_OpenWebLeave(Sender : TObject);
Begin
    If OpenWebEnabled Then
        Try btn_OpenWeb.Color := COLOR_BG_ELEVATED; Except End
    Else
        Try btn_OpenWeb.Color := COLOR_BG_CARD; Except End;
End;

Procedure chk_HidePingsEnter(Sender : TObject);
Begin Try chk_HidePings.Color := COLOR_BG_HOVER; Except End; End;
Procedure chk_HidePingsLeave(Sender : TObject);
Begin LeaveToggle(chk_HidePings, HidePingsFlag); End;

Procedure chk_OnlySlowEnter(Sender : TObject);
Begin Try chk_OnlySlow.Color := COLOR_BG_HOVER; Except End; End;
Procedure chk_OnlySlowLeave(Sender : TObject);
Begin LeaveToggle(chk_OnlySlow, OnlySlowFlag); End;

Procedure chk_OnTopEnter(Sender : TObject);
Begin Try chk_OnTop.Color := COLOR_BG_HOVER; Except End; End;
Procedure chk_OnTopLeave(Sender : TObject);
Begin LeaveToggle(chk_OnTop, AlwaysOnTopFlag); End;

Procedure tab_LogEnter(Sender : TObject);
Begin
    Try
        If Not mmo_Log.Visible Then tab_Log.Color := $004A4A4A;
    Except End;
End;
Procedure tab_LogLeave(Sender : TObject);
Begin
    Try
        If mmo_Log.Visible Then tab_Log.Color := COLOR_BG_LOG
        Else tab_Log.Color := COLOR_BG_CARD;
    Except End;
End;

Procedure tab_PerfEnter(Sender : TObject);
Begin
    Try
        If Not mmo_Perf.Visible Then tab_Perf.Color := $004A4A4A;
    Except End;
End;
Procedure tab_PerfLeave(Sender : TObject);
Begin
    Try
        If mmo_Perf.Visible Then tab_Perf.Color := COLOR_BG_LOG
        Else tab_Perf.Color := COLOR_BG_CARD;
    Except End;
End;


Procedure tab_LogClick(Sender : TObject);
Begin
    Try
        mmo_Log.Visible := True;
        mmo_Perf.Visible := False;
        tab_Log.Color := COLOR_BG_LOG;
        tab_Log.Font.Color := COLOR_TEXT_BODY;
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
        tab_Perf.Color := COLOR_BG_LOG;
        tab_Perf.Font.Color := COLOR_TEXT_BODY;
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
    { Set by the first tick, which logs _tick_first. A session that logs  }
    { _session_start and never _tick_first armed a timer that never ran.  }
    PumpTicked           : Boolean;


{ UnitStamp_StatusForm - which build of StatusForm.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_StatusForm(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;

{ UnitStampsResponse - every unit's stamp, so app_ping can name a unit that   }
{ Altium is still running an older compile of. One version constant cannot:  }
{ a unit that fails to compile keeps serving its previous image while         }
{ Main.pas reads new.                                                         }
Function UnitStampsResponse(RequestId : String) : String;
Begin
    Result := BuildSuccessResponse(RequestId,
        '{"Main":"' + UnitStamp_Main(0) + '"'
        + ',"Utils":"' + UnitStamp_Utils(0) + '"'
        + ',"Application":"' + UnitStamp_Application(0) + '"'
        + ',"Project":"' + UnitStamp_Project(0) + '"'
        + ',"Library":"' + UnitStamp_Library(0) + '"'
        + ',"PCBGeneric":"' + UnitStamp_PCBGeneric(0) + '"'
        + ',"PCB":"' + UnitStamp_PCB(0) + '"'
        + ',"Generic":"' + UnitStamp_Generic(0) + '"'
        + ',"Audit":"' + UnitStamp_Audit(0) + '"'
        + ',"StatusForm":"' + UnitStamp_StatusForm(0) + '"}');
End;

{ The reply names the board it came from when GetPCBBoardAnywhere answered  }
{ from one that was not focused: with a library or a sheet in front, a      }
{ board found by walking the projects looks exactly like the one the caller }
{ meant. Only a success whose data is an object is touched.                 }
Function BoardFallbackNote(Response : String) : String;
Var
    Marker, Added, Front : String;
    P : Integer;
Begin
    Result := Response;
    If BoardFallbackPath = '' Then Exit;
    Marker := '"success":true,"data":' + Chr(123);
    P := Pos(Marker, Response);
    If P = 0 Then Exit;
    P := P + Length(Marker);
    Front := BoardFallbackFrom;
    If Front = '' Then Front := 'no document';
    Added := '"board_used":"' + EscapeJsonString(BoardFallbackPath) + '"'
        + ',"board_note":"No board was focused (' + EscapeJsonString(Front)
        + ' in front), so this used ' + EscapeJsonString(ExtractFileName(BoardFallbackPath))
        + ', the first board of the open projects."';
    If Copy(Response, P, 1) <> Chr(125) Then Added := Added + ',';
    Result := Copy(Response, 1, P - 1) + Added + Copy(Response, P, Length(Response));
End;

{ SavingRefusal - with the Saving switch off, the error for a command whose  }
{ whole job is writing a file, or whose close would drop unsaved edits. ''  }
{ when the command may run. Commands that only save on the side still run: }
{ SaveHeldBack stops their writes and SavingNote reports them.             }
Function SavingRefusal(Command : String; Params : String; RequestId : String) : String;
Var
    Named : String;
Begin
    Result := '';
    If Not SavingOff Then Exit;
    If (Command = 'application.save_all') Or (Command = 'project.save_all')
        Or (Command = 'application.save_document') Or (Command = 'project.save') Then
    Begin
        Result := SavingOffError(RequestId, 'This command saves documents.');
        Exit;
    End;
    If (Command = 'application.create_document') Or (Command = 'generic.add_sheet') Then
    Begin
        Result := SavingOffError(RequestId, 'A new document is created by writing its file.');
        Exit;
    End;
    If (Command = 'pcb.make_library') Or (Command = 'pcb.make_pad_via_library') Then
    Begin
        Result := SavingOffError(RequestId, 'The library is made by writing a new file.');
        Exit;
    End;
    If (Command = 'pcb.save_copy_as')
        And (LowerCase(ExtractJsonValue(Params, 'check_only')) <> 'true') Then
    Begin
        Result := SavingOffError(RequestId, 'A copy is made by saving the board to a new file.');
        Exit;
    End;
    If (Command = 'generic.simcfg_document')
        And (LowerCase(ExtractJsonValue(Params, 'action')) = 'save') Then
    Begin
        Result := SavingOffError(RequestId, 'This saves the simulation settings file.');
        Exit;
    End;
    If (Command = 'project.close') And (ExtractJsonValue(Params, 'save') <> 'false') Then
    Begin
        Result := SavingOffError(RequestId, 'Closing with save writes the project first; '
            + 'pass save=false to close without saving. Nothing was closed.');
        Exit;
    End;
    If Command = 'application.execute_menu' Then
    Begin
        If Pos('SAVE', UpperCase(ExtractJsonValue(Params, 'menu_path'))) > 0 Then
            Result := SavingOffError(RequestId, 'This menu item saves.');
        Exit;
    End;
    If (Command = 'application.run_process') Or (Command = 'generic.run_process') Then
    Begin
        Named := ExtractJsonValue(Params, 'process_name') + ExtractJsonValue(Params, 'process');
        If Pos('SAVE', UpperCase(Named)) > 0 Then
            Result := SavingOffError(RequestId, 'The process ' + Named + ' saves.');
    End;
End;

{ SavingNote - a command that edited a document while saving was off says  }
{ what it did not write: the edit is in Altium and nowhere on disk. Only a }
{ success whose data is an object is touched, as in BoardFallbackNote.      }
Function SavingNote(Response : String) : String;
Var
    Marker, Added, Names, Rest, Name : String;
    P, Bar : Integer;
Begin
    Result := Response;
    If SavesHeld = 0 Then Exit;
    Marker := '"success":true,"data":' + Chr(123);
    P := Pos(Marker, Response);
    If P = 0 Then Exit;
    P := P + Length(Marker);
    Names := '';
    Rest := HeldFiles;
    While Rest <> '' Do
    Begin
        Bar := Pos('|', Rest);
        If Bar = 0 Then
        Begin
            Name := Rest;
            Rest := '';
        End
        Else
        Begin
            Name := Copy(Rest, 1, Bar - 1);
            Rest := Copy(Rest, Bar + 1, Length(Rest));
        End;
        If Names <> '' Then Names := Names + ',';
        Names := Names + '"' + EscapeJsonString(Name) + '"';
    End;
    Added := '"saving_off":true,"not_saved":[' + Names + ']'
        + ',"saving_note":"Saving is switched off on the bridge dashboard, so '
        + 'these were not written. The edits are in Altium only."';
    If Copy(Response, P, 1) <> Chr(125) Then Added := Added + ',';
    Result := Copy(Response, 1, P - 1) + Added + Copy(Response, P, Length(Response));
End;

Function ProcessOne(Command : String; Params : String; RequestId : String) : String;
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

    { Answered here, after every unit it asks, rather than in Application.pas. }
    If Command = 'application.get_unit_stamps' Then
    Begin
        Result := UnitStampsResponse(RequestId);
        Exit;
    End;

    SheetsSeen := 0;
    SheetsSkipped := 0;
    LibScopeName := '';
    LibScopePart := 0;
    BoardFallbackPath := '';
    BoardFallbackFrom := '';
    BoardMissWhy := '';
    SavesHeld := 0;
    HeldFiles := '';
    Result := SavingRefusal(Command, Params, RequestId);
    If Result <> '' Then Exit;
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
    Result := SheetCoverage(Result);
    Result := BoardFallbackNote(Result);
    Result := SavingNote(Result);
End;

{..............................................................................}
{ Whether one item's reply is a failure. Every reply starts with its           }
{ protocol_version, id and success keys, in that order, so the first success   }
{ key is the envelope's own and not one inside its data.                       }
{..............................................................................}

Function ReplyFailed(Reply : String) : Boolean;
Var
    P : Integer;
Begin
    P := Pos('"success":', Reply);
    Result := (P = 0) Or (Copy(Reply, P + 10, 5) = 'false');
End;

{..............................................................................}
{ A bulk call: the params carry bulk_count and the items as bulk_item_0 to     }
{ bulk_item_<count-1>, each the parameters one call of the command takes. The  }
{ items run through ProcessOne in order inside ONE board edit, and the edits   }
{ on one schematic join up, so a bulk call is one undo step per document.      }
{ Every item's reply comes back whole, in order,                               }
{ under "items". The run stops at the first failed item unless bulk_continue   }
{ is true, and the items after it are reported as skipped.                     }
{..............................................................................}

Function ProcessBulk(Command : String; Params : String; RequestId : String) : String;
Var
    Count, I, Ran, Failed, Skipped, Depth : Integer;
    ItemParams, ItemReply, Replies, Category, SchOpen : String;
    KeepGoing, Stopped, OnBoard : Boolean;
Begin
    Count := StrToIntDef(ExtractJsonValue(Params, 'bulk_count'), 0);
    If Count <= 0 Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAMETER',
            'bulk_count must be a positive number of items.');
        Exit;
    End;
    If Copy(Command, 1, 12) = 'application.' Then
    Begin
        Result := BuildErrorResponse(RequestId, 'INVALID_PARAMETER',
            Command + ' runs once per call and takes no items.');
        Exit;
    End;
    KeepGoing := LowerCase(ExtractJsonValue(Params, 'bulk_continue')) = 'true';
    { Board, footprint library and object commands edit through PCBServer, }
    { so their items share one board edit.                                 }
    Category := Copy(Command, 1, Pos('.', Command) - 1);
    OnBoard := ((Category = 'pcb') Or (Category = 'generic')
        Or (Category = 'library')) And (PCBServer <> Nil);

    Replies := '';
    Ran := 0;
    Failed := 0;
    Skipped := 0;
    Stopped := False;
    If OnBoard Then PcbBeginEdit(0);
    { Schematic edits name their document, so they are joined as they }
    { come: see SchBeginEdit.                                         }
    SchBulkHold := True;
    Try
        For I := 0 To Count - 1 Do
        Begin
            If Replies <> '' Then Replies := Replies + ',';
            If Stopped Then
            Begin
                Replies := Replies + '{"success":false,"skipped":true}';
                Skipped := Skipped + 1;
                Continue;
            End;
            ItemParams := ExtractJsonValue(Params, 'bulk_item_' + IntToStr(I));
            If ItemParams = '' Then ItemParams := '{}';
            { A handler that exits before ending its edit leaves the count  }
            { up; one that ends an edit twice takes it to zero, and that    }
            { closed the shared edit, so it is opened again. Either way the }
            { next item finds the depth as this one did.                    }
            Depth := PcbEditDepth;
            SchOpen := SchEditOpen;
            ItemReply := ProcessOne(Command, ItemParams, RequestId);
            If OnBoard And (Depth > 0) And (PcbEditDepth <= 0) Then
                PCBServer.PreProcess;
            PcbEditDepth := Depth;
            SchEditOpen := SchOpen;
            Ran := Ran + 1;
            If ReplyFailed(ItemReply) Then
            Begin
                Failed := Failed + 1;
                If Not KeepGoing Then Stopped := True;
            End;
            Replies := Replies + ItemReply;
        End;
    Finally
        If OnBoard Then PcbEndEdit(0);
        SchBulkHold := False;
        SchReleaseHeld(0);
    End;

    Result := BuildSuccessResponse(RequestId,
        '{"bulk":true,"count":' + IntToStr(Count)
        + ',"ran":' + IntToStr(Ran)
        + ',"failed":' + IntToStr(Failed)
        + ',"skipped":' + IntToStr(Skipped)
        + ',"items":[' + Replies + ']}');
End;

Function ProcessCommand(Command : String; Params : String; RequestId : String) : String;
Begin
    { A handler that crashed mid-edit leaves the count up, and every later }
    { edit would then skip PreProcess. Each request starts from none open. }
    PcbEditDepth := 0;
    SchEditOpen := '|';
    SchBulkHold := False;
    SchReleaseHeld(0);
    If ExtractJsonValue(Params, 'bulk_count') <> '' Then
        Result := ProcessBulk(Command, Params, RequestId)
    Else
        Result := ProcessOne(Command, Params, RequestId);
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

{ SessionFocus - put the session's own document back in front, in the same }
{ turn of the loop as its command, so nothing can move it in between. Pin   }
{ is the session_doc_path its server sent. Returns the envelope field that }
{ says what happened, '' when nothing did. Only an open document is shown: }
{ a closed one is reported, never reopened, because reopening outside its  }
{ project makes it a free document.                                        }
Function SessionFocus(PinIn : String) : String;
Var
    Pin, Current, After : String;
    ServerDoc : IServerDocument;
Begin
    Result := '';
    { Both spelled one way: C:/x and C:\x name the same file but open as two }
    { documents, and a mismatch here would re-show it on every command.    }
    Pin := CanonicalPathValue('file_path', PinIn);
    Current := CanonicalPathValue('file_path', CurrentFocusedDocPath(0));
    If SessionFocusAction(Pin, Current) <> 'restore' Then Exit;
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(Pin); Except ServerDoc := Nil; End;
    If ServerDoc = Nil Then
    Begin
        Result := '"session_document":' + JsonObj(
            JsonStr('missing', Pin) + ',' + JsonStr('kept', Current) + ',' +
            JsonStr('note', 'This session''s document is no longer open, so '
                + 'the command ran on the one in front.'));
        Exit;
    End;
    Try Client.ShowDocument(ServerDoc); Except End;
    After := CanonicalPathValue('file_path', CurrentFocusedDocPath(0));
    If UpperCase(After) <> UpperCase(Pin) Then
    Begin
        Result := '"session_document":' + JsonObj(
            JsonStr('not_restored', Pin) + ',' + JsonStr('kept', After) + ',' +
            JsonStr('note', 'The focus had moved and this session''s '
                + 'document could not be put back, so the command ran on '
                + 'the one in front.'));
        Exit;
    End;
    Result := '"session_document":' + JsonObj(
        JsonStr('restored', Pin) + ',' + JsonStr('from', Current));
End;

Function ProcessSingleRequest(Dummy : Integer): Boolean;
Var
    RequestPath, RequestId : String;
    RequestContent, ResponseContent : String;
    Command, Params, ProtoVer, EnvelopeError : String;
    ExceptionMsg : String;
    FocusBefore, FocusAfter, SessionNote : String;
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
      them and this is the one place every command passes through.
      Taken AFTER the session's own document is put back, so the note
      below reports only what the handler itself moved. }
    SessionNote := SessionFocus(ExtractJsonValue(RequestContent, 'session_doc_path'));
    FocusBefore := CurrentFocusedDocPath(0);
    ResetNextStep(0);

    ExceptionMsg := '';
    { Heartbeat: write progress_<id>.json so Python can distinguish "still      }
    { working" from "polling loop dead" when the 10 s default deadline runs   }
    { out on a legitimately-slow handler. Delete only AFTER writing the       }
    { response, so at no point are both files missing.                        }
    StartProgress(RequestId);
    LastErrorId := '';
    LastErrorCode := '';
    LastErrorMsg := '';
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

        If SessionNote <> '' Then
            ResponseContent := AppendEnvelopeField(ResponseContent, SessionNote);

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
        { The message as the handler wrote it, when this reply is the error }
        { it built: the JSON copy has lost non-Latin text to '?' (#91).     }
        If (LastErrorId = RequestId) And (LastErrorCode = DashCode)
            And (LastErrorMsg <> '') Then
            DashDetail := LastErrorMsg;
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
            If Not PumpTicked Then
            Begin
                PumpTicked := True;
                AppendLog(FormatLogStamp(0) + ',0,_tick_first,interval='
                          + IntToStr(PumpInterval));
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
Var
    Armed : Boolean;
Begin
    If Running Then Exit;

    InitDefaultConfig(0);
    EnsureWorkspaceDir(0);
    LoadMCPConfig(0);
    LoadSavingSwitch(0);
    { Startup purge: nothing on disk can belong to a live exchange, because no
      pump was running to serve it. Responses are purged here but NOT in
      CleanupMCPServer(0) -- on shutdown a client may still be reading one. }
    CleanupOrphanRequests(0);
    CleanupOrphanResponses(0);
    CleanupOrphanProgress(0);

    { Script globals outlive a stop and a redeploy, but a design object    }
    { held in one does not: MEASURED, the first symbol write after a       }
    { relaunch read CurrentPartID off the old LastCreatedLibComponent and  }
    { stopped the loop on "Undeclared identifier". Start with none held.   }
    LastCreatedLibComponent := Nil;
    LastCreatedLibComponentName := '';

    Running := True;
    PumpStopPath := WorkspaceDir + 'stop';
    If FileExists(PumpStopPath) Then DeleteFile(PumpStopPath);

    PumpIdleCount := 0;
    PumpLastActivityMs := GetTickCount;
    PumpInTick := False;
    PumpFinalised := False;
    PumpQuitting := False;
    PumpTicked := False;

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
    Armed := False;
    Try
        tmr_Poll.Interval := PumpInterval;
        tmr_Poll.Enabled := True;
    Except End;
    { Read back: an arm that raised inside the Try used to leave a session }
    { that logged _session_start and then served nothing, with no sign.    }
    Try Armed := tmr_Poll.Enabled; Except Armed := False; End;
    If Not Armed Then
    Begin
        AppendLog(FormatLogStamp(0) + ',0,_arm_failed,the poll timer did not start');
        UpdateStatusHeader('MCP: poll timer did not start');
    End;
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
