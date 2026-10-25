"""PDF 编辑器入口:python main.py [文件.pdf]"""
import os
import sys


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def selftest(report_path: str | None) -> int:
    """无界面自检(给打包后的 exe 用):打开、改中文、保存、字体子集化、重新读取。"""
    import tempfile

    import engine as E

    lines, ok = [], True
    try:
        tmp = tempfile.mkdtemp(prefix="pdfeditor_selftest_")
        src = os.path.join(tmp, "src.pdf")
        doc = E.fitz.open()
        page = doc.new_page()
        font = E.font_for_text("yahei", False, False, "中")
        tw = E.fitz.TextWriter(page.rect)
        tw.append((72, 100), "原来的中文文字 original", font=font, fontsize=16)
        tw.write_text(page)
        page.insert_text((72, 140), "second line stays", fontsize=12, fontname="helv")
        doc.subset_fonts()  # 真实 PDF 里的字体基本都已裁剪,这里模拟这种情况
        doc.save(src)
        doc.close()
        d = E.open_document(src)
        ln = E.page_lines(d[0])[0]
        ov = E.overlays_from_lines(0, [ln])[0]
        ov.props["text"] = "修改后的中文 edited"
        out = os.path.join(tmp, "out.pdf")
        E.save_document(d, [ov], out)
        text = E.open_document(out)[0].get_text().replace(" ", " ")
        size = os.path.getsize(out)
        lines.append(f"fitz={E.fitz.__doc__.splitlines()[0] if E.fitz.__doc__ else ''}")
        lines.append(f"text={text.strip()!r} size={size}")
        ok = "修改后的中文 edited" in text and "原来" not in text and size < 300_000

        # 格式转换用到的组件(Markdown / Story / python-docx / openpyxl / python-pptx / mammoth)都要能在打包后工作
        import converter as C
        md = os.path.join(tmp, "t.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("# 标题 Title\n\n正文 **粗体** text\n\n| 列A | 列B |\n|---|---|\n| 苹果 | 12 |\n| 香蕉 | 34 |\n")
        r = C.convert(md, "pdf", tmp)
        lines.append(f"md->pdf={'ok' if r.ok else 'FAIL ' + r.message}")
        ok = ok and r.ok
        if r.ok:
            pdf2 = r.outputs[0]
            docx = None
            for target in ("docx", "xlsx", "pptx", "txt", "html", "png"):
                r2 = C.convert(pdf2, target, tmp, C.Options(dpi=60))
                lines.append(f"pdf->{target}={'ok' if r2.ok else 'FAIL ' + r2.message}")
                ok = ok and r2.ok
                if target == "docx" and r2.ok:
                    docx = r2.outputs[0]
            if docx:
                C._docx_builtin(docx, os.path.join(tmp, "b.pdf"), C._Ctx(None, None))
                lines.append("docx->pdf(mammoth)=ok")
        import ocr
        if ocr.available() and ocr.list_languages():
            d3 = E.open_document(pdf2) if r.ok else None
            res = ocr.ocr_pdf_pages(d3, [0], dpi=150) if d3 is not None else {}
            n_lines = sum(len(v[0]) for v in res.values())
            lines.append(f"ocr_lines={n_lines}")
            ok = ok and n_lines > 0
        else:
            lines.append("ocr=skipped")
    except Exception:  # noqa: BLE001
        import traceback
        lines.append(traceback.format_exc())
        ok = False
    lines.append("SELFTEST " + ("OK" if ok else "FAILED"))
    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    else:
        print("\n".join(lines))
    return 0 if ok else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        sys.exit(selftest(sys.argv[2] if len(sys.argv) > 2 else None))
    if len(sys.argv) > 1 and sys.argv[1] == "--convert":  # 命令行转换:main.py --convert 文件... --to docx
        import converter
        sys.exit(converter.cli(sys.argv[2:]))
    if len(sys.argv) > 2 and sys.argv[1] == "--ui-smoke":  # 打包检查:不显示窗口,打开示例文件并截图
        sys.exit(ui_smoke(sys.argv[2]))
    sys.exit(run_gui(sys.argv[1] if len(sys.argv) > 1 else None))


def ui_smoke(out_png: str) -> int:
    """在离屏模式下启动界面、生成一个 PDF 打开、渲染、截图,检查打包后的 Qt 组件是否齐全。"""
    import tempfile
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QElapsedTimer, QSettings
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    import engine as E
    app = QApplication(sys.argv)
    fdir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for f in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "arial.ttf"):
        if os.path.exists(os.path.join(fdir, f)):
            QFontDatabase.addApplicationFont(os.path.join(fdir, f))
    from ui import theme
    from ui.mainwindow import MainWindow
    tmp = tempfile.mkdtemp(prefix="pdfeditor_smoke_")
    src = os.path.join(tmp, "smoke.pdf")
    doc = E.fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "UI smoke test", fontsize=24)
    doc.save(src)
    doc.close()
    theme.apply(app, "light")
    win = MainWindow(QSettings(os.path.join(tmp, "s.ini"), QSettings.IniFormat))
    win.resize(1280, 800)
    win.show()
    ok = win.open_path(src, ask=False)
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < 1500:
        app.processEvents()
    ok = ok and win.view.pages and win.view.pages[0].full is not None
    win.grab().save(out_png)
    print("UI SMOKE", "OK" if ok else "FAILED")
    return 0 if ok else 1


def run_gui(path=None) -> int:
    if sys.platform == "win32":  # 任务栏显示自己的图标,而不是 Python 的
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFEditor.App")
        except Exception:
            pass
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("PDF 编辑器")
    app.setOrganizationName("PDFEditor")
    app.setWindowIcon(QIcon(resource_path("app.ico")))
    # 界面是中文:数字统一用小数点(不随系统区域显示成 "5,0"),Qt 自带的对话框(选颜色等)也显示中文
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    QLocale.setDefault(QLocale(QLocale.Chinese, QLocale.China))
    tr = QTranslator(app)
    if tr.load(QLocale(QLocale.Chinese, QLocale.China), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(tr)
    from ui import theme
    from ui.mainwindow import MainWindow
    settings = QSettings("PDFEditor", "PDFEditor")
    theme.apply(app, settings.value("theme", "light"))
    win = MainWindow(settings)
    win.show()
    if path:
        QTimer.singleShot(50, lambda: win.open_path(path, ask=False))
    return app.exec()


if __name__ == "__main__":
    main()
