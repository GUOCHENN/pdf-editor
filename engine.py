"""PDF 编辑引擎:所有与界面无关的 PDF 操作都在这里。

核心思路
--------
用户在界面里添加/编辑的内容都先保存为 ``Overlay`` 对象(文字、图片、形状、标记……),
并不立刻写进 PDF,所以随时可以移动、修改、删除。预览和最终保存用的是同一套绘制代码,
所以"所见即所得"。保存时才通过 ``apply_overlays`` 把它们真正写入 PDF。

坐标系:所有 Overlay 的坐标都是页面"显示坐标"(已应用页面旋转,左上角为原点,单位 pt)。
"""
from __future__ import annotations

import copy
import functools
import itertools
import math
import os
import re
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # 旧版 PyMuPDF
    import fitz  # type: ignore

from PIL import Image

# ----------------------------------------------------------------------------
# 颜色
# ----------------------------------------------------------------------------


def rgb(color):
    """'#rrggbb' -> (r, g, b) 0~1;None 保持 None。"""
    if not color:
        return None
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def hexcolor(value: int) -> str:
    return "#%06x" % (value & 0xFFFFFF)


# ----------------------------------------------------------------------------
# 字体
# ----------------------------------------------------------------------------

_BASE14 = {
    "helv": ("helv", "hebo", "heit", "hebi"),
    "tiro": ("tiro", "tibo", "tiit", "tibi"),
    "cour": ("cour", "cobo", "coit", "cobi"),
}

# key: (显示名, {样式: 字体文件名}, 找不到文件时的内置替代)
FONT_TABLE = {
    "yahei": ("微软雅黑", {"": "msyh.ttc", "b": "msyhbd.ttc"}, "helv"),
    "simsun": ("宋体", {"": "simsun.ttc"}, "tiro"),
    "simhei": ("黑体", {"": "simhei.ttf"}, "helv"),
    "kaiti": ("楷体", {"": "simkai.ttf"}, "tiro"),
    "fangsong": ("仿宋", {"": "simfang.ttf"}, "tiro"),
    "arial": ("Arial", {"": "arial.ttf", "b": "arialbd.ttf", "i": "ariali.ttf", "bi": "arialbi.ttf"}, "helv"),
    "times": ("Times New Roman", {"": "times.ttf", "b": "timesbd.ttf", "i": "timesi.ttf", "bi": "timesbi.ttf"}, "tiro"),
    "courier": ("Courier New", {"": "cour.ttf", "b": "courbd.ttf", "i": "couri.ttf", "bi": "courbi.ttf"}, "cour"),
    "calibri": ("Calibri", {"": "calibri.ttf", "b": "calibrib.ttf", "i": "calibrii.ttf", "bi": "calibriz.ttf"}, "helv"),
    "helv": ("Helvetica (内置)", {}, "helv"),
    "tiro": ("Times (内置)", {}, "tiro"),
    "cour": ("Courier (内置)", {}, "cour"),
}

# 文字里出现当前字体没有的字时,依次尝试这些字体(用于中文等)
_FALLBACK_ORDER = ["yahei", "simsun", "simhei", "kaiti", "fangsong", "arial"]


