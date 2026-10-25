"""引擎自检:python tests/test_engine.py [图片输出目录]  (不依赖 pytest)"""
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine as E  # noqa: E402
from make_sample import make_sample  # noqa: E402
from PIL import Image, ImageChops, ImageStat  # noqa: E402

fitz = E.fitz
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="pdfed_test_"))
OUT.mkdir(parents=True, exist_ok=True)
SAMPLE = str(OUT / "sample.pdf")
make_sample(SAMPLE)

failures = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def text_of(doc, p=0):
    return doc[p].get_text().replace(" ", " ")  # 嵌入字体写出的空格读回为 NBSP


print("== 页面文字行 / 分栏")
doc = E.open_document(SAMPLE)
lines = E.page_lines(doc[0])
texts = [l["text"] for l in lines]
check(any("示例文档" in t for t in texts), "找到中文标题行")
tight = [l for l in lines if l["text"].startswith("Tight line")]
check(len(tight) == 4, f"找到 4 行紧密英文行 (实际 {len(tight)})")
cells = [l for l in lines if l["text"].startswith("Cell")]
ovs = E.overlays_from_lines(0, cells)
check(len(ovs) == 3, f"3x3 表格被分成 3 栏 (实际 {len(ovs)})")
check(all(o.props["text"].count("\n") == 2 for o in ovs), "每栏合并成 3 行")

print("== 根据原字体名猜替换字体")
for name, flags, want in (("NimbusSans-Regular", 4, "arial"), ("NimbusRoman-Regular", 0, "times"),
                          ("ABCDEF+TimesNewRomanPS-BoldMT", 20, "times"), ("DroidSansFallback", 0, "arial"),
                          ("Consolas", 8, "courier"), ("ABCDEF+MicrosoftYaHei", 0, "yahei"),
                          ("SimSun", 4, "simsun"), ("Weird-Unknown", 0, "arial"), ("Weird-Unknown", 4, "times")):
    got, _b, _i = E.guess_font_style(name, flags)
    check(got == want, f"{name} flags={flags} -> {got} (期望 {want})")
check(E.guess_font_style("Arial-BoldItalicMT", 0)[1:] == (True, True), "识别粗体 + 斜体")

print("== 编辑单行:只删这一行,不误伤上下行")
doc = E.open_document(SAMPLE)
lines = E.page_lines(doc[0])
ln = [l for l in lines if l["text"].startswith("Tight line 1")][0]
ov = E.overlays_from_lines(0, [ln])[0]
ov.props["text"] = "CHANGED LINE ONE"
E.apply_overlays(doc, [ov])
t = text_of(doc)
check("Tight line 0" in t and "Tight line 2" in t and "Tight line 3" in t, "相邻行完好")
check("Tight line 1" not in t, "原文字已被真正删除")
check("CHANGED LINE ONE" in t, "新文字已写入")

print("== 编辑中文行 + 字体子集化")
doc = E.open_document(SAMPLE)
lines = E.page_lines(doc[0])
ln = [l for l in lines if "点击" in l["text"]][0]
ov = E.overlays_from_lines(0, [ln])[0]
ov.props["text"] = "已经改过的中文内容 ABC 123"
save_to = str(OUT / "edited_cjk.pdf")
E.save_document(doc, [ov], save_to)
size = os.path.getsize(save_to)
check(size < 300_000, f"保存后文件很小 ({size} 字节),中文字体已子集化")
re_doc = E.open_document(save_to)
check("已经改过的中文内容 ABC 123" in text_of(re_doc), "重新打开后文字可读取")
re_doc[0].get_pixmap(dpi=110).save(str(OUT / "edited_cjk.png"))

