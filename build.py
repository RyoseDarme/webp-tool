"""Build a portable one-file Windows application with its own conversion tools."""
from pathlib import Path
import importlib.metadata
import os
import shutil
import subprocess
import sys

from PIL import Image, ImageDraw
import PySide6
from version import VERSION, APP_NAME as PRODUCT_NAME

ROOT = Path(__file__).resolve().parent
APP_NAME = f"{PRODUCT_NAME}_v{VERSION}"
icon = Image.new("RGBA", (256, 256), (10, 134, 116, 255))
draw = ImageDraw.Draw(icon)
draw.rounded_rectangle((40, 52, 216, 204), 22, fill="#ffffff")
draw.polygon([(107, 85), (107, 171), (173, 128)], fill="#0a8674")
draw.rounded_rectangle((49, 184, 207, 213), 8, fill="#a5e4cf")
icon.save(ROOT / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

ffmpeg = Path(shutil.which("ffmpeg")).resolve()
ffprobe = Path(shutil.which("ffprobe")).resolve()
licenses = ROOT / "licenses"
licenses.mkdir(exist_ok=True)
ffmpeg_license = ffmpeg.parent.parent / "LICENSE"
if ffmpeg_license.exists():
    shutil.copy2(ffmpeg_license, licenses / "FFmpeg-LICENSE.txt")
    shutil.copy2(ffmpeg_license, ROOT / "LICENSE.txt")
    shutil.copy2(ffmpeg_license, licenses / "Application-GPL-3.0.txt")
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.exists():
    shutil.copy2(python_license, licenses / "Python-LICENSE.txt")
pillow_dist = importlib.metadata.distribution("Pillow")
for item in pillow_dist.files:
    if str(item).endswith("licenses/LICENSE"):
        shutil.copy2(pillow_dist.locate_file(item), licenses / "Pillow-LICENSE.txt")
(licenses / "THIRD-PARTY.txt").write_text(
    "Video WebP Tool: GPL-3.0-or-later. Application source accompanies the release.\n"
    "FFmpeg 8.1 full build: GPLv3; build: https://www.gyan.dev/ffmpeg/builds/\n"
    "FFmpeg source: https://github.com/FFmpeg/FFmpeg/tree/n8.1\n"
    "Build recipes: https://github.com/GyanD/codexffmpeg\n"
    "PySide6 / Qt 6.11.1: used under GPLv3; dynamically loaded libraries.\n"
    "Source: https://code.qt.io/pyside/pyside-setup.git/\n"
    "Qt source: https://download.qt.io/archive/qt/6.11/6.11.1/submodules/\n"
    "Licenses: https://doc.qt.io/qtforpython-6/licenses.html\n"
    "Python: PSF license; Pillow: HPND license. See accompanying license files.\n"
    "PyInstaller bootloader: GPL with exception permitting bundled applications.\n",
    encoding="utf-8",
)

command = [
    sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", APP_NAME, "--icon", str(ROOT / "icon.ico"),
    "--distpath", str(ROOT / "release"), "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT), "--add-binary", f"{ffmpeg};bin", "--add-binary", f"{ffprobe};bin",
    "--add-data", f"{ROOT / 'icon.ico'};.", "--add-data", f"{licenses};licenses",
    "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
    "--exclude-module", "PySide6.QtQml", "--exclude-module", "PySide6.QtQuick",
    "--exclude-module", "numpy", "--exclude-module", "scipy", "--exclude-module", "matplotlib",
    str(ROOT / "app.py"),
]
# Python 3.10 ships an older VC runtime. Qt 6.11 needs the newer, backwards
# compatible runtime shipped in its own wheel. Put that runtime at the bundle
# root as well: Python is loaded before Qt and Windows reuses DLLs by basename.
qt_root = Path(PySide6.__file__).parent
for dll in ("VCRUNTIME140.dll", "VCRUNTIME140_1.dll", "MSVCP140.dll", "MSVCP140_1.dll", "MSVCP140_2.dll"):
    runtime = qt_root / dll
    if runtime.is_file():
        command[-1:-1] = ["--add-binary", f"{runtime};."]
# Do not let unrelated PDF/image tools on the developer machine's PATH supply
# same-named ICU/UCRT DLLs. Qt uses Windows' own ICU forwarders on this platform.
build_environment = os.environ.copy()
system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
build_environment["PATH"] = os.pathsep.join(map(str, [qt_root, Path(sys.base_prefix), system_root / "System32", system_root]))
subprocess.run(command, cwd=ROOT, check=True, env=build_environment)
shutil.copy2(ROOT / "使用说明.txt", ROOT / "release" / "使用说明.txt")
shutil.copy2(ROOT / "LICENSE.txt", ROOT / "release" / "LICENSE.txt")
shutil.copytree(licenses, ROOT / "release" / "licenses", dirs_exist_ok=True)
print(ROOT / "release" / f"{APP_NAME}.exe")
