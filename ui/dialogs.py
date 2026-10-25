"""对话框:参数输入、确认、提示、密码、使用说明。统一的卡片式外观。"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QTextBrowser, QVBoxLayout, QWidget)

from ui import icons
from ui.theme import T

APP = "PDF 编辑器"


class CardDialog(QDialog):
    def __init__(self, parent, title: str, subtitle: str = "", width: int = 440):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(26, 22, 26, 20)
        self.lay.setSpacing(14)
        head = QLabel(title)
        head.setObjectName("DialogTitle")
        self.lay.addWidget(head)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("Muted")
            sub.setWordWrap(True)
            self.lay.addWidget(sub)
        self.content = QVBoxLayout()
        self.content.setSpacing(10)
        self.lay.addLayout(self.content)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        self.buttons.addStretch(1)
        self.lay.addSpacing(4)
        self.lay.addLayout(self.buttons)

    def add_button(self, text, role="ghost", slot=None):
        b = QPushButton(text)
        b.setObjectName({"primary": "Primary", "danger": "DangerFill", "ghost": ""}.get(role, ""))
        b.setMinimumWidth(84)
        if slot:
            b.clicked.connect(slot)
        self.buttons.addWidget(b)
        if role in ("primary", "danger"):
            b.setDefault(True)
            b.setAutoDefault(True)
        else:
            b.setAutoDefault(False)
        return b


class ParamDialog(CardDialog):
    """按字段描述生成的参数对话框。

    fields: [{'key','label','kind'('str'|'int'|'float'|'choice'|'color'|'password'|'bool'),'default',
              'choices','min','max','step','hint'}]"""

    def __init__(self, parent, title, fields, ok_text="确定", note=None):
        super().__init__(parent, title, note or "")
        self.fields = fields
        self.widgets = {}
        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        first = None
        for f in fields:
            kind = f.get("kind", "str")
            if kind == "choice":
                w = QComboBox()
                w.addItems([str(c) for c in f["choices"]])
                w.setCurrentText(str(f.get("default", f["choices"][0])))
            elif kind == "color":
                w = QPushButton()
                w.setObjectName("ColorButton")
                w.color = f.get("default", "#888888")
                w.setFixedSize(64, 30)
                self._paint_color(w)
                w.clicked.connect(lambda _c=False, ww=w: self._pick(ww))
            elif kind in ("int", "float"):
                w = QSpinBox() if kind == "int" else QDoubleSpinBox()
                w.setRange(f.get("min", 0), f.get("max", 99999))
                w.setSingleStep(f.get("step", 1))
                w.setValue(f.get("default", 0))
                w.setMinimumWidth(110)
            elif kind == "bool":
                w = QCheckBox(f.get("text", ""))
                w.setChecked(bool(f.get("default")))
            else:
                w = QLineEdit(str(f.get("default", "")))
                if kind == "password":
                    w.setEchoMode(QLineEdit.Password)
                w.setMinimumWidth(240)
            self.widgets[f["key"]] = w
            first = first or w
            if f.get("hint"):
                box = QWidget()
                v = QVBoxLayout(box)
                v.setContentsMargins(0, 0, 0, 0)
                v.setSpacing(3)
                v.addWidget(w)
                h = QLabel(f["hint"])
                h.setObjectName("Faint")
                h.setWordWrap(True)
                v.addWidget(h)
                form.addRow(f["label"], box)
            else:
                form.addRow(f["label"], w)
        self.content.addLayout(form)
        self.add_button("取消", "ghost", self.reject)
        self.add_button(ok_text, "primary", self.accept)
        if first is not None:
            first.setFocus()

    def _paint_color(self, w):
        w.setStyleSheet(f"QPushButton#ColorButton {{ background: {w.color}; border: 1px solid {T.c('border_strong')};"
                        f" border-radius: 8px; }}")

    def _pick(self, w):
        c = QColorDialog.getColor(QColor(w.color), self, "选择颜色")
        if c.isValid():
            w.color = c.name()
            self._paint_color(w)

    def values(self):
        out = {}
        for f in self.fields:
            w = self.widgets[f["key"]]
            kind = f.get("kind", "str")
            if kind == "choice":
                out[f["key"]] = w.currentText()
            elif kind == "color":
                out[f["key"]] = w.color
            elif kind in ("int", "float"):
                out[f["key"]] = w.value()
            elif kind == "bool":
                out[f["key"]] = w.isChecked()
            else:
                out[f["key"]] = w.text()
        return out

    @classmethod
    def ask(cls, parent, title, fields, **kw):
        dlg = cls(parent, title, fields, **kw)
        ok = dlg.exec() == QDialog.Accepted
        vals = dlg.values() if ok else None
        dlg.deleteLater()
        return vals


class MessageDialog(CardDialog):
    def __init__(self, parent, title, text, kind="info", buttons=(("确定", "primary", "ok"),), detail=""):
        super().__init__(parent, title, "", width=420)
        row = QHBoxLayout()
        row.setSpacing(14)
        ic = QPushButton()
        ic.setObjectName("MsgIcon")
        ic.setProperty("kind", kind)
        ic.setFixedSize(40, 40)
        ic.setIconSize(QSize(22, 22))
        ic.setFocusPolicy(Qt.NoFocus)
        col = {"info": "primary", "warn": "warning", "error": "danger", "ask": "primary", "danger": "danger"}[kind]
        ic.setIcon(icons.icon({"info": "info", "warn": "info", "error": "close", "ask": "help",
                               "danger": "trash"}[kind], T.c(col)))
        row.addWidget(ic, 0, Qt.AlignTop)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lab.setMinimumWidth(300)
        v = QVBoxLayout()
        v.addWidget(lab)
        if detail:
            d = QLabel(detail)
            d.setObjectName("Faint")
            d.setWordWrap(True)
            d.setTextInteractionFlags(Qt.TextSelectableByMouse)
            v.addWidget(d)
        row.addLayout(v, 1)
        self.content.addLayout(row)
        self.result_key = None
        for text_, role, key in buttons:
            self.add_button(text_, role, lambda _c=False, k=key: self._done(k))

    def _done(self, key):
        self.result_key = key
        self.accept()


def message(parent, text, kind="info", title=APP, detail=""):
    dlg = MessageDialog(parent, title, text, kind, detail=detail)
    dlg.exec()
    dlg.deleteLater()


def info(parent, text, title=APP):
    message(parent, text, "info", title)


def warn(parent, text, title=APP, detail=""):
    message(parent, text, "warn", title, detail)


def error(parent, text, title=APP, detail=""):
    message(parent, text, "error", title, detail)


def confirm(parent, text, ok="确定", title=APP, danger=False) -> bool:
    dlg = MessageDialog(parent, title, text, "danger" if danger else "ask",
                        buttons=(("取消", "ghost", "cancel"), (ok, "danger" if danger else "primary", "ok")))
    dlg.exec()
    r = dlg.result_key == "ok"
    dlg.deleteLater()
    return r


def ask_save(parent, name) -> str:
    """'save' / 'discard' / 'cancel'"""
    dlg = MessageDialog(parent, "保存修改?", f"「{name}」有未保存的修改。要在关闭前保存吗?", "ask",
                        buttons=(("不保存", "ghost", "discard"), ("取消", "ghost", "cancel"), ("保存", "primary", "save")))
    dlg.exec()
    r = dlg.result_key or "cancel"
    dlg.deleteLater()
    return r


def ask_password(parent, text, title="需要密码"):
    dlg = CardDialog(parent, title, text, width=400)
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.Password)
    edit.setPlaceholderText("密码")
    dlg.content.addWidget(edit)
    dlg.add_button("取消", "ghost", dlg.reject)
    dlg.add_button("打开", "primary", dlg.accept)
    edit.setFocus()
    ok = dlg.exec() == QDialog.Accepted
    pw = edit.text() if ok else None
    dlg.deleteLater()
    return pw


HELP_HTML = """
<h3>改 PDF 里原有的文字</h3>
<p>选 <b>编辑文字</b>(E)→ 点一下要改的那一行 → 直接打字 → <span class=k>Esc</span> 或点空白处完成。<br>
像在 Word 里一样跨行编辑:行首按 <span class=k>退格</span> 会接到上一行继续往上删;行尾按 <span class=k>Delete</span>
会把下一行接上来;<span class=k>↑</span> <span class=k>↓</span> 直接移到上一行 / 下一行接着改。<br>
拖拽框选可以一次改好几行。保存时原来的文字会被真正删掉,换成你改好的。</p>
<h3>添加内容</h3>
<p><b>添加文字</b>(T):点一下就能打字;拖出一个框则是自动换行的文字框。<br>
<b>标记</b>:高亮(H)/ 下划线(U)/ 删除线(K),像选文字一样拖过去。<br>
<b>画笔</b>(P)适合手写签名;<b>形状</b>:矩形(R)/ 椭圆(O)/ 直线(L)/ 箭头(A),按住 Shift 画正方形、正圆、45° 线。<br>
<b>插入图片</b>(I):也可以直接把图片文件拖到页面上。<br>
<b>白块</b>(W)只是盖住内容;<b>涂黑</b>(X)保存时会永久删除下面的文字和图片。</p>
<h3>修改已添加的内容</h3>
<p>用 <b>选择</b>(V)点一下选中:拖动移动(拖到别的页也可以)、拖控制点缩放、<span class=k>Delete</span> 删除、
方向键微调(Shift 一次 10 点)、<span class=k>Ctrl+C</span> / <span class=k>Ctrl+V</span> 复制粘贴、
<span class=k>Ctrl+D</span> 复制一份。拖空白处可以框选多个对象。双击文字重新编辑。右侧面板可以改颜色、字号、线宽等。</p>
<h3>页面</h3>
<p>左侧缩略图:点击跳转,Ctrl / Shift 多选,拖动调整顺序,右键旋转、删除、插入、提取。把 PDF 或图片拖进缩略图栏就能插入到那个位置。</p>
<h3>浏览</h3>
<p>所有页面连续排列,直接滚动。<span class=k>Ctrl+滚轮</span> 缩放,按住 <span class=k>空格</span> 或鼠标中键拖动画面。
<span class=k>Ctrl+F</span> 查找文字,<span class=k>Ctrl+Z</span> / <span class=k>Ctrl+Y</span> 撤销 / 重做。</p>
<h3>扫描件</h3>
<p>整页是图片的 PDF:先用 <b>文字识别 OCR</b>,每页会多出一层看不见的文字,可以搜索、复制,也能用「编辑文字」修改——
修改时会自动用背景色盖住扫描图里的原字。OCR 使用 Windows 自带的识别引擎。</p>
<h3>表单</h3>
<p>有可填写表单域的 PDF:选 <b>填写表单</b>(F),蓝色方框就是表单域。中文内容会直接写在页面上。</p>
<h3>格式转换</h3>
<p>PDF → Word / Excel / PowerPoint / 图片 / 纯文本 / 网页;Word / Excel / PPT / 图片 / 文本 → PDF。可以一次转换多个文件。</p>
"""


def show_help(parent):
    dlg = CardDialog(parent, "使用说明", "", width=620)
    dlg.resize(680, 640)
    tb = QTextBrowser()
    tb.setOpenExternalLinks(True)
    c = T.colors
    tb.document().setDefaultStyleSheet(
        f"h3 {{ color: {c['text']}; font-size: 14px; margin: 14px 0 4px 0; }}"
        f"p {{ color: {c['text2']}; line-height: 150%; margin: 0; }}"
        f"b {{ color: {c['text']}; }}"
        f".k {{ background: {c['surface2']}; color: {c['text']}; }}")
    tb.setHtml(HELP_HTML)
    dlg.content.addWidget(tb, 1)
    dlg.add_button("知道了", "primary", dlg.accept)
    dlg.exec()
    dlg.deleteLater()


def show_about(parent):
    message(parent, "PDF 编辑器\n\n编辑文字、添加批注与图形、管理页面、文字识别、格式转换。\n基于 Python · PyMuPDF · Qt。", "info", "关于")
