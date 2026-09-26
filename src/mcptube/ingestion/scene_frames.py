"""Scene-change frame extraction from YouTube videos via ffmpeg."""

import logging
import subprocess
from pathlib import Path

import yt_dlp

from mcptube.config import settings

logger = logging.getLogger(__name__)


class SceneFrameError(Exception):
    """Raised when scene-change frame extraction fails."""


class SceneFrameExtractor:
    """Extracts key frames from YouTube videos using ffmpeg scene-change detection.

    Uses ffmpeg's scene filter to detect visual transitions and extract
    only frames where significant visual change occurs. This is ideal
    for lectures, slides, demos, and presentations where the screen
    content changes at meaningful moments.

    The scene threshold (0.0–1.0) controls sensitivity:
    - Lower = more frames (catches subtle changes)
    - Higher = fewer frames (only major transitions)
    - Default 0.4 is a good balance for most content

    Extraction runs in two passes (see issue #11). The first pass only detects
    candidate scene changes; the second writes JPEGs for an evenly spread subset
    of them. A single pass capped with -frames:v would stop the decoder at the
    first max_frames transitions, silently leaving the back of a video unseen.
    """

    _DEFAULT_THRESHOLD = 0.4
    _MAX_FRAMES = 50  # safety cap
    _SCALE_WIDTH = 1280
    # Prefer H.264 at <=720p: native 1280x720 matches _SCALE_WIDTH exactly and
    # decodes far faster than AV1/VP9 1080p60. Muxed "best" no longer exists on
    # most YouTube videos, so bestvideo must come first.
    _FORMAT_SELECTOR = (
        "bestvideo[vcodec^=avc1][height<=720]"
        "/bestvideo[height<=720][ext=mp4]"
        "/bestvideo[ext=mp4]/bestvideo/best[ext=mp4]/best"
    )

    def __init__(self, threshold: float | None = None) -> None:
        """Initialize scene frame extractor.

        Args:
            threshold: Scene-change sensitivity (0.0–1.0). Default 0.4.
        """
        import os

        env_threshold = os.environ.get("MCPTUBE_SCENE_THRESHOLD")
        self._threshold = (
            threshold
            or (float(env_threshold) if env_threshold else None)
            or self._DEFAULT_THRESHOLD
        )
        logger.info("Scene threshold: %s", self._threshold)

    def extract_scene_frames(
        self,
        video_id: str,
        max_frames: int | None = None,
    ) -> list[dict]:
        """Extract key frames at scene-change points from a YouTube video.

        Args:
            video_id: YouTube video ID.
            max_frames: Maximum frames to extract. Defaults to _MAX_FRAMES.

        Returns:
            List of dicts with keys: "path" (Path), "timestamp" (float), "index" (int)

        Raises:
            SceneFrameError: If extraction fails.
        """
        max_frames = max_frames or self._MAX_FRAMES

        # Ensure output directory exists
        output_dir = self._output_dir(video_id)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Check cache — if frames already extracted, return them
        cached = self._load_cached(output_dir)
        if cached:
            logger.info("Scene frames cache hit: %d frames for %s", len(cached), video_id)
            return cached[:max_frames]

        # Download to a local temp file first, then run ffmpeg against it.
        # Streaming a googlevideo URL straight into ffmpeg gets throttled hard
        # by YouTube (~2 MB/min), while yt-dlp's own downloader saturates the
        # link (~20 MB/s). Scene detection must read the whole video, so the
        # difference is hours vs. seconds per video.
        local_path = self._download_video(video_id)
        try:
            duration = self._probe_duration(str(local_path))
            candidates = self._detect_candidates(str(local_path))

            if not candidates:
                logger.warning(
                    "No scene changes detected for %s at threshold %s; "
                    "falling back to single-pass extraction",
                    video_id,
                    self._threshold,
                )
                frames = self._extract_with_ffmpeg(
                    str(local_path), output_dir, max_frames
                )
            else:
                if len(candidates) > max_frames:
                    first_n_end = candidates[max_frames - 1]
                    logger.warning(
                        "Scene-frame cap reached for %s: %d scene changes detected, "
                        "keeping %d spread evenly across the runtime. Taking the "
                        "first %d would have covered only %.1fs of %.1fs (%.0f%%).",
                        video_id,
                        len(candidates),
                        max_frames,
                        max_frames,
                        first_n_end,
                        duration,
                        (100 * first_n_end / duration) if duration else 0.0,
                    )
                selected = self._select_evenly(candidates, max_frames, duration)
                frames = self._extract_at_timestamps(
                    str(local_path), output_dir, selected
                )
        finally:
            try:
                local_path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not delete temp video: %s", local_path)

        logger.info("Extracted %d scene-change frames for %s", len(frames), video_id)
        return frames

    def _download_video(self, video_id: str) -> Path:
        """Download the video to a temp file via yt-dlp; return its path."""
        import tempfile

        tmp_dir = Path(tempfile.gettempdir()) / "mcptube_scenes"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(tmp_dir / f"{video_id}.%(ext)s")

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": self._FORMAT_SELECTOR,
            "outtmpl": outtmpl,
            "noplaylist": True,
            "overwrites": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}", download=True
                )
                path = Path(ydl.prepare_filename(info))
        except Exception as e:
            raise SceneFrameError(f"Failed to download video {video_id}: {e}") from e

        if not path.exists():
            matches = list(tmp_dir.glob(f"{video_id}.*"))
            if not matches:
                raise SceneFrameError(f"Downloaded file not found for {video_id}")
            path = matches[0]
        return path

    def _resolve_stream_url(self, video_id: str) -> str:
        """Resolve a direct stream URL from a YouTube video ID."""
        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": self._FORMAT_SELECTOR,
            "skip_download": True,
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info is None:
                    raise SceneFrameError(f"yt-dlp returned no info for: {video_id}")
                stream_url = info.get("url")
                if not stream_url:
                    raise SceneFrameError(f"No stream URL resolved for: {video_id}")
                return stream_url
        except yt_dlp.utils.DownloadError as e:
            raise SceneFrameError(f"Failed to resolve stream URL: {e}") from e

    @staticmethod
    def _probe_duration(video_path: str) -> float:
        """Video duration in seconds, or 0.0 if ffprobe cannot determine it."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            return float(result.stdout.strip())
        except (subprocess.SubprocessError, ValueError, FileNotFoundError):
            logger.warning("Could not probe duration for %s", video_path)
            return 0.0

    def _detect_candidates(self, video_path: str) -> list[float]:
        """Pass 1: list every scene-change timestamp, encoding nothing.

        "-f null -" discards the output, so this costs one decode of the video
        and no JPEG writes. scale and format are deliberately absent: in the
        normal chain select runs before them, so they never influenced which
        frames were detected and would only add work here.
        """
        cmd = [
            "ffmpeg",
            "-i", video_path,
            "-vf", f"select='gt(scene,{self._threshold})',showinfo",
            "-fps_mode", "vfr",
            "-an",
            "-f", "null",
            "-",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        except subprocess.TimeoutExpired:
            raise SceneFrameError("ffmpeg timed out during scene detection")
        except FileNotFoundError:
            raise SceneFrameError(
                "ffmpeg not found. Install it: https://ffmpeg.org/download.html"
            )

        timestamps = self._parse_showinfo_timestamps(result.stderr)

        # Deduplicate, preserving order.
        seen: set[float] = set()
        unique: list[float] = []
        for ts in timestamps:
            if ts not in seen:
                seen.add(ts)
                unique.append(ts)
        return unique

    @staticmethod
    def _select_evenly(
        candidates: list[float], max_frames: int, duration: float
    ) -> list[float]:
        """Pick at most max_frames timestamps spread across the whole runtime.

        Scene changes cluster: intros, animated transitions and busy passages can
        burn the entire budget in the first minute, so keeping the first N is
        systematically biased towards the start of the video (issue #11).

        Instead the runtime is divided into max_frames equal buckets and the
        earliest candidate in each is taken, so every stretch of the video gets
        representation. Buckets containing no scene change leave budget unspent;
        the remainder is backfilled with whichever leftover candidates sit
        furthest from an already-chosen timestamp, so the extra frames add new
        coverage instead of piling up next to an existing one.
        """
        if len(candidates) <= max_frames:
            return list(candidates)

        if duration <= 0:
            # No usable duration — fall back to even spacing by position.
            step = len(candidates) / max_frames
            return [candidates[int(i * step)] for i in range(max_frames)]

        bucket_width = duration / max_frames
        earliest_per_bucket: dict[int, float] = {}
        for ts in candidates:
            bucket = min(int(ts / bucket_width), max_frames - 1)
            if bucket not in earliest_per_bucket:
                earliest_per_bucket[bucket] = ts

        selected = sorted(earliest_per_bucket.values())
        if len(selected) >= max_frames:
            return selected[:max_frames]

        taken = set(selected)
        leftovers = [ts for ts in candidates if ts not in taken]
        while leftovers and len(selected) < max_frames:
            furthest = max(
                leftovers,
                key=lambda ts: min(abs(ts - s) for s in selected),
            )
            leftovers.remove(furthest)
            selected.append(furthest)
            selected.sort()
        return selected

    def _extract_at_timestamps(
        self, video_path: str, output_dir: Path, timestamps: list[float]
    ) -> list[dict]:
        """Pass 2: write one JPEG per selected timestamp.

        Seeking per frame beats re-running the scene filter over the whole file:
        "-ss" before "-i" jumps to the nearest keyframe and decodes forward, so
        this touches a few seconds of video per frame instead of paying for a
        second full decode.
        """
        # Drop frames from any earlier run so a shorter result cannot leave
        # stale JPEGs behind to be picked up as part of this extraction.
        for stale in output_dir.glob("scene_*.jpg"):
            stale.unlink(missing_ok=True)

        frames: list[dict] = []
        for i, ts in enumerate(timestamps, start=1):
            path = output_dir / f"scene_{i:04d}.jpg"
            cmd = [
                "ffmpeg",
                "-ss", f"{ts:.3f}",
                "-i", video_path,
                # format=yuvj420p is required: ffmpeg 9's mjpeg encoder refuses
                # limited-range YUV ("Non full-range YUV is non-standard"), fails
                # to open, and silently produces zero frames for every video.
                "-vf", f"scale={self._SCALE_WIDTH}:-1,format=yuvj420p",
                "-frames:v", "1",
                "-q:v", "2",
                "-y",
                str(path),
            ]
            try:
                subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            except subprocess.TimeoutExpired:
                logger.warning("ffmpeg timed out extracting frame at %.2fs", ts)
                continue
            except FileNotFoundError:
                raise SceneFrameError(
                    "ffmpeg not found. Install it: https://ffmpeg.org/download.html"
                )

            if path.exists():
                frames.append({
                    "path": path,
                    "timestamp": ts,
                    "index": len(frames),
                })
            else:
                logger.warning("ffmpeg produced no frame at %.2fs", ts)

        if not frames:
            raise SceneFrameError(
                f"ffmpeg produced no frames for any of {len(timestamps)} timestamps"
            )

        self._save_metadata(output_dir, frames)
        return frames

    def _extract_with_ffmpeg(
        self, stream_url: str, output_dir: Path, max_frames: int
    ) -> list[dict]:
        """Single-pass extraction; fallback for when no scene changes are found.

        Note this caps with -frames:v, so it stops at the first max_frames
        matches. It is only reached when pass 1 detected nothing, in which case
        there is no candidate list to spread out.
        """
        output_pattern = str(output_dir / "scene_%04d.jpg")

        cmd = [
            "ffmpeg",
            "-i", stream_url,
            # format=yuvj420p is required: ffmpeg 9's mjpeg encoder refuses
            # limited-range YUV ("Non full-range YUV is non-standard"), fails to
            # open, and silently produces zero frames for every video.
            "-vf", (f"select='gt(scene,{self._threshold})',"
                    f"scale={self._SCALE_WIDTH}:-1,format=yuvj420p,showinfo"),
            "-fps_mode", "vfr",
            "-frames:v", str(max_frames),
            "-q:v", "2",
            "-y",
            output_pattern,
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=1800,
            )

            if result.returncode != 0:
                # ffmpeg may return non-zero but still produce frames
                if not any(output_dir.glob("scene_*.jpg")):
                    raise SceneFrameError(
                        f"ffmpeg failed (code {result.returncode}): {result.stderr[:300]}"
                    )

        except subprocess.TimeoutExpired:
            raise SceneFrameError("ffmpeg timed out during scene detection")
        except FileNotFoundError:
            raise SceneFrameError(
                "ffmpeg not found. Install it: https://ffmpeg.org/download.html"
            )

        # Parse timestamps from ffmpeg showinfo output
        timestamps = self._parse_showinfo_timestamps(result.stderr)

        # Build frame list
        frames = []
        for i, path in enumerate(sorted(output_dir.glob("scene_*.jpg"))):
            timestamp = timestamps[i] if i < len(timestamps) else 0.0
            frames.append({
                "path": path,
                "timestamp": timestamp,
                "index": i,
            })

        # Save timestamp metadata for cache
        self._save_metadata(output_dir, frames)

        return frames

    @staticmethod
    def _parse_showinfo_timestamps(stderr: str) -> list[float]:
        """Parse frame timestamps from ffmpeg showinfo filter output.

        showinfo outputs lines like:
            [Parsed_showinfo_2 ...] n: 0 pts: 12345 pts_time:1.234 ...
        """
        import re

        timestamps = []
        pattern = re.compile(r"pts_time:\s*([\d.]+)")
        for line in stderr.split("\n"):
            if "showinfo" in line:
                match = pattern.search(line)
                if match:
                    timestamps.append(float(match.group(1)))
        return timestamps

    def _load_cached(self, output_dir: Path) -> list[dict] | None:
        """Load cached frames if metadata file exists."""
        import json

        meta_path = output_dir / "metadata.json"
        if not meta_path.exists():
            return None

        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            frames = []
            for entry in data:
                path = output_dir / entry["filename"]
                if path.exists():
                    frames.append({
                        "path": path,
                        "timestamp": entry["timestamp"],
                        "index": entry["index"],
                    })
            return frames if frames else None
        except Exception:
            return None

    @staticmethod
    def _save_metadata(output_dir: Path, frames: list[dict]) -> None:
        """Save frame metadata for caching."""
        import json

        meta = [
            {
                "filename": f["path"].name,
                "timestamp": f["timestamp"],
                "index": f["index"],
            }
            for f in frames
        ]
        meta_path = output_dir / "metadata.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    @staticmethod
    def _output_dir(video_id: str) -> Path:
        """Get the output directory for scene frames."""
        return settings.frames_dir / f"{video_id}_scenes"
