@echo off
rem setlocal confines every `set` below to this cmd.exe process and its
rem children. Without it, running this script from an interactive prompt
rem leaves ANTHROPIC_API_KEY set in that shell, and anything launched from it
rem afterwards -- the Claude CLI included -- inherits the key.
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set MCPTUBE_EXPORT_DIR=C:\Users\mrsim\OneDrive - Initiative Project Inc\Vaults\ai-research\raw\sources

rem The key is stored as MCPTUBE_ANTHROPIC_KEY, never as a global
rem ANTHROPIC_API_KEY: a user-level variable of that name is inherited by
rem every process on this machine, and the Claude CLI behind LLM Wiki reads
rem it in preference to its own signed-in session. Rename it into place for
rem this process only, so mcptube.exe inherits it and nothing else does.
rem
rem `if defined` is required: in a batch file an undefined %VAR% expands to
rem the literal text "%VAR%", which would be handed over as the credential.
rem
rem Absence is not fatal here -- `mcptube export` builds an LLMClient only to
rem detect which providers exist and makes no LLM calls. Unlike _batch.py,
rem this reads the current shell only, so a key set by `setx` in another
rem window needs a new shell to be visible.
set "ANTHROPIC_API_KEY="
if defined MCPTUBE_ANTHROPIC_KEY (
  set "ANTHROPIC_API_KEY=%MCPTUBE_ANTHROPIC_KEY%"
) else (
  echo [setup] MCPTUBE_ANTHROPIC_KEY not set - export needs no LLM calls, continuing
)

"C:\Users\mrsim\.local\bin\mcptube.exe" export %*
endlocal & exit /b %ERRORLEVEL%
