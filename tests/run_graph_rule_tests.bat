@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ========================================
echo  DocGraph - Graph Rule Automated Tests
echo ========================================
backend\.venv\Scripts\python.exe tests\run_graph_rule_tests.py
pause
