; Instalador de CelCam para Windows 10 y 11 de 64 bits.
; Lo arma scripts\construir.ps1 con lo que deja en pc\dist\CelCam y pc\stage.

#define AppName "CelCam"
#define AppVersion "1.0.0"

[Setup]
AppId={{C5E1F0A2-6D47-4B3E-9A18-2F7C4D9E6B10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=CelCam
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador de {#AppName}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
InfoBeforeFile=antes.txt
OutputDir=..\dist
OutputBaseFilename=CelCam-Instalador-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=CelCamAppMutex
SetupIconFile=..\pc\stage\celcam.ico
UninstallDisplayIcon={app}\CelCam.exe
CloseApplications=yes

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "escritorio"; Description: "Crear un acceso directo en el Escritorio"

[Files]
Source: "..\pc\dist\CelCam\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; La cámara virtual: una para apps de 64 bits y otra para las de 32.
Source: "..\pc\stage\vcam\x64\celcam_vcam.dll"; DestDir: "{app}\vcam\x64"; Flags: ignoreversion regserver restartreplace uninsrestartdelete
Source: "..\pc\stage\vcam\x86\celcam_vcam.dll"; DestDir: "{app}\vcam\x86"; Flags: ignoreversion regserver 32bit restartreplace uninsrestartdelete
Source: "..\pc\stage\CelCam.apk"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\pc\stage\celcam.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\TERCEROS.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\CelCam.exe"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\CelCam.exe"; Tasks: escritorio

[Registry]
; El tamaño que recuerda la cámara virtual se borra al desinstalar.
Root: HKCU; Subkey: "Software\CelCam"; Flags: uninsdeletekey dontcreatekey

[Run]
; Sin esto, Windows bloquearía la conexión del celular en redes marcadas como públicas.
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""CelCam"""; Flags: runhidden
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""CelCam"" dir=in action=allow program=""{app}\CelCam.exe"" enable=yes profile=any protocol=tcp"; Flags: runhidden
Filename: "{app}\CelCam.exe"; Description: "Abrir CelCam"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""CelCam"""; Flags: runhidden; RunOnceId: "firewall-quita"
