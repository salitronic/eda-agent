{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>                                      }
{..............................................................................}
{ Main.pas - Constants, IPC primitives and JSON helpers for the Altium bridge   }
{ The script polls for request_<id>.json files, processes commands, writes      }
{ response_<id>.json with the matching ID. Per-request files eliminate the      }
{ stale-response race that the old single-file scheme had.                       }
{..............................................................................}

Const
    // Bump SCRIPT_VERSION whenever the .pas sources change. Python reads the
    // same string from the on-disk Main.pas and compares it to what ping
    // returns, mismatch means Altium is running a stale compiled script
    // (DelphiScript caches compiled units until the script project is
    // reopened or Altium is restarted).
    SCRIPT_VERSION = '2026.10.30.7';

    // How far up the mechanical layers a pair tidy looks. Altium allows 1024,
    // and checking every combination of those is a million probes for a stack
    // that in practice stops in the low tens. The bound is reported back so a
    // pair above it is known to have been skipped rather than judged clean.
    MechScanLimit = 64;

    // The colour Altium gives a net label by default: maroon, RGB(128, 0, 0),
    // packed as BGR. Every net label a tool places takes it.
    NET_LABEL_COLOR = 128;

    // Wire protocol version. Bumped whenever the request/response JSON shape
    // changes incompatibly. Python and Pascal must agree; mismatch returns
    // PROTOCOL_VERSION_MISMATCH on the Pascal side and raises on the Python
    // side. v2 introduced per-request IPC files, structured error.details,
    // and the protocol_version field itself.
    PROTOCOL_VERSION = 2;

    { Milliseconds during which SmartCompile reuses the previous DM_Compile    }
    { result instead of recompiling. Design-review snapshots fire 3-4 project  }
    { handlers back-to-back; each DM_Compile can be 5-10 s on a real design,   }
    { so bursts without this cache add up to 30-40 s of needless recompiles.   }
    COMPILE_CACHE_TTL_MS = 2000;

    { How long one Claude session may run commands back to back while      }
    { another session is waiting. See ScanForRequestFile. A session alone   }
    { is never slotted.                                                     }
    SESSION_SLOT_MS = 30000;

    CONFIG_FILE = 'mcp_config.json';
    SAVING_FILE = 'saving.json';

    // ISch_RobotManager SendMessage IDs (from Altium Schematic API docs).
    SCHM_PrimitiveRegistration = 1;
    SCHM_BeginModify           = 2;
    SCHM_EndModify             = 3;

    // DelphiScript does NOT predefine Delphi's MaxInt (raises "Undeclared
    // identifier" at runtime), so it is declared here. Used as a
    // "smallest seen so far" sentinel in board-statistics scans.
    //
    // NOT 2147483647. AD25 rejects that literal outright with "Invalid
    // constant" while compiling the script, which stops the loop before
    // it serves anything (issue #22, measured on AD25).
    //
    // 1e9 internal units is 100 inches, which is Altium's own maximum
    // board dimension, so a coordinate could in principle equal it. That
    // is acceptable here: the bounding-box scan carries its own Found
    // flag as the real guard, and the dimension sentinels it initialises
    // (track width, hole size, annular ring) are orders of magnitude
    // below it.
    MAX_INT = 1000000000;

    // TLibIdentifierKind in declaration order: 0 Any, 1 NameNoType,
    // 2 NameWithType, 3 FullPath, 4 VaultName (System API constants).
    // VaultName is how SchServer.LoadComponent addresses a part in a
    // Workspace. Written as a number so no enum name has to exist in
    // this host; an unknown identifier stops the script.
    LIB_IDENT_KIND_VAULT_NAME = 4;

