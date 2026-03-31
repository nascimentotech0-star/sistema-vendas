@echo off
title Nascimento Tech — Servidor
cd /d %~dp0

echo ============================================
echo   Nascimento Tech — Iniciando servidor...
echo ============================================

:: Faz backup antes de iniciar
python backup.py
echo.

:: Inicia com Gunicorn (2 workers, porta 5000)
echo Iniciando servidor em http://localhost:5000
echo Pressione CTRL+C para parar.
echo.
gunicorn -w 2 -b 0.0.0.0:5000 --timeout 120 app:app

pause
