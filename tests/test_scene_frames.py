"""Tests for scene-change frame extraction via ffmpeg."""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from mcptube.ingestion.scene_frames import SceneFrameError, SceneFrameExtractor


@pytest.fixture
def extractor():
    return SceneFrameExtractor(threshold=0.4)


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "frames" / "abc123_scenes"
    d.mkdir(parents=True)
    return d


@pytest.fixture
def cached_frames(output_dir):
    """Create fake cached scene frames with metadata."""
    frames = []
    for i in range(3):
        path = output_dir / f"scene_{i+1:04d}.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)  # fake JPEG header
        frames.append({
            "filename": path.name,
            "timestamp": float(i * 10),
            "index": i,
        })
    meta_path = output_dir / "metadata.json"
    meta_path.write_text(json.dumps(frames, indent=2))
    return frames


class TestInit:
    def test_default_threshold(self):
        ext = SceneFrameExtractor()
        assert ext._threshold == 0.4

    def test_custom_threshold(self):
        ext = SceneFrameExtractor(threshold=0.6)
        assert ext._threshold == 0.6


class TestParseShowinfoTimestamps:
    def test_parse_single_timestamp(self):
        stderr = "[Parsed_showinfo_2 @ 0x1234] n:   0 pts:  12345 pts_time:1.234 pos:5678\n"
        ts = SceneFrameExtractor._parse_showinfo_timestamps(stderr)
        assert len(ts) == 1
        assert abs(ts[0] - 1.234) < 0.001

    def test_parse_multiple_timestamps(self):
        stderr = (
            "[Parsed_showinfo_2 @ 0x1] n:0 pts:0 pts_time:0.000 pos:0\n"
            "[Parsed_showinfo_2 @ 0x1] n:1 pts:1000 pts_time:10.500 pos:100\n"
            "[Parsed_showinfo_2 @ 0x1] n:2 pts:2000 pts_time:25.750 pos:200\n"
        )
        ts = SceneFrameExtractor._parse_showinfo_timestamps(stderr)
        assert len(ts) == 3
        assert abs(ts[0] - 0.0) < 0.001
        assert abs(ts[1] - 10.5) < 0.001
        assert abs(ts[2] - 25.75) < 0.001

    def test_parse_no_showinfo_lines(self):
        stderr = "frame=100 fps=25 time=00:00:04.00 bitrate=1000kbps\n"
        ts = SceneFrameExtractor._parse_showinfo_timestamps(stderr)
        assert ts == []

    def test_parse_empty_stderr(self):
        ts = SceneFrameExtractor._parse_showinfo_timestamps("")
        assert ts == []

    def test_parse_mixed_output(self):
        stderr = (
            "Input #0, mov,mp4 from 'stream.mp4'\n"
            "[Parsed_showinfo_2 @ 0x1] n:0 pts:0 pts_time:5.000 pos:0\n"
            "frame=50 fps=25\n"
            "[Parsed_showinfo_2 @ 0x1] n:1 pts:1000 pts_time:15.000 pos:100\n"
            "Output #0, image2\n"
        )
        ts = SceneFrameExtractor._parse_showinfo_timestamps(stderr)
        assert len(ts) == 2
        assert abs(ts[0] - 5.0) < 0.001
        assert abs(ts[1] - 15.0) < 0.001


