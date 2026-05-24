@echo off
setlocal

echo ==========================================
echo [INFO] Iniciando Siftool...
echo ==========================================

REM Verificar se o Python esta instalado
python --version >nul 2>&1
if errorlevel 1 goto :no_python

REM Verificar se o ambiente virtual existe
if exist .venv goto :run_app

echo [INFO] Criando ambiente virtual (.venv)...
python -m venv .venv
if errorlevel 1 goto :err_venv

echo [INFO] Atualizando pip...
.venv\Scripts\python.exe -m pip install --upgrade pip

echo [INFO] Instalando dependencias de requirements.txt...
.venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 goto :err_deps

:run_app
echo [INFO] Iniciando aplicacao...
start "" ".venv\Scripts\pythonw.exe" siftool.py
exit /b 0

:no_python
echo [ERRO] Python nao encontrado no sistema. Por favor, instale o Python.
pause
exit /b 1

:err_venv
echo [ERRO] Falha ao criar o ambiente virtual (.venv).
pause
exit /b 1

:err_deps
echo [ERRO] Falha ao instalar as dependencias.
pause
exit /b 1
