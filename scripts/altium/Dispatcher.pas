{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Dispatcher.pas - The attach entry point. Compiles last.                       }
{                                                                              }
{ The polling loop and the per-request dispatcher USED to live here, as a      }
{ blocking `While Running Do` loop that held Altium's single-threaded          }
{ scripting engine for the whole session. Closing Altium while it ran HUNG     }
{ the application: the close request was dispatched inside the loop's own      }
{ Application.ProcessMessages, Altium's close path then blocked without        }
{ pumping the engine, and the script could never get back to its               }
{ Client.IsQuitting check. Measured as Windows AppHangB1 events (X2.EXE        }
{ Event 1002) with no matching "Shutdown Commenced" in DXP_Shutdown.log, so    }
{ the hang was BEFORE Altium's shutdown sequence, and with a session in        }
{ activity.log that never got its _session_end line.                           }
{                                                                              }
{ Dispatch now runs on a TTimer declared in StatusForm.dfm: the script returns }
{ to Altium between requests, so the close path is never waiting on it. A      }
{ form's event handler resolves ONLY within the form's own unit (measured: a   }
{ cross-unit handler never fired once, and failed silently), so the pump and   }
{ everything it calls had to move into StatusForm.pas along with it.           }
{                                                                              }
{ WHAT DID NOT CHANGE IS HOW THE USER STARTS IT. StartMCPServer is still a     }
{ parameterless procedure in this file, so the attach step is the same         }
{ File > Run Script... > Altium_API > Dispatcher.pas > StartMCPServer it has   }
{ always been, and every doc, recovery hint and the project's StartProcName    }
{ still name the same place. The real starter is StatusForm.StartMCPPump,      }
{ which takes a Dummy argument purely to stay out of the Run Script dialog.    }
{..............................................................................}

{..............................................................................}
{ Attach: hand off to the pump in StatusForm.pas and return.                   }
{                                                                              }
{ The return is the point. This used to block for the entire session; now the  }
{ scripting engine is free between timer ticks and Altium can close without    }
{ waiting on a script that never yields back.                                  }
{..............................................................................}

Procedure StartMCPServer;
Begin
    StartMCPPump(0);
End;

{..............................................................................}
{ Write the 'stop' file so a running server exits on its next tick.            }
{                                                                              }
{ HIDDEN FROM THE RUN SCRIPT DIALOG, because it cannot be useful there.       }
{ The scripting engine runs one script at a time, so while a request is being  }
{ dispatched there is no way to pick this out of the dialog and run it, and    }
{ when the pump is NOT running there is nothing to stop: the sentinel would    }
{ just sit there, and the pump deletes a stale one at startup anyway.          }
{                                                                              }
{ Nothing calls it. Python stops the pump with the application.stop_server     }
{ COMMAND, and the dashboard's Detach button sets Running := False directly,   }
{ which is the same result by a shorter route. It is kept rather than deleted  }
{ because the sentinel it writes is the documented out-of-band stop and a      }
{ future caller may want it; the argument keeps it out of a list of four       }
{ things a human is choosing between.                                          }
{..............................................................................}

Procedure StopMCPServer(Dummy : Integer);
Var
    StopPath : String;
    F : TextFile;
Begin
    EnsureWorkspaceDir(0);
    StopPath := WorkspaceDir + 'stop';
    Try
        AssignFile(F, StopPath);
        Rewrite(F);
        Writeln(F, '1');
        CloseFile(F);
        ShowMessage('MCP server stop signal sent. The server will exit within 500ms.');
    Except
        ShowMessage('Failed to write stop file: ' + StopPath);
    End;
End;
