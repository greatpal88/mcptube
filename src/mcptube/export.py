"""Export processed videos as self-contained source-material markdown.

Patch 8 — additive. This module reads the existing stores only (the SQLite
video repository plus the persisted per-frame vision descriptions) and never
writes to them, so any video can be re-exported without re-ingesting.

What it exports is *source material*, not mcptube's compiled wiki pages: the
full transcript organised under chapters, and the raw per-frame vision
descriptions. Downstream knowledge bases compile their own pages from this.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from mcptube.config import settings
from mcptube.models import Chapter, Video

logger = logging.getLogger(__name__)

SLUG_MAX_LEN = 60


def default_export_dir() -> Path:
    """Where exports land. Override with MCPTUBE_EXPORT_DIR."""
    env = os.environ.get("MCPTUBE_EXPORT_DIR")
    if env:
        return Path(env)
    return settings.data_dir / "exports"


def slugify(text: str, max_len: int = SLUG_MAX_LEN) -> str:
    """Lowercase ASCII slug: alphanumerics and single hyphens."""
    norm = unicodedata.normalize("NFKD", text)
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug or "untitled"


def export_filename(video: Video) -> str:
    """youtube-<videoID>-<slugified-title>.md"""
    return f"youtube-{video.video_id}-{slugify(video.title)}.md"


def hms(seconds: float) -> str:
    """Seconds to HH:MM:SS."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _yaml_str(value: object) -> str:
    """Safe YAML scalar. JSON double-quoted strings are valid YAML."""
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def chapter_at(timestamp: float, chapters: list[Chapter]) -> Chapter | None:
    """The chapter containing a timestamp, or None."""
    found = None
    for ch in sorted(chapters, key=lambda c: c.start):
        if ch.start <= timestamp:
            found = ch
        else:
            break
    return found


def load_frame_descriptions(video_id: str) -> list[dict]:
    """Per-frame vision descriptions, newest run, in timestamp order.

    Primary source is the persisted video wiki page, which is where mcptube
    stores the raw per-frame vision output. This reads the raw frame records
    only — never the compiled wiki prose. Falls back to the scene-frame
    metadata sidecar (timestamps but no descriptions) when vision has not run.
    """
    frames: list[dict] = []
    try:
        from mcptube.wiki.storage import FileWikiRepository

        page = FileWikiRepository().get_video_page(video_id)
        if page is not None:
            for f in getattr(page, "key_frames", []) or []:
                frames.append({
                    "filename": getattr(f, "filename", "") or "",
                    "timestamp": float(getattr(f, "timestamp", 0.0) or 0.0),
                    "description": getattr(f, "description", "") or "",
                })
    except Exception as e:  # never let export fail on an optional source
        logger.warning("Could not read frame descriptions for %s: %s", video_id, e)

    if not frames:
        meta = scene_dir(video_id) / "metadata.json"
        if meta.exists():
            try:
                for f in json.loads(meta.read_text(encoding="utf-8")):
                    frames.append({
                        "filename": f.get("filename", ""),
                        "timestamp": float(f.get("timestamp", 0.0)),
                        "description": "",
                    })
            except Exception as e:
                logger.warning("Could not read %s: %s", meta, e)

    return sorted(frames, key=lambda f: f["timestamp"])


def scene_dir(video_id: str) -> Path:
    """Directory holding extracted scene frames for a video."""
    return Path(settings.frames_dir) / f"{video_id}_scenes"


def frame_path(video_id: str, filename: str) -> Path | None:
    """On-disk path for a frame image, or None if it is not retained."""
    if not filename:
        return None
    p = scene_dir(video_id) / filename
    return p if p.exists() else None


def assets_dir(export_dir: Path | str, video_id: str) -> Path:
    """Where a video's frame images live inside the vault.

    Sibling of the sources directory: <vault>/raw/assets/<videoID>/ for an
    export dir of <vault>/raw/sources.
    """
    return Path(export_dir).parent / "assets" / video_id


