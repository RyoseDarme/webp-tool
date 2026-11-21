from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QSettings, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMainWindow, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QAbstractItemView, QSpinBox, QComboBox,
)

from converter import Cancelled, Converter, Result, cli
from image_converter import ImageConverter, IMAGE_EXTENSIONS, convert_media
from version import VERSION, APP_NAME
from update_ui import UpdateDialog


VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".wmv", ".flv", ".mpeg", ".mpg", ".ts", ".mts", ".m2ts", ".gif", ".webp"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def recommended_parallelism():
    """Conservative default; large WebP jobs retain many full-resolution frames."""
    memory_limit = 2
    if os.name == "nt":
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in
                ("total", "available", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            memory_limit = max(1, status.total // (4 * 1024 ** 3))
    return max(1, min(4, memory_limit, (os.cpu_count() or 2) // 2))


class Job(QThread):
    update = Signal(int, str, object)
    succeeded = Signal(int, object)
    failed = Signal(int, str, bool)

    def __init__(self, row, source, max_mb, output, keep_fps, image_mode="lossless"):
        super().__init__()
        self.row = row
        self.source, self.max_mb, self.output, self.keep_fps = source, max_mb, output, keep_fps
        self.image_mode = image_mode
        self.converter = ImageConverter(lambda message, value: self.update.emit(self.row, message, value))

    def run(self):
        try:
            self.succeeded.emit(self.row, convert_media(self.converter, self.source, self.max_mb, self.output, self.keep_fps, self.image_mode))
        except Cancelled:
            self.failed.emit(self.row, "已取消", True)
        except Exception as exc:
            self.failed.emit(self.row, str(exc), False)

    def cancel(self):
        self.converter.cancel()


class DropZone(QFrame):
    files = Signal(list)
    browse = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(145)
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for text, name in [("＋", "dropIcon"), ("将图片或视频拖到这里", "dropTitle"), ("自动识别类型 · 支持混合批量转换，也可以点击添加文件", "muted")]:
            label = QLabel(text)
            label.setObjectName(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            self.setProperty("dragging", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event):
        self.setProperty("dragging", False)
        self.style().unpolish(self)
        self.style().polish(self)

    def dropEvent(self, event):
        self.dragLeaveEvent(event)
        self.files.emit([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.acceptProposedAction()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.browse.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"图片 / 视频转 WebP · v{VERSION}")
        self.setWindowIcon(QIcon(str(ROOT / "icon.ico")))
        self.resize(1250, 850)
        self.setMinimumSize(1100, 780)
        self.settings = QSettings("LocalTools", "VideoWebP")
        self.pending = deque()
        self.active = {}
        self.cancelling = False
        self.known = set()
        self.results = {}
        self.last_output = None
        self.closing = False
        self.row_progress = {}
        self.row_states = {}
        self.started_at = {}
        self.tick_timer = QTimer(self)
        self.tick_timer.setInterval(1000)
        self.tick_timer.timeout.connect(self.refresh_elapsed)
        self.tick_timer.start()

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(30, 25, 30, 22)
        layout.setSpacing(16)
        top = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("图片 / 视频转 WebP")
        title.setObjectName("title")
        titles.addWidget(title)
        subtitle = QLabel("图片无损保真 · 视频高清动图 · 保留原尺寸")
        subtitle.setObjectName("muted")
        titles.addWidget(subtitle)
        top.addLayout(titles)
        top.addStretch()
        badge = QLabel("本地转换 · 素材不上传")
        badge.setObjectName("badge")
        top.addWidget(badge)
        self.update_button = QPushButton("检查更新")
        self.update_button.clicked.connect(self.show_updates)
        top.addWidget(self.update_button)
        layout.addLayout(top)

        drop = DropZone()
        drop.files.connect(self.add_files)
        drop.browse.connect(self.browse)
        layout.addWidget(drop)

        options = QFrame()
        options.setObjectName("card")
        settings_layout = QVBoxLayout(options)
        settings_layout.setContentsMargins(18, 15, 18, 15)
        row = QHBoxLayout()
        row.addWidget(QLabel("视频 / 动图上限"))
        self.size = QDoubleSpinBox()
        self.size.setAccessibleName("文件大小上限")
        self.size.setRange(0.1, 1000)
        self.size.setDecimals(1)
        self.size.setSingleStep(1)
        self.size.setSuffix(" MB")
        try:
            self.size.setValue(float(self.settings.value("max_mb", 10)))
        except (TypeError, ValueError):
            self.size.setValue(10)
        self.size.setMinimumWidth(120)
        row.addWidget(self.size)
        row.addSpacing(22)
        self.keep_fps = QCheckBox("锁定原帧率")
        self.keep_fps.setToolTip("勾选后不减少帧数；若无法同时满足体积和清晰度要求，会提示调整上限。")
        self.keep_fps.setChecked(self.settings.value("keep_fps", False, type=bool))
        row.addWidget(self.keep_fps)
        row.addSpacing(16)
        row.addWidget(QLabel("同时转换"))
        self.parallelism = QSpinBox()
        self.parallelism.setRange(1, 8)
        self.parallelism.setSuffix(" 个")
        self.parallelism.setAccessibleName("同时转换数量")
        self.parallelism.setToolTip("多个视频同时转换。建议 2～4 个；高分辨率或较长视频占用内存较多。降低数量时，正在转换的文件会继续完成。")
        try:
            self.parallelism.setValue(int(self.settings.value("parallelism", recommended_parallelism())))
        except (ValueError, TypeError):
            self.parallelism.setValue(recommended_parallelism())
        self.parallelism.valueChanged.connect(self.change_parallelism)
        row.addWidget(self.parallelism)
        row.addStretch()
        original_size = QLabel("始终保留原尺寸")
        original_size.setObjectName("accent")
        row.addWidget(original_size)
        settings_layout.addLayout(row)
        image_row = QHBoxLayout()
        image_row.addWidget(QLabel("静态图片画质"))
        self.image_mode = QComboBox()
        self.image_mode.addItem("无损保真（推荐）", "lossless")
        self.image_mode.addItem("高画质 95（文件更小）", "quality95")
        saved_mode = self.settings.value("image_mode", "lossless")
        self.image_mode.setCurrentIndex(max(0, self.image_mode.findData(saved_mode)))
        self.image_mode.setToolTip("无损模式保留解码后的像素与透明通道，不新增压缩噪点。高画质 95 为有损压缩。")
        image_row.addWidget(self.image_mode)
        image_note = QLabel("图片不受视频体积上限限制 · 优先保留文字和细节")
        image_note.setObjectName("muted")
        image_row.addWidget(image_note)
        image_row.addStretch()
        settings_layout.addLayout(image_row)
        second = QHBoxLayout()
        self.output_label = QLabel("输出到原文件所在文件夹")
        self.output_label.setObjectName("muted")
        self.output_label.setMinimumWidth(200)
        self.output_label.setWordWrap(True)
        self.output_dir = ""
        second.addWidget(self.output_label, 1)
        self.output_button = QPushButton("更改位置")
        self.output_button.clicked.connect(self.choose_output)
        second.addWidget(self.output_button)
        self.reset_output = QPushButton("恢复默认")
        self.reset_output.clicked.connect(lambda: self.set_output(""))
        second.addWidget(self.reset_output)
        settings_layout.addLayout(second)
        layout.addWidget(options)

        list_title = QHBoxLayout()
        self.count = QLabel("转换队列")
        self.count.setObjectName("sectionTitle")
        list_title.addWidget(self.count)
        list_title.addStretch()
        self.add_button = QPushButton("添加图片 / 视频")
        self.add_button.clicked.connect(self.browse)
        list_title.addWidget(self.add_button)
        self.clear_button = QPushButton("清空列表")
        self.clear_button.clicked.connect(self.clear)
        list_title.addWidget(self.clear_button)
        layout.addLayout(list_title)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["文件", "输出尺寸", "文件大小", "状态 / 当前步骤", "当前步骤进度", "已用时间"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in [(1, 125), (2, 100), (3, 230), (4, 145), (5, 90)]:
            self.table.setColumnWidth(column, width)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(47)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.itemDoubleClicked.connect(self.show_item)
        layout.addWidget(self.table, 1)

        self.status = QLabel("就绪 · 拖入图片或视频即可开始")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(22)
        self.progress.setFormat("队列进度 · 0/0 个已结束")
        layout.addWidget(self.progress)
        bottom = QHBoxLayout()
        footnote = QLabel("每轮显示实际处理进度；压缩后检查并保存，完成显示 100%。")
        footnote.setObjectName("muted")
        bottom.addWidget(footnote, 1)
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_all)
        bottom.addWidget(self.cancel_button)
        self.open_button = QPushButton("打开输出文件夹")
        self.open_button.setObjectName("primary")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        bottom.addWidget(self.open_button)
        layout.addLayout(bottom)
        self.setStyleSheet(STYLE)

    def show_updates(self):
        UpdateDialog(self).exec()

    def set_output(self, directory):
        self.output_dir = directory
        self.output_label.setText(directory or "输出到原文件所在文件夹")
        self.output_label.setToolTip(directory)

    def choose_output(self):
        directory = QFileDialog.getExistingDirectory(self, "选择输出文件夹", self.output_dir or str(Path.home()))
        if directory:
            self.set_output(directory)

    def browse(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图片或视频", "", "图片和视频 (*.jpg *.jpeg *.jfif *.png *.bmp *.tif *.tiff *.gif *.webp *.mov *.mp4 *.m4v *.mkv *.avi *.webm *.wmv *.flv *.mpeg *.mpg *.ts *.mts *.m2ts);;静态图片 (*.jpg *.jpeg *.jfif *.png *.bmp *.tif *.tiff *.webp);;视频和动图 (*.mov *.mp4 *.m4v *.mkv *.avi *.webm *.gif *.webp);;所有文件 (*)")
        self.add_files(paths)

    def add_files(self, paths):
        skipped = []
        for raw in paths:
            path = Path(raw).resolve()
            key = os.path.normcase(str(path))
            if key in self.known:
                continue
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                skipped.append(path.name)
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, text in enumerate([path.name, "保留原尺寸", "—", "等待中", "", "—"]):
                item = QTableWidgetItem(text)
                item.setToolTip(str(path) if column == 0 else text)
                self.table.setItem(row, column, item)
            bar = QProgressBar()
            bar.setAccessibleName(f"{path.name} 的转换进度")
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFormat("等待中")
            bar.setFixedHeight(23)
            bar.setToolTip("每轮压缩显示独立进度，最终保存成功后显示完成 100%。")
            wrapper = QWidget()
            bar_layout = QHBoxLayout(wrapper)
            bar_layout.setContentsMargins(7, 8, 7, 8)
            bar_layout.addWidget(bar)
            self.table.setCellWidget(row, 4, wrapper)
            self.row_progress[row] = bar
            self.row_states[row] = "pending"
            self.known.add(key)
            self.pending.append((row, path, self.size.value(), self.output_dir or None, self.keep_fps.isChecked(), self.image_mode.currentData()))
        self.count.setText(f"转换队列 · {self.table.rowCount()} 个文件")
        self.refresh_queue()
        if skipped:
            QMessageBox.information(self, "部分文件未添加", "以下文件不是支持的图片或视频：\n" + "\n".join(skipped[:12]))
        self.start_next()

    def start_next(self):
        if self.closing or self.cancelling:
            return
        while self.pending and len(self.active) < self.parallelism.value():
            self.start_job(self.pending.popleft())
        self.cancel_button.setEnabled(bool(self.active or self.pending))
        self.clear_button.setEnabled(not (self.active or self.pending))
        self.refresh_elapsed()
        if not self.pending and not self.active:
            self.cancel_button.setEnabled(False)
            self.clear_button.setEnabled(True)
            if self.table.rowCount():
                completed = len(self.results)
                self.status.setText(f"队列已结束 · 成功 {completed} 个，共 {self.table.rowCount()} 个")
                self.refresh_queue()
            return
    def change_parallelism(self, value):
        self.settings.setValue("parallelism", value)
        self.start_next()

    def start_job(self, item):
        row, path, max_mb, output, keep_fps, image_mode = item
        self.started_at[row] = time.monotonic()
        self.row_states[row] = "active"
        self.row_progress[row].setFormat("准备 · 0%")
        self.table.item(row, 5).setText("00:00")
        self.table.item(row, 3).setText("正在转换")
        self.cancel_button.setEnabled(True)
        self.clear_button.setEnabled(False)
        self.settings.setValue("max_mb", self.size.value())
        self.settings.setValue("keep_fps", self.keep_fps.isChecked())
        self.settings.setValue("image_mode", self.image_mode.currentData())
        job = Job(row, path, max_mb, output, keep_fps, image_mode)
        self.active[row] = job
        job.update.connect(self.update_progress)
        job.succeeded.connect(self.success)
        job.failed.connect(self.failure)
        job.finished.connect(self.job_finished)
        job.start()

    @Slot(int, str, object)
    def update_progress(self, row, message, percent):
        if self.row_states.get(row) != "active":
            return
        self.table.item(row, 3).setText(message)
        self.table.item(row, 3).setToolTip(message)
        bar = self.row_progress[row]
        if percent is not None:
            bar.setRange(0, 100)
            bar.setValue(min(99, max(0, int(percent))))
            prefix = message.split("轮", 1)[0] + "轮" if message.startswith("第 ") else "检查" if "检查" in message else "处理"
            bar.setFormat(prefix + " · %p%")
        else:
            bar.setRange(0, 0)
            bar.setFormat("处理中")
        self.refresh_elapsed()

    def refresh_elapsed(self):
        running = [row for row, state in self.row_states.items() if state == "active"]
        for row in running:
            seconds = int(time.monotonic() - self.started_at[row])
            self.table.item(row, 5).setText(f"{seconds // 60:02d}:{seconds % 60:02d}")
        if running:
            self.status.setText(f"同时转换 {len(running)} 个 · 等待 {len(self.pending)} 个 · 每个文件的进度与耗时见列表")

    def refresh_queue(self):
        total = self.table.rowCount()
        finished = sum(state in {"success", "failed", "cancelled"} for state in self.row_states.values())
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(finished)
        self.progress.setFormat(f"队列进度 · {finished}/{total} 个已结束 · 成功 {len(self.results)} 个")

    def finish_row(self, row, state):
        self.refresh_elapsed()
        self.row_states[row] = state
        self.row_progress[row].setRange(0, 100)
        if state == "success":
            self.row_progress[row].setValue(100)
            self.row_progress[row].setFormat("完成 · 100%")
        else:
            self.row_progress[row].setFormat("已取消" if state == "cancelled" else "未完成")
        self.refresh_queue()

    @Slot(int, object)
    def success(self, row, result: Result):
        self.results[row] = result
        self.finish_row(row, "success")
        self.last_output = result.output
        self.table.item(row, 1).setText(f"{result.width} × {result.height}")
        self.table.item(row, 2).setText(f"{result.size / 1_000_000:.2f} MB")
        item = self.table.item(row, 3)
        item.setText(f"完成 · {result.encoding}" if result.encoding else "完成 · 静态画面" if result.frames == 1 else "完成 · 已降帧率" if result.reduced_fps else "完成")
        item.setForeground(QColor("#087f70"))
        detail = result.encoding if result.encoding else f"{result.frames} 帧 · {result.duration_ms / 1000:g} 秒 · 画质 {result.quality}"
        item.setToolTip(f"{result.output}\n{detail}")
        self.open_button.setEnabled(True)

    @Slot(int, str, bool)
    def failure(self, row, message, cancelled):
        self.finish_row(row, "cancelled" if cancelled else "failed")
        item = self.table.item(row, 3)
        item.setText("已取消" if cancelled else "转换失败 · 双击查看")
        item.setForeground(QColor("#a66028" if cancelled else "#bd3c3c"))
        item.setToolTip(message)
        self.status.setText(message)

    @Slot()
    def job_finished(self):
        job = self.sender()
        self.active.pop(job.row, None)
        job.deleteLater()
        if self.closing and not self.active:
            QTimer.singleShot(0, self.close)
        elif not self.closing:
            if not self.active:
                self.cancelling = False
            QTimer.singleShot(150, self.start_next)

    def cancel_all(self):
        self.cancelling = bool(self.active)
        while self.pending:
            row, *_ = self.pending.popleft()
            self.table.item(row, 3).setText("已取消")
            self.finish_row(row, "cancelled")
        if self.active:
            for job in list(self.active.values()):
                job.cancel()
            self.cancel_button.setEnabled(False)
            self.status.setText("正在取消，请稍候…")

    def clear(self):
        if self.active or self.pending:
            return
        self.table.setRowCount(0)
        self.known.clear()
        self.results.clear()
        self.row_progress.clear()
        self.row_states.clear()
        self.started_at.clear()
        self.count.setText("转换队列")
        self.status.setText("就绪 · 拖入图片或视频即可开始")
        self.refresh_queue()

    def show_item(self, item):
        row = item.row()
        if row in self.results:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.results[row].output))
        elif self.table.item(row, 3).text().startswith("转换失败"):
            QMessageBox.information(self, "转换未完成", self.table.item(row, 3).toolTip())

    def open_output(self):
        result = self.results.get(self.table.currentRow())
        path = result.output if result else self.last_output
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))

    def closeEvent(self, event):
        if self.active:
            self.closing = True
            self.cancel_all()
            self.setEnabled(False)
            event.ignore()
        else:
            event.accept()


STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #243744; }
QMainWindow { background: #f5f7fa; }
QLabel#title { font-size: 28px; font-weight: 700; color: #182f3a; }
QLabel#muted { color: #73828d; font-size: 12px; }
QLabel#accent { color: #087f70; font-weight: 600; }
QLabel#badge { color: #087f70; background: #e5f4ef; padding: 9px 13px; border-radius: 15px; font-size: 12px; }
QFrame#dropZone { background: #ffffff; border: 2px dashed #b1d8ce; border-radius: 14px; }
QFrame#dropZone[dragging=true] { background: #e6f7f1; border-color: #138b76; }
QLabel#dropIcon { font-size: 25px; color: #138b76; font-weight: 500; }
QLabel#dropTitle { font-size: 19px; font-weight: 600; }
QFrame#card { background: white; border: 1px solid #e1e7ed; border-radius: 12px; }
QLabel#sectionTitle { font-weight: 600; font-size: 14px; }
QPushButton { background: white; border: 1px solid #dce3e9; padding: 8px 14px; border-radius: 7px; }
QPushButton:hover { background: #edf5f3; border-color: #8ebeb1; }
QPushButton:pressed { background: #d8eee7; }
QPushButton:disabled { color: #adb6bf; background: #f1f4f7; }
QPushButton#primary { color: white; background: #0a8674; border: 1px solid #0a8674; font-weight: 600; }
QPushButton#primary:hover { background: #076e61; }
QPushButton#primary:disabled { background: #adc9c1; border-color: #adc9c1; }
QDoubleSpinBox, QSpinBox, QComboBox, QLineEdit { background: #f8fafc; border: 1px solid #dce3e9; border-radius: 6px; padding: 7px; }
QCheckBox { spacing: 7px; }
QTableWidget { background: white; alternate-background-color: #fafcfd; border: 1px solid #e1e7ed; border-radius: 8px; selection-background-color: #e5f3ee; selection-color: #243744; }
QHeaderView::section { background: #eef2f6; color: #697b88; border: none; padding: 10px; font-weight: 500; }
QTableWidget::item { padding: 7px; border-bottom: 1px solid #f0f3f6; }
QProgressBar { background: #e1e9ee; border: none; border-radius: 3px; text-align: center; color: #173d39; font-size: 11px; }
QProgressBar::chunk { background: #0a8674; border-radius: 3px; }
QLabel#status { color: #3c5665; font-size: 12px; }
QToolTip { color: #243744; background: #ffffff; border: 1px solid #dce3e9; padding: 6px; }
"""


def main():
    if "--convert" in sys.argv:
        return cli()
    app = QApplication(sys.argv)
    app.setApplicationName("视频转 WebP")
    app.setApplicationVersion(VERSION)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow()
    window.show()
    paths = [arg for arg in sys.argv[1:] if Path(arg).is_file()]
    if paths:
        QTimer.singleShot(200, lambda: window.add_files(paths))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
