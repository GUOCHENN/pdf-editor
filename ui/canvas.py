"""页面画布:所有页面上下连续排列,像阅读器一样直接滚动浏览。

结构
----
* 场景坐标的单位是 pt(PDF 点)。视图按 缩放 × 96/72 放大,所以 100% 就是纸面实际大小。
* 每页是一个 PageItem,只画底图(PDF 原有内容)。底图按需在主线程里一页一页渲染
  (PyMuPDF 不是线程安全的),先渲染看得见的页;放得很大时,额外渲染一块看得见区域的高清图。
* 用户添加的每个对象(Overlay)是 PageItem 的一个子图元 OverlayItem,坐标就是页面坐标。
  对象的图像由 engine 渲染(和保存时同一套代码,所见即所得),移动时只是挪动图元,不用重画。
* 选中框、悬停高亮、框选矩形等界面元素用 Qt 直接画,线宽固定为屏幕像素。
* 鼠标操作交给当前工具(tools.py);文字就地编辑在 text_edit.py。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsItem, QGraphicsScene, QGraphicsView

import engine as E
from ui.theme import T
from ui import tasks

PT2PX = 96.0 / 72.0
GAP = 18.0
MARGIN = 26.0
MAX_FULL_PIXELS = 14_000_000      # 整页底图的像素上限,再大就改为"整页低清 + 可见区域高清"
MAX_OVERLAY_PIXELS = 12_000_000
HANDLE_PX = 8
MIN_ZOOM, MAX_ZOOM = 0.1, 8.0


def to_qimage(pm) -> QImage:
    """fitz.Pixmap → QImage(深拷贝,不依赖 pm 的内存)。"""
    fmt = QImage.Format_RGBA8888_Premultiplied if pm.alpha else QImage.Format_RGB888
    return QImage(pm.samples, pm.width, pm.height, pm.stride, fmt).copy()


def overlay_sig(ov):
    p = dict(ov.props)
    if p.get("data"):
        p["data"] = (id(p["data"]), len(p["data"]))
    return ov.kind, tuple(round(v, 3) for v in ov.rect), repr(sorted(p.items()))


def seg_dist(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def hit(ov, x, y, tol) -> bool:
    k, p = ov.kind, ov.props
    if k in ("line", "arrow"):
        return seg_dist((x, y), ov.rect[:2], ov.rect[2:]) <= tol + p["width"] / 2
    if k == "ink":
        pts = p["points"]
        if len(pts) == 1:
            return math.hypot(x - pts[0][0], y - pts[0][1]) <= tol + p["width"]
        return any(seg_dist((x, y), pts[i], pts[i + 1]) <= tol + p["width"] / 2 for i in range(len(pts) - 1))
    if k == "markup":
        return any(r[0] - 1 <= x <= r[2] + 1 and r[1] - 1 <= y <= r[3] + 1 for r in p["rects"])
    x0, y0, x1, y1 = ov.bbox()
    if k in ("rect", "ellipse") and not p["fill"]:
        t = tol + p["width"] / 2
        outer = x0 - t <= x <= x1 + t and y0 - t <= y <= y1 + t
        inner = x0 + t < x < x1 - t and y0 + t < y < y1 - t
        return outer and not inner
    return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol


def handles_for(ov) -> dict:
    k = ov.kind
    if k in ("line", "arrow"):
        return {"p1": (ov.rect[0], ov.rect[1]), "p2": (ov.rect[2], ov.rect[3])}
    if k == "markup":
        return {}
    x0, y0, x1, y1 = ov.bbox()
    if k == "text":
        return {"e": (x1, (y0 + y1) / 2)}
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    return {"nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0), "e": (x1, my), "se": (x1, y1), "s": (mx, y1),
            "sw": (x0, y1), "w": (x0, my)}


HANDLE_CURSOR = {"nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor, "ne": Qt.SizeBDiagCursor,
                 "sw": Qt.SizeBDiagCursor, "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor, "e": Qt.SizeHorCursor,
                 "w": Qt.SizeHorCursor, "p1": Qt.SizeAllCursor, "p2": Qt.SizeAllCursor}


# ============================================================================
# 图元
# ============================================================================


class PageItem(QGraphicsItem):
    """一页:阴影 + 白纸 + 底图。"""

    def __init__(self, pno: int, w: float, h: float):
        super().__init__()
        self.pno, self.w, self.h = pno, w, h
        self.px = 1.0                  # 1 个屏幕像素是多少 pt
        self.full = None               # 整页底图 QPixmap
        self.full_key = None           # (版本, 覆盖区域…)
        self.full_scale = 0.0
        self.detail = None             # 放大时可见区域的高清图
        self.detail_rect = QRectF()
        self.detail_key = None
        self.low = None                # 离开视野后保留的低清图,重新渲染前先顶着用
        self.low_key = None
        self.setZValue(0)

    def boundingRect(self):
        m = 14 * self.px
        return QRectF(-m, -m, self.w + 2 * m, self.h + 2 * m)

    def set_px(self, px: float):
        self.prepareGeometryChange()
        self.px = px

    def paint(self, p: QPainter, opt, widget=None):
        r = QRectF(0, 0, self.w, self.h)
        px = self.px
        p.setPen(Qt.NoPen)
        sh = T.q("page_shadow")
        for i, a in ((1, 26), (3, 14), (6, 8), (10, 4)):  # 柔和的多层阴影
            sh.setAlpha(a)
            p.setBrush(sh)
            p.drawRoundedRect(r.adjusted(-i * px * 0.6, -i * px * 0.2, i * px * 0.6, i * px * 1.0), i * px, i * px)
        p.fillRect(r, Qt.white)
        src = self.full if self.full is not None else self.low
        if src is not None:
            p.drawPixmap(r, src, QRectF(src.rect()))
        if self.detail is not None and self.detail_key == self.full_key:
            p.drawPixmap(self.detail_rect, self.detail, QRectF(self.detail.rect()))


class OverlayItem(QGraphicsItem):
    """一个用户对象的图像(engine 渲染的透明图)。"""

    def __init__(self, ov, parent: PageItem):
        super().__init__(parent)
        self.ov = ov
        self.uid = ov.uid
        self.rect = QRectF()
        self.pix = None
        self.sig = None
        self.scale = 0.0

    def boundingRect(self):
        return self.rect

    def paint(self, p: QPainter, opt, widget=None):
        if self.pix is not None:
            p.drawPixmap(self.rect, self.pix, QRectF(self.pix.rect()))

    def render(self, page_size, scale: float):
        x0, y0, x1, y1 = self.ov.bbox()
        area = max((x1 - x0) * (y1 - y0), 1.0)
        scale = min(scale, math.sqrt(MAX_OVERLAY_PIXELS / area))
        pm, clip = E.overlay_pixmap(self.ov, page_size, scale)
        self.prepareGeometryChange()
        self.rect = QRectF(clip[0], clip[1], max(clip[2] - clip[0], 0), max(clip[3] - clip[1], 0))
        self.pix = QPixmap.fromImage(to_qimage(pm)) if pm is not None else None
        self.sig = overlay_sig(self.ov)
        self.scale = scale
        self.update()

    def shift(self, dx: float, dy: float):
        """对象被平移了:图像不变,只挪位置。"""
        self.prepareGeometryChange()
        self.rect.translate(dx, dy)
        self.sig = overlay_sig(self.ov)
        self.update()


class _Chrome(QGraphicsItem):
    """界面元素的基类:场景坐标,线宽按屏幕像素。"""

    def __init__(self, z):
        super().__init__()
        self.px = 1.0
        self.setZValue(z)
        self._br = QRectF()

    def boundingRect(self):
        return self._br

    def _set_br(self, r: QRectF):
        self.prepareGeometryChange()
        m = 12 * self.px
        self._br = r.adjusted(-m, -m, m, m) if not r.isNull() else QRectF()
        self.update()

    def pen(self, key="primary", width=1.0, alpha=None, dash=False):
        pen = QPen(T.q(key, alpha), width)
        pen.setCosmetic(True)
        if dash:
            pen.setDashPattern([4, 3])
        return pen


class SelectionItem(_Chrome):
    def __init__(self):
        super().__init__(1000)
        self.boxes = []      # 场景坐标的外框
        self.lines = []      # 直线/箭头:两个端点
        self.handles = []    # 控制点(场景坐标)

    def set(self, boxes, lines, handles):
        self.boxes, self.lines, self.handles = boxes, lines, handles
        r = QRectF()
        for b in boxes:
            r = r.united(b)
        for a, b in lines:
            r = r.united(QRectF(a, b).normalized())
        for h in handles:
            r = r.united(QRectF(h, h))
        self._set_br(r)

    def paint(self, p: QPainter, opt, widget=None):
        px = self.px
        p.setBrush(Qt.NoBrush)
        for b in self.boxes:
            p.setPen(self.pen("primary", 1.5))
            p.drawRect(b.adjusted(-2 * px, -2 * px, 2 * px, 2 * px))
        for a, b in self.lines:
            p.setPen(self.pen("primary", 1.0, 120, dash=True))
            p.drawLine(a, b)
        s = HANDLE_PX * px
        for h in self.handles:
            p.setPen(self.pen("primary", 1.5))
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(QRectF(h.x() - s / 2, h.y() - s / 2, s, s), 2 * px, 2 * px)


class RectsItem(_Chrome):
    """一组半透明矩形:悬停的文字行、搜索结果、表单域、拖选文字的预览……"""

    def __init__(self, z, fill="primary", fill_alpha=40, line="primary", line_alpha=170, radius=2.0):
        super().__init__(z)
        self.rects = []
        self.current = -1
        self.style = (fill, fill_alpha, line, line_alpha, radius)
        self.fill_color = None

    def set(self, rects, current=-1, color=None):
        self.rects, self.current, self.fill_color = list(rects), current, color
        r = QRectF()
        for x in self.rects:
            r = r.united(x)
        self._set_br(r)

    def paint(self, p: QPainter, opt, widget=None):
        fill, fa, line, la, rad = self.style
        px = self.px
        for i, r in enumerate(self.rects):
            cur = i == self.current
            if self.fill_color:
                c = QColor(self.fill_color)
                c.setAlpha(110)
                p.setBrush(c)
                p.setPen(Qt.NoPen)
            else:
                p.setBrush(T.q(fill, fa * 2 if cur else fa) if fill not in ("search",) else
                           QColor(255, 176, 32, 150 if cur else 70))
                if fill == "search":
                    pen = QPen(QColor(230, 120, 0) if cur else QColor(255, 176, 32, 160), 2 if cur else 1)
                    pen.setCosmetic(True)
                    p.setPen(pen)
                else:
                    p.setPen(self.pen(line, 1.5 if cur else 1.0, la))
            p.drawRoundedRect(r.adjusted(-px, -px, px, px), rad * px, rad * px)


class RubberItem(_Chrome):
    def __init__(self):
        super().__init__(1100)
        self.r = None

    def set(self, r):
        self.r = r
        self._set_br(r if r is not None else QRectF())

    def paint(self, p: QPainter, opt, widget=None):
        if self.r is None:
            return
        p.setBrush(T.q("primary", 28))
        p.setPen(self.pen("primary", 1.0, 200, dash=True))
        p.drawRect(self.r)


class PreviewItem(QGraphicsItem):
    """拖动绘制形状、画笔书写时的即时预览(页面坐标,PageItem 的子图元)。松开鼠标后换成真正的对象。"""

    def __init__(self, parent: PageItem):
        super().__init__(parent)
        self.path = QPainterPath()
        self.pen = QPen(Qt.black, 1)
        self.brush = Qt.NoBrush
        self.setZValue(900)

    def set(self, path: QPainterPath, pen: QPen, brush=Qt.NoBrush):
        self.prepareGeometryChange()
        self.path, self.pen, self.brush = path, pen, brush
        self.update()

    def boundingRect(self):
        w = self.pen.widthF() if not self.pen.isCosmetic() else 2
        return self.path.boundingRect().adjusted(-w - 12, -w - 12, w + 12, w + 12)

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(self.pen)
        p.setBrush(self.brush)
        p.drawPath(self.path)


# ============================================================================
# 视图
# ============================================================================


class PageView(QGraphicsView):
    zoomChanged = Signal(float)
    message = Signal(str)                          # 给用户的提示(主窗口显示为浮动提示)
    contextRequested = Signal(object, int, float, float, object)  # 全局坐标, 页号, x, y, 对象 uid 或 None
    filesDropped = Signal(list, int, float, float)  # 文件路径, 页号(-1 = 不在页面上), x, y
    editingChanged = Signal(bool)                  # 开始 / 结束就地编辑文字

    def __init__(self, model, state, parent=None):
        super().__init__(parent)
        self.model, self.state = model, state
        self.scene_ = QGraphicsScene(self)
        self.setScene(self.scene_)
        self.setFrameShape(QFrame.NoFrame)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform | QPainter.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.viewport().setAcceptDrops(True)
        self.apply_theme()

        self.zoom = 1.0
        self.s = PT2PX
        self.pages: list[PageItem] = []
        self.items: dict[int, OverlayItem] = {}
        self._prep: dict = {}
        self.selection_item = SelectionItem()
        self.hover_item = RectsItem(950)
        self.search_item = RectsItem(940, fill="search")
        self.form_item = RectsItem(930, fill_alpha=34, line_alpha=200)
        self.markup_preview = RectsItem(960, fill_alpha=0, line_alpha=0, radius=0)
        self.rubber = RubberItem()
        self._chrome = [self.selection_item, self.hover_item, self.search_item, self.form_item,
                        self.markup_preview, self.rubber]
        for it in self._chrome:
            self.scene_.addItem(it)
        self.hits: list = []          # 搜索结果 [(页号, rect)]
        self.hit_idx = -1
        self.hidden_uids: set = set()  # 正在编辑的文字对象:图像先藏起来,显示编辑框

        self._render_timer = QTimer(self, singleShot=True, interval=0, timeout=self._render_tick)
        self._settle_timer = QTimer(self, singleShot=True, interval=140, timeout=self._kick)
        self._scroll_timer = QTimer(self, singleShot=True, interval=40, timeout=self._kick)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll)
        self.horizontalScrollBar().valueChanged.connect(self._on_scroll)

        from ui.text_edit import TextEditing
        from ui.tools import make_tools
        self.edits = TextEditing(self)
        self.tools = make_tools(self)
        self.tool = self.tools["select"]
        self._pan = None
        self._space = False
        self._pass_mouse = False

        model.opened.connect(self.rebuild)
        model.structureChanged.connect(self.on_structure)
        model.overlaysChanged.connect(self.on_overlays)
        state.selectionChanged.connect(self.update_chrome)
        state.toolChanged.connect(self.on_tool)

    def apply_theme(self):
        self.setBackgroundBrush(T.q("desk"))
        self.viewport().update()

    # ------------------------------------------------------------------ 构建与同步
    def rebuild(self):
        """打开了新文档:重建所有页面。"""
        self.edits.drop()
        self.hits, self.hit_idx = [], -1
        self._build_pages()
        self.sync_overlays(None)
        self.update_chrome()
        self.update_search()
        if self.model.is_open:
            self.fit_initial()
            self.goto(0)
        self._kick()

    def _close_prep(self):
        for _key, _page, tmp in self._prep.values():
            if tmp is not None:
                tmp.close()
        self._prep.clear()

    def _build_pages(self):
        self._close_prep()
        for uid in list(self.items):
            self._remove_item(uid)
        for p in self.pages:
            self.scene_.removeItem(p)
        self.pages = []
        if self.model.is_open:
            for i in range(self.model.page_count()):
                w, h = self.model.page_size(i)
                item = PageItem(i, w, h)
                item.set_px(1 / self.s)
                self.scene_.addItem(item)
                self.pages.append(item)
        self._layout()

    def _layout(self):
        y = MARGIN
        maxw = max([p.w for p in self.pages], default=400.0)
        for p in self.pages:
            p.setPos(-p.w / 2, y)
            y += p.h + GAP
        h = (y - GAP + MARGIN) if self.pages else 400.0
        self.scene_.setSceneRect(-maxw / 2 - MARGIN, 0, maxw + 2 * MARGIN, h)

    def on_structure(self):
        """PDF 本身变了(页面增删、旋转、填表……)。尽量保持滚动位置。"""
        self.edits.drop()
        cur = self.state.current_page
        sizes = [self.model.page_size(i) for i in range(self.model.page_count())]
        same = len(sizes) == len(self.pages) and all(
            abs(p.w - w) < 0.01 and abs(p.h - h) < 0.01 for p, (w, h) in zip(self.pages, sizes))
        if same:
            self._close_prep()
            for uid in list(self.items):
                self._remove_item(uid)
            for p in self.pages:
                p.low = p.full if p.full is not None else p.low  # 先用旧图顶着,避免闪白
                p.full_key = None
                p.detail = None
        else:
            self._build_pages()
            self.goto(min(cur, len(self.pages) - 1))
        alive = {o.uid for o in self.model.overlays}
        self.state.select([u for u in self.state.selection if u in alive])
        self.hits, self.hit_idx = [], -1
        self.update_search()
        self.sync_overlays(None)
        self.update_chrome()
        self.update_forms()
        self._kick()

    def on_overlays(self, pages):
        self.sync_overlays(pages)
        alive = {o.uid for o in self.model.overlays}
        if any(u not in alive for u in self.state.selection):
            self.state.select([u for u in self.state.selection if u in alive])
        self.update_chrome()
        self._kick()

    def _remove_item(self, uid):
        it = self.items.pop(uid, None)
        if it is not None and it.scene() is not None:
            self.scene_.removeItem(it)

    def sync_overlays(self, pages=None):
        """让图元和模型里的对象一致(按 uid 对应;对象变了才重新渲染)。"""
        if not self.model.is_open:
            return
        n = len(self.pages)
        want = {}
        for idx, ov in enumerate(self.model.overlays):
            if 0 <= ov.page < n and (pages is None or ov.page in pages):
                want[ov.uid] = (idx, ov)
        for uid, it in list(self.items.items()):
            if pages is not None and it.parentItem() is not None and it.parentItem().pno not in pages \
                    and it.ov.page not in pages:
                continue
            if uid not in want or it.parentItem() is not self.pages[want[uid][1].page]:
                self._remove_item(uid)
        scale = self.render_scale()
        for uid, (idx, ov) in want.items():
            it = self.items.get(uid)
            page = self.pages[ov.page]
            if it is None:
                it = OverlayItem(ov, page)
                self.items[uid] = it
            it.ov = ov
            it.setZValue(10 + idx)
            it.setVisible(uid not in self.hidden_uids)
            if it.sig != overlay_sig(ov):
                it.render((page.w, page.h), scale if self._page_visible(ov.page) else min(scale, 1.5))

    def overlay_item(self, uid):
        return self.items.get(uid)

    def hide_overlay(self, uid, hidden=True):
        if hidden:
            self.hidden_uids.add(uid)
        else:
            self.hidden_uids.discard(uid)
        it = self.items.get(uid)
        if it is not None:
            it.setVisible(not hidden)

    # ------------------------------------------------------------------ 坐标
    def render_scale(self) -> float:
        return self.s * self.devicePixelRatioF()

    def page_item(self, pno) -> PageItem | None:
        return self.pages[pno] if 0 <= pno < len(self.pages) else None

    def page_at_scene(self, sp: QPointF, nearest=False):
        best, bd = None, 1e18
        for p in self.pages:
            r = QRectF(p.pos(), QRectF(0, 0, p.w, p.h).size())
            if r.contains(sp):
                return p.pno
            if nearest:
                d = max(r.top() - sp.y(), 0, sp.y() - r.bottom())
                if d < bd:
                    best, bd = p.pno, d
        return best

    def to_page(self, pno: int, sp: QPointF):
        p = self.pages[pno].pos()
        return sp.x() - p.x(), sp.y() - p.y()

    def to_scene(self, pno: int, x: float, y: float) -> QPointF:
        p = self.pages[pno].pos()
        return QPointF(p.x() + x, p.y() + y)

    def scene_rect_of(self, pno: int, r) -> QRectF:
        p = self.pages[pno].pos()
        return QRectF(p.x() + r[0], p.y() + r[1], r[2] - r[0], r[3] - r[1])

    def view_pos_of(self, pno, x, y):
        return self.mapFromScene(self.to_scene(pno, x, y))

    def visible_scene_rect(self) -> QRectF:
        return self.mapToScene(self.viewport().rect()).boundingRect()

    def tol(self) -> float:
        """鼠标命中容差(pt)。"""
        return 5 / self.s

    # ------------------------------------------------------------------ 命中测试
    def hit_test(self, pno, x, y, exclude=()):
        tol = self.tol()
        for ov in reversed(self.model.page_overlays(pno)):
            if ov.uid in exclude or ov.uid in self.hidden_uids:
                continue
            if hit(ov, x, y, tol):
                return ov
        return None

    def selected_overlays(self):
        out = []
        for uid in self.state.selection:
            ov = self.model.find(uid)
            if ov is not None:
                out.append(ov)
        return out

    def handle_at(self, view_pos):
        sel = self.selected_overlays()
        if len(sel) != 1 or sel[0].uid in self.hidden_uids:
            return None
        ov = sel[0]
        if not 0 <= ov.page < len(self.pages):
            return None
        r = HANDLE_PX * 0.5 + 4
        for name, (hx, hy) in handles_for(ov).items():
            vp = self.view_pos_of(ov.page, hx, hy)
            if abs(vp.x() - view_pos.x()) <= r and abs(vp.y() - view_pos.y()) <= r:
                return ov, name
        return None

    # ------------------------------------------------------------------ 界面元素
    def update_chrome(self, offset=None):
        """重画选中框。offset:拖动中的临时位移 (dx, dy) pt。"""
        boxes, lines, handles = [], [], []
        sel = [o for o in self.selected_overlays() if o.uid not in self.hidden_uids and 0 <= o.page < len(self.pages)]
        dx, dy = offset or (0.0, 0.0)
        for ov in sel:
            if ov.kind in ("line", "arrow"):
                a = self.to_scene(ov.page, ov.rect[0] + dx, ov.rect[1] + dy)
                b = self.to_scene(ov.page, ov.rect[2] + dx, ov.rect[3] + dy)
                lines.append((a, b))
            else:
                x0, y0, x1, y1 = ov.bbox()
                boxes.append(self.scene_rect_of(ov.page, (x0 + dx, y0 + dy, x1 + dx, y1 + dy)))
        if len(sel) == 1:
            ov = sel[0]
            handles = [self.to_scene(ov.page, hx + dx, hy + dy) for hx, hy in handles_for(ov).values()]
        px = 1 / self.s
        for it in self._chrome:
            it.px = px
        self.selection_item.set(boxes, lines, handles)

    def set_hover(self, pno, rects):
        self.hover_item.set([self.scene_rect_of(pno, r) for r in rects] if pno is not None else [])

    def set_rubber(self, r: QRectF | None):
        self.rubber.set(r)

    def set_markup_preview(self, pno, rects, color=None):
        self.markup_preview.set([self.scene_rect_of(pno, r) for r in rects] if pno is not None else [],
                                color=color)

    def update_forms(self):
        if self.state.tool != "form" or not self.model.is_open:
            self.form_item.set([])
            return
        rects = []
        for p in self._visible_pages(extra=1):
            rects += [self.scene_rect_of(p, w["rect"]) for w in self.model.widgets(p)]
        self.form_item.set(rects)

    # ------------------------------------------------------------------ 搜索结果
    def set_hits(self, hits, idx=-1):
        self.hits, self.hit_idx = hits, idx
        self.update_search()
        if 0 <= idx < len(hits):
            pno, r = hits[idx]
            self.ensure_visible(pno, r)

    def update_search(self):
        rects, cur = [], -1
        for k, (pno, r) in enumerate(self.hits):
            if 0 <= pno < len(self.pages):
                if k == self.hit_idx:
                    cur = len(rects)
                rects.append(self.scene_rect_of(pno, r))
        self.search_item.set(rects, cur)

    def ensure_visible(self, pno, r, margin=80):
        sr = self.scene_rect_of(pno, r)
        vis = self.visible_scene_rect()
        m = margin / self.s
        if not vis.adjusted(m, m, -m, -m).contains(sr):
            self.centerOn(sr.center().x() if sr.width() > vis.width() * 0.8 else vis.center().x(),
                          sr.center().y())
            if not self.visible_scene_rect().contains(sr):
                self.centerOn(sr.center())

    # ------------------------------------------------------------------ 缩放与导航
    def set_zoom(self, z: float, anchor=None):
        z = max(MIN_ZOOM, min(MAX_ZOOM, z))
        if abs(z - self.zoom) < 1e-6:
            return
        if anchor is None:
            anchor = self.viewport().rect().center()
        sp = self.mapToScene(anchor)
        self.zoom = z
        self.s = z * PT2PX
        self.setTransform(QTransform.fromScale(self.s, self.s))
        delta = self.mapFromScene(sp) - anchor
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        px = 1 / self.s
        for p in self.pages:
            p.set_px(px)
        self.update_chrome()
        self.update_search()
        self.edits.on_zoom()
        self.tools["form"].commit_editor()
        self.zoomChanged.emit(z)
        self._settle_timer.start()

    def zoom_by(self, factor, anchor=None):
        self.set_zoom(self.zoom * factor, anchor)

    def _fit_zoom(self, w, h=None):
        """让宽 w(以及高 h)pt 的页面正好放进视口的缩放比例(两边留出页面外的空白)。"""
        # 竖直滚动条可能还没出现(文档刚打开),按它会出现来算,免得刚好多出一条横向滚动条
        sb = 0 if self.verticalScrollBar().isVisible() else self.verticalScrollBar().sizeHint().width()
        vw = max(self.viewport().width() - sb - 8, 100)
        z = vw / ((w + 2 * MARGIN) * PT2PX)
        if h is not None:
            vh = max(self.viewport().height() - 6, 100)
            z = min(z, vh / ((h + GAP + 24) * PT2PX))
        return z

    def fit_width(self):
        if not self.pages:
            return
        p = self.pages[min(self.state.current_page, len(self.pages) - 1)]
        self.set_zoom(self._fit_zoom(p.w))

    def fit_page(self):
        if not self.pages:
            return
        cur = min(self.state.current_page, len(self.pages) - 1)
        p = self.pages[cur]
        self.set_zoom(self._fit_zoom(p.w, p.h))
        self.goto(cur)

    def fit_initial(self):
        p = self.pages[0]
        z = min(max(self._fit_zoom(p.w), 0.25), 1.25)
        self.zoom = -1  # 强制刷新
        self.set_zoom(z)

    def goto(self, pno: int):
        if not self.pages:
            return
        pno = max(0, min(pno, len(self.pages) - 1))
        p = self.pages[pno]
        vis = self.visible_scene_rect()
        top = p.pos().y() - 12 / self.s
        self.centerOn(vis.center().x(), top + vis.height() / 2)
        self.state.set_current_page(pno)

    def scroll_by_pages(self, d: int):
        self.goto(self.state.current_page + d)

    # ------------------------------------------------------------------ 可见页 / 当前页
    def _page_visible(self, pno) -> bool:
        return pno in self._visible_pages(extra=1)

    def _visible_pages(self, extra=0) -> list:
        vis = self.visible_scene_rect()
        out = [p.pno for p in self.pages if p.pos().y() <= vis.bottom() and p.pos().y() + p.h >= vis.top()]
        if out and extra:
            lo, hi = max(0, out[0] - extra), min(len(self.pages) - 1, out[-1] + extra)
            out = list(range(lo, hi + 1))
        return out

    def _update_current(self):
        vis = self.visible_scene_rect()
        best, bh = None, -1.0
        for p in self.pages:
            y0, y1 = p.pos().y(), p.pos().y() + p.h
            h = min(y1, vis.bottom()) - max(y0, vis.top())
            if h > bh + 0.5:
                best, bh = p.pno, h
        if best is not None:
            self.state.set_current_page(best)

    def _on_scroll(self, _v=None):
        if not hasattr(self, "tools"):
            return
        self._update_current()
        self._scroll_timer.start()
        self.edits.on_scroll()
        self.tools["form"].commit_editor()

    def eventFilter(self, obj, ev):
        """填写表单时的输入框:Esc 取消,失去焦点就提交。"""
        form = self.tools["form"] if hasattr(self, "tools") else None
        if form is not None and form.editor and obj is form.editor["ed"]:
            if ev.type() == ev.Type.KeyPress and form.editor_key(ev):
                return True
            if ev.type() == ev.Type.FocusOut:
                QTimer.singleShot(0, lambda o=obj: form.commit_editor() if form.editor and form.editor["ed"] is o
                                  else None)
        return super().eventFilter(obj, ev)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_current()
        self._scroll_timer.start()

    # ------------------------------------------------------------------ 渲染队列
    def _kick(self):
        if not self._render_timer.isActive():
            self._render_timer.start(0)
        self.update_forms()

    def _base_key(self, pno):
        covers = tuple(tuple(round(v, 2) for v in r) for r in self.model.covers(pno))
        patches = tuple((tuple(round(v, 2) for v in r), c) for r, c in self.model.patches(pno))
        return self.model.ver, covers, patches

    def prepared(self, pno):
        key = self._base_key(pno)
        ent = self._prep.get(pno)
        if ent is not None and ent[0] == key:
            return ent[1], key
        if ent is not None and ent[2] is not None:
            ent[2].close()
        page, tmp = E.prepared_page(self.model.doc, pno, self.model.covers(pno), self.model.patches(pno))
        self._prep[pno] = (key, page, tmp)
        if len(self._prep) > 8:  # 只保留最近用到的几页
            for old in list(self._prep)[:-8]:
                o = self._prep.pop(old)
                if o[2] is not None:
                    o[2].close()
        else:
            self._prep[pno] = self._prep.pop(pno)
        return page, key

    def _want_full(self, p: PageItem) -> float:
        return min(self.render_scale(), math.sqrt(MAX_FULL_PIXELS / max(p.w * p.h, 1)))

    def render_page_now(self, pno):
        """立即渲染这一页的底图(比如刚开始编辑某行文字,原文要马上消失)。"""
        if 0 <= pno < len(self.pages) and not tasks.is_busy():
            self._render_full(self.pages[pno])
            self._render_detail(self.pages[pno], force=True)

    def _render_full(self, p: PageItem):
        page, key = self.prepared(p.pno)
        scale = self._want_full(p)
        pm = page.get_pixmap(matrix=E.fitz.Matrix(scale, scale), alpha=False)
        p.full = QPixmap.fromImage(to_qimage(pm))
        p.full_key, p.full_scale = key, scale
        p.low, p.low_key = None, None
        if p.detail_key != key:
            p.detail = None
        p.update()

    def _detail_needed(self, p: PageItem):
        """放大到整页图不够清晰时,返回需要高清渲染的区域(页面坐标);否则 None。"""
        want = self.render_scale()
        if want <= p.full_scale * 1.04:
            return None
        vis = self.visible_scene_rect().translated(-p.pos())
        r = vis.intersected(QRectF(0, 0, p.w, p.h))
        if r.isEmpty():
            return None
        return r

    def _render_detail(self, p: PageItem, force=False):
        r = self._detail_needed(p)
        if r is None:
            p.detail = None
            return False
        want = self.render_scale()
        key = self._base_key(p.pno)
        cur_ok = (p.detail is not None and p.detail_key == key and p.detail_rect.contains(r)
                  and abs(p.detail.width() / max(p.detail_rect.width(), 1e-6) - want) < want * 0.03)
        if cur_ok and not force:
            return False
        m = 0.25
        rr = r.adjusted(-r.width() * m, -r.height() * m, r.width() * m, r.height() * m).intersected(
            QRectF(0, 0, p.w, p.h))
        page, key = self.prepared(p.pno)
        clip = E.fitz.Rect(rr.left(), rr.top(), rr.right(), rr.bottom())  # 显示坐标(旋转后的)
        pm = page.get_pixmap(matrix=E.fitz.Matrix(want, want), clip=clip, alpha=False)
        p.detail = QPixmap.fromImage(to_qimage(pm))
        p.detail_rect = QRectF(rr)
        p.detail_key = key
        p.update()
        return True

    def _render_tick(self):
        if not self.model.is_open or not self.pages:
            return
        if tasks.is_busy():  # 后台任务正在用 PyMuPDF
            self._render_timer.start(200)
            return
        visible = self._visible_pages()
        if not visible:
            return
        vis = self.visible_scene_rect()
        cy = vis.center().y()
        visible.sort(key=lambda i: abs(self.pages[i].pos().y() + self.pages[i].h / 2 - cy))
        scale = self.render_scale()
        did = False
        for i in visible:  # 1. 看得见的页:底图
            p = self.pages[i]
            if p.full_key != self._base_key(i) or abs(p.full_scale - self._want_full(p)) > 1e-3:
                self._render_full(p)
                did = True
                break
        if not did:
            for i in visible:  # 2. 看得见的页:放大时的高清区域
                if self._render_detail(self.pages[i]):
                    did = True
                    break
        if not did:  # 3. 看得见的页:对象按当前缩放重新渲染
            n = 0
            for it in self.items.values():
                if it.ov.page in visible and abs(it.scale - scale) > 1e-3 and it.isVisible():
                    pg = self.pages[it.ov.page]
                    it.render((pg.w, pg.h), scale)
                    n += 1
                    if n >= 12:
                        break
            did = n > 0
        if not did:  # 4. 预读上下相邻的页
            lo, hi = min(visible), max(visible)
            for i in (hi + 1, lo - 1, hi + 2, lo - 2):
                if 0 <= i < len(self.pages):
                    p = self.pages[i]
                    if p.full_key != self._base_key(i) or abs(p.full_scale - self._want_full(p)) > 1e-3:
                        self._render_full(p)
                        did = True
                        break
        if did:
            self._render_timer.start(0)
        else:
            self._evict(visible)

    def _evict(self, visible):
        """离视野很远的页只保留低清图,省内存。"""
        lo, hi = min(visible) - 4, max(visible) + 4
        for p in self.pages:
            if (p.pno < lo or p.pno > hi) and p.full is not None:
                w = max(int(p.w * 0.6), 60)
                p.low = p.full.scaledToWidth(w, Qt.SmoothTransformation)
                p.full, p.full_key, p.full_scale, p.detail = None, None, 0.0, None

    # ------------------------------------------------------------------ 工具与鼠标
    def on_tool(self, name):
        self.edits.commit()
        old = self.tool
        self.tool = self.tools.get(name, self.tools["select"])
        if old is not self.tool:
            old.deactivate()
        self.tool.activate()
        self.set_hover(None, [])
        self.set_rubber(None)
        self.set_markup_preview(None, [])
        self.update_forms()
        self.viewport().setCursor(self.tool.cursor)

    def page_event(self, e, fixed_page=None):
        from ui.tools import PageEvent
        pos = e.position() if hasattr(e, "position") else QPointF(e.pos())
        sp = self.mapToScene(pos.toPoint())
        if fixed_page is not None:
            pno, inside = fixed_page, True
        else:
            pno = self.page_at_scene(sp)
            inside = pno is not None
            if pno is None:
                pno = self.page_at_scene(sp, nearest=True)
        if pno is None:
            return None
        x, y = self.to_page(pno, sp)
        return PageEvent(pno, x, y, inside, pos, sp, e.modifiers(), getattr(e, "button", lambda: None)())

    def mousePressEvent(self, e):
        self.setFocus(Qt.MouseFocusReason)
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and self._space):
            self._pan = (e.position(), self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if self.edits.active and self.edits.contains_view_pos(e.position()):
            self._pass_mouse = True
            super().mousePressEvent(e)
            return
        if e.button() == Qt.RightButton:
            return  # 右键交给 contextMenuEvent
        if e.button() != Qt.LeftButton or not self.model.is_open:
            return
        consumed = self.edits.active
        self.edits.commit()
        ev = self.page_event(e)
        if ev is None:
            return
        if consumed and self.tool.name == "text":
            return  # 第一次点击只是结束输入,不新建文字框
        self.tool.press(ev)

    def mouseMoveEvent(self, e):
        if self._pan is not None:
            p0, h0, v0 = self._pan
            d = e.position() - p0
            self.horizontalScrollBar().setValue(h0 - int(d.x()))
            self.verticalScrollBar().setValue(v0 - int(d.y()))
            return
        if self._pass_mouse:
            super().mouseMoveEvent(e)
            return
        if not self.model.is_open:
            return
        if e.buttons() & Qt.LeftButton:
            self.tool.move(e)
        else:
            if self.edits.active and self.edits.contains_view_pos(e.position()):
                self.viewport().setCursor(Qt.IBeamCursor)
                return
            ev = self.page_event(e)
            if ev is not None:
                self.tool.hover(ev)
            if self._space:
                self.viewport().setCursor(Qt.OpenHandCursor)

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.viewport().setCursor(Qt.OpenHandCursor if self._space else self.tool.cursor)
            return
        if self._pass_mouse:
            self._pass_mouse = False
            super().mouseReleaseEvent(e)
            return
        if e.button() == Qt.LeftButton and self.model.is_open:
            self.tool.release(e)

    def mouseDoubleClickEvent(self, e):
        if self.edits.active and self.edits.contains_view_pos(e.position()):
            self._pass_mouse = True
            super().mouseDoubleClickEvent(e)
            return
        if e.button() == Qt.LeftButton and self.model.is_open:
            ev = self.page_event(e)
            if ev is not None:
                self.tool.double_click(ev)

    def contextMenuEvent(self, e):
        if not self.model.is_open:
            return
        if self.edits.active and self.edits.contains_view_pos(QPointF(e.pos())):
            super().contextMenuEvent(e)  # 编辑框自己的右键菜单(复制/粘贴)
            return
        self.edits.commit()
        sp = self.mapToScene(e.pos())
        pno = self.page_at_scene(sp)
        if pno is None:
            return
        x, y = self.to_page(pno, sp)
        ov = self.hit_test(pno, x, y)
        if ov is not None and ov.uid not in self.state.selection:
            self.state.select([ov.uid])
        self.contextRequested.emit(e.globalPos(), pno, x, y, ov.uid if ov is not None else None)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            dy = e.angleDelta().y()
            if dy:
                self.zoom_by(1.0015 ** dy, e.position().toPoint())
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        if self.edits.active:
            super().keyPressEvent(e)
            return
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.viewport().setCursor(Qt.OpenHandCursor)
            return
        if self.tool.key(e):
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.viewport().setCursor(self.tool.cursor)
            return
        super().keyReleaseEvent(e)

    def focusOutEvent(self, e):
        self._space = False
        super().focusOutEvent(e)

    # ------------------------------------------------------------------ 拖放文件
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if not e.mimeData().hasUrls():
            super().dropEvent(e)
            return
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        sp = self.mapToScene(e.position().toPoint())
        pno = self.page_at_scene(sp) if self.pages else None
        x = y = 0.0
        if pno is not None:
            x, y = self.to_page(pno, sp)
        e.acceptProposedAction()
        self.filesDropped.emit(paths, -1 if pno is None else pno, x, y)
