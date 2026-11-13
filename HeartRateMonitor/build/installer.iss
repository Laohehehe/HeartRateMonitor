; ============================================================================
; 心率监测器 —— Inno Setup 安装包脚本
;
; 版本号规则：跟随发布日期（本次 26.11.13）
;
; 编译（发布目录必须显式传入）：
;   ISCC.exe /DPublishDir="D:\path\to\publish" installer.iss
;
; 产物：build\output\HeartRateMonitor-<版本>-setup.exe
; ============================================================================

#define MyAppName "心率监测器"
#define MyAppVersion "26.11.13"
#define MyAppPublisher "Laohehehe"
#define MyAppExeName "HeartRateMonitor.exe"
#define MyAppUrl "https://github.com/Laohehehe/HeartRateMonitor"

; 发布产物目录，由命令行 /DPublishDir 传入
#ifndef PublishDir
  #error 请在命令行指定发布目录，例如：ISCC.exe /DPublishDir="D:\DevTools\caches\hrm-publish" installer.iss
#endif

[Setup]
; AppId 一经确定不要修改，否则升级会变成并存安装
AppId={{8F3C2B14-7A61-4E2B-9C55-2D7E6A1B9F30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}
AppUpdatesURL={#MyAppUrl}/releases
DefaultDirName={autopf}\HeartRateMonitor
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=HeartRateMonitor-{#MyAppVersion}-setup
SetupIconFile=..\Assets\tray_icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#PublishDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
