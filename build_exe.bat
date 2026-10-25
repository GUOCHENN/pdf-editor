@echo off
chcp 65001 >nul
rem 打包成单个 exe(不需要用户电脑装 Python)。输出:dist\PDFEditor.exe
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller
python make_icon.py
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name PDFEditor --icon app.ico --add-data "app.ico;." ^
    --hidden-import fontTools.subset --hidden-import fontTools.ttLib --hidden-import mammoth ^
    --collect-submodules markdown --collect-data docx --collect-data pptx --collect-data openpyxl ^
    --exclude-module matplotlib --exclude-module numpy --exclude-module scipy --exclude-module tkinter ^
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtOpenGL --exclude-module PySide6.QtPdf --exclude-module PySide6.QtSql ^
    main.py
echo.
echo 完成:dist\PDFEditor.exe
pause
