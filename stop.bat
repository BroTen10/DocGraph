@echo off
setlocal

REM ============================================================
REM  基于知识图谱的自动文档审查智能体 - 一键停止脚本
REM  关闭后端/前端进程，并停止数据库容器（不删除，保留数据）
REM ============================================================

set "PG_CONTAINER=doc-review-postgres"
set "NEO4J_CONTAINER=doc-review-neo4j"

echo ============================================================
echo   停止文档审查智能体全部服务
echo ============================================================
echo.

REM ---- 1. 关闭后端进程（占用 8000 端口）----
echo [1/3] 关闭后端进程（端口 8000）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr "LISTENING"') do (
    echo       终止 PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
echo       完成。
echo.

REM ---- 2. 关闭前端进程（占用 5173 端口）----
echo [2/3] 关闭前端进程（端口 5173）...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173 " ^| findstr "LISTENING"') do (
    echo       终止 PID %%a
    taskkill /F /PID %%a >nul 2>&1
)
echo       完成。
echo.

REM ---- 3. 停止数据库容器（docker stop，不删除）----
echo [3/3] 停止 Postgres + Neo4j 容器...
docker stop %PG_CONTAINER% >nul 2>&1
docker stop %NEO4J_CONTAINER% >nul 2>&1
echo       完成。
echo.

echo ============================================================
echo   全部服务已停止。
echo   数据库数据已保留在 Docker 卷中，下次 start.bat 会自动恢复。
echo ============================================================
pause
endlocal
