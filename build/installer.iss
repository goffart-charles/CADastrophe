#ifndef AppVersion
  #include "version.iss"
#endif
#ifndef SourceDir
  #define SourceDir SourcePath + "out\" + AppVersion + "\app"
#endif
#ifndef OutputDir
  #define OutputDir SourcePath + "out\" + AppVersion
#endif
#if !FileExists(AddBackslash(SourceDir) + "CADastrophe.exe")
  #error Paquet applicatif absent : executer build.ps1 avant de compiler installer.iss.
#endif
#include AddBackslash(SourceDir) + "..\installer-limits.iss"

[Setup]
AppId={{95FE45BC-48D8-4838-AAD4-6BB41F223AE5}
AppName=CADastrophe
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\CADastrophe
DefaultGroupName=CADastrophe
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=CADastrophe-{#AppVersion}-windows-x64-setup
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\CADastrophe.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le bureau"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "runtime\.cadastrophe-prefix,plugins\settings.json"

[InstallDelete]
; Replace our private runtime so files dropped from the release cannot linger.
Type: filesandordirs; Name: "{app}\runtime"; Check: ExistingInstallation
#include AddBackslash(SourceDir) + "..\obsolete-files.iss"

[Icons]
Name: "{group}\CADastrophe"; Filename: "{app}\CADastrophe.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\CADastrophe"; Filename: "{app}\CADastrophe.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[UninstallDelete]
Type: files; Name: "{app}\runtime\.cadastrophe-prefix"

[Run]
Filename: "{app}\CADastrophe.exe"; Description: "Lancer CADastrophe"; Flags: nowait postinstall skipifsilent

[Code]
function ExistingInstallation: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\CADastrophe.exe')) and
    FileExists(ExpandConstant('{app}\bootstrap.py')) and
    FileExists(ExpandConstant('{app}\release.json'));
end;

function CheckInstallationDirectory: String;
begin
  Result := '';
  if Length(ExpandConstant('{app}')) > {#MaxInstallDirLength} then
    Result := 'Le dossier choisi est trop long pour cette installation. Choisir un chemin de {#MaxInstallDirLength} caractères maximum, par exemple le dossier proposé par défaut.';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var DirectoryError: String;
begin
  Result := True;
  if CurPageID = wpSelectDir then begin
    DirectoryError := CheckInstallationDirectory;
    if DirectoryError <> '' then begin
      SuppressibleMsgBox(DirectoryError, mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := CheckInstallationDirectory;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var ExitCode: Integer;
begin
  if CurStep = ssPostInstall then
    if not Exec(ExpandConstant('{app}\CADastrophe.exe'), '--initialize-runtime',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ExitCode) then
      RaiseException('Impossible de préparer le runtime de CADastrophe.')
    else if ExitCode <> 0 then
      RaiseException('Échec de préparation du runtime. Consulter CADastrophe\logs\launcher.log dans LocalAppData.');
end;
