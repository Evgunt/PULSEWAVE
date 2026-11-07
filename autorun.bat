@echo off
cd /d "%~dp0"
echo [%date% %time%] Запуск проверки VPN...

:: Команда call заставляет батник ждать завершения python
call python multi_tester.py

echo [%date% %time%] Работа скрипта завершена.
