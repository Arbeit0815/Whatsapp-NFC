@echo off
rem Baut Krypto-Waechter.exe - eine einzelne Datei, die ohne Python laeuft.
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo Python wurde nicht gefunden - bitte zuerst Python installieren.
    pause
    exit /b 1
)

echo PyInstaller installieren ...
%PY% -m pip install --upgrade pyinstaller || goto fehler

echo Krypto-Waechter.exe bauen ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name Krypto-Waechter ^
    --icon krypto_waechter\assets\krypto-waechter.ico ^
    --add-data "krypto_waechter\assets;krypto_waechter\assets" ^
    Krypto-Waechter.pyw || goto fehler

echo.
echo Fertig: %~dp0dist\Krypto-Waechter.exe
explorer "%~dp0dist"
pause
exit /b 0

:fehler
echo.
echo Beim Bauen ist ein Fehler aufgetreten (siehe oben).
pause
exit /b 1
