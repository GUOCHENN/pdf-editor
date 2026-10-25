"""生成程序图标 app.ico:python make_icon.py

图标的画法在 ui/brand.py(程序里的标志也用它)。每个尺寸单独绘制,小尺寸会自动简化,不是把大图缩小。"""
import io
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "app.ico"
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(OUT.parent))
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QFontDatabase, QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    fdir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for f in ("seguibl.ttf", "segoeuib.ttf", "segoeui.ttf"):  # 离屏模式不会自动找字体("PDF"字样要用)
        if (fdir / f).exists():
            QFontDatabase.addApplicationFont(str(fdir / f))
    from ui.brand import icon_image

    imgs = []
    for s in SIZES:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        icon_image(s).save(buf, "PNG")
        imgs.append(Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA"))
    big = imgs[-1]
    big.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=imgs[:-1])
    big.save(OUT.with_suffix(".png"))
    del app
    print("已生成", OUT)


if __name__ == "__main__":
    main()
