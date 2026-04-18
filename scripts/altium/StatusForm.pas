{ SPDX-License-Identifier: Apache-2.0                                   }
{ Copyright (c) 2026 George Saliba                                      }
{..............................................................................}
{ StatusForm.pas - DFM-backed floating status window for the MCP polling loop.  }
{                                                                                }
{ The matching StatusForm.dfm declares TStatusForm with one child TLabel named   }
{ lbl_Status. Because the form is built from a DFM (not constructed with         }
{ TForm.Create + AddControl), Altium's DelphiScript host registers the child    }
{ controls and their properties — so lbl_Status.Caption works at runtime.        }
{..............................................................................}

{ OnClose handler wired from StatusForm.dfm. Clears the Running flag so the  }
{ polling loop in Dispatcher.pas exits cleanly on the next tick. Uses the    }
{ standard Delphi TCloseEvent signature: Sender + var Action.                 }
Procedure StatusFormClose(Sender : TObject; Var Action : TCloseAction);
Begin
    Try Running := False; Except End;
End;

Procedure ShowStatusForm;
Begin
    Try
        If Not StatusForm.Visible Then StatusForm.Show;
    Except End;
End;

Procedure HideStatusForm;
Begin
    Try
        If StatusForm.Visible Then StatusForm.Hide;
    Except End;
End;

Procedure SetStatusText(Msg : String);
Begin
    Try lbl_Status.Caption := Msg; Except End;
End;
