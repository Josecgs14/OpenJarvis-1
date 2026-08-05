# Instalador automático de OpenJarvis (fork Josecgs14) para Windows.
#
# Uso (una sola línea en PowerShell):
#   irm https://raw.githubusercontent.com/Josecgs14/OpenJarvis-1/claude/jarvis-project-9g0igq/scripts/install/windows-jarvis.ps1 | iex
#
# Hace todo: instala uv, descarga el proyecto, instala dependencias,
# pide y guarda la clave de OpenAI (localmente, vía setx), crea un
# acceso directo "Encender Jarvis.bat" en el Escritorio y arranca el
# servidor con la interfaz web en http://127.0.0.1:8000.
# Para actualizar el proyecto más adelante: vuelve a ejecutar esta misma línea.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Branch = 'claude/jarvis-project-9g0igq'
$ZipUrl = "https://github.com/Josecgs14/OpenJarvis-1/archive/refs/heads/$($Branch -replace '/','%2F').zip"
$InstallRoot = Join-Path $HOME 'Jarvis'
$AppDir = Join-Path $InstallRoot ('OpenJarvis-1-' + ($Branch -replace '/', '-'))

Write-Host ''
Write-Host '=== Instalador de Jarvis (OpenJarvis) para Windows ===' -ForegroundColor Cyan
Write-Host ''

# ── 1. uv ────────────────────────────────────────────────────────────────
$uvBin = Join-Path $HOME '.local\bin'
if (-not (Get-Command uv -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $uvBin 'uv.exe'))) {
    Write-Host '[1/5] Instalando uv (gestor de Python)...' -ForegroundColor Yellow
    irm https://astral.sh/uv/install.ps1 | iex
} else {
    Write-Host '[1/5] uv ya está instalado.' -ForegroundColor Green
}
if ($env:Path -notlike "*$uvBin*") { $env:Path = "$uvBin;$env:Path" }

# ── 2. Descargar el proyecto ─────────────────────────────────────────────
Write-Host '[2/5] Descargando el proyecto desde tu GitHub...' -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$zipPath = Join-Path $InstallRoot 'jarvis.zip'
iwr $ZipUrl -OutFile $zipPath
if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
Expand-Archive $zipPath -DestinationPath $InstallRoot -Force
Remove-Item $zipPath

# ── 3. Dependencias ──────────────────────────────────────────────────────
Write-Host '[3/5] Instalando Jarvis y su servidor (esto tarda unos minutos)...' -ForegroundColor Yellow
Push-Location $AppDir
uv sync --extra server
Pop-Location

# ── 4. Clave de OpenAI ───────────────────────────────────────────────────
if (-not $env:OPENAI_API_KEY) {
    Write-Host '[4/5] Se necesita tu clave de OpenAI (empieza con sk-...).' -ForegroundColor Yellow
    Write-Host '      Se guarda SOLO en esta computadora. Al escribirla no se ve — es normal.'
    $secure = Read-Host 'Pega tu clave y presiona Enter' -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
    if (-not $key) { throw 'No se escribió ninguna clave.' }
    setx OPENAI_API_KEY $key | Out-Null
    $env:OPENAI_API_KEY = $key
    Write-Host '      Clave guardada de forma permanente.' -ForegroundColor Green
} else {
    Write-Host '[4/5] La clave de OpenAI ya está configurada.' -ForegroundColor Green
}

# ── 5. Acceso directo + arranque ─────────────────────────────────────────
Write-Host '[5/5] Creando "Encender Jarvis.bat" en el Escritorio...' -ForegroundColor Yellow
$desktop = [Environment]::GetFolderPath('Desktop')
$launcher = Join-Path $desktop 'Encender Jarvis.bat'
@"
@echo off
title Jarvis
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
cd /d "$AppDir"
start "" cmd /c "timeout /t 12 >nul & start http://127.0.0.1:8000"
uv run jarvis serve
pause
"@ | Set-Content -Path $launcher -Encoding ASCII

Write-Host ''
Write-Host '=== Listo. Encendiendo Jarvis... ===' -ForegroundColor Cyan
Write-Host 'La interfaz se abrira sola en http://127.0.0.1:8000'
Write-Host 'Para apagarlo: cierra la ventana. Para volver a encenderlo:'
Write-Host 'doble clic en "Encender Jarvis.bat" en tu Escritorio.'
Write-Host ''
Start-Process -FilePath $launcher
