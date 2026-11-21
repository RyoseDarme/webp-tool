"""Size-limited animated WebP conversion. All work stays on the local machine."""
from __future__ import annotations

import argparse
from collections import deque
from dataclasses import asdict, dataclass
from fractions import Fraction
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Callable

from PIL import Image


class ConversionError(Exception):
    pass


class Cancelled(ConversionError):
    pass


@dataclass
class VideoInfo:
    width: int
    height: int
    duration: float
    fps: float


@dataclass
class Result:
    source: str
    output: str
    size: int
    width: int
    height: int
    frames: int
    duration_ms: int
    quality: int
    fps: float
    reduced_fps: bool
    encoding: str = ""


def find_tool(name: str) -> str:
    roots = [Path(getattr(sys, "_MEIPASS", Path(__file__).parent)), Path(sys.executable).parent]
    for root in roots:
        for candidate in (root / "bin" / f"{name}.exe", root / f"{name}.exe"):
            if candidate.is_file():
                return str(candidate)
    path = shutil.which(name)
    if not path:
        raise ConversionError(f"缺少转换组件 {name}，请重新解压或下载完整应用。")
    return path


def publish(candidate: Path, directory: Path, stem: str, label: str = "动图") -> Path:
    """Reserve a fresh name; never replace another file, including concurrent jobs."""
    for index in range(1, 10000):
        suffix = "" if index == 1 else f"_{index}"
        output = directory / f"{stem}_{label}{suffix}.webp"
        try:
            fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            continue
        os.close(fd)
        try:
            os.replace(candidate, output)
        except BaseException:
            output.unlink(missing_ok=True)
            raise
        return output
    raise ConversionError("同名文件过多，请选择其他输出文件夹。")


