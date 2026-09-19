@echo off
rem setlocal confines every `set` below to this cmd.exe process and its
rem children. Without it these leak into the calling shell, and anything
rem launched from that shell afterwards -- the Claude CLI included --
rem inherits them.
setlocal
rem The API key must never be global. A user-level ANTHROPIC_API_KEY is
rem inherited by every process on this machine, and the Claude CLI behind
rem LLM Wiki prefers it over its own signed-in session. Clear any inherited
rem copy here; _batch.py supplies the real key to its mcptube subprocesses
rem from MCPTUBE_ANTHROPIC_KEY.
set "ANTHROPIC_API_KEY="
if defined MCPTUBE_ANTHROPIC_KEY (
  echo [setup] MCPTUBE_ANTHROPIC_KEY present in this shell
) else (
  echo [setup] MCPTUBE_ANTHROPIC_KEY not in this shell - _batch.py will read HKCU\Environment
)
rem PYTHONUTF8 is for _batch.py itself: it echoes mcptube's stderr into
rem _batch.log, and that text can contain non-ASCII. Without this the log
rem write raises UnicodeEncodeError and kills the run mid-batch.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
rem MCPTUBE_SKIP_WIKI is honoured, not forced here: it is set persistently
rem via setx. _batch.py also reads it from HKCU\Environment, so a terminal
rem opened before the setx still picks it up.
if defined MCPTUBE_SKIP_WIKI (
  echo [setup] MCPTUBE_SKIP_WIKI=%MCPTUBE_SKIP_WIKI%  - wiki compilation skipped
) else (
  echo [setup] MCPTUBE_SKIP_WIKI not set - full wiki compilation will run
)
cd /d C:\Users\mrsim\Tools\mcptube
"C:\Users\mrsim\pipx\venvs\mcptube\Scripts\python.exe" -u _batch.py >> _batch.log 2>&1
endlocal & exit /b %ERRORLEVEL%