@functools.lru_cache(maxsize=1)
def _font_index() -> dict:
    dirs = []
    if os.name == "nt":
        win = os.environ.get("WINDIR", r"C:\Windows")
        dirs += [Path(win) / "Fonts", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Windows/Fonts"]
    else:
        dirs += [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts",
                 Path("/usr/share/fonts"), Path.home() / ".fonts"]
    index = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for root, _dirs, files in os.walk(d):
            for f in files:
                index.setdefault(f.lower(), os.path.join(root, f))
    return index


def _find_font_file(filename: str):
    return _font_index().get(filename.lower())


def font_available(key: str) -> bool:
    spec = FONT_TABLE.get(key)
    if not spec:
        return False
    return not spec[1] or any(_find_font_file(f) for f in spec[1].values())


def font_choices():
    """[(key, 显示名)],只列出本机能用的字体。"""
    return [(k, v[0]) for k, v in FONT_TABLE.items() if font_available(k)]


def font_label(key: str) -> str:
    return FONT_TABLE.get(key, (key,))[0]


def default_font_key() -> str:
    for k in ("yahei", "simsun", "arial"):
        if font_available(k):
            return k
    return "helv"


@functools.lru_cache(maxsize=64)
def _font_from_file(path: str):
    return fitz.Font(fontfile=path)


@functools.lru_cache(maxsize=16)
def _builtin_font(name: str):
    return fitz.Font(name)


def _base14(base: str, bold: bool, italic: bool):
    names = _BASE14.get(base, _BASE14["helv"])
    return _builtin_font(names[(1 if bold else 0) + (2 if italic else 0)])


def _load_font(key: str, bold: bool, italic: bool):
    if key in (None, "", "auto"):
        key = default_font_key()
    spec = FONT_TABLE.get(key)
    if not spec:
        return _base14("helv", bold, italic)
    _label, files, base = spec
    style = ("b" if bold else "") + ("i" if italic else "")
    for st in dict.fromkeys([style, "b" if bold else "", ""]):
        fn = files.get(st)
        path = _find_font_file(fn) if fn else None
        if path:
            try:
                return _font_from_file(path)
            except Exception:
                continue
    return _base14(base, bold, italic)


def _covers(font, text: str) -> bool:
    return all(font.has_glyph(ord(c)) for c in set(text) if c not in "\n\r\t \u00a0")


def font_for_text(key: str, bold: bool, italic: bool, text: str):
    """选字体:优先用指定字体,缺字就换一个有这些字的字体,最后用 PyMuPDF 自带的 CJK 字体。"""
    font = _load_font(key, bold, italic)
    if _covers(font, text):
        return font
    for k in _FALLBACK_ORDER:
        if font_available(k):
            f2 = _load_font(k, bold, italic)
            if _covers(f2, text):
                return f2
    return _builtin_font("cjk")


_ALIASES = [
    (("msyh", "microsoftyahei", "yahei", "微软雅黑"), "yahei"),
    (("simsun", "nsimsun", "宋体", "songti"), "simsun"),
    (("simhei", "黑体", "heiti"), "simhei"),
    (("simkai", "kaiti", "楷体"), "kaiti"),
    (("simfang", "fangsong", "仿宋"), "fangsong"),
    (("timesnewroman", "times"), "times"),
    (("couriernew",), "courier"),
    (("courier",), "cour"),
    (("calibri",), "calibri"),
    (("arial",), "arial"),
    (("helvetica",), "helv"),
]


_MONO_HINTS = ("mono", "courier", "consolas", "typewriter", "menlo", "lucidaconsole")
_SANS_HINTS = ("sans", "arial", "helvetica", "roboto", "verdana", "tahoma", "segoe", "gothic", "lato", "dengxian",
               "pingfang", "heiti", "unifont", "opensans", "ubuntu")
_SERIF_HINTS = ("serif", "roman", "times", "minion", "georgia", "garamond", "palatino", "cambria", "bookman",
                "century", "song", "ming", "kai", "fang", "mincho", "batang")


def guess_font_style(name: str, flags: int):
    """根据 PDF 里原字体名/标志位,猜出 (字体 key, 加粗, 斜体)。"""
    clean = re.sub(r"^[A-Z]{6}\+", "", name or "")
    low = re.sub(r"[\s\-_,]", "", clean).lower()
    bold = bool(flags & 16) or any(w in low for w in ("bold", "black", "heavy", "semibold"))
    italic = bool(flags & 2) or any(w in low for w in ("italic", "oblique"))
    key = None
    for needles, k in _ALIASES:
        if any(n in low for n in needles):
            key = k
            break
    if key is None or not font_available(key):
        # 字体名比标志位可靠(MuPDF 给的"衬线"标志位经常不准,例如 NimbusSans 也会被标成衬线)
        if any(h in low for h in _MONO_HINTS):
            kind = "mono"
        elif any(h in low for h in _SANS_HINTS):
            kind = "sans"
        elif any(h in low for h in _SERIF_HINTS):
            kind = "serif"
        else:
            kind = "mono" if flags & 8 else ("serif" if flags & 4 else "sans")
        key = {"mono": ("courier", "cour"), "serif": ("times", "tiro"), "sans": ("arial", "helv")}[kind]
        key = key[0] if font_available(key[0]) else key[1]
    return key, bold, italic


# ----------------------------------------------------------------------------
# Overlay:用户添加到页面上的可编辑对象
# ----------------------------------------------------------------------------

_uids = itertools.count(1)

DEFAULT_PROPS = {
    # cover: 要删除的原文区域;bg/bgrects: 编辑扫描件文字时,先用背景色盖住原来的图片文字
    # justify: 两端对齐(自动换行时,除了每段最后一行,每行都撑满整个宽度)
    "text": dict(text="", font="auto", size=14.0, color="#000000", bold=False, italic=False,
                 wrap=False, lh=None, cover=[], bg=None, bgrects=[], justify=False),
    "rect": dict(stroke="#e53935", width=2.0, fill=None, opacity=1.0),
    "ellipse": dict(stroke="#e53935", width=2.0, fill=None, opacity=1.0),
    "line": dict(stroke="#e53935", width=2.0),
    "arrow": dict(stroke="#e53935", width=2.0),
    "ink": dict(stroke="#e53935", width=2.0, points=[]),
    "image": dict(data=b"", opacity=1.0),
    "markup": dict(sub="highlight", color="#ffeb3b", rects=[]),
    "redact": dict(),
}


class Overlay:
    def __init__(self, kind: str, page: int, rect=(0, 0, 0, 0), **props):
        self.uid = next(_uids)
        self.kind = kind
        self.page = page
        self.rect = [float(v) for v in rect]
        self.props = copy.deepcopy(DEFAULT_PROPS[kind])
        self.props.update(props)
        self._cache = None

    def clone(self, new_uid: bool = False) -> "Overlay":
        o = copy.copy(self)
        o.rect = list(self.rect)
        o.props = copy.deepcopy(self.props)  # bytes 不可变,图片数据不会被真的复制
        if new_uid:
            o.uid = next(_uids)
        return o

    # -- 几何 -----------------------------------------------------------------
    def bbox(self):
        k = self.kind
        if k == "text":
            return layout_text(self)["box"]
        if k == "ink":
            pts = self.props["points"]
            if not pts:
                return (0, 0, 0, 0)
            pad = self.props["width"] / 2
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        if k == "markup":
            rs = self.props["rects"]
            if not rs:
                return (0, 0, 0, 0)
            return (min(r[0] for r in rs), min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))
        x0, y0, x1, y1 = self.rect
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def translate(self, dx: float, dy: float):
        self.rect[0] += dx
        self.rect[1] += dy
        self.rect[2] += dx
        self.rect[3] += dy
        if self.kind == "ink":
            self.props["points"] = [(x + dx, y + dy) for x, y in self.props["points"]]
        elif self.kind == "markup":
            self.props["rects"] = [[r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy] for r in self.props["rects"]]

    def set_bbox(self, new):
        """把对象缩放到新的外框(矩形/椭圆/图片/涂画笔迹)。"""
        nx0, ny0, nx1, ny1 = new
        if self.kind == "ink":
            ox0, oy0, ox1, oy1 = self.bbox()
            pad = self.props["width"] / 2
            ox0, oy0, ox1, oy1 = ox0 + pad, oy0 + pad, ox1 - pad, oy1 - pad
            sx = (nx1 - nx0 - 2 * pad) / (ox1 - ox0) if ox1 - ox0 > 1e-6 else 1
            sy = (ny1 - ny0 - 2 * pad) / (oy1 - oy0) if oy1 - oy0 > 1e-6 else 1
            self.props["points"] = [(nx0 + pad + (x - ox0) * sx, ny0 + pad + (y - oy0) * sy)
                                    for x, y in self.props["points"]]
        else:
            self.rect = [nx0, ny0, nx1, ny1]


# ----------------------------------------------------------------------------
# 文字排版
# ----------------------------------------------------------------------------

_CLOSERS = set("，。、！？；：,.!?;:)]}）】》」』”’…—%")


