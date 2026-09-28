@echo off
rem Startet den Krypto-Waechter ohne Konsolenfenster.
cd /d "%~dp0"

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0Krypto-Waechter.pyw" %*
    exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0Krypto-Waechter.pyw" %*
    exit /b 0
)

echo.
echo Python wurde nicht gefunden.
echo Bitte Python von https://www.python.org/downloads/ installieren
echo und dabei "Add python.exe to PATH" anhaken. Danach erneut starten.
echo.
pause
exit /b 1
