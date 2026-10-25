"""后台任务:在线程里运行耗时操作(文字识别、格式转换……),界面显示进度,可以取消。

PyMuPDF 不是线程安全的:任务运行期间 is_busy() 为 True,界面里的渲染定时任务会暂停等待。
"""
from __future__ import annotations

import gc
import queue
import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

_busy = 0


def is_busy() -> bool:
    return _busy > 0


class TaskDialog(QDialog):
    def __init__(self, parent, title: str, fn, can_cancel=True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.fn = fn
        self.q: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.outcome = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(12)
        head = QLabel(title)
        head.setObjectName("PanelTitle")
        lay.addWidget(head)
        self.text = QLabel("正在处理…")
        self.text.setObjectName("Muted")
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn = QPushButton("取消")
        self.btn.clicked.connect(self._cancel)
        self.btn.setVisible(can_cancel)
        row.addWidget(self.btn)
        lay.addLayout(row)
        self.can_cancel = can_cancel
        self.timer = QTimer(self, interval=50, timeout=self._poll)

    def start(self):
        global _busy
        _busy += 1
        # 垃圾回收如果碰巧在后台线程里回收 Qt 对象会出问题:先在主线程回收一次,任务期间暂停自动回收
        gc.collect()
        self._gc_was_enabled = gc.isenabled()
        gc.disable()
        threading.Thread(target=self._work, daemon=True).start()
        self.timer.start()

    def _work(self):
        try:
            self.q.put(("done", self.fn(self._progress, self.cancel_flag.is_set)))
        except BaseException as ex:  # noqa: BLE001
            self.q.put(("error", ex))

    def _progress(self, done, total, text=None):
        self.q.put(("progress", done, total, text))

    def _cancel(self):
        self.cancel_flag.set()
        self.text.setText("正在取消…")
        self.btn.setEnabled(False)

    def reject(self):  # Esc / 关闭按钮 = 取消,等任务自己结束
        if self.outcome is None:
            if self.can_cancel:
                self._cancel()
            return
        super().reject()

    def _poll(self):
        global _busy
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "progress":
                    _k, done, total, text = msg
                    if total:
                        self.bar.setRange(0, int(total))
                        self.bar.setValue(int(done))
                    if text:
                        self.text.setText(text)
                else:
                    self.outcome = msg
                    self.timer.stop()
                    _busy -= 1
                    if self._gc_was_enabled:
                        gc.enable()
                    self.accept()
                    return
        except queue.Empty:
            pass


def run_task(parent, title: str, fn, can_cancel=True):
    """fn(progress, cancelled) 在后台线程运行;返回它的结果,它抛出的异常原样抛出。

    progress(done, total, text=None) 汇报进度;cancelled() 为 True 时任务应尽快结束。"""
    dlg = TaskDialog(parent, title, fn, can_cancel)
    dlg.start()
    dlg.exec()
    kind, value = dlg.outcome
    dlg.deleteLater()
    if kind == "error":
        raise value
    return value
