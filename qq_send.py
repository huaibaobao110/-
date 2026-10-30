"""Activate QQ window and paste + send text."""

from __future__ import annotations

import ctypes
import os
import time
from typing import Optional

import win32api
import win32con
import win32gui
import win32process

from clipboard_util import set_text
from wechat_send import _key_combo, force_foreground

kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _enum_windows() -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []

    def callback(hwnd: int, _: list) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            result.append((hwnd, title))

    win32gui.EnumWindows(callback, result)
    return result


def _process_image_path(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(512)
        ok = kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return buf.value if ok else ""
    finally:
        kernel32.CloseHandle(handle)


def _is_qq_process(hwnd: int) -> bool:
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        path = _process_image_path(pid).lower().replace("/", "\\")
    except Exception:
        return False
    if not path:
        return False
    name = os.path.basename(path)
    if name in {"qq.exe", "qqnt.exe"}:
        return True
    if "\\qq\\" in path and name.endswith(".exe") and "uninstall" not in name:
        return True
    return False


def _window_area(hwnd: int) -> int:
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return max(0, right - left) * max(0, bottom - top)
    except Exception:
        return 0


def find_qq_window(title_hint: str = "QQ") -> Optional[int]:
    """Find a visible QQ window. Prefer title hint (chat name), else largest QQ window."""
    hint = (title_hint or "QQ").strip()
    windows = _enum_windows()
    candidates: list[tuple[int, str, int]] = []

    for hwnd, title in windows:
        area = _window_area(hwnd)
        if area < 220 * 220:
            continue
        low = title.lower()
        if any(x in title for x in ("安装", "更新", "崩溃")) or "setup" in low:
            continue
        is_qq = _is_qq_process(hwnd)
        title_hit = (hint in title) or (title.strip() in {"QQ", "QQNT"})
        if is_qq or title_hit:
            candidates.append((hwnd, title, area))

    if not candidates:
        return None

    # 1) exact title
    exact = [c for c in candidates if c[1] == hint]
    if exact:
        return max(exact, key=lambda x: x[2])[0]
    # 2) title contains hint (e.g. friend nickname)
    if hint and hint not in {"QQ", "QQNT"}:
        partial = [c for c in candidates if hint in c[1]]
        if partial:
            return max(partial, key=lambda x: x[2])[0]
    # 3) QQ-process windows that look like open chats
    chats = [
        c
        for c in candidates
        if _is_qq_process(c[0]) and c[1].strip() not in {"QQ", "QQNT"}
    ]
    if chats:
        return max(chats, key=lambda x: x[2])[0]
    # 4) largest QQ process window
    qq_only = [c for c in candidates if _is_qq_process(c[0])]
    pool = qq_only or candidates
    return max(pool, key=lambda x: x[2])[0]


def paste_and_send(
    text: str,
    title_hint: str = "QQ",
    *,
    send: bool = True,
    ctrl_enter: bool = False,
) -> None:
    """Focus QQ, paste into input, optionally send (Enter or Ctrl+Enter)."""
    hwnd = find_qq_window(title_hint)
    if not hwnd:
        raise RuntimeError(
            f"找不到 QQ 窗口（标题含「{title_hint}」或 QQ 进程）。"
            "请先打开桌面版 QQ 并进入某个聊天；"
            "也可在 config.json 设置 qq_window_title 为当前聊天标题。"
        )

    # reuse WeChat clipboard retry + focus hardening
    from wechat_send import _set_text_retry

    _set_text_retry(text)
    force_foreground(hwnd)
    time.sleep(0.28)

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width = right - left
    height = bottom - top
    if width > 50 and height > 80:
        cx = int(width * 0.62)
        for cy in (height - 48, height - 72, height - 36):
            if cy < height * 0.4:
                continue
            screen_pt = win32gui.ClientToScreen(hwnd, (cx, cy))
            win32api.SetCursorPos(screen_pt)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            time.sleep(0.12)
            break

    for vk in (win32con.VK_CONTROL, win32con.VK_SHIFT, win32con.VK_MENU):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)

    _key_combo(win32con.VK_CONTROL, ord("A"))
    time.sleep(0.05)
    _key_combo(win32con.VK_CONTROL, ord("V"))
    time.sleep(0.2)

    if send:
        if ctrl_enter:
            _key_combo(win32con.VK_CONTROL, win32con.VK_RETURN)
        else:
            win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
            win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)
