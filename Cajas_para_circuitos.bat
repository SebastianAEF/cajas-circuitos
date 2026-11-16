@echo off
rem Lanzador de "Cajas para circuitos" (Windows). Abre la interfaz sin consola.
setlocal
set "AQUI=%~dp0"
set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%AQUI%cajas_circuitos.py"
    exit /b 0
)
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%AQUI%cajas_circuitos.py"
    exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%AQUI%cajas_circuitos.py"
    exit /b 0
)
echo No se encontro Python 3 con tkinter. Instala Python desde python.org.
pause
exit /b 1
