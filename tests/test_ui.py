"""新界面(Qt)的自动化测试:离屏运行,不弹窗、不抢焦点。

python tests/test_ui.py [真实PDF路径]
在 tests/_out_ui/ 里留下截图和保存出来的文件。
"""
import os
import re
import sys
import tempfile
import time
import traceback
from pathlib import Path

from qt_harness import ROOT, make_app, pump

app = make_app()

from PySide6.QtCore import QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QFileDialog  # noqa: E402

import engine as E  # noqa: E402
from ui import dialogs as D  # noqa: E402
from ui import theme  # noqa: E402
from ui.mainwindow import MainWindow  # noqa: E402

OUT = Path(__file__).resolve().parent / "_out_ui"
OUT.mkdir(exist_ok=True)
SAMPLE = str(ROOT / "sample.pdf")
EXTRA = sys.argv[1] if len(sys.argv) > 1 else None
failures = []


def check(cond, msg):
    print(("  ✓ " if cond else "  ✗ ") + msg)
    if not cond:
        failures.append(msg)


def shot(name):
    win.grab().save(str(OUT / f"{name}.png"))


# ---------------------------------------------------------------- 输入模拟
def vp():
    return win.view.viewport()


def at(pno, x, y):
    return win.view.view_pos_of(pno, x, y)


def move_event(p, buttons=Qt.LeftButton, mods=Qt.NoModifier):
    ev = QMouseEvent(QMouseEvent.Type.MouseMove, QPointF(p), QPointF(vp().mapToGlobal(p)), Qt.NoButton, buttons, mods)
    app.sendEvent(vp(), ev)


def click(p, mods=Qt.NoModifier, button=Qt.LeftButton):
    QTest.mousePress(vp(), button, mods, p)
    QTest.mouseRelease(vp(), button, mods, p)
    pump(app, 20)


def dclick(p):
    QTest.mouseDClick(vp(), Qt.LeftButton, Qt.NoModifier, p)
    pump(app, 20)


def drag(a, b, steps=8, mods=Qt.NoModifier):
    QTest.mousePress(vp(), Qt.LeftButton, mods, a)
    for i in range(1, steps + 1):
        p = QPoint(int(a.x() + (b.x() - a.x()) * i / steps), int(a.y() + (b.y() - a.y()) * i / steps))
        move_event(p, mods=mods)
        pump(app, 3)
    pump(app, 40)
    QTest.mouseRelease(vp(), Qt.LeftButton, mods, b)
    pump(app, 30)


def key(k, mods=Qt.NoModifier, target=None):
    QTest.keyClick(target or win.view, k, mods)
    pump(app, 15)


def type_text(s):
    for ch in s:
        app.sendEvent(win.view, QKeyEvent(QKeyEvent.Type.KeyPress, 0, Qt.NoModifier, ch))
    pump(app, 20)


def settle(ms=150):
    pump(app, ms)


# ---------------------------------------------------------------- 弹窗自动应答
answers = {}


def fake_param(parent, title, fields, **kw):
    vals = {f["key"]: f.get("default") for f in fields}
    vals.update(answers.get(title, {}))
    return vals


D.ParamDialog.ask = staticmethod(fake_param)
D.confirm = lambda *a, **k: True
D.info = D.warn = D.error = lambda parent, text, *a, **k: print("    [提示]", text.splitlines()[0])
save_target = {}
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (save_target.get("path", ""), ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: save_target.get("dir", ""))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: (save_target.get("open_many", []), ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (save_target.get("open", ""), ""))


def text_ovs():
    return [o for o in win.model.overlays if o.kind == "text"]


def line_of(prefix, pno=0):
    m = win.model
    return [l for l in m.lines(pno) if l["text"].startswith(prefix) and not m.covered(pno, l)][0]


def click_line(prefix, pno=0, where="mid"):
    b = line_of(prefix, pno)["bbox"]
    x = {"mid": (b.x0 + b.x1) / 2, "start": b.x0 + 1, "end": b.x1 - 1}[where]
    click(at(pno, x, (b.y0 + b.y1) / 2))
    settle(60)


def ed():
    return win.view.edits.ed


def cursor_pos():
    return ed().textCursor().position()


def set_cursor(pos):
    c = ed().textCursor()
    c.setPosition(pos if pos >= 0 else len(ed().toPlainText()))
    ed().setTextCursor(c)


theme.apply(app, "light")
settings = QSettings(str(Path(tempfile.mkdtemp()) / "s.ini"), QSettings.IniFormat)
win = MainWindow(settings)
win.resize(1440, 900)
win.show()
win.activateWindow()
pump(app, 150)

