"""Batch ingest + export for the ai-research vault.

Resumable: skips videos already in the library with an export file present.
Writes incremental results so progress survives a dropped connection.
Reads secrets from the persistent user environment (HKCU\\Environment) rather
than from any file on disk -- from MCPTUBE_ANTHROPIC_KEY, never from a global
ANTHROPIC_API_KEY. See anthropic_key() for why.
"""
import json
import os
import subprocess
import sys
import time
import winreg
from pathlib import Path

VIDEO_IDS = [
    "EDxVn1q8udE", "AlqUtIHHuvI", "ff7om2bBLKM", "ASlLmFPlJ3M", "xC8C1w2fxg4",
    "b4d32pBa3UY", "DTCyvo6cC54", "hQvwMj7IJe4", "MS7E5TXNviM", "ib74sLgjIBM",
    "1JTyPNeD9dk", "5aUgDDWd4-k", "NdeOsuoIGuc", "SQrFue3LbwI", "aX8Y183qDpY",
    "cV02finVi4o", "gQef3d3erOs", "Fc5PcxMPigk", "h2MjhbwVKLk", "E8tdIxcfEnk",
    "ud7wzdiM0gk", "gb5TlGw6Uks", "yysILVsfLFM", "TLQLfa7yH4I", "oy49gvlC4eE",
    "FiOTrxq9ckM", "o_YngNoGP1I", "5mfwXuS06Ok", "lGtBPrSrnjY", "3XIGcM7VICc",
    "oh60eagiArU", "WCFoA38rlEg", "7WZ6XldxX0U", "9oi-b5Dvtso", "ZvDkJsKE80k",
    "Qgi5hb7yxjU", "oOCN30ulVyo", "iTY8Q449YNQ", "jdbOVepEtUE", "TErjuicvK_c",
    "TWCSvvTfJNU", "yu7PYU0ONT4", "Bm84BAtOfQw", "jPSirKTGTfo", "A8_nNYLTXEQ",
    "FPnFp8vFM9k", "oXmofS-sjwI", "W2toHa3e4BY",
]

# The pipx venv entry point, not the ~\.local\bin shim. This .exe embeds
# "#!C:\\Users\\mrsim\\pipx\\venvs\\mcptube\\Scripts\\python.exe", so it is
# guaranteed to run the patched site-packages copy.
MCPTUBE = r"C:\Users\mrsim\pipx\venvs\mcptube\Scripts\mcptube.exe"
HERE = Path(__file__).parent
RESULTS = HERE / "_batch_results.json"
MAX_FRAMES = 50


def user_env(name: str) -> str | None:
    """Read a persistent user env var (set by setx) from the registry."""
    if os.environ.get(name):
        return os.environ[name].strip()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            # .strip() matters: a stray leading space makes Windows reject the
            # path with WinError 123 (invalid filename syntax).
            return str(winreg.QueryValueEx(k, name)[0]).strip()
    except OSError:
        return None


