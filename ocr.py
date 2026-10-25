"""文字识别(OCR):调用 Windows 10/11 自带的 OCR 引擎,不需要安装任何额外组件。

通过 PowerShell 调用 WinRT 的 Windows.Media.Ocr。所有图片在一个 PowerShell 进程里依次识别,
每识别完一张就输出一行(base64 的 JSON),这样界面能显示进度,也能随时取消。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile

_PS_HEADER = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $task = $asTaskGeneric.MakeGenericMethod($type).Invoke($null, @($op))
    $task.Wait(-1) | Out-Null
    $task.Result
}
function Emit($obj) {
    $json = $obj | ConvertTo-Json -Depth 8 -Compress
    [Console]::Out.WriteLine([Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($json)))
    [Console]::Out.Flush()
}
"""

_PS_LANGS = _PS_HEADER + r"""
$r = @()
foreach ($l in [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages) {
    $r += @{ tag = $l.LanguageTag; name = $l.DisplayName }
}
Emit @{ langs = $r; maxdim = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension }
"""

_PS_OCR = "param([string]$List, [string]$Lang)\n" + _PS_HEADER + r"""
if ($Lang) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language($Lang)))
} else {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
}
if (-not $engine) { Emit @{ error = 'no-engine' }; exit 2 }
$i = 0
foreach ($path in (Get-Content -LiteralPath $List -Encoding UTF8)) {
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $res = Await ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        foreach ($ln in $res.Lines) {
            $words = @()
            foreach ($w in $ln.Words) {
                $words += @{ t = $w.Text; x = $w.BoundingRect.X; y = $w.BoundingRect.Y; w = $w.BoundingRect.Width; h = $w.BoundingRect.Height }
            }
            $lines += @{ text = $ln.Text; words = $words }
        }
        Emit @{ i = $i; lines = $lines; angle = $res.TextAngle }
    } catch {
        Emit @{ i = $i; error = $_.Exception.Message }
    }
    $i++
}
"""

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class OcrError(Exception):
    pass


def available() -> bool:
    return sys.platform == "win32"


def _run_script(script: str, args: list[str], timeout=None):
    """启动 PowerShell 执行脚本,逐行产出解码后的 JSON 对象。"""
    fd, path = tempfile.mkstemp(suffix=".ps1")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:  # 带 BOM,PowerShell 5.1 才会按 UTF-8 读
        f.write(script)
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path] + args
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    return proc, path


def list_languages() -> list[tuple[str, str]]:
    """本机 OCR 引擎支持的语言 [(语言标签, 显示名)]。"""
    if not available():
        return []
    proc, path = _run_script(_PS_LANGS, [])
    try:
        out, _err = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        return []
    finally:
        os.unlink(path)
    for line in out.decode("utf-8", "replace").splitlines():
        try:
            data = json.loads(base64.b64decode(line).decode("utf-8"))
            langs = data.get("langs", [])
            if isinstance(langs, dict):
                langs = [langs]
            return [(l["tag"], l["name"]) for l in langs]
        except Exception:
            continue
    return []


def ocr_images(paths: list[str], lang: str | None = None, progress=None, cancel=None) -> list[dict]:
    """识别一组图片,返回与 paths 等长的列表,每项 {'lines': [{'text','words':[{t,x,y,w,h}]}]}(坐标是图片像素)。
    progress(done, total) 每识别完一张调用一次;cancel() 返回 True 时中止并抛出 OcrError。"""
    if not available():
        raise OcrError("文字识别需要 Windows 10/11 自带的 OCR 引擎")
    fd, listfile = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(os.path.abspath(p) for p in paths))
    proc, script = _run_script(_PS_OCR, ["-List", listfile] + (["-Lang", lang] if lang else []))
    results: list[dict | None] = [None] * len(paths)
    try:
        done = 0
        for raw in proc.stdout:
            if cancel and cancel():
                proc.kill()
                raise OcrError("已取消")
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                data = json.loads(base64.b64decode(line).decode("utf-8"))
            except Exception:
                continue
            if data.get("error") == "no-engine":
                raise OcrError("找不到可用的 OCR 语言。请在 Windows 设置 → 时间和语言 → 语言和区域 中添加中文(简体),"
                               "并勾选“光学字符识别”。")
            i = data.get("i")
            if i is None:
                continue
            if "error" in data:
                raise OcrError(f"识别失败:{data['error']}")
            lines = data.get("lines") or []
            if isinstance(lines, dict):
                lines = [lines]
            for ln in lines:
                w = ln.get("words") or []
                ln["words"] = [w] if isinstance(w, dict) else w
            results[i] = {"lines": lines, "angle": data.get("angle") or 0}
            done += 1
            if progress:
                progress(done, len(paths))
        proc.wait(timeout=30)
        if done == 0 and paths:
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            raise OcrError("文字识别没有返回结果。" + (f"\n{err[:400]}" if err else ""))
    finally:
        if proc.poll() is None:
            proc.kill()
        for p in (listfile, script):
            try:
                os.unlink(p)
            except OSError:
                pass
    return [r if r is not None else {"lines": [], "angle": 0} for r in results]


