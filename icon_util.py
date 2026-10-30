"""Tiny toolbar icons as CTkImage (no external asset files)."""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw
import customtkinter as ctk


def _copy_pil(size: int = 18, color: tuple[int, int, int] = (51, 65, 85)) -> Image.Image:
    """Simple dual-document copy glyph."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # back page
    d.rounded_rectangle((4, 2, size - 2, size - 6), radius=2, outline=color, width=2)
    # front page
    d.rounded_rectangle((1, 5, size - 5, size - 1), radius=2, outline=color, width=2, fill=(255, 255, 255, 230))
    # lines on front
    y0 = 8
    for i in range(3):
        y = y0 + i * 3
        d.line((4, y, size - 8, y), fill=color, width=1)
    return img


@lru_cache(maxsize=4)
def copy_icon(size: int = 18) -> ctk.CTkImage:
    pil = _copy_pil(size)
    return ctk.CTkImage(light_image=pil, dark_image=pil, size=(size, size))
