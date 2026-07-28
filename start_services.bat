@echo off
chcp 65001 >nul
title DocGraph - Starting Frontend & Backend

cd /d "%~dp0"

:: --- Read APP_PORT from backend\.env ---
set APP_PORT=8800
for /f "usebackq tokens=1,* delims==" %%a in ("backend\.env") do (
    if /i "%%a"=="APP_PORT" set APP_PORT=%%b
)
:: Trim trailing whitespace
set APP_PORT=%APP_PORT: =%

echo ========================================
echo  DocGraph - Starting Services
echo  Backend port: %APP_PORT%
echo  Frontend port: 5173
echo ========================================
echo.

echo [1/2] Starting Backend (FastAPI, port %APP_PORT%)...
start "DocGraph-Backend" cmd /k "chcp 65001 >nul && cd /d %~dp0backend && .venv\Scripts\python.exe run.py"

echo [2/2] Starting Frontend (Vite, port 5173)...
start "DocGraph-Frontend" cmd /k "chcp 65001 >nul && set VITE_API_TARGET=http://localhost:%APP_PORT% && cd /d %~dp0frontend && node_modules\.bin\vite.cmd"

echo.
echo Both services started in separate windows.
echo Close windows manually or run stop_services.bat to stop.
echo.
pause
