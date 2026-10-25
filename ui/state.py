"""编辑状态:当前工具、选中的对象、各类对象的默认样式、当前页。和文档内容无关,不进撤销历史。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

import engine as E

# 同一类的工具在工具栏里共用一个按钮(显示最近用过的那个)
TOOL_GROUPS = {
    "markup": ["highlight", "underline", "strike"],
    "shape": ["rect", "ellipse", "line", "arrow"],
    "cover": ["whiteout", "redact"],
}
GROUP_OF_TOOL = {t: g for g, ts in TOOL_GROUPS.items() for t in ts}

TOOL_LABELS = {
    "select": "选择", "edit": "编辑文字", "text": "添加文字", "form": "填写表单",
    "highlight": "高亮", "underline": "下划线", "strike": "删除线",
    "rect": "矩形", "ellipse": "椭圆", "line": "直线", "arrow": "箭头",
    "ink": "画笔", "whiteout": "白块遮盖", "redact": "涂黑(永久删除)",
}
TOOL_KEYS = {
    "select": "V", "edit": "E", "text": "T", "highlight": "H", "underline": "U", "strike": "K",
    "rect": "R", "ellipse": "O", "line": "L", "arrow": "A", "ink": "P", "whiteout": "W", "redact": "X", "form": "F",
}
TOOL_HINTS = {
    "select": "点击选中对象,拖动移动,拖控制点缩放 · 双击文字重新编辑 · 拖空白处框选多个对象",
    "edit": "点击页面上的文字直接修改 · 拖拽框选可一次修改多行 · 行首退格 / 行尾 Delete 可跨行编辑",
    "text": "点击页面任意位置开始输入 · Esc 或点击空白处完成",
    "form": "点击蓝色方框填写:文本框直接输入,复选框、单选框点一下,下拉框选择",
    "highlight": "像选中文字一样从头拖到尾,即可高亮这段文字",
    "underline": "像选中文字一样从头拖到尾,即可添加下划线",
    "strike": "像选中文字一样从头拖到尾,即可添加删除线",
    "rect": "拖动绘制矩形 · 按住 Shift 画正方形",
    "ellipse": "拖动绘制椭圆 · 按住 Shift 画正圆",
    "line": "拖动绘制直线 · 按住 Shift 锁定 45° 角",
    "arrow": "拖动绘制箭头 · 按住 Shift 锁定 45° 角",
    "ink": "按住左键自由书写,适合手写签名",
    "whiteout": "拖出矩形盖住内容(只是盖住,之后还能移动或删除)",
    "redact": "拖出矩形,保存时其中的文字和图片会被永久删除",
}

PALETTE = ["#000000", "#E5484D", "#F76B15", "#FFC53D", "#30A46C", "#0090FF", "#6E56CF", "#FFFFFF"]
MARKUP_PALETTE = ["#FFE066", "#A3E635", "#5EEAD4", "#7DD3FC", "#F9A8D4", "#E5484D", "#0090FF", "#1F2328"]


def style_group(ov) -> str | None:
    """对象属于哪一类样式(决定属性面板显示哪些设置)。"""
    k = ov.kind
    if k == "text":
        return "text"
    if k == "markup":
        return ov.props["sub"]
    if k == "ink":
        return "ink"
    if k in ("rect", "ellipse", "line", "arrow"):
        return "shape"
    return None


def tool_group(tool: str) -> str | None:
    if tool in ("edit", "text"):
        return "text"
    if tool in ("highlight", "underline", "strike", "ink", "whiteout"):
        return tool
    if tool in ("rect", "ellipse", "line", "arrow"):
        return "shape"
    return None


class EditorState(QObject):
    toolChanged = Signal(str)
    selectionChanged = Signal()
    styleChanged = Signal(str)
    currentPageChanged = Signal(int)
    pageSelectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tool = "select"
        self.variant = {g: ts[0] for g, ts in TOOL_GROUPS.items()}
        self.selection: list[int] = []
        self.current_page = 0
        self.page_sel: set[int] = set()
        self.clipboard: list = []
        self.styles = {
            "text": dict(font=E.default_font_key(), size=14.0, color="#000000", bold=False, italic=False),
            "shape": dict(stroke="#E5484D", width=2.0, fill=None, opacity=1.0),
            "ink": dict(stroke="#1B3A8C", width=2.0),
            "highlight": dict(color="#FFE066"),
            "underline": dict(color="#E5484D"),
            "strike": dict(color="#E5484D"),
            "whiteout": dict(fill="#FFFFFF"),
        }

    # ---- 工具
    def set_tool(self, tool: str):
        g = GROUP_OF_TOOL.get(tool)
        if g:
            self.variant[g] = tool
        if tool != self.tool:
            self.tool = tool
        self.toolChanged.emit(tool)

    # ---- 选中
    def select(self, uids):
        uids = list(dict.fromkeys(uids))
        if uids != self.selection:
            self.selection = uids
            self.selectionChanged.emit()

    def clear_selection(self):
        self.select([])

    # ---- 页面
    def set_current_page(self, pno: int):
        if pno != self.current_page:
            self.current_page = pno
            self.currentPageChanged.emit(pno)

    def set_page_selection(self, pages):
        pages = set(pages)
        if pages != self.page_sel:
            self.page_sel = pages
            self.pageSelectionChanged.emit()

    def target_pages(self, n: int) -> list:
        """页面操作的对象:缩略图里多选的页;没有多选就是当前页。"""
        idxs = sorted(i for i in self.page_sel if 0 <= i < n)
        if len(idxs) > 1 or (idxs and idxs[0] == self.current_page):
            return idxs
        return [min(self.current_page, max(n - 1, 0))]

    # ---- 样式
    def set_style(self, group: str, key: str, value):
        st = self.styles.setdefault(group, {})
        if st.get(key) != value:
            st[key] = value
            self.styleChanged.emit(group)
