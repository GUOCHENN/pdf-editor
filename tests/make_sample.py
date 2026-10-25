"""生成一份用来试用/测试的示例 PDF:python tests/make_sample.py [输出路径]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine  # noqa: E402

fitz = engine.fitz


def make_sample(path: str):
    doc = fitz.open()
    yahei = engine.font_for_text("yahei", False, False, "中")
    yahei_b = engine.font_for_text("yahei", True, False, "中")

    page = doc.new_page(width=595, height=842)
    tw = fitz.TextWriter(page.rect)
    tw.append((72, 90), "PDF 编辑器 示例文档", font=yahei_b, fontsize=26)
    tw.write_text(page, color=(0.1, 0.2, 0.5))
    tw = fitz.TextWriter(page.rect)
    para = ["这是一段中文正文,用来测试编辑文字功能。", "点击“编辑文字”工具,再点这一行,就能直接修改它。",
            "拖拽框选还可以一次编辑多行,行距保持不变。"]
    for i, t in enumerate(para):
        tw.append((72, 140 + i * 22), t, font=yahei, fontsize=13)
    tw.write_text(page, color=(0.1, 0.1, 0.1))
    y = 260
    for i in range(4):  # 行距很紧的英文,用来验证删除一行不会误伤相邻行
        page.insert_text((72, y), f"Tight line {i}: The quick brown fox jumps over the lazy dog", fontsize=12,
                         fontname="helv")
        y += 13
    page.insert_text((72, 360), "Times Roman paragraph: Hello, PDF world!", fontsize=14, fontname="tiro",
                     color=(0.6, 0.1, 0.1))
    # 表格:同一行里有多列,验证并排的栏不会被合并
    for r in range(3):
        for c in range(3):
            page.insert_text((72 + c * 150, 430 + r * 24), f"Cell {r}-{c}", fontsize=12, fontname="helv")
    page.draw_rect(fitz.Rect(72, 415, 522, 500), color=(0.5, 0.5, 0.5), width=0.8)

    page2 = doc.new_page(width=595, height=842)
    tw = fitz.TextWriter(page2.rect)
    tw.append((72, 90), "第二页", font=yahei_b, fontsize=22)
    tw.write_text(page2, color=(0, 0, 0))
    page2.insert_text((72, 140), "Second page body text for highlighting and searching.", fontsize=13,
                      fontname="helv")

    page3 = doc.new_page(width=842, height=595)  # 横向页
    page3.insert_text((72, 100), "Landscape page", fontsize=20, fontname="helv")
    doc.subset_fonts()  # 字体只保留用到的字,文件从 20MB+ 降到几十 KB
    doc.save(path, garbage=3, deflate=True)
    doc.close()


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parent / "sample.pdf")
    make_sample(out)
    print("已生成", out)
