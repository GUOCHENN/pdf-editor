"""界面上反复用到的小组件:工具按钮、颜色选择、分段按钮、浮动提示、搜索框、缩放控件……"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QColorDialog, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ui import icons
from ui.state import GROUP_OF_TOOL, TOOL_GROUPS, TOOL_KEYS, TOOL_LABELS
from ui.theme import T

TOOL_ICON = {"select": "select", "edit": "edit_text", "text": "add_text", "form": "form", "highlight": "highlight",
             "underline": "underline", "strike": "strike", "rect": "rect", "ellipse": "ellipse", "line": "line",
             "arrow": "arrow", "ink": "pen", "whiteout": "whiteout", "redact": "redact", "image": "image"}


def icon_button(name: str, tip: str = "", size: int = 34, icon_size: int = 20, checkable=False, obj="Tool"):
    b = QToolButton()
    b.setObjectName(obj)
    icons.bind(b, name, size=icon_size)
    b.setIconSize(QSize(icon_size, icon_size))
    b.setFixedSize(size, size)
    b.setCheckable(checkable)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.setFocusPolicy(Qt.NoFocus)
    return b


def divider(vertical=True) -> QFrame:
    f = QFrame()
    f.setObjectName("Divider" if vertical else "HDivider")
    if vertical:
        f.setFixedWidth(1)
    else:
        f.setFixedHeight(1)
    return f


def label(text: str, obj: str | None = None, wrap=False) -> QLabel:
    lab = QLabel(text)
    if obj:
        lab.setObjectName(obj)
    lab.setWordWrap(wrap)
    return lab


# ============================================================================
# 工具栏
# ============================================================================


class ToolButton(QToolButton):
    """工具按钮。同类工具(形状、标记、遮盖)共用一个按钮:右下角有小三角,再点一次或右键选择具体工具。"""
    variantPicked = Signal(str)

    def __init__(self, tool: str, group: str | None = None):
        super().__init__()
        self.group = group
        self.tool = tool
        self.setObjectName("Tool")
        self.setCheckable(True)
        self.setFixedSize(36, 36)
        self.setIconSize(QSize(20, 20))
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.set_tool(tool)

    def set_tool(self, tool: str):
        self.tool = tool
        icons.bind(self, TOOL_ICON[tool])
        tip = f"{TOOL_LABELS[tool]}  ({TOOL_KEYS[tool]})"
        if self.group:
            tip += "\n再点一次或右键:选择其它" + {"shape": "形状", "markup": "标记", "cover": "遮盖方式"}[self.group]
        self.setToolTip(tip)

    def variant_menu(self):
        menu = QMenu(self)
        for t in TOOL_GROUPS[self.group]:
            act = menu.addAction(icons.icon(TOOL_ICON[t]), f"{TOOL_LABELS[t]}\t{TOOL_KEYS[t]}")
            act.setCheckable(True)
            act.setChecked(t == self.tool)
            act.triggered.connect(lambda _c=False, tt=t: self.variantPicked.emit(tt))
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))

    def contextMenuEvent(self, e):
        if self.group:
            self.variant_menu()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.group:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(T.q("primary" if self.isChecked() else "text3"))
            w, h = self.width(), self.height()
            from PySide6.QtGui import QPolygonF
            from PySide6.QtCore import QPointF
            p.drawPolygon(QPolygonF([QPointF(w - 5, h - 9), QPointF(w - 5, h - 5), QPointF(w - 9, h - 5)]))
            p.end()


class ToolPalette(QWidget):
    imageRequested = Signal()

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ToolPalette")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 3, 4, 3)
        lay.setSpacing(2)
        self.buttons: dict[str, ToolButton] = {}
        layout_spec = [["select", "edit", "text"], ["markup", "ink", "shape"], ["image", "cover"], ["form"]]
        for gi, grp in enumerate(layout_spec):
            if gi:
                d = divider()
                d.setFixedHeight(20)
                lay.addWidget(d)
                lay.addSpacing(2)
            for key in grp:
                if key == "image":
                    b = icon_button("image", "插入图片 / 签名  (I)", size=36)
                    b.clicked.connect(self.imageRequested.emit)
                    lay.addWidget(b)
                    continue
                if key in TOOL_GROUPS:
                    b = ToolButton(state.variant[key], key)
                    b.variantPicked.connect(state.set_tool)
                else:
                    b = ToolButton(key)
                b.clicked.connect(lambda _c=False, btn=b: self._clicked(btn))
                self.buttons[key] = b
                lay.addWidget(b)
        state.toolChanged.connect(self.sync)
        self.sync(state.tool)

    def _clicked(self, btn: ToolButton):
        was = self.state.tool == btn.tool
        if btn.group and was:
            btn.setChecked(True)
            btn.variant_menu()
            return
        self.state.set_tool(btn.tool)

    def sync(self, tool: str):
        g = GROUP_OF_TOOL.get(tool)
        for key, b in self.buttons.items():
            if key in TOOL_GROUPS:
                if g == key and b.tool != tool:
                    b.set_tool(tool)
                b.setChecked(g == key)
            else:
                b.setChecked(key == tool)


# ============================================================================
# 颜色
# ============================================================================


class Swatch(QAbstractButton):
    def __init__(self, color: str | None, size=22):
        super().__init__()
        self.color = color
        self.setCheckable(True)
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.setToolTip(color.upper() if color else "无")
        self._hover = False

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        if self.isChecked() or self._hover:
            pen = QPen(T.q("primary") if self.isChecked() else T.q("border_strong"), 2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        if self.color:
            p.setPen(QPen(T.q("border_strong"), 1))
            p.setBrush(QColor(self.color))
            p.drawEllipse(r)
        else:  # "无":白底红斜线
            p.setPen(QPen(T.q("border_strong"), 1))
            p.setBrush(QColor("#FFFFFF"))
            p.drawEllipse(r)
            p.setPen(QPen(T.q("danger"), 1.6))
            p.drawLine(r.topRight() + (r.center() - r.topRight()) * 0.3, r.bottomLeft() + (r.center() - r.bottomLeft()) * 0.3)
        p.end()


class ColorRow(QWidget):
    """一排颜色圆点 + 自定义颜色。picked(颜色字符串或 None)。"""
    picked = Signal(object)

    def __init__(self, palette, allow_none=False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(False)
        self.swatches = []
        cols = ([None] if allow_none else []) + list(palette)
        for c in cols:
            s = Swatch(c)
            s.clicked.connect(lambda _c=False, col=c: self._pick(col))
            lay.addWidget(s)
            self.swatches.append(s)
        self.custom = Swatch("#FFFFFF")
        self.custom.setToolTip("更多颜色…")
        self.custom.clicked.connect(self._more)
        self.custom.paintEvent = self._paint_custom
        lay.addWidget(self.custom)
        lay.addStretch(1)
        self.value = None

    def _paint_custom(self, e):
        s = self.custom
        p = QPainter(s)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(s.rect()).adjusted(3, 3, -3, -3)
        if s.isChecked():
            p.setPen(QPen(T.q("primary"), 2))
            p.drawEllipse(QRectF(s.rect()).adjusted(1, 1, -1, -1))
        from PySide6.QtGui import QConicalGradient
        g = QConicalGradient(r.center(), 90)
        for i, c in enumerate(["#FF5A5F", "#FFC53D", "#30A46C", "#0090FF", "#6E56CF", "#FF5A5F"]):
            g.setColorAt(i / 5, QColor(c))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(r)
        if s.isChecked() and s.color:
            p.setBrush(QColor(s.color))
            p.setPen(QPen(QColor("#FFFFFF"), 1.5))
            p.drawEllipse(r.adjusted(4, 4, -4, -4))
        p.end()

    def _pick(self, col):
        self.set_value(col)
        self.picked.emit(col)

    def _more(self):
        cur = QColor(self.value or "#000000")
        c = QColorDialog.getColor(cur, self.window(), "选择颜色")
        if c.isValid():
            self._pick(c.name().upper())
        else:
            self.set_value(self.value)

    def set_value(self, col):
        self.value = col
        found = False
        for s in self.swatches:
            on = (s.color or None) == (col or None) if col is None or s.color is None else \
                s.color.lower() == col.lower()
            s.setChecked(on and not found)
            found = found or on
        self.custom.setChecked(not found and col is not None)
        self.custom.color = col if not found else None
        self.custom.update()


# ============================================================================
# 分段按钮
# ============================================================================


class Segmented(QWidget):
    changed = Signal(str)

    def __init__(self, items, parent=None):
        """items: [(key, 文字, 图标名或 None)]"""
        super().__init__(parent)
        self.setObjectName("Segmented")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.btns = {}
        for key, text, ic in items:
            b = QToolButton()
            b.setObjectName("SegButton")
            b.setCheckable(True)
            b.setText(text)
            b.setFocusPolicy(Qt.NoFocus)
            b.setCursor(Qt.PointingHandCursor)
            if ic:
                icons.bind(b, ic, size=16)
                b.setIconSize(QSize(16, 16))
                b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.clicked.connect(lambda _c=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.btns[key] = b
            lay.addWidget(b)

    def set_value(self, key):
        b = self.btns.get(key)
        if b:
            b.setChecked(True)


# ============================================================================
# 浮动提示
# ============================================================================


class Toast(QFrame):
    """画面底部中间的浮动提示,几秒后淡出。可以带几个按钮(比如转换完成后的"打开文件")。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 9, 10, 9)
        lay.setSpacing(4)
        self.label = QLabel()
        self.label.setObjectName("ToastText")
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.lay = lay
        self.buttons: list[QPushButton] = []
        self.text = ""
        self.fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fx)
        self.anim = QPropertyAnimation(self.fx, b"opacity", self)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.timer = QTimer(self, singleShot=True, timeout=self._fade)
        self.hide()

    def show_message(self, text: str, ms: int = 3200, actions=()):
        """actions: [(按钮文字, 点击后执行的函数)]"""
        self.text = text
        for b in self.buttons:
            b.deleteLater()
        self.buttons = []
        for label_text, fn in actions:
            b = QPushButton(label_text)
            b.setObjectName("ToastButton")
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, f=fn: (self.hide(), f()))
            self.lay.addWidget(b)
            self.buttons.append(b)
        # 没有按钮时不挡住下面的页面;有按钮时要能点
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not actions)
        self.label.setText(text)
        maxw = min(720, max(self.parentWidget().width() - 80, 240))
        btn_w = sum(b.sizeHint().width() + 4 for b in self.buttons)
        natural = self.label.fontMetrics().horizontalAdvance(text) + 4
        lw = max(min(natural, maxw - btn_w - 28), 120)
        self.label.setFixedWidth(lw)
        self.label.setFixedHeight(self.label.heightForWidth(lw) if natural > lw else self.label.sizeHint().height())
        self.adjustSize()
        self._place()
        self.anim.stop()
        self.fx.setOpacity(0.0)
        self.show()
        self.raise_()
        self.anim.setDuration(160)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(1.0)
        self.anim.start()
        self.timer.start(ms)

    def _place(self):
        pw = self.parentWidget()
        self.move((pw.width() - self.width()) // 2, pw.height() - self.height() - 28)

    def _fade(self):
        self.anim.stop()
        self.anim.setDuration(400)
        self.anim.setStartValue(self.fx.opacity())
        self.anim.setEndValue(0.0)
        self.anim.start()
        self.anim.finished.connect(self._hide_once)

    def _hide_once(self):
        try:
            self.anim.finished.disconnect(self._hide_once)
        except (RuntimeError, TypeError):
            pass
        if self.fx.opacity() < 0.05:
            self.hide()


# ============================================================================
# 搜索框
# ============================================================================


class SearchBox(QWidget):
    search = Signal(str)
    step = Signal(int)
    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.edit = QLineEdit()
        self.edit.setObjectName("Search")
        self.edit.setPlaceholderText("查找文字  Ctrl+F")
        self.edit.setClearButtonEnabled(True)
        self.act = QAction(self.edit)
        icons.bind(self.act, "search", "text3", size=16)
        self.edit.addAction(self.act, QLineEdit.LeadingPosition)
        self.edit.returnPressed.connect(self._enter)
        self.edit.textChanged.connect(self._changed)
        lay.addWidget(self.edit)
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        self.count.setMinimumWidth(0)
        lay.addWidget(self.count)
        self.prev = icon_button("chevron_left", "上一个  Shift+Enter", size=28, icon_size=16)
        self.next = icon_button("chevron_right", "下一个  Enter", size=28, icon_size=16)
        self.prev.clicked.connect(lambda: self.step.emit(-1))
        self.next.clicked.connect(lambda: self.step.emit(1))
        lay.addWidget(self.prev)
        lay.addWidget(self.next)
        self.set_count(None)
        self._last = ""

    def _enter(self):
        from PySide6.QtWidgets import QApplication
        back = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        text = self.edit.text()
        if text and text == self._last:
            self.step.emit(-1 if back else 1)
        else:
            self._last = text
            self.search.emit(text)

    def _changed(self, text):
        if not text:
            self._last = ""
            self.cleared.emit()

    def set_count(self, info):
        """info: None(不显示)/ (当前序号, 总数)"""
        if info is None:
            self.count.setText("")
            self.count.hide()
            self.prev.hide()
            self.next.hide()
            return
        cur, total = info
        self.count.setText("无结果" if total == 0 else f"{cur} / {total}")
        self.count.show()
        self.prev.setVisible(total > 0)
        self.next.setVisible(total > 0)

    def focus(self):
        self.edit.setFocus(Qt.ShortcutFocusReason)
        self.edit.selectAll()


# ============================================================================
# 状态栏控件
# ============================================================================


class ZoomControl(QWidget):
    zoomTo = Signal(float)
    zoomStep = Signal(int)
    fit = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        out = icon_button("zoom_out", "缩小  Ctrl+-", size=28, icon_size=16)
        out.clicked.connect(lambda: self.zoomStep.emit(-1))
        lay.addWidget(out)
        self.btn = QToolButton()
        self.btn.setObjectName("TextButton")
        self.btn.setText("100%")
        self.btn.setMinimumWidth(62)
        self.btn.setPopupMode(QToolButton.InstantPopup)
        self.btn.setFocusPolicy(Qt.NoFocus)
        self.btn.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self)
        for z in (50, 75, 100, 125, 150, 200, 300, 400):
            menu.addAction(f"{z}%", lambda zz=z: self.zoomTo.emit(zz / 100))
        menu.addSeparator()
        menu.addAction(icons.icon("fit_width"), "适合宽度\tCtrl+9", lambda: self.fit.emit("width"))
        menu.addAction(icons.icon("fit_page"), "适合整页\tCtrl+8", lambda: self.fit.emit("page"))
        self.btn.setMenu(menu)
        lay.addWidget(self.btn)
        zin = icon_button("zoom_in", "放大  Ctrl++", size=28, icon_size=16)
        zin.clicked.connect(lambda: self.zoomStep.emit(1))
        lay.addWidget(zin)
        lay.addSpacing(4)
        fw = icon_button("fit_width", "适合宽度  Ctrl+9", size=28, icon_size=16)
        fw.clicked.connect(lambda: self.fit.emit("width"))
        fp = icon_button("fit_page", "适合整页  Ctrl+8", size=28, icon_size=16)
        fp.clicked.connect(lambda: self.fit.emit("page"))
        lay.addWidget(fw)
        lay.addWidget(fp)

    def set_zoom(self, z):
        self.btn.setText(f"{round(z * 100)}%")


