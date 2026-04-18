object StatusForm: TStatusForm
  Left = 200
  Top = 200
  BorderStyle = bsToolWindow
  Caption = 'EDA Agent MCP'
  ClientHeight = 50
  ClientWidth = 520
  Color = clBtnFace
  Font.Charset = DEFAULT_CHARSET
  Font.Color = clWindowText
  Font.Height = -12
  Font.Name = 'Consolas'
  Font.Style = []
  FormStyle = fsStayOnTop
  OldCreateOrder = False
  ParentFont = False
  Position = poScreenCenter
  PixelsPerInch = 96
  TextHeight = 14
  OnClose = StatusFormClose
  object lbl_Status: TLabel
    Left = 8
    Top = 16
    Width = 504
    Height = 20
    AutoSize = False
    Caption = 'MCP: starting...'
    Font.Charset = DEFAULT_CHARSET
    Font.Color = clWindowText
    Font.Height = -12
    Font.Name = 'Consolas'
    Font.Style = []
    ParentFont = False
  end
end
