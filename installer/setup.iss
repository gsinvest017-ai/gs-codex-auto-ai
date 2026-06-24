; setup.iss — CodexAutoAI 桌面 App 安裝檔（Inno Setup 6）
; 免系統管理員、裝到 %LOCALAPPDATA%、桌面 + 開始選單捷徑（金色圖示）。
; 由 build-installer.ps1 以 /DAppVersion=<ver> 編譯；payload 由該腳本預先備妥。

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{B3E1B7C2-9A4D-4E6F-8C1A-CODEXAUTOAI01}
AppName=CodexAutoAI
AppVersion={#AppVersion}
AppPublisher=GS Invest
DefaultDirName={localappdata}\CodexAutoAI
DefaultGroupName=CodexAutoAI
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
SetupIconFile=..\desktop\codexautoai.ico
OutputDir=..\dist
OutputBaseFilename=CodexAutoAI-setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "捷徑:"

[Files]
; payload\ 由 build-installer.ps1 備妥（框架檔 + dist\CodexAutoAI.exe + 圖示）
Source: "payload\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\CodexAutoAI"; Filename: "{app}\CodexAutoAI.exe"; WorkingDir: "{app}"; IconFilename: "{app}\desktop\codexautoai.ico"
Name: "{autodesktop}\CodexAutoAI"; Filename: "{app}\CodexAutoAI.exe"; WorkingDir: "{app}"; IconFilename: "{app}\desktop\codexautoai.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\CodexAutoAI.exe"; Description: "立即啟動 CodexAutoAI"; Flags: nowait postinstall skipifsilent
