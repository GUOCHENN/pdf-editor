"""工具:每个工具是一个小状态机,接收页面坐标的鼠标事件。

PageView 负责把鼠标事件换算成"第几页、页面上的哪个点"(PageEvent),再交给当前工具。
对象的增删改都通过 model.change / model.push 进行,所以每一步都能撤销。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QLineEdit, QMenu, QPlainTextEdit

import engine as E
from ui.canvas import HANDLE_CURSOR, PreviewItem, handles_for, overlay_sig
from ui.theme import T


@dataclass
class PageEvent:
    pno: int
    x: float
    y: float
    inside: bool
    pos: QPointF        # 视口坐标
    scene: QPointF
    mods: object
    button: object = None

    @property
    def shift(self):
        return bool(self.mods & Qt.ShiftModifier)

    @property
    def ctrl(self):
        return bool(self.mods & Qt.ControlModifier)


# ============================================================================
# 对象操作(工具、菜单、属性面板共用)
# ============================================================================


class Ops:
    @staticmethod
    def delete(v):
        sel = v.selected_overlays()
        if not sel:
            return
        uids = {o.uid for o in sel}
        pages = {o.page for o in sel}
        m = v.model

        def fn():
            m.overlays = [o for o in m.overlays if o.uid not in uids]
        m.change("删除对象" if len(sel) == 1 else f"删除 {len(sel)} 个对象", fn, pages)
        v.state.clear_selection()

    @staticmethod
    def nudge(v, dx, dy):
        sel = v.selected_overlays()
        if not sel:
            return

        def fn():
            for o in sel:
                o.translate(dx, dy)
                it = v.overlay_item(o.uid)
                if it is not None:
                    it.shift(dx, dy)
        v.model.change("微调位置", fn, {o.page for o in sel}, key="nudge")

    @staticmethod
    def copy(v):
        sel = v.selected_overlays()
        if sel:
            v.state.clipboard = [o.clone() for o in sel]
            v.message.emit(f"已复制 {len(sel)} 个对象 · Ctrl+V 粘贴" if len(sel) > 1 else "已复制 · Ctrl+V 粘贴")

    @staticmethod
    def paste(v, at=None, items=None):
        """粘贴剪贴板里的对象。at=(页号, x, y):粘贴到这个位置;否则粘贴到当前页,并错开一点。"""
        src = items if items is not None else v.state.clipboard
        if not src or not v.model.is_open:
            return
        n = v.model.page_count()
        pno = at[0] if at else min(v.state.current_page, n - 1)
        new = []
        for o in src:
            c = o.clone(new_uid=True)
            if c.kind == "text":  # 副本不应再去删除/遮盖原文
                c.props["cover"], c.props["bgrects"], c.props["bg"] = [], [], None
            new.append(c)
        x0 = min(c.bbox()[0] for c in new)
        y0 = min(c.bbox()[1] for c in new)
        same_page = all(c.page == pno for c in new)
        for c in new:
            if at:
                c.translate(at[1] - x0, at[2] - y0)
            elif same_page:
                c.translate(14, 14)
            c.page = pno
        m = v.model
        m.change("粘贴", lambda: m.overlays.extend(new), {pno})
        v.state.select([c.uid for c in new])

    @staticmethod
    def duplicate(v):
        sel = v.selected_overlays()
        if sel:
            Ops.paste(v, items=[o.clone() for o in sel])

    @staticmethod
    def reorder(v, to_top: bool):
        sel = v.selected_overlays()
        if not sel:
            return
        uids = {o.uid for o in sel}
        m = v.model

        def fn():
            rest = [o for o in m.overlays if o.uid not in uids]
            mine = [o for o in m.overlays if o.uid in uids]
            m.overlays = rest + mine if to_top else mine + rest
        m.change("置于顶层" if to_top else "置于底层", fn, {o.page for o in sel})

    @staticmethod
    def select_all(v):
        pno = v.state.current_page
        v.state.select([o.uid for o in v.model.page_overlays(pno)])

    @staticmethod
    def set_prop(v, key, value):
        """属性面板的修改:作用在正在编辑的文字或选中的对象上。"""
        if v.edits.active:
            v.edits.set_prop(key, value)
            return
        sel = [o for o in v.selected_overlays() if key in o.props]
        sel = [o for o in sel if o.props.get(key) != value]
        if not sel:
            return

        def fn():
            for o in sel:
                o.props[key] = value
                o._cache = None
        v.model.change("修改属性", fn, {o.page for o in sel}, key="prop:" + key)


# ============================================================================
# 工具
# ============================================================================


class Tool:
    name = ""
    cursor = Qt.ArrowCursor

    def __init__(self, view):
        self.v = view
        self.m = view.model
        self.st = view.state
        self.drag = None

    def activate(self):
        pass

    def deactivate(self):
        self.drag = None

    def press(self, ev: PageEvent):
        pass

    def move(self, e):
        pass

    def release(self, e):
        self.drag = None

    def hover(self, ev: PageEvent):
        pass

    def double_click(self, ev: PageEvent):
        pass

    def page_xy(self, e, pno):
        sp = self.v.mapToScene(e.position().toPoint())
        return self.v.to_page(pno, sp)

    def key(self, e) -> bool:
        k, mods = e.key(), e.modifiers()
        v = self.v
        if k == Qt.Key_Escape:
            if self.drag is not None:
                self.cancel_drag()
            elif self.st.selection:
                self.st.clear_selection()
            elif self.st.tool != "select":
                self.st.set_tool("select")
            return True
        if k in (Qt.Key_Delete, Qt.Key_Backspace) and self.st.selection:
            Ops.delete(v)
            return True
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.st.selection:
            step = 10.0 if mods & Qt.ShiftModifier else 1.0
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0.0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0.0)
            Ops.nudge(v, dx, dy)
            return True
        if k == Qt.Key_PageDown:
            v.scroll_by_pages(1)
            return True
        if k == Qt.Key_PageUp:
            v.scroll_by_pages(-1)
            return True
        if k == Qt.Key_Home and not mods:
            v.goto(0)
            return True
        if k == Qt.Key_End and not mods:
            v.goto(10 ** 6)
            return True
        if k == Qt.Key_Return and len(self.st.selection) == 1:
            ov = self.m.find(self.st.selection[0])
            if ov is not None and ov.kind == "text":
                v.edits.begin(ov)
                return True
        return False

    def cancel_drag(self):
        self.drag = None


class SelectTool(Tool):
    name = "select"

    def press(self, ev: PageEvent):
        v, st = self.v, self.st
        h = v.handle_at(ev.pos)
        if h is not None:
            ov, name = h
            self.drag = dict(mode="resize", ov=ov, h=name, orig=ov.clone(), bbox=ov.bbox(), before=self.m.state(),
                             moved=False, pno=ov.page)
            return
        ov = v.hit_test(ev.pno, ev.x, ev.y) if ev.inside else None
        if ov is not None:
            if ev.shift or ev.ctrl:
                if ov.uid in st.selection:
                    st.select([u for u in st.selection if u != ov.uid])
                    return
                st.select(st.selection + [ov.uid])
            elif ov.uid not in st.selection:
                st.select([ov.uid])
            self.drag = dict(mode="move", start=QPointF(ev.scene), before=self.m.state(), moved=False, d=(0.0, 0.0))
            return
        if not (ev.shift or ev.ctrl):
            st.clear_selection()
        self.drag = dict(mode="rubber", p0=QPointF(ev.scene), base=list(st.selection), moved=False)

    def move(self, e):
        d = self.drag
        if d is None:
            return
        v = self.v
        sp = v.mapToScene(e.position().toPoint())
        mode = d["mode"]
        if mode == "move":
            dx, dy = sp.x() - d["start"].x(), sp.y() - d["start"].y()
            if not d["moved"]:
                if math.hypot(dx, dy) * v.s < 3:
                    return
                d["moved"] = True
            if e.modifiers() & Qt.ShiftModifier:  # 按住 Shift 只沿水平或竖直方向移动
                if abs(dx) > abs(dy):
                    dy = 0.0
                else:
                    dx = 0.0
            d["d"] = (dx, dy)
            for ov in v.selected_overlays():
                it = v.overlay_item(ov.uid)
                if it is not None:
                    it.setPos(dx, dy)
            v.update_chrome(offset=(dx, dy))
        elif mode == "resize":
            x, y = v.to_page(d["pno"], sp)
            d["moved"] = True
            self._resize(d, x, y, bool(e.modifiers() & Qt.ShiftModifier))
            v.sync_overlays({d["pno"]})
            v.update_chrome()
        elif mode == "rubber":
            r = QRectF(d["p0"], sp).normalized()
            if r.width() * v.s > 3 or r.height() * v.s > 3:
                d["moved"] = True
                v.set_rubber(r)
                hit = self._in_rect(r)
                self.st.select(d["base"] + [u for u in hit if u not in d["base"]])

    def _in_rect(self, r: QRectF):
        out = []
        v = self.v
        for ov in self.m.overlays:
            if 0 <= ov.page < len(v.pages):
                b = ov.bbox()
                if v.scene_rect_of(ov.page, b).intersects(r):
                    out.append(ov.uid)
        return out

    def release(self, e):
        d, self.drag = self.drag, None
        if d is None:
            return
        v, m = self.v, self.m
        mode = d["mode"]
        if mode == "move" and d["moved"]:
            dx, dy = d["d"]
            pages = set()
            for ov in v.selected_overlays():
                it = v.overlay_item(ov.uid)
                if it is not None:
                    it.setPos(0, 0)
                pages.add(ov.page)
                x0, y0, x1, y1 = ov.bbox()
                c = v.to_scene(ov.page, (x0 + x1) / 2 + dx, (y0 + y1) / 2 + dy)
                tp = v.page_at_scene(c)
                pinned = ov.kind == "text" and (ov.props["cover"] or ov.props.get("bgrects"))
                if tp is not None and tp != ov.page and not pinned:  # 拖到了另一页
                    off = v.pages[ov.page].pos() - v.pages[tp].pos()
                    ov.translate(dx + off.x(), dy + off.y())
                    ov.page = tp
                    pages.add(tp)
                else:
                    ov.translate(dx, dy)
                    if it is not None:
                        it.shift(dx, dy)
            m.push("移动对象", d["before"])
            m.touch(pages)
            v.update_chrome()
        elif mode == "move":
            v.update_chrome()
        elif mode == "resize" and d["moved"]:
            m.push("调整大小", d["before"])
            m.touch({d["pno"]})
        elif mode == "rubber":
            v.set_rubber(None)

    def cancel_drag(self):
        d, self.drag = self.drag, None
        v = self.v
        if d and d["mode"] == "move":
            for ov in v.selected_overlays():
                it = v.overlay_item(ov.uid)
                if it is not None:
                    it.setPos(0, 0)
            v.update_chrome()
        elif d and d["mode"] == "resize" and d["moved"]:
            ov = d["ov"]
            ov.rect, ov.props = list(d["orig"].rect), d["orig"].props
            ov._cache = None
            v.sync_overlays({d["pno"]})
            v.update_chrome()
        elif d and d["mode"] == "rubber":
            v.set_rubber(None)

    @staticmethod
    def _resize(d, x, y, shift):
        ov, o, h = d["ov"], d["orig"], d["h"]
        if ov.kind in ("line", "arrow"):
            ax, ay = (o.rect[2], o.rect[3]) if h == "p1" else (o.rect[0], o.rect[1])
            if shift:  # 锁定 45° 的倍数
                ang = math.atan2(y - ay, x - ax)
                L = math.hypot(x - ax, y - ay)
                ang = round(ang / (math.pi / 4)) * (math.pi / 4)
                x, y = ax + L * math.cos(ang), ay + L * math.sin(ang)
            if h == "p1":
                ov.rect[0], ov.rect[1] = x, y
            else:
                ov.rect[2], ov.rect[3] = x, y
            return
        x0, y0, x1, y1 = d["bbox"]
        if ov.kind == "text":
            ov.props["wrap"] = True
            ov.rect[2] = max(x, x0 + ov.props["size"] * 1.5)
            ov._cache = None
            return
        nx0, ny0, nx1, ny1 = x0, y0, x1, y1
        if "w" in h:
            nx0 = min(x, x1 - 4)
        if "e" in h:
            nx1 = max(x, x0 + 4)
        if "n" in h:
            ny0 = min(y, y1 - 4)
        if "s" in h:
            ny1 = max(y, y0 + 4)
        keep = (ov.kind == "image") != shift  # 图片默认保持比例,按 Shift 自由缩放;其它相反
        if keep and len(h) == 2 and y1 > y0 and x1 > x0:
            ratio = (x1 - x0) / (y1 - y0)
            w, hh = nx1 - nx0, ny1 - ny0
            if w / max(hh, 1e-6) > ratio:
                w = hh * ratio
            else:
                hh = w / ratio
            nx0, nx1 = (nx1 - w, nx1) if "w" in h else (nx0, nx0 + w)
            ny0, ny1 = (ny1 - hh, ny1) if "n" in h else (ny0, ny0 + hh)
        if ov.kind == "ink":  # 每次都从拖动前的笔迹缩放,避免误差累积
            ov.props["points"] = list(o.props["points"])
        ov.set_bbox((nx0, ny0, nx1, ny1))

    def double_click(self, ev: PageEvent):
        if not ev.inside:
            return
        ov = self.v.hit_test(ev.pno, ev.x, ev.y)
        if ov is not None and ov.kind == "text":
            self.v.edits.begin(ov, cursor=("pt", ev.x, ev.y))

    def hover(self, ev: PageEvent):
        v = self.v
        h = v.handle_at(ev.pos)
        if h is not None:
            v.viewport().setCursor(HANDLE_CURSOR.get(h[1], Qt.SizeAllCursor))
        elif ev.inside and v.hit_test(ev.pno, ev.x, ev.y) is not None:
            v.viewport().setCursor(Qt.SizeAllCursor)
        else:
            v.viewport().setCursor(self.cursor)


class EditTool(SelectTool):
    """编辑页面上原有的文字。"""
    name = "edit"

    def press(self, ev: PageEvent):
        v = self.v
        v.set_hover(None, [])
        if v.handle_at(ev.pos) is not None:
            return super().press(ev)
        if not ev.inside:
            return
        ov = v.hit_test(ev.pno, ev.x, ev.y)
        if ov is not None and ov.kind == "text":
            v.edits.begin(ov, cursor=("pt", ev.x, ev.y))
            return
        if ov is not None:
            return super().press(ev)
        self.st.clear_selection()
        self.drag = dict(mode="pick", p0=QPointF(ev.scene), pno=ev.pno, x0=ev.x, y0=ev.y, moved=False)

    def move(self, e):
        d = self.drag
        if d is None or d["mode"] != "pick":
            return super().move(e)
        sp = self.v.mapToScene(e.position().toPoint())
        r = QRectF(d["p0"], sp).normalized()
        if r.width() * self.v.s > 4 or r.height() * self.v.s > 4:
            d["moved"] = True
            self.v.set_rubber(r)
            x0, y0 = self.v.to_page(d["pno"], r.topLeft())
            x1, y1 = self.v.to_page(d["pno"], r.bottomRight())
            self.v.set_hover(d["pno"], [tuple(ln["bbox"]) for ln in self._lines_in(d["pno"], x0, y0, x1, y1)])

    def _lines_in(self, pno, x0, y0, x1, y1):
        return [ln for ln in self.m.lines(pno) if ln["bbox"].x0 < x1 and ln["bbox"].x1 > x0
                and ln["bbox"].y0 < y1 and ln["bbox"].y1 > y0 and not self.m.covered(pno, ln)]

    def release(self, e):
        d = self.drag
        if d is None or d["mode"] != "pick":
            return super().release(e)
        self.drag = None
        v, m = self.v, self.m
        v.set_rubber(None)
        v.set_hover(None, [])
        pno = d["pno"]
        x, y = self.page_xy(e, pno)
        if not d["moved"]:
            ln = m.line_at(pno, x, y)
            picked = [ln] if ln else []
        else:
            picked = self._lines_in(pno, min(x, d["x0"]), min(y, d["y0"]), max(x, d["x0"]), max(y, d["y0"]))
        if not picked:
            if not d["moved"]:
                if m.is_scanned(pno):
                    v.message.emit("这一页是扫描件(图片),还没有可编辑的文字 · 先用「文字识别 OCR」识别一下")
                else:
                    v.message.emit("这里没有可编辑的文字 · 可以用「添加文字」写新内容,或用「白块」盖住旧内容")
            return
        before = m.state()
        ovs = E.overlays_from_lines(pno, picked, m.doc[pno])
        m.overlays.extend(ovs)
        m.touch({pno})
        if len(ovs) == 1:
            v.edits.begin(ovs[0], before=before, new=True, cursor=("pt", x, y) if not d["moved"] else "end")
        else:
            m.push("编辑文字", before)
            self.st.select([o.uid for o in ovs])
            v.message.emit(f"已把 {len(ovs)} 段文字变成可编辑 · 点一下其中一段开始修改")

    def hover(self, ev: PageEvent):
        v = self.v
        if v.handle_at(ev.pos) is not None:
            v.set_hover(None, [])
            return super().hover(ev)
        ov = v.hit_test(ev.pno, ev.x, ev.y) if ev.inside else None
        if ov is not None:
            v.set_hover(None, [])
            v.viewport().setCursor(Qt.IBeamCursor if ov.kind == "text" else Qt.SizeAllCursor)
            return
        ln = self.m.line_at(ev.pno, ev.x, ev.y) if ev.inside else None
        if ln is not None:
            v.set_hover(ev.pno, [tuple(ln["bbox"])])
            v.viewport().setCursor(Qt.IBeamCursor)
        else:
            v.set_hover(None, [])
            v.viewport().setCursor(Qt.ArrowCursor)

    def deactivate(self):
        super().deactivate()
        self.v.set_hover(None, [])


class AddTextTool(SelectTool):
    name = "text"
    cursor = Qt.IBeamCursor

    def press(self, ev: PageEvent):
        v = self.v
        if v.handle_at(ev.pos) is not None:
            return super().press(ev)
        if not ev.inside:
            return
        ov = v.hit_test(ev.pno, ev.x, ev.y)
        if ov is not None and ov.kind == "text":
            v.edits.begin(ov, cursor=("pt", ev.x, ev.y))
            return
        if ov is not None:
            return super().press(ev)
        self.st.clear_selection()
        self.drag = dict(mode="newtext", p0=QPointF(ev.scene), pno=ev.pno, x0=ev.x, y0=ev.y, moved=False)

    def move(self, e):
        d = self.drag
        if d is None or d["mode"] != "newtext":
            return super().move(e)
        sp = self.v.mapToScene(e.position().toPoint())
        r = QRectF(d["p0"], sp).normalized()
        if r.width() * self.v.s > 8:
            d["moved"] = True
            self.v.set_rubber(r)

    def release(self, e):
        d = self.drag
        if d is None or d["mode"] != "newtext":
            return super().release(e)
        self.drag = None
        v, m = self.v, self.m
        v.set_rubber(None)
        pno = d["pno"]
        st = dict(self.st.styles["text"])
        x, y = d["x0"], d["y0"]
        ov = E.Overlay("text", pno, (x, y, x, y), text="", **st)
        font = E.font_for_text(st["font"], st["bold"], st["italic"], "中")
        _lh, off = E.line_metrics(font, st["size"])
        if d["moved"]:  # 拖出来的是固定宽度、自动换行的文字框
            x1, y1 = self.page_xy(e, pno)
            ov.rect = [min(x, x1), min(y, y1), max(x, x1), min(y, y1)]
            ov.props["wrap"] = True
        else:
            ov.translate(0, -off - 2)  # 点击位置大致落在第一行文字中部
        before = m.state()
        m.overlays.append(ov)
        m.touch({pno})
        v.edits.begin(ov, before=before, new=True)


class FormTool(Tool):
    name = "form"
    cursor = Qt.ArrowCursor

    def __init__(self, view):
        super().__init__(view)
        self.editor = None

    def widget_at(self, pno, x, y):
        for w in self.m.widgets(pno):
            r = w["rect"]
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return w
        return None

    def hover(self, ev: PageEvent):
        w = self.widget_at(ev.pno, ev.x, ev.y) if ev.inside else None
        self.v.viewport().setCursor(Qt.PointingHandCursor if w else Qt.ArrowCursor)

    def press(self, ev: PageEvent):
        self.commit_editor()
        if not ev.inside:
            return
        w = self.widget_at(ev.pno, ev.x, ev.y)
        if w is None:
            if self.m.widgets(ev.pno):
                self.v.message.emit("这里没有表单域,请点击蓝色的方框")
            else:
                self.v.message.emit("这一页没有可填写的表单域 · 可以用「添加文字」直接在页面上写")
            return
        k = w["kind"]
        pno = ev.pno
        if k == "check":
            self.set_value(pno, w, not w["checked"])
        elif k == "radio":
            if not w["checked"]:
                self.set_value(pno, w, True)
        elif k in ("combo", "list"):
            menu = QMenu(self.v)
            for ch in w["choices"]:
                act = menu.addAction(str(ch))
                act.setCheckable(True)
                act.setChecked(ch == w["value"])
                act.triggered.connect(lambda _c=False, val=ch: self.set_value(pno, w, val))
            if not w["choices"]:
                menu.addAction("(没有可选项)").setEnabled(False)
            menu.exec(self.v.viewport().mapToGlobal(ev.pos.toPoint()))
        else:
            self.open_editor(pno, w)

    def set_value(self, pno, w, value):
        try:
            self.m.mutate("填写表单", lambda d: E.set_widget(d, pno, w["xref"], value))
        except Exception as ex:  # noqa: BLE001
            self.v.message.emit(f"填写失败:{ex}")

    def open_editor(self, pno, w):
        v = self.v
        r = w["rect"]
        a = v.view_pos_of(pno, r[0], r[1])
        b = v.view_pos_of(pno, r[2], r[3])
        size_px = max(11, int((w["size"] or min(12.0, (r[3] - r[1]) * 0.7)) * v.s))
        ed = QPlainTextEdit(v.viewport()) if w["multiline"] else QLineEdit(v.viewport())
        ed.setObjectName("FormEditor")
        ed.setStyleSheet(f"font-size:{size_px}px; background:#FFFDF2; color:#111; border:2px solid {T.c('primary')};"
                         f"border-radius:3px; padding:0 2px;")
        if w["multiline"]:
            ed.setPlainText(w["value"] or "")
        else:
            ed.setText(w["value"] or "")
            ed.returnPressed.connect(self.commit_editor)
        ed.setGeometry(a.x() - 2, a.y() - 2, max(b.x() - a.x(), 80) + 4, max(b.y() - a.y(), size_px + 8) + 4)
        ed.show()
        ed.setFocus()
        self.editor = dict(ed=ed, w=w, pno=pno, multi=bool(w["multiline"]))
        ed.installEventFilter(v)
        QTimer.singleShot(0, ed.setFocus)

    def editor_key(self, e) -> bool:
        if self.editor and e.key() == Qt.Key_Escape:
            self.commit_editor(cancel=True)
            return True
        return False

    def commit_editor(self, cancel=False):
        fe, self.editor = self.editor, None
        if not fe:
            return
        ed, w = fe["ed"], fe["w"]
        value = ed.toPlainText() if fe["multi"] else ed.text()
        ed.hide()
        ed.deleteLater()
        self.v.setFocus()
        if cancel or value == (w["value"] or ""):
            return
        pno = fe["pno"]
        if not E.needs_cjk(value):
            self.set_value(pno, w, value)
            return
        # 表单域自带的字体画不出中文:改成把文字直接写在页面上,并移除这个表单域
        r = w["rect"]
        size = w["size"] or max(8.0, min(14.0, (r[3] - r[1]) * 0.7))
        ov = E.Overlay("text", pno, (r[0] + 2, r[1], r[2] - 2, r[1]), text=value, size=size,
                       font=self.st.styles["text"]["font"], color="#000000", wrap=bool(w["multiline"]))
        x0, y0, x1, y1 = E.layout_text(ov)["box"]
        ov.translate(0, 1 - (y0 - r[1]) if w["multiline"] else (r[1] + r[3]) / 2 - (y0 + y1) / 2)
        self.m.mutate("填写表单", lambda d: E.delete_widget(d, pno, w["xref"]), then=lambda: self.m.overlays.append(ov))
        self.v.message.emit("表单域无法显示中文,已把内容直接写在页面上")

    def deactivate(self):
        super().deactivate()
        self.commit_editor()


class MarkupTool(Tool):
    cursor = Qt.IBeamCursor

    def __init__(self, view, sub):
        super().__init__(view)
        self.name = self.sub = sub
        self._timer = QTimer(view, singleShot=True, interval=25, timeout=self._preview)
        self._last = None

    def press(self, ev: PageEvent):
        if not ev.inside:
            return
        self.st.clear_selection()
        self.drag = dict(p0=(ev.x, ev.y), pno=ev.pno, start=QPointF(ev.pos), rects=[])

    def move(self, e):
        d = self.drag
        if d is None:
            return
        if (e.position() - d["start"]).manhattanLength() < 4:
            return
        self._last = self.page_xy(e, d["pno"])
        if not self._timer.isActive():
            self._timer.start()

    def _preview(self):
        d = self.drag
        if d is None or self._last is None:
            return
        d["rects"] = self.m.select_words(d["pno"], d["p0"], self._last)
        col = self.st.styles[self.sub]["color"]
        self.v.set_markup_preview(d["pno"], d["rects"], col if self.sub == "highlight" else col)

    def release(self, e):
        d, self.drag = self.drag, None
        self._timer.stop()
        self.v.set_markup_preview(None, [])
        if d is None or (e.position() - d["start"]).manhattanLength() < 4:
            return
        rects = self.m.select_words(d["pno"], d["p0"], self.page_xy(e, d["pno"]))
        if not rects:
            self.v.message.emit("没有选中文字 · 扫描件里的文字要先用「文字识别 OCR」识别")
            return
        ov = E.Overlay("markup", d["pno"], (0, 0, 0, 0), sub=self.sub, color=self.st.styles[self.sub]["color"],
                       rects=rects)
        label = {"highlight": "添加高亮", "underline": "添加下划线", "strike": "添加删除线"}[self.sub]
        self.m.change(label, lambda: self.m.overlays.append(ov), {d["pno"]})

    def cancel_drag(self):
        self.drag = None
        self._timer.stop()
        self.v.set_markup_preview(None, [])


class ShapeTool(Tool):
    cursor = Qt.CrossCursor

    def __init__(self, view, kind):
        super().__init__(view)
        self.name = self.kind = kind

    def press(self, ev: PageEvent):
        if not ev.inside:
            return
        self.st.clear_selection()
        page = self.v.page_item(ev.pno)
        self.drag = dict(p0=(ev.x, ev.y), pno=ev.pno, p1=(ev.x, ev.y), preview=PreviewItem(page), moved=False)

    def _p1(self, d, x, y, shift):
        x0, y0 = d["p0"]
        if not shift:
            return x, y
        if self.kind in ("line", "arrow"):
            ang = round(math.atan2(y - y0, x - x0) / (math.pi / 4)) * (math.pi / 4)
            L = math.hypot(x - x0, y - y0)
            return x0 + L * math.cos(ang), y0 + L * math.sin(ang)
        s = max(abs(x - x0), abs(y - y0))
        return x0 + math.copysign(s, x - x0), y0 + math.copysign(s, y - y0)

    def move(self, e):
        d = self.drag
        if d is None:
            return
        x, y = self.page_xy(e, d["pno"])
        x, y = self._p1(d, x, y, bool(e.modifiers() & Qt.ShiftModifier))
        d["p1"] = (x, y)
        x0, y0 = d["p0"]
        if math.hypot(x - x0, y - y0) * self.v.s < 3:
            return
        d["moved"] = True
        st = self.st.styles["shape"]
        path = QPainterPath()
        r = QRectF(QPointF(x0, y0), QPointF(x, y)).normalized()
        brush = Qt.NoBrush
        if self.kind in ("whiteout", "redact"):
            path.addRect(r)
            col = QColor(self.st.styles["whiteout"]["fill"]) if self.kind == "whiteout" else QColor(0, 0, 0, 210)
            pen = QPen(T.q("primary", 220), 1.2)
            pen.setCosmetic(True)
            pen.setDashPattern([4, 3])
            brush = col
        else:
            pen = QPen(QColor(st["stroke"] or "#000000"), st["width"])
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if not st["stroke"]:
                pen = QPen(Qt.NoPen)
            if self.kind == "rect":
                path.addRect(r)
            elif self.kind == "ellipse":
                path.addEllipse(r)
            elif self.kind == "line":
                path.moveTo(x0, y0)
                path.lineTo(x, y)
            else:
                left, right, base = E._arrow_points((x0, y0), (x, y), st["width"])
                path.moveTo(x0, y0)
                path.lineTo(*base)
                head = QPainterPath()
                head.moveTo(x, y)
                head.lineTo(*left)
                head.lineTo(*right)
                head.closeSubpath()
                path.addPath(head)
            if self.kind in ("rect", "ellipse") and st["fill"]:
                c = QColor(st["fill"])
                c.setAlphaF(st["opacity"])
                brush = c
            elif self.kind == "arrow":
                brush = QColor(st["stroke"] or "#000000")
        d["preview"].set(path, pen, brush)

    def release(self, e):
        d, self.drag = self.drag, None
        if d is None:
            return
        prev = d["preview"]
        if prev.scene() is not None:
            prev.scene().removeItem(prev)
        if not d["moved"]:
            return
        (x0, y0), (x1, y1) = d["p0"], d["p1"]
        pno = d["pno"]
        st = self.st.styles["shape"]
        k = self.kind
        if k in ("rect", "ellipse"):
            ov = E.Overlay(k, pno, (x0, y0, x1, y1), stroke=st["stroke"], width=st["width"], fill=st["fill"],
                           opacity=st["opacity"])
        elif k in ("line", "arrow"):
            ov = E.Overlay(k, pno, (x0, y0, x1, y1), stroke=st["stroke"] or "#000000", width=st["width"])
        elif k == "whiteout":
            ov = E.Overlay("rect", pno, (x0, y0, x1, y1), stroke=None, fill=self.st.styles["whiteout"]["fill"],
                           width=0, opacity=1.0)
        else:
            ov = E.Overlay("redact", pno, (x0, y0, x1, y1))
        if k in ("rect", "ellipse", "whiteout", "redact"):
            ov.rect = list(ov.bbox())
            if ov.rect[2] - ov.rect[0] < 2 and ov.rect[3] - ov.rect[1] < 2:
                return
        label = {"rect": "添加矩形", "ellipse": "添加椭圆", "line": "添加直线", "arrow": "添加箭头",
                 "whiteout": "添加白块", "redact": "添加涂黑"}[k]
        self.m.change(label, lambda: self.m.overlays.append(ov), {pno})
        self.st.set_tool("select")
        self.st.select([ov.uid])

    def cancel_drag(self):
        d, self.drag = self.drag, None
        if d and d["preview"].scene() is not None:
            d["preview"].scene().removeItem(d["preview"])


class InkTool(Tool):
    name = "ink"
    cursor = Qt.CrossCursor

    def press(self, ev: PageEvent):
        if not ev.inside:
            return
        self.st.clear_selection()
        page = self.v.page_item(ev.pno)
        self.drag = dict(pno=ev.pno, pts=[(ev.x, ev.y)], preview=PreviewItem(page))
        self._draw()

    def _draw(self):
        d = self.drag
        st = self.st.styles["ink"]
        path = QPainterPath()
        pts = d["pts"]
        path.moveTo(*pts[0])
        for p in pts[1:]:
            path.lineTo(*p)
        if len(pts) == 1:
            path.lineTo(pts[0][0] + 0.01, pts[0][1])
        pen = QPen(QColor(st["stroke"]), st["width"])
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        d["preview"].set(path, pen)

    def move(self, e):
        d = self.drag
        if d is None:
            return
        x, y = self.page_xy(e, d["pno"])
        lx, ly = d["pts"][-1]
        if math.hypot(x - lx, y - ly) * self.v.s >= 1.5:
            d["pts"].append((x, y))
            self._draw()

    def release(self, e):
        d, self.drag = self.drag, None
        if d is None:
            return
        prev = d["preview"]
        st = self.st.styles["ink"]
        ov = E.Overlay("ink", d["pno"], (0, 0, 0, 0), stroke=st["stroke"], width=st["width"], points=d["pts"])
        self.m.change("画笔", lambda: self.m.overlays.append(ov), {d["pno"]})
        if prev.scene() is not None:  # 对象图像出来之后再去掉预览,避免闪一下
            prev.scene().removeItem(prev)

    def cancel_drag(self):
        d, self.drag = self.drag, None
        if d and d["preview"].scene() is not None:
            d["preview"].scene().removeItem(d["preview"])


def make_tools(view) -> dict:
    tools = {
        "select": SelectTool(view), "edit": EditTool(view), "text": AddTextTool(view), "form": FormTool(view),
        "ink": InkTool(view),
    }
    for sub in ("highlight", "underline", "strike"):
        tools[sub] = MarkupTool(view, sub)
    for k in ("rect", "ellipse", "line", "arrow", "whiteout", "redact"):
        tools[k] = ShapeTool(view, k)
    return tools


__all__ = ["Ops", "PageEvent", "make_tools", "overlay_sig", "handles_for"]