class PageNav(QWidget):
    goto = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.prev = icon_button("chevron_left", "上一页  PageUp", size=28, icon_size=16)
        self.next = icon_button("chevron_right", "下一页  PageDown", size=28, icon_size=16)
        self.edit = QLineEdit("0")
        self.edit.setObjectName("PageBox")
        self.edit.setFixedWidth(44)
        self.edit.setAlignment(Qt.AlignCenter)
        self.total = QLabel("/ 0")
        self.total.setObjectName("Muted")
        self.prev.clicked.connect(lambda: self.goto.emit(self.cur - 1))
        self.next.clicked.connect(lambda: self.goto.emit(self.cur + 1))
        self.edit.returnPressed.connect(self._enter)
        lay.addWidget(self.prev)
        lay.addWidget(self.edit)
        lay.addWidget(self.total)
        lay.addSpacing(2)
        lay.addWidget(self.next)
        self.cur, self.n = 0, 0

    def _enter(self):
        try:
            self.goto.emit(int(self.edit.text()) - 1)
        except ValueError:
            self.set_page(self.cur, self.n)

    def set_page(self, cur, n):
        self.cur, self.n = cur, n
        self.edit.setText(str(cur + 1) if n else "0")
        self.total.setText(f"/ {n}")
        self.prev.setEnabled(cur > 0)
        self.next.setEnabled(cur < n - 1)