print("== 所见即所得:预览图 与 保存后渲染图 对比(含旋转页)")
png = io.BytesIO()
Image.new("RGBA", (40, 24), (30, 120, 220, 255)).save(png, "PNG")
for rot in (0, 90, 180, 270):
    doc = E.open_document(SAMPLE)
    doc[0].set_rotation(rot)
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    items = [
        E.Overlay("text", 0, (60, 60, 300, 60), text="Hello 你好\nSecond line", size=20, color="#c62828"),
        E.Overlay("rect", 0, (60, 150, 200, 230), fill="#ffcc00", opacity=0.5),
        E.Overlay("ellipse", 0, (220, 150, 320, 230), stroke="#2e7d32", width=3),
        E.Overlay("arrow", 0, (60, 300, 260, 260)),
        E.Overlay("line", 0, (60, 320, 260, 330), stroke="#1565c0"),
        E.Overlay("ink", 0, (0, 0, 0, 0), points=[(300, 300), (330, 330), (360, 300), (390, 340)]),
        E.Overlay("image", 0, (60, 360, 160, 420), data=png.getvalue()),
    ]
    scale = 1.0
    base = E.render_base(doc, 0, scale)
    layer = E.render_layer((W, H), scale, items)
    preview = E.compose(base, layer)
    work = E.doc_from_bytes(E.snapshot(doc))
    E.apply_overlays(work, items)
    pm = work[0].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    final = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
    check(preview.size == final.size, f"旋转 {rot}°:预览与成品尺寸一致 {preview.size}")
    diff = ImageChops.difference(preview, final).convert("L")
    mean = ImageStat.Stat(diff).mean[0]
    check(mean < 1.2, f"旋转 {rot}°:预览≈成品 (平均差 {mean:.3f})")
    preview.save(str(OUT / f"wysiwyg_{rot}_preview.png"))
    final.save(str(OUT / f"wysiwyg_{rot}_final.png"))

print("== 半透明颜色不发暗(预乘 alpha 处理)")
lay = E.render_layer((100, 100), 1.0, [E.Overlay("rect", 0, (10, 10, 60, 60), stroke=None, fill="#ffff00", opacity=0.4)])
white = Image.new("RGB", lay.size, (255, 255, 255))
px = E.compose(white, lay).getpixel((30, 30))
check(px[0] > 245 and px[1] > 245 and 140 < px[2] < 170, f"黄色 40% 叠在白底上 = {px} (期望约 255,255,153)")

print("== 标记(高亮/下划线)、涂黑")
doc = E.open_document(SAMPLE)
doc[0].set_rotation(90)
lines = E.page_lines(doc[0])
check(len(lines) == 0, "旋转 90° 后原本水平的文字变成竖排,不提供编辑 (实际 %d 行)" % len(lines))
doc = E.open_document(SAMPLE)
page = doc[1]
rects = E.select_words(page, (72, 130), (300, 140))
check(len(rects) >= 1, f"拖选到文字 ({len(rects)} 行)")
mk = E.Overlay("markup", 1, (0, 0, 0, 0), sub="highlight", rects=rects)
mk2 = E.Overlay("markup", 1, (0, 0, 0, 0), sub="underline", color="#ff0000", rects=rects)
rd = E.Overlay("redact", 0, (70, 128, 530, 150))
E.apply_overlays(doc, [mk, mk2, rd])
check(len(list(doc[1].annots())) == 2, "写入了 2 个真正的批注")
check("正文" not in text_of(doc, 0) and "测试编辑文字" not in text_of(doc, 0), "涂黑区域的文字被永久删除")
doc[1].get_pixmap(dpi=100).save(str(OUT / "markup.png"))

print("== 保存 / 加密 / 重新打开")
doc = E.open_document(SAMPLE)
enc = str(OUT / "enc.pdf")
E.save_document(doc, [], enc, password="s3cret")
try:
    E.open_document(enc)
    check(False, "加密文件无密码应打不开")
except E.NeedPassword:
    check(True, "加密文件无密码打不开")
d2 = E.open_document(enc, "s3cret")
check(len(d2) == 3, "密码正确可以打开")
plain = str(OUT / "plain.pdf")
E.save_document(d2, [], plain)
check(len(E.open_document(plain)) == 3, "去掉密码后另存可直接打开")

print("== 页面操作")
doc = E.open_document(SAMPLE)
E.rotate_pages(doc, [0], 90)
check(doc[0].rotation == 90, "旋转")
E.insert_blank(doc, 0)
check(len(doc) == 4 and doc[1].rect.width == doc[0].rect.width, "插入空白页并沿用尺寸")
new_idx = E.move_pages(doc, [3], 0)
check(new_idx == [0] and abs(doc[0].rect.width - 842) < 1, "移动页面到最前")
E.delete_pages(doc, [1, 2])
check(len(doc) == 2, "删除页面")
try:
    E.delete_pages(doc, [0, 1])
    check(False, "不能删光所有页")
except ValueError:
    check(True, "不能删光所有页")