class TestSelectEvenly:
    """Even distribution of the frame budget across the runtime (issue #11)."""

    def test_under_budget_returns_everything(self):
        cands = [1.0, 2.0, 3.0]
        assert SceneFrameExtractor._select_evenly(cands, 50, 100.0) == cands

    def test_never_exceeds_budget(self):
        cands = [float(i) for i in range(500)]
        sel = SceneFrameExtractor._select_evenly(cands, 50, 500.0)
        assert len(sel) == 50

    def test_only_returns_real_candidates(self):
        cands = [i * 3.7 for i in range(400)]
        sel = SceneFrameExtractor._select_evenly(cands, 50, 1480.0)
        assert set(sel).issubset(set(cands))

    def test_result_is_sorted_and_unique(self):
        cands = [i * 0.5 for i in range(600)]
        sel = SceneFrameExtractor._select_evenly(cands, 50, 300.0)
        assert sel == sorted(sel)
        assert len(set(sel)) == len(sel)

    def test_spreads_past_a_front_loaded_cluster(self):
        """The failure in issue #11: an intro that eats the whole budget."""
        # 200 scene changes in the first 20s, then 30 spread over 1000s.
        cands = [i * 0.1 for i in range(200)] + [20.0 + i * 32.0 for i in range(30)]
        sel = SceneFrameExtractor._select_evenly(cands, 50, 1000.0)
        # first-N would have stopped at 4.9s; this must reach the end.
        assert sel[-1] > 900.0
        # and must not spend the whole budget on the intro.
        assert sum(1 for t in sel if t < 20.0) < 50

    def test_unknown_duration_falls_back_to_positional_spacing(self):
        cands = [float(i) for i in range(300)]
        sel = SceneFrameExtractor._select_evenly(cands, 50, 0.0)
        assert len(sel) == 50
        assert sel == sorted(sel)

    def test_backfills_when_buckets_are_empty(self):
        """All candidates in one bucket still spends the full budget."""
        cands = [i * 0.001 for i in range(500)]
        sel = SceneFrameExtractor._select_evenly(cands, 50, 1000.0)
        assert len(sel) == 50


class TestCaching:
    def test_load_cached_frames(self, extractor, output_dir, cached_frames):
        loaded = extractor._load_cached(output_dir)
        assert loaded is not None
        assert len(loaded) == 3
        assert loaded[0]["timestamp"] == 0.0
        assert loaded[1]["timestamp"] == 10.0
        assert loaded[2]["timestamp"] == 20.0

    def test_load_cached_returns_none_when_no_metadata(self, extractor, output_dir):
        loaded = extractor._load_cached(output_dir)
        assert loaded is None

    def test_load_cached_returns_none_when_files_missing(self, extractor, output_dir):
        meta = [{"filename": "missing.jpg", "timestamp": 0.0, "index": 0}]
        (output_dir / "metadata.json").write_text(json.dumps(meta))
        loaded = extractor._load_cached(output_dir)
        assert loaded is None

    def test_save_metadata(self, extractor, output_dir):
        frames = [
            {"path": output_dir / "scene_0001.jpg", "timestamp": 5.0, "index": 0},
            {"path": output_dir / "scene_0002.jpg", "timestamp": 15.0, "index": 1},
        ]
        extractor._save_metadata(output_dir, frames)
        meta_path = output_dir / "metadata.json"
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert len(data) == 2
        assert data[0]["filename"] == "scene_0001.jpg"
        assert data[1]["timestamp"] == 15.0


class TestOutputDir:
    @patch("mcptube.ingestion.scene_frames.settings")
    def test_output_dir_path(self, mock_settings, tmp_path):
        mock_settings.frames_dir = tmp_path / "frames"
        path = SceneFrameExtractor._output_dir("abc123")
        assert path == tmp_path / "frames" / "abc123_scenes"


class TestResolveStreamUrl:
    @patch("mcptube.ingestion.scene_frames.yt_dlp.YoutubeDL")
    def test_successful_resolve(self, mock_ydl_class, extractor):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"url": "https://stream.example.com/video.mp4"}
        mock_ydl_class.return_value = mock_ydl

        url = extractor._resolve_stream_url("abc123")
        assert url == "https://stream.example.com/video.mp4"

    @patch("mcptube.ingestion.scene_frames.yt_dlp.YoutubeDL")
    def test_resolve_no_info(self, mock_ydl_class, extractor):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = None
        mock_ydl_class.return_value = mock_ydl

        with pytest.raises(SceneFrameError, match="no info"):
            extractor._resolve_stream_url("abc123")

    @patch("mcptube.ingestion.scene_frames.yt_dlp.YoutubeDL")
    def test_resolve_no_stream_url(self, mock_ydl_class, extractor):
        mock_ydl = MagicMock()
        mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl.__exit__ = MagicMock(return_value=False)
        mock_ydl.extract_info.return_value = {"url": None}
        mock_ydl_class.return_value = mock_ydl

        with pytest.raises(SceneFrameError, match="No stream URL"):
            extractor._resolve_stream_url("abc123")


