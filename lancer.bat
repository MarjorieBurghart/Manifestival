@echo off
cd /d "%~dp0"
python lancer_manifestival.py %*
if errorlevel 1 pause
