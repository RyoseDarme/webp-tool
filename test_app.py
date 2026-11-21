import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import time

from PySide6.QtWidgets import QApplication

from app import MainWindow
from converter import Result
from converter import Converter, Cancelled, find_tool
import subprocess
import threading


def wait_for(app, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for worker state")


def test_per_file_progress_is_retained_and_queue_counts_completions(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.start_next = lambda: None
    paths = [tmp_path / "first.mov", tmp_path / "second.mov"]
    for path in paths:
        path.touch()
    window.add_files(list(map(str, paths)))
    assert window.row_progress[0].value() == window.row_progress[1].value() == 0
    window.row_states[0] = "active"
    window.started_at[0] = time.monotonic() - 65
    window.update_progress(0, "第 1 轮压缩 · 画质 100 · 已处理 28 帧", 47)
    assert window.row_progress[0].value() == 47
    assert "第 1 轮" in window.row_progress[0].format()
    assert window.table.item(0, 5).text() == "01:05"
    assert window.row_progress[1].value() == 0
    assert window.progress.value() == 0
    window.update_progress(0, "第 2 轮压缩 · 画质 80", 0)
    assert "第 2 轮" in window.row_progress[0].format()
    window.update_progress(0, "检查动图 · 59/60 帧", 98)
    assert window.row_progress[0].value() == 98
    window.success(0, Result(str(paths[0]), str(tmp_path / "out.webp"), 1000, 1300, 2553, 60, 1999, 100, 30, False))
    assert window.row_progress[0].value() == 100
    assert window.row_progress[0].format() == "完成 · 100%"
    assert window.progress.value() == 1 and window.progress.maximum() == 2
    window.started_at[1] = time.monotonic()
    window.row_states[1] = "active"
    window.update_progress(1, "第 1 轮压缩 · 画质 100", 36)
    assert window.row_progress[0].value() == 100
    assert window.row_progress[1].value() == 36
    window.failure(1, "测试失败", False)
    assert window.row_progress[1].format() == "未完成"
    assert window.progress.value() == 2
    assert "成功 1 个" in window.progress.format()
    window.pending.clear()
    window.clear()
    assert window.table.rowCount() == 0
    assert window.progress.value() == 0
    window.close()


def test_parallel_jobs_route_out_of_order_results_refill_and_resize(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    gates = {str(index): threading.Event() for index in range(5)}
    entered = set()
    lock = threading.Lock()

    def controlled_convert(self, source, *_):
        name = source.stem
        with lock:
            entered.add(name)
        self.progress(f"第 1 轮 · {name}", 20 + int(name))
        while not gates[name].wait(0.01):
            self.check_cancel()
        self.check_cancel()
        return Result(str(source), str(tmp_path / f"{name}.webp"), 1000 + int(name), 64, 48, 2, 1000, 100, 2, False)

    monkeypatch.setattr(Converter, "convert", controlled_convert)
    window = MainWindow()
    window.settings = type("Settings", (), {"setValue": lambda *_: None})()
    window.parallelism.setValue(2)
    sources = [tmp_path / f"{index}.mov" for index in range(5)]
    for source in sources:
        source.touch()
    try:
        window.add_files(list(map(str, sources)))
        wait_for(app, lambda: entered == {"0", "1"} and window.row_progress[1].value() == 21)
        assert set(window.active) == {0, 1} and len(window.pending) == 3
        # Finish the second row before the first; only that row may complete.
        gates["1"].set()
        wait_for(app, lambda: 1 in window.results and "2" in entered)
        assert window.row_progress[0].value() == 20
        assert window.row_progress[1].value() == 100
        assert window.results[1].source == str(sources[1])
        window.parallelism.setValue(1)
        gates["2"].set()
        wait_for(app, lambda: 2 in window.results and set(window.active) == {0})
        assert "3" not in entered
        window.parallelism.setValue(3)
        wait_for(app, lambda: {"3", "4"} <= entered)
        assert len(window.active) == 3
        for gate in gates.values():
            gate.set()
        wait_for(app, lambda: len(window.results) == 5 and not window.active)
        assert all(window.results[row].source == str(sources[row]) for row in range(5))
        assert all(bar.value() == 100 for bar in window.row_progress.values())
        assert window.progress.value() == 5
    finally:
        window.cancel_all()
        for gate in gates.values():
            gate.set()
        wait_for(app, lambda: not window.active)
        window.pending.clear()
        window.close()


def test_cancel_and_close_stop_all_workers_and_pending_tasks(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    entered = set()

    def blocked_convert(self, source, *_):
        entered.add(source.stem)
        while not self.stopped.wait(0.01):
            pass
        self.check_cancel()

    monkeypatch.setattr(Converter, "convert", blocked_convert)
    for close_window in (False, True):
        window = MainWindow()
        window.settings = type("Settings", (), {"setValue": lambda *_: None})()
        window.parallelism.setValue(2)
        entered.clear()
        sources = [tmp_path / f"{index}.mov" for index in range(4)]
        for source in sources:
            source.touch()
        window.add_files(list(map(str, sources)))
        wait_for(app, lambda: len(entered) == 2)
        if close_window:
            window.close()
        else:
            window.cancel_all()
        wait_for(app, lambda: not window.active)
        assert len(entered) == 2 and not window.pending
        assert all(state == "cancelled" for state in window.row_states.values())
        assert not window.results
        window.close()


def test_real_ffmpeg_processes_overlap_and_create_separate_outputs(tmp_path):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / "one.mov"
    subprocess.run([find_tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
                    "testsrc=size=480x640:rate=20:duration=1.5", "-c:v", "qtrle", str(source)],
                   check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    import shutil
    second = tmp_path / "two.mov"
    shutil.copy2(source, second)
    window = MainWindow()
    window.settings = type("Settings", (), {"setValue": lambda *_: None})()
    window.parallelism.setValue(2)
    window.size.setValue(10)
    window.set_output(str(tmp_path / "output"))
    overlap = False
    try:
        window.add_files([str(source), str(second)])
        deadline = time.monotonic() + 45
        while (window.active or window.pending) and time.monotonic() < deadline:
            app.processEvents()
            running = [job.converter.process for job in window.active.values()]
            if len(running) == 2 and all(p and p.poll() is None for p in running):
                overlap = True
            time.sleep(0.01)
        assert not window.active and not window.pending
        assert overlap and len(window.results) == 2
        assert all(result.frames == 30 and result.size < 10_000_000 for result in window.results.values())
        assert len({result.output for result in window.results.values()}) == 2
        assert all(bar.value() == 100 for bar in window.row_progress.values())
    finally:
        window.cancel_all()
        wait_for(app, lambda: not window.active)
        window.close()
