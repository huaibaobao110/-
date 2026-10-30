"""Activate WeChat window and paste + send text."""

from __future__ import annotations

import ctypes
import time
from typing import Optional

import win32api
import win32con
import win32gui
import win32process

from clipboard_util import set_text

user32 = ctypes.windll.user32


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


def find_wechat_window(title_hint: str = "微信") -> Optional[int]:
    """Prefer exact personal WeChat over 企业微信."""
    windows = _enum_windows()
    exact = [h for h, t in windows if t == title_hint]
    if exact:
        return exact[0]
    partial = [
        h
        for h, t in windows
        if title_hint in t and "企业" not in t and "WXWork" not in t
    ]
    return partial[0] if partial else None


def force_foreground(hwnd: int) -> bool:
    """Bring window to foreground; return True if it became foreground."""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)

    if win32gui.GetForegroundWindow() == hwnd:
        return True

    current_thread = win32api.GetCurrentThreadId()
    foreground = win32gui.GetForegroundWindow()
    foreground_thread = (
        win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    )
    target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]

    attached_fg = False
    attached_tg = False
    try:
        # Common unlock: brief Alt so Windows allows SetForegroundWindow
        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.05)

        if foreground_thread and foreground_thread != current_thread:
            attached_fg = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, True)
            )
        if target_thread and target_thread != current_thread:
            attached_tg = bool(
                user32.AttachThreadInput(current_thread, target_thread, True)
            )
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        win32gui.BringWindowToTop(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached_tg:
            user32.AttachThreadInput(current_thread, target_thread, False)
        if attached_fg:
            user32.AttachThreadInput(current_thread, foreground_thread, False)

    time.sleep(0.15)
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    # Last resort: click the window title bar area to force activation
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        cx = (left + right) // 2
        cy = top + 12
        win32api.SetCursorPos((cx, cy))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.12)
    except Exception:
        pass
    return win32gui.GetForegroundWindow() == hwnd


def _key_combo(*vk_codes: int) -> None:
    for code in vk_codes:
        win32api.keybd_event(code, 0, 0, 0)
    time.sleep(0.02)
    for code in reversed(vk_codes):
        win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)


def _click_client(hwnd: int, cx: int, cy: int) -> None:
    screen_pt = win32gui.ClientToScreen(hwnd, (int(cx), int(cy)))
    win32api.SetCursorPos(screen_pt)
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _set_text_retry(text: str, tries: int = 5) -> None:
    last: Exception | None = None
    for i in range(tries):
        try:
            set_text(text)
            return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.08 * (i + 1))
    raise RuntimeError(f"无法写入剪贴板：{last}")


def paste_and_send(text: str, title_hint: str = "微信", send: bool = True) -> None:
    """Focus WeChat, paste text into the current chat input, optionally press Enter."""
    hwnd = find_wechat_window(title_hint)
    if not hwnd:
        raise RuntimeError(
            f"找不到微信窗口（标题含「{title_hint}」）。请先打开桌面版微信并进入某个聊天。"
        )

    _set_text_retry(text)
    ok = force_foreground(hwnd)
    time.sleep(0.28)
    if not ok and win32gui.GetForegroundWindow() != hwnd:
        # click into window body once more
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        win32api.SetCursorPos(((left + right) // 2, (top + bottom) // 2))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        time.sleep(0.2)
        force_foreground(hwnd)
        time.sleep(0.2)

    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width = right - left
    height = bottom - top
    if width > 50 and height > 80:
        # WeChat: contact list left ~30%, chat+input on the right.
        # Old code clicked width/2 which often hits the contact list / divider.
        cx = int(width * 0.68)
        # input sits above bottom toolbar; try two spots
        for cy in (height - 55, height - 85, height - 35):
            if cy < height * 0.45:
                continue
            _click_client(hwnd, cx, cy)
            time.sleep(0.12)
            break

    # Ensure no stuck modifiers
    for vk in (win32con.VK_CONTROL, win32con.VK_SHIFT, win32con.VK_MENU):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)

    if win32gui.GetForegroundWindow() != hwnd:
        force_foreground(hwnd)
        time.sleep(0.15)

    _key_combo(win32con.VK_CONTROL, ord("A"))  # select existing draft
    time.sleep(0.05)
    _key_combo(win32con.VK_CONTROL, ord("V"))
    time.sleep(0.2)

    if send:
        # WeChat default: Enter sends (Ctrl+Enter newline)
        win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
        win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
        time.sleep(0.1)
