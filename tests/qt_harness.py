"""Qt 界面测试的公共准备:离屏运行(不弹窗、不抢焦点),并手动加载系统字体(离屏模式不会自动找字体)。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_FONTS = ["msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "segoeui.ttf", "segoeuib.ttf", "seguisb.ttf", "arial.ttf", "arialbd.ttf",
          "times.ttf", "cour.ttf", "simsun.ttc", "simhei.ttf", "simkai.ttf", "simfang.ttf", "calibri.ttf", "cambria.ttc"]


def make_app():
    app = QApplication.instance() or QApplication(sys.argv)
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        fdir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for f in _FONTS:
            p = fdir / f
            if p.exists():
                QFontDatabase.addApplicationFont(str(p))
    return app


def pump(app, ms: int = 0):
    from PySide6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while True:
        app.processEvents()
        if t.elapsed() >= ms:
            break
