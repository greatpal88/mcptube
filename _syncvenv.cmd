@echo off
rem Re-apply this clone's mcptube sources onto the installed pipx venv copy.
rem
rem Several fixes live only in this clone and in the venv's site-packages,
rem because mcptube installs from PyPI and they are not released upstream yet.
rem A `pipx upgrade` or reinstall overwrites site-packages and silently
rem reverts them. Run this afterwards to put them back.
rem
rem   _syncvenv.cmd --check      report what differs, write nothing
rem   _syncvenv.cmd              copy the differing files into the venv
rem   _syncvenv.cmd --force      copy even when the versions disagree
rem
rem The clone is the source of truth; nothing is ever copied back out of the
rem venv. A version mismatch refuses by default -- see _syncvenv.py.
rem
rem setlocal confines every `set` below to this cmd.exe process and its
rem children, so nothing here leaks into the calling shell.
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem No API key is set or needed: this copies files and makes no LLM calls.

rem The pipx venv interpreter, not a bare `python`. Two reasons: it is the
rem interpreter whose site-packages we are writing into, and it is the one
rem whose importlib.metadata reports the INSTALLED version the guard compares
rem against. A different python would report a different package, or none.
"C:\Users\mrsim\pipx\venvs\mcptube\Scripts\python.exe" "%~dp0_syncvenv.py" %*
endlocal & exit /b %ERRORLEVEL%