data = E.extract_pages(doc, [1])
check(len(E.doc_from_bytes(data)) == 1, "提取页面")
check(E.parse_ranges("1-2,5", 9) == [[0, 1], [4]] and E.parse_ranges("8-", 9) == [[7, 8]], "页码范围解析")
d_merge = E.open_document(SAMPLE)
added = E.insert_files(d_merge, [SAMPLE], 1)
check(added == 3 and len(d_merge) == 6, "合并 PDF")
img_path = str(OUT / "pic.png")
Image.new("RGB", (200, 100), (200, 50, 50)).save(img_path)
E.insert_files(d_merge, [img_path], 6)
check(len(d_merge) == 7, "图片作为新页面插入")

print("== 水印 / 页码")
doc = E.open_document(SAMPLE)
E.add_watermark(doc, "机密 CONFIDENTIAL", size=50, angle=40)
E.add_page_numbers(doc, "第 {n} 页 / 共 {total} 页")
check("第 1 页 / 共 3 页" in text_of(doc, 0) and "第 3 页 / 共 3 页" in text_of(doc, 2), "页码文字正确")
check("机密 CONFIDENTIAL" in text_of(doc, 1), "水印文字写入")
doc[0].get_pixmap(dpi=70).save(str(OUT / "watermark.png"))

print("== 搜索")
doc = E.open_document(SAMPLE)
res = E.search_text(doc, "Second page")
check(res and res[0][0] == 1, "搜索到第二页")

print("== 多页叠加层:字体只嵌一份并裁剪")
doc = E.open_document(SAMPLE)
ovs = [E.Overlay("text", i, (60, 300, 60, 300), text=f"第 {i + 1} 页的新文字 abc", size=16) for i in range(3)]
big = str(OUT / "multi.pdf")
E.save_document(doc, ovs, big)
check(os.path.getsize(big) < 400_000, f"3 页都加中文文字后文件仍很小 ({os.path.getsize(big)} 字节)")
chk = E.open_document(big)
check(all(f"第 {i + 1} 页的新文字" in text_of(chk, i) for i in range(3)), "每页的文字都写入")

print("== PDF 表单域")
fd = fitz.open()
fp = fd.new_page()
for name, typ, rect in (("name", fitz.PDF_WIDGET_TYPE_TEXT, (120, 65, 320, 90)),
                        ("agree", fitz.PDF_WIDGET_TYPE_CHECKBOX, (120, 110, 135, 125)),
                        ("city", fitz.PDF_WIDGET_TYPE_COMBOBOX, (120, 140, 260, 165))):
    w = fitz.Widget()
    w.field_name, w.field_type, w.rect = name, typ, fitz.Rect(rect)
    if name == "city":
        w.choice_values = ["北京", "上海", "Madrid"]
    fp.add_widget(w)
def add_radio_group(doc, page, name, rects):
    """PyMuPDF 不能直接创建单选框,这里先建复选框,再手工改成同一个单选框组(只用于造测试数据)。"""
    for i, r in enumerate(rects):
        w = fitz.Widget()
        w.field_name, w.field_type, w.rect = f"_tmp{i}", fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.Rect(r)
        page.add_widget(w)
    xrefs = [w.xref for w in page.widgets() if w.field_name.startswith("_tmp")]
    parent = doc.get_new_xref()
    doc.update_object(parent, "<< /FT /Btn /Ff 49152 /T (%s) /Kids [%s] >>" % (name, " ".join(f"{x} 0 R" for x in xrefs)))
    for x in xrefs:
        doc.xref_set_key(x, "Parent", f"{parent} 0 R")
        for key in ("T", "FT", "Ff"):
            doc.xref_set_key(x, key, "null")


add_radio_group(fd, fp, "sex", ((120, 190, 135, 205), (200, 190, 215, 205)))
form = E.doc_from_bytes(fd.tobytes())
ws = E.list_widgets(form[0])
kinds = {w["name"] + ("%d" % i if w["name"] == "sex" else ""): w for i, w in enumerate(ws)}
check({w["kind"] for w in ws} == {"text", "check", "combo", "radio"} and len(ws) == 5, f"识别 5 个表单域 ({[w['kind'] for w in ws]})")
by = {w["name"]: w for w in ws}
E.set_widget(form, 0, by["name"]["xref"], "Zhang San")
E.set_widget(form, 0, by["agree"]["xref"], True)
E.set_widget(form, 0, by["city"]["xref"], "上海")
radios = [w for w in ws if w["kind"] == "radio"]
E.set_widget(form, 0, radios[1]["xref"], True)
ws2 = {w["xref"]: w for w in E.list_widgets(form[0])}
check(ws2[by["name"]["xref"]]["value"] == "Zhang San", "文本域写入")
check(ws2[by["agree"]["xref"]]["checked"] is True, "复选框勾选")
check(ws2[by["city"]["xref"]]["value"] == "上海", "下拉框选择")
check(ws2[radios[1]["xref"]]["checked"] and not ws2[radios[0]["xref"]]["checked"], "单选框:选中一个,同组其它取消")
check(E.needs_cjk("张三") and not E.needs_cjk("Zhang San é"), "判断哪些文本需要用自带字体渲染")
E.delete_widget(form, 0, by["name"]["xref"])
check(len(E.list_widgets(form[0])) == 4, "删除表单域")
rt = E.doc_from_bytes(E.snapshot(form))
check(len(E.list_widgets(rt[0])) == 4, "保存再读取表单域仍然正确")