def line_metrics(font, size: float, lh_ratio=None):
    """(行高, 首行基线相对文字框顶部的偏移)。"""
    asc, desc = font.ascender, font.descender
    glyph_h = size * (asc - desc)
    lh = size * lh_ratio if lh_ratio else max(size * 1.15, glyph_h * 0.92)
    return lh, (lh - glyph_h) / 2 + size * asc


def _wrap(text: str, font, size: float, maxw, ends=None):
    """按宽度换行。ends:传入一个列表时,依次记下每一行是不是一段的最后一行(两端对齐时最后一行不撑开)。"""
    out = []
    for para in text.split("\n"):
        start = len(out)
        _wrap_para(para, font, size, maxw, out)
        if ends is not None:
            ends.extend([False] * (len(out) - start - 1) + [True])
    return out


def _wrap_para(para, font, size, maxw, out):
    if maxw is None:
        out.append(para)
        return
    line, lw = "", 0.0
    for ch in para:
        adv = font.glyph_advance(ord(ch)) * size
        if line and lw + adv > maxw and ch not in _CLOSERS and ch != " ":
            sp = line.rfind(" ")
            if sp > 0:
                out.append(line[:sp].rstrip())
                line = line[sp + 1:] + ch
                lw = font.text_length(line, size)
            else:
                out.append(line)
                line, lw = ch, adv
        else:
            line += ch
            lw += adv
    out.append(line)


def layout_text(ov: Overlay) -> dict:
    """计算文字对象的换行、尺寸。非自动换行的文字会同时更新 rect 的右边界。"""
    p = ov.props
    text = p["text"].replace("\t", "    ")
    wrap = bool(p["wrap"])
    width = max(ov.rect[2] - ov.rect[0], p["size"]) if wrap else 0
    key = (text, p["font"], p["size"], p["bold"], p["italic"], wrap, round(width, 2), p.get("lh"),
           round(ov.rect[0], 2), round(ov.rect[1], 2))
    if ov._cache and ov._cache[0] == key:
        lay = ov._cache[1]
    else:
        font = font_for_text(p["font"], p["bold"], p["italic"], text)
        size = p["size"]
        lh, off = line_metrics(font, size, p.get("lh"))
        ends = []
        lines = _wrap(text, font, size, width if wrap else None, ends)
        widths = [font.text_length(s, size) for s in lines]
        w = width if wrap else max(widths + [size * 0.6])
        x0, y0 = ov.rect[0], ov.rect[1]
        lay = dict(lines=lines, ends=ends, widths=widths, font=font, size=size, lh=lh, base0=y0 + off,
                   box=(x0, y0, x0 + w, y0 + lh * len(lines)))
        ov._cache = (key, lay)
    if not wrap:
        ov.rect[2] = lay["box"][2]
    return lay


def _justified_pieces(line: str, natural: float, width: float, font, size: float):
    """两端对齐的一行:返回 [(相对横坐标, 文字片段)]。不适合撑开(太稀疏、只有一个字)时返回 None。"""
    extra = width - natural
    if extra <= 0.05:
        return None
    stripped = line.rstrip()
    if " " in stripped.strip():  # 西文:加宽空格
        words = stripped.split(" ")
        gaps = len(words) - 1
        if gaps <= 0 or extra / gaps > size * 3:
            return None
        add = extra / gaps
        out, x = [], 0.0
        sp = font.text_length(" ", size)
        for k, word in enumerate(words):
            if word:
                out.append((x, word))
            x += font.text_length(word, size) + sp + (add if k < len(words) - 1 else 0)
        return out
    chars = list(stripped)  # 中文等没有空格的文字:加宽字间距
    if len(chars) < 2 or extra / (len(chars) - 1) > size:
        return None
    add = extra / (len(chars) - 1)
    out, x = [], 0.0
    for ch in chars:
        out.append((x, ch))
        x += font.text_length(ch, size) + add
    return out


def text_runs(ov: Overlay):
    """文字对象要画的每一段:[(x, 基线 y, 文字)]。两端对齐的行会拆成多段。"""
    lay = layout_text(ov)
    x0 = ov.rect[0]
    width = lay["box"][2] - lay["box"][0]
    justify = bool(ov.props.get("justify")) and bool(ov.props.get("wrap"))
    runs = []
    for i, line in enumerate(lay["lines"]):
        if not line.strip():
            continue
        y = lay["base0"] + i * lay["lh"]
        pieces = None
        if justify and not lay["ends"][i]:
            pieces = _justified_pieces(line, lay["widths"][i], width, lay["font"], lay["size"])
        if pieces is None:
            runs.append((x0, y, line))
        else:
            runs += [(x0 + dx, y, s) for dx, s in pieces]
    return runs


# ----------------------------------------------------------------------------
# 把 Overlay 画到页面上
# ----------------------------------------------------------------------------


def _draw_text(page, ov: Overlay):
    lay = layout_text(ov)
    if ov.props.get("bg") and ov.props.get("bgrects"):  # 先用背景色盖住扫描图里的原文字
        sh = page.new_shape()
        for r in ov.props["bgrects"]:
            sh.draw_rect(fitz.Rect(*r))
        sh.finish(color=None, fill=rgb(ov.props["bg"]))
        sh.commit()
    tw = fitz.TextWriter(page.rect)
    runs = text_runs(ov)
    for x, y, s in runs:
        tw.append((x, y), s, font=lay["font"], fontsize=lay["size"])
    if runs:
        tw.write_text(page, color=rgb(ov.props["color"]))


def _arrow_points(p1, p2, width):
    ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    head = max(9.0, width * 4.5)
    spread = math.radians(26)
    left = (p2[0] - head * math.cos(ang - spread), p2[1] - head * math.sin(ang - spread))
    right = (p2[0] - head * math.cos(ang + spread), p2[1] - head * math.sin(ang + spread))
    base = (p2[0] - head * 0.8 * math.cos(ang), p2[1] - head * 0.8 * math.sin(ang))
    return left, right, base