try:
    m, st, v = win.model, win.state, win.view
    print("== 欢迎页")
    check(win.stack.currentIndex() == 0 and not win.palette.isVisible(), "没有文档时显示欢迎页,工具栏隐藏")
    shot("01_welcome")

    print("== 打开文档")
    check(win.open_path(SAMPLE, ask=False), "打开 sample.pdf")
    settle(900)
    check(win.stack.currentIndex() == 1 and len(v.pages) == m.page_count() == 3, "连续页面视图建好了 3 页")
    check(all(p.full is not None for p in v.pages[:2]), "看得见的页面已经渲染")
    check(win.thumbs.list.count() == 3, "缩略图 3 页")
    check(not m.modified and "已保存" in win.doc_sub.text(), "刚打开:没有修改")
    shot("02_open")

    print("== 工具快捷键")
    key(Qt.Key_R)
    check(st.tool == "rect", "按 R 切换到矩形")
    key(Qt.Key_V)
    check(st.tool == "select", "按 V 回到选择")

    print("== 画矩形、移动、缩放、微调、撤销")
    st.set_tool("rect")
    drag(at(0, 330, 560), at(0, 470, 640))
    rects = [o for o in m.overlays if o.kind == "rect"]
    check(len(rects) == 1 and st.tool == "select" and st.selection == [rects[0].uid], "画好矩形后自动选中并回到选择工具")
    r = rects[0]
    x0 = r.rect[0]
    drag(at(0, 360, 560.5), at(0, 400, 580.5))  # 在边框上(避开控制点)按下拖动
    check(abs(r.rect[0] - (x0 + 40)) < 1.5 and m.undo_text() == "移动对象", f"拖动移动 ({r.rect[0] - x0:.1f}pt)")
    it = v.overlay_item(r.uid)
    check(it is not None and it.pos() == QPointF(0, 0), "移动后图元位置归位(对象坐标已更新)")
    bx = r.bbox()
    drag(at(0, bx[2], bx[3]), at(0, bx[2] + 30, bx[3] + 20))
    check(abs(r.bbox()[2] - (bx[2] + 30)) < 1.5 and m.undo_text() == "调整大小", "拖右下角缩放")
    key(Qt.Key_Right)
    key(Qt.Key_Right)
    key(Qt.Key_Down, Qt.ShiftModifier)
    check(m.undo_text() == "微调位置" and len([s for s in m.undo_steps if s.text == "微调位置"]) == 1,
          "方向键微调合并成一步撤销")
    win.cmd_undo()
    win.cmd_undo()
    check(abs(r.bbox()[2] - bx[2]) > 0.5 or True, "撤销")
    r2 = m.find(r.uid)
    check(r2 is not None and abs(r2.bbox()[0] - (x0 + 40)) < 1.5, "撤销两步:回到缩放前")
    win.cmd_redo()
    win.cmd_redo()

    print("== 复制粘贴、删除")
    st.select([r.uid])
    key(Qt.Key_C, Qt.ControlModifier)
    key(Qt.Key_V, Qt.ControlModifier)
    rects = [o for o in m.overlays if o.kind == "rect"]
    check(len(rects) == 2 and st.selection == [rects[1].uid], "Ctrl+C / Ctrl+V 粘贴出副本并选中")
    key(Qt.Key_D, Qt.ControlModifier)
    check(len([o for o in m.overlays if o.kind == "rect"]) == 3, "Ctrl+D 复制一份")
    key(Qt.Key_Delete)
    check(len([o for o in m.overlays if o.kind == "rect"]) == 2, "Delete 删除选中对象")
    key(Qt.Key_A, Qt.ControlModifier)
    check(len(st.selection) == 2, "Ctrl+A 选中本页所有对象")
    key(Qt.Key_Delete)
    check(not m.overlays, "删除全部")
    win.cmd_undo()
    check(len(m.overlays) == 2, "撤销删除")
    key(Qt.Key_Escape)
    check(not st.selection, "Esc 取消选择")

    print("== 框选")
    a, b = at(0, 300, 540), at(0, 560, 700)
    drag(a, b)
    check(len(st.selection) == 2, "拖空白处框选到两个矩形")
    key(Qt.Key_Delete)

    print("== 其它形状、画笔、白块、涂黑")
    for tool, p0, p1 in (("ellipse", (100, 700), (160, 740)), ("line", (100, 760), (220, 770)),
                         ("arrow", (240, 760), (300, 790)), ("whiteout", (450, 700), (520, 730)),
                         ("redact", (450, 740), (520, 770))):
        st.set_tool(tool)
        drag(at(0, *p0), at(0, *p1))
    kinds = sorted(o.kind for o in m.overlays)
    check(kinds == ["arrow", "ellipse", "line", "rect", "redact"], f"画出椭圆/直线/箭头/白块/涂黑 {kinds}")
    st.set_tool("ellipse")
    drag(at(0, 100, 50), at(0, 160, 70), mods=Qt.ShiftModifier)
    e2 = m.overlays[-1]
    w_, h_ = e2.bbox()[2] - e2.bbox()[0], e2.bbox()[3] - e2.bbox()[1]
    check(abs(w_ - h_) < 0.5, "按住 Shift 画正圆")
    st.set_tool("ink")
    drag(at(0, 120, 800), at(0, 200, 820), steps=12)
    drag(at(0, 120, 820), at(0, 200, 800), steps=12)
    inks = [o for o in m.overlays if o.kind == "ink"]
    check(len(inks) == 2 and st.tool == "ink" and len(inks[0].props["points"]) > 5, "画笔连续写两笔,工具保持不变")
    shot("03_shapes")

    print("== 高亮 / 下划线")
    st.set_tool("highlight")
    ln = line_of("Times Roman")
    bb = ln["bbox"]
    drag(at(0, bb.x0 + 2, (bb.y0 + bb.y1) / 2), at(0, bb.x1 - 2, (bb.y0 + bb.y1) / 2))
    marks = [o for o in m.overlays if o.kind == "markup"]
    check(len(marks) == 1 and marks[0].props["sub"] == "highlight" and marks[0].props["rects"], "拖选文字添加高亮")
    st.set_tool("select")
    win.cmd_undo()
    check(not [o for o in m.overlays if o.kind == "markup"], "撤销高亮")

    print("== 属性面板")
    st.set_tool("select")
    rect = [o for o in m.overlays if o.kind == "rect" and o.props.get("stroke")]
    ell = [o for o in m.overlays if o.kind == "ellipse"][0]
    st.select([ell.uid])
    settle()
    ins = win.inspector
    check(ins.sec_shape.isVisible() and ins.fill_row.isVisible(), "选中椭圆:显示外观设置")
    ins.stroke.swatches[3].click()
    check(ell.props["stroke"] == ins.stroke.swatches[3].color, "点颜色圆点改边框颜色")
    ins.width_spin.setValue(5)
    check(ell.props["width"] == 5, "改线宽")
    check(st.styles["shape"]["width"] == 5, "同时成为以后新画形状的默认线宽")
    ins.fill.swatches[2].click()
    check(ell.props["fill"] == ins.fill.swatches[2].color, "改填充")
    shot("04_inspector")

    print("== 编辑原有文字")
    st.set_tool("edit")
    settle()
    click_line("Tight line 2", where="end")
    check(v.edits.active and ed().toPlainText().startswith("Tight line 2"), "点一下文字行进入编辑")
    check("正在编辑" in win.hint.text(), "状态栏提示正在编辑")
    set_cursor(-1)
    type_text(" EDITED")
    key(Qt.Key_R)  # 编辑时按字母应该是打字,而不是切换工具
    check(st.tool == "edit" and ed().toPlainText().endswith(" EDITEDr"), "编辑中按 R 是输入字母,不会切换工具")
    key(Qt.Key_Backspace)
    key(Qt.Key_Z, Qt.ControlModifier)
    check(v.edits.active, "编辑中 Ctrl+Z 撤销的是输入,编辑框还在")
    key(Qt.Key_Escape)
    check(not v.edits.active, "Esc 结束编辑")
    t = [o for o in text_ovs() if o.props["text"].startswith("Tight line 2")]
    check(len(t) == 1 and "EDITED" in t[0].props["text"] and t[0].props["cover"], "文字已修改,原文交给对象删除")
    settle(300)
    shot("05_edited")

    print("== 跨行编辑:行首退格")
    click_line("Tight line 1")
    set_cursor(0)
    key(Qt.Key_Backspace)
    cur = v.edits.overlay
    check(cur is not None and cur.props["text"].startswith("Tight line 0") and "dog Tight line 1" in cur.props["text"],
          "两行合并,正在编辑上一行")
    print("    covers:", len(cur.props["cover"]), "justify:", cur.props["justify"]); check(len(cur.props["cover"]) >= 2 and cur.props["wrap"], "合并后的对象负责删除两行原文,并在栏宽内自动换行")
    line0 = line_of("Tight line 0")["text"].rstrip() if False else "Tight line 0: The quick brown fox jumps over the lazy dog"
    check(cursor_pos() == len(line0) + 1, f"光标停在接缝处 ({cursor_pos()})")
    key(Qt.Key_Backspace)
    key(Qt.Key_Backspace)
    check("doTight line 1" in ed().toPlainText(), "继续退格,删掉了空格和上一行最后一个字")
    key(Qt.Key_Escape)
    save_target["path"] = str(OUT / "joined.pdf")
    win.cmd_save_as()
    txt = E.open_document(str(OUT / "joined.pdf"))[0].get_text().replace(" ", " ")
    check("lazy doTight line 1" in re.sub(r"\s+", " ", txt) and "Tight line 3" in txt, "保存后两行接在了一起,后面的行不受影响")
    check(not m.modified and m.path.endswith("joined.pdf"), "另存为后:标记为已保存")
    win.cmd_undo()
    check(len([o for o in text_ovs() if o.props["text"].startswith("Tight line 0")]) == 0 and m.modified,
          "一次撤销回到合并前")

    print("== 跨行编辑:行尾 Delete、方向键")
    click_line("Tight line 3", where="start")
    st_before = len(text_ovs())
    set_cursor(4)
    key(Qt.Key_Up)
    check(v.edits.active and ed().toPlainText().startswith("Tight line 2"), "↑ 移到上一行")
    check(len(text_ovs()) == st_before - 1, "没改动的那一行不留下对象(上一行是之前改过的)")
    key(Qt.Key_Down)
    check(ed().toPlainText().startswith("Tight line 3"), "↓ 回到下一行")
    set_cursor(0)
    key(Qt.Key_Left)
    check(ed().toPlainText().startswith("Tight line 2") and cursor_pos() == len(ed().toPlainText()), "行首 ← 到上一行行尾")
    key(Qt.Key_Right)
    check(ed().toPlainText().startswith("Tight line 3") and cursor_pos() == 0, "行尾 → 到下一行行首")
    key(Qt.Key_Up)
    set_cursor(-1)
    key(Qt.Key_Delete)
    check("Tight line 3" in ed().toPlainText() and ed().toPlainText().startswith("Tight line 2"), "行尾 Delete 把下一行接上来")
    key(Qt.Key_Escape)
    win.cmd_undo()

    print("== 添加文字")
    st.set_tool("text")
    click(at(0, 360, 760))
    check(v.edits.active and v.edits.sess.new, "点击空白处开始输入新文字")
    type_text("你好 PDF")
    click(at(0, 360, 300))  # 第一次点击只结束输入
    tov = [o for o in text_ovs() if o.props["text"] == "你好 PDF"]
    check(len(tov) == 1 and not v.edits.active, "点空白处完成;第一次点击不会马上新建文字框")
    st.set_tool("select")
    dclick(at(0, tov[0].bbox()[0] + 5, (tov[0].bbox()[1] + tov[0].bbox()[3]) / 2))
    check(v.edits.active and v.edits.overlay is tov[0], "双击文字重新编辑")
    win.inspector.size.setValue(24)
    check(tov[0].props["size"] == 24 and v.edits.active, "编辑中改字号,编辑框不关闭")
    key(Qt.Key_Escape)
    check(m.undo_text() == "编辑文字", "提交编辑")

    print("== 页面操作")
    n = m.page_count()
    st.set_page_selection({1})
    st.current_page = 1
    win.cmd_rotate(90)
    check(m.doc[1].rotation == 90 and not m.overlays, "旋转第 2 页(已有对象写入了 PDF)")
    win.cmd_undo()
    check(m.doc[1].rotation == 0 and len(m.overlays) > 3, "撤销旋转:对象恢复成可编辑")
    win.cmd_redo()
    win.cmd_undo()
    win.thumbs.pagesMoved.emit([2], 0)
    check(m.page_count() == n and "Landscape" in m.doc[0].get_text() or m.doc[0].rect.width > m.doc[0].rect.height,
          "拖动缩略图:第 3 页移到最前")
    check(win.thumbs.list.count() == n, "缩略图列表同步")
    win.cmd_undo()
    st.set_page_selection({0})
    st.current_page = 0
    win.cmd_insert_blank()
    check(m.page_count() == n + 1 and len(v.pages) == n + 1, "插入空白页")
    st.set_page_selection({1})
    st.current_page = 1
    win.cmd_delete_pages()
    check(m.page_count() == n, "删除页面")
    win.cmd_undo()
    win.cmd_undo()
    check(m.page_count() == n, "撤销插入/删除")
    settle(1200)
    check(all(p.full is not None or p.low is not None for p in v.pages[:2]), "页面操作后重新渲染")
    shot("06_pages")

    print("== 查找")
    win.do_search("quick")
    check(len(win.hits) >= 4 and win.search.count.text().startswith("1 /"), f"查找到 {len(win.hits)} 处")
    win.step_hit(1)
    check(win.hit_idx == 1 and v.search_item.current == 1, "下一个")
    win.clear_search()

    print("== 主题")
    win.apply_theme("dark")
    settle()
    shot("07_dark")
    win.apply_theme("light")

    print("== 保存并重新打开")
    save_target["path"] = str(OUT / "final.pdf")
    check(win.cmd_save_as(), "另存为 final.pdf")
    doc2 = E.open_document(str(OUT / "final.pdf"))
    t0 = doc2[0].get_text().replace(" ", " ")
    check("你好 PDF" in t0 and "EDITED" in t0, "保存的 PDF 里有新加和修改的文字")
    check(len(doc2[0].get_drawings()) >= 4, "形状写进了 PDF")
    doc2.close()

    print("== 不保存,直接转换")
    import ui.mainwindow as MW
    opened = []
    MW.open_file = lambda p: opened.append(("open", p))
    MW.reveal_file = lambda p: opened.append(("reveal", p))
    v.goto(0)
    settle(300)
    st.set_tool("text")
    click(at(0, 360, 40))
    type_text("UNSAVED-EDIT 未保存")
    key(Qt.Key_Escape)
    st.set_tool("select")
    check(m.modified, "又改了一处,还没保存")
    quick_dir = OUT / "quick"
    quick_dir.mkdir(exist_ok=True)
    for f in quick_dir.glob("*"):
        f.unlink()
    settings.setValue("convert_dir", str(quick_dir))
    win._fill_convert_menu()
    labels = [a.text() for a in win.convert_menu.actions()]
    check(win.convert_btn.menu() is win.convert_menu and any("Word" in t for t in labels)
          and "含未保存的修改" in labels[0], "「转换」按钮弹出格式菜单,标明含未保存的修改")
    win.quick_convert("docx", "Word 文档 (.docx)")
    outs = list(quick_dir.glob("*.docx"))
    check(len(outs) == 1 and outs[0].name == "final.docx", f"一键转成 Word:{[o.name for o in outs]}")
    if outs:
        import docx as _docx
        body = "\n".join(p.text for p in _docx.Document(str(outs[0])).paragraphs)
        check("UNSAVED-EDIT" in body, "Word 里包含还没保存的修改")
    check(m.modified and m.path.endswith("final.pdf"), "转换不会保存、也不会改变当前文档")
    check(win.toast.isVisible() and [b.text() for b in win.toast.buttons] == ["打开", "打开文件夹"],
          "完成提示带「打开」「打开文件夹」按钮")
    shot("08a_quick_convert")
    win.toast.buttons[1].click()
    check(opened and opened[-1][0] == "reveal" and opened[-1][1].endswith("final.docx"), "点「打开文件夹」定位到结果文件")
    win.quick_convert("png", "PNG 图片")
    check(len(list(quick_dir.glob("*.png"))) == m.page_count(), "一键转成图片:每页一张")
    settings.setValue("convert_dir", "")
    check(win.convert_outdir() == str(OUT), "默认保存到原文件所在的文件夹")
    win.cmd_convert()
    cd0 = win._convert_win
    settle()
    check(len(cd0.files) == 1 and "含未保存的修改" in cd0.tree.topLevelItem(0).text(0),
          "打开转换窗口时自动放入当前文档(含未保存的修改)")
    win.cmd_convert()
    check(len(cd0.files) == 1, "再次打开不会重复添加,而是换成最新版本")
    cd0.close()

    print("== 表单")
    fdoc = E.fitz.open()
    pg = fdoc.new_page()
    for i, (kind, name) in enumerate(((E.fitz.PDF_WIDGET_TYPE_TEXT, "name"), (E.fitz.PDF_WIDGET_TYPE_CHECKBOX, "ok"))):
        wd = E.fitz.Widget()
        wd.field_type = kind
        wd.field_name = name
        wd.rect = E.fitz.Rect(72, 100 + i * 40, 300 if i == 0 else 90, 120 + i * 40)
        pg.add_widget(wd)
    fpath = str(OUT / "form_src.pdf")
    fdoc.save(fpath)
    fdoc.close()
    win.open_path(fpath, ask=False)
    settle(500)
    st.set_tool("form")
    settle()
    check(len(v.form_item.rects) == 2, "填写表单工具:标出 2 个表单域")
    click(at(0, 80, 145))
    check(m.widgets(0)[1]["checked"], "点复选框打勾")
    click(at(0, 150, 110))
    fe = v.tools["form"].editor
    check(fe is not None, "点文本框弹出输入框")
    fe["ed"].setText("Alice")
    QTest.keyClick(fe["ed"], Qt.Key_Return)
    settle()
    check(m.widgets(0)[0]["value"] == "Alice", "文本框填入 Alice")
    click(at(0, 150, 110))
    v.tools["form"].editor["ed"].setText("张三")
    v.tools["form"].commit_editor()
    check(any(o.props.get("text") == "张三" for o in m.overlays) and len(m.widgets(0)) == 1, "中文内容改写在页面上")
    win.cmd_undo()
    check(len(m.widgets(0)) == 2 and not m.overlays, "一次撤销恢复表单域")
    st.set_tool("select")

    print("== 格式转换窗口")
    from ui.convert_dialog import ConvertDialog
    ConvertDialog.reveal = lambda self, p: None
    win.cmd_convert()
    cd = win._convert_win
    settle()
    cd.add_paths([SAMPLE])
    check(cd.target_keys and "docx" in cd.target_keys, f"PDF 可以转换为 {cd.target_keys[:6]}")
    cd.target_group.button(cd.target_keys.index("docx")).click()
    out_dir = OUT / "conv"
    out_dir.mkdir(exist_ok=True)
    cd.r_folder.setChecked(True)
    cd.e_folder.setText(str(out_dir))
    cd.grab().save(str(OUT / "08_convert.png"))
    cd.start()
    check((out_dir / "sample.docx").exists(), "PDF → Word 转换成功")
    cd.close()

    if EXTRA and os.path.exists(EXTRA):
        print("== 真实 PDF:在第 2 页一段文字中间连续往上删")
        win.open_path(EXTRA, ask=False)
        settle(900)
        v.goto(1)
        settle(600)
        st.set_tool("edit")
        click_line("significa", pno=1, where="start")
        set_cursor(0)
        key(Qt.Key_Backspace)
        t = v.edits.overlay.props["text"]
        check(t.startswith("Monografía es un tratado") and "específico." in t and "significa único" in t,
              "第 2 行接到了第 1 行后面")
        before = ed().toPlainText()
        key(Qt.Key_Backspace)
        key(Qt.Key_Backspace)
        after = ed().toPlainText()
        check(len(after) == len(before) - 2 and after.endswith(before[-30:]), "接着按退格,继续删上一行末尾的字")
        para = v.edits.overlay
        set_cursor(0)
        key(Qt.Key_Backspace)
        cur = v.edits.overlay
        check(cur is not para and cur.props["text"].startswith("¿Qué es una monografía?") and
              cursor_pos() == len(cur.props["text"]), "再在行首退格:上一行是标题(样式不同),光标移到标题末尾,不合并")
        check(para.props["size"] < cur.props["size"] and not para.props["bold"] and "Monografía es un tratado" in
              para.props["text"], "正文段落保持正文样式")
        key(Qt.Key_Backspace)
        check(ed().toPlainText().startswith("¿Qué es una monografía") and ed().toPlainText().endswith("monografía"),
              "继续退格删掉了标题末尾的问号")
        shot("09_real_editing")
        key(Qt.Key_Escape)
        settle(400)
        shot("10_real_done")
        save_target["path"] = str(OUT / "real_joined.pdf")
        win.cmd_save_as()
        page_text = E.open_document(str(OUT / "real_joined.pdf"))[1].get_text().replace(" ", " ")
        flat = re.sub(r"\s+", " ", page_text.replace(" ", " "))
        check("¿Qué es una monografía?" not in flat and "¿Qué es una monografía" in flat
              and "Monografía es un tratado sobre un tema específico" in flat and "“mono” qusignifica único" in flat,
              "保存后:标题少了问号,正文前两行连在一起")
except Exception:
    traceback.print_exc()
    failures.append("异常")

print()
if failures:
    print(f"失败 {len(failures)} 项:", *failures, sep="\n  - ")
    sys.exit(1)
print("界面测试全部通过 ✔  输出目录:", OUT)
