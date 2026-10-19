@echo off
setlocal
set "PY=%LOCALAPPDATA%\tac3d_env\Scripts\python.exe"
if not exist "%PY%" (
  echo No encuentro el entorno de Python en %LOCALAPPDATA%\tac3d_env
  echo Hay que reinstalarlo: python -m venv "%LOCALAPPDATA%\tac3d_env" y luego
  echo "%LOCALAPPDATA%\tac3d_env\Scripts\pip" install torch==2.6.0 TotalSegmentator scikit-image trimesh nibabel pydicom fast-simplification pylibjpeg pylibjpeg-libjpeg pypdf --extra-index-url https://download.pytorch.org/whl/cu124
  pause
  exit /b 1
)
if "%~1"=="" (
  echo Arrastra el archivo .zip del estudio sobre este archivo, o escribe su ruta:
  set /p "ZIP=Ruta del zip: "
) else (
  set "ZIP=%~1"
)
if /i "%~1"=="--rehacer" (
  "%PY%" -u "%~dp0pipeline\importar.py" %*
) else (
  "%PY%" -u "%~dp0pipeline\importar.py" "%ZIP:"=%" %2 %3 %4 %5
)
echo.
pause