def _draw_overlay(page, ov: Overlay, preview: bool):
    k, p = ov.kind, ov.props
    if k == "text":
        _draw_text(page, ov)
    elif k in ("rect", "ellipse"):
        stroke, fill = rgb(p["stroke"]), rgb(p["fill"])
        if not stroke and not fill:
            return
        sh = page.new_shape()
        r = fitz.Rect(*ov.bbox())
        if r.width < 0.5 or r.height < 0.5:
            return
        (sh.draw_rect if k == "rect" else sh.draw_oval)(r)
        sh.finish(color=stroke, fill=fill, width=p["width"] if stroke else 0,
                  fill_opacity=p["opacity"], closePath=True)
        sh.commit()
    elif k in ("line", "arrow"):
        p1, p2 = (ov.rect[0], ov.rect[1]), (ov.rect[2], ov.rect[3])
        if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) < 1:
            return
        col = rgb(p["stroke"])
        sh = page.new_shape()
        if k == "arrow":
            left, right, base = _arrow_points(p1, p2, p["width"])
            sh.draw_line(p1, base)
            sh.finish(color=col, width=p["width"], lineCap=1)
            sh.draw_polyline([p2, left, right])
            sh.finish(color=col, fill=col, width=0.5, closePath=True, lineJoin=1)
        else:
            sh.draw_line(p1, p2)
            sh.finish(color=col, width=p["width"], lineCap=1)
        sh.commit()
    elif k == "ink":
        pts = p["points"]
        col = rgb(p["stroke"])
        sh = page.new_shape()
        if len(pts) == 1:
            sh.draw_circle(pts[0], p["width"] / 2)
            sh.finish(color=col, fill=col, width=0)
        elif len(pts) > 1:
            sh.draw_polyline(pts)
            sh.finish(color=col, width=p["width"], closePath=False, lineCap=1, lineJoin=1)
        sh.commit()
    elif k == "image":
        if p["data"]:
            page.insert_image(fitz.Rect(*ov.bbox()), stream=p["data"], keep_proportion=False, overlay=True)
    elif preview and k == "markup":
        col = rgb(p["color"])
        sh = page.new_shape()
        for r in p["rects"]:
            if p["sub"] == "highlight":
                sh.draw_rect(fitz.Rect(*r))
                sh.finish(color=None, fill=col, fill_opacity=0.4)
            else:
                y = r[3] - (r[3] - r[1]) * 0.16 if p["sub"] == "underline" else (r[1] + r[3]) / 2
                sh.draw_line((r[0], y), (r[2], y))
                sh.finish(color=col, width=1.2)
        sh.commit()
    elif preview and k == "redact":
        sh = page.new_shape()
        sh.draw_rect(fitz.Rect(*ov.bbox()))
        sh.finish(color=None, fill=(0, 0, 0))
        sh.commit()


def layer_page(doc, items, size, preview: bool, exclude_uid=None):
    """在 doc 里新增一页,把一组 Overlay 画上去;返回新页的页号,没有可画内容返回 None。

    final 模式(preview=False)不画"标记"和"涂黑":它们在保存时分别变成真正的批注/内容删除。"""
    todo = [o for o in items if o.uid != exclude_uid and (preview or o.kind not in ("markup", "redact"))]
    if not todo:
        return None
    page = doc.new_page(width=size[0], height=size[1])
    for ov in todo:
        _draw_overlay(page, ov, preview)
    return page.number


def build_layer(items, size, preview: bool, exclude_uid=None):
    """单页覆盖层文档(预览用)。没有可画内容返回 None。"""
    doc = fitz.open()
    if layer_page(doc, items, size, preview, exclude_uid) is None:
        doc.close()
        return None
    return doc


def subset_fonts(doc):
    """把内嵌字体裁剪成只含用到的字(微软雅黑整套 12MB,裁剪后只剩几十 KB)。"""
    try:
        doc.subset_fonts()
    except Exception:
        pass


def overlay_pad(ov: Overlay) -> float:
    """对象预览图在外框之外要多留的边(线宽、箭头、斜体出头等)。"""
    w = float(ov.props.get("width") or 0)
    if ov.kind in ("line", "arrow"):
        return max(w * 3, 10.0)
    if ov.kind in ("rect", "ellipse", "ink"):
        return w / 2 + 2
    return 3.0


def overlay_pixmap(ov: Overlay, size, scale: float):
    """单个对象的预览图(透明背景,只渲染它所在的区域)。返回 (fitz.Pixmap 或 None, 区域 (x0,y0,x1,y1) pt)。
    Pixmap 是预乘 alpha 的 RGBA。

    编辑扫描件文字时用的背景色补丁(bg)不画在这里:它固定在原位置,由页面底图负责。"""
    if ov.kind == "text" and ov.props.get("bg"):
        ov = ov.clone()
        ov.props["bg"] = None
    x0, y0, x1, y1 = ov.bbox()
    pad = overlay_pad(ov)
    clip = fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & fitz.Rect(0, 0, size[0], size[1])
    if clip.is_empty or clip.width < 0.5 or clip.height < 0.5:
        return None, tuple(clip)
    doc = build_layer([ov], size, True)
    if doc is None:
        return None, tuple(clip)
    pm = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=True)
    doc.close()
    return pm, tuple(clip)


def render_overlay(ov: Overlay, size, scale: float):
    """同 overlay_pixmap,返回 (PIL RGBA 或 None, 区域)。"""
    pm, clip = overlay_pixmap(ov, size, scale)
    if pm is None:
        return None, clip
    return Image.frombytes("RGBa", (pm.width, pm.height), pm.samples).convert("RGBA"), clip


def bg_patches(items) -> list:
    """编辑扫描件文字时盖住原字的背景色块 [(rect, '#rrggbb')]。"""
    return [(r, o.props["bg"]) for o in items if o.kind == "text" and o.props.get("bg") for r in o.props.get("bgrects", [])]


