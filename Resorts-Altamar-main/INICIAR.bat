@echo off
setlocal
cd /d "%~dp0"
rem Busca Python: primero el lanzador "py" y luego "python".
set "ALTAMAR_PY="
where py >NUL 2>&1 && set "ALTAMAR_PY=py -3"
if not defined ALTAMAR_PY where python >NUL 2>&1 && set "ALTAMAR_PY=python"
if not defined ALTAMAR_PY (
  echo No se encontro Python. Instala Python 3.11 o posterior desde python.org o Microsoft Store y vuelve a intentarlo.
  pause
  exit /b 1
)
%ALTAMAR_PY% app.py --open
if errorlevel 1 (
  echo No se pudo iniciar. Comprueba que Python 3.11 o posterior esta instalado y el puerto 8080 esta libre.
  pause
)
