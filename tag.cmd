@echo off
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\tag_cli.py" %*
  exit /b
)
python "%~dp0scripts\tag_cli.py" %*
