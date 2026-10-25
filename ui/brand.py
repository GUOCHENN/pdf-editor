"""程序图标:靛蓝渐变圆角方块 + 白色文档 + 斜放的黄色铅笔("可以编辑的 PDF")。

图标文件(app.ico)、窗口左上角和欢迎页的标志都用这里画,保证一致。按尺寸自动简化:
16/24 像素只画方块、纸和一笔黄色;32 像素加两行字;64 像素以上才画完整的铅笔和"PDF"标签。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap

INDIGO_LIGHT = "#7C83FF"
INDIGO = "#5B5BF0"
INDIGO_DARK = "#3F37C9"


def _page_path(x0, y0, x1, y1, r, fold) -> QPainterPath:
    p = QPainterPath()
    p.moveTo(x0 + r, y0)
    p.lineTo(x1 - fold, y0)
    p.lineTo(x1, y0 + fold)
    p.lineTo(x1, y1 - r)
    p.quadTo(x1, y1, x1 - r, y1)
    p.lineTo(x0 + r, y1)
    p.quadTo(x0, y1, x0, y1 - r)
    p.lineTo(x0, y0 + r)
    p.quadTo(x0, y0, x0 + r, y0)
    p.closeSubpath()
    return p


def paint_icon(p: QPainter, size: float):
    """在 (0,0,size,size) 里画图标。坐标按 256 的网格设计,再整体缩放。"""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    k = size / 256.0
    p.scale(k, k)
    tiny = size < 28        # 16、24
    small = size < 56       # 32、48

    # ---- 底板:圆角方块 + 斜向渐变 + 顶部一点高光
    m = 10 if tiny else 14
    tile = QRectF(m, m, 256 - 2 * m, 256 - 2 * m)
    rad = 58 if tiny else 54
    if not small:  # 轻微的投影
        for i, a in ((6, 18), (3, 26)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(30, 27, 75, a))
            p.drawRoundedRect(tile.translated(0, i), rad, rad)
    g = QLinearGradient(tile.topLeft(), tile.bottomRight())
    g.setColorAt(0.0, QColor(INDIGO_LIGHT))
    g.setColorAt(0.55, QColor(INDIGO))
    g.setColorAt(1.0, QColor(INDIGO_DARK))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(g))
    p.drawRoundedRect(tile, rad, rad)
    if not tiny:
        hl = QLinearGradient(QPointF(0, tile.top()), QPointF(0, tile.center().y()))
        hl.setColorAt(0.0, QColor(255, 255, 255, 46))
        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(hl))
        p.drawRoundedRect(tile, rad, rad)

    # ---- 白纸
    if tiny:
        x0, y0, x1, y1, r, fold = 66, 46, 178, 208, 12, 40
    else:
        x0, y0, x1, y1, r, fold = 64, 42, 178, 206, 12, 38
    if not small:  # 纸的影子
        p.setBrush(QColor(20, 16, 80, 60))
        p.drawPath(_page_path(x0 + 3, y0 + 6, x1 + 3, y1 + 6, r, fold))
    p.setBrush(QColor("#FFFFFF"))
    p.drawPath(_page_path(x0, y0, x1, y1, r, fold))
    # 折角
    corner = QPainterPath()
    corner.moveTo(x1 - fold, y0)
    corner.lineTo(x1 - fold, y0 + fold - 8)
    corner.quadTo(x1 - fold, y0 + fold, x1 - fold + 8, y0 + fold)
    corner.lineTo(x1, y0 + fold)
    corner.closeSubpath()
    p.setBrush(QColor("#C9CCFF"))
    p.drawPath(corner)

    # ---- 文字行
    line_col = QColor("#B4B9FF")
    if tiny:
        pass
    elif small:
        for i, w in enumerate((76, 56)):
            p.setBrush(line_col)
            p.drawRoundedRect(QRectF(x0 + 20, y0 + 52 + i * 26, w, 13), 6.5, 6.5)
    else:
        for i, w in enumerate((72, 58, 66)):
            p.setBrush(line_col)
            p.drawRoundedRect(QRectF(x0 + 20, y0 + 50 + i * 22, w, 10), 5, 5)
    if size >= 96:  # "PDF" 标签:太小时看不清,就不画了
        pill = QRectF(x0 + 18, y1 - 50, 62, 30)
        p.setBrush(QColor(INDIGO))
        p.drawRoundedRect(pill, 9, 9)
        f = QFont("Segoe UI", 1)
        f.setPixelSize(19)
        f.setWeight(QFont.Black)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.5)
        p.setFont(f)
        p.setPen(QColor("#FFFFFF"))
        p.drawText(pill, Qt.AlignCenter, "PDF")
        p.setPen(Qt.NoPen)

    # ---- 铅笔(斜着压在纸的右下角)
    p.save()
    if tiny:
        p.translate(196, 196)
        p.rotate(-135)
        p.setBrush(QColor("#FBBF24"))
        p.drawRoundedRect(QRectF(-4, -20, 120, 40), 12, 12)
        p.restore()
        p.restore()
        return
    p.translate(204, 204)   # 笔尾的位置
    p.rotate(-135)          # 笔尖朝左上
    w = 34 if not small else 40
    L = 132 if not small else 120
    if not small:  # 铅笔的影子
        p.setBrush(QColor(20, 16, 80, 70))
        p.drawRoundedRect(QRectF(-4, -w / 2 + 7, L, w), 6, 6)
    # 橡皮 + 金属箍
    p.setBrush(QColor("#F9A8D4"))
    p.drawRoundedRect(QRectF(-6, -w / 2, 22, w), 7, 7)
    p.setBrush(QColor("#CBD5E1"))
    p.drawRect(QRectF(12, -w / 2, 14, w))
    if not small:
        p.setBrush(QColor("#94A3B8"))
        p.drawRect(QRectF(17, -w / 2, 3, w))
    # 笔杆:黄色,上下两侧深浅不同,显得有立体感
    body = QRectF(26, -w / 2, L - 26 - 30, w)
    p.setBrush(QColor("#FBBF24"))
    p.drawRect(body)
    if not small:
        p.setBrush(QColor("#FCD34D"))
        p.drawRect(QRectF(body.left(), -w / 2, body.width(), w / 3))
        p.setBrush(QColor("#F59E0B"))
        p.drawRect(QRectF(body.left(), w / 6, body.width(), w / 3))
    # 削开的木头 + 笔芯
    tip = QPainterPath()
    tx = body.right()
    tip.moveTo(tx, -w / 2)
    tip.lineTo(tx + 30, 0)
    tip.lineTo(tx, w / 2)
    tip.closeSubpath()
    p.setBrush(QColor("#FDE7B0"))
    p.drawPath(tip)
    lead = QPainterPath()
    lead.moveTo(tx + 19, -w / 2 * 11 / 30)
    lead.lineTo(tx + 30, 0)
    lead.lineTo(tx + 19, w / 2 * 11 / 30)
    lead.closeSubpath()
    p.setBrush(QColor("#1F2937"))
    p.drawPath(lead)
    p.restore()
    p.restore()


def icon_image(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    paint_icon(p, size)
    p.end()
    return img


def icon_pixmap(size: int, dpr: float = 2.0) -> QPixmap:
    px = int(round(size * dpr))
    img = icon_image(px)
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm
