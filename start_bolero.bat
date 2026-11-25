@echo off
title Bolero Studio — FTTx Network Architect
cd /d "%~dp0"
echo Memulai server Bolero Studio di http://127.0.0.1:8000 ...
start "" "http://127.0.0.1:8000"
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
pause