@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PYTHON_EXE=C:\Users\HP\.workbuddy\binaries\python\versions\3.12.8\python.exe

if not exist "%PYTHON_EXE%" (
    echo [错误] 找不到 Python: %PYTHON_EXE%
    pause
    exit /b 1
)

:: 补装缺失依赖（静默）
"%PYTHON_EXE%" -m pip install --quiet pywin32 retry2 winrt-runtime watchdog openpyxl matplotlib >nul 2>&1

echo ============================================
echo   AhabAssistantLimbusCompany 开发模式
echo ============================================
echo.
"%PYTHON_EXE%" main_dev.py

pause
exit /b 0