class TestExtractWithFfmpeg:
    @patch("mcptube.ingestion.scene_frames.subprocess.run")
    def test_successful_extraction(self, mock_run, extractor, output_dir):
        # Create fake frame files as if ffmpeg wrote them
        for i in range(2):
            (output_dir / f"scene_{i+1:04d}.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 50)

        mock_run.return_value = MagicMock(
            returncode=0,
            stderr=(
                "[Parsed_showinfo_2 @ 0x1] n:0 pts:0 pts_time:5.000 pos:0\n"
                "[Parsed_showinfo_2 @ 0x1] n:1 pts:1000 pts_time:20.000 pos:100\n"
            ),
        )

        frames = extractor._extract_with_ffmpeg("https://stream.example.com/v.mp4", output_dir, 50)
        assert len(frames) == 2
        assert frames[0]["timestamp"] == 5.0
        assert frames[1]["timestamp"] == 20.0

    @patch("mcptube.ingestion.scene_frames.subprocess.run")
    def test_ffmpeg_failure_no_frames(self, mock_run, extractor, output_dir):
        mock_run.return_value = MagicMock(returncode=1, stderr="Error: invalid input")
        with pytest.raises(SceneFrameError, match="ffmpeg failed"):
            extractor._extract_with_ffmpeg("https://stream.example.com/v.mp4", output_dir, 50)

    @patch("mcptube.ingestion.scene_frames.subprocess.run")
    def test_ffmpeg_timeout(self, mock_run, extractor, output_dir):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120)
        with pytest.raises(SceneFrameError, match="timed out"):
            extractor._extract_with_ffmpeg("https://stream.example.com/v.mp4", output_dir, 50)

    @patch("mcptube.ingestion.scene_frames.subprocess.run")
    def test_ffmpeg_not_found(self, mock_run, extractor, output_dir):
        mock_run.side_effect = FileNotFoundError()
        with pytest.raises(SceneFrameError, match="ffmpeg not found"):
            extractor._extract_with_ffmpeg("https://stream.example.com/v.mp4", output_dir, 50)

    @patch("mcptube.ingestion.scene_frames.subprocess.run")
    def test_ffmpeg_nonzero_but_frames_produced(self, mock_run, extractor, output_dir):
        """ffmpeg sometimes returns non-zero but still writes frames."""
        (output_dir / "scene_0001.jpg").write_bytes(b"\xff\xd8" + b"\x00" * 50)
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="[Parsed_showinfo_2 @ 0x1] n:0 pts:0 pts_time:3.000 pos:0\n",
        )
        frames = extractor._extract_with_ffmpeg("https://stream.example.com/v.mp4", output_dir, 50)
        assert len(frames) == 1


class TestExtractSceneFrames:
    @patch.object(SceneFrameExtractor, "_output_dir")
    def test_returns_cached(self, mock_dir, extractor, output_dir, cached_frames):
        mock_dir.return_value = output_dir
        frames = extractor.extract_scene_frames("abc123")
        assert len(frames) == 3

    @patch.object(SceneFrameExtractor, "_output_dir")
    def test_max_frames_limits_cached(self, mock_dir, extractor, output_dir, cached_frames):
        mock_dir.return_value = output_dir
        frames = extractor.extract_scene_frames("abc123", max_frames=2)
        assert len(frames) == 2