print("== 扫描件 OCR → 可搜索、可编辑")
import ocr  # noqa: E402

if not ocr.available() or not ocr.list_languages():
    print("  skip 本机没有可用的 Windows OCR 引擎")
else:
    src = E.open_document(SAMPLE)
    pm = src[0].get_pixmap(dpi=150)
    scan = fitz.open()
    sp = scan.new_page(width=src[0].rect.width, height=src[0].rect.height)
    sp.insert_image(sp.rect, pixmap=pm)
    scan_path = str(OUT / "scan.pdf")
    scan.save(scan_path)
    sd = E.open_document(scan_path)
    check(E.page_is_scanned(sd[0]) and not E.page_has_text(sd[0]), "识别出这是扫描件(没有文字、有整页图片)")
    res = ocr.ocr_pdf_pages(sd, [0], dpi=200)
    lines, scale = res[0]
    check(len(lines) >= 10, f"OCR 识别出 {len(lines)} 行")
    img_before = E.render_base(sd, 0, 1.0)
    n = E.add_text_layer(sd, res)
    check(n >= 10 and E.page_has_text(sd[0]), f"写入 {n} 行不可见文字层")
    txt = text_of(sd)
    check("Tight line" in txt and "编辑" in txt, "文字层可以被搜索/提取")
    check(E.fix_punct("Hello, PDF world！") == "Hello, PDF world!" and E.fix_punct("Line 0：The") == "Line 0: The"
          and E.fix_punct("你好，world") == "你好，world" and E.fix_punct("10：30") == "10:30",
          "全角标点按上下文改回半角")
    check("：" not in "".join(l["text"] for l in lines if l["text"].startswith("Tight")) or "Tight line 0:" in txt,
          "写入文字层的行里英文旁边的标点是半角")
    check(len(sd[0].get_text("words")) > 20, "有可选择的单词")
    diff = ImageChops.difference(img_before, E.render_base(sd, 0, 1.0)).convert("L")
    check(diff.getextrema()[1] == 0, f"不可见文字层完全不改变页面外观 (最大像素差 {diff.getextrema()[1]})")
    # 编辑 OCR 出来的一行
    ls = E.page_lines(sd[0])
    target = [l for l in ls if "Tight line 0" in l["text"]]
    check(target and target[0]["invisible"], "OCR 行被标记为不可见文字")
    ov = E.overlays_from_lines(0, target[:1], sd[0])[0]
    check(ov.props["bg"] is not None and ov.props["bgrects"], f"扫描件文字取了背景色 {ov.props['bg']}")
    ov.props["text"] = "REPLACED SCAN LINE"
    out_scan = str(OUT / "scan_edit.pdf")
    E.save_document(sd, [ov], out_scan)
    after = E.open_document(out_scan)
    rimg = after[0].get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
    big_img = Image.frombytes("RGB", (rimg.width, rimg.height), rimg.samples)
    bb = target[0]["bbox"]
    k = 150 / 72
    old_region = big_img.crop((int(bb.x0 * k + bb.width * k * 0.7), int(bb.y0 * k), int(bb.x1 * k), int(bb.y1 * k)))
    dark = sum(1 for p in old_region.getdata() if sum(p) < 200)
    check(dark < 10, f"原来扫描图里那行字被盖住了 (行尾区域暗像素 {dark})")
    check("REPLACED SCAN LINE" in text_of(after) and "Tight line 0" not in text_of(after)
          and "Tight line 1" in text_of(after), "新文字写入,这一行旧的 OCR 文字层被替换,其它行不受影响")
    big_img.save(str(OUT / "scan_edit.png"))

print()
if failures:
    print(f"失败 {len(failures)} 项:")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("全部通过 ✔  输出目录:", OUT)
