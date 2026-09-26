# mcptube — install record

Manual patches applied directly to the **pipx virtualenv**, outside of pip.

| | |
|---|---|
| pipx venv | `C:\Users\mrsim\pipx\venvs\mcptube` |
| site-packages | `C:\Users\mrsim\pipx\venvs\mcptube\Lib\site-packages\mcptube` |
| Installed version | mcptube 0.2.1 (PyPI) |
| Python | 3.12.10 |
| Working clone | `C:\Users\mrsim\Tools\mcptube` (branch `vision`) |

---

## ⚠️ Reapply after upgrade

**Every patch below lives only in the pipx venv's `site-packages`. `pipx upgrade mcptube`,
`pipx reinstall mcptube`, or `pipx install --force mcptube` overwrites those files and silently
reverts all of them.** Nothing errors — ingestion and export just go back to their old behaviour,
which for several of these patches means producing *zero* frames or *no* export at all.

### Patched file inventory

These are the only files that differ from upstream `origin/vision`. The clone at
`C:\Users\mrsim\Tools\mcptube` is the master copy of all of them.

| File | Status | What the patch does |
|---|---|---|
| `src/mcptube/export.py` | **new file** | Patch 8 (LLM Wiki markdown export) + Patch 9 (frame JPEGs copied to `raw\assets\<videoID>\`, referenced relatively) |
| `src/mcptube/cli.py` | +71 lines | Registers the `export` subcommand (`--all`, `--out/-o`, `--no-fetch-published`, `--no-assets`) |
| `src/mcptube/ingestion/scene_frames.py` | heavily patched | Patch 10 (even sampling) + the six pre-existing venv fixes listed at the bottom of this file |
| `src/mcptube/ingestion/vision.py` | 1 line | Vision model `claude-sonnet-4-20250514` → `claude-sonnet-5` (the former is retired on the first-party API) |
| `src/mcptube/llm.py` | 6 lines | Same model repoint, plus `litellm.drop_params = True` (Sonnet 5 rejects the hardcoded `temperature=0.2`) |
| `src/mcptube/wiki/engine.py` | +25 lines | Patch 11 — honours `MCPTUBE_SKIP_WIKI` |
| `src/mcptube/wiki/extractor.py` | +15 lines | Patch 11 — `build_pages_without_llm()` |
| `src/mcptube/ingestion/frames.py` | 1 line | Format selector `best[ext=mp4]/best` → `bestvideo[ext=mp4]/bestvideo/best[ext=mp4]/best`; YouTube no longer serves a muxed stream for most videos |
| `tests/test_scene_frames.py` | +46 lines | `TestSelectEvenly` — 7 tests covering Patch 10 |

Not tracked by git, but part of the working setup: `_batch.py`, `_run_batch.cmd`,
`_run_export.cmd`, `_batch_results.json`, `_model_cmp.json`.

**Entry point used by the batch runner (verified 2026-09-18):** `_run_batch.cmd` invokes
`pipx\venvs\mcptube\Scripts\python.exe` on `_batch.py`, and `_batch.py` shells out to
`pipx\venvs\mcptube\Scripts\mcptube.exe`, whose header embeds
`#!C:\Users\mrsim\pipx\venvs\mcptube\Scripts\python.exe`. Both hops therefore resolve to the
patched `site-packages`. `_batch.py` previously called the `~\.local\bin\mcptube.exe` shim
instead; that is a Windows symlink into the same venv, but it is one unverifiable indirection, so
it was repointed at the venv entry point directly. `_run_batch.cmd` also now sets `PYTHONUTF8=1`
for its own process — `_batch.py` echoes mcptube's stderr into `_batch.log`, and on a cp1252
stream a single non-ASCII character there raises `UnicodeEncodeError` and kills the run.

### Restore command

After any upgrade or reinstall, re-copy **all eight** patched modules from the clone — copying only
`scene_frames.py` leaves the export step and the model repoint reverted:

```powershell
$clone = "C:\Users\mrsim\Tools\mcptube\src\mcptube"
$site  = "C:\Users\mrsim\pipx\venvs\mcptube\Lib\site-packages\mcptube"

Copy-Item "$clone\export.py"                  "$site\export.py"                  -Force
Copy-Item "$clone\cli.py"                     "$site\cli.py"                     -Force
Copy-Item "$clone\llm.py"                     "$site\llm.py"                     -Force
Copy-Item "$clone\ingestion\scene_frames.py" "$site\ingestion\scene_frames.py" -Force
Copy-Item "$clone\ingestion\vision.py"       "$site\ingestion\vision.py"       -Force
Copy-Item "$clone\ingestion\frames.py"       "$site\ingestion\frames.py"       -Force
Copy-Item "$clone\wiki\engine.py"           "$site\wiki\engine.py"           -Force
Copy-Item "$clone\wiki\extractor.py"        "$site\wiki\extractor.py"        -Force

Get-ChildItem "$site" -Recurse -Filter __pycache__ | Remove-Item -Recurse -Force
```

### Verify all eight are live

```powershell
& "C:\Users\mrsim\pipx\venvs\mcptube\Scripts\python.exe" -c @"
import litellm
from mcptube.ingestion.scene_frames import SceneFrameExtractor as S
from mcptube.ingestion.vision import VisionDescriber as V
from mcptube.ingestion.frames import FrameExtractor
import mcptube.export, mcptube.cli, inspect
print('patch10   ', hasattr(S, '_select_evenly'))
print('yuvj420p  ', 'yuvj420p' in inspect.getsource(S))
print('download  ', hasattr(S, '_download_video'))
print('export    ', hasattr(mcptube.export, 'export_video'))
print('cli export', hasattr(mcptube.cli, 'export'))
print('assets    ', hasattr(mcptube.export, 'copy_frame_assets'))
print('model     ', V._VISION_MODELS['ANTHROPIC_API_KEY'])
print('drop_param', litellm.drop_params)
from mcptube.wiki.engine import _skip_wiki
from mcptube.wiki.extractor import KnowledgeExtractor as K
print('patch11   ', hasattr(K, 'build_pages_without_llm'))
print('skip_wiki ', _skip_wiki())
"@
```

Anything printing `False`, or a model other than `anthropic/claude-sonnet-5`, means the upgrade
reverted that patch.

A second, independent check — the subcommand has to actually be reachable from the entry point:

```powershell
& "C:\Users\mrsim\pipx\venvs\mcptube\Scripts\mcptube.exe" export --help
```


These patches are **not** on `origin/vision`. The clone is the only master copy — do not delete it.

As of **2026-09-19** the clone's changes are **committed locally** on branch `vision`, so an
accidental `git checkout` no longer discards them. They have not been pushed. The batch scripts
(`_batch.py`, `_run_batch.cmd`, `_reingest.cmd`, `_run_export.cmd`), this record, and the run
artifacts (`_batch_results.json`, `_model_cmp.json`) are tracked from that commit onward;
`_batch.log` remains untracked, matched by `*.log` in `.gitignore`.

---

## Clone ↔ venv reconciliation

**Verified:** 2026-09-18

Every `.py` file under `site-packages\mcptube` was compared byte-for-byte against the
corresponding file under the clone's `src\mcptube`, ignoring line endings (venv LF, clone CRLF).

- **26 of 26 modules identical.** No file exists in one tree and not the other.
- No files needed to be changed in the clone — the earlier Patch 10 sync had already brought it
  level, and Patches 8 and 9 had been written to both trees at the time they were applied.
- The one extra file in the venv was `ingestion\scene_frames.py.pre-patch10.bak`, a deliberate
  backup, not drift. It was deleted on 2026-09-26 (see Maintenance log).

The clone is therefore a complete recovery source for the venv as it stands.

---

## Secrets — `MCPTUBE_ANTHROPIC_KEY`

**Changed:** 2026-09-19
**Files:** `_batch.py`, `_run_batch.cmd`, `_reingest.cmd`, `_run_export.cmd`

### Why the key is not called `ANTHROPIC_API_KEY`

**`ANTHROPIC_API_KEY` must not be set globally on this machine.** A user-level variable of that
name — anything set with `setx`, i.e. stored in `HKCU\Environment` — is inherited by *every*
process started from then on. The Claude CLI that backs LLM Wiki reads `ANTHROPIC_API_KEY` and
prefers it over its own signed-in session, so a global key silently re-routes those sessions onto
raw API billing under a different identity. Nothing announces this: the sessions keep working,
they are simply being paid for and attributed elsewhere.

The batch scripts need that key, though. So it is stored under a name nothing else looks for,
**`MCPTUBE_ANTHROPIC_KEY`**, and renamed to `ANTHROPIC_API_KEY` only inside the environment handed
to the mcptube child process. Nothing outside that child ever sees the name.

```powershell
setx MCPTUBE_ANTHROPIC_KEY "sk-ant-..."
```

If a global one was ever set, delete it — `setx ANTHROPIC_API_KEY ""` is **not** enough, as that
leaves the variable defined-but-empty:

```powershell
reg delete "HKCU\Environment" /v ANTHROPIC_API_KEY /f
```

Both take effect only in *new* processes; already-open terminals, and the Claude desktop app,
keep their inherited copy until restarted.

### How each script does it

| Script | Mechanism |
|---|---|
| `_batch.py` | `anthropic_key(env)` reads `MCPTUBE_ANTHROPIC_KEY` through `user_env()` — process environment first, then `HKCU\Environment`, so a `setx` from another window is picked up without reopening this terminal. It then pops any inherited `ANTHROPIC_API_KEY` out of the copy and writes the key in, where `env` is the dict passed to `subprocess.run(..., env=env)`. The script's own `os.environ` is never touched. A missing key is **fatal** (exit 1): without one, `add_video` still succeeds and exports a file with frame timestamps and no descriptions. |
| `_run_batch.cmd`, `_reingest.cmd` | `setlocal`, then `set "ANTHROPIC_API_KEY="` to scrub any inherited copy before `_batch.py` starts — `_batch.py` supplies the real key to each mcptube subprocess itself. Both echo whether the key is visible in the current shell; neither forces a value. |
| `_run_export.cmd` | Invokes `mcptube.exe` directly, so it does the rename itself inside `setlocal`. Absence is **not** fatal here: `export` constructs an `LLMClient` (`cli.py:40`) only to detect which providers exist, and makes no LLM calls. Unlike `_batch.py` this reads the current shell only, so a key set by `setx` elsewhere needs a new shell to be visible. |

### A global key is a hard stop

If `anthropic_key()` finds an inherited `ANTHROPIC_API_KEY`, it prints a FATAL and exits **1**
rather than warning and carrying on. The run itself would have been correct either way — the
inherited value is scrubbed from `env` before the key is written in — but the global is still
hijacking LLM Wiki's Claude CLI sessions for as long as it exists, and a warning scrolling past
in `_batch.log` is easy to miss. Refusing to start is what actually gets it cleaned up.

So a batch run now has two ways to stop before ingesting anything:

| Condition | Result |
|---|---|
| `MCPTUBE_ANTHROPIC_KEY` missing from both the environment and `HKCU\Environment` | FATAL, exit 1 |
| `ANTHROPIC_API_KEY` present in the inherited environment | FATAL, exit 1 |

The second can fire even after the registry value is deleted: already-open terminals, and the
Claude desktop app, keep their inherited copy until restarted. That is the intended behaviour —
those are exactly the sessions still being mis-billed.

### Three traps this relies on

1. **`setlocal` is what makes "subprocess only" true.** Without it, a `set` in a `.cmd` persists in
   the calling shell for the rest of its life, and anything started from that shell afterwards —
   the Claude CLI included — inherits it. This is the same hijack, one scope down.
2. **The `if defined` guard is required.** In a batch file an undefined `%VAR%` expands to the
   *literal text* `%VAR%`, not to an empty string. Without the guard,
   `set "ANTHROPIC_API_KEY=%MCPTUBE_ANTHROPIC_KEY%"` on an unset key would hand mcptube the
   literal string `%MCPTUBE_ANTHROPIC_KEY%` as a credential.
3. **`endlocal & exit /b %ERRORLEVEL%`, never a bare `endlocal`.** A trailing bare `endlocal`
   resets the exit code to 0 — measured: a script ending `setlocal … cmd /c exit /b 7 … endlocal`
   exits **0**, and with `endlocal & exit /b %ERRORLEVEL%` exits **7**. `%ERRORLEVEL%` is expanded
   when the line is parsed, before `endlocal` runs, so it captures the pre-`endlocal` value.
   Without it a failed batch run reports success to whatever launched it.

### Verified 2026-09-19

- With no global set and `MCPTUBE_ANTHROPIC_KEY=sk-ant-GOOD`, `anthropic_key()` put
  `sk-ant-GOOD` into the subprocess env and left the parent's `os.environ` untouched.
- With `ANTHROPIC_API_KEY=sk-ant-STALE` also inherited, it exited **1** without starting the
  batch (see "A global key is a hard stop" below).
- `_run_batch.cmd` and `_reingest.cmd` reached the `_batch.py` invocation with
  `ANTHROPIC_API_KEY` empty and `MCPTUBE_ANTHROPIC_KEY` intact; the calling shell's
  `sk-ant-STALE` was unchanged on return.
- `_run_export.cmd` child saw `sk-ant-GOOD`; the caller was back to `sk-ant-STALE` the instant the
  script returned.
- With the key absent from both the environment and the registry, `_reingest.cmd` exited **1**
  with the FATAL message and did **not** fall back to the stale `ANTHROPIC_API_KEY` in scope.

### Not affected — do not "fix" these

`print('model     ', V._VISION_MODELS['ANTHROPIC_API_KEY'])` in the verify block above reads a
**dict key in mcptube's own source**, not an environment variable; it stays as it is. The same
goes for `llm.py`'s `_KEY_TO_MODEL` and `vision.py`'s `_VISION_MODELS`. mcptube itself genuinely
looks up `os.environ["ANTHROPIC_API_KEY"]` — which is precisely why the rename has to happen in
the child's environment rather than inside mcptube.

---

## Patch 11 — `MCPTUBE_SKIP_WIKI`

**Applied:** 2026-09-18
**Files:** `mcptube/wiki/engine.py`, `mcptube/wiki/extractor.py`
**Applied to:** pipx venv **and** the working clone (both, content-identical)

### What it does

`MCPTUBE_SKIP_WIKI=1` (also accepts `true`/`yes`/`on`, case-insensitive, whitespace
tolerant) skips mcptube's internal wiki compilation during `add_video`. Everything the
vault export depends on is kept:

| Kept | Skipped |
|---|---|
| Transcript + chapters | Knowledge-extraction LLM pass (`KnowledgeExtractor.extract`) |
| Classification tags | Entity / topic / concept page creation |
| Scene-frame extraction | `_rewrite_entity_overview` calls |
| Vision frame descriptions | `_rewrite_synthesis` calls |
| The video wiki page, with `key_frames` | The video page's `summary` and `key_timestamps` |

### Why the video page is still written

`key_frames` — the per-frame vision descriptions — lives on the video wiki page, and
`export.load_frame_descriptions()` reads it from there. Skipping the wiki entirely would
throw away the descriptions. So `WikiEngine.ingest_video` swaps the LLM extraction pass for
`KnowledgeExtractor.build_pages_without_llm()`, which calls the same `_build_pages()` with an
empty `data` dict. `key_frames` is assembled mechanically from the vision output, so it
survives; only `summary` and `key_timestamps` come from the skipped call and are left empty.
`_build_pages` also returns empty entity/topic/concept lists, so `update_wiki()` writes the
video page and makes no LLM calls of its own.

Verified by direct invocation: 2 frame descriptions in, 2 out with text intact, transcript and
tags preserved, 0 entity/topic/concept pages, `summary=''`, `key_timestamps={}`. `text_only=True`
still blanks `key_frames` as before.

### Honoured by

- `_batch.py` — reads it via `user_env()` (environment first, then `HKCU\Environment`), so a
  terminal opened before the `setx` still picks it up; passes it to every `mcptube` subprocess
  and logs which mode the run is in.
- `_run_batch.cmd`, `_reingest.cmd` — echo the effective setting at startup. They deliberately
  do **not** force a value; the persistent `setx` is the single source of truth.

Set persistently with:

```powershell
setx MCPTUBE_SKIP_WIKI 1
```

Since 2026-09-26 the mcptube MCP server is no longer registered in Claude Desktop (see
Maintenance log), so `_batch.py` is the only consumer of this setting. If the server is ever
re-added, it inherits its environment from the Claude desktop app and only picks this up after
that app restarts.

### Cost

Measured from the 36 video wiki pages on disk after the 2026-09-18 run: median **~9,150 input /
~3,174 output tokens per video** of wiki compilation, ≈ **$0.048 per video** at Sonnet 5's
$2/$10 per MTok. See "Wiki compilation cost" below.

### `no_vision` detection

`_batch.py`'s `vision_ran()` now tests the frame descriptions themselves —
`any(f["description"].strip() for f in page["key_frames"])` — rather than the mere existence of
the video page. Under Patch 11 the page always exists, so a page-exists test would have passed
on undescribed frames and, had the page been skipped too, would have flagged every video.

---

## Patch 10 — even-sampling frame selection

**Applied:** 2026-09-18
**Fixes:** [issue #11](https://github.com/0xchamin/mcptube/issues/11) — "Scene frame cap is
first-N, silently truncating the end of the video"
**File:** `mcptube/ingestion/scene_frames.py`
**Applied to:** pipx venv **and** the working clone (both, content-identical)
**Backup of pre-patch venv file:** `…\ingestion\scene_frames.py.pre-patch10.bak` — deleted 2026-09-26; the pre-patch code remains in git history

### Problem

`_extract_with_ffmpeg` capped extraction with `-frames:v 50`. That is an *output* limit: ffmpeg
tears down the pipeline as soon as 50 JPEGs are written, so the decoder never reaches the rest of
the video. On any video with more than 50 scene changes the back of the runtime was never
examined, and nothing in the output said so.

### Change

Extraction is now two passes:

1. **Detect** — `select='gt(scene,T)',showinfo` with `-f null -`. Nothing is encoded, so this
   costs one decode and no writes. Produces the full candidate timestamp list.
   (`scale`/`format` are deliberately omitted: `select` runs before them in the normal chain, so
   they never influenced detection.)
2. **Select** — `_select_evenly()` divides the runtime into `_MAX_FRAMES` equal buckets and takes
   the earliest candidate in each. Empty buckets leave budget unspent, so the remainder is
   backfilled with the leftover candidates furthest from an already-chosen one.
3. **Extract** — one `ffmpeg -ss <t> … -frames:v 1` per selected timestamp. Per-frame seeking
   avoids a second full decode.

When the cap would have truncated, a `logger.warning` now names the cost explicitly:

```
Scene-frame cap reached for ASlLmFPlJ3M: 115 scene changes detected, keeping 50 spread
evenly across the runtime. Taking the first 50 would have covered only 705.2s of 1942.9s (36%).
```

`_extract_with_ffmpeg` is retained as the fallback for when pass 1 detects no scene changes at
all (there is no candidate list to spread in that case).

### Measured — `ASlLmFPlJ3M`, 32:23 (1943 s), threshold 0.4

115 scene changes detected; budget 50 frames.

| | Before (first-N) | After (even) |
|---|---|---|
| Frames | 50 (cap) | 50 (cap) |
| First / last frame | 2.3 s / 705.2 s | 2.3 s / 1900.9 s |
| **Runtime covered** | **36.3%** | **97.8%** |
| Mean gap between frames | — | 38.7 s (min 13.4 s, max 185.0 s) |

Frames per quarter of runtime:

| Quarter | Before | After |
|---|---|---|
| Q1 0–486 s | 32 | 11 |
| Q2 486–972 s | 18 | 16 |
| Q3 972–1457 s | **0** | 12 |
| Q4 1457–1943 s | **0** | 11 |

Extraction wall time (download + detect + 50 seeks): 60.7 s.

### Tests

`tests/test_scene_frames.py` — 29 passed (22 pre-existing + 7 new in `TestSelectEvenly`).

```powershell
# conftest.py imports chromadb, which is not a declared dependency, so --noconftest is required
python -m pytest tests/test_scene_frames.py -q --noconftest
```

---

## Pre-existing venv modifications (present before Patch 10)

Found already applied to the venv's `scene_frames.py` and carried forward by Patch 10. They were
**not** in the clone; Patch 10 synced the clone up to match, so the two copies now agree.

| Change | Why |
|---|---|
| `format=yuvj420p` in the filter chain | ffmpeg 9's mjpeg encoder rejects limited-range YUV ("Non full-range YUV is non-standard"), fails to open, and silently writes **zero** frames for every video. |
| `_download_video()` — download first, then run ffmpeg on the local file | Streaming a googlevideo URL into ffmpeg gets throttled to ~2 MB/min; yt-dlp's own downloader saturates the link. Hours vs. seconds per video. |
| `_FORMAT_SELECTOR` preferring H.264 ≤720p | 1280×720 matches `_SCALE_WIDTH` exactly and decodes far faster than AV1/VP9 1080p60. Muxed `best` no longer exists on most YouTube videos. |
| `MCPTUBE_SCENE_THRESHOLD` env override | Lets a batch run set the threshold without editing code. |
| `-fps_mode vfr` (was `-vsync vfr`) | `-vsync` is deprecated in ffmpeg 9. |
| ffmpeg timeout 120 s → 1800 s | Scene detection must read the whole video. |

Every other module in the venv is byte-identical to the clone apart from line endings (venv LF,
clone CRLF).

---

## Wiki compilation cost — what Patch 11 saves

**Measured:** 2026-09-18, from the 36 video wiki pages and 700+ entity/topic/concept pages on
disk after that day's run. Token counts were **not** logged by mcptube, so these are computed
from the actual stored prompts and outputs at **4 characters/token**; treat them as ±15%, not
as billing figures.

Per video, `add_video` makes these LLM calls that Patch 11 removes:

1. **One knowledge-extraction call.** Input = a 1,842-char template + the full transcript + the
   whole frame-description block. This is the dominant cost: transcripts ran 4.4k–75.5k chars
   (median 21k). Output = the extraction JSON (summary, key timestamps, and every
   entity/topic/concept entry), median ~10.3k chars.
2. **A rewrite call per already-existing page the video touches** — `_rewrite_entity_overview`
   (512 max tokens) or `_rewrite_synthesis` (1,024 max tokens). New pages are saved without an
   LLM call, so this count starts at zero and grows as the wiki fills in: the first videos
   ingested made 0 rewrites, later ones made 5–11 (median 3, mean 3.7).

| | Median | Mean |
|---|---|---|
| Input tokens / video | 9,150 | 10,700 |
| Output tokens / video | 3,174 | 3,280 |
| Rewrite calls / video | 3 | 3.7 |
| **Cost / video @ Sonnet 5 $2/$10 per MTok** | **$0.048** | **$0.052** |

Across the 36 measured videos: **~$1.86 total**. For a 48-video batch, roughly **$2.30–2.50**.

Two caveats worth keeping in view:

- The saving is **not** flat. It scales with transcript length — the longest video in the set
  (`gb5TlGw6Uks`, 75.5k chars) cost ~$0.11 on its own — and the rewrite count grows as the wiki
  accumulates overlapping pages, so per-video wiki cost drifts *upward* over a long run while
  vision cost stays flat.
- Vision is the larger line item and is untouched by Patch 11: ~50 frames/video at 107–213
  output tokens each. Patch 11 cuts the wiki overhead, not the bulk of the spend.

---

## Maintenance log

### 2026-09-26

- Deleted the Patch 10 before/after comparison frame folders `ASlLmFPlJ3M_scenes_BASELINE_prepatch10`
  (4.68 MB, 51 files) and `b4d32pBa3UY_scenes_BASELINE_prepatch10` (2.22 MB, 51 files) from
  `C:\Users\mrsim\.mcptube\frames`. Nothing referenced them — mcptube reads only `<id>_scenes` —
  and the live `_scenes` folders for both videos were left intact.
- Deleted `ingestion\scene_frames.py.pre-patch10.bak` from the pipx venv after confirming the live
  `scene_frames.py` differs from it (it has `_select_evenly`; the backup did not) and matches the
  clone apart from line endings.
- Rotated `MCPTUBE_ANTHROPIC_KEY`. The new key was tested end-to-end with one video, and the old
  key was revoked in the Anthropic Console. No key value is recorded here.
- Removed the mcptube MCP server from Claude Desktop's `claude_desktop_config.json`; the previous
  config is backed up as `claude_desktop_config.json.bak-2026-09-26`. Checked afterwards: the live
  config's `mcpServers` is empty, and the backup still holds the `mcptube` entry. The string
  `mcptube` still appears under the config's `preferences` key; that is not a server entry.
- Videos are now added **only** via `_batch.py`. With the MCP server gone, nothing in Claude
  Desktop can call `add_video`, and the Desktop app no longer needs `MCPTUBE_SKIP_WIKI` or an API
  key in its environment.

---

## Environment

ffmpeg 9.0.1-full_build (Gyan) · yt-dlp 2026.8.19 · Windows 11 Pro 26200 · Python 3.12.10
