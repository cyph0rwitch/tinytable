; TinyTable Windows installer (Inno Setup 6).
; Expects the PyInstaller output in dist\TinyTable; build.ps1 runs the whole chain:
;   iscc /DAppVersion=0.1.0 packaging\windows\installer.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "TinyTable"
#define ServerExe "tinytable-server.exe"
#define Dist "..\..\dist\TinyTable"

[Setup]
; Keep AppId fixed across releases so upgrades replace the previous install.
AppId={{3E374E12-9037-4EA4-B6D1-72EEA92B4602}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=cyph0rwitch
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no admin prompt); data is per-user anyway.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=TinyTable-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#ServerExe}
CloseApplications=yes

[Types]
Name: "standard"; Description: "Standard (local TinyTable)"
Name: "custom"; Description: "Custom"; Flags: iscustom

[Components]
Name: "core"; Description: "TinyTable server, web UI and CLI"; Types: standard custom; Flags: fixed
Name: "kindroid"; Description: "Kindroid companion sidecar"; Types: custom
Name: "matrix"; Description: "Matrix bridge"; Types: custom

[Tasks]
Name: "autostart"; Description: "Start TinyTable when I sign in"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Excludes: "tinytable-kindroid.exe,tinytable-matrix.exe"; Flags: ignoreversion recursesubdirs createallsubdirs; Components: core
Source: "{#Dist}\tinytable-kindroid.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: kindroid
Source: "{#Dist}\tinytable-matrix.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: matrix
; Seed settings once; never overwrite or remove the user's edited copy.
Source: "config.env.template"; DestDir: "{userappdata}\{#AppName}"; DestName: "config.env"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ServerExe}"; Parameters: "--open"
Name: "{group}\{#AppName} settings"; Filename: "{win}\notepad.exe"; Parameters: """{userappdata}\{#AppName}\config.env"""
Name: "{group}\{#AppName} logs"; Filename: "{localappdata}\{#AppName}\logs"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ServerExe}"; Parameters: "--open"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#ServerExe}"; Tasks: autostart

[Dirs]
Name: "{localappdata}\{#AppName}\logs"

[Run]
Filename: "{app}\{#ServerExe}"; Parameters: "--open"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent
Filename: "https://ollama.com/download/windows"; Description: "Ollama was not found. Open its download page (needed for local-model replies)"; Flags: shellexec nowait postinstall skipifsilent unchecked; Check: not OllamaInstalled

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM {#ServerExe}"; Flags: runhidden; RunOnceId: "StopTinyTable"

[Code]
function OllamaInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'));
end;
