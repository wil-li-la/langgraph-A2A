@echo off
REM Sets up the Python virtual environment for the LangGraph A2A backend.
REM Run from the backend\ directory: setup.bat

setlocal enabledelayedexpansion

REM Default python version if PYTHON is not set
if "%PYTHON%"=="" (
    set PYTHON=python
)

echo ==> Creating virtual environment...
%PYTHON% -m venv .venv
if errorlevel 1 exit /b 1

echo ==> Installing stretch3-zmq-core (ZMQ client, LFS skipped)...
set GIT_LFS_SKIP_SMUDGE=1
git clone https://github.com/lnfu/stretch3-zmq.git C:\temp\stretch3-zmq
if errorlevel 1 exit /b 1

.venv\Scripts\pip.exe install C:\temp\stretch3-zmq\packages\core
if errorlevel 1 exit /b 1

echo ==> Installing cure skills (no-detection branch, LFS skipped)...
git clone --branch no-detection https://github.com/wil-li-la/stretch_skills.git C:\temp\cure-no-detection
if errorlevel 1 exit /b 1

.venv\Scripts\pip.exe install --no-deps C:\temp\cure-no-detection
if errorlevel 1 exit /b 1

echo ==> Installing cure runtime dependencies...
.venv\Scripts\pip.exe install rerun-sdk pyzmq scipy opencv-python
if errorlevel 1 exit /b 1

echo ==> Applying cure patches (navigate_avoidance + UTF-8 config)...
.venv\Scripts\python.exe patches\cure_patches.py
if errorlevel 1 exit /b 1

echo ==> Installing project dependencies (pyproject.toml)...
.venv\Scripts\pip.exe install -e .
if errorlevel 1 exit /b 1

echo.
echo Done. Activate with:  .venv\Scripts\activate
echo Then run:             python -m app --host localhost --port 9999