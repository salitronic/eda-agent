# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Read dialog text that has no programmatic representation at all.

THE LAST RESORT, AND IT IS NEEDED. Altium paints its dialog messages
with Delphi TLabel, a TGraphicControl that owns no window handle. That
was measured three ways on a live "Comparator Results (No Differences)"
dialog, and all three came back empty:

  GetWindowText        ''
  WM_GETTEXT           WM_GETTEXTLENGTH answered 0 on the message panel
  MSAA accessible tree walked in full, exposing the OK button and the
                       panels by class name, and no message text at all

The text is on screen and has no accessible representation, so the only
remaining source is the pixels. This captures the window and runs the
OCR engine that ships with Windows.

WHY WINDOWS OCR. It needs no extra install, no model download and no
network: Windows.Media.Ocr is part of the OS and is already present with
en-GB and en-US on this host. Tesseract would mean a dependency the
project does not otherwise carry.

TREAT THE RESULT AS A HINT, NOT AS TRUTH. OCR misreads characters,
especially in part numbers where 0/O and 1/l/I are interchangeable to a
recogniser. Everything here is reported as ``ocr_text`` and never merged
into the fields a caller acts on. A dialog's CAPTION stays the
authoritative signal, because that one is read exactly.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile

import psutil

from ..noconsole import no_window
from ..atomicfile import replace_with_retry
from . import windows as win

#: PrintWindow flag. Renders the full window content including anything
#: drawn by DWM, which a plain BitBlt of the screen would miss when the
#: window is partly obscured.
_PW_RENDERFULLCONTENT = 0x00000002

# PrintWindow sends work to the target UI thread and has no timeout. A
# hung Altium command must not also hang screenshots and crash recovery.
_CAPTURE_TIMEOUT_S = 5.0
_LOG = logging.getLogger(__name__)

#: WinRT's OCR pipeline is async, and PowerShell 5.1 cannot await an
#: IAsyncOperation directly, hence the AsTask reflection dance. Kept as
#: a file rather than an inline -Command so quoting cannot corrupt it.
_OCR_PS1 = r"""
param([Parameter(Mandatory=$true)][string]$Path, [switch]$Words)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq 'AsTask' -and
        $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(20000) | Out-Null
    $t.Result
}

[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) { throw 'no OCR language pack is installed' }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
if ($Words) {
    foreach ($line in $result.Lines) { foreach ($w in $line.Words) {
        $r = $w.BoundingRect
        Write-Output ("{0}`t{1}`t{2}`t{3}" -f [int]$r.X, [int]$r.Y, [int]$r.Height, $w.Text)
    } }
} else {
    foreach ($line in $result.Lines) { Write-Output $line.Text }
}
"""


def available() -> bool:
    """Whether a window can be captured and recognised on this host."""
    if not win.available():
        return False
    try:
        import PIL.Image                          # noqa: F401
        import win32ui                            # noqa: F401
    except ImportError:
        return False
    return os.name == "nt"


def _capture_command(hwnd: int, path: str, enlarge: int) -> list[str]:
    return [sys.executable, "-m", "eda_agent.ui.ocr", str(hwnd), path,
            str(enlarge)]


def _run_capture(command: list[str], timeout: float) -> int:
    # stdin closed too: inherited, the worker shares the MCP server's stdio
    # pipe, and a native module load in it can then wait on that pipe.
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, **no_window())
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Windows' venv python.exe may be a launcher with a second Python
        # process underneath it. Kill descendants before their parent, while
        # the relationship is still available, then wait for their exits.
        children = []
        try:
            children = psutil.Process(proc.pid).children(recursive=True)
        except psutil.Error:
            pass
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                _LOG.warning("capture descendant cleanup failed: %s", exc)
        # A denied descendant must not skip root cleanup. Keep each attempt
        # independent and preserve the original timeout for the caller.
        try:
            proc.kill()
        except OSError as exc:
            _LOG.warning("capture worker termination failed: %s", exc)
        try:
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _LOG.warning("capture worker did not exit during cleanup: %s", exc)
        try:
            _, alive = psutil.wait_procs(children, timeout=1)
            if alive:
                _LOG.warning("capture descendants still alive after cleanup: %s",
                             [child.pid for child in alive])
        except psutil.Error as exc:
            _LOG.warning("capture descendant wait failed: %s", exc)
        raise


def capture_png(hwnd: int, path: str, enlarge: int = 1, *,
                timeout: float = _CAPTURE_TIMEOUT_S) -> bool:
    """Render a window to a PNG within ``timeout`` seconds.

    PrintWindow can wait forever for an unresponsive UI thread. Run it in
    a disposable hidden process, attempting to terminate its process tree on
    timeout with up to two seconds for exit cleanup. OS-denied cleanup is
    logged and cannot guarantee termination. A thread timeout cannot release
    a stalled PrintWindow call. Failed captures leave an
    existing destination untouched and remove their partial output.

    ``enlarge`` is for OCR; ordinary screenshots retain their real pixels.
    """
    if timeout <= 0 or not available():
        return False
    staged = ""
    try:
        # Same directory allows an atomic replace, including destinations
        # on a different drive from the system temporary directory.
        fd, staged = tempfile.mkstemp(
            prefix=".eda-capture-", suffix=".png",
            dir=os.path.dirname(os.path.abspath(path)))
        os.close(fd)
        result = _run_capture(
            _capture_command(int(hwnd), staged, int(enlarge)),
            timeout=timeout)
        if result != 0 or os.path.getsize(staged) == 0:
            return False
        replace_with_retry(staged, path)
        return True
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False
    finally:
        if staged:
            try:
                os.remove(staged)
            except OSError:
                pass


