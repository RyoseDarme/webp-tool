"""Render a deterministic example of the progress UI without running conversions."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import time

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont, QFontDatabase
from app import MainWindow
from converter import Result

app = QApplication([])
QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
app.setFont(QFont("Microsoft YaHei", 10))
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    files = [root / f"详情动图{index}.mov" for index in (1, 2, 3)]
    for file in files:
        file.touch()
    window = MainWindow()
    window.start_next = lambda: None
    window.add_files(list(map(str, files)))
    window.pending.clear()
    window.started_at[0] = time.monotonic() - 95
    window.row_states[0] = "active"
    window.success(0, Result(str(files[0]), str(root / "out.webp"), 7558582, 1300, 2553, 60, 1999, 100, 30, False))
    window.started_at[1] = time.monotonic() - 47
    window.row_states[1] = "active"
    window.update_progress(1, "第 1 轮压缩 · 画质 100 · 原帧率 · 已处理 28 帧", 47)
    window.started_at[2] = time.monotonic() - 45
    window.row_states[2] = "active"
    window.update_progress(2, "第 2 轮压缩 · 画质 80 · 原帧率 · 已处理 42 帧", 70)
    window.show()
    app.processEvents()
    window.grab().save(str(Path(__file__).parent / "progress-preview.png"))
    window.pending.clear()
    window.close()
