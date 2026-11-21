"""Public GitHub release updates, downloaded on a worker and verified before use."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import urllib.request

from PySide6.QtCore import QThread, Signal

from version import VERSION


def repository_name(value):
    value = value.strip().removesuffix("/").removesuffix(".git")
    if value.startswith("https://github.com/"):
        value = value[len("https://github.com/"):]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("请填写 GitHub 仓库链接，例如 https://github.com/用户名/仓库名")
    return value


def version_tuple(value):
    if not re.fullmatch(r"v?\d+\.\d+\.\d+", value):
        raise ValueError("更新版本号格式无效。")
    return tuple(map(int, value.lstrip("v").split(".")))


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int
    notes: str
    repository: str

    @classmethod
    def parse(cls, data, repository):
        repository = repository_name(repository)
        version_tuple(data["version"])
        prefix = f"https://github.com/{repository}/releases/download/"
        url = data["url"]
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        if not url.startswith(prefix) or parts.query or parts.fragment or not parts.path.endswith(".exe"):
            raise ValueError("更新文件必须来自所选仓库的 GitHub Release。")
        checksum = data["sha256"].lower()
        if not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise ValueError("更新文件缺少有效 SHA-256 校验值。")
        size = data["size"]
        if type(size) is not int or not 0 < size <= 1_500_000_000:
            raise ValueError("更新包大小无效。")
        return cls(data["version"], url, checksum, size, str(data.get("notes", ""))[:12000], repository)


def open_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": f"WebP-Tool/{VERSION}", "Cache-Control": "no-cache"})
    response = urllib.request.urlopen(request, timeout=20)
    if not response.geturl().startswith("https://"):
        response.close()
        raise ValueError("更新连接必须使用 HTTPS。")
    return response


def check_release(repository):
    repository = repository_name(repository)
    url = f"https://github.com/{repository}/releases/latest/download/latest.json"
    with open_url(url) as response:
        payload = response.read(128 * 1024 + 1)
    if len(payload) > 128 * 1024:
        raise ValueError("更新信息超出大小限制。")
    return Release.parse(json.loads(payload), repository)


def download_release(release, directory, progress, stopped):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"webp-update-{release.version}.exe"
    partial = target.with_suffix(".exe.part")
    try:
        digest = hashlib.sha256()
        received = 0
        with open_url(release.url) as response, partial.open("wb") as output:
            while True:
                if stopped.is_set():
                    raise ValueError("已取消下载。")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > release.size:
                    raise ValueError("更新包超出声明大小。")
                output.write(chunk)
                digest.update(chunk)
                progress(received, release.size)
        if stopped.is_set():
            raise ValueError("已取消下载。")
        if received != release.size or digest.hexdigest() != release.sha256:
            raise ValueError("更新包完整性校验失败，请重新下载。")
        os.replace(partial, target)
        return target
    finally:
        partial.unlink(missing_ok=True)


class UpdateWorker(QThread):
    checked = Signal(object)
    downloaded = Signal(str)
    progress = Signal(int, int)
    failed = Signal(str)

    def __init__(self, repository, release=None, directory=None):
        super().__init__()
        self.repository, self.release, self.directory = repository, release, directory
        self.stopped = threading.Event()

    def run(self):
        try:
            if self.release:
                path = download_release(self.release, self.directory, self.progress.emit, self.stopped)
                self.downloaded.emit(str(path))
            else:
                release = check_release(self.repository)
                if not self.stopped.is_set():
                    self.checked.emit(release)
        except Exception as exc:
            self.failed.emit(str(exc))


def verify_download(path, release):
    path = Path(path)
    if path.stat().st_size != release.size:
        raise ValueError("下载文件已改变，请重新下载。")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != release.sha256:
        raise ValueError("下载文件已改变，请重新下载。")



def quote_ps(value):
    return "'" + str(value).replace("'", "''") + "'"


def install_script(current, downloaded, pid):
    # Retry handles both the Qt child and the PyInstaller bootloader releasing the EXE.
    return f"""$ErrorActionPreference = 'Stop'
$target = {quote_ps(current)}
$download = {quote_ps(downloaded)}
$backup = $target + '.previous'
$log = $download + '.update.log'
Wait-Process -Id {int(pid)} -ErrorAction SilentlyContinue
$moved = $false
try {{
  for ($attempt = 0; $attempt -lt 60; $attempt++) {{
    try {{
      Move-Item -LiteralPath $target -Destination $backup -Force
      $moved = $true
      break
    }} catch {{ Start-Sleep -Seconds 1 }}
  }}
  if (-not $moved) {{ throw 'Cannot replace the current executable. Download is preserved.' }}
  Copy-Item -LiteralPath $download -Destination $target -Force
  Start-Process -FilePath $target
  'Update installed. Previous executable preserved as .previous.' | Set-Content -LiteralPath $log
}} catch {{
  $_ | Out-String | Set-Content -LiteralPath $log
  if ($moved) {{
    Copy-Item -LiteralPath $backup -Destination $target -Force
    Start-Process -FilePath $target
  }}
}}
"""


def launch_install(downloaded):
    if not getattr(sys, "frozen", False) or os.name != "nt":
        raise ValueError("请使用打包后的 Windows EXE 安装更新。")
    script = install_script(Path(sys.executable).resolve(), Path(downloaded).resolve(), os.getpid())
    path = Path(downloaded).with_suffix(".update.ps1")
    path.write_text(script, encoding="utf-8-sig")
    system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    powershell = system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.Popen([str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                      "-WindowStyle", "Hidden", "-File", str(path)],
                     creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
