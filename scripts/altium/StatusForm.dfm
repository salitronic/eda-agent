object StatusForm: TStatusForm
  Left = 200
  Top = 200
  BorderIcons = [biSystemMenu, biMinimize]
  BorderStyle = bsSizeable
  Caption = 'EDA Agent MCP'
  ClientHeight = 600
  ClientWidth = 380
  Color = $00353535
  Constraints.MinHeight = 400
  Constraints.MinWidth = 396
  Font.Charset = DEFAULT_CHARSET
  Font.Color = $00E5E5E5
  Font.Height = -11
  Font.Name = 'Segoe UI'
  Font.Style = []
  ParentFont = False
  FormStyle = fsStayOnTop
  OldCreateOrder = False
  Position = poDesigned
  PixelsPerInch = 96
  TextHeight = 13
  OnClose = StatusFormClose
  object pnl_Header: TPanel
    Left = 0
    Top = 0
    Width = 380
    Height = 26
    Align = alTop
    BevelOuter = bvNone
    Color = $00404040
    object pnl_StatusDot: TPanel
      Left = 10
      Top = 9
      Width = 8
      Height = 8
      BevelOuter = bvNone
      Caption = ''
      Color = $0064C46B
    end
    object lbl_Spinner: TLabel
      Left = 24
      Top = 5
      Width = 12
      Height = 16
      AutoSize = False
      Caption = ''
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00FFB363
      Font.Height = -12
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
    object lbl_Status: TLabel
      Left = 38
      Top = 5
      Width = 250
      Height = 16
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = 'idle'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -12
      Font.Name = 'Segoe UI Semibold'
      Font.Style = []
      ParentFont = False
    end
    object lbl_Version: TLabel
      Left = 288
      Top = 7
      Width = 84
      Height = 14
      Alignment = taRightJustify
      AutoSize = False
      Caption = 'v?'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00808080
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
  end
  object pnl_KPIs: TPanel
    Left = 0
    Top = 26
    Width = 380
    Height = 76
    Align = alTop
    BevelOuter = bvNone
    Color = $00353535
    object pnl_K1: TPanel
      Left = 8
      Top = 8
      Width = 86
      Height = 62
      BevelOuter = bvNone
      Color = $00404040
      object lbl_LblUp: TLabel
        Left = 0
        Top = 9
        Width = 86
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'UPTIME'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValUp: TLabel
        Left = 0
        Top = 25
        Width = 86
        Height = 28
        Alignment = taCenter
        AutoSize = False
        Caption = '0s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -19
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K2: TPanel
      Left = 99
      Top = 8
      Width = 86
      Height = 62
      BevelOuter = bvNone
      Color = $00404040
      object lbl_LblReq: TLabel
        Left = 0
        Top = 9
        Width = 86
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'REQUESTS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValReq: TLabel
        Left = 0
        Top = 25
        Width = 86
        Height = 28
        Alignment = taCenter
        AutoSize = False
        Caption = '0'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -19
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K3: TPanel
      Left = 190
      Top = 8
      Width = 86
      Height = 62
      BevelOuter = bvNone
      Color = $00404040
      object lbl_LblMs: TLabel
        Left = 0
        Top = 9
        Width = 86
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'BUSY TIME'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValMs: TLabel
        Left = 0
        Top = 25
        Width = 86
        Height = 28
        Alignment = taCenter
        AutoSize = False
        Caption = '0s'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -19
        Font.Name = 'Segoe UI Semibold'
        Font.Style = []
        ParentFont = False
      end
    end
    object pnl_K4: TPanel
      Left = 281
      Top = 8
      Width = 91
      Height = 62
      BevelOuter = bvNone
      Color = $00404040
      object lbl_LblStop: TLabel
        Left = 0
        Top = 9
        Width = 91
        Height = 12
        Alignment = taCenter
        AutoSize = False
        Caption = 'AUTO-OFF IN'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -9
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_ValStop: TLabel
        Left = 0
        Top = 25
        Width = 91
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
    Top = 102
    Width = 380
    Height = 0
    Align = alTop
    BevelOuter = bvNone
    Color = $00353535
    object lbl_LastErr: TLabel
      Left = 10
      Top = 1
      Width = 360
      Height = 14
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = ''
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $006D6BFF
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
  end
  object pnl_Controls: TPanel
    Left = 0
    Top = 102
    Width = 380
    Height = 90
    Align = alTop
    BevelOuter = bvNone
    Color = $00353535
    object btn_OpenWeb: TPanel
      Left = 8
      Top = 4
      Width = 118
      Height = 24
      BevelOuter = bvNone
      Caption = 'Dashboard'
      Color = $00404040
      Cursor = crDefault
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00808080
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = btn_OpenWebClick
      OnMouseEnter = btn_OpenWebEnter
      OnMouseLeave = btn_OpenWebLeave
      object ico_OpenWeb: TLabel
        Left = 9
        Top = 5
        Width = 16
        Height = 16
        AutoSize = False
        Caption = #59252
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00808080
        Font.Height = -12
        Font.Name = 'Segoe MDL2 Assets'
        Font.Style = []
        ParentFont = False
        Transparent = True
        OnClick = btn_OpenWebClick
        OnMouseEnter = btn_OpenWebEnter
      end
    end
    object btn_Pause: TPanel
      Left = 131
      Top = 4
      Width = 118
      Height = 24
      BevelOuter = bvNone
      Caption = 'Pause'
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      OnClick = btn_PauseClick
      OnMouseEnter = btn_PauseEnter
      OnMouseLeave = btn_PauseLeave
      object ico_Pause: TLabel
        Left = 16
        Top = 5
        Width = 16
        Height = 16
        AutoSize = False
        Caption = #59241
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe MDL2 Assets'
        Font.Style = []
        ParentFont = False
        Transparent = True
        OnClick = btn_PauseClick
        OnMouseEnter = btn_PauseEnter
      end
      object ico_Resume: TLabel
        Left = 16
        Top = 5
        Width = 16
        Height = 16
        AutoSize = False
        Caption = #59240
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00111111
        Font.Height = -12
        Font.Name = 'Segoe MDL2 Assets'
        Font.Style = []
        ParentFont = False
        Transparent = True
        Visible = False
        OnClick = btn_PauseClick
        OnMouseEnter = btn_PauseEnter
      end
    end
    object btn_Renew: TPanel
      Left = 254
      Top = 4
      Width = 118
      Height = 24
      BevelOuter = bvNone
      Caption = 'Renew'
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 2
      OnClick = btn_RenewClick
      OnMouseEnter = btn_RenewEnter
      OnMouseLeave = btn_RenewLeave
      object ico_Renew: TLabel
        Left = 19
        Top = 5
        Width = 16
        Height = 16
        AutoSize = False
        Caption = #59180
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe MDL2 Assets'
        Font.Style = []
        ParentFont = False
        Transparent = True
        OnClick = btn_RenewClick
        OnMouseEnter = btn_RenewEnter
      end
    end
    object btn_ClearLog: TPanel
      Left = 8
      Top = 32
      Width = 118
      Height = 24
      BevelOuter = bvNone
      Caption = 'Clear log'
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 3
      OnClick = btn_ClearLogClick
      OnMouseEnter = btn_ClearLogEnter
      OnMouseLeave = btn_ClearLogLeave
      object ico_ClearLog: TLabel
        Left = 13
        Top = 5
        Width = 16
        Height = 16
        AutoSize = False
        Caption = #59213
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe MDL2 Assets'
        Font.Style = []
        ParentFont = False
        Transparent = True
        OnClick = btn_ClearLogClick
        OnMouseEnter = btn_ClearLogEnter
      end
    end
    object btn_Saving: TPanel
      Left = 131
      Top = 32
      Width = 118
      Height = 24
      BevelOuter = bvNone
      Caption = 'Saving on'
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 4
      OnClick = btn_SavingClick
      OnMouseEnter = btn_SavingEnter
      OnMouseLeave = btn_SavingLeave
      object ico_Saving: TLabel
        Left = 9
        Top = 5
        Width = 16
        Height = 16
        AutoSize = False
        Caption = #59214
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe MDL2 Assets'
        Font.Style = []
        ParentFont = False
        Transparent = True
        OnClick = btn_SavingClick
        OnMouseEnter = btn_SavingEnter
      end
    end
    object btn_Detach: TPanel
      Left = 254
      Top = 32
      Width = 118
      Height = 24
      BevelOuter = bvNone
      Caption = 'Detach'
      Color = $003030C8
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00FFFFFF
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 5
      OnClick = btn_DetachClick
      OnMouseEnter = btn_DetachEnter
      OnMouseLeave = btn_DetachLeave
      object ico_Detach: TLabel
        Left = 18
        Top = 5
        Width = 16
        Height = 16
        AutoSize = False
        Caption = #59368
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00FFFFFF
        Font.Height = -12
        Font.Name = 'Segoe MDL2 Assets'
        Font.Style = []
        ParentFont = False
        Transparent = True
        OnClick = btn_DetachClick
        OnMouseEnter = btn_DetachEnter
      end
    end
    object ico_Filter: TLabel
      Left = 10
      Top = 66
      Width = 16
      Height = 16
      AutoSize = False
      Caption = #59164
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00A0A0A0
      Font.Height = -12
      Font.Name = 'Segoe MDL2 Assets'
      Font.Style = []
      ParentFont = False
      Transparent = True
    end
    object edt_Filter: TEdit
      Left = 30
      Top = 64
      Width = 138
      Height = 20
      BorderStyle = bsNone
      Color = $002B2B2B
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
      TabOrder = 6
      Text = ''
      OnChange = edt_FilterChange
    end
    object chk_HidePings: TPanel
      Left = 176
      Top = 64
      Width = 62
      Height = 20
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Hide pings'
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 7
      OnClick = chk_HidePingsClick
      OnMouseEnter = chk_HidePingsEnter
      OnMouseLeave = chk_HidePingsLeave
    end
    object chk_OnlySlow: TPanel
      Left = 242
      Top = 64
      Width = 62
      Height = 20
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Slow only'
      Color = $00353535
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00A0A0A0
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 8
      OnClick = chk_OnlySlowClick
      OnMouseEnter = chk_OnlySlowEnter
      OnMouseLeave = chk_OnlySlowLeave
    end
    object chk_OnTop: TPanel
      Left = 308
      Top = 64
      Width = 64
      Height = 20
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'On top'
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 9
      OnClick = chk_OnTopClick
      OnMouseEnter = chk_OnTopEnter
      OnMouseLeave = chk_OnTopLeave
    end
  end
  object pnl_TabBar: TPanel
    Left = 0
    Top = 192
    Width = 380
    Height = 24
    Align = alTop
    BevelOuter = bvNone
    Color = $00404040
    object tab_Log: TPanel
      Left = 4
      Top = 2
      Width = 60
      Height = 22
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Log'
      Color = $002B2B2B
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 0
      OnClick = tab_LogClick
      OnMouseEnter = tab_LogEnter
      OnMouseLeave = tab_LogLeave
    end
    object tab_Perf: TPanel
      Left = 66
      Top = 2
      Width = 60
      Height = 22
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Perf'
      Color = $00404040
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00A0A0A0
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
      Left = 132
      Top = 6
      Width = 204
      Height = 14
      Alignment = taRightJustify
      AutoSize = False
      Caption = 'newest at top'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00808080
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
    object btn_CopyLog: TPanel
      Left = 344
      Top = 3
      Width = 28
      Height = 18
      BevelOuter = bvNone
      Caption = #59592
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -12
      Font.Name = 'Segoe MDL2 Assets'
      Font.Style = []
      ParentFont = False
      TabOrder = 2
      OnClick = btn_CopyLogClick
      OnMouseLeave = btn_CopyLogLeave
    end
  end
  object mmo_Log: TMemo
    Left = 0
    Top = 216
    Width = 380
    Height = 360
    Align = alClient
    BorderStyle = bsNone
    Color = $002B2B2B
    Font.Charset = DEFAULT_CHARSET
    Font.Color = $00DCDCDC
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
    Top = 216
    Width = 380
    Height = 360
    Align = alClient
    BorderStyle = bsNone
    Color = $002B2B2B
    Font.Charset = DEFAULT_CHARSET
    Font.Color = $00DCDCDC
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
    Top = 576
    Width = 380
    Height = 24
    Align = alBottom
    BevelOuter = bvNone
    Color = $00404040
    object lbl_FooterHint: TLabel
      Left = 8
      Top = 5
      Width = 60
      Height = 14
      AutoSize = False
      Caption = 'Standalone:'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00808080
      Font.Height = -10
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
    end
    object lbl_LaunchCmd: TLabel
      Left = 68
      Top = 5
      Width = 272
      Height = 14
      AutoSize = False
      EllipsisPosition = epEndEllipsis
      Caption = 'python -m eda_agent.server dashboard --port 8766'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00A0A0A0
      Font.Height = -10
      Font.Name = 'Consolas'
      Font.Style = []
      ParentFont = False
    end
    object btn_CopyCmd: TPanel
      Left = 344
      Top = 3
      Width = 28
      Height = 18
      BevelOuter = bvNone
      Caption = #59592
      Color = $00545454
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -12
      Font.Name = 'Segoe MDL2 Assets'
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
