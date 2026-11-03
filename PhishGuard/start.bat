@echo off
chcp 65001 >nul
title PhishGuard

cd /d "%~dp0"

set "PYTHON_EXE="

:: 1. Проверяем установленный Python 3.11 в LocalAppData
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    goto found
)

:: 2. Проверяем другие возможные пути установки Python
for %%D in (312 311 310 39) do (
    if exist "C:\Python%%D\python.exe" (
        set "PYTHON_EXE=C:\Python%%D\python.exe"
        goto found
    )
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%D\python.exe" (
        set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python%%D\python.exe"
        goto found
    )
)

:: 3. Проверяем системный PATH
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=python"
    goto found
)

:: 4. Проверяем py лаунчер
where py >nul 2>nul
if %ERRORLEVEL% equ 0 (
    set "PYTHON_EXE=py"
    goto found
)

echo.
echo ============================================================
echo [ОШИБКА] Python не найден на вашем компьютере!
echo ============================================================
echo Установите Python 3.9 или выше с официального сайта python.org
echo или через Microsoft Store.
echo.
pause
exit /b 1

:found
"%PYTHON_EXE%" run.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo ============================================================
    echo Приложение завершило работу с кодом ошибки %ERRORLEVEL%.
    echo ============================================================
    pause
)