def render_layer(size, scale: float, items, exclude_uid=None):
    """渲染覆盖层为透明 RGBA 图片,无内容返回 None。"""
    doc = build_layer(items, size, True, exclude_uid)
    if doc is None:
        return None
    pm = doc[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
    # PyMuPDF 输出的是预乘 alpha,用 RGBa 模式读入再转换才不会发暗
    img = Image.frombytes("RGBa", (pm.width, pm.height), pm.samples).convert("RGBA")
    doc.close()
    return img


# ----------------------------------------------------------------------------
# 读取页面里已有的文字行(用于"编辑文字")
# ----------------------------------------------------------------------------

_TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP


def _band(it: dict, items) -> "fitz.Rect":
    """删除某一行文字时使用的窄带。

    MuPDF 的涂改会删除所有与矩形相交的字符,而相邻行的字符框(含上下留白)在行距紧时会互相重叠,
    直接用整行外框会误删上下行。这里把矩形收窄到不碰相邻行字符框的范围。"""
    r = it["bbox"]
    cy = (r.y0 + r.y1) / 2
    y0, y1 = r.y0, r.y1
    for o in items:
        if o is it:
            continue
        b = o["bbox"]
        if b.x1 > r.x0 + 0.2 and b.x0 < r.x1 - 0.2:
            if b.y1 <= cy and b.y1 > y0:
                y0 = b.y1 + 0.05
            elif b.y0 >= cy and b.y0 < y1:
                y1 = b.y0 - 0.05
    if y1 - y0 < 1:
        y0, y1 = cy - 0.5, cy + 0.5
    return fitz.Rect(r.x0 + 0.2, y0, r.x1 - 0.2, y1)


def page_lines(page) -> list:
    """页面上所有水平文字行(显示坐标)。每项含 bbox/band/text/size/color/font/flags/origin。"""
    rm = page.rotation_matrix
    lin = fitz.Matrix(rm.a, rm.b, rm.c, rm.d, 0, 0)
    items = []
    try:  # 不可见文字(OCR 文字层)的区域:这种行下面的图片文字需要用背景色盖住才算"改掉"
        hidden = [fitz.Rect(t["bbox"]) for t in page.get_texttrace() if t.get("type") == 3]
    except Exception:
        hidden = []
    for b in page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            spans = [s for s in ln["spans"] if s["text"]]
            text = "".join(s["text"] for s in spans).replace(" ", " ")  # TextWriter 写出的空格读回来是 NBSP
            if not text.strip():
                continue
            d = fitz.Point(ln["dir"]) * lin
            if abs(d.y) > 0.12 or d.x <= 0:  # 只处理视觉上水平的文字
                continue
            main = max(spans, key=lambda s: len(s["text"].strip()))
            o = fitz.Point(spans[0]["origin"]) * rm
            raw = fitz.Rect(ln["bbox"])
            invisible = raw.get_area() > 0 and any((h & raw).get_area() > 0.5 * raw.get_area() for h in hidden)
            items.append(dict(bbox=(raw * rm).normalize(), text=text, size=main["size"],
                              color=hexcolor(main["color"]), font=main["font"], flags=main["flags"],
                              origin=(o.x, o.y), invisible=invisible))
    for it in items:
        it["band"] = _band(it, items)
    return items


def _median(vals):
    vals = sorted(vals)
    return vals[len(vals) // 2]


def sample_bg(img: Image.Image, rect, scale: float) -> str:
    """估计 rect(显示坐标 pt)区域的背景色:取外框上下两条边的像素中位数。"""
    W, H = img.size
    x0, y0, x1, y1 = (int(v * scale) for v in rect)
    x0, x1 = max(0, x0), min(W, max(x1, x0 + 1))
    strips = []
    for ya, yb in ((y0 - 3, y0 - 1), (y1 + 1, y1 + 3)):
        ya, yb = max(0, ya), min(H, yb)
        if yb > ya and x1 > x0:
            strips.append(img.crop((x0, ya, x1, yb)))
    if not strips:
        strips.append(img.crop((x0, max(0, y0), x1, min(H, y0 + 2))))
    px = [p for s in strips for p in s.getdata()]
    if not px:
        return "#ffffff"
    return "#%02x%02x%02x" % tuple(_median([p[c] for p in px]) for c in range(3))


def overlays_from_lines(pno: int, lines: list, page=None) -> list:
    """把选中的若干文字行变成可编辑的文字 Overlay(同一段落的行合并,并排的栏分开)。

    传入 page 时,如果选中的是 OCR 文字层(不可见文字)的行,会取背景色,编辑时先盖住扫描图里的原字。"""
    lines = sorted(lines, key=lambda l: (round(l["origin"][1], 1), l["origin"][0]))
    bg_img = None
    if page is not None and any(l.get("invisible") for l in lines):
        bg_img = render_base(page.parent, pno, 1.5)
    clusters = []
    for l in lines:
        for c in clusters:
            last = c[-1]
            dy = l["origin"][1] - last["origin"][1]
            cx0, cx1 = min(x["bbox"].x0 for x in c), max(x["bbox"].x1 for x in c)
            if (0.5 * last["size"] < dy < 3.2 * last["size"] and l["bbox"].x1 > cx0 and l["bbox"].x0 < cx1
                    and abs(l["size"] - last["size"]) < 1.5):
                c.append(l)
                break
        else:
            clusters.append([l])
    result = []
    for c in clusters:
        first = c[0]
        key, bold, italic = guess_font_style(first["font"], first["flags"])
        # 行尾的空格看不见,留着的话在行尾按退格会"没反应"(删的是空格),所以去掉
        text = "\n".join(x["text"].rstrip() for x in c)
        size = round(first["size"], 2)
        font = font_for_text(key, bold, italic, text)
        lh_ratio = None
        if len(c) > 1:
            ys = [x["origin"][1] for x in c]
            lh_ratio = (ys[-1] - ys[0]) / (len(ys) - 1) / size
        _lh, off = line_metrics(font, size, lh_ratio)
        x0 = min(x["origin"][0] for x in c)
        y0 = first["origin"][1] - off
        ov = Overlay("text", pno, (x0, y0, max(x["bbox"].x1 for x in c), y0),
                     text=text, font=key, size=size, color=first["color"], bold=bold, italic=italic,
                     wrap=False, lh=lh_ratio, cover=[list(x["band"]) for x in c])
        if bg_img is not None and all(x.get("invisible") for x in c):
            ov.props["bg"] = _median_color([sample_bg(bg_img, x["bbox"], 1.5) for x in c])
            ov.props["bgrects"] = [list(x["bbox"] + (-1, -0.5, 1, 0.5)) for x in c]
            ov.props["color"] = "#000000"  # OCR 文字层本身没有颜色,按黑字处理
        result.append(ov)
    return result


def _median_color(hexes):
    chans = [[int(h[i:i + 2], 16) for h in hexes] for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(_median(c) for c in chans)


def select_words(page, p0, p1, words=None) -> list:
    """像在阅读器里拖选文字一样:从 p0 到 p1(显示坐标)之间的文字,返回每行一个矩形(显示坐标)。

    words:page.get_text("words") 的结果,拖动中反复调用时传入缓存好的结果可以快很多。"""
    words = sorted(page.get_text("words") if words is None else words, key=lambda w: (w[5], w[6], w[7]))
    if not words:
        return []
    rm, dm = page.rotation_matrix, page.derotation_matrix

    def nearest(pt):
        up = fitz.Point(pt) * dm
        best, bi = 1e18, -1
        for i, w in enumerate(words):
            dx = max(w[0] - up.x, 0, up.x - w[2])
            dy = max(w[1] - up.y, 0, up.y - w[3])
            d = dx * dx + dy * dy
            if d < best:
                best, bi = d, i
        return bi, math.sqrt(best)

    (a, da), (b, db) = nearest(p0), nearest(p1)
    if da > 40 and db > 40:
        return []
    i, j = sorted((a, b))
    lines = {}
    for w in words[i:j + 1]:
        key = (w[5], w[6])
        r = lines.get(key)
        lines[key] = fitz.Rect(w[:4]) if r is None else r | fitz.Rect(w[:4])
    return [list((r * rm).normalize()) for r in lines.values()]


def search_text(doc, needle: str) -> list:
    """[(页号, [显示坐标矩形...])...]"""
    found = []
    if not needle:
        return found
    for pno in range(len(doc)):
        page = doc[pno]
        rects = page.search_for(needle)
        if rects:
            rm = page.rotation_matrix
            found.append((pno, [list((fitz.Rect(r) * rm).normalize()) for r in rects]))
    return found


# ----------------------------------------------------------------------------
# 写入 PDF
# ----------------------------------------------------------------------------


def _apply_redactions(page, rects, fill, remove_graphics: bool):
    if not rects:
        return
    dm = page.derotation_matrix
    for r in rects:
        page.add_redact_annot((fitz.Rect(*r) * dm).normalize(), fill=fill, cross_out=False)
    if remove_graphics:
        page.apply_redactions()
    else:
        try:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
        except (TypeError, AttributeError):
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)


def put_layer(page, layer_doc, lpno: int = 0):
    """把覆盖层文档的第 lpno 页(显示坐标)放到页面上。旋转过的页面要反向旋转回来。"""
    rot = page.rotation
    target = fitz.Rect(page.rect)
    if rot:
        target = (target * page.derotation_matrix).normalize()
    page.show_pdf_page(target, layer_doc, lpno, rotate=rot)


def _markup_annots(page, items):
    dm = page.derotation_matrix
    adders = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
              "strike": page.add_strikeout_annot}
    for ov in items:
        if ov.kind != "markup" or not ov.props["rects"]:
            continue
        quads = [(fitz.Rect(*r) * dm).normalize().quad for r in ov.props["rects"]]
        annot = adders[ov.props["sub"]](quads)
        annot.set_colors(stroke=rgb(ov.props["color"]))
        annot.update()


def apply_overlays(doc, overlays):
    """把 Overlay 永久写入 PDF。"""
    by_page = {}
    for ov in overlays:
        by_page.setdefault(ov.page, []).append(ov)
    layers = fitz.open()  # 所有页的覆盖层放在同一个文档里:字体只嵌入一份,并且只需裁剪一次
    placements = []
    for pno, items in sorted(by_page.items()):
        page = doc[pno]
        covers = [r for o in items if o.kind == "text" for r in o.props["cover"]]
        _apply_redactions(page, covers, fill=False, remove_graphics=False)
        _apply_redactions(page, [o.bbox() for o in items if o.kind == "redact"], fill=(0, 0, 0),
                          remove_graphics=True)
        idx = layer_page(layers, items, (page.rect.width, page.rect.height), preview=False)
        if idx is not None:
            placements.append((pno, idx))
        _markup_annots(page, items)
    if placements:
        subset_fonts(layers)
        for pno, idx in placements:
            put_layer(doc[pno], layers, idx)
    layers.close()


def prepared_page(doc, pno: int, covers=(), patches=()):
    """返回 (可直接渲染的页面, 临时文档或 None)。

    covers:被"编辑文字"替换掉的原文区域,会先把原文字真正删除;
    patches:[(rect, 颜色)] 编辑扫描件文字时盖住原字的背景色块。
    没有 covers/patches 时直接返回原页面;否则返回临时文档里的副本页,用完要关闭临时文档。"""
    if not covers and not patches:
        return doc[pno], None
    tmp = fitz.open()
    tmp.insert_pdf(doc, from_page=pno, to_page=pno)
    tp = tmp[0]
    if covers:
        _apply_redactions(tp, covers, fill=False, remove_graphics=False)
    if patches:
        ld = fitz.open()
        lp = ld.new_page(width=tp.rect.width, height=tp.rect.height)
        sh = lp.new_shape()
        for r, col in patches:
            sh.draw_rect(fitz.Rect(*r))
            sh.finish(color=None, fill=rgb(col))
        sh.commit()
        put_layer(tp, ld)
        ld.close()
    return tp, tmp


def render_base(doc, pno: int, scale: float, covers=(), patches=()):
    """渲染页面底图(PIL RGB)。参数含义见 prepared_page。"""
    page, tmp = prepared_page(doc, pno, covers, patches)
    pm = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    if tmp is not None:
        tmp.close()
    return Image.frombytes("RGB", (pm.width, pm.height), pm.samples)


def compose(base: Image.Image, layer):
    if layer is None:
        return base
    out = base.copy()
    out.paste(layer, (0, 0), layer)
    return out


# ----------------------------------------------------------------------------
# 打开 / 保存
# ----------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


class NeedPassword(Exception):
    pass


def open_document(path: str, password: str | None = None):
    """从文件读入内存再打开(不锁定原文件,方便原地保存)。图片文件会被转成单页 PDF。"""
    p = Path(path)
    data = p.read_bytes()
    if p.suffix.lower() in IMAGE_EXTS:
        img = fitz.open(stream=data, filetype=p.suffix.lstrip(".").lower())
        data = img.convert_to_pdf()
        img.close()
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise NeedPassword()
    return doc


def snapshot(doc) -> bytes:
    return doc.tobytes(encryption=fitz.PDF_ENCRYPT_NONE)


def doc_from_bytes(data: bytes):
    return fitz.open(stream=data, filetype="pdf")


def save_document(doc, overlays, path: str, password: str | None = None):
    """保存:在副本上写入覆盖对象,原来的编辑状态不受影响。"""
    work = doc_from_bytes(snapshot(doc))
    try:
        apply_overlays(work, overlays)
        try:
            work.subset_fonts()  # 内嵌的中文字体只保留用到的字,文件从十几 MB 缩到几十 KB
        except Exception:
            pass
        kw = dict(garbage=3, deflate=True)
        if password:
            kw.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password)
        else:
            kw.update(encryption=fitz.PDF_ENCRYPT_NONE)
        tmp = str(path) + ".tmp~"
        work.save(tmp, **kw)
        os.replace(tmp, path)
    finally:
        work.close()


