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
      Width = 226
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
    object btn_Update: TPanel
      Left = 266
      Top = 4
      Width = 22
      Height = 18
      BevelOuter = bvNone
      Caption = #59542
      Color = $00404040
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00589EE0
      Font.Height = -12
      Font.Name = 'Segoe MDL2 Assets'
      Font.Style = []
      ParentFont = False
      TabOrder = 1
      Visible = False
      OnClick = btn_UpdateClick
      OnMouseEnter = btn_UpdateEnter
      OnMouseLeave = btn_UpdateLeave
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
      OnDblClick = pnl_K4DblClick
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
        OnDblClick = pnl_K4DblClick
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
        OnDblClick = pnl_K4DblClick
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
    object tab_Config: TPanel
      Left = 128
      Top = 2
      Width = 60
      Height = 22
      BevelOuter = bvNone
      Alignment = taCenter
      Caption = 'Config'
      Color = $00404040
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00A0A0A0
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      TabOrder = 3
      OnClick = tab_ConfigClick
      OnMouseEnter = tab_ConfigEnter
      OnMouseLeave = tab_ConfigLeave
    end
    object lbl_LogHint: TLabel
      Left = 194
      Top = 6
      Width = 118
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
    object btn_SponsorHeart: TPanel
      Left = 318
      Top = 3
      Width = 22
      Height = 18
      BevelOuter = bvNone
      Caption = #57355
      Color = $00404040
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00AA4AEA
      Font.Height = -11
      Font.Name = 'Segoe MDL2 Assets'
      Font.Style = []
      ParentFont = False
      TabOrder = 4
      OnClick = btn_SponsorClick
      OnMouseEnter = btn_SponsorHeartEnter
      OnMouseLeave = btn_SponsorHeartLeave
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
  object pnl_Sponsor: TPanel
    Left = 0
    Top = 508
    Width = 380
    Height = 68
    Align = alBottom
    BevelOuter = bvNone
    Color = $00342834
    TabOrder = 5
    Visible = False
    object pnl_SponsorEdge: TPanel
      Left = 0
      Top = 0
      Width = 3
      Height = 68
      Align = alLeft
      BevelOuter = bvNone
      Color = $00AA4AEA
      TabOrder = 0
    end
    object ico_Sponsor: TLabel
      Left = 13
      Top = 23
      Width = 22
      Height = 22
      AutoSize = False
      Caption = #57355
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00AA4AEA
      Font.Height = -18
      Font.Name = 'Segoe MDL2 Assets'
      Font.Style = []
      ParentFont = False
      Transparent = True
    end
    object lbl_SponsorTitle: TLabel
      Left = 42
      Top = 6
      Width = 234
      Height = 16
      AutoSize = False
      Caption = 'Finding eda-agent useful?'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00E5E5E5
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = [fsBold]
      ParentFont = False
      Transparent = True
    end
    object lbl_Sponsor: TLabel
      Left = 42
      Top = 22
      Width = 234
      Height = 44
      AutoSize = False
      Caption = 'Building it takes many hours of development and testing. Sponsoring helps fund new features and ongoing maintenance.'
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00D0D0D0
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = []
      ParentFont = False
      Transparent = True
      WordWrap = True
    end
    object btn_Sponsor: TPanel
      Left = 282
      Top = 23
      Width = 66
      Height = 22
      BevelOuter = bvNone
      Caption = 'Sponsor'
      Color = $006E3A8A
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00FFFFFF
      Font.Height = -11
      Font.Name = 'Segoe UI'
      Font.Style = [fsBold]
      ParentFont = False
      TabOrder = 1
      OnClick = btn_SponsorClick
      OnMouseEnter = btn_SponsorEnter
      OnMouseLeave = btn_SponsorLeave
    end
    object btn_SponsorClose: TPanel
      Left = 356
      Top = 3
      Width = 20
      Height = 18
      BevelOuter = bvNone
      Caption = #59153
      Color = $00342834
      Cursor = crHandPoint
      Font.Charset = DEFAULT_CHARSET
      Font.Color = $00808080
      Font.Height = -8
      Font.Name = 'Segoe MDL2 Assets'
      Font.Style = []
      ParentFont = False
      TabOrder = 2
      OnClick = btn_SponsorCloseClick
      OnMouseEnter = btn_SponsorCloseEnter
      OnMouseLeave = btn_SponsorCloseLeave
    end
  end
  object pnl_Config: TPanel
    Left = 0
    Top = 216
    Width = 380
    Height = 360
    Align = alClient
    BevelOuter = bvNone
    Color = $002B2B2B
    TabOrder = 4
    Visible = False
    object scr_Config: TScrollBox
      Left = 0
      Top = 0
      Width = 380
      Height = 360
      Align = alClient
      BorderStyle = bsNone
      Color = $002B2B2B
      ParentColor = False
      HorzScrollBar.Visible = False
      VertScrollBar.Tracking = True
      TabOrder = 0
      object lbl_CfgGrpPerm: TLabel
        Left = 12
        Top = 8
        Width = 340
        Height = 16
        AutoSize = False
        Caption = 'PERMISSIONS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00FFB363
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_CfgEdits: TLabel
        Left = 12
        Top = 34
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Edits to designs'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_EditsAllowed: TPanel
        Left = 166
        Top = 30
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Allowed'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 1
        OnClick = opt_EditsAllowedClick
      end
      object opt_EditsReadOnly: TPanel
        Left = 262
        Top = 30
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Read-only'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 2
        OnClick = opt_EditsReadOnlyClick
      end
      object lbl_CfgEditsHint: TLabel
        Left = 12
        Top = 58
        Width = 340
        Height = 28
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
      object sep_Cfg1: TPanel
        Left = 12
        Top = 94
        Width = 340
        Height = 1
        BevelOuter = bvNone
        Color = $00545454
        TabOrder = 3
      end
      object lbl_CfgDelete: TLabel
        Left = 12
        Top = 106
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Deleting'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_DeleteAllowed: TPanel
        Left = 166
        Top = 102
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Allowed'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 4
        OnClick = opt_DeleteAllowedClick
      end
      object opt_DeleteRefused: TPanel
        Left = 262
        Top = 102
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Refused'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 5
        OnClick = opt_DeleteRefusedClick
      end
      object lbl_CfgDeleteHint: TLabel
        Left = 12
        Top = 130
        Width = 340
        Height = 28
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
      object sep_Cfg2: TPanel
        Left = 12
        Top = 166
        Width = 340
        Height = 1
        BevelOuter = bvNone
        Color = $00545454
        TabOrder = 6
      end
      object lbl_CfgClose: TLabel
        Left = 12
        Top = 178
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Closing documents'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_CloseAllowed: TPanel
        Left = 166
        Top = 174
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Allowed'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 7
        OnClick = opt_CloseAllowedClick
      end
      object opt_CloseRefused: TPanel
        Left = 262
        Top = 174
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Refused'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 8
        OnClick = opt_CloseRefusedClick
      end
      object lbl_CfgCloseHint: TLabel
        Left = 12
        Top = 202
        Width = 340
        Height = 28
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
      object sep_Cfg3: TPanel
        Left = 12
        Top = 238
        Width = 340
        Height = 1
        BevelOuter = bvNone
        Color = $00545454
        TabOrder = 9
      end
      object lbl_CfgAuto: TLabel
        Left = 12
        Top = 250
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Menus, dialogs, keys'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_AutoOn: TPanel
        Left = 166
        Top = 246
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 10
        OnClick = opt_AutoOnClick
      end
      object opt_AutoOff: TPanel
        Left = 262
        Top = 246
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 11
        OnClick = opt_AutoOffClick
      end
      object lbl_CfgAutoHint: TLabel
        Left = 12
        Top = 274
        Width = 340
        Height = 28
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
      object sep_Cfg4: TPanel
        Left = 12
        Top = 310
        Width = 340
        Height = 1
        BevelOuter = bvNone
        Color = $00545454
        TabOrder = 12
      end
      object lbl_CfgGrpView: TLabel
        Left = 12
        Top = 322
        Width = 340
        Height = 16
        AutoSize = False
        Caption = 'VIEW'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00FFB363
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_CfgView: TLabel
        Left = 12
        Top = 348
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Keep board in view'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_ViewOff: TPanel
        Left = 166
        Top = 344
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 13
        OnClick = opt_ViewOffClick
      end
      object opt_ViewOn: TPanel
        Left = 262
        Top = 344
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 14
        OnClick = opt_ViewOnClick
      end
      object lbl_CfgViewHint: TLabel
        Left = 12
        Top = 372
        Width = 340
        Height = 28
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
      object sep_Cfg5: TPanel
        Left = 12
        Top = 408
        Width = 340
        Height = 1
        BevelOuter = bvNone
        Color = $00545454
        TabOrder = 15
      end
      object lbl_CfgGrpValues: TLabel
        Left = 12
        Top = 420
        Width = 340
        Height = 16
        AutoSize = False
        Caption = 'VALUES'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00FFB363
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_CfgValue: TLabel
        Left = 12
        Top = 442
        Width = 340
        Height = 16
        AutoSize = False
        Caption = 'Component value format'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_FmtNone: TPanel
        Left = 12
        Top = 462
        Width = 82
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'None'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 16
        OnClick = opt_FmtNoneClick
      end
      object opt_FmtRkm: TPanel
        Left = 98
        Top = 462
        Width = 82
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = '4K7  2u2'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 17
        OnClick = opt_FmtRkmClick
      end
      object opt_FmtSi: TPanel
        Left = 184
        Top = 462
        Width = 82
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = '4.7k  2.2u'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 18
        OnClick = opt_FmtSiClick
      end
      object opt_FmtSiUnit: TPanel
        Left = 270
        Top = 462
        Width = 82
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = '4.7k  2.2uF'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 19
        OnClick = opt_FmtSiUnitClick
      end
      object lbl_CfgValueHint: TLabel
        Left = 12
        Top = 490
        Width = 340
        Height = 28
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
      object sep_Cfg6: TPanel
        Left = 12
        Top = 526
        Width = 340
        Height = 1
        BevelOuter = bvNone
        Color = $00545454
        TabOrder = 20
      end
      object lbl_CfgGrpEco: TLabel
        Left = 12
        Top = 538
        Width = 340
        Height = 16
        AutoSize = False
        Caption = 'DIALOGS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00FFB363
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_CfgEco: TLabel
        Left = 12
        Top = 564
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Tool dialogs'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_EcoAuto: TPanel
        Left = 166
        Top = 560
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Automatic'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 21
        OnClick = opt_EcoAutoClick
      end
      object opt_EcoManual: TPanel
        Left = 262
        Top = 560
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Manual'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 22
        OnClick = opt_EcoManualClick
      end
      object lbl_CfgEcoHint: TLabel
        Left = 12
        Top = 588
        Width = 340
        Height = 28
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
      object sep_Cfg7: TPanel
        Left = 12
        Top = 624
        Width = 340
        Height = 1
        BevelOuter = bvNone
        Color = $00545454
        TabOrder = 23
      end
      object lbl_CfgGrpChecks: TLabel
        Left = 12
        Top = 636
        Width = 340
        Height = 16
        AutoSize = False
        Caption = 'CHECKS'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00FFB363
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object lbl_CfgChkFp: TLabel
        Left = 12
        Top = 664
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Footprint vs drawing'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_ChkFpOn: TPanel
        Left = 166
        Top = 660
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 24
        OnClick = opt_ChkFpOnClick
      end
      object opt_ChkFpOff: TPanel
        Left = 262
        Top = 660
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 25
        OnClick = opt_ChkFpOffClick
      end
      object lbl_CfgChkSym: TLabel
        Left = 12
        Top = 694
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Symbol vs pin table'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_ChkSymOn: TPanel
        Left = 166
        Top = 690
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 26
        OnClick = opt_ChkSymOnClick
      end
      object opt_ChkSymOff: TPanel
        Left = 262
        Top = 690
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 27
        OnClick = opt_ChkSymOffClick
      end
      object lbl_CfgChkPad: TLabel
        Left = 12
        Top = 724
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Symbol pins vs pads'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_ChkPadOn: TPanel
        Left = 166
        Top = 720
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 28
        OnClick = opt_ChkPadOnClick
      end
      object opt_ChkPadOff: TPanel
        Left = 262
        Top = 720
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 29
        OnClick = opt_ChkPadOffClick
      end
      object lbl_CfgChkBody: TLabel
        Left = 12
        Top = 754
        Width = 150
        Height = 16
        AutoSize = False
        Caption = '3D body vs drawing'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_ChkBodyOn: TPanel
        Left = 166
        Top = 750
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 30
        OnClick = opt_ChkBodyOnClick
      end
      object opt_ChkBodyOff: TPanel
        Left = 262
        Top = 750
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 31
        OnClick = opt_ChkBodyOffClick
      end
      object lbl_CfgChkSpice: TLabel
        Left = 12
        Top = 784
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'SPICE model file'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_ChkSpiceOn: TPanel
        Left = 166
        Top = 780
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 32
        OnClick = opt_ChkSpiceOnClick
      end
      object opt_ChkSpiceOff: TPanel
        Left = 262
        Top = 780
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 33
        OnClick = opt_ChkSpiceOffClick
      end
      object lbl_CfgChkFab: TLabel
        Left = 12
        Top = 814
        Width = 150
        Height = 16
        AutoSize = False
        Caption = 'Fab export'
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -12
        Font.Name = 'Segoe UI'
        Font.Style = [fsBold]
        ParentFont = False
      end
      object opt_ChkFabOn: TPanel
        Left = 166
        Top = 810
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'On'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 34
        OnClick = opt_ChkFabOnClick
      end
      object opt_ChkFabOff: TPanel
        Left = 262
        Top = 810
        Width = 90
        Height = 24
        BevelOuter = bvNone
        Alignment = taCenter
        Caption = 'Off'
        Color = $00545454
        Cursor = crHandPoint
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00E5E5E5
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
        TabOrder = 35
        OnClick = opt_ChkFabOffClick
      end
      object lbl_CfgChecksHint: TLabel
        Left = 12
        Top = 842
        Width = 340
        Height = 42
        AutoSize = False
        WordWrap = True
        Caption = ''
        Font.Charset = DEFAULT_CHARSET
        Font.Color = $00A0A0A0
        Font.Height = -11
        Font.Name = 'Segoe UI'
        Font.Style = []
        ParentFont = False
      end
    end
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
