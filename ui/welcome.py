"""没有打开文档时显示的欢迎页:打开文件、拖放、最近打开、常用工具。"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy,
                               QVBoxLayout, QWidget)

from ui import icons
from ui.widgets import label


def ago(ts: float) -> str:
    d = time.time() - ts
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    if d < 86400:
        return f"{int(d // 3600)} 小时前"
    if d < 86400 * 30:
        return f"{int(d // 86400)} 天前"
    return time.strftime("%Y-%m-%d", time.localtime(ts))


class Logo(QLabel):
    """程序标志(和 exe 图标是同一个图案,见 ui/brand.py)。"""

    def __init__(self, size=44):
        super().__init__()
        self.setObjectName("AppLogo")
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self._size = size
        self.refresh()

    def refresh(self):
        from ui.brand import icon_pixmap
        self.setPixmap(icon_pixmap(self._size, max(self.devicePixelRatioF(), 2.0)))


class RecentRow(QPushButton):
    def __init__(self, path: str, ts: float):
        super().__init__()
        self.setObjectName("RecentRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.path = path
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("doc", icons.T.c("primary"), 22))
        ic.setFixedSize(26, 26)
        lay.addWidget(ic)
        v = QVBoxLayout()
        v.setSpacing(1)
        name = label(os.path.basename(path), "RecentName")
        folder = label(os.path.dirname(path), "Faint")
        folder.setMaximumWidth(460)
        v.addWidget(name)
        v.addWidget(folder)
        lay.addLayout(v, 1)
        lay.addWidget(label(ago(ts), "Faint"))
        self.setMinimumHeight(54)
        self.setToolTip(path)
        exists = os.path.exists(path)
        self.setEnabled(exists)
        if not exists:
            folder.setText("文件已不存在 · " + os.path.dirname(path))


class WelcomePage(QWidget):
    openRequested = Signal()
    openPath = Signal(str)
    convertRequested = Signal()
    mergeRequested = Signal()
    imagesRequested = Signal()
    clearRecent = Signal()
    helpRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Welcome")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        h = QHBoxLayout(page)
        h.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(820)
        col.setMinimumWidth(560)
        h.addWidget(col, 6)
        h.addStretch(1)
        v = QVBoxLayout(col)
        v.setContentsMargins(24, 48, 24, 32)
        v.setSpacing(18)

        head = QHBoxLayout()
        head.setSpacing(16)
        self.logo = Logo(52)
        head.addWidget(self.logo)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(label("PDF 编辑器", "WelcomeTitle"))
        tv.addWidget(label("改文字、加批注、签名、整理页面、格式转换 —— 都在一个窗口里完成。", "WelcomeSub"))
        head.addLayout(tv, 1)
        v.addLayout(head)
        v.addSpacing(6)

        grid = QGridLayout()
        grid.setSpacing(14)
        # 拖放区
        self.drop = QFrame()
        self.drop.setObjectName("DropZone")
        self.drop.setMinimumHeight(230)
        dl = QVBoxLayout(self.drop)
        dl.setAlignment(Qt.AlignCenter)
        dl.setSpacing(10)
        di = QLabel()
        di.setAlignment(Qt.AlignCenter)
        di.setPixmap(icons.pixmap("drop", icons.T.c("primary"), 40))
        self.drop_icon = di
        dl.addWidget(di)
        t1 = label("把 PDF 或图片拖到这里", "DropTitle")
        t1.setAlignment(Qt.AlignCenter)
        dl.addWidget(t1)
        t2 = label("图片会自动转成 PDF", "Faint")
        t2.setAlignment(Qt.AlignCenter)
        dl.addWidget(t2)
        dl.addSpacing(4)
        btn = QPushButton("  打开文件")
        btn.setObjectName("Primary")
        btn.setProperty("big", True)
        icons.bind(btn, "open", "on_primary", size=18)
        btn.setIconSize(QSize(18, 18))
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(self.openRequested.emit)
        btn.setMinimumHeight(40)
        btn.setMinimumWidth(160)
        dl.addWidget(btn, 0, Qt.AlignCenter)
        kb = label("Ctrl + O", "Kbd")
        dl.addWidget(kb, 0, Qt.AlignCenter)
        grid.addWidget(self.drop, 0, 0, 3, 1)
        # 快捷入口
        self.quick_icons = []
        for r, (ic, title, sub, sig) in enumerate((
                ("convert", "格式转换", "PDF ⇄ Word / Excel / PPT / 图片", self.convertRequested),
                ("merge", "合并 PDF", "把多个 PDF / 图片合成一个文件", self.mergeRequested),
                ("image", "图片转 PDF", "照片、扫描图片一键变成 PDF", self.imagesRequested))):
            b = QPushButton()
            b.setObjectName("QuickCard")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            b.setMinimumSize(250, 68)
            bl = QHBoxLayout(b)
            bl.setContentsMargins(16, 10, 16, 10)
            bl.setSpacing(14)
            icl = QPushButton()
            icl.setObjectName("HeadIcon")
            icl.setFixedSize(38, 38)
            icl.setIconSize(QSize(20, 20))
            icl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            icons.bind(icl, ic, "primary", size=20)
            bl.addWidget(icl)
            tv = QVBoxLayout()
            tv.setSpacing(1)
            tv.addWidget(label(title, "QuickTitle"))
            tv.addWidget(label(sub, "Faint"))
            bl.addLayout(tv, 1)
            b.clicked.connect(sig.emit)
            grid.addWidget(b, r, 1)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        v.addLayout(grid)

        # 最近打开
        self.recent_card = QFrame()
        self.recent_card.setObjectName("Card")
        rl = QVBoxLayout(self.recent_card)
        rl.setContentsMargins(10, 12, 10, 10)
        rl.setSpacing(2)
        rh = QHBoxLayout()
        rh.setContentsMargins(8, 0, 4, 4)
        rh.addWidget(label("最近打开", "PanelTitle"))
        rh.addStretch(1)
        clr = QPushButton("清除记录")
        clr.setObjectName("Ghost")
        clr.setCursor(Qt.PointingHandCursor)
        clr.clicked.connect(self.clearRecent.emit)
        rh.addWidget(clr)
        rl.addLayout(rh)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(0)
        rl.addLayout(self.recent_box)
        v.addWidget(self.recent_card)

        tips = QHBoxLayout()
        tips.addStretch(1)
        hb = QPushButton("使用说明")
        hb.setObjectName("Ghost")
        icons.bind(hb, "help", size=16)
        hb.setCursor(Qt.PointingHandCursor)
        hb.clicked.connect(self.helpRequested.emit)
        tips.addWidget(hb)
        tips.addStretch(1)
        v.addLayout(tips)
        v.addStretch(1)

    def set_recent(self, items):
        while self.recent_box.count():
            w = self.recent_box.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for path, ts in items[:8]:
            row = RecentRow(path, ts)
            row.clicked.connect(lambda _c=False, p=path: self.openPath.emit(p))
            self.recent_box.addWidget(row)
        self.recent_card.setVisible(bool(items))

    def set_drop_active(self, on: bool):
        self.drop.setProperty("active", on)
        self.drop.style().unpolish(self.drop)
        self.drop.style().polish(self.drop)

    def retheme(self):
        self.logo.refresh()
        self.drop_icon.setPixmap(icons.pixmap("drop", icons.T.c("primary"), 40))
