"""The host machine: which OS and CPU, how to open a URL, how to use its clipboard.

fishbowl runs on:
  macOS    Apple Silicon (arm64) and Intel (x86_64)   → Lima VM (vz on Apple Silicon, QEMU on Intel)
  Windows  x86_64 (Intel / AMD) and arm64              → WSL2 distro
  Linux    x86_64 and arm64                            → Lima VM (QEMU + KVM)
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import webbrowser


def kind() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def arch() -> str:
    m = platform.machine().lower()
    return "arm64" if m in ("arm64", "aarch64", "armv8l") else "x86_64" if m in ("x86_64", "amd64", "x64") else m


def default_backend() -> str:
    return "wsl" if kind() == "windows" else "lima"


def describe() -> str:
    names = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}
    chip = arch()
    if kind() == "macos":
        chip = "Apple Silicon" if chip == "arm64" else "Intel"
    return f"{names[kind()]} · {chip}"


def open_url(url: str) -> bool:
    try:
        if kind() == "macos":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        return webbrowser.open(url)  # Windows: ShellExecute; Linux: xdg-open and friends
    except Exception:
        return False


# ------------------------------------------------------------------ clipboard


def _win_clipboard():
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    return ctypes, user32, kernel32


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _win_get() -> str:
    ctypes, user32, kernel32 = _win_clipboard()
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr) if ptr else ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _win_set(text: str) -> None:
    ctypes, user32, kernel32 = _win_clipboard()
    data = text.encode("utf-16-le") + b"\0\0"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        return
    try:
        user32.EmptyClipboard()
        user32.SetClipboardData(CF_UNICODETEXT, handle)  # the clipboard now owns the memory
    finally:
        user32.CloseClipboard()


def _linux_tool() -> tuple[list[str], list[str]] | None:
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        return ["wl-paste", "--no-newline"], ["wl-copy"]
    if shutil.which("xclip"):
        return ["xclip", "-o", "-selection", "clipboard"], ["xclip", "-i", "-selection", "clipboard"]
    return None


def clipboard_available() -> bool:
    return kind() in ("macos", "windows") or _linux_tool() is not None


def clipboard_get() -> str:
    k = kind()
    if k == "macos":
        return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
    if k == "windows":
        return _win_get()
    tool = _linux_tool()
    return subprocess.run(tool[0], capture_output=True, text=True).stdout if tool else ""


def clipboard_set(text: str) -> None:
    k = kind()
    if k == "macos":
        subprocess.run(["pbcopy"], input=text, text=True)
    elif k == "windows":
        _win_set(text)
    elif tool := _linux_tool():
        subprocess.run(tool[1], input=text, text=True)