# ============================================================================
# 属性面板的分组
# ============================================================================


class Section(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 10, 16, 6)
        self.lay.setSpacing(8)
        self.title = label(title.upper(), "SectionTitle")
        self.lay.addWidget(self.title)

    def add(self, w):
        self.lay.addWidget(w)
        return w

    def row(self, text: str, w, stretch=True, vertical=False):
        """一行设置:左边文字说明,右边控件;vertical=True 时文字在上、控件在下(颜色这类比较宽的控件)。"""
        box = QWidget()
        h = QVBoxLayout(box) if vertical else QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(5 if vertical else 8)
        lab = label(text, "Muted")
        if not vertical:
            lab.setFixedWidth(52)
        h.addWidget(lab)
        h.addWidget(w, 1 if stretch else 0)
        if not stretch:
            h.addStretch(1)
        self.lay.addWidget(box)
        box.label = lab
        return box


class ActionList(QWidget):
    """属性面板里的一列操作按钮(图标 + 文字)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(1)

    def add(self, icon_name, text, fn, tip="", danger=False):
        b = QPushButton(text)
        b.setObjectName("ListAction")
        if danger:
            b.setProperty("danger", True)
        icons.bind(b, icon_name, "danger" if danger else None, size=18)
        b.setIconSize(QSize(18, 18))
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda _c=False: fn())
        self.lay.addWidget(b)
        return b
