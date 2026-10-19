# Compila TAC3D.exe (PyInstaller) y el instalador (Inno Setup 6).
# Requisitos de compilacion (solo en el equipo que compila):
#   python -m venv %LOCALAPPDATA%\tac3d_build
#   %LOCALAPPDATA%\tac3d_build\Scripts\pip install PySide6 pyinstaller pydicom psutil numpy
#   winget install JRSoftware.InnoSetup
$ErrorActionPreference = "Stop"
$root = Resolve-Path "$PSScriptRoot\..\.."
$py = "$env:LOCALAPPDATA\tac3d_build\Scripts\python.exe"
$out = "$env:LOCALAPPDATA\tac3d_build\out"

& $py -m PyInstaller --noconfirm --clean --log-level WARN --distpath "$out\dist" --workpath "$out\work" "$PSScriptRoot\tac3d.spec"
if ($LASTEXITCODE) { throw "PyInstaller fallo" }
Write-Output "Programa: $out\dist\TAC3D\TAC3D.exe"

$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { Write-Warning "No esta Inno Setup 6: solo se genero la carpeta del programa"; exit 0 }
New-Item -ItemType Directory -Force "$root\instalador" | Out-Null
& $iscc /Q "/DDist=$out\dist\TAC3D" "/O$root\instalador" "$PSScriptRoot\instalador.iss"
if ($LASTEXITCODE) { throw "Inno Setup fallo" }
Get-ChildItem "$root\instalador\*.exe" | ForEach-Object { "Instalador: $($_.FullName) ($([int]($_.Length / 1MB)) MB)" }
