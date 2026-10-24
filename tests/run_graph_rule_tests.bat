@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PYTHON_EXE=%~dp0..\backend\.venv\Scripts\python.exe"
if defined DOCGRAPH_TEST_PYTHON set "PYTHON_EXE=%DOCGRAPH_TEST_PYTHON%"
echo ========================================
echo  DocGraph - Graph Rule Automated Tests
echo ========================================
"%PYTHON_EXE%" tests\run_graph_rule_tests.py
pause
