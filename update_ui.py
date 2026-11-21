from pathlib import Path
import os
import sys

from PySide6.QtCore import QStandardPaths, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton,
                               QHBoxLayout, QProgressBar, QTextEdit, QMessageBox)

from updater import UpdateWorker, repository_name, version_tuple, launch_install, verify_download
from version import VERSION, REPOSITORY


class UpdateDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("在线更新")
        self.setMinimumSize(580, 420)
        self.worker = None
        self.release = None
        self.downloaded_path = None
        self.settings = parent.settings
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"当前版本 v{VERSION} · 图片视频转 WebP"))
        layout.addWidget(QLabel("更新仓库（GitHub 公开仓库）"))
        self.repository = QLineEdit(str(self.settings.value("updates/repository", REPOSITORY)))
        self.repository.setPlaceholderText("https://github.com/用户名/仓库名")
        layout.addWidget(self.repository)
        self.info = QLabel("点击检查更新。图片和视频转换本身无需联网。")
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        self.notes = QTextEdit()
        self.notes.setReadOnly(True)
        layout.addWidget(self.notes, 1)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.check = QPushButton("检查更新")
        self.check.clicked.connect(self.check_now)
        row.addWidget(self.check)
        self.page = QPushButton("版本发布页")
        self.page.clicked.connect(self.open_page)
        row.addWidget(self.page)
        self.download = QPushButton("下载新版")
        self.download.setEnabled(False)
        self.download.clicked.connect(self.download_now)
        row.addWidget(self.download)
        self.install = QPushButton("关闭并安装更新")
        self.install.setEnabled(False)
        self.install.clicked.connect(self.install_now)
        row.addWidget(self.install)
        layout.addLayout(row)
        self.repository.textChanged.connect(self.reset_release)

    def reset_release(self):
        self.release = None
        self.downloaded_path = None
        self.download.setEnabled(False)
        self.install.setEnabled(False)

    def start_worker(self, worker):
        self.worker = worker
        self.repository.setEnabled(False)
        self.check.setEnabled(False)
        self.download.setEnabled(False)
        self.install.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        worker.failed.connect(self.failed)
        worker.finished.connect(self.worker_finished)
        worker.start()

    def check_now(self):
        try:
            repository = repository_name(self.repository.text())
        except ValueError as exc:
            self.info.setText(str(exc))
            return
        self.reset_release()
        self.notes.clear()
        self.settings.setValue("updates/repository", repository)
        self.info.setText("正在检查最新版本…")
        worker = UpdateWorker(repository)
        worker.checked.connect(self.checked)
        self.start_worker(worker)

    def checked(self, release):
        self.notes.setPlainText(release.notes)
        if version_tuple(release.version) > version_tuple(VERSION):
            self.release = release
            self.info.setText(f"发现 v{release.version} · {release.size / 1_000_000:.1f} MB，可下载更新。")
        else:
            self.info.setText(f"当前已是最新版本（发布版本 v{release.version}）。")

    def download_now(self):
        if not self.release:
            return
        directory = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "updates"
        self.info.setText("正在下载 · 完成后将校验 SHA-256…")
        worker = UpdateWorker(self.release.repository, self.release, directory)
        worker.progress.connect(self.download_progress)
        worker.downloaded.connect(self.download_complete)
        self.start_worker(worker)

    def download_progress(self, done, total):
        self.progress.setRange(0, 100)
        self.progress.setValue(int(done * 100 / total))
        self.progress.setFormat(f"{done / 1_000_000:.1f} / {total / 1_000_000:.1f} MB · %p%")

    def download_complete(self, path):
        self.downloaded_path = path
        self.info.setText("下载完成，文件校验通过。安装时将关闭程序，保留上一版本和原有设置。")
        self.install.setEnabled(True)

    def failed(self, error):
        self.info.setText("更新未完成，可重试：" + error)

    def worker_finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.repository.setEnabled(True)
        self.check.setEnabled(True)
        self.download.setEnabled(self.release is not None and self.downloaded_path is None)
        self.install.setEnabled(self.downloaded_path is not None)
        self.progress.hide()

    def open_page(self):
        try:
            repository = repository_name(self.repository.text())
            QDesktopServices.openUrl(QUrl(f"https://github.com/{repository}/releases"))
        except ValueError as exc:
            self.info.setText(str(exc))

    def install_now(self):
        parent = self.parent()
        if parent.active or parent.pending:
            self.info.setText("请等待转换队列完成，或取消转换后再安装更新。")
            return
        if not self.downloaded_path:
            return
        if not getattr(sys, "frozen", False):
            self.info.setText("开发模式已完成下载验证。请使用打包 EXE 安装更新。")
            return
        reply = QMessageBox.question(self, "安装更新", "即将关闭本程序、替换为已校验的新版并重新打开。是否现在安装？")
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            verify_download(self.downloaded_path, self.release)
            launch_install(self.downloaded_path)
        except Exception as exc:
            self.failed(str(exc))
            return
        self.accept()
        parent.close()

    def reject(self):
        if self.worker:
            self.worker.stopped.set()
            self.info.setText("正在结束网络请求，请稍候再关闭（最多等待网络超时）。")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self.reject()
            event.ignore()
        else:
            event.accept()