# ----------------------------------------------------------------------------
# 页面操作
# ----------------------------------------------------------------------------


def rotate_pages(doc, idxs, delta: int):
    for i in idxs:
        doc[i].set_rotation((doc[i].rotation + delta) % 360)


def delete_pages(doc, idxs):
    idxs = sorted(set(idxs))
    if len(idxs) >= len(doc):
        raise ValueError("不能删除全部页面")
    doc.delete_pages(idxs)


def move_pages(doc, idxs, dest: int):
    """把 idxs 这些页移到原编号 dest 之前(dest 可等于页数,表示移到末尾)。返回移动后的新页号。"""
    idxs = sorted(set(idxs))
    rest = [i for i in range(len(doc)) if i not in idxs]
    ins = sum(1 for i in rest if i < dest)
    doc.select(rest[:ins] + idxs + rest[ins:])
    return list(range(ins, ins + len(idxs)))


def insert_blank(doc, after: int):
    ref = doc[min(max(after, 0), len(doc) - 1)].rect
    doc.new_page(pno=after + 1, width=ref.width, height=ref.height)


def extract_pages(doc, idxs) -> bytes:
    new = fitz.open()
    for i in idxs:
        new.insert_pdf(doc, from_page=i, to_page=i)
    data = new.tobytes(garbage=3, deflate=True)
    new.close()
    return data


