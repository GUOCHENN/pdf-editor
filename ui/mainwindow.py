"""主窗口:顶栏(菜单、文档名、工具栏、撤销重做、查找、保存)、左侧缩略图、中间画布、右侧属性面板、底部状态栏。

所有命令(cmd_*)都在这里;它们只调用 DocumentModel / PageView / engine,不直接操作界面细节。
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QMainWindow, QMenu, QPushButton,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import engine as E
import ocr
from ui import dialogs as D
from ui import icons, theme
from ui.canvas import PageView
from ui.model import DocumentModel
from ui.panels import Inspector, ThumbnailPanel
from ui.state import EditorState, TOOL_HINTS, TOOL_KEYS
from ui.tasks import run_task
from ui.tools import Ops
from ui.welcome import Logo, WelcomePage
from ui.widgets import PageNav, SearchBox, Toast, ToolPalette, ZoomControl, divider, icon_button, label

APP = "PDF 编辑器"


def open_file(path: str):
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — 没有关联的程序时,退而打开所在文件夹
        reveal_file(path)


def reveal_file(path: str):
    """在资源管理器里打开文件所在的文件夹并选中它。"""
    import subprocess
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    except Exception:  # noqa: BLE001
        pass


OPEN_FILTER = "PDF 和图片 (*.pdf *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;PDF 文件 (*.pdf);;所有文件 (*.*)"
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;所有文件 (*.*)"


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("PDFEditor", "PDFEditor")
        self.model = DocumentModel(self)
        self.state = EditorState(self)
        self.last_dir = self.settings.value("last_dir", str(Path.home()))
        self.hits: list = []
        self.hit_idx = -1
        self._convert_win = None
        self.setWindowTitle(APP)
        self.setAcceptDrops(True)
        self.resize(1360, 880)
        self.setMinimumSize(980, 620)

        self.view = PageView(self.model, self.state)
        self._build()
        self._build_actions()
        self._connect()
        self.refresh_title()
        self.refresh_history()
        self.refresh_status()
        self.welcome.set_recent(self.recent())
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    # ======================================================================
    # 界面
    # ======================================================================
    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_appbar())
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.welcome = WelcomePage()
        self.stack.addWidget(self.welcome)
        editor = QWidget()
        h = QHBoxLayout(editor)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.thumbs = ThumbnailPanel(self.model, self.state)
        h.addWidget(self.thumbs)
        self.center = QWidget()
        self.center.setObjectName("Center")
        cl = QVBoxLayout(self.center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.view)
        h.addWidget(self.center, 1)
        self.inspector = Inspector(self)
        h.addWidget(self.inspector)
        self.stack.addWidget(editor)
        self.toast = Toast(self.center)
        v.addWidget(self._build_statusbar())
        self.thumbs.setVisible(self.settings.value("show_thumbs", True, type=bool))
        self.inspector.setVisible(self.settings.value("show_inspector", True, type=bool))

    def _build_appbar(self):
        bar = QWidget()
        bar.setObjectName("AppBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(58)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 12, 0)
        h.setSpacing(6)
        left = QWidget()
        lh = QHBoxLayout(left)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.setSpacing(8)
        self.menu_btn = icon_button("menu", "菜单", size=36)
        self.menu_btn.setPopupMode(QToolButton.InstantPopup)
        lh.addWidget(self.menu_btn)
        self.logo = Logo(30)
        lh.addWidget(self.logo)
        tv = QVBoxLayout()
        tv.setSpacing(0)
        tv.setContentsMargins(4, 0, 0, 0)
        trow = QHBoxLayout()
        trow.setSpacing(6)
        self.doc_title = label(APP, "DocTitle")
        self.dot = label("●", "Dot")
        self.dot.setToolTip("有未保存的修改")
        trow.addWidget(self.doc_title)
        trow.addWidget(self.dot)
        trow.addStretch(1)
        tv.addLayout(trow)
        self.doc_sub = label("", "Faint")
        tv.addWidget(self.doc_sub)
        lh.addLayout(tv, 1)
        h.addWidget(left, 1)

        self.palette = ToolPalette(self.state)
        h.addWidget(self.palette, 0, Qt.AlignVCenter)

        right = QWidget()
        rh = QHBoxLayout(right)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.setSpacing(4)
        rh.addStretch(1)
        self.undo_btn = icon_button("undo", "撤销  Ctrl+Z", size=34)
        self.redo_btn = icon_button("redo", "重做  Ctrl+Y", size=34)
        rh.addWidget(self.undo_btn)
        rh.addWidget(self.redo_btn)
        d = divider()
        d.setFixedHeight(22)
        rh.addSpacing(4)
        rh.addWidget(d)
        rh.addSpacing(4)
        self.search = SearchBox()
        rh.addWidget(self.search)
        rh.addSpacing(4)
        self.convert_btn = QPushButton(" 转换")
        self.convert_btn.setObjectName("Ghost")
        icons.bind(self.convert_btn, "convert", size=18)
        self.convert_btn.setIconSize(QSize(18, 18))
        self.convert_btn.setToolTip("把正在编辑的文档直接转换成 Word / Excel / PPT / 图片……(不用先保存)")
        self.convert_btn.setCursor(Qt.PointingHandCursor)
        self.convert_btn.setProperty("hasMenu", True)
        # 打开文档后,点"转换"弹出格式菜单,选一个就直接转换当前文档(包括还没保存的修改)
        self.convert_menu = QMenu(self)
        self.convert_menu.aboutToShow.connect(self._fill_convert_menu)
        rh.addWidget(self.convert_btn)
        self.save_btn = QPushButton(" 保存")
        self.save_btn.setObjectName("Primary")
        icons.bind(self.save_btn, "save", "on_primary", size=17)
        self.save_btn.setIconSize(QSize(17, 17))
        self.save_btn.setToolTip("保存  Ctrl+S")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        rh.addWidget(self.save_btn)
        self.panel_btn = icon_button("panel_right", "显示 / 隐藏属性面板", size=34, checkable=True)
        rh.addWidget(self.panel_btn)
        h.addWidget(right, 1)
        # 没打开文档时(欢迎页)隐藏的控件
        self.doc_only = [self.palette, self.undo_btn, self.redo_btn, d, self.search, self.save_btn, self.panel_btn,
                         self.doc_sub]
        return bar

    def _build_statusbar(self):
        bar = QWidget()
        bar.setObjectName("StatusBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(38)
        h = QHBoxLayout(bar)
        h.setContentsMargins(10, 0, 10, 0)
        h.setSpacing(6)
        self.thumbs_btn = icon_button("sidebar", "显示 / 隐藏页面缩略图", size=28, icon_size=17, checkable=True)
        h.addWidget(self.thumbs_btn)
        self.hint = label("", "Hint")
        h.addWidget(self.hint, 1)
        self.page_nav = PageNav()
        h.addWidget(self.page_nav)
        d = divider()
        d.setFixedHeight(18)
        h.addSpacing(6)
        h.addWidget(d)
        h.addSpacing(6)
        self.zoom_ctl = ZoomControl()
        h.addWidget(self.zoom_ctl)
        self.statusbar = bar
        return bar

    # ======================================================================
    # 动作与快捷键
    # ======================================================================
    def _act(self, text, fn, shortcut=None, icon=None, needs_doc=True):
        a = QAction(text, self)
        if icon:
            icons.bind(a, icon)
        if shortcut:
            seqs = shortcut if isinstance(shortcut, (list, tuple)) else [shortcut]
            a.setShortcuts([QKeySequence(s) for s in seqs])
            a.setShortcutContext(Qt.WindowShortcut)
        a.triggered.connect(lambda _c=False: fn())
        a.needs_doc = needs_doc
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self.acts = {}
        A["open"] = self._act("打开…", self.cmd_open, "Ctrl+O", "open", False)
        A["save"] = self._act("保存", self.cmd_save, "Ctrl+S", "save")
        A["save_as"] = self._act("另存为…", self.cmd_save_as, "Ctrl+Shift+S")
        A["encrypt"] = self._act("加密另存为…", self.cmd_save_encrypted, None, "lock")
        A["export_img"] = self._act("导出为图片…", self.cmd_export_images, None, "export")
        A["export_txt"] = self._act("导出全部文字…", self.cmd_export_text)
        A["close"] = self._act("关闭文档", self.cmd_close, "Ctrl+W")
        A["quit"] = self._act("退出", self.close, None, None, False)
        A["undo"] = self._act("撤销", self.cmd_undo, "Ctrl+Z", "undo")
        A["redo"] = self._act("重做", self.cmd_redo, ["Ctrl+Y", "Ctrl+Shift+Z"], "redo")
        A["copy"] = self._act("复制", lambda: Ops.copy(self.view), "Ctrl+C", "copy")
        A["paste"] = self._act("粘贴", self.cmd_paste, "Ctrl+V", "paste")
        A["dup"] = self._act("复制一份", lambda: Ops.duplicate(self.view), "Ctrl+D")
        A["delete"] = self._act("删除对象", lambda: Ops.delete(self.view), None, "trash")
        A["select_all"] = self._act("全选本页对象", lambda: Ops.select_all(self.view), "Ctrl+A")
        A["find"] = self._act("查找…", self.search.focus, "Ctrl+F", "search")
        A["image"] = self._act("插入图片 / 签名…", self.cmd_add_image, "I", "image")
        A["rot_l"] = self._act("向左旋转 90°", lambda: self.cmd_rotate(-90), None, "rotate_left")
        A["rot_r"] = self._act("向右旋转 90°", lambda: self.cmd_rotate(90), None, "rotate_right")
        A["blank"] = self._act("在后面插入空白页", self.cmd_insert_blank, None, "page_add")
        A["insert"] = self._act("插入 PDF / 图片…", self.cmd_insert_files, None, "merge")
        A["up"] = self._act("上移", lambda: self.cmd_move(-1))
        A["down"] = self._act("下移", lambda: self.cmd_move(1))
        A["del_pages"] = self._act("删除页面", self.cmd_delete_pages, None, "trash")
        A["extract"] = self._act("提取为新 PDF…", self.cmd_extract, None, "extract")
        A["split"] = self._act("拆分 PDF…", self.cmd_split, None, "split")
        A["convert"] = self._act("格式转换…", self.cmd_convert, "Ctrl+Shift+C", "convert", False)
        A["merge"] = self._act("合并多个文件为新 PDF…", self.cmd_merge_files, None, "merge", False)
        A["ocr"] = self._act("文字识别 OCR…", self.cmd_ocr, None, "ocr")
        A["watermark"] = self._act("添加水印…", self.cmd_watermark, None, "watermark")
        A["numbers"] = self._act("添加页码…", self.cmd_page_numbers, None, "numbers")
        A["zoom_in"] = self._act("放大", lambda: self.view.zoom_by(1.2), ["Ctrl+=", "Ctrl++"], "zoom_in")
        A["zoom_out"] = self._act("缩小", lambda: self.view.zoom_by(1 / 1.2), "Ctrl+-", "zoom_out")
        A["zoom_100"] = self._act("实际大小 100%", lambda: self.view.set_zoom(1.0), "Ctrl+0")
        A["fit_w"] = self._act("适合宽度", self.view.fit_width, "Ctrl+9", "fit_width")
        A["fit_p"] = self._act("适合整页", self.view.fit_page, "Ctrl+8", "fit_page")
        A["thumbs"] = self._act("页面缩略图", self.toggle_thumbs, None, "sidebar", False)
        A["thumbs"].setCheckable(True)
        A["inspector"] = self._act("属性面板", self.toggle_inspector, None, "panel_right", False)
        A["inspector"].setCheckable(True)
        A["dark"] = self._act("深色模式", self.toggle_theme, None, "moon", False)
        A["dark"].setCheckable(True)
        A["help"] = self._act("使用说明", lambda: D.show_help(self), "F1", "help", False)
        A["about"] = self._act("关于", lambda: D.show_about(self), None, "info", False)
        for tool, key in TOOL_KEYS.items():  # 单字母切换工具(在输入框里打字时不会触发)
            self._act(f"工具:{tool}", lambda t=tool: self.set_tool(t), key)

        m = QMenu(self)
        self.main_menu = m
        m.addAction(A["open"])
        self.recent_menu = m.addMenu("最近打开")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addAction(A["save"])
        m.addAction(A["save_as"])
        m.addAction(A["encrypt"])
        m.addSeparator()
        sub = m.addMenu("导出")
        sub.addAction(A["export_img"])
        sub.addAction(A["export_txt"])
        sub.addAction(A["extract"])
        sub.addAction(A["split"])
        m.addSeparator()
        sub = m.addMenu("编辑")
        for k in ("undo", "redo", None, "copy", "paste", "dup", "delete", "select_all", None, "find"):
            sub.addSeparator() if k is None else sub.addAction(A[k])
        sub = m.addMenu("页面")
        for k in ("rot_l", "rot_r", None, "blank", "insert", None, "up", "down", None, "del_pages"):
            sub.addSeparator() if k is None else sub.addAction(A[k])
        sub = m.addMenu("工具")
        for k in ("convert", "ocr", "merge", None, "watermark", "numbers", None, "image"):
            sub.addSeparator() if k is None else sub.addAction(A[k])
        sub = m.addMenu("视图")
        for k in ("zoom_in", "zoom_out", "zoom_100", "fit_w", "fit_p", None, "thumbs", "inspector", "dark"):
            sub.addSeparator() if k is None else sub.addAction(A[k])
        m.addSeparator()
        m.addAction(A["help"])
        m.addAction(A["about"])
        m.addSeparator()
        m.addAction(A["close"])
        m.addAction(A["quit"])
        self.menu_btn.setMenu(m)
        A["thumbs"].setChecked(self.thumbs.isVisibleTo(self) or self.settings.value("show_thumbs", True, type=bool))
        A["inspector"].setChecked(self.settings.value("show_inspector", True, type=bool))
        A["dark"].setChecked(theme.T.mode == "dark")

    def _connect(self):
        m, st, v = self.model, self.state, self.view
        m.opened.connect(self._on_opened)
        m.historyChanged.connect(self.refresh_history)
        m.modifiedChanged.connect(lambda _m: self.refresh_title())
        m.structureChanged.connect(self.refresh_status)
        m.structureChanged.connect(self.refresh_title)
        st.currentPageChanged.connect(lambda _p: self.refresh_status())
        st.toolChanged.connect(lambda _t: self.refresh_status())
        st.selectionChanged.connect(self.refresh_status)
        # 在别的程序里复制了东西(比如截图)之后,Ctrl+V 应该贴那个,而不是之前复制的页面对象
        QGuiApplication.clipboard().dataChanged.connect(lambda: setattr(self.state, "clipboard", []))
        v.editingChanged.connect(lambda _e: self.refresh_status())
        v.editingChanged.connect(lambda _e: self.inspector.refresh())
        v.zoomChanged.connect(self.zoom_ctl.set_zoom)
        v.message.connect(self.flash)
        v.contextRequested.connect(self._canvas_menu)
        v.filesDropped.connect(self._files_dropped_on_page)
        self.undo_btn.clicked.connect(self.cmd_undo)
        self.redo_btn.clicked.connect(self.cmd_redo)
        self.save_btn.clicked.connect(self.cmd_save)
        self.convert_btn.clicked.connect(lambda: None if self.model.is_open else self.cmd_convert())
        self.panel_btn.setChecked(self.inspector.isVisibleTo(self) or self.acts["inspector"].isChecked())
        self.panel_btn.clicked.connect(self.toggle_inspector)
        self.thumbs_btn.setChecked(self.acts["thumbs"].isChecked())
        self.thumbs_btn.clicked.connect(self.toggle_thumbs)
        self.palette.imageRequested.connect(self.cmd_add_image)
        self.search.search.connect(self.do_search)
        self.search.step.connect(self.step_hit)
        self.search.cleared.connect(self.clear_search)
        self.page_nav.goto.connect(self.view.goto)
        self.zoom_ctl.zoomTo.connect(self.view.set_zoom)
        self.zoom_ctl.zoomStep.connect(lambda d: self.view.zoom_by(1.2 if d > 0 else 1 / 1.2))
        self.zoom_ctl.fit.connect(lambda k: self.view.fit_width() if k == "width" else self.view.fit_page())
        self.thumbs.pageClicked.connect(self.view.goto)
        self.thumbs.contextRequested.connect(self._page_menu)
        self.thumbs.pagesMoved.connect(self.cmd_move_to)
        self.thumbs.filesDropped.connect(lambda paths, at: self.insert_paths(paths, at))
        w = self.welcome
        w.openRequested.connect(self.cmd_open)
        w.openPath.connect(lambda p: self.open_path(p))
        w.convertRequested.connect(self.cmd_convert)
        w.mergeRequested.connect(self.cmd_merge_files)
        w.imagesRequested.connect(self.cmd_images_to_pdf)
        w.clearRecent.connect(self._clear_recent)
        w.helpRequested.connect(lambda: D.show_help(self))
        self._update_enabled()

    # ======================================================================
    # 状态显示
    # ======================================================================
    def refresh_title(self):
        m = self.model
        if m.is_open:
            name = m.display_name()
            self.setWindowTitle(f"{'● ' if m.modified else ''}{name} - {APP}")
            self.doc_title.setText(name)
            self.dot.setVisible(m.modified)
            n = m.page_count()
            self.doc_sub.setText(f"{n} 页 · " + ("有未保存的修改" if m.modified else
                                                 ("已保存" if m.path else "尚未保存")))
        else:
            self.setWindowTitle(APP)
            self.doc_title.setText(APP)
            self.dot.setVisible(False)
            self.doc_sub.setText("打开一个 PDF 开始编辑")

    def refresh_history(self):
        m = self.model
        self.undo_btn.setEnabled(m.can_undo())
        self.redo_btn.setEnabled(m.can_redo())
        self.undo_btn.setToolTip(f"撤销 {m.undo_text()}  Ctrl+Z" if m.can_undo() else "撤销  Ctrl+Z")
        self.redo_btn.setToolTip(f"重做 {m.redo_text()}  Ctrl+Y" if m.can_redo() else "重做  Ctrl+Y")
        self.refresh_title()

    def refresh_status(self):
        m, st = self.model, self.state
        n = m.page_count()
        self.page_nav.set_page(min(st.current_page, max(n - 1, 0)), n)
        if self.view.edits.active:
            self.hint.setText("正在编辑文字 · Esc 完成 · 行首退格接到上一行 · ↑ ↓ 移到上 / 下一行")
        elif st.selection and st.tool == "select":
            k = len(st.selection)
            self.hint.setText(("已选中 1 个对象" if k == 1 else f"已选中 {k} 个对象")
                              + " · 拖动移动 · 方向键微调 · Delete 删除 · Ctrl+D 复制一份")
        else:
            self.hint.setText(TOOL_HINTS.get(st.tool, "") if m.is_open else "")

    def _update_enabled(self):
        is_open = self.model.is_open
        for a in self.acts.values():
            if getattr(a, "needs_doc", False):
                a.setEnabled(is_open)
        for w in self.doc_only:
            w.setVisible(is_open)
        self.statusbar.setVisible(is_open)
        self.stack.setCurrentIndex(1 if is_open else 0)
        self.convert_btn.setMenu(self.convert_menu if is_open else None)

    def flash(self, text, ms=3600, actions=()):
        self.toast.show_message(text, ms, actions)

    # ======================================================================
    # 打开 / 保存 / 关闭
    # ======================================================================
    def _ready(self) -> bool:
        """命令执行前:先结束正在进行的文字输入。返回是否有打开的文档。"""
        self.view.edits.commit()
        self.view.tools["form"].commit_editor()
        return self.model.is_open

    def confirm_discard(self) -> bool:
        self.view.edits.commit()
        if not self.model.is_open or not self.model.modified:
            return True
        ans = D.ask_save(self, self.model.display_name())
        if ans == "cancel":
            return False
        if ans == "save":
            return self.cmd_save()
        return True

    def cmd_open(self):
        if not self.confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", self.last_dir, OPEN_FILTER)
        if path:
            self.open_path(path, ask=False)

    def open_path(self, path, ask=True) -> bool:
        if ask and not self.confirm_discard():
            return False
        if not os.path.exists(path):
            D.warn(self, f"文件不存在:\n{path}")
            self._forget_recent(path)
            return False
        pw = None
        while True:
            try:
                self.model.open(path, pw)
                break
            except E.NeedPassword:
                pw = D.ask_password(self, f"「{os.path.basename(path)}」已加密,请输入打开密码。" +
                                    ("\n密码不正确,请重试。" if pw else ""))
                if pw is None:
                    return False
            except Exception as ex:  # noqa: BLE001
                D.error(self, "无法打开这个文件。", detail=str(ex))
                return False
        self.model.password = pw
        self.last_dir = os.path.dirname(path)
        self._add_recent(path)
        QTimer.singleShot(60, self._after_open_hints)
        return True

    def _on_opened(self):
        self.state.clear_selection()
        self.state.set_page_selection(set())
        self.state.current_page = 0
        self.state.set_tool("select")
        self.clear_search()
        self._update_enabled()
        self.refresh_title()
        self.refresh_history()
        self.refresh_status()
        if not self.model.is_open:
            self.welcome.set_recent(self.recent())
        else:
            QTimer.singleShot(0, self._initial_fit)

    def _initial_fit(self):
        if self.model.is_open:
            self.view.fit_initial()
            self.view.goto(0)

    def _after_open_hints(self):
        m = self.model
        if not m.is_open:
            return
        probe = range(min(m.page_count(), 3))
        if any(m.is_scanned(i) for i in probe):
            self.flash("这份 PDF 看起来是扫描件 · 用「文字识别 OCR」可以让它变成可搜索、可编辑的文字", 8000)
        elif any(m.widgets(i) for i in probe):
            self.flash("这份 PDF 有可填写的表单 · 选「填写表单」工具(F)直接填写", 7000)

    def cmd_close(self):
        if self.model.is_open and self.confirm_discard():
            self.model.close()

    def cmd_save(self) -> bool:
        if not self._ready():
            return False
        if not self.model.path:
            return self.cmd_save_as()
        return self._save_to(self.model.path, self.model.password)

    def cmd_save_as(self) -> bool:
        if not self._ready():
            return False
        init = os.path.join(self.last_dir, os.path.splitext(self.model.display_name())[0] + ".pdf")
        path, _ = QFileDialog.getSaveFileName(self, "另存为", init, "PDF 文件 (*.pdf)")
        if not path:
            return False
        return self._save_to(path, self.model.password)

    def cmd_save_encrypted(self):
        if not self._ready():
            return
        res = D.ParamDialog.ask(self, "加密另存为", [
            dict(key="pw", label="打开密码", kind="password"),
            dict(key="pw2", label="再输入一次", kind="password"),
        ], note="设置后,打开这个 PDF 需要输入密码。请牢记密码,忘记后无法找回。", ok_text="继续")
        if not res:
            return
        if not res["pw"] or res["pw"] != res["pw2"]:
            D.warn(self, "两次输入的密码不一致,或者密码为空。")
            return
        init = os.path.join(self.last_dir, os.path.splitext(self.model.display_name())[0] + "_加密.pdf")
        path, _ = QFileDialog.getSaveFileName(self, "加密另存为", init, "PDF 文件 (*.pdf)")
        if path:
            self._save_to(path, res["pw"])

    def _save_to(self, path, password) -> bool:
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                self.model.save(path, password)
            finally:
                QApplication.restoreOverrideCursor()
        except Exception as ex:  # noqa: BLE001
            D.error(self, "保存失败。如果文件正被其他程序打开,请先关闭它,或者换个文件名另存为。", detail=str(ex))
            return False
        self.last_dir = os.path.dirname(path)
        self._add_recent(path)
        self.refresh_title()
        self.flash(f"已保存 · {os.path.basename(path)}")
        return True

    # ======================================================================
    # 撤销 / 剪贴板 / 工具
    # ======================================================================
    def cmd_undo(self):
        if self._ready() and self.model.undo():
            self.flash(f"已撤销:{self.model.redo_text()}", 1800)

    def cmd_redo(self):
        if self._ready() and self.model.redo():
            self.flash(f"已重做:{self.model.undo_text()}", 1800)

    def set_tool(self, tool):
        if self.model.is_open:
            self.state.set_tool(tool)

    def cmd_paste(self):
        if not self._ready():
            return
        if self.state.clipboard:
            Ops.paste(self.view)
            return
        md = QGuiApplication.clipboard().mimeData()
        if md is not None and md.hasImage():  # 系统剪贴板里的图片(截图等)直接贴到页面上
            img = QGuiApplication.clipboard().image()
            if not img.isNull():
                from PySide6.QtCore import QBuffer, QIODevice
                buf = QBuffer()
                buf.open(QIODevice.WriteOnly)
                img.save(buf, "PNG")
                self.place_image(bytes(buf.data()), img.width(), img.height())

    # ======================================================================
    # 图片
    # ======================================================================
    @staticmethod
    def _image_bytes(path):
        from PIL import Image
        img = Image.open(path)
        img.load()
        if img.format in ("PNG", "JPEG"):
            return Path(path).read_bytes(), img.width, img.height
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, "PNG")
        return buf.getvalue(), img.width, img.height

    def cmd_add_image(self):
        if not self._ready():
            return
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", self.last_dir, IMAGE_FILTER)
        if path:
            self.add_image_file(path)

    def add_image_file(self, path, at=None):
        try:
            data, w, h = self._image_bytes(path)
        except Exception as ex:  # noqa: BLE001
            D.error(self, "无法读取这张图片。", detail=str(ex))
            return
        self.last_dir = os.path.dirname(path)
        self.place_image(data, w, h, at)

    def place_image(self, data, iw, ih, at=None):
        """把图片放到页面上。at=(页号, x, y) 为图片中心;默认放在当前视野中间。"""
        v, m = self.view, self.model
        if at is None:
            c = v.mapToScene(v.viewport().rect().center())
            pno = v.page_at_scene(c, nearest=True)
            if pno is None:
                return
            x, y = v.to_page(pno, c)
        else:
            pno, x, y = at
        pw, ph = m.page_size(pno)
        w = min(iw * 72 / 96, pw / 3)
        h = w * ih / max(iw, 1)
        x = min(max(x, w / 2), pw - w / 2)
        y = min(max(y, h / 2), ph - h / 2)
        ov = E.Overlay("image", pno, (x - w / 2, y - h / 2, x + w / 2, y + h / 2), data=data)
        m.change("插入图片", lambda: m.overlays.append(ov), {pno})
        self.state.set_tool("select")
        self.state.select([ov.uid])

    # ======================================================================
    # 拖放
    # ======================================================================
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            if not self.model.is_open:
                self.welcome.set_drop_active(True)

    def dragLeaveEvent(self, e):
        self.welcome.set_drop_active(False)

    def dropEvent(self, e):
        self.welcome.set_drop_active(False)
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self._open_dropped(paths))

    def _open_dropped(self, paths):
        pdfs = [p for p in paths if Path(p).suffix.lower() in {".pdf"} | E.IMAGE_EXTS]
        if not pdfs:
            self.flash("只能打开 PDF 或图片文件 · 其它格式请用「格式转换」")
            return
        if len(pdfs) > 1 and not self.model.is_open:
            self.combine_to_new(pdfs, "合并")
            return
        self.open_path(pdfs[0])

    def _files_dropped_on_page(self, paths, pno, x, y):
        imgs = [p for p in paths if Path(p).suffix.lower() in E.IMAGE_EXTS]
        pdfs = [p for p in paths if Path(p).suffix.lower() == ".pdf"]
        if imgs and pno >= 0:
            for k, p in enumerate(imgs):
                self.add_image_file(p, (pno, x + 14 * k, y + 14 * k))
            return
        if pdfs:
            menu = QMenu(self)
            a1 = menu.addAction(icons.icon("open"), f"打开「{os.path.basename(pdfs[0])}」")
            cur = pno if pno >= 0 else self.state.current_page
            a2 = menu.addAction(icons.icon("merge"), f"插入到第 {cur + 1} 页后面")
            a3 = menu.addAction(icons.icon("merge"), "插入到文档末尾")
            from PySide6.QtGui import QCursor
            act = menu.exec(QCursor.pos())
            if act is a1:
                self.open_path(pdfs[0])
            elif act is a2:
                self.insert_paths(pdfs, cur + 1)
            elif act is a3:
                self.insert_paths(pdfs, self.model.page_count())
        elif imgs:
            self.flash("把图片拖到页面上就能插入")

    # ======================================================================
    # 页面操作
    # ======================================================================
    def target_pages(self):
        return self.state.target_pages(self.model.page_count())

    def _structural(self, text, fn):
        try:
            return self.model.structural(text, fn), True
        except Exception as ex:  # noqa: BLE001
            D.error(self, f"{text}失败。", detail=str(ex))
            return None, False

    def _after_structure(self, page=None, sel=None):
        n = self.model.page_count()
        page = max(0, min(self.state.current_page if page is None else page, n - 1))
        self.state.set_page_selection(set(sel) if sel is not None else {page})
        self.view.goto(page)

    def cmd_rotate(self, delta):
        if not self._ready():
            return
        idxs = self.target_pages()
        _r, ok = self._structural("旋转页面", lambda d: E.rotate_pages(d, idxs, delta))
        if ok:
            self._after_structure(idxs[0], idxs)

    def cmd_delete_pages(self):
        if not self._ready():
            return
        idxs = self.target_pages()
        if len(idxs) >= self.model.page_count():
            D.info(self, "至少要保留一页。")
            return
        what = f"第 {idxs[0] + 1} 页" if len(idxs) == 1 else f"选中的 {len(idxs)} 页"
        if not D.confirm(self, f"删除{what}?\n(可以用撤销找回)", "删除", danger=True):
            return
        _r, ok = self._structural("删除页面", lambda d: E.delete_pages(d, idxs))
        if ok:
            self._after_structure(min(idxs[0], self.model.page_count() - 1))

    def cmd_insert_blank(self):
        if not self._ready():
            return
        last = max(self.target_pages())
        _r, ok = self._structural("插入空白页", lambda d: E.insert_blank(d, last))
        if ok:
            self._after_structure(last + 1)

    def cmd_move(self, direction):
        if not self._ready():
            return
        idxs = self.target_pages()
        n = self.model.page_count()
        if (direction < 0 and idxs[0] == 0) or (direction > 0 and idxs[-1] >= n - 1):
            return
        self.cmd_move_to(idxs, idxs[0] - 1 if direction < 0 else idxs[-1] + 2)

    def cmd_move_to(self, idxs, dest):
        if not self._ready():
            return
        new, ok = self._structural("移动页面", lambda d: E.move_pages(d, idxs, dest))
        if ok:
            self._after_structure(new[0], new)

    def cmd_insert_files(self):
        if not self._ready():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要插入的 PDF 或图片", self.last_dir, OPEN_FILTER)
        if not paths:
            return
        res = D.ParamDialog.ask(self, "插入位置", [
            dict(key="where", label="插入到", kind="choice", choices=["当前页之后", "文档末尾", "文档开头"],
                 default="当前页之后")], ok_text="插入")
        if not res:
            return
        at = {"当前页之后": self.state.current_page + 1, "文档末尾": self.model.page_count(), "文档开头": 0}[res["where"]]
        self.insert_paths(paths, at)

    def insert_paths(self, paths, at):
        if not self._ready():
            return
        ok_paths = [p for p in paths if Path(p).suffix.lower() in {".pdf"} | E.IMAGE_EXTS]
        if not ok_paths:
            self.flash("只能插入 PDF 或图片")
            return
        self.last_dir = os.path.dirname(ok_paths[0])
        added, ok = self._structural("插入页面", lambda d: E.insert_files(d, list(ok_paths), at))
        if ok:
            self._after_structure(at, range(at, at + added))
            self.flash(f"已插入 {added} 页")

    def cmd_extract(self):
        if not self._ready():
            return
        idxs = self.target_pages()
        init = os.path.join(self.last_dir, os.path.splitext(self.model.display_name())[0] + "_提取.pdf")
        path, _ = QFileDialog.getSaveFileName(self, f"提取 {len(idxs)} 页为新 PDF", init, "PDF 文件 (*.pdf)")
        if not path:
            return
        work = self.model.baked_copy()
        try:
            Path(path).write_bytes(E.extract_pages(work, idxs))
        finally:
            work.close()
        self.flash(f"已提取 {len(idxs)} 页 · {os.path.basename(path)}")

    def cmd_split(self):
        if not self._ready():
            return
        n = self.model.page_count()
        res = D.ParamDialog.ask(self, "拆分 PDF", [
            dict(key="mode", label="拆分方式", kind="choice", choices=["每页一个文件", "按页码范围"], default="每页一个文件"),
            dict(key="ranges", label="页码范围", default=f"1-{max(1, n // 2)}, {max(1, n // 2) + 1}-{n}" if n > 1 else "1",
                 hint="仅「按页码范围」时有效;用逗号分隔,每段生成一个文件"),
        ], ok_text="选择保存位置…")
        if not res:
            return
        try:
            groups = [[i] for i in range(n)] if res["mode"] == "每页一个文件" else E.parse_ranges(res["ranges"], n)
        except ValueError as ex:
            D.warn(self, str(ex))
            return
        folder = QFileDialog.getExistingDirectory(self, "选择保存拆分文件的文件夹", self.last_dir)
        if not folder:
            return
        stem = os.path.splitext(self.model.display_name())[0]
        work = self.model.baked_copy()
        try:
            for g in groups:
                tag = f"第{g[0] + 1}页" if len(g) == 1 else f"第{g[0] + 1}-{g[-1] + 1}页"
                Path(folder, f"{stem}_{tag}.pdf").write_bytes(E.extract_pages(work, g))
        finally:
            work.close()
        self.flash(f"已拆分为 {len(groups)} 个文件")

    # ======================================================================
    # 导出
    # ======================================================================
    def cmd_export_images(self):
        if not self._ready():
            return
        res = D.ParamDialog.ask(self, "导出为图片", [
            dict(key="scope", label="导出范围", kind="choice", choices=["全部页面", "当前页", "选中的页面"], default="全部页面"),
            dict(key="fmt", label="格式", kind="choice", choices=["png", "jpg"], default="png"),
            dict(key="dpi", label="清晰度 (DPI)", kind="int", default=150, min=36, max=600, step=10),
        ], ok_text="选择文件夹…")
        if not res:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择保存图片的文件夹", self.last_dir)
        if not folder:
            return
        n = self.model.page_count()
        idxs = {"全部页面": list(range(n)), "当前页": [self.state.current_page],
                "选中的页面": self.target_pages()}[res["scope"]]
        stem = os.path.splitext(self.model.display_name())[0]
        work = self.model.baked_copy()
        try:
            paths = run_task(self, "导出图片", lambda prog, cancel: self._export_imgs(work, idxs, folder, stem, res, prog,
                                                                                cancel))
        finally:
            work.close()
        self.flash(f"已导出 {len(paths)} 张图片")

    @staticmethod
    def _export_imgs(work, idxs, folder, stem, res, prog, cancel):
        out = []
        for k, i in enumerate(idxs):
            if cancel():
                break
            out += E.export_images(work, [i], folder, stem, res["dpi"], res["fmt"])
            prog(k + 1, len(idxs), f"正在导出第 {i + 1} 页…")
        return out

    def cmd_export_text(self):
        if not self._ready():
            return
        init = os.path.join(self.last_dir, os.path.splitext(self.model.display_name())[0] + ".txt")
        path, _ = QFileDialog.getSaveFileName(self, "导出全部文字", init, "文本文件 (*.txt)")
        if not path:
            return
        work = self.model.baked_copy()
        try:
            Path(path).write_text(E.extract_text(work).replace(" ", " "), encoding="utf-8")
        finally:
            work.close()
        self.flash(f"文字已导出 · {os.path.basename(path)}")

    # ======================================================================
    # 合并 / 图片转 PDF(生成新文档)
    # ======================================================================
    def cmd_merge_files(self):
        if not self.confirm_discard():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要合并的 PDF 或图片(按选择顺序合并)", self.last_dir, OPEN_FILTER)
        if paths:
            self.combine_to_new(paths, "合并")

    def cmd_images_to_pdf(self):
        if not self.confirm_discard():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择图片", self.last_dir, IMAGE_FILTER)
        if paths:
            self.combine_to_new(paths, "图片")

    def combine_to_new(self, paths, stem):
        doc = E.fitz.open()
        try:
            E.insert_files(doc, list(paths), 0)
        except Exception as ex:  # noqa: BLE001
            doc.close()
            D.error(self, "合并失败。", detail=str(ex))
            return
        data = doc.tobytes()
        doc.close()
        self.last_dir = os.path.dirname(paths[0])
        base = os.path.splitext(os.path.basename(paths[0]))[0]
        self.model.load(E.doc_from_bytes(data), name=f"{base}_{stem}.pdf")
        self.refresh_title()
        self.flash(f"已把 {len(paths)} 个文件合成一个新文档 · 记得保存(Ctrl+S)", 6000)

    # ======================================================================
    # 水印 / 页码 / OCR / 转换
    # ======================================================================
    def _mutate(self, text, fn, then=None):
        try:
            return self.model.mutate(text, fn, then), True
        except Exception as ex:  # noqa: BLE001
            D.error(self, f"{text}失败。", detail=str(ex))
            return None, False

    def cmd_watermark(self):
        if not self._ready():
            return
        res = D.ParamDialog.ask(self, "添加水印", [
            dict(key="text", label="水印文字", default="机密"),
            dict(key="size", label="字号", kind="int", default=64, min=8, max=300, step=4),
            dict(key="angle", label="倾斜角度", kind="int", default=45, min=0, max=90, step=5),
            dict(key="opacity", label="不透明度 %", kind="int", default=25, min=5, max=100, step=5),
            dict(key="color", label="颜色", kind="color", default="#888888"),
        ], note="水印会加到所有页面上。", ok_text="添加")
        if not res or not res["text"].strip():
            return
        font = self.state.styles["text"]["font"]
        _r, ok = self._mutate("添加水印", lambda d: E.add_watermark(
            d, res["text"], res["size"], res["color"], res["opacity"] / 100, res["angle"], font))
        if ok:
            self.flash("已添加水印")

    def cmd_page_numbers(self):
        if not self._ready():
            return
        res = D.ParamDialog.ask(self, "添加页码", [
            dict(key="fmt", label="页码格式", default="第 {n} 页 / 共 {total} 页", hint="{n} = 当前页码,{total} = 总页数"),
            dict(key="pos", label="位置", kind="choice", default="底部居中",
                 choices=["底部居中", "底部靠右", "底部靠左", "顶部居中", "顶部靠右", "顶部靠左"]),
            dict(key="size", label="字号", kind="int", default=11, min=6, max=48),
            dict(key="start", label="起始页码", kind="int", default=1, min=0, max=9999),
        ], ok_text="添加")
        if not res:
            return
        pos = {"底部居中": "bottom-center", "底部靠右": "bottom-right", "底部靠左": "bottom-left",
               "顶部居中": "top-center", "顶部靠右": "top-right", "顶部靠左": "top-left"}[res["pos"]]
        font = self.state.styles["text"]["font"]
        _r, ok = self._mutate("添加页码", lambda d: E.add_page_numbers(
            d, res["fmt"], pos, res["size"], 28, res["start"], font=font))
        if ok:
            self.flash("已添加页码")

    def cmd_ocr(self):
        if not self._ready():
            return
        if not ocr.available():
            D.info(self, "文字识别需要 Windows 10 / 11 自带的 OCR 引擎。")
            return
        langs = ocr.list_languages()
        if not langs:
            D.warn(self, "这台电脑没有可用的 OCR 语言。\n请打开 Windows 设置 → 时间和语言 → 语言和区域,"
                         "添加「中文(简体)」并勾选「光学字符识别」。")
            return
        m = self.model
        names = {f"{name} ({tag})": tag for tag, name in langs}
        auto = "自动(优先中文)"
        scanned = [i for i in range(m.page_count()) if m.is_scanned(i)]
        res = D.ParamDialog.ask(self, "文字识别 (OCR)", [
            dict(key="scope", label="识别范围", kind="choice", choices=["当前页", "选中的页面", "全部页面"],
                 default="全部页面" if len(scanned) > 1 else "当前页"),
            dict(key="lang", label="识别语言", kind="choice", choices=[auto] + list(names), default=auto),
            dict(key="dpi", label="清晰度 (DPI)", kind="int", default=200, min=100, max=400, step=50),
            dict(key="skip", label="已有文字的页面", kind="choice", choices=["跳过(推荐)", "也重新识别"], default="跳过(推荐)"),
        ], note="识别后,扫描件的每一页会多出一层看不见的文字:可以搜索、复制,也能用「编辑文字」直接修改。",
            ok_text="开始识别")
        if not res:
            return
        n = m.page_count()
        idxs = {"当前页": [self.state.current_page], "选中的页面": self.target_pages(),
                "全部页面": list(range(n))}[res["scope"]]
        if res["skip"].startswith("跳过"):
            idxs = [i for i in idxs if not E.page_has_text(m.doc[i])]
            if not idxs:
                D.info(self, "所选页面已经有文字了,不需要识别。\n(想重新识别的话,把「已有文字的页面」改成「也重新识别」。)")
                return
        lang = None if res["lang"] == auto else names[res["lang"]]
        job = ocr.render_for_ocr(m.doc, idxs, res["dpi"])  # 渲染要在主线程
        try:
            pages = run_task(self, "文字识别", lambda prog, cancel: ocr.recognize(
                job, lang, lambda d, t: prog(d, t, f"已识别 {d} / {t} 页…"), cancel))
        except ocr.OcrError as ex:
            if "取消" in str(ex):
                self.flash("已取消文字识别")
            else:
                D.warn(self, str(ex))
            return
        if not any(lines for lines, _s in pages.values()):
            D.info(self, "没有识别到文字。可以试试提高清晰度,或者换一种识别语言。")
            return
        cnt, ok = self._mutate("文字识别", lambda d: E.add_text_layer(d, pages))
        if ok:
            self.flash(f"识别完成:{len(idxs)} 页,共 {cnt} 行文字 · 现在可以搜索、复制,或用「编辑文字」修改", 8000)

    def cmd_convert(self):
        """打开格式转换窗口;正在编辑文档时,把它(含未保存的修改)放进待转换列表。"""
        self._ready()
        from ui.convert_dialog import ConvertDialog
        dlg = self._convert_win
        if dlg is None or not dlg.isVisible():
            dlg = self._convert_win = ConvertDialog(self)
            dlg.show()
        else:
            dlg.raise_()
            dlg.activateWindow()
        if self.model.is_open:
            dlg.refresh_current()

    # ---- 一键转换当前文档 ------------------------------------------------
    QUICK_TARGETS = [("docx", "Word 文档 (.docx)", "edit_text"), ("xlsx", "Excel 表格 (.xlsx)", "form"),
                     ("pptx", "PowerPoint 演示文稿 (.pptx)", "fit_page"), ("png", "PNG 图片(每页一张)", "image"),
                     ("jpg", "JPG 图片(每页一张)", "image"), ("txt", "纯文本 (.txt)", "doc"),
                     ("html", "网页 (.html)", "doc"), ("pdf", "压缩 PDF(文件更小)", "doc")]

    def convert_outdir(self) -> str:
        """一键转换的结果放在哪:用户指定的文件夹;否则和原文件放在一起(新文档放在最近用过的文件夹)。"""
        custom = self.settings.value("convert_dir", "")
        if custom and os.path.isdir(custom):
            return custom
        d = os.path.dirname(self.model.path) if self.model.path else self.last_dir
        if d and os.path.isdir(d) and os.access(d, os.W_OK):
            return d
        return str(Path.home() / "Documents")

    def _fill_convert_menu(self):
        m = self.convert_menu
        m.clear()
        name = self.model.display_name()
        head = m.addAction(f"把「{name}」转换为" + (" · 含未保存的修改" if self.model.modified else ""))
        head.setEnabled(False)
        for key, text, ic in self.QUICK_TARGETS:
            m.addAction(icons.icon(ic), text, lambda k=key, t=text: self.quick_convert(k, t))
        m.addSeparator()
        folder = self.convert_outdir()
        shown = m.fontMetrics().elidedText(folder, Qt.ElideMiddle, 300)
        where = m.addAction(f"保存到:{shown}")
        where.setEnabled(False)
        where.setToolTip(folder)
        m.addAction(icons.icon("open"), "更改保存位置…", self._choose_convert_dir)
        if self.settings.value("convert_dir", ""):
            m.addAction("改回:和原文件放在同一个文件夹", lambda: self.settings.setValue("convert_dir", ""))
        m.addSeparator()
        m.addAction(icons.icon("convert"), "更多格式、批量转换其它文件…\tCtrl+Shift+C", self.cmd_convert)

    def _choose_convert_dir(self):
        d = QFileDialog.getExistingDirectory(self, "转换结果保存到哪个文件夹", self.convert_outdir())
        if d:
            self.settings.setValue("convert_dir", d)
            self.flash(f"转换结果以后保存到:{d}")

    def quick_convert(self, key: str, text: str):
        """把正在编辑的文档(包括还没保存的修改)直接转换成别的格式,不用先保存。"""
        if not self._ready():
            return
        import shutil

        import converter as C
        outdir = self.convert_outdir()
        try:
            tmp = self.model.export_temp()
        except Exception as ex:  # noqa: BLE001
            D.error(self, "无法导出当前文档。", detail=str(ex))
            return
        opts = C.Options()
        if key == "pdf":  # 压缩:和转换窗口里的默认值一致
            opts.dpi, opts.quality = 110, 70
        elif key == "pptx":
            opts.quality = 85
        try:
            r = run_task(self, f"转换为 {text}", lambda prog, cancel: C.convert(tmp, key, outdir, opts, prog, cancel))
        except C.ConvertCancelled:
            self.flash("已取消转换")
            return
        except Exception as ex:  # noqa: BLE001
            D.error(self, "转换时出错。", detail=str(ex))
            return
        finally:
            shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
        if not r.ok:
            D.warn(self, "转换失败。", detail=r.message)
            return
        out = r.outputs[0]
        more = f" 等 {len(r.outputs)} 个文件" if len(r.outputs) > 1 else ""
        note = f"({r.message})" if r.message else ""
        self.flash(f"已转换:{os.path.basename(out)}{more}{note}", 12000,
                   actions=[("打开", lambda: open_file(out)), ("打开文件夹", lambda: reveal_file(out))])

    # ======================================================================
    # 查找
    # ======================================================================
    def do_search(self, text):
        if not self._ready():
            return
        self.hits = [(p, r) for p, rects in E.search_text(self.model.doc, text) for r in rects] if text else []
        self.hit_idx = -1
        if not self.hits:
            self.search.set_count((0, 0) if text else None)
            self.view.set_hits([])
            return
        # 从当前页开始找
        cur = self.state.current_page
        start = next((k for k, (p, _r) in enumerate(self.hits) if p >= cur), 0)
        self.hit_idx = start - 1
        self.step_hit(1)

    def step_hit(self, d):
        if not self.hits:
            return
        self.hit_idx = (self.hit_idx + d) % len(self.hits)
        self.search.set_count((self.hit_idx + 1, len(self.hits)))
        self.view.set_hits(self.hits, self.hit_idx)

    def clear_search(self):
        self.hits, self.hit_idx = [], -1
        self.search.set_count(None)
        self.view.set_hits([])

    # ======================================================================
    # 右键菜单
    # ======================================================================
    def _canvas_menu(self, gpos, pno, x, y, uid):
        m = QMenu(self)
        A = self.acts
        if uid is not None:
            ov = self.model.find(uid)
            if ov is not None and ov.kind == "text":
                m.addAction(icons.icon("edit_text"), "编辑文字", lambda: self.view.edits.begin(ov, cursor=("pt", x, y)))
                m.addSeparator()
            m.addAction(A["copy"])
            m.addAction(A["dup"])
            m.addAction(A["delete"])
            m.addSeparator()
            m.addAction(icons.icon("front"), "置于顶层", lambda: Ops.reorder(self.view, True))
            m.addAction(icons.icon("back"), "置于底层", lambda: Ops.reorder(self.view, False))
        else:
            a = m.addAction(icons.icon("paste"), "粘贴到这里", lambda: Ops.paste(self.view, at=(pno, x, y)))
            a.setEnabled(bool(self.state.clipboard))
            m.addAction(icons.icon("add_text"), "在这里添加文字", lambda: self._add_text_at(pno, x, y))
            m.addAction(A["image"])
            m.addSeparator()
            self.state.set_page_selection({pno})
            m.addAction(A["rot_l"])
            m.addAction(A["rot_r"])
            m.addAction(A["blank"])
            m.addAction(A["del_pages"])
        m.exec(gpos)

    def _add_text_at(self, pno, x, y):
        st = dict(self.state.styles["text"])
        ov = E.Overlay("text", pno, (x, y, x, y), text="", **st)
        before = self.model.state()
        self.model.overlays.append(ov)
        self.model.touch({pno})
        self.view.edits.begin(ov, before=before, new=True)

    def _page_menu(self, gpos):
        if not self.model.is_open:
            return
        m = QMenu(self)
        A = self.acts
        idxs = self.target_pages()
        title = m.addAction(f"第 {idxs[0] + 1} 页" if len(idxs) == 1 else f"选中的 {len(idxs)} 页")
        title.setEnabled(False)
        for k in ("rot_l", "rot_r", None, "up", "down", "blank", "insert", None, "extract", "del_pages"):
            m.addSeparator() if k is None else m.addAction(A[k])
        m.exec(gpos)

    # ======================================================================
    # 视图
    # ======================================================================
    def toggle_thumbs(self):
        vis = not self.thumbs.isVisible() if self.model.is_open else not self.acts["thumbs"].isChecked()
        self.thumbs.setVisible(vis)
        self.acts["thumbs"].setChecked(vis)
        self.thumbs_btn.setChecked(vis)
        self.settings.setValue("show_thumbs", vis)

    def toggle_inspector(self):
        vis = not self.inspector.isVisible() if self.model.is_open else not self.acts["inspector"].isChecked()
        self.inspector.setVisible(vis)
        self.acts["inspector"].setChecked(vis)
        self.panel_btn.setChecked(vis)
        self.settings.setValue("show_inspector", vis)

    def toggle_theme(self):
        mode = "light" if theme.T.mode == "dark" else "dark"
        self.apply_theme(mode)
        self.settings.setValue("theme", mode)

    def apply_theme(self, mode):
        theme.apply(QApplication.instance(), mode)
        icons.refresh_all()
        self.acts["dark"].setChecked(mode == "dark")
        self.view.apply_theme()
        self.view.update_chrome()
        self.logo.refresh()
        self.welcome.retheme()
        self.thumbs.list.viewport().update()
        self.inspector.refresh()

    # ======================================================================
    # 最近打开
    # ======================================================================
    def recent(self):
        raw = self.settings.value("recent", [])
        out = []
        if isinstance(raw, list):
            for item in raw:
                try:
                    p, t = str(item).rsplit("|", 1)
                    out.append((p, float(t)))
                except ValueError:
                    continue
        return out

    def _save_recent(self, items):
        self.settings.setValue("recent", [f"{p}|{t}" for p, t in items[:12]])
        self.welcome.set_recent(items)

    def _add_recent(self, path):
        path = os.path.abspath(path)
        items = [(p, t) for p, t in self.recent() if os.path.normcase(p) != os.path.normcase(path)]
        self._save_recent([(path, time.time())] + items)

    def _forget_recent(self, path):
        self._save_recent([(p, t) for p, t in self.recent() if p != path])

    def _clear_recent(self):
        self._save_recent([])

    def _fill_recent_menu(self):
        self.recent_menu.clear()
        items = self.recent()
        for p, _t in items[:10]:
            self.recent_menu.addAction(os.path.basename(p), lambda pp=p: self.open_path(pp)).setToolTip(p)
        if not items:
            self.recent_menu.addAction("(没有记录)").setEnabled(False)

    # ======================================================================
    # 关闭
    # ======================================================================
    def closeEvent(self, e):
        if not self.confirm_discard():
            e.ignore()
            return
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("last_dir", self.last_dir)
        if self._convert_win is not None:
            self._convert_win.close()
        e.accept()