def log(msg: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def anthropic_key(env: dict) -> None:
    """Put the Anthropic key into `env` as ANTHROPIC_API_KEY, from
    MCPTUBE_ANTHROPIC_KEY.

    The key must NOT be set globally as ANTHROPIC_API_KEY. A user-level
    variable of that name (HKCU\\Environment) is inherited by every process
    started on this machine, and the Claude CLI that backs LLM Wiki reads it
    in preference to its own signed-in session -- so a global key silently
    reroutes those sessions onto raw API billing under a different identity.

    So the key lives under a name nothing else looks for, and is renamed to
    ANTHROPIC_API_KEY only here, inside `env`: the dict handed to
    subprocess.run(). This process's own os.environ is never touched, and the
    rename reaches nothing but the mcptube child processes.
    """
    key = user_env("MCPTUBE_ANTHROPIC_KEY")
    if not key:
        log("FATAL: MCPTUBE_ANTHROPIC_KEY not found in environment or registry")
        log('       set it with:  setx MCPTUBE_ANTHROPIC_KEY "sk-ant-..."')
        sys.exit(1)

    # A global ANTHROPIC_API_KEY is a hard stop, not a warning. This run would
    # be correct either way -- it is scrubbed from `env` below -- but every
    # hour the global survives is an hour of LLM Wiki's Claude CLI sessions
    # being billed and attributed to this key instead of the signed-in
    # account, and a warning scrolling past in _batch.log is easy to miss.
    # Refusing to start is what actually gets it cleaned up.
    if env.pop("ANTHROPIC_API_KEY", None):
        log("FATAL: ANTHROPIC_API_KEY is set globally. It hijacks LLM Wiki's "
            "Claude CLI sessions. Remove it, then re-run:")
        log('       reg delete "HKCU\\Environment" /v ANTHROPIC_API_KEY /f')
        log("       (already-open terminals and the Claude desktop app keep "
            "their inherited copy until restarted)")
        sys.exit(1)

    env["ANTHROPIC_API_KEY"] = key


def run(args: list[str], env: dict, timeout: int = 3600):
    return subprocess.run(args, env=env, capture_output=True, text=True,
                          timeout=timeout, encoding="utf-8", errors="replace")


def frame_stats(video_id: str, duration: float) -> dict:
    """Frame count and coverage from the scene metadata sidecar."""
    meta = (Path.home() / ".mcptube" / "frames" / f"{video_id}_scenes"
            / "metadata.json")
    if not meta.exists():
        return {"frames": 0, "first": None, "last": None,
                "coverage_pct": 0.0, "capped": False}
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return {"frames": 0, "first": None, "last": None,
                "coverage_pct": 0.0, "capped": False}
    ts = sorted(float(f.get("timestamp", 0.0)) for f in data)
    cov = (ts[-1] / duration * 100) if (ts and duration) else 0.0
    return {"frames": len(ts), "first": ts[0] if ts else None,
            "last": ts[-1] if ts else None, "coverage_pct": round(cov, 1),
            "capped": len(ts) >= MAX_FRAMES}


# mcptube's vision step writes this literal into a frame description when the
# call fails, rather than leaving the field empty. It is non-empty, so a
# .strip() test counts it as described -- match it explicitly.
VISION_PLACEHOLDER = "(description unavailable)"


def described(frame: dict) -> bool:
    """True if a frame carries real vision output, not a failure placeholder."""
    text = (frame.get("description") or "").strip()
    return bool(text) and text != VISION_PLACEHOLDER


def vision_ran(video_id: str) -> bool:
    """True if frame descriptions were actually written for this video.

    `add_video` saves the video and returns success even when the LLM is
    unavailable (bad/missing key): it just skips classification, vision and
    the wiki page. The export then falls back to the scene sidecar and writes
    a file with frame timestamps but no descriptions. Nothing in the CLI
    output says so, so check explicitly.

    This looks at the descriptions themselves, not merely at the presence of
    the video page: under MCPTUBE_SKIP_WIKI the page is still written (that
    is where key_frames lives) but carries no summary, so a page-exists test
    would pass on a page whose frames were never described -- and, with the
    wiki step skipped, a page-absent test would flag every video.

    Every frame must be described, not just one: a vision run cut short by a
    rate limit or a mid-run error leaves the later frames blank, and the
    export ships those gaps without comment. A frame count of zero is a
    failure too -- `all()` over an empty list would otherwise pass a video
    whose frames were never extracted.
    """
    page = (Path.home() / ".mcptube" / "wiki" / "video"
            / f"video-{video_id}.json")
    if not page.exists():
        return False
    try:
        data = json.loads(page.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    frames = data.get("key_frames") or []
    if not frames:
        return False
    placeholder = sum(1 for f in frames
                      if (f.get("description") or "").strip()
                      == VISION_PLACEHOLDER)
    missing = sum(1 for f in frames if not described(f))
    if missing:
        log(f"    {missing}/{len(frames)} frames have no vision description"
            f" ({placeholder} placeholder, {missing - placeholder} blank)")
        return False
    return True


def clear_frame_cache(video_id: str) -> None:
    """Move a cached scene-frame dir aside so extraction actually re-runs.

    `extract_scene_frames` returns cached frames before it re-extracts, and
    `remove_video` does not touch the cache -- so remove+add alone silently
    reuses the old frames.
    """
    d = Path.home() / ".mcptube" / "frames" / f"{video_id}_scenes"
    if d.exists():
        d.rename(d.with_name(f"{video_id}_scenes.stale-{int(time.time())}"))


def video_row(video_id: str):
    """(title, channel, duration) from the store, or None if absent."""
    import sqlite3
    db = Path.home() / ".mcptube" / "mcptube.db"
    con = sqlite3.connect(db)
    try:
        r = con.execute(
            "select title, channel, duration from videos where video_id=?",
            (video_id,)).fetchone()
    finally:
        con.close()
    return r


def main() -> None:
    # Named video IDs on the command line: re-ingest just those, from
    # scratch, ignoring the resume state. Used to redo videos whose earlier
    # run predates a code change.
    targets = [a for a in sys.argv[1:] if not a.startswith("-")]
    force = bool(targets)
    video_ids = targets or VIDEO_IDS

    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["MCPTUBE_SCENE_THRESHOLD"] = "0.4"

    anthropic_key(env)

    skip_wiki = user_env("MCPTUBE_SKIP_WIKI")
    if skip_wiki:
        env["MCPTUBE_SKIP_WIKI"] = skip_wiki

    export_dir = user_env("MCPTUBE_EXPORT_DIR")
    if export_dir:
        env["MCPTUBE_EXPORT_DIR"] = export_dir
    ffmpeg_dir = r"C:\Users\mrsim\AppData\Local\Microsoft\WinGet\Links"
    env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

    results = {}
    if RESULTS.exists():
        try:
            results = json.loads(RESULTS.read_text(encoding="utf-8"))
        except Exception:
            results = {}

    log(f"batch start: {len(video_ids)} videos, threshold 0.4, Sonnet 5"
        + ("  [FORCED RE-INGEST]" if force else ""))
    log(f"export dir : {export_dir}")
    log("wiki       : " + ("SKIPPED (MCPTUBE_SKIP_WIKI="
                           f"{skip_wiki}) - video page + frame descriptions "
                           "only" if skip_wiki
                           else "full compilation (MCPTUBE_SKIP_WIKI not set)"))
    t_batch = time.time()

    for i, vid in enumerate(video_ids, 1):
        prior = results.get(vid)
        if force:
            prior = None
        # Done means: exported, and either it has frames or we already spent a
        # re-ingest attempt on it. A 0-frame result from an interrupted run is
        # not done.
        if (prior and prior.get("status") == "ok" and prior.get("export")
                and Path(prior["export"]).exists()
                and (prior.get("frames", 0) > 0 or prior.get("reingested"))):
            log(f"[{i}/{len(video_ids)}] {vid} already done - skipping")
            continue

        log(f"[{i}/{len(video_ids)}] {vid} ingesting...")
        t0 = time.time()
        entry = {"video_id": vid}

        if force:
            clear_frame_cache(vid)
            if video_row(vid) is not None:
                run([MCPTUBE, "remove", vid], env, timeout=300)

        row = video_row(vid)
        # A run interrupted mid-vision leaves the video in the library with no
        # frames. Treat that as incomplete and re-ingest once, rather than
        # exporting a file with an empty Visual content section.
        partial = (row is not None
                   and frame_stats(vid, 1.0)["frames"] == 0
                   and not (prior or {}).get("reingested"))
        if partial:
            log("    in library but 0 frames - re-ingesting")
            run([MCPTUBE, "remove", vid], env, timeout=300)
            entry["reingested"] = True
            row = None

        if row is None:
            r = run([MCPTUBE, "add",
                     f"https://www.youtube.com/watch?v={vid}"], env)
            if video_row(vid) is None:
                tail = (r.stderr or r.stdout or "")[-300:].replace("\n", " ")
                entry.update(status="ingest_failed", error=tail)
                results[vid] = entry
                RESULTS.write_text(json.dumps(results, indent=2),
                                   encoding="utf-8")
                log(f"    FAILED: {tail}")
                continue
        else:
            log("    already in library - reusing")

        title, channel, duration = video_row(vid)
        entry.update(title=title, channel=channel, duration=duration)
        entry.update(frame_stats(vid, duration or 0.0))

        r = run([MCPTUBE, "export", vid], env, timeout=600)
        # Locate the file on disk rather than parsing stdout - more robust
        # than splitting on the arrow glyph the CLI prints.
        hits = sorted(Path(export_dir).glob(f"youtube-{vid}-*.md")) \
            if export_dir else []
        path = str(hits[0]) if hits else ""
        entry["export"] = path
        entry["vision"] = vision_ran(vid)
        if not path:
            entry["status"] = "export_failed"
        elif not entry["vision"]:
            # File written, but with no frame descriptions in it.
            entry["status"] = "no_vision"
        else:
            entry["status"] = "ok"
        if not path:
            entry["error"] = (r.stderr or r.stdout or "")[-300:].replace("\n", " ")
        entry["seconds"] = round(time.time() - t0, 1)

        results[vid] = entry
        RESULTS.write_text(json.dumps(results, indent=2), encoding="utf-8")
        flag = "  *** HIT 50-FRAME CAP ***" if entry.get("capped") else ""
        if entry["status"] == "no_vision":
            flag += ("  *** NO VISION DESCRIPTIONS - check "
                     "MCPTUBE_ANTHROPIC_KEY ***")
        log(f"    {entry['frames']} frames, {entry['coverage_pct']}% coverage,"
            f" {entry['seconds']}s{flag}")

    ok = sum(1 for e in results.values() if e.get("status") == "ok")
    log(f"batch done: {ok}/{len(video_ids)} ok "
        f"in {(time.time()-t_batch)/60:.0f} min")


if __name__ == "__main__":
    main()
