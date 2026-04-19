object StatusForm: TStatusForm
  Left = 200
  Top = 200
  BorderIcons = [biSystemMenu, biMinimize]
  BorderStyle = bsSizeable
  Caption = 'EDA Agent MCP'
  ClientHeight = 540
  ClientWidth = 340
  Color = $001E1E1E
  Font.Charset = DEFAULT_CHARSET
  Font.Color = $00E0E0E0
  Font.Height = -11
  Font.Name = 'Segoe UI'
  Font.Style = []
  FormStyle = fsStayOnTop
  OldCreateOrder = False
  ParentFont = False
  Position = poScreenCenter
  PixelsPerInch = 96
  TextHeight = 13
  OnClose = StatusFormClose
  object pnl_Top: TPanel
    Left = 0
    Top = 0
    Width = 340
    Height = 104
    Align = alTop
    BevelOuter = bvNone
    Color = $00252526
    object lbl_Status: TLabel
      Left = 12
      Top = 10
      Width = 316
      Height = 20
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = 'MCP: starting...'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00F0D090
      Font.Height = -14
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
    end
    object pnl_Stats: TPanel
      Left = 10
      Top = 34
      Width = 320
      Height = 46
      BevelOuter = bvNone
      Color = $001A1A1A
      object lbl_LblUp: TLabel
        Left = 0
        Top = 4
        Width = 80
        Height = 14
        Alignment = taCenter
        AutoSize = False
        Caption = 'UPTIME'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00808080
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValUp: TLabel
        Left = 0
        Top = 20
        Width = 80
        Height = 22
        Alignment = taCenter
        AutoSize = False
        Caption = '0s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E0E0E0
        Font.Height = -15
        Font.Name = 'Consolas'
        Font.Style = []
        ParentFont = False
      end
      object lbl_LblReq: TLabel
        Left = 80
        Top = 4
        Width = 80
        Height = 14
        Alignment = taCenter
        AutoSize = False
        Caption = 'REQUESTS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00808080
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValReq: TLabel
        Left = 80
        Top = 20
        Width = 80
        Height = 22
        Alignment = taCenter
        AutoSize = False
        Caption = '0'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E0E0E0
        Font.Height = -15
        Font.Name = 'Consolas'
        Font.Style = []
        ParentFont = False
      end
      object lbl_LblMs: TLabel
        Left = 160
        Top = 4
        Width = 80
        Height = 14
        Alignment = taCenter
        AutoSize = False
        Caption = 'ALTIUM MS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00808080
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValMs: TLabel
        Left = 160
        Top = 20
        Width = 80
        Height = 22
        Alignment = taCenter
        AutoSize = False
        Caption = '0'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E0E0E0
        Font.Height = -15
        Font.Name = 'Consolas'
        Font.Style = []
        ParentFont = False
      end
      object lbl_LblStop: TLabel
        Left = 240
        Top = 4
        Width = 80
        Height = 14
        Alignment = taCenter
        AutoSize = False
        Caption = 'DETACH IN'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00808080
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValStop: TLabel
        Left = 240
        Top = 20
        Width = 80
        Height = 22
        Alignment = taCenter
        AutoSize = False
        Caption = '60s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E0E0E0
        Font.Height = -15
        Font.Name = 'Consolas'
        Font.Style = []
        ParentFont = False
      end
    end
    object lbl_LastErr: TLabel
      Left = 12
      Top = 84
      Width = 240
      Height = 14
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = ''
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $006060FF
      Font.Height = -11
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
    object lbl_Version: TLabel
      Left = 256
      Top = 84
      Width = 74
      Height = 14
      Alignment = taRightJustify
      AutoSize = False
      Caption = 'v?'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00606060
      Font.Height = -10
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
  end
  object pnl_Controls: TPanel
    Left = 0
    Top = 104
    Width = 340
    Height = 60
    Align = alTop
    BevelOuter = bvNone
    Color = $001E1E1E
    object btn_Detach: TPanel
      Left = 10
      Top = 4
      Width = 72
      Height = 24
      BevelOuter = bvNone
      Caption = 'Detach'
      Color = $003A3A3A
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E0E0E0
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_DetachClick
      OnMouseEnter = btn_DetachEnter
      OnMouseLeave = btn_DetachLeave
    end
    object btn_ClearLog: TPanel
      Left = 88
      Top = 4
      Width = 60
      Height = 24
      BevelOuter = bvNone
      Caption = 'Clear'
      Color = $003A3A3A
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E0E0E0
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      OnClick = btn_ClearLogClick
      OnMouseEnter = btn_ClearLogEnter
      OnMouseLeave = btn_ClearLogLeave
    end
    object btn_ResetPerf: TPanel
      Left = 154
      Top = 4
      Width = 80
      Height = 24
      BevelOuter = bvNone
      Caption = 'Reset Perf'
      Color = $003A3A3A
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E0E0E0
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 2
      OnClick = btn_ResetPerfClick
      OnMouseEnter = btn_ResetPerfEnter
      OnMouseLeave = btn_ResetPerfLeave
    end
    object chk_HidePings: TPanel
      Left = 10
      Top = 34
      Width = 150
      Height = 20
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = '  [x] Hide pings'
      Color = $001E1E1E
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00D0D0D0
      Font.Height = -11
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
      TabOrder = 3
      OnClick = chk_HidePingsClick
      OnMouseEnter = chk_HidePingsEnter
      OnMouseLeave = chk_HidePingsLeave
    end
    object chk_OnlySlow: TPanel
      Left = 170
      Top = 34
      Width = 160
      Height = 20
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = '  [ ] Only >100ms'
      Color = $001E1E1E
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00D0D0D0
      Font.Height = -11
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
      TabOrder = 4
      OnClick = chk_OnlySlowClick
      OnMouseEnter = chk_OnlySlowEnter
      OnMouseLeave = chk_OnlySlowLeave
    end
  end
  object pnl_TabBar: TPanel
    Left = 0
    Top = 164
    Width = 340
    Height = 24
    Align = alTop
    BevelOuter = bvNone
    Color = $00252526
    object tab_Log: TPanel
      Left = 0
      Top = 0
      Width = 70
      Height = 24
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Log'
      Color = $001A1A1A
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00F0D090
      Font.Height = -11
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = tab_LogClick
      OnMouseEnter = tab_LogEnter
      OnMouseLeave = tab_LogLeave
    end
    object tab_Perf: TPanel
      Left = 70
      Top = 0
      Width = 70
      Height = 24
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Perf'
      Color = $00252526
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00909090
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      OnClick = tab_PerfClick
      OnMouseEnter = tab_PerfEnter
      OnMouseLeave = tab_PerfLeave
    end
  end
  object mmo_Log: TMemo
    Left = 0
    Top = 188
    Width = 340
    Height = 352
    Align = alClient
    BorderStyle = bsNone
    Color = $001A1A1A
    Font.Charset = DEFAULT_CHARSET
    Font.Color = $00D4D4D4
    Font.Height = -11
    Font.Name = 'Consolas'
    Font.Style = []
    ParentFont = False
    ReadOnly = True
    ScrollBars = ssVertical
    TabOrder = 0
  end
  object mmo_Perf: TMemo
    Left = 0
    Top = 188
    Width = 340
    Height = 352
    Align = alClient
    BorderStyle = bsNone
    Color = $001A1A1A
    Font.Charset = DEFAULT_CHARSET
    Font.Color = $00D4D4D4
    Font.Height = -11
    Font.Name = 'Consolas'
    Font.Style = []
    ParentFont = False
    ReadOnly = True
    ScrollBars = ssVertical
    TabOrder = 1
    Visible = False
  end
end
