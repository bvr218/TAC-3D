; Inno Setup 6: instalador de TAC 3D. Se compila desde compilar.ps1 (pasa /DDist=carpeta TAC3D de PyInstaller).
#define AppName "TAC 3D"
#define AppVer "1.1.1"
#ifndef Dist
  #define Dist "..\..\dist\TAC3D"
#endif

[Setup]
AppId={{6E2C9B1A-4F3D-4C8E-9A71-3D7AC3D00001}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher={#AppName}
DefaultDirName={localappdata}\Programs\TAC 3D
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputBaseFilename=TAC3D_Instalador_{#AppVer}
SetupIconFile=..\tac3d.ico
UninstallDisplayIcon={app}\TAC3D.exe
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ExtraDiskSpaceRequired=10000000000

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#Dist}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\TAC3D.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\TAC3D.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TAC3D.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
type
  TMemoryStatusEx = record
    dwLength: DWORD; dwMemoryLoad: DWORD;
    ullTotalPhys: Int64; ullAvailPhys: Int64; ullTotalPageFile: Int64; ullAvailPageFile: Int64;
    ullTotalVirtual: Int64; ullAvailVirtual: Int64; ullAvailExtendedVirtual: Int64;
  end;

function GlobalMemoryStatusEx(var lpBuffer: TMemoryStatusEx): BOOL;
  external 'GlobalMemoryStatusEx@kernel32.dll stdcall';

function InitializeSetup(): Boolean;
var
  M: TMemoryStatusEx;
  GB: Integer;
begin
  Result := True;
  M.dwLength := SizeOf(M);
  if GlobalMemoryStatusEx(M) then
  begin
    GB := M.ullTotalPhys div (1024 * 1024 * 1024);
    if GB < 7 then
      Result := MsgBox('Este equipo tiene ' + IntToStr(GB) + ' GB de RAM. TAC 3D necesita al menos 8 GB '
        + '(recomendado 16 GB) para procesar estudios; podra ver los ya procesados.' + #13#10#13#10
        + '¿Instalar de todos modos?', mbConfirmation, MB_YESNO) = IDYES;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if MsgBox('¿Borrar tambien el motor de IA y los modelos descargados (varios GB)?' + #13#10
      + 'Los estudios procesados NO se borran.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    begin
      DelTree(ExpandConstant('{localappdata}\TAC3D'), True, True, True);
      DelTree(ExpandConstant('{%USERPROFILE}\.totalsegmentator'), True, True, True);
    end;
end;