def copy_frame_assets(video_id: str, frames: list[dict],
                      export_dir: Path | str) -> dict[str, str]:
    """Copy frame JPEGs into the vault; map filename -> relative markdown path.

    Keeping the images beside the markdown makes the export portable: the
    document no longer points into mcptube's private cache, so the vault can be
    moved, synced or shared without breaking every image reference.
    """
    import shutil

    mapping: dict[str, str] = {}
    if not frames:
        return mapping
    target = assets_dir(export_dir, video_id)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Could not create assets dir %s: %s", target, e)
        return mapping

    for f in frames:
        name = f.get("filename") or ""
        src = frame_path(video_id, name)
        if not src:
            continue
        try:
            shutil.copy2(src, target / name)
            mapping[name] = f"../assets/{video_id}/{name}"
        except OSError as e:
            logger.warning("Could not copy frame %s: %s", name, e)
    return mapping


def _published_cache_path() -> Path:
    return Path(settings.data_dir) / "published_cache.json"


def _load_published_cache() -> dict:
    p = _published_cache_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def fetch_published(video_id: str, use_cache: bool = True) -> str | None:
    """Publication date as YYYY-MM-DD, or None.

    mcptube does not persist this — there is no column on the videos table and
    no field on the Video model — so it comes from a yt-dlp metadata lookup
    (no download). Results are cached in the data dir so re-exports do not
    re-query and work offline.
    """
    cache = _load_published_cache() if use_cache else {}
    if video_id in cache:
        return cache[video_id]

    try:
        import yt_dlp

        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                               "skip_download": True}) as ydl:
            info = ydl.extract_info(
                f"https://www.youtube.com/watch?v={video_id}", download=False)
        raw = (info.get("release_date") or info.get("upload_date") or "")
    except Exception as e:
        logger.warning("Could not fetch publication date for %s: %s", video_id, e)
        return None

    if not (len(raw) == 8 and raw.isdigit()):
        return None

    value = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    cache[video_id] = value
    try:
        _published_cache_path().write_text(
            json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as e:
        logger.warning("Could not write published cache: %s", e)
    return value


def build_frontmatter(
    video: Video,
    *,
    published: str | None = None,
    exported_at: datetime | None = None,
) -> list[str]:
    """YAML frontmatter block, as lines."""
    exported_at = exported_at or datetime.now(timezone.utc)
    processed = video.added_at.isoformat() if video.added_at else None

    lines = ["---"]
    lines.append(f"title: {_yaml_str(video.title)}")
    lines.append(f"channel: {_yaml_str(video.channel)}")
    lines.append(f"video_url: {_yaml_str(video.url)}")
    lines.append(f"video_id: {_yaml_str(video.video_id)}")
    lines.append(f"duration: {_yaml_str(hms(video.duration))}")
    lines.append(f"duration_seconds: {video.duration}")
    lines.append(
        f"published: {_yaml_str(published)}" if published else "published: null"
    )
    lines.append(
        f"date_processed: {_yaml_str(processed)}" if processed else "date_processed: null"
    )
    lines.append(f"date_exported: {_yaml_str(exported_at.isoformat())}")
    lines.append('source_type: "youtube"')
    if video.tags:
        lines.append("tags:")
        lines.extend(f"  - {_yaml_str(t)}" for t in video.tags)
    else:
        lines.append("tags: []")
    lines.append("---")
    return lines


def _transcript_lines(video: Video) -> list[str]:
    """Full transcript, grouped under chapter headings where they exist."""
    lines = ["## Transcript", ""]
    segments = sorted(video.transcript, key=lambda s: s.start)

    if not segments:
        lines.append("_No transcript available for this video._")
        lines.append("")
        return lines

    chapters = sorted(video.chapters, key=lambda c: c.start)
    if not chapters:
        for seg in segments:
            lines.append(f"[{hms(seg.start)}] {seg.text}")
        lines.append("")
        return lines

    bounds = [c.start for c in chapters[1:]] + [float("inf")]
    idx = 0
    for chapter, end in zip(chapters, bounds):
        lines.append(f"### [{hms(chapter.start)}] {chapter.title}")
        lines.append("")
        wrote = False
        while idx < len(segments) and segments[idx].start < end:
            lines.append(f"[{hms(segments[idx].start)}] {segments[idx].text}")
            idx += 1
            wrote = True
        if not wrote:
            lines.append("_No transcript segments in this chapter._")
        lines.append("")

    while idx < len(segments):  # anything before the first chapter marker
        lines.append(f"[{hms(segments[idx].start)}] {segments[idx].text}")
        idx += 1
    return lines


def _visual_lines(video: Video, frames: list[dict],
                  asset_map: dict[str, str] | None = None) -> list[str]:
    """Every extracted frame, in timestamp order, with its vision description."""
    lines = ["## Visual content", ""]
    if not frames:
        lines.append(
            "_No frames were extracted for this video, or vision analysis did not run._"
        )
        lines.append("")
        return lines

    described = sum(1 for f in frames if (f.get("description") or "").strip())
    lines.append(
        f"{len(frames)} key frames extracted by scene-change detection; "
        f"{described} with vision descriptions."
    )
    lines.append("")

    chapters = sorted(video.chapters, key=lambda c: c.start)
    for f in frames:
        ts = f["timestamp"]
        lines.append(f"### [{hms(ts)}] {f.get('filename') or 'frame'}")
        lines.append("")
        ch = chapter_at(ts, chapters)
        if ch:
            lines.append(f"- **Chapter:** {ch.title} (starts [{hms(ch.start)}])")
        name = f.get("filename", "")
        rel = (asset_map or {}).get(name)
        if rel:
            lines.append("")
            lines.append(f"![]({rel})")
        else:
            # No copy available - fall back to the on-disk cache path so the
            # frame is still traceable.
            path = frame_path(video.video_id, name)
            if path:
                lines.append(f"- **Frame image:** `{path}`")
        lines.append("")
        lines.append(f.get("description") or "_No vision description recorded._")
        lines.append("")
    return lines


def build_markdown(
    video: Video,
    *,
    published: str | None = None,
    exported_at: datetime | None = None,
    frames: list[dict] | None = None,
    fetch_published_date: bool = True,
    asset_map: dict[str, str] | None = None,
) -> str:
    """Assemble the full self-contained source-material document."""
    if frames is None:
        frames = load_frame_descriptions(video.video_id)
    if published is None and fetch_published_date:
        published = fetch_published(video.video_id)

    lines = build_frontmatter(video, published=published, exported_at=exported_at)
    lines.append("")
    lines.append(f"# {video.title}")
    lines.append("")
    lines.append(f"**Source:** {video.url}")
    if video.channel:
        lines.append(f"**Channel:** {video.channel}")
    lines.append(f"**Duration:** {hms(video.duration)}")
    lines.append("")
    lines.extend(_transcript_lines(video))
    lines.append("")
    lines.extend(_visual_lines(video, frames, asset_map))

    text = "\n".join(lines).rstrip() + "\n"
    return text


def export_video(
    video: Video,
    out_dir: Path | str | None = None,
    *,
    published: str | None = None,
    fetch_published_date: bool = True,
    copy_assets: bool = True,
) -> Path:
    """Write one video's source-material markdown. Overwrites in place."""
    target_dir = Path(out_dir) if out_dir else default_export_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    frames = load_frame_descriptions(video.video_id)
    asset_map = (copy_frame_assets(video.video_id, frames, target_dir)
                 if copy_assets else {})

    path = target_dir / export_filename(video)
    path.write_text(
        build_markdown(video, published=published, frames=frames,
                       fetch_published_date=fetch_published_date,
                       asset_map=asset_map),
        encoding="utf-8")
    if asset_map:
        logger.info("Copied %d frames to %s", len(asset_map),
                    assets_dir(target_dir, video.video_id))
    logger.info("Exported %s → %s", video.video_id, path)
    return path