def pick_language(preferred: str | None = None) -> str | None:
    """没有指定语言时,优先用中文引擎(同时能识别英文数字),否则交给系统按用户语言选择。"""
    if preferred:
        return preferred
    for tag, _name in list_languages():
        if tag.lower().startswith("zh"):
            return tag
    return None


def render_for_ocr(doc, idxs, dpi: int = 200) -> dict:
    """把要识别的页渲染成图片(必须在主线程调用,PyMuPDF 不是线程安全的)。返回可交给 recognize 的任务。"""
    import pymupdf as fitz

    tmp = tempfile.mkdtemp(prefix="pdfeditor_ocr_")
    paths, scales = [], []
    for i in idxs:
        page = doc[i]
        scale = dpi / 72.0
        longest = max(page.rect.width, page.rect.height) * scale
        if longest > 9000:  # Windows OCR 引擎单边最大 10000 像素
            scale *= 9000 / longest
        p = os.path.join(tmp, f"p{i:05d}.png")
        page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(p)
        paths.append(p)
        scales.append(scale)
    return dict(idxs=list(idxs), paths=paths, scales=scales, tmp=tmp)


def recognize(job: dict, lang: str | None = None, progress=None, cancel=None) -> dict:
    """识别 render_for_ocr 渲染好的图片(可在后台线程运行,不碰 PyMuPDF)。
    返回 {页号: (行列表, 图片像素/pt 的比例)},可直接交给 engine.add_text_layer。"""
    try:
        results = ocr_images(job["paths"], pick_language(lang), progress, cancel)
        return {i: (normalize(r), s) for i, r, s in zip(job["idxs"], results, job["scales"])}
    finally:
        for p in job["paths"]:
            try:
                os.unlink(p)
            except OSError:
                pass
        try:
            os.rmdir(job["tmp"])
        except OSError:
            pass


def ocr_pdf_pages(doc, idxs, lang: str | None = None, dpi: int = 200, progress=None, cancel=None) -> dict:
    """渲染并识别 PDF 的若干页(同一线程里完成,给脚本/测试用)。"""
    return recognize(render_for_ocr(doc, idxs, dpi), lang, progress, cancel)


# ---------------------------------------------------------------------------
# 结果整理
# ---------------------------------------------------------------------------


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x2E80 <= o <= 0xA4CF) or (0xF900 <= o <= 0xFAFF) or (0xFE30 <= o <= 0xFE4F) or (0xFF00 <= o <= 0xFFEF)


def join_words(words: list[dict]) -> str:
    """拼接单词:中文之间不加空格,英文/数字之间加空格。"""
    out = ""
    for w in words:
        t = w["t"]
        if out and not (_is_cjk(out[-1]) or _is_cjk(t[0])):
            out += " "
        out += t
    return out


def normalize(result: dict) -> list[dict]:
    """[{'text','bbox':(x0,y0,x1,y1)}...],坐标仍是图片像素;按从上到下排序。"""
    lines = []
    for ln in result["lines"]:
        words = [w for w in ln["words"] if w.get("t")]
        if not words:
            continue
        x0 = min(w["x"] for w in words)
        y0 = min(w["y"] for w in words)
        x1 = max(w["x"] + w["w"] for w in words)
        y1 = max(w["y"] + w["h"] for w in words)
        lines.append({"text": join_words(words), "bbox": (x0, y0, x1, y1)})
    lines.sort(key=lambda l: (round(l["bbox"][1] / 8), l["bbox"][0]))
    return lines
