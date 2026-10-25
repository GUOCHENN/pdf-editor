"""左侧页面缩略图栏、右侧属性面板。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QListView, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QSlider, QStyle, QStyledItemDelegate,
                               QVBoxLayout, QWidget)

import engine as E
from ui import icons, tasks
from ui.canvas import to_qimage
from ui.state import MARKUP_PALETTE, PALETTE, TOOL_GROUPS, TOOL_LABELS, style_group, tool_group
from ui.theme import T
from ui.tools import Ops
from ui.widgets import ActionList, ColorRow, Section, Segmented, TOOL_ICON, icon_button, label

THUMB_W = 112
ROLE_PIX = Qt.UserRole + 1
ROLE_SIZE = Qt.UserRole + 2


# ============================================================================
# 缩略图
# ============================================================================


class ThumbDelegate(QStyledItemDelegate):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel

    def sizeHint(self, opt, index):
        w, h = index.data(ROLE_SIZE) or (595, 842)
        th = THUMB_W * h / max(w, 1)
        return QSize(THUMB_W + 36, int(th) + 40)

    def paint(self, p: QPainter, opt, index):
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = opt.rect
        w, h = index.data(ROLE_SIZE) or (595, 842)
        tw = THUMB_W
        th = tw * h / max(w, 1)
        card = QRectF(r.x() + (r.width() - tw) / 2, r.y() + 8, tw, th)
        sel = bool(opt.state & QStyle.State_Selected)
        hover = bool(opt.state & QStyle.State_MouseOver)
        cur = index.row() == self.panel.current
        # 阴影
        p.setPen(Qt.NoPen)
        for i, a in ((1, 30), (3, 12)):
            c = T.q("page_shadow")
            c.setAlpha(a)
            p.setBrush(c)
            p.drawRoundedRect(card.adjusted(-i * 0.5, 0, i * 0.5, i), 3 + i, 3 + i)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(card, 3, 3)
        pix = index.data(ROLE_PIX)
        if isinstance(pix, QPixmap) and not pix.isNull():
            p.drawPixmap(card, pix, QRectF(pix.rect()))
        # 选中 / 当前页 / 悬停 外框
        if sel or cur or hover:
            col = T.q("primary") if (sel or cur) else T.q("border_strong")
            pen = QPen(col, 2.5 if (sel or cur) else 1.5)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(card.adjusted(-3, -3, 3, 3), 6, 6)
        # 页码
        num = str(index.row() + 1)
        fm = p.fontMetrics()
        nw = max(fm.horizontalAdvance(num) + 14, 24)
        nr = QRectF(r.x() + (r.width() - nw) / 2, card.bottom() + 8, nw, 19)
        if cur:
            p.setPen(Qt.NoPen)
            p.setBrush(T.q("primary"))
            p.drawRoundedRect(nr, 9.5, 9.5)
            p.setPen(T.q("on_primary"))
        else:
            p.setPen(T.q("text2"))
        p.drawText(nr, Qt.AlignCenter, num)
        p.restore()


class ThumbList(QListWidget):
    pagesMoved = Signal(list, int)
    filesDropped = Signal(list, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Thumbs")
        self.setViewMode(QListView.ListMode)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDropIndicatorShown(False)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._drop_y = None

    def _dest(self, pos):
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.count(), (self.visualRect(self.model().index(self.count() - 1, 0)).bottom()
                                  if self.count() else 0)
        r = self.visualRect(idx)
        if pos.y() < r.center().y():
            return idx.row(), r.top()
        return idx.row() + 1, r.bottom()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.source() is self:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls() or e.source() is self:
            _d, y = self._dest(e.position().toPoint())
            self._drop_y = y
            self.viewport().update()
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, e):
        self._drop_y = None
        self.viewport().update()
        super().dragLeaveEvent(e)

    def dropEvent(self, e):
        dest, _y = self._dest(e.position().toPoint())
        self._drop_y = None
        self.viewport().update()
        if e.mimeData().hasUrls() and e.source() is not self:
            paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
            e.setDropAction(Qt.CopyAction)
            e.accept()
            if paths:
                self.filesDropped.emit(paths, dest)
            return
        rows = sorted(i.row() for i in self.selectedIndexes())
        # 不让 QListWidget 自己挪动项目(也不要删除源项目):页面顺序由文档决定,改完文档再重建列表
        e.setDropAction(Qt.CopyAction)
        e.accept()
        if rows and not (rows == list(range(rows[0], rows[-1] + 1)) and rows[0] <= dest <= rows[-1] + 1):
            self.pagesMoved.emit(rows, dest)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._drop_y is not None:
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(T.q("primary"), 3, Qt.SolidLine, Qt.RoundCap))
            y = self._drop_y
            p.drawLine(14, y, self.viewport().width() - 14, y)
            p.setBrush(T.q("primary"))
            p.drawEllipse(QRectF(10, y - 4, 8, 8))
            p.end()


class ThumbnailPanel(QWidget):
    pageClicked = Signal(int)
    contextRequested = Signal(object)
    pagesMoved = Signal(list, int)
    filesDropped = Signal(list, int)

    def __init__(self, model, state, parent=None):
        super().__init__(parent)
        self.model, self.state = model, state
        self.setObjectName("SidePanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(THUMB_W + 56)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(4)
        head = QHBoxLayout()
        head.setContentsMargins(16, 0, 8, 0)
        self.title = label("页面", "PanelTitle")
        self.count = label("", "Faint")
        head.addWidget(self.title)
        head.addWidget(self.count)
        head.addStretch(1)
        self.more = icon_button("more", "页面操作", size=28, icon_size=18)
        self.more.clicked.connect(lambda: self.contextRequested.emit(self.more.mapToGlobal(self.more.rect().bottomLeft())))
        head.addWidget(self.more)
        lay.addLayout(head)
        self.list = ThumbList()
        self.list.setItemDelegate(ThumbDelegate(self))
        lay.addWidget(self.list, 1)
        self.current = 0
        self._sync = False
        self.list.itemSelectionChanged.connect(self._sel_changed)
        self.list.itemClicked.connect(lambda it: self.pageClicked.emit(self.list.row(it)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.pagesMoved.connect(self.pagesMoved)
        self.list.filesDropped.connect(self.filesDropped)
        self.list.verticalScrollBar().valueChanged.connect(lambda _v: self._kick())
        self.stale: set[int] = set()
        self.keys: dict[int, object] = {}
        self.timer = QTimer(self, singleShot=True, interval=0, timeout=self._tick)
        self.soon = QTimer(self, singleShot=True, interval=350, timeout=self._kick)
        model.opened.connect(self.rebuild)
        model.structureChanged.connect(self.rebuild)
        model.overlaysChanged.connect(self._overlays)
        state.currentPageChanged.connect(self.set_current)
        state.pageSelectionChanged.connect(self._apply_page_sel)

    # ---- 列表
    def rebuild(self):
        self._sync = True
        old_n = self.list.count()
        n = self.model.page_count()
        if n != old_n:
            self.list.clear()
            for i in range(n):
                it = QListWidgetItem()
                it.setData(ROLE_SIZE, self.model.page_size(i))
                self.list.addItem(it)
        else:
            for i in range(n):
                self.list.item(i).setData(ROLE_SIZE, self.model.page_size(i))
        self.keys.clear()
        self.stale = set(range(n))
        self.count.setText(f"{n} 页" if n else "")
        self.current = min(self.state.current_page, max(n - 1, 0))
        self._sync = False
        self._apply_page_sel()
        self._kick()

    def _overlays(self, pages):
        n = self.list.count()
        self.stale |= set(range(n)) if pages is None else {p for p in pages if 0 <= p < n}
        self.soon.start()

    def set_current(self, pno):
        old = self.current
        self.current = pno
        if len(self.state.page_sel) <= 1:
            self.state.set_page_selection({pno})
        for r in (old, pno):
            it = self.list.item(r)
            if it is not None:
                self.list.update(self.list.indexFromItem(it))
        it = self.list.item(pno)
        if it is not None:
            self.list.scrollToItem(it, QAbstractItemView.EnsureVisible)

    def _apply_page_sel(self):
        sel = self.state.page_sel or {self.current}
        self._sync = True
        for i in range(self.list.count()):
            it = self.list.item(i)
            want = i in sel
            if it.isSelected() != want:
                it.setSelected(want)
        self._sync = False

    def _sel_changed(self):
        if self._sync:
            return
        rows = {self.list.row(it) for it in self.list.selectedItems()}
        if rows:
            self.state.set_page_selection(rows)

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if it is not None and not it.isSelected():
            self.list.clearSelection()
            it.setSelected(True)
            self.pageClicked.emit(self.list.row(it))
        self.contextRequested.emit(self.list.viewport().mapToGlobal(pos))

    # ---- 渲染
    def _kick(self):
        if not self.timer.isActive():
            self.timer.start(0)

    def _visible_rows(self):
        vp = self.list.viewport().rect()
        x = vp.center().x()

        def row_at(y0, step):
            for k in range(12):  # 视口边缘可能正好落在两项之间的空隙里,往里多试几个点
                r = self.list.indexAt(QPoint(x, y0 + k * step)).row()
                if r >= 0:
                    return r
            return -1

        top = max(row_at(vp.top(), 8), 0)
        bottom = row_at(vp.bottom(), -8)
        if bottom < 0:
            bottom = self.list.count() - 1
        return top, bottom

    def _tick(self):
        if not self.model.is_open or not self.stale:
            return
        if tasks.is_busy():
            self.timer.start(250)
            return
        top, bottom = self._visible_rows()
        lo, hi = max(0, top - 6), min(self.list.count() - 1, bottom + 6)
        cand = [i for i in self.stale if lo <= i <= hi]
        if not cand:
            # 离得远的页先不渲染;已经渲染过但离开视野很远的释放掉
            for i in range(self.list.count()):
                if (i < lo - 40 or i > hi + 40) and self.list.item(i).data(ROLE_PIX) is not None:
                    self.list.item(i).setData(ROLE_PIX, None)
                    self.stale.add(i)
            return
        i = min(cand, key=lambda r: (not (top <= r <= bottom), abs(r - top)))
        self.stale.discard(i)
        try:
            self._render(i)
        except Exception:  # noqa: BLE001 — 个别损坏的页面不影响其它页
            pass
        if self.stale:
            self.timer.start(0)

    def _render(self, i):
        m = self.model
        w, h = m.page_size(i)
        dpr = self.devicePixelRatioF()
        scale = THUMB_W * dpr / w
        page, tmp = E.prepared_page(m.doc, i, m.covers(i), m.patches(i))
        pm = page.get_pixmap(matrix=E.fitz.Matrix(scale, scale), alpha=False)
        if tmp is not None:
            tmp.close()
        img = to_qimage(pm)
        items = m.page_overlays(i)
        if items:
            layer = E.build_layer(items, (w, h), True)
            if layer is not None:
                lp = layer[0].get_pixmap(matrix=E.fitz.Matrix(scale, scale), alpha=True)
                layer.close()
                p = QPainter(img)
                p.drawImage(0, 0, to_qimage(lp))
                p.end()
        pix = QPixmap.fromImage(img)
        pix.setDevicePixelRatio(dpr)
        self.list.item(i).setData(ROLE_PIX, pix)


# ============================================================================
# 属性面板
# ============================================================================

KIND_TITLES = {"text": "文字", "rect": "矩形", "ellipse": "椭圆", "line": "直线", "arrow": "箭头", "ink": "笔迹",
               "image": "图片", "redact": "涂黑区域", "highlight": "高亮", "underline": "下划线", "strike": "删除线"}
KIND_ICONS = {"text": "add_text", "rect": "rect", "ellipse": "ellipse", "line": "line", "arrow": "arrow", "ink": "pen",
              "image": "image", "redact": "redact", "highlight": "highlight", "underline": "underline",
              "strike": "strike"}


class Inspector(QWidget):
    """右侧面板:选中对象时显示它的属性;没选中时显示当前工具的默认样式和页面/文档操作。"""

    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self.view = win.view
        self.model, self.state = win.model, win.state
        self.setObjectName("Inspector")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(280)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # 标题
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 14, 12, 8)
        hl.setSpacing(10)
        self.head_icon = QPushButton()
        self.head_icon.setObjectName("HeadIcon")
        self.head_icon.setFixedSize(34, 34)
        self.head_icon.setIconSize(QSize(18, 18))
        self.head_icon.setFocusPolicy(Qt.NoFocus)
        hl.addWidget(self.head_icon)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        self.head_title = label("", "PanelTitle")
        self.head_sub = label("", "Faint")
        tv.addWidget(self.head_title)
        tv.addWidget(self.head_sub)
        hl.addLayout(tv, 1)
        outer.addWidget(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 0, 0, 16)
        self.body.setSpacing(2)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self._updating = False
        self._build()
        self.state.selectionChanged.connect(self.refresh)
        self.state.toolChanged.connect(lambda _t: self.refresh())
        self.state.currentPageChanged.connect(lambda _p: self.refresh_head())
        self.model.overlaysChanged.connect(lambda _p: self.refresh())
        self.model.opened.connect(self.refresh)
        self.model.structureChanged.connect(self.refresh)
        self.refresh()

    # ---- 构建
    def _build(self):
        B = self.body
        # 工具变体
        self.sec_variant = Section("类型")
        self.variants = {}
        for g, tools in TOOL_GROUPS.items():
            seg = Segmented([(t, TOOL_LABELS[t].split("(")[0].replace("遮盖", ""), None) for t in tools])
            seg.changed.connect(self.state.set_tool)
            self.sec_variant.add(seg)
            self.variants[g] = seg
        B.addWidget(self.sec_variant)

        # 文字
        self.sec_text = Section("文字")
        self.font = QComboBox()
        self.font.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.font.setMinimumContentsLength(6)
        for k, lab in E.font_choices():
            self.font.addItem(lab, k)
        self.font.currentIndexChanged.connect(lambda _i: self._set("font", self.font.currentData()))
        self.sec_text.row("字体", self.font)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        self.size = QDoubleSpinBox()
        self.size.setRange(4, 300)
        self.size.setDecimals(1)
        self.size.setSingleStep(1)
        self.size.setSuffix(" pt")
        self.size.setMinimumWidth(60)
        self.size.valueChanged.connect(lambda v: self._set("size", round(v, 2)))
        rl.addWidget(self.size, 1)
        self.bold = self._toggle("bold", "粗体")
        self.italic = self._toggle("italic", "斜体")
        rl.addWidget(self.bold)
        rl.addWidget(self.italic)
        self.sec_text.row("字号", row)
        self.text_color = ColorRow(PALETTE)
        self.text_color.picked.connect(lambda c: self._set("color", c or "#000000"))
        self.sec_text.row("颜色", self.text_color, vertical=True)
        B.addWidget(self.sec_text)

        # 形状 / 画笔
        self.sec_shape = Section("外观")
        self.stroke = ColorRow(PALETTE, allow_none=True)
        self.stroke.picked.connect(lambda c: self._set("stroke", c))
        self.stroke_row = self.sec_shape.row("边框", self.stroke, vertical=True)
        self.width_box = QWidget()
        wl = QHBoxLayout(self.width_box)
        wl.setContentsMargins(0, 0, 0, 0)
        self.width_slider = QSlider(Qt.Horizontal)
        self.width_slider.setRange(1, 40)
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.5, 40)
        self.width_spin.setSingleStep(0.5)
        self.width_spin.setDecimals(1)
        self.width_spin.setFixedWidth(64)
        self.width_slider.valueChanged.connect(lambda v: self._set("width", v / 2) if not self._updating else None)
        self.width_spin.valueChanged.connect(lambda v: self._set("width", round(v, 2)))
        wl.addWidget(self.width_slider, 1)
        wl.addWidget(self.width_spin)
        self.sec_shape.row("粗细", self.width_box)
        self.fill = ColorRow(PALETTE, allow_none=True)
        self.fill.picked.connect(lambda c: self._set("fill", c))
        self.fill_row = self.sec_shape.row("填充", self.fill, vertical=True)
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.valueChanged.connect(lambda v: self._set("opacity", v / 100) if not self._updating else None)
        self.opacity_row = self.sec_shape.row("不透明", self.opacity)
        B.addWidget(self.sec_shape)

        # 标记颜色 / 白块颜色
        self.sec_color = Section("颜色")
        self.mark_color = ColorRow(MARKUP_PALETTE)
        self.mark_color.picked.connect(lambda c: self._set("color", c))
        self.sec_color.add(self.mark_color)
        self.cover_color = ColorRow(["#FFFFFF", "#F6F7F9", "#FFF8E1", "#E8EAED", "#000000"])
        self.cover_color.picked.connect(lambda c: self._set("fill", c or "#FFFFFF"))
        self.sec_color.add(self.cover_color)
        B.addWidget(self.sec_color)

        # 说明文字
        self.sec_note = Section("说明")
        self.note = label("", "Muted", wrap=True)
        self.sec_note.add(self.note)
        B.addWidget(self.sec_note)

        # 排列
        self.sec_arrange = Section("排列")
        grid = QWidget()
        gl = QHBoxLayout(grid)
        gl.setContentsMargins(0, 0, 0, 0)
        gl.setSpacing(6)
        for ic, tip, fn in (("front", "置于顶层", lambda: Ops.reorder(self.view, True)),
                            ("back", "置于底层", lambda: Ops.reorder(self.view, False)),
                            ("copy", "复制一份  Ctrl+D", lambda: Ops.duplicate(self.view)),
                            ("trash", "删除  Delete", lambda: Ops.delete(self.view))):
            b = icon_button(ic, tip, size=36, icon_size=19, obj="Boxed")
            b.clicked.connect(fn)
            gl.addWidget(b)
        gl.addStretch(1)
        self.sec_arrange.add(grid)
        self.edit_btn = QPushButton("编辑文字")
        icons.bind(self.edit_btn, "edit_text", size=16)
        self.edit_btn.clicked.connect(self._edit_selected)
        self.sec_arrange.add(self.edit_btn)
        B.addWidget(self.sec_arrange)

        # 页面
        self.sec_page = Section("页面")
        pa = ActionList()
        w = self.win
        pa.add("rotate_left", "向左旋转", lambda: w.cmd_rotate(-90))
        pa.add("rotate_right", "向右旋转", lambda: w.cmd_rotate(90))
        pa.add("page_add", "在后面插入空白页", w.cmd_insert_blank)
        pa.add("merge", "插入 PDF / 图片…", w.cmd_insert_files)
        pa.add("extract", "提取为新 PDF…", w.cmd_extract)
        pa.add("trash", "删除页面", w.cmd_delete_pages, danger=True)
        self.sec_page.add(pa)
        B.addWidget(self.sec_page)

        # 文档
        self.sec_doc = Section("文档工具")
        da = ActionList()
        da.add("ocr", "文字识别 OCR…", w.cmd_ocr, "扫描件变成可搜索、可编辑的文字")
        da.add("convert", "格式转换…", w.cmd_convert, "PDF ⇄ Word / Excel / PPT / 图片 / 文本")
        da.add("watermark", "添加水印…", w.cmd_watermark)
        da.add("numbers", "添加页码…", w.cmd_page_numbers)
        da.add("split", "拆分 PDF…", w.cmd_split)
        da.add("lock", "加密另存为…", w.cmd_save_encrypted)
        da.add("export", "导出为图片…", w.cmd_export_images)
        self.sec_doc.add(da)
        B.addWidget(self.sec_doc)
        B.addStretch(1)

    def _toggle(self, name, tip):
        b = QPushButton()
        b.setObjectName("Toggle")
        b.setCheckable(True)
        b.setToolTip(tip)
        icons.bind(b, name, size=16)
        b.setIconSize(QSize(16, 16))
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(lambda checked: self._set(name, checked))
        return b

    def _edit_selected(self):
        sel = self.view.selected_overlays()
        if len(sel) == 1 and sel[0].kind == "text":
            self.view.edits.begin(sel[0])

    # ---- 当前上下文
    def context(self):
        """返回 (样式分组, 取值来源 dict, 选中的对象列表)。"""
        if self.view.edits.active:
            ov = self.view.edits.overlay
            return "text", ov.props, [ov]
        sel = self.view.selected_overlays()
        if sel:
            groups = {style_group(o) for o in sel}
            g = groups.pop() if len(groups) == 1 else None
            if g is None and all(o.kind == "rect" and not o.props.get("stroke") for o in sel):
                g = "shape"
            return g, sel[0].props, sel
        g = tool_group(self.state.tool)
        return g, (self.state.styles.get(g, {}) if g else {}), []

    def _set(self, key, value):
        if self._updating:
            return
        g, _vals, sel = self.context()
        if g is None:
            return
        if sel or self.view.edits.active:
            Ops.set_prop(self.view, key, value)
        if g in self.state.styles and key in self.state.styles[g] or (g == "whiteout" and key == "fill"):
            self.state.set_style(g, key, value)
        self.refresh()

    def refresh_head(self):
        g, _vals, sel = self.context()
        if self.view.edits.active:
            title, ic, sub = "正在编辑文字", "edit_text", "Esc 完成 · 行首退格可接到上一行"
        elif sel:
            if len(sel) > 1:
                title, ic = f"已选中 {len(sel)} 个对象", "select"
            else:
                o = sel[0]
                k = o.props["sub"] if o.kind == "markup" else o.kind
                if o.kind == "rect" and not o.props.get("stroke") and o.props.get("fill"):
                    title, ic = "白块", "whiteout"
                else:
                    title, ic = KIND_TITLES.get(k, "对象"), KIND_ICONS.get(k, "select")
            sub = f"第 {sel[0].page + 1} 页"
        elif self.model.is_open and self.state.tool != "select":
            t = self.state.tool
            title, ic = TOOL_LABELS[t], TOOL_ICON[t]
            sub = "新建对象的默认样式"
        elif self.model.is_open:
            title, ic = f"第 {self.state.current_page + 1} 页", "page"
            sub = f"共 {self.model.page_count()} 页 · 没有选中对象"
        else:
            title, ic, sub = "未打开文档", "doc", ""
        self.head_title.setText(title)
        self.head_sub.setText(sub)
        icons.bind(self.head_icon, ic, "primary", size=18)

    def refresh(self):
        self.refresh_head()
        g, vals, sel = self.context()
        tool = self.state.tool
        editing = self.view.edits.active
        is_open = self.model.is_open
        self._updating = True
        try:
            grp = next((gg for gg, ts in TOOL_GROUPS.items() if tool in ts), None)
            show_variant = grp is not None and not sel and not editing
            self.sec_variant.setVisible(show_variant)
            for gg, seg in self.variants.items():
                seg.setVisible(gg == grp)
                if gg == grp:
                    seg.set_value(tool)
            self.sec_text.setVisible(g == "text")
            if g == "text":
                fk = vals.get("font") if vals.get("font") not in (None, "auto") else E.default_font_key()
                i = self.font.findData(fk)
                if i < 0:
                    self.font.addItem(E.font_label(fk), fk)
                    i = self.font.count() - 1
                self.font.setCurrentIndex(i)
                self.size.setValue(float(vals.get("size", 14)))
                self.bold.setChecked(bool(vals.get("bold")))
                self.italic.setChecked(bool(vals.get("italic")))
                self.text_color.set_value(vals.get("color"))
            shape = g in ("shape", "ink")
            self.sec_shape.setVisible(shape)
            if shape:
                kinds = {o.kind for o in sel} if sel else ({tool} if tool in ("rect", "ellipse", "line", "arrow", "ink")
                                                           else set())
                closed = bool(kinds) and kinds <= {"rect", "ellipse"}
                self.sec_shape.title.setText("画笔" if g == "ink" else "外观")
                self.stroke_row.label.setText("颜色" if not closed else "边框")
                self.stroke.swatches[0].setVisible(closed)  # 只有矩形、椭圆可以"无边框"
                self.stroke.set_value(vals.get("stroke"))
                w = float(vals.get("width", 2) or 0)
                self.width_slider.setValue(int(round(w * 2)))
                self.width_spin.setValue(w)
                self.fill_row.setVisible(closed)
                self.opacity_row.setVisible(closed)
                self.fill.set_value(vals.get("fill"))
                self.opacity.setValue(int(round(float(vals.get("opacity", 1.0)) * 100)))
            mark = g in ("highlight", "underline", "strike")
            cover = g == "whiteout"
            self.sec_color.setVisible(mark or cover)
            self.mark_color.setVisible(mark)
            self.cover_color.setVisible(cover)
            if mark:
                self.mark_color.set_value(vals.get("color"))
            if cover:
                self.cover_color.set_value(vals.get("fill"))
            note = ""
            kinds = {o.kind for o in sel}
            if "redact" in kinds or (not sel and tool == "redact"):
                note = "保存时,涂黑区域里的文字、图片和图形会被永久删除,无法恢复。只是想遮住的话请用「白块」。"
            elif "image" in kinds:
                note = "拖动移动,拖四角等比缩放(按住 Shift 自由缩放)。"
            elif not sel and tool == "ink":
                note = "按住左键书写,松开后可以接着写下一笔。写完按 Esc 或切换到「选择」。"
            elif not sel and tool == "form":
                note = "蓝色方框是可以填写的表单域。中文内容会直接写在页面上。"
            elif not sel and tool == "edit":
                note = "点一下页面上的文字就能修改;拖拽框选可以一次改好几行。\n行首按退格会接到上一行,↑ ↓ 可以换行继续改。"
            self.sec_note.setVisible(bool(note))
            self.note.setText(note)
            self.sec_arrange.setVisible(bool(sel) and not editing)
            self.edit_btn.setVisible(len(sel) == 1 and sel[0].kind == "text" and not editing)
            idle = is_open and not sel and not editing and tool == "select"
            self.sec_page.setVisible(idle)
            self.sec_doc.setVisible(idle)
        finally:
            self._updating = False
