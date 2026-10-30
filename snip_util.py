"""Launch Windows screen snipping and wait for a clipboard image."""

from __future__ import annotations

import ctypes
import os
import time
from typing import Callable

import win32api
import win32con
from PIL import Image, ImageGrab

user32 = ctypes.windll.user32


def clipboard_sequence() -> int:
    return int(user32.GetClipboardSequenceNumber())


def _image_fingerprint(img: Image.Image | None) -> tuple | None:
    if img is None or not isinstance(img, Image.Image):
        return None
    # cheap fingerprint: size + a few sample pixels
    w, h = img.size
    samples = []
    for x, y in ((0, 0), (w // 2, h // 2), (max(0, w - 1), max(0, h - 1))):
        samples.append(img.getpixel((x, y)))
    return (w, h, tuple(samples))


def grab_clipboard_image() -> Image.Image | None:
    clip = ImageGrab.grabclipboard()
    if isinstance(clip, Image.Image):
        return clip.convert("RGB")
    if isinstance(clip, list) and clip:
        try:
            return Image.open(clip[0]).convert("RGB")
        except Exception:
            return None
    return None


def _press_win_shift_s() -> None:
    vk_win = win32con.VK_LWIN
    vk_shift = win32con.VK_SHIFT
    vk_s = ord("S")
    win32api.keybd_event(vk_win, 0, 0, 0)
    win32api.keybd_event(vk_shift, 0, 0, 0)
    win32api.keybd_event(vk_s, 0, 0, 0)
    win32api.keybd_event(vk_s, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(vk_shift, 0, win32con.KEYEVENTF_KEYUP, 0)
    win32api.keybd_event(vk_win, 0, win32con.KEYEVENTF_KEYUP, 0)


def launch_screen_snip() -> None:
    """Open Windows snipping overlay (same as Win+Shift+S)."""
    try:
        os.startfile("ms-screenclip:")  # noqa: S606 — local protocol handler
        return
    except OSError:
        pass
    _press_win_shift_s()


def snip_to_image(
    *,
    timeout_sec: float = 60.0,
    poll_sec: float = 0.25,
    on_waiting: Callable[[], None] | None = None,
) -> Image.Image:
    """
    Open snipping UI and wait until user copies a new screenshot to clipboard.
    Raises TimeoutError if cancelled / timed out.
    """
    before_seq = clipboard_sequence()
    before_fp = _image_fingerprint(grab_clipboard_image())

    launch_screen_snip()
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        if on_waiting:
            on_waiting()
        time.sleep(poll_sec)
        seq = clipboard_sequence()
        if seq == before_seq:
            continue
        img = grab_clipboard_image()
        fp = _image_fingerprint(img)
        if img is not None and fp != before_fp:
            return img

    raise TimeoutError("截图已取消或超时。请框选消息区域后松手，截图会自动进入剪贴板。")
