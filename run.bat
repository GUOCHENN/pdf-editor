@echo off
rem 双击运行 PDF 编辑器(需要已安装 Python 和依赖:pip install -r requirements.txt)
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw main.py %*
) else (
    python main.py %*
    if errorlevel 1 pause
)
