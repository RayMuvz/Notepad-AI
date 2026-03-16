; Inno Setup script for Notepad_AI
; Adjust paths as needed before compiling with Inno Setup.

#define MyAppName "Notepad_AI"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "cri0s"
#define MyAppExeName "Notepad_AI.exe"

[Setup]
AppId={{A3E2A7E9-4E8D-4A4D-9B42-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
SetupIconFile="notepad.ico"
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=no
OutputDir=.
OutputBaseFilename=Notepad_AI_Installer
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Source should point to the PyInstaller dist folder containing Notepad_AI.exe
Source: "dist\Notepad_AI\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
; Start Menu shortcut
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; Desktop shortcut (optional task)
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

