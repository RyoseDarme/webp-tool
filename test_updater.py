import hashlib
import io
import threading

import pytest

import updater


def manifest(payload=b"executable"):
    return {"version": "1.4.0", "url": "https://github.com/owner/tool/releases/download/v1.4.0/app.exe",
            "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload), "notes": "new"}


def test_versions_repository_and_manifest_boundaries():
    assert updater.version_tuple("v1.10.0") > updater.version_tuple("1.9.0")
    assert updater.repository_name("https://github.com/owner/tool.git") == "owner/tool"
    for bad in ("http://github.com/owner/tool", "owner/tool/extra", "a/../tool"):
        with pytest.raises(ValueError):
            updater.repository_name(bad)
    for patch in ({"url": "https://evil.invalid/a.exe"}, {"sha256": "missing"}, {"size": -1},
                  {"version": "2.0-beta"}, {"url": "https://github.com/owner/other/releases/download/v2/app.exe"}):
        with pytest.raises(ValueError):
            updater.Release.parse({**manifest(), **patch}, "owner/tool")


def test_download_progress_hash_tampering_and_cleanup(tmp_path, monkeypatch):
    payload = b"executable" * 100000
    release = updater.Release.parse(manifest(payload), "owner/tool")
    monkeypatch.setattr(updater, "open_url", lambda _: io.BytesIO(payload))
    events = []
    target = updater.download_release(release, tmp_path, lambda *a: events.append(a), threading.Event())
    assert target.read_bytes() == payload
    assert len(events) > 1 and events[-1] == (len(payload), len(payload))
    updater.verify_download(target, release)
    target.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        updater.verify_download(target, release)
    monkeypatch.setattr(updater, "open_url", lambda _: io.BytesIO(payload[:-1] + b"x"))
    with pytest.raises(ValueError, match="校验失败"):
        updater.download_release(release, tmp_path, lambda *_: None, threading.Event())
    assert not list(tmp_path.glob("*.part"))
    assert target.read_bytes() == b"tampered"


def test_cancel_never_publishes_update(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "open_url", lambda _: io.BytesIO(b"executable"))
    stop = threading.Event()
    stop.set()
    with pytest.raises(ValueError, match="取消"):
        updater.download_release(updater.Release.parse(manifest(), "owner/tool"), tmp_path, lambda *_: None, stop)
    assert not list(tmp_path.iterdir())


def test_install_script_quotes_paths_and_preserves_backup():
    script = updater.install_script("C:/User's Files/tool.exe", "D:/new.exe", 12345)
    assert "C:/User''s Files/tool.exe" in script
    assert "Wait-Process -Id 12345" in script
    assert "'.previous'" in script and "if ($moved)" in script


def test_update_dialog_network_lifecycle_and_retry(monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from app import MainWindow
    from update_ui import UpdateDialog
    import time
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    dialog = UpdateDialog(window)
    dialog.settings = type("Settings", (), {"setValue": lambda *_: None})()
    dialog.repository.setText("owner/tool")
    monkeypatch.setattr(updater, "check_release", lambda _: updater.Release.parse(manifest(), "owner/tool"))
    dialog.check_now()
    assert not dialog.check.isEnabled()
    deadline = time.monotonic() + 5
    while dialog.worker and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert dialog.worker is None and dialog.download.isEnabled()
    assert "1.4.0" in dialog.info.text()
    def fail(_):
        raise OSError("offline")
    monkeypatch.setattr(updater, "check_release", fail)
    dialog.check_now()
    while dialog.worker and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert dialog.check.isEnabled() and not dialog.download.isEnabled()
    assert "offline" in dialog.info.text()
    dialog.close()
    window.close()
