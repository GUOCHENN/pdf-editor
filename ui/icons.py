"""自绘的线条图标(24×24 网格,1.75 线宽,圆角端点)。颜色随主题变化。"""
from __future__ import annotations

import weakref
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ui.theme import T

_P = {
    "menu": '<path d="M4 6.5h16M4 12h16M4 17.5h16"/>',
    "open": '<path d="M3.5 7A1.5 1.5 0 0 1 5 5.5h4l2 2h8A1.5 1.5 0 0 1 20.5 9v8.5A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    "save": '<path d="M5.5 4h10.5l3.5 3.5v11a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 4 18.5v-13A1.5 1.5 0 0 1 5.5 4z"/>'
            '<path d="M8 4v4.5h7V4"/><rect x="7.5" y="13" width="9" height="7" rx=".8"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/>',
    "select": '<path d="M5.5 3.8l12.7 6.9-5.9 1.6-2.6 5.9z"/><path d="M12.6 12.6l4.6 4.6"/>',
    "edit_text": '<path d="M4 7V5h11v2"/><path d="M9.5 5v14"/><path d="M7.5 19h4"/><path d="M18 11v9"/><path d="M16.3 11h3.4"/><path d="M16.3 20h3.4"/>',
    "add_text": '<path d="M4 7V5h11v2"/><path d="M9.5 5v14"/><path d="M7.5 19h4"/><path d="M18 13.5v7"/><path d="M14.5 17h7"/>',
    "form": '<rect x="3.5" y="4" width="17" height="16" rx="2.2"/><path d="M7 8.5h10"/><rect x="7" y="11.5" width="10" height="3" rx="1"/><path d="M7 17.2l1.2 1.1 2-2.3"/>',
    "highlight": '<path d="M9 15l-3.5 3.5H10l1-1"/><path d="M15 4.5l4.5 4.5-8 8-4.5-4.5z"/><path d="M4 21h16"/>',
    "underline": '<path d="M7 4v6a5 5 0 0 0 10 0V4"/><path d="M5 20h14"/>',
    "strike": '<path d="M16.3 7.6C15.6 5.7 14 4.6 12 4.6c-2.5 0-4.4 1.4-4.4 3.5 0 1.5.9 2.5 3.1 3.2"/><path d="M4 12.5h16"/>'
              '<path d="M14.4 14.2c1.4.6 2.2 1.5 2.2 2.8 0 2-1.9 3.4-4.5 3.4-2.1 0-3.9-1-4.6-2.9"/>',
    "rect": '<rect x="4" y="5.5" width="16" height="13" rx="2.2"/>',
    "ellipse": '<ellipse cx="12" cy="12" rx="8.5" ry="6.5"/>',
    "line": '<path d="M5 19L19 5"/>',
    "arrow": '<path d="M5 19L19 5"/><path d="M10.5 5H19v8.5"/>',
    "pen": '<path d="M3 16.5c2-3.2 3.8-5.2 5.3-5.2 1.6 0 1.3 3.4 2.9 3.4 1.8 0 2.6-6.7 4.6-6.7 1.7 0 2 4.4 5.2 4.4"/>',
    "image": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><circle cx="9" cy="10" r="1.7"/><path d="M20.5 16.2l-5-5L5 19.4"/>',
    "whiteout": '<path d="M8.5 20h11.5"/><path d="M5.7 15.5l8.4-8.4a2 2 0 0 1 2.8 0l2 2a2 2 0 0 1 0 2.8l-6.4 6.4a2 2 0 0 1-1.4.6H8.6a2 2 0 0 1-1.4-.6l-1.5-1.5a1.2 1.2 0 0 1 0-1.3z"/><path d="M9.6 11.6l4.8 4.8"/>',
    "redact": '<rect x="4" y="9.2" width="16" height="5.6" rx="1.2" fill="{c}"/><path d="M4 5.2h9M4 18.8h12"/>',
    "zoom_in": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M11 8.3v5.4M8.3 11h5.4"/>',
    "zoom_out": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2M8.3 11h5.4"/>',
    "fit_width": '<path d="M4 5.5v13M20 5.5v13"/><path d="M7.5 12h9"/><path d="M10 9.5L7.5 12l2.5 2.5M14 9.5l2.5 2.5-2.5 2.5"/>',
    "fit_page": '<rect x="6" y="3.5" width="12" height="17" rx="1.8"/><path d="M9 8h6M9 11.5h6M9 15h3.5"/>',
    "rotate_left": '<path d="M4.5 4.5V9H9"/><path d="M5 13.5A7.5 7.5 0 1 0 7.2 7.2L4.5 9"/>',
    "rotate_right": '<path d="M19.5 4.5V9H15"/><path d="M19 13.5A7.5 7.5 0 1 1 16.8 7.2L19.5 9"/>',
    "trash": '<path d="M4.5 7h15"/><path d="M10 11v6M14 11v6"/><path d="M6.5 7l.8 11.6A1.6 1.6 0 0 0 8.9 20h6.2a1.6 1.6 0 0 0 1.6-1.4L17.5 7"/><path d="M9.5 7V4.6h5V7"/>',
    "page_add": '<path d="M14 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3.5V8h4.5"/><path d="M12 11v6M9 14h6"/>',
    "doc": '<path d="M14 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h10a1.5 1.5 0 0 0 1.5-1.5V8z"/><path d="M14 3.5V8h4.5"/><path d="M9 12.5h6M9 16h4"/>',
    "extract": '<path d="M13 3.5H7A1.5 1.5 0 0 0 5.5 5v14A1.5 1.5 0 0 0 7 20.5h5"/><path d="M13 3.5V8h4.5V11"/><path d="M14.5 17h6M18 14.5l2.5 2.5-2.5 2.5"/>',
    "merge": '<rect x="3.5" y="6" width="10" height="13" rx="1.5"/><path d="M10 6V4.5A1 1 0 0 1 11 3.5h8.5a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-6"/>',
    "split": '<circle cx="6" cy="6.5" r="2.5"/><circle cx="6" cy="17.5" r="2.5"/><path d="M8.1 7.9L20 17M8.1 16.1L20 7"/>',
    "ocr": '<path d="M4 8V5.5A1.5 1.5 0 0 1 5.5 4H8M16 4h2.5A1.5 1.5 0 0 1 20 5.5V8M20 16v2.5a1.5 1.5 0 0 1-1.5 1.5H16M8 20H5.5A1.5 1.5 0 0 1 4 18.5V16"/>'
           '<path d="M8 9h8M8 12h8M8 15h5"/>',
    "convert": '<path d="M4 8.5h13.5"/><path d="M14 5l3.5 3.5L14 12"/><path d="M20 15.5H6.5"/><path d="M10 12l-3.5 3.5L10 19"/>',
    "watermark": '<path d="M12 3.6s6 6.3 6 10.4a6 6 0 0 1-12 0c0-4.1 6-10.4 6-10.4z"/><path d="M9.2 14.5a2.9 2.9 0 0 0 2.6 2.7"/>',
    "numbers": '<path d="M9.5 4L7.5 20M16.5 4l-2 16M5 9h14.5M4 15h14.5"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2.2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><path d="M12 14.5v2"/>',
    "export": '<path d="M12 4v11"/><path d="M7.5 10.5L12 15l4.5-4.5"/><path d="M5 19.5h14"/>',
    "help": '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.6a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2-2.4 3.5"/><path d="M12 17.1v.1"/>',
    "chevron_left": '<path d="M14.5 6l-6 6 6 6"/>',
    "chevron_right": '<path d="M9.5 6l6 6-6 6"/>',
    "chevron_down": '<path d="M6 9.5l6 6 6-6"/>',
    "sidebar": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="M9.5 4.5v15"/>',
    "panel_right": '<rect x="3.5" y="4.5" width="17" height="15" rx="2.2"/><path d="M14.5 4.5v15"/>',
    "moon": '<path d="M19.5 14.6A7.8 7.8 0 1 1 9.4 4.5a6.2 6.2 0 0 0 10.1 10.1z"/>',
    "sun": '<circle cx="12" cy="12" r="3.8"/><path d="M12 2.8v2M12 19.2v2M2.8 12h2M19.2 12h2M5.5 5.5l1.4 1.4M17.1 17.1l1.4 1.4M5.5 18.5l1.4-1.4M17.1 6.9l1.4-1.4"/>',
    "more": '<circle cx="5.5" cy="12" r="1.3" fill="{c}"/><circle cx="12" cy="12" r="1.3" fill="{c}"/><circle cx="18.5" cy="12" r="1.3" fill="{c}"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a1.5 1.5 0 0 0-1.5-1.5H6A1.5 1.5 0 0 0 4.5 6v8A1.5 1.5 0 0 0 6 15.5h2.5"/>',
    "paste": '<rect x="5.5" y="5" width="13" height="16" rx="2"/><path d="M9 5V3.8h6V5"/><path d="M9 11h6M9 15h4"/>',
    "front": '<rect x="8.5" y="8.5" width="11" height="11" rx="1.8" fill="{c}" fill-opacity=".18"/><path d="M15.5 8.5V5.5a1 1 0 0 0-1-1h-9a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h3"/>',
    "back": '<rect x="4.5" y="4.5" width="11" height="11" rx="1.8" fill="{c}" fill-opacity=".18"/><path d="M8.5 15.5v3a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-9a1 1 0 0 0-1-1h-3"/>',
    "bold": '<path d="M7 5h6a3.5 3.5 0 0 1 0 7H7z"/><path d="M7 12h7a3.5 3.5 0 0 1 0 7H7z"/>',
    "italic": '<path d="M10 5h8M6 19h8M14.5 5l-5 14"/>',
    "palette": '<path d="M12 3.5a8.5 8.5 0 0 0 0 17c1.4 0 1.9-1 1.4-2-.6-1.2.2-2.5 1.6-2.5H17a3.5 3.5 0 0 0 3.5-3.5c0-5-3.8-9-8.5-9z"/>'
               '<circle cx="7.8" cy="11" r="1" fill="{c}"/><circle cx="10.5" cy="7.5" r="1" fill="{c}"/><circle cx="15" cy="8" r="1" fill="{c}"/>',
    "page": '<rect x="5.5" y="3.5" width="13" height="17" rx="1.8"/>',
    "drop": '<path d="M12 15V4"/><path d="M7.5 8.5L12 4l4.5 4.5"/><path d="M4.5 14v4a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2v-4"/>',
    "sparkle": '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1L5 10.5l5.1-1.9z"/><path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5M12 7.8v.1"/>',
    "keyboard": '<rect x="2.5" y="6" width="19" height="12" rx="2"/><path d="M6 9.5h.01M9.3 9.5h.01M12.6 9.5h.01M15.9 9.5h.01M18 9.5h.01M7.5 14.5h9"/>',
}


