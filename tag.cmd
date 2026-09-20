@echo off
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" "%~dp0scripts\tag_cli.py" %*
  exit /b
)
>&2 echo Error: this Tag source checkout is not prepared.
>&2 echo.
>&2 echo For development, run:
>&2 echo   install.ps1 -DependenciesOnly
>&2 echo.
>&2 echo For a normal managed installation, run:
>&2 echo   install.ps1
exit /b 2