class Converter:
    def __init__(self, progress: Callable[[str, float | None], None] | None = None):
        self.progress = progress or (lambda message, percent: None)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.process: subprocess.Popen | None = None
        self.round_number = 0

    def cancel(self):
        self.stopped.set()
        with self.lock:
            if self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                except OSError:
                    pass

    def check_cancel(self):
        if self.stopped.is_set():
            raise Cancelled("已取消")

    def run_process(self, command: list[str], phase: str, duration: float = 0) -> str:
        self.check_cancel()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.progress(phase, 0)
        with tempfile.TemporaryFile() as errors:
            with self.lock:
                self.check_cancel()
                process = subprocess.Popen(
                    command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT if duration else errors,
                    creationflags=flags, text=True, encoding="utf-8", errors="replace",
                )
                self.process = process
            collected = []
            recent = deque(maxlen=24)
            last_percent = -1
            frames_received = False
            try:
                assert process.stdout is not None
                for line in process.stdout:
                    self.check_cancel()
                    if duration and "showinfo" in line and "pts_time:" in line:
                        # libwebp_anim buffers the entire animation, so its regular
                        # output timestamps stay at zero until encoding finishes.
                        # Read actual input-frame timestamps instead of animating
                        # a fictitious timer or relying on delayed output packets.
                        match = re.search(r"\bn:\s*(\d+).*?pts_time:([\d.eE+\-]+)", line)
                        if match:
                            frames_received = True
                            frame, timestamp = int(match[1]), float(match[2])
                            percent = min(99, max(last_percent, max(0, int(timestamp / duration * 100))))
                            if percent > last_percent:
                                last_percent = percent
                                self.progress(f"{phase} · 已处理 {frame} 帧", percent)
                    elif duration and not frames_received and line.startswith("out_time_us="):
                        try:
                            percent = min(99, max(0, int(line.split("=", 1)[1]) / duration / 10000))
                            if percent > last_percent:
                                last_percent = int(percent)
                                self.progress(phase, percent)
                        except ValueError:
                            pass
                    elif not duration:
                        collected.append(line)
                    elif "showinfo" not in line:
                        recent.append(line)
                code = process.wait()
                self.check_cancel()
                if code:
                    if duration:
                        detail = "".join(recent).strip()[-1800:]
                    else:
                        errors.seek(0)
                        detail = errors.read().decode("utf-8", "replace").strip()[-1800:]
                    raise ConversionError(detail or "视频读取或转换失败，请检查文件是否完整。")
                if duration:
                    self.progress(f"{phase} · 本轮编码完成", 100)
                return "".join(collected)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
                with self.lock:
                    self.process = None

    def probe(self, source: Path) -> VideoInfo:
        raw = self.run_process([
            find_tool("ffprobe"), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration,avg_frame_rate,r_frame_rate:stream_side_data=rotation:format=duration",
            "-of", "json", str(source),
        ], "正在读取视频信息")
        try:
            data = json.loads(raw)
            stream = data["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
            rotation = next((float(side["rotation"]) for side in stream.get("side_data_list", []) if "rotation" in side), 0)
            if round(rotation) % 180 != 0:
                width, height = height, width
            duration = 0.0
            for candidate in (stream.get("duration"), data.get("format", {}).get("duration")):
                try:
                    duration = float(candidate)
                except (ValueError, TypeError):
                    continue
                if math.isfinite(duration) and duration > 0:
                    break
            fps = 0.0
            for rate in (stream.get("avg_frame_rate"), stream.get("r_frame_rate")):
                try:
                    fps = float(Fraction(rate or "0"))
                except (ValueError, ZeroDivisionError):
                    continue
                if fps > 0:
                    break
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ConversionError("没有找到可读取的视频画面，或文件信息不完整。") from exc
        if not math.isfinite(duration) or duration <= 0:
            raise ConversionError("无法确定视频时长，请先将视频导出为 MP4 或 MOV。")
        if not 0 < width <= 16383 or not 0 < height <= 16383:
            raise ConversionError("WebP 的单边尺寸最大为 16383 像素，当前视频无法保留原尺寸转换。")
        return VideoInfo(width, height, duration, fps or 30)

    def encode(self, source: Path, target: Path, info: VideoInfo, quality: int, fps: float | None):
        self.check_cancel()
        rate_text = "原帧率" if fps is None else f"{fps:g} 帧/秒"
        phase = f"第 {self.round_number} 轮压缩 · 画质 {quality} · {rate_text}"
        command = [
            find_tool("ffmpeg"), "-hide_banner", "-loglevel", "info", "-nostdin", "-y",
            "-threads", "2", "-filter_threads", "1",
            "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
        ]
        filters = ([f"fps={fps:g}"] if fps is not None else []) + ["showinfo=checksum=0"]
        command += ["-vf", ",".join(filters)]
        command += [
            "-c:v", "libwebp_anim", "-lossless", "0", "-quality", str(quality),
            "-compression_level", "6", "-loop", "0", "-progress", "pipe:1",
            "-stats_period", "0.5", "-nostats", str(target),
        ]
        self.run_process(command, phase, info.duration)
        if not target.is_file() or target.stat().st_size == 0:
            raise ConversionError("未生成有效动图。")

    def convert(self, source: Path | str, max_mb: float = 10, output_dir: Path | str | None = None,
                keep_fps: bool = False) -> Result:
        source = Path(source).resolve()
        self.round_number = 0
        if not source.is_file():
            raise ConversionError("视频文件不存在或已被移动。")
        if not math.isfinite(max_mb) or max_mb <= 0:
            raise ConversionError("文件大小上限必须大于 0。")
        budget = int(max_mb * 1_000_000)
        directory = Path(output_dir).resolve() if output_dir else source.parent
        directory.mkdir(parents=True, exist_ok=True)
        info = self.probe(source)
        self.progress(f"原尺寸 {info.width} × {info.height} · {info.duration:g} 秒 · {info.fps:g} 帧/秒", None)
        with tempfile.TemporaryDirectory(prefix=".webp-work-", dir=directory) as temp:
            work = Path(temp)
            trial, best = work / "trial.webp", work / "best.webp"
            best_quality, chosen_fps = 100, None

            def attempt(quality: int, fps: float | None) -> tuple[bool, int]:
                self.round_number += 1
                self.encode(source, trial, info, quality, fps)
                size = trial.stat().st_size
                fits = size < budget
                self.progress(f"本轮 {size / 1_000_000:.2f} MB / 上限 {max_mb:g} MB" + (" · 已满足体积要求" if fits else " · 正在优化体积"), None)
                return fits, size

            fits, size = attempt(100, None)
            if fits:
                os.replace(trial, best)
            else:
                fits, size = attempt(80, None)
                if not fits and not keep_fps:
                    current_rate = info.fps
                    while not fits and current_rate > 1:
                        # Estimate a useful next rate rather than retry every single FPS.
                        current_rate = max(1, math.floor(min(current_rate * 0.75, current_rate * budget / size * 0.88)))
                        chosen_fps = float(current_rate)
                        fits, size = attempt(80, chosen_fps)
                if not fits:
                    extra = "，或取消“锁定原帧率”" if keep_fps else ""
                    raise ConversionError(f"在保留原尺寸和较高清晰度的条件下，仍无法压到 {max_mb:g} MB。请提高体积上限或缩短视频{extra}。")
                os.replace(trial, best)
                best_quality = 80
                low, high = 80, 100
                # Bounded search keeps the best fitting result even if size isn't monotonic.
                for _ in range(4):
                    if high - low <= 1:
                        break
                    quality = (low + high) // 2
                    fits, _ = attempt(quality, chosen_fps)
                    if fits:
                        low = best_quality = quality
                        os.replace(trial, best)
                    else:
                        high = quality

            self.check_cancel()
            self.progress("正在检查动图完整性", 0)
            total_ms = 0
            try:
                with Image.open(best) as image:
                    if image.size != (info.width, info.height):
                        raise ConversionError("输出尺寸与视频不一致，已停止保存。")
                    frames = image.n_frames
                    for index in range(frames):
                        self.check_cancel()
                        image.seek(index)
                        image.load()
                        total_ms += image.info.get("duration", 0)
                        if index == 0 or index == frames - 1 or index % max(1, frames // 100) == 0:
                            self.progress(f"检查动图 · {index + 1}/{frames} 帧", min(99, (index + 1) / frames * 100))
            except OSError as exc:
                raise ConversionError("动图完整性检查失败，请重试。") from exc
            # An entirely still video is legitimately optimized to a static WebP.
            static = frames == 1 and total_ms == 0
            if not static and (total_ms <= 0 or abs(total_ms / 1000 - info.duration) > max(0.25, 1.1 / (chosen_fps or info.fps))):
                raise ConversionError("动图播放时长异常，已停止保存。")
            if best.stat().st_size >= budget:
                raise ConversionError("文件体积检查失败，已停止保存。")
            self.check_cancel()
            self.progress("检查通过 · 正在保存", 99)
            output = publish(best, directory, source.stem)
            result = Result(str(source), str(output), output.stat().st_size, info.width, info.height,
                            frames, total_ms, best_quality, chosen_fps or info.fps, chosen_fps is not None)
            self.progress(f"完成 · {result.size / 1_000_000:.2f} MB · {result.width} × {result.height}", 100)
            return result


def cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="图片 / 视频转 WebP（保留原尺寸）")
    parser.add_argument("--convert", nargs="+", required=True)
    parser.add_argument("--output-dir")
    parser.add_argument("--max-mb", type=float, default=10)
    parser.add_argument("--keep-fps", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--image-mode", choices=["lossless", "quality95"], default="lossless")
    args = parser.parse_args(argv)
    records = []
    for source in args.convert:
        try:
            from image_converter import ImageConverter, convert_media
            records.append({"ok": True, **asdict(convert_media(ImageConverter(), source, args.max_mb,
                            args.output_dir, args.keep_fps, args.image_mode))})
        except Exception as exc:
            records.append({"ok": False, "source": source, "error": str(exc)})
    text = json.dumps(records, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    if sys.stdout:
        print(text)
    return 0 if all(item["ok"] for item in records) else 1


if __name__ == "__main__":
    raise SystemExit(cli())
