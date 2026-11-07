@echo off
:: Принудительно задаем кодировку UTF-8 для корректного вывода текста
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo       Sgorka PULSEWAVE 4.0.0
echo ============================================

if not exist "sources.txt" copy /Y "sources.example.txt" "sources.txt" >nul

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Sozdanie virtualnogo okruzheniya...
  py -3.11 -m venv .venv
  if errorlevel 1 goto error
)

echo [2/4] Ustanovka zavisimostey...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error

echo [3/4] Proverka sintaksisa...
".venv\Scripts\python.exe" -m py_compile gui.py tunnel.py multi_tester.py settings.py
if errorlevel 1 goto error

echo [4/4] Sborga prilozheniya...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name "PULSEWAVE" --icon "app_icon.ico" --add-data "app_icon.png;." --version-file "version_info.txt" --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import multi_tester --hidden-import tunnel --hidden-import github_publisher --collect-all keyring --collect-all pystray --collect-all PIL gui.py
if errorlevel 1 goto error

if exist "sources.txt" copy /Y "sources.txt" "dist\PULSEWAVE\sources.txt" >nul

echo.
echo GOTOVO: dist\PULSEWAVE\PULSEWAVE.exe
echo Sing-box is not included. 
echo Download from: https://github.com
pause
exit /b 0

:error
echo.
echo [ERROR] Sborga ne vypolnena.
pause
exit /b 1
