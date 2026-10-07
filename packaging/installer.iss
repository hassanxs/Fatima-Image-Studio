; Inno Setup 6 script for Fatima Image Studio. Built by packaging\build.ps1, which passes
; AppVersion, SourceDir (the staged app + embeddable Python), IconFile and OutputDir.
;
; Per-user install, no admin prompt. The app keeps models, engine and settings in
; %LOCALAPPDATA%\Fatima Image Studio and images in Pictures\Fatima Image Studio, so updates never touch them.

#define AppName "Fatima Image Studio"
#define AppExe "FatimaImageStudio.exe"
#define RepoUrl "https://github.com/hassanxs/fatima-image-studio"

[Setup]
; Never change AppId: it is how updates find the existing install.
AppId={{0DF886EC-A4FE-4BD2-A7F4-94BA81A7ADE4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Hassan
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
LicenseFile={#SourceDir}\LICENSE
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename=FatimaImageStudio-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startup"; Description: "Start with Windows (runs quietly in the tray)"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; Replace the code and runtime wholesale on update, so files removed upstream don't linger.
Type: filesandordirs; Name: "{app}\studio"
Type: filesandordirs; Name: "{app}\python"
Type: files; Name: "{app}\app.ico"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Comment: "Bulk image generation on this PC"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon
; Same name and arguments the app's own "Start with Windows" switch uses, so the two stay in sync.
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; Parameters: "--tray --no-browser"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent
; The in-app updater runs this installer silently with /relaunch=1, so the app comes back by itself.
Filename: "{app}\{#AppExe}"; Parameters: "--tray --no-browser"; WorkingDir: "{app}"; Flags: nowait; Check: ShouldRelaunch

[UninstallDelete]
Type: files; Name: "{userstartup}\{#AppName}.lnk"
Type: filesandordirs; Name: "{app}\studio"
Type: filesandordirs; Name: "{app}\python"

[Code]
const
  DataDir = '{localappdata}\Fatima Image Studio';

{ Stop a running copy (FatimaImageStudio.exe, or the bundled Python in 1.0.0) so its files can be replaced. }
procedure StopRunningApp();
var
  Code: Integer;
  Cmd: String;
begin
  Cmd := '-NoProfile -NonInteractive -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like ''' +
         ExpandConstant('{app}') + '\python\*'' -or $_.ExecutablePath -like ''' +
         ExpandConstant('{app}') + '\{#AppExe}'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"';
  Exec('powershell.exe', Cmd, '', SW_HIDE, ewWaitUntilTerminated, Code);
  Sleep(800);
end;

function ShouldRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:relaunch|0}') = '1';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  if DirExists(ExpandConstant('{app}')) then
    StopRunningApp();
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  StopRunningApp();
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Dir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Dir := ExpandConstant(DataDir);
    if DirExists(Dir) and not UninstallSilent() then
      if MsgBox('Also delete the downloaded models, engine and settings?' + #13#10 + #13#10 +
                'They can take 5-30 GB in:' + #13#10 + Dir + #13#10 + #13#10 +
                'Choose No to keep them for a later reinstall. Your images in Pictures\Fatima Image Studio are always kept.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Dir, True, True, True);
  end;
end;
