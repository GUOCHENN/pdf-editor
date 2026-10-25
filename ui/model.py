"""文档模型:打开的 PDF + 用户添加的对象(Overlay)+ 撤销历史。

界面上的所有修改都经过这里,所以撤销/重做、"有未保存修改"的判断都只在这一处实现。

撤销用"快照"实现:每一步记下修改前后的状态(文档版本号 + 所有对象的副本)。
对象很小,复制成本可以忽略;只有旋转/删除页面这类会改动 PDF 本身的操作才会产生新的文档版本,
旧版本的字节保存在 versions 里,撤销时重新打开。
"""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

import engine as E

UNDO_LIMIT = 100
MERGE_SECONDS = 1.0


@dataclass
class State:
    ver: int
    overlays: list = field(default_factory=list)


@dataclass
class Step:
    text: str
    before: State
    after: State
    key: str | None = None
    t: float = 0.0


_LOST = object()  # "已保存的状态"已经不在历史里了(撤销后又做了新修改)


class DocumentModel(QObject):
    opened = Signal()                 # 打开/关闭了文档
    structureChanged = Signal()       # PDF 本身变了(页面增删、旋转、填表、水印……),页面要重新渲染
    overlaysChanged = Signal(object)  # 这些页上的对象变了:set[int];None 表示全部
    historyChanged = Signal()
    modifiedChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = None
        self.path: str | None = None
        self.password: str | None = None
        self.name_hint: str | None = None
        self.overlays: list[E.Overlay] = []
        self.ver = 0
        self._ver_counter = 0
        self.versions: dict[int, bytes] = {}
        self.undo_steps: list[Step] = []
        self.redo_steps: list[Step] = []
        self._clean = None
        self._modified = False
        self._cache: dict = {}

    # ------------------------------------------------------------------ 打开 / 关闭
    def open(self, path: str, password: str | None = None):
        """打开文件;加密文件没有给对密码时抛出 E.NeedPassword。"""
        doc = E.open_document(path, password)
        self._reset(doc, path, password)

    def load(self, doc, path=None, password=None, name=None):
        """载入已经打开的文档。name:还没有保存过的新文档(合并、图片转 PDF)显示的名字。"""
        self._reset(doc, path, password, name)

    def close(self):
        self._reset(None, None, None)

    def _reset(self, doc, path, password, name=None):
        if self.doc is not None:
            self.doc.close()
        self.doc, self.path, self.password = doc, path, password
        self.name_hint = name
        self.overlays = []
        self.versions.clear()
        self.undo_steps.clear()
        self.redo_steps.clear()
        self._clean = None
        self._cache.clear()
        self._ver_counter += 1
        self.ver = self._ver_counter
        self._set_modified(False)
        self.opened.emit()
        self.historyChanged.emit()

    # ------------------------------------------------------------------ 查询
    @property
    def is_open(self) -> bool:
        return self.doc is not None

    def page_count(self) -> int:
        return len(self.doc) if self.doc is not None else 0

    def page_size(self, pno: int):
        r = self.doc[pno].rect
        return r.width, r.height

    def page_overlays(self, pno: int) -> list:
        return [o for o in self.overlays if o.page == pno]

    def find(self, uid):
        for o in self.overlays:
            if o.uid == uid:
                return o
        return None

    def display_name(self) -> str:
        if self.path:
            return os.path.basename(self.path)
        return (self.name_hint or "未命名.pdf") if self.doc is not None else ""

    def covers(self, pno: int) -> list:
        return [r for o in self.overlays if o.page == pno and o.kind == "text" for r in o.props["cover"]]

    def patches(self, pno: int) -> list:
        return E.bg_patches(self.page_overlays(pno))

    # 和文档版本绑定的缓存(页面文字行、表单域、单词……),文档一变自动失效
    def _cached(self, kind: str, pno: int, fn):
        key = (kind, self.ver, pno)
        if key not in self._cache:
            if len(self._cache) > 64:
                self._cache.clear()
            try:
                self._cache[key] = fn(self.doc[pno])
            except Exception:  # noqa: BLE001 — 个别页面结构损坏时不让界面崩溃
                self._cache[key] = []
        return self._cache[key]

    def lines(self, pno: int) -> list:
        return self._cached("lines", pno, E.page_lines)

    def widgets(self, pno: int) -> list:
        return self._cached("widgets", pno, E.list_widgets)

    def words(self, pno: int) -> list:
        return self._cached("words", pno, lambda p: p.get_text("words"))

    def is_scanned(self, pno: int) -> bool:
        return bool(self._cached("scanned", pno, lambda p: [True] if E.page_is_scanned(p) else []))

    def covered(self, pno: int, line) -> bool:
        """这一行原文是否已经被某个文字对象接管(正在被编辑/已经改过)。"""
        b = line["band"]
        for r in self.covers(pno):
            if all(abs(r[i] - b[i]) < 0.3 for i in range(4)):
                return True
        return False

    def line_at(self, pno: int, x: float, y: float):
        best, area = None, 1e18
        for ln in self.lines(pno):
            b = ln["bbox"]
            if b.x0 <= x <= b.x1 and b.y0 <= y <= b.y1 and not self.covered(pno, ln):
                a = b.width * b.height
                if a < area:
                    best, area = ln, a
        return best

    def select_words(self, pno: int, p0, p1) -> list:
        return E.select_words(self.doc[pno], p0, p1, words=self.words(pno))

    # ------------------------------------------------------------------ 历史
    def state(self) -> State:
        return State(self.ver, [o.clone() for o in self.overlays])

    def push(self, text: str, before: State, key: str | None = None):
        """记录一步撤销:before 是修改前的状态(修改前用 state() 取得),修改后的状态就是现在。

        key 相同且间隔很短的连续修改(方向键微调、拖动滑块)合并成一步。"""
        if self.doc is None:
            return
        after = self.state()
        now = time.monotonic()
        top = self.undo_steps[-1] if self.undo_steps else None
        if (key and top is not None and top.key == key and now - top.t < MERGE_SECONDS and top is not self._clean
                and top.after.ver == before.ver):
            top.after, top.t = after, now
        else:
            self.undo_steps.append(Step(text, before, after, key, now))
            if len(self.undo_steps) > UNDO_LIMIT:
                dropped = self.undo_steps.pop(0)
                if dropped is self._clean:
                    self._clean = _LOST
        if self._clean is not None and self._clean is not _LOST and self._clean in self.redo_steps:
            self._clean = _LOST
        self.redo_steps.clear()
        self._prune()
        self._update_modified()
        self.historyChanged.emit()

    def change(self, text: str, fn, pages=None, key: str | None = None):
        """执行 fn() 修改对象,并记一步撤销。"""
        before = self.state()
        result = fn()
        self.push(text, before, key)
        self.overlaysChanged.emit(pages)
        return result

    def touch(self, pages=None):
        """对象已经被直接修改(不记撤销,或撤销另外记),通知界面刷新。"""
        self.overlaysChanged.emit(pages)

    def can_undo(self) -> bool:
        return bool(self.undo_steps)

    def can_redo(self) -> bool:
        return bool(self.redo_steps)

    def undo_text(self) -> str:
        return self.undo_steps[-1].text if self.undo_steps else ""

    def redo_text(self) -> str:
        return self.redo_steps[-1].text if self.redo_steps else ""

    def undo(self) -> bool:
        if not self.undo_steps:
            return False
        step = self.undo_steps.pop()
        self.redo_steps.append(step)
        self._restore(step.before)
        return True

    def redo(self) -> bool:
        if not self.redo_steps:
            return False
        step = self.redo_steps.pop()
        self.undo_steps.append(step)
        self._restore(step.after)
        return True

    def _restore(self, st: State):
        pages = {o.page for o in self.overlays} | {o.page for o in st.overlays}
        structural = st.ver != self.ver
        if structural:
            self._ensure_bytes(self.ver)
            old = self.doc
            self.doc = E.doc_from_bytes(self.versions[st.ver])
            old.close()
            self.ver = st.ver
        self.overlays = [o.clone() for o in st.overlays]
        self._prune()
        self._update_modified()
        if structural:
            self.structureChanged.emit()
        else:
            self.overlaysChanged.emit(pages)
        self.historyChanged.emit()

    def _ensure_bytes(self, ver: int):
        if ver not in self.versions:
            self.versions[ver] = E.snapshot(self.doc)

    def _prune(self):
        keep = {self.ver}
        for s in self.undo_steps + self.redo_steps:
            keep.add(s.before.ver)
            keep.add(s.after.ver)
        for v in list(self.versions):
            if v not in keep:
                del self.versions[v]

    # ------------------------------------------------------------------ 修改 PDF 本身
    def _replace_doc(self, text: str, fn, bake: bool, then=None):
        """then():新文档换上之后、记撤销之前调用,用来顺便修改对象(和换文档算同一步)。"""
        if self.doc is None:
            return None
        data = E.snapshot(self.doc)
        work = E.doc_from_bytes(data)
        try:
            if bake and self.overlays:
                E.apply_overlays(work, self.overlays)
            result = fn(work)
        except Exception:
            work.close()
            raise
        before = self.state()
        self.versions.setdefault(self.ver, data)
        old = self.doc
        self.doc = work
        old.close()
        self._ver_counter += 1
        self.ver = self._ver_counter
        if bake:
            self.overlays = []
        if then is not None:
            then()
        self.push(text, before)
        self.structureChanged.emit()
        return result

    def structural(self, text: str, fn):
        """改变页面结构(旋转/删除/移动/插入……):已有的对象先写进 PDF,再执行 fn(新文档)。"""
        return self._replace_doc(text, fn, bake=True)

    def mutate(self, text: str, fn, then=None):
        """修改 PDF 内容但不改变页面结构(填表单、水印、OCR 文字层……):对象保持可编辑。"""
        return self._replace_doc(text, fn, bake=False, then=then)

    # ------------------------------------------------------------------ 保存 / 导出
    @property
    def modified(self) -> bool:
        return self._modified

    def _update_modified(self):
        top = self.undo_steps[-1] if self.undo_steps else None
        self._set_modified(top is not self._clean)

    def _set_modified(self, v: bool):
        if v != self._modified:
            self._modified = v
            self.modifiedChanged.emit(v)

    def save(self, path: str, password: str | None = None):
        E.save_document(self.doc, self.overlays, path, password)
        self.path, self.password = path, password
        self._clean = self.undo_steps[-1] if self.undo_steps else None
        self._update_modified()
        self.historyChanged.emit()

    def baked_copy(self):
        """带有当前所有编辑内容的文档副本(导出、提取页面用)。用完要 close()。"""
        work = E.doc_from_bytes(E.snapshot(self.doc))
        if self.overlays:
            E.apply_overlays(work, self.overlays)
        return work

    def export_temp(self) -> str:
        """把当前文档(含未保存的修改)存成临时 PDF,给格式转换用。"""
        tmpdir = tempfile.mkdtemp(prefix="pdfeditor_cur_")
        stem = os.path.splitext(self.display_name() or "未命名")[0]
        path = os.path.join(tmpdir, stem + ".pdf")
        E.save_document(self.doc, self.overlays, path)
        return path
