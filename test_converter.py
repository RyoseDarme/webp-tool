from pathlib import Path
import subprocess
import threading

from PIL import Image
import pytest

from converter import Cancelled, ConversionError, Converter, VideoInfo, find_tool


class SimulatedConverter(Converter):
    """Real decodable WebP frames, with controlled sizes for deterministic budget tests."""
    def __init__(self, require_lower_fps=False):
        super().__init__()
        self.require_lower_fps = require_lower_fps
        self.attempts = []

    def probe(self, source):
        return VideoInfo(64, 48, 2, 10)

    def encode(self, source, target, info, quality, fps):
        self.check_cancel()
        self.attempts.append((quality, fps))
        frames = [Image.new("RGB", (64, 48), color) for color in ("red", "blue")]
        frames[0].save(target, format="WEBP", save_all=True, append_images=frames[1:], duration=1000, loop=0)
        size = 6000 + (quality - 80) * 300
        if self.require_lower_fps and fps is None:
            size += 14000
        with target.open("ab") as file:
            file.write(b"\0" * max(0, size - target.stat().st_size))


def test_budget_search_keeps_best_and_never_overwrites(tmp_path):
    source = tmp_path / "中文 空格.mov"
    source.touch()
    existing = tmp_path / "中文 空格_动图.webp"
    existing.write_bytes(b"do not replace")
    converter = SimulatedConverter()
    result = converter.convert(source, max_mb=0.01)
    assert existing.read_bytes() == b"do not replace"
    assert Path(result.output).name == "中文 空格_动图_2.webp"
    assert result.size < 10000 and result.quality == 93
    assert (result.width, result.height, result.duration_ms) == (64, 48, 2000)
    assert not list(tmp_path.glob(".webp-work-*"))


def test_adaptive_frame_rate_is_reported(tmp_path):
    source = tmp_path / "motion.mp4"
    source.touch()
    converter = SimulatedConverter(require_lower_fps=True)
    result = converter.convert(source, max_mb=0.01)
    assert result.reduced_fps and result.fps < 10
    assert result.size < 10000 and result.width == 64


def test_keep_frame_rate_failure_does_not_publish_oversized_file(tmp_path):
    source = tmp_path / "motion.mp4"
    source.touch()
    with pytest.raises(ConversionError, match="锁定原帧率"):
        SimulatedConverter(require_lower_fps=True).convert(source, max_mb=0.01, keep_fps=True)
    assert not list(tmp_path.glob("*.webp"))
    assert not list(tmp_path.glob(".webp-work-*"))


def test_cancel_terminates_an_active_child():
    import sys
    converter = Converter()
    timer = threading.Timer(0.4, converter.cancel)
    timer.start()
    try:
        with pytest.raises(Cancelled):
            converter.run_process([sys.executable, "-c", "import time; time.sleep(30)"], "test")
    finally:
        timer.cancel()
    assert converter.process is None


def test_real_ffmpeg_conversion_and_invalid_input(tmp_path):
    source = tmp_path / "真实测试 中文.mov"
    subprocess.run([
        find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc=size=96x64:rate=10:duration=0.6",
        "-c:v", "qtrle", str(source),
    ], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    result = Converter().convert(source, keep_fps=True)
    assert (result.width, result.height, result.frames) == (96, 64, 6)
    assert abs(result.duration_ms - 600) <= 1
    assert result.size < 10_000_000 and not result.reduced_fps
    with Image.open(result.output) as image:
        assert image.info["loop"] == 0
    bad = tmp_path / "损坏.mp4"
    bad.write_bytes(b"invalid data")
    with pytest.raises(ConversionError):
        Converter().convert(bad)


def test_rotated_video_retains_display_dimensions(tmp_path):
    base = tmp_path / "base.mp4"
    rotated = tmp_path / "旋转.mp4"
    flags = subprocess.CREATE_NO_WINDOW
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=96x64:rate=5:duration=0.6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(base)],
                   check=True, creationflags=flags)
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-display_rotation", "90", "-i", str(base),
                    "-c", "copy", str(rotated)], check=True, creationflags=flags)
    result = Converter().convert(rotated)
    assert (result.width, result.height) == (64, 96)


def test_still_video_is_a_valid_static_webp(tmp_path):
    source = tmp_path / "still.mov"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "color=c=blue:size=96x64:rate=10:duration=1", "-c:v", "qtrle", str(source)],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    result = Converter().convert(source)
    assert result.frames == 1 and result.duration_ms == 0
    assert result.width == 96 and result.height == 64


def test_real_encoding_reports_frames_before_encoder_finishes(tmp_path):
    source = tmp_path / "progress.mov"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=320x240:rate=20:duration=2", "-c:v", "qtrle", str(source)],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    events = []
    converter = Converter()

    def record(message, percent):
        alive = converter.process is not None and converter.process.poll() is None
        events.append((message, percent, alive))

    converter.progress = record
    result = converter.convert(source)
    live_progress = [(text, value) for text, value, alive in events
                     if alive and "已处理" in text and value is not None and 0 < value < 99]
    assert len(live_progress) >= 5
    values = [value for _, value in live_progress]
    assert values == sorted(values)
    assert any("第 1 轮" in text for text, _ in live_progress)
    assert any("检查动图" in text for text, _, _ in events)
    assert events[-1][1] == 100
    assert result.frames == 40
