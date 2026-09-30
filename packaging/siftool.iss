; Siftool installer (Inno Setup 6): per user, no administrator prompt, in Brazilian Portuguese.
; Distribution spec 2. Compiled by packaging/build.py, which passes every define below:
;   /DAppVersion=2.0.0-beta.1                 the version in the file name and in "Aplicativos instalados"
;   /DVersionInfo=2.0.0.1                     setup.exe's numeric version resource
;   /DProductTextVersion="2.0.0 beta 1"       ...and the one for people
;   /DSourceDir=<repo>\dist\Siftool           the program folder PyInstaller made
;   /DOutputDir=<repo>\dist
;   /DIconFile=<repo>\src\siftool_gui\assets\siftool.ico
;   /DLicenseFile=<repo>\dist\Siftool\LICENSE.txt
;
; It installs to %LOCALAPPDATA%\Programs\Siftool, adds a Start menu shortcut (and, if asked, one on the
; desktop) and, unless unchecked, "Limpar com Siftool" to the right-click menu of JPG, PNG and WEBP photos
; and of folders -- all under HKCU. Uninstalling removes all of that and keeps the user's settings
; (%APPDATA%\Siftool), as is usual.

#if !Defined(AppVersion) || !Defined(VersionInfo) || !Defined(ProductTextVersion) || !Defined(SourceDir) \
    || !Defined(OutputDir) || !Defined(IconFile) || !Defined(LicenseFile)
  #error Compile with packaging/build.py: it passes the version and the folders.
#endif

#define AppName "Siftool"
#define AppExe "Siftool.exe"
#define AppPublisher "Felipe Floriani"
#define AppURL "https://github.com/FFloriani/siftool"
#define PhotoVerb "Limpar com Siftool"
#define FolderVerb "Limpar fotos com Siftool"
#define Classes "Software\Classes"

[Setup]
; Fixed forever: Windows knows the installed Siftool by it (upgrades, uninstall).
AppId={{5CF2D26D-7EC0-4251-8DA1-E3C6471521D5}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile={#LicenseFile}
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ChangesAssociations=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#VersionInfo}
VersionInfoProductVersion={#VersionInfo}
VersionInfoProductTextVersion={#ProductTextVersion}
VersionInfoTextVersion={#ProductTextVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Instalador do {#AppName}
VersionInfoProductName={#AppName}
VersionInfoCopyright={#AppPublisher}. Licença GPLv3.

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "contextmenu"; Description: "Adicionar ""{#PhotoVerb}"" ao clique direito"; GroupDescription: "Integração com o Windows:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; "Limpar com Siftool" on JPG, PNG and WEBP photos, and "Limpar fotos com Siftool" on folders. The
; uninstaller works through these lines bottom up: each verb key goes away whole (uninsdeletekey), then
; each key above it, only when nothing else is left in it (uninsdeletekeyifempty). Reinstalling with the
; option unchecked takes the verbs away (deletekey, "not contextmenu").
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpg"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpg\shell"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpg\shell\Siftool"; ValueType: string; ValueName: ""; ValueData: "{#PhotoVerb}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpg\shell\Siftool"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"",0"; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpg\shell\Siftool\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpg\shell\Siftool"; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpeg"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpeg\shell"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpeg\shell\Siftool"; ValueType: string; ValueName: ""; ValueData: "{#PhotoVerb}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpeg\shell\Siftool"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"",0"; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpeg\shell\Siftool\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.jpeg\shell\Siftool"; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.png"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.png\shell"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.png\shell\Siftool"; ValueType: string; ValueName: ""; ValueData: "{#PhotoVerb}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.png\shell\Siftool"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"",0"; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.png\shell\Siftool\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.png\shell\Siftool"; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.webp"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.webp\shell"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.webp\shell\Siftool"; ValueType: string; ValueName: ""; ValueData: "{#PhotoVerb}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.webp\shell\Siftool"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"",0"; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.webp\shell\Siftool\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\SystemFileAssociations\.webp\shell\Siftool"; Flags: deletekey; Tasks: not contextmenu
Root: HKCU; Subkey: "{#Classes}\Directory"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\Directory\shell"; Flags: uninsdeletekeyifempty; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\Directory\shell\Siftool"; ValueType: string; ValueName: ""; ValueData: "{#FolderVerb}"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\Directory\shell\Siftool"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\{#AppExe}"",0"; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\Directory\shell\Siftool\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: contextmenu
Root: HKCU; Subkey: "{#Classes}\Directory\shell\Siftool"; Flags: deletekey; Tasks: not contextmenu

[Run]
Filename: "{app}\{#AppExe}"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall skipifsilent
