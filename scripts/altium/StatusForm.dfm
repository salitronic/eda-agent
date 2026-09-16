object StatusForm: TStatusForm
  Left = 200
  Top = 200
  BorderIcons = [biSystemMenu, biMinimize]
  BorderStyle = bsSizeable
  Caption = 'EDA Agent MCP'
  ClientHeight = 650
  ClientWidth = 380
  Color = $001A1B1E
  Constraints.MinHeight = 500
  Constraints.MinWidth = 360
  Font.Charset = DEFAULT_CHARSET
  Font.Color = $00E1E2E6
  Font.Height = -11
  Font.Name = 'Segoe UI'
  Font.Style = []
  FormStyle = fsStayOnTop
  OldCreateOrder = False
  ParentFont = False
  Position = poDesigned
  PixelsPerInch = 96
  TextHeight = 13
  OnClose = StatusFormClose
  object pnl_Header: TPanel
    Left = 0
    Top = 0
    Width = 380
    Height = 48
    Align = alTop
    BevelOuter = bvNone
    Color = $00202126
    object pnl_StatusDot: TPanel
      Left = 14
      Top = 19
      Width = 10
      Height = 10
      BevelOuter = bvNone
      Caption = ''
      Color = $0064C46B
    end
    object lbl_Spinner: TLabel
      Left = 28
      Top = 14
      Width = 14
      Height = 18
      AutoSize = False
      Alignment = taCenter
      Caption = ''
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00FF9E4A
      Font.Height = -14
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
    object lbl_Status: TLabel
      Left = 46
      Top = 14
      Width = 244
      Height = 20
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = 'idle'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E1E2E6
      Font.Height = -13
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
    end
    object lbl_Version: TLabel
      Left = 290
      Top = 18
      Width = 80
      Height = 14
      Alignment = taRightJustify
      AutoSize = False
      Caption = 'v?'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00696C73
      Font.Height = -10
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
  end
  object pnl_KPIs: TPanel
    Left = 0
    Top = 48
    Width = 380
    Height = 76
    Align = alTop
    BevelOuter = bvNone
    Color = $001A1B1E
    object pnl_K1: TPanel
      Left = 12
      Top = 6
      Width = 84
      Height = 64
      BevelOuter = bvNone
      Color = $0025262B
      object lbl_LblUp: TLabel
        Left = 0
        Top = 8
        Width = 84
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'UPTIME'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $008B8E96
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValUp: TLabel
        Left = 0
        Top = 24
        Width = 84
        Height = 28
        Alignment = taCenter
        AutoSize = False
        Caption = '0s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E1E2E6
        Font.Height = -19
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K2: TPanel
      Left = 104
      Top = 6
      Width = 84
      Height = 64
      BevelOuter = bvNone
      Color = $0025262B
      object lbl_LblReq: TLabel
        Left = 0
        Top = 8
        Width = 84
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'REQUESTS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $008B8E96
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValReq: TLabel
        Left = 0
        Top = 24
        Width = 84
        Height = 28
        Alignment = taCenter
        AutoSize = False
        Caption = '0'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E1E2E6
        Font.Height = -19
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K3: TPanel
      Left = 196
      Top = 6
      Width = 84
      Height = 64
      BevelOuter = bvNone
      Color = $0025262B
      object lbl_LblMs: TLabel
        Left = 0
        Top = 8
        Width = 84
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'BUSY TIME'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $008B8E96
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValMs: TLabel
        Left = 0
        Top = 24
        Width = 84
        Height = 28
        Alignment = taCenter
        AutoSize = False
        Caption = '0s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E1E2E6
        Font.Height = -19
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K4: TPanel
      Left = 288
      Top = 6
      Width = 80
      Height = 64
      BevelOuter = bvNone
      Color = $0025262B
      object lbl_LblStop: TLabel
        Left = 0
        Top = 8
        Width = 80
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'AUTO-OFF IN'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $008B8E96
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValStop: TLabel
        Left = 0
        Top = 24
        Width = 80
        Height = 28
        Alignment = taCenter
        AutoSize = False
        Caption = '10m'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $0064C46B
        Font.Height = -19
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
  end
  object pnl_ErrBar: TPanel
    Left = 0
    Top = 124
    Width = 380
    Height = 18
    Align = alTop
    BevelOuter = bvNone
    Color = $001A1B1E
    object lbl_LastErr: TLabel
      Left = 14
      Top = 2
      Width = 354
      Height = 14
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = ''
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $006D6BFF
      Font.Height = -10
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
  end
  object pnl_Primary: TPanel
    Left = 0
    Top = 142
    Width = 380
    Height = 40
    Align = alTop
    BevelOuter = bvNone
    Color = $001A1B1E
    object btn_OpenWeb: TPanel
      Left = 12
      Top = 6
      Width = 356
      Height = 28
      BevelOuter = bvNone
      Caption = 'Open Dashboard  (waiting for MCP)'
      Color = $0025262B
      Cursor = crDefault
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00696C73
      Font.Height = -12
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_OpenWebClick
      OnMouseEnter = btn_OpenWebEnter
      OnMouseLeave = btn_OpenWebLeave
    end
  end
  object pnl_Controls: TPanel
    Left = 0
    Top = 182
    Width = 380
    Height = 70
    Align = alTop
    BevelOuter = bvNone
    Color = $001A1B1E
    object btn_Pause: TPanel
      Left = 12
      Top = 4
      Width = 80
      Height = 24
      BevelOuter = bvNone
      Caption = 'Pause'
      Color = $002A2C32
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E1E2E6
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_PauseClick
      OnMouseEnter = btn_PauseEnter
      OnMouseLeave = btn_PauseLeave
    end
    object btn_Renew: TPanel
      Left = 96
      Top = 4
      Width = 80
      Height = 24
      BevelOuter = bvNone
      Caption = 'Renew'
      Color = $002A2C32
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E1E2E6
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      OnClick = btn_RenewClick
      OnMouseEnter = btn_RenewEnter
      OnMouseLeave = btn_RenewLeave
    end
    object btn_ClearLog: TPanel
      Left = 180
      Top = 4
      Width = 80
      Height = 24
      BevelOuter = bvNone
      Caption = 'Clear log'
      Color = $002A2C32
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E1E2E6
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 2
      OnClick = btn_ClearLogClick
      OnMouseEnter = btn_ClearLogEnter
      OnMouseLeave = btn_ClearLogLeave
    end
    object btn_Detach: TPanel
      Left = 288
      Top = 4
      Width = 80
      Height = 24
      BevelOuter = bvNone
      Caption = 'Detach'
      Color = $00B14545
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00FFFFFF
      Font.Height = -11
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
      TabOrder = 3
      OnClick = btn_DetachClick
      OnMouseEnter = btn_DetachEnter
      OnMouseLeave = btn_DetachLeave
    end
    object lbl_FilterLabel: TLabel
      Left = 14
      Top = 40
      Width = 30
      Height = 14
      Caption = 'filter'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $008B8E96
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = [fsBold]
      ParentFont = False
    end
    object edt_Filter: TEdit
      Left = 48
      Top = 38
      Width = 108
      Height = 20
      BorderStyle = bsNone
      Color = $0025262B
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E1E2E6
      Font.Height = -11
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
      TabOrder = 4
      Text = ''
      OnChange = edt_FilterChange
    end
    object chk_HidePings: TPanel
      Left = 162
      Top = 40
      Width = 70
      Height = 18
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = ' [x] pings'
      Color = $001A1B1E
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00C9CBD1
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 5
      OnClick = chk_HidePingsClick
      OnMouseEnter = chk_HidePingsEnter
      OnMouseLeave = chk_HidePingsLeave
    end
    object chk_OnlySlow: TPanel
      Left = 236
      Top = 40
      Width = 80
      Height = 18
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = ' [ ] >100ms'
      Color = $001A1B1E
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00C9CBD1
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 6
      OnClick = chk_OnlySlowClick
      OnMouseEnter = chk_OnlySlowEnter
      OnMouseLeave = chk_OnlySlowLeave
    end
    object chk_OnTop: TPanel
      Left = 320
      Top = 40
      Width = 56
      Height = 18
      BevelOuter = bvNone
      Alignment = taLeftJustify
      Caption = ' [x] pin'
      Color = $001A1B1E
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00C9CBD1
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 7
      OnClick = chk_OnTopClick
      OnMouseEnter = chk_OnTopEnter
      OnMouseLeave = chk_OnTopLeave
    end
  end
  object pnl_TabBar: TPanel
    Left = 0
    Top = 252
    Width = 380
    Height = 26
    Align = alTop
    BevelOuter = bvNone
    Color = $00202126
    object tab_Log: TPanel
      Left = 12
      Top = 0
      Width = 64
      Height = 26
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Log'
      Color = $001A1B1E
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00FF9E4A
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
      Left = 76
      Top = 0
      Width = 64
      Height = 26
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Perf'
      Color = $00202126
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $008B8E96
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      OnClick = tab_PerfClick
      OnMouseEnter = tab_PerfEnter
      OnMouseLeave = tab_PerfLeave
    end
    object lbl_LogHint: TLabel
      Left = 140
      Top = 7
      Width = 228
      Height = 14
      AutoSize = False
      Alignment = taRightJustify
      Caption = 'newest at top'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00696C73
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
  end
  object mmo_Log: TMemo
    Left = 0
    Top = 278
    Width = 380
    Height = 342
    Align = alClient
    BorderStyle = bsNone
    Color = $0017181B
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
    Top = 278
    Width = 380
    Height = 342
    Align = alClient
    BorderStyle = bsNone
    Color = $0017181B
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
  object pnl_Footer: TPanel
    Left = 0
    Top = 620
    Width = 380
    Height = 30
    Align = alBottom
    BevelOuter = bvNone
    Color = $0017181B
    object lbl_FooterHint: TLabel
      Left = 12
      Top = 3
      Width = 356
      Height = 11
      AutoSize = False
      Caption = 'Run standalone (no AI needed):'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00696C73
      Font.Height = -9
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
    object lbl_LaunchCmd: TLabel
      Left = 12
      Top = 14
      Width = 280
      Height = 13
      AutoSize = False
      Caption = 'python -m eda_agent.server dashboard --port 8766'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00D4D4D4
      Font.Height = -11
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
    object btn_CopyCmd: TPanel
      Left = 300
      Top = 6
      Width = 68
      Height = 20
      BevelOuter = bvNone
      Caption = 'Copy'
      Color = $002A2C32
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E1E2E6
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_CopyCmdClick
    end
  end
  object tmr_Spinner: TTimer
    Enabled = False
    Interval = 120
    OnTimer = tmr_SpinnerTimer
    Left = 340
    Top = 8
  end
  object tmr_Poll: TTimer
    Enabled = False
    Interval = 10
    OnTimer = tmr_PollTimer
    Left = 308
    Top = 8
  end
end
