"""Exercise the packaged GUI queue offscreen, with two real video inputs."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import time

from PIL import Image
import psutil

from converter import find_tool
from version import VERSION, APP_NAME

root = Path(__file__).resolve().parent
exe = root / "release" / f"{APP_NAME}_v{VERSION}.exe"
environment = os.environ.copy()
environment["PATH"] = ""
environment.pop("PYTHONPATH", None)
environment["QT_QPA_PLATFORM"] = "offscreen"
report = {}
with tempfile.TemporaryDirectory(prefix="webp-parallel-qa-") as directory:
    folder = Path(directory)
    source = folder / "并行测试一.mov"
    second = folder / "并行测试二.mov"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=640x900:rate=24:duration=2", "-c:v", "qtrle", str(source)],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    shutil.copy2(source, second)
    still = folder / "透明静图.png"
    still_image = Image.new("RGBA", (100, 70), (10, 120, 230, 127))
    still_image.save(still)
    launcher = subprocess.Popen([str(exe), str(source), str(second), str(still)], cwd=folder, env=environment,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    owner = psutil.Process(launcher.pid)
    overlap = False
    children = []
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if launcher.poll() is not None:
                raise AssertionError(f"Application exited early: {launcher.returncode}")
            children = owner.children(recursive=True)
            encoders = []
            for child in children:
                try:
                    if child.name().lower() == "ffmpeg.exe":
                        encoders.append(child)
                except psutil.NoSuchProcess:
                    pass
            overlap = overlap or len(encoders) >= 2
            outputs = list(folder.glob("*_动图.webp"))
            if len(outputs) == 2 and (folder / "透明静图_图片.webp").is_file() and not encoders:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Packaged GUI conversion timed out")
        assert overlap, "Did not observe two FFmpeg processes running together"
        results = []
        for output in outputs:
            with Image.open(output) as image:
                assert image.size == (640, 900) and image.n_frames == 48
            assert output.stat().st_size < 10_000_000
            results.append({"name": output.name, "bytes": output.stat().st_size, "frames": 48, "size": [640, 900]})
        with Image.open(folder / "透明静图_图片.webp") as converted:
            assert converted.convert("RGBA").tobytes() == still_image.tobytes()
        report = {"ok": True, "version": VERSION, "lossless_image_pixels": True,
                  "observed_two_encoders": overlap, "external_path_empty": True, "outputs": results}
    finally:
        try:
            children = owner.children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in reversed(children):
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(children, timeout=5)
        if launcher.poll() is None:
            launcher.terminate()
        launcher.wait(timeout=10)
(root / f"v{VERSION}-packaged-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
