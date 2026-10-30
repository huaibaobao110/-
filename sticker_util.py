"""Crop and lightly analyze sticker / emoji image regions."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parent
STICKER_DIR = ROOT / "_stickers"


def _mood_from_patch(img: Image.Image) -> str:
    """Heuristic mood label from color stats (no ML)."""
    rgb = img.convert("RGB")
    # ignore near-bg margins a bit
    w, h = rgb.size
    crop = rgb.crop((int(w * 0.12), int(h * 0.12), int(w * 0.88), int(h * 0.88)))
    if crop.size[0] < 4 or crop.size[1] < 4:
        crop = rgb
    stat = ImageStat.Stat(crop)
    r, g, b = stat.mean
    # rough saturation / value
    mx, mn = max(r, g, b), min(r, g, b)
    sat = (mx - mn) / max(mx, 1.0)
    val = mx / 255.0

    # warm vs cool
    warm = r - (g + b) / 2
    cool = b - (r + g) / 2

    if sat < 0.12 and val > 0.55:
        return "无语"
    if warm > 25 and val > 0.45:
        if sat > 0.35:
            return "宠溺"
        return "开心"
    if cool > 20 and val < 0.55:
        return "冷淡"
    if g > r + 15 and g > b + 10:
        return "平和"
    if val > 0.7 and sat > 0.25:
        return "搞笑"
    if val < 0.35:
        return "委屈"
    return "表情"


def enhance_sticker(img: Image.Image) -> Image.Image:
    """Boost sticker contrast for clearer local saves / optional future OCR."""
    work = img.convert("RGB")
    work = ImageOps.autocontrast(work, cutoff=2)
    work = ImageEnhance.Color(work).enhance(1.25)
    work = ImageEnhance.Sharpness(work).enhance(1.35)
    work = work.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=2))
    return work


def label_sticker(img: Image.Image, *, index: int = 0, save: bool = True) -> str:
    """
    Return a dialogue marker like [表情包:开心].
    Optionally save enhanced crop under _stickers/.
    """
    mood = _mood_from_patch(img)
    if save:
        try:
            STICKER_DIR.mkdir(exist_ok=True)
            enhanced = enhance_sticker(img)
            enhanced.save(STICKER_DIR / f"sticker_{index:02d}_{mood}.png")
        except Exception:
            pass
    return f"[表情包:{mood}]"


def crop_and_label(
    full: Image.Image,
    box: tuple[float, float, float, float],
    *,
    index: int = 0,
) -> str:
    """box = (x, y, w, h) in full image coords."""
    x, y, w, h = box
    W, H = full.size
    left = max(0, int(x))
    top = max(0, int(y))
    right = min(W, int(x + w))
    bottom = min(H, int(y + h))
    if right - left < 8 or bottom - top < 8:
        return "[表情包]"
    patch = full.crop((left, top, right, bottom))
    return label_sticker(patch, index=index, save=True)
