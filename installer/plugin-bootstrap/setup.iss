; setup.iss — Codex plugin for Claude Code 的一鍵安裝執行檔（Inno Setup 6）
;
; 這支「安裝檔」不裝任何檔案到硬碟——它要做的事是把 plugin 設定進使用者既有的
; Claude Code。所以刻意關掉 CreateAppDir 與 Uninstallable：
;   * 沒有 app 目錄要建（腳本跑完就沒事了）
;   * 沒有「解除安裝」可言（要移除請用 `claude plugin uninstall`，不是控制台）
; 留一個空的解除安裝項目在「應用程式與功能」裡，只會讓人以為按了就能移乾淨。
;
; 由 build.ps1 以 /DAppVersion=<ver> 編譯。

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{7F2C4A91-63D5-4B18-9E77-CODEXPLUGIN01}
AppName=Codex plugin for Claude Code
AppVersion={#AppVersion}
AppPublisher=GS Invest
; 不建安裝目錄、不留解除安裝項目——見檔頭說明
CreateAppDir=no
Uninstallable=no
; 只設定目前使用者的 Claude Code，不需要系統管理員
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
SetupIconFile=..\..\desktop\codexautoai.ico
OutputDir=..\..\dist
OutputBaseFilename=CodexPlugin-setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
DisableReadyPage=yes
; 裝完直接看得到腳本輸出，不要讓使用者對著一個「完成」畫面猜發生了什麼
AlwaysShowDirOnReadyPage=no

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=安裝 Codex plugin for Claude Code
WelcomeLabel2=這會把 OpenAI 官方的 Codex plugin 設定進你的 Claude Code。%n%n裝好之後，在 Claude Code 裡可以用：%n%n    /codex:review — 請 Codex 審查目前的 git 變更%n    /codex:adversarial-review — 請 Codex 唱反調，挑戰設計取捨%n    /codex:rescue — 卡住時把任務交給 Codex 接手%n    /codex:transfer — 把整段 session 轉給 Codex 繼續%n%n需要 Claude Code 與 Node.js；缺少的部分會自動安裝。%n可以重複執行，已完成的步驟會自動跳過。
FinishedLabel=設定完成。%n%n開一個新的 Claude Code session，輸入 /codex: 就會看到上面四個指令。%n%n若視窗顯示尚未登入 Codex，請執行：codex login

[Files]
; 唯一的酬載就是那支腳本；解壓到暫存、跑完即棄（deleteafterinstall）
Source: "Install-CodexPlugin.ps1"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Run]
; 用 powershell.exe（Windows 內建）而不是 pwsh——內部機器不一定裝了 PowerShell 7。
; -ExecutionPolicy Bypass 是必要的：預設的 Restricted / RemoteSigned 會擋掉從網路
; 下載來的未簽章腳本，而這支腳本正是從安裝檔解壓出來的。
; runascurrentuser：plugin 要裝進「目前這個人」的 ~/.claude，提權會裝錯使用者。
; **在安裝階段跑，不是 postinstall。** postinstall 是使用者按下「完成」才執行，
; 但 deleteafterinstall 在那之前就把 {tmp} 的腳本清掉了——兩個旗標湊在一起會變成
; 按完成之後找不到檔案。改成安裝中執行（waituntilterminated），清理必然在其後。
; -Pause 讓主控台等使用者看完再關；沒有它視窗會一閃而過。
Filename: "powershell.exe"; \
  Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{tmp}\Install-CodexPlugin.ps1"" -Pause"; \
  StatusMsg: "正在設定 Codex plugin（會開一個視窗顯示進度）…"; \
  Flags: runascurrentuser waituntilterminated
