@echo off
cd /d "%~dp0"
echo Abriendo el visor 3D en el navegador...
echo No cierres esta ventana mientras uses el visor.
start "" http://localhost:8765/visor/index.html
python -m http.server 8765 --bind 127.0.0.1