def insert_files(doc, paths, at: int) -> int:
    """在第 at 页之前插入其他 PDF 或图片,返回新增页数。"""
    added = 0
    for path in paths:
        src = open_document(path)
        doc.insert_pdf(src, start_at=at + added)
        added += len(src)
        src.close()
    return added


def parse_ranges(spec: str, n: int) -> list:
    """'1-3,5,8-' -> [[0,1,2],[4],[7,...,n-1]],每个范围一组。"""
    groups = []
    for part in re.split(r"[,，;；\s]+", spec.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d*)\s*[-~—]\s*(\d*)", part)
        if m:
            a = int(m.group(1)) if m.group(1) else 1
            b = int(m.group(2)) if m.group(2) else n
        elif part.isdigit():
            a = b = int(part)
        else:
            raise ValueError(f"无法识别的页码范围:{part}")
        if a < 1 or b > n or a > b:
            raise ValueError(f"页码范围超出 1~{n}:{part}")
        groups.append(list(range(a - 1, b)))
    if not groups:
        raise ValueError("请输入页码范围")
    return groups


def export_images(doc, idxs, outdir: str, stem: str, dpi: int = 150, fmt: str = "png") -> list:
    paths = []
    for i in idxs:
        out = os.path.join(outdir, f"{stem}_{i + 1:03d}.{fmt}")
        doc[i].get_pixmap(dpi=dpi, alpha=False).save(out)
        paths.append(out)
    return paths


def extract_text(doc) -> str:
    return "\n\n".join(f"—— 第 {i + 1} 页 ——\n{page.get_text()}" for i, page in enumerate(doc))


# ----------------------------------------------------------------------------
# 水印 / 页码
# ----------------------------------------------------------------------------


def add_watermark(doc, text: str, size: float = 60, color: str = "#888888", opacity: float = 0.3,
                  angle: float = 45, font: str = "auto", pages=None):
    f = font_for_text(font, False, False, text)
    targets = list(pages if pages is not None else range(len(doc)))
    ld = fitz.open()  # 所有页的水印放在同一个文档里,字体只嵌入一份
    for pno in targets:
        r = doc[pno].rect
        lp = ld.new_page(width=r.width, height=r.height)
        center = fitz.Point(r.width / 2, r.height / 2)
        lh, off = line_metrics(f, size)
        tw = fitz.TextWriter(lp.rect)
        tw.append((center.x - f.text_length(text, size) / 2, center.y - lh / 2 + off), text, font=f, fontsize=size)
        tw.write_text(lp, color=rgb(color), opacity=opacity, morph=(center, fitz.Matrix(angle)))
    subset_fonts(ld)
    for k, pno in enumerate(targets):
        put_layer(doc[pno], ld, k)
    ld.close()


# ----------------------------------------------------------------------------
# 扫描件文字识别(OCR)结果写入
# ----------------------------------------------------------------------------

_FULL2HALF = {"：": ":", "，": ",", "．": ".", "！": "!", "？": "?", "；": ";", "（": "(", "）": ")", "％": "%",
              "＋": "+", "－": "-", "＝": "=", "＿": "_", "／": "/", "＠": "@", "＃": "#", "＆": "&", "＊": "*"}


