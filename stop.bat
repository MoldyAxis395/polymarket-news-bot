@echo off
cd /d "%~dp0"
if not exist data\bot.pid (echo Nessun bot. & exit /b)
for /f %%p in (data\bot.pid) do taskkill /pid %%p /f
del data\bot.pid
