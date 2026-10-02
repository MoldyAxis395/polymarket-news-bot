@echo off
rem Starts the paper bot hidden (no window). Logs in data\bot.log
cd /d "%~dp0"
if exist data\bot.pid (
  for /f %%p in (data\bot.pid) do tasklist /fi "PID eq %%p" | find "%%p" >nul && (echo Bot gia' in esecuzione, PID %%p & exit /b)
)
start "" pythonw -m bot.main
echo Bot avviato. Report: python report.py
