@echo off
chcp 65001 >nul
title LanUs Analytics v4.0 PRO - CS2 Rating 3.0 Engine
color 0B

echo =====================================================================
echo           LANUS CS2 ANALYTICS (v4.0 PRO / HLTV 3.0 ANALOG)
echo =====================================================================
echo.

:: 1. Проверка наличия Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ОШИБКА] Python не обнаружен в системе!
    echo Пожалуйста, установите Python 3.10+ с сайта https://www.python.org/
    echo При установке ОБЯЗАТЕЛЬНО поставьте галочку "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

:: 2. Проверка и автоматическая доустановка библиотек
echo [*] Проверка и установка библиотек (demoparser2, pandas, uvicorn)...
python -m pip install -q -r requirements.txt
if %errorlevel% neq 0 (
    echo [Предупреждение] Некоторые пакеты не удалось загрузить, пробуем запуск...
)

:: 3. Автоматическое открытие сайта в браузере
echo [*] Запуск сервера LanUs Analytics на http://localhost:8080 ...
echo [*] Открытие веб-интерфейса в браузере...

start "" http://localhost:8080

echo.
echo =====================================================================
echo   СЕРВЕР АКТИВЕН! Не закрывайте это окно во время работы с сайтом.
echo   Сайт открыт по адресу: http://localhost:8080
echo   Для остановки нажмите Ctrl + C
echo =====================================================================
echo.

python server.py
pause
