"""Cross-platform clipboard helpers (Windows pywin32, else Tk)."""

from __future__ import annotations

import sys
import time


def get_text() -> str:
    if sys.platform == "win32":
        try:
            import win32clipboard
            import win32con

            win32clipboard.OpenClipboard()
            try:
                if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                    data = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    return data if isinstance(data, str) else str(data)
                return ""
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            pass
    try:
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        try:
            return r.clipboard_get()
        except Exception:
            return ""
        finally:
            r.destroy()
    except Exception:
        return ""


def set_text(text: str) -> None:
    if sys.platform == "win32":
        try:
            import win32clipboard
            import win32con

            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            finally:
                win32clipboard.CloseClipboard()
            time.sleep(0.05)
            return
        except Exception:
            pass
    try:
        import tkinter as tk

        r = tk.Tk()
        r.withdraw()
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
    except Exception as e:
        raise RuntimeError(f"无法写入剪贴板: {e}") from e
