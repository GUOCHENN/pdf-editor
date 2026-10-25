"""文字就地编辑:在页面上直接打字修改。

编辑框是页面上的一个 QGraphicsTextItem,按 1 个单位 = 1 个屏幕像素来排版(所以光标始终是 1 像素细线,
缩放时重新排版)。字体、字号、行距尽量和 engine 的排版一致,提交后由 engine 重新渲染成最终效果。

跨行编辑(和 Word 的体验一致):
* 行首按退格:这一行接到上一行末尾;行尾按 Delete:把下一行接上来。合并后的文字在原来的栏宽内自动换行。
* ↑ ↓(在第一行/最后一行)、行首 ←、行尾 →:直接移到上一行 / 下一行接着改。
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QTextBlockFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import QGraphicsTextItem, QStyle

import engine as E
from ui.theme import T

QT_FAMILY = {"yahei": "Microsoft YaHei", "simsun": "SimSun", "simhei": "SimHei", "kaiti": "KaiTi",
             "fangsong": "FangSong", "arial": "Arial", "times": "Times New Roman", "courier": "Courier New",
             "calibri": "Calibri", "helv": "Arial", "tiro": "Times New Roman", "cour": "Courier New"}

_DPI = None
_FIXED_HEIGHT = getattr(QTextBlockFormat.LineHeightTypes.FixedHeight, "value", 2)


def _font_dpi() -> float:
    """Qt 把字号(磅)换算成像素时用的 DPI(通常 96)。"""
    global _DPI
    if _DPI is None:
        a, b = QFont("Arial"), QFont("Arial")
        a.setPointSizeF(72)
        b.setPixelSize(72)
        hb = QFontMetricsF(b).height()
        _DPI = 72.0 * QFontMetricsF(a).height() / hb if hb > 0 else 96.0
    return _DPI


def qt_font(key: str, size_px: float, bold=False, italic=False) -> QFont:
    if key in (None, "", "auto") or key not in QT_FAMILY:
        key = E.default_font_key()
    f = QFont(QT_FAMILY.get(key, "Microsoft YaHei"))
    f.setPointSizeF(max(size_px, 1.0) * 72.0 / _font_dpi())
    f.setBold(bool(bold))
    f.setItalic(bool(italic))
    f.setHintingPreference(QFont.PreferNoHinting)
    f.setStyleStrategy(QFont.PreferAntialias)
    return f


_LATIN_END = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,;:!?)]}\"'%")


def join_sep(a: str, b: str) -> str:
    """两行文字拼起来时中间要不要加空格(西文单词之间要,中文不要,连字符结尾不要)。"""
    if not a or not b or a[-1].isspace() or b[0].isspace() or a.endswith("-"):
        return ""
    return " " if a[-1] in _LATIN_END and (b[0].isalnum() and b[0].isascii() or b[0] in "(\"'") else ""


class InlineEditor(QGraphicsTextItem):
    def __init__(self, ctl: "TextEditing", ov, page_item):
        super().__init__(page_item)
        self.ctl, self.ov = ctl, ov
        self.setZValue(2000)
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        doc = self.document()
        doc.setUseDesignMetrics(True)
        opt = doc.defaultTextOption()
        opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(opt)
        self.setPlainText(ov.props["text"])
        doc.contentsChanged.connect(self._changed)
        self.restyle()

    # ---- 外观
    def restyle(self):
        """按对象的样式和当前缩放重新排版。"""
        p = self.ov.props
        s = self.ctl.view.s
        lay = E.layout_text(self.ov)
        self.setScale(1.0 / s)
        self.document().setDocumentMargin(3.0)
        self.setFont(qt_font(p["font"], p["size"] * s, p["bold"], p["italic"]))
        self.setDefaultTextColor(QColor(p["color"]))
        if p["wrap"]:
            w = max(self.ov.rect[2] - self.ov.rect[0], p["size"])
            self.setTextWidth(w * s + 6.0)
        else:
            self.setTextWidth(-1)
        opt = self.document().defaultTextOption()
        opt.setAlignment(Qt.AlignJustify if p.get("justify") and p["wrap"] else Qt.AlignLeft)
        self.document().setDefaultTextOption(opt)
        cur = QTextCursor(self.document())
        cur.select(QTextCursor.Document)
        bf = QTextBlockFormat()
        bf.setLineHeight(lay["lh"] * s, _FIXED_HEIGHT)
        cur.mergeBlockFormat(bf)
        self._align()

    def _align(self):
        """让第一行的基线和 engine 排版的基线重合。"""
        lay = E.layout_text(self.ov)
        s = self.ctl.view.s
        self.document().size()  # 触发排版
        block = self.document().firstBlock()
        tl = block.layout()
        if tl is None or tl.lineCount() == 0:
            self.setPos(self.ov.rect[0] - 3.0 / s, self.ov.rect[1] - 3.0 / s)
            return
        line = tl.lineAt(0)
        base = tl.position().y() + line.y() + line.ascent()
        x_off = tl.position().x() + line.x()
        self.setPos(self.ov.rect[0] - x_off / s, lay["base0"] - base / s)

    def _changed(self):
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        r = self.boundingRect()
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        fill = T.q("primary", 22)
        painter.setBrush(fill)
        pen = QPen(T.q("primary", 200), 1.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        painter.restore()
        # 不要 Qt 默认画的虚线焦点框(上面已经画了自己的边框)
        option.state &= ~(QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_Selected)
        super().paint(painter, option, widget)

    # ---- 光标
    def cursor_page_x(self) -> float:
        cur = self.textCursor()
        block = cur.block()
        tl = block.layout()
        line = tl.lineForTextPosition(cur.positionInBlock())
        if not line.isValid():
            return self.mapToParent(QPointF(0, 0)).x()
        x = tl.position().x() + line.cursorToX(cur.positionInBlock())[0]
        return self.mapToParent(QPointF(x, 0)).x()

    def place_cursor(self, where):
        """where:"end" / "start" / 字符位置 int / ("x", 页面横坐标, "first"|"last") / ("pt", x, y)。"""
        doc = self.document()
        cur = QTextCursor(doc)
        if where == "start":
            cur.movePosition(QTextCursor.Start)
        elif isinstance(where, int):
            cur.setPosition(max(0, min(where, doc.characterCount() - 1)))
        elif isinstance(where, tuple) and where[0] in ("x", "pt"):
            if where[0] == "x":
                blk = doc.firstBlock() if where[2] == "first" else doc.lastBlock()
                tl = blk.layout()
                ln = tl.lineAt(0 if where[2] == "first" else max(tl.lineCount() - 1, 0))
                ly = tl.position().y() + (ln.y() + ln.height() / 2 if ln.isValid() else 0)
                local = QPointF(self.mapFromParent(QPointF(where[1], 0)).x(), ly)
            else:
                local = self.mapFromParent(QPointF(where[1], where[2]))
            pos = doc.documentLayout().hitTest(local, Qt.FuzzyHit)
            cur.setPosition(max(pos, 0))
        else:
            cur.movePosition(QTextCursor.End)
        self.setTextCursor(cur)

    def _at_first_line(self, cur: QTextCursor) -> bool:
        blk = cur.block()
        if blk != self.document().firstBlock():
            return False
        ln = blk.layout().lineForTextPosition(cur.positionInBlock())
        return not ln.isValid() or ln.lineNumber() == 0

    def _at_last_line(self, cur: QTextCursor) -> bool:
        blk = cur.block()
        if blk != self.document().lastBlock():
            return False
        tl = blk.layout()
        ln = tl.lineForTextPosition(cur.positionInBlock())
        return not ln.isValid() or ln.lineNumber() == tl.lineCount() - 1

    # ---- 键盘
    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        cur = self.textCursor()
        plain = not (mods & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier))
        if k == Qt.Key_Escape or (k in (Qt.Key_Return, Qt.Key_Enter) and mods & Qt.ControlModifier):
            self.ctl.commit()
            return
        if k == Qt.Key_Tab:
            cur.insertText("    ")
            return
        if plain and not cur.hasSelection():
            if k == Qt.Key_Backspace and cur.position() == 0:
                self.ctl.join(-1)
                return
            if k == Qt.Key_Delete and cur.atEnd():
                self.ctl.join(1)
                return
            if k == Qt.Key_Up and self._at_first_line(cur):
                self.ctl.navigate(-1, "x")
                return
            if k == Qt.Key_Down and self._at_last_line(cur):
                self.ctl.navigate(1, "x")
                return
            if k == Qt.Key_Left and cur.position() == 0:
                self.ctl.navigate(-1, "end")
                return
            if k == Qt.Key_Right and cur.atEnd():
                self.ctl.navigate(1, "start")
                return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        # 切到别的窗口、弹出右键菜单时不结束编辑;点属性面板改字号也不结束(改动直接作用在正在编辑的文字上)
        super().focusOutEvent(e)


@dataclass
class Session:
    ov: object
    before: object
    new: bool
    orig: str
    cover: bool
    dirty: bool = False


class TextEditing:
    """管理"正在编辑哪段文字",以及提交、跨行合并、上下行移动。"""

    def __init__(self, view):
        self.view = view
        self.model = view.model
        self.state = view.state
        self.ed: InlineEditor | None = None
        self.sess: Session | None = None

    @property
    def active(self) -> bool:
        return self.ed is not None

    @property
    def overlay(self):
        return self.sess.ov if self.sess else None

    def contains_view_pos(self, pos) -> bool:
        if self.ed is None:
            return False
        sp = self.view.mapToScene(pos.toPoint() if hasattr(pos, "toPoint") else pos)
        m = 3 / self.view.s
        return self.ed.sceneBoundingRect().adjusted(-m, -m, m, m).contains(sp)

    # ------------------------------------------------------------------ 开始 / 结束
    def begin(self, ov, before=None, new=False, cursor="end", dirty=False):
        """在 ov 上打开编辑框。before:这次编辑开始前的文档状态(默认现在);new:对象是刚为这次编辑创建的。"""
        if self.ed is not None:
            self.commit()
        page = self.view.page_item(ov.page)
        if page is None:
            return
        if before is None:
            before = self.model.state()
        self.sess = Session(ov, before, new, ov.props["text"], bool(ov.props["cover"]), dirty)
        self.view.hide_overlay(ov.uid, True)
        if (ov.props["cover"] or ov.props.get("bgrects")) and page.full_key != self.view._base_key(ov.page):
            self.view.render_page_now(ov.page)  # 原文马上消失,不要和编辑框叠在一起
        self.ed = InlineEditor(self, ov, page)
        self.ed.place_cursor(cursor)
        self.view.setFocus(Qt.OtherFocusReason)
        self.ed.setFocus(Qt.OtherFocusReason)
        self.state.select([ov.uid])
        self.view.update_chrome()
        self.view.ensure_visible(ov.page, ov.bbox(), margin=40)
        self.view.editingChanged.emit(True)

    def _remove_editor(self):
        ed = self.ed
        self.ed = None
        if ed is not None:
            ed.clearFocus()
            sc = ed.scene()
            if sc is not None:
                sc.removeItem(ed)

    def drop(self):
        """丢弃编辑框,不提交(文档被整体替换时用)。"""
        was = self.ed is not None
        if self.sess is not None:
            self.view.hide_overlay(self.sess.ov.uid, False)
        self._remove_editor()
        self.sess = None
        if was:
            self.view.editingChanged.emit(False)

    def commit(self):
        """结束编辑,把文字写回对象(记一步撤销)。"""
        if self.ed is None:
            return
        self._commit()
        if self.ed is None:
            self.view.editingChanged.emit(False)

    def _commit(self):
        ed, sess = self.ed, self.sess
        if ed is None or sess is None:
            return
        text = ed.toPlainText()
        self._remove_editor()
        self.sess = None
        ov = sess.ov
        self.view.hide_overlay(ov.uid, False)
        if self.model.find(ov.uid) is not ov:
            self.view.update_chrome()
            return
        discard = sess.new and not sess.dirty and (
            (not sess.cover and not text.strip()) or (sess.cover and text == sess.orig))
        if discard:  # 新建了但什么都没改:当作没发生过
            self.model.overlays = [o for o in self.model.overlays if o.uid != ov.uid]
            self.state.select([])
            self.model.touch({ov.page})
            return
        if text != ov.props["text"] or sess.dirty or sess.new:
            ov.props["text"] = text
            ov._cache = None
            if not text.strip() and not ov.props["cover"]:  # 添加的文字被清空了 = 删除它
                self.model.overlays = [o for o in self.model.overlays if o.uid != ov.uid]
                self.state.select([])
                self.model.push("删除文字", sess.before)
            else:
                self.model.push("编辑文字", sess.before)
        self.model.touch({ov.page})

    # ------------------------------------------------------------------ 编辑中修改样式
    def set_prop(self, key, value):
        if self.sess is None:
            return
        ov = self.sess.ov
        if ov.props.get(key) == value:
            return
        ov.props[key] = value
        ov._cache = None
        self.sess.dirty = True
        self.ed.restyle()

    def on_zoom(self):
        if self.ed is not None:
            self.ed.restyle()

    def on_scroll(self):
        pass

    # ------------------------------------------------------------------ 跨行
    def neighbor(self, ov, direction):
        """ov 上方(-1)或下方(1)最近的一行:已有的文字对象,或者页面上还没编辑过的原文行。"""
        x0, y0, x1, y1 = ov.bbox()
        cy = (y0 + y1) / 2
        best, bestd = None, 1e18
        cands = [("ov", o, o.bbox()) for o in self.model.page_overlays(ov.page) if o is not ov and o.kind == "text"]
        cands += [("line", ln, tuple(ln["bbox"])) for ln in self.model.lines(ov.page)
                  if not self.model.covered(ov.page, ln)]
        for kind, obj, r in cands:
            if min(r[2], x1) - max(r[0], x0) <= 0:  # 要在同一栏(横向有重叠)
                continue
            d = ((r[1] + r[3]) / 2 - cy) * direction
            if 2 < d < bestd:
                best, bestd = (kind, obj), d
        return best

    def _target_overlay(self, target):
        kind, obj = target
        if kind == "ov":
            return obj, False
        page = self.view.state.current_page if self.sess is None else self.sess.ov.page
        ov = E.overlays_from_lines(page, [obj], self.model.doc[page])[0]
        self.model.overlays.append(ov)
        return ov, True

    @staticmethod
    def _absorb(dst, src):
        """src 的原文区域也交给 dst 负责删除/盖住。"""
        dst.props["cover"] = list(dst.props.get("cover", [])) + list(src.props.get("cover", []))
        dst.props["bgrects"] = list(dst.props.get("bgrects", [])) + list(src.props.get("bgrects", []))
        if not dst.props.get("bg") and src.props.get("bg"):
            dst.props["bg"] = src.props["bg"]

    def _column_right(self, pno, x0, y0, y1, size):
        """这段文字所在栏的右边界:看附近、左边对齐的原文行最远到哪里。"""
        best = 0.0
        win = size * 10
        for ln in self.model.lines(pno):
            b = ln["bbox"]
            if b.y1 < y0 - win or b.y0 > y1 + win:
                continue
            if x0 - size * 2 <= b.x0 <= x0 + size * 4:
                best = max(best, b.x1)
        return best

    def _reflow(self, upper, lower):
        """两行合并到 upper 之前调用:合并后在原来的栏宽里自动换行,行距沿用原来两行的间距。返回栏的右边界。"""
        ub, lb = E.layout_text(upper), E.layout_text(lower)
        x0 = min(upper.rect[0], lower.rect[0])
        # 栏宽按原文的实际范围算:两端对齐的段落里字距被拉开过;段落的头两行也可能比整栏短
        covers = list(upper.props.get("cover") or []) + list(lower.props.get("cover") or [])
        x1 = max([ub["box"][2], lb["box"][2]] + [r[2] + 0.2 for r in covers] +
                 [self._column_right(upper.page, x0, ub["box"][1], lb["box"][3], upper.props["size"])])
        size = upper.props["size"]
        if not upper.props.get("lh"):
            dy = lb["base0"] - (ub["base0"] + (len(ub["lines"]) - 1) * ub["lh"])
            if 0.8 * size < dy < 3.0 * size:
                upper.props["lh"] = dy / size
        upper.rect = [x0, upper.rect[1], x1, upper.rect[1]]
        upper.props["wrap"] = True
        upper._cache = None
        return x1

    def _paragraph_tail(self, lower, col_right, step=None):
        """lower 下面、属于同一段的原文行。合并两行后把整段剩下的部分也接进来,删字时后面的字能像 Word
        一样往上流,而不是挤出一行压到下面的文字上。判断"同一段":上一行写满了整栏、样式相同、行距一致、左边对齐。

        step:这一段的行距(合并的两行之间的距离);段落之间通常会多空一点,行距一变就说明换段了。"""
        pno = lower.page
        size = lower.props["size"]
        lay = E.layout_text(lower)
        last_base = lay["base0"] + (len(lay["lines"]) - 1) * lay["lh"]
        covers = lower.props.get("cover") or []
        prev_x1 = max((r[2] + 0.2 for r in covers), default=lay["box"][2])
        if step is None and lower.props.get("lh"):
            step = lay["lh"]
        x0 = lower.rect[0]
        cands = sorted((ln for ln in self.model.lines(pno) if not self.model.covered(pno, ln)
                        and ln["origin"][1] > last_base + 1), key=lambda ln: ln["origin"][1])
        tail = []
        for ln in cands:
            if abs(ln["bbox"].x0 - x0) > size * 1.2:
                if ln["bbox"].x1 < x0 or ln["bbox"].x0 > col_right:
                    continue  # 别的栏里的行
                break
            if prev_x1 < col_right - max(size * 1.5, 4):  # 上一行没写满:这一段到此结束
                break
            dy = ln["origin"][1] - last_base
            if not 0.8 * size < dy < 2.4 * size or (step and abs(dy - step) > max(1.0, step * 0.12)):
                break
            if not self._same_style(("ov", lower), ("line", ln)) or ln["text"].lstrip()[:1] in "•●▪■◦·-–—>➢":
                break
            tail.append(ln)
            step = step or dy
            last_base, prev_x1 = ln["origin"][1], ln["bbox"].x1
        return tail

    def _is_justified(self, ov, col_right) -> bool:
        """原文是不是两端对齐:除最后一行外,各行都撑到了栏的右边,而且字距被拉开过。"""
        pno = ov.page
        bands = ov.props.get("cover") or []
        lines = [ln for ln in self.model.lines(pno)
                 if any(all(abs(ln["band"][i] - b[i]) < 0.3 for i in range(4)) for b in bands)]
        lines.sort(key=lambda ln: ln["origin"][1])
        body = lines[:-1]
        if not body:
            return False
        p = ov.props
        font = E.font_for_text(p["font"], p["bold"], p["italic"], ov.props["text"])
        stretched = [ln for ln in body if abs(ln["bbox"].x1 - col_right) < 1.5
                     and font.text_length(ln["text"].rstrip(), p["size"]) < ln["bbox"].width - 1.0]
        return len(stretched) * 2 >= len(body)

    @staticmethod
    def _style_of(target):
        """(字号, 粗体, 斜体, 颜色):已有对象直接读;原文行按 engine 的规则推断。"""
        kind, obj = target
        if kind == "ov":
            p = obj.props
            return p["size"], bool(p["bold"]), bool(p["italic"]), (p["color"] or "").lower()
        _key, bold, italic = E.guess_font_style(obj["font"], obj["flags"])
        return obj["size"], bold, italic, (obj["color"] or "").lower()

    @classmethod
    def _same_style(cls, a, b) -> bool:
        sa, sb = cls._style_of(a), cls._style_of(b)
        return (abs(sa[0] - sb[0]) <= max(0.6, sa[0] * 0.06) and sa[1] == sb[1] and sa[2] == sb[2]
                and sa[3] == sb[3])

    def join(self, direction):
        """行首退格(-1):这一行接到上一行末尾;行尾 Delete(1):把下一行接到这一行末尾。

        两行样式不同(比如正文接到标题后面)时不合并——一个文字对象只能有一种样式,合并后整段都会变成
        标题的样子,还会压到下面的文字。这时只把光标移到上一行末尾 / 下一行开头,继续按键就能接着删。"""
        ed, sess = self.ed, self.sess
        if ed is None:
            return
        ov = sess.ov
        text = ed.toPlainText()
        target = self.neighbor(ov, direction)
        if target is None:
            self.view.message.emit("已经是这一栏的第一行了" if direction < 0 else "已经是这一栏的最后一行了")
            return
        if not self._same_style(("ov", ov), target):
            self.view.message.emit(("上一行" if direction < 0 else "下一行") + "的字体样式不同(比如标题),没有合并 · "
                                   "已移过去,继续按" + ("退格" if direction < 0 else " Delete") + "可以接着删")
            self.navigate(direction, "end" if direction < 0 else "start")
            return
        untouched = sess.new and not sess.dirty and text == sess.orig
        ov.props["text"] = text
        ov._cache = None
        if untouched:  # 刚点开还没改过:合并和点开算同一步
            before = sess.before
        else:  # 先把已经输入的内容记一步,撤销时能回到"改了但还没合并"的状态
            self.model.push("编辑文字", sess.before)
            before = self.model.state()
        self._remove_editor()
        self.sess = None
        self.view.hide_overlay(ov.uid, False)
        tov, _created = self._target_overlay(target) if target[0] == "line" else (target[1], False)
        upper, lower = (tov, ov) if direction < 0 else (ov, tov)  # 合并后保留上面那个对象
        sep = join_sep(upper.props["text"], lower.props["text"])
        pos = len(upper.props["text"]) + len(sep)
        merged = upper.props["text"] + sep + lower.props["text"]
        ub, lb = E.layout_text(upper), E.layout_text(lower)
        step = lb["base0"] - (ub["base0"] + (len(ub["lines"]) - 1) * ub["lh"])  # 这一段的行距
        col_right = self._reflow(upper, lower)
        page = self.model.doc[upper.page]
        for ln in self._paragraph_tail(lower, col_right, step):  # 同一段后面的行也接进来,整段重新排
            t = E.overlays_from_lines(upper.page, [ln], page)[0]
            merged += join_sep(merged, t.props["text"]) + t.props["text"]
            self._absorb(upper, t)
        upper.props["text"] = merged
        self._absorb(upper, lower)
        upper.props["justify"] = upper.props.get("justify") or self._is_justified(upper, col_right)
        upper._cache = None
        self.model.overlays = [o for o in self.model.overlays if o.uid != lower.uid]
        self.model.touch({upper.page})
        self.begin(upper, before=before, new=False, cursor=pos, dirty=True)

    def navigate(self, direction, mode):
        """移到上/下一行继续编辑。mode:"x" 保持光标横向位置,"end" 放到行尾,"start" 放到行首。"""
        ed, sess = self.ed, self.sess
        if ed is None:
            return
        target = self.neighbor(sess.ov, direction)
        if target is None:
            self.view.message.emit("已经是这一栏的第一行了" if direction < 0 else "已经是这一栏的最后一行了")
            return
        cur_x = ed.cursor_page_x() if mode == "x" else None
        self.commit()
        if target[0] == "ov":
            obj = self.model.find(target[1].uid)
            if obj is None:
                return
            target = ("ov", obj)
        before = self.model.state()
        page = target[1].page if target[0] == "ov" else sess.ov.page
        if target[0] == "line":
            tov = E.overlays_from_lines(page, [target[1]], self.model.doc[page])[0]
            self.model.overlays.append(tov)
            created = True
            self.model.touch({page})
        else:
            tov, created = target[1], False
        if mode == "end":
            cursor = "end"
        elif mode == "start":
            cursor = "start"
        else:
            cursor = ("x", cur_x, "last" if direction < 0 else "first")
        self.begin(tov, before=before, new=created, cursor=cursor)