def fix_punct(text: str) -> str:
    """OCR 的中文引擎会把英文旁边的标点识别成全角(如 "world！"),这里按上下文改回半角。"""
    out = []
    for i, ch in enumerate(text):
        half = _FULL2HALF.get(ch)
        if half:
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            prev_ascii = bool(prev) and prev.isascii() and prev.isalnum()
            nxt_ascii = (not nxt) or nxt.isascii()
            if prev_ascii and nxt_ascii:
                ch = half
                if half in ":,;!?" and nxt.isascii() and nxt.isalpha():
                    ch = half + " "  # 全角标点本身带空隙,改成半角后补一个空格
        out.append(ch)
    return "".join(out)


def ocr_line_geometry(text: str, bbox, font):
    """根据识别出的行框(pt)算字号和首行基线:字号让文字宽度刚好撑满行框。"""
    x0, y0, x1, y1 = bbox
    w, h = x1 - x0, y1 - y0
    nat = font.text_length(text, 1.0)
    if nat <= 0 or w <= 0 or h <= 0:
        return None
    size = max(2.0, min(w / nat, h * 1.2))
    asc, desc = font.ascender, font.descender
    base = y0 + (h - size * (asc - desc)) / 2 + size * asc
    return size, base


def add_text_layer(doc, pages: dict):
    """把 OCR 结果写成不可见文字层:{页号: (行列表[{'text','bbox'(图片像素)}], 图片像素/pt 比例)}。

    写入后这页就能搜索、复制、用"编辑文字"修改了。"""
    ld = fitz.open()
    placed = []
    for pno, (lines, scale) in sorted(pages.items()):
        r = doc[pno].rect
        lp = ld.new_page(width=r.width, height=r.height)
        n = 0
        for ln in lines:
            text = fix_punct(ln["text"]).replace(" ", " ").strip()
            if not text:
                continue
            bbox = [v / scale for v in ln["bbox"]]
            font = font_for_text("yahei", False, False, text)
            geo = ocr_line_geometry(text, bbox, font)
            if geo is None:
                continue
            size, base = geo
            tw = fitz.TextWriter(lp.rect)
            tw.append((bbox[0], base), text, font=font, fontsize=size)
            tw.write_text(lp, render_mode=3)  # 3 = 不可见
            n += 1
        placed.append((pno, lp.number, n))
    subset_fonts(ld)
    total = 0
    for pno, lpno, n in placed:
        if n:
            put_layer(doc[pno], ld, lpno)
            total += n
    ld.close()
    return total


def page_has_text(page, min_chars: int = 8) -> bool:
    return len(page.get_text().strip()) >= min_chars


def page_is_scanned(page) -> bool:
    """没有文字、但有大块图片的页(扫描件/截图)。"""
    if page_has_text(page, 3):
        return False
    try:
        area = page.rect.get_area()
        return any(fitz.Rect(i["bbox"]).get_area() > 0.3 * area for i in page.get_image_info())
    except Exception:
        return False


# ----------------------------------------------------------------------------
# PDF 表单域
# ----------------------------------------------------------------------------

_WIDGET_KIND = {"Text": "text", "CheckBox": "check", "RadioButton": "radio", "ComboBox": "combo", "ListBox": "list"}


def list_widgets(page) -> list:
    """页面上可填写的表单域(显示坐标)。"""
    rm = page.rotation_matrix
    out = []
    for w in page.widgets() or []:
        kind = _WIDGET_KIND.get(w.field_type_string)
        if kind is None or (w.field_flags or 0) & 1:  # 按钮/签名域/只读域不处理
            continue
        value = w.field_value
        on = w.on_state() if kind in ("check", "radio") else None
        checked = kind in ("check", "radio") and value not in (False, None, "", "Off", "No", "0")
        out.append(dict(xref=w.xref, name=w.field_name, kind=kind, rect=list((fitz.Rect(w.rect) * rm).normalize()),
                        value=value, checked=checked, on=on, choices=list(w.choice_values or []),
                        multiline=bool((w.field_flags or 0) & 4096), size=w.text_fontsize or 0))
    return out


def _find_widget(page, xref):
    for w in page.widgets() or []:
        if w.xref == xref:
            return w
    raise ValueError("找不到这个表单域")


def set_widget(doc, pno: int, xref: int, value):
    """写入表单域的值。复选框传 True/False;单选框传 True(同组其它选项会取消);文本/下拉传字符串。"""
    page = doc[pno]
    w = _find_widget(page, xref)
    kind = _WIDGET_KIND.get(w.field_type_string)
    if kind == "check":
        w.field_value = w.on_state() if value else "Off"
        w.update()
    elif kind == "radio":
        for other in page.widgets():
            if other.field_name == w.field_name and other.xref != xref and other.field_type_string == "RadioButton":
                other.field_value = "Off"
                other.update()
        w.field_value = w.on_state()
        w.update()
    else:
        w.field_value = value
        w.update()


def delete_widget(doc, pno: int, xref: int):
    page = doc[pno]
    page.delete_widget(_find_widget(page, xref))


def needs_cjk(text: str) -> bool:
    """表单域的内置字体画不出这些字(中文等),需要改用我们自己的字体。"""
    try:
        text.encode("cp1252")
        return False
    except UnicodeEncodeError:
        return True


def add_page_numbers(doc, fmt: str = "{n} / {total}", position: str = "bottom-center", size: float = 11,
                     margin: float = 28, start: int = 1, color: str = "#333333", font: str = "auto"):
    ovs = []
    total = len(doc)
    for pno in range(total):
        page = doc[pno]
        text = fmt.replace("{n}", str(pno + start)).replace("{total}", str(total + start - 1))
        ov = Overlay("text", pno, (0, 0, 0, 0), text=text, size=size, color=color, font=font)
        x0, y0, x1, y1 = layout_text(ov)["box"]
        w, h = x1 - x0, y1 - y0
        horiz, vert = position.split("-")[1], position.split("-")[0]
        x = {"left": margin, "center": (page.rect.width - w) / 2, "right": page.rect.width - margin - w}[horiz]
        y = margin if vert == "top" else page.rect.height - margin - h
        ov.translate(x - ov.rect[0], y - ov.rect[1])
        ovs.append(ov)
    apply_overlays(doc, ovs)
