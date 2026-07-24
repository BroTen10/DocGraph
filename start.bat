@echo off
setlocal

REM ============================================================
REM  基于知识图谱的自动文档审查智能体 - 一键启动脚本
REM  拉起：Postgres + Neo4j（已有 Docker 容器）+ 后端 FastAPI + 前端 Vite
REM  双击本文件即可运行；关闭各子窗口或运行 stop.bat 停止。
REM ============================================================

cd /d "%~dp0"
set "PROJECT_ROOT=%CD%"
set "BACKEND_DIR=%PROJECT_ROOT%\backend"
set "FRONTEND_DIR=%PROJECT_ROOT%\frontend"

set "PG_CONTAINER=doc-review-postgres"
set "NEO4J_CONTAINER=doc-review-neo4j"

echo ============================================================
echo   基于知识图谱的自动文档审查智能体 - 一键启动
echo ============================================================
echo.

REM ---- 1. 检查 Docker ----
echo [1/5] 检查 Docker 环境...
docker info >nul 2>&1
if errorlevel 1 (
    echo [错误] Docker 未运行。请先启动 Docker Desktop 后再执行本脚本。
    echo        开始菜单搜索 "Docker Desktop" 启动，等待其状态变为 running。
    pause
    exit /b 1
)
echo       Docker 正常。
echo.

REM ---- 2. 启动已有数据库容器 ----
echo [2/5] 启动 Postgres + Neo4j 容器...

docker start %PG_CONTAINER% >nul 2>&1
if errorlevel 1 (
    echo [错误] 无法启动容器 %PG_CONTAINER%，请确认该容器已在 Docker 中创建。
    pause
    exit /b 1
)
echo       Postgres 容器已启动。

docker start %NEO4J_CONTAINER% >nul 2>&1
if errorlevel 1 (
    echo [错误] 无法启动容器 %NEO4J_CONTAINER%，请确认该容器已在 Docker 中创建。
    pause
    exit /b 1
)
echo       Neo4j 容器已启动。

echo       等待数据库就绪...
:wait_pg
docker exec %PG_CONTAINER% pg_isready -U postgres -d doc_review >nul 2>&1
if errorlevel 1 (
    timeout /t 2 /nobreak >nul
    goto wait_pg
)
echo       数据库就绪（Postgres:5432  Neo4j:7687）。
echo.

REM ---- 3. 启动后端 ----
echo [3/5] 启动后端 FastAPI（端口 8000）...
if not exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
    echo [错误] 未找到后端虚拟环境：%BACKEND_DIR%\.venv
    echo        请先在 backend 目录下创建虚拟环境并安装 requirements.txt。
    pause
    exit /b 1
)
start "后端 - FastAPI (8000)" /D "%BACKEND_DIR%" cmd /k "chcp 65001 >nul & .\.venv\Scripts\python.exe run.py"
echo       后端窗口已打开。
echo.

REM ---- 4. 启动前端 ----
echo [4/5] 启动前端 Vite 开发服务器（端口 5173）...
if not exist "%FRONTEND_DIR%\node_modules" (
    echo       首次运行，正在安装前端依赖（npm install）...
    pushd "%FRONTEND_DIR%"
    call npm install
    if errorlevel 1 (
        echo [错误] 前端依赖安装失败。
        popd
        pause
        exit /b 1
    )
    popd
    echo       前端依赖安装完成。
)
start "前端 - Vite (5173)" /D "%FRONTEND_DIR%" cmd /k "chcp 65001 >nul & npm run dev"
echo       前端窗口已打开。
echo.

REM ---- 5. 等待后端就绪后打开浏览器 ----
echo [5/5] 等待后端服务就绪...
set "WAIT_SEC=0"
:wait_backend
powershell -NoProfile -Command "try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:8000/api/health' -TimeoutSec 2).StatusCode } catch { 0 }" > "%TEMP%\_doc_review_hc.txt" 2>nul
set /p HC=<"%TEMP%\_doc_review_hc.txt"
if "%HC%"=="200" (
    echo       后端已就绪。
    goto open_browser
)
set /a WAIT_SEC+=2
if %WAIT_SEC% geq 90 (
    echo [警告] 后端 90 秒内未响应，仍尝试打开浏览器。如页面报错请稍后刷新。
    goto open_browser
)
echo       等待中... %WAIT_SEC%s
timeout /t 2 /nobreak >nul
goto wait_backend

:open_browser
echo.
echo ============================================================
echo   全部启动完成！
echo.
echo   Postgres:      localhost:5432  (postgres / postgres)
echo   Neo4j 控制台:  http://localhost:7474  (neo4j / neo4jpassword)
echo   后端 API:      http://localhost:8000/api/health
echo   前端页面:      http://localhost:5173
echo.
echo   关闭对应子窗口即可停止后端/前端。
echo   如需停止数据库容器，请运行 stop.bat
echo ============================================================
timeout /t 3 /nobreak >nul
start "" http://localhost:5173
echo.
echo 此窗口可以关闭。如需停止全部服务，请运行 stop.bat
pause
endlocal
