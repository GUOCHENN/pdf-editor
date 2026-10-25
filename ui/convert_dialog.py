"""格式转换窗口:左边是文件列表(可拖入),右边选目标格式和选项。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QRadioButton, QSpinBox,
                               QStackedLayout, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import converter as C
from ui import dialogs as D
from ui import icons
from ui.tasks import run_task
from ui.theme import T
from ui.widgets import label

IMAGE_TARGETS = {"png", "jpg", "webp", "tiff", "bmp"}
SHORT_LABEL = {"docx": "Word (.docx)", "xlsx": "Excel (.xlsx)", "pptx": "PPT (.pptx)", "png": "PNG 图片",
               "jpg": "JPG 图片", "webp": "WebP 图片", "tiff": "TIFF 图片", "bmp": "BMP 图片", "txt": "纯文本 (.txt)",
               "html": "网页 (.html)", "svg": "SVG 矢量图", "md": "Markdown"}
TARGET_ICON = {"pdf": "doc", "docx": "edit_text", "xlsx": "form", "pptx": "fit_page", "txt": "doc", "html": "doc",
               "svg": "image", "md": "doc"}


class FileTree(QTreeWidget):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DropOnly)
        self.setRootIsDecorated(False)
        self.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.setHeaderLabels(["文件", "类型", "状态"])
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.header().setSectionResizeMode(2, QHeaderView.Interactive)
        self.header().resizeSection(2, 220)
        self.setAlternatingRowColors(False)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        self.dlg.add_paths(paths)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.dlg.remove_selected()
            return
        super().keyPressEvent(e)


class ConvertDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("格式转换")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(980, 660)
        self.setMinimumSize(860, 560)
        self.setAcceptDrops(True)
        self.files: list[str] = []
        self.hints: dict[str, str] = {}
        self.shown_names: dict[str, str] = {}
        self.target_keys: list[str] = []
        self.last_outputs: list[str] = []
        self.shown: dict = {}
        self.current_tmp: str | None = None
        self.last_dir = win.last_dir
        self._build()
        self.refresh_targets()

    # ------------------------------------------------------------------ 界面
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(12)
        ic = QPushButton()
        ic.setObjectName("HeadIcon")
        ic.setFixedSize(40, 40)
        ic.setIconSize(QSize(22, 22))
        icons.bind(ic, "convert", "primary", size=22)
        head.addWidget(ic)
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(label("格式转换", "DialogTitle"))
        engines = C.office_engines()
        sub = (f"已检测到 {' / '.join(engines)}:Word、Excel、PowerPoint 可以高保真转成 PDF。" if engines else
               "没有检测到 Office / WPS / LibreOffice:Word 和 Excel 会用内置方式转换(版式较简单),PowerPoint 暂时无法转换。")
        s = label(sub, "Muted", wrap=True)
        tv.addWidget(s)
        head.addLayout(tv, 1)
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, 1)

        # ---- 左:文件
        left = QFrame()
        left.setObjectName("Card")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(14, 12, 14, 12)
        ll.setSpacing(10)
        lh = QHBoxLayout()
        lh.addWidget(label("要转换的文件", "PanelTitle"))
        self.count = label("", "Faint")
        lh.addWidget(self.count)
        lh.addStretch(1)
        ll.addLayout(lh)
        self.stack_holder = QWidget()
        self.stack = QStackedLayout(self.stack_holder)
        self.empty = QFrame()
        self.empty.setObjectName("DropZone")
        el = QVBoxLayout(self.empty)
        el.setAlignment(Qt.AlignCenter)
        di = QLabel()
        di.setPixmap(icons.pixmap("drop", T.c("primary"), 34))
        di.setAlignment(Qt.AlignCenter)
        el.addWidget(di)
        t = label("把文件拖到这里", "DropTitle")
        t.setAlignment(Qt.AlignCenter)
        el.addWidget(t)
        t2 = label("PDF、Word、Excel、PPT、图片、文本、网页都可以,一次可以放多个", "Faint")
        t2.setAlignment(Qt.AlignCenter)
        el.addWidget(t2)
        self.stack.addWidget(self.empty)
        self.tree = FileTree(self)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected_output())
        self.stack.addWidget(self.tree)
        ll.addWidget(self.stack_holder, 1)
        bl = QHBoxLayout()
        bl.setSpacing(6)
        for text, ic_name, fn in (("添加文件", "open", self.add_files), ("添加当前文档", "doc", self.add_current),
                                  ("移除", "close", self.remove_selected), ("清空", "trash", self.clear)):
            b = QPushButton(" " + text)
            icons.bind(b, ic_name, size=16)
            b.setIconSize(QSize(16, 16))
            b.clicked.connect(fn)
            bl.addWidget(b)
            if text == "添加当前文档":
                b.setEnabled(self.win.model.is_open)
        bl.addStretch(1)
        ll.addLayout(bl)
        self.hint = label("", "Faint", wrap=True)
        self.hint.setStyleSheet(f"color: {T.c('danger')};")
        ll.addWidget(self.hint)
        body.addWidget(left, 3)

        # ---- 右:设置
        right = QFrame()
        right.setObjectName("Card")
        right.setFixedWidth(360)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(16, 12, 16, 14)
        rl.setSpacing(10)
        rl.addWidget(label("转换为", "PanelTitle"))
        self.targets_box = QWidget()
        self.targets_grid = QGridLayout(self.targets_box)
        self.targets_grid.setContentsMargins(0, 0, 0, 0)
        self.targets_grid.setSpacing(6)
        self.target_group = QButtonGroup(self)
        self.target_group.setExclusive(True)
        self.target_group.idClicked.connect(lambda _i: self.on_target_change())
        rl.addWidget(self.targets_box)
        self.no_target = label("先在左边添加文件", "Faint")
        rl.addWidget(self.no_target)

        self.opts = QWidget()
        form = QGridLayout(self.opts)
        form.setContentsMargins(0, 6, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.rows = {}
        self.e_pages = QLineEdit()
        self.e_pages.setPlaceholderText("全部页面,例如 1-3,5,8-")
        self.e_dpi = QSpinBox()
        self.e_dpi.setRange(36, 600)
        self.e_dpi.setSingleStep(10)
        self.e_dpi.setValue(150)
        self.e_quality = QSpinBox()
        self.e_quality.setRange(10, 100)
        self.e_quality.setSingleStep(5)
        self.e_quality.setValue(90)
        self.e_pagesize = QComboBox()
        self.e_pagesize.addItems(["适应 A4 页面", "与图片大小一致"])
        self.e_merge = QCheckBox("多张图片合并成一个 PDF")
        self.e_ocr = QCheckBox("扫描页自动识别文字(OCR)")
        self.e_ocr.setChecked(True)
        self.e_pptx = QComboBox()
        self.e_pptx.addItems(["文字可编辑(文本框 + 背景图)", "每页是一张图片"])
        r = 0
        for name, text, w in (("pages", "页码范围", self.e_pages), ("dpi", "清晰度 DPI", self.e_dpi),
                              ("quality", "图片质量", self.e_quality), ("pagesize", "页面大小", self.e_pagesize),
                              ("merge", "", self.e_merge), ("ocr", "", self.e_ocr), ("pptx", "PPT 文字", self.e_pptx)):
            lab = label(text, "Muted")
            form.addWidget(lab, r, 0)
            form.addWidget(w, r, 1)
            self.rows[name] = (lab, w)
            r += 1
        form.setColumnStretch(1, 1)
        rl.addWidget(self.opts)

        rl.addWidget(label("保存到", "PanelTitle"))
        self.r_same = QRadioButton("和源文件放在同一个文件夹")
        self.r_same.setChecked(True)
        self.r_folder = QRadioButton("指定文件夹")
        rl.addWidget(self.r_same)
        rl.addWidget(self.r_folder)
        fr = QHBoxLayout()
        self.e_folder = QLineEdit()
        self.e_folder.setPlaceholderText("选择文件夹…")
        b = QPushButton("浏览…")
        b.clicked.connect(self.choose_folder)
        fr.addWidget(self.e_folder, 1)
        fr.addWidget(b)
        rl.addLayout(fr)
        rl.addStretch(1)
        body.addWidget(right)

        # ---- 底部
        bar = QHBoxLayout()
        self.c_open = QCheckBox("完成后打开所在文件夹")
        self.c_open.setChecked(True)
        bar.addWidget(self.c_open)
        bar.addStretch(1)
        self.summary = label("", "Muted")
        bar.addWidget(self.summary)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        bar.addWidget(close)
        self.btn_start = QPushButton("  开始转换")
        self.btn_start.setObjectName("Primary")
        icons.bind(self.btn_start, "convert", "on_primary", size=17)
        self.btn_start.setMinimumWidth(130)
        self.btn_start.clicked.connect(self.start)
        bar.addWidget(self.btn_start)
        root.addLayout(bar)

    # ------------------------------------------------------------------ 拖放到整个窗口
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.add_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    # ------------------------------------------------------------------ 文件列表
    def add_paths(self, paths):
        added, bad = 0, []
        for p in paths:
            p = os.path.abspath(p)
            if C.group_of(p) is None:
                bad.append(os.path.basename(p))
                continue
            if p in self.files:
                continue
            self.files.append(p)
            shown = self.shown_names.get(p) or os.path.basename(p)
            it = QTreeWidgetItem([shown, C.GROUP_NAMES[C.group_of(p)], "等待转换"])
            it.setData(0, Qt.UserRole, p)
            it.setToolTip(0, p)
            it.setIcon(0, icons.icon("doc", T.c("text3")))
            self.tree.addTopLevelItem(it)
            added += 1
        if bad:
            D.info(self, "这些文件的类型暂时不支持,已跳过:\n" + "\n".join(bad[:8]), "格式转换")
        self.refresh_targets()

    def _item(self, path):
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, Qt.UserRole) == path:
                return it
        return None

    def add_files(self):
        exts = " ".join("*" + e for e in C.all_extensions())
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要转换的文件", self.last_dir,
            f"支持的文件 ({exts});;PDF (*.pdf);;Word (*.doc *.docx *.rtf *.odt *.wps);;Excel (*.xls *.xlsx *.ods *.csv);;"
            "PowerPoint (*.ppt *.pptx *.odp);;图片 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp *.ico);;"
            "文本 / 网页 (*.txt *.md *.markdown *.html *.htm);;所有文件 (*.*)")
        if paths:
            self.last_dir = os.path.dirname(paths[0])
            self.add_paths(paths)

    def add_current(self):
        m = self.win.model
        if not m.is_open:
            D.info(self, "编辑器里还没有打开文档。", "格式转换")
            return
        self.win.view.edits.commit()
        try:
            tmp = os.path.abspath(m.export_temp())
        except Exception as ex:  # noqa: BLE001
            D.error(self, "无法导出当前文档。", "格式转换", str(ex))
            return
        self.hints[tmp] = os.path.dirname(m.path) if m.path else self.last_dir
        self.shown_names[tmp] = m.display_name() + ("(含未保存的修改)" if m.modified else "(当前文档)")
        self.current_tmp = tmp
        self.add_paths([tmp])

    def refresh_current(self):
        """把列表里的"当前文档"换成最新的版本(从编辑器打开转换窗口时调用,不用先保存)。"""
        old = self.current_tmp
        if old and old in self.files:
            it = self._item(old)
            if it is not None:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
            self.files.remove(old)
        self.add_current()

    def remove_selected(self):
        for it in self.tree.selectedItems():
            p = it.data(0, Qt.UserRole)
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(it))
            if p in self.files:
                self.files.remove(p)
        self.refresh_targets()

    def clear(self):
        self.tree.clear()
        self.files.clear()
        self.refresh_targets()

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择保存转换结果的文件夹", self.last_dir)
        if d:
            self.e_folder.setText(d)
            self.r_folder.setChecked(True)

    # ------------------------------------------------------------------ 目标格式
    def refresh_targets(self):
        self.stack.setCurrentIndex(1 if self.files else 0)
        self.count.setText(f"{len(self.files)} 个" if self.files else "")
        old = self.current_key()
        targets = C.targets_for(self.files) if self.files else []
        for b in self.target_group.buttons():
            self.target_group.removeButton(b)
            b.deleteLater()
        self.target_keys = [k for k, _ in targets]
        for i, (k, lab) in enumerate(targets):
            b = QPushButton(SHORT_LABEL.get(k, lab))
            b.setToolTip(lab)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            icons.bind(b, TARGET_ICON.get(k, "image" if k in IMAGE_TARGETS else "doc"), checked_key="primary", size=16)
            self.target_group.addButton(b, i)
            self.targets_grid.addWidget(b, i // 2, i % 2)
            b.show()  # 窗口已经显示后才加进来的控件要手动 show
        if targets:
            idx = self.target_keys.index(old) if old in self.target_keys else 0
            self.target_group.button(idx).setChecked(True)
            self.hint.setText("")
        else:
            self.hint.setText("" if not self.files else "所选文件的类型不同,没有共同的目标格式。请分开转换,或移除其中一类文件。")
        self.no_target.setVisible(not targets)
        self.on_target_change(reset_defaults=old not in self.target_keys)

    def current_key(self):
        i = self.target_group.checkedId()
        return self.target_keys[i] if 0 <= i < len(self.target_keys) else None

    def on_target_change(self, reset_defaults=True):
        key = self.current_key()
        groups = {C.group_of(f) for f in self.files}
        pdf_src = "pdf" in groups
        office_src = bool(groups & {"word", "excel", "ppt"})
        show = {
            "pages": bool(key) and (pdf_src or (office_src and key in IMAGE_TARGETS)),
            "dpi": bool(key) and ((key in IMAGE_TARGETS and (pdf_src or office_src)) or (key == "pdf" and pdf_src)
                                  or key == "pptx"),
            "quality": bool(key) and (key in ("jpg", "webp") or (key == "pdf" and pdf_src) or key == "pptx"),
            "pagesize": key == "pdf" and "image" in groups,
            "merge": key == "pdf" and "image" in groups and len(self.files) > 1 and groups == {"image"},
            "ocr": pdf_src and key in ("docx", "xlsx", "txt", "html"),
            "pptx": key == "pptx",
        }
        self.shown = show
        for name, (lab, w) in self.rows.items():
            lab.setVisible(show[name])
            w.setVisible(show[name])
        self.opts.setVisible(any(show.values()))
        if reset_defaults and key:
            compress = key == "pdf" and pdf_src
            self.e_dpi.setValue(110 if compress else 150)
            self.e_quality.setValue(70 if compress else (85 if key == "pptx" else 90))

    def collect_options(self) -> C.Options:
        s = self.shown
        return C.Options(
            pages=self.e_pages.text().strip() if s.get("pages") else "",
            dpi=self.e_dpi.value(), quality=self.e_quality.value(),
            merge=self.e_merge.isChecked() and bool(s.get("merge")),
            page_size="a4" if self.e_pagesize.currentIndex() == 0 else "fit",
            ocr=self.e_ocr.isChecked(),
            pptx_mode="editable" if self.e_pptx.currentIndex() == 0 else "image")

    # ------------------------------------------------------------------ 执行
    def outdir_for(self, src: str) -> str:
        if self.r_folder.isChecked() and self.e_folder.text().strip():
            return self.e_folder.text().strip()
        return self.hints.get(src) or os.path.dirname(src)

    def start(self):
        key = self.current_key()
        if not self.files:
            D.info(self, "请先添加要转换的文件。", "格式转换")
            return
        if not key:
            D.info(self, "没有可用的目标格式,请检查所选文件。", "格式转换")
            return
        if self.r_folder.isChecked() and not self.e_folder.text().strip():
            D.info(self, "请选择保存转换结果的文件夹。", "格式转换")
            return
        try:
            opts = self.collect_options()
            if opts.pages:
                C.E.parse_ranges(opts.pages, 10 ** 6)
        except ValueError as ex:
            D.warn(self, f"页码范围写得不对:{ex}", "格式转换")
            return
        srcs = list(self.files)
        outdirs = {s: self.outdir_for(s) for s in srcs}
        for s in srcs:
            it = self._item(s)
            if it:
                it.setText(2, "等待转换")
        self.summary.setText("")
        self.btn_start.setEnabled(False)
        try:
            results = run_task(self, "格式转换", lambda progress, cancel: C.convert_many(
                srcs, key, outdirs.__getitem__, opts, progress, cancel))
        except Exception as ex:  # noqa: BLE001
            D.error(self, "转换时出错。", "格式转换", str(ex))
            return
        finally:
            self.btn_start.setEnabled(True)
        self.show_results(results, srcs)

    def show_results(self, results, srcs):
        self.last_outputs = []
        ok = bad = 0
        merged = [r for r in results if r.ok and len(srcs) > 1 and r.src == srcs[0] and "合并" in r.message]
        for r in results:
            it = self._item(r.src) if r.src else None
            if it is not None:
                if r.ok:
                    name = os.path.basename(r.outputs[0]) if r.outputs else ""
                    more = f" 等 {len(r.outputs)} 个文件" if len(r.outputs) > 1 else ""
                    it.setText(2, f"✓ {name}{more}" + (f"  ({r.message})" if r.message else ""))
                    it.setForeground(2, T.q("success"))
                else:
                    it.setText(2, "✗ " + r.message.splitlines()[0][:80])
                    it.setForeground(2, T.q("danger"))
                    it.setToolTip(2, r.message)
            if r.ok:
                ok += 1
                self.last_outputs += r.outputs
            elif r.src:
                bad += 1
        if merged:
            for s in srcs[1:]:
                it = self._item(s)
                if it:
                    it.setText(2, "✓ 已合并到第一个文件的 PDF 里")
        cancelled = any(r.message == "已取消" for r in results)
        done = {r.src for r in results}
        for s in srcs:
            if s not in done and not merged:
                it = self._item(s)
                if it:
                    it.setText(2, "已取消" if cancelled else "")
        text = f"成功 {ok} 个" + (f",失败 {bad} 个" if bad else "") + ("(已取消)" if cancelled else "")
        self.summary.setText(text)
        self.summary.setStyleSheet(f"color: {T.c('danger') if bad else T.c('success')};")
        if bad:
            first = next((r for r in results if not r.ok and r.src), None)
            if first:
                D.warn(self, f"有 {bad} 个文件转换失败。", "格式转换",
                       f"{os.path.basename(first.src)}:\n{first.message}")
        if self.last_outputs and self.c_open.isChecked() and not cancelled:
            self.reveal(self.last_outputs[0])

    def reveal(self, path: str):
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            else:
                os.startfile(os.path.dirname(path))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    def open_selected_output(self):
        sel = self.tree.selectedItems()
        if not sel or not self.last_outputs:
            return
        stem = Path(sel[0].data(0, Qt.UserRole)).stem
        for out in self.last_outputs:
            if Path(out).stem.startswith(stem[:20]):
                try:
                    os.startfile(out)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    self.reveal(out)
                return
