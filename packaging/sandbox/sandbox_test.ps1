# M17.6: Windows Sandbox içinde kendiliğinden çalışır (AuditTrace_Sandbox.wsb oturum açınca başlatır).
# Temiz, internetsiz Windows'ta: sessiz kurulum -> öz-test -> ölçüm (3 analiz) -> pencereyi aç. Sonuçlar
# C:\Sonuc (ana makinede Masaüstü\AuditTrace_Sandbox_Sonuc) klasörüne yazılır.
$ErrorActionPreference = "Continue"
$out = "C:\Sonuc"
$log = Join-Path $out "sandbox_sonuc.txt"
function Note($text) { "$(Get-Date -Format HH:mm:ss) $text" | Tee-Object -FilePath $log -Append }

Note "Sandbox denemesi basladi. Windows: $([Environment]::OSVersion.VersionString), bellek: $([math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)) GB"
Note "internet: $(if (Test-Connection 1.1.1.1 -Count 1 -Quiet -ErrorAction SilentlyContinue) { 'VAR (beklenmiyordu)' } else { 'yok (beklenen)' })"
$setup = Get-ChildItem "C:\Kurulum" -Filter "AuditTrace_Kurulum_*.exe" | Select-Object -First 1
$t = Measure-Command { $p = Start-Process $setup.FullName -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait -PassThru }
Note "kurulum cikis kodu $($p.ExitCode), sure $([int]$t.TotalSeconds) sn (isitma oz-testi dahil)"
$app = Join-Path $env:LOCALAPPDATA "Programs\AuditTrace\AuditTrace.exe"
Note "program var mi: $(Test-Path $app); baslat menusu kisayolu: $(Test-Path (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\AuditTrace.lnk')); masaustu kisayolu: $(Test-Path (Join-Path ([Environment]::GetFolderPath('Desktop')) 'AuditTrace.lnk'))"
$warm = Join-Path $env:LOCALAPPDATA "AuditTrace\logs\kurulum_oz_test.json"
if (Test-Path $warm) { Copy-Item $warm $out; Note "isitma oz-testi kopyalandi (kurulum_oz_test.json)" } else { Note "isitma oz-testi dosyasi YOK" }
$t = Measure-Command { $m = Start-Process $app -ArgumentList '--measure', "$out\olcum" -Wait -PassThru }
Note "olcum (sunucu + 3 analiz) cikis kodu $($m.ExitCode), sure $([int]$t.TotalSeconds) sn; ayrintilar olcum\olcum.json"
Note "uygulama penceresi aciliyor: ornek dosyayla dene, Word indir, mevzuat arama elle denenebilir; kaldirma: Ayarlar > Uygulamalar > AuditTrace"
Start-Process $app
