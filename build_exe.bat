@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo ============================================
echo       Сборка PULSEWAVE 4.0.0
echo ============================================

if not exist "sources.txt" copy /Y "sources.example.txt" "sources.txt" >nul

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Создание виртуального окружения...
  py -3.11 -m venv .venv
  if errorlevel 1 goto error
)

echo [2/4] Установка зависимостей...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto error
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto error

echo [3/4] Проверка синтаксиса...
.venv\Scripts\python.exe -m py_compile gui.py tunnel.py multi_tester.py settings.py
if errorlevel 1 goto error

echo [4/4] Сборка приложения...
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed --onedir --name PULSEWAVE --icon app_icon.ico --add-data "app_icon.png;." --version-file version_info.txt --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import multi_tester --hidden-import tunnel --hidden-import github_publisher --collect-all keyring --collect-all pystray --collect-all PIL gui.py
if errorlevel 1 goto error

if exist "sources.txt" copy /Y "sources.txt" "dist\PULSEWAVE\sources.txt" >nul

echo.
echo ГОТОВО: dist\PULSEWAVE\PULSEWAVE.exe
echo Sing-box НЕ включён в сборку. Пользователь устанавливает его отдельно
echo из официального проекта и выбирает путь к sing-box.exe в приложении.
echo https://github.com/SagerNet/sing-box/releases
pause
exit /b 0
:error
echo.
echo [ОШИБКА] Сборка не выполнена. Проверьте сообщения выше.
pause
exit /b 1
