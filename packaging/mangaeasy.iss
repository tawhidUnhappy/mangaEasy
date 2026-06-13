; Inno Setup script for mangaEasy Windows installer
; Wraps the PyInstaller one-folder build into a standard Windows Setup.exe

#define MyAppName      "mangaEasy"
#define MyAppPublisher "mangaEasy contributors"
#define MyAppURL       "https://github.com/tawhidUnhappy/mangaEasy"
#define MyAppExeName   "mangaeasy.exe"
; Version is injected at build time via /DMyAppVersion=... flag
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{B7A3C2D1-4E5F-6789-ABCD-EF0123456789}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output goes to the repo root so the workflow can easily find it
OutputDir=..\dist
OutputBaseFilename=mangaEasy-Setup-{#MyAppVersion}
SetupIconFile=..\packaging\icon.ico
UninstallDisplayIcon={app}\mangaeasy.exe
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Require admin so we can write to Program Files and add to system PATH
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; Architecture
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";    Description: "Create a &desktop shortcut";    GroupDescription: "Additional icons:"; Flags: unchecked
Name: "addtopath";      Description: "Add mangaeasy to system &PATH"; GroupDescription: "Additional tasks:"; Flags: unchecked

[Files]
; Ship the entire one-folder PyInstaller output
Source: "..\dist\mangaEasy\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\mangaEasy";          Filename: "{app}\{#MyAppExeName}"; Parameters: "app"; Comment: "Open mangaEasy control centre"
Name: "{group}\Uninstall mangaEasy"; Filename: "{uninstallexe}"
Name: "{commondesktop}\mangaEasy";  Filename: "{app}\{#MyAppExeName}"; Parameters: "app"; Comment: "Open mangaEasy control centre"; Tasks: desktopicon

[Run]
; Offer to launch immediately after install
Filename: "{app}\{#MyAppExeName}"; Parameters: "app"; Description: "Launch mangaEasy now"; Flags: nowait postinstall skipifsilent

[Registry]
; Add to system PATH when the user ticked that task
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\Session Manager\Environment"; \
  ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; \
  Check: NeedsAddPath('{app}'); Tasks: addtopath

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKLM,
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment',
    'Path', OrigPath)
  then begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Param + ';', ';' + OrigPath + ';') = 0;
end;