def _capture_png_inline(hwnd: int, path: str, enlarge: int = 1) -> bool:
    """Worker-only Win32 capture. Never call in the MCP process.

    ``enlarge`` multiplies the size, which only the text recogniser
    wants; a screenshot stays at the window's real pixels so that it can
    be measured.

    PrintWindow asks the window to draw ITSELF, so the capture is
    correct even when the dialog is behind something else or off the
    visible desktop, and it never depends on what the user happens to
    have in front.
    """
    import ctypes

    import win32con
    import win32gui
    import win32ui
    from PIL import Image

    # This subprocess does not inherit the MCP process's DPI awareness.
    # Initialize before reading rectangles so bitmap size uses physical pixels.
    win._make_dpi_aware()
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return False

    window_dc = win32gui.GetWindowDC(hwnd)
    src = win32ui.CreateDCFromHandle(window_dc)
    dst = src.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(src, width, height)
    dst.SelectObject(bitmap)
    try:
        ok = ctypes.windll.user32.PrintWindow(
            hwnd, dst.GetSafeHdc(), _PW_RENDERFULLCONTENT)
        if not ok:
            # Some windows refuse PrintWindow; copying from the window
            # DC still works when the dialog is actually on screen.
            dst.BitBlt((0, 0), (width, height), src, (0, 0),
                       win32con.SRCCOPY)
        info = bitmap.GetInfo()
        image = Image.frombuffer(
            "RGB", (info["bmWidth"], info["bmHeight"]),
            bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1)
        # Upscaling helps the recogniser on small UI type, which is
        # usually 8 or 9 point and sits near its resolution limit.
        # Screenshots are left at size: doubled they hold no more detail
        # and give wrong measurements.
        if enlarge > 1:
            image = image.resize((width * enlarge, height * enlarge), Image.LANCZOS)
        image.save(path)
        return True
    finally:
        dst.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(hwnd, window_dc)
        win32gui.DeleteObject(bitmap.GetHandle())


def _capture_worker(argv: list[str]) -> int:
    """The child process entry point; no GUI or input is synthesized."""
    if len(argv) != 3:
        return 2
    try:
        return 0 if _capture_png_inline(int(argv[0]), argv[1], int(argv[2])) else 1
    except Exception:                              # noqa: BLE001
        return 1


def read_window_text(hwnd: int, timeout: float = 30.0) -> list:
    """Every line of text OCR can find in a window. Empty on failure.

    Never raises: this is a best-effort enrichment of a report that must
    still be produced when it cannot run.
    """
    return _recognise(hwnd, timeout, words=False)


def read_window_rows(hwnd: int, timeout: float = 30.0) -> list:
    """The window's text as screen rows, left to right. Empty on failure.

    Line mode splits a grid by column, so a message loses the row it
    belongs to; here every word is placed by its position and words whose
    vertical centres agree form one row.
    """
    return rows_from_words(_recognise(hwnd, timeout, words=True))


def rows_from_words(lines: list) -> list:
    """Group ``x<TAB>y<TAB>height<TAB>word`` records into rows of text."""
    words = []
    for line in lines:
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        try:
            x, y, h = int(parts[0]), int(parts[1]), int(parts[2])
        except ValueError:
            continue
        words.append((y + h / 2.0, x, max(h, 1), parts[3]))
    rows: list = []
    for centre, x, h, text in sorted(words):
        if rows and abs(centre - rows[-1]["centre"]) <= rows[-1]["h"] * 0.6:
            rows[-1]["words"].append((x, text))
        else:
            rows.append({"centre": centre, "h": h, "words": [(x, text)]})
    return [" ".join(t for _, t in sorted(r["words"])) for r in rows]


def _recognise(hwnd: int, timeout: float, words: bool) -> list:
    if not available():
        return []
    tmp = tempfile.mkdtemp(prefix="eda-agent-ocr-")
    png = os.path.join(tmp, "dialog.png")
    script = os.path.join(tmp, "ocr.ps1")
    try:
        if not capture_png(hwnd, png, enlarge=2):
            return []
        with open(script, "w", encoding="utf-8") as handle:
            handle.write(_OCR_PS1)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", script, "-Path", png] + (["-Words"] if words else []),
            capture_output=True, text=True, timeout=timeout, **no_window())
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines()
                if line.strip()]
    except Exception:                             # noqa: BLE001
        return []
    finally:
        for path in (png, script):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(_capture_worker(sys.argv[1:]))
