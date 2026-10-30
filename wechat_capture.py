"""Capture WeChat chat area and OCR the bottom-most incoming message."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import win32con
import win32gui
from PIL import Image, ImageEnhance, ImageGrab, ImageOps

from paths_util import app_dir, resource_dir
from wechat_send import find_wechat_window, force_foreground

ROOT = app_dir()
OCR_SCRIPT = resource_dir() / "ocr_win.ps1"
if not OCR_SCRIPT.exists():
    OCR_SCRIPT = ROOT / "ocr_win.ps1"
CONFIG_PATH = ROOT / "config.json"

DEFAULT_CROP = {
    "left": 0.36,
    "top": 0.08,
    "right": 0.995,
    # Hard stop above input/toolbar (avoids false bubbles near composer)
    "bottom": 0.82,
    # Fallback lower-strip ratio if bubble detect fails
    "recent_from": 0.78,
}


def _load_crop() -> dict:
    crop = dict(DEFAULT_CROP)
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        user = cfg.get("capture_crop") or {}
        for key in DEFAULT_CROP:
            if key in user:
                crop[key] = float(user[key])
    except Exception:
        pass
    return crop


def _row_score(img: Image.Image, y: int, x0: int, x1: int) -> float:
    total = 0.0
    bright = 0
    n = 0
    step = max(2, (x1 - x0) // 100)
    for x in range(x0, x1, step):
        p = img.getpixel((x, y))
        b = (p[0] + p[1] + p[2]) / 3.0
        total += b
        if b > 48:
            bright += 1
        n += 1
    if n == 0:
        return 0.0
    return (total / n - 30.0) + bright * 1.8


def _content_runs(
    img: Image.Image,
    x0: int,
    x1: int,
    y0: int,
    y1: int,
    threshold: float = 7.0,
    min_height: int = 16,
    gap: int = 8,
) -> list[tuple[int, int]]:
    hits: list[int] = []
    for y in range(y0, y1, 2):
        if _row_score(img, y, x0, x1) > threshold:
            hits.append(y)
    if not hits:
        return []

    runs: list[tuple[int, int]] = []
    start = prev = hits[0]
    for y in hits[1:]:
        if y - prev <= gap:
            prev = y
        else:
            runs.append((start, prev))
            start = prev = y
    runs.append((start, prev))
    # Keep short single-line bubbles; drop tiny noise/timestamp ticks
    return [(a, b) for a, b in runs if (b - a) >= min_height]


def _find_bottom_incoming_bubble(
    img: Image.Image, left: int, right: int, top: int, bottom: int
) -> tuple[int, int] | None:
    """Locate the lowest incoming bubble using the left half of the chat pane."""
    mid = left + max(80, int((right - left) * 0.55))
    # Prefer slightly wider scan so longer bubbles aren't missed
    mid_wide = left + max(120, int((right - left) * 0.70))

    runs = _content_runs(img, left, mid, top, bottom, threshold=7.0)
    if len(runs) < 1:
        runs = _content_runs(img, left, mid_wide, top, bottom, threshold=6.0, min_height=14)
    if not runs:
        return None

    a, b = runs[-1]  # lowest bubble above the composer cutoff
    # If two bubbles got merged, keep the lower half of the run
    if (b - a) > 110:
        a = b - 70
    pad_top = 8
    pad_bottom = 12
    return max(top, a - pad_top), min(bottom, b + pad_bottom)


def _restore_and_focus(hwnd: int) -> None:
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.45)

    force_foreground(hwnd)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOPMOST,
        0,
        0,
        0,
        0,
        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
    )
    time.sleep(0.45)
    if win32gui.GetForegroundWindow() != hwnd:
        force_foreground(hwnd)
        time.sleep(0.25)


def _release_topmost(hwnd: int) -> None:
    try:
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_NOTOPMOST,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
        )
    except Exception:
        pass


def _capture_window(hwnd: int) -> Image.Image:
    _restore_and_focus(hwnd)
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = right - left
        height = bottom - top
        if width < 200 or height < 200:
            raise RuntimeError("微信窗口太小或仍在最小化，请先打开聊天窗口")
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        if img.size[0] < 100:
            raise RuntimeError("截图失败")
        return img
    finally:
        _release_topmost(hwnd)


def _detect_chat_left(img: Image.Image) -> int | None:
    w, h = img.size
    y0, y1 = int(h * 0.25), int(h * 0.65)
    if y1 <= y0:
        return None

    def avg_bri(x: int) -> float:
        total = 0.0
        n = 0
        for y in range(y0, y1, 6):
            p = img.getpixel((x, y))
            total += (p[0] + p[1] + p[2]) / 3.0
            n += 1
        return total / max(n, 1)

    best = None
    for x in range(int(w * 0.18), int(w * 0.55), 4):
        if avg_bri(x) < 40:
            best = x
            break
    return best


def _chat_region(
    img: Image.Image,
    crop: dict | None = None,
    *,
    history: bool = False,
) -> Image.Image:
    """Crop chat pane. history=True keeps the full message area for multi-turn OCR."""
    crop = crop or _load_crop()
    w, h = img.size
    left = int(w * crop["left"])
    detected = _detect_chat_left(img)
    if detected is not None:
        left = max(left, detected + 8)

    pane_top = int(h * crop["top"])
    right = int(w * crop["right"])
    bottom = int(h * crop["bottom"])
    if right - left < 80 or bottom - pane_top < 80:
        return img

    if history:
        # Keep almost the whole message list (both sides)
        return img.crop((left, pane_top, right, bottom))

    found = _find_bottom_incoming_bubble(img, left, right, pane_top, bottom)
    if found:
        top, bot = found
        return img.crop((left, top, right, bot))

    recent_from = float(crop.get("recent_from", 0.82))
    top = pane_top + int((bottom - pane_top) * recent_from)
    if bottom - top < 60:
        top = max(pane_top, bottom - 90)
    return img.crop((left, top, right, bottom))


def _enhance_for_ocr(img: Image.Image) -> Image.Image:
    gray = ImageOps.grayscale(img)
    inverted = ImageOps.invert(gray)
    contrast = ImageEnhance.Contrast(inverted).enhance(2.2)
    sharp = ImageEnhance.Sharpness(contrast).enhance(1.6)
    return sharp.convert("RGB")


def _run_ocr(image_path: Path) -> dict:
    out_path = image_path.with_suffix(".ocr.json")
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(OCR_SCRIPT),
        "-ImagePath",
        str(image_path),
        "-OutPath",
        str(out_path),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
    )
    if not out_path.exists():
        out = (proc.stdout or "").strip()
        if out:
            line = out.splitlines()[-1]
            try:
                data = json.loads(line)
                if data.get("ok"):
                    return data
            except json.JSONDecodeError:
                pass
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"OCR 未写出结果: {err or proc.returncode}")
    data = json.loads(out_path.read_text(encoding="utf-8"))
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "OCR 失败")
    return data


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs / extended
    "\U00002600-\U000027BF"  # misc symbols
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F680-\U0001F6FF"  # transport
    "\U0001F1E0-\U0001F1FF"  # flags
    "]+",
    flags=re.UNICODE,
)

# Common OCR / WeChat symbol leftovers worth keeping
_KEEP_SYMBOLS = set("!?？！…~～、，。:)(:D^_^→←↑↓★☆♥❤√×✔✅❌😊😂🤣😅😆😉😍😘🥰🤔👍🙏😭😡")

# Windows OCR often splits 亻-side glyphs or reads 亻 as katakana イ/ｲ
# e.g. 你 →「イ尔」, 他 →「イ也」, 们 →「イ门」
_OCR_GLYPH_FIXES: tuple[tuple[str, str], ...] = (
    ("イ尔", "你"),
    ("ィ尔", "你"),
    ("ｲ尔", "你"),
    ("亻尔", "你"),
    ("イ你", "你"),
    ("ｲ你", "你"),
    ("イ也", "他"),
    ("ｲ也", "他"),
    ("亻也", "他"),
    ("イ门", "们"),
    ("ｲ门", "们"),
    ("亻门", "们"),
    ("イ何", "何"),
    ("ｲ何", "何"),
    ("亻何", "何"),
)
_OCR_GLYPH_SPLIT_RE = re.compile(
    r"[イィｲ亻]\s*[尔也门何你]"
)


def _fix_ocr_glyphs(text: str) -> str:
    """Repair common Windows OCR splits of 亻-radical characters (你/他/们…)."""
    if not text:
        return text
    # Collapse "イ 尔" / "亻 尔" before dictionary replace
    def _merge_split(m: re.Match[str]) -> str:
        raw = re.sub(r"\s+", "", m.group(0))
        for bad, good in _OCR_GLYPH_FIXES:
            if raw == bad:
                return good
        # イ/亻 + 尔 → 你 even if variant not listed
        if raw.endswith("尔"):
            return "你"
        if raw.endswith("也"):
            return "他"
        if raw.endswith("门"):
            return "们"
        return raw

    text = _OCR_GLYPH_SPLIT_RE.sub(_merge_split, text)
    for bad, good in _OCR_GLYPH_FIXES:
        if bad in text:
            text = text.replace(bad, good)
    return text


def _has_emoji_or_kaomoji(text: str) -> bool:
    if _EMOJI_RE.search(text):
        return True
    if any(ch in _KEEP_SYMBOLS for ch in text):
        return True
    # simple kaomoji / emoticon
    if re.search(r"[:;]=?[)(/\\DpP]|[(/\\][_^.][)/\\]|233+|hhh+|哈哈哈?", text, re.I):
        return True
    return False


def _clean_ocr_text(text: str) -> str:
    text = text.strip()
    text = _fix_ocr_glyphs(text)
    # Keep emoji; only collapse spaces between CJK letters
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = _fix_ocr_glyphs(text)  # again after space collapse
    # Don't strip if the whole token is emoji/symbol
    if _has_emoji_or_kaomoji(text) and len(text) <= 8:
        return text.strip()
    text = re.sub(r"^[。．\.\,，、;；:\s]+", "", text)
    return text.strip()


def _is_noise_line(text: str) -> bool:
    if not text:
        return True
    if "\ufffd" in text:
        return True
    compact = re.sub(r"\s+", "", text).replace("：", ":")
    # Keep single CJK / emoji / meaningful symbol (e.g. 嗯 / ？ / 😂)
    if len(compact) == 1:
        ch = compact
        if "\u4e00" <= ch <= "\u9fff":
            return False
        if _has_emoji_or_kaomoji(ch) or ch in _KEEP_SYMBOLS:
            return False
        if ch.isalnum():
            return False
        return True
    if re.fullmatch(r"\d{1,2}", compact):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", compact):
        return True
    if text in {"昨天", "星期日", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六"}:
        return True
    # pure decorative dots
    if re.fullmatch(r"[.·•…]{1,6}", compact):
        return True
    return False


def _detect_sticker_placeholders(
    img: Image.Image, ocr_rows: list[dict], width: float, height: float
) -> list[dict]:
    """
    Find colorful / high-variance patches that look like stickers and aren't covered by OCR.
    Returns synthetic rows with text like [表情包].
    """
    width_i = max(1, int(width))
    height_i = max(1, int(height))
    if width_i < 80 or height_i < 80:
        return []
    # Downsample for speed (PIL resize requires int size)
    small_w = max(40, width_i // 8)
    small_h = max(40, height_i // 8)
    small = img.convert("RGB").resize(
        (small_w, small_h), Image.Resampling.BILINEAR
    )
    sw, sh = small.size
    sx = width_i / sw
    sy = height_i / sh

    def is_bg(p: tuple[int, int, int]) -> bool:
        r, g, b = p
        mx, mn = max(r, g, b), min(r, g, b)
        # dark WeChat bg or flat gray bubble-ish
        if mx <= 55 and (mx - mn) <= 12:
            return True
        if 45 <= mn and mx <= 75 and (mx - mn) <= 14:
            return True
        return False

    def is_vivid(p: tuple[int, int, int]) -> bool:
        r, g, b = p
        mx, mn = max(r, g, b), min(r, g, b)
        return (mx - mn) >= 35 or mx >= 170

    mask = [[False] * sw for _ in range(sh)]
    for y in range(sh):
        for x in range(sw):
            p = small.getpixel((x, y))
            mask[y][x] = (not is_bg(p)) and is_vivid(p)

    visited = [[False] * sw for _ in range(sh)]
    blobs: list[dict] = []
    for y in range(sh):
        for x in range(sw):
            if not mask[y][x] or visited[y][x]:
                continue
            # flood fill
            stack = [(x, y)]
            visited[y][x] = True
            cells = []
            while stack:
                cx, cy = stack.pop()
                cells.append((cx, cy))
                for nx, ny in (
                    (cx - 1, cy),
                    (cx + 1, cy),
                    (cx, cy - 1),
                    (cx, cy + 1),
                ):
                    if 0 <= nx < sw and 0 <= ny < sh and mask[ny][nx] and not visited[ny][nx]:
                        visited[ny][nx] = True
                        stack.append((nx, ny))
            if len(cells) < 18:
                continue
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            # sticker-ish square/rect, not full-width banner
            if bw < 6 or bh < 6:
                continue
            if bw > sw * 0.55:
                continue
            aspect = bw / max(bh, 1)
            if aspect < 0.45 or aspect > 2.4:
                continue
            # map back to original coords
            ox = x0 * sx
            oy = y0 * sy
            ow = bw * sx
            oh = bh * sy
            # skip if heavily overlaps an OCR text box
            overlap = False
            for r in ocr_rows:
                rx, ry, rw, rh = r["x"], r["y"], max(r["w"], 8), max(r["h"], 8)
                if not (ox + ow < rx or rx + rw < ox or oy + oh < ry or ry + rh < oy):
                    # intersection area ratio vs blob
                    ix0, iy0 = max(ox, rx), max(oy, ry)
                    ix1, iy1 = min(ox + ow, rx + rw), min(oy + oh, ry + rh)
                    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                    if inter > 0.35 * (ow * oh):
                        overlap = True
                        break
            if overlap:
                continue
            blobs.append(
                {
                    "text": "[表情包]",
                    "x": ox,
                    "y": oy,
                    "w": ow,
                    "h": oh,
                }
            )

    # keep at most a few, prefer larger; label mood via image processing
    blobs.sort(key=lambda b: b["w"] * b["h"], reverse=True)
    blobs = blobs[:5]
    try:
        from sticker_util import crop_and_label

        for i, b in enumerate(blobs):
            b["text"] = crop_and_label(
                img, (b["x"], b["y"], b["w"], b["h"]), index=i
            )
    except Exception:
        for b in blobs:
            b["text"] = "[表情包]"
    return blobs


def _collect_ocr_rows(ocr: dict, *, scale: float = 1.0) -> list[dict]:
    """Normalize OCR lines to {text,x,y,w,h} in original image coords."""
    s = scale if scale and scale > 0 else 1.0
    rows: list[dict] = []
    for item in ocr.get("lines") or []:
        text = _clean_ocr_text(item.get("text") or "")
        if _is_noise_line(text) or _is_ui_noise_line(text):
            continue
        rows.append(
            {
                "text": text,
                "x": float(item.get("x") or 0) / s,
                "y": float(item.get("y") or 0) / s,
                "w": float(item.get("w") or 0) / s,
                "h": float(item.get("h") or 0) / s,
            }
        )
    rows.sort(key=lambda r: (r["y"], r["x"]))
    return rows


def _cluster_bubbles(rows: list[dict], width: float) -> list[dict]:
    """Group wrapped lines into bubbles by vertical proximity + similar x."""
    if not rows:
        return []
    bubbles: list[list[dict]] = [[rows[0]]]
    for row in rows[1:]:
        prev = bubbles[-1][-1]
        gap = row["y"] - prev["y"]
        same_side = abs(row["x"] - prev["x"]) < max(80.0, width * 0.22)
        if gap <= 40 and same_side:
            bubbles[-1].append(row)
        else:
            bubbles.append([row])

    out: list[dict] = []
    for group in bubbles:
        text = "\n".join(r["text"] for r in group).strip()
        if not text:
            continue
        xs = [r["x"] for r in group]
        rights = [r["x"] + max(r["w"], 1.0) for r in group]
        ys = [r["y"] for r in group]
        bottoms = [r["y"] + max(r.get("h", 14), 8) for r in group]
        x0 = min(xs)
        x1 = max(rights)
        y0 = min(ys)
        out.append(
            {
                "text": text,
                "x": x0,
                "y": y0,
                "w": max(1.0, x1 - x0),
                "h": max(12.0, max(bottoms) - y0),
                "cx": (x0 + x1) / 2.0,
            }
        )
    return out


_UI_NOISE_RE = re.compile(
    r"(微信回复助手|聊天对象|身份底色|专属话术|候选回复|截图识别|读取微信|"
    r"重新生成|仅填入|发送到微信|新建|编辑|删除|准备就绪|已生成|"
    r"正常风格|暧昧风格|搞笑风格|稳妥得体|关心体贴)"
)


def _is_ui_noise_line(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if _UI_NOISE_RE.search(t):
        return True
    if t.startswith("专属话术"):
        return True
    return False


def _bubble_looks_green(img: Image.Image | None, bubble: dict) -> bool | None:
    """Detect WeChat green self-bubble near text. None = inconclusive."""
    if img is None:
        return None
    W, H = img.size
    x = int(bubble.get("x") or 0)
    y = int(bubble.get("y") or 0)
    w = max(8, int(bubble.get("w") or 8))
    h = max(8, int(bubble.get("h") or 14))
    # sample a few points to the left of text (inside green pad) and around
    samples = []
    for dx, dy in ((-10, h // 2), (-6, 2), (-6, h - 2), (4, -4), (w // 2, -5)):
        px = min(W - 1, max(0, x + dx))
        py = min(H - 1, max(0, y + dy))
        samples.append(img.getpixel((px, py)))
    greenish = 0
    for p in samples:
        r, g, b = p[:3]
        if g > 150 and g > r + 25 and g > b + 15 and r > 80:
            greenish += 1
    if greenish >= 2:
        return True
    if greenish == 0:
        # gray/white left bubble?
        grayish = 0
        for p in samples:
            r, g, b = p[:3]
            if abs(r - g) < 18 and abs(g - b) < 18 and r > 200:
                grayish += 1
        if grayish >= 2:
            return False
    return None


def _role_for_bubble(
    bubble: dict,
    width: float,
    *,
    image: Image.Image | None = None,
) -> str:
    """WeChat: left/gray = 对方, right/green = 自己."""
    text = (bubble.get("text") or "").strip()
    # Previously sent helper replies often start with 自动：
    if re.match(r"^自动[:：]", text):
        return "自己"

    green = _bubble_looks_green(image, bubble)
    if green is True:
        return "自己"
    if green is False:
        return "对方"

    cx = float(bubble.get("cx") or 0)
    x = float(bubble.get("x") or 0)
    # Strong right alignment (WeChat/QQ self bubbles sit on the right)
    # Use stricter threshold: x>0.42 alone was mis-tagging mid-left long bubbles as 自己
    if cx >= width * 0.58 or x >= width * 0.50:
        return "自己"
    return "对方"


def parse_dialogue(
    ocr: dict,
    *,
    image: Image.Image | None = None,
    scale: float = 1.0,
) -> list[dict]:
    """
    Parse OCR (+ optional sticker blobs) into ordered turns:
      [{"role": "对方"|"自己", "text": "..."}, ...]
    scale: OCR was run on upscaled image; map coords back to `image` space.
    """
    ocr_w = float(ocr.get("width") or 0) or 1.0
    ocr_h = float(ocr.get("height") or 0) or 1.0
    if image is not None and scale != 1.0:
        width = float(image.size[0])
        height = float(image.size[1])
    else:
        width = ocr_w / scale if scale else ocr_w
        height = ocr_h / scale if scale else ocr_h
        if image is not None:
            width = float(image.size[0])
            height = float(image.size[1])

    rows = _collect_ocr_rows(ocr, scale=scale)
    rows = [r for r in rows if not _is_ui_noise_line(r["text"])]
    if image is not None:
        try:
            rows.extend(_detect_sticker_placeholders(image, rows, width, height))
        except Exception:
            pass
        rows.sort(key=lambda r: (r["y"], r["x"]))
    bubbles = _cluster_bubbles(rows, width)
    turns: list[dict] = []
    for b in bubbles:
        if _is_ui_noise_line(b.get("text") or ""):
            continue
        role = _role_for_bubble(b, width, image=image)
        turns.append({"role": role, "text": b["text"], "h": b.get("h", 0)})
    merged: list[dict] = []
    for turn in turns:
        if merged and merged[-1]["role"] == turn["role"]:
            merged[-1]["text"] = f"{merged[-1]['text']}\n{turn['text']}"
        else:
            merged.append({"role": turn["role"], "text": turn["text"]})
    return merged


def format_dialogue(turns: list[dict]) -> str:
    if not turns:
        return ""
    lines = []
    for t in turns:
        role = t.get("role") or "对方"
        text = (t.get("text") or "").strip()
        if not text:
            continue
        parts = [p for p in text.splitlines() if p.strip()]
        if not parts:
            continue
        lines.append(f"{role}：{parts[0]}")
        for p in parts[1:]:
            lines.append(f"　　{p}")
    return "\n".join(lines).strip()


def flip_dialogue_roles(text: str) -> str:
    """Swap 对方/自己 labels in formatted dialogue."""
    if not text.strip():
        return text
    # protect placeholders
    out = text.replace("对方：", "⟦SELF⟧").replace("自己：", "对方：").replace("⟦SELF⟧", "自己：")
    out = out.replace("对方:", "⟦SELF⟧").replace("自己:", "对方:").replace("⟦SELF⟧", "自己:")
    return out


def _pick_incoming(
    ocr: dict,
    *,
    multi: bool = True,
    prefer_left: bool = False,
    as_dialogue: bool = True,
    image: Image.Image | None = None,
    scale: float = 1.0,
) -> str:
    """Extract chat text; default formats 对方/自己 dialogue from layout."""
    if as_dialogue:
        turns = parse_dialogue(ocr, image=image, scale=scale)
        if prefer_left:
            while len(turns) > 1 and turns[0]["role"] == "自己":
                turns = turns[1:]
        text = format_dialogue(turns)
        if text:
            return text

    width = float(ocr.get("width") or 1) / (scale or 1.0)
    rows = _collect_ocr_rows(ocr, scale=scale)
    items = [(r["y"], r["x"], r["text"]) for r in rows]
    if prefer_left:
        leftish = [it for it in items if it[1] < width * 0.62]
        items = leftish or items
    if not items:
        full = _clean_ocr_text(ocr.get("text") or "")
        if not full or "\ufffd" in full:
            raise RuntimeError("没有识别到聊天文字，请确保聊天内容清晰可见")
        parts = [
            p.strip()
            for p in full.splitlines()
            if p.strip() and not _is_noise_line(p.strip())
        ]
        if not parts:
            raise RuntimeError("没有识别到聊天文字，请确保聊天内容清晰可见")
        return "\n".join(parts) if multi else parts[-1]

    items.sort(key=lambda t: (t[0], t[1]))
    if not multi:
        return items[-1][2]
    parts = [items[0][2]]
    for i in range(1, len(items)):
        if items[i][0] - items[i - 1][0] > 48:
            parts.append("")
        parts.append(items[i][2])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()


def _ocr_image_region(
    img: Image.Image,
    *,
    multi: bool = True,
    prefer_left: bool = False,
    as_dialogue: bool = True,
) -> str:
    """Enhance + upscale + OCR a prepared image region."""
    if img.mode not in {"RGB", "L"}:
        img = img.convert("RGB")
    try:
        img.save(ROOT / "_debug_chat.png")
    except Exception:
        pass

    with tempfile.TemporaryDirectory(prefix="wxocr_") as tmp:
        path = Path(tmp) / "chat.png"
        enhanced = _enhance_for_ocr(img)
        big = enhanced.resize(
            (max(1, enhanced.size[0] * 2), max(1, enhanced.size[1] * 2)),
            Image.Resampling.LANCZOS,
        )
        big.save(path)
        try:
            big.save(ROOT / "_debug_chat_ocr.png")
        except Exception:
            pass
        ocr = _run_ocr(path)
        scale = 2.0  # we always upscale ×2 above
        return _pick_incoming(
            ocr,
            multi=multi,
            prefer_left=prefer_left,
            as_dialogue=as_dialogue,
            image=img,
            scale=scale,
        )


def _looks_like_full_wechat(img: Image.Image) -> bool:
    """Heuristic: large landscape screenshot likely a full desktop WeChat window."""
    w, h = img.size
    return w >= 900 and h >= 600 and w >= h


def recognize_from_image(
    img: Image.Image,
    *,
    crop_full_window: bool | None = None,
    multi: bool = True,
    history: bool = True,
) -> str:
    """OCR a screenshot into 对方/自己 dialogue when possible."""
    if img is None:
        raise RuntimeError("没有图片")
    if not isinstance(img, Image.Image):
        raise RuntimeError("不支持的图片格式")

    work = img.convert("RGB")
    try:
        work.save(ROOT / "_debug_full.png")
    except Exception:
        pass

    do_crop = _looks_like_full_wechat(work) if crop_full_window is None else crop_full_window
    if do_crop:
        region = _chat_region(work, _load_crop(), history=history)
        return _ocr_image_region(
            region,
            multi=multi,
            prefer_left=False,
            as_dialogue=True,
        )
    # User snip / imported region: full selected history
    return _ocr_image_region(
        work,
        multi=multi,
        prefer_left=False,
        as_dialogue=True,
    )


def recognize_from_path(path: str | Path, *, multi: bool = True, history: bool = True) -> str:
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"找不到图片：{p}")
    with Image.open(p) as im:
        return recognize_from_image(im.copy(), multi=multi, history=history)


def recognize_from_clipboard_image(*, multi: bool = True, history: bool = True) -> str:
    clip = ImageGrab.grabclipboard()
    if clip is None:
        raise RuntimeError("剪贴板里没有图片，可用「截图识别」或 Win+Shift+S")
    if isinstance(clip, list):
        if not clip:
            raise RuntimeError("剪贴板里没有图片")
        return recognize_from_path(clip[0], multi=multi, history=history)
    if isinstance(clip, Image.Image):
        return recognize_from_image(clip, multi=multi, history=history)
    raise RuntimeError("剪贴板内容不是可识别的图片")


def snip_and_recognize(*, timeout_sec: float = 60.0) -> str:
    """Open system snipping UI, wait for screenshot, then OCR dialogue history."""
    from snip_util import snip_to_image

    img = snip_to_image(timeout_sec=timeout_sec)
    return recognize_from_image(img, crop_full_window=False, multi=True, history=True)


def capture_latest_incoming(title_hint: str = "微信", *, history: bool = True) -> str:
    hwnd = find_wechat_window(title_hint)
    if not hwnd:
        raise RuntimeError("找不到微信窗口，请先打开桌面版微信并进入某个聊天")

    img = _capture_window(hwnd)
    try:
        img.save(ROOT / "_debug_full.png")
    except Exception:
        pass
    return recognize_from_image(
        img, crop_full_window=True, multi=True, history=history
    )
