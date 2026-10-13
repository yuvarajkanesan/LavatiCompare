; Inno Setup script - run build.bat first (it produces dist\LavatiCompare\).
#define AppName "Lavati Compare"
#define AppVersion "2.0"
#define AppExe "LavatiCompare.exe"

[Setup]
AppId={{8F5B6A1E-4C0D-4C77-9B7A-1A7E5E0C1A11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Lavati Softwares
AppCopyright=Copyright (c) 2026 Lavati Softwares (MIT License)
LicenseFile=installer\EULA.txt
#ifdef Sign
SignTool=lavatisign $f
SignedUninstaller=yes
#endif
VersionInfoCompany=Lavati Softwares
VersionInfoDescription=Lavati Compare Setup
VersionInfoProductName=Lavati Compare
WizardSizePercent=115
DisableProgramGroupPage=yes
AllowNoIcons=yes
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=assets\lavati.ico
OutputDir=dist
OutputBaseFilename=LavatiCompare-Setup
Compression=lzma2/ultra64
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
SolidCompression=yes
WizardStyle=modern
WizardImageFile=installer\wizard-large.bmp
WizardSmallImageFile=installer\wizard-small.bmp
InfoBeforeFile=installer\welcome.txt
DisableWelcomePage=no
DisableReadyPage=no
ShowLanguageDialog=no
SetupMutex=LavatiCompareSetupMutex
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
DirExistsWarning=no
UsePreviousTasks=yes
UninstallDisplayName={#AppName}
VersionInfoVersion={#AppVersion}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesInstallIn64BitMode=x64compatible

[Messages]
WelcomeLabel1=Welcome to Lavati Compare
WelcomeLabel2=This will install Lavati Compare {#AppVersion} on your computer.%n%nIt is recommended that you close other applications before continuing.
FinishedHeadingLabel=Lavati Compare is ready
FinishedLabel=Setup has finished installing Lavati Compare. Start it from the Start menu or the desktop shortcut.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "explorer"; Description: "Add ""Select as left side"" and ""Compare to the selected left side"" to the &Explorer right-click menu"; GroupDescription: "Integration:"

[Files]
Source: "dist\LavatiCompare\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; per-user Explorer entries (removed on uninstall)
Root: HKCU; Subkey: "Software\Classes\*\shell\LavatiSelect"; ValueType: string; ValueData: "Select as left side (Lavati Compare)"; Tasks: explorer; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\LavatiSelect"; ValueName: "Icon"; ValueType: string; ValueData: "{app}\{#AppExe}"; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\*\shell\LavatiSelect\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" --select ""%1"""; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\*\shell\LavatiCompareWith"; ValueType: string; ValueData: "Compare to the selected left side (Lavati Compare)"; Tasks: explorer; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\*\shell\LavatiCompareWith"; ValueName: "Icon"; ValueType: string; ValueData: "{app}\{#AppExe}"; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\*\shell\LavatiCompareWith\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" --compare-with ""%1"""; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Directory\shell\LavatiSelect"; ValueType: string; ValueData: "Select as left side (Lavati Compare)"; Tasks: explorer; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\LavatiSelect"; ValueName: "Icon"; ValueType: string; ValueData: "{app}\{#AppExe}"; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Directory\shell\LavatiSelect\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" --select ""%1"""; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Directory\shell\LavatiCompareWith"; ValueType: string; ValueData: "Compare to the selected left side (Lavati Compare)"; Tasks: explorer; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Directory\shell\LavatiCompareWith"; ValueName: "Icon"; ValueType: string; ValueData: "{app}\{#AppExe}"; Tasks: explorer
Root: HKCU; Subkey: "Software\Classes\Directory\shell\LavatiCompareWith\command"; ValueType: string; ValueData: """{app}\{#AppExe}"" --compare-with ""%1"""; Tasks: explorer

[UninstallDelete]
; everything the app creates besides the install folder
Type: filesandordirs; Name: "{app}\_internal"

[InstallDelete]
; a clean update: remove files of the previous version that the new one no longer ships
Type: filesandordirs; Name: "{app}\_internal"

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
var
  KeepData: Boolean;

const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

procedure DeleteTree(const Path: String);
begin
  if DirExists(Path) then DelTree(Path, True, True, True);
end;

procedure CleanRegistry;
var
  Roots: array[0..1] of String;
  Verbs: array[0..1] of String;
  i, j: Integer;
begin
  Roots[0] := 'Software\Classes\*\shell';
  Roots[1] := 'Software\Classes\Directory\shell';
  Verbs[0] := 'LavatiSelect';
  Verbs[1] := 'LavatiCompareWith';
  for i := 0 to 1 do
    for j := 0 to 1 do
      RegDeleteKeyIncludingSubkeys(HKCU, Roots[i] + Verbs[j]);
  RegDeleteValue(HKCU, RunKey, 'LavatiCompare');
end;

procedure CleanGitConfig;
var
  Code: Integer;
begin
  { remove what "Configure Git now" wrote to the user's global git config (only our own entries) }
  Exec(ExpandConstant('{cmd}'), '/c git config --global --remove-section difftool.lavati', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{cmd}'), '/c git config --global --remove-section mergetool.lavati', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{cmd}'), '/c git config --global --unset merge.tool "^lavati$"', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{cmd}'), '/c git config --global --unset diff.tool "^lavati$"', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
    { a running copy keeps its files locked, which would leave the folder behind }
    Exec(ExpandConstant('{cmd}'), '/c taskkill /F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  if CurUninstallStep = usPostUninstall then
  begin
    CleanRegistry;
    CleanGitConfig;
    if not KeepData then
    begin
      DeleteTree(ExpandConstant('{userappdata}\LavatiCompare'));
      DeleteTree(ExpandConstant('{localappdata}\LavatiCompare'));
    end;
    { the uninstaller lives inside the install folder, so remove it a moment after it exits.
      Only our own files go, and rmdir without /s only removes the folder when it is empty, so a
      shared folder chosen at install time is never wiped. }
    Exec(ExpandConstant('{cmd}'), '/c ping 127.0.0.1 -n 3 >nul & del /q "' + ExpandConstant('{app}') + '\unins*.*" & rmdir "' + ExpandConstant('{app}') + '"', '', SW_HIDE, ewNoWait, Code);
  end;
end;

function HasParam(const Name: String): Boolean;
var
  i: Integer;
begin
  Result := False;
  for i := 1 to ParamCount do
    if CompareText(ParamStr(i), Name) = 0 then Result := True;
end;

{ make sure a running copy is closed before an update replaces its files }
function InitializeUninstall(): Boolean;
var
  Code: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/c taskkill /F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, Code);
  { silent uninstalls wipe everything unless started with /KEEPDATA; interactive ones ask (default: wipe) }
  if UninstallSilent then
    KeepData := HasParam('/KEEPDATA')
  else
    KeepData := MsgBox('Keep your sessions, rules and settings?' + #13#10 + #13#10 +
      'Choose Yes to keep them for a future install, or No to remove all Lavati Compare data from this computer.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  Result := True;
end;
