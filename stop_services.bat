@echo off
chcp 65001 >nul
title DocGraph - Stopping Services

echo ========================================
echo  DocGraph - Stopping Services
echo ========================================
echo.

echo Stopping Backend (FastAPI) ...
taskkill /f /fi "WINDOWTITLE eq DocGraph-Backend" >nul 2>&1
if %errorlevel% equ 0 (echo  [OK] Backend stopped) else (echo  [--] Backend not running)

echo Stopping Frontend (Vite) ...
taskkill /f /fi "WINDOWTITLE eq DocGraph-Frontend" >nul 2>&1
if %errorlevel% equ 0 (echo  [OK] Frontend stopped) else (echo  [--] Frontend not running)

echo.
echo Done.
timeout /t 2 >nul
