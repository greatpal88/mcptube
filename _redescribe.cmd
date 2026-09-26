@echo off
rem Repair pass: re-run vision only on frames whose description is the
rem "(description unavailable)" placeholder, reusing the already-extracted
rem JPEGs. No re-ingest, no re-extraction, no re-export.
rem
rem   _redescribe.cmd --dry-run --all      size the run, call nothing
rem   _redescribe.cmd --all                repair every affected video
rem   _redescribe.cmd jdbOVepEtUE ...      repair named videos
rem
rem setlocal confines every `set` below to this cmd.exe process and its
rem children. Without it, running this script from an interactive prompt
rem leaves ANTHROPIC_API_KEY set in that shell, and anything launched from it
rem afterwards -- the Claude CLI included -- inherits the key.
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

rem The key is stored as MCPTUBE_ANTHROPIC_KEY, never as a global
rem ANTHROPIC_API_KEY: a user-level variable of that name is inherited by
rem every process on this machine, and the Claude CLI reads it in preference
rem to its own signed-in session. Rename it into place for this process only.
rem
rem `if defined` is required: in a batch file an undefined %VAR% expands to
rem the literal text "%VAR%", which would be handed over as the credential.
rem
rem Unlike export, this DOES make vision calls, so a missing key is fatal for
rem a real run -- _redescribe.py exits 1 and describes nothing. --dry-run
rem needs no key. This reads the current shell only, so a key set by `setx`
rem in another window needs a new shell to be visible.
set "ANTHROPIC_API_KEY="
if defined MCPTUBE_ANTHROPIC_KEY (
  set "ANTHROPIC_API_KEY=%MCPTUBE_ANTHROPIC_KEY%"
) else (
  echo [setup] MCPTUBE_ANTHROPIC_KEY not set - only --dry-run will work
)

rem The pipx venv interpreter, not a bare `python`: mcptube and litellm are
rem installed there and nowhere else on PATH.
"C:\Users\mrsim\pipx\venvs\mcptube\Scripts\python.exe" "%~dp0_redescribe.py" %*
endlocal & exit /b %ERRORLEVEL%
