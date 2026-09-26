"""Re-run vision on the frames whose description is the failure placeholder.

Repair pass, not an ingest. It reuses the JPEGs already extracted under
~/.mcptube/frames/<videoID>_scenes/, so nothing is downloaded, no scene
detection re-runs, and no video is touched in the SQLite store. Frames that
already carry a real description are never re-sent -- only the placeholders
are, which is what keeps the cost proportional to the damage.

The placeholder is written by mcptube's vision step when a call fails
(see mcptube/ingestion/vision.py). It is a non-empty string, so it reads
downstream as a real description; that is the whole reason these frames went
unnoticed until now.

Usage:
    _redescribe.cmd <videoID> [<videoID> ...]     repair those videos
    _redescribe.cmd --all                          every affected video
    _redescribe.cmd --dry-run --all                report scope, call nothing

--dry-run makes no API calls. Use it to size a run before paying for it.
"""
import os
import sys
import time
from pathlib import Path

VISION_PLACEHOLDER = "(description unavailable)"
FRAMES_DIR = Path.home() / ".mcptube" / "frames"
WIKI_DIR = Path.home() / ".mcptube" / "wiki" / "video"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def is_placeholder(text: str) -> bool:
    return (text or "").strip() == VISION_PLACEHOLDER


def affected_video_ids() -> list[str]:
    """Every video whose wiki page carries at least one placeholder frame."""
    import json

    out = []
    for p in sorted(WIKI_DIR.glob("video-*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        frames = data.get("key_frames") or []
        if any(is_placeholder(f.get("description")) for f in frames):
            out.append(data.get("video_id") or p.stem.replace("video-", ""))
    return out


def scene_dir(video_id: str) -> Path:
    return FRAMES_DIR / f"{video_id}_scenes"


def targets(page) -> list[tuple[int, object]]:
    """(index, frame) for every placeholder frame on a page."""
    return [(i, f) for i, f in enumerate(page.key_frames)
            if is_placeholder(f.description)]


def plan(video_id: str, repo):
    """What a repair of this video would send. Returns (page, jobs, missing)."""
    page = repo.get_video_page(video_id)
    if page is None:
        return None, [], []

    jobs, missing = [], []
    for idx, frame in targets(page):
        path = scene_dir(video_id) / frame.filename
        if path.exists():
            jobs.append({"path": path, "timestamp": frame.timestamp,
                         "index": idx})
        else:
            missing.append(frame.filename)
    return page, jobs, missing


def repair(video_id: str, repo, describer) -> tuple[int, int]:
    """Re-describe one video's placeholder frames. Returns (fixed, remaining)."""
    page, jobs, missing = plan(video_id, repo)
    if page is None:
        log(f"  {video_id}: no wiki page - skipped")
        return 0, 0
    if missing:
        log(f"  {video_id}: {len(missing)} cached frame(s) gone from disk, "
            f"cannot re-describe those")
    if not jobs:
        log(f"  {video_id}: nothing to repair")
        return 0, 0

    log(f"  {video_id}: re-describing {len(jobs)} frame(s)")
    results = describer.describe_frames(jobs)

    # Map back by filename rather than position: describe_frames batches
    # internally and a short reply would otherwise shift every later frame
    # onto the wrong description.
    by_name = {r.filename: r.description for r in results}

    fixed = 0
    for job in jobs:
        new = by_name.get(job["path"].name, "")
        if new and not is_placeholder(new):
            page.key_frames[job["index"]].description = new
            fixed += 1

    if fixed:
        repo.save_page(page)  # versions the old page, then reindexes FTS

    remaining = sum(1 for _ in targets(page))
    log(f"  {video_id}: {fixed} repaired, {remaining} still placeholder")
    return fixed, remaining


def main() -> int:
    args = [a for a in sys.argv[1:]]
    dry = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if "--all" in args:
        ids = affected_video_ids()
        args = [a for a in args if a != "--all"]
    else:
        ids = args

    if not ids:
        print(__doc__)
        return 2

    from mcptube.wiki.storage import FileWikiRepository
    repo = FileWikiRepository()

    if dry:
        log(f"DRY RUN - no API calls. {len(ids)} video(s).")
        total_frames = total_bytes = 0
        for vid in ids:
            page, jobs, missing = plan(vid, repo)
            if page is None:
                log(f"  {vid}: no wiki page")
                continue
            nbytes = sum(j["path"].stat().st_size for j in jobs)
            total_frames += len(jobs)
            total_bytes += nbytes
            note = f", {len(missing)} missing on disk" if missing else ""
            log(f"  {vid}: {len(jobs)} frame(s), "
                f"{nbytes/1_048_576:.1f} MiB{note}")
        log(f"TOTAL: {total_frames} frames, "
            f"{total_bytes/1_048_576:.1f} MiB of JPEG")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log("ANTHROPIC_API_KEY not in this process - vision cannot run.")
        return 1

    from mcptube.ingestion.vision import VisionDescriber
    from mcptube.llm import LLMClient

    describer = VisionDescriber(LLMClient())
    t0 = time.time()
    fixed = remaining = 0
    for vid in ids:
        f, r = repair(vid, repo, describer)
        fixed += f
        remaining += r
    log(f"done: {fixed} frame(s) repaired, {remaining} still placeholder, "
        f"{(time.time()-t0)/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
