@echo off
cd /d "d:\claude code\sourcing-tool"
set PATH=C:\Users\alanh\AppData\Roaming\npm;%PATH%
set LOGFILE=data\p1_expand.log
echo === P1 Expand started at %date% %time% === > %LOGFILE%
echo Working dir: %CD% >> %LOGFILE%
echo PATH: %PATH% >> %LOGFILE%
uv run python batch_p1_expand.py >> %LOGFILE% 2>&1
echo === P1 Expand finished at %date% %time% === >> %LOGFILE%