Var
    WorkspaceDir : String;
    Running : Boolean;

    { The last error reply built, unescaped: request id, code and message.  }
    { The status window shows this instead of re-reading the JSON, which    }
    { turns non-Latin text into '?'. See BuildErrorResponseDetailed.        }
    LastErrorId, LastErrorCode, LastErrorMsg : String;

    { Polling tunables, defaults below, overridden by mcp_config.json at      }
    { startup via LoadMCPConfig. Single source of truth: the config file.     }
    PollIntervalActiveMs : Integer;
    PollIntervalIdleMs   : Integer;
    IdleThreshold        : Integer;
    AutoShutdownMs       : Cardinal;
    YieldIterations      : Integer;
    YieldEveryNActive    : Integer;

    { SmartCompile cache state. Tick of the last DM_Compile and the Project    }
    { pointer it was run against, so we only skip when the SAME project was    }
    { compiled recently. Reset to 0 / Nil at startup.                          }
    LastCompileTick : Cardinal;
    { Documents the last save pass actually reached. See
      SaveOneDocByDocRef. Reset by App_SaveAll before each pass. }
    SaveAttempts : Integer;
    { Libraries the last save pass left alone because no tool edited them,
      and their names. See SaveOneDocByDocRef. }
    LibsSkippedClean : Integer;
    SkippedLibNames : String;
    { File names, upper case, of every document a tool has flagged for
      saving this session, each wrapped in '|'. See NoteDocEdited. }
    EditedDocs : String;
    { Documents the current save pass has already handled, wrapped in '|'.
      A free document is reached once per project walk, and the second
      visit would report a library it had just written as left alone. }
    SavePassSeen : String;
    { 'edited' while application.stop_server saves: only documents the  
      editor marks modified or a tool recorded are written, and
      CleanLeftAlone counts the rest. '' (as at startup) is the forced
      flush app_save_all relies on. See SaveOneDocByDocRef. }
    SaveScope : String;
    CleanLeftAlone : Integer;
    { The dashboard's Saving switch. False (saving allowed) unless the     }
    { switch was turned off; kept in SAVING_FILE so it outlasts a restart. }
    { SavesHeld and HeldFiles count what the current command did not write. }
    SavingOff : Boolean;
    SavesHeld : Integer;
    HeldFiles : String;
    LastCompiledProject : IProject;
    { Sheets a project-wide read asked for in the current request, and how }
    { many were not loaded and so not read. Reset per request by           }
    { ProcessCommand; see ProjectSheet and SheetCoverage.                  }
    SheetsSeen : Integer;
    SheetsSkipped : Integer;
    { The board GetPCBBoardAnywhere answered from when none was focused,  }
    { and the kind of the document that was in front; reset per request   }
    { and put on the reply by BoardFallbackNote. BoardMissWhy says why it }
    { gave no board at all, for the NO_PCB refusal.                       }
    BoardFallbackPath : String;
    BoardFallbackFrom : String;
    BoardMissWhy : String;

    { How many board edits are open, and how many of them were opened by  }
    { a bulk call. Only the outermost PcbBeginEdit and PcbEndEdit reach   }
    { Altium, so a bulk call is one edit however many items it runs. See  }
    { PcbBeginEdit and RunBulk.                                           }
    PcbEditDepth : Integer;

    { The schematic and SchLib edits that are open, as '|' then one entry  }
    { per edit, 'R' (it called Altium) or 'N' (nested inside one that did) }
    { followed by the document path and '|'. While SchBulkHold is set, the }
    { last edit a bulk item closes stays open as SchHeldDoc, so the next   }
    { item on the same document joins it. See SchBeginEdit.               }
    SchEditOpen : String;
    SchBulkHold : Boolean;
    SchHeld : Boolean;
    SchHeldDoc : ISch_Document;
    SchHeldPath : String;

    { The symbol a lib_component scope names, and its part (0 = none     }
    { asked for), for the walk that follows. Set by ApplyLibComponentScope }
    { on every call, cleared per request by ProcessOne.                    }
    LibScopeName : String;
    LibScopePart : Integer;

    { Time slots between Claude sessions sharing this Altium. SlotSession  }
    { is the session tag whose slot is running, SlotStartTick when it      }
    { began, QueueNoteTick when queue.json was last written. See           }
    { ScanForRequestFile.                                                  }
    SlotSession : String;
    SlotStartTick : Integer;
    QueueNoteTick : Integer;

    { Silent cast-failure counter, incremented every time a defensive       }
    { Try/Except in an iteration helper swallows an interface cast that      }
    { ObjectId-checking should have ruled out. Surfaced via application.ping }
    { as cast_errors so a non-zero value at session end isn't invisible.     }
    CastErrorCount : Integer;

    { Tracks the most recently created/selected library component so the    }
    { Lib_Add* primitive helpers can target it directly. SchLib's           }
    { CurrentSchComponent setter is a no-op in DelphiScript, assigning to  }
    { it does not move the editor's selection, so primitives that read     }
    { CurrentSchComponent end up attaching to whatever the editor was       }
    { showing before (typically Component_1, the default empty placeholder).}
    { Storing the reference here gives us a working "current target" the    }
    { primitive helpers can trust.                                           }
    LastCreatedLibComponent : ISch_Component;

    { The name that reference was known by, recorded when it was set.

      ASKING THE COMPONENT ITS OWN NAME IS NOT AN OPTION HERE.
      Measured on AD26: reading LibReference off THIS global raised
      "Undeclared identifier: LibReference", and the modal took the
      polling loop with it because undeclared identifiers are not
      catchable and the Try/Except around it did nothing. One caller sat
      on that dialog for over two minutes.

      The property itself is fine. ScanLibForComponent reads it off
      every component it walks, ten lines earlier in the same lookup,
      and has always worked. What differs is the reference: this one is
      held ACROSS commands, and the document it belongs to may have been
      closed, reopened or re-imported since. A member read on a
      component that no longer exists is reported as an undeclared
      identifier rather than as a missing object, which is why it reads
      as an API error and is not one.

      So the reference is cleared whenever this script reopens the
      library, and the name is compared against this recorded string
      rather than against anything read back off the component. }
    LastCreatedLibComponentName : String;

{..............................................................................}
{ Initialise polling tunables to compile-time defaults. Called by the          }
{ dispatcher startup before LoadMCPConfig so a missing/corrupt config file    }
{ still leaves the loop with sane values.                                     }
{..............................................................................}

Procedure InitDefaultConfig(Dummy : Integer);
Begin
    PollIntervalActiveMs := 10;
    PollIntervalIdleMs   := 30;
    IdleThreshold        := 150;
    AutoShutdownMs       := 600000;  { 10 min }
    YieldIterations      := 5;
    YieldEveryNActive    := 5;
End;

{..............................................................................}
{ Batch tool helpers                                                            }
{                                                                               }
{ New-generation batch tools use '~~' (double tilde) as the operation           }
{ separator and ';' as field separator within an operation. '~~' doesn't       }
{ appear in Altium object names, filters, or property strings, so it's         }
{ unambiguous even when a single operation's property list contains '|'.       }
{                                                                               }
{ Defined in Main.pas so Library.pas and Generic.pas can both use them,       }
{ the Altium project compiles files in DesignN order (Main, ..., Library,     }
{ ..., Generic) and a callee must come earlier than its caller.               }
{..............................................................................}

Function NextBatchOp(Var Remaining : String) : String;
Var
    SepPos : Integer;
Begin
    Result := '';
    While Length(Remaining) > 0 Do
    Begin
        SepPos := Pos('~~', Remaining);
        If SepPos = 0 Then
        Begin
            Result := Remaining;
            Remaining := '';
            Exit;
        End;
        Result := Copy(Remaining, 1, SepPos - 1);
        Remaining := Copy(Remaining, SepPos + 2, Length(Remaining));
        If Result <> '' Then Exit;
    End;
End;

Function GetBatchField(Op : String; Key : String) : String;
Var
    Remaining, Field, FKey, FVal : String;
    SepPos, EqPos : Integer;
Begin
    Result := '';
    Remaining := Op;
    While Length(Remaining) > 0 Do
    Begin
        SepPos := Pos(';', Remaining);
        If SepPos = 0 Then
        Begin
            Field := Remaining;
            Remaining := '';
        End
        Else
        Begin
            Field := Copy(Remaining, 1, SepPos - 1);
            Remaining := Copy(Remaining, SepPos + 1, Length(Remaining));
        End;
        EqPos := Pos('=', Field);
        If EqPos > 0 Then
        Begin
            FKey := Copy(Field, 1, EqPos - 1);
            FVal := Copy(Field, EqPos + 1, Length(Field));
            If UpperCase(FKey) = UpperCase(Key) Then
            Begin
                Result := FVal;
                Exit;
            End;
        End;
    End;
End;

{ The value of an op's LAST field, everything after ';<key>='. GetBatchField  }
{ stops at the next ';', which cut a batch_modify set of Text=first;second    }
{ to Text=first. Python writes set last and refuses a ';' in the fields      }
{ before it, so the first ';set=' is the real one.                           }
Function GetBatchTail(Op : String; Key : String) : String;
Var
    Marker : String;
    P : Integer;
Begin
    Result := '';
    Marker := ';' + LowerCase(Key) + '=';
    P := Pos(Marker, LowerCase(Op));
    If P > 0 Then
        Result := Copy(Op, P + Length(Marker), Length(Op))
    Else If Pos(LowerCase(Key) + '=', LowerCase(Op)) = 1 Then
        Result := Copy(Op, Length(Key) + 2, Length(Op));
End;

{..............................................................................}
{ SmartCompile                                                                  }
{                                                                               }
{ Thin wrapper over Project.DM_Compile that skips the compile when the SAME     }
{ project was compiled less than COMPILE_CACHE_TTL_MS ago.                      }
{..............................................................................}

{ Probe whether any logical document in the project has been modified by    }
{ an out-of-band edit (typically: user clicked in Altium's UI between MCP   }
{ calls). If so the cached DM_Compile is stale even if it's within the     }
{ TTL window, force a fresh recompile so subsequent queries see the       }
{ new netlist / component set.                                             }
Function ProjectHasDirtyDocs(Project : IProject) : Boolean;
Var
    I : Integer;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    Readable : Boolean;
Begin
    { UNREADABLE IS NOT CLEAN. This used to swallow the exception and carry
      on, so a project whose documents could not be reached at all reported
      nothing dirty and the cached netlist was reused. Reading nothing and
      there being nothing are different answers and only one of them means
      the cache is still good.

      A Nil ServerDoc is the exception to that and stays clean on purpose:
      it means the document is not open in the editor, and a closed
      document cannot be holding unsaved edits. Treating it as dirty would
      force a recompile on every call for any project with a closed sheet,
      which is most of them. }
    Result := False;
    If Project = Nil Then Exit;
    For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Nil;
        Try
            Doc := Project.DM_LogicalDocuments(I);
        Except
            { Written out rather than swallowed, so the next reader can see
              that the failure is handled by the Nil check below and not
              simply ignored. }
            Doc := Nil;
        End;
        If Doc = Nil Then
        Begin
            { The project structure would not answer for one of its own
              documents. Say dirty and recompile rather than guess. }
            Result := True;
            Exit;
        End;

        ServerDoc := Nil;
        Readable := False;
        Try
            ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
            Readable := True;
        Except
            Readable := False;
        End;

        If Not Readable Then
        Begin
            Result := True;
            Exit;
        End;

        If ServerDoc <> Nil Then
        Begin
            Try
                If ServerDoc.Modified Then
                Begin
                    Result := True;
                    Exit;
                End;
            Except
                { Could not read the flag, so it is not evidence of clean. }
                Result := True;
                Exit;
            End;
        End;
    End;
End;

Procedure SmartCompile(Project : IProject);
Begin
    If Project = Nil Then Exit;
    // Honour the TTL window only when nothing in the project has changed
    // since the last compile. An external UI edit invalidates the cache
    // immediately, so the next MCP call after the user clicked "Add part"
    // sees the new state instead of a 2-second-stale snapshot.
    If (Project = LastCompiledProject) And (LastCompileTick > 0) And
       ((GetTickCount - LastCompileTick) < COMPILE_CACHE_TTL_MS) And
       (Not ProjectHasDirtyDocs(Project)) Then
        Exit;
    Project.DM_Compile;
    LastCompiledProject := Project;
    LastCompileTick := GetTickCount;
End;

{ Resolve the post-compile document set. After Project.DM_Compile the          }
{ flattened netlist (with hierarchy resolved through sheet symbols and sheet  }
{ entries) lives on DM_PhysicalDocuments, NOT on DM_LogicalDocuments. The      }
{ source-side per-sheet Pin.DM_FlattenedNetName values are NOT unified across  }
{ hierarchy boundaries, only the physical-document pin reads carry the flat   }
{ project-wide net names. Reference: Altium SDK Connectivity.pas              }
{ (DM_PhysicalDocuments walked after DM_Compile to harvest pin nets).         }
{                                                                              }
{ Returns the physical-document count when populated (the standard post-      }
{ compile case), falls back to logical for projects that haven't compiled or  }
{ have a single sheet so the caller keeps working in degenerate cases.         }
Procedure GetCompiledDocs(Project : IProject; Var DocCount : Integer; Var UsePhysical : Boolean);
Begin
    DocCount := 0;
    UsePhysical := False;
    If Project = Nil Then Exit;
    Try DocCount := Project.DM_PhysicalDocumentCount; Except End;
    If DocCount > 0 Then
    Begin
        UsePhysical := True;
        Exit;
    End;
    Try DocCount := Project.DM_LogicalDocumentCount; Except End;
End;

Function GetCompiledDoc(Project : IProject; Idx : Integer; UsePhysical : Boolean) : IDocument;
Begin
    Result := Nil;
    If Project = Nil Then Exit;
    If UsePhysical Then
        Try Result := Project.DM_PhysicalDocuments(Idx); Except End
    Else
        Try Result := Project.DM_LogicalDocuments(Idx); Except End;
End;

Procedure InvalidateCompileCache(Dummy : Integer);
Begin
    LastCompileTick := 0;
    LastCompiledProject := Nil;
End;

{..............................................................................}
{ ISch_RobotManager.SendMessage helpers.                                        }
{..............................................................................}

Procedure SchBeginModify(Obj : ISch_BasicContainer);
Begin
    If (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, Nil, SCHM_BeginModify, Nil);
End;

Procedure SchEndModify(Obj : ISch_BasicContainer);
Begin
    If (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(Obj.I_ObjectAddress, Nil, SCHM_EndModify, Nil);
End;

Procedure SchRegisterObject(Container, Obj : ISch_BasicContainer);
Begin
    If (Container <> Nil) And (Obj <> Nil) And (SchServer <> Nil) Then
        SchServer.RobotManager.SendMessage(
            Container.I_ObjectAddress, Nil, SCHM_PrimitiveRegistration,
            Obj.I_ObjectAddress);
End;

{..............................................................................}
{ Mark one document dirty by path. IT DOES NOT WRITE.                          }
{                                                                              }
{ Deferred save is deliberate: marking is cheap, and app_save_all flushes at   }
{ a checkpoint. The hazard is the NAME. Called SaveDocByPath it produced a     }
{ comment in Generic.pas claiming it wrote to disk, and three tool docstrings  }
{ promising the caller a save. A caller who needs bytes on disk must call      }
{ app_save_all or proj_save.                                                   }
{..............................................................................}

{..............................................................................}
{ Record that a tool edited a document, so app_save_all knows to write it.     }
{                                                                              }
{ A library opened only to read from used to be rewritten by every save-all,   }
{ because the save pass flushes every open document. MEASURED: a save-all      }
{ after copying symbols out of ten user libraries rewrote all ten, untouched.  }
{ Libraries are now written only when they appear here. The Modified flag      }
{ cannot stand in for this list, because it reads False while the editor tab   }
{ shows the document edited.                                                   }
{                                                                              }
{ Kept by FILE NAME, because DM_FullPath returns a bare name for a document    }
{ that is open outside any project. Two libraries with the same name in        }
{ different folders are both written, which errs on the side of saving.       }
{..............................................................................}

Procedure NoteDocEdited(FilePath : String);
Var
    Key : String;
Begin
    If FilePath = '' Then Exit;
    Key := '|' + UpperCase(ExtractFileName(FilePath)) + '|';
    If Pos(Key, EditedDocs) = 0 Then EditedDocs := EditedDocs + Key;
End;

Function DocWasEdited(FilePath : String) : Boolean;
Begin
    Result := False;
    If FilePath = '' Then Exit;
    Result := Pos('|' + UpperCase(ExtractFileName(FilePath)) + '|', EditedDocs) > 0;
End;

Procedure ForgetDocEdited(FilePath : String);
Var
    Key : String;
    P : Integer;
Begin
    If FilePath = '' Then Exit;
    Key := '|' + UpperCase(ExtractFileName(FilePath)) + '|';
    P := Pos(Key, EditedDocs);
    If P > 0 Then
        EditedDocs := Copy(EditedDocs, 1, P - 1)
            + Copy(EditedDocs, P + Length(Key), Length(EditedDocs));
End;

Function IsLibraryDocPath(FilePath : String) : Boolean;
Var
    Ext : String;
Begin
    Ext := UpperCase(ExtractFileExt(FilePath));
    Result := (Ext = '.SCHLIB') Or (Ext = '.PCBLIB');
End;

{..............................................................................}
{ Open and close a board edit. Only the outermost pair calls Altium's         }
{ PreProcess and PostProcess, so a bulk call that wraps its items in one pair }
{ makes every item's own pair part of the same edit. A single call works as   }
{ before: its pair is the outermost.                                          }
{..............................................................................}

Procedure PcbBeginEdit(Dummy : Integer);
Begin
    If PcbEditDepth <= 0 Then
    Begin
        PcbEditDepth := 0;
        PCBServer.PreProcess;
    End;
    PcbEditDepth := PcbEditDepth + 1;
End;

Procedure PcbEndEdit(Dummy : Integer);
Begin
    If PcbEditDepth > 0 Then PcbEditDepth := PcbEditDepth - 1;
    If PcbEditDepth = 0 Then PCBServer.PostProcess;
End;

{..............................................................................}
{ Open and close a schematic or SchLib edit. The schematic server takes the   }
{ document, so the open edits are kept per document path: an edit on a        }
{ document that already has one open is nested and does not reach Altium,    }
{ one on another document does. During a bulk call the last edit an item      }
{ closes is held open, and the next item on the same document joins it, so    }
{ the whole call is one undo step per document. RunBulk closes what is held.  }
{..............................................................................}

Function SchDocPath(Doc : ISch_Document) : String;
Begin
    Result := '';
    If Doc = Nil Then Exit;
    Try Result := Doc.DocumentName; Except End;
End;

Procedure SchReleaseHeld(Dummy : Integer);
Begin
    If Not SchHeld Then Exit;
    SchHeld := False;
    Try SchServer.ProcessControl.PostProcess(SchHeldDoc, 'Edit'); Except End;
    SchHeldDoc := Nil;
    SchHeldPath := '';
End;

{ Removes the last open edit on Path and returns its kind, 'R' or 'N', or '' }
{ when none is open on it.                                                   }
Function SchPopEdit(Path : String) : String;
Var
    Rest, Entry, Kept : String;
    P, N, Last : Integer;
Begin
    Result := '';
    { First the position of the last entry on Path, then the list without it. }
    Last := 0;
    N := 0;
    Rest := Copy(SchEditOpen, 2, Length(SchEditOpen));
    While Rest <> '' Do
    Begin
        P := Pos('|', Rest);
        If P = 0 Then P := Length(Rest) + 1;
        Entry := Copy(Rest, 1, P - 1);
        Rest := Copy(Rest, P + 1, Length(Rest));
        N := N + 1;
        If Copy(Entry, 2, Length(Entry)) = Path Then Last := N;
    End;
    If Last = 0 Then Exit;
    Kept := '';
    N := 0;
    Rest := Copy(SchEditOpen, 2, Length(SchEditOpen));
    While Rest <> '' Do
    Begin
        P := Pos('|', Rest);
        If P = 0 Then P := Length(Rest) + 1;
        Entry := Copy(Rest, 1, P - 1);
        Rest := Copy(Rest, P + 1, Length(Rest));
        N := N + 1;
        If N = Last Then
            Result := Copy(Entry, 1, 1)
        Else
            Kept := Kept + Entry + '|';
    End;
    SchEditOpen := '|' + Kept;
End;

Procedure SchBeginEdit(Doc : ISch_Document; Name : String);
Var
    Path : String;
Begin
    Path := SchDocPath(Doc);
    If SchEditOpen = '' Then SchEditOpen := '|';
    If (Pos('|R' + Path + '|', SchEditOpen) > 0)
        Or (Pos('|N' + Path + '|', SchEditOpen) > 0)
        Or (SchHeld And (SchHeldPath = Path)) Then
    Begin
        SchEditOpen := SchEditOpen + 'N' + Path + '|';
        Exit;
    End;
    { Another document: what a previous bulk item held is finished. }
    If SchHeld And (SchEditOpen = '|') Then SchReleaseHeld(0);
    SchServer.ProcessControl.PreProcess(Doc, Name);
    SchEditOpen := SchEditOpen + 'R' + Path + '|';
End;

Procedure SchEndEdit(Doc : ISch_Document; Name : String);
Var
    Path, Kind : String;
Begin
    Path := SchDocPath(Doc);
    If SchEditOpen = '' Then SchEditOpen := '|';
    Kind := SchPopEdit(Path);
    If Kind = 'N' Then Exit;
    If (Kind = 'R') And SchBulkHold And (SchEditOpen = '|') Then
    Begin
        If SchHeld And (SchHeldPath <> Path) Then SchReleaseHeld(0);
        SchHeld := True;
        SchHeldDoc := Doc;
        SchHeldPath := Path;
        Exit;
    End;
    { One more close than opens on the held document ends the held edit. }
    If (Kind = '') And SchHeld And (SchHeldPath = Path) Then
    Begin
        SchReleaseHeld(0);
        Exit;
    End;
    SchServer.ProcessControl.PostProcess(Doc, Name);
End;

{ SchEndEdit for a handler that reads back what Altium does AT the commit   }
{ (a UniqueId reminted at PostProcess): the edit reaches Altium now, even   }
{ when a bulk call would have held it. Nested in the same handler's own     }
{ edit on the document, it closes as SchEndEdit would.                      }
Procedure SchCommitEdit(Doc : ISch_Document; Name : String);
Var
    Path, Kind : String;
Begin
    Path := SchDocPath(Doc);
    If SchEditOpen = '' Then SchEditOpen := '|';
    Kind := SchPopEdit(Path);
    If (Pos('|R' + Path + '|', SchEditOpen) > 0)
        Or (Pos('|N' + Path + '|', SchEditOpen) > 0) Then Exit;
    If (Kind <> 'R') And SchHeld And (SchHeldPath = Path) Then
    Begin
        SchReleaseHeld(0);
        Exit;
    End;
    SchServer.ProcessControl.PostProcess(Doc, Name);
End;

Procedure MarkDocDirtyByPath(FilePath : String);
Var
    ServerDoc : IServerDocument;
Begin
    If FilePath = '' Then Exit;
    NoteDocEdited(FilePath);
    ServerDoc := Client.GetDocumentByPath(FilePath);
    If ServerDoc = Nil Then Exit;
    Try ServerDoc.SetModified(True); Except End;
End;

{..............................................................................}
{ Read that same flag back. The counterpart to MarkDocDirtyByPath, and the     }
{ one definition of "unsaved" for every handler that reports it.               }
{                                                                              }
{ A path with no resident IServerDocument reports False: a document that is    }
{ not open in the editor holds no unsaved edits. Treat a True as reliable and  }
{ a False as a floor, not a clean bill. App_SaveAll records that Modified does }
{ not always propagate from ProcessControl, which is why that handler measures }
{ file timestamps instead of trusting this.                                    }
{..............................................................................}

Function DocIsModified(FilePath : String) : Boolean;
Var
    ServerDoc : IServerDocument;
Begin
    Result := False;
    If FilePath = '' Then Exit;
    ServerDoc := Nil;
    Try
        ServerDoc := Client.GetDocumentByPath(FilePath);
        If ServerDoc <> Nil Then
            If ServerDoc.Modified Then Result := True;
    Except
        Result := False;
    End;
End;

{..............................................................................}
{ GetPCBBoardAnywhere - Focus-independent PCB board lookup.                    }
{                                                                              }
{ When no board is focused it answers from the first PcbDoc of the open        }
{ projects, and the reply then names that board (BoardFallbackNote), so a     }
{ caller can tell it from the one it meant. With a PcbLib in front the caller  }
{ is working on a library, not a board: it answers only when exactly one board }
{ is open, and with several gives Nil with the reason in BoardMissWhy, rather  }
{ than reading or writing whichever board the walk reached first.             }
{..............................................................................}

Function GetPCBBoardAnywhere(Dummy : Integer): IPCB_Board;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    ServerDoc : IServerDocument;
    PrevView : IServerDocumentView;
    Path, FocusKind, Candidates : String;
    I, P, Found : Integer;
Begin
    Result := Nil;

    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;

    { Fast path: a PCB tab is focused, so the PCB editor server is loaded    }
    { and PCBServer.GetCurrentPCBBoard resolves. Gate on the focused doc's   }
    { kind, read through the DM layer (which never loads a server). If no    }
    { PcbDoc has been opened this session the PCB server module is not       }
    { registered, and merely REFERENCING PCBServer.GetCurrentPCBBoard raises }
    { an uncatchable "Undeclared identifier" modal (late-bound against the   }
    { absent server) that Try/Except cannot swallow and that hangs the       }
    { polling loop. So only touch PCBServer when a PCB is actually focused.  }
    Doc := Workspace.DM_FocusedDocument;
    FocusKind := '';
    If Doc <> Nil Then
        Try FocusKind := UpperCase(Doc.DM_DocumentKind); Except End;
    If FocusKind = 'PCB' Then
    Begin
        Result := PCBServer.GetCurrentPCBBoard;
        If Result <> Nil Then Exit;
    End;

    { A library in front: a board is meant only when there is just one. }
    If FocusKind = 'PCBLIB' Then
    Begin
        Found := 0;
        Candidates := '';
        For P := 0 To Workspace.DM_ProjectCount - 1 Do
        Begin
            Project := Workspace.DM_Projects(P);
            If Project = Nil Then Continue;
            For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
            Begin
                Doc := Project.DM_LogicalDocuments(I);
                If Doc = Nil Then Continue;
                Path := '';
                Try Path := Doc.DM_FullPath; Except End;
                If Path = '' Then Continue;
                If (UpperCase(Doc.DM_DocumentKind) <> 'PCB') And
                   (Pos('.PCBDOC', UpperCase(Path)) <= 0) Then Continue;
                Found := Found + 1;
                If Candidates <> '' Then Candidates := Candidates + ', ';
                Candidates := Candidates + Path;
            End;
        End;
        If Found > 1 Then
        Begin
            BoardMissWhy := 'A PCB library is in front and ' + IntToStr(Found)
                + ' boards are open (' + Candidates + '), so no board was '
                + 'picked. For the library use the lib_ tools; for a board, '
                + 'focus it first.';
            Exit;
        End;
    End;

    { Fallback: no PCB tab is focused (the user is on a schematic, a library }
    { or the script project). Walk EVERY project open in the workspace - not }
    { just the focused one, which is often the script project with no PcbDoc }
    { - find the first PcbDoc, and open it. Opening the document loads the   }
    { PCB server and moves focus to it; a non-nil ServerDoc from            }
    { OpenDocument('PCB', ...) proves the server is now registered, so the   }
    { GetCurrentPCBBoard read below is safe. The IPCB_Board pointer stays    }
    { valid after we restore the user's previous view.                       }
    PrevView := Nil;
    Try
        If (Client <> Nil) Then PrevView := Client.GetCurrentView;
    Except End;

    For P := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Workspace.DM_Projects(P);
        If Project = Nil Then Continue;

        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            Path := '';
            Try Path := Doc.DM_FullPath; Except End;
            If Path = '' Then Continue;
            If (UpperCase(Doc.DM_DocumentKind) <> 'PCB') And
               (Pos('.PCBDOC', UpperCase(Path)) <= 0) Then Continue;

            ServerDoc := Nil;
            Try ServerDoc := Client.OpenDocument('PCB', Path); Except End;
            If ServerDoc = Nil Then Continue;

            Try Client.ShowDocument(ServerDoc); Except End;
            Try Result := PCBServer.GetCurrentPCBBoard; Except End;
            If Result <> Nil Then
            Begin
                BoardFallbackPath := Path;
                BoardFallbackFrom := FocusKind;
                Break;
            End;
        End;

        If Result <> Nil Then Break;
    End;

    { Restore the user's prior view so the focus blink is invisible. }
    If (PrevView <> Nil) And (Client <> Nil) Then
        Try Client.ShowDocument(PrevView.OwnerDocument); Except End;
End;

{..............................................................................}
{ ResolvePCBBoard - get a SPECIFIC board by path when several PcbDocs are     }
{ open. GetPCBBoardAnywhere returns the focused/current board, which is the   }
{ WRONG one when the caller meant a different (e.g. newly created) board.     }
{ PCBServer.GetPCBBoardByPath is undeclared on this build, so the target is   }
{ shown (which focuses it) and GetCurrentPCBBoard read. Empty path means      }
{ GetPCBBoardAnywhere.                                                        }
{                                                                             }
{ A loaded document is shown; an unloaded one is opened only when it is a    }
{ document of an open project or a file that exists. A mangled path (no such }
{ file) once opened as a "free document" and left the project unfocused. A   }
{ path that does not resolve gives Nil, never some other board, and a board  }
{ that did not come to the front is Nil too, so the caller reports it.       }
{..............................................................................}

Function ResolvePCBBoard(Path : String) : IPCB_Board;
Var
    ServerDoc : IServerDocument;
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    DocPath : String;
    I, P : Integer;
    InProject : Boolean;
Begin
    Result := Nil;
    If Path = '' Then
    Begin
        Result := GetPCBBoardAnywhere(0);
        Exit;
    End;
    ServerDoc := Nil;
    Try ServerDoc := Client.GetDocumentByPath(Path); Except ServerDoc := Nil; End;
    If ServerDoc = Nil Then
    Begin
        InProject := False;
        Try Workspace := GetWorkspace; Except Workspace := Nil; End;
        If Workspace <> Nil Then
            For P := 0 To Workspace.DM_ProjectCount - 1 Do
            Begin
                Project := Workspace.DM_Projects(P);
                If Project = Nil Then Continue;
                For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
                Begin
                    Doc := Project.DM_LogicalDocuments(I);
                    If Doc = Nil Then Continue;
                    DocPath := '';
                    Try DocPath := Doc.DM_FullPath; Except End;
                    If UpperCase(DocPath) = UpperCase(Path) Then InProject := True;
                End;
            End;
        { A file in no project opens as a free document, which it is   }
        { anyway (a scratch copy); a path naming no file is refused.   }
        If Not InProject Then
            If Not FileExists(Path) Then Exit;
        Try ServerDoc := Client.OpenDocument('PCB', Path); Except ServerDoc := Nil; End;
        If ServerDoc = Nil Then Exit;
    End;
    Try Client.ShowDocument(ServerDoc); Except End;
    Try Result := PCBServer.GetCurrentPCBBoard; Except Result := Nil; End;
    If Result <> Nil Then
        If UpperCase(Result.FileName) <> UpperCase(Path) Then Result := Nil;
End;

{ The path of the board in front, '' when none is. For a check that a focus }
{ took; it is focus-dependent on purpose.                                    }
Function FrontPCBBoardPath(Dummy : Integer) : String;
Var
    Board : IPCB_Board;
Begin
    Result := '';
    Try Board := PCBServer.GetCurrentPCBBoard; Except Board := Nil; End;
    If Board <> Nil Then
        Try Result := Board.FileName; Except Result := ''; End;
End;

{..............................................................................}
{ GetPCBBoardForMutation - the board an EDIT is allowed to touch.              }
{                                                                              }
{ GetPCBBoardAnywhere WANDERS, and that is correct for a read. When no PcbDoc  }
{ is focused it walks every open project, opens the first board it finds,      }
{ reads it, and restores the previous view so the focus change is invisible.   }
{ For a query that is a convenience. For a DELETE it is a misfire: with a      }
{ library focused and two boards open, obj_delete would remove primitives from }
{ whichever board the walk reached first, and hide the fact that it had        }
{ switched documents to do it.                                                 }
{                                                                              }
{ So an edit gets a board only when the target is UNAMBIGUOUS: a PcbDoc is     }
{ focused, or exactly one is open anywhere. Two open and none focused is       }
{ refused, and the refusal names them, because picking one is a guess the      }
{ caller has to make rather than one this should make silently.                }
{                                                                              }
{ Why carries the reason when the Result is Nil.                               }
{..............................................................................}

Function GetPCBBoardForMutation(Var Why : String) : IPCB_Board;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    Path, Candidates, OnePath : String;
    I, P, Found : Integer;
Begin
    Result := Nil;
    Why := '';

    Workspace := GetWorkspace;
    If Workspace = Nil Then
    Begin
        Why := 'No workspace is open.';
        Exit;
    End;

    { A focused PcbDoc is unambiguous, whatever else is open. }
    Doc := Workspace.DM_FocusedDocument;
    If (Doc <> Nil) And (UpperCase(Doc.DM_DocumentKind) = 'PCB') Then
    Begin
        Result := PCBServer.GetCurrentPCBBoard;
        If Result <> Nil Then Exit;
    End;

    Found := 0;
    Candidates := '';
    OnePath := '';
    For P := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Workspace.DM_Projects(P);
        If Project = Nil Then Continue;
        For I := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Project.DM_LogicalDocuments(I);
            If Doc = Nil Then Continue;
            Path := '';
            Try Path := Doc.DM_FullPath; Except End;
            If Path = '' Then Continue;
            If (UpperCase(Doc.DM_DocumentKind) <> 'PCB') And
               (Pos('.PCBDOC', UpperCase(Path)) <= 0) Then Continue;
            Found := Found + 1;
            OnePath := Path;
            If Candidates <> '' Then Candidates := Candidates + ', ';
            Candidates := Candidates + Path;
        End;
    End;

    If Found = 0 Then
    Begin
        Why := 'No PCB document is open, so there is nothing to edit.';
        Exit;
    End;

    If Found > 1 Then
    Begin
        Why := 'This edits a board, and ' + IntToStr(Found)
            + ' are open with none of them focused: ' + Candidates
            + '. Refusing rather than picking one. Focus the board you '
            + 'mean, or name it with board_path. A library being in front '
            + 'does NOT make this edit apply to the library.';
        Exit;
    End;

    { Exactly one board open. Opening it is safe because there is no other }
    { one it could have meant.                                             }
    Result := ResolvePCBBoard(OnePath);
    If Result = Nil Then
        Why := 'The only open board, ' + OnePath + ', could not be opened.';
End;

{ SaveHeldBack - True when the dashboard's Saving switch is off, in which   }
{ case the caller must not write Path. Every save the bridge makes asks    }
{ this first. Each one held back is counted for the reply, so a command    }
{ that edited something says its edit is still only in Altium.             }
Function SaveHeldBack(Path : String) : Boolean;
Var
    Name : String;
Begin
    Result := SavingOff;
    If Not Result Then Exit;
    SavesHeld := SavesHeld + 1;
    Name := ExtractFileName(Path);
    If (Name <> '') And (Pos('|' + Name + '|', '|' + HeldFiles + '|') = 0) Then
    Begin
        If HeldFiles <> '' Then HeldFiles := HeldFiles + '|';
        HeldFiles := HeldFiles + Name;
    End;
End;

{ Save every modified IServerDocument the workspace knows about, both     }
{ project-attached docs and free-floating docs (libraries opened           }
{ standalone, scratch docs). Free docs live inside the synthetic           }
{ DM_FreeDocumentsProject which we iterate like any normal project.        }
Procedure SaveOneDocByDocRef(Doc : IDocument);
Var
    ServerDoc : IServerDocument;
    Path : String;
    AgeBefore, AgeAfter : Integer;
Begin
    If Doc = Nil Then Exit;
    Try
        { Only a document OPEN in the editor has an IServerDocument. A
          closed project member returns Nil and is skipped, correctly: it
          cannot be holding unsaved edits. SaveAttempts counts the ones
          actually reached, so a pass that wrote nothing because nothing
          was open is not confused with one the editor declined. }
        Path := Doc.DM_FullPath;
        ServerDoc := Client.GetDocumentByPath(Path);
    Except ServerDoc := Nil; End;
    If ServerDoc = Nil Then Exit;
    If Pos('|' + UpperCase(Path) + '|', SavePassSeen) > 0 Then Exit;
    SavePassSeen := SavePassSeen + '|' + UpperCase(Path) + '|';

        { A LIBRARY NO TOOL EDITED IS LEFT ALONE. Libraries are opened to
          read from and copy out of far more often than they are edited, and
          the forced flush below rewrote every one of them. See
          NoteDocEdited. }
        If IsLibraryDocPath(Path) And (Not DocWasEdited(Path)) Then
        Begin
            LibsSkippedClean := LibsSkippedClean + 1;
            If SkippedLibNames <> '' Then SkippedLibNames := SkippedLibNames + '|';
            SkippedLibNames := SkippedLibNames + ExtractFileName(Path);
            Exit;
        End;

        { STOPPING THE BRIDGE WRITES ONLY WHAT WAS EDITED. The forced flush   }
        { below rewrote every clean schematic open in Altium on each detach. }
        { An edit the Modified flag missed is not lost by skipping it: the   }
        { editor still holds it and asks on close.                           }
        If SaveScope = 'edited' Then
        Begin
            If (Not ServerDoc.Modified) And (Not DocWasEdited(Path)) Then
            Begin
                CleanLeftAlone := CleanLeftAlone + 1;
                Exit;
            End;
        End;

        { Saving switched off: nothing is written, and only a document with }
        { something to write is reported as held back.                      }
        If SavingOff Then
        Begin
            If ServerDoc.Modified Or DocWasEdited(Path) Then
                If SaveHeldBack(Path) Then Exit;
            Exit;
        End;

        { Unconditional flush, Modified flag does not always propagate from   }
        { ProcessControl.PostProcess to the IServerDocument layer in newer    }
        { Altium builds, so checking it can leave docs unsaved. DoFileSave    }
        { on a clean doc is a fast no-op.                                     }
        SaveAttempts := SaveAttempts + 1;
        AgeBefore := -1;
        Try AgeBefore := FileAge(Path); Except AgeBefore := -1; End;
        Try ServerDoc.SetModified(True); Except End;
        Try ServerDoc.DoFileSave(''); Except End;
        { Once written, a document has nothing pending until the next edit. }
        { Any kind, not only libraries: a stop saves what was edited, and a  }
        { sheet left marked would be rewritten by every later stop.          }
        AgeAfter := -1;
        Try AgeAfter := FileAge(Path); Except AgeAfter := -1; End;
        If (AgeAfter >= 0) And (AgeAfter <> AgeBefore) Then ForgetDocEdited(Path);
End;

{ Write the project FILE itself. Adding or removing a document changes the  }
{ project in memory without marking it modified, so a save that waits for   }
{ the flag never writes the .PrjPcb and the change is gone after a reopen   }
{ (issue #94; measured: two documents added, proj_save, file listed none).  }
{ Called where membership changes, and by proj_save for the named project;  }
{ never by the save-everything passes, which must not rewrite projects      }
{ nobody touched.                                                           }
Function SaveProjectFile(Project : IProject) : Boolean;
Var
    ProjDoc : IServerDocument;
    Path : String;
Begin
    Result := False;
    If Project = Nil Then Exit;
    Path := '';
    Try Path := Project.DM_ProjectFullPath; Except End;
    If (Path = '') Or (Not FileExists(Path)) Then Exit;
    If SaveHeldBack(Path) Then Exit;
    ProjDoc := Nil;
    Try ProjDoc := Client.GetDocumentByPath(Path); Except End;
    If ProjDoc = Nil Then Exit;
    Try ProjDoc.SetModified(True); Except End;
    Try
        ProjDoc.DoFileSave('');
        Result := True;
    Except
        Result := False;
    End;
End;

Procedure SaveProjectMembers(Project : IProject);
Var
    J : Integer;
    ProjectServerDoc : IServerDocument;
Begin
    If Project = Nil Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        SaveOneDocByDocRef(Project.DM_LogicalDocuments(J));
    // The project file itself, when it's a real on-disk project
    Try
        ProjectServerDoc := Client.GetDocumentByPath(Project.DM_ProjectFullPath);
        If (ProjectServerDoc <> Nil) And ProjectServerDoc.Modified Then
            If Not SaveHeldBack(Project.DM_ProjectFullPath) Then
            Begin
                Try ProjectServerDoc.DoFileSave(''); Except End;
            End;
    Except End;
End;

{ CountDirtyInProject / CountDirtyDocuments - how many documents are STILL     }
{ unsaved. The only way to tell a save that worked from one Altium refused.    }
{                                                                              }
{ MEASURED on AD26: app_save_all returned saved:true while Altium was raising  }
{ "A command is currently active and save cannot be completed at this time"    }
{ once per dirty document. Every one of those saves was declined, and the tool }
{ still reported success, because it only checked that SaveAllDirty had not    }
{ raised. DoFileSave does not raise when the editor refuses.                   }
Function CountDirtyInProject(Project : IProject) : Integer;
Var
    J : Integer;
    Doc : IDocument;
    ServerDoc : IServerDocument;
Begin
    Result := 0;
    If Project = Nil Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc <> Nil Then
        Begin
            Try
                ServerDoc := Client.GetDocumentByPath(Doc.DM_FullPath);
                If (ServerDoc <> Nil) And ServerDoc.Modified Then
                    Result := Result + 1;
            Except End;
        End;
    End;
End;

Function CountDirtyDocuments(Dummy : Integer): Integer;
Var
    Workspace : IWorkspace;
    I : Integer;
Begin
    Result := 0;
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
        Result := Result + CountDirtyInProject(Workspace.DM_Projects(I));
    Try
        Result := Result + CountDirtyInProject(Workspace.DM_FreeDocumentsProject);
    Except End;
End;

{ ResolveLoadedDocPath - turn a bare document name into an ABSOLUTE path.     }
{                                                                             }
{ DM_FullPath AND DocumentName BOTH RETURN A BARE BASENAME for a free         }
{ document, one that is open but not a member of any project. Anything that   }
{ feeds that string to WorkspaceManager:OpenObject / CloseObject, or to        }
{ CreateLibCompInfoReader, gets a silent no-op or a reader for the wrong      }
{ file, because those all want a real path.                                   }
{                                                                             }
{ MEASURED: app_get_active_document and lib_get_component_details both        }
{ reported "SWEEP_A.SchLib" for a document whose actual path is under the     }
{ scratch directory, while the same call given an explicit library_path       }
{ reported the full path. A reopen built on the basename did nothing at all   }
{ and the failure looked like a lookup bug.                                   }
{                                                                             }
{ Returns '' when no absolute path can be found, so a caller can REFUSE       }
{ rather than proceed with a string that will quietly do nothing.             }
Function LooksAbsolutePath(P : String) : Boolean;
Begin
    Result := (Copy(P, 2, 1) = ':') Or (Copy(P, 1, 2) = '\\');
End;

Function MatchDocPathInProject(Project : IProject; Wanted : String) : String;
Var
    J : Integer;
    Doc : IDocument;
    Full : String;
Begin
    Result := '';
    If Project = Nil Then Exit;
    For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
    Begin
        Doc := Project.DM_LogicalDocuments(J);
        If Doc <> Nil Then
        Begin
            Full := '';
            Try Full := Doc.DM_FullPath; Except End;
            If LooksAbsolutePath(Full)
                And (UpperCase(ExtractFileName(Full)) = UpperCase(Wanted)) Then
            Begin
                Result := Full;
                Exit;
            End;
        End;
    End;
End;

Function ResolveLoadedDocPath(NameOrPath : String) : String;
Var
    Workspace : IWorkspace;
    Wanted : String;
    I : Integer;
Begin
    Result := '';
    If NameOrPath = '' Then Exit;
    If LooksAbsolutePath(NameOrPath) Then
    Begin
        Result := NameOrPath;
        Exit;
    End;

    Wanted := ExtractFileName(NameOrPath);
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;

    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Result := MatchDocPathInProject(Workspace.DM_Projects(I), Wanted);
        If Result <> '' Then Exit;
    End;

    { Free documents live in the synthetic FreeDocumentsProject, and they are }
    { precisely the ones that report a basename, so this is the branch that   }
    { usually answers.                                                        }
    Try
        Result := MatchDocPathInProject(Workspace.DM_FreeDocumentsProject, Wanted);
    Except End;
End;

{ DocFullPath - the absolute path of a document, never its bare name.         }
{                                                                             }
{ DM_FileName is the file NAME. MEASURED on AD 26.10.1.6: a project member    }
{ came back as "CloseSheet.SchDoc", and two handlers passed that on as        }
{ file_path. A caller got a path that was not one, and a modified lookup on   }
{ it could never resolve. DM_FullPath first, the name only as a last resort,  }
{ and a bare result resolved against the open projects.                       }
Function DocFullPath(Doc : IDocument) : String;
Var
    P, Resolved : String;
Begin
    Result := '';
    If Doc = Nil Then Exit;
    P := '';
    Try P := Doc.DM_FullPath; Except P := ''; End;
    If P = '' Then
    Begin
        Try P := Doc.DM_FileName; Except P := ''; End;
    End;
    If (P <> '') And (Not LooksAbsolutePath(P)) Then
    Begin
        Resolved := ResolveLoadedDocPath(P);
        If Resolved <> '' Then P := Resolved;
    End;
    Result := P;
End;

{..............................................................................}
{ Did the save actually write anything.                                        }
{                                                                              }
{ app_save_all used to answer this with CountDirtyDocuments, which walks the   }
{ workspace exactly the way SaveAllDirty does: same GetWorkspace, same         }
{ DM_Projects loop, same silent Exit when the workspace is Nil. So when the    }
{ enumeration came back empty the save wrote nothing AND the check counted     }
{ nothing, and zero was reported as success. A verifier that shares the        }
{ failure mode of the thing it verifies cannot catch it.                       }
{                                                                              }
{ It also read ServerDoc.Modified, which SaveOneDocByDocRef already documents  }
{ as unreliable: the flag does not always propagate from                       }
{ ProcessControl.PostProcess to the IServerDocument layer on newer builds.     }
{ MEASURED: dirty_doc_count 0 immediately after a wire placement and an entry  }
{ move, and 29 property edits lost on reload while app_save_all reported       }
{ saved:true throughout.                                                       }
{                                                                              }
{ FILE TIMESTAMPS ARE THE GROUND TRUTH. A document either got newer on disk    }
{ or it did not, and that answer does not depend on any Altium flag.           }
{..............................................................................}

Function WorkspaceDocPaths(Dummy : Integer) : String;
Var
    Workspace : IWorkspace;
    Project : IProject;
    Doc : IDocument;
    I, J : Integer;
    Path : String;
Begin
    Result := '';
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
    Begin
        Project := Nil;
        Try Project := Workspace.DM_Projects(I); Except End;
        If Project = Nil Then Continue;
        For J := 0 To Project.DM_LogicalDocumentCount - 1 Do
        Begin
            Doc := Nil;
            Try Doc := Project.DM_LogicalDocuments(J); Except End;
            If Doc = Nil Then Continue;
            Path := '';
            Try Path := Doc.DM_FullPath; Except End;
            If Path <> '' Then
            Begin
                If Result <> '' Then Result := Result + '|';
                Result := Result + Path;
            End;
        End;
    End;
End;

{ File age for each pipe-separated path, as its own pipe-separated list.       }
{ A path that does not exist yet reports -1, which simply cannot match a       }
{ later age and therefore counts as written once it appears.                   }

Function AgesForPaths(PathList : String) : String;
Var
    Remaining, Path : String;
    P, Age : Integer;
Begin
    Result := '';
    Remaining := PathList;
    While Remaining <> '' Do
    Begin
        P := Pos('|', Remaining);
        If P > 0 Then
        Begin
            Path := Copy(Remaining, 1, P - 1);
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P);
        End
        Else
        Begin
            Path := Remaining;
            Remaining := '';
        End;
        Age := -1;
        Try Age := FileAge(Path); Except Age := -1; End;
        If Result <> '' Then Result := Result + '|';
        Result := Result + IntToStr(Age);
    End;
End;

{ How many entries differ between two age lists of the same shape.             }

Function CountChangedAges(BeforeList : String; AfterList : String) : Integer;
Var
    RemA, RemB, A, B : String;
    P : Integer;
Begin
    Result := 0;
    RemA := BeforeList;
    RemB := AfterList;
    While (RemA <> '') And (RemB <> '') Do
    Begin
        P := Pos('|', RemA);
        If P > 0 Then
        Begin
            A := Copy(RemA, 1, P - 1);
            RemA := Copy(RemA, P + 1, Length(RemA) - P);
        End
        Else
        Begin
            A := RemA;
            RemA := '';
        End;

        P := Pos('|', RemB);
        If P > 0 Then
        Begin
            B := Copy(RemB, 1, P - 1);
            RemB := Copy(RemB, P + 1, Length(RemB) - P);
        End
        Else
        Begin
            B := RemB;
            RemB := '';
        End;

        If A <> B Then Result := Result + 1;
    End;
End;

Function CountPathEntries(PathList : String) : Integer;
Var
    Remaining : String;
    P : Integer;
Begin
    Result := 0;
    Remaining := PathList;
    While Remaining <> '' Do
    Begin
        Result := Result + 1;
        P := Pos('|', Remaining);
        If P > 0 Then
            Remaining := Copy(Remaining, P + 1, Length(Remaining) - P)
        Else
            Remaining := '';
    End;
End;

Procedure SaveAllDirty(Dummy : Integer);
Var
    Workspace : IWorkspace;
    I : Integer;
Begin
    Workspace := GetWorkspace;
    If Workspace = Nil Then Exit;
    SavePassSeen := '';
    LibsSkippedClean := 0;
    SkippedLibNames := '';

    // Real projects + their member docs
    For I := 0 To Workspace.DM_ProjectCount - 1 Do
        SaveProjectMembers(Workspace.DM_Projects(I));

    // Free documents live inside the synthetic FreeDocumentsProject
    Try SaveProjectMembers(Workspace.DM_FreeDocumentsProject); Except End;
End;

{..............................................................................}
{ Resolve workspace directory.                                                  }
{                                                                                }
{ DelphiScript has no access to environment variables, so the Python side       }
{ writes the absolute workspace path to a pointer file at a fixed location:    }
{     C:\ProgramData\eda-agent\workspace-path.txt                            }
{ This script reads that file. Python writes it at MCP-server startup and      }
{ whenever `eda-agent install-scripts` runs, so by the time this script      }
{ needs the workspace the pointer is always current.                            }
{                                                                                }
{ Fallback (pointer missing): C:\EDA Agent\workspace\                        }
{..............................................................................}

Function ResolveDefaultWorkspaceDir(Dummy : Integer): String;
Var
    PointerFile : String;
    F : TextFile;
    Line : String;
Begin
    Result := '';
    PointerFile := 'C:\ProgramData\eda-agent\workspace-path.txt';
    Try
        If FileExists(PointerFile) Then
        Begin
            AssignFile(F, PointerFile);
            Reset(F);
            Try
                If Not Eof(F) Then
                    ReadLn(F, Line);
            Finally
                CloseFile(F);
            End;
            // Trim CR/LF/space
            While (Length(Line) > 0) And ((Line[Length(Line)] = #13) Or (Line[Length(Line)] = #10) Or (Line[Length(Line)] = ' ')) Do
                Line := Copy(Line, 1, Length(Line) - 1);
            While (Length(Line) > 0) And (Line[1] = ' ') Do
                Line := Copy(Line, 2, Length(Line));
            If Line <> '' Then
                Result := Line;
        End;
    Except
        Result := '';
    End;
    If Result = '' Then
        Result := 'C:\EDA Agent\workspace\';
    // Ensure trailing backslash so path joins work consistently
    If Copy(Result, Length(Result), 1) <> '\' Then
        Result := Result + '\';
End;

{..............................................................................}
{ JSON Helper Functions                                                        }
{..............................................................................}

Function LoadTextFile(SL : TStringList; FilePath : String) : Boolean;
Var
    Stream : THandleStream;
    Attempt : Integer;
    Handle : Integer;
    Ok : Boolean;
Begin
    { Read every file through here, never SL.LoadFromFile. A sharing          }
    { violation inside LoadFromFile's TFileStream arrived as a modal          }
    { EFOpenError on Altium Designer 26.10.1.5 before the surrounding         }
    { Try/Except ran, stalling the polling loop. FileOpen returns -1 on a     }
    { sharing violation instead of raising, and once the handle is held       }
    { LoadFromStream has no open left to fail. The lock is usually brief (a   }
    { scanner touching a freshly written file), so retry for about 180 ms.    }
    { False when the file is missing or stayed locked; SL is then empty.      }
    Ok := False;
    SL.Text := '';
    If FileExists(FilePath) Then
    Begin
        Attempt := 0;
        While Attempt < 12 Do
        Begin
            Inc(Attempt);
            Handle := FileOpen(FilePath, fmOpenRead Or fmShareDenyNone);
            If Handle <> -1 Then
            Begin
                Try
                    Stream := THandleStream.Create(Handle);
                    Try
                        Try
                            SL.LoadFromStream(Stream);
                            Ok := True;
                        Except
                            Ok := False;
                        End;
                    Finally
                        Stream.Free;
                    End;
                Finally
                    FileClose(Handle);
                End;
            End;
            If Ok Then Break;
            Sleep(15);
        End;
    End;
    Result := Ok;
End;

Function ReadFileContent(FilePath : String) : String;
Var
    SL : TStringList;
    Content : String;
Begin
    { NOTE: stash the content in a LOCAL string and assign Result from it as }
    { the LAST statement. Assigning Result directly from SL.Text (an object  }
    { property) and then Exit-ing triggers the DelphiScript return-clobber    }
    { bug: the caller receives the FilePath argument instead of the file      }
    { content, so the dispatcher reads the request path as the command and    }
    { silently processes nothing. See delphiscript_result_clobber.            }
    Content := '';
    SL := TStringList.Create;
    Try
        If LoadTextFile(SL, FilePath) Then Content := SL.Text;
    Finally
        SL.Free;
    End;
    Result := Content;
End;

Procedure WriteFileContent(FilePath : String; Content : String);
Var
    SL : TStringList;
    Attempt : Integer;
    Ok : Boolean;
Begin
    { Write via TStringList.SaveToFile, NOT TextFile Rewrite/Write. Rewrite    }
    { raises a low-level EInOutError on a sharing violation (I/O error 32)     }
    { that the Altium script engine surfaces as a modal before the            }
    { surrounding Try/Except can swallow it, stalling the polling loop.        }
    { SaveToFile goes through a TFileStream and raises an ordinary VCL         }
    { exception that Try/Except catches cleanly -- no engine modal. The lock   }
    { is transient (Defender scan, or Python reading the response mid-write),  }
    { so retry briefly. Same idiom as the proven sibling MCP                  }
    { (OutputLines.Text := json; SaveToFile).                                 }
    { CLEAR THE DESTINATION FIRST, with calls that cannot raise.

      MEASURED: EFCreateError, "Cannot create file ... because it is
      being used by another process", arrived as a modal and stalled the
      polling loop. The retry below cannot help with that, because the
      engine surfaces the exception before the surrounding Try/Except
      runs, so the first failure is already a modal. Same behaviour that
      defeated Try/Except around StrToFloat, same answer: stop the
      exception happening rather than trying to catch it.

      FileExists and DeleteFile return Booleans and do not raise, so this
      loop is safe no matter who holds the file. Only once the name is
      free is the raising create attempted.

      tmp + RenameFile would be the tidier fix and is ruled out: the
      sibling implementation in reference/CoAltium records that
      DelphiScript's RenameFile silently failed for some paths and the
      response never reached its final filename.

      This does not make a create infallible. A fresh name can still be
      grabbed between the check and the create, by a virus scanner most
      likely. It removes the reported case, which is a create against a
      name that is already there and already held. }
    Attempt := 0;
    While (Attempt < 40) And FileExists(FilePath) Do
    Begin
        Inc(Attempt);
        DeleteFile(FilePath);
        If FileExists(FilePath) Then Sleep(15);
    End;

    Attempt := 0;
    While Attempt < 12 Do
    Begin
        Inc(Attempt);
        Ok := False;
        SL := TStringList.Create;
        Try
            Try
                SL.Text := Content;
                SL.SaveToFile(FilePath);
                Ok := True;
            Except
                Ok := False;
            End;
        Finally
            SL.Free;
        End;
        If Ok Then Exit;
        { The destination may have reappeared, so clear it again before
          the next create rather than repeating the failing call. }
        If FileExists(FilePath) Then DeleteFile(FilePath);
        Sleep(15);
    End;
End;

{ Append a line to workspace/activity.log for performance profiling. The     }
{ polling loop uses this to record per-command timings, and handlers can     }
{ add their own sub-stage timings for bottleneck analysis. Silently           }
{ swallows IO errors, logging must not break a command.                      }
Procedure AppendLog(Line : String);
Var
    F : TextFile;
    LogPath : String;
Begin
    Try
        LogPath := WorkspaceDir + 'activity.log';
        AssignFile(F, LogPath);
        If FileExists(LogPath) Then Append(F) Else Rewrite(F);
        Try
            WriteLn(F, Line);
        Finally
            CloseFile(F);
        End;
    Except
        // Never raise from the logger
    End;
End;

Function FormatLogStamp(Dummy : Integer): String;
Begin
    Try
        Result := FormatDateTime('yyyy-mm-dd hh:nn:ss.zzz', Now);
    Except
        Result := '';
    End;
End;

Procedure RecordCastError(Where : String);
Begin
    Inc(CastErrorCount);
    AppendLog(FormatLogStamp(0) + ',0,_cast_error,' + Where);
End;

Function IsWhitespaceOrColon(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = ' ') Or (C = ':') Or (C = #9) Or (C = #10) Or (C = #13);
End;

Function IsDelimiter(S : String; Idx : Integer) : Boolean;
Var
    C : String;
Begin
    C := Copy(S, Idx, 1);
    Result := (C = '') Or (C = ',') Or (C = '}') Or (C = ']') Or (C = ' ') Or (C = #9) Or (C = #10) Or (C = #13);
End;

{ Hex digit to integer (0-15). Returns -1 for invalid input. }
Function HexDigitValue(Ch : String) : Integer;
Var
    O : Integer;
Begin
    Result := -1;
    If Length(Ch) <> 1 Then Exit;
    O := Ord(Ch[1]);
    If (O >= Ord('0')) And (O <= Ord('9')) Then Result := O - Ord('0')
    Else If (O >= Ord('a')) And (O <= Ord('f')) Then Result := O - Ord('a') + 10
    Else If (O >= Ord('A')) And (O <= Ord('F')) Then Result := O - Ord('A') + 10;
End;

Function UnescapeJsonString(S : String) : String;
Var
    I, L : Integer;
    Ch, NextCh, HexStr : String;
    Code, D0, D1, D2, D3 : Integer;
Begin
    // Char-by-char JSON unescape with full \uXXXX support. The naive
    // StringReplace cascade (\t -> tab, \n -> LF, ..., \\ -> \) is broken
    // for sequences like \\temp, handles escapes left-to-right so \\
    // collapses to \ before evaluating the following char.
    Result := '';
    I := 1;
    L := Length(S);
    While I <= L Do
    Begin
        Ch := Copy(S, I, 1);
        If (Ch = '\') And (I < L) Then
        Begin
            NextCh := Copy(S, I + 1, 1);
            If NextCh = '\' Then Begin Result := Result + '\'; Inc(I, 2); End
            Else If NextCh = 'n' Then Begin Result := Result + #10; Inc(I, 2); End
            Else If NextCh = 't' Then Begin Result := Result + #9; Inc(I, 2); End
            Else If NextCh = 'r' Then Begin Result := Result + #13; Inc(I, 2); End
            Else If NextCh = '"' Then Begin Result := Result + '"'; Inc(I, 2); End
            Else If NextCh = '/' Then Begin Result := Result + '/'; Inc(I, 2); End
            Else If NextCh = 'b' Then Begin Result := Result + #8; Inc(I, 2); End
            Else If NextCh = 'f' Then Begin Result := Result + #12; Inc(I, 2); End
            Else If NextCh = 'u' Then
            Begin
                // \uXXXX, 4 hex digits. Codepoints <= 255 become one
                // character; higher ones are replaced with '?'. The bridge
                // sends non-ASCII as literal UTF-8 behind a byte-order mark
                // (_publish_request), so only control characters arrive
                // escaped from it.
                If I + 5 <= L Then
                Begin
                    HexStr := Copy(S, I + 2, 4);
                    D0 := HexDigitValue(Copy(HexStr, 1, 1));
                    D1 := HexDigitValue(Copy(HexStr, 2, 1));
                    D2 := HexDigitValue(Copy(HexStr, 3, 1));
                    D3 := HexDigitValue(Copy(HexStr, 4, 1));
                    If (D0 >= 0) And (D1 >= 0) And (D2 >= 0) And (D3 >= 0) Then
                    Begin
                        Code := (D0 * 4096) + (D1 * 256) + (D2 * 16) + D3;
                        If Code <= 255 Then
                            Result := Result + Chr(Code)
                        Else
                            Result := Result + '?';
                        Inc(I, 6);
                    End
                    Else
                    Begin
                        // Bad hex, keep literal
                        Result := Result + Ch + NextCh;
                        Inc(I, 2);
                    End;
                End
                Else
                Begin
                    Result := Result + Ch + NextCh;
                    Inc(I, 2);
                End;
            End
            Else
            Begin
                // Unknown escape, keep both chars literally
                Result := Result + Ch + NextCh;
                Inc(I, 2);
            End;
        End
        Else
        Begin
            Result := Result + Ch;
            Inc(I);
        End;
    End;
End;

{ One spelling for a file path a caller sends. Altium keys an open document  }
{ by the path string it was opened with, so the same file opened as         }
{ C:/dir\x.PcbLib and as C:\dir\x.PcbLib is TWO open documents. A later     }
{ open or focus by the other spelling loaded a second copy from disk, the   }
{ tool then edited that copy, and an edit after it raised "Object accessed  }
{ after being destroyed" in ADVPCB (measured 2026-10-18). Only a value under }
{ a key ending in "path" that is a drive or UNC path is rewritten, so a     }
{ menu_path like "File|Open" passes untouched.                              }
Function CanonicalPathValue(Key : String; Value : String) : String;
Var
    K : String;
Begin
    Result := Value;
    K := LowerCase(Key);
    If (Length(K) < 4) Or (Copy(K, Length(K) - 3, 4) <> 'path') Then Exit;
    If (Length(Value) >= 3) And (Copy(Value, 2, 1) = ':')
        And ((Copy(Value, 3, 1) = '/') Or (Copy(Value, 3, 1) = '\')) Then
        Result := StringReplace(Value, '/', '\', -1)
    Else If (Copy(Value, 1, 2) = '//') Or (Copy(Value, 1, 2) = '\\') Then
        Result := StringReplace(Value, '/', '\', -1);
End;

Function ExtractJsonValue(Json : String; Key : String) : String;
Var
    StartPos, EndPos : Integer;
    SearchKey : String;
    BraceCount : Integer;
    BackslashCount, TempPos : Integer;
    InStr : Boolean;
    Ch : String;
Begin
    Result := '';
    SearchKey := '"' + Key + '"';
    StartPos := Pos(SearchKey, Json);
    If StartPos > 0 Then
    Begin
        StartPos := StartPos + Length(SearchKey);
        // Skip whitespace and colon
        While (StartPos <= Length(Json)) And IsWhitespaceOrColon(Json, StartPos) Do
            Inc(StartPos);

        If StartPos <= Length(Json) Then
        Begin
            If Copy(Json, StartPos, 1) = '"' Then
            Begin
                // String value
                Inc(StartPos);
                EndPos := StartPos;
                While (EndPos <= Length(Json)) Do
                Begin
                    If Copy(Json, EndPos, 1) = '"' Then
                    Begin
                        // Count consecutive backslashes before this quote
                        BackslashCount := 0;
                        TempPos := EndPos - 1;
                        While (TempPos >= StartPos) And (Copy(Json, TempPos, 1) = '\') Do
                        Begin
                            Inc(BackslashCount);
                            Dec(TempPos);
                        End;
                        // Even number of backslashes means quote is real
                        If (BackslashCount Mod 2) = 0 Then Break;
                    End;
                    Inc(EndPos);
                End;
                Result := CanonicalPathValue(Key,
                    UnescapeJsonString(Copy(Json, StartPos, EndPos - StartPos)));
            End
            Else If (Copy(Json, StartPos, 1) = '{') Or
                    (Copy(Json, StartPos, 1) = '[') Then
            Begin
                // Container value (object or array). Depth-count braces AND
                // brackets together, skipping string literals so a '}' or
                // ']' inside a "string" can't close the container early.
                EndPos := StartPos;
                BraceCount := 0;
                InStr := False;
                While EndPos <= Length(Json) Do
                Begin
                    Ch := Copy(Json, EndPos, 1);
                    If InStr Then
                    Begin
                        If Ch = '"' Then
                        Begin
                            // Real close-quote only after an even run of '\'.
                            BackslashCount := 0;
                            TempPos := EndPos - 1;
                            While (TempPos >= StartPos) And
                                  (Copy(Json, TempPos, 1) = '\') Do
                            Begin
                                Inc(BackslashCount);
                                Dec(TempPos);
                            End;
                            If (BackslashCount Mod 2) = 0 Then InStr := False;
                        End;
                    End
                    Else
                    Begin
                        If Ch = '"' Then InStr := True
                        Else If (Ch = '{') Or (Ch = '[') Then Inc(BraceCount)
                        Else If (Ch = '}') Or (Ch = ']') Then Dec(BraceCount);
                    End;
                    Inc(EndPos);
                    If (Not InStr) And (BraceCount = 0) Then Break;
                End;
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End
            Else
            Begin
                // Number or other value
                EndPos := StartPos;
                While (EndPos <= Length(Json)) And (Not IsDelimiter(Json, EndPos)) Do
                    Inc(EndPos);
                Result := Copy(Json, StartPos, EndPos - StartPos);
            End;
        End;
    End;
End;

{..............................................................................}
{ JSON envelope builders.                                                       }
{                                                                               }
{ All responses include protocol_version so the Python side can detect a       }
{ stale Pascal-side compile after a wire-format change. BuildErrorResponse     }
{ takes an optional structured `details` JSON value (pass '' to omit). The     }
{ Detailed variant lets handlers attach machine-readable failure context       }
{ (which item in a batch failed, what type was expected, etc).                 }
{..............................................................................}

Function BuildSuccessResponse(RequestId : String; Data : String) : String;
Begin
    If Data = '' Then
        Data := 'null';
    Result := '{"protocol_version":' + IntToStr(PROTOCOL_VERSION) +
              ',"id":"' + RequestId + '","success":true,"data":' +
              Data + ',"error":null}';
End;

{..............................................................................}
{ ProjectSheet - a project sheet for a project-wide read, counted.            }
{                                                                              }
{ SchServer.GetSchDocumentByPath returns Nil for a sheet the editor has not    }
{ loaded, and every project loop skipped it with Continue. An audit over a    }
{ partly loaded project then answered checked:0 / violations:0, the same as   }
{ "looked and found nothing". Each lookup through here is counted, and        }
{ SheetCoverage puts the counts on the reply at the one dispatch point, so a  }
{ loop added later is covered without remembering to.                         }
{..............................................................................}
Function ProjectSheet(Path : String) : ISch_Document;
Begin
    Result := Nil;
    Inc(SheetsSeen);
    Try Result := SchServer.GetSchDocumentByPath(Path); Except Result := Nil; End;
    If Result = Nil Then Inc(SheetsSkipped);
End;

{ The reply with the sheet counts added when a project-wide read skipped a   }
{ sheet. Only a success whose data is an object is touched; an error, or a   }
{ reply that read no project sheet, goes back unchanged.                     }
Function SheetCoverage(Response : String) : String;
Var
    Marker, Added : String;
    P : Integer;
Begin
    Result := Response;
    If SheetsSkipped = 0 Then Exit;
    Marker := '"success":true,"data":' + Chr(123);
    P := Pos(Marker, Response);
    If P = 0 Then Exit;
    P := P + Length(Marker);
    Added := '"sheets_seen":' + IntToStr(SheetsSeen)
        + ',"sheets_skipped":' + IntToStr(SheetsSkipped)
        + ',"sheets_warning":"' + IntToStr(SheetsSkipped) + ' of '
        + IntToStr(SheetsSeen) + ' project sheets are not loaded in the editor '
        + 'and were NOT read, so counts and findings cover only the rest. '
        + 'Run proj_load_sheets and ask again."';
    { A data object that was empty takes the fields without a comma. }
    If Copy(Response, P, 1) <> Chr(125) Then Added := Added + ',';
    Result := Copy(Response, 1, P - 1) + Added + Copy(Response, P, Length(Response));
End;

{..............................................................................}
{ CROSS-DOCUMENT HINTS                                                        }
{                                                                              }
{ "No PCB library is active" is true and unhelpful. It says what is missing    }
{ and not that the SAME operation exists for the other document kind, so the   }
{ reasonable conclusion from it is that the capability is absent. That         }
{ conclusion has been drawn and reported more than once, and each time the     }
{ tool was there under the other namespace.                                    }
{                                                                              }
{ The split is the thing worth stating: lib_ acts on .PcbLib and .SchLib, pcb_ }
{ on .PcbDoc, sch_ and obj_ on .SchDoc. Attached here rather than at the 277   }
{ call sites so it cannot be right in some of them and stale in the rest.      }
{..............................................................................}

Function MessageNamesATool(Msg : String) : Boolean;
Begin
    Result := (Pos('pcb_', Msg) > 0) Or (Pos('lib_', Msg) > 0)
           Or (Pos('sch_', Msg) > 0) Or (Pos('proj_', Msg) > 0)
           Or (Pos('obj_', Msg) > 0) Or (Pos('app_', Msg) > 0)
           Or (Pos('run_', Msg) > 0);
End;

{ What IS focused, for a refusal that only says what is not. "No schematic   }
{ document is active" with a .SchLib in front of the editor sent a session   }
{ hunting for the call that had stolen focus, when naming the .SchLib would  }
{ have made the next move obvious.                                           }
{ File name only: a full path carries the user's name into every log. DM     }
{ calls only, which never load an editor server, so this cannot raise an     }
{ uncatchable undeclared-identifier modal from inside an error path.         }
Function FocusedDocumentNote(Dummy : Integer) : String;
Var
    Workspace : IWorkspace;
    Doc : IDocument;
    Name : String;
Begin
    Result := '';
    Try
        Workspace := GetWorkspace;
        If Workspace = Nil Then Exit;
        Doc := Workspace.DM_FocusedDocument;
        If Doc = Nil Then
        Begin
            Result := 'No document has editor focus.';
            Exit;
        End;
        Name := ExtractFileName(Doc.DM_FullPath);
        If Name <> '' Then
            Result := 'The focused document is ' + Name + ' ('
                    + Doc.DM_DocumentKind + ').';
    Except
        Result := '';
    End;
End;

{ Handler messages rarely end in a full stop, so a note appended to one ran  }
{ on, live: "No schematic document is active No document has editor focus."  }
Function WithFullStop(S : String) : String;
Var
    Last : String;
Begin
    Result := S;
    If S = '' Then Exit;
    Last := Copy(S, Length(S), 1);
    If (Last <> '.') And (Last <> '!') And (Last <> '?') Then
        Result := S + '.';
End;

Function CrossDocumentHint(ErrorCode : String) : String;
Begin
    Result := '';
    If (ErrorCode = 'NO_PCBLIB') Then
        Result := 'This is the LIBRARY tool and needs a .PcbLib. The same '
                + 'operation on an open board is in the pcb_ namespace.'
    Else If (ErrorCode = 'NO_SCHLIB') Then
        Result := 'This is the LIBRARY tool and needs a .SchLib. For a '
                + 'sheet, the sch_ and obj_ tools act on the open .SchDoc.'
    Else If (ErrorCode = 'NO_PCB') Or (ErrorCode = 'NO_BOARD') Then
        Result := 'This tool acts on an open .PcbDoc. To edit a footprint '
                + 'inside a .PcbLib, use the lib_ tools instead.'
    Else If (ErrorCode = 'NO_SCHDOC') Or (ErrorCode = 'NO_SCHEMATIC') Then
        Result := 'This tool acts on an open .SchDoc sheet. To edit a '
                + 'symbol inside a .SchLib, use the lib_ tools instead.';
End;

Function BuildErrorResponseDetailed(RequestId : String; ErrorCode : String;
                                    ErrorMsg : String; DetailsJson : String) : String;
Var
    EscMsg, Ch, HexDigits, Hint, Focus : String;
    I, O : Integer;
    Pointed : Boolean;
Begin
    { The generic hint goes on only when the handler has not already    }
    { pointed somewhere itself: a specific pointer beats the generic one }
    { and must not be doubled. Decided on the handler's own words,       }
    { BEFORE the focus note is added, because a file name such as        }
    { power_lib_parts.SchLib contains "lib_" and would read as a pointer.}
    { Why GetPCBBoardAnywhere gave no board, when it decided not to guess. }
    If (ErrorCode = 'NO_PCB') And (BoardMissWhy <> '') Then
        ErrorMsg := WithFullStop(ErrorMsg) + ' ' + BoardMissWhy;
    Hint := CrossDocumentHint(ErrorCode);
    Pointed := MessageNamesATool(ErrorMsg);
    If Hint <> '' Then
    Begin
        { A fact, not a pointer, so it goes on every wrong-document refusal. }
        Focus := FocusedDocumentNote(0);
        If Focus <> '' Then
            ErrorMsg := WithFullStop(ErrorMsg) + ' ' + Focus;
        If Not Pointed Then
            ErrorMsg := WithFullStop(ErrorMsg) + ' ' + Hint;
    End;

    // Inline escape (EscapeJsonString is not yet declared in build order).
    // Must also \uXXXX-escape control and non-ASCII characters: one raw
    // byte >127 in an error message (an accented file path, say) makes the
    // whole response unparseable on the Python side and hides the real
    // error. All four digits: a CJK path is above 255.
    HexDigits := '0123456789abcdef';
    EscMsg := '';
    For I := 1 To Length(ErrorMsg) Do
    Begin
        Ch := Copy(ErrorMsg, I, 1);
        O := Ord(Ch[1]);
        If O = Ord('\') Then EscMsg := EscMsg + '\\'
        Else If O = Ord('"') Then EscMsg := EscMsg + '\"'
        Else If O = 13 Then EscMsg := EscMsg + '\r'
        Else If O = 10 Then EscMsg := EscMsg + '\n'
        Else If O = 9 Then EscMsg := EscMsg + '\t'
        Else If (O < 32) Or (O >= 128) Then
            EscMsg := EscMsg + '\u'
                + Copy(HexDigits, ((O Shr 12) And $F) + 1, 1)
                + Copy(HexDigits, ((O Shr 8) And $F) + 1, 1)
                + Copy(HexDigits, ((O Shr 4) And $F) + 1, 1)
                + Copy(HexDigits, (O And $F) + 1, 1)
        Else
            EscMsg := EscMsg + Ch;
    End;
    If DetailsJson = '' Then
        Result := '{"protocol_version":' + IntToStr(PROTOCOL_VERSION) +
                  ',"id":"' + RequestId + '","success":false,"data":null,' +
                  '"error":{"code":"' + ErrorCode + '","message":"' + EscMsg +
                  '","details":null}}'
    Else
        Result := '{"protocol_version":' + IntToStr(PROTOCOL_VERSION) +
                  ',"id":"' + RequestId + '","success":false,"data":null,' +
                  '"error":{"code":"' + ErrorCode + '","message":"' + EscMsg +
                  '","details":' + DetailsJson + '}}';

    { The message as written, for the status window. Read back out of the
      JSON it loses every character above 255 to '?', since the unescaper
      cannot build one in this script host (issue #91). }
    LastErrorId := RequestId;
    LastErrorCode := ErrorCode;
    LastErrorMsg := ErrorMsg;
End;

Function BuildErrorResponse(RequestId : String; ErrorCode : String; ErrorMsg : String) : String;
Begin
    Result := BuildErrorResponseDetailed(RequestId, ErrorCode, ErrorMsg, '');
End;

Procedure EnsureWorkspaceDir(Dummy : Integer);
Begin
    If WorkspaceDir = '' Then
        WorkspaceDir := ResolveDefaultWorkspaceDir(0);
    If Not DirectoryExists(WorkspaceDir) Then
        ForceDirectories(WorkspaceDir);
End;

{ The command refused because saving is switched off. What says why the  }
{ command cannot run without writing a file.                            }
Function SavingOffError(RequestId : String; What : String) : String;
Begin
    Result := BuildErrorResponse(RequestId, 'SAVING_OFF',
        What + ' Saving is switched off on the bridge dashboard, so nothing '
        + 'was written. Turn the Saving switch back on there to save.');
End;

{ The Saving switch outlasts a restart of the bridge and of Altium: it is  }
{ read from SAVING_FILE in the workspace when the pump starts. No file, or }
{ one that does not say off, means saving is allowed.                     }
Procedure LoadSavingSwitch(Dummy : Integer);
Var
    Content : String;
Begin
    SavingOff := False;
    If Not FileExists(WorkspaceDir + SAVING_FILE) Then Exit;
    Content := ReadFileContent(WorkspaceDir + SAVING_FILE);
    SavingOff := LowerCase(ExtractJsonValue(Content, 'saving')) = 'off';
End;

Procedure StoreSavingSwitch(Dummy : Integer);
Begin
    If SavingOff Then
        WriteFileContent(WorkspaceDir + SAVING_FILE, '{"saving":"off"}')
    Else
        WriteFileContent(WorkspaceDir + SAVING_FILE, '{"saving":"on"}');
End;

{..............................................................................}
{ Per-request IPC helpers.                                                      }
{                                                                               }
{ Request files: request_<id>.json, Python writes them, Pascal scans the      }
{ workspace each polling cycle and processes the first one it finds.           }
{ Response files: response_<id>.json, Pascal writes them, Python polls for    }
{ the specific path matching its own request ID.                               }
{                                                                               }
{ Per-request files eliminate the stale-response race that the old single-     }
{ file scheme had: two concurrent callers (e.g. keep-alive + user tool) used   }
{ to step on each other's response.json. With one file per request, callers    }
{ poll only their own filename and never see another's payload.                }
{                                                                               }
{ The request ID embedded in the filename is restricted to UUID-shape chars    }
{ (alphanumeric, hyphen, underscore) by IsValidRequestId, anything else is    }
{ rejected so a malformed ID can't escape the workspace dir via path tricks.   }
{..............................................................................}

Function IsValidRequestId(Id : String) : Boolean;
Var
    I, O : Integer;
    Ch : String;
Begin
    Result := False;
    If (Length(Id) < 1) Or (Length(Id) > 64) Then Exit;
    For I := 1 To Length(Id) Do
    Begin
        Ch := Copy(Id, I, 1);
        O := Ord(Ch[1]);
        If Not (((O >= Ord('0')) And (O <= Ord('9'))) Or
                ((O >= Ord('a')) And (O <= Ord('z'))) Or
                ((O >= Ord('A')) And (O <= Ord('Z'))) Or
                (Ch = '-') Or (Ch = '_')) Then
            Exit;
    End;
    Result := True;
End;

Function RequestFilePath(RequestId : String) : String;
Begin
    Result := WorkspaceDir + 'request_' + RequestId + '.json';
End;

Function ResponseFilePath(RequestId : String) : String;
Begin
    Result := WorkspaceDir + 'response_' + RequestId + '.json';
End;

Function ProgressFilePath(RequestId : String) : String;
Begin
    Result := WorkspaceDir + 'progress_' + RequestId + '.json';
End;

{ Heartbeat protocol for long-running commands.                                }
{                                                                              }
{ DelphiScript is single-threaded: while a handler is executing, the polling  }
{ loop cannot respond to anything else. A 10-second Python deadline on a      }
{ legitimately-slow operation (large project compile, multi-document iterate, }
{ heavy emit pass) fires a false timeout. Heartbeat fixes that by writing a   }
{ short-lived marker file at the start of dispatch; the Python bridge treats  }
{ marker presence as "Altium is still working on it, keep waiting".           }
{                                                                              }
{ Ordering matters: write the response file BEFORE deleting the progress     }
{ file so there is never a moment where neither exists (which would let       }
{ Python's deadline-check race the cleanup and fire a false hard-timeout).    }
Procedure StartProgress(RequestId : String);
Var
    ProgressPath : String;
Begin
    If Not IsValidRequestId(RequestId) Then Exit;
    ProgressPath := ProgressFilePath(RequestId);
    Try
        WriteFileContent(ProgressPath,
            '{"started_ms":' + IntToStr(GetTickCount) + '}');
    Except End;
End;

Procedure EndProgress(RequestId : String);
Var ProgressPath : String;
Begin
    If Not IsValidRequestId(RequestId) Then Exit;
    ProgressPath := ProgressFilePath(RequestId);
    Try DeleteFile(ProgressPath); Except End;
End;

{ Wipe orphaned progress_*.json files at session start. A previous run that   }
{ crashed mid-handler would leave its progress marker behind; left untouched, }
{ Python could keep extending its deadline against a stale marker.            }
Procedure CleanupOrphanProgress(Dummy : Integer);
Var
    Files : TStringList;
    I : Integer;
Begin
    Files := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'progress_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
            Try DeleteFile(Files[I]); Except End;
    Finally
        Files.Free;
    End;
End;

{ Pick up the next request. Python writes per-request file request_<id>.json.}
{ The script enumerates request_*.json files via FindFiles (the documented   }
{ Altium DelphiScript helper; SysUtils FindFirst is not exposed to scripts)  }
{ and processes the first one it finds.                                      }
{                                                                              }
{ Per-request files on both sides, request_<id>.json + response_<id>.json, }
{ eliminate any cross-caller race: each caller publishes to its own filename }
{ and polls only its own response file.                                      }
{                                                                              }
{ TIME SLOTS BETWEEN CLAUDE SESSIONS. Each eda-agent server (one per Claude   }
{ session) writes ids <tag>-<seq>-<random>: an 8-character session tag and an }
{ 8-digit sequence. Any other id is the 'legacy' session (a server from       }
{ before this). Of the pending requests, each session's lowest sequence is    }
{ its next. With one session pending it is served straight through: one      }
{ session is never slotted. With several, the session whose slot is running  }
{ keeps it for SESSION_SLOT_MS, then the next waiting session in tag order    }
{ gets one slot, so no session has a second slot before every waiting one     }
{ has had one. A command is never cut off: slots change between commands.    }
{ While sessions contend, queue.json is refreshed about once a second, so a  }
{ waiting client can tell a queue from a dead loop.                           }

{ PickSession - which session's request goes next. Tags: the sessions with   }
{ a pending request, joined by '|'. Current: the session whose slot is       }
{ running, ElapsedMs how long it has run. A session alone always goes; the   }
{ current session keeps its slot until SlotMs; otherwise the first tag after }
{ Current in string order, wrapping to the lowest. Pure strings, so the Free }
{ Pascal cross-check runs a verbatim copy.                                   }
Function PickSession(Tags : String; Current : String; ElapsedMs : Integer;
    SlotMs : Integer) : String;
Var
    Rest, Tag, Lowest, After : String;
    Bar, Count : Integer;
    HasCurrent : Boolean;
Begin
    Result := '';
    Lowest := '';
    After := '';
    Count := 0;
    HasCurrent := False;
    Rest := Tags;
    While Rest <> '' Do
    Begin
        Bar := Pos('|', Rest);
        If Bar = 0 Then
        Begin
            Tag := Rest;
            Rest := '';
        End
        Else
        Begin
            Tag := Copy(Rest, 1, Bar - 1);
            Rest := Copy(Rest, Bar + 1, Length(Rest));
        End;
        If Tag <> '' Then
        Begin
            Inc(Count);
            If Tag = Current Then HasCurrent := True;
            If (Lowest = '') Or (Tag < Lowest) Then Lowest := Tag;
            If Tag > Current Then
                If (After = '') Or (Tag < After) Then After := Tag;
        End;
    End;
    If Count = 0 Then Exit;
    If Count = 1 Then
    Begin
        Result := Lowest;
        Exit;
    End;
    If HasCurrent And (ElapsedMs < SlotMs) Then
    Begin
        Result := Current;
        Exit;
    End;
    If After <> '' Then Result := After
    Else Result := Lowest;
End;

{ SessionFocusAction - whether to put a session's document back in front    }
{ before its command runs. Most commands act on the focused document, and   }
{ any session can move it between two calls of another: measured on        }
{ 2026-10-23, 309 of about 1500 commands in a day were set_active_document, }
{ sessions taking the focus back from each other, with a gap in which a    }
{ write could land on the wrong document. Pin is the document the session  }
{ last had in front, Current what is in front now, both canonical.         }
{   'keep'    no pin, or the pin is already in front                      }
{   'restore' put the pin back in front first                             }
{ A session's command always runs on the document that session chose, even }
{ when a person at the keyboard clicked elsewhere in between, by choice    }
{ (2026-10-23): a write on a document the session never picked             }
{ is worse than a view that moves. A session is pointed at another         }
{ document by focusing it, which moves its pin.                           }
{ Pure strings, so the Free Pascal cross-check runs a verbatim copy.       }
Function SessionFocusAction(Pin : String; Current : String) : String;
Begin
    Result := 'keep';
    If Pin = '' Then Exit;
    If UpperCase(Pin) = UpperCase(Current) Then Exit;
    Result := 'restore';
End;

Function ScanForRequestFile(Var FilePath : String; Var RequestId : String) : Boolean;
Var
    Files, Tags, BestSeq, BestName : TStringList;
    I, NameLen, Pick, NowTick : Integer;
    Name, IdPart, Tag, Seq, NextTag, Pending : String;
Begin
    Result := False;
    FilePath := '';
    RequestId := '';

    Files := TStringList.Create;
    Tags := TStringList.Create;
    BestSeq := TStringList.Create;
    BestName := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'request_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
        Begin
            Name := ExtractFileName(Files[I]);
            NameLen := Length(Name);
            // FindFiles can return uppercase filenames on Windows;
            // case-insensitive prefix/suffix check.
            If (NameLen >= 14) And
               (UpperCase(Copy(Name, 1, 8)) = 'REQUEST_') And
               (UpperCase(Copy(Name, NameLen - 4, 5)) = '.JSON') Then
            Begin
                IdPart := Copy(Name, 9, NameLen - 13);
                If IsValidRequestId(IdPart) Then
                Begin
                    Tag := 'legacy';
                    Seq := '';
                    If (Length(IdPart) >= 19) And (Pos('-', IdPart) = 9)
                        And (Copy(IdPart, 18, 1) = '-') Then
                    Begin
                        Tag := LowerCase(Copy(IdPart, 1, 8));
                        Seq := Copy(IdPart, 10, 8);
                    End;
                    Pick := Tags.IndexOf(Tag);
                    If Pick < 0 Then
                    Begin
                        Tags.Add(Tag);
                        BestSeq.Add(Seq);
                        BestName.Add(Name);
                    End
                    Else
                    Begin
                        If Seq < BestSeq[Pick] Then
                        Begin
                            BestSeq[Pick] := Seq;
                            BestName[Pick] := Name;
                        End;
                    End;
                End;
            End;
        End;

        If Tags.Count > 0 Then
        Begin
            NowTick := GetTickCount;
            Pending := '';
            For I := 0 To Tags.Count - 1 Do
            Begin
                If Pending <> '' Then Pending := Pending + '|';
                Pending := Pending + Tags[I];
            End;
            NextTag := PickSession(Pending, SlotSession, NowTick - SlotStartTick,
                SESSION_SLOT_MS);
            Pick := Tags.IndexOf(NextTag);
            If Pick < 0 Then Pick := 0;
            If Tags.Count > 1 Then
            Begin
                If (NowTick - QueueNoteTick >= 1000) Or (Tags[Pick] <> SlotSession) Then
                Begin
                    Pending := '';
                    For I := 0 To Tags.Count - 1 Do
                    Begin
                        If Pending <> '' Then Pending := Pending + ',';
                        Pending := Pending + '"' + Tags[I] + '"';
                    End;
                    Try
                        WriteFileContent(WorkspaceDir + 'queue.json',
                            '{"serving":"' + Tags[Pick] + '","pending":[' + Pending
                            + '],"slot_ms":' + IntToStr(SESSION_SLOT_MS) + '}');
                    Except End;
                    QueueNoteTick := NowTick;
                End;
            End;
            If Tags[Pick] <> SlotSession Then
            Begin
                SlotSession := Tags[Pick];
                SlotStartTick := NowTick;
            End;
            RequestId := Copy(BestName[Pick], 9, Length(BestName[Pick]) - 13);
            FilePath := WorkspaceDir + BestName[Pick];
            Result := True;
        End;
    Finally
        Files.Free;
        Tags.Free;
        BestSeq.Free;
        BestName.Free;
    End;
End;

{ Write the response file directly. Earlier versions did a tmp+RenameFile    }
{ for atomicity, but DelphiScript's RenameFile silently failed for some      }
{ paths and the response never reached the final filename. The Python side  }
{ tolerates a partially-written response (json.load raises, retried until    }
{ success) so direct write is acceptable.                                    }
Procedure WriteResponseFile(RequestId : String; JsonContent : String);
Var
    FinalPath : String;
Begin
    If Not IsValidRequestId(RequestId) Then Exit;
    FinalPath := ResponseFilePath(RequestId);
    WriteFileContent(FinalPath, JsonContent);
End;

{ Wipe leftover request_*.json files at session start so a previous run's    }
{ orphan can't replay against this session. A crashed loop, or a client that }
{ abandoned a long sweep, leaves request files on disk; without this the     }
{ next session would answer them all before serving anything new -- and each }
{ answer would resolve against whatever document is focused NOW, not the one }
{ the caller meant.                                                          }
{                                                                              }
{ The purged count is logged, so an operator can tell "the loop is slow" from }
{ "the loop is draining a backlog".                                          }
{ EXCEPT DURING A RESTART. eda-agent relaunch --restart writes             }
{ maintenance.json for as long as it takes, and other sessions' calls wait  }
{ through it instead of failing, so the requests on disk then are live      }
{ callers waiting for the new loop, not orphans. Each one still runs on its }
{ own session's document (SessionFocus), and a caller that gave up has      }
{ withdrawn its own request.                                               }
Procedure CleanupOrphanRequests(Dummy : Integer);
Var
    Files : TStringList;
    I, Purged : Integer;
Begin
    If FileExists(WorkspaceDir + 'maintenance.json') Then
    Begin
        AppendLog(FormatLogStamp(0) + ',0,_purge_requests,kept for restart,0,');
        Exit;
    End;
    Purged := 0;
    Files := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'request_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
            Try
                DeleteFile(Files[I]);
                Inc(Purged);
            Except End;
    Finally
        Files.Free;
    End;
    If Purged > 0 Then
        AppendLog(FormatLogStamp(0) + ',0,_purge_requests,count=' + IntToStr(Purged)
            + ',0,');
End;

{ Wipe leftover response_*.json files at session start. Python deletes its   }
{ own response file after reading it, but a client that timed out (or died)  }
{ leaves the response it never collected. These accumulate forever otherwise.}
{ Only safe at startup: no client can still be waiting on a response written }
{ by a session that has already ended.                                       }
{ Hidden from the Run Script dialog by its argument; Dummy is never read. }
Procedure CleanupOrphanResponses(Dummy : Integer);
Var
    Files : TStringList;
    I, Purged : Integer;
Begin
    Purged := 0;
    Files := TStringList.Create;
    Try
        FindFiles(WorkspaceDir, 'response_*.json', 63, False, Files);
        For I := 0 To Files.Count - 1 Do
            Try
                DeleteFile(Files[I]);
                Inc(Purged);
            Except End;
    Finally
        Files.Free;
    End;
    If Purged > 0 Then
        AppendLog(FormatLogStamp(0) + ',0,_purge_responses,count=' + IntToStr(Purged)
            + ',0,');
End;

{..............................................................................}
{ Load runtime config from mcp_config.json. The file lives in the workspace   }
{ and is the single source of truth for polling tunables. Both Python and    }
{ Pascal read from it. Missing or corrupt config leaves the defaults set by  }
{ InitDefaultConfig in place.                                                 }
{..............................................................................}

Procedure LoadMCPConfig(Dummy : Integer);
Var
    ConfigPath, Content, V : String;
    N : Integer;
Begin
    ConfigPath := WorkspaceDir + CONFIG_FILE;
    If Not FileExists(ConfigPath) Then Exit;
    Content := ReadFileContent(ConfigPath);
    If Content = '' Then Exit;

    V := ExtractJsonValue(Content, 'poll_interval_active_ms');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then PollIntervalActiveMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'poll_interval_idle_ms');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then PollIntervalIdleMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'idle_threshold');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then IdleThreshold := N; Except End; End;

    V := ExtractJsonValue(Content, 'auto_shutdown_ms');
    If V <> '' Then Begin Try N := StrToInt(V); If N >= 0 Then AutoShutdownMs := N; Except End; End;

    V := ExtractJsonValue(Content, 'yield_iterations');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then YieldIterations := N; Except End; End;

    V := ExtractJsonValue(Content, 'yield_every_n_active');
    If V <> '' Then Begin Try N := StrToInt(V); If N > 0 Then YieldEveryNActive := N; Except End; End;
End;

{..............................................................................}
{ Wire-envelope validation. Verifies that an incoming request matches the     }
{ contract Python emits, non-empty id with valid filename chars, non-empty   }
{ command, and a present (possibly empty) params object. Returns '' on        }
{ success, or a short reason string for the dispatcher to surface as          }
{ MALFORMED_REQUEST. Per-command param validation is the handler's job;       }
{ this is the universal envelope check.                                       }
{..............................................................................}

Function ValidateRequestEnvelope(RequestId, Command : String) : String;
Begin
    Result := '';
    If Not IsValidRequestId(RequestId) Then
    Begin
        Result := 'invalid request id (must be 1-64 chars of A-Z a-z 0-9 _ -)';
        Exit;
    End;
    If Command = '' Then
    Begin
        Result := 'request missing required field: command';
        Exit;
    End;
End;

{ Request dispatch and the poll timer are in StatusForm.pas (the pump's     }
{ handler must live in the form's own unit); the StartMCPServer entry point }
{ stays in Dispatcher.pas, which compiles last.                             }

{ UnitStamp_Main - which build of Main.pas Altium compiled; 'dev' here. }
{ install-scripts writes the hash of this file into the deployed copy. }
Function UnitStamp_Main(Dummy : Integer) : String;
Begin
    Result := 'dev';
End;
