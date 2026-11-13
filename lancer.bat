@echo off
cd /d "%~dp0"
python explorateur.py %*
if errorlevel 1 pause
