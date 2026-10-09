@echo off
cd /d "%~dp0"
py -3 -m web_ui.server %*
pause
