; M17.5 AuditTrace kurulum programı (Inno Setup 6). Derleme: python packaging/build.py --installer (ISCC'yi bulur,
; sürümü mufettis.config.APP_VERSION'dan /DAppVersion ile verir). Kaynak: dist\AuditTrace (build.py çıktısı).
;
; Kullanıcı başına kurulum: yönetici gerekmez, %LOCALAPPDATA%\Programs\AuditTrace. Kullanıcının verisi (inceleme
; kayıtları, denetim günlükleri, yetki tablosu, ayarlar) %LOCALAPPDATA%\AuditTrace'tedir; kurulum ve güncelleme ona
; dokunmaz, kaldırma da silmez (yalnız Edge profili ve günlükler gibi önbellekler silinir).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "AuditTrace"
#define Root ".."

[Setup]
; AppId sabit kalmalı: yeni sürüm eskinin üzerine kurulur.
AppId={{E92EE63D-AAEC-41D8-98DB-AE008C9663B4}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AuditTrace
VersionInfoDescription=AuditTrace kurulum programı
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=
DefaultDirName={autopf}\{#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
OutputDir={#Root}\dist
OutputBaseFilename=AuditTrace_Kurulum_{#AppVersion}
SetupIconFile={#Root}\mufettis\ui\brand\icon.ico
UninstallDisplayIcon={app}\AuditTrace.exe
UninstallDisplayName={#AppName}
WizardStyle=modern
; Modeller zaten sıkıştırılmış gibi davranır; orta düzey sıkıştırma ve 2 iş parçacığı (işlemciyi yormamak için).
Compression=lzma2/normal
LZMANumBlockThreads=2
SolidCompression=no
DiskSpanning=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes

[Languages]
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol koy"; GroupDescription: "Kısayollar:"

[InstallDelete]
; Güncellemede eski sürümün program dosyaları kalmasın (kullanıcı verisi bu klasörde değil).
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\tesseract"

[Files]
Source: "{#Root}\dist\AuditTrace\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\AuditTrace.exe"; Comment: "Kredi dosyası ve teminat kontrolü"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\AuditTrace.exe"; Tasks: desktopicon; Comment: "Kredi dosyası ve teminat kontrolü"

[Run]
; Isıtma: bir kez öz-test (modeller, OCR, örnek dosya). Windows Defender paketi şimdi tarar; ilk açılış hızlı olur.
Filename: "{app}\AuditTrace.exe"; Parameters: "--selftest ""{localappdata}\AuditTrace\logs\kurulum_oz_test.json"""; StatusMsg: "İlk açılış hazırlanıyor (bir iki dakika sürebilir)..."; Flags: runhidden waituntilterminated
Filename: "{app}\AuditTrace.exe"; Description: "AuditTrace'i şimdi aç"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Önbellekler silinir; kullanıcının kayıtları (%LOCALAPPDATA%\AuditTrace\data) kalır.
Type: filesandordirs; Name: "{localappdata}\AuditTrace\edge_profile"
Type: filesandordirs; Name: "{localappdata}\AuditTrace\webview"
Type: filesandordirs; Name: "{localappdata}\AuditTrace\logs"
