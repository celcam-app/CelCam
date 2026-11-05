# Arma CelCam. Lo usan la PC de desarrollo y GitHub Actions.
# Uso: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\construir.ps1 [-Pasos camara,app,programa,instalador]
# Variables opcionales: CELCAM_FIRMA (archivo .properties con la llave del APK).
param([string[]]$Pasos = @('camara', 'app', 'programa', 'instalador'))

$ErrorActionPreference = 'Stop'
# Con -File, «camara,app» llega como un solo texto.
$Pasos = $Pasos -split ','
$raiz = Split-Path -Parent $PSScriptRoot
$stage = "$raiz\pc\stage"
New-Item -ItemType Directory -Force $stage | Out-Null

function Revisar($paso) { if ($LASTEXITCODE -ne 0) { throw "Falló: $paso (código $LASTEXITCODE)" } }

# Las herramientas locales están en tools\; en GitHub Actions ya vienen en el sistema.
$python = if (Test-Path "$raiz\pc\.venv\Scripts\python.exe") { "$raiz\pc\.venv\Scripts\python.exe" } else { 'python' }
if (Test-Path "$raiz\tools\jdk") { $env:JAVA_HOME = "$raiz\tools\jdk" }
if (Test-Path "$raiz\tools\android") { $env:ANDROID_HOME = "$raiz\tools\android" }

if ($Pasos -contains 'camara') {
    Write-Host "== Cámara virtual"
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
    $softcam = "$raiz\vcam\softcam"
    foreach ($plataforma in 'x64', 'Win32') {
        & $msbuild "$softcam\src\softcam\softcam.vcxproj" /m /nologo /v:minimal /p:Configuration=Release "/p:Platform=$plataforma" "/p:SolutionDir=$softcam\"
        Revisar "compilar la cámara ($plataforma)"
    }
    New-Item -ItemType Directory -Force "$stage\vcam\x64", "$stage\vcam\x86" | Out-Null
    Copy-Item "$softcam\dist\bin\x64\softcam.dll" "$stage\vcam\x64\celcam_vcam.dll" -Force
    Copy-Item "$softcam\dist\bin\Win32\softcam.dll" "$stage\vcam\x86\celcam_vcam.dll" -Force
}

if ($Pasos -contains 'app') {
    Write-Host "== App de Android"
    $gradle = if (Test-Path "$raiz\tools\gradle\bin\gradle.bat") { "$raiz\tools\gradle\bin\gradle.bat" } else { 'gradle' }
    $firma = $env:CELCAM_FIRMA
    $argumentos = @('--no-daemon', '--console=plain', 'assembleRelease')
    if ($firma -and (Test-Path $firma)) { $argumentos += "-PcelcamFirma=$firma" } else { Write-Warning "Sin llave de firma: el APK sale firmado con la llave de pruebas." }
    Push-Location "$raiz\android"
    try {
        & $gradle @argumentos
        Revisar "compilar la app"
    } finally { Pop-Location }
    Copy-Item "$raiz\android\app\build\outputs\apk\release\app-release.apk" "$stage\CelCam.apk" -Force
}

if ($Pasos -contains 'programa') {
    Write-Host "== Programa de PC"
    & $python "$raiz\scripts\hacer_icono.py" "$stage\celcam.ico"
    Revisar "crear el ícono"
    Push-Location "$raiz\pc"
    try {
        & $python -m PyInstaller --noconfirm --clean --windowed --name CelCam --icon "$stage\celcam.ico" `
            --version-file version_info.txt --collect-all customtkinter --collect-submodules av --paths . celcam_inicio.py
        Revisar "empaquetar el programa"
    } finally { Pop-Location }
}

if ($Pasos -contains 'instalador') {
    Write-Host "== Instalador"
    $iscc = @(
        "$raiz\tools\innosetup\ISCC.exe"
        Get-ChildItem "${env:ProgramFiles(x86)}\Inno Setup*\ISCC.exe", "$env:ProgramFiles\Inno Setup*\ISCC.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    & $iscc /Q "$raiz\installer\CelCam.iss"
    Revisar "armar el instalador"
    Get-ChildItem "$raiz\dist\CelCam-Instalador-*.exe" | ForEach-Object { "{0}  {1:N1} MB" -f $_.FullName, ($_.Length / 1MB) }
}