def _svg(name: str, color: str) -> bytes:
    body = _P[name].replace("{c}", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str, size: int = 20, dpr: float = 2.0) -> QPixmap:
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(_svg(name, color))).render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def icon(name: str, color: str | None = None, checked_color: str | None = None, size: int = 20) -> QIcon:
    """主题色图标。checked_color:按钮选中时的颜色(默认主色)。"""
    ic = QIcon()
    normal = color or T.c("text2")
    ic.addPixmap(pixmap(name, normal, size), QIcon.Normal, QIcon.Off)
    ic.addPixmap(pixmap(name, checked_color or T.c("primary"), size), QIcon.Normal, QIcon.On)
    ic.addPixmap(pixmap(name, T.c("text"), size), QIcon.Active, QIcon.Off)
    ic.addPixmap(pixmap(name, checked_color or T.c("primary"), size), QIcon.Active, QIcon.On)
    ic.addPixmap(pixmap(name, T.c("text3"), size), QIcon.Disabled, QIcon.Off)
    return ic


def names():
    return list(_P)


def png_file(name: str, color: str, size: int = 16) -> str:
    """把图标存成 PNG 文件,返回路径(样式表里的 image: url(...) 只能引用文件)。"""
    import os
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "pdfeditor_icons")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}_{color.lstrip('#')}_{size}.png")
    if not os.path.exists(path):
        pixmap(name, color, size, 2.0).save(path, "PNG")
    return path.replace("\\", "/")


# 切换浅色/深色主题时,所有按钮的图标要换颜色:用 bind() 设置的图标会在 refresh_all() 时重新生成
_bound = weakref.WeakKeyDictionary()


def bind(button, name: str, color_key: str | None = None, checked_key: str | None = None, size: int = 20):
    _bound[button] = (name, color_key, checked_key, size)
    _apply(button, name, color_key, checked_key, size)


def _apply(button, name, color_key, checked_key, size):
    button.setIcon(icon(name, T.c(color_key) if color_key else None, T.c(checked_key) if checked_key else None, size))


def refresh_all():
    for btn, args in list(_bound.items()):
        try:
            _apply(btn, *args)
        except RuntimeError:  # 控件已经销毁
            pass
